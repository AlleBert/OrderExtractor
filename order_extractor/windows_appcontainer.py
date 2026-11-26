"""Launch a packaged worker with an AppContainer token and no capabilities."""

import ctypes
import os
import subprocess
from contextlib import contextmanager
from ctypes import wintypes as wt
from functools import lru_cache
from pathlib import Path

from .windows_job import WindowsJob
from .worker_limits import CPU_SECONDS, ENV_NAMES, MEMORY

PROFILE = "OrderExtractor.PdfWorker"
SECURITY_CAPABILITIES_ATTRIBUTE = 0x00020009
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
WAIT_OBJECT = 0
WAIT_TIMEOUT = 258
PTR = ctypes.c_void_p
SIZE_T = ctypes.c_size_t


class SecurityCapabilities(ctypes.Structure):
    _fields_ = [("app_container_sid", PTR), ("capabilities", PTR), ("count", wt.DWORD), ("reserved", wt.DWORD)]


class StartupInfo(ctypes.Structure):
    _fields_ = [
        ("size", wt.DWORD), ("reserved", wt.LPWSTR), ("desktop", wt.LPWSTR), ("title", wt.LPWSTR),
        ("x", wt.DWORD), ("y", wt.DWORD), ("width", wt.DWORD), ("height", wt.DWORD),
        ("x_chars", wt.DWORD), ("y_chars", wt.DWORD), ("fill", wt.DWORD), ("flags", wt.DWORD),
        ("show", wt.WORD), ("reserved_size", wt.WORD), ("reserved_bytes", PTR),
        ("stdin", wt.HANDLE), ("stdout", wt.HANDLE), ("stderr", wt.HANDLE),
    ]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", PTR)]


class ProcessInformation(ctypes.Structure):
    _fields_ = [("process", wt.HANDLE), ("thread", wt.HANDLE), ("pid", wt.DWORD), ("tid", wt.DWORD)]


def bind(library, name, arguments, returns):
    function = getattr(library, name)
    function.argtypes = arguments
    function.restype = returns
    return function


def library(name):
    return ctypes.WinDLL(name, use_last_error=True)


def checked(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


@contextmanager
def profile_sid(kernel):
    userenv = library("userenv")
    security = library("advapi32")
    create = bind(userenv, "CreateAppContainerProfile", [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, PTR, wt.DWORD,
                  ctypes.POINTER(PTR)], ctypes.c_long)
    derive = bind(userenv, "DeriveAppContainerSidFromAppContainerName", [wt.LPCWSTR, ctypes.POINTER(PTR)], ctypes.c_long)
    to_string = bind(security, "ConvertSidToStringSidW", [PTR, ctypes.POINTER(wt.LPWSTR)], wt.BOOL)
    free_sid = bind(security, "FreeSid", [PTR], PTR)
    local_free = bind(kernel, "LocalFree", [PTR], PTR)
    sid = PTR()
    code = create(PROFILE, "Order Extractor PDF Worker", "Worker senza capability di rete", None, 0, ctypes.byref(sid))
    if code & 0xFFFFFFFF == 0x800700B7:  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)
        code = derive(PROFILE, ctypes.byref(sid))
    if code != 0 or not sid:
        raise OSError(f"AppContainer non disponibile (HRESULT {code & 0xFFFFFFFF:08X}).")
    try:
        rendered = wt.LPWSTR()
        checked(to_string(sid, ctypes.byref(rendered)))
        try:
            yield sid, rendered.value
        finally:
            local_free(ctypes.cast(rendered, PTR))
    finally:
        free_sid(sid)


@contextmanager
def startup_info(kernel, sid):
    initialize = bind(kernel, "InitializeProcThreadAttributeList", [PTR, wt.DWORD, wt.DWORD, ctypes.POINTER(SIZE_T)], wt.BOOL)
    update = bind(kernel, "UpdateProcThreadAttribute", [PTR, wt.DWORD, SIZE_T, PTR, SIZE_T, PTR, PTR], wt.BOOL)
    discard = bind(kernel, "DeleteProcThreadAttributeList", [PTR], None)
    length = SIZE_T()
    initialize(None, 1, 0, ctypes.byref(length))
    if not length.value:
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = ctypes.create_string_buffer(length.value)
    checked(initialize(attributes, 1, 0, ctypes.byref(length)))
    try:
        capability = SecurityCapabilities(sid, None, 0, 0)
        checked(update(attributes, 0, SECURITY_CAPABILITIES_ATTRIBUTE, ctypes.byref(capability),
                       ctypes.sizeof(capability), None, None))
        startup = StartupInfoEx()
        startup.startup.size = ctypes.sizeof(startup)
        startup.attributes = ctypes.cast(attributes, PTR)
        yield startup
    finally:
        discard(attributes)


