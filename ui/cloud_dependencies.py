"""Fixed production admission; explicit separate private-alpha/dev layouts."""
from pathlib import Path
import sys

from cloud_contract import CloudInputError


def component_roots(kind):
    if kind not in ('cpu', 'directml', 'cuda', 'train-cpu', 'train-cuda'):
        raise ValueError('Unknown .cloud dependency component.')
    if getattr(sys, 'frozen', False):
        from cloud_production import component_root
        return component_root('dml' if kind == 'directml' else
            'cuda' if kind in ('cuda', 'train-cuda') else 'cpu'), component_root('common')
    root = Path(__file__).resolve().parents[1]
    common = root / '.venv' / 'Lib' / 'site-packages'
    component = ('.venv-cloud-dml' if kind == 'directml' else
                 '.venv-cloud-cuda' if kind in ('cuda', 'train-cuda') else '.venv-cloud-cpu')
    selected = root / component / 'Lib' / 'site-packages'
    return selected,common


def training_runtimes():
    def installed(kind):
        try:
            return (component_roots(kind)[0] / 'torch/__init__.py').is_file()
        except (OSError, ValueError, RuntimeError):
            return False
    return installed('train-cpu'), installed('train-cuda')


def training_install_help(kind):
    if getattr(sys, 'frozen', False):
        return ('Install the .cloud CUDA add-on or choose CPU.' if kind == 'train-cuda'
                else 'Restore the .cloud CPU training component from the core package.')
    flag = '-CloudCUDA' if kind == 'train-cuda' else '-CloudCPU'
    return f'Run .\\BuildCommunity.ps1 {flag} -SkipNativeBuild from the VaporLock folder.'


def activate_training(device):
    """Select one Torch installation. Automatic can fall back to installed CPU training."""
    import re
    if type(device) is not str or re.fullmatch(r'auto|cpu|cuda(?::[0-9]{1,2})?', device) is None:
        raise CloudInputError('Choose Automatic, CPU, or an NVIDIA CUDA device for .cloud training.')
    cpu, cuda = training_runtimes()
    use_cpu = device == 'cpu' or (device == 'auto' and not cuda)
    kind = 'train-cpu' if use_cpu else 'train-cuda'
    if not (cpu if use_cpu else cuda):
        raise CloudInputError('.cloud training needs its PyTorch runtime. ' + training_install_help(kind))
    activate(kind)


def ready_for(provider):
    """Fixed payload presence only. Never import GPU libraries into the GUI."""
    kind={'cpu':'cpu','dml':'directml','cuda':'cuda'}.get(provider)
    if kind is None:return False
    try:
        selected,common=component_roots(kind)
        if getattr(sys, 'frozen', False):
            from cloud_production import worker_root
            needed = [worker_root() / 'VaporLockCloudTools.exe', selected / 'inventory.json', common / 'inventory.json']
        else:
            command()  # fixed interpreter, not an environment-selected executable
            needed=[common/'numpy/__init__.py',Path(__file__).with_name('cloud_worker.py')]
        needed+=([selected/'torch/__init__.py'] if kind=='cuda' else
                 [selected/'onnx/__init__.py',selected/'onnxruntime/capi/onnxruntime_pybind11_state.pyd'])
        return all(path.is_file() for path in needed)
    except (OSError,RuntimeError,ValueError,ImportError):return False


def activate(kind):
    if kind not in ('cpu', 'directml', 'cuda', 'train-cpu', 'train-cuda'):
        raise ValueError('Unknown .cloud dependency component.')
    if getattr(sys, 'frozen', False):
        from cloud_production import activate as admit
        return admit('dml' if kind == 'directml' else 'cuda' if kind in ('cuda','train-cuda') else 'cpu')
    selected,common=component_roots(kind)
    if not selected.is_dir() or not common.is_dir():
        raise CloudInputError('The selected .cloud runtime is missing. Prepare the source dependencies with BuildCommunity.ps1.')
    # No model/project paths or environment-selected import directories.
    sys.path[:0] = [str(selected), str(common)]


def activate_common():
    if getattr(sys, 'frozen', False):
        from cloud_production import activate as admit
        return admit('common')
    common = Path(__file__).resolve().parents[1] / '.venv/Lib/site-packages'
    if not (common / 'PIL/__init__.py').is_file():
        raise CloudInputError('Run .\\BuildCommunity.ps1 -InstallDependencies -SkipNativeBuild to prepare the source .cloud tools.')
    sys.path.insert(0, str(common))


def command(*, annotation=False):
    if getattr(sys, 'frozen', False):
        from cloud_production import invocation
        return invocation(annotation=annotation)
    root = Path(__file__).resolve().parents[1]
    # Windows venv python.exe is a redirector that creates another process.
    # Use the already-selected interpreter's base executable so the annotation
    # worker remains one process inside its existing one-process Job.
    executable = Path(getattr(sys, '_base_executable', sys.executable))
    if not executable.is_file():
        raise RuntimeError('The source .cloud Python runtime is missing. Run BuildCommunity.ps1 to prepare .venv.')
    script = 'cloud_annotation_worker.py' if annotation else 'cloud_worker.py'
    return [str(executable), '-I', '-S', '-B', '-u', str(root / 'ui' / script), '--gated']
