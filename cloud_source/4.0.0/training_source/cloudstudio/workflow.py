"""Imported-box annotation, prepared images, and recoverable review navigation.

The desktop owns widgets and accepted labels. Background workers produce draft
geometry; only explicit review changes training eligibility.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import json
import math
from pathlib import Path
import threading
import time
from uuid import uuid4

from PIL import Image
from PySide6.QtCore import QEvent, QTimer, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)

from .geometry import validate_annotation
from .masks import has_encasing, replace_encasing, encasing_equal
from .review import ReviewSession, annotation_digest


class ReviewWorkflowMixin:
    def __init__(self, *args, **kwargs):
        self._review_session = None
        self._session_suspended = False
        self._keep_recovery_draft = False
        self._closing_review = False
        self._image_schedule = 0
        self._model_proposed = set()
        self._review_started = time.monotonic()
        self._reviewed_targets = set()
        self._reviewed_images = set()
        self._last_navigation_ms = None
        self._navigation_started = None
        self._outline_times = deque(maxlen=100)
        self._corrections = 0
        self._review_history = {}
        self._last_comparison = None
        self._live_start_ticket = 0
        self._prepared_image_key = None
        # The ordinary worker owns inference. This queue only coordinates its
        # existing requests and stages complete, UNREVIEWED pairs in the GUI.
        self._outline_batch_active = False
        self._outline_batch_paused = False
        self._outline_batch_context = None
        self._outline_batch_token = None
        self._outline_queue = []
        self._outline_auto_targets = set()
        self._outline_attempted = set()
        self._outline_failures = {}
        self._outline_preferred = ""
        self._outline_internal = False
        self._outline_preserving = False
        self._outline_last_selection = ""
        super().__init__(*args, **kwargs)

    def _build_ui(self):
        super()._build_ui()
        self.yolo_button = QPushButton("Import YOLO boxes")
        yolo_menu = QMenu(self.yolo_button)
        yolo_menu.addAction("Dataset folder…", lambda: self._import_yolo_dialog("folder"))
        yolo_menu.addAction("Dataset YAML…", lambda: self._import_yolo_dialog("yaml"))
        self.yolo_button.setMenu(yolo_menu)
        self.file_bar.insertWidget(4, self.yolo_button)
        self.merge_button = QPushButton("Merge datasets…")
        merge_menu = QMenu(self.merge_button)
        merge_menu.addAction("Merge another Cloud project…", self._merge_dataset_dialog)
        merge_menu.addAction("Merge YOLO class names into player…", lambda: self._import_yolo_dialog("yaml"))
        self.merge_button.setMenu(merge_menu)
        self.merge_button.setToolTip("Combine two saved datasets without duplicating identical images, or import several YOLO names as the single player class.")
        self.file_bar.insertWidget(5, self.merge_button)
        self.guide_button = QPushButton(".cloud integration guide")
        self.guide_button.clicked.connect(self._open_integration_guide)
        self.file_bar.insertWidget(6, self.guide_button)

        options = QHBoxLayout()
        self.prepare_check = QCheckBox("Prepare upcoming images")
        self.prepare_check.setChecked(True)
        self.prepare_check.toggled.connect(self._preparation_changed)
        self.auto_draft_check = QCheckBox("Use loaded model for draft labels")
        self.auto_draft_check.setChecked(True)
        self.auto_draft_check.setToolTip("Draft labels appear automatically on empty, unreviewed images when a detector is loaded.")
        self.auto_draft_check.toggled.connect(self._schedule_image_work)
        options.addWidget(self.prepare_check)
        options.addWidget(self.auto_draft_check)
        options.addStretch()
        self.annotation_layout.insertLayout(2, options)
        self.review_metrics = QLabel("Draw a box or import YOLO boxes to begin.")
        self.review_metrics.setWordWrap(True)
        self.annotation_layout.addWidget(self.review_metrics)

        self.review_filter = QComboBox()
        for label, value in (("All images", "all"), ("Unreviewed images", "unreviewed"),
                             ("Flagged for another look", "flagged"), ("Reviewed images", "reviewed")):
            self.review_filter.addItem(label, value)
        self.review_filter.currentIndexChanged.connect(self._filter_images)
        self.image_sidebar_layout.insertWidget(2, self.review_filter)
        self.previous_button = QPushButton("Previous image [Alt+Left]")
        self.previous_button.clicked.connect(self._previous_review_image)
        self.undo_review_button = QPushButton("Undo last review")
        self.undo_review_button.clicked.connect(self._undo_last_review)
        self.skip_button = QPushButton("Flag & skip [S]")
        self.skip_button.clicked.connect(self._skip_review_image)
        self.negative_next_button = QPushButton("Empty negative & next [X]")
        self.negative_next_button.clicked.connect(self._negative_and_next)
        for button in (self.previous_button, self.undo_review_button, self.skip_button, self.negative_next_button):
            self.image_sidebar_layout.addWidget(button)
        self.canvas.installEventFilter(self)
        self.tabs.currentChanged.connect(self._schedule_image_work)

        self.compare_button = QPushButton("Compare two .cloud models")
        self.compare_button.clicked.connect(self._compare_models_dialog)
        self.evaluation_form.addRow(self.compare_button)
        self.open_comparison_button = QPushButton("Open last comparison report")
        self.open_comparison_button.clicked.connect(self._open_comparison)
        self.open_comparison_button.setEnabled(False)
        self.evaluation_form.addRow(self.open_comparison_button)
        self.detail_preview_button = QPushButton("Preview training outline detail")
        self.detail_preview_button.clicked.connect(self._preview_training_outline)
        self.evaluation_form.addRow(self.detail_preview_button)

    def _use_project(self, project):
        self._checkpoint_review()
        previous = self._review_session
        self._session_suspended = True
        self._review_session = ReviewSession(project)
        remembered = self._review_session.data.get("last_image_id")
        self._review_history = self._review_session.data.get("history", {})
        self._model_proposed.clear()
        try:
            super()._use_project(project)
            if self.project is not project:
                self._review_session = previous
                return
            preferences = self._review_session.data.get("preferences", {})
            self.prepare_check.setChecked(bool(preferences.get("prepare", True)))
            self.auto_draft_check.setChecked(bool(preferences.get("auto_draft", True)))
            for key, widget in (("assist_backend", self.assist_backend), ("assist_device", self.assist_device), ("assist_crop", self.assist_crop)):
                index = widget.findData(preferences.get(key))
                if index >= 0:
                    widget.blockSignals(True)
                    widget.setCurrentIndex(index)
                    widget.blockSignals(False)
            known = {row["id"] for row in project.manifest["images"]}
            if remembered in known and remembered != self.current_image_id:
                self._load_image(remembered)
            self.init_model_path.clear()
        finally:
            self._session_suspended = False
        self._restore_review_state()
        self._schedule_image_work()

    def _load_image(self, image_id):
        if self._outline_batch_active:
            self._workflow_notice("Finish or cancel the automatic outlines before changing images.", True)
            return False
        if image_id != self.current_image_id:
            self._checkpoint_review()
        suspended = self._session_suspended
        self._session_suspended = True
        try:
            result = super()._load_image(image_id)
        finally:
            self._session_suspended = suspended
        if result:
            self._image_schedule += 1
            self._outline_attempted.clear()
            self._outline_failures.clear()
            # Automatic image preparation owns boxes already present when the
            # image opens/imports. A box just drawn by the user keeps its live
            # proposal until Enter or N, even when other targets exist.
            self._outline_auto_targets = {row["id"] for row in self.canvas.annotations if not has_encasing(row)}
            self._outline_batch_paused = False
            self._outline_last_selection = self.canvas.selected_id
            if not self._session_suspended:
                self._restore_review_state()
                self._checkpoint_review()
                self._schedule_image_work()
        return result

    def _checkpoint_review(self):
        if self._review_session is None or self._session_suspended or self._closing_review:
            return True
        try:
            state = self._review_session.data
            state["preferences"] = {"prepare": self.prepare_check.isChecked(),
                                    "auto_draft": self.auto_draft_check.isChecked(),
                                    "assist_backend": self.assist_backend.currentData(),
                                    "assist_device": self.assist_device.currentData(),
                                    "assist_crop": self.assist_crop.currentData()}
            image_id = self.current_image_id
            if image_id is not None and self.project is not None:
                record = self.project.record(image_id)
                state["last_image_id"] = image_id
                digest = annotation_digest(self.canvas.annotations)
                state["history"][image_id] = {"digest": digest, "undo": deepcopy(self.canvas._undo[-12:]),
                                              "redo": deepcopy(self.canvas._redo[-12:])}
                if self.canvas.has_assist or self.canvas.has_draft:
                    state["drafts"][image_id] = {
                        "digest": digest, "pixel_sha256": record.get("pixel_sha256"),
                        "annotation_id": self.canvas.assist_annotation_id or self.canvas._draft_id,
                        "points": deepcopy(self._assist_points), "labels": list(self._assist_labels),
                        "result": deepcopy(self._assist_result), "detail": self.assist_detail.currentData(),
                        "manual": deepcopy(self.canvas._draft),
                    }
                    self._remember_target_draft()
                elif not self._keep_recovery_draft:
                    state["drafts"].pop(image_id, None)
            self._review_session.save()
            return True
        except Exception as exc:
            if hasattr(self, "save_status"):
                self.save_status.setText("Labels saved; session recovery unavailable: " + str(exc))
            return False

    def _restore_review_state(self):
        if self._review_session is None or self.current_image_id is None or self.canvas.has_assist:
            return
        image_id = self.current_image_id
        record = self.project.record(image_id)
        digest = annotation_digest(self.canvas.annotations)
        history = self._review_session.data["history"].get(image_id, {})
        if isinstance(history, dict) and history.get("digest") == digest:
            def valid_history(rows):
                if not isinstance(rows, list):
                    return []
                return [row for row in rows[-12:] if isinstance(row, list) and
                        all(isinstance(ann, dict) and not validate_annotation(ann, *self.canvas.image_size,
                            require_complete=False) for ann in row)]
            self.canvas._undo = deepcopy(valid_history(history.get("undo", [])))
            self.canvas._redo = deepcopy(valid_history(history.get("redo", [])))
        if not record.get("reviewed") and self._restore_target_draft():
            return
        draft = self._review_session.data["drafts"].get(image_id)
        if (not isinstance(draft, dict) or draft.get("digest") != digest
                or draft.get("pixel_sha256") != record.get("pixel_sha256")):
            return
        annotation = next((ann for ann in self.canvas.annotations if ann["id"] == draft.get("annotation_id")), None)
        if annotation is None:
            return
        if draft.get("manual"):
            points = draft["manual"]
            if isinstance(points, list) and len(points) <= 512 and all(
                    isinstance(point, list) and len(point) == 2 and all(isinstance(v, (int, float)) for v in point)
                    and 0 <= point[0] <= record["width"] and 0 <= point[1] <= record["height"] for point in points):
                self.canvas._draft = deepcopy(points)
                self.canvas._draft_id = annotation["id"]
                self.canvas.selected_id = annotation["id"]
                self.canvas.set_tool("wrapper")
                self.canvas.draftChanged.emit(True)
                self._workflow_notice("Recovered your unfinished manual outline. Finish it or continue editing.")
            return
        points, labels = draft.get("points", []), draft.get("labels", [])
        if (not isinstance(points, list) or not isinstance(labels, list) or len(points) != len(labels)
                or len(points) > 256 or any(type(label) is not int or label not in (0, 1) for label in labels)):
            return
        if any(not isinstance(point, list) or len(point) != 2 or
               any(not isinstance(v, (int, float)) for v in point) or
               not (0 <= point[0] < record["width"] and 0 <= point[1] < record["height"]) for point in points):
            return
        if not self.canvas.begin_assist(annotation["id"]):
            return
        self._assist_result = None
        detail_index = self.assist_detail.findData(draft.get("detail"))
        if detail_index >= 0:
            self.assist_detail.blockSignals(True)
            self.assist_detail.setCurrentIndex(detail_index)
            self.assist_detail.blockSignals(False)
        self._assist_context = self._assist_identity()
        self._assist_points, self._assist_labels = deepcopy(points), list(labels)
        self._assist_prompt_revision += 1
        with Image.open(self.project.image_path(record)) as image:
            self._assist_image = image.convert("RGB").copy()
        result = draft.get("result")
        if isinstance(result, dict) and has_encasing(result):
            candidate = replace_encasing(annotation, result)
            self._assist_result = deepcopy(result) if not validate_annotation(candidate, *self.canvas.image_size) else None
        self.canvas.set_assist_preview(self._assist_result.get("wrapper") if self._assist_result else None, points, labels,
                                       mask=self._assist_result.get("mask") if self._assist_result else None)
        self._assist_refinement_token = None
        self._populate_assist_candidates()
        self.canvas.set_tool("assist_fg")
        self._refresh_assist_controls()
        self._workflow_notice("Recovered the unaccepted outline and correction clicks. Check it, then press Enter.")
        if self._assist_result is None:
            QTimer.singleShot(0, self._request_assist)

    def _schedule_image_work(self, *_):
        if self._session_suspended or self._closing_review:
            return
        if hasattr(self, "tabs") and self.tabs.currentWidget() != self.annotate_tab:
            if self._assist_thread is not None:
                self._assist_thread.pause_preparation()
            return
        generation = self._image_schedule
        image_id = self.current_image_id
        QTimer.singleShot(0, lambda: self._start_image_work(image_id, generation))

    def _outline_image_key(self):
        if self.project is None or self.current_image_id is None:
            return None
        return (str(self.project.root.resolve()), self.current_image_id, self.canvas.image_revision)

    def _outline_key(self, annotation):
        return (self._outline_image_key(), annotation["id"], tuple(annotation.get("box", ())))

    def _remember_target_draft(self, annotation=None, result=None, points=None, labels=None):
        """Keep correction state independently from other targets' edits."""
        if self._review_session is None or self.current_image_id is None:
            return
        annotation = annotation or self.canvas.selected_annotation()
        if annotation is None:
            return
        record = self.project.record(self.current_image_id)
        row = {"box": deepcopy(annotation["box"]),
               "saved_wrapper": deepcopy(annotation.get("wrapper", [])),
               "saved_mask": deepcopy(annotation.get("mask")),
               "pixel_sha256": record.get("pixel_sha256"),
               "points": deepcopy(self._assist_points if points is None else points),
               "labels": list(self._assist_labels if labels is None else labels),
               "result": deepcopy(self._assist_result if result is None else result),
               "detail": self.assist_detail.currentData(),
               "manual": deepcopy(self.canvas._draft) if self.canvas._draft_id == annotation["id"] else []}
        if self._unsaved:
            saved = next((item for item in record.get("annotations", []) if item["id"] == annotation["id"]), None)
            if saved is not None and saved.get("box") == annotation["box"]:
                row["saved_wrapper"] = deepcopy(saved.get("wrapper", []))
                row["saved_mask"] = deepcopy(saved.get("mask"))
        images = self._review_session.data["target_drafts"]
        rows = images.pop(self.current_image_id, {})
        rows.pop(annotation["id"], None)
        rows[annotation["id"]] = row
        images[self.current_image_id] = rows

    def _target_draft(self, annotation):
        if self._review_session is None or self.current_image_id is None:
            return None
        row = self._review_session.data["target_drafts"].get(self.current_image_id, {}).get(annotation["id"])
        record = self.project.record(self.current_image_id)
        if (not isinstance(row, dict) or row.get("box") != annotation.get("box")
                or row.get("saved_wrapper") != annotation.get("wrapper", [])
                or row.get("saved_mask") != annotation.get("mask")
                or row.get("pixel_sha256") != record.get("pixel_sha256")):
            return None
        points, labels = row.get("points", []), row.get("labels", [])
        width, height = self.canvas.image_size
        def valid_points(values, limit, inclusive=False):
            return isinstance(values, list) and len(values) <= limit and all(
                isinstance(point, list) and len(point) == 2
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in point)
                and 0 <= point[0] <= width and 0 <= point[1] <= height
                and (inclusive or (point[0] < width and point[1] < height)) for point in values)
        if (not valid_points(points, 256) or not isinstance(labels, list)
                or len(points) != len(labels) or any(type(label) is not int or label not in (0, 1) for label in labels)
                or not valid_points(row.get("manual", []), 512, True)):
            return None
        return deepcopy(row)

    def _activate_target_outline(self, annotation, *, request_missing=False):
        """Show saved geometry/clicks immediately; inference is only for edits."""
        if self.canvas.has_assist or self.canvas.has_draft:
            return self.canvas.assist_annotation_id == annotation["id"]
        row = self._target_draft(annotation)
        self.canvas.selected_id = annotation["id"]
        if row and row.get("manual"):
            self.canvas._draft = deepcopy(row["manual"])
            self.canvas._draft_id = annotation["id"]
            self.canvas.set_tool("wrapper")
            self.canvas.draftChanged.emit(True)
            return True
        if not self.canvas.begin_assist(annotation["id"]):
            return False
        self._assist_context = self._assist_identity()
        self._assist_prompt_revision += 1
        self._assist_points = deepcopy(row.get("points", [])) if row else []
        self._assist_labels = list(row.get("labels", [])) if row else []
        if row:
            detail = self.assist_detail.findData(row.get("detail"))
            if detail >= 0:
                self.assist_detail.blockSignals(True)
                self.assist_detail.setCurrentIndex(detail)
                self.assist_detail.blockSignals(False)
        with Image.open(self.project.image_path(self.current_image_id)) as source:
            self._assist_image = source.convert("RGB").copy()
        result = deepcopy(row.get("result")) if row else None
        if not isinstance(result, dict):
            result = None
        if result and has_encasing(result):
            candidate = replace_encasing(annotation, result)
            if validate_annotation(candidate, *self.canvas.image_size):
                result = dict(result, wrapper=None, mask=None)
        # A recovered row with no result represents interrupted computation.
        # Its saved outline must not masquerade as the answer to newer clicks.
        if result is None and row is None and has_encasing(annotation):
            if not validate_annotation(annotation, *self.canvas.image_size):
                result = {"wrapper": deepcopy(annotation.get("wrapper", [])),
                          "mask": deepcopy(annotation.get("mask")),
                          "backend": annotation.get("assist_backend", "MobileSAM"),
                          "warnings": deepcopy(annotation.get("assist_warnings", [])),
                          "diagnostics": {"saved_outline": True}}
        self._assist_result = result
        self.canvas.set_assist_preview(result.get("wrapper") if result else None,
                                       self._assist_points, self._assist_labels,
                                       mask=result.get("mask") if result else None)
        self._assist_refinement_token = result.get("refinement_token") if result else None
        self._populate_assist_candidates()
        self.canvas.set_tool("assist_fg")
        self._canvas_selection_changed(annotation["id"])
        self._refresh_assist_controls()
        if request_missing and result is None:
            return self._request_assist()
        return True

    def _restore_target_draft(self):
        rows = self._review_session.data["target_drafts"].get(self.current_image_id, {})
        if not rows:
            return False
        legacy = self._review_session.data["drafts"].get(self.current_image_id, {})
        order = [legacy.get("annotation_id")] + list(reversed(rows))
        for annotation_id in order:
            annotation = next((row for row in self.canvas.annotations if row["id"] == annotation_id), None)
            if annotation is None or self._target_draft(annotation) is None:
                continue
            if self._activate_target_outline(annotation):
                self._workflow_notice("Recovered this target's outline and correction clicks. Other boxes continue automatically.")
                if not self.canvas.has_draft and self._assist_result is None:
                    QTimer.singleShot(0, self._request_assist)
                return True
        return False

    def _start_outline_batch(self, *, retry=False):
        if (self._outline_batch_active or self._assist_busy or self._job is not None
                or self.project is None or self.current_image_id is None or self._closing_review
                or self._live_session is not None or self.canvas.has_draft):
            return False
        self._outline_preferred = self.canvas.selected_id
        if retry:
            self._outline_attempted.clear()
        if self.canvas.has_assist:
            # Old 3.1.1 drafts and edited previews retain their exact prompts.
            # Their successful geometry becomes a saved, unreviewed pair.
            if self._assist_result and has_encasing(self._assist_result):
                if not self._accept_assist():
                    return False
            else:
                self._remember_target_draft()
                self._clear_assist_state()
        if self._unsaved and not self._persist_current():
            return False
        pending = [row for row in self.canvas.annotations if not has_encasing(row)
                   and self._outline_key(row) not in self._outline_attempted]
        if not pending:
            return False
        self._outline_batch_active = True
        self._outline_batch_paused = False
        self._outline_batch_context = self._outline_image_key()
        self._outline_batch_token = None
        self._outline_queue = [(row["id"], tuple(row["box"])) for row in pending]
        if not self._outline_preferred:
            self._outline_preferred = pending[0]["id"]
        self._refresh_summary()
        QTimer.singleShot(0, self._run_next_outline)
        return True

    def _run_next_outline(self):
        if not self._outline_batch_active or self._assist_busy or self._closing_review:
            return
        if self._outline_batch_context != self._outline_image_key():
            self._finish_outline_batch(stale=True)
            return
        if self._job is not None or self._live_session is not None:
            # A generic worker can finish between image loading and scheduling.
            # Its completion signal resumes this queue; no request is dropped.
            return
        while self._outline_queue:
            annotation_id, original_box = self._outline_queue.pop(0)
            annotation = next((row for row in self.canvas.annotations if row["id"] == annotation_id), None)
            if annotation is None or tuple(annotation["box"]) != original_box or has_encasing(annotation):
                continue
            self._outline_attempted.add(self._outline_key(annotation))
            issues = validate_annotation(annotation, *self.canvas.image_size, require_complete=False)
            if issues:
                self._outline_failures[annotation_id] = " ".join(issues)
                continue
            self._outline_internal = True
            self.canvas.edit_locked = False
            started = False
            try:
                self._activate_target_outline(annotation)
                started = self._request_assist()
                if started:
                    self._outline_batch_token = self._assist_inflight_token
                    index = next(i for i, row in enumerate(self.canvas.annotations) if row["id"] == annotation_id)
                    self._workflow_notice(f"Generating target {index+1} of {len(self.canvas.annotations)}. Every box is outlined automatically; Enter reviews the completed image.")
                else:
                    self._outline_failures[annotation_id] = "Outline request could not start. Select this target and retry."
                    self._remember_target_draft()
                    self._clear_assist_state()
            finally:
                self._outline_internal = False
                self._refresh_summary()
            if started:
                return
            if self._unsaved:
                self._finish_outline_batch(paused=True)
                return
        self._finish_outline_batch()

    def _finish_outline_batch(self, *, paused=False, stale=False):
        self._outline_batch_active = False
        self._outline_batch_paused = paused
        self._outline_batch_token = None
        self._outline_queue = []
        self.canvas.edit_locked = False
        if stale or self._closing_review or self.project is None or self.current_image_id is None:
            self._outline_batch_context = None
            self._refresh_summary()
            return
        annotations = self.canvas.annotations
        preferred = next((row for row in annotations if row["id"] in self._outline_failures and not has_encasing(row)), None)
        if preferred is None:
            preferred = next((row for row in annotations if row["id"] == self._outline_preferred), None)
        if preferred is None and annotations:
            preferred = annotations[0]
        if preferred is not None and not self.canvas.has_assist:
            self._outline_internal = True
            try:
                self._activate_target_outline(preferred)
            finally:
                self._outline_internal = False
        complete = sum(has_encasing(row) for row in annotations)
        if paused:
            message = f"Automatic outlines paused · {complete} of {len(annotations)} ready. Recompute/Resume outlines continues the remaining boxes. Saved outlines are unreviewed."
        elif complete < len(annotations):
            message = f"{complete} of {len(annotations)} outlines ready. Every box was attempted. Select an incomplete target and add a foreground/background click or recompute its outline; complete every target before Enter."
        else:
            message = f"All {complete} outlines are ready and saved as unreviewed. Select any target to correct it; Enter reviews this image and moves next."
            warning_count = sum(bool(row.get("assist_warnings")) for row in annotations)
            if warning_count:
                message += f" {warning_count} target(s) have detail warnings shown beside their labels."
        self._refresh_instances()
        self._refresh_summary()
        self._checkpoint_review()
        self._workflow_notice(message, paused or complete < len(annotations))
        if not paused:
            self._prepare_upcoming_images()

    def _canvas_selection_changed(self, annotation_id):
        changed = annotation_id != self._outline_last_selection
        self._outline_last_selection = annotation_id
        super()._canvas_selection_changed(annotation_id)
        if changed and annotation_id and not self._outline_internal and not self._changing:
            context = self._outline_image_key()
            QTimer.singleShot(0, lambda: self._activate_selected_outline(annotation_id, context))

    def _activate_selected_outline(self, annotation_id, context):
        if (context != self._outline_image_key() or annotation_id != self.canvas.selected_id
                or self._outline_batch_active or self._assist_busy or self._job is not None
                or self._closing_review or self.canvas.has_assist or self.canvas.has_draft):
            return
        annotation = self.canvas.selected_annotation()
        if annotation is None or annotation.get("draft_review"):
            return
        if has_encasing(annotation) or self._target_draft(annotation):
            self._activate_target_outline(annotation)
            self._checkpoint_review()
            if (self.canvas.has_assist and self._assist_result is None
                    and not self._outline_batch_paused):
                self._request_assist()
        elif len(self.canvas.annotations) > 1:
            self._start_outline_batch()
        else:
            self._outline_existing_box(annotation)

    def _resolve_assist(self):
        if not self.canvas.has_assist:
            return True
        if len(self.canvas.annotations) <= 1:
            # Keep the existing explicit protection for a lone manual target
            # when changing images/tools. Multi-target selection is the new
            # automatic save-and-restore path.
            return super()._resolve_assist()
        if self._assist_busy or self._outline_batch_active:
            self._workflow_notice("Finish or cancel the automatic outlines before changing targets.", True)
            return False
        # Target selection/navigation is not a second acceptance workflow.
        # Valid edits are saved unreviewed; unfinished prompts remain recoverable.
        if self._assist_result and has_encasing(self._assist_result):
            return self._accept_assist()
        if not self._checkpoint_review():
            return False
        self._outline_preserving = True
        try:
            return self._reject_assist()
        finally:
            self._outline_preserving = False

    def _workflow_is_busy(self):
        if self._outline_batch_active:
            self._workflow_notice("The remaining boxes are being outlined automatically. Check all outlines when ready, then press Enter.", True)
            return True
        return super()._workflow_is_busy()

    def _assist_button_clicked(self):
        if self._outline_batch_active:
            self._cancel_assist()
        elif self._outline_batch_paused:
            self._start_outline_batch(retry=True)
        else:
            return super()._assist_button_clicked()

    def _cancel_assist(self):
        if self._outline_batch_active:
            self._outline_batch_paused = True
            if not self._assist_busy:
                self._finish_outline_batch(paused=True)
                return
        return super()._cancel_assist()

    def _refresh_assist_controls(self):
        super()._refresh_assist_controls()
        if self._outline_batch_paused and not self._assist_busy and not self._outline_batch_active:
            self.assist_start_button.setText("Resume outlines")

    def _refresh_instances(self):
        super()._refresh_instances()
        for index, annotation in enumerate(self.canvas.annotations):
            message = self._outline_failures.get(annotation["id"])
            if message and not has_encasing(annotation):
                self.instance_list.item(index).setText(self.instance_list.item(index).text() + "\nNeeds correction: " + message)

    def _start_image_work(self, image_id, generation):
        if (image_id is None or image_id != self.current_image_id or generation != self._image_schedule
                or self._job is not None or self._assist_busy or self._live_session is not None
                or self.project is None or self._closing_review or not self.isVisible()
                or self.tabs.currentWidget() != self.annotate_tab or self._outline_batch_active
                or self._outline_batch_paused):
            return
        record = self.project.record(image_id)
        if record.get("reviewed"):
            return
        pending = [ann for ann in self.canvas.annotations if not has_encasing(ann)
                   and ann["id"] in self._outline_auto_targets
                   and self._outline_key(ann) not in self._outline_attempted]
        # A recovered first-box preview is preserved and staged before the
        # remaining boxes are processed. It must not hold the whole image up.
        if len(self.canvas.annotations) > 1 and pending and not self.canvas.has_draft:
            self._start_outline_batch()
            return
        if self.canvas.has_assist or self.canvas.has_draft:
            return
        pending = next((ann for ann in self.canvas.annotations if not has_encasing(ann)), None)
        if pending is not None:
            self._outline_existing_box(pending)
        elif not self.canvas.annotations and self.auto_draft_check.isChecked() and self._runtime is not None:
            self._draft_from_loaded_model()
        else:
            self._prepare_upcoming_images()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_image_work()

    def _prepare_upcoming_images(self):
        if (self.project is None or self.current_image_id is None or not self.prepare_check.isChecked()
                or self._job is not None or self._assist_busy or self._closing_review
                or self._live_session is not None or self.tabs.currentWidget() != self.annotate_tab
                or self._outline_batch_active):
            return
        records = self.project.manifest["images"]
        index = next((i for i, row in enumerate(records) if row["id"] == self.current_image_id), None)
        if index is None:
            return
        thread = self._ensure_assist_thread()
        requests = [{"identity": (str(self.project.root), row["id"]),
                     "path": str(self.project.image_path(row)), "device": self.assist_device.currentData(),
                     "backend": self.assist_backend.currentData()}
                    for row in records[index:index+2]]
        thread.prepare_images(requests)

    def _preparation_changed(self, checked):
        if not checked and self._assist_thread is not None:
            self._assist_thread.prepare_images([])
        elif checked and not self._session_suspended:
            self._prepare_upcoming_images()
        self._checkpoint_review()

    def _ensure_assist_thread(self):
        thread = super()._ensure_assist_thread()
        if not getattr(thread, "_review_prepared_connected", False):
            thread.prepared.connect(self._image_prepared)
            thread._review_prepared_connected = True
        return thread

    @Slot(object)
    def _image_prepared(self, outcome):
        if self.project is None or tuple(outcome.get("identity", ())) != (str(self.project.root), self.current_image_id):
            return
        if outcome.get("result"):
            self._prepared_image_key = (str(self.project.root), self.current_image_id)
            self._refresh_review_metrics()

    def _outline_existing_box(self, annotation):
        if validate_annotation(annotation, *self.canvas.image_size, require_complete=False):
            self._workflow_notice("This imported box needs correction before its outline can be generated.", True)
            return False
        if not self.canvas.select(annotation["id"]):
            return False
        self._auto_assisted_boxes.add((self.current_image_id, annotation["id"]))
        return self._auto_assist_if_current(self._assist_identity())

    def _draft_from_loaded_model(self):
        identity = (str(self.project.root), self.current_image_id, str(self._model_path),
                    self.confidence_spin.value(), self.nms_spin.value(), id(self._runtime),
                    self.maximum_detections.value(), self.minimum_height.value())
        if identity in self._model_proposed:
            return False
        self._model_proposed.add(identity)
        path = self.project.image_path(self.current_image_id)
        runtime = self._runtime
        settings = {"confidence": self.confidence_spin.value(), "nms_iou": self.nms_spin.value(),
                    "max_detections": self.maximum_detections.value(), "min_height": self.minimum_height.value(),
                    "debug": True}
        def work(progress, should_stop):
            with Image.open(path) as image:
                return runtime.predict(image.convert("RGB"), **settings)
        def done(result):
            if (self.project is None or (str(self.project.root), self.current_image_id, str(self._model_path)) != identity[:3]
                    or self.canvas.annotations or self.project.record(self.current_image_id).get("reviewed")):
                return
            additions = []
            for detection in result.get("detections", []):
                if "primary_box" not in detection:
                    continue
                annotation = {"id": uuid4().hex, "class_id": 0, "box": deepcopy(detection["primary_box"]),
                              "wrapper": deepcopy(detection.get("wrapper") or []), "source": "model",
                              "model": Path(identity[2]).name, "confidence": float(detection.get("confidence", 0)),
                              "draft_review": True}
                annotation = replace_encasing(annotation, detection)
                if not validate_annotation(annotation, *self.canvas.image_size):
                    additions.append(annotation)
            if additions:
                self.canvas.replace_annotations(additions)
                self.canvas.select(additions[0]["id"])
                self._workflow_notice(f"{len(additions)} draft target(s). Check each outline; Enter accepts a target and continues.")
            else:
                self._workflow_notice("The model found no targets. Draw a missed target or use Empty negative & next if the image is empty.")
        return self._start_job("Automatic annotation", work, done, cancellable=False)

    @Slot()
    def _approve_and_advance(self):
        if self._advancing_image or self._workflow_is_busy():
            return False
        if self.project is None or self.current_image_id is None:
            self._workflow_notice("Open an image, then draw its first target box.", True)
            return False
        self._advancing_image = True
        self._navigation_started = time.perf_counter()
        try:
            image_id = self.current_image_id
            selected = self.canvas.selected_id
            if not self._commit_visible_outline():
                return False
            record = self.project.record(image_id)
            if not self.canvas.annotations and not (record.get("reviewed") and not self._unsaved):
                self._workflow_notice("Draw a target box first. If this image has no targets, choose Mark empty image as negative explicitly.", True)
                return False
            changed = False
            additions = deepcopy(self.canvas.annotations)
            for ann in additions:
                if ann["id"] == selected and ann.get("draft_review"):
                    ann.pop("draft_review", None)
                    changed = True
            if changed:
                self.canvas.replace_annotations(additions)
            for index, annotation in enumerate(self.canvas.annotations):
                incomplete = not has_encasing(annotation)
                issues = validate_annotation(annotation, *self.canvas.image_size, require_complete=not incomplete)
                if issues:
                    self._workflow_notice(f"Target {index+1} is incomplete: " + " ".join(issues) + " Finish every pair before moving next.", True)
                    return False
                if incomplete:
                    self._checkpoint_review()
                    self._workflow_notice(f"Outline accepted. Generating target {index+1} of {len(self.canvas.annotations)} on this image.")
                    return self._outline_existing_box(annotation)
                if annotation.get("draft_review"):
                    self.canvas.select(annotation["id"])
                    self._checkpoint_review()
                    self._workflow_notice(f"Check target {index+1} of {len(self.canvas.annotations)}. Enter accepts it; Edit… corrects it.")
                    return True
            if self._unsaved and not self._persist_current():
                return False
            record = self.project.record(image_id)
            prior_review = bool(record.get("reviewed"))
            try:
                self.project.set_reviewed(image_id, True)
            except Exception as exc:
                self._workflow_notice("The outline is saved, but this image could not be marked reviewed: " + str(exc), True)
                return False
            if self._review_session is not None and not prior_review:
                self._review_session.data["reviews"].append({"image_id": image_id,
                    "digest": annotation_digest(self.canvas.annotations), "prior_reviewed": False})
                self._review_session.data["flagged"] = [key for key in self._review_session.data["flagged"] if key != image_id]
            if self._review_session is not None:
                self._review_session.data["target_drafts"].pop(image_id, None)
                self._review_session.data["drafts"].pop(image_id, None)
            self._reviewed_targets.update((image_id, ann["id"]) for ann in self.canvas.annotations)
            self._reviewed_images.add(image_id)
            self._checkpoint_review()
            self._refresh_review()
            self._refresh_image_record()
            self._refresh_summary()
            self._refresh_review_metrics()
            return self._advance_review_image()
        finally:
            self._advancing_image = False

    def _advance_review_image(self):
        records = self.project.manifest["images"]
        index = next((i for i, row in enumerate(records) if row["id"] == self.current_image_id), -1)
        following = next((row for row in records[index+1:] if self._record_visible(row)), None)
        if following is None:
            self._workflow_at_end = True
            self.canvas.set_tool("box")
            self.canvas.setFocus()
            self._workflow_notice("Reviewed · end of image set. All targets on this image are saved. There is no next image.")
            return True
        if not self._load_image(following["id"]):
            self._workflow_notice("This image is reviewed, but the next image could not be opened. Its labels were not changed.", True)
            return False
        if self._navigation_started is not None:
            self._last_navigation_ms = (time.perf_counter() - self._navigation_started) * 1000
            self._navigation_started = None
            self._refresh_review_metrics()
        return True

    def _record_visible(self, record):
        mode = self.review_filter.currentData() if hasattr(self, "review_filter") else "all"
        if mode == "unreviewed" and record.get("reviewed"):
            return False
        if mode == "reviewed" and not record.get("reviewed"):
            return False
        if mode == "flagged" and (self._review_session is None or record["id"] not in self._review_session.data["flagged"]):
            return False
        needle = self.image_filter.text().strip().lower()
        return not needle or needle in self._image_text(record).lower()

    def _filter_images(self, *_):
        if self.project is None:
            return
        for index in range(self.image_list.count()):
            item = self.image_list.item(index)
            try:
                item.setHidden(not self._record_visible(self.project.record(item.data(Qt.ItemDataRole.UserRole))))
            except KeyError:
                item.setHidden(True)

    def _previous_review_image(self):
        if self.project is None or self._workflow_is_busy():
            return False
        records = self.project.manifest["images"]
        index = next((i for i, row in enumerate(records) if row["id"] == self.current_image_id), 0)
        return self._navigate_review_to(records[index-1]["id"]) if index > 0 else False

    def _navigate_review_to(self, image_id):
        if self._unsaved and not self._persist_current():
            return False
        if not self._checkpoint_review():
            return False
        self._keep_recovery_draft = True
        try:
            self._reject_assist()
            self.canvas.discard_draft()
            return self._load_image(image_id)
        finally:
            self._keep_recovery_draft = False

    def _undo_last_review(self):
        if self._review_session is None or self._workflow_is_busy():
            return False
        if (self._unsaved and not self._persist_current()) or not self._checkpoint_review():
            return False
        reviews = self._review_session.data["reviews"]
        while reviews:
            previous = reviews[-1]
            try:
                record = self.project.record(previous["image_id"])
            except KeyError:
                reviews.pop()
                continue
            if annotation_digest(record["annotations"]) != previous.get("digest"):
                self._workflow_notice("This image changed after review. Reopen it to check the newer edits.", True)
                return False
            self.project.set_reviewed(record["id"], False)
            reviews.pop()
            if not self._navigate_review_to(record["id"]):
                return False
            self._checkpoint_review()
            self._workflow_notice("Last review undone. The saved boxes and outlines are ready to check again.")
            return True
        self._workflow_notice("There is no recent review to undo.")
        return False

    def _skip_review_image(self):
        if self.project is None or self.current_image_id is None or self._workflow_is_busy():
            return False
        if not self._checkpoint_review():
            return False
        self._keep_recovery_draft = True
        try:
            self._reject_assist()
            self.canvas.discard_draft()
            self.project.set_reviewed(self.current_image_id, False)
            self._review_session.data["flagged"].append(self.current_image_id)
            self._refresh_image_record()
            self._checkpoint_review()
            records = self.project.manifest["images"]
            index = next(i for i, row in enumerate(records) if row["id"] == self.current_image_id)
            if index + 1 < len(records):
                self._load_image(records[index+1]["id"])
            else:
                self._workflow_notice("Flagged for another look · end of image set.")
            self._refresh_image_record()
            return True
        finally:
            self._keep_recovery_draft = False

    def _negative_and_next(self):
        if self.project is None or self.current_image_id is None or self._workflow_is_busy():
            return False
        if self.canvas.annotations or self.canvas.has_assist or self.canvas.has_draft:
            self._workflow_notice("An empty negative must have no target labels. Review or remove the existing targets first.", True)
            return False
        self._navigation_started = time.perf_counter()
        prior_review = self.project.record(self.current_image_id).get("reviewed", False)
        try:
            self.project.set_reviewed(self.current_image_id, True)
        except Exception as exc:
            self._workflow_notice("Could not save this negative image: " + str(exc), True)
            return False
        if self._review_session is not None:
            if not prior_review:
                self._review_session.data["reviews"].append({"image_id": self.current_image_id,
                    "digest": annotation_digest([]), "prior_reviewed": False})
            self._review_session.data["flagged"] = [key for key in self._review_session.data["flagged"]
                                                   if key != self.current_image_id]
        self._reviewed_images.add(self.current_image_id)
        self._checkpoint_review()
        self._refresh_review()
        self._refresh_image_record()
        self._refresh_summary()
        return self._advance_review_image()

    def eventFilter(self, watched, event):
        if (watched in (getattr(self, "canvas", None), getattr(self, "image_list", None), getattr(self, "instance_list", None))
                and event.type() == QEvent.Type.KeyPress and QApplication.activeModalWidget() is None
                and self.tabs.currentWidget() == self.annotate_tab and not event.isAutoRepeat()):
            modifiers = event.modifiers()
            if event.key() == Qt.Key.Key_Left and modifiers == Qt.KeyboardModifier.AltModifier:
                self._previous_review_image()
                return True
            if modifiers == Qt.KeyboardModifier.NoModifier and event.key() in (Qt.Key.Key_S, Qt.Key.Key_X):
                self._skip_review_image() if event.key() == Qt.Key.Key_S else self._negative_and_next()
                return True
        return super().eventFilter(watched, event)

    def _annotations_changed(self, annotations):
        super()._annotations_changed(annotations)
        self._checkpoint_review()

    @Slot(object)
    def _assist_completed(self, outcome):
        if outcome.get("token") != self._assist_inflight_token:
            return
        batch = self._outline_batch_active and outcome.get("token") == self._outline_batch_token
        current = self._outline_batch_context == self._outline_image_key() if batch else True
        super()._assist_completed(outcome)
        if outcome.get("result"):
            elapsed = outcome["result"].get("timings_ms", {}).get("total")
            if isinstance(elapsed, (int, float)):
                self._outline_times.append(float(elapsed))
        if batch:
            if not current:
                self._finish_outline_batch(stale=True)
                return
            if outcome.get("cancelled") or self._outline_batch_paused or self._closing_review:
                self._remember_target_draft()
                self._clear_assist_state()
                self._finish_outline_batch(paused=True)
                return
            annotation = self.canvas.selected_annotation()
            self._outline_internal = True
            self.canvas.edit_locked = False
            try:
                accepted = bool(self._assist_result and has_encasing(self._assist_result)) and self._accept_assist()
                if accepted and not self._unsaved:
                    if annotation is not None:
                        self._outline_failures.pop(annotation["id"], None)
                else:
                    warnings = (self._assist_result or {}).get("warnings", [])
                    message = outcome.get("error") or " ".join(str(value) for value in warnings) or "No usable outline. Add a correction click or recompute."
                    if annotation is not None:
                        self._outline_failures[annotation["id"]] = message
                    if self.canvas.has_assist:
                        self._assist_result = dict(self._assist_result or {}, wrapper=None, mask=None, warnings=[message])
                        self._remember_target_draft()
                        self._clear_assist_state()
            finally:
                self._outline_internal = False
            self._outline_batch_token = None
            self._checkpoint_review()
            self._refresh_review_metrics()
            if self._unsaved:
                self._finish_outline_batch(paused=True)
                self._workflow_notice("Automatic outlines paused because an edit could not be saved. Resolve the save error before resuming or reviewing.", True)
            else:
                self._refresh_summary()
                QTimer.singleShot(0, self._run_next_outline)
            return
        if self._assist_result and has_encasing(self._assist_result) and self.canvas.assist_annotation_id:
            self._outline_failures.pop(self.canvas.assist_annotation_id, None)
        self._checkpoint_review()
        self._refresh_review_metrics()
        if not self._closing_review:
            self._schedule_image_work()
            if outcome.get("result"):
                self._prepare_upcoming_images()

    def _add_assist_point(self, point, label):
        before = len(self._assist_points)
        result = super()._add_assist_point(point, label)
        self._corrections += max(0, len(self._assist_points) - before)
        self._checkpoint_review()
        return result

    def _draft_changed(self, pending):
        super()._draft_changed(pending)
        self._checkpoint_review()

    def _accept_assist(self):
        annotation = self.canvas.selected_annotation()
        proposal = deepcopy(self._assist_result)
        points, labels = deepcopy(self._assist_points), list(self._assist_labels)
        if (not self._assist_busy and annotation is not None and proposal and has_encasing(proposal)
                and self._assist_context == self._assist_identity()
                and encasing_equal(proposal, annotation)):
            # Inspecting an existing good outline is a no-op: preserve manual
            # source tags, metadata, and undo history exactly.
            self._clear_assist_state()
            result = True
            self._refresh_summary()
        else:
            result = super()._accept_assist()
        if result and annotation is not None:
            current = next((row for row in self.canvas.annotations if row["id"] == annotation["id"]), None)
            if current is not None:
                self._remember_target_draft(current, result=proposal, points=points, labels=labels)
        self._checkpoint_review()
        return result

    def _reject_assist(self):
        annotation_id = self.canvas.assist_annotation_id
        if (not self._assist_busy and self._review_session is not None and annotation_id
                and not self._outline_preserving and not self._keep_recovery_draft and not self._closing_review):
            rows = self._review_session.data["target_drafts"].get(self.current_image_id, {})
            rows.pop(annotation_id, None)
        result = super()._reject_assist()
        self._checkpoint_review()
        return result

    def _refresh_review_metrics(self):
        if not hasattr(self, "review_metrics"):
            return
        minutes = max((time.monotonic() - self._review_started) / 60, .01)
        count = len(self._reviewed_targets)
        suffix = f" · latest outline {self._outline_times[-1]:.0f} ms" if self._outline_times else ""
        if self._last_navigation_ms is not None:
            suffix += f" · save + next {self._last_navigation_ms:.0f} ms"
        if self.project is not None and self._prepared_image_key == (str(self.project.root), self.current_image_id):
            suffix += " · image prepared"
        self.review_metrics.setText(f"This session: {len(self._reviewed_images)} reviewed images · {len(self._reviewed_images)/minutes:.1f} images/min · {count} targets · {self._corrections} correction clicks" + suffix)

    @Slot()
    def _job_finished(self):
        super()._job_finished()
        if self._outline_batch_active:
            QTimer.singleShot(0, self._run_next_outline)
        self._schedule_image_work()

    def _start_job(self, name, function, on_result, cancellable=False):
        # Image preparation uses the same Torch resources as training. Stop at
        # its next boundary before executing a generic worker; the wait stays
        # off the GUI thread and the ordinary job cancellation remains active.
        self._live_start_ticket += 1
        preparing = self._assist_thread.pause_preparation() if self._assist_thread is not None else None
        if preparing is None:
            return super()._start_job(name, function, on_result, cancellable)
        def after_preparation(progress, should_stop):
            tick = threading.Event()
            while preparing.is_set():
                tick.wait(.01)
            return function(progress, should_stop)
        return super()._start_job(name, after_preparation, on_result, cancellable)

    def _start_live(self):
        if self._closing_review or self._job is not None or self._assist_busy:
            return
        preparing = self._assist_thread.pause_preparation() if self._assist_thread is not None else None
        if preparing is not None and preparing.is_set():
            self._live_start_ticket += 1
            ticket = self._live_start_ticket
            self.statusBar().showMessage("Finishing image preparation before live capture…")
            def continue_start():
                if ticket == self._live_start_ticket and not self._closing_review:
                    self._start_live()
            QTimer.singleShot(25, continue_start)
            return
        return super()._start_live()

    def _stop_live(self):
        self._live_start_ticket += 1
        return super()._stop_live()

    def _refresh_summary(self):
        super()._refresh_summary()
        if hasattr(self, "yolo_button"):
            busy = self._job is not None or self._assist_busy or self._live_session is not None or self._outline_batch_active
            self.yolo_button.setEnabled(not busy)
            self.compare_button.setEnabled(self.project is not None and not busy)
            self.merge_button.setEnabled(self.project is not None and not busy)

    def _merge_dataset_dialog(self):
        if not self._need_project() or not self._ready_to_leave():
            return
        source = QFileDialog.getExistingDirectory(self, "Choose the Cloud project to merge into this dataset")
        if not source:
            return
        project = self.project
        def scan(progress, should_stop):
            from .dataset_merge import scan_project_merge
            return scan_project_merge(project, source)
        def show(plan):
            QTimer.singleShot(0, lambda: self._show_dataset_merge(plan, project))
        self._start_job("Inspect dataset merge", scan, show)

    def _show_dataset_merge(self, plan, project):
        if self.project is not project or self._closing_review:
            return
        if self._job is not None:
            QTimer.singleShot(25, lambda: self._show_dataset_merge(plan, project))
            return
        if plan.get("errors"):
            self._error("Datasets need attention before merging", "\n".join(plan["errors"][:10]))
            return
        summary = plan["summary"]
        dialog = QDialog(self)
        dialog.setWindowTitle("Merge into " + project.manifest.get("name", "current dataset"))
        dialog.resize(650, 450)
        layout = QVBoxLayout(dialog)
        info = QLabel(f"Add {summary['images_to_add']:,} new images. Reuse {summary['duplicate_images']:,} pixel-identical images.\n"
                      f"{summary.get('conflict_images', 0):,} duplicate images have different labels or review status.\n"
                      "The source project is kept. Saved masks, boxes and reviewed negative images are carried over.\n"
                      "All labels belong to the single player class.")
        info.setWordWrap(True)
        layout.addWidget(info)
        incoming_duplicates = summary.get("source_images", 0) > summary.get("unique_source_images", 0)
        if plan.get("warnings"):
            warning = QLabel("\n".join(plan["warnings"]))
            warning.setWordWrap(True)
            layout.addWidget(warning)
        policy = QComboBox()
        if incoming_duplicates:
            policy.addItem("Keep current labels; otherwise keep the first incoming record", "keep_current")
            policy.addItem("Use the last incoming record's labels and review status", "prefer_incoming")
        else:
            policy.addItem("Keep this dataset's labels on conflicting duplicates", "keep_current")
            policy.addItem("Use the incoming dataset's labels on conflicting duplicates", "prefer_incoming")
        policy.setToolTip("A duplicate image gets one complete annotation set. Combining overlapping boxes would count the same target twice. Choose which saved labels to keep when the sets differ.")
        layout.addWidget(policy)
        details = QPlainTextEdit(json.dumps({"summary": summary, "warnings": plan.get("warnings", []), "conflicts": plan.get("conflicts", [])}, ensure_ascii=False, indent=2))
        details.setReadOnly(True)
        layout.addWidget(details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Merge datasets")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_policy = policy.currentData()
        def merge(progress, should_stop):
            from .dataset_merge import merge_projects
            return merge_projects(project, plan, conflict_policy=selected_policy)
        def done(result):
            self._refresh_images()
            if self.current_image_id:
                self._load_image(self.current_image_id)
            elif project.manifest["images"]:
                self._load_image(project.manifest["images"][0]["id"])
            self._refresh_summary()
            self._show_result(result)
            self.statusBar().showMessage(f"Merged {result['images_added']:,} new images; {result['images_skipped']:,} duplicates reused; {result['images_replaced']:,} existing label sets replaced.")
        self._start_job("Merge datasets", merge, done)

    def _import_yolo_dialog(self, source_kind="folder"):
        if not self._need_project() or not self._ready_to_leave():
            return
        if source_kind == "yaml":
            source, _ = QFileDialog.getOpenFileName(self, "Choose the YOLO dataset YAML", "", "YOLO dataset (*.yaml *.yml)")
        else:
            source = QFileDialog.getExistingDirectory(self, "Choose a YOLO dataset folder (images and labels)")
        if source:
            project_root = str(self.project.root)
            def work(progress, should_stop):
                from .yolo import scan_yolo
                return scan_yolo(source)
            def done(plan):
                # The scan worker finishes before a second mutating job starts.
                QTimer.singleShot(50, lambda: self._choose_yolo_classes(plan, project_root))
            self._start_job("Read YOLO dataset", work, done)

    def _choose_yolo_classes(self, plan, project_root=None):
        if self.project is None or self._closing_review or (project_root is not None and str(self.project.root) != project_root):
            return
        if self._job is not None:
            QTimer.singleShot(25, lambda: self._choose_yolo_classes(plan, project_root))
            return
        if plan.get("errors"):
            self._error("YOLO dataset needs attention", "Fix the import issues and try again.", "\n".join(plan["errors"]))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Import YOLO boxes")
        dialog.resize(560, 500)
        layout = QVBoxLayout(dialog)
        summary = plan.get("summary", {})
        label = QLabel(f"{summary.get('images', 0)} images · {summary.get('boxes', 0)} boxes\n"
                       f"{summary.get('clipped_boxes', 0)} boxes clipped to image edges · "
                       f"{summary.get('empty_labels', 0) + summary.get('missing_labels', 0)} images without boxes\n"
                       "Checked class names are merged into one player class. Identical boxes under multiple checked names become one target. "
                       "Imported images stay unreviewed; X approves an empty negative.")
        label.setWordWrap(True)
        layout.addWidget(label)
        classes = QListWidget()
        for row in plan.get("classes", []):
            item = QListWidgetItem(f"{row['id']} · {row.get('name', row['id'])} · {row.get('boxes', 0)} boxes")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            player_alias = " ".join(str(row.get("name", "")).lower().replace("_", " ").split()) in {"player", "verified player"}
            item.setCheckState(Qt.CheckState.Checked if len(plan["classes"]) == 1 or player_alias else Qt.CheckState.Unchecked)
            classes.addItem(item)
        layout.addWidget(classes)
        if plan.get("warnings"):
            warning = QPlainTextEdit("\n".join(plan["warnings"]))
            warning.setReadOnly(True)
            warning.setMaximumHeight(130)
            layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Merge checked names as player" if len(plan.get("classes", [])) > 1 else "Import boxes")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        def refresh():
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(classes.count() == 0 or any(
                classes.item(i).checkState() == Qt.CheckState.Checked for i in range(classes.count())))
        classes.itemChanged.connect(refresh)
        refresh()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = [classes.item(i).data(Qt.ItemDataRole.UserRole) for i in range(classes.count())
                    if classes.item(i).checkState() == Qt.CheckState.Checked]
        self._import_yolo_plan(plan, selected)

    def _import_yolo_plan(self, plan, selected_classes=None):
        project = self.project
        def work(progress, should_stop):
            from .yolo import import_yolo
            return import_yolo(project, plan, selected_classes=selected_classes)
        def done(result):
            self._refresh_images()
            image_ids = result.get("image_ids", [])
            if image_ids:
                self._load_image(image_ids[0])
            self.tabs.setCurrentWidget(self.annotate_tab)
            self._show_result(result)
            self.statusBar().showMessage(f"Imported {result.get('images_added', 0)} images and {result.get('boxes_added', 0)} YOLO boxes "
                                         f"({result.get('clipped_boxes', 0)} clipped to image edges; {result.get('duplicate_boxes_merged', 0)} duplicate boxes merged). "
                                         "Outlines start automatically; X approves an empty negative.")
        return self._start_job("Import YOLO boxes", work, done)

    def _choose_initial_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a .cloud model to improve", "", "Cloud models (*.cloud)")
        if not path:
            return
        try:
            self._set_initial_model_path(path)
        except Exception as exc:
            self._error("Cannot initialize from this model", str(exc))

    def _compare_models_dialog(self):
        if not self._need_project() or not self._ready_to_leave():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose exactly two .cloud models to compare", "", "Cloud models (*.cloud)")
        if not paths:
            return
        if len(paths) != 2:
            self._error("Choose two models", "Select the older and newer .cloud model in the same file picker.")
            return
        split = self.eval_split.currentData()
        if split not in ("val", "test"):
            self._error("Use validation or test images", "Choose val or test for a model comparison.")
            return
        output = self.project.root / "comparisons" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
        project_path = self.project.root
        confidence = self.confidence_spin.value()
        def work(progress, should_stop):
            from .comparison import compare_models
            return compare_models(project_path, paths[0], paths[1], output, split=split,
                                  confidence=confidence, thresholds=[.15, .35, .55, .75] if split == "val" else None,
                                  progress=progress, should_stop=should_stop)
        def done(result):
            self._show_result({key: value for key, value in result.items() if key != "per_image"})
            self._last_comparison = result.get("report_path")
            self.open_comparison_button.setEnabled(bool(self._last_comparison))
            state = "stopped" if result.get("cancelled") else "complete"
            self.inference_status.setText(f"Comparison {state}. Open the report to inspect missed targets, false detections and outlines."
                                          if self._last_comparison else f"Comparison {state} before a report was produced.")
            self._open_comparison()
        self._start_job("Compare models", work, done, cancellable=True)

    def _open_comparison(self):
        if self._last_comparison:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_comparison)))

    def _open_integration_guide(self):
        path = Path(__file__).resolve().parents[1] / "docs/CLOUD_INTEGRATION.html"
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _preview_training_outline(self):
        if not self._need_image():
            return
        annotation = self.canvas.selected_annotation()
        if annotation is None or not has_encasing(annotation):
            self._error("Choose a complete target", "Select a target with an accepted encasing first.")
            return
        from .target_preview import preview_annotation_target
        from PIL import ImageDraw
        from .gui import _pil_qimage
        with Image.open(self.project.image_path(self.current_image_id)) as source:
            image = source.convert("RGB")
        from .config import ModelConfig
        settings = self._training_settings_values()
        config = ModelConfig(**{key: settings[key] for key in
                             ("architecture", "input_size", "vertices", "channels", "roi_size", "mask_size")})
        preview = preview_annotation_target(annotation, image.width, image.height, self.train_vertices.currentData(),
                                            config=config, mask_samples=settings["mask_target_samples"])
        if config.is_mask:
            import numpy as np
            from .masks import decode_mask
            truth = decode_mask(preview["original_mask"])
            sampled = decode_mask(preview["sampled_mask"])
            pixels = np.asarray(image).copy()
            for region, color in ((truth & sampled, (70, 240, 190)), (truth & ~sampled, (255, 95, 120)), (~truth & sampled, (255, 160, 60))):
                pixels[region] = (pixels[region].astype(float)*.4 + np.asarray(color)*.6).astype(np.uint8)
            image = Image.fromarray(pixels)
            description = f"{config.mask_size}×{config.mask_size} training mask. Green: retained foreground; pink: missed label pixels; orange: added background.\n"
            description += f"Source-label IoU after grid reconstruction: {preview['mask_iou']:.4f}. " + preview["message"]
        else:
            draw = ImageDraw.Draw(image)
            original = [tuple(point) for point in annotation["wrapper"]]
            reduced = [tuple(point) for point in preview["sampled_wrapper"]]
            draw.line(original + original[:1], fill=(70, 240, 190), width=3)
            draw.line(reduced + reduced[:1], fill=(255, 135, 80), width=2)
            description = f"Green: reviewed outline ({len(original)} vertices). Orange: training target ({len(reduced)} vertices).\n"
            description += preview["message"] + (f" Polygon IoU: {preview['polygon_iou']:.3f}." if preview["polygon_iou"] is not None else "")
        image.thumbnail((1100, 720))
        dialog = QDialog(self)
        dialog.setWindowTitle("Reviewed outline and actual training target")
        layout = QVBoxLayout(dialog)
        label = QLabel(description)
        label.setWordWrap(True)
        layout.addWidget(label)
        from PySide6.QtGui import QPixmap
        picture = QLabel()
        picture.setPixmap(QPixmap.fromImage(_pil_qimage(image)))
        layout.addWidget(picture)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def closeEvent(self, event):
        self._live_start_ticket += 1
        if self._job is not None or self._live_session is not None:
            return super().closeEvent(event)
        if not self._closing_review and self._checkpoint_review():
            self._closing_review = True
        if self._closing_review and not self._assist_busy:
            super()._reject_assist()
            self.canvas.discard_draft()
        super().closeEvent(event)
        if (not event.isAccepted() and not self._assist_busy and not self._assist_shutdown_close
                and self._job is None and self._live_session is None):
            self._closing_review = False
            self._restore_review_state()

