"""Per-directory lock for explicit dependency installation, independent of updates."""
from contextlib import contextmanager
from pathlib import Path
import ctypes, hashlib, sys
from dataset_paths import local_path, display_path, reject_reparse

@contextmanager
def _installation_lock(destination: Path):
    if sys.platform != "win32":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    canonical_destination = display_path(reject_reparse(destination).resolve())
    suffix = hashlib.sha256(str(canonical_destination).casefold().encode("utf-8")).hexdigest()
    handle = kernel.CreateMutexW(None, False, "Local\\VaporLockInstall-" + suffix)
    if not handle:
        raise RuntimeError("Windows could not coordinate this installation. Please retry.")
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 0) in (0, 128)
        if not acquired:
            raise RuntimeError("Another installer is updating this folder. Wait for it to finish before retrying.")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)

