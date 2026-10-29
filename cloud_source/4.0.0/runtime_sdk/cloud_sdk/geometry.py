"""Geometry shared by annotation, training, and every inference backend.

Coordinates are continuous image pixels, origin at the top left. A wrapper is
one simple closed boundary; the last vertex need not repeat the first.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageOps

EPS = 1e-6
MAX_LABEL_VERTICES = 2048


def _points(points) -> np.ndarray:
    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 2 or not np.isfinite(array).all():
        raise ValueError("Polygon coordinates must be finite [x, y] pairs.")
    if len(array) > 1 and np.linalg.norm(array[-1] - array[0]) <= EPS:
        array = array[:-1]
    return array


def signed_area(points) -> float:
    points = _points(points)
    if len(points) < 3:
        return 0.0
    other = np.roll(points, -1, axis=0)
    return float(np.sum(points[:, 0] * other[:, 1] - other[:, 0] * points[:, 1]) / 2)


def polygon_area(points) -> float:
    try:
        return abs(signed_area(points))
    except (ValueError, TypeError):
        return 0.0


def _cross(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def is_simple_polygon(points) -> bool:
    """Reject crossings, repeated nonadjacent vertices, and overlapping edges."""
    try:
        p = _points(points)
    except (ValueError, TypeError):
        return False
    n = len(p)
    if not 3 <= n <= MAX_LABEL_VERTICES or polygon_area(p) <= EPS:
        return False
    following = np.roll(p, -1, axis=0)
    edges = following - p
    if np.any(np.linalg.norm(edges, axis=1) <= EPS):
        return False
    # Adjacent collinear edges may continue straight, but may not double back.
    previous = np.roll(edges, 1, axis=0)
    if np.any((np.abs(_cross(previous, edges)) <= EPS) &
              (np.sum(previous * edges, axis=1) < -EPS)):
        return False
    for i in range(n):
        js = np.arange(i + 2, n)
        if i == 0:
            js = js[js != n - 1]
        if not len(js):
            continue
        a, b = p[i], following[i]
        c, d = p[js], following[js]
        ab, cd = b - a, d - c
        o1, o2 = _cross(ab, c - a), _cross(ab, d - a)
        o3, o4 = _cross(cd, a - c), _cross(cd, b - c)
        bbox = np.all(np.maximum(np.minimum(a, b), np.minimum(c, d)) <=
                      np.minimum(np.maximum(a, b), np.maximum(c, d)) + EPS, axis=1)
        opposite1 = ((o1 <= EPS) & (o2 >= -EPS)) | ((o2 <= EPS) & (o1 >= -EPS))
        opposite2 = ((o3 <= EPS) & (o4 >= -EPS)) | ((o4 <= EPS) & (o3 >= -EPS))
        if np.any(bbox & opposite1 & opposite2):
            return False
    return True


def validate_annotation(ann, width, height, require_complete=True) -> list[str]:
    errors = []
    if not isinstance(ann, dict):
        return ["Annotation must be an object."]
    if ann.get("class_id") != 0:
        errors.append("This version supports class 0 (player) only.")
    try:
        box = np.asarray(ann.get("box"), dtype=np.float64)
        if box.shape != (4,) or not np.isfinite(box).all():
            raise ValueError
        if box[2] - box[0] <= EPS or box[3] - box[1] <= EPS:
            errors.append("Main box must have positive width and height.")
        if box[0] < -EPS or box[1] < -EPS or box[2] > width + EPS or box[3] > height + EPS:
            errors.append("Main box must be inside the image.")
    except (ValueError, TypeError):
        box = None
        errors.append("Main box requires four finite coordinates [left, top, right, bottom].")
    # An S2 raster mask is authoritative. An optional polygon is only a
    # compatibility preview and cannot represent holes or separate islands.
    has_mask = ann.get("mask") is not None
    if has_mask:
        from .masks import validate_mask
        try:
            validate_mask(ann["mask"], width, height, box=box, allow_empty=False)
        except (ValueError, TypeError, OverflowError) as exc:
            errors.append("Encasing mask is invalid: " + str(exc))
    wrapper = ann.get("wrapper", [])
    if wrapper is None:
        wrapper = []
    if not isinstance(wrapper, (list, tuple, np.ndarray)):
        return errors + ["Encasing must be a list of [x, y] vertices."]
    if not len(wrapper):
        if require_complete and not has_mask:
            errors.append("Encasing is missing: draw the second label before reviewing.")
        return errors
    try:
        polygon = _points(wrapper)
    except (ValueError, TypeError):
        return errors + ["Encasing vertices must be finite [x, y] coordinates."]
    if not 3 <= len(polygon) <= MAX_LABEL_VERTICES:
        errors.append(f"Encasing needs 3 to {MAX_LABEL_VERTICES} vertices.")
    elif not is_simple_polygon(polygon):
        errors.append("Encasing must enclose an area without crossing or overlapping itself.")
    if np.any(polygon < -EPS) or np.any(polygon[:, 0] > width + EPS) or np.any(polygon[:, 1] > height + EPS):
        errors.append("Encasing vertices must be inside the image.")
    if box is not None and len(polygon):
        if np.any(polygon < box[:2] - EPS) or np.any(polygon > box[2:] + EPS):
            errors.append("The main box must contain the full encasing; adjust the box or outline.")
    return errors


def resample_polygon(points, count) -> np.ndarray:
    """Uniform arc-length samples, clockwise in image coordinates, topmost start.

    Starting-point normalization assists labeling consistency; the trainer also
    uses cyclic matching because a contour has no privileged first vertex.
    """
    if not isinstance(count, (int, np.integer)) or count < 3 or count > MAX_LABEL_VERTICES:
        raise ValueError("Sample count must be an integer from 3 to 2048.")
    p = _points(points)
    if len(p) < 3 or polygon_area(p) <= EPS:
        raise ValueError("Cannot resample an empty or degenerate encasing.")
    if signed_area(p) < 0:
        p = p[::-1]
    start = np.lexsort((p[:, 0], p[:, 1]))[0]
    p = np.roll(p, -int(start), axis=0)
    lengths = np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1)
    keep = lengths > EPS
    if not np.all(keep):
        p = p[keep]
        lengths = np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1)
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    targets = np.arange(count, dtype=np.float64) * cumulative[-1] / count
    indexes = np.searchsorted(cumulative, targets, side="right") - 1
    indexes = np.clip(indexes, 0, len(p) - 1)
    alpha = (targets - cumulative[indexes]) / lengths[indexes]
    result = p[indexes] + alpha[:, None] * (p[(indexes + 1) % len(p)] - p[indexes])
    return result.astype(np.float32)


def box_iou(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.shape != (4,) or b.shape != (4,) or not np.isfinite(a).all() or not np.isfinite(b).all():
        return 0.0
    intersection = np.maximum(np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2]), 0)
    area = float(np.prod(intersection))
    union = float(np.prod(np.maximum(a[2:] - a[:2], 0)) + np.prod(np.maximum(b[2:] - b[:2], 0)) - area)
    return area / union if union > EPS else 0.0


def polygon_iou(a, b, width, height) -> float:
    """Raster IoU on the original image pixel grid (evaluation only).

    Rasterization includes boundary pixels as Pillow ImageDraw defines them;
    this is a documented approximation to continuous polygon IoU, not COCO AP.
    Only the joint bounding region is allocated.
    """
    if not is_simple_polygon(a) or not is_simple_polygon(b):
        return 0.0
    a, b = _points(a), _points(b)
    both = np.concatenate((a, b))
    low = np.maximum(np.floor(both.min(axis=0)), 0).astype(int)
    high = np.minimum(np.ceil(both.max(axis=0)) + 1, [int(width), int(height)]).astype(int)
    shape = high - low
    if np.any(shape <= 0):
        return 0.0
    if int(shape[0]) * int(shape[1]) > 100_000_000:
        raise ValueError("Polygon evaluation region exceeds 100 million pixels.")
    masks = []
    for polygon in (a, b):
        mask = Image.new("1", tuple(int(x) for x in shape), 0)
        ImageDraw.Draw(mask).polygon([tuple(v) for v in polygon - low], fill=1)
        masks.append(np.asarray(mask, dtype=bool))
    union = np.count_nonzero(masks[0] | masks[1])
    return float(np.count_nonzero(masks[0] & masks[1]) / union) if union else 0.0


def _boundary_samples(points):
    p = _points(points)
    perimeter = np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1).sum()
    return resample_polygon(p, max(32, min(2048, math.ceil(perimeter)))).astype(np.float64)


def _distances_to_boundary(samples, polygon):
    p = _points(polygon)
    edges = np.roll(p, -1, axis=0) - p
    lengths = np.maximum(np.sum(edges * edges, axis=1), EPS)
    chunks = []
    for begin in range(0, len(samples), 256):
        delta = samples[begin:begin + 256, None, :] - p[None, :, :]
        alpha = np.clip(np.sum(delta * edges[None, :, :], axis=2) / lengths[None, :], 0, 1)
        distance = np.linalg.norm(delta - alpha[:, :, None] * edges[None, :, :], axis=2)
        chunks.append(distance.min(axis=1))
    return np.concatenate(chunks)


def boundary_f_score(a, b, tolerance=2.0) -> float:
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Boundary tolerance must be finite and nonnegative.")
    if not is_simple_polygon(a) or not is_simple_polygon(b):
        return 0.0
    precision = np.mean(_distances_to_boundary(_boundary_samples(a), b) <= tolerance)
    recall = np.mean(_distances_to_boundary(_boundary_samples(b), a) <= tolerance)
    return float(2 * precision * recall / (precision + recall)) if precision + recall else 0.0


def letterbox(image: Image.Image, size: int):
    if not isinstance(size, int) or not 32 <= size <= 2048:
        raise ValueError("Input side must be an integer from 32 to 2048.")
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    scale = min(size / width, size / height)
    new_width = max(1, min(size, round(width * scale)))
    new_height = max(1, min(size, round(height * scale)))
    pad_x, pad_y = (size - new_width) // 2, (size - new_height) // 2
    resized = image.resize((new_width, new_height), Image.Resampling.BILINEAR)
    output = Image.new("RGB", (size, size), (114, 114, 114))
    output.paste(resized, (pad_x, pad_y))
    # Rounding resized dimensions gives slightly different exact x/y scales.
    meta = {"scale": scale, "scale_x": new_width / width, "scale_y": new_height / height,
            "pad_x": pad_x, "pad_y": pad_y, "original_width": width,
            "original_height": height, "size": size}
    array = np.asarray(output, dtype=np.float32).transpose(2, 0, 1) / 255.0
    return np.ascontiguousarray(array), meta


def transform_points(points, meta, inverse=False) -> np.ndarray:
    values = np.asarray(points, dtype=np.float32)
    if values.size == 0:
        return values.reshape(0, 2)
    if values.ndim != 2 or values.shape[1] != 2:
        raise ValueError("Expected N by 2 points.")
    scale = np.array([meta.get("scale_x", meta["scale"]), meta.get("scale_y", meta["scale"])], dtype=np.float32)
    pad = np.array([meta["pad_x"], meta["pad_y"]], dtype=np.float32)
    return (values - pad) / scale if inverse else values * scale + pad


def transform_box(box, meta, inverse=False) -> list[float]:
    return transform_points(np.asarray(box).reshape(2, 2), meta, inverse).reshape(4).tolist()
