"""Install Vapor Lock's optional, isolated virtual Xbox controller bridge."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request


BRIDGE_VERSION = "1.22.0"
BRIDGE_FILENAME = "ViGEmBus_1.22.0_x64_x86_arm64.exe"
BRIDGE_URL = "https://github.com/nefarius/ViGEmBus/releases/download/v1.22.0/ViGEmBus_1.22.0_x64_x86_arm64.exe"
BRIDGE_SHA256 = "89220A7865076B342892F98865F3499FB7C4CFD673159E89D352C360FD014C6A"


def report(message: str) -> None:
    print(json.dumps({"message": message}), flush=True)


def service_present() -> bool:
    try:
        result = subprocess.run(
            ["sc.exe", "query", "ViGEmBus"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def download_destination(root: Path) -> Path:
    preferred = root / "controller-bridge"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred / BRIDGE_FILENAME
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "VaporLock-controller-bridge"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback / BRIDGE_FILENAME


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def download_verified(destination: Path) -> None:
    if destination.is_file() and sha256(destination) == BRIDGE_SHA256:
        report("Using the verified virtual-controller driver already downloaded for this Vapor Lock copy.")
        return
    temporary = destination.with_suffix(destination.suffix + ".part")
    report(f"Downloading the signed virtual-controller driver ({BRIDGE_VERSION})...")
    request = urllib.request.Request(BRIDGE_URL, headers={"User-Agent": "Vapor-Lock/0.0.3"})
    with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    if sha256(temporary) != BRIDGE_SHA256:
        raise RuntimeError("The downloaded controller-bridge installer did not match its published integrity hash.")
    temporary.replace(destination)


def run_elevated(installer: Path) -> int:
    # The driver is an explicit, optional Windows component. Start-Process with
    # RunAs provides the normal UAC consent flow and waits for its exit code.
    escaped = str(installer).replace("'", "''")
    command = (
        "$process = Start-Process -FilePath '" + escaped
        + "' -ArgumentList '/qn' -Verb RunAs -Wait -PassThru; exit $process.ExitCode"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.returncode


def install(root: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("The virtual Xbox controller bridge is available only on Windows.")
    if service_present():
        report("Virtual-controller driver is already installed and ready.")
        return
    destination = download_destination(root)
    download_verified(destination)
    report("Windows will ask for administrator approval to install the optional virtual-controller driver.")
    exit_code = run_elevated(destination)
    if exit_code not in {0, 3010, 1641}:
        raise RuntimeError(f"The virtual-controller driver installer exited with code {exit_code}.")
    if service_present():
        report("Virtual-controller driver installed. Vapor Lock can now create a separate Xbox controller when Virtual Xbox output is selected.")
        return
    if exit_code in {3010, 1641}:
        raise RuntimeError("The virtual-controller driver was installed but Windows must restart before it can be used.")
    raise RuntimeError("Windows did not make the virtual-controller driver available after installation.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    arguments = parser.parse_args()
    try:
        install(Path(arguments.root).resolve())
    except Exception as error:
        report(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
