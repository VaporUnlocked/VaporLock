"""Independent NumPy reference inference and optional PyTorch CPU/CUDA execution.

The NumPy implementation executes the native tensor schema directly. It does
not deserialize or invoke PyTorch, ONNX, or ONNX Runtime. It is a correctness
reference, and no performance advantage over optimized runtimes is claimed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import time

import numpy as np
from PIL import Image

from .config import ModelConfig
from .format import load_cloud
from .geometry import EPS, is_simple_polygon, letterbox, polygon_area, transform_box, transform_points


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return (1.0 / (1.0 + np.exp(-np.clip(values, -80, 80)))).astype(np.float32)


@lru_cache(maxsize=64)
def _nonadjacent_edges(count: int) -> tuple[np.ndarray, np.ndarray]:
    first, second = np.triu_indices(count, k=2)
    keep = (first != 0) | (second != count - 1)
    return first[keep], second[keep]


def _simple_prediction(points) -> bool:
    """The reference topology predicate vectorized for at most 64 vertices.

    Each nonadjacent edge pair uses the identical float64 operations and EPS
    thresholds as geometry.is_simple_polygon. The model's bounded 16/32/64
    vertices make one vectorized pass cheap; large annotation polygons still
    use the memory-bounded general implementation.
    """

    try:
        p = np.asarray(points, dtype=np.float64)
        if p.ndim != 2 or p.shape[1] != 2 or not np.isfinite(p).all():
            return False
        if len(p) > 1 and np.linalg.norm(p[-1] - p[0]) <= EPS:
            p = p[:-1]
    except (ValueError, TypeError):
        return False
    count = len(p)
    if count > 64:
        return is_simple_polygon(points)
    if count < 3 or polygon_area(p) <= EPS:
        return False
    following = np.roll(p, -1, axis=0)
    edges = following - p
    if np.any(np.linalg.norm(edges, axis=1) <= EPS):
        return False

    def cross(a, b):
        return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]

    previous = np.roll(edges, 1, axis=0)
    if np.any((np.abs(cross(previous, edges)) <= EPS) &
              (np.sum(previous * edges, axis=1) < -EPS)):
        return False
    first, second = _nonadjacent_edges(count)
    a, b, c, d = p[first], following[first], p[second], following[second]
    ab, cd = b - a, d - c
    o1, o2 = cross(ab, c - a), cross(ab, d - a)
    o3, o4 = cross(cd, a - c), cross(cd, b - c)
    bbox = np.all(np.maximum(np.minimum(a, b), np.minimum(c, d)) <=
                  np.minimum(np.maximum(a, b), np.maximum(c, d)) + EPS, axis=1)
    opposite1 = ((o1 <= EPS) & (o2 >= -EPS)) | ((o2 <= EPS) & (o1 >= -EPS))
    opposite2 = ((o3 <= EPS) & (o4 >= -EPS)) | ((o4 <= EPS) & (o3 >= -EPS))
    return not bool(np.any(bbox & opposite1 & opposite2))


def _torch_inference_model(config: ModelConfig):
    """Construct the same learned network with a shared-image ROI execution path.

    A live frame has one feature map. Sampling vertically stacked ROI grids
    avoids copying that whole map for every selected box. This changes only
    the inference execution layout; weight names, equations, and the training
    implementation stay the same. Other input layouts use the normal method.
    """

    import torch
    from .model import CloudNet

    class InferenceCloudNet(CloudNet):
        def wrap(self, features, boxes, batch_indices):
            if self.config.is_mask:
                return super().wrap(features, boxes, batch_indices)
            if (torch.is_grad_enabled() or features.ndim != 4 or features.shape[0] != 1
                    or features.shape[1] != self.config.channels or boxes.ndim != 2
                    or boxes.shape[1] != 4 or not len(boxes)
                    or batch_indices.ndim != 1 or batch_indices.shape[0] != len(boxes)
                    or batch_indices.dtype != torch.int64
                    or bool(torch.any(batch_indices != 0))):
                return super().wrap(features, boxes, batch_indices)
            boxes = boxes.clamp(0, 1)
            extent = (boxes[:, 2:] - boxes[:, :2]).clamp_min(0)
            grid = (boxes[:, None, None, :2] + self.roi_grid.to(boxes) *
                    extent[:, None, None, :]) * 2 - 1
            grid = grid.to(dtype=features.dtype)
            count, size = len(boxes), self.config.sample_size
            sampled = torch.nn.functional.grid_sample(
                features, grid.reshape(1, count * size, size, 2), mode="bilinear",
                padding_mode="zeros", align_corners=True)
            roi = sampled.reshape(self.config.channels, count, size, size).permute(1, 0, 2, 3)
            if self.config.is_mask:
                return self._mask_from_rois(roi)
            encoded = torch.nn.functional.relu(self.roi_fc(roi.flatten(1)))
            offsets = torch.tanh(self.wrapper_head(encoded)).reshape(-1, self.config.vertices, 2)
            relative = torch.sigmoid(self.template_logits.to(offsets) + 5.0 * offsets)
            return boxes[:, None, :2] + relative * extent[:, None, :]

    return InferenceCloudNet(config)


def decode_boxes_numpy(raw_boxes: np.ndarray, config: ModelConfig | None = None) -> np.ndarray:
    if raw_boxes.ndim != 4 or raw_boxes.shape[1] != 4:
        raise ValueError("raw_boxes must have shape B,4,H,W")
    values = _sigmoid(raw_boxes.transpose(0, 2, 3, 1))
    height, width = raw_boxes.shape[-2:]
    y, x = np.meshgrid(np.arange(height, dtype=np.float32),
                       np.arange(width, dtype=np.float32), indexing="ij")
    offsets = values[..., :2]
    if config is not None and config.is_mask:
        offsets = offsets * 3.0 - 1.0
    cx, cy = (x + offsets[..., 0]) / width, (y + offsets[..., 1]) / height
    hw, hh = values[..., 2] * 0.5, values[..., 3] * 0.5
    return np.stack((cx - hw, cy - hh, cx + hw, cy + hh), axis=-1).clip(0, 1)


def _conv2d(images: np.ndarray, weight: np.ndarray, bias: np.ndarray,
            stride: int = 1, padding: int = 0, dilation: int = 1) -> np.ndarray:
    """NCHW cross-correlation with bounded im2col tiles, no framework dependency."""

    batch, channels, _, _ = images.shape
    out_channels, in_channels, kh, kw = weight.shape
    if channels != in_channels:
        raise ValueError("convolution channel mismatch")
    if padding:
        images = np.pad(images, ((0, 0), (0, 0), (padding, padding), (padding, padding)))
    effective_kernel = ((kh - 1) * dilation + 1, (kw - 1) * dilation + 1)
    windows = np.lib.stride_tricks.sliding_window_view(images, effective_kernel, axis=(2, 3))
    windows = windows[:, :, ::stride, ::stride, ::dilation, ::dilation]
    oh, ow = windows.shape[2:4]
    output = np.empty((batch, out_channels, oh, ow), dtype=np.float32)
    flat_weights = weight.reshape(out_channels, -1).T
    # At most about 8 MiB in the im2col temporary for common input shapes.
    rows_per_tile = max(1, (2 * 1024 * 1024) // max(1, channels * kh * kw * ow))
    for bi in range(batch):
        for y0 in range(0, oh, rows_per_tile):
            y1 = min(oh, y0 + rows_per_tile)
            tile = windows[bi, :, y0:y1].transpose(1, 2, 0, 3, 4)
            columns = np.ascontiguousarray(tile).reshape(-1, channels * kh * kw)
            values = columns @ flat_weights + bias
            output[bi, :, y0:y1] = values.reshape(y1 - y0, ow, out_channels).transpose(2, 0, 1)
    return output


def _depthwise_conv2d(images: np.ndarray, weight: np.ndarray, bias: np.ndarray,
                      stride: int = 1, padding: int = 1, dilation: int = 1) -> np.ndarray:
    """One spatial filter per channel; no dense C-by-C kernel is materialized."""
    if weight.shape[1] != 1 or weight.shape[0] != images.shape[1]:
        raise ValueError("depthwise convolution channel mismatch")
    if padding:
        images = np.pad(images, ((0, 0), (0, 0), (padding, padding), (padding, padding)))
    kh, kw = weight.shape[-2:]
    kernel = ((kh - 1) * dilation + 1, (kw - 1) * dilation + 1)
    windows = np.lib.stride_tricks.sliding_window_view(images, kernel, axis=(2, 3))
    windows = windows[:, :, ::stride, ::stride, ::dilation, ::dilation]
    result = np.einsum("nchwkl,ckl->nchw", windows, weight[:, 0], optimize=False)
    return result + bias[None, :, None, None]


def _resize_bilinear(values: np.ndarray, height: int, width: int) -> np.ndarray:
    """NCHW bilinear interpolation matching align_corners=False edge padding."""
    ih, iw = values.shape[-2:]
    x = (np.arange(width, dtype=np.float32) + 0.5) * np.float32(iw / width) - 0.5
    y = (np.arange(height, dtype=np.float32) + 0.5) * np.float32(ih / height) - 0.5
    x, y = x.clip(0, iw - 1), y.clip(0, ih - 1)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, iw - 1), np.minimum(y0 + 1, ih - 1)
    dx, dy = x - x0.astype(np.float32), y - y0.astype(np.float32)
    top = values[:, :, y0, :][:, :, :, x0] * (1 - dx)[None, None, None, :]
    top += values[:, :, y0, :][:, :, :, x1] * dx[None, None, None, :]
    bottom = values[:, :, y1, :][:, :, :, x0] * (1 - dx)[None, None, None, :]
    bottom += values[:, :, y1, :][:, :, :, x1] * dx[None, None, None, :]
    return top * (1 - dy)[None, None, :, None] + bottom * dy[None, None, :, None]


def _perimeter_template(count: int) -> np.ndarray:
    distance = np.arange(count, dtype=np.float32) * (4.0 / count)
    x = np.where(distance < 1, distance, np.where(distance < 2, 1,
                 np.where(distance < 3, 3 - distance, 0)))
    y = np.where(distance < 1, 0, np.where(distance < 2, distance - 1,
                 np.where(distance < 3, 1, 4 - distance)))
    return np.stack((x, y), axis=-1).astype(np.float32)


def _sample_rois(features: np.ndarray, boxes: np.ndarray, batch_indices: np.ndarray,
                 roi_size: int, *, centered: bool = False) -> np.ndarray:
    """Bilinear grid sampling with align_corners=True and zero padding."""

    _, channels, height, width = features.shape
    line = ((np.arange(roi_size, dtype=np.float32) + 0.5) / roi_size if centered
            else np.linspace(0, 1, roi_size, dtype=np.float32))
    gy, gx = np.meshgrid(line, line, indexing="ij")
    unit_grid = np.stack((gx, gy), axis=-1)
    extent = np.maximum(boxes[:, 2:] - boxes[:, :2], 0)
    grid = (boxes[:, None, None, :2] + unit_grid * extent[:, None, None, :]) * 2 - 1
    x, y = (grid[..., 0] + 1) * 0.5 * (width - 1), (grid[..., 1] + 1) * 0.5 * (height - 1)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    dx, dy = x - x0.astype(np.float32), y - y0.astype(np.float32)
    output = np.zeros((len(boxes), roi_size, roi_size, channels), dtype=np.float32)
    feature_map = features.transpose(0, 2, 3, 1)
    for xx, yy, coefficient in (
        (x0, y0, (1 - dx) * (1 - dy)),
        (x0 + 1, y0, dx * (1 - dy)),
        (x0, y0 + 1, (1 - dx) * dy),
        (x0 + 1, y0 + 1, dx * dy),
    ):
        valid = (xx >= 0) & (xx < width) & (yy >= 0) & (yy < height)
        values = feature_map[batch_indices[:, None, None], np.clip(yy, 0, height - 1),
                             np.clip(xx, 0, width - 1)]
        output += values * (coefficient * valid)[..., None]
    return output.transpose(0, 3, 1, 2)


class NumpyCloudNet:
    """Pure NumPy executor for the supported versioned CloudVision equations."""

    def __init__(self, config: ModelConfig, weights: dict[str, np.ndarray]):
        self.config, self.weights = config, weights
        if config.is_mask:
            return
        self.template = _perimeter_template(config.vertices)
        inset = self.template * 0.90 + 0.05
        self.template_logits = np.log(inset / (1.0 - inset))

    def detect(self, images: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        images = np.asarray(images, dtype=np.float32)
        if images.ndim != 4 or images.shape[1:] != (3, self.config.input_size, self.config.input_size):
            raise ValueError("images have the wrong shape for this model")
        if self.config.is_mask:
            return self._detect_mask(images)
        x = images
        for name in ("conv1", "conv2", "conv3"):
            x = np.maximum(_conv2d(x, self.weights[f"{name}.weight"],
                                   self.weights[f"{name}.bias"], stride=2, padding=1), 0)
        for i, dilation in enumerate((1, 2, 4, 8), start=1):
            name = f"context{i}"
            x = np.maximum(x + _conv2d(x, self.weights[f"{name}.weight"],
                                       self.weights[f"{name}.bias"], padding=dilation,
                                       dilation=dilation), 0)
        logits = _conv2d(x, self.weights["objectness.weight"], self.weights["objectness.bias"])
        boxes = _conv2d(x, self.weights["box_head.weight"], self.weights["box_head.bias"])
        return x, logits, boxes

    def _conv(self, values: np.ndarray, name: str, **kwargs) -> np.ndarray:
        return _conv2d(values, self.weights[name + ".weight"], self.weights[name + ".bias"], **kwargs)

    def _depthwise(self, values: np.ndarray, name: str, **kwargs) -> np.ndarray:
        return _depthwise_conv2d(values, self.weights[name + ".weight"],
                                 self.weights[name + ".bias"], **kwargs)

    def _separable(self, values: np.ndarray, name: str, stride: int = 1,
                   dilation: int = 1) -> np.ndarray:
        spatial = np.maximum(self._depthwise(values, name + "_dw", stride=stride,
                                             padding=dilation, dilation=dilation), 0)
        return np.maximum(self._conv(spatial, name + "_pw"), 0)

    def _detect_mask(self, images: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        def up(values):
            return np.repeat(np.repeat(values, 2, axis=2), 2, axis=3)
        detail = self._separable(np.maximum(self._conv(images, "conv1", stride=2, padding=1), 0),
                                 "detail", stride=2)
        stage8 = self._separable(detail, "stage8", stride=2)
        stage16 = self._separable(stage8, "stage16", stride=2)
        stage32 = self._separable(stage16, "stage32", stride=2)
        stage32 = (stage32 + self._separable(stage32, "context", dilation=2)) * 0.5
        pyramid16 = self._separable((stage16 + up(stage32)) * 0.5, "fuse16")
        pyramid8 = self._separable((stage8 + up(pyramid16)) * 0.5, "fuse8")
        features = self._separable((detail + up(pyramid8)) * 0.5, "fuse4")
        return features, self._conv(features, "objectness"), self._conv(features, "box_head")

    def _mask_from_rois(self, roi: np.ndarray) -> np.ndarray:
        count, size = len(roi), self.config.sample_size
        line = (np.arange(size, dtype=np.float32) + 0.5) / size
        gy, gx = np.meshgrid(line, line, indexing="ij")
        coordinates = np.broadcast_to(np.stack((gx * 2 - 1, gy * 2 - 1))[None], (count, 2, size, size))
        encoded = np.maximum(self._conv(np.concatenate((roi, coordinates), axis=1), "mask_reduce"), 0)
        residual = np.maximum(self._conv(np.maximum(self._depthwise(encoded, "mask_dw"), 0), "mask_pw"), 0)
        encoded = np.maximum(encoded + residual, 0)
        encoded = _resize_bilinear(encoded, self.config.mask_size, self.config.mask_size)
        encoded = np.maximum(encoded + self._depthwise(encoded, "mask_refine_dw"), 0)
        return self._conv(encoded, "mask_out")

    def wrap(self, features: np.ndarray, boxes: np.ndarray,
             batch_indices: np.ndarray) -> np.ndarray:
        boxes = np.asarray(boxes, dtype=np.float32)
        batch_indices = np.asarray(batch_indices)
        if features.ndim != 4 or features.shape[1] != self.config.channels:
            raise ValueError("features have the wrong channel count or rank")
        if boxes.ndim != 2 or boxes.shape[1] != 4:
            raise ValueError("boxes must have shape N,4")
        if batch_indices.shape != (len(boxes),) or batch_indices.dtype != np.dtype("int64"):
            raise ValueError("batch_indices must be an int64 array with shape N")
        if not len(boxes):
            if self.config.is_mask:
                return np.empty((0, 1, self.config.mask_size, self.config.mask_size), dtype=np.float32)
            return np.empty((0, self.config.vertices, 2), dtype=np.float32)
        if np.any(batch_indices < 0) or np.any(batch_indices >= features.shape[0]):
            raise ValueError("batch index is outside the feature batch")
        boxes = boxes.clip(0, 1)
        if self.config.is_mask:
            roi = _sample_rois(features, boxes, batch_indices, self.config.sample_size, centered=True)
            return self._mask_from_rois(roi)
        roi = _sample_rois(features, boxes, batch_indices, self.config.roi_size)
        w = self.weights
        hidden = np.maximum(roi.reshape(len(boxes), -1) @ w["roi_fc.weight"].T + w["roi_fc.bias"], 0)
        offsets = np.tanh(hidden @ w["wrapper_head.weight"].T + w["wrapper_head.bias"])
        offsets = offsets.reshape(-1, self.config.vertices, 2)
        relative = _sigmoid(self.template_logits + 5.0 * offsets)
        extent = np.maximum(boxes[:, 2:] - boxes[:, :2], 0)
        return boxes[:, None, :2] + relative * extent[:, None, :]


class OnnxCloudNet:
    """Optional graph-pair adapter with identical detect/wrap signatures.

    Sessions use graph optimization and standard host-array I/O. This adapter
    does not claim tuned CUDA I/O binding or a TensorRT performance baseline.
    """

    def __init__(self, output_dir: str | Path, provider: str = "CPUExecutionProvider",
                 device_id: int = 0, intra_op_num_threads: int = 0):
        if type(intra_op_num_threads) is not int or not 0 <= intra_op_num_threads <= 64:
            raise ValueError("ONNX CPU threads must be an integer from 0 (automatic) to 64")
        if provider == "CUDAExecutionProvider":
            # When present, PyTorch loads its matching CUDA/cuDNN libraries.
            # A standalone ORT installation may instead provide them on PATH.
            try:
                import torch  # noqa: F401
            except ImportError:
                pass
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("Install onnxruntime to execute exported ONNX models") from exc
        # This local research app does not opt into runtime usage telemetry.
        ort.disable_telemetry_events()
        from .export import read_onnx_export
        self.config, self.metadata, detector_path, wrapper_path = read_onnx_export(output_dir)
        if provider not in ort.get_available_providers():
            raise RuntimeError(f"ONNX Runtime provider {provider} is unavailable; available: "
                               f"{ort.get_available_providers()}")
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = intra_op_num_threads
        options.log_severity_level = 3
        providers = [provider] if provider == "CPUExecutionProvider" else [
            (provider, {"device_id": str(device_id)}), "CPUExecutionProvider"
        ]
        self.detector = ort.InferenceSession(str(detector_path), sess_options=options, providers=providers)
        self.wrapper = ort.InferenceSession(str(wrapper_path), sess_options=options, providers=providers)
        for session in (self.detector, self.wrapper):
            if provider not in session.get_providers():
                raise RuntimeError(f"ONNX Runtime could not initialize {provider}; "
                                   "check its CUDA/cuDNN requirements or choose CPU")
            # An execution error must not silently turn a CUDA benchmark into CPU.
            session.disable_fallback()
        self.provider = provider

    def detect(self, images: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        images = np.asarray(images, dtype=np.float32)
        if images.ndim != 4 or images.shape[1:] != (3, self.config.input_size, self.config.input_size):
            raise ValueError("images have the wrong shape for this model")
        outputs = self.detector.run(["features", "objectness_logits", "raw_boxes"],
                                    {"images": np.ascontiguousarray(images)})
        return tuple(outputs)

    def wrap(self, features: np.ndarray, boxes: np.ndarray,
             batch_indices: np.ndarray) -> np.ndarray:
        boxes, batch_indices = np.asarray(boxes, dtype=np.float32), np.asarray(batch_indices)
        if boxes.ndim != 2 or boxes.shape[1] != 4:
            raise ValueError("boxes must have shape N,4")
        if batch_indices.shape != (len(boxes),) or batch_indices.dtype != np.dtype("int64"):
            raise ValueError("batch_indices must be an int64 array with shape N")
        if not len(boxes):
            if self.config.is_mask:
                return np.empty((0, 1, self.config.mask_size, self.config.mask_size), dtype=np.float32)
            return np.empty((0, self.config.vertices, 2), dtype=np.float32)
        if np.any(batch_indices < 0) or np.any(batch_indices >= features.shape[0]):
            raise ValueError("batch index is outside the feature batch")
        return self.wrapper.run(["mask_logits" if self.config.is_mask else "wrappers"], {
            "features": np.ascontiguousarray(features, dtype=np.float32),
            "boxes": np.ascontiguousarray(boxes),
            "batch_indices": np.ascontiguousarray(batch_indices),
        })[0]


def _mask_preview(mask: dict, box: list[float]) -> list[list[float]]:
    """Small convex envelope for legacy polygon-only consumers.

    This preview is explicitly approximate. The accompanying RLE mask, never
    this envelope, defines foreground, holes, disconnected parts and metrics.
    Work scales with occupied rows rather than every foreground pixel.
    """
    from .masks import decode_mask
    pixels = decode_mask(mask, full=False)
    rows = np.flatnonzero(pixels.any(axis=1))
    if not len(rows):
        return []
    occupied = pixels[rows]
    left = occupied.argmax(axis=1)
    right = pixels.shape[1] - occupied[:, ::-1].argmax(axis=1)
    ox, oy = mask["origin"]
    points = set()
    for y, x1, x2 in zip(rows.tolist(), left.tolist(), right.tolist()):
        points.update(((x1 + ox, y + oy), (x2 + ox, y + oy),
                       (x1 + ox, y + oy + 1), (x2 + ox, y + oy + 1)))
    ordered = sorted(points)
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    # Pixel cells can straddle a fractional guiding-box edge. Keep the legacy
    # preview inside that box; source foreground remains the exact RLE value.
    return [[float(np.clip(x, box[0], box[2])), float(np.clip(y, box[1], box[3]))]
            for x, y in lower[:-1] + upper[:-1]]


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float,
          limit: int) -> np.ndarray:
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    while len(order) and len(keep) < limit:
        first = int(order[0])
        keep.append(first)
        rest = order[1:]
        if not len(rest):
            break
        low = np.maximum(boxes[first, :2], boxes[rest, :2])
        high = np.minimum(boxes[first, 2:], boxes[rest, 2:])
        intersection = np.prod(np.maximum(high - low, 0), axis=1)
        a = np.prod(np.maximum(boxes[first, 2:] - boxes[first, :2], 0))
        b = np.prod(np.maximum(boxes[rest, 2:] - boxes[rest, :2], 0), axis=1)
        iou = intersection / np.maximum(a + b - intersection, 1e-12)
        order = rest[iou <= threshold]
    return np.asarray(keep, dtype=np.int64)


class CloudRuntime:
    def __init__(self, path: str | Path, backend: str = "numpy", device: str = "cpu",
                 onnx_threads: int = 0):
        if backend not in ("numpy", "torch", "onnx"):
            raise ValueError("backend must be 'numpy', 'torch', or 'onnx'")
        self.backend = backend
        self._torch = None
        if backend == "onnx":
            if device not in ("cpu", "auto", "cuda") and not device.startswith("cuda:"):
                raise ValueError("ONNX inference supports CPU and CUDA devices")
            provider = "CUDAExecutionProvider" if device.startswith("cuda") else "CPUExecutionProvider"
            device_id = int(device.split(":", 1)[1]) if ":" in device else 0
            self.network = OnnxCloudNet(path, provider=provider, device_id=device_id,
                                        intra_op_num_threads=onnx_threads)
            self.config, self.metadata = self.network.config, self.network.metadata
            self.device = "cpu" if device == "auto" else device
            return
        self.config, weights, self.metadata = load_cloud(path)
        if backend == "numpy":
            if device not in ("cpu", "auto"):
                raise ValueError("the NumPy reference backend supports CPU only")
            self.device = "cpu"
            self.network = NumpyCloudNet(self.config, weights)
        else:
            try:
                import torch
            except ImportError as exc:
                raise RuntimeError("Install PyTorch to use the torch inference backend") from exc
            self._torch = torch
            if device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            self.device = torch.device(device)
            if self.device.type not in ("cpu", "cuda"):
                raise ValueError("this release supports torch CPU and CUDA devices")
            if self.device.type == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("CUDA is unavailable; choose CPU or install a CUDA-enabled PyTorch build")
            with torch.random.fork_rng(devices=[]):
                self.network = _torch_inference_model(self.config)
            self.network.load_state_dict({name: torch.from_numpy(value) for name, value in weights.items()},
                                         strict=True)
            self.network.to(self.device).eval()

    def _synchronize(self) -> None:
        if self._torch is not None and self.device.type == "cuda":
            self._torch.cuda.synchronize(self.device)

    def predict(self, image: Image.Image, confidence: float = 0.35,
                nms_iou: float = 0.5, max_detections: int = 32,
                debug: bool = False, min_height: float = 0) -> dict:
        """Return only usable encasing geometry in original, oriented image pixels."""

        if not isinstance(image, Image.Image):
            raise TypeError("image must be a Pillow Image")
        for name, value in (("confidence", confidence), ("nms_iou", nms_iou)):
            if not np.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a finite number between 0 and 1")
        if type(max_detections) is not int or not 1 <= max_detections <= 256:
            raise ValueError("max_detections must be an integer from 1 to 256")
        if not np.isfinite(min_height) or min_height < 0:
            raise ValueError("min_height must be finite and nonnegative")
        started = time.perf_counter()
        array, meta = letterbox(image, self.config.input_size)
        preprocess_done = time.perf_counter()
        self._synchronize()
        inference_started = time.perf_counter()
        if self.backend in ("numpy", "onnx"):
            features, logits, raw_boxes = self.network.detect(array[None])
            decoded = decode_boxes_numpy(raw_boxes, self.config)[0].reshape(-1, 4)
            scores = _sigmoid(logits[0, 0]).reshape(-1)
        else:
            torch = self._torch
            from .model import decode_boxes
            with torch.inference_mode():
                input_tensor = torch.from_numpy(array[None]).to(self.device)
                features, logits, raw_boxes = self.network.detect(input_tensor)
                decoded = decode_boxes(raw_boxes, self.config)[0].reshape(-1, 4).float().cpu().numpy()
                scores = logits[0, 0].sigmoid().reshape(-1).float().cpu().numpy()
        self._synchronize()
        detector_done = time.perf_counter()
        detector_seconds = detector_done - inference_started
        inference_seconds = detector_seconds

        # Discard letterbox padding and use the visible-image ROI as guidance.
        size = self.config.input_size
        width, height = meta["original_width"], meta["original_height"]
        low = np.asarray([meta["pad_x"], meta["pad_y"]], dtype=np.float32) / size
        high = low + np.asarray([width * meta.get("scale_x", meta["scale"]),
                                 height * meta.get("scale_y", meta["scale"])], dtype=np.float32) / size
        boxes = decoded.copy()
        boxes[:, :2] = np.maximum(boxes[:, :2], low)
        boxes[:, 2:] = np.minimum(boxes[:, 2:], high)
        extents = boxes[:, 2:] - boxes[:, :2]
        original_heights = extents[:, 1] * size / meta.get("scale_y", meta["scale"])
        eligible = np.isfinite(scores) & np.isfinite(boxes).all(axis=1)
        eligible &= (extents[:, 0] > 1e-7) & (extents[:, 1] > 1e-7) & (original_heights >= min_height)
        valid = eligible & (scores >= confidence)
        candidates = np.flatnonzero(valid)
        candidate_limit = max(256, min(2048, max_detections * 16))
        candidates = candidates[np.argsort(-scores[candidates], kind="stable")[:candidate_limit]]
        kept = _nms(boxes[candidates], scores[candidates], nms_iou, max_detections)
        selected = candidates[kept]
        selected_boxes = boxes[selected]
        filtering_done = time.perf_counter()

        polygons: list[np.ndarray] = []
        encasing_seconds = 0.0
        if len(selected):
            self._synchronize()
            inference_started = time.perf_counter()
            # Limit the gathered ROI feature copies on the torch path.
            for start in range(0, len(selected), 16):
                batch_boxes = selected_boxes[start:start + 16]
                if self.backend in ("numpy", "onnx"):
                    chunk = self.network.wrap(features, batch_boxes,
                                              np.zeros(len(batch_boxes), dtype=np.int64))
                else:
                    torch = self._torch
                    with torch.inference_mode():
                        chunk = self.network.wrap(features,
                                                  torch.from_numpy(batch_boxes).to(self.device),
                                                  torch.zeros(len(batch_boxes), dtype=torch.int64,
                                                              device=self.device)).float().cpu().numpy()
                polygons.extend(chunk)
            self._synchronize()
            encasing_seconds = time.perf_counter() - inference_started
            inference_seconds += encasing_seconds

        geometry_started = time.perf_counter()
        detections: list[dict] = []
        invalid = 0
        empty_masks = 0
        for index, polygon in zip(selected, polygons):
            if self.config.is_mask:
                from .masks import project_roi_mask
                if not np.isfinite(polygon).all():
                    invalid += 1
                    continue
                source_box = transform_box(boxes[index] * size, meta, inverse=True)
                encoded = project_roi_mask(_sigmoid(polygon[0]), source_box, (width, height))
                if not any(encoded["counts"][1::2]):
                    empty_masks += 1
                    continue
                item = {"class_id": 0, "class_name": "player", "confidence": float(scores[index]),
                        "mask": encoded, "wrapper": _mask_preview(encoded, source_box),
                        "wrapper_is_approximate": True, "wrapper_kind": "mask_convex_envelope_preview"}
                if debug:
                    item["primary_box"] = source_box
                detections.append(item)
                continue
            restored = transform_points(polygon * size, meta, inverse=True)
            restored[:, 0] = restored[:, 0].clip(0, width)
            restored[:, 1] = restored[:, 1].clip(0, height)
            if (not np.isfinite(restored).all() or polygon_area(restored) <= 1e-4
                    or not _simple_prediction(restored)):
                invalid += 1
                continue
            item = {"class_id": 0, "class_name": "player", "confidence": float(scores[index]),
                    "wrapper": restored.tolist()}
            if debug:
                item["primary_box"] = transform_box(boxes[index] * size, meta, inverse=True)
            detections.append(item)
        diagnostics = None
        if debug:
            primary_detections = [
                {"class_id": 0, "class_name": "player", "confidence": float(scores[index]),
                 "primary_box": transform_box(boxes[index] * size, meta, inverse=True)}
                for index in selected
            ]
            diagnostics = {"primary_detections": primary_detections,
                           "primary_count": len(primary_detections),
                           "max_confidence": float(scores[eligible].max()) if np.any(eligible) else None,
                           "raw_max_confidence": float(scores[np.isfinite(scores)].max()) if np.isfinite(scores).any() else None,
                           "candidates_above_threshold": int(np.count_nonzero(valid)),
                           "confidence_threshold": float(confidence)}
        finished = time.perf_counter()
        preprocessing = preprocess_done - started
        total = finished - started
        result = {"detections": detections, "invalid_contours": invalid,
                  "timing_ms": {"preprocess": preprocessing * 1000,
                                "inference": inference_seconds * 1000,
                                "postprocess": max(0, total - preprocessing - inference_seconds) * 1000,
                                "total": total * 1000}}
        if self.config.is_mask:
            result["empty_masks"] = empty_masks
        if diagnostics is not None:
            diagnostics["stage_timing_ms"] = {
                "detector": detector_seconds * 1000, "encasing": encasing_seconds * 1000,
                "filter": (filtering_done - detector_done) * 1000,
                "geometry": (finished - geometry_started) * 1000}
            result["diagnostics"] = diagnostics
        return result
