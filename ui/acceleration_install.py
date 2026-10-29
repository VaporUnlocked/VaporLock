"""Install optional ONNX Runtime acceleration files into one Vapor Lock copy."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile


CUDA_ORT_VERSION = "1.29.0"
DML_ORT_VERSION = "1.24.4"
TENSORRT_RTX_VERSION = "1.6.1.120"
# This URL is an NVIDIA download resolver, not an expiring asset URL. NVIDIA
# creates a fresh, short-lived redirect for each install request.
TENSORRT_RTX_WINDOWS_URL = (
    "https://developer.nvidia.com/downloads/trt/rtx_sdk/secure/1.6/"
    "TensorRT-RTX-1.6.1.120-Windows-amd64-cuda-12.9-Release-external.zip"
)
TENSORRT_RTX_SHA256 = "a688478af9e7b824f4918ef3b3038c3aca6f1ea84ff30a52d7ba8196c65a5a5a"
NUGET_URL = "https://api.nuget.org/v3-flatcontainer/{package}/{version}/{package}.{version}.nupkg"
# Immutable SHA-512 package values taken from the NuGet catalog records for
# these exact versions. A changed package is rejected before any DLL is copied
# into the local engine folder.
NUGET_SHA512 = {
    ("microsoft.ml.onnxruntime.directml", "1.24.4"): "ZjOyv495vhfVXoTJp2utRyn8ir1TFIvCj0B9JP8QZGBXS0oFx+lY7ZCZqSdnVShQX6EtxYi2T3VOtYXcmBTV4A==",
    ("microsoft.ai.directml", "1.15.4"): "/edn9WkEq8kP1T9l2HKckYq39uPF4ezdR5kI/AK0U1zysIYPerKsubcx1suAm3LD1dTQKFP7j16gIqR7xE7yhQ==",
    ("microsoft.ml.onnxruntime.gpu.windows", "1.29.0"): "HxKZw+u5TUp6tRSvQWCM8QVioCZt1BYRU4JmesKyGjjWyIbvkFVDUzZiOwlp+gtq2xtgzvjRnZRi8zavgZZzDg==",
}


def report(message: str) -> None:
    print(json.dumps({"message": message}), flush=True)


def download_package(package: str, version: str, destination: Path) -> None:
    report(f"Downloading {package} {version}...")
    normalized_name = package.lower()
    expected_hash = NUGET_SHA512.get((normalized_name, version))
    if not expected_hash:
        raise RuntimeError(f"No integrity hash is recorded for {package} {version}.")
    request = urllib.request.Request(NUGET_URL.format(package=normalized_name, version=version), headers={"User-Agent": "Vapor-Lock/0.0.2"})
    with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
    hasher = hashlib.sha512()
    with destination.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    actual_hash = base64.b64encode(hasher.digest()).decode("ascii")
    if not hmac.compare_digest(actual_hash, expected_hash):
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"The downloaded {package} package did not match its expected integrity hash.")


def extract_native(package: Path, destination: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        members = [name for name in archive.namelist() if name.startswith("runtimes/win-x64/native/") and not name.endswith("/")]
        if not members:
            raise RuntimeError("The downloaded package did not contain Windows x64 native files.")
        destination.mkdir(parents=True, exist_ok=True)
        for member in members:
            target = destination / Path(member).name
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def extract_file(package: Path, member: str, destination: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        try:
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
        except KeyError as error:
            raise RuntimeError(f"The downloaded package did not contain {member}.") from error


def install_cuda(root: Path) -> None:
    from dependency_install_lock import _installation_lock
    from runtime_generations import prepare_cuda
    with _installation_lock(root):
        prepare_cuda(root, report=report)
    report("CUDA runtime files are installed and verified. Select CUDA, then Start to use them.")


def install_dml(root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="vaporlock-dml-") as temporary:
        temporary_root = Path(temporary)
        package = temporary_root / "onnxruntime-directml.nupkg"
        destination = root / "engine" / "dml"
        download_package("Microsoft.ML.OnnxRuntime.DirectML", DML_ORT_VERSION, package)
        extract_native(package, destination)
        directml = temporary_root / "directml.nupkg"
        download_package("Microsoft.AI.DirectML", "1.15.4", directml)
        extract_file(directml, "bin/x64-win/DirectML.dll", destination / "DirectML.dll")
    if not (root / "engine" / "dml" / "onnxruntime.dll").is_file() or not (root / "engine" / "dml" / "DirectML.dll").is_file():
        raise RuntimeError("The DirectML provider was not installed correctly.")
    report("DirectML acceleration installed.")


def download_tensor_rt_rtx(destination: Path) -> None:
    report(f"Downloading NVIDIA TensorRT RTX {TENSORRT_RTX_VERSION}...")
    # A cache-busting query guarantees NVIDIA returns a newly signed redirect
    # rather than a stale cached redirect after its short validity window.
    url = f"{TENSORRT_RTX_WINDOWS_URL}?cachebust={int(__import__('time').time())}"
    request = urllib.request.Request(url, headers={"User-Agent": "Vapor-Lock/0.0.8"})
    with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
    hasher = hashlib.sha256()
    with destination.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    actual_hash = hasher.hexdigest()
    if not hmac.compare_digest(actual_hash, TENSORRT_RTX_SHA256):
        destination.unlink(missing_ok=True)
        raise RuntimeError("The downloaded TensorRT RTX runtime did not match its expected integrity hash.")


def install_tensor_rt_rtx(root: Path) -> None:
    destination = root / "engine" / "trt"
    with tempfile.TemporaryDirectory(prefix="vaporlock-tensorrt-rtx-") as temporary:
        archive_path = Path(temporary) / "tensorrt-rtx.zip"
        download_tensor_rt_rtx(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            wanted = {
                "TensorRT-RTX-1.6.1.120/bin/tensorrt_rtx_1_6.dll": "tensorrt_rtx_1_6.dll",
                "TensorRT-RTX-1.6.1.120/bin/tensorrt_onnxparser_rtx_1_6.dll": "tensorrt_onnxparser_rtx_1_6.dll",
            }
            destination.mkdir(parents=True, exist_ok=True)
            for member, filename in wanted.items():
                try:
                    with archive.open(member) as source, (destination / filename).open("wb") as output:
                        shutil.copyfileobj(source, output)
                except KeyError as error:
                    raise RuntimeError("The TensorRT RTX archive did not contain its required Windows runtime files.") from error
    manifest = {
        "runtime": "TensorRT RTX",
        "version": TENSORRT_RTX_VERSION,
        "archive_sha256": TENSORRT_RTX_SHA256,
    }
    (destination / "runtime.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if not all((destination / name).is_file() for name in ("tensorrt_rtx_1_6.dll", "tensorrt_onnxparser_rtx_1_6.dll")):
        raise RuntimeError("TensorRT RTX was not installed correctly.")
    report("TensorRT RTX acceleration installed.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("cuda", "dml", "trt"), required=True)
    parser.add_argument("--root", required=True)
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    try:
        if arguments.provider == "cuda":
            install_cuda(root)
        elif arguments.provider == "dml":
            install_dml(root)
        else:
            install_tensor_rt_rtx(root)
    except Exception as error:
        report(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
