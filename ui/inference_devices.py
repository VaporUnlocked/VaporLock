"""Read-only native device ordinals; no model, SDK download or GPU context creation."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import uuid


class GUID(C.Structure):
    _fields_ = [('raw', C.c_byte*16)]
    @classmethod
    def parse(cls, text): return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


def _method(pointer, index, result, *arguments):
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result, C.c_void_p, *arguments)(table[index])


def directml_devices():
    class Desc(C.Structure):
        _fields_ = [('name', W.WCHAR*128), ('vendor', W.UINT), ('device', W.UINT),
                    ('subsystem', W.UINT), ('revision', W.UINT), ('video', C.c_size_t),
                    ('system', C.c_size_t), ('shared', C.c_size_t), ('low', W.DWORD),
                    ('high', W.LONG), ('flags', W.UINT)]
    factory = C.c_void_p()
    dxgi = C.WinDLL('dxgi.dll', winmode=0x800)
    d3d = C.WinDLL('d3d12.dll', winmode=0x800)
    create = dxgi.CreateDXGIFactory1
    create.argtypes = [C.POINTER(GUID), C.POINTER(C.c_void_p)]; create.restype = W.LONG
    device = d3d.D3D12CreateDevice
    device.argtypes = [C.c_void_p, C.c_int, C.POINTER(GUID), C.c_void_p]; device.restype = W.LONG
    iid = GUID.parse('770aae78-f26f-4dba-a829-253c83d1b387')
    if create(C.byref(iid), C.byref(factory)) < 0: raise OSError('Device enumeration unavailable')
    found = []
    try:
        enumerate_adapter = _method(factory, 12, W.LONG, W.UINT, C.POINTER(C.c_void_p))
        for index in range(64):
            adapter = C.c_void_p(); status = enumerate_adapter(factory, index, C.byref(adapter))
            if status & 0xffffffff == 0x887A0002: break
            if status < 0: raise OSError('Device enumeration changed')
            try:
                desc = Desc()
                if _method(adapter, 10, W.LONG, C.POINTER(Desc))(adapter, C.byref(desc)) < 0:
                    raise OSError('Device description unavailable')
                iid_device = GUID.parse('189819f1-1db6-4b57-be54-1821339b85f7')
                if not desc.flags & 2 and device(adapter, 0xb000, C.byref(iid_device), None) >= 0:
                    found.append((index, desc.name))
            finally: _method(adapter, 2, W.ULONG)(adapter)
        if not _method(factory, 13, W.BOOL)(factory): raise OSError('Device enumeration changed')
        return tuple(found)
    finally: _method(factory, 2, W.ULONG)(factory)


def cuda_devices(*, tensorrt=False):
    # System32-only driver API, not an arbitrary CUDA_PATH or pip DLL.
    driver = C.WinDLL('nvcuda.dll', winmode=0x800)
    def fn(name, *types):
        f = getattr(driver, name); f.argtypes = list(types); f.restype = C.c_int; return f
    if fn('cuInit', C.c_uint)(0): raise OSError('CUDA enumeration unavailable')
    count = C.c_int()
    if fn('cuDeviceGetCount', C.POINTER(C.c_int))(C.byref(count)): raise OSError('CUDA enumeration unavailable')
    if not 0 <= count.value <= 64: raise OSError('CUDA device limit')
    found = []
    for index in range(count.value):
        dev = C.c_int(); name = C.create_string_buffer(256); major = C.c_int(); minor = C.c_int()
        if (fn('cuDeviceGet', C.POINTER(C.c_int), C.c_int)(C.byref(dev), index)
                or fn('cuDeviceGetName', C.c_void_p, C.c_int, C.c_int)(name, len(name), dev)
                or fn('cuDeviceComputeCapability', C.POINTER(C.c_int), C.POINTER(C.c_int), C.c_int)(C.byref(major), C.byref(minor), dev)):
            raise OSError('CUDA device description unavailable')
        # Both shipped NVIDIA runtimes require Turing or newer (CUDA13 / TRT RTX).
        if major.value*10+minor.value >= 75:
            found.append((index, name.value.decode('utf-8', errors='replace')))
    return tuple(found)


def devices(provider):
    if os.name != 'nt': return ()
    try:
        return directml_devices() if provider == 'dml' else cuda_devices(tensorrt=provider == 'trt')
    except (OSError, AttributeError): return ()
