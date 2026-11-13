"""Build the unrestricted native client with optional native provider SDKs."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.request import urlopen
import zipfile


ORT_DML_VERSION = '1.24.4'
ORT_BASE_PACKAGE = 'Microsoft.ML.OnnxRuntime.Gpu.Windows'
ORT_BASE_VERSION = '1.29.0'
ORT_BASE_SHA256 = 'b9ebf51477be65146c1268d69eaecc2f8c81564340ed7197d42a7b75c1d4edf7'
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
ORT_BASE_FILES = (
    *('buildTransitive/native/include/' + header for header in (
        'onnxruntime_c_api.h', 'onnxruntime_cxx_api.h', 'onnxruntime_cxx_inline.h',
        'onnxruntime_ep_c_api.h', 'onnxruntime_error_code.h', 'onnxruntime_float16.h',
    )),
    ORT_DML_NATIVE + 'onnxruntime.lib',
    ORT_DML_NATIVE + 'onnxruntime.dll',
    ORT_DML_NATIVE + 'onnxruntime_providers_shared.dll',
)
DIRECTML_FILES = ('include/DirectML.h', 'bin/x64-win/DirectML.dll')
TENSORRT_DLLS = ('tensorrt_rtx_1_6.dll', 'tensorrt_onnxparser_rtx_1_6.dll')
TENSORRT_SDK_FILES = ('include/NvInfer.h', 'include/NvOnnxParser.h',
                      *(f'lib/{Path(name).stem}.lib' for name in TENSORRT_DLLS),
                      *(f'bin/{name}' for name in TENSORRT_DLLS))


def restore_nuget(cache, name, version, required, *, local_archive=None, sha256=None):
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
    with tempfile.TemporaryDirectory(dir=cache) as scratch:
        scratch = Path(scratch)
        extracted = scratch / 'sdk'
        if local_archive is not None and local_archive.is_file():
            archive = local_archive
            print(f'Restoring {name} {version} from the local CUDA download cache', flush=True)
        else:
            archive = scratch / 'package.nupkg'
            print(f'Restoring {name} {version} from NuGet.org', flush=True)
            with urlopen(url, timeout=60) as response, archive.open('wb') as output:
                shutil.copyfileobj(response, output)
        if sha256 is not None:
            with archive.open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != sha256:
                    raise ValueError(f'{name} {version} does not match the expected SHA-256.')
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
    parser.add_argument('--onnx-root', type=Path, help='Extracted ONNX Runtime Windows SDK for CPU/CUDA')
    parser.add_argument('--synthetic-only', action='store_true', help='Build the base engine for test signals without an ONNX Runtime SDK')
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
    cache = root / 'build/dependencies'
    if args.synthetic_only and (args.onnx_root or args.tensorrt_root):
        parser.error('--synthetic-only cannot be used with --onnx-root or --tensorrt-root')
    if args.tensorrt_root is not None:
        missing = [name for name in TENSORRT_SDK_FILES if not (args.tensorrt_root / name).is_file()]
        if missing:
            parser.error(f'TensorRT RTX SDK at {args.tensorrt_root} is missing {", ".join(missing)}.')
    if not args.synthetic_only and args.onnx_root is None:
        try:
            args.onnx_root = restore_nuget(
                cache, ORT_BASE_PACKAGE, ORT_BASE_VERSION, ORT_BASE_FILES, sha256=ORT_BASE_SHA256,
                local_archive=root / f'acceleration/cuda/cache/ort-gpu-{ORT_BASE_VERSION}.nupkg',
            )
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            parser.exit(1, f'Could not restore native ONNX Runtime: {error}\n'
                        'Supply an extracted package with --onnx-root to build offline.\n')
    require_directml = args.native_directml or args.dml_root is not None or args.directml_root is not None
    if args.native_directml:
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
               f'-DVAPORLOCK_REQUIRE_ONNXRUNTIME={"OFF" if args.synthetic_only else "ON"}',
               f'-DVAPORLOCK_REQUIRE_DIRECTML={"ON" if require_directml else "OFF"}']
    if args.synthetic_only:
        command.append('-DONNXRUNTIME_ROOT=')
    if args.generator:
        command += ['-G', args.generator]
    for option, value in (('ONNXRUNTIME_ROOT', args.onnx_root), ('ONNXRUNTIME_DML_ROOT', args.dml_root),
                          ('VAPORLOCK_DIRECTML_ROOT', args.directml_root),
                          ('VAPORLOCK_TENSORRT_RTX_ROOT', args.tensorrt_root), ('CUDAToolkit_ROOT', args.cuda_toolkit)):
        if value:
            command.append(f'-D{option}={value.absolute()}')
    subprocess.run(command, check=True)
    subprocess.run(['cmake', '--build', str(output), '--config', 'Release', '--parallel', str(args.jobs)], check=True)
    if not args.synthetic_only:
        print('Native CPU engine built with ONNX Runtime. CUDA also requires its runtime files from Model Library.')
    if require_directml:
        engine = output / 'Release/dml/VaporLockDml.exe'
        required = (engine, engine.with_name('onnxruntime.dll'), engine.with_name('onnxruntime_providers_shared.dll'),
                    engine.with_name('DirectML.dll'))
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            parser.exit(1, f'Native DirectML build is missing {", ".join(missing)} in {engine.parent}.\n')
        print(f'Native DirectML engine: {engine}')
    if args.tensorrt_root is not None:
        engine = output / 'Release/trt/VaporLockTensorRt.exe'
        required = (engine, engine.with_name('onnxruntime.dll'), engine.with_name('onnxruntime_providers_shared.dll'),
                    *(engine.with_name(name) for name in TENSORRT_DLLS))
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            parser.exit(1, f'Native TensorRT RTX build is missing {", ".join(missing)} in {engine.parent}.\n')
        print(f'Native TensorRT RTX engine: {engine}')
    print('Native build finished. Only backends whose SDKs were supplied are compiled; inspect the CMake output.')


if __name__ == '__main__':
    main()
