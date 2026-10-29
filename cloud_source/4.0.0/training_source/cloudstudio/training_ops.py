"""Training-only operations that preserve the published model's ROI semantics."""

from __future__ import annotations

import torch
from torch import Tensor


def roi_bilinear_deterministic(features: Tensor, grid: Tensor, batch_indices: Tensor) -> Tensor:
    """Bilinear, zero-padded, align_corners=True sampling without grid_sample.

    CUDA grid_sample backward has no strict deterministic implementation. This
    equivalent gather formulation allows PyTorch's deterministic gather backward
    instead. It samples the retained batch directly, so a target never requires
    a separate copy of the whole feature map. Strict algorithm checking remains
    enabled: a device/version that cannot execute deterministically raises.
    """
    batches, channels, height, width = features.shape
    count, roi_height, roi_width, _ = grid.shape
    if count != len(batch_indices):
        raise ValueError("Each ROI grid needs one source batch index.")
    if not count:
        return features.sum().mul(0).expand(0, channels, roi_height, roi_width)
    # CUDA grid_sample accumulates interpolation in float for half/bfloat16
    # inputs. Keep double precision for numerical/gradient contract checks.
    work_dtype = torch.float64 if features.dtype == torch.float64 else torch.float32
    coordinates = grid.to(work_dtype)
    x = (coordinates[..., 0] + 1) * ((width - 1) / 2)
    y = (coordinates[..., 1] + 1) * ((height - 1) / 2)
    x0, y0 = x.floor(), y.floor()
    x1, y1 = x0 + 1, y0 + 1
    base = batch_indices[:, None, None] * (height * width)
    flat = features.to(work_dtype).permute(1, 0, 2, 3).reshape(channels, -1)

    def corner(cx: Tensor, cy: Tensor, weight: Tensor) -> Tensor:
        valid = (cx >= 0) & (cx < width) & (cy >= 0) & (cy < height)
        index = base + cy.clamp(0, height - 1).long() * width + cx.clamp(0, width - 1).long()
        values = torch.gather(flat, 1, index.reshape(1, -1).expand(channels, -1))
        return values * (weight * valid).reshape(1, -1)

    sampled = (corner(x0, y0, (x1 - x) * (y1 - y)) +
               corner(x1, y0, (x - x0) * (y1 - y)) +
               corner(x0, y1, (x1 - x) * (y - y0)) +
               corner(x1, y1, (x - x0) * (y - y0)))
    return sampled.reshape(channels, count, roi_height, roi_width).permute(1, 0, 2, 3).to(features.dtype)


def resize_bilinear_deterministic(features: Tensor, output_size: tuple[int, int]) -> Tensor:
    """Bilinear resize with align_corners=False and deterministic gather backward.

    The S2 mask head upsamples its features before refinement. Older supported
    Torch releases cannot always differentiate CUDA bilinear interpolation in
    strict deterministic mode. Use the same pixel-center mapping and clamped
    border extension explicitly, without changing inference or stored weights.
    """
    if features.ndim != 4:
        raise ValueError("Bilinear resize needs N,C,H,W features.")
    if (not isinstance(output_size, (tuple, list)) or len(output_size) != 2 or
            any(type(value) is not int or value < 1 for value in output_size)):
        raise ValueError("Resize output dimensions must be positive integers.")
    batch, _, height, width = features.shape
    output_height, output_width = output_size
    dtype = torch.float64 if features.dtype == torch.float64 else torch.float32
    xs = ((torch.arange(output_width, device=features.device, dtype=dtype) + .5)
          * (width / output_width) - .5).clamp(0, width - 1)
    ys = ((torch.arange(output_height, device=features.device, dtype=dtype) + .5)
          * (height / output_height) - .5).clamp(0, height - 1)
    xs = xs * (2 / max(width - 1, 1)) - 1
    ys = ys * (2 / max(height - 1, 1)) - 1
    gy, gx = torch.meshgrid(ys, xs, indexing="ij")
    grid = torch.stack((gx, gy), dim=-1)[None].expand(batch, -1, -1, -1)
    return roi_bilinear_deterministic(features, grid,
        torch.arange(batch, dtype=torch.int64, device=features.device))
