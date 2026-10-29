"""Run one Ultralytics job inside Vapor Lock's private Python runtime.

This file is bundled as data beside VaporLockTrainingTools.exe. The frozen
worker supervises it, while the installed private interpreter owns PyTorch,
Ultralytics, and any Windows data-loader child processes.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import time
# The embedded interpreter's ._pth deliberately omits script directories.
# Only this code-owned extracted bundle directory supplies our sibling helper.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from training_security import (Redactor, SafeLogStream, configure_platform, read_credential,
                               scrub_environment, validate_platform, validate_runner_arguments)

_redactor = Redactor()
_transport = None


def emit(event: str, **payload: object) -> None:
    print(json.dumps(_redactor.payload({"type": event, **payload}), separators=(",", ":")),
          file=_transport or sys.stdout, flush=True)


def load_configuration(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("The training job configuration is invalid.")
    return payload


def numeric_loss(trainer: object) -> float | None:
    try:
        loss = getattr(trainer, "tloss")
        if hasattr(loss, "mean"):
            loss = loss.mean()
        if hasattr(loss, "item"):
            loss = loss.item()
        return float(loss)
    except (AttributeError, TypeError, ValueError):
        return None


def run(configuration: dict[str, object], key: str = '') -> None:
    train_args = validate_runner_arguments(configuration)
    project = str(configuration.get('platform_project', '')).strip()
    validate_platform(project, key)
    configure_platform(project, key)
    from ultralytics import YOLO

    root = Path(str(configuration["root"])).resolve()
    model_source = str(configuration["model"])
    output_name = str(configuration["name"])
    expected_epochs = int(train_args.get("epochs", 0) or 0)
    started = time.monotonic()

    emit("training-phase", phase="model", message="Loading the training model and dataset.")
    model = YOLO(model_source)

    def epoch_complete(trainer: object) -> None:
        current = int(getattr(trainer, "epoch", -1)) + 1
        total = int(getattr(trainer, "epochs", expected_epochs) or expected_epochs)
        loss = numeric_loss(trainer)
        message = f"Epoch {current}/{total} complete" if total else f"Epoch {current} complete"
        if loss is not None:
            message += f" - loss {loss:.5f}"
        emit(
            "training-progress",
            phase="epoch",
            current=current,
            total=total,
            loss=loss,
            elapsed_seconds=int(time.monotonic() - started),
            message=message,
        )

    model.add_callback("on_fit_epoch_end", epoch_complete)
    emit("training", name=output_name, dataset=Path(str(configuration["data"])).parent.name,
         platform=bool(configuration.get("platform_project")))
    results = model.train(**train_args)
    trainer = getattr(model, "trainer", None)
    fallback = Path(str(train_args["project"])) / output_name
    save_dir = Path(str(getattr(trainer, "save_dir", getattr(results, "save_dir", fallback))))
    best = save_dir / "weights" / "best.pt"
    if not best.is_file():
        last = save_dir / "weights" / "last.pt"
        if last.is_file():
            best = last
        else:
            raise RuntimeError("Training ended without producing a checkpoint.")

    model_library = root / "models"
    model_library.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    checkpoint = model_library / f"{output_name}_{stamp}.pt"
    shutil.copy2(best, checkpoint)
    output: dict[str, object] = {"checkpoint": str(checkpoint), "run": str(save_dir)}

    if bool(configuration.get("export_onnx", True)):
        emit("training-phase", phase="export", message="Exporting the best checkpoint to ONNX.")
        exported = Path(YOLO(str(best)).export(
            format="onnx",
            imgsz=int(configuration["imgsz"]),
            dynamic=bool(configuration.get("dynamic_onnx", True)),
        ))
        onnx_path = model_library / f"{output_name}_{stamp}.onnx"
        shutil.copy2(exported, onnx_path)
        output["onnx"] = str(onnx_path)
        try:
            import onnx

            onnx.checker.check_model(onnx_path)
            output["onnx_validated"] = True
        except Exception:
            output["onnx_validated"] = False
            output["onnx_validation_error"] = 'The exported ONNX structure could not be verified.'

    platform_project = str(configuration.get("platform_project", "")).strip()
    if platform_project:
        # A project path alone does not establish that a remote run exists.
        state = getattr(trainer, 'platform', None)
        output['platform_connected'] = bool(isinstance(state, dict) and state.get('model_id'))
        if not output['platform_connected']:
            emit('training-log', message='Local training completed, but Platform synchronization was not confirmed.')
    emit("trained", **output)


def main() -> int:
    global _redactor, _transport
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args()
    scrub_environment()
    _transport = sys.stdout
    stdout, stderr = sys.stdout, sys.stderr
    streams = []
    try:
        key = read_credential(sys.stdin.buffer)
        _redactor = Redactor(key)
        streams = [SafeLogStream(lambda line: emit('training-log', message=line), _redactor) for _ in range(2)]
        sys.stdout, sys.stderr = streams
        run(load_configuration(Path(arguments.config).resolve()), key)
    except Exception:
        # SDK exceptions may include arbitrary request headers or configuration.
        emit("error", message='Training failed. Check the model, dataset, runtime and Platform access; sanitized progress is shown above.')
        return 1
    finally:
        for stream in streams:
            stream.finish()
        sys.stdout, sys.stderr = stdout, stderr
        _transport = None
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
