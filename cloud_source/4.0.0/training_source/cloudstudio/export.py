"""Export a trained detector and guided contour or mask head as two ONNX graphs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .config import SUPPORTED_ARCHITECTURES, ModelConfig


EXPORT_FORMAT = "cloudstudio_onnx_export_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_onnx(cloud_path: str | Path, output_dir: str | Path) -> dict:
    """Write checked ONNX graphs and an explicit execution contract.

    A graph pair preserves the conditional pipeline: run the shared detector,
    decode/filter its boxes, then feed the selected boxes and retained features
    into the wrapper. The exported model's contour capacity stays fixed.
    """

    try:
        import onnx
        import torch
        from torch import nn
    except ImportError as exc:
        raise RuntimeError("ONNX export requires PyTorch and onnx; install the export dependencies") from exc
    from .format import load_cloud, load_torch_model

    source, destination = Path(cloud_path), Path(output_dir)
    config, _, metadata = load_cloud(source)
    model = load_torch_model(source, device="cpu")
    output_name = "mask_logits" if config.is_mask else "wrappers"

    class DetectorGraph(nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, images):
            return self.net.detect(images)

    class WrapperGraph(nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, features, boxes, batch_indices):
            return self.net.wrap(features, boxes, batch_indices)

    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cloud-export-", dir=destination) as temporary:
        staging = Path(temporary)
        detector_path, wrapper_path = staging / "detector.onnx", staging / "wrapper.onnx"
        images = torch.zeros(1, 3, config.input_size, config.input_size)
        with torch.inference_mode():
            features, _, _ = model.detect(images)
            # Use the explicit legacy tracing path for a compact opset-17 graph
            # and compatibility without the additional onnxscript dependency.
            torch.onnx.export(
                DetectorGraph(model).eval(), (images,), str(detector_path),
                input_names=["images"],
                output_names=["features", "objectness_logits", "raw_boxes"],
                dynamic_axes={"images": {0: "batch"}, "features": {0: "batch"},
                              "objectness_logits": {0: "batch"}, "raw_boxes": {0: "batch"}},
                opset_version=17, dynamo=False, do_constant_folding=True,
            )
            boxes = torch.tensor([[0.1, 0.1, 0.8, 0.9], [0.2, 0.2, 0.6, 0.7]], dtype=torch.float32)
            batch_indices = torch.zeros(2, dtype=torch.int64)
            torch.onnx.export(
                WrapperGraph(model).eval(), (features, boxes, batch_indices), str(wrapper_path),
                input_names=["features", "boxes", "batch_indices"], output_names=[output_name],
                dynamic_axes={"features": {0: "batch"}, "boxes": {0: "detections"},
                              "batch_indices": {0: "detections"}, output_name: {0: "detections"}},
                opset_version=17, dynamo=False, do_constant_folding=True,
            )
        for graph_path in (detector_path, wrapper_path):
            onnx.checker.check_model(str(graph_path), full_check=True)
        manifest = {
            "format": EXPORT_FORMAT,
            "architecture": config.architecture,
            "config": config.to_dict(),
            "metadata": metadata,
            "source_cloud_sha256": _sha256(source),
            "graphs": {
                "detector": {"file": "detector.onnx", "sha256": _sha256(detector_path)},
                "wrapper": {"file": "wrapper.onnx", "sha256": _sha256(wrapper_path)},
            },
            "preprocess": {
                "input": "NCHW float32 RGB in [0,1]",
                "input_size": config.input_size,
                "resize": "Pillow bilinear letterbox with rounded resized dimensions",
                "padding_rgb": [114, 114, 114],
                "coordinate_origin": "top_left",
                "exif_orientation": "transpose before resize",
            },
            "detector": {
                "inputs": ["images"],
                "outputs": ["features", "objectness_logits", "raw_boxes"],
                "raw_box_decode": "center=(cell+sigmoid(dx,dy))/grid; size=sigmoid(dw,dh); clip xyxy to [0,1]",
            },
            "wrapper": {
                "inputs": ["features", "boxes", "batch_indices"],
                "output": "N,K,2 normalized image-square polygon vertices",
                "boxes": "N,4 float32 normalized xyxy; clipped to visible image before guidance",
                "batch_indices": "N int64; index into detector feature batch",
                "sampling": "bilinear; align_corners=True; padding_mode=zeros",
                "empty_candidates": "skip wrapper graph when N=0",
                "vertices": config.vertices,
            },
            "postprocess": {
                "selection": "confidence filter; at most max(256,min(2048,max_detections*16)) candidates; box IoU NMS",
                "geometry": "inverse exact letterbox transform to image pixels",
                "invalid_geometry": "suppress self-intersecting, nonfinite, or degenerate polygons",
                "public_output": "class, detection confidence, encasing polygon only",
            },
        }
        if config.is_mask:
            manifest["detector"].update({
                "grid_stride": config.grid_stride,
                "raw_box_decode": "center=(cell+3*sigmoid(dx,dy)-1)/grid; size=sigmoid(dw,dh); clip xyxy to [0,1]",
            })
            manifest["wrapper"] = {
                "inputs": ["features", "boxes", "batch_indices"],
                "output_name": "mask_logits",
                "output": "N,1,M,M foreground logits in the guiding ROI; sigmoid then source-pixel projection",
                "mask_size": config.mask_size,
                "boxes": "N,4 float32 normalized xyxy; clipped to visible image before guidance",
                "batch_indices": "N int64; index into detector feature batch",
                "sampling": "ROI sample centers; bilinear align_corners=True; zero padding; mask upsampling align_corners=False",
                "projection": "bilinear source-pixel-center sampling with align_corners=False; threshold 0.5",
                "empty_candidates": "skip wrapper graph when N=0; suppress predicted empty masks",
            }
            manifest["postprocess"].update({
                "invalid_geometry": "suppress nonfinite or empty masks; keep holes and disconnected foreground",
                "public_output": "class, detection confidence, authoritative cloud-rle-v1 mask; approximate legacy polygon preview",
            })
        manifest_path = staging / "model.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        # Publish the manifest last; hash validation rejects partially updated pairs.
        for filename in ("detector.onnx", "wrapper.onnx", "model.json"):
            os.replace(staging / filename, destination / filename)
    return {"output_dir": str(destination), "detector": str(destination / "detector.onnx"),
            "wrapper": str(destination / "wrapper.onnx"), "metadata": str(destination / "model.json"),
            "config": config.to_dict(), "opset": 17}


def read_onnx_export(output_dir: str | Path) -> tuple[ModelConfig, dict, Path, Path]:
    """Read a bounded export manifest and verify the graph pair before execution."""

    directory = Path(output_dir)
    manifest_path = directory / "model.json"
    if manifest_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("ONNX export manifest exceeds supported size")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError(f"invalid ONNX export manifest: {exc}") from exc
    if (not isinstance(manifest, dict) or manifest.get("format") != EXPORT_FORMAT
            or manifest.get("architecture") not in SUPPORTED_ARCHITECTURES):
        raise ValueError("not a supported Cloud Studio ONNX export")
    config = ModelConfig.from_dict(manifest.get("config"))
    if manifest["architecture"] != config.architecture:
        raise ValueError("ONNX architecture does not match the model config")
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("ONNX export model metadata must be an object")
    graphs = manifest.get("graphs")
    if not isinstance(graphs, dict) or set(graphs) != {"detector", "wrapper"}:
        raise ValueError("ONNX export must contain detector and wrapper graphs")
    paths = []
    for name in ("detector", "wrapper"):
        entry = graphs[name]
        if (not isinstance(entry, dict) or entry.get("file") != f"{name}.onnx"
                or not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64):
            raise ValueError("invalid ONNX graph descriptor")
        path = directory / f"{name}.onnx"
        if not 0 < path.stat().st_size <= 128 * 1024 * 1024:
            raise ValueError("ONNX graph size is outside supported limits")
        if _sha256(path) != entry["sha256"]:
            raise ValueError(f"{name}.onnx does not match the export manifest checksum")
        paths.append(path)
    return config, metadata, paths[0], paths[1]
