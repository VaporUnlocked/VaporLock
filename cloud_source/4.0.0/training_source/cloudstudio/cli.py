"""Desktop entry point and scriptable versions of the same research workflow."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys
import traceback


def _print_json(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def _class_ids(value):
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part.isascii() or not part.isdecimal() for part in parts):
        raise argparse.ArgumentTypeError("Use comma-separated nonnegative YOLO class IDs, such as 0,2.")
    result = sorted({int(part) for part in parts})
    if any(identifier > 2**31 - 1 for identifier in result):
        raise argparse.ArgumentTypeError("YOLO class IDs must be at most 2147483647.")
    return result


def _thresholds(value):
    try:
        result = [float(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use comma-separated thresholds, such as 0.2,0.35,0.5.") from exc
    if not 1 <= len(result) <= 20 or any(not math.isfinite(level) or not 0 <= level <= 1 for level in result):
        raise argparse.ArgumentTypeError("Provide 1 to 20 finite thresholds between 0 and 1.")
    return sorted(set(result))


@contextmanager
def _interruptible(message):
    """Restore the caller's handler when main() is used more than once."""
    import signal

    cancelled = False
    previous = signal.getsignal(signal.SIGINT)

    def cancel(signum, frame):
        nonlocal cancelled
        cancelled = True
        print(message, file=sys.stderr, flush=True)

    signal.signal(signal.SIGINT, cancel)
    try:
        yield lambda: cancelled
    finally:
        signal.signal(signal.SIGINT, previous)


