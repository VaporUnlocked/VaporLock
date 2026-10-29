"""Local prompted masks using Light HQ-SAM or the retained MobileSAM backend.

This module proposes geometry only. It does not own a project, edit a main box,
accept labels, or track video. The caller must show the proposal for explicit
review and acceptance. MobileSAM is the small original SAM-compatible model,
not Meta SAM 3 or SAM 3.1 and not a trained player detector.

One instance owns one image embedding. Serialize its calls on a background
worker; the nonblocking guard catches accidental concurrent use. A fingerprint
of oriented RGB pixels invalidates the cache even if a Pillow object was edited
in place. Cancellation is cooperative between native model/geometry calls.

The canonical mask retains every selected in-box pixel, including holes and
separate visible parts. An optional single-ring preview is derived separately;
its measured discrepancy is reported and never replaces the canonical mask.
The legacy mask_to_polygon function remains available for old integrations.

Model/prompt API: https://github.com/ChaoningZhang/MobileSAM
https://github.com/facebookresearch/segment-anything/blob/main/segment_anything/predictor.py
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from collections import OrderedDict
from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import threading
import time

import numpy as np
from PIL import Image, ImageOps

from .geometry import EPS, MAX_LABEL_VERTICES, is_simple_polygon, polygon_area, signed_area
from .assistant_backends import BACKENDS, DEFAULT_BACKEND, normalize_backend, predictor_factory
from .assistant_backends import checkpoint_path as backend_checkpoint_path


BACKEND = BACKENDS[DEFAULT_BACKEND]["label"]
OFFICIAL_CHECKPOINT_SHA256 = BACKENDS[DEFAULT_BACKEND]["sha256"]
MAX_ASSIST_PIXELS = 40_000_000
MAX_PROMPTS = 256
MAX_BOUNDARY_EDGES = 200_000


class AssistCancelled(Exception):
    """An annotation proposal was cancelled without changing any labels."""


def default_checkpoint_path(backend=DEFAULT_BACKEND) -> Path:
    """Location of the checkpoint shipped beside the application's source."""
    return backend_checkpoint_path(backend)


def _check_cancel(cancel) -> None:
    if cancel is None:
        return
    if callable(cancel):
        stopped = cancel()
    elif callable(getattr(cancel, "is_set", None)):
        stopped = cancel.is_set()
    else:
        raise ValueError("Cancellation must be a callable or a threading.Event.")
    if stopped:
        raise AssistCancelled("Encasing assistance cancelled.")


def _cv2():
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Encasing assistance needs OpenCV. Run the application setup again, "
                           "or install its annotation assistance dependencies.") from exc
    return cv2


def _validate_prompts(size, box, points, labels, max_vertices, tolerance):
    width, height = size
    if width <= 0 or height <= 0 or width * height > MAX_ASSIST_PIXELS:
        raise ValueError("Assistant images must contain 1 to 40 million pixels.")
    try:
        bounds = np.asarray(box, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("Select a finite main box [left, top, right, bottom].") from exc
    if bounds.shape != (4,) or not np.isfinite(bounds).all():
        raise ValueError("Select a finite main box [left, top, right, bottom].")
    if (np.any(bounds[:2] < 0) or bounds[2] > width or bounds[3] > height
            or np.any(bounds[2:] - bounds[:2] <= EPS)):
        raise ValueError("The main box must have positive dimensions and lie inside the image.")
    if type(max_vertices) is not int or not 3 <= max_vertices <= MAX_LABEL_VERTICES:
        raise ValueError(f"The editable outline budget must be 3 to {MAX_LABEL_VERTICES} vertices.")
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance) or not 0 <= tolerance <= 100):
        raise ValueError("Outline tolerance must be a finite number from 0 to 100 image pixels.")
    try:
        coordinates = np.asarray([] if points is None else points, dtype=np.float64)
        values = np.asarray([] if labels is None else labels)
    except (TypeError, ValueError) as exc:
        raise ValueError("Prompts need x/y points and matching foreground (1) or background (0) labels.") from exc
    if coordinates.size == 0:
        coordinates = coordinates.reshape(0, 2)
    if (coordinates.ndim != 2 or coordinates.shape[1] != 2
            or not np.isfinite(coordinates).all() or len(coordinates) > MAX_PROMPTS):
        raise ValueError("Use at most 256 finite foreground/background points.")
    if (values.shape != (len(coordinates),) or values.dtype.kind not in "biuf"
            or not np.isfinite(values).all() or not np.isin(values, [0, 1]).all()):
        raise ValueError("Every prompt needs exactly one foreground (1) or background (0) label.")
    values = values.astype(np.int64)
    if len(coordinates):
        if (np.any(coordinates < 0) or np.any(coordinates[:, 0] >= width)
                or np.any(coordinates[:, 1] >= height)):
            raise ValueError("Prompt points must lie inside the image.")
        foreground = coordinates[values == 1]
        if len(foreground) and (np.any(foreground < bounds[:2]) or np.any(foreground > bounds[2:])):
            raise ValueError("Foreground clicks must lie inside the fixed main box. Background clicks may be outside it.")
    return bounds.copy(), coordinates.copy(), values.copy()


def _prompt_conflict(points, labels, box) -> bool:
    foreground = {tuple(point) for point in np.floor(points[labels == 1]).astype(np.int64)}
    background_points = points[labels == 0]
    # Fractional box clipping can distinguish two clicks sharing a raster pixel
    # when the background click lies beyond the fixed box boundary.
    background_points = background_points[np.all(background_points >= box[:2], axis=1)
                                           & np.all(background_points <= box[2:], axis=1)]
    background = {tuple(point) for point in np.floor(background_points).astype(np.int64)}
    return bool(foreground & background)


