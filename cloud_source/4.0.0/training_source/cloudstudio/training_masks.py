"""S2 silhouette objectives, including explicit background-ROI supervision.

Legacy polygon training never calls this module. A bounding rectangle is not
treated as a foreground mask: source annotation pixels supply the labels.
"""
from __future__ import annotations

import numpy as np
import torch
from torch import Tensor
from torch.nn import functional as F

from .training_targets import sample_mask_targets


def mask_loss_parts(logits: Tensor, targets: Tensor) -> dict[str, Tensor]:
    """Per-instance BCE, soft Dice and boundary BCE at the mask output grid.

    Boundary emphasis covers both sides of exterior and interior boundaries.
    BCE still supervises every output cell, including the interior background
    of holes. No smoothness penalty asks the network to fill holes or gaps.
    """
    if logits.shape != targets.shape or logits.ndim != 4 or logits.shape[1] != 1:
        raise ValueError("Mask logits and supervision must have matching N,1,H,W shapes.")
    prediction, target = logits.float(), targets.float()
    if not len(logits):
        empty = prediction.reshape(0, -1).sum(dim=1) if prediction.numel() else prediction.sum().expand(0)
        return {name: empty for name in ("bce", "dice", "boundary")}
    bce_map = F.binary_cross_entropy_with_logits(prediction, target, reduction="none")
    probabilities = prediction.sigmoid()
    intersection = (probabilities * target).flatten(1).sum(dim=1)
    dice = 1 - (2 * intersection + 1) / (
        probabilities.flatten(1).sum(dim=1) + target.flatten(1).sum(dim=1) + 1)
    # Pad explicitly with background so an exterior edge at the ROI border
    # receives the same treatment as an edge in its interior.
    binary = (target >= .5).float()
    padded = F.pad(binary, (1, 1, 1, 1), value=0)
    dilation = F.max_pool2d(padded, kernel_size=3, stride=1)
    erosion = -F.max_pool2d(-padded, kernel_size=3, stride=1)
    boundary = (dilation - erosion).clamp(0, 1)
    boundary_loss = (bce_map * boundary).flatten(1).sum(dim=1) / boundary.flatten(1).sum(dim=1).clamp_min(1)
    return {"bce": bce_map.flatten(1).mean(dim=1), "dice": dice, "boundary": boundary_loss}


def _background_proposals(decoded: Tensor, logits: Tensor, targets: list[dict],
                          limit: int, input_size: int) -> tuple[Tensor, Tensor]:
    """Choose hard predicted boxes with zero intersection with every target box.

    A low IoU is not enough: a small player inside a large candidate must never
    become a false background example. Candidates must be strictly disjoint.
    Reviewed all-negative images can contribute anywhere. Selection is detached
    and stable, and missing safe proposals never fabricate negative labels.
    """
    device = decoded.device
    selected, batch_indices = [], []
    if not limit:
        return decoded.new_empty((0, 4)), torch.empty(0, dtype=torch.int64, device=device)
    with torch.no_grad():
        for batch, target in enumerate(targets):
            all_boxes = decoded[batch].reshape(-1, 4).detach()
            all_scores = logits[batch, 0].reshape(-1).float().detach()
            order = torch.argsort(all_scores, descending=True, stable=True)[:min(512, len(all_boxes))]
            candidates = all_boxes[order]
            sizes = candidates[:, 2:] - candidates[:, :2]
            valid = (sizes >= 1 / input_size).all(dim=1)
            truth = target["boxes"].to(device)
            if len(truth):
                overlap = (torch.minimum(candidates[:, None, 2:], truth[None, :, 2:]) -
                           torch.maximum(candidates[:, None, :2], truth[None, :, :2]))
                valid &= ~(overlap > 0).all(dim=2).any(dim=1)
            candidates = candidates[valid]
            chosen = []
            for candidate in candidates:
                if chosen:
                    previous = torch.stack(chosen)
                    intersection = (torch.minimum(previous[:, 2:], candidate[2:]) -
                                    torch.maximum(previous[:, :2], candidate[:2])).clamp_min(0).prod(dim=1)
                    union = ((previous[:, 2:] - previous[:, :2]).prod(dim=1) +
                             (candidate[2:] - candidate[:2]).prod() - intersection).clamp_min(1e-8)
                    if bool((intersection / union > .5).any()):
                        continue
                chosen.append(candidate)
                if len(chosen) >= limit:
                    break
            selected.extend(chosen)
            batch_indices.extend([batch] * len(chosen))
    return (torch.stack(selected) if selected else decoded.new_empty((0, 4)),
            torch.as_tensor(batch_indices, dtype=torch.int64, device=device))


