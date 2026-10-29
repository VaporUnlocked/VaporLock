"""Optional local Ultralytics training worker for Vapor Lock.

The worker keeps its large Python packages outside the executable in the
selected product copy. It never persists a Platform API key and does not send
anything remotely unless the caller deliberately supplies one for that run.
"""

from __future__ import annotations

import argparse
import json
import math
import yaml
import os
from pathlib import Path
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from datetime import datetime
from training_security import (MAX_RECORD, Redactor, clean_environment, load_extra_arguments,
                               read_credential, scrub_environment, validate_platform, write_credential)

_redactor = Redactor()


def emit(event: str, **payload: object) -> None:
    print(json.dumps(_redactor.payload({"type": event, **payload}), separators=(",", ":")), flush=True)


RUNTIME_SCHEMA_VERSION = 1
RUNTIME_STATE_NAME = "runtime.json"
PYTORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
PYTORCH_CUDA_VERSION = "2.11.0+cu128"
TORCHVISION_CUDA_VERSION = "0.26.0+cu128"
PYTORCH_CPU_VERSION = "2.11.0"
TORCHVISION_CPU_VERSION = "0.26.0"
ULTRALYTICS_VERSION = "8.4.156"
EMBEDDED_PYTHON_VERSION = "3.12.10"
EMBEDDED_PYTHON_URL = f"https://www.python.org/ftp/python/{EMBEDDED_PYTHON_VERSION}/python-{EMBEDDED_PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
_DLL_DIRECTORIES: list[object] = []


def training_root(root: Path) -> Path:
    directory = root / "runtime" / "training"
    from runtime_generations import plain
    plain(directory)
    return directory


def runtime_state_path(root: Path) -> Path:
    return training_root(root) / RUNTIME_STATE_NAME


def load_runtime_state(root: Path) -> dict[str, object] | None:
    state_path = runtime_state_path(root)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("schema_version") != RUNTIME_SCHEMA_VERSION:
        return None
    runtime_name = state.get("python_home", state.get("packages"))
    if (not isinstance(runtime_name, str) or runtime_name in ('', '.', '..')
            or Path(runtime_name).name != runtime_name or ':' in runtime_name
            or '/' in runtime_name or '\\' in runtime_name):
        return None
    from runtime_generations import plain
    candidate = training_root(root) / runtime_name
    plain(candidate)
    if not candidate.resolve().is_relative_to(training_root(root).resolve()):
        raise RuntimeError('The training runtime leaves this installation.')
    return state


def package_root(root: Path) -> Path:
    state = load_runtime_state(root)
    if state is not None:
        if "python_home" in state:
            return training_root(root) / str(state["python_home"]) / "python" / "Lib" / "site-packages"
        return training_root(root) / str(state["packages"])
    # This legacy folder is intentionally not considered installed unless it
    # contains all required runtime modules. Older builds created it before pip
    # had completed, which made a failed install look healthy.
    return training_root(root) / "python-packages"


def safe_remove_generated(path: Path, root: Path) -> None:
    """Remove only installer-owned staging/runtime folders inside this product."""
    base = training_root(root).resolve()
    candidate = path.resolve()
    allowed = ("python-packages-", "python-runtime-", "python-installing-")
    if candidate.parent != base or not candidate.name.startswith(allowed):
        raise RuntimeError("Refusing to remove a path outside the local training runtime.")
    shutil.rmtree(candidate, ignore_errors=True)


def runtime_modules_present(packages: Path) -> bool:
    if not packages.is_dir():
        return False
    return all((packages / name).exists() for name in ("torch", "torchvision", "ultralytics"))


def prepare_runtime(root: Path) -> Path:
    from runtime_generations import plain
    packages = package_root(root)
    plain(packages)
    # Optional packages run in the private interpreter only. They must never
    # precede bundled worker supervision modules on the trusted worker path.
    return packages


