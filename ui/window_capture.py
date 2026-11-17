"""User-selected, session-local window identities."""
import ctypes
from ctypes import wintypes
import os
import sys


def windows_for_capture():
    if sys.platform != "win32":
        return []
    user = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi")
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    dwm.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD)
    rows = []

    @callback_type
    def visit(window, _):
        if not user.IsWindowVisible(window):
            return True
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if not owner.value or owner.value == os.getpid():
            return True
        cloaked = wintypes.DWORD()
        if dwm.DwmGetWindowAttribute(window, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
            return True
        title = ctypes.create_unicode_buffer(256)
        if user.GetWindowTextW(window, title, len(title)) and title.value.strip():
            rows.append((title.value.strip(), int(window), int(owner.value)))
        return len(rows) < 256

    user.EnumWindows(visit, 0)
    return sorted(rows, key=lambda row: (row[0].casefold(), row[2], row[1]))