def _empty_proposal(warning, diagnostics=None):
    return {"wrapper": None, "warnings": [warning], "diagnostics": diagnostics or {}}


def _pixel_boundary_loops(mask: np.ndarray, origin, cancel=None) -> list[np.ndarray]:
    """Trace the exact cell-union boundary; no morphological joining or hull.

    Clockwise outer loops and counterclockwise holes keep the filled pixels on
    the right. At a diagonal touch, prefer a right turn to preserve 4-connectivity.
    """
    height, width = mask.shape
    padded = np.pad(mask, 1, constant_values=False)
    neighbors = (padded[:-2, 1:-1], padded[1:-1, 2:],
                 padded[2:, 1:-1], padded[1:-1, :-2])
    stride = width + 1
    outgoing = {}
    edge_count = 0
    for direction, neighbor in enumerate(neighbors):
        _check_cancel(cancel)
        boundary = mask & ~neighbor
        edge_count += int(np.count_nonzero(boundary))
        if edge_count > MAX_BOUNDARY_EDGES:
            raise ValueError("The mask boundary is too fragmented to edit reliably. Tighten the box or refine the clicks.")
        ys, xs = np.nonzero(boundary)
        if direction == 0:  # top: right
            starts, ends = ys * stride + xs, ys * stride + xs + 1
        elif direction == 1:  # right: down
            starts, ends = ys * stride + xs + 1, (ys + 1) * stride + xs + 1
        elif direction == 2:  # bottom: left
            starts, ends = (ys + 1) * stride + xs + 1, (ys + 1) * stride + xs
        else:  # left: up
            starts, ends = (ys + 1) * stride + xs, ys * stride + xs
        for start, end in zip(starts.tolist(), ends.tolist()):
            outgoing.setdefault(start, {})[direction] = end
    loops = []
    traversed = 0
    for first in sorted(outgoing):
        while outgoing[first]:
            current, previous_direction, vertices = first, None, []
            while True:
                if traversed % 1024 == 0:
                    _check_cancel(cancel)
                available = outgoing.get(current, {})
                if not available:
                    raise ValueError("The mask boundary did not close; refine the prompts or trace this outline manually.")
                if previous_direction is None:
                    direction = min(available)
                else:
                    preferred = ((previous_direction + 1) % 4, previous_direction,
                                 (previous_direction + 3) % 4, (previous_direction + 2) % 4)
                    direction = next(value for value in preferred if value in available)
                vertices.append((current % stride + origin[0], current // stride + origin[1]))
                current = available.pop(direction)
                previous_direction = direction
                traversed += 1
                if traversed > edge_count:
                    raise ValueError("The mask boundary could not be traced safely.")
                if current == first:
                    break
            loops.append(np.asarray(vertices, dtype=np.float64))
    return loops


def _clean_polygon(polygon):
    polygon = np.asarray(polygon, dtype=np.float64)
    if len(polygon) > 1 and np.linalg.norm(polygon[0] - polygon[-1]) <= EPS:
        polygon = polygon[:-1]
    if len(polygon) > 1:
        keep = np.linalg.norm(polygon - np.roll(polygon, 1, axis=0), axis=1) > EPS
        polygon = polygon[keep]
    return polygon


def _polygon_click_conflicts(polygon, points, labels):
    cv2 = _cv2()
    contour = np.asarray(polygon, dtype=np.float32).reshape(-1, 1, 2)
    misses, unwanted = [], []
    for index, (point, label) in enumerate(zip(points, labels)):
        inside = cv2.pointPolygonTest(contour, (float(point[0]), float(point[1])), False) >= 0
        if label == 1 and not inside:
            misses.append(index)
        elif label == 0 and inside:
            unwanted.append(index)
    return misses, unwanted


def _simplify_outline(outline, box, points, labels, max_vertices, tolerance, cancel=None):
    cv2 = _cv2()
    outline = _clean_polygon(np.clip(outline, box[:2], box[2:]))
    source_area = polygon_area(outline)
    best_count, geometric_failure, click_failure = None, False, False
    for epsilon in dict.fromkeys([float(tolerance), float(tolerance) / 2, float(tolerance) / 4, 0.0]):
        _check_cancel(cancel)
        polygon = cv2.approxPolyDP(outline.astype(np.float32).reshape(-1, 1, 2), epsilon, True).reshape(-1, 2)
        polygon = _clean_polygon(np.clip(polygon, box[:2], box[2:]))
        best_count = len(polygon) if best_count is None else min(best_count, len(polygon))
        if not 3 <= len(polygon) <= max_vertices:
            continue
        if not is_simple_polygon(polygon):
            geometric_failure = True
            continue
        misses, unwanted = _polygon_click_conflicts(polygon, points, labels)
        if misses or unwanted:
            click_failure = True
            continue
        # A point budget never authorizes losing substantial shape area. Try a
        # finer outline first; the caller can explicitly choose a larger budget.
        area_change = abs(polygon_area(polygon) - source_area) / max(source_area, EPS)
        if area_change > .03:
            geometric_failure = True
            continue
        if signed_area(polygon) < 0:
            polygon = polygon[::-1]
        first = np.lexsort((polygon[:, 0], polygon[:, 1]))[0]
        polygon = np.roll(polygon, -int(first), axis=0)
        return polygon.tolist(), {"vertices": len(polygon), "tolerance_used_pixels": epsilon,
                                  "outer_area_change_fraction": float(area_change)}
    if click_failure:
        reason = "No simple outline can honor all clicks at this detail setting. Move conflicting clicks or increase the vertex budget."
    elif best_count is not None and best_count > max_vertices:
        reason = (f"The boundary needs at least {best_count} vertices at this tolerance; the budget is {max_vertices}. "
                  "Increase the vertex budget to keep its detail.")
    elif geometric_failure:
        reason = "The mask has thin or touching boundaries that cannot form a faithful simple outline. Refine the clicks or trace it manually."
    else:
        reason = "The mask is too small or thin to form a valid outline. Refine the box and foreground clicks."
    return None, {"rejection": reason, "smallest_attempted_vertices": best_count}


def _mask_to_polygon(mask, box, points, labels, max_vertices, tolerance, cancel=None):
    cv2 = _cv2()
    _check_cancel(cancel)
    height, width = mask.shape
    x0, y0 = np.floor(box[:2]).astype(int)
    x1, y1 = np.ceil(box[2:]).astype(int)
    local = np.ascontiguousarray(mask[y0:y1, x0:x1], dtype=np.uint8)
    mask_pixels = int(np.count_nonzero(mask))
    clipped_pixels = mask_pixels - int(np.count_nonzero(local))
    diagnostics = {"mask_pixels": mask_pixels, "clipped_pixels_outside_box": clipped_pixels,
                   "box": box.tolist(), "foreground_clicks": int(np.count_nonzero(labels == 1)),
                   "background_clicks": int(np.count_nonzero(labels == 0)), "valid": False}
    if not np.any(local):
        return _empty_proposal("The predicted mask is empty inside the main box. Add a foreground click or adjust the box.", diagnostics)
    count, component_labels, component_stats, _ = cv2.connectedComponentsWithStats(local, connectivity=4)
    _check_cancel(cancel)
    prompt_components = []
    for point in points:
        # A background click just outside a fractional box edge can share the
        # same raster pixel as an inside click. It is outside the clipped region.
        if np.any(point < box[:2]) or np.any(point > box[2:]):
            prompt_components.append(0)
            continue
        x, y = np.floor(point).astype(int) - [x0, y0]
        x, y = min(x, x1 - x0 - 1), min(y, y1 - y0 - 1)
        prompt_components.append(int(component_labels[y, x]))
    prompt_components = np.asarray(prompt_components, dtype=int)
    positive_components = prompt_components[labels == 1]
    negative_components = set(prompt_components[labels == 0]) - {0}
    diagnostics["components"] = count - 1
    if np.any(positive_components == 0):
        diagnostics["missed_foreground_clicks"] = int(np.count_nonzero(positive_components == 0))
        return _empty_proposal("The predicted mask misses a foreground click. Move or add a foreground click on the desired object.", diagnostics)
    if len(set(positive_components)) > 1:
        return _empty_proposal("Foreground clicks fall on disconnected mask regions. A single encasing cannot join them without adding background; refine the clicks or annotate separately.", diagnostics)
    if len(positive_components):
        selected = int(positive_components[0])
        if selected in negative_components:
            return _empty_proposal("The same connected mask contains foreground and background clicks. Refine the background clicks to separate the object.", diagnostics)
    else:
        allowed = [index for index in range(1, count) if index not in negative_components]
        if not allowed:
            return _empty_proposal("Every predicted region includes a background click. Add a foreground click or move the conflicting prompt.", diagnostics)
        selected = max(allowed, key=lambda index: (int(component_stats[index, cv2.CC_STAT_AREA]), -index))
    component = component_labels == selected
    selected_pixels = int(component_stats[selected, cv2.CC_STAT_AREA])
    dropped_pixels = int(np.count_nonzero(local)) - selected_pixels
    diagnostics.update({"selected_component_pixels": selected_pixels, "dropped_components": count - 2,
                        "dropped_component_pixels": dropped_pixels,
                        "component_selection": "foreground_click" if len(positive_components) else "largest_without_background_click"})
    warnings = []
    if clipped_pixels:
        warnings.append(f"Clipped {clipped_pixels} predicted pixels outside the fixed main box.")
    if count > 2:
        warnings.append(f"Dropped {count - 2} disconnected mask region(s), totaling {dropped_pixels} pixels. No bridge was added.")
    try:
        loops = _pixel_boundary_loops(component, (x0, y0), cancel)
    except ValueError as exc:
        return {"wrapper": None, "warnings": warnings + [str(exc)], "diagnostics": diagnostics}
    outer = [loop for loop in loops if signed_area(loop) > 0]
    holes = [loop for loop in loops if signed_area(loop) < 0]
    diagnostics.update({"holes": len(holes), "hole_pixels": int(round(sum(polygon_area(loop) for loop in holes))),
                        "boundary_edges": sum(len(loop) for loop in loops)})
    if len(outer) != 1:
        return {"wrapper": None, "warnings": warnings + ["The selected mask has multiple touching outer boundaries; refine the prompts so it forms one simple outline."],
                "diagnostics": diagnostics}
    if holes:
        warnings.append(f"The mask contains {len(holes)} enclosed hole(s). This label supports one outer outline, so those hole pixels would lie inside the encasing. Review carefully.")
    wrapper, shape_diagnostics = _simplify_outline(outer[0], box, points, labels, max_vertices, tolerance, cancel)
    diagnostics.update(shape_diagnostics)
    if wrapper is None:
        warnings.append(shape_diagnostics["rejection"])
    else:
        diagnostics["valid"] = True
    return {"wrapper": wrapper, "warnings": warnings, "diagnostics": diagnostics}


def mask_to_polygon(mask, box, points=None, labels=None, *, max_vertices=128, tolerance=1.5, cancel=None):
    """Convert a binary mask to a reviewable proposal without a model or project.

    Coordinates are original image pixels. A null wrapper plus warnings means
    the requested single-ring geometry cannot be proposed safely. Main-box
    containment, geometry validity, and prompt consistency hold on every return
    that contains a wrapper.
    """
    _check_cancel(cancel)
    array = np.asarray(mask)
    if array.ndim != 2 or array.dtype.kind not in "biuf" or not np.isfinite(array).all() or not np.isin(array, [0, 1]).all():
        raise ValueError("A mask must be a finite binary H by W array.")
    box, points, labels = _validate_prompts((array.shape[1], array.shape[0]), box, points, labels, max_vertices, tolerance)
    if _prompt_conflict(points, labels, box):
        return _empty_proposal("Foreground and background clicks occupy the same mask pixel. Move or remove one of the conflicting clicks.",
                               {"valid": False, "click_conflict": True, "box": box.tolist()})
    return _mask_to_polygon(array.astype(bool, copy=False), box, points, labels, max_vertices, tolerance, cancel)


def _raster_outline_at_centers(polygon, shape, origin):
    """Rasterize a preview at pixel centers for honest mask-fidelity metrics."""
    raster = np.zeros(shape, dtype=bool)
    if polygon is None:
        return raster
    ring = np.asarray(polygon, dtype=np.float64) - np.asarray(origin)
    previous = np.roll(ring, 1, axis=0)
    for row in range(shape[0]):
        y = row + .5
        crossing = (ring[:, 1] > y) != (previous[:, 1] > y)
        if not crossing.any():
            continue
        a, b = ring[crossing], previous[crossing]
        intersections = np.sort(a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1]))
        for left, right in zip(intersections[::2], intersections[1::2]):
            lo, hi = max(0, int(math.ceil(left - .5))), min(shape[1], int(math.ceil(right - .5)))
            if hi > lo:
                raster[row, lo:hi] = True
    return raster