def runtime_python(root: Path) -> Path | None:
    state = load_runtime_state(root)
    if state is None or "python_home" not in state:
        return None
    executable = training_root(root) / str(state["python_home"]) / "python" / "python.exe"
    from runtime_generations import plain
    plain(executable)
    return executable if executable.is_file() else None


def training_runner_path() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / "training_runner.py"


def verify_embedded_runtime(python: Path, require_cuda: bool) -> tuple[bool, str]:
    script = (
        "import timeit, torch, torchvision, ultralytics; from ultralytics import YOLO; "
        "assert torch.__version__; assert torchvision.__version__; assert ultralytics.__version__; "
        "assert callable(YOLO); "
        + ("assert torch.cuda.is_available(), 'CUDA is unavailable'; " if require_cuda else "")
        + "print(f'Torch {torch.__version__}; Ultralytics {ultralytics.__version__}')"
    )
    try:
        completed = subprocess.run(
            [str(python), "-I", "-c", script], capture_output=True, text=True, timeout=30,
            env=clean_environment(dict(os.environ)),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"The private Python runtime could not start: {error}"
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return False, detail[-1] if detail else "The private Python runtime could not import its training packages."
    return True, completed.stdout.strip() or "The private training runtime is ready."


def pip_common_arguments() -> list[str]:
    return [
        "install", "--disable-pip-version-check", "--no-cache-dir", "--only-binary=:all:",
        "--progress-bar", "off", "--timeout", "30", "--retries", "2", "--no-warn-script-location", "--upgrade",
    ]


def download_file(url: str, destination: Path, phase: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "VaporLock-TrainingRuntime/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
            expected = int(response.headers.get("Content-Length", "0") or 0)
            received = 0
            last_update = 0.0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                received += len(chunk)
                now = time.monotonic()
                if now - last_update >= 1.0:
                    if expected:
                        emit("install-progress", message=f"{phase} ({received * 100 // expected}% downloaded).")
                    else:
                        emit("install-progress", message=f"{phase} ({received // (1024 * 1024)} MB downloaded).")
                    last_update = now
    except Exception as error:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"{phase} failed: {error}") from error


def run_installer_command(command: list[str], phase: str) -> None:
    """Run the private runtime's installer with visible activity and a finite network policy."""
    environment = os.environ.copy()
    environment.update({"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1", "PIP_DEFAULT_TIMEOUT": "30"})
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as error:
        raise RuntimeError(f"{phase} could not start: {error}") from error

    transcript: list[str] = []
    lines: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            lines.put(raw.rstrip())
        lines.put(None)

    reader = threading.Thread(target=read_output, name="VaporLockTrainingInstallLog", daemon=True)
    reader.start()
    started = time.monotonic()
    stream_finished = False
    while not stream_finished or process.poll() is None:
        try:
            line = lines.get(timeout=5)
        except queue.Empty:
            elapsed = int(time.monotonic() - started)
            emit("install-progress", message=f"{phase} ({elapsed}s elapsed).")
            continue
        if line is None:
            stream_finished = True
            continue
        if line:
            transcript.append(line)
            emit("install-progress", message=f"{phase}: {line[-220:]}")
    if process.wait() != 0:
        detail = transcript[-1] if transcript else "The installer ended without a diagnostic."
        raise RuntimeError(f"{phase} failed: {detail}")


def prepare_embedded_python(staging: Path) -> Path:
    archive = staging / "python-embed.zip"
    download_file(EMBEDDED_PYTHON_URL, archive, "Downloading the private Python runtime")
    python_home = staging / "python"
    python_home.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as package:
            base = python_home.resolve()
            for member in package.infolist():
                if not (base / member.filename).resolve().is_relative_to(base):
                    raise RuntimeError("The private Python runtime archive contains an unsafe path.")
            package.extractall(python_home)
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeError(f"The private Python runtime archive is invalid: {error}") from error
    pth = python_home / "python312._pth"
    if not pth.is_file():
        raise RuntimeError("The private Python runtime is missing its module-path configuration.")
    pth.write_text("python312.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8")
    python = python_home / "python.exe"
    if not python.is_file():
        raise RuntimeError("The private Python runtime is missing python.exe.")
    get_pip = staging / "get-pip.py"
    download_file(GET_PIP_URL, get_pip, "Downloading the package installer")
    run_installer_command([str(python), str(get_pip), "--no-warn-script-location"], "Installing the package manager")
    return python_home


def nvidia_available() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def install_runtime(root: Path) -> None:
    runtime = training_root(root)
    runtime.mkdir(parents=True, exist_ok=True)
    previous_state_path = runtime_state_path(root)
    previous_state = previous_state_path.read_text(encoding="utf-8") if previous_state_path.is_file() else None
    previous_runtime = load_runtime_state(root)
    activated_destination: Path | None = None
    staging = runtime / "python-installing-runtime"
    if staging.exists():
        safe_remove_generated(staging, root)
    staging.mkdir(parents=True, exist_ok=False)
    use_cuda = nvidia_available()
    profile = "NVIDIA CUDA" if use_cuda else "CPU"
    emit("installing", message=f"Preparing the {profile} Ultralytics training runtime. This first install downloads several large packages.")
    try:
        python_home = prepare_embedded_python(staging)
        python = python_home / "python.exe"
        emit("install-progress", message=f"Downloading {profile} Torch and TorchVision.")
        torch_arguments = [str(python), "-m", "pip", *pip_common_arguments()]
        if use_cuda:
            torch_arguments.extend([
                "--index-url", "https://pypi.org/simple", "--extra-index-url", PYTORCH_CUDA_INDEX,
                f"torch=={PYTORCH_CUDA_VERSION}", f"torchvision=={TORCHVISION_CUDA_VERSION}",
            ])
        else:
            torch_arguments.extend([f"torch=={PYTORCH_CPU_VERSION}", f"torchvision=={TORCHVISION_CPU_VERSION}"])
        run_installer_command(torch_arguments, f"Downloading {profile} Torch and TorchVision")

        emit("install-progress", message="Downloading Ultralytics and its training dependencies.")
        dependencies = [
            f"ultralytics=={ULTRALYTICS_VERSION}", "cloudpickle>=3.1.1", "filelock>=3.16.1",
            "matplotlib>=3.3.0", "numpy>=1.23.0", "nvidia-ml-py>=12.0.0",
            "opencv-python!=4.13.0.90,>=4.7.0", "pillow>=7.1.2", "polars>=0.20.0",
            "psutil>=5.8.0", "pyyaml>=5.3.1", "requests>=2.23.0", "ultralytics-thop>=2.1.6",
            "ultralytics-platform>=0.1.45", "onnx>=1.16.0", "onnxslim>=0.1.82",
        ]
        run_installer_command([str(python), "-m", "pip", *pip_common_arguments(), "--no-deps", dependencies[0]], "Downloading Ultralytics")
        # Resolve the non-Torch dependencies separately. This prevents pip from
        # replacing the deliberately selected CUDA Torch wheel with a CPU wheel.
        run_installer_command([str(python), "-m", "pip", *pip_common_arguments(), *dependencies[1:]], "Downloading training dependencies")

        flavor = "cuda" if use_cuda else "cpu"
        destination = runtime / f"python-runtime-{flavor}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        verified, detail = verify_embedded_runtime(python, use_cuda)
        if not verified:
            raise RuntimeError(detail)
        staging.rename(destination)
        activated_destination = destination
        state = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "python_home": destination.name,
            "profile": profile,
            "torch": PYTORCH_CUDA_VERSION if use_cuda else PYTORCH_CPU_VERSION,
            "ultralytics": ULTRALYTICS_VERSION,
        }
        temporary_state = runtime / f"{RUNTIME_STATE_NAME}.next"
        temporary_state.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temporary_state, runtime_state_path(root))
        if previous_runtime is not None:
            previous_name = str(previous_runtime.get("python_home", previous_runtime.get("packages", "")))
            previous_packages = runtime / previous_name
            if previous_name and previous_packages != destination and previous_packages.exists():
                safe_remove_generated(previous_packages, root)
        emit("ready", location=str(destination), profile=profile, detail=detail)
    except Exception:
        if staging.exists():
            safe_remove_generated(staging, root)
        # A failed verification must not leave the product pointed at a new,
        # incomplete runtime. Restore the last known state before surfacing it.
        if activated_destination is not None and activated_destination.exists():
            safe_remove_generated(activated_destination, root)
        if previous_state is None:
            runtime_state_path(root).unlink(missing_ok=True)
        else:
            runtime_state_path(root).write_text(previous_state, encoding="utf-8")
        raise


