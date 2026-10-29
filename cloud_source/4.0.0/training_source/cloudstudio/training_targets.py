"""Deterministic detection assignment and source-resolution mask supervision.

This module has no Torch dependency. Annotation masks remain in the project;
only sampled supervision enters a training batch, never a deployment model.
"""
from __future__ import annotations

import numpy as np


def assign_center_cells(boxes: np.ndarray, grid_size: int, *, nearby: bool = False) -> np.ndarray:
    """Assign every instance a unique detector slot, or reject the sample.

    S1 keeps its exact center-cell restriction. S2 can encode centers displaced
    by less than 1.5 cells from a slot's center, so a deterministic augmenting-
    path matching can use a 3x3 neighborhood. It never silently drops a target.
    This is a bounded collision mitigation, not unlimited crowd capacity.
    """
    values = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    if type(grid_size) is not int or grid_size < 1 or not np.isfinite(values).all():
        raise ValueError("Assignment needs a positive grid size and finite boxes.")
    centers = (values[:, :2] + values[:, 2:]) * (.5 * grid_size)
    natural = np.clip(np.floor(centers), 0, grid_size - 1).astype(np.int64)
    if not len(values):
        return natural
    if not nearby:
        if len(np.unique(natural, axis=0)) != len(natural):
            raise ValueError("Center-cell collision: the legacy detector supports one object "
                             "center per stride-8 cell. Increase input_size or use the S2 "
                             "mask architecture; no instance has been silently dropped.")
        return natural

    candidates = []
    for center, base in zip(centers, natural):
        slots = []
        for y in range(max(0, int(base[1]) - 1), min(grid_size, int(base[1]) + 2)):
            for x in range(max(0, int(base[0]) - 1), min(grid_size, int(base[0]) + 2)):
                displacement = center - np.asarray([x + .5, y + .5])
                # Sigmoid endpoints are open. A target exactly at the outer
                # endpoint must use a different, representable slot.
                if np.max(np.abs(displacement)) < 1.5:
                    slots.append((float(displacement @ displacement), y, x))
        candidates.append([(x, y) for _, y, x in sorted(slots)])

    owners: dict[tuple[int, int], int] = {}
    assigned: dict[int, tuple[int, int]] = {}

    def place(instance: int, visited: set[tuple[int, int]]) -> bool:
        # Prefer available slots before displacing a previously matched target.
        # This preserves natural assignments when centers do not collide.
        choices = candidates[instance]
        for free_only in (True, False):
            for slot in choices:
                if slot in visited or (slot not in owners) != free_only:
                    continue
                visited.add(slot)
                previous = owners.get(slot)
                if previous is None or place(previous, visited):
                    owners[slot] = instance
                    assigned[instance] = slot
                    return True
        return False

    order = sorted(range(len(values)), key=lambda i: (len(candidates[i]), i))
    for instance in order:
        if not place(instance, set()):
            raise ValueError("Center-cell collision exceeds the S2 detector's nearby-slot "
                             "capacity. Increase input_size or separate this exceptionally "
                             "crowded example; no instance has been silently dropped.")
    return np.asarray([assigned[i] for i in range(len(values))], dtype=np.int64)


def sample_mask_targets(targets: list[dict], guides: np.ndarray,
                        batch_indices: np.ndarray, instance_indices: np.ndarray,
                        size: int, *, samples: int = 2) -> np.ndarray:
    """Sample source binary labels at each predicted/teacher ROI's actual extent.

    Supersampling estimates the foreground fraction in each output cell. The
    source mask is never first downscaled to the detector input. Pixel centers,
    exact x/y letterbox scales and augmentation inversion share the image's
    geometry. Points outside the image are background, including padding.
    """
    if type(size) is not int or size < 1 or samples not in (1, 2, 4):
        raise ValueError("Mask targets need a positive output size and 1, 2, or 4 samples per axis.")
    boxes = np.asarray(guides, dtype=np.float64).reshape(-1, 4)
    batches = np.asarray(batch_indices, dtype=np.int64).reshape(-1)
    instances = np.asarray(instance_indices, dtype=np.int64).reshape(-1)
    if len(boxes) != len(batches) or len(boxes) != len(instances):
        raise ValueError("Each mask guide needs its source image and instance indices.")
    result = np.zeros((len(boxes), 1, size, size), dtype=np.float32)
    unit = (np.arange(size * samples, dtype=np.float64) + .5) / (size * samples)
    for row, (box, batch, instance) in enumerate(zip(boxes, batches, instances)):
        target = targets[int(batch)]
        mask = np.asarray(target["mask_sources"][int(instance)], dtype=np.bool_)
        origins = target.get("mask_origins")
        origin_x, origin_y = origins[int(instance)] if origins is not None else (0, 0)
        meta = target["mask_meta"]
        transform = target.get("mask_transform") or {}
        zoom = float(transform.get("scale", 1.0))
        dx, dy = transform.get("translation", (0.0, 0.0))
        xs = box[0] + (box[2] - box[0]) * unit
        ys = box[1] + (box[3] - box[1]) * unit
        xs = (xs - .5 - float(dx)) / zoom + .5
        ys = (ys - .5 - float(dy)) / zoom + .5
        if transform.get("horizontal_flip", False):
            xs = 1.0 - xs
        xs = np.floor((xs * meta["size"] - meta["pad_x"]) /
                      meta.get("scale_x", meta["scale"])).astype(np.int64)
        ys = np.floor((ys * meta["size"] - meta["pad_y"]) /
                      meta.get("scale_y", meta["scale"])).astype(np.int64)
        xs -= int(origin_x)
        ys -= int(origin_y)
        valid_x = (xs >= 0) & (xs < mask.shape[1])
        valid_y = (ys >= 0) & (ys < mask.shape[0])
        sampled = mask[np.clip(ys, 0, mask.shape[0] - 1)[:, None],
                       np.clip(xs, 0, mask.shape[1] - 1)[None, :]]
        sampled = sampled & valid_y[:, None] & valid_x[None, :]
        result[row, 0] = sampled.reshape(size, samples, size, samples).mean(axis=(1, 3))
    return result