def acl(path, *arguments):
    command = Path(os.environ["SYSTEMROOT"]) / "System32" / "icacls.exe"
    try:
        result = subprocess.run([command, str(path), *arguments, "/Q"], capture_output=True,
                                timeout=20, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OSError("Impossibile inizializzare i permessi AppContainer.") from error
    if result.returncode:
        raise OSError("Impossibile inizializzare i permessi AppContainer.")


@lru_cache(maxsize=8)
def allow_runtime(runtime, sid):
    acl(runtime, "/grant:r", f"*{sid}:(OI)(CI)(RX)")


def environment(directory):
    paths = {"TEMP": str(directory), "TMP": str(directory), "TMPDIR": str(directory),
             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    values = {key.upper(): value for key, value in os.environ.items() if key.upper() in ENV_NAMES}
    # Windows uses this location to initialize the AppContainer profile.
    values["LOCALAPPDATA"] = os.environ["LOCALAPPDATA"]
    # Runtime paths are absolute in the frozen package. Avoid inheriting a user search path.
    values["PATH"] = str(Path(values["SYSTEMROOT"]) / "System32")
    values.update(paths)
    block = "\0".join(f"{key}={values[key]}" for key in sorted(values)) + "\0"
    return ctypes.create_unicode_buffer(block)


def validate_token():
    kernel = library("kernel32")
    security = library("advapi32")
    current = bind(kernel, "GetCurrentProcess", [], wt.HANDLE)
    close = bind(kernel, "CloseHandle", [wt.HANDLE], wt.BOOL)
    open_token = bind(security, "OpenProcessToken", [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)], wt.BOOL)
    info = bind(security, "GetTokenInformation", [wt.HANDLE, ctypes.c_int, PTR, wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL)
    token = wt.HANDLE()
    checked(open_token(current(), 0x0008, ctypes.byref(token)))  # TOKEN_QUERY
    try:
        size = wt.DWORD()
        restricted = wt.DWORD()
        checked(info(token, 29, ctypes.byref(restricted), ctypes.sizeof(restricted), ctypes.byref(size)))
        if not restricted.value:
            raise OSError("Token AppContainer richiesto.")
        info(token, 30, None, 0, ctypes.byref(size))
        if not size.value:
            raise OSError("Impossibile verificare le capability AppContainer.")
        capabilities = ctypes.create_string_buffer(size.value)
        checked(info(token, 30, capabilities, size.value, ctypes.byref(size)))
        if wt.DWORD.from_buffer(capabilities).value != 0:
            raise OSError("Capability AppContainer non consentite.")
    finally:
        close(token)


class AppContainerProcess:
    def __init__(self, executable, arguments, directory):
        kernel = library("kernel32")
        self.close = bind(kernel, "CloseHandle", [wt.HANDLE], wt.BOOL)
        self.wait = bind(kernel, "WaitForSingleObject", [wt.HANDLE, wt.DWORD], wt.DWORD)
        terminate = bind(kernel, "TerminateProcess", [wt.HANDLE, wt.UINT], wt.BOOL)
        resume = bind(kernel, "ResumeThread", [wt.HANDLE], wt.DWORD)
        create = bind(kernel, "CreateProcessW", [wt.LPCWSTR, wt.LPWSTR, PTR, PTR, wt.BOOL, wt.DWORD, PTR, wt.LPCWSTR,
                     ctypes.POINTER(StartupInfoEx), ctypes.POINTER(ProcessInformation)], wt.BOOL)
        self.handle = None
        self.job = None
        executable, directory = executable.resolve(strict=True), directory.resolve(strict=True)
        if directory.is_relative_to(executable.parent) or executable.is_relative_to(directory):
            raise OSError("Le directory del lavoro e del runtime devono essere separate.")
        with profile_sid(kernel) as (sid, name):
            allow_runtime(executable.parent, name)
            acl(directory, "/grant:r", f"*{name}:(OI)(CI)(M)")
            acl(directory, "/setintegritylevel", "(OI)(CI)L")
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(executable), *arguments]))
            env = environment(directory)
            process = ProcessInformation()
            with startup_info(kernel, sid) as start:
                checked(create(str(executable), command, None, None, False,
                               CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
                               env, str(directory), ctypes.byref(start), ctypes.byref(process)))
            try:
                self.job = WindowsJob(process.process, MEMORY, CPU_SECONDS)
                if resume(process.thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handle = process.process
            except BaseException:
                terminate(process.process, 1)
                if self.job is not None:
                    self.job.close()
                    self.job = None
                self.wait(process.process, 5000)
                self.close(process.process)
                raise
            finally:
                self.close(process.thread)

    def is_alive(self):
        if self.handle is None:
            return False
        outcome = self.wait(self.handle, 0)
        if outcome not in {WAIT_OBJECT, WAIT_TIMEOUT}:
            raise ctypes.WinError(ctypes.get_last_error())
        return outcome == WAIT_TIMEOUT

    def finish(self):
        self.stop()

    def stop(self):
        if self.job is not None:
            self.job.close()
            self.job = None
        if self.handle is not None:
            if self.wait(self.handle, 5000) != WAIT_OBJECT:
                raise OSError("Impossibile terminare il processo PDF.")
            checked(self.close(self.handle))
            self.handle = None