def runtime_status(root: Path) -> None:
    packages = prepare_runtime(root)
    state = load_runtime_state(root) or {}
    python = runtime_python(root)
    if not runtime_modules_present(packages):
        verified, detail = False, "The runtime folder does not contain Torch, TorchVision, and Ultralytics."
    elif python is None:
        verified, detail = False, "The private Python runtime is missing."
    else:
        verified, detail = verify_embedded_runtime(python, state.get("profile") == "NVIDIA CUDA")
    emit("runtime", ready=verified, location=str(packages), profile=str(state.get("profile", "")), detail=detail)


def run_private_training(python: Path, runner: Path, configuration: dict[str, object], job_directory: Path, key: str = '') -> None:
    """Supervise a private-runtime training process and forward structured events."""
    if not python.is_file():
        raise RuntimeError("The private training Python runtime is missing. Repair the training runtime and try again.")
    if not runner.is_file():
        raise RuntimeError("The packaged training runner is missing. Repair or rebuild Vapor Lock.")
    job_directory.mkdir(parents=True, exist_ok=True)
    job_id = f"{os.getpid()}-{secrets.token_hex(6)}"
    configuration_path = job_directory / f"training-{job_id}.json"
    runtime_dataset_path: Path | None = None
    runtime_configuration = dict(configuration)
    source_dataset = Path(str(configuration.get("data", ""))).resolve()
    if source_dataset.is_file() and source_dataset.suffix.lower() in {".yaml", ".yml"}:
        runtime_dataset_path = job_directory / f"dataset-{job_id}.yaml"
        write_runtime_dataset_descriptor(source_dataset, runtime_dataset_path)
        training_arguments = dict(runtime_configuration.get("train_args", {}))
        training_arguments["data"] = str(runtime_dataset_path)
        runtime_configuration["train_args"] = training_arguments
    configuration_path.write_text(json.dumps(runtime_configuration, indent=2), encoding="utf-8")
    environment = clean_environment(dict(os.environ))
    environment["PYTHONUNBUFFERED"] = "1"
    working_directory = Path(str(configuration["root"])).resolve() / "training"
    working_directory.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            [str(python), "-I", "-u", str(runner), "--config", str(configuration_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            cwd=str(working_directory),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as error:
        configuration_path.unlink(missing_ok=True)
        if runtime_dataset_path is not None:
            runtime_dataset_path.unlink(missing_ok=True)
        raise RuntimeError('The private training process could not start.') from None

    lines: queue.Queue[str | None] = queue.Queue(maxsize=128)
    stopping = threading.Event()
    redactor = Redactor(key)

    def enqueue(line):
        while not stopping.is_set():
            try:
                lines.put(line, timeout=.1)
                return
            except queue.Full:
                pass

    def read_output() -> None:
        assert process.stdout is not None
        discarding = False
        try:
            while not stopping.is_set():
                raw = process.stdout.readline(MAX_RECORD + 1)
                if not raw:
                    break
                if len(raw) > MAX_RECORD or discarding:
                    discarding = not raw.endswith('\n')
                    if not discarding:
                        enqueue('[oversized training log omitted]')
                    continue
                enqueue(redactor.text(raw.rstrip('\r\n')))
        finally:
            enqueue(None)

    reader = threading.Thread(target=read_output, name="VaporLockTrainingOutput", daemon=True)
    reader.start()
    started = time.monotonic()
    last_heartbeat = started
    stream_finished = False
    last_detail = ""
    reported_error = ""
    completed = False
    try:
        assert process.stdin is not None
        # Popen text mode wraps the pipe; write the small protocol as bytes.
        write_credential(process.stdin.buffer, key)
        while not stream_finished or process.poll() is None:
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                if process.poll() is not None:
                    continue
                now = time.monotonic()
                if now - last_heartbeat >= 5:
                    elapsed = int(now - started)
                    emit(
                        "training-heartbeat",
                        elapsed_seconds=elapsed,
                        message=f"Training is active ({elapsed}s elapsed).",
                    )
                    last_heartbeat = now
                continue
            if line is None:
                stream_finished = True
                continue
            clean = line.strip()
            if not clean:
                continue
            try:
                payload = json.loads(clean)
            except (ValueError, RecursionError):
                last_detail = clean[-500:]
                emit("training-log", message=last_detail)
                continue
            if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
                last_detail = clean[-500:]
                emit("training-log", message=last_detail)
                continue
            if payload["type"] == "error":
                reported_error = str(payload.get("message", "Training failed."))
            elif payload["type"] == "trained":
                completed = True
            print(json.dumps(redactor.payload(payload), separators=(",", ":")), flush=True)
        return_code = process.wait()
        if return_code:
            raise RuntimeError(reported_error or last_detail or f"The private training process exited with code {return_code}.")
        if not completed:
            raise RuntimeError("The private training process stopped without completing or reporting an error.")
    finally:
        stopping.set()
        cleanup_failed = False
        try:
            if process.poll() is None:
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10, check=True)
                else:
                    process.kill()
                process.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            cleanup_failed = True
            # The worker retains its kill-on-close Job until process teardown,
            # including descendants that still hold a pipe after this fallback.
            try:
                process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass
        finally:
            reader.join(timeout=1)
            # Remove every owned job file independently. A failing pipe close
            # must not skip cleanup, nor block on a live reader's IO lock.
            for path in (configuration_path, runtime_dataset_path):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        cleanup_failed = True
            streams = (process.stdin,) if reader.is_alive() else (process.stdin, process.stdout)
            for stream in streams:
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        cleanup_failed = True
            if reader.is_alive():
                cleanup_failed = True
        if cleanup_failed:
            raise RuntimeError('Training cleanup could not complete; the owned worker process tree is stopping.') from None


def dataset_descriptor(data: Path) -> dict:
    value = yaml.safe_load(data.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("The dataset YAML must be a mapping.")
    return value


def dataset_value(data: Path, name: str):
    return dataset_descriptor(data).get(name, "")


def configured_dataset_root(data: Path) -> Path:
    root_value = dataset_value(data, "path") or "."
    if not isinstance(root_value, str):
        raise ValueError("The dataset path must be text.")
    return (data.parent / root_value).resolve()


def write_runtime_dataset_descriptor(source: Path, destination: Path) -> None:
    """Write an Ultralytics descriptor whose dataset root is unambiguous.

    Ultralytics resolves an existing relative `path` against its current
    working directory instead of the descriptor's directory. Vapor Lock uses
    the selected data.yaml as the authority and supplies an absolute root only
    to the private training process.
    """
    descriptor = dataset_descriptor(source)
    descriptor["path"] = configured_dataset_root(source).as_posix()
    destination.write_text(yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")


def relocate_legacy_weights(root: Path) -> int:
    """Move Ultralytics' former root-level cache into the training workspace."""
    legacy = root / "weights"
    if not legacy.is_dir():
        return 0
    destination = root / "training" / "weights"
    destination.mkdir(parents=True, exist_ok=True)
    moved = 0
    for item in legacy.iterdir():
        target = destination / item.name
        if target.exists():
            target = destination / f"{item.stem}-legacy-{secrets.token_hex(4)}{item.suffix}"
        try:
            shutil.move(str(item), str(target))
            moved += 1
        except OSError:
            continue
    try:
        legacy.rmdir()
    except OSError:
        pass
    return moved


def dataset_images(data: Path, split: str) -> list[Path]:
    root = configured_dataset_root(data)
    split_value = dataset_value(data, split)
    if not split_value:
        raise ValueError(f"The dataset configuration does not define {split}:.")
    values = split_value if isinstance(split_value, list) else [split_value]
    images = set()
    extensions = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"The {split} split must contain paths.")
        location = (root / value).resolve()
        if location.is_dir():
            images.update(item.resolve() for item in location.rglob("*") if item.is_file() and item.suffix.lower() in extensions)
        elif location.is_file() and location.suffix.lower() == ".txt":
            for line in location.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                value = line.strip()
                image = (location.parent / value[2:]).resolve() if value.startswith("./") else (root / value).resolve()
                if not image.is_file() or image.suffix.lower() not in extensions:
                    raise ValueError(f"The {split} image list contains a missing or unsupported image: {image}")
                images.add(image)
        else:
            raise ValueError(f"The configured {split} images folder or list does not exist: {location}")
    return sorted(images)


def dataset_class_count(data: Path) -> int:
    names = dataset_value(data, "names")
    if isinstance(names, dict):
        try:
            keys = sorted(int(key) for key in names)
        except (ValueError, TypeError) as exc:
            raise ValueError("Dataset class IDs must be integers.") from exc
        if keys != list(range(len(names))):
            raise ValueError("Dataset class IDs must be consecutive, beginning at zero.")
    elif not isinstance(names, list):
        return 0
    return len(names)


def image_label_path(image: Path) -> Path:
    parts = list(image.parts)
    indices = [index for index, value in enumerate(parts) if value.casefold() == "images"]
    if indices:
        parts[indices[-1]] = "labels"
    return Path(*parts).with_suffix(".txt")


def label_problem(label: Path, class_count: int) -> str | None:
    if not label.is_file():
        return None  # Missing labels represent a valid background image.
    for line_number, raw in enumerate(label.read_text(encoding="utf-8").splitlines(), 1):
        values = raw.split()
        if not values:
            continue
        if len(values) != 5 and (len(values) < 7 or len(values) % 2 == 0):
            return f"{label.name}:{line_number} has an invalid YOLO box or polygon shape"
        try:
            class_id = int(values[0])
            coordinates = [float(value) for value in values[1:]]
        except ValueError:
            return f"{label.name}:{line_number} contains a non-numeric value"
        if class_id < 0 or (class_count and class_id >= class_count):
            return f"{label.name}:{line_number} uses class {class_id}, outside the declared class range"
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in coordinates):
            return f"{label.name}:{line_number} contains coordinates outside 0 to 1"
        if len(values) == 5 and (coordinates[2] <= 0.0 or coordinates[3] <= 0.0):
            return f"{label.name}:{line_number} has a zero-sized box"
    return None


def nvidia_memory() -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if result.returncode:
            return None
        first = result.stdout.splitlines()[0].split(",")
        return int(first[0].strip()), int(first[1].strip())
    except (FileNotFoundError, IndexError, ValueError, subprocess.SubprocessError):
        return None


def run_preflight(args: argparse.Namespace) -> dict[str, object]:
    data = Path(args.data).resolve()
    if not data.is_file() or data.name.lower() != "data.yaml":
        raise ValueError("Choose a prepared dataset build containing data.yaml.")
    train_images = dataset_images(data, "train")
    validation_images = dataset_images(data, "val")
    root = configured_dataset_root(data)
    if not train_images:
        raise ValueError("The prepared dataset has no training images.")
    if not validation_images:
        raise ValueError("The prepared dataset has no validation images.")
    if set(train_images) & set(validation_images):
        raise ValueError("Training and validation splits overlap. Export separate splits before training.")
    class_count = dataset_class_count(data)
    warnings: list[str] = []
    problems: list[str] = []
    labeled = 0
    for image in train_images + validation_images:
        label = image_label_path(image)
        if label.is_file() and label.stat().st_size:
            labeled += 1
        problem = label_problem(label, class_count)
        if problem:
            problems.append(problem)
            if len(problems) >= 12:
                break
    if not class_count:
        warnings.append("No class names were found in data.yaml.")
    if labeled == 0:
        warnings.append("No non-empty label files were found; this would train only a background model.")
    elif labeled < len(train_images):
        warnings.append(f"{len(train_images) - labeled} training images are background samples without annotations.")
    total_bytes = sum(item.stat().st_size for item in train_images + validation_images)
    free = shutil.disk_usage(data.parent).free
    recommended_free = max(2 * 1024**3, total_bytes * 3)
    if free < recommended_free:
        warnings.append("Available disk space is low for checkpoints and cached training data.")
    gpu = nvidia_memory()
    recommendation = "Use automatic batch size first."
    if gpu:
        total, available = gpu
        recommendation = "Use automatic batch size first." if available >= 4096 else "Start with batch size 1 or a smaller image size."
        if available < 2048:
            warnings.append("Less than 2 GB of NVIDIA memory is currently free; close other GPU applications before training.")
    payload: dict[str, object] = {
        "train_images": len(train_images), "validation_images": len(validation_images), "labeled": labeled,
        "classes": class_count, "dataset_bytes": total_bytes, "free_bytes": free,
        "gpu_total_mb": gpu[0] if gpu else 0, "gpu_free_mb": gpu[1] if gpu else 0,
        "recommendation": recommendation, "warnings": warnings, "problems": problems,
    }
    emit("preflight", **payload)
    return payload


def run_training(args: argparse.Namespace, key: str = '') -> None:
    validate_platform(args.platform_project.strip(), key)
    extra = load_extra_arguments(args.extra)
    root = Path(args.root).resolve()
    relocated_weights = relocate_legacy_weights(root)
    if relocated_weights:
        emit("training-log", message=f"Moved {relocated_weights} cached base-weight item(s) into training/weights.")
    packages = prepare_runtime(root)
    python = runtime_python(root)
    if not runtime_modules_present(packages) or python is None:
        raise RuntimeError("Install or repair the local training runtime before starting a training run.")
    model_source = str(args.model).strip()
    if not model_source.startswith("ul://"):
        model_path = Path(model_source).resolve()
        if not model_path.is_file() or model_path.suffix.lower() not in {".pt", ".yaml", ".yml"}:
            raise ValueError("Choose a trainable .pt checkpoint or .yaml model definition from the model library. ONNX files are inference exports and cannot be trained.")
        model_source = str(model_path)
    data = Path(args.data).resolve()
    if not data.is_file() or data.name.lower() != "data.yaml":
        raise ValueError("Choose a prepared dataset build containing data.yaml.")
    # Training cannot recover from malformed labels. Run the same local checks
    # used by the UI before Ultralytics allocates GPU memory or starts a run.
    preflight = run_preflight(argparse.Namespace(data=str(data)))
    problems = [str(item) for item in preflight["problems"]]
    if problems:
        raise ValueError("Training preflight found label issues: " + "; ".join(problems[:3]))
    output_name = "".join(character if character.isalnum() or character in "-_" else "-" for character in args.name.strip())[:64].strip("-_") or "training-run"
    local_project = root / "training" / "runs"
    batch_value: object = -1 if args.batch.strip().lower() == "auto" else int(args.batch)
    device_value: object = None if args.device.strip().lower() == "auto" else args.device
    train_args: dict[str, object] = {
        "data": str(data), "epochs": args.epochs, "imgsz": args.imgsz, "batch": args.batch,
        "device": device_value, "workers": args.workers, "patience": args.patience,
        "optimizer": args.optimizer, "lr0": args.lr0, "lrf": args.lrf,
        "weight_decay": args.weight_decay, "warmup_epochs": args.warmup_epochs,
        "close_mosaic": args.close_mosaic, "cache": False if args.cache.strip().lower() in {"false", "none", "off", "0"} else args.cache, "amp": args.amp,
        "pretrained": args.pretrained, "cos_lr": args.cos_lr, "seed": args.seed,
        "deterministic": args.deterministic, "fraction": args.fraction, "save": True,
        "project": str(local_project), "name": output_name, "exist_ok": False,
    }
    train_args["batch"] = batch_value
    train_args.update(extra)
    platform_project = args.platform_project.strip()
    if platform_project:
        train_args["project"] = platform_project
    configuration: dict[str, object] = {
        "schema_version": 1,
        "root": str(root),
        "model": model_source,
        "data": str(data),
        "name": output_name,
        "platform_project": platform_project,
        "train_args": train_args,
        "imgsz": args.imgsz,
        "export_onnx": args.export_onnx,
        "dynamic_onnx": args.dynamic_onnx,
    }
    emit("training-phase", phase="runtime", message="Starting the verified private training runtime.")
    try:
        run_private_training(python, training_runner_path(), configuration, training_root(root) / "jobs", key)
    finally:
        relocate_legacy_weights(root)


def main() -> int:
    global _redactor
    scrub_environment()
    from worker_process_tree import own_process_tree
    own_process_tree()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install")
    commands.add_parser("status")
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--data", required=True)
    train = commands.add_parser("train")
    train.add_argument("--model", required=True)
    train.add_argument("--data", required=True)
    train.add_argument("--name", default="training-run")
    train.add_argument("--platform-project", default="")
    train.add_argument("--epochs", type=int, default=100)
    train.add_argument("--imgsz", type=int, default=640)
    train.add_argument("--batch", default="auto")
    train.add_argument("--device", default="0")
    train.add_argument("--workers", type=int, default=8)
    train.add_argument("--patience", type=int, default=100)
    train.add_argument("--optimizer", default="auto")
    train.add_argument("--lr0", type=float, default=0.01)
    train.add_argument("--lrf", type=float, default=0.01)
    train.add_argument("--weight-decay", type=float, default=0.0005)
    train.add_argument("--warmup-epochs", type=float, default=3.0)
    train.add_argument("--close-mosaic", type=int, default=10)
    train.add_argument("--cache", default="disk")
    train.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    train.add_argument("--pretrained", action=argparse.BooleanOptionalAction, default=True)
    train.add_argument("--cos-lr", action=argparse.BooleanOptionalAction, default=False)
    train.add_argument("--seed", type=int, default=0)
    train.add_argument("--deterministic", action=argparse.BooleanOptionalAction, default=True)
    train.add_argument("--fraction", type=float, default=1.0)
    train.add_argument("--export-onnx", action=argparse.BooleanOptionalAction, default=True)
    train.add_argument("--dynamic-onnx", action=argparse.BooleanOptionalAction, default=True)
    train.add_argument("--extra", default="")
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    try:
        if arguments.command == "install":
            install_runtime(root)
        elif arguments.command == "status":
            runtime_status(root)
        elif arguments.command == "preflight":
            run_preflight(arguments)
        else:
            key = read_credential(sys.stdin.buffer)
            _redactor = Redactor(key)
            run_training(arguments, key)
    except Exception as error:
        emit("error", message=str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
