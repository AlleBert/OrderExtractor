"""Windows resource controls for the PDF worker and its OCR child."""

import ctypes
from ctypes import wintypes as wt

HANDLE = wt.HANDLE
SIZE_T = ctypes.c_size_t
LONGLONG = ctypes.c_longlong


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", LONGLONG), ("job_time", LONGLONG), ("flags", wt.DWORD),
        ("minimum_working_set", SIZE_T), ("maximum_working_set", SIZE_T), ("active_processes", wt.DWORD),
        ("affinity", SIZE_T), ("priority", wt.DWORD), ("scheduling", wt.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("read", "write", "other", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits), ("io", IoCounters), ("process_memory", SIZE_T), ("job_memory", SIZE_T),
        ("peak_process_memory", SIZE_T), ("peak_job_memory", SIZE_T),
    ]


class WindowsJob:
    def __init__(self, process_handle, memory, cpu_seconds):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        def bind(name, arguments, result):
            function = getattr(self.kernel, name)
            function.argtypes = arguments
            function.restype = result
            return function

        create = bind("CreateJobObjectW", [ctypes.c_void_p, wt.LPCWSTR], HANDLE)
        configure = bind("SetInformationJobObject", [HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD], wt.BOOL)
        assign = bind("AssignProcessToJobObject", [HANDLE, HANDLE], wt.BOOL)
        self.close_handle = bind("CloseHandle", [HANDLE], wt.BOOL)
        self.handle = create(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        # JOB_TIME, ACTIVE_PROCESS, PROCESS_MEMORY, JOB_MEMORY, KILL_ON_JOB_CLOSE.
        limits.basic.flags = 0x0004 | 0x0008 | 0x0100 | 0x0200 | 0x2000
        limits.basic.job_time = cpu_seconds * 10_000_000
        limits.basic.active_processes = 3
        limits.process_memory = memory
        limits.job_memory = memory
        if not configure(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not assign(
            self.handle, process_handle
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def close(self):
        if self.handle:
            if not self.close_handle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None
