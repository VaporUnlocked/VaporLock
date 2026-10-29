"""Honest, fixed-threshold evaluation of the complete box-to-wrapper pipeline.

The primary box is requested for diagnostics only. Ground-truth boxes are never
passed to the runtime or its wrapper head. Polygon and boundary scores are
conditional on box matches, so recall must be considered alongside them.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from .geometry import boundary_f_score, box_iou, polygon_iou, validate_annotation
from .masks import binary_mask_metrics, decode_mask, polygon_mask


def _local_mask(item: dict, width: int, height: int) -> tuple[int, int, np.ndarray]:
    """Decode only an instance's stored crop; ignore an approximate preview."""
    if item.get("mask") is not None:
        values = decode_mask(item["mask"], expected_size=(width, height), full=False)
        x, y = item["mask"]["origin"]
        return int(x), int(y), values
    points = np.asarray(item.get("wrapper", []), dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (2,) or len(points) < 3:
        raise ValueError("Mask evaluation requires an authoritative mask or valid legacy outline.")
    low = np.maximum(np.floor(points.min(axis=0)).astype(np.int64), 0)
    high = np.minimum(np.ceil(points.max(axis=0)).astype(np.int64), (width, height))
    if np.any(high <= low):
        return 0, 0, np.zeros((0, 0), dtype=bool)
    mask = polygon_mask(points - low, int(high[0] - low[0]), int(high[1] - low[1]))
    return int(low[0]), int(low[1]), mask


def _local_mask_iou(prediction, target) -> float:
    px, py, p = prediction
    tx, ty, t = target
    x1, y1 = max(px, tx), max(py, ty)
    x2, y2 = min(px + p.shape[1], tx + t.shape[1]), min(py + p.shape[0], ty + t.shape[0])
    intersection = 0
    if x2 > x1 and y2 > y1:
        intersection = int(np.count_nonzero(p[y1-py:y2-py, x1-px:x2-px] &
                                              t[y1-ty:y2-ty, x1-tx:x2-tx]))
    union = int(np.count_nonzero(p)) + int(np.count_nonzero(t)) - intersection
    return intersection / union if union else 1.0


def _local_mask_metrics(prediction, target) -> dict:
    px, py, p = prediction
    tx, ty, t = target
    x1, y1 = min(px, tx), min(py, ty)
    x2, y2 = max(px + p.shape[1], tx + t.shape[1]), max(py + p.shape[0], ty + t.shape[0])
    # Boundary distances are invariant to this common integer translation.
    # Allocate the union crop rather than two complete screenshot masks.
    shape = (max(1, y2-y1), max(1, x2-x1))
    predicted, expected = np.zeros(shape, dtype=bool), np.zeros(shape, dtype=bool)
    predicted[py-y1:py-y1+p.shape[0], px-x1:px-x1+p.shape[1]] = p
    expected[ty-y1:ty-y1+t.shape[0], tx-x1:tx-x1+t.shape[1]] = t
    return binary_mask_metrics(predicted, expected, boundary_tolerance=2)


def _split_warnings(project: Any, selected_split: str) -> list[str]:
    groups: dict[str, set[str]] = {}
    for record in project.manifest["images"]:
        if record.get("reviewed", False):
            group = str(record.get("group_id") or record["id"])
            groups.setdefault(group, set()).add(record["split"])
    leaked = sorted(group for group, splits in groups.items()
                    if selected_split in splits and len(splits) > 1)
    if not leaked:
        return []
    return ["Reviewed capture groups span multiple splits; evaluation may be "
            "optimistic. Reassign complete groups before trusting results. "
            f"Groups: {', '.join(leaked[:8])}" + (" …" if len(leaked) > 8 else "")]


def match_detections(
    annotations: list[dict], detections: list[dict], iou_threshold: float = 0.5,
) -> list[tuple[int, int, float]]:
    """Greedy, confidence-ordered, one-to-one matching of predicted boxes.

    Returns (prediction index, annotation index, IoU). A duplicate prediction is
    a false positive even if it overlaps an already matched target perfectly.
    """
    if not 0 < iou_threshold <= 1:
        raise ValueError("IoU threshold must be greater than 0 and at most 1.")
    order = sorted(range(len(detections)),
                   key=lambda i: (-float(detections[i]["confidence"]), i))
    unmatched = set(range(len(annotations)))
    matches = []
    for pred_index in order:
        prediction = detections[pred_index]
        if "primary_box" not in prediction:
            raise ValueError("Evaluation requires diagnostic primary boxes from "
                             "runtime.predict(debug=True).")
        best_index, best_iou = None, -1.0
        for target_index in sorted(unmatched):
            if int(prediction.get("class_id", 0)) != int(annotations[target_index]["class_id"]):
                continue
            overlap = float(box_iou(prediction["primary_box"],
                                    annotations[target_index]["box"]))
            if overlap > best_iou:
                best_index, best_iou = target_index, overlap
        if best_index is not None and best_iou >= iou_threshold:
            unmatched.remove(best_index)
            matches.append((pred_index, best_index, best_iou))
    return matches


def match_wrappers(annotations: list[dict], detections: list[dict], width: int, height: int,
                   iou_threshold: float = 0.5, overlap_cache: dict | None = None, *,
                   mask_mode: bool | None = None, mask_cache=None) -> list[tuple[int, int, float]]:
    """One-to-one encasing-IoU matching, independently of box-IoU matches.

    Legacy polygon-only calls keep their original metric. When masks are
    present, use source-pixel masks and never their convex preview polygons.
    """
    if not 0 < iou_threshold <= 1:
        raise ValueError("IoU threshold must be greater than 0 and at most 1.")
    if mask_mode is None:
        mask_mode = any(item.get("mask") is not None for item in annotations + detections)
    if mask_mode and mask_cache is None:
        mask_cache = ([_local_mask(item, width, height) for item in detections],
                      [_local_mask(item, width, height) for item in annotations])
    cache = overlap_cache if overlap_cache is not None else {}
    order = sorted(range(len(detections)), key=lambda i: (-float(detections[i]["confidence"]), i))
    unmatched, matches = set(range(len(annotations))), []
    for pred_index in order:
        prediction = detections[pred_index]
        best_index, best_overlap = None, -1.0
        for target_index in sorted(unmatched):
            target = annotations[target_index]
            if int(prediction.get("class_id", 0)) != int(target["class_id"]):
                continue
            key = (pred_index, target_index)
            if key not in cache:
                if mask_mode:
                    cache[key] = _local_mask_iou(mask_cache[0][pred_index], mask_cache[1][target_index])
                # Both loops are enclosed by these boxes. Disjoint boxes cannot
                # have polygon overlap; avoid unnecessary raster allocations.
                else:
                    cache[key] = (0.0 if box_iou(prediction["primary_box"], target["box"]) <= 0
                                  else float(polygon_iou(prediction["wrapper"], target["wrapper"], width, height)))
            if cache[key] > best_overlap:
                best_index, best_overlap = target_index, cache[key]
        if best_index is not None and best_overlap >= iou_threshold:
            unmatched.remove(best_index)
            matches.append((pred_index, best_index, best_overlap))
    return matches


def _score_image(record: dict, result: dict, *, mask_mode: bool | None = None) -> dict:
    annotations = record["annotations"]
    detections = result["detections"]
    if mask_mode is None:
        mask_mode = ("empty_masks" in result or
                     any(item.get("mask") is not None for item in annotations + detections))
    masks = (([_local_mask(item, int(record["width"]), int(record["height"])) for item in detections],
              [_local_mask(item, int(record["width"]), int(record["height"])) for item in annotations])
             if mask_mode else None)
    matches = match_detections(annotations, detections)
    primary = result.get("diagnostics", {}).get("primary_detections")
    primary_matches = match_detections(annotations, primary) if primary is not None else None
    wrapper_ious, boundary_scores, box_ious = [], [], []
    overlap_cache = {}
    mask_metrics = []
    for pred_index, target_index, overlap in matches:
        if mask_mode:
            metrics = _local_mask_metrics(masks[0][pred_index], masks[1][target_index])
            mask_metrics.append(metrics)
            wrapper_iou = float(metrics["iou"])
            boundary_scores.append(float(metrics["boundary_f1"]))
        else:
            predicted = detections[pred_index]["wrapper"]
            target = annotations[target_index]["wrapper"]
            wrapper_iou = float(polygon_iou(predicted, target, int(record["width"]), int(record["height"])))
            boundary_scores.append(float(boundary_f_score(predicted, target, tolerance=2.0)))
        overlap = float(overlap)
        wrapper_ious.append(wrapper_iou)
        overlap_cache[(pred_index, target_index)] = wrapper_iou
        box_ious.append(float(overlap))
    wrapper_matches = match_wrappers(annotations, detections, int(record["width"]),
                                     int(record["height"]), overlap_cache=overlap_cache,
                                     mask_mode=mask_mode, mask_cache=masks)
    row = {
        "image_id": record["id"],
        "ground_truth_instances": len(annotations),
        "predictions": len(detections),
        "matched_instances": len(matches),
        "false_positives": len(detections) - len(matches),
        "false_negatives": len(annotations) - len(matches),
        "matched_box_ious": box_ious,
        "matched_wrapper_ious": wrapper_ious,
        "matched_boundary_f1": boundary_scores,
        "primary_predictions": len(primary) if primary is not None else None,
        "primary_matched_instances": len(primary_matches) if primary_matches is not None else None,
        "primary_matched_box_ious": [entry[2] for entry in primary_matches] if primary_matches is not None else [],
        "wrapper_matched_instances": len(wrapper_matches),
        "wrapper_matched_ious": [entry[2] for entry in wrapper_matches],
        "invalid_contours_suppressed": int(result.get("invalid_contours", 0)),
        "timing_ms": {key: float(value) for key, value in
                      result.get("timing_ms", {}).items()},
    }
    if mask_mode:
        row.update({"encasing_representation": "source_resolution_binary_mask",
                    "matched_mask_metrics": mask_metrics,
                    "empty_masks_suppressed": int(result.get("empty_masks", 0)),
                    "mask_pixels_on_negative_image":
                        sum(int(np.count_nonzero(item[2])) for item in masks[0]) if not annotations else 0})
    return row


def _mean_or_none(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _summarize(rows: list[dict], split: str, confidence: float,
               warnings: list[str] | None = None) -> dict:
    targets = sum(row["ground_truth_instances"] for row in rows)
    predictions = sum(row["predictions"] for row in rows)
    matched = sum(row["matched_instances"] for row in rows)
    primary_available = bool(rows) and all(row.get("primary_predictions") is not None for row in rows)
    primary_predictions = sum(row["primary_predictions"] for row in rows) if primary_available else None
    primary_matched = sum(row["primary_matched_instances"] for row in rows) if primary_available else None
    wrapper_matched = sum(row["wrapper_matched_instances"] for row in rows)
    precision = matched / predictions if predictions else None
    recall = matched / targets if targets else None
    # If there are targets but no predictions, F1 is zero (zero recall).
    # With no targets, F1 and recall have no positive-class denominator.
    f1 = 2 * matched / (predictions + targets) if targets else None
    empty = []
    if not rows:
        empty.append("No reviewed images in the requested split.")
    if not targets:
        empty.append("No ground-truth instances; positive-class recall and F1 are undefined.")
    if not predictions:
        empty.append("No predictions above the confidence threshold; precision is undefined.")
    if not matched:
        empty.append("No matched instances; matched wrapper, boundary, and box scores are undefined.")
    if not primary_available:
        empty.append("Independent primary-box diagnostics are unavailable for this evaluation.")
    if not wrapper_matched:
        empty.append("No wrapper matches at polygon IoU 0.5.")
    timing = {}
    for phase in ("preprocess", "inference", "postprocess", "total"):
        samples = [row["timing_ms"][phase] for row in rows
                   if phase in row["timing_ms"] and
                   math.isfinite(row["timing_ms"][phase])]
        timing[phase] = ({"mean": float(np.mean(samples)),
                          "median": float(np.median(samples)),
                          "p95": float(np.percentile(samples, 95)),
                          "samples": len(samples)} if samples else
                         {"mean": None, "median": None, "p95": None, "samples": 0})
    summary = {
        "schema_version": 1,
        "split": split,
        "confidence_threshold": float(confidence),
        "box_iou_threshold": 0.5,
        "wrapper_iou_threshold": 0.5,
        "boundary_tolerance_pixels": 2.0,
        "images_evaluated": len(rows),
        "ground_truth_instances": targets,
        "predictions": predictions,
        "matched_instances": matched,
        "false_positives": predictions - matched,
        "false_negatives": targets - matched,
        "box_precision_at_50": precision,
        "box_recall_at_50": recall,
        "box_f1_at_50": f1,
        "emitted_box_precision_at_50": precision,
        "emitted_box_recall_at_50": recall,
        "emitted_box_f1_at_50": f1,
        "primary_diagnostics_available": primary_available,
        "primary_predictions": primary_predictions,
        "primary_matched_instances": primary_matched,
        "primary_box_precision_at_50": (primary_matched / primary_predictions
                                        if primary_predictions else None),
        "primary_box_recall_at_50": primary_matched / targets if primary_available and targets else None,
        "primary_box_f1_at_50": (2 * primary_matched / (primary_predictions + targets)
                                 if primary_available and targets else None),
        "primary_false_positives": primary_predictions - primary_matched if primary_available else None,
        "primary_false_negatives": targets - primary_matched if primary_available else None,
        "mean_primary_matched_box_iou": _mean_or_none(
            [value for row in rows for value in row["primary_matched_box_ious"]]),
        "wrapper_matched_instances": wrapper_matched,
        "wrapper_false_positives": predictions - wrapper_matched,
        "wrapper_false_negatives": targets - wrapper_matched,
        "wrapper_precision_at_50": wrapper_matched / predictions if predictions else None,
        "wrapper_recall_at_50": wrapper_matched / targets if targets else None,
        "wrapper_f1_at_50": 2 * wrapper_matched / (predictions + targets) if targets else None,
        "mean_wrapper_iou_on_wrapper_matches": _mean_or_none(
            [value for row in rows for value in row["wrapper_matched_ious"]]),
        "mean_matched_box_iou": _mean_or_none(
            [value for row in rows for value in row["matched_box_ious"]]),
        "mean_wrapper_iou_on_box_matches": _mean_or_none(
            [value for row in rows for value in row["matched_wrapper_ious"]]),
        "mean_boundary_f1_on_box_matches": _mean_or_none(
            [value for row in rows for value in row["matched_boundary_f1"]]),
        "false_positives_per_frame": (predictions - matched) / len(rows) if rows else None,
        "invalid_contours_suppressed": sum(row["invalid_contours_suppressed"] for row in rows),
        "timing_ms": timing,
        "empty_conditions": empty,
        "warnings": list(warnings or []),
        "metric_notes": [
            "These are fixed-confidence, IoU-0.5 detection metrics, not mAP.",
            "primary_box_* metrics score the detector after box NMS and before invalid-wrapper "
            "suppression. box_* and emitted_box_* metrics score boxes associated with emitted valid wrappers.",
            "wrapper_precision/recall/F1_at_50 independently match emitted polygons at polygon IoU 0.5; "
            "missed and suppressed wrappers count as false negatives.",
            "Wrapper IoU and boundary F1 are reported only for one-to-one box matches; "
            "consider them together with recall.",
            "Ground-truth boxes are used for scoring only; the wrapper receives predicted boxes.",
            "Timings are observations from this evaluation run, include its first inference, "
            "and are not a controlled runtime benchmark.",
        ],
        "per_image": rows,
    }
    mask_rows = [row for row in rows if row.get("encasing_representation") == "source_resolution_binary_mask"]
    if mask_rows:
        all_mask = len(mask_rows) == len(rows)
        metrics = [value for row in mask_rows for value in row["matched_mask_metrics"]]
        mask_predictions = sum(row["predictions"] for row in mask_rows)
        mask_targets = sum(row["ground_truth_instances"] for row in mask_rows)
        mask_matches = sum(row["wrapper_matched_instances"] for row in mask_rows)
        predicted_pixels = sum(value["predicted_pixels"] for value in metrics)
        target_pixels = sum(value["target_pixels"] for value in metrics)
        background_pixels = sum(value["background_pixels_included"] for value in metrics)
        missed_pixels = sum(value["foreground_pixels_missed"] for value in metrics)
        summary.update({
            "schema_version": 2,
            "encasing_representation": ("source_resolution_binary_mask" if all_mask else
                                        "mixed_legacy_polygon_and_source_resolution_masks"),
            "mask_images_evaluated": len(mask_rows),
            "mask_matched_instances": mask_matches,
            "mask_false_positives": mask_predictions - mask_matches,
            "mask_false_negatives": mask_targets - mask_matches,
            "mask_precision_at_50": mask_matches / mask_predictions if mask_predictions else None,
            "mask_recall_at_50": mask_matches / mask_targets if mask_targets else None,
            "mask_f1_at_50": 2 * mask_matches / (mask_predictions + mask_targets) if mask_targets else None,
            "mean_mask_iou_on_mask_matches": _mean_or_none(
                [value for row in mask_rows for value in row["wrapper_matched_ious"]]),
            "mean_mask_iou_on_box_matches": _mean_or_none([value["iou"] for value in metrics]),
            "mean_mask_boundary_f1_on_box_matches": _mean_or_none([value["boundary_f1"] for value in metrics]),
            "mean_background_fraction_on_box_matches": _mean_or_none(
                [value["background_fraction_of_prediction"] for value in metrics]),
            "mean_missed_foreground_fraction_on_box_matches": _mean_or_none(
                [value["missed_fraction_of_target"] for value in metrics]),
            "background_fraction_on_box_matches": background_pixels / predicted_pixels if predicted_pixels else None,
            "missed_foreground_fraction_on_box_matches": missed_pixels / target_pixels if target_pixels else None,
            "background_pixels_on_box_matches": background_pixels,
            "missed_foreground_pixels_on_box_matches": missed_pixels,
            "mask_pixels_on_negative_images": sum(row["mask_pixels_on_negative_image"] for row in mask_rows),
            "empty_masks_suppressed": sum(row["empty_masks_suppressed"] for row in mask_rows),
        })
        if all_mask:
            summary["empty_conditions"] = [message.replace("polygon IoU", "mask IoU") for message in empty]
        summary["metric_notes"] = [
            "These are fixed-confidence, IoU-0.5 detection metrics, not mAP.",
            "Authoritative source-pixel masks define foreground; holes and disconnected parts are preserved. "
            "Approximate legacy preview polygons are never used to score a mask.",
            "For mask images, wrapper_* keys are compatibility aliases of mask encasing scores. "
            "An explicitly mixed report retains the original polygon metric for legacy-only images.",
            "Mask precision, recall and F1 match emitted instances by exact binary mask IoU at 0.5, "
            "independently of the diagnostic box matches. Suppressed or missed masks count as false negatives.",
            "Mask IoU, boundary F1, background spill and missed-foreground fractions are conditional on "
            "one-to-one box matches; interpret them together with recall and false positives.",
            "Boundary F1 uses all outer and inner mask boundaries with a two-source-pixel Euclidean tolerance.",
            "Mean fractions weight matched instances equally; unprefixed fractions pool their pixel counts. "
            "Foreground pixels emitted on truly negative images are additionally reported.",
            "primary_box_* scores the detector after box NMS and before encasing suppression; "
            "box_* and emitted_box_* score boxes associated with emitted encasings.",
            "Ground-truth boxes are used only for scoring; inference receives only image pixels.",
            "Timings include the first inference and are observations, not a controlled runtime benchmark.",
        ]
    return summary


def evaluate_with_predictor(project: Any, records: list[dict],
                            predictor: Callable[[Image.Image], dict], *,
                            split: str = "val", confidence: float = 0.35,
                            should_stop: Callable[[], bool] | None = None) -> dict:
    """Score a predictor without allowing annotation data to enter inference.

    Internal helper also supports training-time cancellation between images.
    A cancelled evaluation explicitly reports its incomplete status.
    """
    rows, cancelled = [], False
    force_masks = any(annotation.get("mask") is not None for record in records
                      for annotation in record["annotations"])
    for record in records:
        if should_stop is not None and should_stop():
            cancelled = True
            break
        for annotation in record["annotations"]:
            errors = validate_annotation(annotation, record["width"], record["height"],
                                         require_complete=True)
            if errors:
                raise ValueError(f"Invalid reviewed labels in {record['id']}: " + "; ".join(errors))
        with Image.open(project.image_path(record)) as opened:
            if opened.size != (record["width"], record["height"]):
                raise ValueError(f"Image dimensions changed for {record['id']}; re-import the image.")
            image = opened.convert("RGB")
        # Deliberately pass only pixels, never the record or its annotations.
        result = predictor(image)
        rows.append(_score_image(record, result, mask_mode=True if force_masks else None))
    summary = _summarize(rows, split, confidence, _split_warnings(project, split))
    summary["cancelled"] = cancelled
    summary["expected_images"] = len(records)
    return summary


def evaluate(project_path: str | Path, cloud_path: str | Path, split: str = "val",
             device: str = "cpu", confidence: float = 0.35) -> dict:
    """Evaluate actual runtime predictions on reviewed images in one split."""
    if split not in {"train", "val", "test"}:
        raise ValueError("split must be train, val, or test.")
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must lie between 0 and 1.")
    from .data import Project
    from .runtime import CloudRuntime

    project = Project.open(project_path)
    runtime = CloudRuntime(cloud_path, backend="torch", device=device)
    records = project.training_records(split)
    summary = evaluate_with_predictor(
        project, records,
        lambda image: runtime.predict(image, confidence=confidence, debug=True),
        split=split, confidence=confidence,
    )
    summary["model_path"] = str(Path(cloud_path).resolve())
    summary["device"] = device
    return summary
