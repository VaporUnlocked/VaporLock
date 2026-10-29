"""Optional, same-host YOLO comparison with explicit latency and box-quality gates.

Ultralytics is deliberately not an application dependency. Supply your own
already-trained local models and a reviewed held-out project; no model name is
resolved or downloaded automatically. Box-only YOLO cannot establish encasing
quality parity, so this report never certifies overall model superiority.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import html
import io
import json
import math
from pathlib import Path
import platform
import time

import numpy as np
from PIL import Image, ImageOps

from .data import Project, atomic_write, utc_now, write_json
from .evaluation import _score_image, _split_warnings, _summarize, match_detections
from .geometry import validate_annotation


class _FrozenImages:
    """Decode one shared frame at a time; do not keep a whole dataset in RAM."""
    def __init__(self, entries):
        self.entries = entries

    def __getitem__(self, index):
        path, expected_hash, size = self.entries[index]
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ValueError("An evaluation image changed after preflight; retry with stable files.")
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        if image.size != size:
            raise ValueError("An evaluation image changed dimensions.")
        return image


def _stats(values):
    return {"p50_ms": float(np.median(values)), "p95_ms": float(np.percentile(values, 95)),
            "mean_ms": float(np.mean(values)), "samples": len(values),
            "fps_from_p50": 1000. / float(np.median(values))}


def _device_key(value, current_cuda_index=0):
    name = str(value)
    if name == "cuda":
        return f"cuda:{current_cuda_index}"
    if name in {"cpu", "cpu:0"}:
        return "cpu"
    return name


def _resolved_yolo_contract(competitor, image_size, device, *, current_cuda_index=0):
    """Reject silently overridden exported shapes/precision before timing.

    This first adapter intentionally accepts native PyTorch checkpoints only.
    Static ONNX/TensorRT artifacts may override requested shape, batch or
    precision. A separate adapter must verify those contracts before comparison.
    """
    predictor = getattr(competitor, "predictor", None)
    model = getattr(predictor, "model", None)
    size = getattr(predictor, "imgsz", None)
    size = [size, size] if isinstance(size, int) else list(size or [])
    backend_format = getattr(model, "format", None)
    native = backend_format == "pt" if backend_format is not None else getattr(model, "pt", None) is True
    fp16 = getattr(model, "fp16", None)
    actual_device = str(getattr(model, "device", "unavailable"))
    if not native:
        raise ValueError("The initial competitor adapter requires a native PyTorch .pt model, "
                         "not an exported/static backend whose shape or precision may override the request.")
    if size != [image_size, image_size]:
        raise ValueError(f"YOLO resolved input {size} differs from the required {image_size}-pixel square.")
    if fp16 is not False:
        raise ValueError("YOLO did not resolve to verified FP32 execution; a precision-mismatched comparison is rejected.")
    if _device_key(actual_device, current_cuda_index) != _device_key(device, current_cuda_index):
        raise ValueError(f"YOLO device {actual_device} differs from Cloud device {device}.")
    arguments = getattr(predictor, "args", None)
    return {"backend": "native_pytorch", "backend_format": backend_format or "legacy_pt",
            "input_square": size, "batch": 1,
            "fp16": fp16, "device": actual_device, "task": competitor.task,
            "end_to_end_head": bool(getattr(model, "end2end", False)),
            "nms_setting": getattr(arguments, "nms", None),
            "agnostic_nms_setting": getattr(arguments, "agnostic_nms", None),
            "nms_note": "Each model uses its normal detection head/postprocessing. An end-to-end YOLO head may not apply IoU NMS; the requested iou is not proof of identical algorithms."}


def _box_quality(records, results):
    targets = predictions = matched = 0
    overlaps = []
    for record, result in zip(records, results):
        detections = result["detections"]
        matches = match_detections(record["annotations"], detections, .5)
        targets += len(record["annotations"])
        predictions += len(detections)
        matched += len(matches)
        overlaps.extend(m[2] for m in matches)
    return {"ground_truth_instances": targets, "predictions": predictions, "matches": matched,
            "precision": matched / predictions if predictions else None,
            "recall": matched / targets if targets else None,
            "f1": 2 * matched / (predictions + targets) if targets else None,
            "mean_matched_box_iou": float(np.mean(overlaps)) if overlaps else None,
            "false_positives_per_image": (predictions - matched) / len(records),
            "false_negatives": targets - matched}


def assess_target(cloud_timing, yolo_timing, cloud_quality, yolo_quality, *,
                  tolerance=.01, minimum_f1=.8):
    """Require meaningful detections as well as both median and tail speed.

    Tolerance is an absolute fraction (0.01 = one percentage point). The
    minimum F1 is a user-selectable benchmark criterion, not a model promise.
    """
    latency = (cloud_timing["p50_ms"] < yolo_timing["p50_ms"] and
               cloud_timing["p95_ms"] < yolo_timing["p95_ms"])
    names = ("precision", "recall", "f1", "mean_matched_box_iou")
    defined = all(q.get(key) is not None for q in (cloud_quality, yolo_quality) for key in names)
    quality = (all(cloud_quality[k] + tolerance >= yolo_quality[k] for k in names)
               and cloud_quality["false_positives_per_image"] <= yolo_quality["false_positives_per_image"] + tolerance
               and cloud_quality["f1"] >= minimum_f1) if defined else None
    return {"faster_at_p50_and_p95": latency, "box_quality_target_met": quality,
            "latency_and_box_target_met": latency and quality is True,
            "minimum_cloud_box_f1": minimum_f1, "absolute_quality_tolerance": tolerance,
            "encasing_quality_parity_measured": False, "overall_superiority_established": False}


def _measure_pairs(records, images, adapters, *, iterations, warmup, synchronize=lambda: None):
    names = tuple(adapters)
    values = {name: [] for name in names}
    outputs = {name: [] for name in names}
    counts = {name: [] for name in names}
    first_image = images[0]
    for name in names:
        for _ in range(warmup):
            adapters[name](first_image)
        synchronize()
    # Alternate order to avoid always giving either runtime the first call.
    for image_index, image in enumerate(images):
        for iteration in range(iterations):
            order = names if (image_index + iteration) % 2 == 0 else names[::-1]
            for name in order:
                synchronize()
                start = time.perf_counter()
                result = adapters[name](image)
                synchronize()
                values[name].append((time.perf_counter() - start) * 1000.)
                counts[name].append(len(result["detections"]))
                if iteration == 0:
                    outputs[name].append(result)
    return ({name: _stats(values[name]) for name in names}, outputs,
            {name: sorted(set(counts[name])) for name in names})


def benchmark_yolo(project_path, cloud_model, yolo_model, output_directory, *,
                   backend="torch", device="cpu", split="test", confidence=.35,
                   yolo_confidence=None, nms_iou=.5, max_detections=32,
                   iterations=5, warmup=3, cpu_threads=4, tolerance=.01,
                   minimum_f1=.8):
    if split not in {"val", "test"}:
        raise ValueError("Use a reviewed val or test split, never training images.")
    if backend not in {"torch", "onnx"}:
        raise ValueError("Competitive benchmarking supports torch or onnx Cloud execution.")
    for name, value in (("confidence", confidence), ("nms_iou", nms_iou),
                        ("tolerance", tolerance), ("minimum_f1", minimum_f1)):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be finite and between 0 and 1.")
    yolo_confidence = confidence if yolo_confidence is None else yolo_confidence
    if type(yolo_confidence) not in (int, float) or not math.isfinite(yolo_confidence) or not 0 <= yolo_confidence <= 1:
        raise ValueError("yolo_confidence must be finite and between 0 and 1.")
    for name, value, lower, upper in (("iterations", iterations, 2, 1000), ("warmup", warmup, 1, 100),
                                     ("max_detections", max_detections, 1, 256), ("cpu_threads", cpu_threads, 1, 64)):
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"{name} must be an integer from {lower} to {upper}.")
    competitor_path = Path(yolo_model).expanduser().resolve()
    if not competitor_path.is_file():
        raise FileNotFoundError("Supply an existing trained local YOLO model file; this command does not download weights.")
    if competitor_path.suffix.lower() != ".pt":
        raise ValueError("This optional comparison requires a native trained YOLO .pt checkpoint. "
                         "Exported competitors need an adapter that verifies their actual shape, batch and precision.")
    output = Path(output_directory).expanduser().resolve()
    for name in ("competition.json", "competition.html"):
        if (output / name).exists():
            raise FileExistsError("Choose a new competition output folder.")
    try:
        import torch
        import ultralytics
        from ultralytics import YOLO
        from ultralytics.cfg import DEFAULT_CFG_DICT
    except ImportError as exc:
        raise RuntimeError("The optional YOLO comparison needs Ultralytics in this Python environment. "
                           "Install it separately with python -m pip install ultralytics, then retry.") from exc
    from .comparison import _model_identity
    from .runtime import CloudRuntime
    project = Project.open(project_path)
    records = copy.deepcopy(project.training_records(split))
    if not records:
        raise ValueError(f"No complete reviewed {split} images are available.")
    if output == project.root or output == project.root / "images" or (project.root / "images") in output.parents:
        raise ValueError("Use a separate comparison output folder outside the image store.")
    warnings = _split_warnings(project, split)
    if warnings:
        raise ValueError("Capture groups overlap evaluation splits. Separate them before benchmarking quality.")
    if project.manifest.get("synthetic"):
        warnings.append("Synthetic project: results do not establish player recognition on game footage.")
    image_entries, hashes = [], []
    evaluation_hashes = set()
    for record in records:
        for annotation in record["annotations"]:
            errors = validate_annotation(annotation, record["width"], record["height"], require_complete=True)
            if errors:
                raise ValueError(f"Invalid reviewed labels in {record['id']}: " + "; ".join(errors))
        raw = project.image_path(record).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        if image.size != (record["width"], record["height"]):
            raise ValueError("A project image changed dimensions; re-import it before benchmarking.")
        image_entries.append((project.image_path(record), digest, image.size))
        hashes.append({"image_id": record["id"], "sha256": digest})
        evaluation_hashes.add(digest)
    for other_split in {"train", "val", "test"} - {split}:
        for record in project.training_records(other_split):
            if hashlib.sha256(project.image_path(record).read_bytes()).hexdigest() in evaluation_hashes:
                raise ValueError("An identical image occurs across dataset splits; remove split leakage.")
    images = _FrozenImages(image_entries)
    old_threads = torch.get_num_threads()
    torch.set_num_threads(cpu_threads)
    try:
        from threadpoolctl import threadpool_limits
        pools = threadpool_limits(limits=cpu_threads)
    except ImportError:
        pools = contextlib.nullcontext()
        warnings.append("NumPy BLAS thread pools could not be explicitly limited.")
    try:
        with pools:
            identity = _model_identity(cloud_model, backend)
            yolo_hash = hashlib.sha256(competitor_path.read_bytes()).hexdigest()
            runtime = CloudRuntime(cloud_model, backend=backend, device=device, onnx_threads=cpu_threads)
            resolved = str(runtime.device)
            if backend == "torch":
                # Pin an indexless CUDA request to the device that actually
                # received the model before another library selects a GPU.
                runtime.device = next(runtime.network.parameters()).device
                resolved = str(runtime.device)
            elif resolved == "cuda":
                # Cloud ONNX explicitly uses provider device_id=0 by default.
                resolved = "cuda:0"
                runtime.device = resolved
            current_cuda_index = torch.cuda.current_device() if resolved.startswith("cuda") else 0
            requested_yolo_device = _device_key(resolved, current_cuda_index)
            if _model_identity(cloud_model, backend)["sha256"] != identity["sha256"]:
                raise ValueError("Cloud model changed while loading.")
            competitor = YOLO(str(competitor_path))
            if competitor.task not in {"detect", "segment"}:
                raise ValueError("This comparison needs a YOLO detection or instance-segmentation model.")
            if hashlib.sha256(competitor_path.read_bytes()).hexdigest() != yolo_hash:
                raise ValueError("YOLO model changed while loading.")
            kwargs = dict(imgsz=runtime.config.input_size, rect=False, conf=yolo_confidence,
                          iou=nms_iou, max_det=max_detections, classes=[0], device=requested_yolo_device,
                          verbose=False, save=False, augment=False)
            # Support both current and older installed official Ultralytics APIs.
            kwargs.update({"quantize": 32} if "quantize" in DEFAULT_CFG_DICT else {"half": False})
            def cloud_predict(image):
                return runtime.predict(image, confidence=confidence, nms_iou=nms_iou,
                                       max_detections=max_detections, debug=True)
            def yolo_predict(image):
                results = competitor.predict(source=image, **kwargs)
                if len(results) != 1:
                    raise ValueError("YOLO returned a different batch size; exactly one frame is required.")
                result = results[0]
                boxes = result.boxes
                if boxes is None:
                    raise ValueError("YOLO output does not contain detection boxes.")
                coordinates = boxes.xyxy.detach().cpu().numpy()
                scores = boxes.conf.detach().cpu().numpy()
                classes = boxes.cls.detach().cpu().numpy()
                return {"detections": [{"class_id": int(label), "confidence": float(score),
                                         "primary_box": box.tolist()}
                                        for box, score, label in zip(coordinates, scores, classes)
                                        if int(label) == 0]}
            # Predictor setup is lazy. Resolve and verify the actual backend
            # once outside timing; requested flags alone are insufficient.
            yolo_predict(images[0])
            yolo_contract = _resolved_yolo_contract(competitor, runtime.config.input_size, resolved,
                current_cuda_index=current_cuda_index)
            # Ultralytics CPU device setup may reset Torch's global thread
            # count. Reapply the benchmark's declared setting after setup.
            torch.set_num_threads(cpu_threads)
            synchronize = (lambda: torch.cuda.synchronize(torch.device(resolved))) if resolved.startswith("cuda") else (lambda: None)
            timing, outputs, counts = _measure_pairs(records, images,
                {"cloud": cloud_predict, "yolo": yolo_predict}, iterations=iterations,
                warmup=warmup, synchronize=synchronize)
            measured_threads = torch.get_num_threads()
            if measured_threads != cpu_threads:
                raise ValueError("A backend changed the Torch thread count during measurement; "
                                 "the controlled comparison cannot be accepted.")
            quality = {name: _box_quality(records, rows) for name, rows in outputs.items()}
            cloud_shape = _summarize([_score_image(record, result) for record, result
                                     in zip(records, outputs["cloud"])], split, confidence)
            target = assess_target(timing["cloud"], timing["yolo"], quality["cloud"], quality["yolo"],
                                   tolerance=tolerance, minimum_f1=minimum_f1)
            report = {"release": "3.2.0", "created_utc": utc_now(), "split": split,
                "images_evaluated": len(records), "image_sha256": hashes,
                "label_fingerprint": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(),
                "environment": {"python": platform.python_version(), "platform": platform.platform(),
                    "torch": torch.__version__, "ultralytics": ultralytics.__version__,
                    "device": resolved, "cpu_threads": measured_threads,
                    "gpu": torch.cuda.get_device_name(torch.device(resolved)) if resolved.startswith("cuda") else None},
                "cloud_model": identity, "yolo_model": {"name": competitor_path.name, "sha256": yolo_hash,
                    "task": competitor.task, "class_0_name": competitor.names.get(0) if isinstance(competitor.names, dict) else competitor.names[0]},
                "yolo_resolved_contract": yolo_contract,
                "settings": {"input_square": runtime.config.input_size, "precision_requested": "float32",
                    "batch": 1, "cloud_backend": backend, "cloud_confidence": confidence,
                    "yolo_prediction_arguments": kwargs, "iterations_per_image": iterations, "warmup": warmup},
                "timing": timing, "box_quality": quality, "cloud_encasing_quality": cloud_shape,
                "detection_counts_observed": counts, "target": target,
                "notes": warnings + [
                    "Time includes each public prediction call, preprocessing, model work, postprocessing, transfers, and box conversion. File/model loading, annotation scoring, capture, and drawing are excluded.",
                    "Both models see identical decoded RGB images, batch one, and a full square of the Cloud model input size; interpolation and internal pipelines are model-specific.",
                    "The YOLO adapter verifies native PyTorch, actual predictor shape, FP32 mode and device after lazy setup. It rejects exported competitors; requested flags alone do not prove those contracts.",
                    "Cloud timings include encasings. YOLO detection supplies boxes; segmentation model latency includes its masks, but cross-model mask-quality parity is not scored here.",
                    "Confidence values need not be equally calibrated. Freeze each model's chosen validation threshold before an untouched test comparison.",
                    "This tests these artifacts and software paths on this host; it is not a comparison with every optimized TensorRT, C++, or CUDA deployment.",
                    "The quality minimum and tolerance are explicit benchmark criteria, not statistical confidence intervals or a general accuracy guarantee."]}
    finally:
        torch.set_num_threads(old_threads)
    write_json(output / "competition.json", report)
    rows = "".join(f"<tr><td>{name}</td><td>{timing[name]['p50_ms']:.3f} ms</td><td>{timing[name]['p95_ms']:.3f} ms</td><td>{html.escape(str(quality[name]['f1']))}</td></tr>" for name in ("cloud", "yolo"))
    document = "<!doctype html><meta charset='utf-8'><title>Cloud / YOLO comparison</title><style>body{font:16px/1.6 system-ui;max-width:1000px;margin:40px auto;padding:24px;color:#173240}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #ccd8df;text-align:left}pre{white-space:pre-wrap;background:#eef4f6;padding:16px}</style><h1>Cloud / YOLO comparison</h1><p>Measured on identical held-out images. Box quality and encasing quality have different scopes.</p><table><tr><th>Runtime</th><th>Median latency</th><th>95th percentile</th><th>Box F1 at IoU 0.5</th></tr>" + rows + "</table><h2>Explicit target result</h2><pre>" + html.escape(json.dumps(target, indent=2)) + "</pre><h2>Scope</h2><ul>" + "".join("<li>" + html.escape(note) + "</li>" for note in report["notes"]) + "</ul><p><a href='competition.json'>Complete settings, hashes, scores and measurements</a></p>"
    atomic_write(output / "competition.html", document.encode())
    return report
