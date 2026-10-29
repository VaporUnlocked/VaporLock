"""CUDA-only worker entry; verify dependency generation before importing ORT."""
import json
import os
from pathlib import Path
import sys


def prepare_runtime(root):
    from runtime_generations import resolve_cuda
    runtime=resolve_cuda(root)
    environment=runtime.environment(dict(os.environ))
    os.environ.clear();os.environ.update(environment)
    handle=os.add_dll_directory(str(runtime.directory))
    return runtime,handle


def main():
    root=Path(sys.executable).resolve().parent.parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    try:
        runtime,handle=prepare_runtime(root)
    except (OSError,ValueError,RuntimeError):
        print(json.dumps({'type':'provider_error','backend':'cuda','code':'CUDA_RUNTIME_UNAVAILABLE'}),flush=True)
        return 1
    try:
        import onnxruntime as ort
        ort.preload_dlls(cuda=True,cudnn=True,msvc=False,directory=str(runtime.directory))
        from dataset_worker import main as worker_main
    except (ImportError,OSError,RuntimeError):
        print(json.dumps({'type':'provider_error','backend':'cuda','code':'CUDA_BINDING_UNAVAILABLE'}),flush=True)
        handle.close()
        return 1
    try:
        return worker_main()
    finally:handle.close()


if __name__=='__main__':raise SystemExit(main())
