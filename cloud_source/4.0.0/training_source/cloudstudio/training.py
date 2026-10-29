"""Paired box and encasing supervision for the Cloud Studio research model.

This trainer uses PyTorch's established autograd and optimizer machinery. Cloud
files contain inference weights only; resumable training state lives separately
in a .cloudtrain checkpoint loaded with PyTorch's restricted weights-only reader.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import tempfile
from typing import Any, Callable
from uuid import uuid4

import numpy as np
from PIL import Image
import torch
from torch import Tensor
from torch.nn import functional as F

from .data import Project
from .config import ARCHITECTURE
from .geometry import (is_simple_polygon, letterbox, resample_polygon, transform_box,
                       transform_points, validate_annotation)
from .model import CloudNet, ModelConfig, decode_boxes
from .training_controls import (learning_rate_for_update as _learning_rate_for_update,
                                make_optimizer as _make_optimizer, training_numerics)


@dataclass
class TrainingConfig:
    epochs: int = 50
    batch_size: int = 8
    learning_rate: float = 0.001
    input_size: int = 320
    vertices: int = 32
    channels: int = 48
    device: str = "auto"
    seed: int = 42
    box_weight: float = 2.0
    contour_weight: float = 3.0
    contour_edge_weight: float = 0.15
    warmup_epochs: int = 3
    weight_decay: float = 0.0001
    val_every: int = 1
    patience: int = 15
    amp: bool = True
    resume_path: str | None = None
    roi_size: int = 8
    cpu_threads: int = 4
    gradient_clip: float = 10.0
    guidance_transition_epochs: int = 5
    validation_confidence: float = 0.35
    allow_no_validation: bool = False
    init_model_path: str | None = None
    augmentation: str = "none"
    scheduler: str = "constant"
    lr_final_factor: float = 0.01
    lr_warmup_epochs: int = 0
    lr_warmup_start_factor: float = 0.1
    adam_beta1: float = 0.9
    adam_beta2: float = 0.999
    adam_epsilon: float = 1e-8
    weight_decay_bias: bool = True
    nominal_batch_size: int = 0
    deterministic: bool = False
    save_period: int = 0
    objectness_weight: float = 1.0
    focal_alpha: float = 0.75
    focal_gamma: float = 2.0
    validation_max_detections: int = 32
    hsv_h: float = 0.0
    hsv_s: float = 0.0
    hsv_v: float = 0.0
    translate: float = 0.0
    scale: float = 0.0
    fliplr: float = 0.0
    gameplay_brightness: float = 0.15
    gameplay_contrast: float = 0.15
    gameplay_blur_probability: float = 0.25
    gameplay_blur_radius: float = 0.8
    gameplay_jpeg_probability: float = 0.35
    gameplay_jpeg_min_quality: int = 75
    architecture: str = ARCHITECTURE
    mask_size: int = 64
    mask_bce_weight: float = 1.0
    mask_dice_weight: float = 1.0
    mask_boundary_weight: float = 0.2
    background_rois_per_image: int = 2
    negative_mask_weight: float = 0.25
    mask_target_samples: int = 2

    def __post_init__(self) -> None:
        for name in ("epochs", "val_every", "cpu_threads", "guidance_transition_epochs"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if type(self.batch_size) is not int or (self.batch_size != -1 and self.batch_size < 1):
            raise ValueError("batch_size must be a positive integer, or -1 for automatic sizing.")
        for name in ("warmup_epochs", "patience", "seed", "lr_warmup_epochs", "save_period", "nominal_batch_size"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer.")
        if self.seed >= 2 ** 32:
            raise ValueError("seed must be less than 2**32.")
        if self.cpu_threads > 64:
            raise ValueError("cpu_threads must be at most 64.")
        if self.nominal_batch_size > 65536:
            raise ValueError("nominal_batch_size must be at most 65536.")
        if type(self.validation_max_detections) is not int or not 1 <= self.validation_max_detections <= 256:
            raise ValueError("validation_max_detections must be an integer from 1 to 256.")
        for name in ("learning_rate", "box_weight", "contour_weight", "gradient_clip", "objectness_weight", "adam_epsilon"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number.")
        if (isinstance(self.weight_decay, bool) or not isinstance(self.weight_decay, (int, float))
                or not math.isfinite(self.weight_decay) or self.weight_decay < 0):
            raise ValueError("weight_decay must be finite and nonnegative.")
        if (isinstance(self.contour_edge_weight, bool) or not isinstance(self.contour_edge_weight, (int, float))
                or not math.isfinite(self.contour_edge_weight) or self.contour_edge_weight < 0):
            raise ValueError("contour_edge_weight must be finite and nonnegative.")
        if (not isinstance(self.validation_confidence, (int, float))
                or isinstance(self.validation_confidence, bool)
                or not math.isfinite(self.validation_confidence)
                or not 0 <= self.validation_confidence <= 1):
            raise ValueError("validation_confidence must be between 0 and 1.")
        for name in ("amp", "allow_no_validation", "weight_decay_bias", "deterministic"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean.")
        for name in ("lr_final_factor", "lr_warmup_start_factor"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(value) or not 0 < value <= 1):
                raise ValueError(f"{name} must be greater than 0 and at most 1.")
        for name in ("adam_beta1", "adam_beta2"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(value) or not 0 <= value < 1):
                raise ValueError(f"{name} must be at least 0 and less than 1.")
        if (isinstance(self.focal_alpha, bool) or not isinstance(self.focal_alpha, (int, float)) or
                not math.isfinite(self.focal_alpha) or not 0 < self.focal_alpha < 1):
            raise ValueError("focal_alpha must be greater than 0 and less than 1.")
        if (isinstance(self.focal_gamma, bool) or not isinstance(self.focal_gamma, (int, float)) or
                not math.isfinite(self.focal_gamma) or self.focal_gamma < 0):
            raise ValueError("focal_gamma must be finite and nonnegative.")
        if self.scheduler not in {"constant", "cosine"}:
            raise ValueError("scheduler must be 'constant' or 'cosine'.")
        for name in ("hsv_h", "hsv_s", "hsv_v", "translate", "scale", "fliplr",
                     "gameplay_brightness", "gameplay_contrast", "gameplay_blur_probability",
                     "gameplay_jpeg_probability"):
            value = getattr(self, name)
            upper = (0.5 if name in {"hsv_h", "translate"} else
                     0.9 if name == "scale" else 1.0)
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(value) or not 0 <= value <= upper):
                raise ValueError(f"{name} must be between 0 and {upper:g}.")
        if (isinstance(self.gameplay_blur_radius, bool) or
                not isinstance(self.gameplay_blur_radius, (int, float)) or
                not math.isfinite(self.gameplay_blur_radius) or not 0 <= self.gameplay_blur_radius <= 5):
            raise ValueError("gameplay_blur_radius must be between 0 and 5 pixels.")
        if type(self.gameplay_jpeg_min_quality) is not int or not 1 <= self.gameplay_jpeg_min_quality <= 95:
            raise ValueError("gameplay_jpeg_min_quality must be an integer from 1 to 95.")
        if not isinstance(self.device, str):
            raise ValueError("device must be auto, cpu, cuda, or cuda:N.")
        if self.resume_path is not None:
            self.resume_path = str(self.resume_path)
        if self.init_model_path is not None:
            self.init_model_path = str(self.init_model_path)
        if self.resume_path and self.init_model_path:
            raise ValueError("Choose either resume_path to continue an experiment or "
                             "init_model_path to improve an existing .cloud in a new run.")
        if self.augmentation not in {"none", "gameplay"}:
            raise ValueError("augmentation must be 'none' or 'gameplay'.")
        for name in ("mask_bce_weight", "mask_dice_weight", "mask_boundary_weight", "negative_mask_weight"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative.")
        if type(self.background_rois_per_image) is not int or not 0 <= self.background_rois_per_image <= 16:
            raise ValueError("background_rois_per_image must be an integer from 0 to 16.")
        if type(self.mask_target_samples) is not int or self.mask_target_samples not in (1, 2, 4):
            raise ValueError("mask_target_samples must be 1, 2, or 4.")
        model_config = _model_config(self)
        if model_config.is_mask and self.mask_bce_weight <= 0:
            raise ValueError("The S2 mask architecture requires a positive mask_bce_weight "
                             "so both foreground and background cells receive supervision.")

    def to_dict(self) -> dict:
        return asdict(self)


def _model_config(config: TrainingConfig) -> ModelConfig:
    return ModelConfig(config.input_size, config.vertices, config.channels, config.roi_size,
                       architecture=config.architecture, mask_size=config.mask_size)


def _device(requested: str) -> torch.device:
    if requested == "auto":
        requested = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        result = torch.device(requested)
    except (RuntimeError, ValueError) as exc:
        raise ValueError("device must be auto, cpu, cuda, or cuda:N.") from exc
    if result.type not in {"cpu", "cuda"}:
        raise ValueError("This trainer supports CPU and NVIDIA CUDA devices.")
    if result.type == "cuda":
        if not torch.cuda.is_available():
            raise ValueError("CUDA was requested but is unavailable in this PyTorch installation.")
        if result.index is not None and result.index >= torch.cuda.device_count():
            raise ValueError("The requested CUDA device index does not exist.")
    return result


def _augment_gameplay(image: Image.Image, *, seed: int, epoch: int, sample_key: str) -> Image.Image:
    """Reproducible, pixel-only perturbations; box and polygon geometry stays exact.

    The local generator depends on the image content and epoch, never access
    order or the process RNG. A resumed partial epoch receives identical pixels
    even when a different number of batches were evaluated before cancellation.
    """
    from .augmentation import augment_gameplay_image
    return augment_gameplay_image(image, seed=seed, epoch=epoch, sample_key=sample_key)


def _decoded_mask_sources(record: dict) -> tuple[list[np.ndarray], list[tuple[int, int]]]:
    """Decode only foreground bounding crops, avoiding image-sized padding per ROI."""
    from .masks import decode_mask, polygon_mask
    sources, origins = [], []
    for annotation in record["annotations"]:
        if annotation.get("mask") is not None:
            source = decode_mask(annotation["mask"], expected_size=(record["width"], record["height"]), full=False)
            origin = tuple(annotation["mask"]["origin"])
        else:
            points = np.asarray(annotation["wrapper"], dtype=np.float64)
            left, top = np.maximum(np.floor(points.min(axis=0)).astype(np.int64), 0)
            right, bottom = np.minimum(np.ceil(points.max(axis=0)).astype(np.int64),
                                       (record["width"], record["height"]))
            origin = (int(left), int(top))
            source = polygon_mask(points - [left, top], int(right-left), int(bottom-top))
        sources.append(source)
        origins.append(origin)
    return sources, origins


class _DetectionDataset:
    """Cache validated targets, but decode image tensors on demand.

    A single-threaded loader avoids Windows multiprocessing surprises and keeps
    worker cancellation predictable. This baseline prioritizes correctness over
    claiming maximum data-loader throughput.
    """

    def __init__(self, project: Project, records: list[dict], config: ModelConfig,
                 should_stop: Callable[[], bool] | None = None, *,
                 augmentation: str = "none", seed: int = 42,
                 training_config: TrainingConfig | None = None,
                 require_representable_labels: bool = True,
                 mask_target_samples: int | None = None):
        if augmentation not in {"none", "gameplay"}:
            raise ValueError("augmentation must be 'none' or 'gameplay'.")
        self.project, self.records, self.config = project, records, config
        self.augmentation, self.seed, self.epoch = augmentation, seed, 0
        self.training_config = training_config
        if type(require_representable_labels) is not bool:
            raise ValueError("require_representable_labels must be a boolean.")
        self.targets, self.hashes = [], []
        self.cancelled = False
        grid_size = config.input_size // config.grid_stride
        for record in records:
            if should_stop is not None and should_stop():
                self.cancelled = True
                break
            for annotation in record["annotations"]:
                errors = validate_annotation(annotation, record["width"], record["height"],
                                             require_complete=True)
                if errors:
                    raise ValueError(f"Invalid reviewed labels in {record['id']}: " + "; ".join(errors))
                if require_representable_labels and not config.is_mask and annotation.get("mask") is not None:
                    from .masks import annotation_mask, polygon_mask
                    if (len(annotation.get("wrapper", [])) < 3 or not np.array_equal(
                            annotation_mask(annotation, record["width"], record["height"]),
                            polygon_mask(annotation["wrapper"], record["width"], record["height"]))):
                        raise ValueError(f"Image {record['id']} contains an authoritative mask that its "
                            "legacy polygon does not preserve. Choose the S2 spatial mask architecture "
                            "and start a new run; S1 cannot silently fill holes or discard mask regions.")
            path = project.image_path(record)
            with Image.open(path) as opened:
                if opened.size != (record["width"], record["height"]):
                    raise ValueError(f"Image dimensions changed for {record['id']}; re-import it.")
                _, meta = letterbox(opened.convert("RGB"), config.input_size)
            boxes = np.asarray([transform_box(ann["box"], meta)
                                for ann in record["annotations"]], dtype=np.float32).reshape(-1, 4)
            boxes /= config.input_size
            sampled_wrappers = []
            for instance_index, ann in enumerate(record["annotations"]
                    if not config.is_mask and require_representable_labels else []):
                sampled = resample_polygon(ann["wrapper"], config.vertices)
                if not is_simple_polygon(sampled):
                    instance_id = ann.get("id", instance_index + 1)
                    raise ValueError(
                        f"Encasing resampling in image {record['id']}, instance {instance_id}, "
                        f"does not form a valid simple loop at {config.vertices} model vertices. "
                        "Increase the model vertex count when available, or simplify/review this outline.")
                sampled_wrappers.append(transform_points(sampled, meta))
            wrappers = (np.zeros((len(boxes), config.vertices, 2), dtype=np.float32)
                        if config.is_mask or not require_representable_labels else
                        np.asarray(sampled_wrappers, dtype=np.float32).reshape(-1, config.vertices, 2))
            wrappers /= config.input_size
            if config.is_mask and require_representable_labels:
                from .training_targets import assign_center_cells
                try:
                    cells = assign_center_cells(boxes, grid_size, nearby=True)
                except ValueError as exc:
                    raise ValueError(f"Image {record['id']}: {exc}") from exc
            else:
                centers = (boxes[:, :2] + boxes[:, 2:]) * 0.5
                cells = np.clip(np.floor(centers * grid_size), 0, grid_size - 1).astype(np.int64)
                unique, counts = np.unique(cells, axis=0, return_counts=True)
                collisions = unique[counts > 1]
                if len(collisions) and require_representable_labels:
                    collision_text = ", ".join(f"({x}, {y})" for x, y in collisions)
                    raise ValueError(
                        f"Center-cell collision in image {record['id']} at grid cell(s) {collision_text}. "
                        "This v0.1 detector supports one object center per stride-8 cell. "
                        "Increase input_size or curate this sample; no instance has been silently dropped.")
            target = {"boxes": torch.from_numpy(boxes), "wrappers": torch.from_numpy(wrappers),
                      "cells": torch.from_numpy(cells)}
            if config.is_mask:
                target["mask_meta"] = meta
            if config.is_mask and require_representable_labels:
                from .training_targets import sample_mask_targets
                sources, origins = _decoded_mask_sources(record)
                count = len(boxes)
                teacher_masks = sample_mask_targets([{**target, "mask_sources": sources,
                    "mask_origins": origins}], boxes, np.zeros(count, dtype=np.int64),
                    np.arange(count, dtype=np.int64), config.mask_size,
                    samples=(mask_target_samples if mask_target_samples is not None else
                             training_config.mask_target_samples if training_config else 2))
                unsupported = np.flatnonzero(~(teacher_masks >= .5).any(axis=(1, 2, 3)))
                if len(unsupported):
                    names = ", ".join(str(record["annotations"][int(i)].get("id", int(i) + 1)) for i in unsupported[:8])
                    raise ValueError(f"Image {record['id']}, target(s) {names}: the selected mask "
                        "resolution produces an empty foreground target in the annotated box. "
                        "Increase mask resolution, or review an unnecessarily loose box while "
                        "keeping the full target enclosed. This target will not be silently trained as background.")
                del teacher_masks, sources, origins
            self.targets.append(target)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            self.hashes.append(digest.hexdigest())

    def __len__(self) -> int:
        return len(self.targets)

    def __getitem__(self, index: int) -> tuple[Tensor, dict]:
        record = self.records[index]
        with Image.open(self.project.image_path(record)) as opened:
            pixels = opened.convert("RGB")
            if self.augmentation == "gameplay":
                if self.training_config is None:
                    pixels = _augment_gameplay(pixels, seed=self.seed, epoch=self.epoch,
                                               sample_key=self.hashes[index])
                else:
                    from .augmentation import augment_gameplay_image
                    settings = self.training_config
                    pixels = augment_gameplay_image(pixels, seed=self.seed, epoch=self.epoch,
                        sample_key=self.hashes[index], brightness=settings.gameplay_brightness,
                        contrast=settings.gameplay_contrast,
                        blur_probability=settings.gameplay_blur_probability,
                        blur_radius=settings.gameplay_blur_radius,
                        jpeg_probability=settings.gameplay_jpeg_probability,
                        jpeg_min_quality=settings.gameplay_jpeg_min_quality)
            image, _ = letterbox(pixels, self.config.input_size)
        target = self.targets[index]
        if self.config.is_mask:
            sources, origins = _decoded_mask_sources(record)
            target = {**target, "mask_sources": sources, "mask_origins": origins}
        if self.training_config is not None and any(getattr(self.training_config, name)
                for name in ("hsv_h", "hsv_s", "hsv_v", "translate", "scale", "fliplr")):
            from .augmentation import augment_training_sample
            image, boxes, wrappers, cells, transform = augment_training_sample(
                image, target["boxes"].numpy(), target["wrappers"].numpy(),
                config=self.training_config, seed=self.seed, epoch=self.epoch,
                sample_key=self.hashes[index])
            target = {**target, "boxes": torch.from_numpy(boxes), "wrappers": torch.from_numpy(wrappers),
                      "cells": torch.from_numpy(cells)}
            if self.config.is_mask:
                target["mask_transform"] = transform
        return torch.from_numpy(image.copy()), target


def _data_fingerprint(datasets: list[_DetectionDataset]) -> str:
    signature = []
    for dataset in datasets:
        for record, content_hash in zip(dataset.records, dataset.hashes):
            signature.append({"id": record["id"], "content_sha256": content_hash,
                              "split": record["split"],
                              "group_id": record.get("group_id"),
                              "annotations": record["annotations"]})
    encoded = json.dumps(signature, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _check_splits(train_set: _DetectionDataset, val_set: _DetectionDataset) -> list[str]:
    if not len(train_set):
        raise ValueError("Training needs at least one reviewed train image.")
    if not any(len(target["boxes"]) for target in train_set.targets):
        raise ValueError("Training needs at least one complete, reviewed box-and-encasing pair; "
                         "a negative-only training set cannot teach player detection.")
    train_groups = {str(rec.get("group_id") or rec["id"]) for rec in train_set.records}
    val_groups = {str(rec.get("group_id") or rec["id"]) for rec in val_set.records}
    if train_groups & val_groups:
        raise ValueError("Training/validation group leakage: complete capture groups must stay "
                         "in one split. Conflicting groups: " + ", ".join(sorted(train_groups & val_groups)[:8]))
    if set(train_set.hashes) & set(val_set.hashes):
        raise ValueError("Identical image files occur in both train and val. Remove this "
                         "split leakage before training.")
    warnings = []
    if len(val_set) and not any(len(target["boxes"]) for target in val_set.targets):
        warnings.append("Validation contains only reviewed negatives. False positives can be measured, "
                        "but recall and wrapper quality cannot be measured.")
    if len(train_set) < 10 or 0 < len(val_set) < 5:
        warnings.append("This dataset is very small; treat its metrics as a workflow check, "
                        "not evidence of generalization to new game footage.")
    return warnings


def _cyclic_contour_parts(predicted: Tensor, target: Tensor, boxes: Tensor | None = None,
                          edge_weight: float = 0.15) -> tuple[Tensor, Tensor, Tensor]:
    """Cyclic position and target-edge-vector losses, preserving labeled concavities.

    The edge term discourages neighboring vertices from collapsing or reversing
    direction. It follows the target's local geometry rather than smoothing
    away arm/leg gaps, and is a regularizer rather than a simplicity guarantee.
    """
    if predicted.shape != target.shape or predicted.ndim != 3 or predicted.shape[-1] != 2:
        raise ValueError("Contour tensors must have matching N,K,2 shapes.")
    if not predicted.shape[0]:
        zero = predicted.sum() * 0
        return zero, zero, zero
    predicted, target = predicted.float(), target.float()
    variants = torch.stack([
        torch.roll(direction, shifts=shift, dims=1)
        for direction in (target, target.flip(1))
        for shift in range(target.shape[1])
    ], dim=1)
    difference = (predicted[:, None] - variants).abs()
    predicted_edges = predicted.roll(-1, dims=1) - predicted
    target_edges = variants.roll(-1, dims=2) - variants
    edge_difference = (predicted_edges[:, None] - target_edges).abs()
    if boxes is not None:
        scale = (boxes[:, 2:] - boxes[:, :2]).float().clamp_min(1e-4)
        difference = difference / scale[:, None, None, :]
        edge_difference = edge_difference / scale[:, None, None, :]
    position_errors = difference.mean(dim=(2, 3))
    # K scaling makes the edge weight comparable across 16/32/64-point models.
    edge_errors = edge_difference.mean(dim=(2, 3)) * predicted.shape[1]
    combined = position_errors + edge_weight * edge_errors
    chosen = combined.argmin(dim=1)
    rows = torch.arange(len(predicted), device=predicted.device)
    position_loss = position_errors[rows, chosen].mean()
    edge_loss = edge_errors[rows, chosen].mean()
    return position_loss + edge_weight * edge_loss, position_loss, edge_loss


def _cyclic_contour_loss(predicted: Tensor, target: Tensor, boxes: Tensor | None = None,
                         edge_weight: float = 0.15) -> Tensor:
    """Loss invariant to loop start and clockwise/counterclockwise order."""
    return _cyclic_contour_parts(predicted, target, boxes, edge_weight)[0]


def _aligned_giou(predicted: Tensor, target: Tensor) -> Tensor:
    intersection = (torch.minimum(predicted[:, 2:], target[:, 2:]) -
                    torch.maximum(predicted[:, :2], target[:, :2])).clamp_min(0).prod(dim=-1)
    predicted_area = (predicted[:, 2:] - predicted[:, :2]).clamp_min(0).prod(dim=-1)
    target_area = (target[:, 2:] - target[:, :2]).clamp_min(0).prod(dim=-1)
    union = (predicted_area + target_area - intersection).clamp_min(1e-7)
    enclosing = (torch.maximum(predicted[:, 2:], target[:, 2:]) -
                 torch.minimum(predicted[:, :2], target[:, :2])).clamp_min(0).prod(dim=-1).clamp_min(1e-7)
    return intersection / union - (enclosing - union) / enclosing


def _guidance_fraction(epoch_index: int, config: TrainingConfig) -> float:
    if epoch_index < config.warmup_epochs:
        return 0.0
    return min(1.0, (epoch_index - config.warmup_epochs + 1) / config.guidance_transition_epochs)


def _guiding_boxes(target: Tensor, predicted: Tensor, fraction: float) -> Tensor:
    if fraction <= 0:
        return target
    if fraction >= 1:
        return predicted.detach()
    extent = (target[:, 2:] - target[:, :2]).clamp_min(1e-4)
    noise = torch.randn_like(target) * torch.cat((extent, extent), dim=1) * 0.04
    perturbed = target + noise * (1 - fraction)
    guided = ((1 - fraction) * perturbed + fraction * predicted.detach()).clamp(0, 1)
    low = torch.minimum(guided[:, :2], guided[:, 2:])
    high = torch.maximum(guided[:, :2], guided[:, 2:])
    return torch.cat((low, torch.maximum(high, low + 1e-5).clamp_max(1)), dim=1)


def _batch_losses(model: CloudNet, images: Tensor, targets: list[dict],
                  config: TrainingConfig, epoch_index: int) -> tuple[Tensor, dict[str, Tensor]]:
    if model.config.is_mask:
        from .training_masks import mask_batch_losses
        return mask_batch_losses(model, images, targets, config, epoch_index)
    features, logits, raw_boxes = model.detect(images)
    decoded = decode_boxes(raw_boxes.float())
    objectness = torch.zeros_like(logits)
    batch_indices, xs, ys, boxes, contours = [], [], [], [], []
    for batch_index, target in enumerate(targets):
        count = len(target["boxes"])
        if not count:
            continue
        cells = target["cells"].to(images.device)
        objectness[batch_index, 0, cells[:, 1], cells[:, 0]] = 1
        batch_indices.append(torch.full((count,), batch_index, dtype=torch.int64, device=images.device))
        xs.append(cells[:, 0])
        ys.append(cells[:, 1])
        boxes.append(target["boxes"].to(images.device))
        contours.append(target["wrappers"].to(images.device))
    positives = sum(len(target["boxes"]) for target in targets)
    # Sparse-positive sigmoid focal loss. Normalizing by positives keeps batches
    # with different object counts comparable; reviewed negatives remain useful.
    bce = F.binary_cross_entropy_with_logits(logits.float(), objectness.float(), reduction="none")
    probabilities = logits.float().sigmoid()
    pt = probabilities * objectness + (1 - probabilities) * (1 - objectness)
    alpha = config.focal_alpha * objectness + (1 - config.focal_alpha) * (1 - objectness)
    focal = (1 - pt).square() if config.focal_gamma == 2 else (1 - pt).pow(config.focal_gamma)
    detection_loss = (alpha * focal * bce).sum() / max(positives, 1)
    if positives:
        batch_idx, x, y = torch.cat(batch_indices), torch.cat(xs), torch.cat(ys)
        target_boxes, target_contours = torch.cat(boxes), torch.cat(contours)
        predicted_boxes = decoded[batch_idx, y, x]
        box_loss = ((1 - _aligned_giou(predicted_boxes, target_boxes)).mean() +
                    F.l1_loss(predicted_boxes, target_boxes))
        guides = _guiding_boxes(target_boxes, predicted_boxes,
                                _guidance_fraction(epoch_index, config))
        predicted_contours = model.wrap(features, guides, batch_idx)
        contour_loss, contour_position_loss, contour_edge_loss = _cyclic_contour_parts(
            predicted_contours, target_contours, target_boxes, config.contour_edge_weight)
    else:
        box_loss = raw_boxes.float().sum() * 0
        contour_loss = features.float().sum() * 0
        contour_position_loss, contour_edge_loss = contour_loss, contour_loss
    total = (config.objectness_weight * detection_loss + config.box_weight * box_loss +
             config.contour_weight * contour_loss)
    return total, {"loss": total, "objectness_loss": detection_loss,
                   "box_loss": box_loss, "contour_loss": contour_loss,
                   "contour_position_loss": contour_position_loss, "contour_edge_loss": contour_edge_loss}


def _window_loss(parts: dict[str, Tensor], config: TrainingConfig,
                 positives: int, window_positives: int, *,
                 images: int | None = None, window_images: int | None = None) -> Tensor:
    """Normalize all microbatches as one effective batch, including negatives.

    Dividing each already positive-normalized loss by a fixed accumulation
    count misweights crowded and negative microbatches. Undo each microbatch's
    normalization, then apply the denominator for the whole current window.
    """
    if "background_mask_loss" in parts:
        denominator = max(window_positives, 1)
        if images is None or window_images is None or not 0 < images <= window_images:
            raise ValueError("Mask accumulation needs the microbatch and effective-window image counts.")
        return (config.objectness_weight * parts["objectness_loss"] * (max(positives, 1) / denominator) +
                config.box_weight * parts["box_loss"] * (positives / denominator) +
                config.contour_weight * parts["contour_loss"] * (positives / denominator) +
                config.negative_mask_weight * parts["background_mask_loss"] * (images / window_images))
    if positives == window_positives:
        return parts["loss"]
    denominator = max(window_positives, 1)
    return (config.objectness_weight * parts["objectness_loss"] * (max(positives, 1) / denominator) +
            config.box_weight * parts["box_loss"] * (positives / denominator) +
            config.contour_weight * parts["contour_loss"] * (positives / denominator))


def _atomic_json(path: Path, value: dict) -> None:
    encoded = json.dumps(value, indent=2, allow_nan=False).encode("utf-8")
    _atomic_bytes(path, encoded)


def _atomic_bytes(path: Path, value: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Refusing to replace a symlink or non-file output: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_checkpoint(path: Path, value: dict) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Unsafe checkpoint output path: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=".checkpoint.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            torch.save(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _rng_state() -> dict:
    numpy_state = np.random.get_state()
    return {"python": random.getstate(), "torch": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
            "numpy": {"generator": numpy_state[0], "keys": numpy_state[1].tolist(),
                      "position": numpy_state[2], "has_gauss": numpy_state[3],
                      "cached_gaussian": numpy_state[4]}}


def _restore_rng(state: dict) -> None:
    random.setstate(state["python"])
    torch.set_rng_state(state["torch"].cpu())
    stored = state["numpy"]
    np.random.set_state((stored["generator"], np.asarray(stored["keys"], dtype=np.uint32),
                         stored["position"], stored["has_gauss"], stored["cached_gaussian"]))
    if torch.cuda.is_available() and len(state.get("cuda", [])) == torch.cuda.device_count():
        torch.cuda.set_rng_state_all([value.cpu() for value in state["cuda"]])


def _load_checkpoint(path: str | Path) -> dict:
    path = Path(path).expanduser()
    if not path.is_file() or path.stat().st_size > 1024 ** 3:
        raise ValueError("Resume checkpoint is missing or exceeds the 1 GiB safety limit.")
    try:
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    except Exception as exc:
        raise ValueError("Cannot read this Cloud training checkpoint with the restricted "
                         "weights-only loader. Use a checkpoint produced by Cloud Studio; "
                         "unsafe pickle loading is not enabled.") from exc
    required = {"checkpoint_version", "architecture", "config", "model_config", "model", "optimizer", "rng", "state",
                "dataset_fingerprint", "run_id", "scaler", "best_model", "history"}
    if (not isinstance(checkpoint, dict) or not required <= checkpoint.keys() or
            checkpoint["checkpoint_version"] not in {2, 3}):
        raise ValueError("Unsupported or incomplete Cloud training checkpoint.")
    checkpoint_model = ModelConfig.from_dict(checkpoint["model_config"])
    if checkpoint["architecture"] != checkpoint_model.architecture:
        raise ValueError("This checkpoint uses different network semantics. Start a new run "
                         "for the current Cloud architecture; weights cannot silently change meaning.")
    if checkpoint.get("resume_safe", True) is not True:
        raise ValueError("This checkpoint cannot be resumed because its optimizer update did not complete. "
                         "Some parameters may have changed before the failure. Resume an earlier intact "
                         "checkpoint, or start a new run from an earlier saved .cloud model.")
    return checkpoint


def _resume_compatibility(checkpoint: dict, config: TrainingConfig, fingerprint: str) -> None:
    if checkpoint["dataset_fingerprint"] != fingerprint:
        raise ValueError("Dataset, labels, ordering, or splits changed since this checkpoint. "
                         "Start a new training run to avoid silently changing the resumed experiment.")
    changeable = {"epochs", "device", "resume_path", "init_model_path", "cpu_threads", "patience", "val_every"}
    # Every newly introduced control defaults to the behavior of 3.1.x. Missing
    # legacy keys are migrated explicitly; unknown stored keys are not ignored.
    defaults = TrainingConfig().to_dict()
    unknown = set(checkpoint["config"]) - defaults.keys()
    if unknown:
        raise ValueError("Unsupported resume configuration field(s): " + ", ".join(sorted(unknown)))
    old = {**defaults, **checkpoint["config"]}
    new = config.to_dict()
    mismatches = [key for key in new if key not in changeable and old.get(key) != new[key]]
    if mismatches:
        raise ValueError("Resume configuration mismatch: " + ", ".join(mismatches) +
                         ". Keep these settings identical or start a new run.")


def _load_initial_model(path: str | Path, requested: ModelConfig) -> tuple[dict, dict]:
    """Load only validated inference weights; never a pickle or training state."""
    from .format import load_cloud

    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size > 130 * 1024 * 1024:
        raise ValueError("The initial .cloud model is missing or exceeds the native format size limit.")
    # Hash the same stable input that supplies the weights. If an external
    # application edits it during loading, do not record misleading provenance.
    def digest() -> str:
        with source.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    before = digest()
    parent_config, weights, parent_metadata = load_cloud(source)
    if digest() != before:
        raise ValueError("The initial model changed while it was being loaded. Try again after the file is stable.")
    if parent_config.architecture != requested.architecture:
        raise ValueError(f"Initial model architecture {parent_config.architecture} differs from "
            f"the selected {requested.architecture}. Improve keeps the source architecture; "
            "switching from a legacy polygon model to S2 requires training from scratch.")
    if parent_config != requested:
        changed = [name for name, value in parent_config.to_dict().items()
                   if getattr(requested, name) != value]
        raise ValueError("Initial model configuration mismatch: " + ", ".join(changed) +
                         ". Use the source model's input size and active architecture capacities "
                         "when improving it; changed capacities require training from scratch.")
    parent_training = parent_metadata.get("training", {})
    # Lean deployment models omit training provenance. A sibling run.json can
    # supply it only when its recorded model SHA-256 matches these exact bytes.
    sidecar = source.parent / "run.json"
    if sidecar.is_file() and sidecar.stat().st_size <= 10 * 1024 * 1024:
        try:
            run = json.loads(sidecar.read_text(encoding="utf-8"))
            if (isinstance(run, dict) and isinstance(run.get("models"), dict) and
                    isinstance(run["models"].get(source.name), dict) and
                    run["models"][source.name].get("sha256") == before and
                    isinstance(run.get("training"), dict)):
                parent_training = run["training"]
        except (OSError, ValueError, TypeError):
            pass  # Optional provenance cannot prevent validated weight loading.
    parent = {"filename": source.name, "sha256": before, "architecture": parent_config.architecture,
              "model_config": parent_config.to_dict()}
    if isinstance(parent_training, dict):
        for key in ("run_id", "dataset_fingerprint"):
            if isinstance(parent_training.get(key), str):
                parent[key] = parent_training[key]
    initialization = {"mode": "warm_start", "parent_model": parent,
                      "optimizer_restored": False, "schedule_restarted": True}
    return weights, initialization


def _prepare_output(output_dir: str | Path, project: Project, checkpoint: dict | None) -> Path:
    original = Path(output_dir).expanduser()
    if original.is_symlink():
        raise ValueError("The output directory must not be a symlink.")
    output = original.resolve()
    project_root = project.root.resolve()
    images_root = (project_root / "images").resolve()
    if output == project_root or output == images_root or images_root in output.parents:
        raise ValueError("Choose a separate run directory, such as project/runs/experiment-001; "
                         "the project root and image store cannot be output directories.")
    reserved = ("best.cloud", "last.cloud", "last.cloudtrain", "config.json", "metrics.json", "training.jsonl", "run.json")
    for name in reserved:
        path = output / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Unsafe output target: {path}")
    existing = any((output / name).exists() for name in reserved)
    if existing and checkpoint is None:
        raise FileExistsError("This run directory already contains training outputs. "
                              "Choose a new directory or explicitly resume its checkpoint.")
    if existing and checkpoint is not None:
        config_path = output / "config.json"
        try:
            saved = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Cannot verify this existing run directory; resume to a new directory.") from exc
        if saved.get("run_id") != checkpoint["run_id"]:
            raise ValueError("The output directory belongs to a different training run.")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _resolve_batch_size(model: CloudNet, dataset: _DetectionDataset, config: TrainingConfig,
                        device: torch.device, amp_enabled: bool, amp_dtype: torch.dtype,
                        checkpoint: dict | None, should_stop=None) -> tuple[int, dict]:
    """Resolve Auto once and record it; resuming never changes a run's batches."""
    if checkpoint and isinstance(checkpoint.get("batch_sizing"), dict):
        details = copy.deepcopy(checkpoint["batch_sizing"])
        resolved = details.get("resolved_batch_size")
        if type(resolved) is not int or resolved < 1:
            raise ValueError("The checkpoint has an invalid resolved batch size.")
        return resolved, details
    if config.batch_size != -1:
        return config.batch_size, {"mode": "explicit", "requested_batch_size": config.batch_size,
                                   "resolved_batch_size": config.batch_size}
    if device.type == "cpu":
        resolved = min(4, len(dataset))
        return resolved, {"mode": "conservative_cpu_fallback", "requested_batch_size": -1,
            "resolved_batch_size": resolved,
            "note": "CPU Auto uses at most four images per batch; it does not measure free RAM. "
                    "Use an explicit batch size to tune CPU memory and throughput."}
    # Probe a detached copy with the most crowded training image. Include a
    # backward/AdamW step so ROI gradients and optimizer moments consume memory.
    # RNG and the real model remain untouched; an OOM reduces the candidate.
    rng = _rng_state()
    probe, probe_optimizer = None, None
    attempts, resolved = [], 0
    free_bytes, _ = torch.cuda.mem_get_info(device)
    budget = int(free_bytes * 0.60)
    baseline = torch.cuda.memory_allocated(device)
    try:
        probe = copy.deepcopy(model)
        probe.train()
        probe_optimizer = _make_optimizer(probe, config)
        index = max(range(len(dataset)), key=lambda i: len(dataset.targets[i]["boxes"]))
        pixels, target = dataset[index]
        maximum = min(len(dataset), 64)
        candidates = []
        candidate = 1
        while candidate < maximum:
            candidates.append(candidate)
            candidate *= 2
        candidates.append(maximum)
        for candidate in candidates:
            if should_stop is not None and should_stop():
                return max(resolved, 1), {"cancelled": True, "mode": "cuda_probe", "attempts": attempts,
                                         "resolved_batch_size": max(resolved, 1), "requested_batch_size": -1}
            images = loss = parts = None
            try:
                probe_optimizer.zero_grad(set_to_none=True)
                torch.cuda.reset_peak_memory_stats(device)
                images = pixels.unsqueeze(0).repeat(candidate, 1, 1, 1).to(device)
                with torch.autocast(device_type="cuda", dtype=amp_dtype, enabled=amp_enabled):
                    loss, parts = _batch_losses(probe, images, [target] * candidate, config, 0)
                loss.backward()
                probe_optimizer.step()
                torch.cuda.synchronize(device)
                peak = int(torch.cuda.max_memory_allocated(device) - baseline)
                accepted = peak <= budget
                attempts.append({"batch_size": candidate, "peak_extra_allocated_bytes": peak,
                                 "within_60_percent_free_memory_budget": accepted})
                if not accepted:
                    break
                resolved = candidate
            except torch.cuda.OutOfMemoryError:
                attempts.append({"batch_size": candidate, "out_of_memory": True})
                break
            finally:
                del images, loss, parts
                probe_optimizer.zero_grad(set_to_none=True)
                torch.cuda.empty_cache()
    except torch.cuda.OutOfMemoryError as exc:
        raise ValueError("CUDA Auto batch cannot fit its one-image training probe. Close other GPU "
                         "tasks, lower input size/channels, or try an explicit batch size of 1.") from exc
    finally:
        del probe_optimizer, probe
        torch.cuda.empty_cache()
        _restore_rng(rng)
    if not resolved:
        raise ValueError("CUDA Auto batch could not fit one image within its conservative memory "
                         "budget. Lower input size/channels or explicitly choose batch size 1.")
    return resolved, {"mode": "cuda_training_probe", "requested_batch_size": -1,
        "resolved_batch_size": resolved, "initial_free_bytes": int(free_bytes),
        "memory_budget_bytes": budget, "attempts": attempts,
        "note": "Measured forward/backward/AdamW on a copy with the most crowded image; "
                "uses at most 60% of initially free CUDA memory and caps the batch at 64. "
                "Other GPU applications can still change available memory during training."}


