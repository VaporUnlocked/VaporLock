"""Background source annotation and Ultralytics dataset packaging for Vapor Lock.

This worker never talks to the live native engine. It receives only paths and
annotation/export options, runs below normal priority, and reports JSON lines
to the Control Center.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import io
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageEnhance, ImageOps
from dataset_storage import atomic_text, commit_annotations, dataset_lock, recover_annotations
from dataset_transaction import AnnotationCommitUncertain
from annotation_provider import BACKENDS, AnnotationProviderError, create_annotation_session


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def additive_label_paths(labels, check=lambda: None):
    """Bound discovery before sorting, including non-label entries/directories."""
    from annotation_output import held_directory
    if not labels.exists():return []
    found=[];pending=[(labels,0)];visited=0
    check()
    while pending:
        directory,depth=pending.pop()
        with held_directory(directory),os.scandir(directory) as entries:
            for entry in entries:
                visited+=1
                if visited>20000:raise ValueError('Additive label discovery exceeds the supported entry limit.')
                if visited%64==0:check()
                if entry.is_symlink() or getattr(entry.stat(follow_symlinks=False),'st_file_attributes',0)&0x400:
                    raise ValueError('Additive labels cannot traverse linked folders or files.')
                if entry.is_dir(follow_symlinks=False):
                    if depth>=64:raise ValueError('Additive label discovery exceeds the supported folder depth.')
                    pending.append((Path(entry.path),depth+1))
                elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower()=='.txt':
                    if len(found)>=10000:raise ValueError('Additive label discovery exceeds the supported file limit.')
                    found.append(Path(entry.path))
    check()
    return sorted(found)


@dataclass
class Annotation:
    class_id: int
    score: float
    box: tuple[float, float, float, float]
    mask_coefficients: np.ndarray | None = None
    polygon: list[tuple[float, float]] = field(default_factory=list)


def emit(event: str, **payload: object) -> None:
    print(json.dumps({"type": event, **payload}, separators=(",", ":")), flush=True)


def lower_priority() -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        except Exception:
            pass


def parse_names(session: ort.InferenceSession) -> list[str]:
    text = session.get_modelmeta().custom_metadata_map.get("names", "")
    try:
        value = ast.literal_eval(text)
        if isinstance(value, (tuple, list)):
            return [str(name) for name in value]
        if isinstance(value, dict) and value:
            pairs = {int(key): str(name) for key, name in value.items()}
            if min(pairs) < 0 or max(pairs) > 100000:
                return []
            return [pairs.get(index, f"class-{index}") for index in range(max(pairs) + 1)]
    except (ValueError, SyntaxError, TypeError):
        pass
    return []


def iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    overlap = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1]) + max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    return overlap / max(1e-9, area - overlap)


def nms(items: list[Annotation], threshold: float) -> list[Annotation]:
    if type(threshold) not in (int,float) or not .01 <= threshold <= .99:
        raise ValueError('Invalid annotation NMS threshold.')
    kept: list[Annotation] = []
    for item in sorted(items, key=lambda item: item.score, reverse=True):
        if not any(item.class_id == accepted.class_id and iou(item.box, accepted.box) >= threshold for accepted in kept):
            kept.append(item)
    return kept


def finalize_annotations(items: list[Annotation], mapping: dict[int, int], threshold: float) -> list[Annotation]:
    from dataclasses import replace
    # Apply the output class identity before class-aware suppression.
    return nms([replace(item, class_id=mapping.get(item.class_id, item.class_id)) for item in items], threshold)


def input_image(path: Path, width: int, height: int, nchw: bool, input_type: str = "tensor(float)", pass_index: int = 0) -> tuple[np.ndarray, float, float, float, float, int, int]:
    # A job decodes each source once; all agreement views share its RGB pixels.
    if isinstance(path, Image.Image):
        image = path if path.mode == 'RGB' else path.convert('RGB')
    else:
        with Image.open(path) as source:
            image = source.convert("RGB")
    if pass_index % 2:
        image = ImageOps.mirror(image)
    if pass_index >= 2:
        # Distinct, geometry-preserving exposure views supplement mirrored views.
        image = ImageEnhance.Brightness(image).enhance(max(0.5, 1.0 - 0.04 * (pass_index // 2)))
    source_width, source_height = image.size
    scale = min(width / source_width, height / source_height)
    resized = image.resize((max(1, round(source_width * scale)), max(1, round(source_height * scale))), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (width, height), (114, 114, 114))
    pad_x = (width - resized.width) // 2
    pad_y = (height - resized.height) // 2
    canvas.paste(resized, (pad_x, pad_y))
    if input_type == "tensor(uint8)":
        values = np.asarray(canvas, dtype=np.uint8)
    elif input_type in ("tensor(float)", "tensor(float16)"):
        values = np.asarray(canvas, dtype=np.float32) / 255.0
        if input_type == "tensor(float16)":
            values = values.astype(np.float16)
    else:
        raise ValueError(f"Unsupported model image input type: {input_type}")
    if nchw:
        values = np.transpose(values, (2, 0, 1))[None, ...]
    else:
        values = values[None, ...]
    return np.ascontiguousarray(values), resized.width / source_width, resized.height / source_height, float(pad_x), float(pad_y), source_width, source_height


def looks_like_end_to_end(values: np.ndarray, mask_channels: int) -> bool:
    """Recognize [xyxy, confidence, class, optional mask coefficients]."""
    if values.shape[1] < 6 + mask_channels:
        return False
    probe = values[: min(128, len(values))]
    probe = probe[np.isfinite(probe[:, 4]) & (probe[:, 4] > 0.001)]
    if not len(probe):
        return False
    scores = probe[:, 4]
    classes = probe[:, 5]
    return bool(
        np.all(np.isfinite(scores))
        and np.all((scores >= -1e-4) & (scores <= 1.0001))
        and np.all(np.abs(classes - np.round(classes)) < 1e-3)
        and np.all(classes >= -1e-3)
        and np.all(np.isfinite(probe[:, :4]))
        and np.all(probe[:, 2] > probe[:, 0])
        and np.all(probe[:, 3] > probe[:, 1])
    )


def decode(output: np.ndarray, names: list[str], confidence: float, nms_iou: float, scale_x: float, scale_y: float, pad_x: float, pad_y: float, source_width: int, source_height: int, input_width: int, input_height: int, mask_channels: int = 0, output_format: str = "auto", coordinate_units: str = "pixels", *, candidate_limit=0, stats=None) -> list[Annotation]:
    if output.dtype.kind not in 'fiu' or not np.isfinite(output).all():
        raise ValueError('The annotation model returned nonfinite or nonnumeric detection output.')
    if output.ndim not in (2, 3) or (output.ndim == 3 and output.shape[0] != 1):
        raise ValueError('The annotation model returned an unsupported detection shape.')
    values = np.squeeze(output, axis=0) if output.ndim == 3 else output
    if values.ndim != 2:
        raise ValueError('The annotation model returned an unsupported detection shape.')
    classes = len(names)
    features = {4 + classes + mask_channels, 5 + classes + mask_channels} if classes else set()
    first, second = values.shape
    transposed = ((first in features and second not in features) or
                  ((first in features) == (second in features) and first >= 5 and first < second and second != 6 + mask_channels))
    if output_format == "end-to-end":
        transposed = False
    if transposed:
        values = values.T
    if values.shape[1] < 5:
        raise ValueError('The annotation model returned too few detection fields.')
    raw_without_objectness = 4 + classes + mask_channels if classes else -1
    raw_with_objectness = 5 + classes + mask_channels if classes else -1
    end_to_end = output_format == "end-to-end" or (output_format == "auto" and not transposed and
        values.shape[1] == 6 + mask_channels and looks_like_end_to_end(values, mask_channels))
    objectness = not end_to_end and (output_format == "yolo-objectness" or
        (output_format == "auto" and values.shape[1] == raw_with_objectness))
    expected_width = 6 + mask_channels if end_to_end else (5 if objectness else 4) + classes + mask_channels
    if ((end_to_end or classes) and values.shape[1] != expected_width
            or not end_to_end and values.shape[1] - (5 if objectness else 4) - mask_channels < 1):
        raise ValueError('The annotation model output does not match its class metadata or selected format.')
    result: list[Annotation] = []
    decoded: list[tuple[int, float, float, float, float, float, np.ndarray | None]] = []
    # Reject low-confidence rows in NumPy before creating Python objects.
    # Float64 reproduces the scalar float multiplication at the threshold.
    if end_to_end:
        scores = values[:, 4].astype(np.float64)
    else:
        start = 5 if objectness else 4
        count = min(classes, values.shape[1] - start - mask_channels) if classes else values.shape[1] - start - mask_channels
        scores = np.max(values[:, start:start + count], axis=1).astype(np.float64)
        if objectness:
            scores *= values[:, 4].astype(np.float64)
    eligible=np.flatnonzero(scores>=confidence)
    if candidate_limit:
        if stats is not None:stats['omitted']=max(0,len(eligible)-candidate_limit)
        eligible=eligible[np.argsort(-scores[eligible],kind='stable')[:candidate_limit]]
    values = values[eligible]
    for row in values:
        if end_to_end:
            x1, y1, x2, y2, score, class_id = map(float, row[:6])
            class_id = int(round(class_id))
            coefficients = row[6:6 + mask_channels].astype(np.float32, copy=True) if mask_channels and row.size >= 6 + mask_channels else None
        else:
            class_start = 5 if objectness else 4
            inferred = row.shape[0] - class_start - mask_channels
            class_count = min(classes, inferred) if classes else inferred
            if class_count <= 0:
                continue
            class_id = int(np.argmax(row[class_start:class_start + class_count]))
            score = float(row[class_start + class_id]) * (float(row[4]) if objectness else 1.0)
            x, y, w, h = map(float, row[:4])
            x1, y1, x2, y2 = x - w / 2, y - h / 2, x + w / 2, y + h / 2
            coefficient_start = class_start + class_count
            coefficients = row[coefficient_start:coefficient_start + mask_channels].astype(np.float32, copy=True) if mask_channels and row.size >= coefficient_start + mask_channels else None
        if not np.isfinite([score, x1, y1, x2, y2]).all() or score < confidence or class_id < 0 or (classes and class_id >= classes):
            continue
        decoded.append((class_id, score, x1, y1, x2, y2, coefficients))
    normalized = coordinate_units == "normalized"
    for class_id, score, x1, y1, x2, y2, coefficients in decoded:
        if normalized:
            x1, x2 = x1 * input_width, x2 * input_width
            y1, y2 = y1 * input_height, y2 * input_height
        x1 = max(0.0, min(source_width, (x1 - pad_x) / scale_x))
        y1 = max(0.0, min(source_height, (y1 - pad_y) / scale_y))
        x2 = max(0.0, min(source_width, (x2 - pad_x) / scale_x))
        y2 = max(0.0, min(source_height, (y2 - pad_y) / scale_y))
        if x2 > x1 and y2 > y1:
            result.append(Annotation(class_id, score, (x1, y1, x2, y2), coefficients))
    return result if end_to_end else nms(result, nms_iou)


def remove_collinear(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop straight grid-edge vertices before simplification."""
    result = points[:]
    changed = True
    while changed and len(result) > 3:
        changed = False
        simplified: list[tuple[float, float]] = []
        for index, point in enumerate(result):
            previous = result[index - 1]
            following = result[(index + 1) % len(result)]
            cross = (point[0] - previous[0]) * (following[1] - point[1]) - (point[1] - previous[1]) * (following[0] - point[0])
            if abs(cross) < 1e-6:
                changed = True
                continue
            simplified.append(point)
        result = simplified
    return result