def mask_batch_losses(model, images: Tensor, targets: list[dict], config,
                      epoch_index: int) -> tuple[Tensor, dict[str, Tensor]]:
    from .model import decode_boxes
    from .training import _aligned_giou, _guidance_fraction, _guiding_boxes

    features, logits, raw_boxes = model.detect(images)
    decoded = decode_boxes(raw_boxes.float(), model.config)
    presence = torch.zeros_like(logits)
    batches, rows, columns, instances, boxes = [], [], [], [], []
    for batch, target in enumerate(targets):
        count = len(target["boxes"])
        if not count:
            continue
        cells = target["cells"].to(images.device)
        presence[batch, 0, cells[:, 1], cells[:, 0]] = 1
        batches.append(torch.full((count,), batch, dtype=torch.int64, device=images.device))
        rows.append(cells[:, 1])
        columns.append(cells[:, 0])
        instances.append(torch.arange(count, dtype=torch.int64, device=images.device))
        boxes.append(target["boxes"].to(images.device))
    positives = sum(len(target["boxes"]) for target in targets)
    binary_loss = F.binary_cross_entropy_with_logits(logits.float(), presence.float(), reduction="none")
    probabilities = logits.float().sigmoid()
    pt = probabilities * presence + (1 - probabilities) * (1 - presence)
    alpha = config.focal_alpha * presence + (1 - config.focal_alpha) * (1 - presence)
    detection_loss = (alpha * (1 - pt).pow(config.focal_gamma) * binary_loss).sum() / max(positives, 1)
    zero = features.float().sum() * 0
    mask_bce, mask_dice, mask_boundary = zero, zero, zero
    if positives:
        batch_indices = torch.cat(batches)
        target_boxes = torch.cat(boxes)
        predicted_boxes = decoded[batch_indices, torch.cat(rows), torch.cat(columns)]
        box_loss = ((1 - _aligned_giou(predicted_boxes, target_boxes)).mean() +
                    F.l1_loss(predicted_boxes, target_boxes))
        guides = _guiding_boxes(target_boxes, predicted_boxes, _guidance_fraction(epoch_index, config))
        mask_logits = model.wrap(features, guides, batch_indices)
        sampled = sample_mask_targets(targets, guides.detach().float().cpu().numpy(),
            batch_indices.detach().cpu().numpy(), torch.cat(instances).cpu().numpy(),
            model.config.mask_size, samples=config.mask_target_samples)
        mask_targets = torch.from_numpy(sampled).to(images.device)
        parts = mask_loss_parts(mask_logits, mask_targets)
        mask_bce, mask_dice, mask_boundary = (parts[name].mean() for name in ("bce", "dice", "boundary"))
    else:
        box_loss = raw_boxes.float().sum() * 0
    mask_loss = (config.mask_bce_weight * mask_bce + config.mask_dice_weight * mask_dice +
                 config.mask_boundary_weight * mask_boundary)

    negative_boxes, negative_batches = _background_proposals(decoded, logits, targets,
        config.background_rois_per_image if config.negative_mask_weight else 0, config.input_size)
    background_loss = zero
    if len(negative_boxes):
        negative_logits = model.wrap(features, negative_boxes, negative_batches)
        negative_losses = F.softplus(negative_logits.float()).flatten(1).mean(dim=1)
        # Every image contributes equally; a crowded image with fewer safe
        # proposals cannot upweight the rest of the batch's negative examples.
        per_image = []
        for batch in range(len(targets)):
            selected = negative_losses[negative_batches == batch]
            per_image.append(selected.mean() if len(selected) else zero)
        background_loss = torch.stack(per_image).mean()
    total = (config.objectness_weight * detection_loss + config.box_weight * box_loss +
             config.contour_weight * mask_loss + config.negative_mask_weight * background_loss)
    return total, {"loss": total, "objectness_loss": detection_loss, "box_loss": box_loss,
        "contour_loss": mask_loss, "mask_bce_loss": mask_bce, "mask_dice_loss": mask_dice,
        "mask_boundary_loss": mask_boundary, "background_mask_loss": background_loss}
