"""Fixed local CMake output discovery for a developer-owned source checkout."""
from functools import lru_cache
import ctypes
from ctypes import wintypes
import mmap
from pathlib import Path
import os

_ONNX_STUB_MESSAGE = 'ONNX Runtime was not found when Vapor Lock was built.'.encode('utf-16-le')
_CAPABILITY_RESOURCE = 201
_RT_RCDATA = 10
_ONNX_SUPPORT = 1
_CUDA_SUPPORT = 2
_LOSSLESS_PREVIEW_SUPPORT = 4


def source_engine(root, provider, *, require_model=False):
    root=Path(root)
    name='VaporLockTensorRt.exe' if provider=='trt' else 'VaporLockDml.exe' if provider=='dml' else 'VaporLock.exe'
    child='trt' if provider=='trt' else 'dml' if provider=='dml' else ''
    for configuration in ('windows-release','windows-cuda-release','windows-debug','windows-cuda-debug'):
        base=root/'build'/configuration
        for directory in (base/('Debug' if 'debug' in configuration else 'Release'),base):
            candidate=directory/child/name
            if candidate.is_file() and _plain(candidate):
                if provider == 'cuda' or require_model:
                    if not provider_ready(candidate, provider, require_model=require_model):
                        continue
                return candidate
    return None

def _plain(path):
    return all(not part.is_symlink() and not (getattr(part.lstat(),'st_file_attributes',0)&0x400)
               for part in (path,*path.parents) if part.exists())

def _resource_flags(path):
    if os.name != 'nt':
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LoadLibraryExW.argtypes = (wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD)
    kernel.LoadLibraryExW.restype = wintypes.HMODULE
    kernel.FindResourceW.argtypes = (wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p)
    kernel.FindResourceW.restype = wintypes.HANDLE
    kernel.SizeofResource.argtypes = (wintypes.HMODULE, wintypes.HANDLE)
    kernel.SizeofResource.restype = wintypes.DWORD
    kernel.LoadResource.argtypes = (wintypes.HMODULE, wintypes.HANDLE)
    kernel.LoadResource.restype = wintypes.HANDLE
    kernel.LockResource.argtypes = (wintypes.HANDLE,)
    kernel.LockResource.restype = ctypes.c_void_p
    kernel.FreeLibrary.argtypes = (wintypes.HMODULE,)
    kernel.FreeLibrary.restype = wintypes.BOOL
    # A data-file mapping reads resources without executing code or loading imports.
    image = kernel.LoadLibraryExW(str(path), None, 2)
    if not image:
        return None
    try:
        resource = kernel.FindResourceW(image, _CAPABILITY_RESOURCE, _RT_RCDATA)
        if resource and kernel.SizeofResource(image, resource) == 2:
            data = kernel.LoadResource(image, resource)
            address = kernel.LockResource(data) if data else None
            if address:
                return int.from_bytes(ctypes.string_at(address, 2), 'little')
    finally:
        kernel.FreeLibrary(image)
    return None


@lru_cache(maxsize=64)
def _engine_flags(path, _size, _mtime, _ctime):
    flags = _resource_flags(path)
    if flags is not None:
        return flags
    # Older builds have no resource. Their synthetic-only detector contains
    # this compiled diagnostic while model-enabled builds omit it.
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            return 0
        offset = int.from_bytes(header[60:64], 'little')
        stream.seek(offset)
        if stream.read(6) != b'PE\x00\x00\x64\x86':
            return 0
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as image:
            return 0 if image.find(_ONNX_STUB_MESSAGE) >= 0 else _ONNX_SUPPORT | _CUDA_SUPPORT


def has_model_support(path, provider):
    if path is None:
        return False
    try:
        if not path.is_file() or not _plain(path):
            return False
        identity = path.stat()
        flags = _engine_flags(path, identity.st_size, identity.st_mtime_ns, identity.st_ctime_ns)
    except OSError:
        return False
    needed = _ONNX_SUPPORT | _CUDA_SUPPORT if provider == 'cuda' else _ONNX_SUPPORT
    return (flags & needed) == needed


def supports_lossless_preview(path):
    try:
        identity = path.stat()
        flags = _engine_flags(path, identity.st_size, identity.st_mtime_ns, identity.st_ctime_ns)
    except OSError:
        return False
    return bool(flags & _LOSSLESS_PREVIEW_SUPPORT)


def provider_ready(path, provider, *, require_model=False):
    if path is None:return False
    if provider == 'cuda' or (provider == 'cpu' and require_model):
        if not has_model_support(path, provider):
            return False
    needed={'cpu':('onnxruntime.dll',) if require_model else (), 'dml':('onnxruntime.dll','DirectML.dll'),
            'cuda':('onnxruntime.dll','onnxruntime_providers_shared.dll','onnxruntime_providers_cuda.dll'),
            'trt':('onnxruntime.dll','onnxruntime_providers_shared.dll','tensorrt_rtx_1_6.dll',
                   'tensorrt_onnxparser_rtx_1_6.dll')}.get(provider)
    return needed is not None and all((path.parent/name).is_file() and _plain(path.parent/name) for name in needed)

def native_environment(directory, inherited):
    env={k:v for k,v in inherited.items() if not k.upper().startswith(('PYTHON','PIP_','CUDA_PATH','CUDNN_PATH'))}
    windows=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    env['PATH']=os.pathsep.join((str(directory),str(windows/'System32'),str(windows)))
    return env