def _mask_to_encasing(mask, box, points, labels, max_vertices, tolerance, cancel=None):
    """Keep the exact in-box raster; the polygon is only a compatibility preview."""
    from .masks import encode_mask
    cv2 = _cv2()
    _check_cancel(cancel)
    height, width = mask.shape
    # The raster contract uses pixel-center containment for fractional boxes.
    # Pixel squares at a fractional edge may straddle it; centers never do.
    x0, y0 = np.maximum(0, np.ceil(box[:2] - .5).astype(np.int64))
    x1, y1 = np.minimum([width, height], np.ceil(box[2:] - .5).astype(np.int64))
    raw_pixels = int(np.count_nonzero(mask))
    local = np.ascontiguousarray(mask[y0:y1, x0:x1], dtype=bool)
    selected_pixels = int(np.count_nonzero(local))
    clipped_pixels = raw_pixels - selected_pixels
    diagnostics = {
        "box": box.tolist(), "valid": False, "mask_authoritative": True,
        "mask_pixels": raw_pixels, "selected_mask_pixels": selected_pixels,
        "clipped_pixels_outside_box": clipped_pixels,
        "clipped_fraction": clipped_pixels / max(raw_pixels, 1),
        "foreground_clicks": int(np.count_nonzero(labels == 1)),
        "background_clicks": int(np.count_nonzero(labels == 0)),
        "dropped_component_pixels": 0, "dropped_components": 0,
        "pixel_containment": "pixel_center_inside_half_open_box",
    }
    if not selected_pixels:
        return dict(_empty_proposal("The predicted mask is empty inside the box. Add a foreground click or adjust the box.", diagnostics), mask=None)
    misses, unwanted = [], []
    for index, (point, label) in enumerate(zip(points, labels)):
        x, y = np.floor(point).astype(np.int64)
        in_box = np.all(point >= box[:2]) and np.all(point < box[2:])
        included = bool(in_box and x0 <= x < x1 and y0 <= y < y1 and local[y-y0, x-x0])
        if label == 1 and not included:
            misses.append(index)
        if label == 0 and included:
            unwanted.append(index)
    if misses or unwanted:
        diagnostics.update(missed_foreground_clicks=len(misses), included_background_clicks=len(unwanted))
        return dict(_empty_proposal("The proposed mask contradicts a foreground or background click. Refine the prompts or choose another candidate.", diagnostics), mask=None)
    count, _, _, _ = cv2.connectedComponentsWithStats(local.astype(np.uint8), connectivity=4)
    background = np.pad(~local, 1, constant_values=True).astype(np.uint8)
    background_count, background_labels, background_stats, _ = cv2.connectedComponentsWithStats(background, connectivity=8)
    exterior = int(background_labels[0, 0])
    hole_ids = [index for index in range(1, background_count) if index != exterior]
    hole_pixels = int(sum(background_stats[index, cv2.CC_STAT_AREA] for index in hole_ids))
    diagnostics.update(components=count - 1, holes=len(hole_ids), hole_pixels=hole_pixels,
                       component_selection="all_in_box_components_preserved")
    encoded = encode_mask(local, origin=(int(x0), int(y0)), image_size=(width, height))
    raw_encoded = encode_mask(mask, image_size=(width, height))
    warnings = []
    if clipped_pixels:
        warnings.append(f"Clipped {clipped_pixels} predicted pixels outside the fixed box; the original proposal is retained for inspection.")
    if count > 2:
        warnings.append(f"Preserved {count - 1} disconnected foreground regions in the exact mask. Check that all belong to this target.")
    if hole_ids:
        warnings.append(f"Preserved {len(hole_ids)} enclosed hole(s), including {hole_pixels} background pixels, in the exact mask.")
    # Legacy consumers can still draw an outer ring. Never use that ring to
    # replace the authoritative mask or to discard islands or fill holes.
    clipped = np.zeros_like(mask, dtype=bool)
    clipped[y0:y1, x0:x1] = local
    try:
        preview = _mask_to_polygon(clipped, box, np.empty((0, 2)), np.empty(0, dtype=np.int64),
                                   max_vertices, tolerance, cancel)
        wrapper = preview.get("wrapper")
        diagnostics["preview_diagnostics"] = preview.get("diagnostics", {})
    except ValueError as exc:
        wrapper = None
        diagnostics["preview_diagnostics"] = {"rejection": str(exc)}
    _check_cancel(cancel)
    if wrapper is not None:
        outline_pixels = _raster_outline_at_centers(wrapper, local.shape, (x0, y0))
        true_positive = int(np.count_nonzero(outline_pixels & local))
        false_positive = int(np.count_nonzero(outline_pixels & ~local))
        false_negative = int(np.count_nonzero(~outline_pixels & local))
        preview_pixels = true_positive + false_positive
        diagnostics.update(
            polygon_mask_iou=true_positive / max(true_positive + false_positive + false_negative, 1),
            polygon_added_background_pixels=false_positive,
            polygon_missed_foreground_pixels=false_negative,
            polygon_background_fraction=false_positive / max(preview_pixels, 1),
            polygon_missed_fraction=false_negative / selected_pixels,
            polygon_exact=(false_positive == 0 and false_negative == 0),
            vertices=len(wrapper),
        )
        if false_positive or false_negative:
            warnings.append(f"The optional polygon preview adds {false_positive} background pixel(s) and misses {false_negative} foreground pixel(s). The saved exact mask preserves both exclusions and target pixels.")
    else:
        diagnostics.update(polygon_mask_iou=None, polygon_exact=False, vertices=0)
        warnings.append("This exact mask cannot be represented faithfully by one editable polygon at the current detail limit. The complete mask is retained and usable.")
    diagnostics["valid"] = True
    return {"mask": encoded, "raw_mask": raw_encoded, "wrapper": wrapper,
            "warnings": warnings, "diagnostics": diagnostics}


