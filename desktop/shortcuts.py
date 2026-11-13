"""Windows hotkeys for an owned VaporLock window."""

import ctypes
from ctypes import wintypes
import sys


_MODIFIERS = {'ALT': 1, 'CTRL': 2, 'SHIFT': 4, 'WIN': 8}
_KEYS = {'SPACE': 0x20, 'TAB': 0x09, 'ESC': 0x1B, 'ESCAPE': 0x1B,
         'INSERT': 0x2D, 'DELETE': 0x2E, 'HOME': 0x24, 'END': 0x23,
         'PAGEUP': 0x21, 'PGUP': 0x21, 'PAGEDOWN': 0x22, 'PGDOWN': 0x22,
         'UP': 0x26, 'DOWN': 0x28, 'LEFT': 0x25, 'RIGHT': 0x27}


def parse_hotkey(text):
    *modifiers, key = (part.strip().upper() for part in text.split('+'))
    if len(set(modifiers)) != len(modifiers) or any(item not in _MODIFIERS for item in modifiers):
        return None
    if len(key) == 1 and key in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
        code = ord(key)
    elif key.startswith('F') and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        code = 0x6F + int(key[1:])
    else:
        code = _KEYS.get(key)
    if code is None:
        return None
    return 0x4000 | sum(_MODIFIERS[item] for item in modifiers), code


class GlobalHotkeys:
    def __init__(self):
        self.actions = {}
        self.handle = None
        self.api = ctypes.WinDLL('user32', use_last_error=True) if sys.platform == 'win32' else None
        if self.api is not None:
            self.api.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
            self.api.RegisterHotKey.restype = wintypes.BOOL
            self.api.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
            self.api.UnregisterHotKey.restype = wintypes.BOOL

    def bind(self, handle, bindings):
        self.close()
        if self.api is None:
            return set()
        self.handle = handle
        installed = set()
        for identity, (sequence, action) in enumerate(bindings, 1):
            parsed = parse_hotkey(sequence)
            if parsed and self.api.RegisterHotKey(handle, identity, *parsed):
                self.actions[identity] = action
                installed.add(sequence)
        return installed

    def action_for(self, message):
        if not self.actions:
            return None
        event = wintypes.MSG.from_address(int(message))
        return self.actions.get(int(event.wParam)) if event.message == 0x0312 else None

    def close(self):
        if self.api is not None and self.handle is not None:
            for identity in self.actions:
                self.api.UnregisterHotKey(self.handle, identity)
        self.actions.clear()
        self.handle = None
