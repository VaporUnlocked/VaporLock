"""Exact binary encasings, with a portable bounded row-major RLE representation.

``size`` is [image height, image width], ``origin`` is [x, y], and ``shape``
is [crop height, crop width]. Counts alternate background/foreground runs,
starting with background. Pixel (x, y) represents the sample at (x+.5, y+.5).
The crop is only a storage optimization: holes and disconnected regions remain.
There is no image, training state, or segmentation assistant in a .cloud model.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

ENCODING = "cloud-rle-v1"
MAX_PIXELS = 100_000_000
MAX_RUNS = 4_000_000


def _size(size, name="image_size"):
    if (not isinstance(size, (tuple, list)) or len(size) != 2
            or any(type(v) is not int or not 1 <= v <= 32768 for v in size)
            or size[0] * size[1] > MAX_PIXELS):
        raise ValueError(f"{name} must be (width, height), at most 32768 per side and 100 million pixels.")
    return tuple(size)


def _schema(value, expected_size=None):
    if not isinstance(value, dict) or set(value) != {"encoding", "size", "origin", "shape", "counts"}:
        raise ValueError("Mask must contain exactly encoding, size, origin, shape and counts.")
    if value["encoding"] != ENCODING:
        raise ValueError("Unsupported binary mask encoding.")
    stored = value["size"]
    if not isinstance(stored, list) or len(stored) != 2:
        raise ValueError("Mask size must be [height, width].")
    width, height = _size([stored[1], stored[0]], "mask size")
    if expected_size is not None and (width, height) != _size(expected_size):
        raise ValueError("Mask image size does not match the image.")
    origin, shape = value["origin"], value["shape"]
    if (not isinstance(origin, list) or len(origin) != 2
            or any(type(v) is not int or v < 0 for v in origin)
            or not isinstance(shape, list) or len(shape) != 2
            or any(type(v) is not int or v < 0 for v in shape)):
        raise ValueError("Mask origin and shape must contain two nonnegative integers.")
    x, y = origin
    h, w = shape
    if x + w > width or y + h > height or h * w > MAX_PIXELS:
        raise ValueError("Mask crop leaves the image.")
    counts = value["counts"]
    if not isinstance(counts, list) or not 1 <= len(counts) <= MAX_RUNS:
        raise ValueError("Mask counts are missing or exceed the supported run limit.")
    if any(type(v) is not int or v < 0 for v in counts):
        raise ValueError("Mask counts must be nonnegative integers.")
    if sum(counts) != h * w or any(v == 0 for v in counts[1:]):
        raise ValueError("Mask runs do not exactly describe the declared crop.")
    if not h or not w:
        if origin != [0, 0] or shape != [0, 0] or counts != [0]:
            raise ValueError("An empty mask uses origin [0,0], shape [0,0], counts [0].")
    return width, height, x, y, w, h, counts


def encode_mask(array, *, origin=(0, 0), image_size=None) -> dict:
    """Encode every binary pixel exactly; trim unused outer rows and columns."""
    pixels = np.asarray(array)
    if pixels.ndim != 2 or pixels.size > MAX_PIXELS or pixels.dtype.kind not in "biuf":
        raise ValueError("A mask must be a bounded two-dimensional binary array.")
    if not np.isfinite(pixels).all() or not np.isin(pixels, (0, 1)).all():
        raise ValueError("A binary mask may contain only 0 and 1.")
    if (not isinstance(origin, (tuple, list)) or len(origin) != 2
            or any(type(v) is not int or v < 0 for v in origin)):
        raise ValueError("Mask origin must be nonnegative integer (x,y).")
    if image_size is None:
        if tuple(origin) != (0, 0):
            raise ValueError("An offset crop requires the full image_size.")
        image_size = (int(pixels.shape[1]), int(pixels.shape[0]))
    width, height = _size(image_size)
    x, y = origin
    if x + pixels.shape[1] > width or y + pixels.shape[0] > height:
        raise ValueError("Mask crop leaves its image.")
    pixels = pixels.astype(bool, copy=False)
    rows, cols = np.flatnonzero(pixels.any(axis=1)), np.flatnonzero(pixels.any(axis=0))
    if not len(rows):
        return {"encoding": ENCODING, "size": [height, width], "origin": [0, 0],
                "shape": [0, 0], "counts": [0]}
    y0, y1, x0, x1 = int(rows.min()), int(rows.max()) + 1, int(cols.min()), int(cols.max()) + 1
    local = np.ascontiguousarray(pixels[y0:y1, x0:x1])
    flat = local.ravel()
    changes = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    runs = np.diff(np.concatenate(([0], changes, [flat.size]))).tolist()
    if bool(flat[0]):
        runs.insert(0, 0)
    if len(runs) > MAX_RUNS:
        raise ValueError("Mask has too many alternating runs; no pixels were discarded.")
    return {"encoding": ENCODING, "size": [height, width], "origin": [x+x0, y+y0],
            "shape": [y1-y0, x1-x0], "counts": runs}


def decode_mask(value, *, expected_size=None, full=True) -> np.ndarray:
    """Validate before allocation. ``full=False`` returns only the stored crop."""
    width, height, x, y, w, h, counts = _schema(value, expected_size)
    if type(full) is not bool:
        raise ValueError("full must be a boolean.")
    local = np.zeros(h*w, dtype=bool)
    offset = 0
    for index, count in enumerate(counts):
        if index % 2:
            local[offset:offset+count] = True
        offset += count
    local = local.reshape(h, w)
    if not full:
        return local
    result = np.zeros((height, width), dtype=bool)
    result[y:y+h, x:x+w] = local
    return result


def validate_mask(value, width, height, box=None, allow_empty=False) -> dict:
    """Check image alignment and foreground pixel-center containment in a box."""
    _, _, x, y, w, h, counts = _schema(value, (width, height))
    foreground = sum(counts[1::2])
    if not allow_empty and not foreground:
        raise ValueError("An object encasing must contain foreground pixels.")
    if box is not None:
        bounds = np.asarray(box, dtype=np.float64)
        if bounds.shape != (4,) or not np.isfinite(bounds).all() or np.any(bounds[2:] <= bounds[:2]):
            raise ValueError("Mask requires a finite positive-area box.")
        local = decode_mask(value, full=False)
        yy, xx = np.flatnonzero(local.any(axis=1)), np.flatnonzero(local.any(axis=0))
        if len(xx) and (np.any(xx+x+.5 < bounds[0]) or np.any(xx+x+.5 >= bounds[2])
                        or np.any(yy+y+.5 < bounds[1]) or np.any(yy+y+.5 >= bounds[3])):
            raise ValueError("Mask foreground pixel centers must be inside the main box.")
    return value


def has_encasing(annotation: dict) -> bool:
    """Fast completeness hint for already-validated UI records."""
    value = annotation.get("mask")
    if isinstance(value, dict):
        counts = value.get("counts", [])
        return bool(isinstance(counts, list) and any(counts[1::2]))
    return bool(annotation.get("wrapper"))


def polygon_mask(points, width, height) -> np.ndarray:
    """Rasterize a legacy polygon at pixel centers using an even-odd fill rule."""
    _size((width, height))
    p = np.asarray(points, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 2 or len(p) < 3 or not np.isfinite(p).all():
        raise ValueError("A legacy encasing needs a finite polygon.")
    result = np.zeros((height, width), dtype=bool)
    x0 = max(0, int(math.floor(p[:, 0].min())))
    y0 = max(0, int(math.floor(p[:, 1].min())))
    x1 = min(width, int(math.ceil(p[:, 0].max())))
    y1 = min(height, int(math.ceil(p[:, 1].max())))
    if x1 <= x0 or y1 <= y0:
        return result
    xx = np.arange(x0, x1, dtype=np.float64)[None, :] + .5
    yy = np.arange(y0, y1, dtype=np.float64)[:, None] + .5
    inside = np.zeros((y1-y0, x1-x0), dtype=bool)
    for a, b in zip(p, np.roll(p, -1, axis=0)):
        if a[1] != b[1]:
            crossings = (a[1] > yy) != (b[1] > yy)
            edge_x = a[0] + (yy-a[1]) * (b[0]-a[0]) / (b[1]-a[1])
            inside ^= crossings & (xx < edge_x)
    result[y0:y1, x0:x1] = inside
    return result


def annotation_mask(annotation, width, height) -> np.ndarray:
    """The exact stored mask is authoritative; old polygons remain readable."""
    if annotation.get("mask") is not None:
        validate_mask(annotation["mask"], width, height, annotation.get("box"))
        return decode_mask(annotation["mask"], expected_size=(width, height))
    return polygon_mask(annotation.get("wrapper", []), width, height)


def project_roi_mask(probabilities, box, image_size, *, threshold=.5) -> dict:
    """Project a spatial ROI prediction onto image pixel centers.

    Bilinear interpolation uses align_corners=False geometry and border values.
    Only centers inside the predicted floating-point box may become foreground.
    Thresholding is the inference decision, not a lossless storage operation.
    """
    width, height = _size(image_size)
    p = np.asarray(probabilities, dtype=np.float32)
    while p.ndim > 2 and p.shape[0] == 1:
        p = p[0]
    bounds = np.asarray(box, dtype=np.float64)
    if p.ndim != 2 or not min(p.shape) or not np.isfinite(p).all():
        raise ValueError("ROI probabilities must be a finite two-dimensional grid.")
    if bounds.shape != (4,) or not np.isfinite(bounds).all() or np.any(bounds[2:] <= bounds[:2]):
        raise ValueError("ROI box must have finite positive dimensions.")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0 < threshold < 1:
        raise ValueError("Mask threshold must be between 0 and 1.")
    x0, y0 = max(0, math.floor(bounds[0])), max(0, math.floor(bounds[1]))
    x1, y1 = min(width, math.ceil(bounds[2])), min(height, math.ceil(bounds[3]))
    if x1 <= x0 or y1 <= y0:
        return encode_mask(np.zeros((0, 0), bool), image_size=(width, height))
    xs, ys = np.arange(x0, x1, dtype=np.float64)+.5, np.arange(y0, y1, dtype=np.float64)+.5
    gx = np.clip((xs-bounds[0])/(bounds[2]-bounds[0])*p.shape[1]-.5, 0, p.shape[1]-1)
    gy = np.clip((ys-bounds[1])/(bounds[3]-bounds[1])*p.shape[0]-.5, 0, p.shape[0]-1)
    lx, ly = np.floor(gx).astype(np.int64), np.floor(gy).astype(np.int64)
    hx, hy = np.minimum(lx+1, p.shape[1]-1), np.minimum(ly+1, p.shape[0]-1)
    wx, wy = (gx-lx).astype(np.float32), (gy-ly).astype(np.float32)
    top = p[ly[:, None], lx[None, :]] * (1-wx)[None, :] + p[ly[:, None], hx[None, :]] * wx[None, :]
    bottom = p[hy[:, None], lx[None, :]] * (1-wx)[None, :] + p[hy[:, None], hx[None, :]] * wx[None, :]
    values = top * (1-wy)[:, None] + bottom * wy[:, None]
    valid = ((xs >= bounds[0]) & (xs < bounds[2]))[None, :] & ((ys >= bounds[1]) & (ys < bounds[3]))[:, None]
    return encode_mask((values >= threshold) & valid, origin=(x0, y0), image_size=(width, height))


def binary_mask_metrics(predicted, target, *, boundary_tolerance=2) -> dict[str, Any]:
    """Pixel IoU, spill/missing counts, and symmetric boundary F1 (in pixels)."""
    p, t = np.asarray(predicted, dtype=bool), np.asarray(target, dtype=bool)
    if p.ndim != 2 or p.shape != t.shape or p.size > MAX_PIXELS:
        raise ValueError("Mask metrics require equally sized two-dimensional masks.")
    if type(boundary_tolerance) is not int or not 0 <= boundary_tolerance <= 32:
        raise ValueError("Boundary tolerance must be an integer from 0 to 32.")
    def edge(a):
        padded = np.pad(a, 1)
        interior = a.copy()
        for dy, dx in ((0,1),(2,1),(1,0),(1,2)):
            interior &= padded[dy:dy+a.shape[0], dx:dx+a.shape[1]]
        return a & ~interior
    def dilate(a, radius):
        padded = np.pad(a, radius)
        output = np.zeros_like(a)
        for dy in range(-radius, radius+1):
            for dx in range(-radius, radius+1):
                if dx*dx+dy*dy <= radius*radius:
                    output |= padded[radius+dy:radius+dy+a.shape[0], radius+dx:radius+dx+a.shape[1]]
        return output
    intersection, union = int(np.count_nonzero(p & t)), int(np.count_nonzero(p | t))
    pcount, tcount = int(np.count_nonzero(p)), int(np.count_nonzero(t))
    pe, te = edge(p), edge(t)
    pn, tn = int(np.count_nonzero(pe)), int(np.count_nonzero(te))
    precision = float(np.count_nonzero(pe & dilate(te, boundary_tolerance))/pn) if pn else 0.
    recall = float(np.count_nonzero(te & dilate(pe, boundary_tolerance))/tn) if tn else 0.
    return {"iou": intersection/union if union else 1.,
            "boundary_f1": 2*precision*recall/(precision+recall) if precision+recall else (1. if not pn and not tn else 0.),
            "predicted_pixels": pcount, "target_pixels": tcount,
            "background_pixels_included": pcount-intersection,
            "foreground_pixels_missed": tcount-intersection,
            "background_fraction_of_prediction": (pcount-intersection)/pcount if pcount else 0.,
            "missed_fraction_of_target": (tcount-intersection)/tcount if tcount else 0.}


def encasing_equal(first, second) -> bool:
    """Compare training truth, independently of an approximate display polygon."""
    if first.get("mask") is not None or second.get("mask") is not None:
        return first.get("mask") == second.get("mask")
    return (first.get("wrapper") or []) == (second.get("wrapper") or [])


def replace_encasing(annotation, proposal) -> dict:
    """Replace both encasing representations so an old mask cannot linger."""
    result = dict(annotation)
    for key in ("mask", "wrapper_is_approximate", "wrapper_kind"):
        result.pop(key, None)
    result["wrapper"] = proposal.get("wrapper") or []
    if proposal.get("mask") is not None:
        result["mask"] = proposal["mask"]
        result["wrapper_is_approximate"] = True
    return result
