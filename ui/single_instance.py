"""Windows user/session/install/edition ownership with a focus-only OS event.

No socket, message parser, remote arguments, file locks or process termination.
Named objects grant access only to the current token user and SYSTEM.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path


class SecurityAttributes(C.Structure):
    _fields_ = [('length', W.DWORD), ('descriptor', C.c_void_p), ('inherit', W.BOOL)]


class InstanceGuard:
    def __init__(self, root, edition):
        if edition not in ('free', 'premium') or os.name != 'nt':
            raise OSError('Instance ownership unavailable')
        self.mutex = self.event = None
        self.primary = False
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.a = C.WinDLL('advapi32', use_last_error=True)
        def api(lib, name, args, result=W.BOOL):
            call = getattr(lib, name); call.argtypes = args; call.restype = result; return call
        self.close_handle = api(self.k, 'CloseHandle', [W.HANDLE])
        self.free = api(self.k, 'LocalFree', [C.c_void_p], C.c_void_p)
        self.wait = api(self.k, 'WaitForSingleObject', [W.HANDLE, W.DWORD], W.DWORD)
        self.set_event = api(self.k, 'SetEvent', [W.HANDLE])
        self.release = api(self.k, 'ReleaseMutex', [W.HANDLE])
        create_mutex = api(self.k, 'CreateMutexW', [C.POINTER(SecurityAttributes), W.BOOL, W.LPCWSTR], W.HANDLE)
        create_event = api(self.k, 'CreateEventW', [C.POINTER(SecurityAttributes), W.BOOL, W.BOOL, W.LPCWSTR], W.HANDLE)
        process = api(self.k, 'GetCurrentProcess', [], W.HANDLE)
        open_token = api(self.a, 'OpenProcessToken', [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)])
        token_info = api(self.a, 'GetTokenInformation', [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)])
        sid_text = api(self.a, 'ConvertSidToStringSidW', [C.c_void_p, C.POINTER(W.LPWSTR)])
        descriptor = api(self.a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p])
        token = W.HANDLE(); text = W.LPWSTR(); sd = C.c_void_p()
        try:
            if not open_token(process(), 8, C.byref(token)):
                raise OSError('Instance identity unavailable')
            needed = W.DWORD()
            token_info(token, 1, None, 0, C.byref(needed))
            if not 1 <= needed.value <= 65536:
                raise OSError('Instance identity unavailable')
            buffer = C.create_string_buffer(needed.value)
            if not token_info(token, 1, buffer, needed, C.byref(needed)):
                raise OSError('Instance identity unavailable')
            sid = C.cast(buffer, C.POINTER(C.c_void_p))[0]
            if not sid_text(sid, C.byref(text)):
                raise OSError('Instance identity unavailable')
            identity = text.value
            canonical = os.path.normcase(str(Path(root).resolve(strict=True)))
            key = hashlib.sha256((identity + '\0' + canonical + '\0' + edition).encode('utf8')).hexdigest()
            name = 'Local\\VaporLockUI-' + key
            if not descriptor(f'D:P(A;;GA;;;{identity})(A;;GA;;;SY)', 1, C.byref(sd), None):
                raise OSError('Instance security unavailable')
            attributes = SecurityAttributes(C.sizeof(SecurityAttributes), sd, False)
            # Event first avoids a listener-registration race. It carries no data.
            self.event = create_event(C.byref(attributes), False, False, name + '-focus')
            if not self.event:
                raise OSError('Instance event unavailable')
            C.set_last_error(0)
            self.mutex = create_mutex(C.byref(attributes), True, name + '-owner')
            already = C.get_last_error() == 183
            if not self.mutex:
                raise OSError('Instance ownership unavailable')
            self.primary = not already
            if already:
                # Abandoned or released ownership is recoverable without killing
                # another process. A live owner remains authoritative.
                waited = self.wait(self.mutex, 0)
                if waited not in (0, 0x80, 0x102):
                    raise OSError('Instance ownership unavailable')
                self.primary = waited in (0, 0x80)
            if not self.primary and not self.set_event(self.event):
                raise OSError('Instance notification unavailable')
        except Exception:
            self.close()
            raise
        finally:
            if text: self.free(C.cast(text, C.c_void_p))
            if sd: self.free(sd)
            if token: self.close_handle(token)

    def take_focus_request(self):
        return bool(self.primary and self.event and self.wait(self.event, 0) == 0)

    def close(self):
        if self.mutex:
            if self.primary: self.release(self.mutex)
            self.close_handle(self.mutex); self.mutex = None
        if self.event:
            self.close_handle(self.event); self.event = None
        self.primary = False


def focus_existing(app, main_window=None):
    target = app.activeModalWidget() or app.activeWindow() or main_window
    if target is None: return
    if target.isMinimized(): target.showNormal()
    else: target.show()
    target.raise_(); target.activateWindow()
    # Windows may reserve foreground focus for another app. Use the ordinary
    # taskbar attention mechanism instead of manipulating unrelated windows.
    app.alert(target, 1500)
