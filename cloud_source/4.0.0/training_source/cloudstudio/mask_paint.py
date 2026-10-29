"""Shared exact-mask Qt rendering for annotation, preview and live overlays.

The returned image owns its pixels. Callers may cache it when a result changes
and draw it repeatedly without decoding the mask during each paint event.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QColor, QImage


def make_mask_image(mask, color=QColor(83, 239, 201), alpha: int = 36, *,
                    outline_alpha: int | None = 245) -> tuple[QImage, tuple[int, int]]:
    """Return a cropped RGBA image plus its origin in source image pixels.

    Canonical RLE is decoded only to its stored crop. Boolean arrays are also
    accepted. Holes and the space between separate pieces remain transparent.
    A one-pixel interior boundary can be emphasized without painting outside
    the authoritative foreground. Pass outline_alpha=None for uniform fill.
    """
    for name, value in (("alpha", alpha), ("outline_alpha", outline_alpha)):
        if (name == "alpha" or value is not None) and (type(value) is not int or not 0 <= value <= 255):
            raise ValueError(f"{name} must be an integer from 0 to 255.")
    if isinstance(mask, dict):
        from .masks import decode_mask
        pixels = decode_mask(mask, full=False)
        origin = tuple(mask["origin"])
    else:
        pixels = np.asarray(mask)
        if pixels.ndim != 2 or pixels.dtype != np.bool_:
            raise ValueError("Mask rendering needs a boolean array or a canonical encoded mask.")
        origin = (0, 0)
    if pixels.ndim != 2:
        raise ValueError("Mask rendering needs a two-dimensional mask.")
    if not pixels.any():
        return QImage(), (int(origin[0]), int(origin[1]))
    ys = np.flatnonzero(pixels.any(axis=1))
    xs = np.flatnonzero(pixels.any(axis=0))
    left, top, right, bottom = int(xs[0]), int(ys[0]), int(xs[-1] + 1), int(ys[-1] + 1)
    pixels = np.ascontiguousarray(pixels[top:bottom, left:right])
    origin = (int(origin[0]) + left, int(origin[1]) + top)
    tint = color if isinstance(color, QColor) else (QColor(*color) if isinstance(color, (tuple, list)) else QColor(color))
    if not tint.isValid():
        raise ValueError("Mask rendering needs a valid color.")
    rgba = np.zeros((*pixels.shape, 4), dtype=np.uint8)
    rgba[..., :3] = (tint.red(), tint.green(), tint.blue())
    rgba[..., 3] = pixels.astype(np.uint8) * alpha
    if outline_alpha is not None:
        padded = np.pad(pixels, 1, constant_values=False)
        interior = (padded[:-2, 1:-1] & padded[2:, 1:-1] &
                    padded[1:-1, :-2] & padded[1:-1, 2:])
        rgba[..., 3][pixels & ~interior] = outline_alpha
    height, width = pixels.shape
    image = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    return image, origin
