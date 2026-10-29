"""Import local YOLO detection labels as unreviewed, independent main boxes.

The scan is read-only and JSON serializable. Import repeats that scan to reject
stale previews, copies image pixels, then commits one project manifest. Labels
never become approved negatives, and existing project records are never edited.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from PIL import Image

from .data import Project, SPLITS, utc_now
from .geometry import validate_annotation

PLAN_VERSION = 1
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_FILE_BYTES = 128 * 1024 * 1024
MAX_IMAGES = 100_000
MAX_LABEL_ROWS = 50_000
MAX_ISSUES = 200
# Accept the same 1% normalized-coordinate tolerance as Ultralytics' label
# verifier. Width/height must still be strictly positive for usable boxes.
NORMALIZED_TOLERANCE = 0.01
_SPLIT_NAMES = {"train": "train", "val": "val", "valid": "val", "validation": "val", "test": "test"}


class _Issues:
    def __init__(self):
        self.errors, self.warnings = [], []
        self.error_count = self.warning_count = 0

    def error(self, message):
        self.error_count += 1
        if len(self.errors) < MAX_ISSUES:
            self.errors.append(str(message))

    def warning(self, message):
        self.warning_count += 1
        if len(self.warnings) < MAX_ISSUES:
            self.warnings.append(str(message))


def _read_bytes(path, maximum):
    with Path(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError(f"File exceeds the {maximum // (1024 * 1024)} MiB limit: {path}")
    return raw


def _read_text(path):
    raw = _read_bytes(path, MAX_TEXT_BYTES)
    try:
        return raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest()
    except UnicodeError as exc:
        raise ValueError(f"Expected a UTF-8 text file: {path}") from exc


def _local_path(value, parent):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Dataset paths must be nonempty local path strings.")
    value = value.strip()
    if (value.startswith(("//", "\\\\")) or "://" in value
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]+:", value)):
        raise ValueError(f"Network URLs and executable dataset paths are unsupported: {value}")
    result = Path(value).expanduser()
    return (result if result.is_absolute() else Path(parent) / result).resolve()


def _relative(path, root):
    return Path(os.path.relpath(path, root)).as_posix()


def _read_config(path, issues):
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("Reading dataset YAML requires PyYAML. Run setup/repair or import the images folder directly.") from exc
    text, _ = _read_text(path)
    try:
        # Aliases are unnecessary for a dataset declaration and can construct
        # recursive or exponentially expanded input even with a safe loader.
        for token in yaml.scan(text):
            if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)):
                raise ValueError("YAML anchors and aliases are unsupported; use a plain dataset declaration.")
        config = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError(f"Cannot read dataset YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("Dataset YAML must be a mapping with local train/val/test paths.")
    for field in ("download", "script", "scripts"):
        if field in config:
            issues.warning(f"The YAML {field!r} field is ignored. The importer never downloads or runs dataset code.")
    for field in ("kpt_shape", "flip_idx"):
        if field in config:
            raise ValueError("This is a pose dataset. Only five-column YOLO detection labels are supported.")
    if str(config.get("task", "detect")).lower() not in {"detect", "detection"}:
        raise ValueError("This importer supports detection datasets; segmentation, pose, and oriented boxes are unsupported.")
    names = {}
    declared = config.get("names")
    if declared is None:
        declared = {}
    if isinstance(declared, list):
        declared = dict(enumerate(declared))
    if not isinstance(declared, dict):
        raise ValueError("YAML names must be a list of class names or a mapping of integer IDs to names.")
    for key, name in declared.items():
        if isinstance(key, bool) or not re.fullmatch(r"(?:0|[1-9][0-9]*)", str(key)):
            raise ValueError("YAML class IDs must be nonnegative integers.")
        identifier = int(key)
        if identifier > 2**31 - 1 or identifier in names:
            raise ValueError("YAML contains a duplicate or out-of-range class ID.")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("YAML class names must contain 1 to 200 characters.")
        names[identifier] = name.strip()
    nc = config.get("nc")
    if nc is not None:
        if type(nc) is not int or not 1 <= nc <= 100_000:
            raise ValueError("YAML nc must be an integer from 1 to 100000.")
        if names and (len(names) != nc or set(names) != set(range(nc))):
            raise ValueError("YAML nc and names disagree; class IDs must cover 0 through nc - 1.")
        if not names:
            names = {index: f"class {index}" for index in range(nc)}
    if names and set(names) != set(range(len(names))):
        raise ValueError("YAML names must use consecutive class IDs starting at zero.")
    if "validation" in config:
        if config.get("val") and config["val"] != config["validation"]:
            raise ValueError("YAML contains conflicting val and validation paths. Keep one validation declaration.")
        if not config.get("val"):
            config["val"] = config["validation"]
            issues.warning("The YAML validation path is treated as val.")
    return config, names


def _walk_images(root):
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if not d.startswith(".") and d != "__pycache__")
        for name in sorted(files):
            path = Path(parent) / name
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                yield path.resolve()


def _inferred_split(path, anchor, default):
    parts = Path(_relative(path, anchor)).parts
    if len(parts) > 1 and parts[0].lower() in _SPLIT_NAMES:
        return _SPLIT_NAMES[parts[0].lower()]
    if anchor.name.lower() in _SPLIT_NAMES:
        return _SPLIT_NAMES[anchor.name.lower()]
    if anchor.name.lower() == "images" and anchor.parent.name.lower() in _SPLIT_NAMES:
        return _SPLIT_NAMES[anchor.parent.name.lower()]
    return default


def _collect(path, split, default_split, entries, issues):
    """Collect a directory, image, or local image-list file without execution."""
    path = Path(path).resolve()
    if path.is_dir():
        paths, anchor = _walk_images(path), path
    elif path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
        paths, anchor = [path], path.parent
    elif path.is_file() and path.suffix.lower() == ".txt":
        content, _ = _read_text(path)
        values = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if len(values) > MAX_IMAGES:
            raise ValueError(f"Image list exceeds {MAX_IMAGES} entries: {path}")
        paths, anchor = [], path.parent
        for line, value in enumerate(values, 1):
            candidate = _local_path(value, path.parent)
            if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_EXTENSIONS:
                issues.error(f"{path}, list entry {line}: image is missing or has an unsupported extension: {value}")
            else:
                paths.append(candidate)
    else:
        raise ValueError(f"Dataset image path is missing or unsupported: {path}")
    for image_path in paths:
        if len(entries) >= MAX_IMAGES:
            raise ValueError(f"Dataset exceeds the {MAX_IMAGES} image limit.")
        image_split = split or _inferred_split(image_path, anchor, default_split)
        key = str(image_path)
        if key in entries:
            if entries[key]["split"] != image_split:
                issues.error(f"The same source image occurs in multiple splits: {image_path}")
            continue
        entries[key] = {"path": image_path, "anchor": anchor, "split": image_split}


def _load_image(path):
    raw = _read_bytes(path, MAX_IMAGE_FILE_BYTES)
    with Image.open(io.BytesIO(raw)) as opened:
        width, height = opened.size
        if not (1 <= width <= 32768 and 1 <= height <= 32768 and width * height <= 100_000_000):
            raise ValueError("Images must be at most 32768 pixels per side and 100 million pixels total.")
        orientation = opened.getexif().get(274, 1)
        if orientation != 1:
            raise ValueError("Image has EXIF rotation/mirroring. Normalize the image and its labels together before importing; coordinates will not be guessed.")
        if getattr(opened, "n_frames", 1) != 1:
            raise ValueError("Multi-frame images are unsupported; export individual image frames first.")
        image = opened.convert("RGB")
    pixel_hash = hashlib.sha256(f"{width}x{height}:".encode() + image.tobytes()).hexdigest()
    return image, pixel_hash, hashlib.sha256(raw).hexdigest()


def _label_candidates(image_path, anchor, label_root, cache):
    if label_root is not None:
        relative = Path(_relative(image_path, anchor))
        if ".." in relative.parts:
            raise ValueError("A source image is outside the chosen image root. Select matching image_root and label_root directories for custom layouts.")
        candidates = [(label_root / relative).with_suffix(".txt")]
    else:
        parts = list(image_path.parts)
        positions = [i for i, part in enumerate(parts[:-1]) if part.lower() == "images"]
        candidates = []
        if positions:
            parts[positions[-1]] = "labels"
            candidates.append(Path(*parts).with_suffix(".txt"))
        candidates.append(image_path.with_suffix(".txt"))
    existing = []
    for candidate in candidates:
        parent = candidate.parent
        if parent not in cache:
            cache[parent] = list(parent.iterdir()) if parent.is_dir() else []
        matches = [p.resolve() for p in cache[parent]
                   if p.name.casefold() == candidate.name.casefold() and p.is_file()]
        existing.extend(matches)
    existing = sorted(set(existing))
    if len(existing) > 1:
        raise ValueError("Ambiguous labels: " + ", ".join(str(path) for path in existing))
    return existing[0] if existing else candidates[0].resolve(), bool(existing)


def _parse_labels(path, width, height, allow_confidence):
    content, digest = _read_text(path)
    result, seen = [], set()
    lines = content.splitlines()
    if len(lines) > MAX_LABEL_ROWS:
        raise ValueError(f"Label file exceeds {MAX_LABEL_ROWS} rows.")
    for number, raw in enumerate(lines, 1):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        fields = text.split()
        expected = {5, 6} if allow_confidence else {5}
        if len(fields) not in expected:
            extra = " Enable allow_confidence explicitly for six-column detection predictions." if len(fields) == 6 else ""
            raise ValueError(f"Line {number}: expected class cx cy width height (5 columns); got {len(fields)}. Segmentation, oriented boxes, and pose labels are unsupported.{extra}")
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)", fields[0]) or int(fields[0]) > 2**31 - 1:
            raise ValueError(f"Line {number}: class ID must be a nonnegative integer.")
        class_id = int(fields[0])
        try:
            values = [float(value) for value in fields[1:]]
        except ValueError as exc:
            raise ValueError(f"Line {number}: box coordinates must be numbers.") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Line {number}: coordinates and confidence must be finite.")
        cx, cy, bw, bh = values[:4]
        if not (-NORMALIZED_TOLERANCE <= cx <= 1 + NORMALIZED_TOLERANCE and
                -NORMALIZED_TOLERANCE <= cy <= 1 + NORMALIZED_TOLERANCE and
                0 < bw <= 1 + NORMALIZED_TOLERANCE and 0 < bh <= 1 + NORMALIZED_TOLERANCE):
            raise ValueError(f"Line {number}: YOLO centers must be normalized to [0, 1] and width/height to (0, 1] (1% boundary tolerance allowed). Use coordinates for the original image dimensions.")
        # A normalized center/size can describe a partially visible target.
        # Preserve its full rectangle and original normalized values, but use
        # only its visible intersection for the assistant and training. Clamp
        # even sub-EPS overshoots: the assistant requires exact image bounds.
        unclipped_box = [(cx - bw / 2) * width, (cy - bh / 2) * height,
                         (cx + bw / 2) * width, (cy + bh / 2) * height]
        box = [min(max(value, 0.0), float(limit))
               for value, limit in zip(unclipped_box, (width, height, width, height))]
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError(f"Line {number}: bounding-box coordinates have no visible area in this image after clipping to its edges. Correct this label or remove the row if the target is entirely outside the frame; images with no targets may have an empty or missing label file.")
        errors = validate_annotation({"class_id": 0, "box": box, "wrapper": []}, width, height, require_complete=False)
        if errors:
            raise ValueError(f"Line {number}: " + " ".join(errors))
        if len(values) == 5 and not 0 <= values[4] <= 1:
            raise ValueError(f"Line {number}: confidence must be in [0, 1].")
        key = (class_id, *values[:4])
        if key in seen:
            raise ValueError(f"Line {number}: duplicate class and bounding box in the same label file.")
        seen.add(key)
        item = {"class_id": class_id, "normalized_box": values[:4], "box": box, "line": number,
                "unclipped_box": unclipped_box, "clipped": box != unclipped_box}
        if len(values) == 5:
            item["confidence"] = values[4]
        result.append(item)
    return result, digest


def _group(path, dataset_root, split, mode, source_group):
    relative = _relative(path, dataset_root)
    if source_group:
        key = "explicit:" + source_group
    else:
        parts = list(Path(relative).parts)
        if parts and parts[0].lower() == "images":
            parts.pop(0)
        if parts and parts[0].lower() in _SPLIT_NAMES:
            parts.pop(0)
        if parts and parts[0].lower() == "images":
            parts.pop(0)
        parent = "/".join(parts[:-1])
        key = "directory:" + parent if mode == "directory" and parent else "image:" + relative
    dataset_key = hashlib.sha256(str(dataset_root).encode()).hexdigest()[:12]
    group_id = "yolo:" + dataset_key + ":" + hashlib.sha256(key.encode()).hexdigest()[:24]
    return group_id, key


def _plan_hash(plan):
    fields = {key: plan[key] for key in ("schema_version", "source", "options", "dataset_root", "classes", "images")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def scan_yolo(source, *, image_root=None, label_root=None, default_split="train",
              group_mode="directory", source_group=None, allow_confidence=False):
    """Read local detection labels and return a reviewable, serializable plan.

    ``source`` is a dataset folder, a data.yaml/dataset.yaml, an images folder,
    an image, or an image-list .txt. Explicit image_root/label_root directories
    use matching paths relative to image_root. No source files are modified.
    Errors are reported in the returned dict; no partial dataset is imported.
    """
    if default_split not in SPLITS:
        raise ValueError("default_split must be train, val, or test.")
    if group_mode not in {"directory", "image"}:
        raise ValueError("group_mode must be directory or image.")
    if source_group is not None and (not isinstance(source_group, str) or not source_group.strip() or len(source_group) > 160):
        raise ValueError("source_group must contain 1 to 160 characters.")
    if type(allow_confidence) is not bool:
        raise ValueError("allow_confidence must be a boolean.")
    issues = _Issues()
    source_path = Path(source).expanduser().resolve()
    image_root = Path(image_root).expanduser().resolve() if image_root is not None else None
    label_root = Path(label_root).expanduser().resolve() if label_root is not None else None
    options = {"image_root": str(image_root) if image_root else None,
               "label_root": str(label_root) if label_root else None,
               "default_split": default_split, "group_mode": group_mode,
               "source_group": source_group.strip() if source_group else None,
               "allow_confidence": allow_confidence}
    dataset_root = source_path if source_path.is_dir() else source_path.parent
    entries, names, images = {}, {}, []
    try:
        config_path = source_path if source_path.suffix.lower() in {".yaml", ".yml"} and source_path.is_file() else None
        if source_path.is_dir() and image_root is None:
            configs = [p for p in (source_path / "data.yaml", source_path / "dataset.yaml",
                                   source_path / "data.yml", source_path / "dataset.yml") if p.is_file()]
            if len(configs) > 1:
                raise ValueError("Multiple dataset YAML files were found. Choose the intended YAML file explicitly.")
            config_path = configs[0] if configs else None
        if label_root is not None and not label_root.is_dir():
            raise ValueError(f"Label root is not a directory: {label_root}")
        if config_path:
            config, names = _read_config(config_path, issues)
            configured_root = config.get("path")
            dataset_root = _local_path("." if configured_root in (None, "") else configured_root, config_path.parent)
            if not dataset_root.is_dir():
                raise ValueError(f"Dataset YAML path does not exist: {dataset_root}")
        else:
            config = None
        if image_root is not None:
            if not image_root.is_dir():
                raise ValueError(f"Image root is not a directory: {image_root}")
            _collect(image_root, None, default_split, entries, issues)
        elif config is not None:
            for split in SPLITS:
                values = config.get(split)
                if values is None or values == "":
                    continue
                values = values if isinstance(values, list) else [values]
                for value in values:
                    resolved = _local_path(value, dataset_root)
                    # Some exported dataset YAMLs retain a parent prefix from
                    # their original archive layout. A local alternative is
                    # used only when the literal path is absent, and reported.
                    if not resolved.exists() and value.startswith("../"):
                        local_alternative = _local_path(value[3:], dataset_root)
                        if local_alternative.exists():
                            resolved = local_alternative
                            issues.warning(f"YAML {split}: {value!r} does not exist; using the local exported path {_relative(resolved, dataset_root)!r}.")
                    _collect(resolved, split, default_split, entries, issues)
            if not entries:
                raise ValueError("Dataset YAML contains no usable local train/val/test images.")
        elif source_path.is_dir() and (source_path / "images").is_dir():
            _collect(source_path / "images", None, default_split, entries, issues)
        elif source_path.is_dir() and any((source_path / name / "images").is_dir() for name in _SPLIT_NAMES):
            for name, split in _SPLIT_NAMES.items():
                if (source_path / name / "images").is_dir():
                    _collect(source_path / name / "images", split, default_split, entries, issues)
        else:
            _collect(source_path, None, default_split, entries, issues)
    except (OSError, ValueError, RecursionError) as exc:
        issues.error(str(exc))
    if not entries and not issues.errors:
        issues.error("No supported images were found. Choose the dataset or its images folder.")

    cache, used_labels, seen_pixels, group_splits, counts = {}, {}, {}, {}, {}
    for key in sorted(entries):
        item = entries[key]
        path, split, anchor = item["path"], item["split"], item["anchor"]
        try:
            image, pixel_hash, file_hash = _load_image(path)
            width, height = image.size
            image.close()
            label, exists = _label_candidates(path, image_root or anchor, label_root, cache)
            if exists and label in used_labels and used_labels[label] != path:
                raise ValueError(f"The label {label} matches more than one image ({used_labels[label]} and {path}); remove duplicate stems/extensions or separate their directories.")
            if exists:
                used_labels[label] = path
                boxes, label_hash = _parse_labels(label, width, height, allow_confidence)
                state = "labeled" if boxes else "empty"
            else:
                boxes, label_hash, state = [], None, "missing"
            if names and any(box["class_id"] not in names for box in boxes):
                unknown = sorted({box["class_id"] for box in boxes if box["class_id"] not in names})
                raise ValueError(f"Label class IDs {unknown} are not declared by dataset YAML names/nc.")
            group_id, group_key = _group(path, dataset_root, split, group_mode, source_group)
            entry = {"source_image": str(path), "source_label": str(label),
                     "relative_image": _relative(path, dataset_root),
                     "relative_label": _relative(label, dataset_root),
                     "width": width, "height": height, "split": split,
                     "group_id": group_id, "group_key": group_key,
                     "pixel_sha256": pixel_hash, "image_file_sha256": file_hash,
                     "label_sha256": label_hash, "label_state": state, "boxes": boxes}
            previous = seen_pixels.get(pixel_hash)
            if previous is not None:
                if previous["split"] != split:
                    raise ValueError(f"Identical image pixels occur in {previous['split']} and {split}: {previous['source_image']} and {path}. Resolve this split leakage before importing.")
                signature = lambda value: sorted((box["class_id"], *box["normalized_box"]) for box in value["boxes"])
                if signature(previous) != signature(entry):
                    raise ValueError(f"Identical image pixels have conflicting labels: {previous['source_image']} and {path}.")
                entry["duplicate_of"] = previous["source_image"]
                issues.warning(f"Exact duplicate will be kept once: {entry['relative_image']}")
            else:
                seen_pixels[pixel_hash] = entry
            if group_id in group_splits and group_splits[group_id] != split:
                raise ValueError(f"Source group {group_key!r} spans multiple splits. Keep a recording/session in one split or choose image grouping for independent stills.")
            group_splits[group_id] = split
            for box in boxes:
                counts[box["class_id"]] = counts.get(box["class_id"], 0) + 1
            images.append(entry)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            issues.error(f"{path}: {exc}")

    clipped_boxes = sum(box["clipped"] for image in images for box in image["boxes"])
    if clipped_boxes:
        issues.warning(f"{clipped_boxes} box(es) extend beyond the image edges and will be clipped to their visible area. Original YOLO coordinates are preserved; source files stay unchanged.")
    for state in ("missing", "empty"):
        count = sum(image["label_state"] == state for image in images)
        if count:
            issues.warning(f"{count} image(s) have {state} labels and will be imported with no boxes. This is valid for negative images. They remain unreviewed; press X on an empty image to approve it as a negative and advance.")
    mapped_duplicates = sum(len(image["boxes"]) - len({tuple(box["box"]) for box in image["boxes"]})
                            for image in images if "duplicate_of" not in image)
    if mapped_duplicates:
        issues.warning(f"There are {mapped_duplicates} duplicate rectangle(s) across source rows when all classes map to player. Selected aliases with exactly equal visible boxes are kept once with all original class provenance.")
    class_ids = sorted(set(names) | set(counts))
    classes = [{"id": identifier, "name": names.get(identifier, f"class {identifier}"),
                "boxes": counts.get(identifier, 0)} for identifier in class_ids]
    summary = {"images": len(images), "boxes": sum(len(image["boxes"]) for image in images),
               "unique_images": len({image["pixel_sha256"] for image in images}),
               "split_counts": {split: sum(image["split"] == split for image in images) for split in SPLITS},
               "missing_labels": sum(image["label_state"] == "missing" for image in images),
               "empty_labels": sum(image["label_state"] == "empty" for image in images),
               "clipped_boxes": clipped_boxes,
               "mapped_duplicate_boxes_all_classes": mapped_duplicates,
               "duplicates": sum("duplicate_of" in image for image in images),
               "error_count": issues.error_count, "warning_count": issues.warning_count}
    plan = {"schema_version": PLAN_VERSION, "source": str(source_path), "options": options,
            "dataset_root": str(dataset_root), "class_ids": class_ids, "classes": classes,
            "images": images, "errors": issues.errors, "warnings": issues.warnings, "summary": summary}
    plan["plan_sha256"] = _plan_hash(plan)
    return plan


def _selected(plan, selected_classes):
    known = set(plan["class_ids"])
    if selected_classes is None:
        if len(known) > 1:
            raise ValueError("This dataset has multiple YOLO classes. Select the target class IDs explicitly; selected classes map to Cloud Studio class 0 (player).")
        return sorted(known)
    if type(selected_classes) is int:
        selected_classes = [selected_classes]
    if not isinstance(selected_classes, (list, tuple, set)) or any(type(value) is not int for value in selected_classes):
        raise ValueError("selected_classes must be a list of integer YOLO class IDs.")
    selected = sorted(set(selected_classes))
    if known and not selected:
        raise ValueError("Select at least one YOLO target class.")
    if not set(selected).issubset(known):
        raise ValueError(f"Selected class IDs are not present or declared in this dataset: {sorted(set(selected) - known)}")
    return selected


def import_yolo(project, source_or_plan, *, selected_classes=None, **scan_options):
    """Import all valid planned images in one transaction, without auto-review.

    Existing pixel-identical project images are reported and left unchanged,
    including their boxes, masks, splits, and review state. Importing an edited
    label file a second time therefore never overwrites a user's corrections.
    """
    if not isinstance(project, Project):
        raise TypeError("project must be an open Cloud Studio Project.")
    if isinstance(source_or_plan, dict):
        if scan_options:
            raise ValueError("Pass scan options to scan_yolo before importing its plan.")
        preview = source_or_plan
        if preview.get("schema_version") != PLAN_VERSION:
            raise ValueError("Unsupported YOLO import plan version.")
        if preview.get("errors") or preview.get("summary", {}).get("error_count", 0):
            raise ValueError("YOLO preflight failed: " + "; ".join(preview.get("errors", [])[:10]))
        try:
            if preview.get("plan_sha256") != _plan_hash(preview):
                raise ValueError("YOLO import preview was modified. Scan the source dataset again.")
            plan = scan_yolo(preview["source"], **preview["options"])
        except (KeyError, TypeError) as exc:
            raise ValueError("Malformed YOLO import plan; scan the source dataset again.") from exc
        if plan["errors"] or plan["plan_sha256"] != preview["plan_sha256"]:
            detail = "; ".join(plan["errors"][:3])
            raise ValueError("YOLO source data or import rules changed after the preview. Scan again before importing." + (" " + detail if detail else ""))
    else:
        plan = scan_yolo(source_or_plan, **scan_options)
    if plan["errors"]:
        raise ValueError("YOLO preflight failed: " + "; ".join(plan["errors"][:10]))
    selected = _selected(plan, selected_classes)
    names = {item["id"]: item["name"] for item in plan["classes"]}
    warning_messages = list(plan["warnings"])
    if len(selected) > 1:
        warning_messages.append(f"Explicitly selected classes {selected} are merged into the single player class; original IDs remain in YOLO provenance.")
    selected_set = set(selected)
    result = {"image_ids": [], "images_added": 0, "boxes_added": 0, "clipped_boxes": 0, "images_skipped": 0,
              "skipped_existing": 0, "skipped_duplicates": 0, "excluded_boxes": 0,
              "duplicate_boxes_merged": 0,
              "selected_classes": selected, "class_mapping": [{"from": value, "name": names[value], "to": 0} for value in selected],
              "review_required": True, "warnings": warning_messages}
    with project._lock:
        existing, groups = {}, {}
        for record in project.manifest["images"]:
            digest = record.get("pixel_sha256")
            if not digest:
                # Older schema-1 records can omit the optional digest. Do not
                # duplicate their annotations merely because metadata is old.
                current_image, digest, _ = _load_image(project.image_path(record))
                current_image.close()
            if digest in existing and existing[digest]["split"] != record["split"]:
                raise ValueError("The existing project already contains identical pixels in multiple splits. Resolve its split conflict before importing.")
            if record["group_id"] in groups and groups[record["group_id"]] != record["split"]:
                raise ValueError("The existing project already contains a source group in multiple splits. Resolve its source-group split conflict before importing.")
            existing[digest] = record
            groups[record["group_id"]] = record["split"]
        pending, seen = [], set()
        for entry in plan["images"]:
            digest = entry["pixel_sha256"]
            if digest in existing:
                current = existing[digest]
                if current["split"] != entry["split"]:
                    raise ValueError(f"Existing image {current['file']} is in {current['split']}, but the YOLO dataset places identical pixels in {entry['split']}. Resolve the split conflict before importing.")
                result["images_skipped"] += 1
                result["skipped_existing"] += 1
                continue
            if digest in seen:
                result["images_skipped"] += 1
                result["skipped_duplicates"] += 1
                continue
            if entry["group_id"] in groups and groups[entry["group_id"]] != entry["split"]:
                raise ValueError("An existing source group would span multiple splits. Resolve the source-group split conflict before importing.")
            groups[entry["group_id"]] = entry["split"]
            seen.add(digest)
            pending.append(entry)
        if result["skipped_existing"]:
            warning_messages.append(f"{result['skipped_existing']} existing image(s) were left unchanged. Import into a new project if you want to replace their original YOLO labels.")
        if not pending:
            return result
        before = copy.deepcopy(project.manifest)
        moved = []
        records = []
        stamp = utc_now()
        with tempfile.TemporaryDirectory(prefix=".yolo-import-", dir=project.root) as temporary:
            try:
                for entry in pending:
                    image, pixel_hash, file_hash = _load_image(entry["source_image"])
                    if pixel_hash != entry["pixel_sha256"] or file_hash != entry["image_file_sha256"]:
                        image.close()
                        raise ValueError("A source image changed during import. Scan and import again.")
                    identifier = uuid4().hex
                    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(entry["source_image"]).stem).strip("_")[:60] or "image"
                    filename = f"{stem}_{identifier[:12]}.png"
                    image.save(Path(temporary) / filename, format="PNG")
                    image.close()
                    annotations, mapped_boxes = [], {}
                    for box in entry["boxes"]:
                        if box["class_id"] not in selected_set:
                            result["excluded_boxes"] += 1
                            continue
                        provenance = {"original_class_id": box["class_id"], "original_class_name": names[box["class_id"]],
                                      "normalized_box": list(box["normalized_box"]), "source_line": box["line"],
                                      "unclipped_box": list(box["unclipped_box"]), "clipped": box["clipped"],
                                      "label_file": entry["relative_label"], "label_sha256": entry["label_sha256"]}
                        if "confidence" in box:
                            provenance["confidence"] = box["confidence"]
                        # Selected aliases such as player / verified player
                        # share class 0. Deduplicate only numerically identical
                        # resulting rectangles, never nearby/overlapping people.
                        key = tuple(box["box"])
                        previous = mapped_boxes.get(key)
                        if previous is not None:
                            original = previous["yolo"]
                            if "merged_sources" not in original:
                                original["merged_sources"] = [copy.deepcopy(original)]
                            original["merged_sources"].append(provenance)
                            original["original_class_ids"] = sorted({item["original_class_id"] for item in original["merged_sources"]})
                            original["original_class_names"] = [names[value] for value in original["original_class_ids"]]
                            result["duplicate_boxes_merged"] += 1
                            continue
                        annotation = {"id": uuid4().hex, "class_id": 0, "box": list(box["box"]),
                                      "wrapper": [], "source": "yolo", "box_source": "yolo", "yolo": provenance}
                        annotations.append(annotation)
                        mapped_boxes[key] = annotation
                    records.append({"id": identifier, "file": "images/" + filename,
                                    "width": entry["width"], "height": entry["height"],
                                    "split": entry["split"], "group_id": entry["group_id"],
                                    "reviewed": False, "annotations": annotations,
                                    "pixel_sha256": pixel_hash, "added_at": stamp,
                                    "yolo": {"dataset": Path(plan["dataset_root"]).name,
                                             "relative_image": entry["relative_image"], "relative_label": entry["relative_label"],
                                             "label_state": entry["label_state"], "label_sha256": entry["label_sha256"],
                                             "image_file_sha256": file_hash, "group_key": entry["group_key"],
                                             "original_split": entry["split"], "selected_class_ids": selected,
                                             "source_class_ids": sorted({box["class_id"] for box in entry["boxes"]}),
                                             "imported_at": stamp}})
                # Check labels again before the single commit; a changed box
                # file must never be attached to an already-scanned image.
                for entry in pending:
                    label_path = Path(entry["source_label"])
                    if entry["label_sha256"] is None:
                        if label_path.exists():
                            raise ValueError("A source label appeared during import. Scan and import again.")
                    elif _read_text(label_path)[1] != entry["label_sha256"]:
                        raise ValueError("A source label changed during import. Scan and import again.")
                for record in records:
                    destination = project.root / record["file"]
                    if destination.exists():
                        raise FileExistsError(f"Refusing to overwrite project image: {destination}")
                    os.replace(Path(temporary) / destination.name, destination)
                    moved.append(destination)
                project.manifest["images"].extend(records)
                project.save()
            except BaseException:
                project.manifest.clear()
                project.manifest.update(before)
                for destination in moved:
                    destination.unlink(missing_ok=True)
                raise
        result["image_ids"] = [record["id"] for record in records]
        result["images_added"] = len(records)
        result["boxes_added"] = sum(len(record["annotations"]) for record in records)
        result["clipped_boxes"] = sum(ann["yolo"]["clipped"] for record in records for ann in record["annotations"])
        if result["duplicate_boxes_merged"]:
            warning_messages.append(f"Merged {result['duplicate_boxes_merged']} duplicate source box(es) after mapping selected classes to player. All original class IDs, names, lines and coordinates remain in annotation provenance; merely overlapping boxes stay separate.")
    return result
