"""Bounded local review state, separate from the training dataset."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .data import atomic_write


def annotation_digest(annotations):
    return hashlib.sha256(json.dumps(annotations, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


class ReviewSession:
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, project):
        self.path = project.root / ".studio-review.json"
        self.data = {"version": 1, "last_image_id": None, "drafts": {}, "target_drafts": {}, "history": {},
                     "reviews": [], "flagged": [], "preferences": {}}
        self.warning = None
        self._last_saved = None
        if self.path.is_file():
            try:
                if self.path.stat().st_size > self.MAX_BYTES:
                    raise ValueError("Review recovery file exceeds its size limit.")
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or value.get("version") != 1:
                    raise ValueError("Unsupported review recovery file.")
                for key in ("drafts", "history", "preferences"):
                    if not isinstance(value.get(key), dict):
                        raise ValueError("Invalid review recovery data.")
                target_drafts = value.get("target_drafts", {})
                if (not isinstance(target_drafts, dict) or any(
                        not isinstance(rows, dict) or any(not isinstance(row, dict) for row in rows.values())
                        for rows in target_drafts.values())):
                    raise ValueError("Invalid per-target outline recovery data.")
                for key in ("reviews", "flagged"):
                    if not isinstance(value.get(key), list):
                        raise ValueError("Invalid review recovery data.")
                if value.get("last_image_id") is not None and not isinstance(value["last_image_id"], str):
                    raise ValueError("Invalid recovered image identifier.")
                if any(not isinstance(item, str) for item in value["flagged"]):
                    raise ValueError("Invalid flagged image identifiers.")
                if any(not isinstance(item, dict) or not isinstance(item.get("image_id"), str)
                       or not isinstance(item.get("digest"), str) for item in value["reviews"]):
                    raise ValueError("Invalid recovered review history.")
                if any(not isinstance(item, dict) for key in ("history", "drafts") for item in value[key].values()):
                    raise ValueError("Invalid recovered draft history.")
                self.data.update({key: value[key] for key in self.data if key in value})
            except (OSError, ValueError, TypeError) as exc:
                self.warning = str(exc)

    def save(self):
        value = deepcopy(self.data)
        for key, limit in (("drafts", 12), ("target_drafts", 12), ("history", 8)):
            value[key] = dict(list(value[key].items())[-limit:])
        value["reviews"] = value["reviews"][-30:]
        value["flagged"] = list(dict.fromkeys(value["flagged"]))[-10000:]
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        while len(raw) > self.MAX_BYTES and value["history"]:
            del value["history"][next(iter(value["history"]))]
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > self.MAX_BYTES:
            raise ValueError("Review recovery data is too large; completed labels are still saved in the project.")
        if raw != self._last_saved:
            atomic_write(self.path, raw)
            self._last_saved = raw
        self.data = value

