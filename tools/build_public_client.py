"""Build the unrestricted native client with optional native provider SDKs."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.request import urlopen
import zipfile


ORT_DML_VERSION = '1.24.4'
DIRECTML_VERSION = '1.15.4'
ORT_DML_INCLUDE = 'build/native/include/'
ORT_DML_NATIVE = 'runtimes/win-x64/native/'
ORT_DML_HEADERS = (
    'onnxruntime_c_api.h', 'onnxruntime_cxx_api.h', 'onnxruntime_cxx_inline.h',
    'onnxruntime_ep_c_api.h', 'onnxruntime_float16.h', 'dml_provider_factory.h',
)
ORT_DML_FILES = (
    *(ORT_DML_INCLUDE + header for header in ORT_DML_HEADERS),
    ORT_DML_NATIVE + 'onnxruntime.lib',
    ORT_DML_NATIVE + 'onnxruntime.dll',
    ORT_DML_NATIVE + 'onnxruntime_providers_shared.dll',
)
DIRECTML_FILES = ('include/DirectML.h', 'bin/x64-win/DirectML.dll')


def restore_nuget(cache, name, version, required):
    """Restore the build inputs of one pinned package into the local cache."""
    destination = cache / f'{name}.{version}'

    def ready(folder):
        return all((folder / member).is_file() for member in required)

    if ready(destination):
        return destination
    if destination.exists():
        raise ValueError(f'Incomplete SDK cache at {destination}. Remove it or supply a local SDK root.')
    cache.mkdir(parents=True, exist_ok=True)
    package = name.lower()
    url = f'https://api.nuget.org/v3-flatcontainer/{package}/{version}/{package}.{version}.nupkg'
    print(f'Restoring {name} {version} from NuGet.org', flush=True)
    with tempfile.TemporaryDirectory(dir=cache) as scratch:
        scratch = Path(scratch)
        archive = scratch / 'package.nupkg'
        extracted = scratch / 'sdk'
        with urlopen(url, timeout=60) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
        with zipfile.ZipFile(archive) as package_zip:
            available = set(package_zip.namelist())
            if missing := sorted(set(required) - available):
                raise ValueError(f'{name} {version} is missing SDK files: {", ".join(missing)}')
            notices = ('LICENSE', 'LICENSE.txt', 'LICENSE-CODE.txt', 'ThirdPartyNotices.txt')
            for member in (*required, *(notice for notice in notices if notice in available)):
                target = extracted / member
                target.parent.mkdir(parents=True, exist_ok=True)
                with package_zip.open(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
        try:
            extracted.rename(destination)
        except OSError:
            if not ready(destination):
                raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generator', default=None, help='Optional installed CMake generator')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--onnx-root', type=Path)
    parser.add_argument('--native-directml', action='store_true', help='Restore pinned NuGet SDKs and require a native DirectML engine')
    parser.add_argument('--dml-root', type=Path, help='Extracted Microsoft.ML.OnnxRuntime.DirectML package')
    parser.add_argument('--directml-root', type=Path, help='Extracted Microsoft.AI.DirectML package')
    parser.add_argument('--tensorrt-root', type=Path)
    parser.add_argument('--cuda-toolkit', type=Path)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error('--jobs must be between 1 and 64')
    root = Path(__file__).resolve().parents[1]
    output = root / 'build/windows-release'
    require_directml = args.native_directml or args.dml_root is not None or args.directml_root is not None
    if args.native_directml:
        cache = root / 'build/dependencies'
        try:
            if args.dml_root is None:
                args.dml_root = restore_nuget(cache, 'Microsoft.ML.OnnxRuntime.DirectML', ORT_DML_VERSION,
                                             ORT_DML_FILES)
            if args.directml_root is None:
                args.directml_root = restore_nuget(cache, 'Microsoft.AI.DirectML', DIRECTML_VERSION, DIRECTML_FILES)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            parser.exit(1, f'Could not restore native DirectML SDKs: {error}\n'
                        'Supply extracted packages with --dml-root and --directml-root to build offline.\n')
    command = ['cmake', '-S', str(root), '-B', str(output),
               '-DCMAKE_BUILD_TYPE=Release', '-DVAPORLOCK_EDITION=community', '-DBUILD_TESTING=OFF',
               f'-DVAPORLOCK_REQUIRE_DIRECTML={"ON" if require_directml else "OFF"}']
    if args.generator:
        command += ['-G', args.generator]
    for option, value in (('ONNXRUNTIME_ROOT', args.onnx_root), ('ONNXRUNTIME_DML_ROOT', args.dml_root),
                          ('VAPORLOCK_DIRECTML_ROOT', args.directml_root),
                          ('VAPORLOCK_TENSORRT_RTX_ROOT', args.tensorrt_root), ('CUDAToolkit_ROOT', args.cuda_toolkit)):
        if value:
            command.append(f'-D{option}={value.absolute()}')
    subprocess.run(command, check=True)
    subprocess.run(['cmake', '--build', str(output), '--config', 'Release', '--parallel', str(args.jobs)], check=True)
    if require_directml:
        engine = output / 'Release/dml/VaporLockDml.exe'
        required = (engine, engine.with_name('onnxruntime.dll'), engine.with_name('onnxruntime_providers_shared.dll'),
                    engine.with_name('DirectML.dll'))
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            parser.exit(1, f'Native DirectML build is missing {", ".join(missing)} in {engine.parent}.\n')
        print(f'Native DirectML engine: {engine}')
    print('Native build finished. Only backends whose SDKs were supplied are compiled; inspect the CMake output.')


if __name__ == '__main__':
    main()
