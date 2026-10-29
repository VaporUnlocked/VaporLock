"""Portable local projects with explicit review gates and atomic writes."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile
import threading
from datetime import datetime, timezone
from uuid import uuid4

from PIL import Image, ImageOps

from .geometry import validate_annotation
from .masks import has_encasing

SCHEMA_VERSION = 2
READABLE_SCHEMAS = (1, 2)
MANIFEST_NAME = "project.json"
SPLITS = ("train", "val", "test")
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write(path, content: bytes):
    """Write beside destination, fsync, and replace without a partial final file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_json(path, value):
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    atomic_write(path, content.encode("utf-8"))


class Project:
    def __init__(self, root, manifest, disk_digest=None):
        self.root = Path(root).resolve()
        self.manifest = manifest
        self._disk_digest = disk_digest
        self._lock = threading.RLock()
        self._store = None
        self._store_revision = None
        self._record_index = {}
        self._index_rows = None
        self._index_length = -1

    @classmethod
    def create(cls, path, name="Untitled"):
        root = Path(path).expanduser().resolve()
        if (root / MANIFEST_NAME).exists():
            raise FileExistsError(f"A project already exists in {root}. Open it instead.")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Project name must contain 1 to 200 characters.")
        root.mkdir(parents=True, exist_ok=True)
        (root / "images").mkdir(exist_ok=True)
        project = cls(root, {"schema_version": SCHEMA_VERSION, "name": name.strip(),
                            "classes": ["player"], "created_at": utc_now(),
                            "updated_at": utc_now(), "images": []})
        project.save()
        return project

    @classmethod
    def open(cls, path):
        root = Path(path).expanduser().resolve()
        if root.is_file():
            if root.name != MANIFEST_NAME:
                raise ValueError("Choose a Cloud Studio project folder or its project.json file.")
            root = root.parent
        manifest_path = root / MANIFEST_NAME
        if not manifest_path.is_file():
            raise FileNotFoundError(f"No {MANIFEST_NAME} in {root}.")
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("Project manifest exceeds the 64 MiB limit.")
        raw = manifest_path.read_bytes()
        try:
            manifest = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("Project JSON is damaged. A previous save may be in project.json.bak.") from exc
        project = cls(root, manifest, hashlib.sha256(raw).hexdigest())
        if isinstance(manifest, dict) and "storage" in manifest:
            from .project_store import FORMAT, ProjectStore
            if (manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("storage") != FORMAT
                    or set(manifest) != {"schema_version", "storage", "database", "project_id"}
                    or not isinstance(manifest.get("project_id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", manifest["project_id"])
                    or manifest.get("database") != f"project-data-{manifest['project_id']}.sqlite3"):
                raise ValueError("Unsupported project storage descriptor.")
            database = (root / manifest["database"]).resolve()
            if database.parent != root:
                raise ValueError("Project database must be inside the project folder.")
            project._store = ProjectStore(database, manifest["project_id"])
            project.manifest, project._store_revision = project._store.load()
        errors = project._structure_errors()
        if errors:
            raise ValueError("Invalid project: " + "\n".join(errors[:20]))
        return project

    @property
    def path(self):
        return self.root / MANIFEST_NAME

    def _structure_errors(self):
        errors = []
        manifest = self.manifest
        if not isinstance(manifest, dict):
            return ["Manifest must be a JSON object."]
        if manifest.get("schema_version") not in READABLE_SCHEMAS:
            errors.append("Unsupported project schema version.")
        if manifest.get("classes") != ["player"]:
            errors.append("This version supports only the player class.")
        images = manifest.get("images")
        if not isinstance(images, list):
            return errors + ["images must be an array."]
        ids, paths = set(), set()
        for index, record in enumerate(images):
            prefix = f"Image {index + 1}"
            if not isinstance(record, dict):
                errors.append(f"{prefix}: record must be an object.")
                continue
            identifier = record.get("id")
            if not isinstance(identifier, str) or not identifier or identifier in ids:
                errors.append(f"{prefix}: missing or duplicate ID.")
            elif len(identifier) > 128:
                errors.append(f"{prefix}: ID too long.")
            else:
                ids.add(identifier)
            file = record.get("file")
            if not isinstance(file, str) or file in paths:
                errors.append(f"{prefix}: missing or duplicate image path.")
            else:
                paths.add(file)
                try:
                    self.image_path(record)
                except (ValueError, OSError):
                    errors.append(f"{prefix}: image path leaves the project folder.")
            if record.get("split") not in SPLITS:
                errors.append(f"{prefix}: split must be train, val, or test.")
            if not isinstance(record.get("group_id"), str) or not record["group_id"]:
                errors.append(f"{prefix}: capture/source group is missing.")
            if not isinstance(record.get("reviewed"), bool):
                errors.append(f"{prefix}: reviewed must be true or false.")
            if not isinstance(record.get("annotations"), list):
                errors.append(f"{prefix}: annotations must be an array.")
            for key in ("width", "height"):
                value = record.get(key)
                if type(value) is not int or not 1 <= value <= 32768:
                    errors.append(f"{prefix}: {key} must be 1 to 32768 pixels.")
        return errors

    @property
    def change_token(self):
        """Cheap logical revision for stale import/merge plans."""
        return (self._disk_digest, self._store_revision)

    def _check_descriptor(self):
        if not self.path.is_file():
            if self._disk_digest is not None:
                raise RuntimeError("The project manifest was removed externally. Reopen or restore it before saving.")
            return None
        raw = self.path.read_bytes()
        if self._disk_digest is None:
            raise FileExistsError("Project appeared during creation; refusing to overwrite it.")
        if hashlib.sha256(raw).hexdigest() != self._disk_digest:
            raise RuntimeError("The project changed in another window or editor. Reopen it before saving.")
        return raw

    def _initialize_store(self):
        from .project_store import migration_lock
        with migration_lock(self.root):
            self._initialize_store_locked()

    def _initialize_store_locked(self):
        """One-time upgrade; the old complete JSON remains a recovery backup."""
        from .project_store import FORMAT, ProjectStore
        old_raw = self._check_descriptor()
        previous_schema = self.manifest.get("schema_version")
        identifier = uuid4().hex
        database = self.root / f"project-data-{identifier}.sqlite3"
        store = ProjectStore(database, identifier)
        descriptor = {"schema_version": SCHEMA_VERSION, "storage": FORMAT,
                      "database": database.name, "project_id": identifier}
        self.manifest["schema_version"] = SCHEMA_VERSION
        try:
            store.initialize(self.manifest)
            # A newly created project has an empty logical checkpoint. Existing
            # v1 data is kept byte-for-byte, including all reviewed labels.
            backup = old_raw or (json.dumps(self.manifest, ensure_ascii=False, allow_nan=False,
                                            separators=(",", ":")) + "\n").encode()
            atomic_write(self.root / (MANIFEST_NAME + ".bak"), backup)
            raw = (json.dumps(descriptor, indent=2) + "\n").encode()
            self._check_descriptor()
            atomic_write(self.path, raw)
        except BaseException:
            self.manifest["schema_version"] = previous_schema
            database.unlink(missing_ok=True)
            Path(str(database) + "-journal").unlink(missing_ok=True)
            raise
        self._store, self._store_revision = store, 0
        self._disk_digest = hashlib.sha256(raw).hexdigest()

    def enable_fast_saves(self):
        """Upgrade once while opening for editing, before the first Enter."""
        with self._lock:
            if self._store is None:
                errors = self._structure_errors()
                if errors:
                    raise ValueError("Cannot save invalid project: " + "; ".join(errors[:10]))
                self._initialize_store()

    def save(self):
        """Bulk structural save. Ordinary annotation edits use _save_records."""
        with self._lock:
            errors = self._structure_errors()
            if errors:
                raise ValueError("Cannot save invalid project: " + "; ".join(errors[:10]))
            old_updated = self.manifest.get("updated_at")
            self.manifest["updated_at"] = utc_now()
            try:
                if self._store is None:
                    self._initialize_store()
                else:
                    self._check_descriptor()
                    self._store_revision = self._store.commit(self.manifest, self._store_revision)
            except BaseException:
                self.manifest["updated_at"] = old_updated
                raise
            self._index_rows = None

    def _save_records(self, records):
        with self._lock:
            if self._store is None:
                # APIs opening a legacy project read-only do not migrate it.
                # Their first explicit mutation performs the same safe upgrade.
                self.save()
                return
            self._check_descriptor()
            old_updated = self.manifest.get("updated_at")
            self.manifest["updated_at"] = utc_now()
            try:
                self._store_revision = self._store.commit(self.manifest, self._store_revision, records=records)
            except BaseException:
                self.manifest["updated_at"] = old_updated
                raise

    def record(self, image_id):
        rows = self.manifest["images"]
        if self._index_rows is not rows or self._index_length != len(rows):
            self._record_index = {item["id"]: item for item in rows}
            self._index_rows, self._index_length = rows, len(rows)
        try:
            return self._record_index[image_id]
        except KeyError:
            raise KeyError(f"No image with ID {image_id}.") from None

    def image_path(self, record_or_id):
        record = self.record(record_or_id) if isinstance(record_or_id, str) else record_or_id
        raw = record.get("file")
        if not isinstance(raw, str) or not raw or "\\" in raw:
            raise ValueError("Image path must use portable forward slashes.")
        relative = Path(raw)
        if relative.is_absolute() or any(part in ("..",) for part in relative.parts) or ":" in raw:
            raise ValueError("Image path must remain inside the project.")
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError("Image path must remain inside the project.")
        return resolved

    def add_image(self, path, split="train", group_id=None):
        source = Path(path).expanduser().resolve()
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            record = self.add_pil(image, source.stem, split, group_id)
        return record

    def add_pil(self, image, name="capture", split="train", group_id=None):
        if split not in SPLITS:
            raise ValueError("Split must be train, val, or test.")
        image = ImageOps.exif_transpose(image).convert("RGB")
        width, height = image.size
        if not 1 <= width <= 32768 or not 1 <= height <= 32768 or width * height > 100_000_000:
            raise ValueError("Images must be at most 32768 pixels per side and 100 million pixels total.")
        # The pixel digest detects repeats even if JPEG/PNG container metadata differs.
        digest = hashlib.sha256(f"{width}x{height}:".encode() + image.tobytes()).hexdigest()
        with self._lock:
            for existing in self.manifest["images"]:
                if existing.get("pixel_sha256") == digest:
                    return existing
            identifier = uuid4().hex
            safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name)).strip("_")[:60] or "image"
            file = f"images/{safe_name}_{identifier[:12]}.png"
            destination = self.root / file
            destination.parent.mkdir(parents=True, exist_ok=True)
            import io
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            atomic_write(destination, buffer.getvalue())
            record = {"id": identifier, "file": file, "width": width, "height": height,
                      "split": split, "group_id": str(group_id or identifier),
                      "reviewed": False, "annotations": [], "pixel_sha256": digest,
                      "added_at": utc_now()}
            self.manifest["images"].append(record)
            try:
                self.save()
            except BaseException:
                self.manifest["images"].remove(record)
                # Only remove this newly created internal image on failed import.
                destination.unlink(missing_ok=True)
                raise
            return record

    def set_annotations(self, image_id, annotations, reviewed=False):
        if not isinstance(annotations, list):
            raise ValueError("Annotations must be a list.")
        record = self.record(image_id)
        replacement = copy.deepcopy(annotations)
        seen = set()
        for index, ann in enumerate(replacement):
            if not isinstance(ann, dict):
                raise ValueError(f"Instance {index + 1} must be an annotation object.")
            ann.setdefault("id", uuid4().hex)
            ann.setdefault("class_id", 0)
            ann.setdefault("source", "manual")
            if not isinstance(ann["id"], str) or not ann["id"] or ann["id"] in seen:
                raise ValueError("Each detection needs a unique, nonempty annotation ID.")
            seen.add(ann["id"])
            errors = validate_annotation(ann, record["width"], record["height"], require_complete=bool(reviewed))
            if errors:
                raise ValueError(f"Instance {index + 1}: " + " ".join(errors))
        old_annotations, old_reviewed = record["annotations"], record["reviewed"]
        with self._lock:
            record["annotations"], record["reviewed"] = replacement, bool(reviewed)
            try:
                self._save_records([record])
            except BaseException:
                record["annotations"], record["reviewed"] = old_annotations, old_reviewed
                raise

    def set_reviewed(self, image_id, reviewed=True):
        record = self.record(image_id)
        self.set_annotations(image_id, record["annotations"], reviewed=reviewed)

    def set_split(self, image_id, split):
        """Change the whole capture group to keep consecutive frames together."""
        if split not in SPLITS:
            raise ValueError("Split must be train, val, or test.")
        group = self.record(image_id)["group_id"]
        affected = [(r, r["split"]) for r in self.manifest["images"] if r["group_id"] == group]
        for record, _ in affected:
            record["split"] = split
        try:
            self._save_records([record for record, _ in affected])
        except BaseException:
            for record, old in affected:
                record["split"] = old
            raise

    def set_group(self, image_id, group_id):
        """Assign related stills/frames to one source group before splitting."""
        if not isinstance(group_id, str) or not group_id.strip() or len(group_id) > 200:
            raise ValueError("Group ID must be 1 to 200 characters.")
        record = self.record(image_id)
        other = next((r for r in self.manifest["images"] if r["group_id"] == group_id and r["id"] != image_id), None)
        old = record["group_id"], record["split"]
        record["group_id"] = group_id.strip()
        if other:
            record["split"] = other["split"]
        try:
            self._save_records([record])
        except BaseException:
            record["group_id"], record["split"] = old
            raise

    def assign_splits(self, val_fraction=.2, seed=42):
        if not math.isfinite(val_fraction) or not 0 < val_fraction < 1:
            raise ValueError("Validation fraction must be between 0 and 1.")
        groups = sorted({r["group_id"] for r in self.manifest["images"] if r["split"] != "test"})
        if len(groups) < 2:
            raise ValueError("Need at least two independent source groups for train/validation. Capture another session or import unrelated images.")
        random.Random(seed).shuffle(groups)
        n_val = max(1, min(len(groups) - 1, round(len(groups) * val_fraction)))
        val_groups = set(groups[:n_val])
        old = [r["split"] for r in self.manifest["images"]]
        for record in self.manifest["images"]:
            if record["split"] != "test":
                record["split"] = "val" if record["group_id"] in val_groups else "train"
        try:
            self.save()
        except BaseException:
            for record, split in zip(self.manifest["images"], old):
                record["split"] = split
            raise
        return self.summary()

    def training_records(self, split):
        if split not in SPLITS:
            raise ValueError("Split must be train, val, or test.")
        result = []
        for record in self.manifest["images"]:
            if record["split"] == split and record["reviewed"]:
                for ann in record["annotations"]:
                    errors = validate_annotation(ann, record["width"], record["height"])
                    if errors:
                        raise ValueError(f"Reviewed image {record['id']} has invalid labels: {' '.join(errors)}")
                result.append(copy.deepcopy(record))
        return result

    def summary(self):
        images = self.manifest["images"]
        split_counts = {s: sum(r["split"] == s for r in images) for s in SPLITS}
        reviewed_counts = {s: sum(r["split"] == s and r["reviewed"] for r in images) for s in SPLITS}
        reviewed = sum(r["reviewed"] for r in images)
        annotations = [ann for record in images for ann in record["annotations"]]
        return {"name": self.manifest["name"], "images": len(images), "image_count": len(images),
                "total": len(images), "reviewed": reviewed, "unreviewed": len(images) - reviewed,
                "instances": len(annotations), "complete_instances": sum(has_encasing(a) for a in annotations),
                "negative_images": sum(r["reviewed"] and not r["annotations"] for r in images),
                "split_counts": split_counts, "reviewed_split_counts": reviewed_counts,
                "groups": len({r["group_id"] for r in images}),
                "splits": {s: {"images": split_counts[s], "reviewed": reviewed_counts[s]} for s in SPLITS}}

    def validate(self):
        errors = self._structure_errors()
        if errors:
            return errors
        groups, hashes = {}, {}
        for record in self.manifest["images"]:
            prefix = f"{record['file']}"
            path = self.image_path(record)
            if not path.is_file():
                errors.append(f"{prefix}: image file is missing.")
            else:
                try:
                    with Image.open(path) as image:
                        image.verify()
                    with Image.open(path) as image:
                        if image.size != (record["width"], record["height"]):
                            errors.append(f"{prefix}: stored dimensions differ from the image.")
                except (OSError, ValueError) as exc:
                    errors.append(f"{prefix}: unreadable image ({exc}).")
            for index, ann in enumerate(record["annotations"]):
                errors.extend(f"{prefix}, instance {index + 1}: {message}" for message in
                              validate_annotation(ann, record["width"], record["height"], require_complete=record["reviewed"]))
            groups.setdefault(record["group_id"], set()).add(record["split"])
            if record.get("pixel_sha256"):
                hashes.setdefault(record["pixel_sha256"], set()).add(record["split"])
        for group, splits in groups.items():
            if len(splits) > 1:
                errors.append(f"Source group {group} is in multiple splits; keep related frames together.")
        if any(len(splits) > 1 for splits in hashes.values()):
            errors.append("Identical image pixels occur in multiple splits.")
        return errors