def mask_to_encasing(mask, box, points=None, labels=None, *, max_vertices=128, tolerance=1.5, cancel=None):
    """Convert a binary mask into a lossless raster label and optional preview.

    The exact mask retains holes and disconnected parts. Prompt conflicts reject
    a candidate instead of deleting a component or filling a gap to satisfy it.
    Fractional box clipping is defined at pixel centers. No mask dilation,
    erosion, convex hull, or disconnected-region bridging is applied.
    """
    _check_cancel(cancel)
    array = np.asarray(mask)
    if (array.ndim != 2 or array.dtype.kind not in "biuf" or not np.isfinite(array).all()
            or not np.isin(array, [0, 1]).all()):
        raise ValueError("A mask must be a finite binary H by W array.")
    box, points, labels = _validate_prompts((array.shape[1], array.shape[0]), box, points, labels, max_vertices, tolerance)
    if _prompt_conflict(points, labels, box):
        return dict(_empty_proposal("Foreground and background clicks occupy the same mask pixel. Move or remove one of the conflicting clicks.",
                                   {"valid": False, "click_conflict": True, "box": box.tolist()}), mask=None)
    return _mask_to_encasing(array.astype(bool, copy=False), box, points, labels, max_vertices, tolerance, cancel)


def _crop_bounds(size, box, points, mode):
    if mode not in ("auto", "full", "crop"):
        raise ValueError("Assistant crop mode must be auto, full, or crop.")
    width, height = size
    full = (0, 0, width, height)
    effective_size = float(max(box[2:] - box[:2]) * 1024 / max(size))
    if mode == "full" or (mode == "auto" and effective_size >= 256):
        return full
    center = (box[:2] + box[2:]) * .5
    extent = np.maximum((box[2:] - box[:2]) * 2, 256)
    low, high = center - extent * .5, center + extent * .5
    if len(points):
        low = np.minimum(low, points.min(axis=0) - 16)
        high = np.maximum(high, points.max(axis=0) + 17)
    low = np.maximum(np.floor(low).astype(np.int64), [0, 0])
    high = np.minimum(np.ceil(high).astype(np.int64), [width, height])
    return (int(low[0]), int(low[1]), int(high[0]), int(high[1]))


