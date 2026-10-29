"""Fixed local CMake output discovery for a developer-owned source checkout."""
from pathlib import Path
import os

def source_engine(root, provider):
    root=Path(root)
    name='VaporLockTensorRt.exe' if provider=='trt' else 'VaporLockDml.exe' if provider=='dml' else 'VaporLock.exe'
    child='trt' if provider=='trt' else 'dml' if provider=='dml' else ''
    for configuration in ('windows-release','windows-cuda-release','windows-debug','windows-cuda-debug'):
        base=root/'build'/configuration
        for directory in (base/('Debug' if 'debug' in configuration else 'Release'),base):
            candidate=directory/child/name
            if candidate.is_file() and _plain(candidate):return candidate
    return None

def _plain(path):
    return all(not part.is_symlink() and not (getattr(part.lstat(),'st_file_attributes',0)&0x400)
               for part in (path,*path.parents) if part.exists())

def provider_ready(path, provider):
    if path is None:return False
    needed={'cpu':(), 'dml':('onnxruntime.dll','DirectML.dll'),
            'cuda':('onnxruntime.dll','onnxruntime_providers_shared.dll','onnxruntime_providers_cuda.dll'),
            'trt':('tensorrt_rtx_1_6.dll','tensorrt_onnxparser_rtx_1_6.dll')}.get(provider)
    return needed is not None and all((path.parent/name).is_file() and _plain(path.parent/name) for name in needed)

def native_environment(directory, inherited):
    env={k:v for k,v in inherited.items() if not k.upper().startswith(('PYTHON','PIP_','CUDA_PATH','CUDNN_PATH'))}
    windows=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    env['PATH']=os.pathsep.join((str(directory),str(windows/'System32'),str(windows)))
    return env
