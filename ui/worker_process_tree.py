"""Keep a background worker's descendants owned by its lifetime on Windows."""

import ctypes
from ctypes import wintypes
import os
import threading

_job_handle = None


def terminate_owned_tree() -> None:
    if os.name == "nt" and _job_handle is not None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject(_job_handle, 1)
    os._exit(1)


def own_process_tree(*, memory_limit=0) -> None:
    global _job_handle
    if os.name != "nt" or _job_handle is not None:
        return

    class BasicLimit(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                    ("Flags", wintypes.DWORD), ("Minimum", ctypes.c_size_t), ("Maximum", ctypes.c_size_t),
                    ("ActiveProcesses", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("Priority", wintypes.DWORD), ("Scheduling", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [("Basic", BasicLimit), ("Io", IoCounters), ("ProcessMemory", ctypes.c_size_t),
                    ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimit()
    limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if memory_limit:
        if type(memory_limit) is not int or not 256*1024**2<=memory_limit<=2*1024**3:
            kernel.CloseHandle(handle);raise ValueError('Invalid worker memory budget.')
        limits.Basic.Flags |= 0x100|0x200
        limits.ProcessMemory=memory_limit;limits.JobMemory=memory_limit
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    # Deliberately keep the sole, non-inherited handle until OS process teardown.
    # Terminating the supervisor then also terminates pip/training descendants.
    _job_handle = handle
    # A one-file PyInstaller launcher is a separate process. QProcess owns that
    # launcher, not this Python process, so also stop when the launcher exits.
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    parent = kernel.OpenProcess(0x100000, False, os.getppid())
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())

    def parent_exited():
        try:
            if kernel.WaitForSingleObject(parent, 0xFFFFFFFF) == 0:
                kernel.TerminateJobObject(handle, 1)
        finally:
            kernel.CloseHandle(parent)

    threading.Thread(target=parent_exited, name="VaporLockWorkerOwner", daemon=True).start()
