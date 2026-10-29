"""Deterministic image variation with linked box and encasing geometry.

No annotated target is removed by an augmentation. A bounded affine candidate
is accepted only if every box stays within the image and every target has a
representable detector slot. S2 masks use the returned transform to sample
their source-resolution labels; holes are not filled during augmentation.
"""
from __future__ import annotations

import hashlib
import io
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


def _rng(namespace: str, seed: int, epoch: int, sample_key: str):
    encoded = f"{namespace}:{seed}:{epoch}:{sample_key}".encode("utf-8")
    return np.random.default_rng(int.from_bytes(hashlib.sha256(encoded).digest()[:8], "little"))


def _setting(config: Any, key: str, default=0.0):
    return config.get(key, default) if isinstance(config, dict) else getattr(config, key, default)


def augment_gameplay_image(image: Image.Image, *, seed: int, epoch: int,
                           sample_key: str, brightness: float = .15,
                           contrast: float = .15, blur_probability: float = .25,
                           blur_radius: float = .8, jpeg_probability: float = .35,
                           jpeg_min_quality: int = 75) -> Image.Image:
    """Lighting, blur and JPEG effects; defaults reproduce gameplay-v1 exactly.

    The RNG namespace and draw order are kept compatible with 3.1.x, including
    a blur lower radius of min(0.2, maximum). Geometry is unchanged.
    """
    rng = _rng("cloud-gameplay-v1", seed, epoch, sample_key)
    result = ImageEnhance.Brightness(image).enhance(float(rng.uniform(1 - brightness, 1 + brightness)))
    result = ImageEnhance.Contrast(result).enhance(float(rng.uniform(1 - contrast, 1 + contrast)))
    if rng.random() < blur_probability:
        result = result.filter(ImageFilter.GaussianBlur(
            radius=float(rng.uniform(min(.2, blur_radius), blur_radius))))
    if rng.random() < jpeg_probability:
        encoded = io.BytesIO()
        result.save(encoded, format="JPEG", quality=int(rng.integers(jpeg_min_quality, 96)), subsampling=0)
        encoded.seek(0)
        with Image.open(encoded) as decoded:
            result = decoded.convert("RGB")
    return result


def _hsv(image: Image.Image, rng, hue: float, saturation: float, value: float):
    if not (hue or saturation or value):
        return image
    hsv = np.asarray(image.convert("HSV"), dtype=np.uint8).copy()
    shift = float(rng.uniform(-hue, hue)) * 256.0
    saturation_gain = float(rng.uniform(1 - saturation, 1 + saturation))
    value_gain = float(rng.uniform(1 - value, 1 + value))
    hsv[..., 0] = np.remainder(hsv[..., 0].astype(np.float32) + shift, 256).astype(np.uint8)
    hsv[..., 1] = np.clip(hsv[..., 1].astype(np.float32) * saturation_gain, 0, 255).astype(np.uint8)
    hsv[..., 2] = np.clip(hsv[..., 2].astype(np.float32) * value_gain, 0, 255).astype(np.uint8)
    # frombytes avoids the deprecated explicit mode argument of fromarray.
    return Image.frombytes("HSV", image.size, hsv.tobytes()).convert("RGB")


def _cells(boxes: np.ndarray, grid: int) -> np.ndarray:
    centers = (boxes[:, :2] + boxes[:, 2:]) * .5
    return np.clip(np.floor(centers * grid), 0, grid - 1).astype(np.int64)


def _transform_targets(boxes, wrappers, zoom, dx, dy, flip):
    box_points = boxes.reshape(-1, 2, 2).astype(np.float64)
    points = wrappers.astype(np.float64)
    if flip:
        box_points[..., 0] = 1.0 - box_points[..., 0]
        points[..., 0] = 1.0 - points[..., 0]
    box_points = (box_points - .5) * zoom + .5 + [dx, dy]
    points = (points - .5) * zoom + .5 + [dx, dy]
    transformed_boxes = np.concatenate((box_points.min(axis=1), box_points.max(axis=1)), axis=1)
    return transformed_boxes.astype(np.float32), points.astype(np.float32)


