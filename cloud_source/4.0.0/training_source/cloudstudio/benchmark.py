"""Reproducible end-to-end measurements without claiming unmeasured speedups."""
from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
import platform
import statistics
import time


def _statistics(values):
    import numpy as np
    return {"mean": statistics.mean(values), "median": statistics.median(values),
            "p50": statistics.median(values),
            "p95": float(np.percentile(values, 95)), "minimum": min(values), "maximum": max(values)}


def _compare(reference, candidate, tolerance_pixels=0.02):
    import numpy as np
    from .geometry import box_iou
    a, b = reference["detections"], candidate["detections"]
    if len(a) != len(b):
        return {"passed": False, "reason": "Different final detection counts.", "reference_count": len(a), "candidate_count": len(b)}
    used = set()
    errors, box_errors, confidence_errors = [], [], []
    mask_disagreements, mask_ious = [], []
    mask_mode = ("empty_masks" in reference or "empty_masks" in candidate or
                 any(item.get("mask") is not None for item in a + b))
    for first in a:
        possibilities = [(box_iou(first["primary_box"], second["primary_box"]), index)
                         for index, second in enumerate(b) if index not in used]
        _, index = max(possibilities)
        used.add(index)
        second = b[index]
        if first["class_id"] != second["class_id"]:
            return {"passed": False, "reason": "Different detection class."}
        if first.get("mask") is not None or second.get("mask") is not None:
            if first.get("mask") is None or second.get("mask") is None:
                return {"passed": False, "reason": "Different encasing representations."}
            if first["mask"]["size"] != second["mask"]["size"]:
                return {"passed": False, "reason": "Different mask image dimensions."}
            from .evaluation import _local_mask, _local_mask_metrics
            height, width = first["mask"]["size"]
            metrics = _local_mask_metrics(_local_mask(first, width, height), _local_mask(second, width, height))
            mask_disagreements.append(metrics["background_pixels_included"] + metrics["foreground_pixels_missed"])
            mask_ious.append(metrics["iou"])
        elif len(first["wrapper"]) != len(second["wrapper"]):
            return {"passed": False, "reason": "Different class or wrapper point count."}
        else:
            p, q = np.asarray(first["wrapper"]), np.asarray(second["wrapper"])
            errors.append(float(np.max(np.abs(p - q))))
        box_errors.append(float(np.max(np.abs(np.asarray(first["primary_box"]) -
                                              np.asarray(second["primary_box"])))))
        confidence_errors.append(abs(first["confidence"] - second["confidence"]))
    max_error = max(errors, default=0.)
    max_confidence = max(confidence_errors, default=0.)
    max_box_error = max(box_errors, default=0.)
    passed = (max_error <= tolerance_pixels and max_box_error <= tolerance_pixels
              and max_confidence <= 1e-4 and reference["invalid_contours"] == candidate["invalid_contours"]
              and not any(mask_disagreements)
              and reference.get("empty_masks", 0) == candidate.get("empty_masks", 0))
    result = {"passed": bool(passed), "detections_compared": len(a),
            "maximum_vertex_error_pixels": max_error, "maximum_box_error_pixels": max_box_error,
            "maximum_confidence_difference": max_confidence,
            "vertex_tolerance_pixels": tolerance_pixels, "confidence_tolerance": 1e-4,
            "empty_output_caveat": "No final detections; raw-tensor parity tests are needed too." if not a else None}
    if mask_mode:
        result.update({"encasing_representation": "source_resolution_binary_mask",
                       "maximum_vertex_error_pixels": None, "vertex_tolerance_pixels": None,
                       "mask_pixel_tolerance": 0,
                       "maximum_mask_pixel_disagreement": max(mask_disagreements, default=0),
                       "minimum_mask_iou": min(mask_ious) if mask_ious else None,
                       "mask_note": "Authoritative projected pixels are compared exactly; approximate polygons are ignored."})
    return result