def perpendicular_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return float(np.hypot(point[0] - start[0], point[1] - start[1]))
    return abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / float(np.hypot(dx, dy))


def rdp(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    distances = [perpendicular_distance(point, points[0], points[-1]) for point in points[1:-1]]
    maximum = max(distances, default=0.0)
    if maximum <= tolerance:
        return [points[0], points[-1]]
    split = 1 + distances.index(maximum)
    return rdp(points[:split + 1], tolerance)[:-1] + rdp(points[split:], tolerance)


def simplify_ring(points: list[tuple[float, float]], tolerance: float = 1.0) -> list[tuple[float, float]]:
    points = remove_collinear(points)
    if len(points) <= 4:
        return points
    start = 0
    end = max(range(1, len(points)), key=lambda index: (points[index][0] - points[start][0]) ** 2 + (points[index][1] - points[start][1]) ** 2)
    first = rdp(points[start:end + 1], tolerance)
    second = rdp(points[end:] + [points[start]], tolerance)
    return first[:-1] + second[:-1]


def mask_contour(mask: np.ndarray, *, strict=False) -> list[tuple[float, float]]:
    """Return the largest exterior boundary of a binary prototype mask."""
    height, width = mask.shape
    outgoing: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for y in range(height):
        if strict and len(outgoing)>8192:return []
        for x in range(width):
            if not mask[y, x]:
                continue
            if y == 0 or not mask[y - 1, x]:
                outgoing[(x, y)].append((x + 1, y))
            if x == width - 1 or not mask[y, x + 1]:
                outgoing[(x + 1, y)].append((x + 1, y + 1))
            if y == height - 1 or not mask[y + 1, x]:
                outgoing[(x + 1, y + 1)].append((x, y + 1))
            if x == 0 or not mask[y, x - 1]:
                outgoing[(x, y + 1)].append((x, y))
    rings: list[list[tuple[float, float]]] = []
    while outgoing:
        start = next(iter(outgoing))
        current = start
        ring: list[tuple[float, float]] = [tuple(map(float, start))]
        for _ in range(mask.size * 4):
            candidates = outgoing.get(current)
            if not candidates:
                break
            following = candidates.pop()
            if not candidates:
                outgoing.pop(current, None)
            current = following
            if current == start:
                if len(ring) >= 3:
                    rings.append(ring)
                break
            ring.append(tuple(map(float, current)))
    if not rings:
        return []
    if strict and (len(rings)!=1 or len(rings[0])>8192):return []
    def area(ring: list[tuple[float, float]]) -> float:
        return abs(sum(point[0] * ring[(index + 1) % len(ring)][1] - point[1] * ring[(index + 1) % len(ring)][0] for index, point in enumerate(ring))) * 0.5
    return simplify_ring(max(rings, key=area))


def instance_polygons(annotations: list[Annotation], prototype: np.ndarray, scale_x: float, scale_y: float, pad_x: float, pad_y: float, source_width: int, source_height: int, input_width: int, input_height: int, *, strict=False) -> list[Annotation]:
    prototypes = np.asarray(prototype, dtype=np.float32)
    if prototypes.ndim != 4 or prototypes.shape[0] != 1 or prototypes.shape[1] <= 0:
        return annotations
    channels, mask_height, mask_width = prototypes.shape[1:]
    flattened = prototypes[0].reshape(channels, -1)
    for annotation in annotations:
        coefficients = annotation.mask_coefficients
        if coefficients is None or coefficients.size != channels:
            continue
        logits = (coefficients @ flattened).reshape(mask_height, mask_width)
        binary = 1.0 / (1.0 + np.exp(-np.clip(logits, -60.0, 60.0))) >= 0.5
        x1, y1, x2, y2 = annotation.box
        input_x1, input_x2 = x1 * scale_x + pad_x, x2 * scale_x + pad_x
        input_y1, input_y2 = y1 * scale_y + pad_y, y2 * scale_y + pad_y
        xs = (np.arange(mask_width, dtype=np.float32) + 0.5) * input_width / mask_width
        ys = (np.arange(mask_height, dtype=np.float32) + 0.5) * input_height / mask_height
        binary &= (xs[None, :] >= input_x1) & (xs[None, :] <= input_x2) & (ys[:, None] >= input_y1) & (ys[:, None] <= input_y2)
        contour = mask_contour(binary,strict=strict)
        if len(contour) < 3:
            continue
        polygon: list[tuple[float, float]] = []
        for x, y in contour:
            source_x = max(0.0, min(source_width, (x * input_width / mask_width - pad_x) / scale_x))
            source_y = max(0.0, min(source_height, (y * input_height / mask_height - pad_y) / scale_y))
            polygon.append((source_x, source_y))
        if len(polygon) >= 3:
            annotation.polygon = polygon
    return annotations


def decode_semantic(output: np.ndarray, names: list[str], confidence: float, scale_x: float, scale_y: float, pad_x: float, pad_y: float, source_width: int, source_height: int, input_width: int, input_height: int) -> list[Annotation]:
    values = np.asarray(output)
    if values.ndim != 4 or values.shape[0] != 1 or values.shape[1] < 2:
        return []
    logits = values[0]
    shifted = logits - np.max(logits, axis=0, keepdims=True)
    probabilities = np.exp(shifted)
    probabilities /= np.sum(probabilities, axis=0, keepdims=True)
    labels = np.argmax(probabilities, axis=0)
    height, width = labels.shape
    visited = np.zeros((height, width), dtype=bool)
    detections: list[Annotation] = []
    minimum_pixels = max(4, labels.size // 20000)
    for start_y in range(height):
        for start_x in range(width):
            class_id = int(labels[start_y, start_x])
            if visited[start_y, start_x] or class_id == 0:
                continue
            queue = [(start_x, start_y)]
            visited[start_y, start_x] = True
            pixels: list[tuple[int, int]] = []
            score = 0.0
            while queue:
                x, y = queue.pop()
                pixels.append((x, y))
                score += float(probabilities[class_id, y, x])
                for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= next_x < width and 0 <= next_y < height and not visited[next_y, next_x] and int(labels[next_y, next_x]) == class_id:
                        visited[next_y, next_x] = True
                        queue.append((next_x, next_y))
            average = score / max(1, len(pixels))
            if len(pixels) < minimum_pixels or average < confidence:
                continue
            xs, ys = zip(*pixels)
            x1 = max(0.0, min(source_width, (min(xs) * input_width / width - pad_x) / scale_x))
            y1 = max(0.0, min(source_height, (min(ys) * input_height / height - pad_y) / scale_y))
            x2 = max(0.0, min(source_width, ((max(xs) + 1) * input_width / width - pad_x) / scale_x))
            y2 = max(0.0, min(source_height, ((max(ys) + 1) * input_height / height - pad_y) / scale_y))
            if x2 > x1 and y2 > y1:
                detections.append(Annotation(class_id, average, (x1, y1, x2, y2)))
    return detections


def dataset_class_names(root: Path) -> list[str]:
    path = root / "classes.txt"
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()] if path.is_file() else []
    except OSError:
        return []


def extend_class_names(names: list[str], highest_class: int) -> list[str]:
    result = list(names)
    while len(result) <= highest_class:
        result.append(f"class-{len(result)}")
    return [name or f"class-{index}" for index, name in enumerate(result)]


def highest_label_class(labels: Path) -> int:
    highest = -1
    if not labels.is_dir():
        return highest
    for path in labels.rglob("*.txt"):
        try:
            rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for row in rows:
            fields = row.split(maxsplit=1)
            try:
                highest = max(highest, int(fields[0])) if fields else highest
            except ValueError:
                continue
    return highest


def unique_image_stems(paths: list[Path]) -> None:
    stems = set()
    for path in paths:
        stem = path.stem.casefold()
        if stem in stems:
            raise ValueError(f"Images share the label name {path.stem}.txt. Rename duplicate image stems before processing.")
        stems.add(stem)


def class_mapping(existing: list[str], model: list[str]) -> tuple[list[str], dict[int, int]]:
    names = list(existing)
    mapping = {}
    for index, name in enumerate(model):
        name = name or f"class-{index}"
        matches = [i for i, item in enumerate(names) if item.casefold() == name.casefold()]
        if len(matches) > 1:
            raise ValueError(f"The dataset has duplicate class names: {name}. Rename them before annotation.")
        if matches:
            mapping[index] = matches[0]
        else:
            mapping[index] = len(names)
            names.append(name)
    return names, mapping


def run_annotation(args: argparse.Namespace) -> int:
    from annotation_output import plain,held_directory
    from dataset_transaction import HeldFile
    import hashlib
    root=plain(args.dataset)
    if getattr(args,'expected_source_signature',None):
        from annotation_preview import source_signature
        if source_signature(root)!=args.expected_source_signature:
            raise ValueError('Annotation sources changed. Refresh the effects preview.')
    model=plain(args.model)
    args.dataset=str(root);args.model=str(model)
    with held_directory(model.parent),HeldFile(model,read_only=True) as held_model:
        model_hash=hashlib.sha256();size=0
        for block in held_model.blocks():
            size+=len(block)
            if size>1024**3:raise ValueError('Annotation model exceeds the supported byte limit.')
            model_hash.update(block)
        expected=getattr(args,'expected_model_sha256',None)
        if expected is not None and expected!=model_hash.hexdigest():raise ValueError('Annotation model changed.')
        args.annotation_model_sha256=model_hash.hexdigest()
        return _run_annotation(args)


def _decode_batch_image(outputs, model_names, args, geometry, width, height, output_format, coordinate_units, pass_index):
    sx,sy,px,py,source_width,source_height=geometry
    detection = next((np.asarray(item) for item in outputs if np.asarray(item).ndim in (2, 3)), None)
    prototype = next((np.asarray(item) for item in outputs if np.asarray(item).ndim == 4 and np.asarray(item).shape[0] == 1 and np.asarray(item).shape[1] > 0), None)
    if detection is not None:
        mask_channels = int(prototype.shape[1]) if prototype is not None else 0
        annotations = decode(detection, model_names, args.confidence, args.nms_iou, sx, sy, px, py, source_width, source_height, width, height, mask_channels, output_format, coordinate_units)
        if prototype is not None:
            annotations = instance_polygons(annotations, prototype, sx, sy, px, py, source_width, source_height, width, height)
    elif prototype is not None:
        raise ValueError("Semantic segmentation is not supported. Choose a detection or instance-segmentation model.")
    else:
        raise ValueError("The model did not return a supported detection output.")
    if pass_index % 2:
        for annotation in annotations:
            x1, y1, x2, y2 = annotation.box
            annotation.box = (source_width - x2, y1, source_width - x1, y2)
            annotation.polygon = [(source_width - x, y) for x, y in reversed(annotation.polygon)]
    return annotations


def _run_annotation(args: argparse.Namespace) -> int:
    root = Path(os.path.abspath(args.dataset))
    images = root / "images"
    labels = root / "labels"
    model = Path(args.model).resolve()
    if not images.is_dir():
        raise ValueError(f"No source images folder exists at: {images}")
    if not model.is_file() or model.suffix.lower() != ".onnx":
        raise ValueError(f"The annotation model is not an existing .onnx file: {model}")
    additive = bool(getattr(args, 'additive', False))
    if additive and args.replace:
        raise ValueError('Choose additive or replace annotation, not both.')
    if additive and getattr(args,'delete_unannotated',False):
        raise ValueError('Additive annotation cannot delete images.')
    if not args.replace and not additive and labels.exists() and any(labels.glob("*.txt")):
        raise ValueError("Annotation labels already exist. Enable replace labels to overwrite them.")
    emit('annotation_stage', stage='initializing')
    initialized_at = time.monotonic()
    session, backend = create_annotation_session(ort, str(model), getattr(args, 'provider', 'auto'))
    emit('provider', requested=backend.requested, selected=backend.selected,
         providers=backend.active_providers, attempts=backend.attempts,
         device_index=backend.device_index, node_execution_verified=False,
         initialization_ms=round((time.monotonic()-initialized_at)*1000, 1))
    input_meta = next((item for item in session.get_inputs() if len(item.shape) == 4 and (item.shape[1] == 3 or item.shape[3] == 3)), None)
    if input_meta is None:
        raise ValueError("The annotation model needs one four-dimensional NCHW or NHWC three-channel image input.")
    shape = input_meta.shape
    nchw = shape[1] == 3
    nhwc = shape[3] == 3

    def static_dimension(value: object, fallback: int = 640) -> int:
        return int(value) if isinstance(value, int) and value > 0 else fallback

    height = static_dimension(shape[2 if nchw else 1])
    width = static_dimension(shape[3 if nchw else 2])
    model_names = parse_names(session)
    expected_labels = {}
    original_labels = {}
    if additive:
        from dataset_transaction import snapshot_bytes
        from annotation_labels import parse_labels
        classes_path=root/'classes.txt'
        classes_snapshot,classes_bytes=snapshot_bytes(classes_path,maximum=1024*1024) if classes_path.exists() else (None,b'')
        expected_labels[classes_path]=classes_snapshot
        # Parse every existing label before any inference, including unpaired labels.
        highest_existing=-1
        original_label_bytes=0
        def discovery_check():
            if callable(getattr(args,'check_cancel',None)):args.check_cancel()
        for label in additive_label_paths(labels,discovery_check):
            snapshot,data=snapshot_bytes(label,maximum=1024*1024)
            original_label_bytes+=len(data)
            if original_label_bytes>32*1024*1024 or len(original_labels)>=10000:
                raise ValueError('Additive label snapshot exceeds the supported limit.')
            rows=parse_labels(data)
            highest_existing=max([highest_existing]+[r['class_id'] for r in rows])
            original_labels[label]=(snapshot,data)
        existing_names=extend_class_names(classes_bytes.decode('utf-8-sig').splitlines(),highest_existing)
    else:
        existing_names = extend_class_names(dataset_class_names(root), highest_label_class(labels))
    if existing_names and not model_names:
        raise ValueError("This model has no class-name metadata. Use a model with class names when annotating an existing class map.")
    names, mapping = class_mapping(existing_names, model_names)
    metadata = session.get_modelmeta().custom_metadata_map
    output_format = getattr(args, "output_format", "auto")
    if output_format == "auto" and str(metadata.get("end2end", "")).casefold() in ("true", "1"):
        output_format = "end-to-end"
    coordinate_units = getattr(args, "coordinate_units", "auto")
    if coordinate_units == "auto":
        coordinate_units = metadata.get("coordinate_units", "pixels")
    if coordinate_units not in ("pixels", "normalized"):
        raise ValueError("Model coordinate_units must be pixels or normalized.")
    image_paths = [path for path in sorted(images.iterdir()) if path.suffix.lower() in IMAGE_EXTENSIONS]
    if not image_paths:
        raise ValueError("The dataset images folder contains no supported images.")
    unique_image_stems(image_paths)
    highest_class = -1
    staged_labels: dict[Path, str] = {}
    # Absent labels are part of the original snapshot too.
    if additive:
        for image_path in image_paths:
            original_labels.setdefault(labels/(image_path.stem+'.txt'),(None,b''))
    added_total=preserved_total=suppressed_total=0
    removals = []
    protected = 0
    annotated = 0
    effects_eligible=set()
    source_snapshots={}
    expected_samples=json.loads(getattr(args,'expected_preview_samples',None) or '{}')
    if (type(expected_samples) is not dict or len(expected_samples)>3 or any(
            type(name) is not str or Path(name).name!=name or name not in {p.name for p in image_paths}
            or type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None
            for name,value in expected_samples.items())):
        raise ValueError('Invalid effects preview samples.')
    delete_empty = getattr(args, 'delete_unannotated', False)
    emit("started", images=len(image_paths), classes=names, model=model.name)
    from annotation_batch import batch_plan, run_pass, MAX_DECODED_BYTES
    batch_size, batch_reason = batch_plan(shape, getattr(args,'batch_size',1), width, height,
                                         1 if getattr(input_meta,'type','')=='tensor(uint8)' else 4)
    fixed_size = shape[0] if type(shape[0]) is int else None
    emit('annotation_batch', requested=getattr(args,'batch_size',1), effective=batch_size, reason=batch_reason)
    for offset in range(0,len(image_paths),batch_size):
        group=[]; decoded_bytes=0; encoded_bytes=0
        try:
            for path in image_paths[offset:offset+batch_size]:
                getattr(args,'check_cancel',lambda:None)()
                image_started=time.monotonic(); label_path=labels/f'{path.stem}.txt'
                from dataset_transaction import snapshot_bytes
                from annotation_output import MAX_IMAGE_BYTES,digest
                image_snapshot,image_bytes=snapshot_bytes(path,maximum=MAX_IMAGE_BYTES)
                encoded_bytes += len(image_bytes)
                if encoded_bytes > MAX_DECODED_BYTES: raise ValueError('Annotation image batch memory limit exceeded.')
                source_snapshots[path]=image_snapshot
                if path.name in expected_samples and digest(image_bytes)!=expected_samples[path.name]:
                    raise ValueError('Annotation preview sample changed. Refresh the effects preview.')
                label_snapshot=None; protected_label=False
                label_bytes=b''
                if delete_empty or additive:
                    label_snapshot,label_bytes=snapshot_bytes(label_path,maximum=1024*1024) if label_path.exists() else (None,b'')
                    protected_label=label_snapshot is not None and bool(label_bytes.decode('utf-8').strip())
                if additive:
                    from annotation_labels import parse_labels
                    label_snapshot,label_bytes=original_labels[label_path]
                    parse_labels(label_bytes)
                    expected_labels[label_path]=label_snapshot
                with Image.open(io.BytesIO(image_bytes)) as encoded_image:
                    decoded_bytes += encoded_image.width*encoded_image.height*3
                    if decoded_bytes > MAX_DECODED_BYTES: raise ValueError('Annotation decoded-image batch memory limit exceeded.')
                    decoded_image=encoded_image.convert('RGB')
                group.append(dict(path=path,image=decoded_image,snapshot=image_snapshot,label_snapshot=label_snapshot,
                                  protected=protected_label,label_bytes=label_bytes,started=image_started,passes=[],
                                  timing={'load_ms':(time.monotonic()-image_started)*1000,'preprocess_ms':0.,'inference_ms':0.,'postprocess_ms':0.}))
            for pass_index in range(max(1,args.passes)):
                getattr(args,'check_cancel',lambda:None)()
                tensors=[];geometry=[]
                for item in group:
                    tick=time.monotonic()
                    prepared=input_image(item['image'],width,height,nchw,getattr(input_meta,'type','tensor(float)'),pass_index)
                    tensors.append(prepared[0]);geometry.append(prepared[1:])
                    item['geometry']=prepared[1:]
                    item['timing']['preprocess_ms']+=(time.monotonic()-tick)*1000
                tick=time.monotonic(); outputs=run_pass(session,input_meta.name,tensors,fixed_size)
                inference_ms=(time.monotonic()-tick)*1000/len(group)
                for item,values,transform in zip(group,outputs,geometry):
                    tick=time.monotonic()
                    item['passes'].append(_decode_batch_image(values,model_names,args,transform,width,height,output_format,coordinate_units,pass_index))
                    item['timing']['inference_ms']+=inference_ms
                    item['timing']['postprocess_ms']+=(time.monotonic()-tick)*1000
                del tensors,outputs
                emit('annotation_stage',stage='annotating',current=offset+1,total=len(image_paths),
                     pass_current=pass_index+1,passes=max(1,args.passes))
        finally:
            for item in group:item['image'].close()
        for position,item in enumerate(group):
            getattr(args,'check_cancel',lambda:None)()
            index=offset+position+1; path=item['path']; label_path=labels/f'{path.stem}.txt'
            image_snapshot=item['snapshot'];label_snapshot=item['label_snapshot'];protected_label=item['protected']
            protected+=int(protected_label);image_started=item['started'];timing=item['timing'];all_passes=item['passes']
            source_width,source_height=item['geometry'][-2:]
            tick = time.monotonic()
            consensus: list[Annotation] = []
            used = [set() for _ in all_passes]
            for candidate in all_passes[0]:
                matched = [candidate]
                matched_indices = []
                for pass_index, later in enumerate(all_passes[1:], 1):
                    choices = [(iou(item.box, candidate.box), i, item) for i, item in enumerate(later)
                               if i not in used[pass_index] and item.class_id == candidate.class_id and iou(item.box, candidate.box) >= args.consensus_iou]
                    if not choices:
                        break
                    _, match_index, same = max(choices, key=lambda item: item[0])
                    matched_indices.append((pass_index, match_index))
                    matched.append(same)
                if len(matched) == len(all_passes):
                    for pass_index, match_index in matched_indices:
                        used[pass_index].add(match_index)
                    boxes = np.asarray([item.box for item in matched])
                    consensus.append(Annotation(candidate.class_id, float(np.mean([item.score for item in matched])), tuple(np.median(boxes, axis=0)), polygon=candidate.polygon))
            # Consensus may shift boxes into overlap; remapping can merge classes.
            # End-to-end models also receive this explicit final-label suppression.
            consensus = finalize_annotations(consensus, mapping, args.nms_iou)
            timing['consensus_ms'] = (time.monotonic()-tick)*1000
            rows = []
            for annotation in consensus:
                highest_class = max(highest_class, annotation.class_id)
                if len(annotation.polygon) >= 3:
                    coordinates = " ".join(f"{x / source_width:.6f} {y / source_height:.6f}" for x, y in annotation.polygon)
                    rows.append(f"{annotation.class_id} {coordinates}\n")
                else:
                    x1, y1, x2, y2 = annotation.box
                    rows.append(f"{annotation.class_id} {(x1 + x2) / 2 / source_width:.6f} {(y1 + y2) / 2 / source_height:.6f} {(x2 - x1) / source_width:.6f} {(y2 - y1) / source_height:.6f}\n")
            if additive:
                from annotation_labels import append_predictions
                merged, added = append_predictions(item['label_bytes'],rows,args.nms_iou)
                preserved_total+=len(parse_labels(item['label_bytes']))
                added_total+=added;suppressed_total+=len(rows)-added
                rows=[merged] if merged else []
            annotated += int(bool(rows))
            if rows:effects_eligible.add(path.name)
            if delete_empty and not rows and not protected_label:
                removals.append({'image': path, 'image_snapshot': image_snapshot,
                                 'label': label_path, 'label_snapshot': label_snapshot})
            elif not (delete_empty and protected_label and not rows):
                staged_labels[label_path] = "".join(rows)
            emit("progress", current=index, total=len(image_paths), file=path.name, annotations=len(consensus), segmentation=sum(bool(item.polygon) for item in consensus),
                 passes=max(1,args.passes), timing={key:round(value,1) for key,value in timing.items()},
                 elapsed_ms=round((time.monotonic()-image_started)*1000,1))
    names = extend_class_names(names, highest_class)
    # IDs already in the dataset never change meaning. Publish the append-only
    # map and invalidate review decisions before atomically replacing labels.
    review_path = root / ".vaporlock-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else {}
    decisions = review.get("decisions", {})
    for path in image_paths:
        decisions.pop(path.relative_to(root).as_posix(), None)
    review["decisions"] = decisions
    updates = {root / "classes.txt": "\n".join(names) + ("\n" if names else ""),
               review_path: json.dumps(review, indent=2), **staged_labels}
    labels.mkdir(parents=True, exist_ok=True)
    from contextlib import ExitStack
    from dataset_transaction import HeldFile
    # Preserve exact inference sources through label publication. An image edit
    # cannot receive labels calculated from older bytes.
    with ExitStack() as source_guards:
        from annotation_output import held_directory
        source_guards.enter_context(held_directory(images))
        if additive:
            for label,(expected,_data) in original_labels.items():
                if label not in updates and expected is not None:
                    if source_guards.enter_context(HeldFile(label,read_only=True)).snapshot()!=expected:
                        raise ValueError('Existing labels changed during annotation.')
        removal_paths={item['image'] for item in removals}
        for path,snapshot in source_snapshots.items():
            if path in removal_paths:continue  # transaction holds/rechecks its removal handles
            if source_guards.enter_context(HeldFile(path,read_only=True)).snapshot()!=snapshot:
                raise ValueError('Annotation source changed during inference. Existing labels were preserved.')
        getattr(args,'check_cancel',lambda:None)()
        result = commit_annotations(root, updates, removals,
            expected_updates={p:v for p,v in expected_labels.items() if p in updates})
    emit("complete", images=len(image_paths), annotated=annotated, removed=result['removed'],
         protected=protected, failed=0, cleanup_pending=result['cleanup_pending'], classes=names, segmentation=True,
         additive=additive, preserved_labels=preserved_total, added_labels=added_total, suppressed_labels=suppressed_total)
    if getattr(args, 'variations_enabled', False):
        if result['cleanup_pending']:
            emit('effects_skipped', code='RECOVERY_REQUIRED', labels_committed=True)
            return 2
        from annotation_output import generate_variations
        try:
            expected_context={path.name:{'image':source_snapshots[path],
                'labelSHA256':digest(staged_labels[labels/(path.stem+'.txt')].encode('utf-8'))}
                for path in image_paths if path.name in effects_eligible}
            generate_variations(root, variation_options(args), seed=getattr(args,'variation_seed',None),
                eligible_images=effects_eligible,expected_context=expected_context,
                expected_classes_sha256=digest(updates[root/'classes.txt'].encode('utf-8')),emit=emit)
        except AnnotationCommitUncertain:
            emit('effects_error', labels_committed=True, outcome_uncertain=True,
                 code='RECOVERY_REQUIRED')
            return 2
        except (ValueError, OSError):
            emit('effects_error', labels_committed=True, outcome_uncertain=False,
                 code='IMAGE_EFFECTS_FAILED')
            return 1
    return 0


def run_package(args: argparse.Namespace) -> int:
    root = Path(args.dataset).resolve()
    images, labels = root / "images", root / "labels"
    names = dataset_class_names(root)
    labels.mkdir(parents=True, exist_ok=True)
    review_path = root / ".vaporlock-review.json"
    try:
        review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else {}
        excluded = {str(key) for key, value in review.get("decisions", {}).items() if value == "rejected"} if isinstance(review, dict) else set()
    except (OSError, json.JSONDecodeError):
        excluded = set()
    pairs = []
    for path in sorted(images.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        key = path.relative_to(root).as_posix()
        if key in excluded:
            continue
        pairs.append((path, labels / f"{path.stem}.txt"))
    if not pairs:
        raise ValueError("The dataset images folder contains no supported images.")
    unique_image_stems([image for image, _ in pairs])
    names = extend_class_names(names, highest_label_class(labels))
    unlabeled = [path.name for _, path in pairs if not path.is_file()]
    random.Random(args.seed).shuffle(pairs)
    validation_count = min(max(1, round(len(pairs) * args.val_split)), max(1, len(pairs) - 1)) if len(pairs) > 1 else 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"vaporlock_dataset_{stamp}_{uuid.uuid4().hex[:8]}"
    output = Path(args.output or root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    folder = output / name
    folder.mkdir(exist_ok=False)
    for split in ("train", "val"):
        (folder / "images" / split).mkdir(parents=True, exist_ok=True)
        (folder / "labels" / split).mkdir(parents=True, exist_ok=True)
    for index, (image, label) in enumerate(pairs):
        split = "val" if index < validation_count else "train"
        shutil.copy2(image, folder / "images" / split / image.name)
        destination_label = folder / "labels" / split / label.name
        if label.is_file():
            shutil.copy2(label, destination_label)
        else:
            # An unannotated capture is a valid YOLO background sample.
            destination_label.touch()
        emit("progress", current=index + 1, total=len(pairs), file=image.name)
    # Omitting `path` makes Ultralytics resolve each split beside data.yaml.
    # `path: .` is resolved against the process working directory by current
    # Ultralytics releases, which breaks otherwise portable dataset archives.
    yaml = "train: images/train\nval: images/val\n\nnames:\n" + "\n".join(f"  {index}: {json.dumps(label or f'class-{index}', ensure_ascii=False)}" for index, label in enumerate(names)) + "\n"
    (folder / "data.yaml").write_text(yaml, encoding="utf-8", newline="\n")
    (folder / "classes.txt").write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")
    archive = output / f"{name}.zip"
    with zipfile.ZipFile(archive.with_suffix(".zip.partial"), "x", zipfile.ZIP_DEFLATED, compresslevel=1) as target:
        for path in folder.rglob("*"):
            if path.is_file():
                target.write(path, path.relative_to(folder))
    archive.with_suffix(".zip.partial").replace(archive)
    emit("packaged", archive=str(archive), images=len(pairs), train=len(pairs) - validation_count, validation=validation_count, background=len(unlabeled), excluded=len(excluded),
         warning="Add at least one more image before training: the validation split is empty." if not validation_count else "")
    return 0


def variation_options(args):
    from annotation_variations import VariationOptions
    return VariationOptions(enabled=args.variations_enabled, copies=args.variation_copies,
        strength=args.variation_strength, effects=tuple(args.variation_effects), extra_copies=args.extra_copies).validate()


def add_annotation_arguments(parser):
    parser.add_argument('--model', required=True)
    parser.add_argument('--provider', choices=BACKENDS, default='auto')
    parser.add_argument('--batch-size', type=int, choices=(1,2,4,8), default=1)
    parser.add_argument('--passes', type=int, default=2)
    parser.add_argument('--confidence', type=float, default=.55)
    parser.add_argument('--nms-iou', type=float, default=.45)
    parser.add_argument('--consensus-iou', type=float, default=.85)
    parser.add_argument('--output-format', choices=('auto','yolo-class','yolo-objectness','end-to-end'), default='auto')
    parser.add_argument('--coordinate-units', choices=('auto','pixels','normalized'), default='auto')
    parser.add_argument('--variations-enabled', action='store_true')
    parser.add_argument('--extra-copies', action='store_true')
    parser.add_argument('--variation-copies', type=int, default=1)
    parser.add_argument('--variation-seed', type=int)
    parser.add_argument('--expected-source-signature')
    parser.add_argument('--expected-preview-samples')
    parser.add_argument('--expected-model-sha256')
    parser.add_argument('--variation-strength', choices=('mild','medium','strong'), default='medium')
    parser.add_argument('--variation-effects', type=lambda value: tuple(value.split(',')),
        default=('blur','colour','exposure','noise','compression'))


def command_parser():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    assist=commands.add_parser('assist')
    assist.add_argument('--image',required=True)
    assist.add_argument('--image-sha256',required=True)
    assist.add_argument('--request-id',required=True)
    add_annotation_arguments(assist)
    annotate = commands.add_parser('annotate')
    annotate.add_argument('--dataset', required=True)
    add_annotation_arguments(annotate)
    annotate.add_argument('--replace', action='store_true')
    annotate.add_argument('--additive', action='store_true')
    annotate.add_argument('--delete-unannotated', action='store_true')
    preview = commands.add_parser('platform-preview')
    preview.add_argument('--dataset', required=True)
    add_annotation_arguments(preview)
    preview.add_argument('--owner', required=True)
    preview.add_argument('--cloud-dataset', required=True)
    preview.add_argument('--limit', type=int, default=100)
    preview.add_argument('--options-fingerprint', required=True)
    preview.add_argument('--cancel-file')
    apply = commands.add_parser('platform-apply')
    apply.add_argument('--dataset', required=True)
    apply.add_argument('--plan-sha256', required=True)
    apply.add_argument('--options-fingerprint', required=True)
    apply.add_argument('--cancel-file')
    package = commands.add_parser('package')
    package.add_argument('--dataset', required=True)
    package.add_argument('--output')
    package.add_argument('--val-split', type=float, default=.15)
    package.add_argument('--seed', type=int, default=42)
    return parser


def main() -> int:
    from worker_process_tree import own_process_tree
    args = command_parser().parse_args()
    own_process_tree(memory_limit=2*1024**3 if args.command=='assist' else 0)
    lower_priority()
    try:
        if args.command=='assist':
            from assist_worker import run
            return run(args)
        if args.command in ('annotate','platform-preview'):
            variation_options(args)
            if args.command=='platform-preview' and args.batch_size!=1: raise ValueError('Cloud annotation batch size must be 1.')
            if (not 1 <= args.passes <= 10 or not .01 <= args.confidence <= .99
                    or not .05 <= args.consensus_iou <= .99 or not .01 <= args.nms_iou <= .99):
                raise ValueError('Invalid annotation settings.')
        if args.command.startswith('platform-'):
            import re
            if re.fullmatch(r'[0-9a-f]{64}',args.options_fingerprint) is None:
                raise ValueError('Invalid preview identity.')
            if args.command=='platform-preview' and not 1<=args.limit<=10000:
                raise ValueError('Invalid image limit.')
            # Secret handoff is bounded stdin, never an argument/environment.
            payload=json.loads(sys.stdin.buffer.read(2049))
            if type(payload) is not dict or set(payload)!={'apiKey'}:
                raise ValueError('Invalid credential handoff.')
            from ultralytics_credentials import _KEY
            key=payload.pop('apiKey')
            if type(key) is not str or _KEY.fullmatch(key) is None:
                raise ValueError('Invalid credential handoff.')
            from ultralytics_annotation import preview, apply_preview
            from annotation_output import plain
            cancel=plain(args.cancel_file) if args.cancel_file else None
            if cancel is not None and (cancel.parent!=plain(args.dataset)
                    or re.fullmatch(r'\.vaporlock-ultralytics-stop-[0-9a-f]{32}',cancel.name) is None):
                raise ValueError('Invalid cancellation marker.')
            def check_cancel():
                if cancel is not None and cancel.exists():
                    from ultralytics_annotation import PlatformError
                    raise PlatformError('CANCELLED')
            args.check_cancel=check_cancel
            with dataset_lock(Path(args.dataset)):
                if args.command=='platform-preview':
                    preview(args,key,run_annotation,emit=emit)
                    return 0
                return apply_preview(args,key,emit=emit)
        with dataset_lock(Path(args.dataset)):
            recover_annotations(Path(args.dataset))
            return run_annotation(args) if args.command == "annotate" else run_package(args)
    except AnnotationProviderError as error:
        emit('provider_error', backend=error.backend, code=error.code)
        return 1
    except AnnotationCommitUncertain as error:
        emit('error', code='RECOVERY_REQUIRED', outcome_uncertain=True, message=str(error))
        return 2
    except Exception as error:
        if args.command=='assist':
            emit('assist_error',code='UNSUPPORTED_OR_INVALID' if isinstance(error,ValueError) else 'FAILED')
        if args.command.startswith('platform-'):
            from ultralytics_annotation import PlatformError
            emit('error', code=error.code if isinstance(error,PlatformError) else 'CLOUD_OPERATION_FAILED',
                message='Ultralytics operation stopped. Review the local operation report before retrying.')
        else:
            emit("error", message=str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
