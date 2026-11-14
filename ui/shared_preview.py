"""Reader for one native engine launch's pagefile-backed preview slots."""
import ctypes
from ctypes import wintypes
import struct
import uuid


class SharedPreview:
    MAX_PIXELS = 3840 * 2160
    HEADER = struct.Struct('<QII')
    SLOTS = 3
    SLOT_BYTES = HEADER.size + MAX_PIXELS * 4

    def __init__(self):
        self.token = uuid.uuid4().hex
        self.mapping = self.lock = self.address = None
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('CreateFileMappingW', (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR), wintypes.HANDLE),
            ('CreateMutexW', (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR), wintypes.HANDLE),
            ('MapViewOfFile', (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, ctypes.c_size_t), ctypes.c_void_p),
            ('WaitForSingleObject', (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
            ('ReleaseMutex', (wintypes.HANDLE,), wintypes.BOOL),
            ('UnmapViewOfFile', (ctypes.c_void_p,), wintypes.BOOL),
            ('CloseHandle', (wintypes.HANDLE,), wintypes.BOOL),
        ):
            function = getattr(self.api, name)
            function.argtypes = args
            function.restype = result
        name = 'Local\\VaporLockPreview-' + self.token
        size = self.SLOT_BYTES * self.SLOTS
        try:
            ctypes.set_last_error(0)
            self.mapping = self.api.CreateFileMappingW(wintypes.HANDLE(-1), None, 4, 0, size, name)
            if not self.mapping or ctypes.get_last_error() == 183:
                raise OSError('Could not create the preview mapping.')
            ctypes.set_last_error(0)
            self.lock = self.api.CreateMutexW(None, False, name + '-lock')
            if not self.lock or ctypes.get_last_error() == 183:
                raise OSError('Could not create the preview lock.')
            self.address = self.api.MapViewOfFile(self.mapping, 4, 0, 0, size)
            if not self.address:
                raise OSError('Could not map the preview pixels.')
        except OSError:
            self.close()
            raise

    def read(self, slot, frame_id, width, height):
        if (not self.address or type(slot) is not int or not 0 <= slot < self.SLOTS
                or not 0 < width * height <= self.MAX_PIXELS):
            return None
        status = self.api.WaitForSingleObject(self.lock, 0)
        if status not in (0, 0x80):
            return None
        try:
            if status == 0x80:  # A writer exited while holding this buffer.
                return None
            address = self.address + slot * self.SLOT_BYTES
            header = self.HEADER.unpack(ctypes.string_at(address, self.HEADER.size))
            if header != (frame_id, width, height):
                return None
            return ctypes.string_at(address + self.HEADER.size, width * height * 4)
        finally:
            self.api.ReleaseMutex(self.lock)

    def close(self):
        if self.address:
            self.api.UnmapViewOfFile(self.address)
            self.address = None
        if self.lock:
            self.api.CloseHandle(self.lock)
            self.lock = None
        if self.mapping:
            self.api.CloseHandle(self.mapping)
            self.mapping = None