def _raw_outputs(runtime, image):
    """Exercise detector and learned contour tensors even on empty final output."""

    import numpy as np
    from .geometry import letterbox
    from .runtime import decode_boxes_numpy
    array, _ = letterbox(image, runtime.config.input_size)
    boxes = np.asarray([[.12, .15, .68, .92], [.35, .24, .82, .78]], dtype=np.float32)
    indices = np.zeros(len(boxes), dtype=np.int64)
    if runtime.backend == "torch":
        torch = runtime._torch
        with torch.inference_mode():
            features, logits, raw_boxes = runtime.network.detect(torch.from_numpy(array[None]).to(runtime.device))
            contours = runtime.network.wrap(features, torch.from_numpy(boxes).to(runtime.device),
                                             torch.from_numpy(indices).to(runtime.device))
            arrays = [item.float().cpu().numpy() for item in (features, logits, raw_boxes, contours)]
    else:
        features, logits, raw_boxes = runtime.network.detect(array[None])
        arrays = [features, logits, raw_boxes, runtime.network.wrap(features, boxes, indices)]
    return dict(zip(("features", "objectness_logits", "raw_boxes",
                     "guided_mask_logits" if runtime.config.is_mask else "guided_wrappers", "decoded_boxes"),
                    [*arrays, decode_boxes_numpy(arrays[2], runtime.config)]))


def _raw_parity(reference, candidate):
    import numpy as np
    fields = {}
    for name, expected in reference.items():
        actual = candidate[name]
        compatible = actual.shape == expected.shape
        passed = compatible and bool(np.allclose(actual, expected, rtol=1e-5, atol=5e-4))
        fields[name] = {"passed": passed, "maximum_absolute_error":
                        float(np.max(np.abs(actual - expected))) if compatible else None}
    return {"passed": all(value["passed"] for value in fields.values()),
            "absolute_tolerance": 5e-4, "relative_tolerance": 1e-5, "tensors": fields}


