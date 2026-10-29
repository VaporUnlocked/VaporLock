"""Merge saved Cloud projects without duplicating pixel-identical images.

Preflight is read-only and JSON serializable. A merge repeats preflight before
one bulk Project.save transaction. Label conflicts use an explicit whole-image
policy; overlapping targets are never unioned or deduplicated approximately.
Source files and the source project are never changed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from .data import Project, SPLITS, utc_now
from .geometry import validate_annotation
from .masks import decode_mask, encode_mask
from .yolo import MAX_IMAGE_FILE_BYTES, _load_image, _read_bytes


PLAN_VERSION = 1
MAX_IMAGES = 100_000
MAX_ISSUES = 200


def _digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _plan_hash(plan):
    return _digest({key: value for key, value in plan.items() if key != "plan_sha256"})


def _change_token(project):
    token = getattr(project, "change_token", None)
    if callable(token):
        token = token()
    if token is None:
        token = getattr(project, "_disk_digest", None)
    # Tokens are comparison hints, not a dependency on the storage backend.
    return str(token) if token is not None else None


def _canonical_polygon(points):
    values = [tuple(float(value) for value in point) for point in points]
    if not values:
        return []
    if len(values) > 1 and values[0] == values[-1]:
        values = values[:-1]
    first = min(range(len(values)), key=lambda index: values[index])
    forward = values[first:] + values[:first]
    reverse = [forward[0], *reversed(forward[1:])]
    return min(forward, reverse)


def _label_signature(record):
    """Compare actual supervised labels, ignoring IDs and provenance fields."""
    values = []
    for annotation in record["annotations"]:
        value = {"class_id": annotation.get("class_id", 0),
                 "box": [float(coordinate) for coordinate in annotation["box"]]}
        if annotation.get("mask") is not None:
            mask = annotation["mask"]
            value["mask"] = encode_mask(decode_mask(mask, full=False), origin=tuple(mask["origin"]),
                                        image_size=(record["width"], record["height"]))
        else:
            value["wrapper"] = _canonical_polygon(annotation.get("wrapper", []))
        values.append(_digest(value))
    return _digest({"reviewed": record["reviewed"], "annotations": sorted(values)})


class _Groups:
    def __init__(self):
        self.parent = {}

    def root(self, value):
        self.parent.setdefault(value, value)
        current = value
        while self.parent[current] != current:
            current = self.parent[current]
        while self.parent[value] != value:
            previous = self.parent[value]
            self.parent[value] = current
            value = previous
        return current

    def join(self, first, second):
        a, b = self.root(first), self.root(second)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _inspect_records(project, manifest, role, errors):
    rows = []
    for record in manifest["images"]:
        try:
            path = project.image_path(record)
            image, actual_hash, file_hash = _load_image(path)
            try:
                if image.size != (record["width"], record["height"]):
                    raise ValueError("Image dimensions differ from the annotation record.")
            finally:
                image.close()
            if record.get("pixel_sha256") and record["pixel_sha256"] != actual_hash:
                raise ValueError("Stored pixel hash differs from the image. Restore the original pixels or review the changed image before merging.")
            annotation_ids = set()
            for index, annotation in enumerate(record["annotations"]):
                if not isinstance(annotation, dict):
                    raise ValueError(f"Annotation {index + 1} must be an object.")
                identifier = annotation.get("id")
                if not isinstance(identifier, str) or not identifier or identifier in annotation_ids:
                    raise ValueError("Annotation IDs must be unique and nonempty within each image.")
                annotation_ids.add(identifier)
                issues = validate_annotation(annotation, record["width"], record["height"],
                                             require_complete=record["reviewed"])
                if issues:
                    raise ValueError(f"Annotation {index + 1}: " + " ".join(issues))
            rows.append({"id": record["id"], "path": str(path), "file": record["file"],
                         "width": record["width"], "height": record["height"],
                         "pixel_sha256": actual_hash, "file_sha256": file_hash,
                         "group_id": record["group_id"], "split": record["split"],
                         "reviewed": record["reviewed"], "annotations": len(record["annotations"]),
                         "exact_masks": sum(annotation.get("mask") is not None for annotation in record["annotations"]),
                         "label_sha256": _label_signature(record)})
        except (OSError, ValueError) as exc:
            errors.append(f"{role} {record.get('file', record.get('id', 'image'))}: {exc}")
    return rows


def scan_project_merge(target, source):
    """Preview a merge of a saved source project into the current target.

    Same pixels in different splits, including capture groups joined through
    shared frames, are a blocking error. Group names from unrelated projects
    are namespaced; shared frames connect related groups across the projects.
    """
    if not isinstance(target, Project):
        raise TypeError("target must be an open Cloud Studio Project.")
    source = Project.open(source.root if isinstance(source, Project) else source)
    if source.root == target.root:
        raise ValueError("Choose a different source project; a project cannot merge itself.")
    with target._lock:
        target_manifest = copy.deepcopy(target.manifest)
        source_manifest = copy.deepcopy(source.manifest)
        errors = list(target._structure_errors()) + list(source._structure_errors())
        if len(target_manifest.get("images", [])) + len(source_manifest.get("images", [])) > MAX_IMAGES:
            errors.append(f"Merge exceeds the {MAX_IMAGES} input-image limit.")
        target_rows, source_rows = [], []
        if not errors:
            target_rows = _inspect_records(target, target_manifest, "Current project", errors)
            source_rows = _inspect_records(source, source_manifest, "Incoming project", errors)
        target_state, source_state = _digest(target_manifest), _digest(source_manifest)
        source_identity = _digest({"root": str(source.root), "created_at": source_manifest.get("created_at"),
                                   "name": source_manifest.get("name")})[:24]
        groups, group_splits, targets_by_hash, source_by_hash = _Groups(), {}, {}, {}
        for role, rows in (("target", target_rows), ("source", source_rows)):
            for row in rows:
                node = (role, row["group_id"])
                groups.root(node)
                group_splits.setdefault(node, set()).add(row["split"])
        for row in target_rows:
            previous = targets_by_hash.get(row["pixel_sha256"])
            if previous is not None:
                errors.append(f"The current project already has duplicate image pixels: {previous['file']} and {row['file']}. Resolve these current-project duplicates before merging another project.")
                groups.join(("target", previous["group_id"]), ("target", row["group_id"]))
            else:
                targets_by_hash[row["pixel_sha256"]] = row
        entries, conflicts, warnings = [], [], []
        for row in source_rows:
            digest = row["pixel_sha256"]
            existing = targets_by_hash.get(digest)
            previous = source_by_hash.get(digest)
            reference = existing or previous
            if existing is not None:
                groups.join(("source", row["group_id"]), ("target", existing["group_id"]))
            if previous is not None:
                groups.join(("source", row["group_id"]), ("source", previous["group_id"]))
            if reference is None:
                action = "add"
            elif reference["label_sha256"] == row["label_sha256"]:
                action = "duplicate"
            else:
                action = "conflict"
            entry = dict(row, source_image_id=row["id"], source_image=row["path"],
                         target_image_id=existing["id"] if existing else None,
                         duplicate_of_source_id=previous["id"] if previous else None,
                         action=action)
            entries.append(entry)
            if action == "conflict":
                conflicts.append({"source_image_id": row["id"], "source_file": row["file"],
                                  "target_image_id": existing["id"] if existing else None,
                                  "duplicate_of_source_id": previous["id"] if previous else None,
                                  "pixel_sha256": digest, "current_annotations": reference["annotations"],
                                  "incoming_annotations": row["annotations"],
                                  "current_reviewed": reference["reviewed"], "incoming_reviewed": row["reviewed"],
                                  "current_exact_masks": reference["exact_masks"], "incoming_exact_masks": row["exact_masks"]})
            source_by_hash.setdefault(digest, row)
        components = {}
        for node in groups.parent:
            components.setdefault(groups.root(node), []).append(node)
        source_group_mapping, target_group_mapping = {}, {}
        occupied_names = {row["group_id"] for row in target_rows}
        for nodes in sorted(components.values()):
            splits = set().union(*(group_splits[node] for node in nodes))
            if len(splits) > 1:
                names = ", ".join(f"{role}:{group}" for role, group in sorted(nodes))
                errors.append(f"Related source group(s) span multiple splits ({', '.join(sorted(splits))}): {names}. Keep their shared frames and related images in one split before merging.")
            current_groups = sorted(group for role, group in nodes if role == "target")
            incoming_groups = sorted(group for role, group in nodes if role == "source")
            if current_groups:
                canonical = current_groups[0]
            else:
                canonical = f"merge:{source_identity}:{_digest(incoming_groups)[:24]}"
                if canonical in occupied_names:
                    errors.append("An unrelated incoming group collides with an existing generated group name. Resolve its group identity before merging.")
                occupied_names.add(canonical)
            for group in current_groups:
                target_group_mapping[group] = canonical
            for group in incoming_groups:
                source_group_mapping[group] = canonical
        group_updates = [{"image_id": row["id"], "old_group_id": row["group_id"],
                          "group_id": target_group_mapping[row["group_id"]], "split": row["split"]}
                         for row in target_rows if target_group_mapping.get(row["group_id"], row["group_id"]) != row["group_id"]]
        for entry in entries:
            entry["merged_group_id"] = source_group_mapping[entry["group_id"]]
        if conflicts:
            warnings.append(f"{len(conflicts)} identical-image annotation conflict(s) need the selected whole-image policy. No overlapping target boxes or masks are unioned.")
        if group_updates:
            warnings.append(f"{len(group_updates)} existing image(s) will join a related group discovered through shared frames. Their split does not change.")
        if len(source_rows) > len(source_by_hash):
            warnings.append("Incoming pixel-identical images are stored once. For conflicting incoming duplicates, keep_current retains the first source record; prefer_incoming uses the last source record in saved project order.")
        if any(row["reviewed"] and not row["annotations"] for row in source_rows):
            warnings.append("Reviewed incoming images with no targets remain reviewed negative images.")
        source_now = Project.open(source.root)
        if _digest(source_now.manifest) != source_state:
            errors.append("The incoming project changed while it was being scanned. Preview the merge again.")
        summary = {
            "target_images": len(target_manifest.get("images", [])), "source_images": len(source_manifest.get("images", [])),
            "unique_source_images": len(source_by_hash),
            "images_to_add": sum(digest not in targets_by_hash for digest in source_by_hash),
            "duplicate_images": sum(entry["action"] != "add" for entry in entries),
            "annotation_conflicts": len(conflicts),
            "conflict_images": len({item["pixel_sha256"] for item in conflicts}),
            "source_annotations": sum(row["annotations"] for row in source_rows),
            "source_exact_masks": sum(row["exact_masks"] for row in source_rows),
            "source_reviewed_images": sum(row["reviewed"] for row in source_rows),
            "source_negative_images": sum(row["reviewed"] and not row["annotations"] for row in source_rows),
            "target_group_updates": len(group_updates),
            "source_split_counts": {split: sum(row["split"] == split for row in source_rows) for split in SPLITS},
            "error_count": len(errors), "warning_count": len(warnings),
        }
        plan = {
            "schema_version": PLAN_VERSION, "kind": "cloud_project_merge",
            "target": {"root": str(target.root), "name": target_manifest.get("name"), "state_sha256": target_state,
                       "change_token": _change_token(target)},
            "source": {"root": str(source.root), "name": source_manifest.get("name"), "state_sha256": source_state,
                       "change_token": _change_token(source), "identity": source_identity},
            "images": entries, "target_images": target_rows,
            "conflicts": conflicts[:MAX_ISSUES], "target_group_updates": group_updates,
            "source_group_mapping": source_group_mapping,
            "errors": errors[:MAX_ISSUES], "warnings": warnings[:MAX_ISSUES], "summary": summary,
        }
        plan["plan_sha256"] = _plan_hash(plan)
        return plan


def _provenance(plan, record):
    return {"source_project": plan["source"]["name"], "source_identity": plan["source"]["identity"],
            "source_image_id": record["id"], "source_file": record["file"],
            "source_group_id": record["group_id"], "source_split": record["split"],
            "merged_at": utc_now()}


def merge_projects(target, source_or_plan, *, conflict_policy="keep_current"):
    """Merge once, using keep_current or prefer_incoming for complete labels.

    keep_current preserves current labels and the first incoming duplicate.
    prefer_incoming replaces all annotations and review state for a conflicting
    identical image with the last saved incoming record. Neither policy changes
    a split or unions competing mask/box sets. The source is read-only.
    """
    if not isinstance(target, Project):
        raise TypeError("target must be an open Cloud Studio Project.")
    if conflict_policy not in ("keep_current", "prefer_incoming"):
        raise ValueError("conflict_policy must be keep_current or prefer_incoming.")
    with target._lock:
        if isinstance(source_or_plan, dict):
            preview = source_or_plan
            if (preview.get("schema_version") != PLAN_VERSION or preview.get("kind") != "cloud_project_merge"
                    or preview.get("target", {}).get("root") != str(target.root)):
                raise ValueError("This is not a merge preview for the current project. Scan again.")
            if preview.get("plan_sha256") != _plan_hash(preview):
                raise ValueError("The merge preview was modified. Scan again.")
            if preview.get("errors") or preview.get("summary", {}).get("error_count"):
                raise ValueError("Project merge preflight failed: " + "; ".join(preview.get("errors", [])[:8]))
            try:
                plan = scan_project_merge(target, preview["source"]["root"])
            except (KeyError, TypeError) as exc:
                raise ValueError("Malformed project merge preview. Scan again.") from exc
            if plan["errors"] or plan["plan_sha256"] != preview["plan_sha256"]:
                raise ValueError("Project data or labels changed after the merge preview. Scan again before merging.")
        else:
            plan = scan_project_merge(target, source_or_plan)
        if plan["errors"]:
            raise ValueError("Project merge preflight failed: " + "; ".join(plan["errors"][:8]))
        source = Project.open(plan["source"]["root"])
        if _digest(source.manifest) != plan["source"]["state_sha256"]:
            raise ValueError("The incoming project changed after the merge preview. Scan again.")
        originals = {record["id"]: record for record in source.manifest["images"]}
        winners = {}
        for entry in plan["images"]:
            digest = entry["pixel_sha256"]
            if conflict_policy == "prefer_incoming" or digest not in winners:
                winners[digest] = entry
        current_by_hash = {row["pixel_sha256"]: row for row in plan["target_images"]}
        additions = [entry for digest, entry in winners.items() if digest not in current_by_hash]
        replacements = [entry for digest, entry in winners.items()
                        if digest in current_by_hash and conflict_policy == "prefer_incoming"
                        and entry["label_sha256"] != current_by_hash[digest]["label_sha256"]]
        result = {
            "image_ids": [], "updated_image_ids": [], "images_added": 0, "images_skipped": len(plan["images"])-len(additions),
            "annotations_added": 0, "annotations_replaced": 0, "exact_masks_added": 0,
            "reviewed_images_added": 0, "negative_images_added": 0,
            "conflicts_detected": plan["summary"]["annotation_conflicts"],
            "conflict_images": plan["summary"]["conflict_images"],
            "images_replaced": 0, "group_updates": len(plan["target_group_updates"]),
            "conflict_policy": conflict_policy, "source_unchanged": True,
            "warnings": list(plan["warnings"]),
        }
        if not additions and not replacements and not plan["target_group_updates"]:
            return result
        before = copy.deepcopy(target.manifest)
        updated = copy.deepcopy(target.manifest)
        updated_records = {record["id"]: record for record in updated["images"]}
        moved, added_records = [], []
        with tempfile.TemporaryDirectory(prefix=".project-merge-", dir=target.root) as temporary:
            try:
                for entry in additions:
                    incoming = originals[entry["source_image_id"]]
                    raw = _read_bytes(source.image_path(incoming), MAX_IMAGE_FILE_BYTES)
                    if hashlib.sha256(raw).hexdigest() != entry["file_sha256"]:
                        raise ValueError("An incoming image changed during the merge. Scan again.")
                    identifier = uuid4().hex
                    if identifier in updated_records:
                        raise RuntimeError("A generated image ID collided with an existing record. Retry the merge.")
                    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(incoming["file"]).stem).strip("_")[:60] or "image"
                    suffix = Path(incoming["file"]).suffix.lower()
                    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
                        suffix = ".image"
                    filename = f"merge_{stem}_{identifier}{suffix}"
                    staged = Path(temporary) / filename
                    with staged.open("xb") as output:
                        output.write(raw)
                        output.flush()
                        os.fsync(output.fileno())
                    record = copy.deepcopy(incoming)
                    record.update(id=identifier, file="images/"+filename, group_id=entry["merged_group_id"],
                                  pixel_sha256=entry["pixel_sha256"])
                    record["merge_provenance"] = _provenance(plan, incoming)
                    updated_records[identifier] = record
                    updated["images"].append(record)
                    added_records.append(record)
                for entry in replacements:
                    current = current_by_hash[entry["pixel_sha256"]]
                    incoming = originals[entry["source_image_id"]]
                    raw = _read_bytes(source.image_path(incoming), MAX_IMAGE_FILE_BYTES)
                    if hashlib.sha256(raw).hexdigest() != entry["file_sha256"]:
                        raise ValueError("An incoming image changed during the merge. Scan again.")
                    record = updated_records[current["id"]]
                    record["annotations"] = copy.deepcopy(incoming["annotations"])
                    record["reviewed"] = incoming["reviewed"]
                    record["merge_labels_provenance"] = _provenance(plan, incoming)
                for change in plan["target_group_updates"]:
                    updated_records[change["image_id"]]["group_id"] = change["group_id"]
                if _digest(Project.open(source.root).manifest) != plan["source"]["state_sha256"]:
                    raise ValueError("Incoming annotations changed during the merge. Scan again.")
                # Recheck target bytes before replacing labels or joining its
                # groups; a raw image edit cannot silently reuse stale labels.
                for row in plan["target_images"]:
                    raw = _read_bytes(target.image_path(row), MAX_IMAGE_FILE_BYTES)
                    if hashlib.sha256(raw).hexdigest() != row["file_sha256"]:
                        raise ValueError("A current-project image changed during the merge. Scan again.")
                for record in added_records:
                    destination = target.image_path(record)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation is portable and cannot replace a file
                    # that appeared after the preflight existence checks.
                    with destination.open("xb") as output:
                        moved.append(destination)
                        with (Path(temporary)/destination.name).open("rb") as staged:
                            while chunk := staged.read(1024*1024):
                                output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                target.manifest.clear()
                target.manifest.update(updated)
                target.save()
            except BaseException:
                target.manifest.clear()
                target.manifest.update(before)
                for destination in moved:
                    destination.unlink(missing_ok=True)
                raise
        result.update(
            image_ids=[record["id"] for record in added_records],
            updated_image_ids=[current_by_hash[entry["pixel_sha256"]]["id"] for entry in replacements],
            images_added=len(added_records), images_replaced=len(replacements),
            annotations_added=sum(len(record["annotations"]) for record in added_records),
            annotations_replaced=sum(len(originals[entry["source_image_id"]]["annotations"]) for entry in replacements),
            exact_masks_added=sum(annotation.get("mask") is not None for record in added_records for annotation in record["annotations"]),
            reviewed_images_added=sum(record["reviewed"] for record in added_records),
            negative_images_added=sum(record["reviewed"] and not record["annotations"] for record in added_records),
        )
        return result