def train(project_path: str | Path, output_dir: str | Path, config: TrainingConfig,
          progress: Callable[[dict], None] | None = None,
          should_stop: Callable[[], bool] | None = None) -> dict:
    """Run supervised training with explicitly scoped deterministic controls."""
    validated = TrainingConfig(**config.to_dict())
    with training_numerics(validated):
        return _train(project_path, output_dir, validated, progress, should_stop)


def _train(project_path: str | Path, output_dir: str | Path, config: TrainingConfig,
           progress: Callable[[dict], None] | None = None,
           should_stop: Callable[[], bool] | None = None) -> dict:
    """Train both supervised layers and save a reviewable, resumable experiment.

    Cancellation is checked during preflight, before every batch, and between
    validation images. Interrupted epochs retain their permutation, batch cursor,
    accumulated losses, optimizer, AMP scaler, and RNG in the checkpoint.
    """
    from .evaluation import evaluate_with_predictor
    from .format import save_cloud
    from .runtime import CloudRuntime

    config = TrainingConfig(**config.to_dict())
    device = _device(config.device)
    torch.set_num_threads(config.cpu_threads)
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(config.seed)
    project = Project.open(project_path)
    model_config = _model_config(config)
    train_records, val_records = project.training_records("train"), project.training_records("val")
    if not val_records and not config.allow_no_validation:
        raise ValueError("At least one reviewed validation image is required. Assign a separate "
                         "capture group to val and review its labels. allow_no_validation=True "
                         "is only for an explicitly unvalidated smoke experiment.")
    train_set = _DetectionDataset(project, train_records, model_config, should_stop,
                                  augmentation=config.augmentation, seed=config.seed,
                                  training_config=config)
    if train_set.cancelled:
        return {"status": "stopped", "epochs_completed": 0, "reason": "Stopped during dataset preflight.",
                "last_cloud": None, "best_cloud": None, "checkpoint": None}
    val_set = _DetectionDataset(project, val_records, model_config, should_stop,
                                require_representable_labels=False,
                                mask_target_samples=config.mask_target_samples)
    if val_set.cancelled:
        return {"status": "stopped", "epochs_completed": 0, "reason": "Stopped during dataset preflight.",
                "last_cloud": None, "best_cloud": None, "checkpoint": None}
    warnings = _check_splits(train_set, val_set)
    if project.manifest.get("synthetic") is True:
        warnings.append("SYNTHETIC DATASET: this run trains on a procedural software fixture. "
                        "Its scores do not measure recognition of players in real games.")
    if not val_records:
        warnings.append("Validation was explicitly disabled. Best model selection uses training "
                        "loss only and provides no held-out quality evidence.")
    if config.epochs <= config.warmup_epochs:
        warnings.append("This run ends during teacher-box warmup. The wrapper will not yet have "
                        "trained on predicted guides; validation still uses predicted boxes.")
    if device.type == "cuda":
        warnings.append("Strict deterministic algorithms are requested for this CUDA run; "
                        "results are still not guaranteed across devices or PyTorch versions."
                        if config.deterministic else
                        "CUDA floating-point operations may be nondeterministic; saved RNG state "
                        "does not guarantee bit-for-bit results across devices or PyTorch versions.")
    if config.lr_warmup_epochs and config.epochs <= config.lr_warmup_epochs:
        warnings.append("This run ends during learning-rate warmup. Increase epochs to exercise "
                        "the later constant or cosine schedule; teacher-box warmup is a separate setting.")
    fingerprint = _data_fingerprint([train_set, val_set])
    checkpoint = _load_checkpoint(config.resume_path) if config.resume_path else None
    initial_weights = None
    initialization = {"mode": "from_scratch", "parent_model": None,
                      "optimizer_restored": False, "schedule_restarted": True}
    if checkpoint is not None:
        _resume_compatibility(checkpoint, config, fingerprint)
        if checkpoint["model_config"] != model_config.to_dict():
            raise ValueError("Checkpoint architecture does not match the requested model.")
        initialization = copy.deepcopy(checkpoint.get("initialization", initialization))
        if checkpoint.get("resolved_device") != str(device):
            warnings.append("The resumed run uses a different device; optimizer and sampler state "
                            "are restored, but exact numerical continuation is not guaranteed.")
    elif config.init_model_path:
        initial_weights, initialization = _load_initial_model(config.init_model_path, model_config)
        warnings.append("This is a new run initialized from the selected .cloud weights. "
                        "It uses all currently reviewed train images, including earlier examples "
                        "that remain in the project; parent optimizer and history are not resumed.")
    output = _prepare_output(output_dir, project, checkpoint)
    run_id = checkpoint["run_id"] if checkpoint else uuid4().hex
    model = CloudNet(model_config).to(device)
    if config.deterministic and device.type == "cuda":
        from .training_ops import roi_bilinear_deterministic, resize_bilinear_deterministic
        model.training_roi_sampler = roi_bilinear_deterministic
        if model_config.is_mask:
            model.training_mask_resizer = resize_bilinear_deterministic
    if initial_weights is not None:
        model.load_state_dict({name: torch.from_numpy(value) for name, value in initial_weights.items()},
                              strict=True)
        del initial_weights
    optimizer = _make_optimizer(model, config)
    amp_enabled = config.amp and device.type == "cuda"
    amp_dtype = torch.bfloat16 if amp_enabled and torch.cuda.is_bf16_supported() else torch.float16
    scaler = torch.amp.GradScaler("cuda", enabled=amp_enabled and amp_dtype == torch.float16)
    batch_size, batch_sizing = _resolve_batch_size(model, train_set, config, device, amp_enabled,
                                                  amp_dtype, checkpoint, should_stop)
    if batch_sizing.get("cancelled"):
        return {"status": "stopped", "epochs_completed": 0, "reason": "Stopped during automatic batch sizing.",
                "last_cloud": None, "best_cloud": None, "checkpoint": None}
    if config.batch_size == -1:
        warnings.append(f"Auto batch resolved to {batch_size}. " + batch_sizing.get("note", ""))
    accumulation_steps = max(1, math.ceil(config.nominal_batch_size / batch_size))
    window_capacity = batch_size * accumulation_steps
    updates_per_epoch = math.ceil(len(train_set) / window_capacity)
    state = {"next_epoch": 0, "batch_offset": 0, "order": None, "running": {}, "running_samples": 0,
             "steps": 0, "updates_seen": 0, "best_score": None, "best_loss": None,
             "best_epoch": None, "bad_epochs": 0, "accumulation_samples": 0,
             "window_end": 0, "window_positives": 0, "window_start_state": None,
             "schedule_total_updates": updates_per_epoch * config.epochs,
             "schedule_updates_per_epoch": updates_per_epoch}
    history, best_state, latest_evaluation = [], None, None
    if checkpoint:
        model.load_state_dict(checkpoint["model"], strict=True)
        optimizer.load_state_dict(checkpoint["optimizer"])
        if scaler.is_enabled() and checkpoint["scaler"]:
            scaler.load_state_dict(checkpoint["scaler"])
        elif scaler.is_enabled():
            warnings.append("The checkpoint has no FP16 scaler state from its earlier execution "
                            "device or precision; the AMP scaler starts fresh on this device.")
        state = {**state, **checkpoint["state"]}
        state["updates_seen"] = checkpoint["state"].get("updates_seen", state["steps"])
        history, best_state = checkpoint["history"], checkpoint["best_model"]
        latest_evaluation = checkpoint.get("latest_evaluation")
        if state["next_epoch"] > config.epochs:
            raise ValueError("epochs is the total target count and is less than the checkpoint's completed count.")
        if (config.epochs != checkpoint["config"]["epochs"] and
                (config.scheduler != "constant" or config.lr_warmup_epochs)):
            warnings.append("Resuming with a different epoch target keeps the checkpoint's original "
                            "learning-rate schedule horizon. Extra epochs hold its final learning rate.")
        gradients = checkpoint.get("gradients", {})
        if state["accumulation_samples"]:
            if not isinstance(gradients, dict) or not gradients:
                raise ValueError("The checkpoint is missing its unfinished accumulation gradients.")
            named = dict(model.named_parameters())
            if set(gradients) - named.keys():
                raise ValueError("The checkpoint has gradients for unknown model parameters.")
            old_scale = float(checkpoint["scaler"].get("scale", 1.0)) if checkpoint["scaler"] else 1.0
            new_scale = float(scaler.get_scale()) if scaler.is_enabled() else 1.0
            for name, gradient in gradients.items():
                parameter = named[name]
                if not isinstance(gradient, Tensor) or gradient.shape != parameter.shape:
                    raise ValueError(f"Checkpoint gradient shape mismatch for {name}.")
                parameter.grad = gradient.to(device=device, dtype=parameter.dtype).clone() * (new_scale / old_scale)
        _restore_rng(checkpoint["rng"])
    _atomic_json(output / "config.json", {"run_id": run_id, "training_config": config.to_dict(),
                 "model_config": model_config.to_dict(), "dataset_fingerprint": fingerprint,
                 "resolved_device": str(device), "amp_enabled": amp_enabled,
                 "amp_dtype": str(amp_dtype) if amp_enabled else None,
                 "batch_sizing": batch_sizing, "accumulation_steps": accumulation_steps,
                 "effective_batch_size": window_capacity,
                 "schedule_total_updates": state["schedule_total_updates"],
                 "schedule_updates_per_epoch": state["schedule_updates_per_epoch"],
                 "train_images": len(train_set), "val_images": len(val_set),
                 "initialization": initialization, "warnings": warnings})

    def emit(value: dict) -> None:
        event = {"time_utc": datetime.now(timezone.utc).isoformat(), **value}
        encoded = json.dumps(event, allow_nan=False)
        with (output / "training.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
        if progress is not None:
            progress(event)

    model_artifacts = {}
    if checkpoint:
        for name, entry in checkpoint.get("model_artifacts", {}).items():
            if not isinstance(name, str) or Path(name).name != name or not isinstance(entry, dict):
                continue
            candidate = output / name
            if candidate.is_file() and candidate.stat().st_size <= 130 * 1024 * 1024:
                with candidate.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if entry.get("sha256") == digest:
                    model_artifacts[name] = copy.deepcopy(entry)

    def metadata() -> dict:
        result = {"project_name": project.manifest.get("name", "Untitled"),
                "synthetic": project.manifest.get("synthetic") is True,
                "training": {"run_id": run_id, "epochs_completed": state["next_epoch"],
                             "optimizer_steps": state["steps"], "dataset_fingerprint": fingerprint,
                             "optimizer_updates_attempted": state["updates_seen"],
                             "train_images": len(train_set), "val_images": len(val_set),
                             "supervision": ["independent_main_box", "linked_encasing_polygon"],
                             "initialization": initialization,
                             "augmentation": config.augmentation,
                             "training_config": {key: value for key, value in config.to_dict().items()
                                                 if key not in {"resume_path", "init_model_path"}},
                             "best_validation_score": state["best_score"] if val_records else None},
                "models": model_artifacts,
                "research_note": "Original small research architecture; quality and speed are unproven "
                                 "outside the explicitly recorded evaluation."}
        if isinstance(project.manifest.get("purpose"), str):
            result["purpose"] = project.manifest["purpose"]
        return result

    def save_deployment(name: str, source_model: CloudNet, *, selected_epoch=None) -> None:
        path = output / name
        save_cloud(path, source_model,
                   metadata={"synthetic": project.manifest.get("synthetic") is True}, deployment_only=True)
        with path.open("rb") as stream:
            sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        model_artifacts[name] = {"sha256": sha256, "bytes": path.stat().st_size,
                                 "epoch": state["next_epoch"] if selected_epoch is None else selected_epoch}

    def save_checkpoint() -> None:
        _atomic_checkpoint(output / "last.cloudtrain", {
            "checkpoint_version": 3, "architecture": model_config.architecture,
            "resume_safe": not optimizer_in_progress,
            "run_id": run_id, "config": config.to_dict(),
            "model_config": model_config.to_dict(), "model": model.state_dict(),
            "optimizer": optimizer.state_dict(), "scaler": scaler.state_dict(),
            "rng": _rng_state(), "state": state, "dataset_fingerprint": fingerprint,
            "best_model": best_state, "history": history,
            "latest_evaluation": latest_evaluation, "resolved_device": str(device),
            "initialization": initialization,
            "gradients": {name: value.grad.detach().cpu().clone() for name, value in model.named_parameters()
                          if value.grad is not None} if state["accumulation_samples"] else {},
            "batch_sizing": batch_sizing, "model_artifacts": model_artifacts,
            "torch_version": str(torch.__version__),
        })
        _atomic_json(output / "run.json", metadata())

    def save_last() -> None:
        save_deployment("last.cloud", model)

    def save_best_snapshot() -> None:
        if best_state is not None:
            snapshot = copy.deepcopy(model).cpu()
            snapshot.load_state_dict(best_state)
            save_deployment("best.cloud", snapshot, selected_epoch=state["best_epoch"])

    status = "completed"
    selection_name = (("emitted_box_f1_at_50_times_matched_mask_iou" if model_config.is_mask else
                       "emitted_box_f1_at_50_times_matched_wrapper_iou") if val_records else "negative_training_loss")
    emit({"event": "start", "epoch": state["next_epoch"], "epochs": config.epochs,
          "device": str(device), "resumed": checkpoint is not None, "train_images": len(train_set),
          "val_images": len(val_set), "initialization": initialization,
          "batch_sizing": batch_sizing, "resolved_batch_size": batch_size,
          "accumulation_steps": accumulation_steps, "effective_batch_size": window_capacity,
          "scheduler": config.scheduler, "deterministic": config.deterministic,
          "augmentation": config.augmentation, "warnings": warnings})
    # A resumed run may use a new output directory; retain the historical best.
    if best_state is not None and not (output / "best.cloud").exists():
        save_best_snapshot()
    batch_in_progress = False
    optimizer_in_progress = False
    try:
        while state["next_epoch"] < config.epochs:
            epoch_index = state["next_epoch"]
            train_set.epoch = epoch_index
            if state["order"] is None:
                state["order"] = torch.randperm(len(train_set)).tolist()
                state["batch_offset"] = 0
                state["running"], state["running_samples"] = {}, 0
            order = state["order"]
            model.train()
            for offset in range(state["batch_offset"], len(order), batch_size):
                if should_stop is not None and should_stop():
                    status = "stopped"
                    break
                if not state["accumulation_samples"]:
                    optimizer.zero_grad(set_to_none=True)
                    state["window_end"] = min(len(order), offset + window_capacity)
                    state["window_positives"] = sum(len(train_set.targets[index]["boxes"])
                        for index in order[offset:state["window_end"]])
                    state["window_start_state"] = {"offset": offset, "rng": _rng_state(),
                        "running": copy.deepcopy(state["running"]), "running_samples": state["running_samples"]}
                    learning_rate = _learning_rate_for_update(config, state["updates_seen"],
                        state["schedule_total_updates"], state["schedule_updates_per_epoch"])
                    for group in optimizer.param_groups:
                        group["lr"] = learning_rate
                batch_in_progress = True
                items = [train_set[index] for index in order[offset:offset + batch_size]]
                images = torch.stack([item[0] for item in items]).to(device)
                targets = [item[1] for item in items]
                with torch.autocast(device_type=device.type, dtype=amp_dtype, enabled=amp_enabled):
                    loss, parts = _batch_losses(model, images, targets, config, epoch_index)
                if not bool(torch.isfinite(loss).item()):
                    raise FloatingPointError("Nonfinite training loss; inspect labels or lower learning_rate.")
                positives = sum(len(target["boxes"]) for target in targets)
                scaled_loss = _window_loss(parts, config, positives, state["window_positives"],
                    images=len(items), window_images=(state["window_end"] - state["window_start_state"]["offset"]))
                scaler.scale(scaled_loss).backward()
                values = {key: float(value.detach().cpu()) for key, value in parts.items()}
                count = len(items)
                state["accumulation_samples"] += count
                stepped, skipped = False, False
                if offset + count >= state["window_end"]:
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), config.gradient_clip,
                                                   error_if_nonfinite=not scaler.is_enabled())
                    old_scale = scaler.get_scale()
                    optimizer_in_progress = True
                    scaler.step(optimizer)
                    scaler.update()
                    optimizer_in_progress = False
                    skipped = scaler.is_enabled() and scaler.get_scale() < old_scale
                    stepped = not skipped
                    state["updates_seen"] += 1
                    state["steps"] += int(stepped)
                    state["accumulation_samples"], state["window_end"], state["window_positives"] = 0, 0, 0
                    state["window_start_state"] = None
                    optimizer.zero_grad(set_to_none=True)
                for key, value in values.items():
                    state["running"][key] = state["running"].get(key, 0.0) + value * count
                state["running_samples"] += count
                state["batch_offset"] = offset + count
                batch_in_progress = False
                emit({"event": "batch", "epoch": epoch_index + 1, "epochs": config.epochs,
                      "batch": offset // batch_size + 1,
                      "batches": math.ceil(len(order) / batch_size), "step": state["steps"],
                      "optimizer_step": stepped, "amp_update_skipped": skipped,
                      "pending_accumulation_samples": state["accumulation_samples"],
                      "learning_rate": optimizer.param_groups[0]["lr"],
                      "predicted_guide_fraction": _guidance_fraction(epoch_index, config), **values})
            if status == "stopped":
                break
            values = {key: value / max(state["running_samples"], 1)
                      for key, value in state["running"].items()}
            state["next_epoch"] = epoch_index + 1
            state["order"], state["batch_offset"] = None, 0
            state["running"], state["running_samples"] = {}, 0
            save_last()
            if config.save_period and (epoch_index + 1) % config.save_period == 0:
                save_deployment(f"epoch_{epoch_index + 1:04d}.cloud", model)
            validate_now = bool(val_records) and (
                (epoch_index + 1) % config.val_every == 0 or epoch_index == 0 or
                epoch_index + 1 == config.epochs)
            score = None
            epoch_evaluation = None
            if validate_now:
                emit({"event": "validation_start", "epoch": epoch_index + 1, "epochs": config.epochs,
                      **values})
                # Runtime model construction must not perturb training shuffles
                # or guide jitter when validation frequency changes on resume.
                with torch.random.fork_rng(devices=[]):
                    runtime = CloudRuntime(output / "last.cloud", backend="torch", device=str(device))
                    epoch_evaluation = evaluate_with_predictor(
                        project, val_records,
                        lambda image: runtime.predict(image, confidence=config.validation_confidence,
                            max_detections=config.validation_max_detections, debug=True),
                        split="val", confidence=config.validation_confidence, should_stop=should_stop)
                    del runtime
                latest_evaluation = epoch_evaluation
                if epoch_evaluation["cancelled"]:
                    status = "stopped"
                elif epoch_evaluation["ground_truth_instances"]:
                    score = ((epoch_evaluation["emitted_box_f1_at_50"] or 0.0) *
                             (epoch_evaluation["mean_wrapper_iou_on_box_matches"] or 0.0))
                else:
                    score = -float(epoch_evaluation["false_positives_per_frame"] or 0.0)
                    selection_name = "negative_false_positives_per_frame_on_negative_only_validation"
            elif not val_records:
                score = -values["loss"]
            improved = False
            if score is not None:
                improved = state["best_score"] is None or score > state["best_score"] + 1e-9
                tie_with_lower_loss = (state["best_score"] is not None and abs(score - state["best_score"]) <= 1e-9
                                       and values["loss"] < state["best_loss"])
                if improved or tie_with_lower_loss:
                    state["best_score"], state["best_loss"], state["best_epoch"] = score, values["loss"], epoch_index + 1
                    best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
                    save_deployment("best.cloud", model, selected_epoch=epoch_index + 1)
                state["bad_epochs"] = 0 if improved else state["bad_epochs"] + 1
            epoch_row = {"epoch": epoch_index + 1, **values,
                         "learning_rate": optimizer.param_groups[0]["lr"], "optimizer_steps": state["steps"],
                         "predicted_guide_fraction": _guidance_fraction(epoch_index, config),
                         "selection_score": score, "selection_metric": selection_name,
                         "validation_improved": improved}
            if epoch_evaluation is not None:
                epoch_row["evaluation"] = {key: value for key, value in epoch_evaluation.items() if key != "per_image"}
            history.append(epoch_row)
            save_checkpoint()
            emit({"event": "epoch", "epochs": config.epochs, **epoch_row})
            if status == "stopped" or (should_stop is not None and should_stop()):
                status = "stopped"
                break
            if score is not None and config.patience and state["bad_epochs"] >= config.patience:
                status = "early_stopped"
                break
    except Exception as exc:
        # A failed forward/backward may have partially modified pending
        # gradients. Rewind that entire unfinished window and its RNG; model
        # weights were unchanged until its optimizer update.
        if batch_in_progress and not optimizer_in_progress and state["window_start_state"]:
            previous = state["window_start_state"]
            state["batch_offset"] = previous["offset"]
            state["running"], state["running_samples"] = previous["running"], previous["running_samples"]
            _restore_rng(previous["rng"])
            state["accumulation_samples"], state["window_end"], state["window_positives"] = 0, 0, 0
            state["window_start_state"] = None
            optimizer.zero_grad(set_to_none=True)
        save_last()
        save_checkpoint()
        _atomic_json(output / "metrics.json", {"status": "failed", "error": str(exc),
                     "epochs_completed": state["next_epoch"], "steps": state["steps"],
                     "checkpoint_resume_safe": not optimizer_in_progress,
                     "warnings": warnings, "history": history, "evaluation": latest_evaluation})
        raise
    save_last()
    save_checkpoint()
    summary = {"status": status, "run_id": run_id, "epochs_completed": state["next_epoch"],
               "initialization": initialization, "augmentation": config.augmentation,
               "partial_epoch_samples": state["batch_offset"], "steps": state["steps"],
               "pending_accumulation_samples": state["accumulation_samples"],
               "optimizer_updates_attempted": state["updates_seen"],
               "resolved_batch_size": batch_size, "batch_sizing": batch_sizing,
               "accumulation_steps": accumulation_steps, "effective_batch_size": window_capacity,
               "best_epoch": state["best_epoch"], "best_score": state["best_score"],
               "selection_metric": selection_name,
               "selection_tie_break": "Lower training loss selects among equal validation scores; "
                                      "ties do not reset validation patience.",
               "last_cloud": str(output / "last.cloud"),
               "best_cloud": str(output / "best.cloud") if best_state is not None else None,
               "checkpoint": str(output / "last.cloudtrain"), "metrics_path": str(output / "metrics.json"),
               "log_path": str(output / "training.jsonl"), "warnings": warnings,
               "run_metadata_path": str(output / "run.json"),
               "evaluation": latest_evaluation, "history": history}
    _atomic_json(output / "metrics.json", summary)
    emit({"event": "finish", "epoch": state["next_epoch"], "epochs": config.epochs,
          "status": status, "steps": state["steps"], "best_epoch": state["best_epoch"],
          "best_score": state["best_score"], "last_cloud": summary["last_cloud"],
          "best_cloud": summary["best_cloud"]})
    return summary