def benchmark(model, image_path, onnx_directory=None, device="cpu", iterations=20,
              warmup=3, confidence=.35, cpu_threads=4, nms_iou=.5,
              max_detections=32, min_height=0):
    import numpy as np
    from PIL import Image, ImageOps
    import torch
    from .runtime import CloudRuntime
    from .format import HEADER
    from .cli import doctor
    if type(iterations) is not int or not 2 <= iterations <= 10000:
        raise ValueError("Iterations must be 2 to 10000.")
    if type(warmup) is not int or not 0 <= warmup <= 1000:
        raise ValueError("Warmup must be 0 to 1000.")
    if type(cpu_threads) is not int or not 1 <= cpu_threads <= 64:
        raise ValueError("CPU threads must be 1 to 64.")
    for name, value in (("confidence", confidence), ("nms_iou", nms_iou)):
        if not np.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be a finite number from 0 to 1.")
    if type(max_detections) is not int or not 1 <= max_detections <= 256:
        raise ValueError("max_detections must be an integer from 1 to 256.")
    if not np.isfinite(min_height) or min_height < 0:
        raise ValueError("min_height must be finite and nonnegative.")
    predict_settings = {"confidence": confidence, "nms_iou": nms_iou,
                        "max_detections": max_detections, "min_height": min_height, "debug": True}
    old_threads = torch.get_num_threads()
    torch.set_num_threads(cpu_threads)
    warnings = ["This is a single-image software benchmark, not a game detection quality benchmark.",
                "No tuned TensorRT comparison, CUDA graphs, or ONNX GPU I/O binding is implemented here.",
                "Timings include preprocessing, both stages, transfers, filtering, topology checks, and Python results; disk image loading/model loading are excluded.",
                "Screen capture, display, and application scheduling are outside this predict-call benchmark.",
                "A smaller descriptor reduces storage and parse work; it does not reduce per-frame neural-network arithmetic."]
    try:
        from threadpoolctl import threadpool_limits
        pool_context = threadpool_limits(limits=cpu_threads)
    except ImportError:
        pool_context = contextlib.nullcontext()
        warnings.append("threadpoolctl is absent; NumPy BLAS thread count is uncontrolled.")
    try:
        with pool_context:
            with Image.open(image_path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
            runtimes, load_ms = {}, {}
            for name, backend in (("cloud_torch", "torch"), ("cloud_numpy_reference_cpu", "numpy")):
                load_started = time.perf_counter()
                runtimes[name] = CloudRuntime(model, backend=backend, device=device if backend == "torch" else "cpu")
                load_ms[name] = (time.perf_counter() - load_started) * 1000
            resolved_device = str(runtimes["cloud_torch"].device)
            if onnx_directory:
                export_manifest = json.loads((Path(onnx_directory) / "model.json").read_text(encoding="utf-8"))
                model_hash = hashlib.sha256(Path(model).read_bytes()).hexdigest()
                if export_manifest.get("source_cloud_sha256") != model_hash:
                    raise ValueError("The ONNX pair was not exported from this exact .cloud file. Re-export before comparing.")
                load_started = time.perf_counter()
                ort_runtime = CloudRuntime(onnx_directory, backend="onnx", device=resolved_device,
                                            onnx_threads=cpu_threads)
                provider = "CUDAExecutionProvider" if resolved_device.startswith("cuda") else "CPUExecutionProvider"
                runtimes["onnxruntime_" + provider] = ort_runtime
                load_ms["onnxruntime_" + provider] = (time.perf_counter() - load_started) * 1000
            measurements, examples = {}, {}
            for name, runtime in runtimes.items():
                for _ in range(warmup):
                    runtime.predict(image, **predict_settings)
                phases = {phase: [] for phase in ("preprocess", "inference", "postprocess", "total")}
                stages = {phase: [] for phase in ("detector", "encasing", "filter", "geometry")}
                wall_times = []
                counts = []
                for _ in range(iterations):
                    wall_started = time.perf_counter()
                    result = runtime.predict(image, **predict_settings)
                    wall_times.append((time.perf_counter() - wall_started) * 1000)
                    for phase in phases:
                        phases[phase].append(result["timing_ms"][phase])
                    for stage in stages:
                        stages[stage].append(result["diagnostics"]["stage_timing_ms"][stage])
                    counts.append(len(result["detections"]))
                examples[name] = result
                measurements[name] = {"timing_ms": {phase: _statistics(values) for phase, values in phases.items()},
                                      "stage_timing_ms": {phase: _statistics(values) for phase, values in stages.items()},
                                      "predict_wall_ms": _statistics(wall_times),
                                      "runtime_initialization_ms": load_ms[name],
                                      "frames_per_second_from_median": 1000 / statistics.median(phases["total"]),
                                      "final_detection_counts": sorted(set(counts)),
                                      "invalid_contours": result["invalid_contours"],
                                      "device": str(runtime.device)}
            parity = {name: _compare(examples["cloud_torch"], output)
                      for name, output in examples.items() if name != "cloud_torch"}
            raw_reference = _raw_outputs(runtimes["cloud_torch"], image)
            raw_parity = {name: _raw_parity(raw_reference, _raw_outputs(runtime, image))
                          for name, runtime in runtimes.items() if name != "cloud_torch"}
            with Path(model).open("rb") as stream:
                _, _, descriptor_bytes, payload_bytes, _ = HEADER.unpack(stream.read(HEADER.size))
            report = {"environment": doctor(), "input_image_size": list(image.size),
                      "model_sha256": hashlib.sha256(Path(model).read_bytes()).hexdigest(),
                      "image_sha256": hashlib.sha256(Path(image_path).read_bytes()).hexdigest(),
                      "image_name": Path(image_path).name,
                      "model_config": runtimes["cloud_torch"].config.to_dict(),
                      "iterations": iterations, "warmup": warmup, "cpu_threads": cpu_threads,
                      "confidence": confidence, "precision": "float32", "batch_size": 1,
                      "nms_iou": nms_iou, "max_detections": max_detections, "min_height_pixels": min_height,
                      "native_storage": {"file_bytes": Path(model).stat().st_size,
                                         "header_bytes": HEADER.size, "descriptor_bytes": descriptor_bytes,
                                         "learned_tensor_bytes": payload_bytes},
                      "diagnostic_boxes_enabled_for_parity": True,
                      "measurements": measurements, "output_parity": parity,
                      "raw_tensor_parity": raw_parity, "warnings": warnings,
                      "speed_target_achieved": None}
            ort_names = [name for name in measurements if name.startswith("onnxruntime_")]
            if ort_names:
                name = ort_names[0]
                reference_ms = measurements[name]["timing_ms"]["total"]["median"]
                cloud_ms = measurements["cloud_torch"]["timing_ms"]["total"]["median"]
                report["cloud_torch_throughput_ratio_to_onnx"] = reference_ms / cloud_ms
                report["comparison_note"] = "Ratio compares the PyTorch research backend with optimized ONNX Runtime graphs using identical host preprocessing/postprocessing; it is not a native CUDA-engine speedup."
            return report
    finally:
        torch.set_num_threads(old_threads)
