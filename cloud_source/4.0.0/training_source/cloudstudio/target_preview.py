"""Inspect detail changes when reviewed encasings become finite training targets."""

from __future__ import annotations

import copy
import base64
import html
import io
from pathlib import Path
from typing import Callable
import zlib

import numpy as np
from PIL import Image

from .comparison import _thumbnail
from .config import ModelConfig
from .data import Project, atomic_write, utc_now, write_json
from .geometry import (boundary_f_score, is_simple_polygon, polygon_area,
                       polygon_iou, resample_polygon, transform_box, validate_annotation)
from .masks import annotation_mask, binary_mask_metrics, decode_mask, encode_mask, project_roi_mask


def _coverage_payload(coverage: np.ndarray, samples: int) -> dict:
    denominator = samples * samples
    # Supersampled binary labels have exact multiples of 1/(samples**2).
    # Preserve every coverage value without thousands of repeated JSON floats.
    values = np.rint(coverage * denominator).astype(np.uint8)
    return {"encoding": "cloud-coverage-grid-v1", "shape": list(values.shape),
            "denominator": denominator, "compression": "zlib-base64-u8-row-major",
            "values": base64.b64encode(zlib.compress(values.tobytes())).decode("ascii")}


def _preview_mask_annotation(annotation: dict, width: int, height: int,
                              config: ModelConfig, samples: int) -> dict:
    from .training_targets import sample_mask_targets
    if type(samples) is not int or samples not in (1, 2, 4):
        raise ValueError("Mask target samples must be 1, 2, or 4 per axis.")
    original = annotation_mask(annotation, width, height)
    # This is the same integer resize/letterbox geometry as geometry.letterbox;
    # computing metadata alone avoids allocating an unused screenshot-sized RGB image.
    size = config.input_size
    scale = min(size / width, size / height)
    resized_w = max(1, min(size, round(width * scale)))
    resized_h = max(1, min(size, round(height * scale)))
    meta = {"size": size, "scale": scale, "scale_x": resized_w / width,
            "scale_y": resized_h / height, "pad_x": (size-resized_w)//2,
            "pad_y": (size-resized_h)//2, "original_width": width, "original_height": height}
    guides = np.asarray([transform_box(annotation["box"], meta)], dtype=np.float32) / size
    target = {"mask_sources": [original], "mask_meta": meta}
    coverage = sample_mask_targets([target], guides, np.asarray([0]), np.asarray([0]),
                                    config.mask_size, samples=samples)[0, 0]
    reconstructed = project_roi_mask(coverage, annotation["box"], (width, height))
    metrics = binary_mask_metrics(decode_mask(reconstructed), original, boundary_tolerance=2)
    valid = bool(np.any(coverage >= .5) and metrics["predicted_pixels"])
    warning = not valid or metrics["iou"] < .95
    if not valid:
        message = ("This target grid cannot retain foreground at the 0.5 output threshold. "
                   "Increase mask resolution or tighten the accurate guiding box before training.")
    elif warning:
        message = ("The finite mask grid changes foreground detail. Inspect missed pixels, extra background, "
                   "holes and small separate parts; try a larger mask resolution.")
    else:
        message = ("This teacher-box target reconstructs at least 0.95 source-pixel IoU. "
                   "It is a capacity preview, not a prediction accuracy guarantee.")
    return {"annotation_id": annotation.get("id"), "box": copy.deepcopy(annotation["box"]),
            "target_representation": "source_resolution_mask", "model_config": config.to_dict(),
            "original_mask": copy.deepcopy(annotation.get("mask")) if annotation.get("mask") is not None
                             else encode_mask(original),
            "sampled_mask": reconstructed, "target_mask_coverage": _coverage_payload(coverage, samples),
            "model_mask_size": config.mask_size, "mask_samples_per_axis": samples,
            "model_vertices": None, "original_vertices": len(annotation.get("wrapper", [])),
            "valid_training_target": valid, "polygon_iou": None,
            "mask_iou": float(metrics["iou"]),
            "boundary_f1_at_2_pixels": float(metrics["boundary_f1"]),
            "area_ratio": metrics["predicted_pixels"] / metrics["target_pixels"] if metrics["target_pixels"] else None,
            "background_pixels_included": metrics["background_pixels_included"],
            "foreground_pixels_missed": metrics["foreground_pixels_missed"],
            "background_fraction_of_prediction": metrics["background_fraction_of_prediction"],
            "missed_fraction_of_target": metrics["missed_fraction_of_target"],
            "detail_warning": warning, "message": message}


def preview_annotation_target(annotation: dict, width: int, height: int,
                              vertices: int = 32, *, config: ModelConfig | None = None,
                              mask_samples: int = 2) -> dict:
    """Return the exact resampling used by training, in original image pixels."""
    if config is not None and not isinstance(config, ModelConfig):
        raise ValueError("Target preview config must be a ModelConfig.")
    if config is not None:
        vertices = config.vertices
    if type(vertices) is not int or vertices not in (16, 32, 64):
        raise ValueError("Model vertices must be 16, 32, or 64.")
    errors = validate_annotation(annotation, width, height, require_complete=True)
    if errors:
        raise ValueError("A target preview requires a complete box and valid outline: " + "; ".join(errors))
    if config is not None and config.is_mask:
        return _preview_mask_annotation(annotation, width, height, config, mask_samples)
    if annotation.get("mask") is not None:
        raise ValueError("The legacy polygon architecture cannot use an authoritative mask's approximate "
                         "preview as ground truth. Select the S2 mask architecture for this target preview.")
    original = copy.deepcopy(annotation["wrapper"])
    sampled = resample_polygon(original, vertices).tolist()
    valid = is_simple_polygon(sampled)
    overlap = float(polygon_iou(original, sampled, width, height)) if valid else None
    return {"annotation_id": annotation.get("id"), "box": copy.deepcopy(annotation["box"]),
            "original_wrapper": original, "sampled_wrapper": sampled,
            "original_vertices": len(original), "model_vertices": vertices,
            "valid_training_target": valid,
            "polygon_iou": overlap,
            "boundary_f1_at_2_pixels": float(boundary_f_score(original, sampled, tolerance=2.0)) if valid else None,
            "area_ratio": float(polygon_area(sampled) / polygon_area(original)) if valid else None,
            "detail_warning": not valid or overlap < 0.95,
            "message": ("Resampling creates a self-intersecting or degenerate loop. This capacity would block training."
                        if not valid else "Some outline detail changes during resampling. Inspect the preview."
                        if overlap < 0.95 else "Resampling retains at least 0.95 polygon IoU with the reviewed outline.")}


def preview_training_targets(project_path: str | Path, output_dir: str | Path, *,
                             vertices: int = 32, split: str = "train",
                             config: ModelConfig | None = None, mask_samples: int = 2,
                             should_stop: Callable[[], bool] | None = None) -> dict:
    """Write an unchanged-label audit with original/target overlays side by side."""
    if config is not None and not isinstance(config, ModelConfig):
        raise ValueError("Target preview config must be a ModelConfig.")
    if config is not None:
        vertices = config.vertices
    if type(vertices) is not int or vertices not in (16, 32, 64):
        raise ValueError("Model vertices must be 16, 32, or 64.")
    if split not in {"train", "val", "test"}:
        raise ValueError("split must be train, val, or test.")
    project = Project.open(project_path)
    records = project.training_records(split)
    if not records:
        raise ValueError(f"No reviewed {split} images are available.")
    output = Path(output_dir).expanduser()
    if output.is_symlink():
        raise ValueError("The preview output directory must not be a symlink.")
    output = output.resolve()
    if output == project.root or output == project.root / "images" or (project.root / "images") in output.parents:
        raise ValueError("Choose a separate target preview directory outside the project image store.")
    for name in ("target_preview.json", "target_preview.html"):
        if (output / name).exists() or (output / name).is_symlink():
            raise FileExistsError("This target preview already has outputs. Choose a new output directory.")
    images, targets, cancelled = [], [], False
    for record in records:
        if should_stop is not None and should_stop():
            cancelled = True
            break
        previews = [preview_annotation_target(ann, record["width"], record["height"], vertices,
                                               config=config, mask_samples=mask_samples)
                    for ann in record["annotations"]]
        with Image.open(project.image_path(record)) as opened:
            if opened.size != (record["width"], record["height"]):
                raise ValueError(f"Image dimensions changed for {record['id']}; re-import the image.")
            thumbnail = _thumbnail(opened.convert("RGB"))
        targets.extend(previews)
        images.append({"image_id": record["id"], "file": record["file"], "width": record["width"],
                       "height": record["height"], "thumbnail": thumbnail, "targets": previews})
    result = {"schema_version": 1, "created_utc": utc_now(), "project_name": project.manifest.get("name", "Untitled"),
              "split": split, "model_vertices": vertices, "images_evaluated": len(images),
              "expected_images": len(records), "targets_evaluated": len(targets),
              "invalid_targets": sum(not item["valid_training_target"] for item in targets),
              "detail_warnings": sum(item["detail_warning"] for item in targets),
              "cancelled": cancelled, "status": "stopped" if cancelled else "completed", "per_image": images,
              "notes": ["This previews target capacity, not a model prediction or training quality score.",
                        "Training uses these exact perimeter samples, then maps them into normalized letterbox coordinates.",
                        "The independent box remains unchanged. Source annotations are never replaced by resampled outlines.",
                        "IoU below 0.95 flags visible detail changes for inspection; it does not automatically reject a valid loop.",
                        "Invalid resampled loops are rejected by the trainer; increase capacity or manually review/simplify the outline."]}
    if config is not None and config.is_mask:
        result.update({"schema_version": 2, "model_vertices": None, "model_architecture": config.architecture,
                       "model_mask_size": config.mask_size, "mask_samples_per_axis": mask_samples,
                       "target_representation": "source_resolution_mask", "model_config": config.to_dict()})
        result["notes"] = [
            "This previews mask target capacity with the annotated guiding box, not a trained model's accuracy.",
            "The coverage grid is sampled directly from the source-resolution labels using the trainer's exact "
            "sampling function. Labels are not first reduced to the detector input resolution.",
            "The second panel projects that coverage grid to source pixels with the runtime's bilinear "
            "projection and 0.5 threshold. A finite mask grid can still lose fine foreground, holes or separate parts.",
            "Training uses fractional coverage values, including background pixels inside the box; "
            "the lossless compressed coverage payload stores multiples of 1/(samples_per_axis**2).",
            "IoU, all-boundary F1 at two source pixels, background spill and missing foreground compare this "
            "reconstruction against the authoritative mask. IoU below 0.95 requests inspection.",
            "Predicted guiding boxes and augmentation can change the target during training. This audit uses "
            "the unaugmented teacher box. It does not establish a trained model's best possible accuracy.",
            "Source masks and annotations remain unchanged. Lossless annotation storage does not make "
            "finite model outputs or predictions lossless.",
        ]
    output.mkdir(parents=True, exist_ok=True)
    result["report_path"], result["json_path"] = str(output / "target_preview.html"), str(output / "target_preview.json")
    write_json(output / "target_preview.json", result)
    atomic_write(output / "target_preview.html", _render(result).encode("utf-8"))
    return result


def _mask_overlay(mask: dict, color: str, *, alpha: int = 140) -> str:
    values = decode_mask(mask, full=False)
    if not values.size:
        return ""
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    rgba[:, :, :3] = [int(color[i:i+2], 16) for i in (1, 3, 5)]
    rgba[:, :, 3] = values.astype(np.uint8) * alpha
    if alpha < 100:
        padded = np.pad(values, 1)
        interior = (padded[:-2, 1:-1] & padded[2:, 1:-1] &
                    padded[1:-1, :-2] & padded[1:-1, 2:])
        rgba[:, :, 3][values & ~interior] = 235
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    x, y = mask["origin"]
    return (f'<image href="data:image/png;base64,{encoded}" x="{x}" y="{y}" '
            f'width="{values.shape[1]}" height="{values.shape[0]}" style="image-rendering:pixelated"/>')


def _coverage_image(payload: dict) -> str:
    values = np.frombuffer(zlib.decompress(base64.b64decode(payload["values"])), dtype=np.uint8)
    values = values.reshape(payload["shape"])
    pixels = np.rint(values.astype(np.float32) * (255 / payload["denominator"])).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _panel(image: dict, key: str, color: str) -> str:
    result = [f'<svg viewBox="0 0 {image["width"]} {image["height"]}" role="img" aria-label="Outline target preview">',
              f'<image href="{image["thumbnail"]}" width="{image["width"]}" height="{image["height"]}"/>']
    for item in image["targets"]:
        if key.endswith("_mask"):
            result.append(_mask_overlay(item[key], color))
            continue
        points = " ".join(f"{float(x):.3f},{float(y):.3f}" for x, y in item[key])
        result.append(f'<polygon points="{points}" fill="none" stroke="{color}" stroke-width="2" vector-effect="non-scaling-stroke"/>')
        if key == "sampled_wrapper":
            for x, y in item[key]:
                result.append(f'<circle cx="{float(x):.3f}" cy="{float(y):.3f}" r="1.3" fill="{color}"/>')
    return "".join(result) + "</svg>"


def _render(result: dict) -> str:
    esc, cards = html.escape, []
    for image in result["per_image"]:
        details = []
        for item in image["targets"]:
            if item.get("target_representation") == "source_resolution_mask":
                css = ' class="warning"' if item["detail_warning"] else ""
                coverage_image = _coverage_image(item["target_mask_coverage"])
                details.append(
                    f'<div{css}><p>{esc(str(item["annotation_id"]))}: {item["model_mask_size"]} × '
                    f'{item["model_mask_size"]} mask grid · source-pixel IoU {item["mask_iou"]:.3f} · '
                    f'boundary F1 {item["boundary_f1_at_2_pixels"]:.3f} · background '
                    f'{100*item["background_fraction_of_prediction"]:.2f}% · foreground missed '
                    f'{100*item["missed_fraction_of_target"]:.2f}%. {esc(item["message"])}</p>'
                    f'<details><summary>Actual fractional training target grid</summary><p>White = foreground, '
                    f'black = background, gray = partial coverage.</p><img class="coverage" '
                    f'alt="Fractional foreground coverage in the actual mask training target" '
                    f'src="{coverage_image}"></details></div>')
                continue
            overlap = "invalid" if item["polygon_iou"] is None else f'{item["polygon_iou"]:.3f}'
            css = ' class="warning"' if item["detail_warning"] else ""
            details.append(f'<p{css}>{esc(str(item["annotation_id"]))}: {item["original_vertices"]} reviewed vertices → '
                           f'{item["model_vertices"]} target vertices · IoU {overlap}. {esc(item["message"])}</p>')
        mask_mode = result.get("target_representation") == "source_resolution_mask"
        source_key, sampled_key = ("original_mask", "sampled_mask") if mask_mode else ("original_wrapper", "sampled_wrapper")
        source_title = "Reviewed source mask" if mask_mode else "Reviewed outline"
        sampled_title = "Training target reconstructed at 0.5" if mask_mode else "Actual training target"
        cards.append(f'<article><h2>{esc(image["file"])}</h2><div class="panels"><section><h3>{source_title}</h3>'
                     f'{_panel(image, source_key, "#93efa4")}</section><section><h3>{sampled_title}</h3>'
                     f'{_panel(image, sampled_key, "#f3a8e4")}</section></div>{"".join(details)}</article>')
    notes = "".join(f"<li>{esc(note)}</li>" for note in result["notes"])
    capacity = (f'{result["model_mask_size"]} × {result["model_mask_size"]} mask grid'
                if result.get("target_representation") == "source_resolution_mask" else
                f'{result["model_vertices"]} model vertices')
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cloud Studio · Training target detail</title><style>body{{margin:0;background:#101721;color:#e6edf7;font:15px/1.5 system-ui,sans-serif}}main{{max-width:1300px;margin:auto;padding:28px}}
.panels{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}svg{{width:100%;background:#070c13}}article{{padding:20px;margin:18px 0;border:1px solid #354153;border-radius:12px}}h2{{font-size:20px}}h3{{font-size:16px}}.coverage{{width:240px;max-width:100%;image-rendering:pixelated}}details{{margin:12px 0}}.warning{{padding:10px;border-left:4px solid #ffcc77;background:#322b21}}@media(max-width:750px){{.panels{{grid-template-columns:1fr}}}}</style>
<main><h1>Training target detail</h1><p>{esc(result["project_name"])} · {esc(result["split"])} · {capacity} · {esc(result["status"])}</p>
<p>{result["images_evaluated"]}/{result["expected_images"]} images · {result["targets_evaluated"]} targets · {result["invalid_targets"]} invalid targets · {result["detail_warnings"]} detail warnings</p><ul>{notes}</ul>{"".join(cards)}</main></html>'''
