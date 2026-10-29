"""Local Cloud Studio desktop application.

Annotation and capture start without importing torch. Model, export, evaluation
and training imports happen inside worker jobs when explicitly requested.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
from uuid import uuid4

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget, QSizePolicy,
)

from . import __version__
from .canvas import AnnotationCanvas
from .data import Project
from .geometry import validate_annotation
from .masks import has_encasing, replace_encasing
from .mask_paint import make_mask_image


STYLE = """
QWidget { background: #101620; color: #dce7f2; font-size: 13px; }
QMainWindow { background: #101620; }
QLabel#brand { color: #f4fbff; font-size: 24px; font-weight: 700; }
QLabel#eyebrow { color: #6ce3cb; font-size: 10px; font-weight: 700; }
QLabel#subtitle { color: #96abc0; }
QLabel#statusGood { color: #6ce3cb; }
QLabel#statusWarning { color: #ffc38b; }
QLabel#sectionTitle { font-size: 18px; font-weight: 600; color: #f0f6fc; }
QFrame#header { border-bottom: 1px solid #283549; }
QPushButton { background: #202c3d; border: 1px solid #34445b; border-radius: 6px; padding: 7px 11px; }
QPushButton:hover { background: #2a3c50; border-color: #5a7b96; }
QPushButton:pressed, QPushButton:checked { background: #234b50; border-color: #60d8c2; color: #8df1de; }
QPushButton:disabled { color: #67778a; background: #182130; border-color: #263244; }
QPushButton#primary { background: #71e2c8; color: #0d2423; font-weight: 700; border-color: #71e2c8; }
QPushButton#primary:hover { background: #95f1da; }
QPushButton#primary:disabled { background: #2c4f4d; color: #779895; border-color: #2c4f4d; }
QPushButton#danger { color: #ffb6aa; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #0d141e; border: 1px solid #34435a; border-radius: 5px; padding: 6px; selection-background-color: #346b77; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #6ce3cb; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: #67778a; border-color: #263244; background: #101925; }
QCheckBox:disabled { color: #67778a; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #1e2b3b; selection-background-color: #315566; }
QListWidget { background: #111b28; border: 1px solid #2c3b50; border-radius: 7px; padding: 4px; }
QListWidget::item { border-radius: 4px; padding: 9px 7px; border-bottom: 1px solid #202e40; }
QListWidget::item:selected { background: #234655; color: #b8fff0; }
QListWidget::item:hover { background: #213344; }
QPlainTextEdit { background: #0c141f; color: #b6c9dd; border: 1px solid #2b3c51; border-radius: 6px; font-family: Consolas, monospace; font-size: 12px; }
QTabWidget::pane { border: 1px solid #2b394d; border-radius: 6px; top: -1px; }
QTabBar::tab { color: #93a8be; background: #162131; padding: 11px 23px; margin-right: 3px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { color: #87ecd6; background: #223647; }
QTabBar::tab:hover { color: #e3eef8; }
QGroupBox { border: 1px solid #2e3d51; border-radius: 7px; margin-top: 12px; padding: 15px 11px 11px 11px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 11px; padding: 0 6px; color: #b5cee1; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #566d85; border-radius: 3px; background: #131e2b; }
QCheckBox::indicator:checked { background: #6ce3cb; border: 3px solid #37615f; }
QProgressBar { background: #10212c; border: 1px solid #324c5c; border-radius: 5px; text-align: center; min-height: 19px; }
QProgressBar::chunk { background: #3b9c91; border-radius: 4px; }
QSplitter::handle { background: #1b2b3b; width: 4px; height: 4px; }
QScrollArea { border: none; }
QToolTip { color: #e4f2fa; background: #263a4d; border: 1px solid #5a788e; padding: 5px; }
QStatusBar { color: #99b2c9; }
"""


def _label(text, name=None, wrap=False):
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    if wrap:
        label.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Minimum)
    return label


def _button(text, callback, primary=False):
    button = QPushButton(text)
    if primary:
        button.setObjectName("primary")
    button.clicked.connect(callback)
    return button


def _spin(low, high, value, step=1):
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setSingleStep(step)
    widget.setValue(value)
    return widget


def _double(low, high, value, step=.05, decimals=3):
    widget = QDoubleSpinBox()
    widget.setDecimals(decimals)
    widget.setRange(low, high)
    widget.setSingleStep(step)
    widget.setValue(value)
    return widget


def _combo(values, selected=None):
    widget = QComboBox()
    for value in values:
        if isinstance(value, tuple):
            widget.addItem(value[0], value[1])
        else:
            widget.addItem(str(value), value)
    if selected is not None:
        index = widget.findData(selected)
        if index >= 0:
            widget.setCurrentIndex(index)
    return widget


def _pil_qimage(image):
    rgb = image.convert("RGB")
    return QImage(rgb.tobytes(), rgb.width, rgb.height, rgb.width * 3,
                  QImage.Format.Format_RGB888).copy()


class FramePreview(QWidget):
    """A noninteractive capture view, separate from saved annotation geometry."""
    def __init__(self):
        super().__init__()
        self.picture = QImage()
        self.predictions = []
        self._mask_frames = []
        self.primary_candidates = []
        self.diagnostic = False
        self.setMinimumSize(360, 250)

    def set_frame(self, image, predictions=None, primary_candidates=None):
        self.picture = _pil_qimage(image) if image is not None else QImage()
        self.predictions = predictions or []
        self._mask_frames = [make_mask_image(row["mask"], QColor("#80f2d6"), 30)
                             if row.get("mask") is not None else None for row in self.predictions]
        self.primary_candidates = primary_candidates or []
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b1018"))
        if self.picture.isNull():
            painter.setPen(QColor("#93a6bc"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Choose a monitor or region, then start live detection.\n\nYou can also preview without a model\nand save frames to annotate.")
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width()/self.picture.width(), self.height()/self.picture.height())
        offset = QPointF((self.width()-self.picture.width()*scale)/2,
                         (self.height()-self.picture.height()*scale)/2)
        painter.translate(offset)
        painter.scale(scale, scale)
        painter.drawImage(QPointF(), self.picture)
        if self.diagnostic:
            for candidate in self.primary_candidates:
                if "primary_box" not in candidate:
                    continue
                x1,y1,x2,y2=candidate["primary_box"]
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#ffbe7a"),1.2/scale,Qt.PenStyle.DashLine))
                painter.drawRect(QRectF(x1,y1,x2-x1,y2-y1))
        for index, detection in enumerate(self.predictions):
            if detection.get("mask") is not None:
                picture, origin = self._mask_frames[index]
                painter.drawImage(QPointF(*origin), picture)
            else:
                polygon = QPolygonF([QPointF(float(x),float(y)) for x,y in detection.get("wrapper",[])])
                painter.setPen(QPen(QColor("#80f2d6"), 1.8/scale))
                painter.setBrush(QColor(87,221,195,20))
                painter.drawPolygon(polygon)
            if self.diagnostic and not self.primary_candidates and "primary_box" in detection:
                x1,y1,x2,y2 = detection["primary_box"]
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#cba2ff"),1.0/scale,Qt.PenStyle.DashLine))
                painter.drawRect(QRectF(x1,y1,x2-x1,y2-y1))


class Worker(QObject):
    result = Signal(object)
    progress = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, function, stop_event):
        super().__init__()
        self.function = function
        self.stop_event = stop_event

    @Slot()
    def run(self):
        try:
            result = self.function(self.progress.emit, self.stop_event.is_set)
            self.result.emit(result)
        except Exception:
            self.error.emit(traceback.format_exc())
        finally:
            self.finished.emit()


class OutlineAssistThread(QThread):
    """One persistent owner for MobileSAM and its bounded image cache."""
    completed = Signal(object)
    prepared = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CloudStudioOutlineAssist")
        self._requests = queue.Queue(maxsize=1)
        self._active = threading.Event()
        self._cancel = threading.Event()
        self._shutdown = threading.Event()
        self._clear_cache = threading.Event()
        self._preparations = []
        self._preparation_lock = threading.Lock()
        self._preparation_epoch = 0
        self._preparing = threading.Event()
        self._wake = threading.Event()

    def submit(self, request):
        if self._shutdown.is_set() or self._active.is_set():
            return False
        self._cancel.clear()
        self._active.set()
        try:
            self._requests.put_nowait(request)
            self._wake.set()
            return True
        except queue.Full:
            self._active.clear()
            return False

    def cancel_request(self):
        self._cancel.set()

    def clear_cache(self):
        self._clear_cache.set()
        self.prepare_images([])

    def prepare_images(self, requests):
        with self._preparation_lock:
            self._preparation_epoch += 1
            self._preparations = list(requests)[:2]
        self._wake.set()

    def pause_preparation(self):
        """Cancel queued/current preparation and expose its safe exit boundary."""
        self.prepare_images([])
        return self._preparing

    def _preparation_cancelled(self, epoch):
        with self._preparation_lock:
            replaced = epoch != self._preparation_epoch
        return replaced or self._shutdown.is_set() or self._active.is_set() or self._clear_cache.is_set()

    def request_shutdown(self):
        self._shutdown.set()
        self._cancel.set()
        self._wake.set()

    def run(self):
        assistant = None
        selected_device = None
        selected_backend = None
        try:
            while not self._shutdown.is_set():
                if self._clear_cache.is_set():
                    self._clear_cache.clear()
                    if assistant is not None:
                        assistant.clear_cache()
                try:
                    request = self._requests.get_nowait()
                except queue.Empty:
                    with self._preparation_lock:
                        preparation = self._preparations.pop(0) if self._preparations else None
                        epoch = self._preparation_epoch
                        if preparation is not None:
                            self._preparing.set()
                    if preparation is None:
                        self._wake.wait(.1)
                        self._wake.clear()
                        continue
                    try:
                        from .annotation_assist import AnnotationAssistant
                        if not callable(getattr(AnnotationAssistant, "prepare", None)):
                            continue
                        requested_backend = preparation.get("backend", "light_hq_sam")
                        if assistant is None or selected_device != preparation["device"] or selected_backend != requested_backend:
                            assistant = AnnotationAssistant(device=preparation["device"], backend=requested_backend)
                            selected_device, selected_backend = preparation["device"], requested_backend
                        with Image.open(preparation["path"]) as source:
                            result = assistant.prepare(source, cancel=lambda: self._preparation_cancelled(epoch))
                        if not self._preparation_cancelled(epoch):
                            self.prepared.emit({"identity": preparation["identity"], "result": result})
                    except Exception as exc:
                        if not self._preparation_cancelled(epoch):
                            self.prepared.emit({"identity": preparation["identity"], "error": str(exc)})
                    finally:
                        self._preparing.clear()
                    continue
                outcome = {"token": request["token"]}
                try:
                    from .annotation_assist import AnnotationAssistant, AssistCancelled
                    if self._cancel.is_set() or self._shutdown.is_set():
                        outcome["cancelled"] = True
                    else:
                        requested_backend = request.get("backend", "light_hq_sam")
                        if assistant is None or selected_device != request["device"] or selected_backend != requested_backend:
                            assistant = AnnotationAssistant(device=request["device"], backend=requested_backend)
                            selected_device, selected_backend = request["device"], requested_backend
                        outcome["result"] = assistant.propose(
                            request["image"], request["box"], request["points"], request["labels"],
                            max_vertices=request["max_vertices"], tolerance=1.5,
                            crop_mode=request.get("crop_mode", "auto"), refinement_token=request.get("refinement_token"),
                            cancel=lambda: self._cancel.is_set() or self._shutdown.is_set(),
                        )
                        if self._cancel.is_set() or self._shutdown.is_set():
                            outcome = {"token":request["token"],"cancelled":True}
                except Exception as exc:
                    if self._cancel.is_set() or self._shutdown.is_set() or exc.__class__.__name__ == "AssistCancelled":
                        outcome["cancelled"] = True
                    else:
                        outcome["error"] = str(exc)
                        outcome["details"] = traceback.format_exc()
                finally:
                    self._active.clear()
                    self._requests.task_done()
                self.completed.emit(outcome)
        finally:
            # Release model resources in the same thread that created them.
            assistant = None


class StudioBaseWindow(QMainWindow):
    def __init__(self, project_path=None, model_path=None, backend=None, device="cpu", live_tab=False):
        super().__init__()
        self.setWindowTitle("Cloud Studio · Box + encasing detection")
        self.resize(1480, 960)
        self.setMinimumSize(1024, 720)
        self.project = None
        self.current_image_id = None
        self._changing = False
        self._unsaved = False
        self._assist_thread = None
        self._assist_busy = False
        self._assist_cancel_pending = False
        self._assist_close_when_idle = False
        self._assist_shutdown_close = False
        self._assist_context = None
        self._assist_refinement_token = None
        self._assist_image = None
        self._assist_points = []
        self._assist_labels = []
        self._assist_prompt_revision = 0
        self._assist_request_serial = 0
        self._assist_token = None
        self._assist_inflight_token = None
        self._assist_result = None
        self._auto_assisted_boxes = set()
        self._advancing_image = False
        self._workflow_at_end = False
        self._job = None
        self._close_when_idle = False
        self._runtime = None
        self._model_path = None
        self._loaded_backend = None
        self._loaded_device = None
        self._loaded_kind = None
        self._suggestions = None
        self._capture = None
        self._last_frame = None
        self._frame_serial = 0
        self._capture_group = "capture-" + uuid4().hex
        self._capture_prediction = None
        self._samples_done = 0
        self._live_session = None
        self._live_frame = None
        self._live_stopping = False
        self._live_freeze_pending = False
        self._after_live_stop = None
        self._live_error = None
        self._live_fps = 0.
        self._live_started = None
        self._live_stale_ms = 1500
        self._live_preview_stale = False
        self._overlay = None
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(40)
        self._live_timer.timeout.connect(self._poll_live)
        self._capture_timer = QTimer(self)
        self._capture_timer.timeout.connect(self._preview_tick)
        self._sample_timer = QTimer(self)
        self._sample_timer.timeout.connect(self._sample_tick)
        self._build_ui()
        if backend is not None:
            backend_index = self._backend_index(backend, device)
            if backend_index >= 0:
                self.backend_combo.setCurrentIndex(backend_index)
        self.setStyleSheet(STYLE)
        self.statusBar().showMessage("Ready. Create a project or open an existing one.")
        self._refresh_summary()
        if project_path:
            try:
                self._open_project_path(project_path)
            except Exception as exc:
                self._error("Cannot open project", str(exc))
        if live_tab:
            self.tabs.setCurrentWidget(self.capture_tab)
        if model_path:
            QTimer.singleShot(0,lambda:self._load_model_path(model_path,backend=backend,device=device,open_live=live_tab))

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 12, 18, 8)
        layout.setSpacing(12)
        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 12)
        brand_layout = QVBoxLayout()
        brand_layout.setSpacing(3)
        brand_layout.addWidget(_label("CLOUD STUDIO", "brand"))
        brand_layout.addWidget(_label(f"BOX + ENCASING  /  LOCAL RESEARCH {__version__}", "eyebrow"))
        header_layout.addLayout(brand_layout)
        header_layout.addStretch()
        self.project_title = _label("No project open", "sectionTitle")
        self.dataset_summary = _label("Two labels per detection. One explicit review gate.", "subtitle")
        project_info = QVBoxLayout()
        project_info.addWidget(self.project_title)
        project_info.addWidget(self.dataset_summary)
        header_layout.addLayout(project_info)
        layout.addWidget(header)
        file_bar = QHBoxLayout()
        self.file_bar = file_bar
        self.new_button = _button("New project", self._new_project)
        self.open_button = _button("Open project", self._open_project)
        self.import_button = _button("Import images", self._import_images, True)
        self.video_button = _button("Import video frames", self._import_video)
        for button in (self.new_button,self.open_button,self.import_button,self.video_button):
            file_bar.addWidget(button)
        self.cancel_button = _button("Cancel task", self._cancel_job)
        self.cancel_button.hide()
        file_bar.addWidget(self.cancel_button)
        file_bar.addStretch()
        self.save_status = _label("Autosave after every completed edit", "subtitle")
        file_bar.addWidget(self.save_status)
        layout.addLayout(file_bar)
        self.tabs = QTabWidget()
        self.annotate_tab = self._build_annotation_tab()
        self.capture_tab = self._build_capture_tab()
        self.train_tab = self._build_training_tab()
        self.inspect_tab = self._build_inspection_tab()
        for live_control,inspect_control in (
            (self.live_confidence,self.confidence_spin),(self.live_nms,self.nms_spin),
            (self.live_maximum,self.maximum_detections),(self.live_minimum,self.minimum_height),
        ):
            live_control.valueChanged.connect(inspect_control.setValue)
            inspect_control.valueChanged.connect(live_control.setValue)
            live_control.valueChanged.connect(self._update_live_settings)
        self.live_diagnostic.toggled.connect(self.diagnostic_check.setChecked)
        self.diagnostic_check.toggled.connect(self.live_diagnostic.setChecked)
        self.tabs.addTab(self.annotate_tab, "01  Annotate")
        self.tabs.addTab(self.capture_tab, "02  Live capture")
        self.tabs.addTab(self.train_tab, "03  Train")
        self.tabs.addTab(self.inspect_tab, "04  Inspect & export")
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

    def _build_annotation_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(10,10,10,10)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.image_sidebar_layout = left_layout
        left_layout.setContentsMargins(0,0,0,0)
        left_layout.addWidget(_label("IMAGE SET", "eyebrow"))
        self.image_filter = QLineEdit()
        self.image_filter.setPlaceholderText("Filter images…")
        self.image_filter.textChanged.connect(self._filter_images)
        left_layout.addWidget(self.image_filter)
        self.image_list = QListWidget()
        self.image_list.setUniformItemSizes(True)
        self.image_list.installEventFilter(self)
        self.image_list.setMinimumWidth(190)
        self.image_list.currentItemChanged.connect(self._image_selected)
        left_layout.addWidget(self.image_list,1)
        self.split_groups_button = _button("Split by capture / source group", self._assign_splits)
        left_layout.addWidget(self.split_groups_button)
        left_layout.addWidget(_label("Nearby frames stay in the same split.\nOnly reviewed images enter training.", "subtitle", True))
        splitter.addWidget(left)
        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        self.annotation_layout = middle_layout
        middle_layout.setContentsMargins(0,0,0,0)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(5)
        self.tool_buttons = {}
        self.tool_group = QButtonGroup(self)
        self.accept_next_button = _button("Accept && next [Enter]",self._approve_and_advance,True)
        self.assist_accept_button = self.accept_next_button
        self.accept_next_button.setToolTip("Approve the visible outline, review the image only when every pair is complete, and open the next image.")
        self.add_target_button = _button("Add another target [N]",self._add_another_target)
        self.add_target_button.setToolTip("Keep the current valid outline and draw another target on this image. Review waits until you press Enter.")
        toolbar.addWidget(self.accept_next_button)
        toolbar.addWidget(self.add_target_button)
        self.annotation_edit_button = _button("Edit…",lambda:None)
        edit_menu = QMenu(self.annotation_edit_button)
        edit_menu.addAction("Edit box / outline [V]",self._edit_current_annotation)
        self.manual_outline_action = edit_menu.addAction("Draw encasing manually [P]",self._redraw_wrapper)
        edit_menu.addAction("Finish manual encasing",lambda:self.canvas.finish_polygon())
        self.annotation_edit_button.setMenu(edit_menu)
        toolbar.addWidget(self.annotation_edit_button)
        toolbar.addStretch()
        toolbar.addWidget(_button("Undo", lambda: self.canvas.undo()))
        toolbar.addWidget(_button("Redo", lambda: self.canvas.redo()))
        toolbar.addWidget(_button("Fit [F]", lambda: self.canvas.fit_image()))
        middle_layout.addLayout(toolbar)
        assist_group = QGroupBox("Automatic outline assistance")
        assist_layout = QVBoxLayout(assist_group)
        assist_top = QHBoxLayout()
        self.assist_backend = _combo([("Light HQ-SAM", "light_hq_sam"), ("MobileSAM", "mobile_sam")])
        self.assist_backend.setToolTip("Light HQ-SAM is the bundled default for more detailed boundaries. MobileSAM remains available for comparison. These pretrained annotation helpers do not initialize your detector.")
        self.assist_backend.currentIndexChanged.connect(self._assist_backend_changed)
        self.assist_crop = _combo([("Automatic detail crop", "auto"), ("Full image", "full"), ("Padded box crop", "crop")])
        self.assist_crop.setToolTip("Automatic uses a padded crop for small targets so more image pixels reach the helper. All correction clicks are included. A full image preserves wider context; a crop concentrates detail.")
        self.assist_crop.currentIndexChanged.connect(self._assist_backend_changed)
        self.assist_candidates = QComboBox()
        self.assist_candidates.setToolTip("Alternate masks from this same request. Switching is immediate. Self-score is the helper's estimate, not a measured accuracy or probability that the player is correct.")
        self.assist_candidates.currentIndexChanged.connect(self._assist_candidate_changed)
        self.assist_candidates.setVisible(False)
        self.assist_device = _combo([("CPU","cpu"),("NVIDIA CUDA","cuda")])
        self.assist_device.setToolTip("The outline helper is separate from the loaded detector. Backend changes take effect on the next assistance request.")
        self.assist_start_button = _button("Recompute outline",self._assist_button_clicked)
        self.assist_start_button.setMinimumWidth(145)
        self.assist_detail = _combo([64,128,256,512],128)
        self.assist_detail.setToolTip("Maximum vertices in the optional compatibility preview. The saved binary mask retains all helper pixels, holes and separate parts regardless of this number. Training mask resolution is a separate setting.")
        self.assist_detail.currentIndexChanged.connect(self._assist_detail_changed)
        assist_top.addWidget(self.assist_backend)
        assist_top.addWidget(self.assist_device)
        assist_top.addWidget(self.assist_start_button)
        assist_top.addWidget(_label("Detail", "subtitle"))
        assist_top.addWidget(self.assist_detail)
        assist_top.addStretch()
        assist_layout.addLayout(assist_top)
        assist_choices = QHBoxLayout()
        assist_choices.addWidget(self.assist_crop)
        assist_choices.addWidget(self.assist_candidates, 1)
        assist_choices.addStretch()
        assist_layout.addLayout(assist_choices)
        assist_tools = QHBoxLayout()
        assist_tools.setSpacing(5)
        for key, text in (("assist_fg","Foreground +"),("assist_bg","Background −")):
            button = _button(text,lambda checked=False,k=key:self._select_canvas_tool(k))
            button.setCheckable(True)
            button.setToolTip("Click the target to keep it. Right click in either assist tool marks background." if key=="assist_fg" else "Click background to exclude it from the encasing.")
            self.tool_group.addButton(button)
            self.tool_buttons[key] = button
            assist_tools.addWidget(button)
        self.assist_undo_click = _button("Undo click",self._remove_assist_point)
        self.assist_reset_clicks = _button("Reset clicks",self._reset_assist_points)
        self.assist_reject_button = _button("Reject",self._reject_assist)
        for button in (self.assist_undo_click,self.assist_reset_clicks,self.assist_reject_button):
            assist_tools.addWidget(button)
        assist_tools.addStretch()
        assist_layout.addLayout(assist_tools)
        self.assist_status = _label("Drag a box to generate its outline automatically. Check it, then press Enter to approve and open the next image.","subtitle",True)
        self.workflow_status = self.assist_status
        assist_layout.addWidget(self.assist_status)
        middle_layout.addWidget(assist_group)
        self.canvas = AnnotationCanvas()
        self.canvas.assist_leave_guard = self._resolve_assist
        self.canvas.assistPointAdded.connect(self._add_assist_point)
        self.canvas.assistAcceptRequested.connect(self._accept_assist)
        self.canvas.assistRemovePointRequested.connect(self._remove_assist_point)
        self.canvas.boxCommitted.connect(self._new_box_committed)
        self.canvas.advanceRequested.connect(self._approve_and_advance)
        self.canvas.addTargetRequested.connect(self._add_another_target)
        self.canvas.editRequested.connect(self._edit_current_annotation)
        self.canvas.manualOutlineRequested.connect(self._redraw_wrapper)
        self.canvas.annotationsChanged.connect(self._annotations_changed)
        self.canvas.selectionChanged.connect(self._canvas_selection_changed)
        self.canvas.toolChanged.connect(self._canvas_tool_changed)
        self.canvas.problem.connect(self._canvas_problem)
        self.canvas.hintChanged.connect(self._set_canvas_hint)
        self.canvas.draftChanged.connect(self._draft_changed)
        middle_layout.addWidget(self.canvas,1)
        self.canvas_hint = _label("Draw a box. The helper generates the exact encasing; left/right clicks refine it. Enter approves and advances.", "subtitle", True)
        middle_layout.addWidget(self.canvas_hint)
        self.annotation_shortcuts = _label("Enter: accept & next  ·  N: add another target  ·  Wheel: zoom  ·  Space + drag / middle drag: pan", "subtitle", True)
        middle_layout.addWidget(self.annotation_shortcuts)
        splitter.addWidget(middle)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0,0,0,0)
        right_layout.addWidget(_label("LINKED INSTANCES", "eyebrow"))
        self.instance_list = QListWidget()
        self.instance_list.installEventFilter(self)
        self.instance_list.setMinimumWidth(190)
        self.instance_list.currentItemChanged.connect(self._instance_selected)
        right_layout.addWidget(self.instance_list,1)
        self.pair_info = _label("Select an instance to inspect its two labels.", "subtitle", True)
        right_layout.addWidget(self.pair_info)
        self.edit_outline_button = _button("Correct encasing", self._correct_encasing)
        self.edit_outline_button.setToolTip("Refine an exact mask with foreground/background clicks, or edit vertices on a legacy polygon.")
        right_layout.addWidget(self.edit_outline_button)
        delete = _button("Delete selected [Del]", lambda: self.canvas.delete_selected())
        delete.setObjectName("danger")
        right_layout.addWidget(delete)
        review_group = QGroupBox("Image review")
        review_layout = QVBoxLayout(review_group)
        self.reviewed_check = QCheckBox("Reviewed for training")
        self.reviewed_check.setToolTip("All pairs must have a valid box and encasing. An empty reviewed image is a deliberate negative sample.")
        self.reviewed_check.toggled.connect(self._review_changed)
        review_layout.addWidget(self.reviewed_check)
        self.review_status = _label("Open an image to begin.", "subtitle", True)
        review_layout.addWidget(self.review_status)
        review_layout.addWidget(_button("Mark empty image as negative", self._mark_negative))
        self.split_combo = _combo(["train","val","test"])
        self.split_combo.currentIndexChanged.connect(self._split_changed)
        review_layout.addWidget(_label("Split for the whole source group", "subtitle"))
        review_layout.addWidget(self.split_combo)
        self.group_label = _label("", "subtitle", True)
        review_layout.addWidget(self.group_label)
        review_layout.addWidget(_button("Set source group", self._set_group))
        right_layout.addWidget(review_group)
        splitter.addWidget(right)
        splitter.setSizes([235,920,255])
        splitter.setStretchFactor(0,0)
        splitter.setStretchFactor(1,1)
        splitter.setStretchFactor(2,0)
        outer.addWidget(splitter,1)
        return page

    def _build_capture_tab(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14,14,14,14)
        controls = QWidget()
        side = QVBoxLayout(controls)
        side.setContentsMargins(0,0,10,0)
        side.addWidget(_label("See the live detections", "sectionTitle"))
        side.addWidget(_label("Choose the screen area you want the model to see. Live detection keeps each result paired with its captured frame.", "subtitle", True))
        live_group=QGroupBox("Live model")
        live_form=QFormLayout(live_group)
        self.live_model_status=_label("No model loaded. Load a .cloud model or an ONNX model.json.","subtitle",True)
        live_form.addRow(self.live_model_status)
        self.live_load_button=_button("Load model…",self._load_model)
        live_form.addRow(self.live_load_button)
        self.live_confidence=_double(.001,.999,.35,.05,3)
        self.live_nms=_double(.01,.99,.5,.05,2)
        self.live_maximum=_spin(1,256,32)
        self.live_minimum=_spin(0,8192,0)
        self.live_target_fps=_spin(1,60,10)
        self.live_target_fps.valueChanged.connect(self._update_live_settings)
        for label,field in (("Confidence",self.live_confidence),("Box NMS overlap",self.live_nms),("Maximum detections",self.live_maximum),("Minimum height (px)",self.live_minimum),("Maximum FPS",self.live_target_fps)):
            live_form.addRow(label,field)
        self.live_diagnostic=QCheckBox("Show first-stage boxes")
        self.live_diagnostic.setToolTip("Orange boxes show detections before invalid encasings are suppressed. Valid encasings remain teal.")
        self.overlay_check=QCheckBox("Show transparent desktop overlay")
        self.overlay_check.toggled.connect(self._overlay_choice_changed)
        self.overlay_status=_label("The preview works with or without a desktop overlay.","subtitle",True)
        self.live_start_button=_button("Start live detection",self._start_live,True)
        self.live_stop_button=_button("Stop detection",self._stop_live)
        self.live_freeze_button=_button("Freeze latest result",self._freeze_live_result)
        side.addWidget(live_group)
        group = QGroupBox("Capture source")
        form = QFormLayout(group)
        self.monitor_combo = QComboBox()
        self.monitor_combo.currentIndexChanged.connect(self._monitor_changed)
        form.addRow("Monitor", self.monitor_combo)
        self.monitor_refresh_button=_button("Refresh monitors", self._refresh_monitors)
        form.addRow(self.monitor_refresh_button)
        self.custom_region = QCheckBox("Use a custom screen region")
        self.custom_region.toggled.connect(self._region_toggle)
        form.addRow(self.custom_region)
        self.region_left = _spin(-32768,32768,0)
        self.region_top = _spin(-32768,32768,0)
        self.region_width = _spin(1,16384,1280)
        self.region_height = _spin(1,16384,720)
        for label,field in (("Left",self.region_left),("Top",self.region_top),("Width",self.region_width),("Height",self.region_height)):
            field.setEnabled(False)
            form.addRow(label,field)
        self.preview_ms = _spin(100,5000,500,100)
        self.preview_ms.setSuffix(" ms")
        form.addRow("Preview interval",self.preview_ms)
        side.insertWidget(2,group)
        start_row = QHBoxLayout()
        self.preview_start = _button("Start preview", self._start_preview, True)
        self.preview_stop = _button("Freeze / stop", self._stop_capture)
        start_row.addWidget(self.preview_start)
        start_row.addWidget(self.preview_stop)
        side.addLayout(start_row)
        self.capture_add = _button("Save frame → annotate", self._add_capture_frame)
        sample_group = QGroupBox("Bounded interval sampling")
        sample_form = QFormLayout(sample_group)
        self.sample_interval = _double(.5,3600.,2.0,.5,1)
        self.sample_interval.setSuffix(" s")
        self.sample_count = _spin(1,5000,20)
        self.capture_split = _combo(["train","val","test"])
        sample_form.addRow("Every",self.sample_interval)
        sample_form.addRow("Maximum frames",self.sample_count)
        sample_form.addRow("Save to split",self.capture_split)
        self.sample_start = _button("Start sampling", self._start_sampling)
        self.sample_stop = _button("Stop sampling", self._stop_sampling)
        sample_form.addRow(self.sample_start,self.sample_stop)
        self.sample_progress = QProgressBar()
        self.sample_progress.setRange(0,20)
        self.sample_progress.setValue(0)
        sample_form.addRow(self.sample_progress)
        side.addWidget(sample_group)
        side.addStretch()
        self.capture_status = _label("Capture idle. Nothing is recorded automatically.", "subtitle", True)
        side.addWidget(self.capture_status)
        capture_scroll=QScrollArea()
        capture_scroll.setWidgetResizable(True)
        capture_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        capture_scroll.setMinimumWidth(430)
        capture_scroll.setMaximumWidth(480)
        capture_scroll.setWidget(controls)
        layout.addWidget(capture_scroll)
        preview_panel = QVBoxLayout()
        self.live_status=_label("Ready. Load a model, choose a monitor or region, then start live detection.","subtitle",True)
        self.live_stats=_label("Main boxes —  ·  Encasings —  ·  Invalid —","eyebrow",True)
        self.live_timing=_label("FPS —  ·  Processing —  ·  Frame age —","subtitle",True)
        preview_panel.addWidget(self.live_status)
        preview_panel.addWidget(self.live_stats)
        preview_panel.addWidget(self.live_timing)
        live_actions=QHBoxLayout()
        for button in (self.live_start_button,self.live_stop_button,self.live_freeze_button,self.capture_add):
            live_actions.addWidget(button)
        live_actions.addStretch()
        preview_panel.addLayout(live_actions)
        display_choices=QHBoxLayout()
        display_choices.addWidget(self.overlay_check)
        display_choices.addWidget(self.live_diagnostic)
        display_choices.addStretch()
        preview_panel.addLayout(display_choices)
        preview_panel.addWidget(self.overlay_status)
        self.capture_preview = FramePreview()
        preview_panel.addWidget(self.capture_preview,1)
        preview_panel.addWidget(_label("One sampling session = one source group. Keep the whole group together when assigning train / validation / test splits.", "subtitle", True))
        preview_panel.addWidget(_label("Freeze a result to inspect it. Save frame → annotate keeps its matching model suggestions unreviewed until you check both labels.", "subtitle", True))
        preview_panel.addWidget(_label("Keep Cloud Studio outside the capture region, or minimize it while the desktop overlay runs, so the model sees your game instead of this preview.","subtitle",True))
        layout.addLayout(preview_panel,1)
        return page

    def _build_training_tab(self):
        from .training_settings import GROUPS, SETTINGS, resolve_settings

        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(14,14,14,14)
        config_container = QWidget()
        config_layout = QVBoxLayout(config_container)
        config_layout.setContentsMargins(0,0,12,0)
        config_layout.addWidget(_label("Train the two linked labels", "sectionTitle"))
        config_layout.addWidget(_label("A box learns the target's location and guides the encasing head. Hover any setting or label for its effect and tradeoff.", "subtitle", True))
        self._training_ui_loading = True
        self.training_controls = {}
        self.training_setting_labels = {}
        self.training_groups = {}

        data_group = QGroupBox("Recipe, initialization and output")
        data_form = QFormLayout(data_group)
        data_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        data_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.training_data_form = data_form
        self.train_data_status = _label("Create a project and review train + validation images.", "subtitle", True)
        data_form.addRow(self.train_data_status)
        self.training_preset = _combo([("Player · your low-augmentation recipe", "player"),
                                      ("Legacy · earlier Cloud defaults", "legacy")], "player")
        self._add_training_row(data_form, "Base recipe", self.training_preset,
            "Player starts with 300 epochs, 640-pixel input, AdamW, cosine learning-rate decay, gentle color/position/size variation, and fresh random weights. It is a starting recipe to validate on your data, not a proven optimum. Editing a field overrides this base recipe.")
        recipe_actions = QHBoxLayout()
        self.training_load_settings = _button("Load settings JSON", self._load_training_settings)
        self.training_save_settings = _button("Save settings JSON", self._save_training_settings)
        self.training_load_settings.setToolTip("Load a Cloud recipe or a run's config.json. All settings are checked before the form changes. Unsupported YOLO-only keys are reported instead of ignored.")
        self.training_save_settings.setToolTip("Save the current explicit settings and initialization paths to a reusable JSON file. Local model and checkpoint paths refer to this computer; the learned model is saved separately after training.")
        recipe_actions.addWidget(self.training_load_settings)
        recipe_actions.addWidget(self.training_save_settings)
        data_form.addRow(recipe_actions)
        self.training_initialization = _combo([("From scratch · new random weights", "scratch"),
                                              ("Improve an existing .cloud", "improve"),
                                              ("Resume an exact experiment", "resume")], "scratch")
        self._add_training_row(data_form, "Start this run", self.training_initialization,
            "From scratch creates new random weights with the selected architecture and no pretrained player knowledge. Improve starts from a chosen .cloud in a new run and optimizer. Resume restores a .cloudtrain checkpoint, including optimizer state, and requires unchanged data and compatible settings. Initialization does not determine inference speed.")
        self.init_model_path = QLineEdit()
        self.init_model_path.setReadOnly(True)
        self.init_model_path.setPlaceholderText("Choose the .cloud whose learned weights to improve")
        self.training_initial_label = self._add_training_row(data_form, "Initial .cloud", self.init_model_path,
            "Used only by Improve. Choosing a model loads its architecture, input size, feature channels, and encasing resolution automatically. Those capacities remain fixed so the weights keep their meaning. The source model is preserved.")
        self.train_choose_initial = _button("Choose model to improve", self._choose_initial_model)
        self.train_choose_initial.setToolTip("Read a validated .cloud and match its trained model capacities before starting a new run from its weights.")
        data_form.addRow(self.train_choose_initial)
        self.resume_path = QLineEdit()
        self.resume_path.setReadOnly(True)
        self.resume_path.setPlaceholderText("Choose a Cloud .cloudtrain recovery checkpoint")
        self.training_resume_label = self._add_training_row(data_form, "Resume checkpoint", self.resume_path,
            "Used only by Resume. This separate training file includes optimizer and recovery state; deployable .cloud files do not. Choosing it restores the original recipe, so new defaults cannot silently alter the resumed experiment.")
        self.train_choose_resume = _button("Choose resume checkpoint", self._choose_resume)
        self.train_choose_resume.setToolTip("Load a Cloud training checkpoint with the restricted weights-only reader. Restore its settings and keep the data and model capacities unchanged for exact continuation.")
        data_form.addRow(self.train_choose_resume)
        self.run_folder = QLineEdit()
        self.run_folder.setPlaceholderText("Project runs folder")
        self._add_training_row(data_form, "Runs folder", self.run_folder,
            "Parent folder for a new, separate run directory. Learned .cloud models, recoverable training checkpoints, metrics, and the complete settings JSON are stored there. Each run gets a unique directory.")
        self.train_choose_folder = _button("Choose runs folder", self._choose_run_folder)
        self.train_choose_folder.setToolTip("Choose the parent directory for training runs; the next run creates its own uniquely named subfolder.")
        data_form.addRow(self.train_choose_folder)
        config_layout.addWidget(data_group)

        player_defaults = resolve_settings("player")
        for group_name in GROUPS:
            group = QGroupBox(group_name)
            form = QFormLayout(group)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setVerticalSpacing(10)
            self.training_groups[group_name] = group
            if group_name == "Run and hardware":
                self.training_options_form = form
            if group_name == "AdamW and learning rate":
                form.addRow(_label("AdamW optimizer · separate learning-rate and teacher-box warmups", "subtitle", True))
            for setting in (item for item in SETTINGS if item.group == group_name):
                if setting.kind == "choice":
                    widget = _combo(setting.choices, setting.default)
                    if setting.name == "device":
                        widget.setEditable(True)
                        widget.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                elif setting.kind == "bool":
                    widget = QCheckBox("Enabled")
                    widget.setChecked(setting.default)
                elif setting.kind == "int" and setting.maximum > 2147483647:
                    widget = _double(setting.minimum, setting.maximum, setting.default, setting.step, 0)
                elif setting.kind == "int":
                    widget = _spin(int(setting.minimum), int(setting.maximum), setting.default, int(setting.step))
                else:
                    widget = _double(setting.minimum, setting.maximum, setting.default, setting.step, setting.decimals)
                widget.setObjectName("training_" + setting.name)
                widget.setAccessibleName(setting.label)
                widget.setAccessibleDescription(setting.help)
                self.training_controls[setting.name] = widget
                setattr(self, setting.widget_name or "train_" + setting.name, widget)
                default_value = player_defaults[setting.name]
                default_text = ("Enabled" if default_value else "Disabled") if setting.kind == "bool" else str(default_value)
                tip = setting.help + "\n\nPlayer recipe: " + default_text + ". JSON setting: " + setting.name
                if setting.minimum is not None and setting.maximum is not None:
                    tip += f"\nControl range: {setting.minimum:g} to {setting.maximum:g}."
                label = self._add_training_row(form, setting.label, widget, tip)
                self.training_setting_labels[setting.name] = label
            config_layout.addWidget(group)
        config_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(config_container)
        scroll.setMinimumWidth(455)
        scroll.setMaximumWidth(560)
        self.training_config_panel = scroll
        outer.addWidget(scroll)

        monitor = QVBoxLayout()
        monitor.addWidget(_label("RUN MONITOR", "eyebrow"))
        self.train_status = _label("Player recipe ready. New runs start from scratch unless you explicitly select Improve or Resume.", "subtitle", True)
        monitor.addWidget(self.train_status)
        self.training_recipe_note = _label("", "subtitle", True)
        monitor.addWidget(self.training_recipe_note)
        self.train_progress = QProgressBar()
        self.train_progress.setValue(0)
        monitor.addWidget(self.train_progress)
        action_row = QHBoxLayout()
        self.train_start = _button("Start training",self._start_training,True)
        self.train_start.setToolTip("Validate the reviewed data and current settings, then create a new run. Only the selected Improve or Resume mode loads existing learned weights.")
        self.train_stop = _button("Stop after current batch",self._cancel_job)
        self.train_stop.setToolTip("Request a stop at a recoverable training boundary. The trainer saves a separate .cloudtrain checkpoint for exact continuation.")
        self.train_stop.setEnabled(False)
        action_row.addWidget(self.train_start)
        action_row.addWidget(self.train_stop)
        action_row.addStretch()
        monitor.addLayout(action_row)
        self.train_log = QPlainTextEdit()
        self.train_log.setReadOnly(True)
        self.train_log.setMaximumBlockCount(3000)
        self.train_log.setPlaceholderText("Resolved batch size, optimizer updates, learning rate, separate presence / box / encasing losses, validation results, and saved paths will appear here.\n\nFresh runs use random weights. The player preset is a starting recipe to evaluate on your own held-out gameplay images.")
        monitor.addWidget(self.train_log,1)
        monitor.addWidget(_label("Deployable .cloud files hold learned tensors and the small inference contract. Training settings, optimizer state, and history live in separate run files.", "subtitle", True))
        monitor.addWidget(_label("Completed edits save automatically. Annotation and dataset changes pause during training.", "subtitle", True))
        outer.addLayout(monitor,1)

        self.training_preset.currentIndexChanged.connect(self._training_preset_changed)
        self.training_initialization.currentIndexChanged.connect(self._training_initialization_changed)
        for name, widget in self.training_controls.items():
            signal = (widget.currentTextChanged if name == "device" else widget.currentIndexChanged) if isinstance(widget, QComboBox) else (widget.toggled if isinstance(widget, QCheckBox) else widget.valueChanged)
            signal.connect(self._update_training_control_state)
        self._training_ui_loading = False
        self._apply_training_settings(resolve_settings("player"), "player")
        return page

    def _add_training_row(self, form, text, widget, tooltip):
        label = _label(text, wrap=True)
        label.setToolTip(tooltip)
        label.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        widget.setToolTip(tooltip)
        widget.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        label.setBuddy(widget)
        form.addRow(label, widget)
        return label

    def _training_settings_values(self, require_initialization=False):
        from .training_settings import SETTINGS, validate_settings

        values = {}
        for setting in SETTINGS:
            widget = self.training_controls[setting.name]
            if setting.kind == "choice":
                value = widget.currentData()
                if setting.name == "device" and widget.currentText() != widget.itemText(widget.currentIndex()):
                    value = widget.currentText().strip()
            elif setting.kind == "bool":
                value = widget.isChecked()
            else:
                value = int(widget.value()) if setting.kind == "int" else float(widget.value())
            values[setting.name] = value
        mode = self.training_initialization.currentData()
        values["init_model_path"] = self.init_model_path.text().strip() or None if mode == "improve" else None
        values["resume_path"] = self.resume_path.text().strip() or None if mode == "resume" else None
        if require_initialization and mode == "improve" and not values["init_model_path"]:
            raise ValueError("Choose the .cloud to improve, or select From scratch for a new model.")
        if require_initialization and mode == "resume" and not values["resume_path"]:
            raise ValueError("Choose the .cloudtrain checkpoint to resume, or select From scratch for a new model.")
        return validate_settings(values)

    def _apply_training_settings(self, values, preset="player"):
        from .training_settings import SETTINGS, resolve_settings

        resolved = resolve_settings(preset, values)
        self._training_ui_loading = True
        try:
            for setting in SETTINGS:
                widget, value = self.training_controls[setting.name], resolved[setting.name]
                if setting.kind == "choice":
                    index = widget.findData(value)
                    if index < 0 and setting.name == "device":
                        widget.addItem(value, value)
                        index = widget.findData(value)
                    widget.setCurrentIndex(index)
                elif setting.kind == "bool":
                    widget.setChecked(value)
                else:
                    if setting.kind == "float":
                        from decimal import Decimal
                        required_decimals = max(setting.decimals, -Decimal(str(value)).as_tuple().exponent)
                        widget.setDecimals(required_decimals)
                    widget.setValue(value)
            self.training_preset.setCurrentIndex(self.training_preset.findData(preset))
            self.init_model_path.setText(resolved.get("init_model_path") or "")
            self.resume_path.setText(resolved.get("resume_path") or "")
            mode = "resume" if resolved.get("resume_path") else "improve" if resolved.get("init_model_path") else "scratch"
            self.training_initialization.setCurrentIndex(self.training_initialization.findData(mode))
        finally:
            self._training_ui_loading = False
        self._update_training_control_state()

    def _training_preset_changed(self, *_):
        if self._training_ui_loading:
            return
        from .training_settings import resolve_settings

        preset = self.training_preset.currentData()
        values = resolve_settings(preset)
        if self.training_initialization.currentData() == "improve" and self.init_model_path.text().strip():
            values.update({name: self.training_controls[name].currentData()
                           for name in ("architecture", "input_size", "vertices", "channels", "roi_size", "mask_size")})
            values["init_model_path"] = self.init_model_path.text().strip()
        self._apply_training_settings(values, preset)
        self.train_status.setText("Applied " + preset + " recipe. You can edit each applicable setting before starting.")

    def _training_initialization_changed(self, *_):
        if self._training_ui_loading:
            return
        mode = self.training_initialization.currentData()
        if mode != "improve":
            self.init_model_path.clear()
        if mode != "resume":
            self.resume_path.clear()
        self._update_training_control_state()

    def _update_training_control_state(self, *_):
        if self._training_ui_loading:
            return
        mode = self.training_initialization.currentData()
        exact_resume = mode == "resume" and bool(self.resume_path.text().strip())
        capacities = {"architecture", "input_size", "vertices", "channels", "roi_size", "mask_size"}
        resume_editable = {"epochs", "device", "cpu_threads", "patience", "val_every"}
        from .config import MASK_ARCHITECTURE
        is_mask = self.training_controls["architecture"].currentData() == MASK_ARCHITECTURE
        mask_settings = {"mask_size", "mask_bce_weight", "mask_dice_weight", "mask_boundary_weight",
                         "background_rois_per_image", "negative_mask_weight", "mask_target_samples"}
        for name, widget in self.training_controls.items():
            enabled = not exact_resume or name in resume_editable
            if (is_mask and name in {"vertices", "roi_size", "contour_edge_weight"}) or (not is_mask and name in mask_settings):
                enabled = False
            if mode == "improve" and self.init_model_path.text().strip() and name in capacities:
                enabled = False
            if name == "lr_final_factor" and self.training_controls["scheduler"].currentData() != "cosine":
                enabled = False
            if name == "lr_warmup_start_factor" and self.training_controls["lr_warmup_epochs"].value() == 0:
                enabled = False
            if name.startswith("gameplay_") and self.train_augmentation.currentData() != "gameplay":
                enabled = False
            widget.setEnabled(enabled)
        for widget in (self.training_initial_label, self.init_model_path, self.train_choose_initial):
            widget.setVisible(mode == "improve")
            widget.setEnabled(mode == "improve")
        for widget in (self.training_resume_label, self.resume_path, self.train_choose_resume):
            widget.setVisible(mode == "resume")
            widget.setEnabled(mode == "resume")
        self.training_preset.setEnabled(not exact_resume)
        batch = self.train_batch.value()
        target = self.training_controls["nominal_batch_size"].value()
        if batch == -1:
            batch_note = "Automatic batch sizing; the resolved batch and accumulation count will appear in the run log."
        elif batch > 0:
            steps = max(1, int((target + batch - 1) // batch)) if target else 1
            batch_note = f"{batch} images per batch · {steps} batch(es) per optimizer update · up to {batch * steps} images per update."
        else:
            batch_note = "Choose a positive batch size or -1 for automatic sizing; 0 is invalid."
        initialization_note = {"scratch": "Fresh random weights; no pretrained player knowledge.",
                               "improve": "New optimizer and run, initialized from your selected .cloud.",
                               "resume": "Restore the selected experiment's settings, optimizer, and recovery state."}[mode]
        self.training_recipe_note.setText(initialization_note + "\n" + batch_note)

    def _load_training_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Cloud training settings", "", "JSON settings (*.json)")
        if not path:
            return
        try:
            from .training_settings import read_settings_file, resolve_settings
            values, preset = read_settings_file(path)
            preset = preset or "player"
            resolved = resolve_settings(preset, values)
            # Validate any explicit initialization before changing the form.
            if resolved.get("init_model_path"):
                from .format import load_cloud
                model_config, _, _ = load_cloud(resolved["init_model_path"])
                for name, value in {key: getattr(model_config, key) for key in
                       ("architecture", "input_size", "vertices", "channels", "roi_size", "mask_size")}.items():
                    if resolved[name] != value:
                        raise ValueError("Settings do not match the initial model's " + name + ". Choose the model with Improve first or load a matching recipe.")
            if resolved.get("resume_path"):
                from .training import TrainingConfig, _load_checkpoint, _resume_compatibility
                checkpoint = _load_checkpoint(resolved["resume_path"])
                _resume_compatibility(checkpoint, TrainingConfig(**resolved), checkpoint["dataset_fingerprint"])
            self._apply_training_settings(resolved, preset)
            self.train_status.setText("Loaded settings from " + str(Path(path).name) + ". Review the initialization choice before starting.")
        except Exception as exc:
            self._error("Cannot load training settings", str(exc))

    def _save_training_settings(self):
        try:
            from .training_settings import save_settings_file
            values = self._training_settings_values(require_initialization=True)
            path, _ = QFileDialog.getSaveFileName(self, "Save Cloud training settings", "cloud_training_settings.json", "JSON settings (*.json)")
            if not path:
                return
            if not path.lower().endswith(".json"):
                path += ".json"
            save_settings_file(path, values, self.training_preset.currentData())
            self.train_status.setText("Saved complete training settings to " + str(Path(path).name) + ".")
        except Exception as exc:
            self._error("Cannot save training settings", str(exc))

    def _build_inspection_tab(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14,14,14,14)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0,0,10,0)
        side.addWidget(_label("Check the output", "sectionTitle"))
        side.addWidget(_label("Encasings are the public geometry. Optional main boxes are diagnostics inside this app.", "subtitle", True))
        model_group = QGroupBox("Local .cloud model")
        model_form = QFormLayout(model_group)
        self.backend_combo = _combo([("NumPy CPU · reference",("numpy","cpu")),("PyTorch CPU",("torch","cpu")),("PyTorch CUDA",("torch","cuda")),("ONNX CPU",("onnx","cpu")),("ONNX CUDA",("onnx","cuda"))])
        model_form.addRow("Backend",self.backend_combo)
        self.model_load = _button("Load .cloud / ONNX model",self._load_model,True)
        model_form.addRow(self.model_load)
        self.model_folder_button=_button("Open ONNX model folder…",self._load_model_folder)
        model_form.addRow(self.model_folder_button)
        model_form.addRow(_label("Backend changes take effect when you load a model.","subtitle",True))
        self.model_status = _label("No model loaded. Annotation works without a model.", "subtitle", True)
        model_form.addRow(self.model_status)
        side.addWidget(model_group)
        inference = QGroupBox("Detection settings")
        inference_form = QFormLayout(inference)
        self.confidence_spin = _double(.001,.999,.35,.05,3)
        self.nms_spin = _double(.01,.99,.5,.05,2)
        self.maximum_detections = _spin(1,256,32)
        self.minimum_height = _spin(0,8192,0)
        self.diagnostic_check = QCheckBox("Show diagnostic main boxes")
        self.diagnostic_check.toggled.connect(self._diagnostic_changed)
        for label,widget in (("Confidence threshold",self.confidence_spin),("Box NMS overlap",self.nms_spin),("Maximum detections",self.maximum_detections),("Minimum height (px)",self.minimum_height)):
            inference_form.addRow(label,widget)
        inference_form.addRow(self.diagnostic_check)
        self.detect_image_button = _button("Detect current image",self._predict_image)
        self.detect_capture_button = _button("Detect capture frame",self._predict_capture)
        self.suggestions_button = _button("Add suggestions as unreviewed",self._add_suggestions)
        inference_form.addRow(self.detect_image_button)
        inference_form.addRow(self.detect_capture_button)
        inference_form.addRow(self.suggestions_button)
        side.addWidget(inference)
        evaluation = QGroupBox("Validation & portability")
        eval_form = QFormLayout(evaluation)
        self.evaluation_form = eval_form
        self.eval_split = _combo(["val","test","train"])
        eval_form.addRow("Evaluate split",self.eval_split)
        self.evaluate_button = _button("Evaluate reviewed dataset",self._evaluate)
        self.export_button = _button("Export detector + wrapper ONNX",self._export_onnx)
        self.save_result_button = _button("Save result JSON",self._save_result)
        eval_form.addRow(self.evaluate_button)
        eval_form.addRow(self.export_button)
        eval_form.addRow(self.save_result_button)
        self.native_actions_note=_label("", "subtitle", True)
        eval_form.addRow(self.native_actions_note)
        side.addWidget(evaluation)
        side.addStretch()
        side.addWidget(_label("Predictions stay unreviewed until you check both labels. An extra label provides measurable supervision; it does not establish correctness by itself.", "subtitle", True))
        inspect_scroll=QScrollArea()
        inspect_scroll.setWidgetResizable(True)
        inspect_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inspect_scroll.setMinimumWidth(395)
        inspect_scroll.setMaximumWidth(445)
        inspect_scroll.setWidget(sidebar)
        layout.addWidget(inspect_scroll)
        results = QVBoxLayout()
        results.addWidget(_label("RESULTS & MEASUREMENTS", "eyebrow"))
        self.inference_status = _label("Load a model to inspect saved images or a frozen capture frame.", "subtitle", True)
        results.addWidget(self.inference_status)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("Normal prediction JSON includes class, confidence and encasing vertices.\n\nEvaluation reports fixed-threshold precision / recall and measured boundary quality, with timing from this device.\n\nThe NumPy backend is a numerical reference, not a speed-optimized GPU runtime.")
        results.addWidget(self.result_text,1)
        self._last_result = None
        layout.addLayout(results,1)
        return page

    def _error(self, title, message, details=None):
        self.statusBar().showMessage(message,15000)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        if details:
            box.setDetailedText(details)
        box.exec()

    def _canvas_problem(self, message):
        self.canvas_hint.setText(message)
        self.canvas_hint.setStyleSheet("color: #ffc38b;")
        self.statusBar().showMessage(message,15000)

    def _set_canvas_hint(self, message):
        self.canvas_hint.setStyleSheet("")
        self.canvas_hint.setText(message)

    def _canvas_tool_changed(self, tool):
        if tool in self.tool_buttons:
            self.tool_buttons[tool].setChecked(True)
        else:
            self.tool_group.setExclusive(False)
            for button in self.tool_buttons.values():
                button.setChecked(False)
            self.tool_group.setExclusive(True)
        if hasattr(self,"annotation_shortcuts"):
            refinement = "  ·  Right click: background  ·  Backspace: undo click" if tool.startswith("assist_") else "  ·  Backspace/right click: remove draft point" if tool=="wrapper" else ""
            self.annotation_shortcuts.setText("Enter: accept & next  ·  N: add another target"+refinement+"  ·  Wheel: zoom  ·  Space + drag: pan")

    def _select_canvas_tool(self, tool):
        if not self.canvas.set_tool(tool):
            self._canvas_tool_changed(self.canvas.tool)

    def eventFilter(self, watched, event):
        navigation_lists = (getattr(self,"image_list",None),getattr(self,"instance_list",None))
        if (watched in navigation_lists and event.type()==QEvent.Type.KeyPress
                and QApplication.activeModalWidget() is None
                and self.tabs.currentWidget()==self.annotate_tab
                and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)):
            key = event.key()
            if key in (Qt.Key.Key_Enter,Qt.Key.Key_Return,Qt.Key.Key_N):
                if not event.isAutoRepeat():
                    self._add_another_target() if key==Qt.Key.Key_N else self._approve_and_advance()
                return True
        return super().eventFilter(watched,event)

    def _workflow_notice(self, message, warning=False):
        self.assist_status.setText(message)
        self.statusBar().showMessage(message)
        if warning:
            self._canvas_problem(message)
        else:
            self._set_canvas_hint(message)

    @Slot(str)
    def _new_box_committed(self, annotation_id):
        if self._changing or self.project is None or self.current_image_id is None:
            return False
        key = (self.current_image_id,annotation_id)
        if key in self._auto_assisted_boxes:
            return False
        annotation = self.canvas.selected_annotation()
        if annotation is None or annotation.get("id")!=annotation_id:
            return False
        record = self.project.record(self.current_image_id)
        saved = next((ann for ann in record.get("annotations",[]) if ann.get("id")==annotation_id),None)
        if self._unsaved or saved != annotation:
            self._workflow_notice("The new box could not be saved. Resolve the save error before computing its outline.",True)
            return False
        self._auto_assisted_boxes.add(key)
        self._workflow_at_end = False
        return self._auto_assist_if_current(self._assist_identity())

    def _auto_assist_if_current(self, identity):
        if identity is None or identity != self._assist_identity():
            return False
        if self._live_session is not None:
            self._request_live_stop(after=lambda:self._auto_assist_if_current(identity),reason="Stopping live detection before outlining the new box…")
            return True
        return self._request_assist()

    def _workflow_is_busy(self):
        if self._assist_busy:
            self._workflow_notice("The outline is still being generated. Check the result when it appears, then press Enter again.",True)
            return True
        if self._job is not None or self._live_session is not None:
            self._workflow_notice("Finish the current operation before approving annotation labels.",True)
            return True
        return False

    def _commit_visible_outline(self):
        if self.canvas.has_draft and not self.canvas.finish_polygon():
            return False
        if self.canvas.has_assist:
            if not self._assist_result or not has_encasing(self._assist_result):
                self._workflow_notice("There is no valid outline to approve. Refine the clicks, recompute, or use Edit… to draw it manually.",True)
                return False
            if not self._accept_assist():
                return False
        return True

    @Slot()
    def _approve_and_advance(self):
        if self._advancing_image or self._workflow_is_busy():
            return False
        if self.project is None or self.current_image_id is None:
            self._workflow_notice("Open an image, then draw its first target box.",True)
            return False
        self._advancing_image = True
        try:
            image_id = self.current_image_id
            if not self._commit_visible_outline():
                return False
            record = self.project.record(image_id)
            annotations = self.canvas.annotations
            explicit_negative = not record.get("annotations") and record.get("reviewed") and not self._unsaved
            if not annotations and not explicit_negative:
                self._workflow_notice("Draw a target box first. If this image has no targets, choose Mark empty image as negative explicitly.",True)
                return False
            for index, annotation in enumerate(annotations):
                issues = validate_annotation(annotation,*self.canvas.image_size,require_complete=True)
                if issues:
                    self._workflow_notice(f"Target {index+1} is incomplete: "+" ".join(issues)+" Finish every pair before moving next.",True)
                    return False
            if self._unsaved or record.get("annotations")!=annotations:
                if not self._persist_current():
                    return False
                record = self.project.record(image_id)
            try:
                if not record.get("reviewed"):
                    self.project.set_reviewed(image_id,True)
            except Exception as exc:
                self._refresh_review()
                self._workflow_notice("The outline is saved, but this image could not be marked reviewed: "+str(exc),True)
                return False
            self._refresh_review()
            self._refresh_images()
            self._refresh_summary()
            records = self.project.manifest.get("images",[])
            position = next((i for i,item in enumerate(records) if item["id"]==image_id),-1)
            if position<0:
                self._workflow_notice("The reviewed image is no longer in this project. Reopen the project before continuing.",True)
                return False
            if position+1>=len(records):
                self._workflow_at_end = True
                self.canvas.set_tool("box")
                self.canvas.setFocus()
                self._workflow_notice("Reviewed · end of image set. All targets on this image are saved. There is no next image.")
                return True
            next_id = records[position+1]["id"]
            if not self._load_image(next_id):
                self._workflow_notice("This image is reviewed, but the next image could not be opened. Its labels were not changed.",True)
                return False
            self.canvas.setFocus()
            self._workflow_notice(f"Previous image reviewed · image {position+2} of {len(records)}. Draw the next target box.")
            return True
        finally:
            self._advancing_image = False

    @Slot()
    def _add_another_target(self):
        if self._workflow_is_busy():
            return False
        if self.project is None or self.current_image_id is None:
            self._workflow_notice("Open an image before adding a target.",True)
            return False
        if not self._commit_visible_outline():
            return False
        annotation = self.canvas.selected_annotation()
        if annotation is not None:
            issues = validate_annotation(annotation,*self.canvas.image_size,require_complete=True)
            if issues:
                self._workflow_notice("Finish the current target's outline before adding another: "+" ".join(issues),True)
                return False
        if self._unsaved and not self._persist_current():
            return False
        self.canvas.select("")
        self.canvas.set_tool("box")
        self.canvas.setFocus()
        self._workflow_at_end = False
        self._workflow_notice("Draw another target box on this image. Press Enter when every target has a complete outline.")
        self._refresh_assist_controls()
        return True

    def _edit_current_annotation(self):
        if self._workflow_is_busy():
            return False
        if self.canvas.has_assist:
            if self._assist_result and has_encasing(self._assist_result):
                if not self._accept_assist():
                    return False
            elif not self._assist_points:
                self._reject_assist()
            else:
                self._workflow_notice("Refine or reject the unfinished proposal before editing the accepted labels.",True)
                return False
        if self.canvas.selected_annotation() is None:
            self._workflow_notice("Choose a target in Linked instances to edit its box or outline.",True)
            return False
        if not self.canvas.set_tool("select"):
            return False
        self.canvas.setFocus()
        self._workflow_notice("Drag the box corners or outline vertices to correct them. Enter approves all complete pairs and moves next.")
        return True

    def _draft_changed(self, pending):
        if pending:
            self.save_status.setText("Encasing draft · Enter to finish")
            self.save_status.setStyleSheet("color: #ffc38b;")
        elif self.canvas.has_assist:
            self.save_status.setText("Outline assistance · accept or reject")
            self.save_status.setStyleSheet("color: #d3a5ff;")
        elif not self._unsaved:
            self.save_status.setText("Completed edits saved automatically")
            self.save_status.setStyleSheet("color: #6ce3cb;")

    def _assist_identity(self):
        ann = self.canvas.selected_annotation()
        if self.project is None or self.current_image_id is None or ann is None:
            return None
        return (str(self.project.root.resolve()),self.current_image_id,self.canvas.image_revision,
                self.canvas.content_revision,ann["id"],json.dumps(ann,sort_keys=True,separators=(",",":")))

    def _refresh_assist_controls(self):
        busy = self._assist_busy or (getattr(self, "_outline_batch_active", False)
                                    and not getattr(self, "_outline_internal", False))
        blocked = self._job is not None or self._live_stopping
        active = self.canvas.has_assist
        selected = self.canvas.selected_annotation() is not None
        self.canvas.edit_locked = busy
        self.assist_start_button.setText("Canceling…" if self._assist_cancel_pending else "Cancel assist" if busy else "Recompute outline")
        self.assist_start_button.setEnabled((busy and not self._assist_cancel_pending) or (selected and not blocked and not busy))
        self.assist_device.setEnabled(not busy and not blocked)
        self.assist_detail.setEnabled(not busy and not blocked)
        self.assist_backend.setEnabled(not busy and not blocked)
        self.assist_crop.setEnabled(not busy and not blocked)
        self.assist_candidates.setEnabled(active and not busy and not blocked)
        self.annotation_edit_button.setEnabled(not busy and not blocked)
        self.edit_outline_button.setEnabled(selected and not busy and not blocked)
        for tool, button in self.tool_buttons.items():
            button.setEnabled(not busy and not blocked and (active if tool.startswith("assist_") else True))
        self.assist_undo_click.setEnabled(bool(self._assist_points) and active and not busy and not blocked)
        self.assist_reset_clicks.setEnabled(bool(self._assist_points) and active and not busy and not blocked)
        current = self._assist_context is not None and self._assist_context == self._assist_identity()
        valid_proposal = bool(self._assist_result and has_encasing(self._assist_result)) and current
        accepted_labels = bool(self.canvas.annotations) and not active
        reviewed_empty = False
        if self.project is not None and self.current_image_id is not None and not self.canvas.annotations and not self._unsaved:
            record = self.project.record(self.current_image_id)
            reviewed_empty = bool(record.get("reviewed") and not record.get("annotations"))
        self.assist_accept_button.setEnabled((valid_proposal or accepted_labels or reviewed_empty or self.canvas.has_draft) and not busy and not blocked)
        self.add_target_button.setEnabled(self.current_image_id is not None and not busy and not blocked)
        self.assist_reject_button.setEnabled(active and not busy and not blocked)
        self.image_list.setEnabled(not busy)
        self.instance_list.setEnabled(not busy)
        self.reviewed_check.setEnabled(not busy)
        self.split_combo.setEnabled(not busy)
        self.split_groups_button.setEnabled(not busy)
        self._draft_changed(self.canvas.has_draft)

    def _assist_backend_changed(self, *_):
        if not hasattr(self, "canvas"):
            return
        self._assist_refinement_token = None
        if self._assist_thread is not None:
            self._assist_thread.clear_cache()
        if self.canvas.has_assist and not self._assist_busy:
            self._request_assist()
        elif hasattr(self, "_schedule_image_work"):
            self._schedule_image_work()

    def _populate_assist_candidates(self):
        if not hasattr(self, "assist_candidates"):
            return
        result = self._assist_result or {}
        candidates = result.get("candidates", [])
        self.assist_candidates.blockSignals(True)
        self.assist_candidates.clear()
        selected = 0
        for index, candidate in enumerate(candidates):
            score = candidate.get("quality_hint")
            suffix = f" · self-score {score:.3f}" if isinstance(score, (int, float)) else ""
            self.assist_candidates.addItem(f"Mask {index+1}{suffix}", index)
            if candidate.get("id") == result.get("id"):
                selected = index
        self.assist_candidates.setCurrentIndex(selected)
        self.assist_candidates.setVisible(len(candidates) > 1)
        self.assist_candidates.blockSignals(False)

    def _assist_candidate_changed(self, index):
        if self._assist_busy or not self.canvas.has_assist or self._assist_context != self._assist_identity():
            return
        original = self._assist_result or {}
        candidates = original.get("candidates", [])
        if not 0 <= index < len(candidates):
            return
        candidate = deepcopy(candidates[index])
        issues = validate_annotation(replace_encasing(self.canvas.selected_annotation(), candidate), *self.canvas.image_size)
        if issues:
            self._workflow_notice("This alternative cannot be accepted: " + " ".join(issues), True)
            return
        candidate["candidates"] = candidates
        candidate["timings_ms"] = original.get("timings_ms", {})
        self._assist_result = candidate
        self._assist_refinement_token = candidate.get("refinement_token")
        self.canvas.set_assist_preview(candidate.get("wrapper"), self._assist_points,
                                       self._assist_labels, mask=candidate.get("mask"))
        self._workflow_notice(f"Mask {index+1} selected. Check the boundary and holes; clicks refine this choice. " + " ".join(candidate.get("warnings", [])))
        self._refresh_assist_controls()
        if hasattr(self, "_checkpoint_review"):
            self._checkpoint_review()

    def _correct_encasing(self):
        if self._workflow_is_busy():
            return False
        annotation = self.canvas.selected_annotation()
        if annotation is not None and annotation.get("mask") is not None:
            if hasattr(self, "_activate_target_outline"):
                self._activate_target_outline(annotation)
            elif not self.canvas.has_assist:
                return self._request_assist()
            self.canvas.set_tool("assist_fg")
            self.canvas.setFocus()
            self._workflow_notice("Left click adds foreground; right click removes background. Enter saves, reviews and moves next.")
            return True
        return self._edit_current_annotation()

    def _ensure_assist_thread(self):
        if self._assist_thread is None:
            thread = OutlineAssistThread(self)
            thread.completed.connect(self._assist_completed)
            thread.finished.connect(self._assist_thread_finished)
            thread.finished.connect(thread.deleteLater)
            self._assist_thread = thread
            thread.start()
        return self._assist_thread

    def _assist_button_clicked(self):
        if self._assist_busy:
            self._cancel_assist()
        else:
            self._request_assist()

    def _request_assist(self):
        if self._defer_until_live_stopped(self._request_assist,"Stopping live detection before outline assistance…"):
            return False
        if self._job is not None or self._assist_busy or not self._need_image():
            return False
        if not self.canvas.resolve_draft(self):
            return False
        if self._unsaved and not self._persist_current():
            return False
        ann = self.canvas.selected_annotation()
        if ann is None:
            self.assist_status.setText("Draw a new target box to generate its outline automatically, or choose an existing target to recompute.")
            return False
        identity = self._assist_identity()
        if self.canvas.has_assist and self._assist_context != identity:
            self._reject_assist()
        if not self.canvas.has_assist:
            if not self.canvas.begin_assist(ann["id"]):
                return False
            self._assist_context = identity
            self._assist_refinement_token = None
            rgb = self.canvas.image.convertToFormat(QImage.Format.Format_RGB888)
            self._assist_image = Image.frombytes("RGB",(rgb.width(),rgb.height()),bytes(rgb.constBits()),"raw","RGB",rgb.bytesPerLine(),1)
            self._assist_points = []
            self._assist_labels = []
            self._assist_prompt_revision += 1
            self.canvas.set_tool("assist_fg")
        self._capture_timer.stop()
        self._sample_timer.stop()
        self._assist_request_serial += 1
        token = (self._assist_context,self._assist_prompt_revision,self._assist_request_serial)
        request = {"token":token,"image":self._assist_image.copy(),"box":deepcopy(ann["box"]),
                   "points":deepcopy(self._assist_points),"labels":list(self._assist_labels),
                   "device":self.assist_device.currentData(), "backend":self.assist_backend.currentData(),
                   "crop_mode": self.assist_crop.currentData(), "refinement_token": self._assist_refinement_token,
                   "max_vertices":int(self.assist_detail.currentData())}
        thread = self._ensure_assist_thread()
        if not thread.submit(request):
            self.assist_status.setText("The outline worker is still finishing a request. Try again when it is ready.")
            return False
        self._assist_token = self._assist_inflight_token = token
        self._assist_result = None
        self._assist_busy = True
        self._assist_cancel_pending = False
        self.canvas.set_assist_preview(points=self._assist_points,labels=self._assist_labels)
        self.assist_status.setText(f"{self.assist_backend.currentText()} · {request['device'].upper()} · Preparing this image or refining its cached outline… You can zoom and pan while it works.")
        self.statusBar().showMessage("Outline assistance running. The accepted labels remain unchanged until you choose Accept outline.")
        self._refresh_summary()
        return True

    @Slot(object)
    def _assist_completed(self, outcome):
        token = outcome.get("token")
        if token != self._assist_inflight_token:
            return
        self._assist_busy = False
        self._assist_cancel_pending = False
        self._assist_inflight_token = None
        self.canvas.edit_locked = False
        current = (self.canvas.has_assist and token == self._assist_token
                   and self._assist_context == self._assist_identity()
                   and token[1] == self._assist_prompt_revision)
        if not current:
            if self.canvas.has_assist and self._assist_context != self._assist_identity():
                self._clear_assist_state()
            self.assist_status.setText("Ignored an old outline result because its image, box, labels, or clicks changed.")
        elif outcome.get("cancelled"):
            self.assist_status.setText("Outline assistance canceled. Your accepted outline and refinement clicks are kept; retry or reject assistance.")
        elif outcome.get("error"):
            message = outcome["error"]
            if any(word in message.lower() for word in ("torch","cv2","opencv","dependency","dependencies","no module")):
                message += " Open Open_Assisted_Annotator.cmd to install outline assistance, or run Install_CPU.cmd / Install_NVIDIA.cmd and reopen Cloud Studio."
            self.assist_status.setText("Outline assistance unavailable: "+message)
            self.statusBar().showMessage("Outline assistance unavailable: "+message)
        else:
            result = outcome.get("result") or {}
            wrapper = result.get("wrapper")
            issues = []
            if has_encasing(result):
                candidate = replace_encasing(self.canvas.selected_annotation(), result)
                issues = validate_annotation(candidate,*self.canvas.image_size,require_complete=True)
            if issues:
                result = dict(result, wrapper=None, mask=None, warnings=list(result.get("warnings",[]))+issues)
                wrapper = None
            self._assist_result = deepcopy(result)
            self._assist_refinement_token = result.get("refinement_token")
            self.canvas.set_assist_preview(wrapper,self._assist_points,self._assist_labels,mask=result.get("mask"))
            self._populate_assist_candidates()
            device = str(result.get("device",self.assist_device.currentData())).upper()
            total = float(result.get("timings_ms",{}).get("total",0.))
            cache = " · cached image" if result.get("diagnostics",{}).get("embedding_cache_hit") else ""
            heading = f"{result.get('backend', self.assist_backend.currentText())} · {device} · {total/1000:.2f} s{cache}. "
            warnings = " ".join(str(value) for value in result.get("warnings",[]))
            self.assist_status.setText(heading+("Check the violet outline. Left/right clicks refine foreground/background; Enter approves and moves next, N adds another target." if has_encasing(result) else "No usable outline. Refine the clicks or choose Edit… → Draw encasing manually. This image will not advance until every pair is complete.")+(" "+warnings if warnings else ""))
            self.statusBar().showMessage("Outline ready. Enter accepts, reviews complete pairs, and moves next. N keeps this image for another target." if has_encasing(result) else "Outline needs refinement. Accepted labels are unchanged.")
        self._refresh_summary()
        if self._assist_close_when_idle:
            self._assist_close_when_idle = False
            QTimer.singleShot(0,self.close)

    def _add_assist_point(self, point, label):
        if self._assist_busy or not self.canvas.has_assist or self._assist_context != self._assist_identity():
            return False
        if len(self._assist_points)>=256:
            self.assist_status.setText("This outline already has 256 refinement clicks. Undo a click or reset the prompts before adding more.")
            return False
        self._assist_points.append([float(point[0]),float(point[1])])
        self._assist_labels.append(int(label))
        self._assist_prompt_revision += 1
        return self._request_assist()

    def _assist_detail_changed(self):
        if not self.canvas.has_assist:
            return
        self._assist_prompt_revision += 1
        self._assist_result = None
        self._assist_token = None
        self.canvas.set_assist_preview(points=self._assist_points,labels=self._assist_labels)
        if self._assist_busy:
            self._cancel_assist()
        else:
            self._request_assist()

    def _remove_assist_point(self):
        if self._assist_busy or not self.canvas.has_assist or not self._assist_points:
            return False
        self._assist_points.pop()
        self._assist_labels.pop()
        self._assist_prompt_revision += 1
        self._assist_refinement_token = None
        return self._request_assist()

    def _reset_assist_points(self):
        if self._assist_busy or not self.canvas.has_assist:
            return False
        self._assist_points = []
        self._assist_labels = []
        self._assist_prompt_revision += 1
        self._assist_refinement_token = None
        return self._request_assist()

    def _cancel_assist(self):
        if not self._assist_busy or self._assist_thread is None:
            return
        self._assist_cancel_pending = True
        self._assist_thread.cancel_request()
        self.assist_status.setText("Cancel requested. Waiting for the current image operation to finish safely…")
        self.statusBar().showMessage(self.assist_status.text())
        self._refresh_assist_controls()

    def _clear_assist_state(self):
        self._assist_prompt_revision += 1
        self._assist_token = None
        self._assist_context = None
        self._assist_image = None
        self._assist_points = []
        self._assist_labels = []
        self._assist_result = None
        self._assist_refinement_token = None
        self.canvas.clear_assist()
        self._populate_assist_candidates()

    def _reject_assist(self):
        if self._assist_busy:
            return False
        self._clear_assist_state()
        self.assist_status.setText("Proposal and clicks discarded. The saved box and accepted encasing are unchanged.")
        self._refresh_assist_controls()
        return True

    def _accept_assist(self):
        if self._assist_busy or not self._assist_result or not has_encasing(self._assist_result):
            return False
        if self._assist_context != self._assist_identity():
            self.assist_status.setText("This proposal is no longer current. Request a new outline for the selected box.")
            return False
        if not self.canvas.accept_assist(self._assist_result.get("wrapper"),
                backend=self._assist_result.get("backend", self.assist_backend.currentText()),
                mask=self._assist_result.get("mask"), warnings=self._assist_result.get("warnings"),
                diagnostics=self._assist_result.get("diagnostics")):
            return False
        self._clear_assist_state()
        self.assist_status.setText("Encasing saved as unreviewed. Use foreground/background clicks to correct a mask; Enter approves the complete image and moves next.")
        self.statusBar().showMessage(self.assist_status.text(),12000)
        self._refresh_summary()
        return True

    def _resolve_assist(self):
        if not self.canvas.has_assist:
            return True
        if self._assist_busy:
            self.statusBar().showMessage("Outline assistance is running. Cancel it or wait before changing this image.",12000)
            return False
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Unaccepted outline assistance")
        dialog.setText("This box has an outline proposal or refinement clicks that have not been accepted.")
        dialog.setInformativeText("Accept the current proposal, discard assistance, or keep refining. Your existing accepted encasing is kept until you accept a replacement.")
        accept = None
        if self._assist_result and has_encasing(self._assist_result) and self._assist_context == self._assist_identity():
            accept = dialog.addButton("Accept outline",QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton("Discard assistance",QMessageBox.ButtonRole.DestructiveRole)
        keep = dialog.addButton("Keep refining",QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(keep)
        dialog.exec()
        if accept is not None and dialog.clickedButton() == accept:
            return self._accept_assist()
        if dialog.clickedButton() == discard:
            return self._reject_assist()
        return False

    @Slot()
    def _assist_thread_finished(self):
        self._assist_thread = None
        if self._assist_busy:
            self._assist_busy = False
            self._assist_cancel_pending = False
            self._assist_inflight_token = None
            self.canvas.edit_locked = False
            self.assist_status.setText("The outline worker stopped. Your accepted labels are unchanged; retry assistance when ready.")
            self._refresh_summary()
        if self._assist_shutdown_close:
            self._assist_shutdown_close = False
            QTimer.singleShot(0,self.close)

    def _ready_to_leave(self):
        if self._assist_busy or not self._resolve_assist():
            return False
        if not self.canvas.resolve_draft(self):
            return False
        if self._unsaved:
            dialog=QMessageBox(self)
            dialog.setWindowTitle("Unsaved edits")
            dialog.setText("Your last edit could not be saved.")
            dialog.setInformativeText("Retry saving, reload the saved project and discard unsaved edits, or keep editing. Reloading also picks up changes made by another editor.")
            retry=dialog.addButton("Retry save",QMessageBox.ButtonRole.AcceptRole)
            discard=dialog.addButton("Discard unsaved edits",QMessageBox.ButtonRole.DestructiveRole)
            keep=dialog.addButton("Keep editing",QMessageBox.ButtonRole.RejectRole)
            dialog.setDefaultButton(keep)
            dialog.exec()
            if dialog.clickedButton()==retry:
                return self._persist_current()
            if dialog.clickedButton()==discard:
                return self._discard_unsaved_and_reload()
            return False
        return True

    def _discard_unsaved_and_reload(self):
        """Reload before clearing the dirty flag, even if the next dialog cancels."""
        if self.project is None:
            return False
        old_project=self.project
        old_image_id=self.current_image_id
        try:
            fresh=Project.open(old_project.root)
            records=fresh.manifest.get("images",[])
            image_id=old_image_id if any(r["id"]==old_image_id for r in records) else records[0]["id"] if records else None
            self.project=fresh
            if image_id is not None:
                if not self._load_image(image_id):
                    self.project=old_project
                    self.current_image_id=old_image_id
                    self._unsaved=True
                    return False
            else:
                self._changing=True
                try:
                    self.canvas.set_image(None,[])
                    self.current_image_id=None
                    self._suggestions=None
                    self.instance_list.clear()
                    self.reviewed_check.blockSignals(True)
                    self.reviewed_check.setChecked(False)
                    self.reviewed_check.blockSignals(False)
                    self.review_status.setText("The saved project contains no images.")
                finally:
                    self._changing=False
            self._unsaved=False
            self._refresh_images(select_id=image_id)
            self._refresh_summary()
            self._draft_changed(False)
            self.statusBar().showMessage("Reloaded the saved project. Unsaved edits were discarded.",12000)
            return True
        except Exception as exc:
            self.project=old_project
            self.current_image_id=old_image_id
            self._unsaved=True
            self._error("Could not reload saved project",str(exc))
            return False

    def _need_project(self):
        if self.project is None:
            self._error("Project required","Create or open a project first.")
            return False
        return True

    def _need_image(self):
        if not self._need_project():
            return False
        if self.current_image_id is None:
            self._error("Image required","Import and select an image in Annotate first.")
            return False
        return True

    def _need_model(self):
        if self._runtime is None or self._model_path is None:
            self._error("Model required","Load a .cloud model or an ONNX model.json first.")
            return False
        return True

    def _new_project(self):
        if self._defer_until_live_stopped(self._new_project,"Stopping live detection before creating a project…"):
            return
        if self._job or not self._ready_to_leave():
            return
        folder = QFileDialog.getExistingDirectory(self,"Choose or create a project folder")
        if not folder:
            return
        name, okay = QInputDialog.getText(self,"Project name","Name",text=Path(folder).name)
        if not okay:
            return
        try:
            project = Project.create(folder,name=name.strip() or Path(folder).name)
            self._use_project(project)
        except Exception as exc:
            self._error("Cannot create project",str(exc))

    def _open_project(self):
        if self._defer_until_live_stopped(self._open_project,"Stopping live detection before opening a project…"):
            return
        if self._job or not self._ready_to_leave():
            return
        folder = QFileDialog.getExistingDirectory(self,"Open a Cloud Studio project folder")
        if folder:
            try:
                self._open_project_path(folder)
            except Exception as exc:
                self._error("Cannot open project",str(exc))

    def _open_project_path(self, path):
        if self._defer_until_live_stopped(lambda:self._open_project_path(path),"Stopping live detection before opening a project…"):
            return False
        if not self._ready_to_leave():
            return False
        project = Project.open(path)
        self._use_project(project)
        return True

    def _use_project(self, project):
        if self._defer_until_live_stopped(lambda:self._use_project(project),"Stopping live detection before switching projects…"):
            return
        if not self._ready_to_leave():
            return
        self._stop_capture()
        self._changing = True
        try:
            project.enable_fast_saves()
            self.project = project
            self.current_image_id = None
            self._unsaved = False
            self._suggestions = None
            self._live_frame = None
            self._capture_prediction = None
            self.canvas.set_image(None,[])
            self.instance_list.clear()
            self.image_filter.clear()
            self.run_folder.setText(str(project.root / "runs"))
            self.resume_path.clear()
            self.init_model_path.clear()
            self.training_initialization.setCurrentIndex(self.training_initialization.findData("scratch"))
            self._update_training_control_state()
            self._capture_group = "capture-" + uuid4().hex
        finally:
            self._changing = False
        self._refresh_images()
        self._refresh_summary()
        if self.image_list.count():
            self.image_list.setCurrentRow(0)
        self.tabs.setCurrentWidget(self.annotate_tab)
        self.statusBar().showMessage(f"Opened {project.manifest.get('name',project.root.name)}. All edits stay on this computer.")

    def _refresh_summary(self):
        has_project = self.project is not None
        busy = self._job is not None or self._assist_busy or getattr(self, "_outline_batch_active", False)
        live_busy=self._live_session is not None
        stopping=self._live_stopping
        self.import_button.setEnabled(has_project and not busy)
        self.video_button.setEnabled(has_project and not busy)
        self.new_button.setEnabled(not busy and not stopping)
        self.open_button.setEnabled(not busy and not stopping)
        self.train_start.setEnabled(has_project and not busy)
        self.model_load.setEnabled(not busy and not stopping)
        self.model_folder_button.setEnabled(not busy and not stopping)
        self.live_load_button.setEnabled(not busy and not stopping)
        loaded = self._runtime is not None
        self.detect_image_button.setEnabled(loaded and self.current_image_id is not None and not busy)
        self.detect_capture_button.setEnabled(loaded and self._last_frame is not None and not busy)
        self.suggestions_button.setEnabled(bool(self._suggestions) and not busy)
        native=loaded and self._loaded_kind!="onnx" and self._loaded_backend!="onnx"
        self.evaluate_button.setEnabled(has_project and native and not busy and not stopping)
        self.export_button.setEnabled(native and not busy and not stopping)
        self.native_actions_note.setText("Evaluation and re-export need the original .cloud file. This ONNX pair can run image and live detection." if loaded and not native else "")
        self.evaluate_button.setToolTip("Load the original .cloud model to evaluate this dataset." if loaded and not native else "Evaluate the reviewed dataset using predicted boxes and encasings.")
        self.export_button.setToolTip("This model is already an ONNX pair. Load its original .cloud file to export again." if loaded and not native else "Export the native model as detector and guided-wrapper ONNX graphs.")
        self.capture_add.setEnabled(has_project and self._last_frame is not None and not busy)
        self.sample_start.setEnabled(has_project and not busy and not live_busy and not self._sample_timer.isActive())
        self.sample_stop.setEnabled(self._sample_timer.isActive())
        self.preview_stop.setEnabled(self._capture_timer.isActive() or self._sample_timer.isActive() or live_busy)
        self.preview_start.setEnabled(not busy and not live_busy and not self._capture_timer.isActive())
        self.live_start_button.setEnabled(loaded and not busy and not live_busy)
        self.live_stop_button.setEnabled(live_busy and not stopping)
        self.live_freeze_button.setEnabled(self._live_frame is not None and not busy and not stopping)
        self.capture_add.setEnabled(has_project and self._last_frame is not None and not busy and not stopping)
        self.monitor_combo.setEnabled(not live_busy and not busy)
        self.monitor_refresh_button.setEnabled(not live_busy and not busy)
        self.custom_region.setEnabled(not live_busy and not busy)
        for control in (self.region_left,self.region_top,self.region_width,self.region_height):
            control.setEnabled(self.custom_region.isChecked() and not live_busy and not busy)
        self.backend_combo.setEnabled(not busy and not stopping)
        self._refresh_assist_controls()
        if not has_project:
            self.project_title.setText("No project open")
            self.train_data_status.setText("Create a project and review train + validation images.")
            return
        images = self.project.manifest.get("images",[])
        counts = {split: sum(1 for r in images if r.get("split")==split and r.get("reviewed")) for split in ("train","val","test")}
        reviewed = sum(1 for r in images if r.get("reviewed"))
        instances = sum(len(r.get("annotations",[])) for r in images)
        self.project_title.setText(self.project.manifest.get("name",self.project.root.name))
        self.dataset_summary.setText(f"{len(images)} images  ·  {instances} pairs  ·  {reviewed} reviewed")
        self.train_data_status.setText(f"Reviewed images: {counts['train']} train · {counts['val']} validation · {counts['test']} test\nUnreviewed images are excluded.")

    def _image_text(self, record):
        count = len(record.get("annotations",[]))
        state = "✓" if record.get("reviewed") else "○"
        name = Path(record["file"]).name
        return f"{state}  {name}\n{record.get('split','train')}  ·  {count} pair{'s' if count != 1 else ''}"

    def _refresh_images(self, select_id=None):
        select_id = select_id or self.current_image_id
        self.image_list.blockSignals(True)
        try:
            self.image_list.clear()
            self._image_items = {}
            self._image_items_project = self.project
            selected = None
            if self.project:
                for record in self.project.manifest.get("images",[]):
                    item = QListWidgetItem(self._image_text(record))
                    item.setData(Qt.ItemDataRole.UserRole,record["id"])
                    item.setToolTip(f"{record['file']}\nSource group: {record.get('group_id','')}\n{'Reviewed' if record.get('reviewed') else 'Unreviewed'}")
                    self.image_list.addItem(item)
                    self._image_items[record["id"]] = item
                    if record["id"] == select_id:
                        selected = item
            if selected:
                self.image_list.setCurrentItem(selected)
        finally:
            self.image_list.blockSignals(False)
        self._filter_images()

    def _refresh_image_record(self, image_id=None):
        """Update one row after an edit; never rebuild a 5,000-image sidebar."""
        image_id = image_id or self.current_image_id
        item = getattr(self, "_image_items", {}).get(image_id)
        if self.project is None or item is None or getattr(self, "_image_items_project", None) is not self.project:
            self._refresh_images(select_id=image_id)
            return
        record = self.project.record(image_id)
        self.image_list.blockSignals(True)
        try:
            item.setText(self._image_text(record))
            item.setToolTip(f"{record['file']}\nSource group: {record.get('group_id','')}\n{'Reviewed' if record.get('reviewed') else 'Unreviewed'}")
            if hasattr(self, "_record_visible"):
                item.setHidden(not self._record_visible(record))
        finally:
            self.image_list.blockSignals(False)

    def _filter_images(self):
        query = self.image_filter.text().strip().lower()
        for index in range(self.image_list.count()):
            item = self.image_list.item(index)
            item.setHidden(bool(query) and query not in item.text().lower())

    def _image_selected(self, current, previous):
        if self._changing or current is None or self.project is None:
            return
        image_id = current.data(Qt.ItemDataRole.UserRole)
        if image_id == self.current_image_id:
            return
        if not self._ready_to_leave():
            self.image_list.blockSignals(True)
            self.image_list.setCurrentItem(previous)
            self.image_list.blockSignals(False)
            return
        self._load_image(image_id)

    def _load_image(self, image_id):
        if self.project is None:
            return False
        if (self._assist_busy or self.canvas.has_assist) and not self._ready_to_leave():
            return False
        try:
            record = self.project.record(image_id)
        except Exception as exc:
            self._error("Cannot load image",str(exc))
            self._refresh_images()
            return False
        self._changing = True
        try:
            self.canvas.set_image(self.project.image_path(record),record.get("annotations",[]))
            self.current_image_id = image_id
            self._unsaved = False
            self._suggestions = None
            self._auto_assisted_boxes.clear()
            self._workflow_at_end = False
            self._refresh_instances()
            self._refresh_review()
        except Exception as exc:
            self._error("Cannot load image",str(exc))
            return False
        finally:
            self._changing = False
        self._refresh_summary()
        self._refresh_image_record(image_id)
        self._draft_changed(False)
        self.canvas.set_tool("box")
        self.canvas.setFocus()
        self.assist_status.setText("Drag a box to outline the next target automatically. Check the result, then Enter approves the image and moves next.")
        selected=self.image_list.currentItem()
        if selected is None or selected.data(Qt.ItemDataRole.UserRole)!=image_id:
            item = getattr(self, "_image_items", {}).get(image_id)
            if item is not None:
                self.image_list.blockSignals(True)
                self.image_list.setCurrentItem(item)
                self.image_list.scrollToItem(item)
                self.image_list.blockSignals(False)
            else:
                self._refresh_images(select_id=image_id)
        return True

    def _select_image_id(self, image_id):
        for index in range(self.image_list.count()):
            item = self.image_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == image_id:
                self.image_list.setCurrentItem(item)
                if self.current_image_id != image_id and self._ready_to_leave():
                    self._load_image(image_id)
                self.image_list.scrollToItem(item)
                return

    def _annotations_changed(self, annotations):
        if self._changing or self.project is None or self.current_image_id is None:
            return
        if self._workflow_at_end:
            self._workflow_at_end = False
            self.assist_status.setText("Labels changed. Check all targets, then press Enter to review this image again.")
        if self.canvas.has_assist and self._assist_context != self._assist_identity():
            if self._assist_busy and self._assist_thread is not None:
                self._assist_thread.cancel_request()
            self._clear_assist_state()
            self.assist_status.setText("The labels changed, so the old outline proposal was discarded. Request assistance for the current box.")
        self._unsaved = True
        if self._persist_current():
            self._refresh_instances()
            self._refresh_image_record()
            self._refresh_summary()

    def _persist_current(self):
        if self.project is None or self.current_image_id is None:
            return True
        try:
            self.project.set_annotations(self.current_image_id,deepcopy(self.canvas.annotations),reviewed=False)
            self._unsaved = False
            self._refresh_review()
            self._draft_changed(self.canvas.has_draft)
            return True
        except Exception as exc:
            self._unsaved = True
            self.save_status.setText("Unsaved edit · needs attention")
            self.save_status.setStyleSheet("color: #ffb6aa;")
            self._error("Edit could not be saved",str(exc))
            return False

    def _refresh_instances(self):
        self.instance_list.blockSignals(True)
        try:
            self.instance_list.clear()
            for index, ann in enumerate(self.canvas.annotations):
                complete = has_encasing(ann)
                count = len(ann.get("wrapper",[]))
                state = (f"Exact mask · {sum(ann['mask']['counts'][1::2]):,} foreground pixels" if ann.get("mask") is not None
                         else f"{count} encasing vertices") if complete else "Encasing needed"
                if ann.get("assist_warnings"):
                    state += " · check warnings"
                source = "model suggestion" if ann.get("source") == "model" else "assisted" if ann.get("source") == "assist" else "manual"
                if ann.get("box_source") == "yolo" or ann.get("source") == "yolo":
                    source = "YOLO box · assisted" if ann.get("source") == "assist" else "YOLO box"
                item = QListWidgetItem(f"{index+1}  player  ·  {source}\n{state}")
                item.setData(Qt.ItemDataRole.UserRole,ann["id"])
                self.instance_list.addItem(item)
                if ann["id"] == self.canvas.selected_id:
                    self.instance_list.setCurrentItem(item)
        finally:
            self.instance_list.blockSignals(False)
        self._update_pair_info()
        self._refresh_assist_controls()

    def _canvas_selection_changed(self, annotation_id):
        self.instance_list.blockSignals(True)
        try:
            self.instance_list.clearSelection()
            if not annotation_id:
                self.instance_list.setCurrentRow(-1)
            else:
                for index in range(self.instance_list.count()):
                    item = self.instance_list.item(index)
                    if item.data(Qt.ItemDataRole.UserRole) == annotation_id:
                        self.instance_list.setCurrentItem(item)
                        break
        finally:
            self.instance_list.blockSignals(False)
        self._update_pair_info()
        self._refresh_assist_controls()

    def _instance_selected(self, current, previous):
        if self._changing:
            return
        annotation_id = current.data(Qt.ItemDataRole.UserRole) if current else ""
        if not self.canvas.select(annotation_id):
            self.instance_list.blockSignals(True)
            self.instance_list.setCurrentItem(previous)
            self.instance_list.blockSignals(False)

    def _update_pair_info(self):
        ann = self.canvas.selected_annotation()
        if ann is None:
            self.pair_info.setText("Select an instance to inspect its two labels.")
            return
        x1,y1,x2,y2 = ann["box"]
        vertices = len(ann.get("wrapper",[]))
        detail = f"Exact mask: {sum(ann['mask']['counts'][1::2]):,} foreground pixels" if ann.get("mask") is not None else f"Encasing: {vertices} vertices"
        warnings = "\n".join(str(v) for v in ann.get("assist_warnings", []))
        self.pair_info.setText(f"Main box: {x2-x1:.1f} × {y2-y1:.1f} px\n{detail}\nPair ID: {ann['id'][:12]}\n" + ("Both labels present." if has_encasing(ann) else "Draw the linked encasing to complete this pair.") + ("\n" + warnings if warnings else ""))

    def _refresh_review(self):
        if self.project is None or self.current_image_id is None:
            return
        record = self.project.record(self.current_image_id)
        reviewed = bool(record.get("reviewed"))
        self.reviewed_check.blockSignals(True)
        self.reviewed_check.setChecked(reviewed)
        self.reviewed_check.blockSignals(False)
        self.split_combo.blockSignals(True)
        self.split_combo.setCurrentIndex(max(0,self.split_combo.findData(record.get("split","train"))))
        self.split_combo.blockSignals(False)
        annotations = record.get("annotations",[])
        incomplete = sum(1 for ann in annotations if not has_encasing(ann))
        if reviewed:
            self.review_status.setText("Reviewed negative image." if not annotations else "Reviewed · eligible for this split.")
            self.review_status.setStyleSheet("color: #6ce3cb;")
        elif incomplete:
            self.review_status.setText(f"Unreviewed · {incomplete} pair(s) still need an encasing.")
            self.review_status.setStyleSheet("color: #ffc38b;")
        else:
            self.review_status.setText("Unreviewed. Check every pair before marking reviewed." if annotations else "No labels. Review only if this is an intentional negative.")
            self.review_status.setStyleSheet("")
        group = str(record.get("group_id",""))
        self.group_label.setText("Group: " + (group if len(group)<38 else group[:35]+"…"))
        self.group_label.setToolTip(group)

    def _review_changed(self, reviewed):
        if self._changing or not self._need_image():
            return
        if not self._ready_to_leave():
            self._refresh_review()
            return
        try:
            self.project.set_reviewed(self.current_image_id,reviewed)
        except Exception as exc:
            self._error("Image is not ready for review",str(exc))
        self._refresh_review()
        self._refresh_image_record()
        self._refresh_summary()

    def _mark_negative(self):
        if not self._need_image():
            return
        if self.canvas.annotations or self.canvas.has_draft:
            self._error("Image contains labels","A negative image must have no instances. Keep existing labels unless this image truly contains no target players.")
            return
        record = self.project.record(self.current_image_id)
        if self._unsaved or record.get("annotations") != self.canvas.annotations:
            if not self._persist_current():
                return
        self._review_changed(True)
        self.canvas.setFocus()

    def _split_changed(self):
        if self._changing or self.project is None or self.current_image_id is None:
            return
        try:
            self.project.set_split(self.current_image_id,self.split_combo.currentData())
            self._refresh_images()
            self._refresh_summary()
            self.statusBar().showMessage("The whole source group moved to this split.",8000)
        except Exception as exc:
            self._error("Could not change split",str(exc))
            self._refresh_review()

    def _set_group(self):
        if not self._need_image() or not self._ready_to_leave():
            return
        record = self.project.record(self.current_image_id)
        group,okay = QInputDialog.getText(self,"Source group","Use the same group for related or nearby frames.",text=str(record.get("group_id","")))
        if okay and group.strip():
            try:
                self.project.set_group(self.current_image_id,group.strip())
                self._refresh_review()
                self._refresh_images()
                self._refresh_summary()
            except Exception as exc:
                self._error("Could not change source group",str(exc))

    def _assign_splits(self):
        if not self._need_project() or not self._ready_to_leave():
            return
        fraction,okay = QInputDialog.getDouble(self,"Group-aware dataset split","Validation fraction (whole groups are kept together)",.2,.05,.8,2)
        if not okay:
            return
        try:
            self.project.assign_splits(val_fraction=fraction,seed=self.train_seed.value())
            self._refresh_images()
            self._refresh_review()
            self._refresh_summary()
            self.statusBar().showMessage("Assigned groups to train and validation. Review the split counts before training.",12000)
        except Exception as exc:
            self._error("Could not assign splits",str(exc))

    def _redraw_wrapper(self):
        if self._workflow_is_busy():
            return False
        ann = self.canvas.selected_annotation()
        if ann is None:
            self._canvas_problem("Select an instance first.")
            return False
        if self.canvas.has_assist:
            if not self._assist_points and not (self._assist_result and has_encasing(self._assist_result)):
                self._reject_assist()
            elif not self._resolve_assist():
                return False
        if self.canvas.has_draft and not self.canvas.resolve_draft(self):
            return
        if has_encasing(ann):
            choice = QMessageBox.question(self,"Redraw encasing","Clear this instance's encasing and draw it again? The main box stays linked, and Undo restores the old encasing.")
            if choice != QMessageBox.StandardButton.Yes:
                return
        self.canvas.clear_wrapper()
        self.canvas.setFocus()
        self.assist_status.setText("Draw the encasing points manually. Enter finishes a valid outline, reviews every complete pair, and moves next.")
        return True

    def _import_images(self):
        if self._defer_until_live_stopped(self._import_images,"Stopping live detection before importing images…"):
            return
        if not self._need_project() or not self._ready_to_leave():
            return
        paths,_ = QFileDialog.getOpenFileNames(self,"Import images","","Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)")
        if not paths:
            return
        project = self.project
        def work(progress,should_stop):
            records,errors = [],[]
            for index,path in enumerate(paths):
                if should_stop():
                    break
                try:
                    records.append(project.add_image(path))
                except Exception as exc:
                    errors.append(f"{Path(path).name}: {exc}")
                progress({"event":"import","completed":index+1,"total":len(paths)})
            return {"records":records,"errors":errors}
        def done(result):
            self._refresh_images()
            if result["records"]:
                self._select_image_id(result["records"][0]["id"])
            self._refresh_summary()
            self.tabs.setCurrentWidget(self.annotate_tab)
            self.statusBar().showMessage(f"Imported {len(result['records'])} image(s); identical pixels reuse the existing record.",12000)
            if result["errors"]:
                self._error("Some images were not imported",f"{len(result['errors'])} image(s) failed.","\n".join(result["errors"]))
        self._start_job("Import images",work,done,cancellable=True)

    def _import_video(self):
        if self._defer_until_live_stopped(self._import_video,"Stopping live detection before importing video…"):
            return
        if not self._need_project() or not self._ready_to_leave():
            return
        try:
            import importlib.util
            if importlib.util.find_spec("cv2") is None:
                self._error("Optional video dependency","Video import needs opencv-python. From this program's environment, run: python -m pip install opencv-python\n\nImage import and screen capture are already available.")
                return
        except Exception as exc:
            self._error("Video import unavailable",str(exc))
            return
        path,_ = QFileDialog.getOpenFileName(self,"Import video frames","","Video (*.mp4 *.mkv *.avi *.mov *.webm)")
        if not path:
            return
        interval,okay = QInputDialog.getDouble(self,"Video sampling","Take one frame every (seconds)",2.,.1,3600.,1)
        if not okay:
            return
        maximum,okay = QInputDialog.getInt(self,"Bounded video import","Maximum frames",100,1,10000)
        if not okay:
            return
        project = self.project
        group_id = "video-" + uuid4().hex
        def work(progress,should_stop):
            import cv2
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                raise ValueError("This video could not be opened by OpenCV.")
            records=[]
            try:
                for index in range(maximum):
                    if should_stop():
                        break
                    cap.set(cv2.CAP_PROP_POS_MSEC,index*interval*1000.)
                    success,frame = cap.read()
                    if not success:
                        break
                    rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
                    record=project.add_pil(Image.fromarray(rgb),name=f"{Path(path).stem}-{index:05d}",group_id=group_id)
                    records.append(record)
                    progress({"event":"video_import","completed":index+1,"total":maximum})
            finally:
                cap.release()
            if not records and not should_stop():
                raise ValueError("No video frames could be decoded.")
            return {"records":records}
        def done(result):
            self._refresh_images()
            if result["records"]:
                self._select_image_id(result["records"][0]["id"])
            self._refresh_summary()
            self.statusBar().showMessage(f"Saved {len(result['records'])} sampled frame(s) in one source group.",12000)
        self._start_job("Import video",work,done,cancellable=True)

    def _refresh_monitors(self):
        if self._defer_until_live_stopped(self._refresh_monitors,"Stopping live detection before changing the capture source…"):
            return
        try:
            import mss
            if self._capture is not None:
                self._capture.close()
            self._capture = mss.mss()
            monitors = list(self._capture.monitors)
            self.monitor_combo.blockSignals(True)
            self.monitor_combo.clear()
            for index,monitor in enumerate(monitors):
                name = "All monitors" if index==0 else f"Monitor {index}"
                name += f" · {monitor['width']} × {monitor['height']}"
                self.monitor_combo.addItem(name,dict(monitor))
            if len(monitors)>1:
                self.monitor_combo.setCurrentIndex(1)
            self.monitor_combo.blockSignals(False)
            self._monitor_changed()
        except Exception as exc:
            self._capture = None
            self.monitor_combo.blockSignals(False)
            self._error("Screen capture unavailable",str(exc))

    def _monitor_changed(self):
        region=self.monitor_combo.currentData()
        if region and not self.custom_region.isChecked():
            self.region_left.setValue(region["left"])
            self.region_top.setValue(region["top"])
            self.region_width.setValue(region["width"])
            self.region_height.setValue(region["height"])

    def _region_toggle(self, custom):
        for widget in (self.region_left,self.region_top,self.region_width,self.region_height):
            widget.setEnabled(custom)
        if not custom:
            self._monitor_changed()

    def _capture_region(self):
        region={"left":self.region_left.value(),"top":self.region_top.value(),
                "width":self.region_width.value(),"height":self.region_height.value()}
        if region["width"]*region["height"]>50000000:
            raise ValueError("The capture region is limited to 50 million pixels per frame. Choose a smaller region.")
        return region

    def _grab_frame(self):
        if self._capture is None:
            self._refresh_monitors()
        if self._capture is None:
            return None
        raw=self._capture.grab(self._capture_region())
        image=Image.frombytes("RGB",raw.size,raw.bgra,"raw","BGRX")
        self._last_frame=image
        self._frame_serial += 1
        self._capture_prediction=None
        self.capture_preview.set_frame(image)
        return image

    def _start_preview(self):
        if self._defer_until_live_stopped(self._start_preview,"Stopping the detector before starting capture preview…"):
            return
        if self._job:
            return
        try:
            if self._grab_frame() is None:
                return
            self._capture_timer.start(self.preview_ms.value())
            self.capture_status.setText("Preview running. Frames are saved only when requested.")
            self._refresh_summary()
        except Exception as exc:
            self._stop_capture()
            self._error("Cannot start capture",str(exc))

    def _preview_tick(self):
        try:
            if self._grab_frame() is None:
                self._stop_capture()
                return
            self._refresh_summary()
        except Exception as exc:
            self._stop_capture()
            self._error("Capture stopped",str(exc))

    def _stop_capture(self):
        self._capture_timer.stop()
        self._sample_timer.stop()
        if self._live_session is not None:
            self._request_live_stop(freeze=True,reason="Live result frozen.")
            return
        self.capture_status.setText("Capture frozen. Save this frame or run the loaded detector on it." if self._last_frame is not None else "Capture idle. Nothing is recorded automatically.")
        self._refresh_summary()

    def _add_capture_frame(self):
        if self._live_session is not None:
            self._request_live_stop(freeze=True,after=self._add_capture_frame,reason="Freezing the latest result before saving…")
            return
        if not self._need_project() or not self._ready_to_leave():
            return
        prediction=self._capture_prediction if self._capture_prediction and self._capture_prediction.get("serial")==self._frame_serial else None
        proposed=deepcopy(prediction["result"].get("detections",[])) if prediction else []
        self._stop_capture()
        try:
            if self._last_frame is None and self._grab_frame() is None:
                return
            record=self.project.add_pil(self._last_frame.copy(),name="capture-"+datetime.now().strftime("%Y%m%d-%H%M%S"),
                                        split=self.capture_split.currentData(),group_id=self._capture_group)
            self._refresh_images()
            self._select_image_id(record["id"])
            self._refresh_summary()
            self.tabs.setCurrentWidget(self.annotate_tab)
            if proposed:
                self._suggestions={"target":("image",record["id"]),"detections":proposed,"image":self._last_frame.copy()}
                self._add_suggestions()
                self.statusBar().showMessage("Saved the frozen frame with its matching model suggestions, all unreviewed. Check every pair before review.",15000)
            else:
                self.statusBar().showMessage("Frame saved as unreviewed. Draw a box and its encasing for each player, then review the image.",12000)
        except Exception as exc:
            self._error("Could not save capture",str(exc))

    def _start_sampling(self):
        if self._defer_until_live_stopped(self._start_sampling,"Stopping live detection before collecting annotation samples…"):
            return
        if not self._need_project() or not self._ready_to_leave():
            return
        self._capture_group="capture-"+uuid4().hex
        self._samples_done=0
        self._samples_limit=self.sample_count.value()
        self._samples_initial_count=len(self.project.manifest.get("images",[]))
        self._samples_split=self.capture_split.currentData()
        self.sample_progress.setRange(0,self._samples_limit)
        self.sample_progress.setValue(0)
        try:
            if self._capture is None:
                self._refresh_monitors()
            if self._capture is None:
                return
            self._sample_timer.start(round(self.sample_interval.value()*1000))
            self._sample_tick()
            self._refresh_summary()
        except Exception as exc:
            self._stop_capture()
            self._error("Cannot start sampling",str(exc))

    def _sample_tick(self):
        if self.project is None:
            self._stop_sampling()
            return
        try:
            image=self._grab_frame()
            if image is None:
                self._stop_capture()
                return
            self.project.add_pil(image,name=f"sample-{self._samples_done:05d}",split=self._samples_split,group_id=self._capture_group)
            self._samples_done += 1
            self.sample_progress.setValue(self._samples_done)
            unique=len(self.project.manifest.get("images",[]))-self._samples_initial_count
            self.capture_status.setText(f"Sampled {self._samples_done} / {self._samples_limit} frames · {unique} new images. Identical frames are deduplicated.")
            if self._samples_done>=self._samples_limit:
                self._sample_timer.stop()
                self._capture_timer.stop()
                self.capture_status.setText(f"Sampling complete: {self._samples_done} captured frames, {unique} new images. Review and label them in Annotate.")
            self._refresh_images()
            self._refresh_summary()
        except Exception as exc:
            self._stop_capture()
            self._error("Sampling stopped",str(exc))

    def _stop_sampling(self):
        self._sample_timer.stop()
        self.capture_status.setText(f"Sampling stopped after {self._samples_done} frame(s). Saved frames remain in the project.")
        self._refresh_summary()

    def _live_settings(self):
        return {"confidence":self.live_confidence.value(),"nms_iou":self.live_nms.value(),
                "max_detections":self.live_maximum.value(),"min_height":self.live_minimum.value(),"debug":True}

    def _update_live_settings(self, *args):
        if self._live_session is not None and not self._live_stopping:
            try:
                self._live_session.update_settings(settings=self._live_settings(),target_fps=self.live_target_fps.value())
            except Exception as exc:
                self._live_error=str(exc)
                self._request_live_stop(reason="Live settings could not be applied: "+str(exc))

    def _start_live(self):
        if self._job is not None or self._live_session is not None:
            return
        if not self._need_model() or not self._ready_to_leave():
            return
        self._capture_timer.stop()
        self._sample_timer.stop()
        if self.monitor_combo.count()==0:
            self._refresh_monitors()
        if self.monitor_combo.count()==0 and not self.custom_region.isChecked():
            self.live_status.setText("Choose a working monitor or custom region before starting live detection.")
            return
        try:
            region=self._capture_region()
            # Main-thread preview capture and worker capture never share an MSS
            # instance; each implementation creates and closes its own handle.
            if self._capture is not None:
                self._capture.close()
                self._capture=None
            from .live import LiveSession
            session=LiveSession(self._runtime,region,settings=self._live_settings(),target_fps=self.live_target_fps.value())
            self._live_session=session
            self._live_frame=None
            self._live_stopping=False
            self._live_freeze_pending=False
            self._live_stop_message="Live detection stopped."
            self._live_error=None
            self._live_fps=0.
            self._live_started=time.monotonic()
            self._live_preview_stale=False
            self._after_live_stop=None
            self._capture_prediction=None
            self._suggestions=None
            self._capture_group="live-"+uuid4().hex
            self.capture_preview.set_frame(None)
            self.live_stats.setText("Main boxes —  ·  Encasings —  ·  Invalid —")
            self.live_timing.setText("Waiting for the first result…")
            self.live_status.setText("Live detection starting · "+self._execution_label())
            self.capture_status.setText("Live detection is running. Frames are saved only when you choose Save frame → annotate.")
            if self.overlay_check.isChecked():
                self._enable_overlay(region)
            session.start()
            self._live_timer.start()
            self.tabs.setCurrentWidget(self.capture_tab)
            self.statusBar().showMessage("Live detection running · "+self._execution_label())
            self._refresh_summary()
        except Exception as exc:
            self._clear_overlay(close=True)
            session=self._live_session
            if session is not None and session.is_running:
                self._live_error=str(exc)
                self._request_live_stop(reason="Live detection could not start: "+str(exc))
            else:
                self._live_session=None
                self._live_timer.stop()
                self.live_status.setText("Live detection could not start: "+str(exc))
                self._refresh_summary()
            self.statusBar().showMessage("Live detection could not start: "+str(exc),15000)

    def _defer_until_live_stopped(self, callback, reason):
        if self._live_session is None:
            return False
        self._request_live_stop(after=callback,reason=reason)
        return True

    def _stop_live(self):
        self._request_live_stop(reason="Live detection stopped. The last captured image is available to inspect.")

    def _request_live_stop(self, *, freeze=False, after=None, reason="Live detection stopped."):
        self._clear_overlay(close=True)
        if after is not None:
            # Only one continuation is retained. A later explicit Close replaces
            # an earlier pending action instead of accumulating background work.
            self._after_live_stop=after
        self._live_stop_message=reason
        self._live_freeze_pending=bool(freeze and self._live_frame is not None)
        if self._live_freeze_pending:
            self._accept_live_frame(self._live_frame,frozen=True)
        else:
            self._clear_live_predictions()
        self.live_status.setText(reason)
        self.statusBar().showMessage(reason)
        if self._live_session is None:
            callback=self._after_live_stop
            self._after_live_stop=None
            self._refresh_summary()
            if callback is not None:
                QTimer.singleShot(0,callback)
            return
        self._live_stopping=True
        self._live_session.request_stop()
        self._live_timer.start()
        self._refresh_summary()
        # Never join a running inference call on the GUI thread. The timer keeps
        # the app responsive until the single in-flight prediction completes.
        if not self._live_session.is_running:
            self._poll_live()

    def _clear_live_predictions(self):
        self.capture_preview.set_frame(self._last_frame)
        self._capture_prediction=None
        if self._suggestions and self._suggestions.get("target",("",))[0]=="capture":
            self._suggestions=None

    def _freeze_live_result(self):
        if self._live_frame is None:
            self.live_status.setText("Wait for a live result before freezing it.")
            return
        if self._live_session is not None:
            self._request_live_stop(freeze=True,reason="Frozen live result. Save the matching frame and suggestions to annotate them.")
        else:
            self._accept_live_frame(self._live_frame,frozen=True)
            self.live_status.setText("Frozen live result. Save the matching frame and suggestions to annotate them.")
            self.statusBar().showMessage(self.live_status.text())
            self._refresh_summary()

    def _poll_live(self):
        session=self._live_session
        if session is None:
            self._live_timer.stop()
            return
        try:
            packet=session.poll()
            if packet is not None and not self._live_stopping:
                if self._live_frame is None or packet.sequence>self._live_frame.sequence:
                    previous=self._live_frame
                    if previous is not None and packet.completed_monotonic>previous.completed_monotonic:
                        rate=(packet.sequence-previous.sequence)/(packet.completed_monotonic-previous.completed_monotonic)
                        self._live_fps=rate if not self._live_fps else .7*self._live_fps+.3*rate
                    self._accept_live_frame(packet)
            status=session.status()
            error=status.get("last_error")
            if error and error!=self._live_error:
                self._live_error=str(error)
                self._request_live_stop(reason="Live detection stopped: "+self._live_error)
                return
            if not session.is_running:
                if not session.join(timeout=0):
                    return
                self._finish_live_session()
                return
            if not self._live_stopping:
                self._update_live_metrics()
                self._check_overlay_health()
        except Exception as exc:
            self._live_error=str(exc)
            self._clear_overlay(close=True)
            self._clear_live_predictions()
            self.live_status.setText("Live detection stopped: "+str(exc))
            self.statusBar().showMessage(self.live_status.text())
            self._live_stopping=True
            self._live_stop_message="Live detection stopped: "+str(exc)
            session.request_stop()
            self._refresh_summary()

    def _finish_live_session(self):
        self._live_timer.stop()
        self._clear_overlay(close=True)
        if not self._live_freeze_pending:
            self._clear_live_predictions()
        self._live_session=None
        self._live_stopping=False
        self._live_started=None
        if self._live_error:
            self.live_status.setText("Live detection stopped: "+self._live_error)
            self.statusBar().showMessage(self.live_status.text(),15000)
        else:
            self.live_status.setText(getattr(self,"_live_stop_message","Live detection stopped."))
            self.statusBar().showMessage(self.live_status.text())
        self.capture_status.setText("Capture is frozen. Choose Save frame → annotate to keep the displayed frame.")
        callback=self._after_live_stop
        self._after_live_stop=None
        self._refresh_summary()
        if callback is not None:
            QTimer.singleShot(0,callback)

    @staticmethod
    def _public_prediction(result):
        public=deepcopy(result)
        public.pop("diagnostics",None)
        for detection in public.get("detections",[]):
            detection.pop("primary_box",None)
        return public

    def _accept_live_frame(self, packet, *, frozen=False):
        self._live_frame=packet
        self._last_frame=packet.image
        self._frame_serial+=1
        result=packet.result
        detections=result.get("detections",[])
        primary=result.get("diagnostics",{}).get("primary_detections",[])
        self._capture_prediction={"serial":self._frame_serial,"result":deepcopy(result),"live_sequence":packet.sequence}
        self._suggestions={"target":("capture",self._frame_serial),"detections":deepcopy(detections),"image":packet.image}
        self.capture_preview.diagnostic=self.diagnostic_check.isChecked()
        stale=packet.age_ms()>self._live_stale_ms and not frozen
        self._live_preview_stale=stale
        self.capture_preview.set_frame(packet.image,[] if stale else detections,[] if stale else primary)
        self._last_result=self._public_prediction(result)
        if frozen or self.tabs.currentWidget()==self.inspect_tab:
            self.result_text.setPlainText(json.dumps(self._last_result,indent=2,ensure_ascii=False,default=str))
        if not frozen and self._overlay is not None and self._overlay.enabled:
            self._overlay.show_frame(packet,diagnostic=self.diagnostic_check.isChecked())
            self._check_overlay_health()
        self._update_live_metrics(frozen=frozen)
        self._refresh_summary()

    def _update_live_metrics(self, *, frozen=False):
        packet=self._live_frame
        if packet is None:
            elapsed=time.monotonic()-self._live_started if self._live_started is not None else 0.
            self.live_timing.setText(f"Waiting for the first result · {elapsed:.1f} s elapsed")
            return
        result=packet.result
        diagnostics=result.get("diagnostics",{})
        emitted=len(result.get("detections",[]))
        invalid=int(result.get("invalid_contours",0))
        primary=diagnostics.get("primary_count",len(diagnostics.get("primary_detections",[])))
        candidates=diagnostics.get("candidates_above_threshold")
        maximum=diagnostics.get("max_confidence")
        counts=f"Main boxes {primary}  ·  Encasings {emitted}  ·  Invalid {invalid}"
        if candidates is not None:
            counts+=f"  ·  Candidates {candidates}"
        if isinstance(maximum,(float,int)):
            counts+=f"  ·  Highest confidence {maximum:.3f}"
        self.live_stats.setText(counts)
        age=packet.age_ms()
        processing=result.get("timing_ms",{}).get("total",packet.inference_ms)
        self.live_timing.setText(f"{self._live_fps:.1f} FPS  ·  {processing:.1f} ms processing  ·  {age:.0f} ms frame age  ·  Frame {packet.sequence}"+(" · FROZEN" if frozen else ""))
        if frozen:
            return
        if age>self._live_stale_ms:
            if not self._live_preview_stale:
                self.capture_preview.set_frame(packet.image)
                self._live_preview_stale=True
            self._clear_overlay()
            self.live_status.setText("This result is old; live outlines are hidden while a fresh frame is processed. Freeze to inspect the recorded result.")
            return
        if not emitted:
            if invalid:
                explanation=f"{primary} main box(es), but {invalid} encasing(s) were invalid. Enable first-stage boxes to inspect them."
            elif primary:
                explanation="Main boxes were found, but no valid encasings were returned."
            else:
                threshold=diagnostics.get("confidence_threshold",self.live_confidence.value())
                explanation=f"No main boxes passed confidence {threshold:.3f}."
                if isinstance(maximum,(float,int)):
                    explanation+=f" Highest eligible confidence: {maximum:.3f}."
            self.live_status.setText(explanation+" · "+self._execution_label())
        else:
            self.live_status.setText(f"Live: {emitted} encasing(s) on the matching captured frame · "+self._execution_label())

    def _clear_overlay(self, *, close=False):
        if self._overlay is not None:
            self._overlay.clear()
            if close:
                self._overlay.close()

    def _check_overlay_health(self):
        if (self._overlay is not None and self.overlay_check.isChecked()
                and not self._overlay.enabled and self._live_session is not None
                and not self._live_stopping):
            reason=str(self._overlay.reason or "Desktop overlay is unavailable.")
            self.overlay_check.blockSignals(True)
            self.overlay_check.setChecked(False)
            self.overlay_check.blockSignals(False)
            self._clear_overlay(close=True)
            self.overlay_status.setText("Preview only: "+reason)

    def _enable_overlay(self, region):
        try:
            from .overlay import OverlayManager
            if self._overlay is None:
                self._overlay=OverlayManager(parent=self,stale_after_ms=self._live_stale_ms)
            enabled,reason=self._overlay.enable(region)
            if not enabled:
                self.overlay_check.blockSignals(True)
                self.overlay_check.setChecked(False)
                self.overlay_check.blockSignals(False)
                self._clear_overlay(close=True)
                self.overlay_status.setText("Preview only: "+str(reason))
                return False
            self.overlay_status.setText("Desktop overlay active. Old results are hidden automatically.")
            return True
        except Exception as exc:
            self.overlay_check.blockSignals(True)
            self.overlay_check.setChecked(False)
            self.overlay_check.blockSignals(False)
            self._clear_overlay(close=True)
            self.overlay_status.setText("Preview only: "+str(exc))
            return False

    def _overlay_choice_changed(self, checked):
        if not checked:
            self._clear_overlay(close=True)
            self.overlay_status.setText("Desktop overlay off. The preview still shows the model's captured frame.")
        elif self._live_session is not None and not self._live_stopping:
            region=self._live_frame.region if self._live_frame is not None else self._capture_region()
            if self._enable_overlay(region) and self._live_frame is not None:
                self._overlay.show_frame(self._live_frame,diagnostic=self.diagnostic_check.isChecked())
        else:
            self.overlay_status.setText("The desktop overlay will start with live detection when capture exclusion is supported.")

    def _choose_run_folder(self):
        folder=QFileDialog.getExistingDirectory(self,"Choose the parent folder for training runs",self.run_folder.text())
        if folder:
            self.run_folder.setText(folder)

    def _choose_resume(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose a Cloud training checkpoint","","Cloud training checkpoints (*.cloudtrain)")
        if not path:
            return
        try:
            self._set_resume_checkpoint_path(path)
        except Exception as exc:
            self._error("Cannot resume this checkpoint", str(exc))

    def _set_initial_model_path(self, path):
        from .format import load_cloud
        model_config, _, _ = load_cloud(path)
        values = self._training_settings_values()
        values.update({key: getattr(model_config, key) for key in
                       ("architecture", "input_size", "vertices", "channels", "roi_size", "mask_size")})
        values.update({"init_model_path": str(Path(path).expanduser()), "resume_path": None})
        self._apply_training_settings(values, self.training_preset.currentData())
        self.train_status.setText("Model capacities matched. This new run starts from the selected .cloud with a new optimizer and all currently reviewed training data.")

    def _set_resume_checkpoint_path(self, path):
        from .training import _load_checkpoint
        from .training_settings import resolve_settings
        checkpoint = _load_checkpoint(path)
        values = resolve_settings("legacy", checkpoint["config"],
                                  {"resume_path": str(Path(path).expanduser()), "init_model_path": None})
        self._apply_training_settings(values, "legacy")
        self.train_status.setText("Restored checkpoint settings. Keep the reviewed data unchanged; only permitted continuation settings are editable. Increase the epoch limit if needed; a resumed schedule keeps its original learning-rate horizon.")

    def _start_training(self):
        if self._defer_until_live_stopped(self._start_training,"Stopping live detection before training…"):
            return
        if not self._need_project() or not self._ready_to_leave():
            return
        try:
            values = self._training_settings_values(require_initialization=True)
        except Exception as exc:
            self._error("Check training settings", str(exc))
            return
        try:
            train_records=self.project.training_records("train")
            val_records=self.project.training_records("val")
            if not train_records or (not val_records and not values["allow_no_validation"]):
                self._error("Reviewed train and validation data required",
                            "Review at least one training image and one validation image. Use separate source groups so nearby frames do not leak into validation. At least one training image must contain a complete player pair.")
                return
            issues=self.project.validate()
            if issues:
                self._error("Dataset needs attention",f"Resolve {len(issues)} dataset issue(s) before training.","\n".join(issues))
                return
        except Exception as exc:
            self._error("Cannot validate dataset",str(exc))
            return
        parent=Path(self.run_folder.text().strip() or str(self.project.root/"runs")).expanduser()
        run_path=parent/("run-"+datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid4().hex[:4])
        project_path=str(self.project.root)
        self.train_log.clear()
        self.train_log.appendPlainText("Output: "+str(run_path))
        self.train_log.appendPlainText(json.dumps(values,indent=2))
        self.train_progress.setRange(0,values["epochs"])
        self.train_progress.setValue(0)
        self.train_status.setText("Preparing the dataset and model…")
        def work(progress,should_stop):
            from .training import TrainingConfig, train
            config=TrainingConfig(**values)
            return train(project_path,str(run_path),config,progress=progress,should_stop=should_stop)
        def done(result):
            self.train_log.appendPlainText("\nRun result:\n"+json.dumps(result,indent=2,default=str))
            self.train_status.setText("Run finished. Inspect the saved metrics and best / last .cloud files below.")
            self._last_training_run=run_path
            self._refresh_summary()
        self._start_job("Training",work,done,cancellable=True)

    def _start_job(self, name, function, on_result, cancellable=False):
        if self._defer_until_live_stopped(lambda:self._start_job(name,function,on_result,cancellable),f"Stopping live detection before {name.lower()}…"):
            return True
        if self._job is not None or self._assist_busy:
            self._error("Work already running","Wait for the current operation to finish, or request its cancellation.")
            return False
        self._stop_capture()
        stop_event=threading.Event()
        thread=QThread(self)
        worker=Worker(function,stop_event)
        worker.moveToThread(thread)
        self._job={"name":name,"thread":thread,"worker":worker,"stop":stop_event,"cancellable":cancellable,"on_result":on_result}
        thread.started.connect(worker.run)
        worker.result.connect(self._job_result)
        worker.progress.connect(self._job_progress)
        worker.error.connect(self._job_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._job_finished)
        thread.finished.connect(thread.deleteLater)
        self.annotate_tab.setEnabled(False)
        self.capture_tab.setEnabled(False)
        self.training_config_panel.setEnabled(False)
        self.backend_combo.setEnabled(False)
        self.train_stop.setEnabled(cancellable)
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.setEnabled(cancellable)
        self.train_stop.setText("Stop after current batch" if name=="Training" else "Cancel current operation")
        self._refresh_summary()
        self.statusBar().showMessage(name+" running…")
        thread.start()
        return True

    @Slot(object)
    def _job_result(self, result):
        # A QObject slot pins completion handling to the GUI thread, including
        # callbacks that update image lists, model state and widgets.
        if self._job is not None:
            try:
                self._job["on_result"](result)
            except Exception:
                self._job_error(traceback.format_exc())

    @Slot(object)
    def _job_progress(self, data):
        if not isinstance(data,dict):
            data={"message":str(data)}
        if self._job and self._job["name"]=="Training":
            self.train_log.appendPlainText(json.dumps(data,ensure_ascii=False,default=str))
            epoch=data.get("epoch")
            if isinstance(epoch,(int,float)):
                self.train_progress.setValue(int(epoch))
            event=data.get("event","progress")
            pieces=[str(event)]
            if epoch is not None:
                pieces.append(f"epoch {epoch} / {data.get('epochs',self.train_epochs.value())}")
            if isinstance(data.get("loss"),(float,int)):
                pieces.append(f"loss {data['loss']:.5f}")
            self.train_status.setText(" · ".join(pieces))
        else:
            completed,total=data.get("completed"),data.get("total")
            message=f"{self._job['name'] if self._job else 'Work'}: {completed} / {total}" if completed is not None else json.dumps(data,default=str)
            self.statusBar().showMessage(message)

    @Slot(str)
    def _job_error(self, detail):
        name=self._job["name"] if self._job else "Operation"
        message=detail.strip().splitlines()[-1] if detail.strip() else "Unknown error"
        if name=="Training":
            self.train_status.setText("Training stopped with an error. Details are in the run log.")
            self.train_log.appendPlainText(detail)
        self._error(name+" failed",message,detail)

    @Slot()
    def _job_finished(self):
        finished_name=self._job["name"] if self._job else "Operation"
        self._job=None
        self.annotate_tab.setEnabled(True)
        self.capture_tab.setEnabled(True)
        self.training_config_panel.setEnabled(True)
        self.backend_combo.setEnabled(True)
        self.train_stop.setEnabled(False)
        self.cancel_button.hide()
        self.train_stop.setText("Stop after current batch")
        self._refresh_images()
        self._refresh_summary()
        if self.statusBar().currentMessage()==finished_name+" running…":
            self.statusBar().showMessage(finished_name+" finished.",8000)
        if self._close_when_idle:
            self._close_when_idle=False
            QTimer.singleShot(0,self.close)

    def _cancel_job(self):
        if self._job and self._job["cancellable"]:
            self._job["stop"].set()
            self.train_stop.setEnabled(False)
            self.cancel_button.setEnabled(False)
            self.train_status.setText("Stop requested. Waiting for the current batch or operation to finish safely…")
            self.statusBar().showMessage("Stop requested; saving work and waiting for the worker to finish.")

    def _load_model(self):
        if self._defer_until_live_stopped(self._load_model,"Stopping live detection before loading a model…"):
            return
        if not self._ready_to_leave():
            return
        path,_=QFileDialog.getOpenFileName(self,"Load a Cloud or ONNX model","","Cloud and ONNX models (*.cloud model.json);;Cloud models (*.cloud);;ONNX export manifest (model.json)")
        if not path:
            return
        self._load_model_path(path)

    def _load_model_folder(self):
        if self._defer_until_live_stopped(self._load_model_folder,"Stopping live detection before loading a model…"):
            return
        if not self._ready_to_leave():
            return
        folder=QFileDialog.getExistingDirectory(self,"Choose the folder containing model.json and both ONNX graphs")
        if folder:
            self._load_model_path(folder)

    def _load_model_path(self,path,backend=None,device=None,open_live=False):
        if self._defer_until_live_stopped(lambda:self._load_model_path(path,backend,device,open_live),"Stopping live detection before switching models…"):
            return False
        if not self._ready_to_leave():
            return False
        try:
            source=Path(path).expanduser().resolve()
            if source.is_dir():
                if not (source/"model.json").is_file():
                    raise ValueError("Choose an ONNX export folder containing model.json, detector.onnx and wrapper.onnx.")
                kind="onnx"
            elif source.name.lower()=="model.json" and source.is_file():
                source=source.parent
                kind="onnx"
            elif source.suffix.lower()==".cloud" and source.is_file():
                kind="cloud"
            else:
                raise ValueError("Select a .cloud file or the model.json supplied with both ONNX graphs. A detector.onnx file alone does not include the guided wrapper.")
            selected_backend,selected_device=self.backend_combo.currentData() or ("numpy","cpu")
            if backend is None:
                backend=selected_backend if (selected_backend=="onnx")== (kind=="onnx") else "onnx" if kind=="onnx" else "numpy"
            if (backend=="onnx")!=(kind=="onnx"):
                raise ValueError("Use an ONNX backend for model.json / ONNX folders, and NumPy or PyTorch for a .cloud file.")
            if backend not in ("onnx","numpy","torch"):
                raise ValueError("Unsupported inference backend.")
            if device is None:
                device=selected_device if backend==selected_backend else "cpu"
            if device not in ("cpu","cuda"):
                raise ValueError("Select CPU or CUDA for inference.")
            if backend=="numpy" and device!="cpu":
                raise ValueError("The NumPy reference backend supports CPU only.")
        except Exception as exc:
            self._error("Cannot load model",str(exc))
            return False
        selected_index=self._backend_index(backend,device)
        if selected_index>=0:
            self.backend_combo.setCurrentIndex(selected_index)
        def work(progress,should_stop):
            from .runtime import CloudRuntime
            options={"onnx_threads":min(4,os.cpu_count() or 1)} if backend=="onnx" else {}
            return CloudRuntime(source,backend=backend,device=device,**options)
        def done(runtime):
            self._runtime=runtime
            self._model_path=source
            self._loaded_kind=kind
            self._loaded_backend=getattr(runtime,"backend",backend)
            self._loaded_device=str(getattr(runtime,"device",device))
            self._suggestions=None
            self._live_frame=None
            self._capture_prediction=None
            self.capture_preview.set_frame(self._last_frame)
            self.canvas.predictions=[]
            self.canvas.update()
            config=runtime.config.to_dict()
            synthetic=runtime.metadata.get("synthetic") is True
            fixture_note="\nSYNTHETIC TEST FIXTURE · not trained on game footage" if synthetic else ""
            display_name=f"{source.name} / model.json" if kind=="onnx" else source.name
            execution=self._execution_label()
            status=f"{display_name}\n{execution}\n{config.get('input_size')} px · {config.get('vertices')} vertices"+fixture_note
            self.model_status.setText(status)
            self.model_status.setStyleSheet("color: #ffc38b;" if synthetic else "")
            self.live_model_status.setText(status)
            self.live_model_status.setStyleSheet("color: #ffc38b;" if synthetic else "")
            for label in (self.model_status,self.live_model_status):
                label.setMinimumHeight(max(label.fontMetrics().lineSpacing()*(status.count("\n")+1),label.heightForWidth(max(200,label.width()))))
            self.live_status.setText(f"{display_name} · {execution}. Choose a screen area, then Start live detection.")
            self._show_result({"model":str(source),"backend":self._loaded_backend,"device":self._loaded_device,"config":config,"metadata":runtime.metadata})
            self.inference_status.setText("Model loaded. Choose a saved image or freeze a capture frame.")
            self.statusBar().showMessage("Loaded "+display_name+" · "+execution,12000)
            if open_live:
                self.tabs.setCurrentWidget(self.capture_tab)
            self._refresh_summary()
        return self._start_job("Load model",work,done)

    def _backend_index(self, backend, device):
        # Qt stores the Python tuple as a QVariant list; findData(tuple) is not
        # consistently comparable across PySide versions, so compare its values.
        for index in range(self.backend_combo.count()):
            value=self.backend_combo.itemData(index)
            if value is not None and tuple(value)==(backend,device):
                return index
        return -1

    def _execution_label(self):
        backend=self._loaded_backend or getattr(self._runtime,"backend","unknown")
        device=self._loaded_device or str(getattr(self._runtime,"device","cpu"))
        names={"numpy":"NumPy reference","torch":"PyTorch","onnx":"ONNX Runtime"}
        provider=getattr(getattr(self._runtime,"network",None),"provider",None)
        label=f"{names.get(backend,backend)} · {device.upper()}"
        if provider:
            label+=f" · {provider}"
        return label

    def _predict_image(self):
        if self._defer_until_live_stopped(self._predict_image,"Stopping live detection before inspecting the selected image…"):
            return
        if not self._need_image() or not self._need_model() or not self._ready_to_leave():
            return
        try:
            with Image.open(self.project.image_path(self.current_image_id)) as source:
                picture=source.convert("RGB")
        except Exception as exc:
            self._error("Cannot read image",str(exc))
            return
        self._predict(picture,("image",self.current_image_id))

    def _predict_capture(self):
        if self._live_session is not None:
            self._request_live_stop(freeze=True,after=self._predict_capture,reason="Freezing capture before image inspection…")
            return
        if not self._need_model():
            return
        if self._last_frame is None:
            self._error("Capture frame required","Start preview in Live capture and freeze a frame first.")
            return
        if not self._ready_to_leave():
            return
        self._stop_capture()
        self._predict(self._last_frame.copy(),("capture",self._frame_serial))

    def _predict(self, picture, target):
        runtime=self._runtime
        settings={"confidence":self.confidence_spin.value(),"nms_iou":self.nms_spin.value(),
                  "max_detections":self.maximum_detections.value(),"min_height":self.minimum_height.value(),"debug":True}
        def work(progress,should_stop):
            return runtime.predict(picture,**settings)
        def done(result):
            detections=result.get("detections",[])
            if target[0]=="image" and target[1]==self.current_image_id:
                self.canvas.predictions=deepcopy(detections)
                self.canvas.diagnostic_boxes=self.diagnostic_check.isChecked()
                self.canvas.update()
                self._suggestions={"target":target,"detections":deepcopy(detections),"image":picture}
                self.tabs.setCurrentWidget(self.annotate_tab)
            elif target[0]=="capture" and target[1]==self._frame_serial:
                self.capture_preview.diagnostic=self.diagnostic_check.isChecked()
                self.capture_preview.set_frame(picture,detections,result.get("diagnostics",{}).get("primary_detections",[]))
                self._capture_prediction={"serial":target[1],"result":result}
                self._suggestions={"target":target,"detections":deepcopy(detections),"image":picture}
                self.tabs.setCurrentWidget(self.capture_tab)
            public=deepcopy(result)
            public.pop("diagnostics",None)
            for detection in public.get("detections",[]):
                detection.pop("primary_box",None)
            self._show_result(public)
            total=result.get("timing_ms",{}).get("total")
            total_text=f" · {total:.1f} ms end to end" if isinstance(total,(int,float)) else ""
            message=f"{len(detections)} detection(s){total_text} · {result.get('invalid_contours',0)} invalid contour(s) suppressed."
            self.inference_status.setText(message)
            self.statusBar().showMessage(message+" Add suggestions from Inspect & export if you want to label them.",15000)
            self._refresh_summary()
        self.inference_status.setText("Running the detector and its box-guided encasing head…")
        self._start_job("Inference",work,done)

    def _diagnostic_changed(self, enabled):
        self.canvas.diagnostic_boxes=enabled
        self.canvas.update()
        self.capture_preview.diagnostic=enabled
        self.capture_preview.update()
        if self._overlay is not None and self._overlay.enabled and self._live_frame is not None and not self._live_stopping:
            self._overlay.show_frame(self._live_frame,diagnostic=enabled)

    def _add_suggestions(self):
        if self._live_session is not None:
            self._request_live_stop(freeze=True,after=self._add_suggestions,reason="Freezing the latest suggestions before adding labels…")
            return
        if not self._need_project() or not self._suggestions or not self._ready_to_leave():
            return
        suggestions=self._suggestions
        target=suggestions["target"]
        if not suggestions["detections"]:
            self._error("No suggestions","The model returned no valid detections. A no-detection result does not automatically mark an image as negative.")
            return
        if target[0]=="capture":
            try:
                record=self.project.add_pil(suggestions["image"],name="model-capture-"+datetime.now().strftime("%Y%m%d-%H%M%S"),
                                            split=self.capture_split.currentData(),group_id=self._capture_group)
                self._refresh_images()
                self._select_image_id(record["id"])
            except Exception as exc:
                self._error("Could not save capture",str(exc))
                return
        elif target[1]!=self.current_image_id:
            self._error("Image changed","Run detection again on the selected image before adding suggestions.")
            return
        if self.current_image_id is None:
            return
        width,height=self.canvas.image_size
        additions=[]
        errors=[]
        for detection in suggestions["detections"]:
            if "primary_box" not in detection:
                errors.append("Model output did not include its diagnostic guide box.")
                continue
            ann={"id":uuid4().hex,"class_id":0,"box":deepcopy(detection["primary_box"]),
                 "wrapper":deepcopy(detection.get("wrapper") or []),"source":"model",
                 "model":self._model_path.name if self._model_path else "unknown",
                 "confidence":float(detection.get("confidence",0.))}
            ann = replace_encasing(ann, detection)
            issues=validate_annotation(ann,width,height,require_complete=True)
            if issues:
                errors.extend(issues)
            else:
                additions.append(ann)
        if additions:
            self.canvas.replace_annotations(self.canvas.annotations+additions)
            self.canvas.predictions=[]
            self.canvas.update()
            self._suggestions=None
            self.tabs.setCurrentWidget(self.annotate_tab)
            self._refresh_summary()
            self._set_canvas_hint(f"Added {len(additions)} model suggestion(s) as unreviewed. Check both labels; use Undo to remove this batch.")
        if errors:
            self._error("Some suggestions were rejected","Invalid pairs were not added. Their geometry was not replaced or repaired automatically.","\n".join(errors))

    def _evaluate(self):
        if self._defer_until_live_stopped(self._evaluate,"Stopping live detection before evaluation…"):
            return
        if not self._need_project() or not self._need_model() or not self._ready_to_leave():
            return
        if self._loaded_kind=="onnx" or self._loaded_backend=="onnx":
            self._error("Original .cloud model required","This ONNX pair supports image and live detection. Load its original .cloud file for dataset evaluation.")
            return
        split=self.eval_split.currentData()
        project_path=str(self.project.root)
        model_path=str(self._model_path)
        confidence=self.confidence_spin.value()
        # The evaluation API uses the PyTorch research backend, independently of
        # the NumPy preview choice. Explicit CPU is the reproducible default here.
        device="cuda" if (self._loaded_device or "cpu").startswith("cuda") else "cpu"
        def work(progress,should_stop):
            from .evaluation import evaluate
            return evaluate(project_path,model_path,split=split,device=device,confidence=confidence)
        def done(result):
            self._show_result(result)
            self.inference_status.setText(f"Evaluation complete on reviewed {split} images. Metrics and measured timing are below.")
        self._start_job("Evaluation",work,done)

    def _export_onnx(self):
        if self._defer_until_live_stopped(self._export_onnx,"Stopping live detection before export…"):
            return
        if not self._need_model():
            return
        if self._loaded_kind=="onnx" or self._loaded_backend=="onnx":
            self._error("Model already in ONNX form","Load the original .cloud model if you want to export a new ONNX pair.")
            return
        folder=QFileDialog.getExistingDirectory(self,"Choose output folder for the two ONNX graphs")
        if not folder:
            return
        path=str(self._model_path)
        def work(progress,should_stop):
            from .export import export_onnx
            return export_onnx(path,folder)
        def done(result):
            self._show_result(result)
            self.inference_status.setText("Export complete: detector, guided-wrapper graph and matching metadata.")
        self._start_job("ONNX export",work,done)

    def _show_result(self, result):
        self._last_result=deepcopy(result)
        self.result_text.setPlainText(json.dumps(result,indent=2,ensure_ascii=False,default=str))

    def _save_result(self):
        if self._last_result is None:
            self._error("No result yet","Run inference or evaluation first.")
            return
        suggested=str(self.project.root/"result.json") if self.project else "result.json"
        path,_=QFileDialog.getSaveFileName(self,"Save result JSON",suggested,"JSON (*.json)")
        if path:
            try:
                target=Path(path)
                temporary=target.with_name(target.name+".tmp-"+uuid4().hex[:8])
                temporary.write_text(json.dumps(self._last_result,indent=2,ensure_ascii=False,default=str)+"\n",encoding="utf-8")
                temporary.replace(target)
                self.statusBar().showMessage("Saved result: "+str(target),10000)
            except Exception as exc:
                self._error("Could not save result",str(exc))

    def closeEvent(self, event):
        if self._live_session is not None:
            self._request_live_stop(after=self.close,reason="Stopping live detection before closing…")
            event.ignore()
            return
        if self._job is not None:
            name=self._job["name"]
            if self._job["cancellable"]:
                choice=QMessageBox.question(self,"Work is running",f"Request a safe stop of {name.lower()} and close when it finishes? Saved dataset edits and completed checkpoints will be kept.")
                if choice==QMessageBox.StandardButton.Yes:
                    self._close_when_idle=True
                    self._cancel_job()
            else:
                choice=QMessageBox.question(self,"Work is running",f"{name} is still running. Close automatically after it finishes?")
                if choice==QMessageBox.StandardButton.Yes:
                    self._close_when_idle=True
            event.ignore()
            return
        if self._assist_busy:
            self._assist_close_when_idle = True
            self._cancel_assist()
            event.ignore()
            return
        if not self._ready_to_leave():
            event.ignore()
            return
        if self._assist_thread is not None and self._assist_thread.isRunning():
            self._assist_shutdown_close = True
            self._assist_thread.request_shutdown()
            self.statusBar().showMessage("Closing the outline assistant safely…")
            event.ignore()
            return
        self._stop_capture()
        if self._overlay is not None:
            self._overlay.close()
        if self._capture is not None:
            try:
                self._capture.close()
            except Exception:
                pass
            self._capture=None
        event.accept()


from .workflow import ReviewWorkflowMixin


class MainWindow(ReviewWorkflowMixin, StudioBaseWindow):
    """Complete desktop app, including imported-box and rapid review workflows."""


def run_gui(project_path=None,model_path=None,backend=None,device="cpu",live_tab=False):
    app=QApplication.instance()
    if app is None:
        app=QApplication(sys.argv)
    app.setApplicationName("Cloud Studio")
    app.setOrganizationName("Cloud Studio Research")
    app.setFont(QFont("Segoe UI",10))
    window=MainWindow(project_path,model_path=model_path,backend=backend,device=device,live_tab=live_tab)
    window.show()
    # Keep the window alive for embedded Qt callers as well as the normal entry.
    app._cloud_studio_window=window
    return app.exec()