def doctor():
    report = {"python": sys.version, "platform": platform.platform(), "packages": {}, "cuda_available": False}
    for name in ("numpy", "Pillow", "PySide6-Essentials", "mss", "PyYAML", "torch", "opencv-python", "onnx", "onnxruntime", "onnxruntime-gpu"):
        try:
            report["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["packages"][name] = None
    if report["packages"]["torch"]:
        try:
            import torch
            report["cuda_available"] = torch.cuda.is_available()
            report["cuda_build"] = torch.version.cuda
            report["gpus"] = [{"index": index, "name": torch.cuda.get_device_name(index),
                                "memory_gib": round(torch.cuda.get_device_properties(index).total_memory / 1024 ** 3, 2)}
                               for index in range(torch.cuda.device_count())]
        except Exception as exc:
            report["torch_error"] = str(exc)
    if report["packages"]["onnxruntime"] or report["packages"]["onnxruntime-gpu"]:
        try:
            import onnxruntime as ort
            ort.disable_telemetry_events()
            report["onnx_available_providers"] = ort.get_available_providers()
            report["onnx_provider_note"] = "Availability is not an executed CUDA inference test."
        except Exception as exc:
            report["onnx_error"] = str(exc)
    return report


def _parser():
    parser = argparse.ArgumentParser(prog="cloud-studio", description="Cloud Studio — box + encasing annotation, training, and inference.")
    sub = parser.add_subparsers(dest="command")
    gui = sub.add_parser("gui", help="Open the local desktop app (default).")
    gui.add_argument("project", nargs="?")
    gui.add_argument("--model", help="Load a .cloud model, ONNX model.json, or ONNX export directory.")
    gui.add_argument("--backend", choices=("numpy", "torch", "onnx"), help="Defaults to ONNX for exported graphs, NumPy for .cloud.")
    gui.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    gui.add_argument("--live", action="store_true", help="Open Live capture; capture starts when Start is clicked.")
    sub.add_parser("doctor", help="Show installed packages and visible GPU devices.")
    demo = sub.add_parser("demo", help="Create synthetic software-test data, never a game-trained model.")
    demo.add_argument("directory")
    demo.add_argument("--count", type=int, default=72)
    demo.add_argument("--size", type=int, default=160)
    demo.add_argument("--seed", type=int, default=7)
    validate = sub.add_parser("validate", help="Check a dataset, label geometry, and splits.")
    validate.add_argument("project")
    yolo = sub.add_parser("import-yolo", help="Import existing YOLO detection boxes for assisted encasing annotation.")
    yolo.add_argument("project", help="Existing Cloud Studio project; unused for a read-only scan.")
    yolo.add_argument("source", help="Local dataset directory, YAML, image directory, or image-list text file.")
    yolo.add_argument("--classes", type=_class_ids,
                      help="Target IDs such as 0,2; required for multiple classes. Selected IDs map to Cloud class 0.")
    yolo.add_argument("--scan-only", action="store_true", help="Report classes/counts/errors without changing any project.")
    yolo.add_argument("--images", dest="image_root", help="Explicit image root for nonstandard dataset layouts.")
    yolo.add_argument("--labels", dest="label_root", help="Explicit label root mirroring the image root.")
    yolo.add_argument("--split", choices=("train", "val", "test"), default="train",
                      help="Fallback split when folders or YAML do not already declare one (default: train).")
    yolo.add_argument("--group-mode", choices=("directory", "image"), default="directory",
                      help="Group source directories, or independent images, for split integrity.")
    yolo.add_argument("--group", dest="source_group", help="Treat this import as one named recording/session.")
    yolo.add_argument("--allow-confidence", action="store_true",
                      help="Accept a sixth prediction-confidence column and retain it in provenance.")
    train = sub.add_parser("train", help="Train both supervised layers from reviewed examples.")
    train.add_argument("project")
    train.add_argument("--output", required=True)
    from .training_settings import add_training_arguments
    add_training_arguments(train)
    evaluate = sub.add_parser("evaluate", help="Evaluate actual predicted boxes and encasings.")
    evaluate.add_argument("project")
    evaluate.add_argument("model")
    evaluate.add_argument("--split", choices=("val", "test", "train"), default="val")
    evaluate.add_argument("--device", default="cpu")
    evaluate.add_argument("--confidence", type=float, default=.35)
    evaluate.add_argument("--output")
    comparison = sub.add_parser("compare-models", help="Compare two models on the same held-out images and export a failure browser.")
    comparison.add_argument("project")
    comparison.add_argument("model_a")
    comparison.add_argument("model_b")
    comparison.add_argument("--output", required=True, help="New directory for comparison.html and comparison.json.")
    comparison.add_argument("--split", choices=("val", "test"), default="val")
    comparison.add_argument("--confidence", type=float, default=.35)
    comparison.add_argument("--thresholds", type=_thresholds, help="Validation-only confidence sweep, for example 0.2,0.35,0.5.")
    comparison.add_argument("--backend", choices=("numpy", "torch", "onnx"), default="numpy")
    comparison.add_argument("--device", default="cpu")
    preview = sub.add_parser("preview-targets", help="Preview reviewed encasings versus the exact polygon or mask training targets.")
    preview.add_argument("project")
    preview.add_argument("--output", required=True, help="New directory for target_preview.html and target_preview.json.")
    preview.add_argument("--vertices", type=int, choices=(16, 32, 64), default=32)
    from .config import ARCHITECTURE, SUPPORTED_ARCHITECTURES
    preview.add_argument("--architecture", choices=SUPPORTED_ARCHITECTURES, default=ARCHITECTURE,
                         help="Use cloudvision_s2_v1 for authoritative raster-mask targets; default preserves legacy polygon previews.")
    preview.add_argument("--input-size", type=int, default=640, help="Detector letterbox side used by the selected model.")
    preview.add_argument("--mask-size", type=int, choices=(32, 64, 128), default=64,
                         help="S2 foreground-grid side length.")
    preview.add_argument("--mask-samples", type=int, choices=(1, 2, 4), default=2,
                         help="Samples per grid axis, matching the training mask target supersampling setting.")
    preview.add_argument("--split", choices=("train", "val", "test"), default="train")
    predict = sub.add_parser("predict", help="Return encasing JSON, including authoritative raster masks for S2 models.")
    predict.add_argument("model")
    predict.add_argument("image")
    predict.add_argument("--backend", choices=("numpy", "torch", "onnx"), default="numpy")
    predict.add_argument("--device", default="cpu")
    predict.add_argument("--confidence", type=float, default=.35)
    predict.add_argument("--max-detections", type=int, default=32)
    predict.add_argument("--nms-iou", type=float, default=.5)
    predict.add_argument("--min-height", type=float, default=0)
    predict.add_argument("--debug-boxes", action="store_true")
    predict.add_argument("--output", help="Save JSON to this path.")
    predict.add_argument("--overlay", help="Save a PNG of model encasings on this image.")
    export = sub.add_parser("export-onnx", help="Export the two guided inference stages to ONNX.")
    export.add_argument("model")
    export.add_argument("directory")
    inspect = sub.add_parser("inspect", help="Inspect a .cloud model without loading executable objects.")
    inspect.add_argument("model")
    compact = sub.add_parser("compact", help="Write a .cloud containing only learned tensors and required inference metadata.")
    compact.add_argument("model", help="Source .cloud; remains untouched unless explicitly also chosen as output.")
    compact.add_argument("--output", required=True, help="Destination .cloud for deployment.")
    benchmark = sub.add_parser("benchmark", help="Compare identical model outputs and end-to-end timing.")
    benchmark.add_argument("model")
    benchmark.add_argument("image")
    benchmark.add_argument("--onnx-directory")
    benchmark.add_argument("--device", default="cpu")
    benchmark.add_argument("--iterations", type=int, default=20)
    benchmark.add_argument("--warmup", type=int, default=3)
    benchmark.add_argument("--confidence", type=float, default=.35)
    benchmark.add_argument("--cpu-threads", type=int, default=4)
    benchmark.add_argument("--nms-iou", type=float, default=.5)
    benchmark.add_argument("--max-detections", type=int, default=32)
    benchmark.add_argument("--min-height", type=float, default=0)
    benchmark.add_argument("--output")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command in (None, "gui"):
            from .gui import run_gui
            return run_gui(getattr(args, "project", None), model_path=getattr(args, "model", None),
                           backend=getattr(args, "backend", None), device=getattr(args, "device", "cpu"),
                           live_tab=getattr(args, "live", False)) or 0
        if args.command == "doctor":
            _print_json(doctor())
        elif args.command == "demo":
            from .demo import make_demo
            _print_json(make_demo(args.directory, args.count, args.size, args.seed).summary())
        elif args.command == "validate":
            from .data import Project
            project = Project.open(args.project)
            errors = project.validate()
            _print_json({"summary": project.summary(), "errors": errors, "valid": not errors})
            return 1 if errors else 0
        elif args.command == "import-yolo":
            from .yolo import import_yolo, scan_yolo

            plan = scan_yolo(args.source, image_root=args.image_root, label_root=args.label_root,
                             default_split=args.split, group_mode=args.group_mode,
                             source_group=args.source_group, allow_confidence=args.allow_confidence)
            summary = {"status": "invalid" if plan["errors"] else "scanned", "source": plan["source"],
                       "summary": plan["summary"], "classes": plan["classes"],
                       "selected_classes": args.classes,
                       "class_selection_required": len(plan["class_ids"]) > 1 and args.classes is None,
                       "plan_sha256": plan["plan_sha256"], "warnings": plan["warnings"], "errors": plan["errors"]}
            if args.classes is not None and not set(args.classes).issubset(plan["class_ids"]) and not plan["errors"]:
                raise ValueError("Selected class IDs are not present or declared in this dataset: " +
                                 str(sorted(set(args.classes) - set(plan["class_ids"]))))
            if args.scan_only or plan["errors"]:
                _print_json(summary)
                return 1 if plan["errors"] else 0
            from .data import Project

            project = Project.open(args.project)
            result = import_yolo(project, plan, selected_classes=args.classes)
            _print_json({"status": "imported", "project": str(project.root),
                         **{key: value for key, value in result.items() if key != "image_ids"},
                         "next_step": "Open this project in the annotator to generate encasings and review the imported boxes."})
        elif args.command == "train":
            from .training import TrainingConfig, train
            from .training_settings import settings_from_arguments
            config = TrainingConfig(**settings_from_arguments(args))
            def progress(event):
                print(json.dumps(event, allow_nan=False), flush=True)
            with _interruptible("Stopping at the next safe batch boundary...") as should_stop:
                _print_json(train(args.project, args.output, config, progress=progress, should_stop=should_stop))
        elif args.command == "evaluate":
            from .evaluation import evaluate
            result = evaluate(args.project, args.model, split=args.split, device=args.device, confidence=args.confidence)
            if args.output:
                from .data import write_json
                write_json(args.output, result)
            _print_json(result)
        elif args.command == "compare-models":
            from .comparison import compare_models

            with _interruptible("Stopping after the current image comparison...") as should_stop:
                result = compare_models(args.project, args.model_a, args.model_b, args.output,
                                        split=args.split, confidence=args.confidence, thresholds=args.thresholds,
                                        backend=args.backend, device=args.device, should_stop=should_stop)
            summary = {key: result[key] for key in
                       ("status", "cancelled", "images_evaluated", "expected_images", "report_path", "json_path")}
            if "models" in result:
                summary.update({"split": result["split"], "dataset_fingerprint": result["dataset_fingerprint"],
                                "confidence_threshold": result["confidence_threshold"], "warnings": result["warnings"],
                                "models": [{"name": model["name"], "sha256": model["sha256"],
                                            **{key: model["metrics"][key] for key in
                                               ("false_negatives", "false_positives", "emitted_box_f1_at_50",
                                                "wrapper_f1_at_50", "mean_wrapper_iou_on_box_matches")}}
                                           for model in result["models"]]})
            _print_json(summary)
            return 130 if result["cancelled"] else 0
        elif args.command == "preview-targets":
            from .target_preview import preview_training_targets
            from .config import ModelConfig
            config = ModelConfig(input_size=args.input_size, vertices=args.vertices,
                                 architecture=args.architecture, mask_size=args.mask_size)

            with _interruptible("Stopping after the current target preview...") as should_stop:
                result = preview_training_targets(args.project, args.output, vertices=args.vertices,
                                                  config=config, mask_samples=args.mask_samples,
                                                  split=args.split, should_stop=should_stop)
            summary = {key: result[key] for key in
                         ("status", "cancelled", "split", "model_vertices", "images_evaluated", "expected_images",
                          "targets_evaluated", "invalid_targets", "detail_warnings", "report_path", "json_path")}
            for key in ("model_architecture", "model_mask_size", "mask_samples_per_axis"):
                if key in result:
                    summary[key] = result[key]
            _print_json(summary)
            return 130 if result["cancelled"] else 0
        elif args.command == "predict":
            from PIL import Image, ImageDraw, ImageOps
            from .runtime import CloudRuntime
            with Image.open(args.image) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")
            runtime = CloudRuntime(args.model, backend=args.backend, device=args.device)
            result = runtime.predict(image, confidence=args.confidence, nms_iou=args.nms_iou,
                max_detections=args.max_detections, min_height=args.min_height, debug=args.debug_boxes)
            if args.output:
                from .data import write_json
                write_json(args.output, result)
            if args.overlay:
                draw = ImageDraw.Draw(image)
                for detection in result["detections"]:
                    if detection.get("mask") is not None:
                        import numpy as np
                        from .masks import decode_mask
                        pixels = decode_mask(detection["mask"], expected_size=image.size, full=False)
                        padded = np.pad(pixels, 1)
                        interior = (padded[:-2, 1:-1] & padded[2:, 1:-1] &
                                    padded[1:-1, :-2] & padded[1:-1, 2:])
                        alpha = pixels.astype(np.uint8) * 48
                        alpha[pixels & ~interior] = 235
                        image.paste((69, 231, 203), tuple(detection["mask"]["origin"]), Image.fromarray(alpha))
                    else:
                        polygon = [tuple(point) for point in detection["wrapper"]]
                        draw.line(polygon + polygon[:1], fill=(69, 231, 203), width=max(2, image.width // 400))
                    if args.debug_boxes:
                        draw.rectangle(detection["primary_box"], outline=(245, 169, 69), width=1)
                destination = Path(args.overlay)
                destination.parent.mkdir(parents=True, exist_ok=True)
                image.save(destination, format="PNG")
            _print_json(result)
        elif args.command == "export-onnx":
            from .export import export_onnx
            _print_json(export_onnx(args.model, args.directory))
        elif args.command == "inspect":
            from .format import load_cloud
            config, weights, metadata = load_cloud(args.model)
            _print_json({"architecture": config.architecture, "config": config.to_dict(), "metadata": metadata,
                "parameters": sum(array.size for array in weights.values()),
                "tensor_count": len(weights), "weights_bytes": sum(array.nbytes for array in weights.values())})
        elif args.command == "compact":
            from .format import compact_cloud
            _print_json(compact_cloud(args.model, args.output))
        elif args.command == "benchmark":
            from .benchmark import benchmark
            result = benchmark(args.model, args.image, args.onnx_directory, args.device,
                args.iterations, args.warmup, args.confidence, args.cpu_threads,
                nms_iou=args.nms_iou, max_detections=args.max_detections, min_height=args.min_height)
            if args.output:
                from .data import write_json
                write_json(args.output, result)
            _print_json(result)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Cloud Studio: {exc}", file=sys.stderr)
        if isinstance(exc, ImportError):
            print("Run Setup_Windows.cmd to install the app and trainer, or see README.html for optional packages.", file=sys.stderr)
        if __import__("os").environ.get("CLOUD_DEBUG") == "1":
            traceback.print_exc()
        return 1