def augment_training_sample(image_chw: np.ndarray, boxes: np.ndarray,
                            wrappers: np.ndarray, *, config: Any, seed: int,
                            epoch: int, sample_key: str):
    """Return pixels, linked boxes/encasings, center cells and transform details.

    Input pixels are square RGB float32 CHW in [0, 1]; geometry is normalized
    to the same square. HSV settings are independent of the optional gameplay
    effects, which the caller applies before letterboxing. Identity settings
    leave pixels bit-identical. A local RNG makes partial-epoch resume exact.
    """
    pixels = np.asarray(image_chw, dtype=np.float32)
    boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 4)
    wrappers = np.asarray(wrappers, dtype=np.float32)
    if pixels.ndim != 3 or pixels.shape[0] != 3 or pixels.shape[1] != pixels.shape[2]:
        raise ValueError("Training augmentation needs square RGB CHW pixels.")
    if wrappers.ndim != 3 or wrappers.shape[0] != len(boxes) or wrappers.shape[-1] != 2:
        raise ValueError("Every training box must have one linked encasing array.")
    if not all(np.isfinite(a).all() for a in (pixels, boxes, wrappers)):
        raise ValueError("Training pixels and linked geometry must be finite.")
    size = pixels.shape[-1]
    from .config import MASK_ARCHITECTURE
    nearby = _setting(config, "architecture", "") == MASK_ARCHITECTURE
    grid = size // (4 if nearby else 8)
    hue, saturation, value, translate, scale, fliplr = [
        float(_setting(config, key)) for key in ("hsv_h", "hsv_s", "hsv_v", "translate", "scale", "fliplr")
    ]
    info = {"geometry_applied": False, "geometry_attempts": 0, "reason": "disabled",
            "scale": 1.0, "translation": [0.0, 0.0], "horizontal_flip": False}
    if nearby:
        from .training_targets import assign_center_cells
        cells = assign_center_cells(boxes, grid, nearby=True)
    else:
        cells = _cells(boxes, grid)
    if not any((hue, saturation, value, translate, scale, fliplr)):
        return pixels.copy(), boxes.copy(), wrappers.copy(), cells, info

    pixel_rng = _rng("cloud-hsv-v1", seed, epoch, sample_key)
    geometry_rng = _rng("cloud-affine-v1", seed, epoch, sample_key)
    image = Image.fromarray(np.rint(np.clip(pixels.transpose(1, 2, 0), 0, 1) * 255).astype(np.uint8))
    image = _hsv(image, pixel_rng, hue, saturation, value)
    output_boxes, output_wrappers = boxes.copy(), wrappers.copy()
    if any((translate, scale, fliplr)):
        info["reason"] = "no_valid_bounded_transform"
        for attempt in range(1, 17):
            zoom = float(geometry_rng.uniform(1 - scale, 1 + scale))
            dx, dy = geometry_rng.uniform(-translate, translate, size=2)
            flip = bool(geometry_rng.random() < fliplr)
            candidate_boxes, candidate_wrappers = _transform_targets(boxes, wrappers, zoom, dx, dy, flip)
            info["geometry_attempts"] = attempt
            if len(candidate_boxes) and (np.any(candidate_boxes < 0) or np.any(candidate_boxes > 1)):
                continue
            if nearby:
                try:
                    candidate_cells = assign_center_cells(candidate_boxes, grid, nearby=True)
                except ValueError:
                    continue
            else:
                candidate_cells = _cells(candidate_boxes, grid)
                if len(np.unique(candidate_cells, axis=0)) != len(candidate_cells):
                    continue
            # Positive uniform scale/reflection preserves the simple-loop
            # topology and containment of the validated original encasings.
            # Pillow's affine matrix maps output edge coordinates to source.
            if flip:
                image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            offset_x = size * (.5 * (1 - zoom) + float(dx))
            offset_y = size * (.5 * (1 - zoom) + float(dy))
            if zoom != 1.0 or dx != 0.0 or dy != 0.0:
                image = image.transform(image.size, Image.Transform.AFFINE,
                    (1 / zoom, 0, -offset_x / zoom, 0, 1 / zoom, -offset_y / zoom),
                    resample=Image.Resampling.BILINEAR, fillcolor=(114, 114, 114))
            output_boxes, output_wrappers, cells = candidate_boxes, candidate_wrappers, candidate_cells
            info.update(geometry_applied=True, reason="accepted", scale=zoom,
                        translation=[float(dx), float(dy)], horizontal_flip=flip)
            break
    output = np.ascontiguousarray(np.asarray(image, dtype=np.float32).transpose(2, 0, 1) / 255.)
    return output, output_boxes, output_wrappers, cells, info
