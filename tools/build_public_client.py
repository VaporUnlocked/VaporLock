"""Build the unrestricted native client using CMake and locally supplied SDKs."""
from pathlib import Path
import argparse
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generator', default=None, help='Optional installed CMake generator')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--onnx-root', type=Path)
    parser.add_argument('--dml-root', type=Path)
    parser.add_argument('--tensorrt-root', type=Path)
    parser.add_argument('--cuda-toolkit', type=Path)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error('--jobs must be between 1 and 64')
    root = Path(__file__).resolve().parents[1]
    output = root / 'build/windows-release'
    command = ['cmake', '-S', str(root), '-B', str(output),
               '-DVAPORLOCK_EDITION=community', '-DBUILD_TESTING=OFF']
    if args.generator:
        command += ['-G', args.generator]
    for option, value in (('ONNXRUNTIME_ROOT', args.onnx_root), ('ONNXRUNTIME_DML_ROOT', args.dml_root),
                          ('VAPORLOCK_TENSORRT_RTX_ROOT', args.tensorrt_root), ('CUDAToolkit_ROOT', args.cuda_toolkit)):
        if value:
            command.append(f'-D{option}={value.absolute()}')
    subprocess.run(command, check=True)
    subprocess.run(['cmake', '--build', str(output), '--config', 'Release', '--parallel', str(args.jobs)], check=True)
    print('Native build finished. Only backends whose SDKs were supplied are compiled; inspect the CMake output.')


if __name__ == '__main__':
    main()
