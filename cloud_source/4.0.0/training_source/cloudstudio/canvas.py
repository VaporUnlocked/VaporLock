"""Image-space editing canvas for independently labelled boxes and encasings.

No training libraries are imported here. A box is saved immediately as an
incomplete pair; its unfinished polygon draft is deliberately kept separate and
must be committed or explicitly discarded before changing images.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QMessageBox, QWidget

from .geometry import validate_annotation
from .masks import has_encasing, replace_encasing
from .mask_paint import make_mask_image


class AnnotationCanvas(QWidget):
    annotationsChanged = Signal(object)
    selectionChanged = Signal(str)
    hintChanged = Signal(str)
    toolChanged = Signal(str)
    draftChanged = Signal(bool)
    problem = Signal(str)
    assistPointAdded = Signal(object, int)
    assistAcceptRequested = Signal()
    assistRemovePointRequested = Signal()
    boxCommitted = Signal(str)
    advanceRequested = Signal()
    addTargetRequested = Signal()
    editRequested = Signal()
    manualOutlineRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(360, 260)
        self.image = QImage()
        self.annotations: list[dict] = []
        self.predictions: list[dict] = []
        self.diagnostic_boxes = False
        self.selected_id = ""
        self.tool = "box"
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.zoom = 1.0
        self.offset = QPointF()
        self._fit = True
        self._draft: list[list[float]] = []
        self._draft_id = ""
        self._cursor_img: QPointF | None = None
        self._box_start: QPointF | None = None
        self._box_end: QPointF | None = None
        self._drag = None
        self._drag_before = None
        self._pan_pos = None
        self._space_down = False
        self._undo: list[list[dict]] = []
        self._redo: list[list[dict]] = []
        self._max_history = 100
        self._mask_paint_cache = {}
        self.image_revision = 0
        self.content_revision = 0
        self.assist_annotation_id = ""
        self.assist_proposal = []
        self.assist_mask = None
        self.assist_points = []
        self.assist_labels = []
        self.assist_leave_guard = None
        self.edit_locked = False

    @property
    def has_draft(self):
        return bool(self._draft)

    @property
    def image_size(self):
        return self.image.width(), self.image.height()

    @property
    def has_assist(self):
        return bool(self.assist_annotation_id)

    def _resolve_assist(self):
        if not self.has_assist:
            return True
        if self.edit_locked:
            self.problem.emit("Outline assistance is running. Cancel it or wait for the result before editing labels.")
            return False
        if self.assist_leave_guard is not None:
            return bool(self.assist_leave_guard())
        self.problem.emit("Accept or reject the outline proposal before changing labels.")
        return False

    def begin_assist(self, annotation_id):
        if self.has_draft:
            self.problem.emit("Finish or discard the manual encasing draft before using outline assistance.")
            return False
        if not any(a["id"] == annotation_id for a in self.annotations):
            return False
        if self.has_assist and self.assist_annotation_id != annotation_id and not self._resolve_assist():
            return False
        self.assist_annotation_id = annotation_id
        self.selected_id = annotation_id
        self.update()
        return True

    def set_assist_preview(self, wrapper=None, points=None, labels=None, *, mask=None):
        self.assist_proposal = deepcopy(wrapper or [])
        self.assist_mask = deepcopy(mask)
        self.assist_points = deepcopy(points or [])
        self.assist_labels = list(labels or [])
        self.update()

    def clear_assist(self):
        self.assist_annotation_id = ""
        self.assist_proposal = []
        self.assist_mask = None
        self.assist_points = []
        self.assist_labels = []
        if self.tool in ("assist_fg", "assist_bg"):
            self.tool = "select"
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.toolChanged.emit("select")
        self.update()

    def accept_assist(self, wrapper=None, backend="Light HQ-SAM", *, mask=None, warnings=None, diagnostics=None):
        if self.edit_locked or not self.has_assist:
            return False
        index = next((i for i, a in enumerate(self.annotations) if a["id"] == self.assist_annotation_id), -1)
        if index < 0:
            return False
        candidate = deepcopy(replace_encasing(self.annotations[index], {"wrapper": wrapper, "mask": mask}))
        candidate["source"] = "assist"
        candidate["assist_warnings"] = list(warnings or [])
        candidate["assist_diagnostics"] = deepcopy(diagnostics or {})
        candidate["assist_backend"] = backend
        errors = validate_annotation(candidate, *self.image_size, require_complete=True)
        if errors:
            self.problem.emit("Outline cannot be accepted: " + "; ".join(errors))
            return False
        before = deepcopy(self.annotations)
        self.annotations[index] = candidate
        self.clear_assist()
        self._push_change(before)
        self.selectionChanged.emit(self.selected_id)
        self.hintChanged.emit("Assisted encasing saved as unreviewed. Drag its vertices to correct it, then check every pair before review.")
        return True

    def set_image(self, path: str | Path | None, annotations=None):
        # The caller must resolve drafts first. This catches accidental silent loss.
        if self.has_draft:
            raise RuntimeError("Finish or discard the encasing draft before changing images.")
        if self.has_assist:
            raise RuntimeError("Accept or discard outline assistance before changing images.")
        picture = QImage(str(path)) if path else QImage()
        if path and picture.isNull():
            raise ValueError(f"Could not read image: {path}")
        self.image = picture
        self.image_revision += 1
        self.content_revision += 1
        self.annotations = deepcopy(annotations or [])
        self.predictions = []
        self.selected_id = ""
        self._undo.clear()
        self._redo.clear()
        self._draft_id = ""
        self._drag = self._drag_before = None
        self._box_start = self._box_end = None
        self._fit = True
        self.fit_image()
        self.set_tool("box")
        self.selectionChanged.emit("")
        self.update()

    def selected_annotation(self):
        return next((a for a in self.annotations if a["id"] == self.selected_id), None)

    def select(self, annotation_id: str):
        if annotation_id != self.selected_id and (self.edit_locked or not self._resolve_assist()):
            return False
        if self.has_draft and annotation_id != self._draft_id:
            self.problem.emit("Finish the current encasing with Enter, or discard its draft first.")
            return False
        self.selected_id = annotation_id or ""
        self.selectionChanged.emit(self.selected_id)
        self.update()
        return True

    def set_tool(self, tool: str):
        if tool not in ("select", "box", "wrapper", "assist_fg", "assist_bg") or self.edit_locked:
            return False
        if tool in ("assist_fg", "assist_bg") and not self.has_assist:
            self.problem.emit("Draw a new box to start its outline automatically, or choose Recompute outline for an existing target.")
            return False
        if tool not in ("assist_fg", "assist_bg") and not self._resolve_assist():
            return False
        if self.has_draft and tool != "wrapper":
            self.problem.emit("Finish the encasing with Enter before switching tools, or discard its draft.")
            return
        self.tool = tool
        self.setCursor(Qt.CursorShape.CrossCursor if tool != "select" else Qt.CursorShape.ArrowCursor)
        self.toolChanged.emit(tool)
        hints = {
            "box": "Drag a box around the next target. Its outline is generated automatically; Enter approves the image and moves next.",
            "wrapper": "Click encasing points inside the selected box. Enter closes the outline, approves complete pairs, and moves next.",
            "select": "Drag a corner to resize a box, a vertex to edit the encasing, or inside the box to move the pair.",
            "assist_fg": "Left click keeps foreground; right click removes background. Enter accepts and moves next; N adds another target.",
            "assist_bg": "Click background to exclude it from the encasing. Green + points keep foreground; red − points remove background.",
        }
        self.hintChanged.emit(hints[tool])
        self.update()
        return True

    def fit_image(self):
        if self.image.isNull():
            return
        available_w = max(50, self.width() - 50)
        available_h = max(50, self.height() - 50)
        self.zoom = min(available_w / self.image.width(), available_h / self.image.height())
        self.offset = QPointF((self.width() - self.image.width() * self.zoom) / 2,
                              (self.height() - self.image.height() * self.zoom) / 2)
        self._fit = True
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fit_image()

    def image_point(self, position: QPointF, clamp=True):
        p = (position - self.offset) / self.zoom
        if clamp and not self.image.isNull():
            p.setX(min(max(0., p.x()), float(self.image.width())))
            p.setY(min(max(0., p.y()), float(self.image.height())))
        return p

    def _inside_image(self, p):
        return not self.image.isNull() and 0 <= p.x() <= self.image.width() and 0 <= p.y() <= self.image.height()

    def _selected_index(self):
        return next((i for i, a in enumerate(self.annotations) if a["id"] == self.selected_id), -1)

    def _push_change(self, before):
        if before == self.annotations:
            return
        self._undo.append(deepcopy(before))
        self._undo = self._undo[-self._max_history:]
        self._redo.clear()
        self.content_revision += 1
        self.annotationsChanged.emit(deepcopy(self.annotations))
        self.update()

    def replace_annotations(self, annotations):
        if self.edit_locked or self.has_assist:
            self.problem.emit("Accept or reject the outline assistance before replacing labels.")
            return False
        if self.has_draft:
            self.problem.emit("Finish or discard the encasing draft first.")
            return False
        before = deepcopy(self.annotations)
        self.annotations = deepcopy(annotations)
        self.selected_id = ""
        self._push_change(before)
        self.selectionChanged.emit("")
        return True

    def undo(self):
        if self.edit_locked or not self._resolve_assist():
            return
        if self.has_draft:
            self._draft.pop()
            self.draftChanged.emit(self.has_draft)
            self.update()
            return
        if self._undo:
            self._redo.append(deepcopy(self.annotations))
            self.annotations = self._undo.pop()
            self.content_revision += 1
            self.selected_id = ""
            self.annotationsChanged.emit(deepcopy(self.annotations))
            self.selectionChanged.emit("")
            self.update()

    def redo(self):
        if self.edit_locked or not self._resolve_assist():
            return
        if self.has_draft:
            self.problem.emit("Finish or discard the encasing draft before redoing edits.")
            return
        if self._redo:
            self._undo.append(deepcopy(self.annotations))
            self.annotations = self._redo.pop()
            self.content_revision += 1
            self.selected_id = ""
            self.annotationsChanged.emit(deepcopy(self.annotations))
            self.selectionChanged.emit("")
            self.update()

    def discard_draft(self):
        self._draft.clear()
        self._draft_id = ""
        self.draftChanged.emit(False)
        self.update()

    def resolve_draft(self, parent=None):
        if not self.has_draft:
            return True
        dialog = QMessageBox(parent or self)
        dialog.setWindowTitle("Unfinished encasing")
        dialog.setText("There is an unfinished encasing on this image.")
        dialog.setInformativeText("Finish and save it, discard only the draft points, or keep editing. The main box is already saved as an unreviewed pair.")
        finish = dialog.addButton("Finish encasing", QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton("Discard draft", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("Keep editing", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        if dialog.clickedButton() == finish:
            return self.finish_polygon()
        if dialog.clickedButton() == discard:
            self.discard_draft()
            return True
        return False

    def finish_polygon(self):
        if self.edit_locked or self.has_assist:
            self.problem.emit("Use Accept outline for the proposal, or reject assistance before drawing manually.")
            return False
        if len(self._draft) < 3:
            self.problem.emit("An encasing needs at least three distinct points.")
            return False
        index = next((i for i, a in enumerate(self.annotations) if a["id"] == self._draft_id), -1)
        if index < 0:
            self.problem.emit("Select a main box before drawing its encasing.")
            return False
        candidate = deepcopy(replace_encasing(self.annotations[index], {"wrapper": self._draft}))
        candidate.pop("assist_warnings", None)
        candidate.pop("assist_diagnostics", None)
        candidate["source"] = "manual"
        errors = validate_annotation(candidate, *self.image_size, require_complete=True)
        if errors:
            self.problem.emit("Encasing cannot be saved: " + "; ".join(errors))
            return False
        before = deepcopy(self.annotations)
        self.annotations[index] = candidate
        self.discard_draft()
        self._push_change(before)
        self.set_tool("select")
        self.hintChanged.emit("Box and encasing saved. Mark this image Reviewed after checking every pair.")
        return True

    def delete_selected(self):
        if self.edit_locked or not self._resolve_assist():
            return
        if self.has_draft:
            self.problem.emit("Finish or discard the encasing draft before deleting an instance.")
            return
        if self.selected_id:
            before = deepcopy(self.annotations)
            self.annotations = [a for a in self.annotations if a["id"] != self.selected_id]
            self.selected_id = ""
            self._push_change(before)
            self.selectionChanged.emit("")

    def clear_wrapper(self):
        if self.edit_locked or not self._resolve_assist():
            return
        ann = self.selected_annotation()
        if ann is None:
            self.problem.emit("Select an instance first.")
            return
        if self.has_draft:
            self.problem.emit("Finish or discard the current draft first.")
            return
        before = deepcopy(self.annotations)
        ann["wrapper"] = []
        for key in ("mask", "wrapper_is_approximate", "assist_warnings", "assist_diagnostics"):
            ann.pop(key, None)
        ann["source"] = "manual"
        self._push_change(before)
        self.set_tool("wrapper")

    def _hit_test(self, p):
        tolerance = 9 / self.zoom
        # Selected control points win over other overlapping instances.
        indices = list(range(len(self.annotations) - 1, -1, -1))
        selected = self._selected_index()
        if selected >= 0:
            indices.remove(selected)
            indices.insert(0, selected)
        for i in indices:
            a = self.annotations[i]
            if i == selected:
                for j, point in enumerate([] if a.get("mask") is not None else a.get("wrapper", [])):
                    if math.hypot(p.x() - point[0], p.y() - point[1]) <= tolerance:
                        return i, "vertex", j
                x1, y1, x2, y2 = a["box"]
                for j, point in enumerate(((x1,y1), (x2,y1), (x2,y2), (x1,y2))):
                    if math.hypot(p.x() - point[0], p.y() - point[1]) <= tolerance:
                        return i, "corner", j
            x1, y1, x2, y2 = a["box"]
            if x1 <= p.x() <= x2 and y1 <= p.y() <= y2:
                return i, "move", None
        return None

    def mousePressEvent(self, event):
        self.setFocus()
        pos = event.position()
        if event.button() == Qt.MouseButton.MiddleButton or (self._space_down and event.button() == Qt.MouseButton.LeftButton):
            self._pan_pos = pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self.edit_locked:
            return
        if self.tool in ("assist_fg", "assist_bg"):
            if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton) or self.image.isNull():
                return
            p = self.image_point(pos, clamp=False)
            if (not self._inside_image(p) or not self.has_assist
                    or p.x() >= self.image.width() or p.y() >= self.image.height()):
                return
            ann = self.selected_annotation()
            label = 0 if event.button() == Qt.MouseButton.RightButton or self.tool == "assist_bg" else 1
            if ann is None:
                return
            x1, y1, x2, y2 = ann["box"]
            if label and not (x1 <= p.x() <= x2 and y1 <= p.y() <= y2):
                self.problem.emit("Foreground clicks belong inside the selected main box. Resize the box first if needed.")
                return
            self.assistPointAdded.emit([p.x(), p.y()], label)
            return
        if event.button() == Qt.MouseButton.RightButton and self.tool == "wrapper":
            if self._draft:
                self._draft.pop()
                self.draftChanged.emit(self.has_draft)
                self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton or self.image.isNull():
            return
        p = self.image_point(pos, clamp=False)
        if not self._inside_image(p):
            return
        if not self._resolve_assist():
            return
        if self.tool == "box":
            self._box_start = self._box_end = p
        elif self.tool == "wrapper":
            ann = self.selected_annotation()
            if ann is None:
                hit = self._hit_test(p)
                if hit:
                    self.select(self.annotations[hit[0]]["id"])
                else:
                    self.problem.emit("Draw or select the main box first; the encasing belongs to that box.")
                return
            if ann.get("wrapper") and not self.has_draft:
                self.problem.emit("This target already has an encasing. Use Edit… to adjust its vertices or draw a replacement manually.")
                return
            x1, y1, x2, y2 = ann["box"]
            if not (x1 <= p.x() <= x2 and y1 <= p.y() <= y2):
                self.problem.emit("Place encasing points inside the main box. Resize the box first if needed.")
                return
            if self._draft and math.hypot(p.x() - self._draft[-1][0], p.y() - self._draft[-1][1]) < 1e-5:
                return
            if len(self._draft) >= 3 and math.hypot(p.x() - self._draft[0][0], p.y() - self._draft[0][1]) < 8 / self.zoom:
                self.finish_polygon()
                return
            self._draft_id = ann["id"]
            self._draft.append([p.x(), p.y()])
            self.draftChanged.emit(True)
        else:
            hit = self._hit_test(p)
            if hit:
                index, kind, handle = hit
                if not self.select(self.annotations[index]["id"]):
                    return
                self._drag = (index, kind, handle, p)
                self._drag_before = deepcopy(self.annotations)
            else:
                self.select("")
        self.update()

    def mouseMoveEvent(self, event):
        if self._pan_pos is not None:
            self.offset += event.position() - self._pan_pos
            self._pan_pos = event.position()
            self._fit = False
            self.update()
            return
        p = self.image_point(event.position())
        self._cursor_img = p
        if self._box_start is not None:
            self._box_end = p
        if self._drag is not None and self._drag_before is not None:
            index, kind, handle, start = self._drag
            original = self._drag_before[index]
            ann = deepcopy(original)
            if kind == "vertex":
                x1, y1, x2, y2 = ann["box"]
                ann["wrapper"][handle] = [max(x1, min(x2, p.x())), max(y1, min(y2, p.y()))]
            elif kind == "corner":
                box = list(ann["box"])
                if handle in (0, 3):
                    box[0] = min(p.x(), box[2] - 1)
                else:
                    box[2] = max(p.x(), box[0] + 1)
                if handle in (0, 1):
                    box[1] = min(p.y(), box[3] - 1)
                else:
                    box[3] = max(p.y(), box[1] + 1)
                ann["box"] = box
            else:
                x1, y1, x2, y2 = original["box"]
                dx = max(-x1, min(self.image.width() - x2, p.x() - start.x()))
                dy = max(-y1, min(self.image.height() - y2, p.y() - start.y()))
                if original.get("mask") is not None:
                    # Integer translation preserves every stored label pixel.
                    dx, dy = round(dx), round(dy)
                    ann["mask"]["origin"] = [original["mask"]["origin"][0] + dx,
                                               original["mask"]["origin"][1] + dy]
                ann["box"] = [x1 + dx, y1 + dy, x2 + dx, y2 + dy]
                ann["wrapper"] = [[x + dx, y + dy] for x, y in (original.get("wrapper") or [])]
            ann["source"] = "manual"
            self.annotations[index] = ann
        self.update()

    def mouseReleaseEvent(self, event):
        if self._pan_pos is not None:
            self._pan_pos = None
            self.setCursor(Qt.CursorShape.CrossCursor if self.tool != "select" else Qt.CursorShape.ArrowCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._box_start is not None:
            start, end = self._box_start, self._box_end or self._box_start
            self._box_start = self._box_end = None
            x1, x2 = sorted((start.x(), end.x()))
            y1, y2 = sorted((start.y(), end.y()))
            if x2 - x1 >= 2 and y2 - y1 >= 2:
                before = deepcopy(self.annotations)
                ann = {"id": uuid4().hex, "class_id": 0, "box": [x1, y1, x2, y2], "wrapper": [], "source": "manual"}
                self.annotations.append(ann)
                self.selected_id = ann["id"]
                self._push_change(before)
                self.selectionChanged.emit(self.selected_id)
                self.set_tool("wrapper")
                # annotationsChanged is synchronous in the GUI thread, so the
                # owner has completed autosave before assistance is requested.
                self.boxCommitted.emit(ann["id"])
            else:
                self.problem.emit("Draw a box at least 2 pixels wide and high.")
        if self._drag is not None and self._drag_before is not None:
            index = self._drag[0]
            errors = validate_annotation(self.annotations[index], *self.image_size, require_complete=False)
            before = self._drag_before
            self._drag = self._drag_before = None
            if errors:
                self.annotations = before
                self.problem.emit("Edit reverted: " + "; ".join(errors))
            else:
                self._push_change(before)
        self.update()

    def wheelEvent(self, event):
        if self.image.isNull():
            return
        p = self.image_point(event.position(), clamp=False)
        target = max(.025, min(30., self.zoom * (1.2 ** (event.angleDelta().y() / 120))))
        self.zoom = target
        self.offset = event.position() - p * target
        self._fit = False
        self.update()
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key.Key_Space:
            self._space_down = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif key in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            if not event.isAutoRepeat() and not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
                self.advanceRequested.emit()
        elif key == Qt.Key.Key_N and not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
            if not event.isAutoRepeat():
                self.addTargetRequested.emit()
        elif self.edit_locked and key != Qt.Key.Key_F:
            return
        elif key == Qt.Key.Key_Backspace and self.has_assist:
            self.assistRemovePointRequested.emit()
        elif key == Qt.Key.Key_Escape and self.has_assist:
            self._resolve_assist()
        elif key == Qt.Key.Key_Backspace and self.has_draft:
            self._draft.pop()
            self.draftChanged.emit(self.has_draft)
            self.update()
        elif key == Qt.Key.Key_Escape and self.has_draft:
            self.resolve_draft(self)
        elif key == Qt.Key.Key_Delete:
            self.delete_selected()
        elif key == Qt.Key.Key_Z and modifiers & Qt.KeyboardModifier.ControlModifier:
            self.redo() if modifiers & Qt.KeyboardModifier.ShiftModifier else self.undo()
        elif key == Qt.Key.Key_Y and modifiers & Qt.KeyboardModifier.ControlModifier:
            self.redo()
        elif key == Qt.Key.Key_B:
            self.set_tool("box")
        elif key == Qt.Key.Key_P:
            self.manualOutlineRequested.emit()
        elif key == Qt.Key.Key_V:
            self.editRequested.emit()
        elif key == Qt.Key.Key_F:
            self.fit_image()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._space_down = False
            self.setCursor(Qt.CursorShape.CrossCursor if self.tool != "select" else Qt.CursorShape.ArrowCursor)
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._space_down = False
        super().focusOutEvent(event)

    def _draw_encasing(self, painter, annotation, color, fill_alpha=20, dashed=False):
        mask = annotation.get("mask")
        if mask is None:
            return self._draw_polygon(painter, annotation.get("wrapper") or [], color, fill_alpha, dashed)
        key = (id(mask), color.rgba(), fill_alpha)
        cached = self._mask_paint_cache.get(key)
        if cached is None or cached[0] is not mask:
            picture, origin = make_mask_image(mask, color, fill_alpha)
            if len(self._mask_paint_cache) >= 32:
                self._mask_paint_cache.pop(next(iter(self._mask_paint_cache)))
            cached = self._mask_paint_cache[key] = (mask, picture, origin)
        painter.drawImage(QPointF(*cached[2]), cached[1])

    def _draw_polygon(self, painter, points, color, fill_alpha=20, dashed=False):
        if not points:
            return
        pen = QPen(color, 1.8 / self.zoom)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        fill = QColor(color)
        fill.setAlpha(fill_alpha)
        painter.setBrush(fill)
        polygon = QPolygonF([QPointF(float(x), float(y)) for x, y in points])
        painter.drawPolygon(polygon)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0b1018"))
        if self.image.isNull():
            painter.setPen(QColor("#93a6bc"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Draw a box. Check the outline. Press Enter.\n\nCreate or open a project, then import images.\nThe selected outline helper runs automatically after each new box.")
            return
        painter.save()
        painter.translate(self.offset)
        painter.scale(self.zoom, self.zoom)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1.0)
        painter.drawImage(QPointF(0, 0), self.image)
        painter.setClipRect(QRectF(0, 0, self.image.width(), self.image.height()))
        for prediction in self.predictions:
            self._draw_encasing(painter, prediction, QColor("#c49bff"), 15, True)
            if self.diagnostic_boxes and "primary_box" in prediction:
                x1, y1, x2, y2 = prediction["primary_box"]
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#c49bff"), 1.0 / self.zoom, Qt.PenStyle.DotLine))
                painter.drawRect(QRectF(x1,y1,x2-x1,y2-y1))
        for index, ann in enumerate(self.annotations):
            selected = ann["id"] == self.selected_id
            incomplete = not has_encasing(ann)
            color = QColor("#ffba76" if incomplete else "#5de1ca" if selected else "#5fbded")
            x1,y1,x2,y2 = ann["box"]
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, (1.7 if selected else 1.0) / self.zoom, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(x1,y1,x2-x1,y2-y1))
            self._draw_encasing(painter, ann, color, 25 if selected else 12)
            if selected:
                radius = 4 / self.zoom
                painter.setBrush(QColor("#0b1018"))
                painter.setPen(QPen(color, 1.5 / self.zoom))
                for vx, vy in ([] if ann.get("mask") is not None else ann.get("wrapper", [])):
                    painter.drawEllipse(QPointF(vx,vy),radius,radius)
                for cx,cy in ((x1,y1),(x2,y1),(x2,y2),(x1,y2)):
                    painter.drawRect(QRectF(cx-radius,cy-radius,radius*2,radius*2))
            # Labels use screen-sized text rather than enormous text on zoom.
            painter.save()
            painter.translate(x1,y1)
            painter.scale(1/self.zoom,1/self.zoom)
            painter.setPen(color)
            label = f"{index+1}  player" + (" · encasing needed" if incomplete else "")
            painter.fillRect(QRectF(0,0,len(label)*6.9+12,22),QColor(7,15,22,210))
            painter.drawText(QPointF(6,15),label)
            painter.restore()
        if self.assist_mask is not None or self.assist_proposal:
            self._draw_encasing(painter, {"mask": self.assist_mask, "wrapper": self.assist_proposal}, QColor("#d3a5ff"), 65, True)
        for point, label in zip(self.assist_points, self.assist_labels):
            center = QPointF(float(point[0]), float(point[1]))
            color = QColor("#70f2bb" if label else "#ff8c8c")
            painter.setPen(QPen(color, 1.8 / self.zoom))
            painter.setBrush(QColor(12, 20, 30, 235))
            painter.drawEllipse(center, 6 / self.zoom, 6 / self.zoom)
            radius = 3 / self.zoom
            painter.drawLine(center + QPointF(-radius, 0), center + QPointF(radius, 0))
            if label:
                painter.drawLine(center + QPointF(0, -radius), center + QPointF(0, radius))
        if self._box_start is not None and self._box_end is not None:
            painter.setPen(QPen(QColor("#5de1ca"),1.5/self.zoom,Qt.PenStyle.DashLine))
            painter.setBrush(QColor(93,225,202,20))
            painter.drawRect(QRectF(self._box_start,self._box_end).normalized())
        if self._draft:
            painter.setPen(QPen(QColor("#ffcf88"),2/self.zoom))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF([QPointF(x,y) for x,y in self._draft]))
            if self._cursor_img is not None:
                painter.setPen(QPen(QColor("#ffcf88"),1/self.zoom,Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(*self._draft[-1]),self._cursor_img)
            painter.setPen(QPen(QColor("#ffcf88"),1.5/self.zoom))
            painter.setBrush(QColor("#101923"))
            for x,y in self._draft:
                painter.drawEllipse(QPointF(x,y),4/self.zoom,4/self.zoom)
            painter.setBrush(QColor("#ffcf88"))
            painter.drawEllipse(QPointF(*self._draft[0]),5/self.zoom,5/self.zoom)
        painter.restore()
        painter.setPen(QColor("#8fa1b5"))
        painter.drawText(QPointF(14,self.height()-13),f"{self.image.width()} × {self.image.height()} px  ·  {self.zoom*100:.0f}%")
