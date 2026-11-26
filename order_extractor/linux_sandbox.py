"""Linux development worker restrictions. Apply before parsing any PDF."""

import ctypes
import errno
import os
import platform
import shutil
import sys
from contextlib import ExitStack
from pathlib import Path

LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.syscall.restype = ctypes.c_long
LIBC.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
LIBC.prctl.restype = ctypes.c_int
CREATE_RULESET = 444
ADD_RULE = 445
RESTRICT_SELF = 446
READ = 0b1101  # execute, read file, read directory
FILE_READ = 0b0101
WRITE = sum(1 << bit for bit in (1, 4, 5, 7, 8, 14))  # write, delete, create, truncate
HANDLED = (1 << 15) - 1


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def checked(result):
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def allow(ruleset, path, rights, *, optional=False):
    try:
        descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except FileNotFoundError:
        if optional:
            return
        raise
    try:
        rule = PathRule(rights, descriptor)
        checked(LIBC.syscall(ADD_RULE, ruleset, 1, ctypes.byref(rule), 0))
    finally:
        os.close(descriptor)


def restrict_files(work):
    if platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}:
        raise OSError(errno.EOPNOTSUPP, "Architettura Landlock non supportata.")
    abi = checked(LIBC.syscall(CREATE_RULESET, ctypes.c_void_p(), 0, 1))
    if abi < 3:
        raise OSError(errno.EOPNOTSUPP, "Il worker richiede Linux con Landlock ABI 3 o successiva.")
    policy = Ruleset(HANDLED)
    descriptor = checked(LIBC.syscall(CREATE_RULESET, ctypes.byref(policy), ctypes.sizeof(policy), 0))
    with ExitStack() as cleanup:
        cleanup.callback(os.close, descriptor)
        for directory in {"/usr", "/lib", "/lib64", sys.prefix, sys.base_prefix, str(Path(__file__).parent)}:
            path = Path(directory).resolve()
            if path == Path("/"):
                raise OSError("Radice del runtime Python non supportata.")
            allow(descriptor, path, READ, optional=True)
        for file in ("/etc/ld.so.cache", "/dev/urandom"):
            allow(descriptor, file, FILE_READ, optional=True)
        allow(descriptor, "/dev/null", FILE_READ | 2)
        allow(descriptor, "/etc/fonts", READ, optional=True)
        executable = shutil.which(os.environ.get("OE_TESSERACT", "tesseract"))
        if executable:
            allow(descriptor, Path(executable).resolve(), FILE_READ)
        tessdata = os.environ.get("TESSDATA_PREFIX")
        if tessdata:
            languages = os.environ.get("OE_OCR_LANGS", "eng").split("+")
            for language in languages:
                if not language or not language.isascii() or not language.replace("_", "").isalnum():
                    raise OSError("Modello OCR configurato non valido.")
                allow(descriptor, Path(tessdata) / (language + ".traineddata"), FILE_READ)
        # The document directory permits data creation and deletion without executable access.
        allow(descriptor, work, (READ & ~1) | WRITE)
        checked(LIBC.prctl(38, 1, 0, 0, 0))  # PR_SET_NO_NEW_PRIVS
        checked(LIBC.syscall(RESTRICT_SELF, descriptor, 0))


def restrict_network():
    seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.restype = None
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_rule_add.restype = ctypes.c_int
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_load.restype = ctypes.c_int
    filter_ = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not filter_:
        raise OSError("Filtro seccomp non disponibile.")
    try:
        for name in (b"socket", b"socketpair", b"setsid", b"setpgid", b"io_uring_setup"):
            syscall = seccomp.seccomp_syscall_resolve_name(name)
            if syscall < 0 or seccomp.seccomp_rule_add(filter_, 0x00050001, syscall, 0) != 0:
                raise OSError("Impossibile inizializzare il filtro seccomp.")
        if seccomp.seccomp_load(filter_) != 0:
            raise OSError("Impossibile applicare il filtro seccomp.")
    finally:
        seccomp.seccomp_release(filter_)
