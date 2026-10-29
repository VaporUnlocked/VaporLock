"""Compare two models on the same reviewed pixels and export a failure browser.

This module uses the normal inference API and never supplies ground-truth boxes
to a model. It needs no training dependencies when using the NumPy backend.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import html
import io
import json
import math
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image

from .data import Project, atomic_write, utc_now, write_json
from .evaluation import _score_image, _split_warnings, _summarize
from .geometry import validate_annotation
from .runtime import CloudRuntime


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _model_identity(path: str | Path, backend: str) -> dict:
    source = Path(path).expanduser().resolve()
    if backend == "onnx":
        from .export import read_onnx_export
        config, _, detector, wrapper = read_onnx_export(source)
        descriptor = source / "model.json" if source.is_dir() else source
        hashes = {"model.json": _file_hash(descriptor), "detector.onnx": _file_hash(detector),
                  "wrapper.onnx": _file_hash(wrapper)}
        digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("utf-8")).hexdigest()
        return {"name": source.name, "sha256": digest, "file_sha256": hashes,
                "model_config": config.to_dict()}
    return {"name": source.name, "sha256": _file_hash(source)}


def _at_threshold(result: dict, confidence: float) -> dict:
    """NMS is descending-score ordered, so a higher-threshold prefix is stable."""
    detections = [item for item in result["detections"] if item["confidence"] >= confidence]
    original_primary = result.get("diagnostics", {}).get("primary_detections")
    if original_primary is None:
        raise ValueError("Comparison requires runtime.predict(debug=True) primary diagnostics.")
    primary = [item for item in original_primary if item["confidence"] >= confidence]
    return {"detections": detections,
            "diagnostics": {"primary_detections": primary},
            "invalid_contours": len(primary) - len(detections),
            "timing_ms": result.get("timing_ms", {})}


def _failure_categories(row: dict) -> list[str]:
    result = []
    if row["false_negatives"]:
        result.append("missed_targets")
    if row["false_positives"]:
        result.append("false_detections")
    if any(value < 0.75 for value in row["matched_box_ious"]):
        result.append("poor_boxes")
    if any(value < 0.75 for value in row["matched_wrapper_ious"]):
        result.append("poor_encasings")
    if row["invalid_contours_suppressed"]:
        result.append("invalid_contours")
    return result


def _thumbnail(image: Image.Image) -> str:
    thumb = image.copy()
    thumb.thumbnail((800, 500), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    thumb.save(output, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _change(a: dict, b: dict) -> str:
    # Classify count changes explicitly. A gain in one count and loss in another
    # is mixed; conditional shape scores are displayed rather than hidden here.
    differences = [b[name] - a[name] for name in
                   ("false_negatives", "false_positives", "invalid_contours_suppressed")]
    if any(value < 0 for value in differences) and any(value > 0 for value in differences):
        return "mixed"
    if any(value < 0 for value in differences):
        return "improved"
    if any(value > 0 for value in differences):
        return "regressed"
    return "unchanged_counts"


def compare_models(project_path: str | Path, model_a_path: str | Path,
                   model_b_path: str | Path, output_dir: str | Path, *,
                   split: str = "val", confidence: float = 0.35,
                   thresholds: Sequence[float] | None = None,
                   backend: str = "numpy", device: str = "cpu",
                   progress: Callable[[dict], None] | None = None,
                   should_stop: Callable[[], bool] | None = None) -> dict:
    """Write comparison.json and a self-contained comparison.html.

    Both models see one decoded copy of each image. Reviewed labels and image
    digests are frozen before inference; changes during reading abort the run.
    Threshold sweeps are validation-only. A test comparison uses exactly the
    supplied confidence and never recommends or selects a threshold.
    """
    if split not in {"val", "test"}:
        raise ValueError("Model comparison requires a held-out val or test split.")
    if isinstance(confidence, bool) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be a finite number between 0 and 1.")
    if split == "test" and thresholds is not None:
        raise ValueError("Threshold comparisons are validation-only. Choose a fixed confidence for test.")
    levels = [float(confidence)]
    if thresholds is not None:
        if not 1 <= len(thresholds) <= 20:
            raise ValueError("Provide between 1 and 20 validation thresholds.")
        for value in thresholds:
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Each threshold must be finite and between 0 and 1.")
            levels.append(float(value))
    levels = sorted(set(levels))
    if backend not in {"numpy", "torch", "onnx"}:
        raise ValueError("backend must be numpy, torch, or onnx.")
    project = Project.open(project_path)
    records = copy.deepcopy(project.training_records(split))
    if not records:
        raise ValueError(f"No complete reviewed {split} images are available for comparison.")
    output = Path(output_dir).expanduser()
    if output.is_symlink():
        raise ValueError("The comparison output directory must not be a symlink.")
    output = output.resolve()
    if output == project.root or output == project.root / "images" or (project.root / "images") in output.parents:
        raise ValueError("Choose a separate comparison output directory, outside the project image store.")
    for name in ("comparison.json", "comparison.html"):
        if (output / name).exists() or (output / name).is_symlink():
            raise FileExistsError("This comparison already has outputs. Choose a new comparison directory.")

    def stopped() -> bool:
        return should_stop is not None and should_stop()

    frozen = []
    for record in records:
        if stopped():
            return {"status": "stopped", "cancelled": True, "report_path": None,
                    "json_path": None, "images_evaluated": 0, "expected_images": len(records)}
        for annotation in record["annotations"]:
            errors = validate_annotation(annotation, record["width"], record["height"], require_complete=True)
            if errors:
                raise ValueError(f"Invalid reviewed labels in {record['id']}: " + "; ".join(errors))
        frozen.append({"id": record["id"], "split": split,
                       "group_id": record.get("group_id"), "annotations": record["annotations"],
                       "content_sha256": _file_hash(project.image_path(record))})
    fingerprint = hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode("utf-8")).hexdigest()
    identities = [_model_identity(path, backend) for path in (model_a_path, model_b_path)]
    runtimes = [CloudRuntime(path, backend=backend, device=device)
                for path in (model_a_path, model_b_path)]
    use_mask_metrics = (any(getattr(runtime.config, "is_mask", False) for runtime in runtimes) or
                        any(annotation.get("mask") is not None for record in records
                            for annotation in record["annotations"]))
    for index, path in enumerate((model_a_path, model_b_path)):
        if _model_identity(path, backend)["sha256"] != identities[index]["sha256"]:
            raise ValueError("A model changed while it was being loaded. Retry with stable model files.")
        identities[index]["model_config"] = runtimes[index].config.to_dict()
    warnings = _split_warnings(project, split)
    if project.manifest.get("synthetic") is True:
        warnings.append("SYNTHETIC DATASET: these results measure a generated software fixture, not game-player recognition.")
    rows = [[[] for _ in levels] for _ in runtimes]
    comparisons, cancelled = [], False
    for index, (record, signature) in enumerate(zip(records, frozen)):
        if stopped():
            cancelled = True
            break
        # One stable file read supplies exactly the same pixels to A and B.
        encoded = project.image_path(record).read_bytes()
        if hashlib.sha256(encoded).hexdigest() != signature["content_sha256"]:
            raise ValueError(f"Image {record['id']} changed during comparison; restart to freeze a new dataset.")
        with Image.open(io.BytesIO(encoded)) as opened:
            if opened.size != (record["width"], record["height"]):
                raise ValueError(f"Image dimensions changed for {record['id']}; re-import the image.")
            image = opened.convert("RGB")
        item = {"image_id": record["id"], "file": record["file"], "group_id": record.get("group_id"),
                "width": record["width"], "height": record["height"],
                "annotations": record["annotations"], "thumbnail": _thumbnail(image)}
        for model_index, runtime in enumerate(runtimes):
            # One low-threshold forward pass supports all higher thresholds.
            result = runtime.predict(image, confidence=levels[0], debug=True)
            for level_index, level in enumerate(levels):
                filtered = _at_threshold(result, level)
                score = _score_image(record, filtered, mask_mode=True if use_mask_metrics else None)
                rows[model_index][level_index].append(score)
                if level == confidence:
                    item["a" if model_index == 0 else "b"] = {
                        "metrics": score, "detections": filtered["detections"],
                        "failure_categories": _failure_categories(score)}
        item["change"] = _change(item["a"]["metrics"], item["b"]["metrics"])
        comparisons.append(item)
        if progress is not None:
            progress({"event": "comparison_image", "completed": index + 1,
                      "total": len(records), "image_id": record["id"]})
    primary_index = levels.index(float(confidence))
    models = []
    for model_index, identity in enumerate(identities):
        summaries = [_summarize(values, split, level, warnings)
                     for values, level in zip(rows[model_index], levels)]
        for summary in summaries:
            summary["cancelled"], summary["expected_images"] = cancelled, len(records)
        models.append({**identity, "metrics": summaries[primary_index],
                       "threshold_comparisons": summaries if thresholds is not None else []})
    report = {"schema_version": 1, "created_utc": utc_now(),
              "status": "stopped" if cancelled else "completed", "cancelled": cancelled,
              "project_name": project.manifest.get("name", "Untitled"), "split": split,
              "dataset_fingerprint": fingerprint, "frozen_dataset": frozen,
              "confidence_threshold": float(confidence), "inference_confidence": levels[0],
              "backend": backend, "device": device, "images_evaluated": len(comparisons),
              "expected_images": len(records), "models": models, "per_image": comparisons,
              "warnings": warnings,
              "notes": ["Both models receive identical pixels; ground-truth annotations are used only for scoring.",
                        "The dataset fingerprint records selected reviewed labels, capture groups, and image bytes.",
                        "Poor box/encasing flags mean IoU below 0.75 among box matches; detection matching uses IoU 0.5.",
                        "Change labels compare missed, false, and invalid counts; inspect shape scores separately.",
                        "Threshold sweeps reuse confidence-ordered predictions from the lowest threshold.",
                        "Timings include the first inference at inference_confidence; they are observations, not a controlled benchmark.",
                        "No model or threshold is automatically selected. Keep final test settings fixed."]}
    if use_mask_metrics:
        report["schema_version"] = 2
        report["encasing_representation"] = "source_resolution_binary_mask"
        report["notes"].extend([
            "Both models are scored in the same source-pixel mask space. Legacy polygons are rasterized "
            "for this cross-representation comparison; authoritative mask holes and separate parts remain intact.",
            "Mask overlays use actual foreground pixels. Display-only convex polygon previews never define "
            "the scoring target or prediction. Mask background spill and missing-foreground fractions are "
            "reported alongside recall and false detections.",
        ])
    output.mkdir(parents=True, exist_ok=True)
    report["report_path"], report["json_path"] = str(output / "comparison.html"), str(output / "comparison.json")
    write_json(output / "comparison.json", report)
    atomic_write(output / "comparison.html", _render_report(report).encode("utf-8"))
    return report


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _overlay(item: dict, predictions: list[dict], color: str) -> str:
    width, height = int(item["width"]), int(item["height"])
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Ground truth and predicted annotations">',
             f'<image href="{item["thumbnail"]}" width="{width}" height="{height}"/>']
    for annotations, stroke, dashed in ((item["annotations"], "#9cff9f", True), (predictions, color, False)):
        for annotation in annotations:
            dash = ' stroke-dasharray="5 4"' if dashed else ""
            if annotation.get("mask") is not None:
                from .target_preview import _mask_overlay
                parts.append(_mask_overlay(annotation["mask"], stroke, alpha=35 if dashed else 55))
            else:
                points = " ".join(f"{float(x):.3f},{float(y):.3f}" for x, y in annotation["wrapper"])
                parts.append(f'<polygon points="{points}" fill="none" stroke="{stroke}" stroke-width="2" '
                             f'vector-effect="non-scaling-stroke"{dash}/>')
            x1, y1, x2, y2 = annotation.get("box", annotation.get("primary_box"))
            parts.append(f'<rect x="{float(x1):.3f}" y="{float(y1):.3f}" width="{float(x2-x1):.3f}" '
                         f'height="{float(y2-y1):.3f}" fill="none" stroke="{stroke}" stroke-width="1" '
                         f'vector-effect="non-scaling-stroke"{dash}/>')
    return "".join(parts) + "</svg>"


def _render_report(report: dict) -> str:
    esc = html.escape
    a, b = report["models"]
    labels = ["A · " + a["name"], "B · " + b["name"]]
    metric_names = [("Reviewed images", "images_evaluated"), ("Missed targets", "false_negatives"),
                    ("False detections", "false_positives"), ("Primary box F1", "primary_box_f1_at_50"),
                    ("Emitted box F1", "emitted_box_f1_at_50"), ("Encasing F1", "wrapper_f1_at_50"),
                    ("Matched encasing IoU", "mean_wrapper_iou_on_box_matches"),
                    ("Matched boundary F1", "mean_boundary_f1_on_box_matches")]
    if report.get("encasing_representation") == "source_resolution_binary_mask":
        metric_names.extend([("Matched background fraction", "background_fraction_on_box_matches"),
                             ("Matched foreground missed", "missed_foreground_fraction_on_box_matches")])
    metrics = "".join(f'<tr><th>{title}</th><td>{_number(a["metrics"].get(key))}</td>'
                      f'<td>{_number(b["metrics"].get(key))}</td></tr>' for title, key in metric_names)
    cards = []
    for item in report["per_image"]:
        tags = sorted(set(item["a"]["failure_categories"] + item["b"]["failure_categories"] + [item["change"]]))
        panels = []
        for key, label, color in zip(("a", "b"), labels, ("#4ee1ef", "#ffa0e5")):
            row = item[key]["metrics"]
            panels.append(f'<section><h3>{esc(label)}</h3>{_overlay(item, item[key]["detections"], color)}'
                          f'<p>Missed: {row["false_negatives"]} · False: {row["false_positives"]} · '
                          f'Encasing IoU: {_number(sum(row["matched_wrapper_ious"])/len(row["matched_wrapper_ious"]) if row["matched_wrapper_ious"] else None)}</p></section>')
        cards.append(f'<article data-tags="{esc(" ".join(tags), quote=True)}"><h2>{esc(item["file"])}</h2>'
                     f'<p>{esc(item["image_id"])} · {esc(item["change"].replace("_", " "))} · '
                     f'{esc(str(item.get("group_id") or "No capture group"))}</p><div class="panels">'
                     + "".join(panels) + "</div></article>")
    threshold_rows = []
    for label, model in zip(labels, (a, b)):
        for summary in model["threshold_comparisons"]:
            threshold_rows.append(f'<tr><td>{esc(label)}</td><td>{summary["confidence_threshold"]:.2f}</td>'
                                  f'<td>{_number(summary["emitted_box_precision_at_50"])}</td>'
                                  f'<td>{_number(summary["emitted_box_recall_at_50"])}</td>'
                                  f'<td>{_number(summary["wrapper_f1_at_50"])}</td></tr>')
    thresholds = ('<h2>Validation threshold comparison</h2><table><thead><tr><th>Model</th><th>Confidence</th>'
                  '<th>Box precision</th><th>Box recall</th><th>Encasing F1</th></tr></thead><tbody>'
                  + "".join(threshold_rows) + '</tbody></table>') if threshold_rows else ""
    warnings = "".join(f"<p class=warning>{esc(value)}</p>" for value in report["warnings"])
    notes = "".join(f"<li>{esc(value)}</li>" for value in report["notes"])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cloud Studio · Model comparison</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#101721;color:#e6edf7;font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1400px;margin:auto;padding:28px}}h1{{font-size:30px}}h2{{font-size:20px}}h3{{font-size:16px}}
table{{border-collapse:collapse;width:100%;margin:18px 0}}td,th{{padding:9px 12px;border-bottom:1px solid #354153;text-align:left}}
.panels{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}svg{{width:100%;display:block;background:#070c13}}
article{{padding:20px;margin:18px 0;border:1px solid #354153;border-radius:12px;background:#17212e}}article[hidden]{{display:none}}
.warning{{padding:12px;border-left:4px solid #ffcc77;background:#322b21}}code{{overflow-wrap:anywhere}}select{{padding:8px;background:#233147;color:inherit;border:1px solid #4d617e;border-radius:5px}}
nav{{position:sticky;top:0;background:#101721;padding:12px 0;z-index:2}}@media(max-width:760px){{.panels{{grid-template-columns:1fr}}main{{padding:14px}}}}
</style><main><h1>Model comparison</h1><p>{esc(report["project_name"])} · {esc(report["split"])} ·
{report["images_evaluated"]}/{report["expected_images"]} reviewed images · {esc(report["status"])} · confidence {report["confidence_threshold"]:.2f}</p>
{warnings}<table><thead><tr><th>Metric</th><th>{esc(labels[0])}</th><th>{esc(labels[1])}</th></tr></thead><tbody>{metrics}</tbody></table>
<p>Green dashed annotations are ground truth. Cyan is model A; pink is model B. Conditional outline scores must be read alongside missed targets.</p>
{thresholds}<h2>Image review</h2><nav><label>Show <select id="filter"><option value="all">All images</option><option value="missed_targets">Missed targets</option><option value="false_detections">False detections</option><option value="poor_encasings">Poor encasings</option><option value="poor_boxes">Poor boxes</option><option value="invalid_contours">Invalid encasings</option><option value="regressed">B regressed on error counts</option><option value="improved">B improved on error counts</option><option value="mixed">Mixed changes</option></select></label> <span id="count"></span></nav>
{"".join(cards)}<h2>Reproducibility and interpretation</h2><p>Dataset SHA-256: <code>{report["dataset_fingerprint"]}</code></p>
<p>A SHA-256: <code>{a["sha256"]}</code><br>B SHA-256: <code>{b["sha256"]}</code></p><p>Backend: {esc(report["backend"])} · device: {esc(report["device"])} · inference threshold: {report["inference_confidence"]:.2f}</p><ul>{notes}</ul>
</main><script>const filter=document.getElementById('filter');const cards=[...document.querySelectorAll('article[data-tags]')];function applyFilter(){{let shown=0;for(const card of cards){{card.hidden=filter.value!=='all'&&!card.dataset.tags.split(' ').includes(filter.value);if(!card.hidden)shown++;}}document.getElementById('count').textContent=shown+' images';}}filter.addEventListener('change',applyFilter);applyFilter();</script></html>'''