@contextmanager
def _torch_threads(torch, count):
    """Bound CPU work while the GUI has reserved model execution for assistance."""
    previous = torch.get_num_threads()
    try:
        if previous != count:
            torch.set_num_threads(count)
        yield
    finally:
        if previous != count:
            torch.set_num_threads(previous)


class AnnotationAssistant:
    """Cached pretrained image/prompt segmentation, with no label write access."""

    def __init__(self, checkpoint_path=None, device="cpu", predictor=None, *, cpu_threads=4, backend=DEFAULT_BACKEND):
        if not isinstance(device, str) or (device not in ("cpu", "auto", "cuda")
                                           and not (device.startswith("cuda:") and device[5:].isdigit())):
            raise ValueError("Annotation assistance supports CPU, auto, or CUDA devices.")
        if type(cpu_threads) is not int or not 1 <= cpu_threads <= 16:
            raise ValueError("Assistant CPU threads must be an integer from 1 to 16.")
        self.backend = normalize_backend(backend)
        self.backend_label = BACKENDS[self.backend]["label"]
        self._official_checkpoint_sha256 = BACKENDS[self.backend]["sha256"]
        bundled = default_checkpoint_path() if self.backend == DEFAULT_BACKEND else default_checkpoint_path(self.backend)
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path is not None else bundled
        self._bundled_checkpoint = self.checkpoint_path.resolve() == bundled.resolve()
        self.device = device
        self.cpu_threads = min(cpu_threads, os.cpu_count() or 1)
        self._predictor = predictor
        self._torch = None
        self._image_key = None
        self._embedding_cache = OrderedDict()
        self._cache_limit = 3
        self._refinement_cache = OrderedDict()
        self._refinement_cache_limit = 32
        self._last_refinement = OrderedDict()
        self._candidate_serial = 0
        self._checkpoint_sha256 = None
        self._guard = threading.Lock()

    def _ensure_predictor(self, cancel):
        if self._predictor is not None:
            return
        _check_cancel(cancel)
        if not self.checkpoint_path.is_file():
            raise RuntimeError(f"The {self.backend_label} checkpoint is missing: {self.checkpoint_path}. "
                               "Restore the complete application download or choose the other bundled assistant.")
        self._checkpoint_sha256 = hashlib.sha256(self.checkpoint_path.read_bytes()).hexdigest()
        if self._bundled_checkpoint and self._checkpoint_sha256 != self._official_checkpoint_sha256:
            raise RuntimeError(f"The bundled {self.backend_label} checkpoint does not match its verified official SHA-256. "
                               "Restore the checkpoint from the complete application download.")
        _check_cancel(cancel)
        try:
            import torch
            build_model, Predictor = predictor_factory(self.backend)
        except ImportError as exc:
            raise RuntimeError(f"{self.backend_label} assistance needs PyTorch and the bundled model code. Run the application setup again.") from exc
        resolved = "cuda:0" if self.device == "auto" and torch.cuda.is_available() else self.device
        if resolved == "auto":
            resolved = "cpu"
        if resolved == "cuda":
            resolved = "cuda:0"
        if resolved.startswith("cuda"):
            if not torch.cuda.is_available() or int(resolved.split(":")[1]) >= torch.cuda.device_count():
                raise RuntimeError("The selected CUDA device is unavailable. Choose CPU or install a supported CUDA PyTorch build.")
        with _torch_threads(torch, self.cpu_threads), torch.random.fork_rng(devices=[]), torch.inference_mode():
            model = build_model(checkpoint=str(self.checkpoint_path))
            model.to(device=resolved)
            model.eval()
            predictor = Predictor(model)
        _check_cancel(cancel)
        self._torch = torch
        self._predictor = predictor
        self.device = resolved

    def clear_cache(self):
        if not self._guard.acquire(blocking=False):
            raise RuntimeError("Wait for the current assistance request before clearing its image cache.")
        try:
            self._image_key = None
            self._embedding_cache.clear()
            self._refinement_cache.clear()
            self._last_refinement.clear()
            if self._predictor is not None and callable(getattr(self._predictor, "reset_image", None)):
                self._predictor.reset_image()
        finally:
            self._guard.release()

    def _activate_image(self, pixels, key, cancel):
        """Restore or encode one image on the assistant's owning thread."""
        if key == self._image_key:
            return True
        cached = self._embedding_cache.get(key)
        if cached is not None:
            for name, value in cached.items():
                setattr(self._predictor, name, value)
            self._predictor.is_image_set = True
            self._image_key = key
            self._embedding_cache.move_to_end(key)
            return True
        _check_cancel(cancel)
        self._image_key = None
        self._predictor.set_image(pixels, image_format="RGB")
        self._synchronize()
        self._image_key = key
        if all(hasattr(self._predictor, name) for name in ("features", "original_size", "input_size")):
            names = ("features", "original_size", "input_size", "interm_features")
            self._embedding_cache[key] = {name: getattr(self._predictor, name) for name in names
                                          if hasattr(self._predictor, name)}
            self._embedding_cache.move_to_end(key)
            while len(self._embedding_cache) > self._cache_limit:
                self._embedding_cache.popitem(last=False)
        _check_cancel(cancel)
        return False

    def prepare(self, image, *, cancel=None):
        """Prepare image features without proposing, changing, or approving labels."""
        if not self._guard.acquire(blocking=False):
            raise RuntimeError("Only one annotation assistance request may run at a time.")
        started = time.perf_counter()
        try:
            _check_cancel(cancel)
            if not isinstance(image, Image.Image):
                raise ValueError("Assistant images must be Pillow images.")
            image = ImageOps.exif_transpose(image)
            if not 0 < image.width * image.height <= MAX_ASSIST_PIXELS:
                raise ValueError("Assistant images must contain 1 to 40 million pixels.")
            pixels = np.ascontiguousarray(image.convert("RGB"), dtype=np.uint8)
            key = (image.size, hashlib.sha256(memoryview(pixels)).hexdigest())
            self._ensure_predictor(cancel)
            thread_scope = _torch_threads(self._torch, self.cpu_threads) if self._torch is not None else nullcontext()
            inference_scope = self._torch.inference_mode() if self._torch is not None else nullcontext()
            with thread_scope, inference_scope:
                hit = self._activate_image(pixels, key, cancel)
            return {"image_sha256": key[1], "embedding_cache_hit": hit,
                    "cached_images": len(self._embedding_cache),
                    "total_ms": (time.perf_counter() - started) * 1000}
        finally:
            self._guard.release()

    def _prior_mask_input(self, token, target_key, points, labels):
        """Reuse only the chosen mask's logits when prompts are appended.

        Undo, reset, changed boxes, changed pixels, and crop changes start from
        the explicit prompts. They never retain a removed click through logits.
        """
        if token is None:
            token = self._last_refinement.get(target_key)
        cached = self._refinement_cache.get(token)
        if cached is None or cached["target_key"] != target_key:
            return None
        old_points, old_labels = cached["points"], cached["labels"]
        count = len(old_points)
        if (len(points) <= count or not np.array_equal(points[:count], old_points)
                or not np.array_equal(labels[:count], old_labels)):
            return None
        self._refinement_cache.move_to_end(token)
        return cached["logits"].copy()

    def _remember_candidate(self, candidate, logits, target_key, points, labels):
        if logits is None:
            candidate["refinement_token"] = None
            return
        self._candidate_serial += 1
        token_input = repr((self.backend, self._checkpoint_sha256, target_key,
                            self._candidate_serial, candidate["id"])).encode("utf-8")
        token = hashlib.sha256(token_input).hexdigest()
        self._refinement_cache[token] = {"target_key": target_key, "points": points.copy(),
                                         "labels": labels.copy(), "logits": logits.copy()}
        self._refinement_cache.move_to_end(token)
        while len(self._refinement_cache) > self._refinement_cache_limit:
            self._refinement_cache.popitem(last=False)
        candidate["refinement_token"] = token

    def propose(self, image, box, points=None, labels=None, *, max_vertices=128,
                tolerance=1.5, crop_mode="auto", refinement_token=None, cancel=None):
        """Return exact mask candidates; never mutate accepted annotations.

        Select a returned candidate without inference, then pass its opaque
        refinement_token with the next added point. Candidate logits remain in
        this worker's bounded memory cache, never in project JSON or .cloud.
        Cropping changes embedding resolution, not the original image pixels.
        """
        if not self._guard.acquire(blocking=False):
            raise RuntimeError("Only one annotation assistance request may run at a time.")
        started = time.perf_counter()
        timings = {"load": 0.0, "embedding": 0.0, "prompt": 0.0, "polygon": 0.0, "total": 0.0}
        try:
            _check_cancel(cancel)
            if not isinstance(image, Image.Image):
                raise ValueError("Annotation assistance needs a Pillow image.")
            if refinement_token is not None and (not isinstance(refinement_token, str) or len(refinement_token) > 128):
                raise ValueError("A refinement token must be the short opaque token from a previous candidate.")
            oriented = ImageOps.exif_transpose(image)
            box, points, labels = _validate_prompts(oriented.size, box, points, labels, max_vertices, tolerance)
            crop = _crop_bounds(oriented.size, box, points, crop_mode)
            if _prompt_conflict(points, labels, box):
                result = dict(_empty_proposal("Foreground and background clicks occupy the same mask pixel. Move or remove one of the conflicting clicks.",
                              {"valid": False, "click_conflict": True, "box": box.tolist()}), mask=None, candidates=[])
                return self._finish(result, None, timings, started)
            pixels = np.ascontiguousarray(oriented.convert("RGB"), dtype=np.uint8)
            image_sha = hashlib.sha256(memoryview(pixels)).hexdigest()
            x0, y0, x1, y1 = crop
            crop_pixels = np.ascontiguousarray(pixels[y0:y1, x0:x1])
            crop_size = (x1 - x0, y1 - y0)
            key = (crop_size, hashlib.sha256(memoryview(crop_pixels)).hexdigest())
            target_key = (image_sha, tuple(box.tolist()), crop)
            input_logits = self._prior_mask_input(refinement_token, target_key, points, labels)
            local_box = box - np.array([x0, y0, x0, y0])
            local_points = points - np.array([x0, y0])
            checkpoint_started = time.perf_counter()
            self._ensure_predictor(cancel)
            timings["load"] = (time.perf_counter() - checkpoint_started) * 1000
            thread_scope = _torch_threads(self._torch, self.cpu_threads) if self._torch is not None else nullcontext()
            inference_scope = self._torch.inference_mode() if self._torch is not None else nullcontext()
            candidates = []
            cache_hit = key == self._image_key or key in self._embedding_cache
            with thread_scope, inference_scope:
                _check_cancel(cancel)
                encode_started = time.perf_counter()
                self._activate_image(crop_pixels, key, cancel)
                if not cache_hit:
                    timings["embedding"] = (time.perf_counter() - encode_started) * 1000
                _check_cancel(cancel)
                prompt_started = time.perf_counter()
                for multi in (False, True):
                    kwargs = dict(point_coords=local_points.astype(np.float32) if len(points) else None,
                                  point_labels=labels if len(labels) else None,
                                  box=local_box.astype(np.float32), multimask_output=multi,
                                  return_logits=False)
                    if input_logits is not None:
                        kwargs["mask_input"] = input_logits
                    masks, scores, low_res = self._predictor.predict(**kwargs)
                    self._synchronize()
                    _check_cancel(cancel)
                    masks, scores = np.asarray(masks), np.asarray(scores)
                    if (masks.ndim != 3 or masks.shape[1:] != crop_pixels.shape[:2]
                            or scores.shape != (len(masks),) or not 1 <= len(masks) <= 8
                            or masks.dtype.kind not in "biuf"):
                        raise RuntimeError(f"{self.backend_label} returned an unexpected mask or quality-score shape.")
                    low_res = np.asarray(low_res) if low_res is not None else None
                    valid_logits = (low_res is not None and low_res.ndim == 3 and len(low_res) == len(masks)
                                    and low_res.shape[1:] == (256, 256) and low_res.dtype.kind == "f"
                                    and np.isfinite(low_res).all())
                    for index, (mask, score) in enumerate(zip(masks, scores)):
                        mode = "multi" if multi else "single"
                        if not np.isfinite(mask).all() or not np.isin(mask, [0, 1]).all() or not np.isfinite(score):
                            candidates.append((None, None, mode, index, None))
                        else:
                            full_mask = np.zeros(pixels.shape[:2], dtype=bool)
                            full_mask[y0:y1, x0:x1] = mask.astype(bool, copy=False)
                            logits = np.array(low_res[index:index+1], dtype=np.float32, copy=True) if valid_logits else None
                            candidates.append((full_mask, float(score), mode, index, logits))
                timings["prompt"] = (time.perf_counter() - prompt_started) * 1000
            _check_cancel(cancel)
            polygon_started = time.perf_counter()
            proposals, rejected = [], []
            # Keep all distinct masks, not only the largest connected region.
            seen = {}
            for mask, score, mode, index, logits in candidates:
                _check_cancel(cancel)
                if mask is None:
                    rejected.append({"mode": mode, "index": index, "warnings": ["Nonfinite or nonbinary model output."]})
                    continue
                proposal = _mask_to_encasing(mask, box, points, labels, max_vertices, tolerance, cancel)
                proposal["id"] = f"{mode}:{index}"
                proposal["quality_hint"] = score
                proposal["backend"], proposal["device"] = self.backend_label, self.device
                diagnostics = proposal["diagnostics"]
                diagnostics.update(mask_mode=mode, mask_index=index, model_quality_hint=score)
                if proposal.get("mask") is not None:
                    # An IoU estimate for an unmodified raw proposal no longer
                    # applies after clipping. This is an explicit ranking
                    # heuristic, not a calibrated score or correctness proof.
                    ranking = score - .35 * diagnostics["clipped_fraction"]
                    diagnostics["selection_score"] = float(ranking)
                    diagnostics["selection_rule"] = "model_quality_hint_minus_0.35_times_box_clipping_fraction"
                    fingerprint = hashlib.sha256(repr(proposal["mask"]).encode("utf-8")).hexdigest()
                    previous = seen.get(fingerprint)
                    if previous is not None:
                        if ranking > previous[0]:
                            proposals.remove(previous)
                        else:
                            continue
                    entry = (ranking, proposal, logits)
                    seen[fingerprint] = entry
                    proposals.append(entry)
                else:
                    rejected.append({"mode": mode, "index": index, "warnings": proposal["warnings"],
                                     "diagnostics": diagnostics})
            timings["polygon"] = (time.perf_counter() - polygon_started) * 1000
            common = {
                "embedding_cache_hit": cache_hit, "image_sha256": image_sha,
                "image_size": list(oriented.size), "candidate_count": len(candidates),
                "usable_candidates": len(proposals), "rejected_candidates": rejected,
                "checkpoint_sha256": self._checkpoint_sha256,
                "checkpoint_origin": "bundled" if self._bundled_checkpoint else "custom_path",
                "checkpoint_verified_official": self._checkpoint_sha256 == self._official_checkpoint_sha256,
                "cpu_threads": self.cpu_threads,
                "quality_hint_note": f"{self.backend_label}'s mask-quality estimate, not calibrated confidence or measured annotation accuracy.",
                "max_vertices": max_vertices, "tolerance_requested_pixels": float(tolerance),
                "crop_mode": crop_mode, "crop_box": list(crop),
                "crop_applied": crop != (0, 0, oriented.width, oriented.height),
                "effective_target_long_side_pixels": float(max(box[2:] - box[:2]) * 1024 / max(crop_size)),
                "mask_input_used": input_logits is not None,
                "refinement_rule": "selected_logits_only_for_appended_clicks_with_matching_image_box_and_crop",
            }
            if proposals:
                proposals.sort(key=lambda item: (-item[0], item[1]["id"]))
                for _, proposal, logits in proposals:
                    proposal["diagnostics"].update(deepcopy(common))
                    if self._checkpoint_sha256 is not None and self._checkpoint_sha256 != self._official_checkpoint_sha256:
                        proposal["warnings"].append(f"Using a custom {self.backend_label} checkpoint whose weights differ from the verified bundled pretrained model.")
                    self._remember_candidate(proposal, logits, target_key, points, labels)
                score = proposals[0][1]["quality_hint"]
                result = deepcopy(proposals[0][1])
                result["candidates"] = [deepcopy(item[1]) for item in proposals]
                if result.get("refinement_token"):
                    self._last_refinement[target_key] = result["refinement_token"]
                    self._last_refinement.move_to_end(target_key)
                    while len(self._last_refinement) > self._refinement_cache_limit:
                        self._last_refinement.popitem(last=False)
            else:
                score = None
                warnings = list(dict.fromkeys(warning for item in rejected for warning in item["warnings"]))
                result = {"wrapper": None, "mask": None, "candidates": [],
                          "warnings": warnings or ["No usable mask was proposed; add foreground/background clicks."],
                          "diagnostics": dict(common, valid=False, box=box.tolist())}
            _check_cancel(cancel)
            return self._finish(result, score, timings, started)
        finally:
            self._guard.release()

    def _synchronize(self):
        if self._torch is not None and self.device.startswith("cuda"):
            self._torch.cuda.synchronize(self.device)

    def _finish(self, result, score, timings, started):
        timings["total"] = (time.perf_counter() - started) * 1000
        result.update({"backend": self.backend_label, "backend_id": self.backend, "device": self.device, "quality_hint": score, "timings_ms": timings})
        return result
