"""Native controller-process identity/exit observation; no process launch or kill.

Windows APIs are loaded only when the native launcher has yielded its own PID.
Missing access/identity/exit/CloseHandle evidence remains unknown, never stopped.
"""
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys

_IDENTITY_FAILURES = []


class OwnedProcessExit:
    def __init__(self, native_pid, expected_executable):
        if sys.platform != 'win32' or type(native_pid) is not int or not 1 <= native_pid <= 0xffffffff:
            raise ValueError('PROCESS_IDENTITY_REJECTED')
        if not isinstance(expected_executable, (str, Path)):
            raise ValueError('PROCESS_IDENTITY_REJECTED')
        expected_path = Path(expected_executable).resolve(strict=True)
        self._handle = None
        self._exited = False
        self._closed = False
        self._api = ctypes.WinDLL('kernel32', use_last_error=True)
        self._api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self._api.OpenProcess.restype = wintypes.HANDLE
        self._api.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                                        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        self._api.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._api.WaitForSingleObject.restype = wintypes.DWORD
        self._api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._api.CloseHandle.restype = wintypes.BOOL
        handle = self._api.OpenProcess(0x100000 | 0x1000, False, native_pid)  # SYNCHRONIZE, QUERY_LIMITED_INFORMATION
        if not handle:
            raise ValueError('PROCESS_IDENTITY_UNAVAILABLE')
        self._handle = handle
        try:
            buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if not self._api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                raise ValueError('PROCESS_IDENTITY_UNAVAILABLE')
            if Path(buffer.value).resolve(strict=True) != expected_path:
                raise ValueError('PROCESS_IDENTITY_REJECTED')
        except Exception:
            try:
                released = self.close()
            except Exception:
                released = False
            if not released:
                _IDENTITY_FAILURES.append(self)
            raise
        self.native_pid = native_pid

    def observed_exit(self):
        if self._exited:
            return True
        if self._handle is None:
            return False
        result = self._api.WaitForSingleObject(self._handle, 0)
        if result == 0:  # WAIT_OBJECT_0 on this held process object, not a reused PID
            self._exited = True
        return self._exited

    def close(self):
        if self._handle is None:
            return self._closed
        if self._api.CloseHandle(self._handle):
            self._handle = None
            self._closed = True
            return True
        return False  # preserve numeric handle and owner on failure

    def stopped_and_released(self):
        return self.observed_exit() and self.close()
