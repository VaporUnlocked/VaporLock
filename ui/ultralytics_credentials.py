"""Profile-owned, current-Windows-user protection for Ultralytics credentials."""
import base64
import ctypes
from ctypes import wintypes as W
import os
import re

PREFIX = 'dpapi-ul-v1:'
MAX_BLOB = 8192
_KEY = re.compile(r'ul_[0-9a-fA-F]{40}\Z')
_ENTROPY = b'VaporLock/Ultralytics/profile-key/v1'


class CredentialUnavailable(ValueError):
    def __init__(self):
        super().__init__('Saved Ultralytics key is unavailable here. Re-enter it.')


class _Blob(ctypes.Structure):
    _fields_ = [('size', W.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data):
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return buffer, _Blob(len(data), buffer)


def _crypt(data, protect):
    if os.name != 'nt':
        raise CredentialUnavailable()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [W.HLOCAL]
    kernel.LocalFree.restype = W.HLOCAL
    source, input_blob = _blob(data)
    entropy_buffer, entropy = _blob(_ENTROPY)
    output = _Blob()
    if protect:
        fn = api.CryptProtectData
        fn.argtypes = [ctypes.POINTER(_Blob), W.LPCWSTR, ctypes.POINTER(_Blob), W.LPVOID, W.LPVOID, W.DWORD, ctypes.POINTER(_Blob)]
        args = [ctypes.byref(input_blob), 'Vapor Lock Ultralytics API key', ctypes.byref(entropy), None, None, 1, ctypes.byref(output)]
    else:
        fn = api.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(_Blob), W.LPVOID, ctypes.POINTER(_Blob), W.LPVOID, W.LPVOID, W.DWORD, ctypes.POINTER(_Blob)]
        args = [ctypes.byref(input_blob), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)]
    fn.restype = W.BOOL
    try:
        if not fn(*args):
            raise CredentialUnavailable()
        if not 0 < output.size <= MAX_BLOB:
            raise CredentialUnavailable()
        return ctypes.string_at(output.data, output.size)
    finally:
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(ctypes.cast(output.data, W.HLOCAL))
        ctypes.memset(source, 0, len(data))


def validate_envelope(value):
    if type(value) is not str or len(value) > MAX_BLOB or (value and not value.startswith(PREFIX)):
        raise CredentialUnavailable()
    if value:
        try:
            raw = base64.b64decode(value[len(PREFIX):], validate=True)
        except (ValueError, TypeError):
            raise CredentialUnavailable() from None
        if not 1 <= len(raw) <= MAX_BLOB // 2:
            raise CredentialUnavailable()
    return value


def protect_key(key):
    if type(key) is not str or _KEY.fullmatch(key) is None:
        raise ValueError('Enter a valid Ultralytics API key.')
    return PREFIX + base64.b64encode(_crypt(key.encode('ascii'), True)).decode('ascii')


def restore_key(value):
    validate_envelope(value)
    if not value:
        raise CredentialUnavailable()
    try:
        key = _crypt(base64.b64decode(value[len(PREFIX):], validate=True), False).decode('ascii')
    except (ValueError, UnicodeError):
        raise CredentialUnavailable() from None
    if _KEY.fullmatch(key) is None:
        raise CredentialUnavailable()
    return key


def key_state(value):
    if not value:
        return 'missing'
    try:
        restore_key(value)
    except (ValueError, OSError):
        return 'unavailable'
    return 'available'
