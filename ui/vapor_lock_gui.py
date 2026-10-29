"""Qt control center for the native Vapor Lock engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from copy import deepcopy
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
from statistics import median
from functools import wraps
from vlui_native_injection import native_injection, require_native_effect, writable_native_path
from vlui_native_typography import scaled_native_stylesheet
from batch1_controls import TuningLock, PreciseSpinBox, WrappedStatusLabel, tuning_guard
from controller_thresholds import validate_thresholds
from batch3_settings import FIELDS as BATCH3_FIELDS, validate as validate_batch3, spin as batch3_spin
from guide_links import add_guide_button, guide_topic
from vlui_live_context import validated_native_settings
from vlui_c014_settings import CAPTURE_FIELDS, validated_capture_field, validated_capture_settings
from annotation_provider import BACKENDS as ANNOTATION_BACKENDS
_NATIVE_INJECTION = native_injection()
from dataset_storage import atomic_text, dataset_lock, recover_annotations


def disposable_deny(capability):
    def decorate(method):
        @wraps(method)
        def guarded(self, *args, **kwargs):
            if _NATIVE_INJECTION is not None:
                self.log('INFO', 'This operation is unavailable in the verification session.')
                return None
            return method(self, *args, **kwargs)
        return guarded
    return decorate


def dataset_edit(method):
    @wraps(method)
    def edit(self, *args, **kwargs):
        if _NATIVE_INJECTION is not None:
            self.log('INFO', 'This operation is unavailable in the verification session.')
            return None
        try:
            with dataset_lock(self.dataset_root_for_tools()):
                recover_annotations(self.dataset_root_for_tools())
                return method(self, *args, **kwargs)
        except (OSError, ValueError) as error:
            self.log("WARN", str(error))
            return None
    return edit

from PySide6.QtCore import QEvent, QObject, QPointF, QProcess, QProcessEnvironment, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QFontDatabase, QGuiApplication, QIcon, QImage, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QKeySequenceEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from motion_compensation import (
    MotionCompensationPreset,
    find_motion_preset,
    load_motion_compensation,
    save_motion_compensation,
    valid_motion_identifier,
)
from local_control_api import DEFAULT_PORT, LocalControlApi, regenerate_token
from model_discovery import discover_local_models
from settings_guide import guide_path
from interface_themes import THEMES, theme_for, theme_stylesheet
from engine_stream import EngineLineReader, OverlayFrameGate, PreviewFrameGate, native_observation_age_ms, native_tick_ms
from engine_capabilities import EngineCapabilities
from session_diagnostics import SessionReportCache
from tuning_tools import (ClassPolicyEditor, ControllerTester, PerformanceHistory, PerformanceChart,
                          InferenceMeasurement, output_diagnostics, validated_policies, policies_wire)
from localization import (
    InterfaceTranslator,
    LANGUAGE_OPTIONS,
    format_text,
    load_preferred_language,
    normalize_language,
    save_preferred_language,
    translate_text,
    apply_qt_language, DialogLocalizer,
)

try:
    from product_build import PRODUCT_VERSION as BUILD_PRODUCT_VERSION
except ImportError:
    BUILD_PRODUCT_VERSION = ""
# Internal protocol label retained for compatibility; there are no paid editions.
BUILD_EDITION = "premium"


# A portable PyInstaller build keeps the executable at the product root. In
# development, source-relative paths keep the workspace layout convenient.
COMPONENT_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
# Public releases keep launchable support executables out of the product root.
# The frozen control center lives in ``app`` while profiles, engines, and user
# assets remain one level above it.
ROOT = COMPONENT_ROOT.parent if getattr(sys, "frozen", False) and (COMPONENT_ROOT.parent / "engine").is_dir() else COMPONENT_ROOT
if (COMPONENT_ROOT.parent / 'information/alpha-manifest.json').is_file():
    ROOT = COMPONENT_ROOT.parent
_READ_ONLY_PRODUCT_ROOT = ROOT
if _NATIVE_INJECTION is not None:
    ROOT = _NATIVE_INJECTION.paths.directory('product')
PROFILE_DIRECTORY = (_NATIVE_INJECTION.paths.directory('profiles') if _NATIVE_INJECTION is not None else ROOT / "profiles")
INITIALIZATION_DIRECTORY = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Vapor Lock"
EDITION = "premium"
IS_FREE_EDITION = False  # Compatibility export for older extension modules.
INITIALIZATION_STATE_FILE = INITIALIZATION_DIRECTORY / f"initialization-0.1-{EDITION}.json"
if _NATIVE_INJECTION is not None:
    INITIALIZATION_DIRECTORY = _NATIVE_INJECTION.paths.directory('preferences')
    INITIALIZATION_STATE_FILE = _NATIVE_INJECTION.paths.initialization_file
CONTROLLER_OUTPUT_CONSENT = (
    "Xbox-compatible controller output is off until you explicitly enable it. It creates a separate local controller, "
    "and Windows requests administrator approval only when you choose to install or start that component."
)


def product_icon_path() -> Path:
    """Find the brand icon in source and in the PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", COMPONENT_ROOT)) / "assets" / "VaporLock.ico"
        if bundled.is_file():
            return bundled
    return ROOT / "assets" / "VaporLock.ico"


def product_background_path() -> Path:
    """Find the authored vapor background in source and packaged builds."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", COMPONENT_ROOT)) / "assets" / "Background.png"
        if bundled.is_file():
            return bundled
    return ROOT / "Background.png"


def product_version() -> str:
    """Use the packaged version, with a source fallback for development."""
    if str(BUILD_PRODUCT_VERSION).strip():
        return str(BUILD_PRODUCT_VERSION).strip()
    try:
        version_file=_READ_ONLY_PRODUCT_ROOT/'information/product_version.txt'
        if not version_file.is_file():version_file=_READ_ONLY_PRODUCT_ROOT/'product_version.txt'
        release = version_file.read_text(encoding='utf-8').strip()
        if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?', release):
            return release
        raise ValueError('Invalid release label')
    except FileNotFoundError:
        pass
    try:
        match = re.search(
            r"project\s*\(\s*VaporLock\s+VERSION\s+([0-9][0-9A-Za-z._-]*)",
            (_READ_ONLY_PRODUCT_ROOT / "CMakeLists.txt").read_text(encoding="utf-8"),
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
    except OSError:
        pass
    return "0.0.5-dev"


PRODUCT_VERSION = product_version()


def resolve_product_path(value: str) -> Path:
    """Resolve profile-relative assets from the current product root."""
    candidate = Path(value).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    return writable_native_path(resolved)


def resolve_dataset_root(value: str) -> Path:
    return resolve_product_path(value or 'datasets')


def profile_path(value: str) -> str:
    """Keep assets shipped inside the product folder portable between PCs."""
    if not value:
        return ""
    candidate = resolve_product_path(value)
    try:
        return str(candidate.relative_to(ROOT.resolve()))
    except ValueError:
        return str(candidate)


def initialization_complete() -> bool:
    try:
        state = json.loads(INITIALIZATION_STATE_FILE.read_text(encoding="utf-8"))
        return (
            state.get("schema_version") == 1
            and state.get("product_generation") == "0.1"
            and state.get("activation_binding_confirmed") is True
        )
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def save_initialization_state() -> None:
    INITIALIZATION_DIRECTORY.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": 1,
        "product_generation": "0.1",
        "activation_binding_confirmed": True,
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    temporary = INITIALIZATION_STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    temporary.replace(INITIALIZATION_STATE_FILE)

APP_STYLESHEET = """
* { font-family:"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; color:#edf6ff; font-size:10pt; }
QMainWindow, QWidget#root { background:transparent; }
QFrame#workspaceShell { background:transparent; }
QWidget#workspaceBackdrop, QStackedWidget { background:transparent; }
QScrollArea, QScrollArea#pageScroll, QScrollArea#pageScroll::viewport, QWidget#pageContent, QFrame#pageColumn { background:transparent; border:0; }
QFrame#commandBar { background:rgba(6, 10, 17, .88); border-bottom:1px solid rgba(125, 188, 245, .29); }
QFrame#navigationDeck { background:rgba(5, 9, 16, .76); border-right:1px solid rgba(125, 188, 245, .25); }
QScrollArea#navigationScroll, QScrollArea#navigationScroll::viewport, QWidget#navigationContent { background:transparent; }
QLabel#brandMark { background:#12233c; border:1px solid #7dbcf5; border-radius:5px; color:#b9e7ff; font-size:9pt; font-weight:800; }
QLabel#brandWordmark { color:#f4f8ff; font-size:11pt; font-weight:800; letter-spacing:0; }
QLabel#brandPath, QLabel#headerKicker, QLabel#headerProfile { color:#8b9bb2; font-size:8pt; font-weight:700; }
QLabel#headerPage { color:#edf6ff; font-size:12pt; font-weight:700; }
QFrame#headerDivider, QFrame#navDivider { background:rgba(125, 188, 245, .26); max-width:1px; min-width:1px; }
QLabel#navGroup { color:#7dbcf5; font-size:8pt; font-weight:800; letter-spacing:1px; padding:12px 12px 4px; }
QPushButton#navButton { background:transparent; border:0; border-left:3px solid transparent; border-radius:0; color:#a7b5c9; font-size:9pt; font-weight:650; min-height:36px; padding:5px 12px; text-align:left; }
QPushButton#navButton:hover { background:rgba(31, 91, 184, .16); color:#edf6ff; }
QPushButton#navButton:checked { background:rgba(31, 91, 184, .25); border-left-color:#8bc8ff; color:#edf6ff; }
QLabel#statusPill { background:rgba(31, 91, 184, .21); border:1px solid rgba(125, 188, 245, .55); border-radius:5px; color:#b9e7ff; font-size:8pt; font-weight:800; padding:6px 10px; }
QLabel#statusPill[state="running"] { background:rgba(75, 70, 170, .28); border-color:#a481f1; color:#e6dbff; }
QLabel#statusPill[state="stopping"] { background:rgba(102, 80, 33, .33); border-color:#f5d75b; color:#fff0a8; }
QLabel#statusPill[state="error"] { background:rgba(130, 45, 91, .31); border-color:#e887c5; color:#ffe7f5; }
QFrame#pageHeading { background:transparent; border-bottom:1px solid rgba(125, 188, 245, .13); }
QLabel#pageKicker, QLabel#sectionLabel { color:#7dbcf5; font-size:8pt; font-weight:800; letter-spacing:1px; }
QLabel#pageTitle { color:#f5f8ff; font-size:22pt; font-weight:750; }
QFrame#sectionHeader { background:transparent; }
QFrame#sectionDot { background:#e887c5; border-radius:1px; max-width:3px; min-width:3px; max-height:12px; min-height:12px; }
QFrame#sectionRule { background:rgba(125, 188, 245, .14); max-height:1px; min-height:1px; }
QFrame#settingsCard { background:rgba(9, 15, 26, .60); border:1px solid rgba(125, 188, 245, .17); border-radius:4px; }
QFrame#settingsCard:hover { background:rgba(15, 25, 42, .72); border-color:rgba(125, 188, 245, .35); }
QFrame#settingAccent { background:#4f86de; border-radius:1px; max-width:2px; min-width:2px; }
QFrame#metricCard { background:rgba(7, 13, 23, .62); border:1px solid rgba(125, 188, 245, .18); border-radius:4px; }
QFrame#metricCard:hover { background:rgba(15, 25, 42, .73); border-color:rgba(185, 231, 255, .37); }
QFrame#signalPanel { background:rgba(8, 14, 25, .63); border:1px solid rgba(125, 188, 245, .20); border-radius:4px; }
QFrame#signalRule, QFrame#signalDivider { background:rgba(125, 188, 245, .24); }
QFrame#signalRule { max-height:1px; min-height:1px; }
QFrame#signalDivider { max-width:1px; min-width:1px; }
QLabel#brand { color:#f5f8ff; font-size:14pt; font-weight:750; }
QLabel#brandKicker { color:#7dbcf5; font-size:8pt; font-weight:800; }
QLabel#editionBadge { color:#e6dbff; background:rgba(91, 61, 160, .32); border:1px solid rgba(167, 129, 241, .65); border-radius:4px; font-size:8pt; font-weight:800; padding:3px 7px; }
QLabel#muted, QLabel#detail, QLabel#fieldDescription { color:#98a7bd; }
QLabel#fieldTitle { color:#edf6ff; font-weight:680; }
QLabel#metricValue { color:#eaf5ff; font-size:18pt; font-weight:750; }
QLabel#metricCaption, QLabel#signalCaption, QLabel#hudLabel { color:#8b9bb2; font-size:8pt; font-weight:800; letter-spacing:.5px; }
QLabel#signalValue, QLabel#hudValue { color:#c9e8ff; font-size:10pt; font-weight:700; }
QFrame#telemetryStrip { background:rgba(5, 9, 16, .84); border-top:1px solid rgba(125, 188, 245, .21); }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit { background:rgba(4, 7, 13, .72); border:1px solid rgba(125, 188, 245, .32); border-radius:5px; color:#edf6ff; min-height:32px; padding:3px 9px; selection-background-color:#365fba; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus { border:1px solid #8bc8ff; }
QComboBox::drop-down { border:0; width:24px; }
QComboBox QAbstractItemView { background:#111a2a; border:1px solid #4d79bc; selection-background-color:#274e99; }
QPushButton, QToolButton { background:#152238; border:1px solid rgba(125, 188, 245, .33); border-radius:5px; color:#eaf4ff; font-weight:650; min-height:32px; padding:4px 12px; }
QToolButton { min-width:32px; max-width:32px; padding:0; }
QPushButton:hover, QToolButton:hover { background:#203756; border-color:#8bc8ff; }
QPushButton#primaryButton { background:#85c7f7; border-color:#b9e7ff; color:#071220; }
QPushButton#primaryButton:hover { background:#b9e7ff; border-color:#e7f7ff; }
QPushButton#dangerButton { background:rgba(139, 49, 99, .78); border-color:#e887c5; color:#fff0f9; }
QPushButton#dangerButton:hover { background:#b44a8a; }
QPushButton:disabled, QToolButton:disabled { background:#111724; border-color:#202c3e; color:#65758b; }
QSlider::groove:horizontal { height:8px; border-radius:3px; background:#263a52; }
QSlider::sub-page:horizontal { background:#7dbcf5; border-radius:3px; }
QSlider::handle:horizontal { background:transparent; border:0; width:1px; height:8px; margin:0; }
QCheckBox { spacing:8px; color:#dfeeff; }
QCheckBox::indicator { width:32px; height:17px; border:1px solid #587699; border-radius:8px; background:#1a2637; }
QCheckBox::indicator:checked { background:#365fba; border-color:#9bd1ff; }
QProgressBar { background:#080d16; border:1px solid rgba(125, 188, 245, .33); border-radius:5px; color:#e5f2ff; min-height:21px; text-align:center; }
QProgressBar::chunk { background:#557fd2; border-radius:4px; }
QTableWidget { background:rgba(10, 16, 28, .89); alternate-background-color:rgba(18, 30, 50, .8); border:1px solid rgba(125, 188, 245, .27); gridline-color:rgba(125, 188, 245, .19); selection-background-color:#274e99; }
QListWidget, QTreeWidget { background:rgba(7, 13, 23, .82); alternate-background-color:rgba(20, 30, 48, .82); border:1px solid rgba(125, 188, 245, .30); border-radius:7px; color:#dcecff; outline:0; }
QListWidget::item, QTreeWidget::item { min-height:28px; padding:4px 8px; border-bottom:1px solid rgba(125, 188, 245, .10); }
QListWidget::item:hover, QTreeWidget::item:hover { background:rgba(31, 91, 184, .22); }
QListWidget::item:selected, QTreeWidget::item:selected { background:rgba(85, 61, 164, .36); color:#f6edff; }
QHeaderView::section { background:#17243a; border:0; border-right:1px solid rgba(125, 188, 245, .22); border-bottom:1px solid rgba(125, 188, 245, .22); color:#dceeff; font-weight:650; padding:7px; }
QFrame#settingsTabs { background:rgba(9, 14, 24, .64); border:1px solid rgba(125, 188, 245, .20); border-radius:4px; }
QFrame#settingsTabs QFrame#settingsCard { background:transparent; border:0; border-bottom:1px solid rgba(125, 188, 245, .16); border-radius:0; }
QFrame#settingsTabs QFrame#settingsCard:hover { background:rgba(31, 91, 184, .11); }
QFrame#settingsTabs QFrame#settingAccent { background:transparent; }
QFrame#secondaryTabRail { background:rgba(5, 9, 16, .48); border-bottom:1px solid rgba(125, 188, 245, .18); min-height:47px; }
QPushButton#secondaryTabButton { background:transparent; border:0; border-bottom:2px solid transparent; border-radius:0; color:#9caec4; font-size:9pt; font-weight:650; min-height:45px; padding:5px 14px; text-align:center; }
QPushButton#secondaryTabButton:hover { background:rgba(31, 91, 184, .18); color:#eaf5ff; }
QPushButton#secondaryTabButton:checked { background:rgba(85, 61, 164, .20); border-bottom-color:#a481f1; color:#f4efff; }
QFrame#secondaryTabDivider { background:rgba(125, 188, 245, .23); max-height:1px; min-height:1px; }
QScrollArea#settingsTabScroll, QScrollArea#settingsTabScroll::viewport, QWidget#settingsTabContent, QScrollArea#secondaryTabScroll, QScrollArea#secondaryTabScroll::viewport { background:transparent; }
QScrollBar:vertical { background:#080c14; width:10px; margin:5px 0; }
QScrollBar::handle:vertical { background:#334d6d; min-height:28px; border-radius:5px; }
QScrollBar::handle:vertical:hover { background:#638cc2; }
QScrollBar:horizontal { background:#080c14; height:9px; }
QScrollBar::handle:horizontal { background:#334d6d; min-width:28px; border-radius:4px; }
QLabel#previewSurface { background:#070a11; border:1px solid rgba(185, 231, 255, .24); border-radius:4px; }
QLabel#policyPreviewSurface { background:#05080e; border:0; border-radius:6px; color:#9db4cf; }
QWidget#datasetReviewCanvas { background:#060b13; border:1px solid rgba(185, 231, 255, .28); border-radius:4px; }
QFrame#policyCommandDeck { background:rgba(5, 10, 18, .67); border:1px solid rgba(125, 188, 245, .22); border-radius:5px; }
QFrame#policyMainSurface { background:transparent; border-right:1px solid rgba(125, 188, 245, .22); }
QFrame#policyInspector { background:rgba(5, 10, 18, .57); border:0; }
QFrame#policyInspectorCard { background:transparent; border:0; border-bottom:1px solid rgba(125, 188, 245, .16); border-radius:0; }
QFrame#policyPreviewFooter { background:rgba(5, 10, 18, .86); border-top:1px solid rgba(125, 188, 245, .25); }
QLabel#policyPreviewCaption { color:#9eafc5; font-size:8pt; }
QLabel#policyPreviewStatus { color:#b9e7ff; font-size:8pt; font-weight:750; }
QLabel#policyInspectorTitle { color:#eef6ff; font-size:10pt; font-weight:750; }
QLabel#policyWorkspaceTitle { color:#f3f7ff; font-size:17pt; font-weight:750; }
QLabel#policyWorkspaceSubtitle { color:#98a7bd; font-size:9pt; }
QLabel#policyCardTitle { color:#edf6ff; font-size:9pt; font-weight:750; }
QLabel#policyControlLabel { color:#a5b5c9; font-size:8pt; }
QLabel#policyControlValue { color:#c9e8ff; font-size:9pt; font-weight:750; }
QLabel#policyMetricCaption { color:#7dbcf5; font-size:8pt; font-weight:800; letter-spacing:1px; }
QLabel#policyMetricValue { color:#dbefff; font-size:14pt; font-weight:750; }
QLabel#policyTrackMetric { color:#f2f7ff; font-size:14pt; font-weight:750; }
QLabel#policyConfidenceMetric { color:#ccecff; font-size:14pt; font-weight:750; }
QLabel#policyMetricDetail { color:#899bb2; font-size:8pt; }
QFrame#policyTopMetric { background:rgba(13, 21, 34, .65); border-left:2px solid #7dbcf5; }
QFrame#policyTopMetric[accent="pink"] { border-left-color:#e887c5; }
QFrame#policyQuickRow { background:transparent; }
QPushButton#policyReset { min-height:28px; padding:3px 10px; }
QToolTip { background:#0b1220; color:#edf6ff; border:1px solid #658ec5; padding:7px; }
"""


UI_APPEARANCE_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Vapor Lock" / "ui-appearance.json"
if _NATIVE_INJECTION is not None:
    UI_APPEARANCE_FILE = _NATIVE_INJECTION.paths.appearance_file
UI_COLOR_ROLES = (
    ("steel", "Secondary text", "#b4bfcd"),
    ("cobalt", "Selection & active controls", "#365fba"),
    ("ice", "Primary text", "#edf6ff"),
    ("baby", "Headings & focus", "#7dbcf5"),
    ("ultraviolet", "Live status & progress", "#a481f1"),
    ("carbon", "Panels & workspace", "#080b12"),
    ("pink", "Danger & section accent", "#e887c5"),
    ("highlight", "Attention highlight", "#f5d75b"),
    ("border", "Control borders", "#708498"),
    ("button_text", "Primary button text", "#05070a"),
)
UI_COLOR_ROLE_DETAILS = {
    "border": "Control outlines, dividers, and scroll indicators, independent of text color.",
    "button_text": "Text on primary action buttons, independent of the panel color.",
    "steel": "Descriptions and secondary labels, independent of control borders.",
    "cobalt": "Selected navigation, active controls, setting accents, and selection backgrounds.",
    "ice": "Primary copy, values, control text, and high-legibility foreground content.",
    "baby": "Headings, keyboard focus, emphasized borders, and primary action surfaces.",
    "ultraviolet": "Running-state indicators, progress fills, and active secondary tabs.",
    "carbon": "Workspace film, cards, fields, menus, and the main panel foundation.",
    "pink": "Destructive actions, section markers, and critical visual accents.",
    "highlight": "Attention states, stopping status, and alternating control emphasis.",
}
DEFAULT_UI_COLORS = {name: value for name, _label, value in UI_COLOR_ROLES}
DEFAULT_UI_OPACITIES = {name: 100 for name, _label, _value in UI_COLOR_ROLES}


@dataclass
class UiAppearance:
    layout: str = "mission-control"
    colors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_UI_COLORS))
    opacities: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_UI_OPACITIES))
    custom_palettes: dict[str, dict] = field(default_factory=dict)


def appearance_palette(appearance: UiAppearance, custom_palette: bool) -> dict[str, str]:
    preset = theme_for(appearance.layout).palette()
    # Directly-created/default appearances also follow the selected preset.
    if not custom_palette or appearance.colors == DEFAULT_UI_COLORS:
        return preset
    return {name: normalized_ui_color(appearance.colors.get(name), fallback) for name, fallback in preset.items()}


def normalized_ui_color(value: object, fallback: str) -> str:
    color = QColor(str(value))
    return color.name(QColor.NameFormat.HexRgb) if color.isValid() else fallback


def normalized_ui_opacity(value: object, fallback: int = 100) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return fallback


def load_ui_appearance(path: Path = UI_APPEARANCE_FILE) -> UiAppearance:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("appearance payload")
        schema_version = int(payload.get("schema_version", 1))
        layout = str(payload.get("layout", "smoky"))
        if layout not in THEMES:
            layout = "smoky"
        raw_colors = payload.get("colors", {})
        if not isinstance(raw_colors, dict):
            raw_colors = {}
        colors = {
            name: normalized_ui_color(raw_colors.get(name), fallback)
            for name, fallback in DEFAULT_UI_COLORS.items()
        }
        raw_opacities = payload.get("opacities", {})
        if not isinstance(raw_opacities, dict):
            raw_opacities = {}
        opacities = {
            name: normalized_ui_opacity(raw_opacities.get(name), fallback)
            for name, fallback in DEFAULT_UI_OPACITIES.items()
        }
        raw_presets = payload.get("custom_palettes", {})
        presets = {key: value for key, value in raw_presets.items() if key in THEMES and isinstance(value, dict)} if isinstance(raw_presets, dict) else {}
        appearance = UiAppearance("smoky" if schema_version < 2 else layout, colors, opacities, presets)
        legacy_defaults = {name: value for name, value in DEFAULT_UI_COLORS.items() if name not in {"border", "button_text"}}
        legacy_defaults["steel"] = "#4a6f9f"
        if schema_version < 3 and raw_colors == legacy_defaults:
            appearance.colors = theme_for(appearance.layout).palette()
        if schema_version < 3:
            save_ui_appearance(appearance, path)
        return appearance
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return UiAppearance()


def save_ui_appearance(appearance: UiAppearance, path: Path = UI_APPEARANCE_FILE) -> bool:
    try:
        path = writable_native_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = writable_native_path(path.with_suffix(".tmp"))
        temporary.write_text(json.dumps({
            "schema_version": 3,
            "layout": appearance.layout,
            "colors": appearance.colors,
            "opacities": appearance.opacities,
            "custom_palettes": appearance.custom_palettes,
        }, indent=2), encoding="utf-8")
        temporary.replace(path)
        return True
    except OSError:
        return False


def css_rgba(value: str, alpha: int) -> str:
    color = QColor(value)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {max(0, min(255, alpha))})"


def ui_stylesheet(appearance: UiAppearance, custom_palette: bool) -> str:
    colors = appearance_palette(appearance, custom_palette)
    opacities = appearance.opacities if custom_palette else DEFAULT_UI_OPACITIES
    normalized_colors = {
        name: normalized_ui_color(colors.get(name), fallback)
        for name, fallback in DEFAULT_UI_COLORS.items()
    }
    normalized_opacities = {
        name: normalized_ui_opacity(opacities.get(name), fallback)
        for name, fallback in DEFAULT_UI_OPACITIES.items()
    }

    def role(name: str, alpha: int = 255) -> str:
        return css_rgba(normalized_colors[name], round(alpha * normalized_opacities[name] / 100.0))

    theme = theme_for(appearance.layout)
    contrast = theme.high_contrast
    panel_alpha = 244 if contrast else 176
    card_alpha = 226 if contrast else 146
    field_alpha = 250 if contrast else 170
    border_alpha = 202 if contrast else 66
    border_width = 2 if contrast else 1
    return APP_STYLESHEET + f"""
* {{ color:{role('ice')}; }}
QDialog#officialModelDialog {{ background:{normalized_colors['carbon']}; }}
QDialog#officialModelDialog QTreeWidget {{ background:{role('carbon')}; alternate-background-color:{role('steel', 24)}; color:{role('ice')}; }}
QDialog#officialModelDialog QTreeWidget::item:selected {{ background:{role('ultraviolet', 95)}; color:{role('ice')}; }}
QDialog#officialModelDialog QHeaderView::section {{ background:{role('carbon')}; color:{role('baby')}; border-bottom:1px solid {role('steel', 100)}; }}
QFrame#commandBar {{ background:{role('carbon', panel_alpha)}; border-bottom:{border_width}px solid {role('baby', border_alpha)}; }}
QFrame#navigationDeck {{ background:{role('carbon', panel_alpha - 10)}; border-right:{border_width}px solid {role('steel', border_alpha)}; }}
QFrame#settingsCard, QFrame#metricCard, QFrame#signalPanel, QFrame#policyCommandDeck, QFrame#policyInspectorCard {{ background:{role('carbon', card_alpha)}; border:{border_width}px solid {role('steel', border_alpha)}; }}
QFrame#settingsTabs {{ background:{role('carbon', 228 if contrast else 138)}; border:{border_width}px solid {role('steel', border_alpha)}; }}
QFrame#settingsTabs QFrame#settingsCard {{ background:transparent; border:0; border-bottom:1px solid {role('steel', 105 if contrast else 47)}; border-radius:0; }}
QFrame#settingsTabs QFrame#settingsCard:hover {{ background:{role('cobalt', 30 if contrast else 18)}; }}
QFrame#settingsTabs QFrame#settingAccent {{ background:transparent; }}
QFrame#policyInspector {{ background:{role('carbon', 211 if contrast else 117)}; border:0; }}
QFrame#policyInspectorCard {{ background:transparent; border:0; border-bottom:1px solid {role('steel', 105 if contrast else 47)}; border-radius:0; }}
QFrame#policyTopMetric {{ background:{role('carbon', 214 if contrast else 130)}; border-left:2px solid {role('baby')}; }}
QFrame#policyTopMetric[accent="pink"] {{ border-left-color:{role('pink')}; }}
QFrame#secondaryTabRail {{ background:{role('carbon', 213 if contrast else 104)}; border-bottom:1px solid {role('steel', border_alpha)}; }}
QFrame#telemetryStrip, QFrame#policyPreviewFooter {{ background:{role('carbon', 223 if contrast else 135)}; border-top:1px solid {role('steel', border_alpha)}; }}
QFrame#pageHeading {{ border-bottom:1px solid {role('steel', border_alpha)}; }}
QFrame#sectionRule {{ background:{role('steel', 86 if contrast else 37)}; }}
QLabel#previewSurface, QLabel#policyPreviewSurface, QWidget#datasetReviewCanvas {{ background:{role('carbon', 250)}; border:{border_width}px solid {role('baby', border_alpha)}; color:{role('steel')}; }}
QFrame#settingsCard:hover, QFrame#metricCard:hover {{ background:{role('cobalt', 42 if contrast else 25)}; border-color:{role('baby', 177 if contrast else 92)}; }}
QFrame#missionTaskPanel {{ background:{role('carbon', 210)}; border:1px solid {role('steel', border_alpha)}; border-radius:5px; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ background:{role('carbon', field_alpha)}; border:{border_width}px solid {role('steel', border_alpha)}; color:{role('ice')}; selection-background-color:{role('cobalt')}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{ border-color:{role('baby')}; }}
QComboBox QAbstractItemView {{ background:{role('carbon')}; border:{border_width}px solid {role('steel')}; selection-background-color:{role('cobalt')}; color:{role('ice')}; }}
QPushButton, QToolButton {{ background:{role('steel', 67 if contrast else 36)}; border:{border_width}px solid {role('steel', border_alpha)}; color:{role('ice')}; }}
QPushButton:hover, QToolButton:hover {{ background:{role('cobalt', 112 if contrast else 70)}; border-color:{role('baby')}; }}
QPushButton#primaryButton {{ background:{role('baby')}; border-color:{role('ice')}; color:{normalized_colors['carbon']}; }}
QPushButton#dangerButton {{ background:{role('pink', 180 if contrast else 122)}; border-color:{role('pink', 208 if contrast else 130)}; color:{role('ice')}; }}
QPushButton#navButton {{ background:transparent; border:0; border-left:3px solid transparent; }}
QPushButton#navButton:hover {{ background:{role('cobalt', 51 if contrast else 30)}; }}
QPushButton#navButton:checked {{ background:{role('cobalt', 96 if contrast else 58)}; border-left:3px solid {role('baby')}; color:{role('ice')}; }}
QLabel#navGroup, QLabel#pageKicker, QLabel#sectionLabel, QLabel#brandKicker, QLabel#policyMetricCaption {{ color:{role('baby')}; }}
QLabel#brandWordmark, QLabel#headerPage, QLabel#pageTitle, QLabel#fieldTitle, QLabel#metricValue {{ color:{role('ice')}; }}
QLabel#muted, QLabel#detail, QLabel#fieldDescription, QLabel#headerProfile {{ color:{role('steel')}; }}
QFrame#sectionDot {{ background:{role('pink')}; }}
QFrame#settingAccent {{ background:{role('cobalt', 218 if contrast else 137)}; }}
QLabel#statusPill {{ background:{role('cobalt', 72)}; border-color:{role('baby')}; color:{role('ice')}; }}
QLabel#statusPill[state="running"] {{ background:{role('ultraviolet', 88)}; border-color:{role('ultraviolet')}; color:{role('ice')}; }}
QLabel#statusPill[state="stopping"] {{ background:{role('highlight', 62)}; border-color:{role('highlight')}; color:{role('ice')}; }}
QCheckBox::indicator:checked {{ background:{role('cobalt')}; border-color:{role('baby')}; }}
QProgressBar::chunk {{ background:{role('ultraviolet')}; }}
QSlider::groove:horizontal {{ height:{10 if contrast else 8}px; border-radius:3px; background:{role('steel', 92)}; }}
QSlider::handle:horizontal {{ background:transparent; border:0; width:1px; height:{10 if contrast else 8}px; margin:0; }}
QSlider[barAccent="baby"]::sub-page:horizontal {{ background:{role('baby')}; border-radius:3px; }}
QSlider[barAccent="pink"]::sub-page:horizontal {{ background:{role('pink')}; border-radius:3px; }}
QSlider[barAccent="ultraviolet"]::sub-page:horizontal {{ background:{role('ultraviolet')}; border-radius:3px; }}
QSlider[barAccent="highlight"]::sub-page:horizontal {{ background:{role('highlight')}; border-radius:3px; }}
QSlider[barAccent="cobalt"]::sub-page:horizontal {{ background:{role('cobalt')}; border-radius:3px; }}
QToolTip {{ background:{role('carbon')}; color:{role('ice')}; border:{border_width}px solid {role('baby')}; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background:{role('steel')}; }}
QPushButton#secondaryTabButton:checked {{ background:{role('ultraviolet', 52 if contrast else 33)}; border-bottom-color:{role('pink')}; color:{role('ice')}; }}
""" + theme_stylesheet(theme, normalized_colors, normalized_opacities)


@dataclass
class Settings:
    profile_name: str = "Default"
    model_path: str = ""
    cloud_project: str = ""
    cloud_output: str = ""
    cloud_run_name: str = "cloud-training"
    cloud_mode: str = "fresh"
    cloud_initial_model: str = ""
    cloud_checkpoint: str = ""
    cloud_epochs: int = 300
    cloud_batch: int = 8
    cloud_patience: int = 75
    cloud_device: str = "auto"
    cloud_annotation_device: str = "cpu"
    cloud_import_split: str = "train"
    provider: str = "auto"
    class_filter: str = ""
    model_class_aliases: list[dict] = field(default_factory=list)
    exclusion_zones: list[dict] = field(default_factory=list)
    confidence: float = 0.55
    nms_iou: float = 0.45
    nms_enabled: bool = True
    nms_class_agnostic: bool = False
    model_output_format: str = "auto"
    model_coordinate_units: str = "auto"
    max_detections: int = 300
    resize_mode: str = "letterbox"
    monitor: int = 1
    region_x: int = 0
    region_y: int = 0
    region_width: int = 640
    region_height: int = 640
    capture_fps: int = 30
    inference_fps: int = 30
    frame_skip: int = 0
    ort_intra_op_threads: int = 0
    inference_dml_device: int = -1
    inference_cuda_device: int = -1
    inference_trt_device: int = -1
    dataset_annotation_batch: int = 1
    ort_inter_op_threads: int = 0
    cuda_graph_replay: bool = True
    cuda_preprocess: bool = True
    match_iou: float = 0.30
    max_missed: int = 18
    track_retention_ms: int = -1
    smoothing: float = 0.35
    box_smoothing: float = 0.25
    anchor_smoothing: float = 0.65
    anchor_settle_radius: float = 2.0
    predict_lost: bool = True
    prediction_lead_ms: int = 50
    prediction_limit_ms: int = 500
    prediction_max_speed: float = 2000.0
    prediction_max_acceleration: float = 12000.0
    prediction_max_distance: float = 80.0
    prediction_noise_floor: float = 1.0
    strategy: int = 0
    acquisition_radius: int = 420
    near_radius: int = 180
    snap_radius: int = 48
    retention_radius: int = 540
    fov_shape: str = "circle"
    switching: float = 0.18
    lock_persistence_ms: int = 900
    lost_target_timeout: int = 12
    minimum_track_age: int = 2
    snap_bypasses_confirmation: bool = False
    near_candidate_wait_ms: int = 150
    lost_target_timeout_ms: int = -1
    anchor_mode: str = "center"
    anchor_x: float = 0.0
    anchor_y: float = 0.0
    watchlist: str = ""
    occlusion_handling: bool = True
    occlusion_grace_frames: int = 12
    occlusion_grace_ms: int = -1
    occlusion_reacquire_radius: int = 180
    class_policies: list[dict] = field(default_factory=list)
    movement_enabled: bool = False
    activation_mode: str = "hold"
    activation_key: str = "right"
    activation_source: str = "keyboard"
    controller_slot: int = 0
    controller_binding: str = "right_trigger"
    controller_trigger_press: int = 30
    controller_trigger_release: int = 30
    manual_target_lock_enabled: bool = False
    manual_lock_source: str = "keyboard"
    manual_lock_key: str = "middle"
    manual_lock_controller_binding: str = "left_shoulder"
    assistance_x: float = 1.0
    assistance_y: float = 1.0
    controller_output_deadzone: float = 0.0
    acquire_confidence: float = 0.0
    retain_confidence: float = 0.0
    gain: float = 0.18
    max_step: int = 24
    deadzone: int = 8
    movement_smoothing: float = 0.35
    output_rate: int = 120
    output_max_observation_age_ms: int = 150
    input_units_per_pixel_x: float = 1.0
    input_units_per_pixel_y: float = 1.0
    max_speed: float = 1440.0
    crosshair_enabled: bool = False
    crosshair_calibration: dict = field(default_factory=dict)
    aim_reference: str = "capture-center"
    aim_reference_x: float = 0.0
    aim_reference_y: float = 0.0
    # A named preset describes measured image drift in pixels per second.
    # The native engine applies the inverse correction only while output is active.
    motion_compensation_enabled: bool = False
    motion_compensation_activation_mode: str = "hold"
    motion_compensation_activation_key: str = "left"
    motion_compensation_mode: str = "preset"
    motion_compensation_library: str = "profiles/motion_compensation.txt"
    motion_compensation_preset: str = ""
    motion_compensation_vertical: float = 0.0
    motion_compensation_horizontal: float = 0.0
    motion_compensation_strength: float = 1.0
    motion_compensation_auto_min_tracks: int = 3
    motion_compensation_auto_consensus: float = 24.0
    motion_compensation_auto_smoothing: float = 0.75
    motion_compensation_auto_max_rate: float = 240.0
    event_action_enabled: bool = False
    event_action_requires_activation: bool = True
    event_action_mode: str = "click"
    event_action_button: str = "left"
    event_action_cooldown_ms: int = 1000
    output_transport: str = "mouse"
    virtual_mouse_opt_in: bool = False
    controller_output_opt_in: bool = False
    virtual_controller_mode: str = "slot0-proxy"
    virtual_controller_identity: str = "vapor-lock-xbox-one-windows"
    virtual_controller_stick: str = "right"
    virtual_controller_strength: float = 1.0
    virtual_controller_manual_priority: bool = True
    hardware_serial_port: str = ""
    hardware_serial_baud: int = 115200
    kmbox_net_address: str = ""
    kmbox_net_port: int = 0
    kmbox_net_device_code: str = ""
    kmbox_a_library_path: str = ""
    kmbox_a_vendor_id: int = 0x6688
    kmbox_a_product_id: int = 0x2021
    invert_x: bool = False
    invert_y: bool = False
    response_curve: str = "linear"
    preview_enabled: bool = True
    preview_fps: int = 5
    preview_quality: int = 80
    overlay_enabled: bool = True
    overlay_boxes: bool = True
    overlay_motion: bool = True
    overlay_fov: bool = True
    overlay_segmentation_masks: bool = False
    segmentation_mask_limit: int = 1
    segmentation_mask_threshold: float = 0.50
    desktop_overlay_enabled: bool = False
    desktop_overlay_opacity: int = 88
    desktop_overlay_labels: bool = True
    desktop_overlay_status: bool = True
    desktop_overlay_motion: bool = False
    desktop_overlay_visible: bool = True
    desktop_overlay_layout: str = "standard"
    desktop_overlay_scale: float = 1.0
    desktop_overlay_capture_bounds: bool = False
    desktop_overlay_palette: str = "vapor"
    desktop_overlay_track_color: str = "#7dbcf5"
    desktop_overlay_selected_color: str = "#e887c5"
    desktop_overlay_fov_color: str = "#a481f1"
    desktop_overlay_text_color: str = "#dfe9f2"
    hotkey_start_stop: str = "Ctrl+Alt+S"
    hotkey_toggle_overlay: str = "Ctrl+Alt+O"
    hotkey_emergency_stop: str = "Ctrl+Alt+X"
    hotkey_next_profile: str = "Ctrl+Alt+P"
    hotkey_toggle_target_lock: str = "Ctrl+Alt+L"
    hotkey_save_screenshot: str = "Ctrl+Alt+F12"
    hotkey_calibration: str = "Ctrl+Alt+C"
    hotkey_profile_1: str = ""
    hotkey_profile_1_target: str = ""
    hotkey_profile_2: str = ""
    hotkey_profile_2_target: str = ""
    hotkey_profile_3: str = ""
    hotkey_profile_3_target: str = ""
    local_api_enabled: bool = False
    local_api_lan_enabled: bool = False
    local_api_port: int = DEFAULT_PORT
    dataset_enabled: bool = False
    dataset_capture_confidence_enabled: bool = False
    dataset_directory: str = ""
    dataset_min_confidence: float = 0.50
    dataset_interval_ms: int = 1000
    dataset_max_images: int = 0
    dataset_max_mib: int = 0
    dataset_capture_requires_activation: bool = True
    dataset_jpeg_quality: int = 92
    dataset_annotation_model: str = ""
    dataset_annotation_provider: str = "auto"
    dataset_annotation_passes: int = 2
    dataset_annotation_confidence: float = 0.55
    dataset_annotation_iou: float = 0.85
    dataset_annotation_nms_iou: float = 0.45
    dataset_annotation_additive: bool = False
    inference_cpu_placement: dict = field(default_factory=dict)
    dataset_annotation_replace: bool = False
    dataset_annotation_delete_unannotated: bool = False
    dataset_effects_enabled: bool = False
    dataset_effects_extra_copies: bool = False
    dataset_effects_copies: int = 1
    dataset_effects_strength: str = "medium"
    dataset_effects_blur: bool = True
    dataset_effects_colour: bool = True
    dataset_effects_exposure: bool = True
    dataset_effects_noise: bool = True
    dataset_effects_compression: bool = True
    ultralytics_owner: str = ""
    ultralytics_dataset: str = ""
    ultralytics_key_protected: str = ""
    ultralytics_annotation_model: str = ""
    ultralytics_annotation_provider: str = "auto"
    ultralytics_annotation_passes: int = 2
    ultralytics_annotation_confidence: float = 0.55
    ultralytics_annotation_iou: float = 0.85
    ultralytics_annotation_nms_iou: float = 0.45
    ultralytics_image_limit: int = 100
    ultralytics_output_directory: str = ""
    ultralytics_effects_enabled: bool = False
    ultralytics_effects_extra_copies: bool = False
    ultralytics_effects_copies: int = 1
    ultralytics_effects_strength: str = "medium"
    ultralytics_effects_blur: bool = True
    ultralytics_effects_colour: bool = True
    ultralytics_effects_exposure: bool = True
    ultralytics_effects_noise: bool = True
    ultralytics_effects_compression: bool = True
    dataset_export_val_split: float = 0.15
    dataset_review_approval_key: str = "enter"
    dataset_review_class_slots: list[int] = field(default_factory=lambda: list(range(9)))
    training_base_model: str = ""
    training_dataset: str = ""
    training_name: str = "vaporlock-training"
    training_platform_project: str = ""
    training_epochs: int = 100
    training_imgsz: int = 640
    training_batch: str = "auto"
    training_device: str = "auto"
    training_workers: int = 8
    training_patience: int = 100
    training_optimizer: str = "auto"
    training_lr0: float = 0.01
    training_lrf: float = 0.01
    training_weight_decay: float = 0.0005
    training_warmup_epochs: float = 3.0
    training_close_mosaic: int = 10
    training_cache: str = "disk"
    training_amp: bool = True
    training_pretrained: bool = True
    training_cos_lr: bool = False
    training_seed: int = 0
    training_deterministic: bool = True
    training_fraction: float = 1.0
    training_export_onnx: bool = True
    training_dynamic_onnx: bool = True
    training_extra_arguments: str = ""

    def __post_init__(self) -> None:
        from cloud_settings import validate as validate_cloud
        validate_cloud(vars(self))
        from batch6_settings import validate
        validate(vars(self))
        from batch7_settings import validate as validate_batch7
        validate_batch7(vars(self))
        from crosshair_reference import validate as validate_crosshair
        validate_crosshair(vars(self))
        from exclusion_zones import validate_zones
        self.exclusion_zones = validate_zones(self.exclusion_zones)
        # Legacy frame durations have a fixed 60 Hz compatibility baseline.
        for duration, frames in (("track_retention_ms", "max_missed"),
                                 ("lost_target_timeout_ms", "lost_target_timeout"),
                                 ("occlusion_grace_ms", "occlusion_grace_frames")):
            if getattr(self, duration) < 0:
                setattr(self, duration, max(0, round(getattr(self, frames) * 1000 / 60)))


SETTING_ENUMS = {
    "ultralytics_annotation_provider": ('auto','directml','cuda','cpu'),
    "dataset_effects_strength": ("mild", "medium", "strong"),
    "ultralytics_effects_strength": ("mild", "medium", "strong"),
    "dataset_annotation_provider": ANNOTATION_BACKENDS,
    "provider": ("auto", "dml", "cuda", "trt", "cpu"),
    "resize_mode": ("letterbox", "stretch"),
    "model_output_format": ("auto", "yolo-class", "yolo-objectness", "end-to-end"),
    "model_coordinate_units": ("auto", "pixels", "normalized"),
    "strategy": (0, 1, 2, 3, 4),
    "fov_shape": ("circle", "rectangle"),
    "anchor_mode": ("center", "top", "bottom", "upper-quarter"),
    "activation_mode": ("hold", "toggle"),
    "activation_source": ("keyboard", "controller", "either"),
    "manual_lock_source": ("keyboard", "controller", "either"),
    "event_action_mode": ("click", "hold"),
    "event_action_button": ("left", "right", "middle"),
    "output_transport": ("mouse", "virtual-mouse", "virtual-xbox", "makcu-v1", "makcu-v2", "serial-hid-bridge", "kmbox-net", "kmbox-a"),
    "virtual_controller_mode": ("slot0-proxy", "separate"),
    "virtual_controller_stick": ("left", "right"),
    "response_curve": ("linear", "quadratic"),
    "aim_reference": ("capture-center", "crosshair-offset"),
    "motion_compensation_activation_mode": ("hold", "click", "constant", "automatic"),
    "motion_compensation_mode": ("preset", "automatic", "combined"),
    "desktop_overlay_layout": ("minimal", "standard", "diagnostics"),
    "desktop_overlay_palette": ("vapor", "contrast"),
    "dataset_review_approval_key": ("enter", "space"),
    "training_cache": ("disk", "ram", "false"),
    "training_optimizer": ("auto", "SGD", "Adam", "AdamW", "RMSProp"),
}

AIM_NATIVE_SETTINGS = (
    "assistance_x", "assistance_y", "controller_output_deadzone", "acquire_confidence", "retain_confidence",
    "track_retention_ms", "lost_target_timeout_ms", "occlusion_grace_ms",
    "snap_bypasses_confirmation", "near_candidate_wait_ms", "output_max_observation_age_ms",
    "prediction_max_speed", "prediction_max_acceleration", "prediction_max_distance", "prediction_noise_floor",
    "input_units_per_pixel_x", "input_units_per_pixel_y", "max_speed",
    "aim_reference", "aim_reference_x", "aim_reference_y",
)


class InitializationWizard(QDialog):
    """Mandatory first-run setup with an explicit, inert calibration stage."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.ui_language = getattr(parent, "ui_language", load_preferred_language())
        self.setWindowTitle("Vapor Lock setup & calibration")
        self.setModal(True)
        self.resize(760, 590)
        self.setMinimumSize(680, 540)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        kicker = QLabel("VAPOR LOCK 0.1 / INITIALIZATION")
        kicker.setObjectName("pageKicker")
        root.addWidget(kicker)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        root.addWidget(self.title)
        self.step = QLabel()
        self.step.setObjectName("detail")
        root.addWidget(self.step)

        self.pages = QStackedWidget()
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.setWidget(self.pages)
        root.addWidget(self.page_scroll, 1)
        self._build_welcome_page()
        self._build_engine_page()
        self._build_capture_page()
        self._build_binding_page()
        self._build_calibration_page()

        actions = QHBoxLayout()
        self.back = QPushButton("Back")
        self.back.setAutoDefault(False)
        self.back.clicked.connect(self.previous_page)
        self.next = QPushButton("Next")
        self.next.setObjectName("primaryButton")
        self.next.setDefault(True)
        self.next.clicked.connect(self.next_page)
        actions.addWidget(self.back)
        actions.addStretch(1)
        actions.addWidget(self.next)
        root.addLayout(actions)
        self.pages.currentChanged.connect(self.refresh_navigation)
        self.pages.currentChanged.connect(lambda _index: self.page_scroll.verticalScrollBar().setValue(0))
        self.refresh_navigation(0)
        self._translator = InterfaceTranslator()
        self._vaporlock_dialog_translator = self._translator
        self._translator.capture(self)
        self._translator.apply(self.ui_language)
        self.refresh_navigation(0)

    def t(self, source):
        return translate_text(source, self.ui_language)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_available_area)

    def _fit_available_area(self):
        if not self.isVisible():
            return
        area = self.screen().availableGeometry()
        width, height = max(1, area.width() - 24), max(1, area.height() - 48)
        self.setMinimumSize(min(680, width), min(540, height))
        self.resize(min(760, width), min(650, height))
        frame = self.frameGeometry()
        frame.moveCenter(area.center())
        self.move(max(area.left(), frame.left()), max(area.top(), frame.top()))

    def _page(self, detail: str) -> QVBoxLayout:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 8, 2, 8)
        layout.setSpacing(14)
        copy = QLabel(detail)
        copy.setObjectName("pageSubtitle")
        copy.setWordWrap(True)
        layout.addWidget(copy)
        self.pages.addWidget(page)
        return layout

    @staticmethod
    def _field(layout: QVBoxLayout, title: str, detail: str, control: QWidget) -> None:
        label = QLabel(title)
        label.setObjectName("fieldTitle")
        layout.addWidget(label)
        copy = QLabel(detail)
        copy.setObjectName("fieldDescription")
        copy.setWordWrap(True)
        layout.addWidget(copy)
        layout.addWidget(control)

    def _build_welcome_page(self) -> None:
        layout = self._page(
            "This one-time setup prepares a usable starting profile. Nothing here starts capture, sends movement, or downloads a component without your choice."
        )
        points = QLabel(
            "You will choose the inference path, capture area, activation binding, and a movement calibration preset. "
            "The wizard is included with VaporLock and can be run again from Overview or its shortcut."
        )
        points.setWordWrap(True)
        points.setObjectName("signalValue")
        layout.addWidget(points)
        layout.addStretch(1)

    def _build_engine_page(self) -> None:
        layout = self._page("Choose the model and processing path used when a session starts.")
        self.model_choice = QComboBox()
        self.model_choice.addItem("No model selected / test signal", "")
        models, aliases = discover_local_models(ROOT / "models")
        for model in models:
            self.model_choice.addItem(model.relative_to(ROOT / "models").as_posix(), str(model))
        current_model = str(resolve_product_path(self.settings.model_path)) if self.settings.model_path else ""
        self.model_choice.setCurrentIndex(max(0, self.model_choice.findData(aliases.get(current_model, current_model))))
        self._field(layout, "Detection model", "Models already in the local models folder are available here.", self.model_choice)
        self.provider_choice = QComboBox()
        for label, value in (("DirectML (recommended default)", "dml"), ("Automatic", "auto"), ("CPU", "cpu"), ("CUDA", "cuda"), ("TensorRT", "tensorrt")):
            self.provider_choice.addItem(label, value)
        self.provider_choice.setCurrentIndex(max(0, self.provider_choice.findData(self.settings.provider)))
        self._field(layout, "Execution provider", "A missing optional runtime is offered after setup; the wizard itself stays responsive.", self.provider_choice)
        layout.addStretch(1)

    def _build_capture_page(self) -> None:
        layout = self._page("Set the initial display, centered capture size, and rate limits.")
        self.monitor_choice = QComboBox()
        self.monitor_choice.addItem("Virtual desktop", 0)
        for index, screen in enumerate(QGuiApplication.screens(), start=1):
            size = screen.size()
            self.monitor_choice.addItem(f"Monitor {index} · {screen.name()} ({size.width()} x {size.height()})", index)
        self.monitor_choice.setCurrentIndex(max(0, self.monitor_choice.findData(self.settings.monitor)))
        self._field(layout, "Capture display", "The native engine captures only the selected centered region.", self.monitor_choice)
        self.region_choice = QComboBox()
        for size in (320, 416, 512, 640, 1024):
            self.region_choice.addItem(f"{size} x {size}", size)
        current_size = self.settings.region_width if self.settings.region_width == self.settings.region_height else 640
        self.region_choice.setCurrentIndex(max(0, self.region_choice.findData(current_size)))
        self._field(layout, "Centered region", "This starts at offset 0, 0 and remains editable in Targeting.", self.region_choice)
        rates = QWidget()
        rates_layout = QHBoxLayout(rates)
        rates_layout.setContentsMargins(0, 0, 0, 0)
        self.capture_rate = QSpinBox()
        self.capture_rate.setRange(1, 1000)
        self.capture_rate.setValue(max(1, self.settings.capture_fps))
        self.capture_rate.setSuffix(" fps capture")
        self.inference_rate = QSpinBox()
        self.inference_rate.setRange(1, 1000)
        self.inference_rate.setValue(max(1, self.settings.inference_fps))
        self.inference_rate.setSuffix(" fps inference")
        rates_layout.addWidget(self.capture_rate)
        rates_layout.addWidget(self.inference_rate)
        self._field(layout, "Rate limits", "These are limits, not promised throughput, and can be typed above 120.", rates)
        layout.addStretch(1)

    def _build_binding_page(self) -> None:
        layout = self._page("Movement and calibration remain inert until you confirm how they are authorized.")
        self.activation_source = QComboBox()
        for label, value in (("Keyboard / mouse", "keyboard"), ("Xbox controller", "controller"), ("Keyboard or controller", "either")):
            self.activation_source.addItem(label, value)
        self.activation_source.setCurrentIndex(max(0, self.activation_source.findData(self.settings.activation_source)))
        self._field(layout, "Activation source", "Choose which input can authorize movement output.", self.activation_source)
        self.activation_mode = QComboBox()
        self.activation_mode.addItem("Hold", "hold")
        self.activation_mode.addItem("Toggle", "toggle")
        self.activation_mode.setCurrentIndex(max(0, self.activation_mode.findData(self.settings.activation_mode)))
        self._field(layout, "Activation mode", "Hold is the safer initial behavior; toggle remains available.", self.activation_mode)
        self.activation_key = QComboBox()
        for label, value in (("Right mouse button", "right"), ("Left Shift", "shift"), ("Left Control", "control"), ("Space", "space")):
            self.activation_key.addItem(label, value)
        self.activation_key.setCurrentIndex(max(0, self.activation_key.findData(self.settings.activation_key)))
        self._field(layout, "Keyboard binding", "Used by Keyboard and Keyboard-or-controller modes.", self.activation_key)
        self.controller_binding = QComboBox()
        for label, value in (("Right trigger", "right_trigger"), ("Left trigger", "left_trigger"), ("Right shoulder", "right_shoulder"), ("Left shoulder", "left_shoulder"), ("A", "a"), ("B", "b"), ("X", "x"), ("Y", "y")):
            self.controller_binding.addItem(label, value)
        self.controller_binding.setCurrentIndex(max(0, self.controller_binding.findData(self.settings.controller_binding)))
        self._field(layout, "Controller binding", "Used by Controller and Keyboard-or-controller modes.", self.controller_binding)
        self.binding_confirmed = QCheckBox("I have reviewed and confirmed the activation binding above")
        self.binding_confirmed.toggled.connect(lambda _checked: self.refresh_navigation(self.pages.currentIndex()))
        layout.addWidget(self.binding_confirmed)
        layout.addStretch(1)

    def _build_calibration_page(self) -> None:
        layout = self._page("Calibration never begins automatically. The shortcut below opens this wizard; applying a preset only changes profile values.")
        self.calibration_shortcut = QKeySequenceEdit(QKeySequence(self.settings.hotkey_calibration))
        self.calibration_shortcut.setClearButtonEnabled(True)
        self.calibration_shortcut.keySequenceChanged.connect(lambda _sequence: self.refresh_navigation(self.pages.currentIndex()))
        self._field(layout, "Calibration shortcut", "A non-empty binding is required before setup can finish.", self.calibration_shortcut)
        self.calibration_preset = QComboBox()
        self.calibration_preset.addItem("Keep shipped profile tuning", "keep")
        self.calibration_preset.addItem("Stable", "stable")
        self.calibration_preset.addItem("Balanced", "balanced")
        self.calibration_preset.addItem("Responsive", "responsive")
        self._field(layout, "Movement calibration", "Stable favors settling, Balanced is a general baseline, and Responsive favors quicker correction.", self.calibration_preset)
        axes = QWidget()
        axes_layout = QHBoxLayout(axes)
        axes_layout.setContentsMargins(0, 0, 0, 0)
        self.invert_x = QCheckBox("Invert horizontal")
        self.invert_x.setChecked(self.settings.invert_x)
        self.invert_y = QCheckBox("Invert vertical")
        self.invert_y.setChecked(self.settings.invert_y)
        axes_layout.addWidget(self.invert_x)
        axes_layout.addWidget(self.invert_y)
        axes_layout.addStretch(1)
        self._field(layout, "Axis direction", "Leave both off unless the destination interprets an axis in reverse.", axes)
        layout.addStretch(1)

    def refresh_navigation(self, index: int) -> None:
        titles = ("Welcome", "Model & engine", "Capture", "Activation binding", "Calibration")
        self.title.setText(self.t(titles[index]))
        self.step.setText(format_text("Step {current} of {total}", self.ui_language, current=index + 1, total=len(titles)))
        self.back.setEnabled(index > 0)
        can_continue = True
        if index == 3:
            can_continue = self.binding_confirmed.isChecked()
        elif index == 4:
            can_continue = not self.calibration_shortcut.keySequence().isEmpty()
        self.next.setEnabled(can_continue)
        self.next.setText(self.t("Apply setup" if index == self.pages.count() - 1 else "Next"))

    def previous_page(self) -> None:
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))

    def next_page(self) -> None:
        index = self.pages.currentIndex()
        if index == 3 and not self.binding_confirmed.isChecked():
            QMessageBox.information(self, self.t("Activation binding"), self.t("Confirm the activation binding before calibration."))
            return
        if index == self.pages.count() - 1:
            if self.calibration_shortcut.keySequence().isEmpty():
                QMessageBox.information(self, self.t("Calibration shortcut"), self.t("Choose a shortcut before applying setup."))
                return
            self.accept()
            return
        self.pages.setCurrentIndex(index + 1)

    def values(self) -> dict[str, object]:
        size = int(self.region_choice.currentData())
        return {
            "model_path": str(self.model_choice.currentData() or ""),
            "provider": str(self.provider_choice.currentData()),
            "monitor": int(self.monitor_choice.currentData()),
            "region_x": 0,
            "region_y": 0,
            "region_width": size,
            "region_height": size,
            "capture_fps": self.capture_rate.value(),
            "inference_fps": self.inference_rate.value(),
            "activation_source": str(self.activation_source.currentData()),
            "activation_mode": str(self.activation_mode.currentData()),
            "activation_key": str(self.activation_key.currentData()),
            "controller_binding": str(self.controller_binding.currentData()),
            "hotkey_calibration": self.calibration_shortcut.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
            "calibration_preset": str(self.calibration_preset.currentData()),
            "invert_x": self.invert_x.isChecked(),
            "invert_y": self.invert_y.isChecked(),
        }


class ConfigSlider(QSlider):
    """Allow selected compact sliders to pass wheel input to their scroll area."""

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self._wheel_scroll_area: QScrollArea | None = None

    def set_wheel_scroll_area(self, scroll_area: QScrollArea | None) -> None:
        self._wheel_scroll_area = scroll_area
        self.setProperty("wheelPassthrough", scroll_area is not None)

    def wheelEvent(self, event) -> None:
        if self._wheel_scroll_area is not None:
            bar = self._wheel_scroll_area.verticalScrollBar()
            delta = event.angleDelta().y()
            if delta and bar.maximum() > bar.minimum():
                steps = delta / 120.0
                bar.setValue(bar.value() - round(steps * max(1, bar.singleStep()) * 3))
                event.accept()
                return
            event.ignore()
            return
        super().wheelEvent(event)


class RangeControl(QWidget):
    changed = Signal(float)
    _accent_index = 0
    _accents = ("baby", "pink", "ultraviolet", "highlight", "cobalt")

    def __init__(self, low: float, high: float, value: float, decimals: int = 0, suffix: str = "") -> None:
        super().__init__()
        self.scale = 10 ** decimals
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.slider = ConfigSlider(Qt.Orientation.Horizontal)
        accent = self._accents[RangeControl._accent_index % len(self._accents)]
        RangeControl._accent_index += 1
        self.slider.setProperty("barAccent", accent)
        self.slider.setRange(round(low * self.scale), round(high * self.scale))
        self.spin = PreciseSpinBox()
        self.spin.setDecimals(max(6, decimals) if decimals else 0)
        self.spin.setKeyboardTracking(False)
        # The slider exposes the product's tested range. Typed values retain
        # a broad expert range so experienced users can tune beyond it.
        self.spin.setRange(-1_000_000 if low < 0 else 0, 1_000_000)
        self.spin.setSingleStep(1 / self.scale)
        self.spin.setSuffix(suffix)
        self.spin.setMinimumWidth(100)
        self.set_value(value)
        self.slider.valueChanged.connect(self._slider_changed)
        self.spin.valueChanged.connect(self._spin_changed)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)

    def pass_slider_wheel_to_parent(self, scroll_area: QScrollArea | None = None) -> None:
        self.slider.setProperty("wheelPassthrough", True)
        if scroll_area is not None:
            self.slider.set_wheel_scroll_area(scroll_area)

    def set_value(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.spin.blockSignals(True)
        self.slider.setValue(round(max(self.slider.minimum() / self.scale, min(self.slider.maximum() / self.scale, value)) * self.scale))
        self.spin.setValue(value)
        self.slider.blockSignals(False)
        self.spin.blockSignals(False)

    def _slider_changed(self, value: int) -> None:
        actual = value / self.scale
        self.spin.blockSignals(True)
        self.spin.setValue(actual)
        self.spin.blockSignals(False)
        self.changed.emit(actual)

    def _spin_changed(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(max(self.slider.minimum() / self.scale, min(self.slider.maximum() / self.scale, value)) * self.scale))
        self.slider.blockSignals(False)
        self.changed.emit(value)


class PreviewCanvas(QLabel):
    """A fixed viewport for one debug frame; frame pixels never affect page layout."""

    def __init__(self) -> None:
        super().__init__("Start capture to receive the live annotated preview")
        self.setWordWrap(True)
        self._frame = QImage()
        self.setObjectName("previewSurface")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(230)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(640, 230)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        from interface_themes import painted_role
        painter.fillRect(self.contentsRect(), QColor(painted_role(self,'input','#080d16')))
        painter.end()
        super().paintEvent(event)

    def set_frame(self, frame: QImage) -> None:
        if frame.isNull():
            self._frame = QImage()
            self.clear()
            return
        self._frame = frame
        self._render_frame()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_frame()

    def _render_frame(self) -> None:
        if self._frame.isNull() or self.contentsRect().size().isEmpty():
            return
        pixmap = QPixmap.fromImage(self._frame)
        self.setPixmap(pixmap.scaled(self.contentsRect().size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def save_snapshot(self, path: Path) -> bool:
        """Write the most recent annotated native preview without resizing it."""
        return not self._frame.isNull() and self._frame.save(str(path), "PNG")


class DatasetReviewQueueDelegate(QStyledItemDelegate):
    """Render persistent, decision-specific queue tiles without selection hiding them."""

    decision_role = int(Qt.ItemDataRole.UserRole) + 1

    def paint(self, painter: QPainter, option, index) -> None:
        decision = str(index.data(self.decision_role) or "")
        if decision not in {"accepted", "rejected"}:
            super().paint(painter, option, index)
            return
        accepted = decision == "accepted"
        fill = QColor("#9d3c88") if accepted else QColor("#8f344a")
        border = QColor("#f08bc8") if accepted else QColor("#e68199")
        title = "ACCEPTED" if accepted else "EXCLUDED"
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        filename = text.split(" · ", 1)[-1]
        rect = option.rect.adjusted(5, 3, -5, -3)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(border, 1.4))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor("#fff3fb"))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        painter.drawText(rect.adjusted(9, 0, -9, 0), Qt.AlignmentFlag.AlignVCenter, f"{title}  ·  {filename}")
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        if str(index.data(self.decision_role) or "") in {"accepted", "rejected"}:
            size.setHeight(max(34, size.height()))
        return size


class DatasetReviewCanvas(QWidget):
    """Direct point-and-box editor for a single YOLO review image."""

    annotations_changed = Signal(list)
    selection_changed = Signal(int)
    zoom_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._image = QImage()
        self._annotations: list[dict[str, object]] = []
        self._selected = -1
        self._pending: QPointF | None = None
        self._dragging = False
        self._active_class = 0
        self._cursor: QPointF | None = None
        self._zoom = 1.0
        self._pan = QPointF()
        self._panning = False
        self._pan_origin = QPointF()
        self._pan_start = QPointF()
        self.setMinimumSize(400, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def set_review_image(self, image: QImage, annotations: list[dict[str, object]]) -> None:
        self._image = image.convertToFormat(QImage.Format.Format_ARGB32)
        self._annotations = [dict(item) for item in annotations]
        self._selected = -1
        self._pending = None
        self._zoom = 1.0
        self._pan = QPointF()
        self.zoom_changed.emit(100)
        self.update()

    def annotations(self) -> list[dict[str, object]]:
        return [dict(item) for item in self._annotations]

    def set_active_class(self, class_id: int) -> None:
        self._active_class = max(0, int(class_id))

    def zoom_percent(self) -> int:
        return round(self._zoom * 100)

    def set_zoom_percent(self, percent: int, anchor: QPointF | None = None) -> None:
        zoom = max(1.0, min(3.0, int(percent) / 100.0))
        if abs(zoom - self._zoom) < 0.001:
            return
        anchor_point = anchor if anchor is not None else None
        normalized_anchor = self._normalized_point(anchor_point) if anchor_point is not None else None
        self._zoom = zoom
        if normalized_anchor is not None and anchor_point is not None:
            size = self._fit_size()
            width = max(1, round(size.width() * self._zoom))
            height = max(1, round(size.height() * self._zoom))
            self._pan = QPointF(
                anchor_point.x() - (self.width() - width) / 2 - normalized_anchor.x() * width,
                anchor_point.y() - (self.height() - height) / 2 - normalized_anchor.y() * height,
            )
        self._constrain_pan()
        self.zoom_changed.emit(self.zoom_percent())
        self.update()

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF()
        self.zoom_changed.emit(100)
        self.update()

    def _fit_size(self) -> QSize:
        if self._image.isNull() or self.width() <= 0 or self.height() <= 0:
            return QSize()
        return self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)

    def _constrain_pan(self) -> None:
        size = self._fit_size()
        max_x = max(0.0, (size.width() * self._zoom - self.width()) / 2.0)
        max_y = max(0.0, (size.height() * self._zoom - self.height()) / 2.0)
        self._pan = QPointF(
            max(-max_x, min(max_x, self._pan.x())),
            max(-max_y, min(max_y, self._pan.y())),
        )

    def _display_rect(self) -> QRect:
        if self._image.isNull() or self.width() <= 0 or self.height() <= 0:
            return QRect()
        scaled = self._fit_size()
        width = max(1, round(scaled.width() * self._zoom))
        height = max(1, round(scaled.height() * self._zoom))
        self._constrain_pan()
        return QRect(
            round((self.width() - width) / 2 + self._pan.x()),
            round((self.height() - height) / 2 + self._pan.y()),
            width,
            height,
        )

    def _normalized_point(self, point: QPointF) -> QPointF | None:
        rect = self._display_rect()
        screen_point = point.toPoint() if hasattr(point, "toPoint") else point
        if rect.isEmpty() or not rect.contains(screen_point):
            return None
        return QPointF(
            max(0.0, min(1.0, (point.x() - rect.x()) / max(1, rect.width()))),
            max(0.0, min(1.0, (point.y() - rect.y()) / max(1, rect.height()))),
        )

    def _screen_point(self, point: QPointF) -> QPointF:
        rect = self._display_rect()
        return QPointF(rect.x() + point.x() * rect.width(), rect.y() + point.y() * rect.height())

    def _annotation_rect(self, annotation: dict[str, object]) -> QRect:
        if annotation.get("kind") != "box":
            return QRect()
        rect = self._display_rect()
        return QRect(
            round(rect.x() + float(annotation["x"]) * rect.width()),
            round(rect.y() + float(annotation["y"]) * rect.height()),
            max(1, round(float(annotation["w"]) * rect.width())),
            max(1, round(float(annotation["h"]) * rect.height())),
        ).normalized()

    def _selected_at(self, point: QPointF) -> int:
        for index in range(len(self._annotations) - 1, -1, -1):
            annotation = self._annotations[index]
            if annotation.get("kind") == "box" and self._annotation_rect(annotation).adjusted(-6, -6, 6, 6).contains(point.toPoint()):
                return index
            if annotation.get("kind") == "polygon":
                points = annotation.get("points", [])
                polygon = QPainterPath()
                if points:
                    polygon.moveTo(self._screen_point(points[0]))
                    for normalized in points[1:]:
                        polygon.lineTo(self._screen_point(normalized))
                    polygon.closeSubpath()
                    if polygon.contains(point):
                        return index
        return -1

    def _emit_annotations(self) -> None:
        self.annotations_changed.emit(self.annotations())

    def delete_selected(self) -> None:
        if not 0 <= self._selected < len(self._annotations):
            return
        self._annotations.pop(self._selected)
        self._selected = -1
        self.selection_changed.emit(-1)
        self._emit_annotations()
        self.update()

    def _finalize_box(self, end: QPointF) -> None:
        if self._pending is None:
            return
        x1, x2 = sorted((self._pending.x(), end.x()))
        y1, y2 = sorted((self._pending.y(), end.y()))
        self._pending = None
        if x2 - x1 < 0.003 or y2 - y1 < 0.003:
            self.update()
            return
        self._annotations.append({
            "kind": "box", "class_id": getattr(self, "_active_class", 0),
            "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1,
        })
        self._selected = len(self._annotations) - 1
        self.selection_changed.emit(self._selected)
        self._emit_annotations()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_origin = event.position()
            self._pan_start = QPointF(self._pan)
            event.accept()
            return
        point = self._normalized_point(event.position())
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if point is None:
            return
        found = self._selected_at(event.position())
        if event.button() == Qt.MouseButton.RightButton:
            if found >= 0:
                self._annotations.pop(found)
                self._selected = -1
                self.selection_changed.emit(-1)
                self._emit_annotations()
                self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if found >= 0 and self._pending is None:
            self._selected = found
            self.selection_changed.emit(found)
            self.update()
            return
        if self._pending is None:
            self._pending = point
            self._cursor = point
            self._dragging = True
            self._selected = -1
            self.selection_changed.emit(-1)
            self.update()
        else:
            self._finalize_box(point)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            delta = event.position() - self._pan_origin
            self._pan = self._pan_start + delta
            self._constrain_pan()
            self.update()
            event.accept()
            return
        self._cursor = self._normalized_point(event.position())
        self.update()

    def leaveEvent(self, event) -> None:
        self._cursor = None
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or self._pending is None:
            return
        point = self._normalized_point(event.position())
        if point is not None and self._dragging:
            dx = abs(point.x() - self._pending.x())
            dy = abs(point.y() - self._pending.y())
            if dx >= 0.008 or dy >= 0.008:
                self._finalize_box(point)
        self._dragging = False

    def wheelEvent(self, event) -> None:
        if self._image.isNull() or not self.rect().contains(event.position().toPoint()):
            return super().wheelEvent(event)
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.set_zoom_percent(self.zoom_percent() + round(steps * 20), event.position())
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and 0 <= self._selected < len(self._annotations):
            self.delete_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        from interface_themes import painted_role
        painter.fillRect(self.rect(), QColor(painted_role(self,'input','#060b13')))
        rect = self._display_rect()
        if self._image.isNull() or rect.isEmpty():
            painter.setPen(QColor(painted_role(self,'secondary','#9db4cf')))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Choose an image to review")
            painter.end()
            return
        painter.drawImage(rect, self._image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index, annotation in enumerate(self._annotations):
            selected = index == self._selected
            color = QColor("#f08bc8") if selected else QColor("#78c3ff")
            painter.setPen(QPen(color, 3 if selected else 2))
            class_id = int(annotation.get("class_id", 0))
            if annotation.get("kind") == "box":
                box = self._annotation_rect(annotation)
                painter.drawRect(box)
                painter.fillRect(QRect(box.x(), max(rect.y(), box.y() - 19), max(36, 12 + len(str(class_id)) * 8), 18), color)
                painter.setPen(QColor("#07111f"))
                painter.drawText(QRect(box.x() + 5, max(rect.y(), box.y() - 19), 50, 18), Qt.AlignmentFlag.AlignVCenter, str(class_id))
            else:
                points = annotation.get("points", [])
                if points:
                    path = QPainterPath(self._screen_point(points[0]))
                    for normalized in points[1:]:
                        path.lineTo(self._screen_point(normalized))
                    path.closeSubpath()
                    painter.drawPath(path)
        cursor = self._cursor
        if cursor is None:
            cursor = self._normalized_point(self.mapFromGlobal(QCursor.pos()))
        if cursor is not None:
            point = self._screen_point(cursor)
            painter.setPen(QPen(QColor("#f7d34a"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(rect.left(), round(point.y()), rect.right(), round(point.y()))
            painter.drawLine(round(point.x()), rect.top(), round(point.x()), rect.bottom())
            painter.setPen(QPen(QColor("#fff2a8"), 2))
            painter.drawLine(round(point.x()) - 10, round(point.y()), round(point.x()) + 10, round(point.y()))
            painter.drawLine(round(point.x()), round(point.y()) - 10, round(point.x()), round(point.y()) + 10)
            painter.drawEllipse(point, 4, 4)
        if self._pending is not None:
            current = cursor or self._pending
            x1, x2 = sorted((self._pending.x(), current.x()))
            y1, y2 = sorted((self._pending.y(), current.y()))
            painter.setPen(QPen(QColor("#f7d34a"), 2, Qt.PenStyle.DashLine))
            painter.drawRect(QRect(round(rect.x() + x1 * rect.width()), round(rect.y() + y1 * rect.height()), round((x2 - x1) * rect.width()), round((y2 - y1) * rect.height())))
        painter.end()


class DesktopOverlay(QWidget):
    """A transparent, click-through representation of native track metadata."""

    def __init__(self) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._payload: dict = {}
        self._native_capture_geometry: tuple[int, int, int, int] | None = None
        self._session_active = False
        self._observation_expiry_ms = 150
        self._labels = True
        self._status = True
        self._motion = False
        self._show_boxes = True
        self._layout = "standard"
        self._scale = 1.0
        self._capture_bounds = False
        self._show_fov = True
        self._fov_shape = "circle"
        self._fov_radii = (48, 180, 420)
        self._palette = "vapor"
        self._track_color = QColor("#7dbcf5")
        self._selected_color = QColor("#e887c5")
        self._fov_color = QColor("#a481f1")
        self._text_color = QColor("#dfe9f2")
        self._provider = ""
        self._fps = 0.0
        self._capture_ms = 0.0
        self._inference_ms = 0.0
        self._tracking_ms = 0.0
        self._window_api = None
        if sys.platform == "win32" and QGuiApplication.platformName() == "windows":
            self._window_api = ctypes.WinDLL("user32", use_last_error=True)
            self._window_api.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, wintypes.UINT,
            ]
            self._window_api.SetWindowPos.restype = wintypes.BOOL
            self._window_api.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
            self._window_api.SetWindowDisplayAffinity.restype = wintypes.BOOL
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(250)
        self._topmost_timer.timeout.connect(self._maintain_topmost)
        self._expiry_timer = QTimer(self)
        self._expiry_timer.setSingleShot(True)
        self._expiry_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._expiry_timer.timeout.connect(self._refresh_observation_expiry)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._restore_window_state()

    def hideEvent(self, event) -> None:
        self._topmost_timer.stop()
        self._expiry_timer.stop()
        super().hideEvent(event)

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() == QEvent.Type.WinIdChange and hasattr(self, "_topmost_timer"):
            # Qt can recreate the HWND after display or window-flag changes.
            QTimer.singleShot(0, self._restore_window_state)
        return result

    def _restore_window_state(self) -> None:
        if not self.isVisible() or not self._session_active:
            return
        if self._native_capture_geometry is not None:
            self._set_physical_geometry(*self._native_capture_geometry, force=True)
        self._exclude_from_capture()
        self._maintain_topmost()
        self._refresh_observation_expiry()
        if not self._topmost_timer.isActive():
            self._topmost_timer.start()

    def _maintain_topmost(self) -> None:
        if not self.isVisible() or not self._session_active:
            self._topmost_timer.stop()
            return
        if self._window_api is None:
            self.raise_()
            return
        # Stay above other topmost/borderless windows without activation, input
        # capture, resizing, or relying on another inference result arriving.
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE, NOMOVE, NOACTIVATE, NOOWNERZORDER
        self._window_api.SetWindowPos(int(self.effectiveWinId()), -1, 0, 0, 0, 0, flags)

    def apply_settings(self, settings: Settings, session_active: bool) -> None:
        if not session_active:
            self._payload = {}
            self._native_capture_geometry = None
        self._session_active = session_active
        self._observation_expiry_ms = max(1, min(2000, settings.output_max_observation_age_ms))
        self._labels = settings.desktop_overlay_labels
        self._status = settings.desktop_overlay_status
        self._motion = settings.desktop_overlay_motion
        self._show_boxes = settings.overlay_boxes
        self._layout = settings.desktop_overlay_layout if settings.desktop_overlay_layout in {"minimal", "standard", "diagnostics"} else "standard"
        self._scale = max(0.25, min(8.0, float(settings.desktop_overlay_scale)))
        self._capture_bounds = settings.desktop_overlay_capture_bounds
        self._show_fov = settings.overlay_fov
        self._fov_shape = settings.fov_shape
        self._fov_radii = (settings.snap_radius, settings.near_radius, settings.acquisition_radius)
        self._palette = settings.desktop_overlay_palette if settings.desktop_overlay_palette in {"vapor", "contrast"} else "vapor"
        def configured_color(value: str, fallback: str) -> QColor:
            color = QColor(value)
            return color if color.isValid() else QColor(fallback)
        self._track_color = configured_color(settings.desktop_overlay_track_color, "#7dbcf5")
        self._selected_color = configured_color(settings.desktop_overlay_selected_color, "#e887c5")
        self._fov_color = configured_color(settings.desktop_overlay_fov_color, "#a481f1")
        self._text_color = configured_color(settings.desktop_overlay_text_color, "#dfe9f2")
        self.setWindowOpacity(max(0.15, min(1.0, settings.desktop_overlay_opacity / 100.0)))
        self._position_for_capture(settings)
        should_show = (
            not False
            and settings.desktop_overlay_enabled
            and settings.desktop_overlay_visible
            and self._session_active
        )
        self.setVisible(should_show)
        self._refresh_observation_expiry()

    def set_payload(self, payload: dict) -> None:
        self._payload = dict(payload)
        try:
            rect = tuple(int(payload[field]) for field in ("capture_left", "capture_top", "width", "height"))
            if rect[2] > 0 and rect[3] > 0 and all(abs(value) <= 1000000 for value in rect):
                self._set_physical_geometry(*rect)
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
        self._refresh_observation_expiry()

    def _refresh_observation_expiry(self) -> None:
        self._expiry_timer.stop()
        now = native_tick_ms()
        from crosshair_reference import remaining_ms
        remaining = min(remaining_ms(self._payload,now),self._observation_expiry_ms - native_observation_age_ms(self._payload, now))
        for track in self._payload.get('tracks', []):
            if isinstance(track, dict) and 'observation_age_ms' in track:
                age = native_observation_age_ms(dict(emitted_tick_ms=self._payload.get('emitted_tick_ms'),
                                                     observation_age_ms=track['observation_age_ms']), now)
                track_remaining = self._observation_expiry_ms - age
                if track_remaining > 0:
                    remaining = min(remaining, track_remaining)
        if self._session_active and self.isVisible() and remaining > 0:
            self._expiry_timer.start(max(1, math.ceil(remaining)))
        self.update()

    def set_runtime_metrics(self, provider: str, fps: float, capture_ms: float = 0.0, inference_ms: float = 0.0, tracking_ms: float = 0.0) -> None:
        self._provider = provider
        self._fps = fps if type(fps) in (int, float) and math.isfinite(fps) and fps >= 0 else None
        self._capture_ms = capture_ms
        self._inference_ms = inference_ms
        self._tracking_ms = tracking_ms
        self.update()

    def _position_for_capture(self, settings: Settings) -> None:
        if self._native_capture_geometry is not None:
            return
        screens = QGuiApplication.screens()
        if not screens:
            return
        if settings.monitor > 0 and settings.monitor <= len(screens):
            screen = screens[settings.monitor - 1]
            geometry = screen.geometry()
            scale = max(1.0, screen.devicePixelRatio())
            physical_width = max(1, round(geometry.width() * scale))
            physical_height = max(1, round(geometry.height() * scale))
            width = min(max(1, settings.region_width), physical_width)
            height = min(max(1, settings.region_height), physical_height)
            left = min(max((physical_width - width) // 2 + settings.region_x, 0), physical_width - width)
            top = min(max((physical_height - height) // 2 + settings.region_y, 0), physical_height - height)
            self.setGeometry(
                round(geometry.x() + left / scale),
                round(geometry.y() + top / scale),
                max(1, round(width / scale)),
                max(1, round(height / scale)),
            )
            return

        # Until the first native frame arrives, use the union of physical
        # display bounds. Qt's Windows screen origins are already physical.
        bounds = QRect()
        for screen in screens:
            geometry = screen.geometry()
            scale = max(1.0, screen.devicePixelRatio())
            bounds = bounds.united(QRect(geometry.x(), geometry.y(),
                                        round(geometry.width() * scale), round(geometry.height() * scale)))
        width = min(max(1, settings.region_width), bounds.width())
        height = min(max(1, settings.region_height), bounds.height())
        left = bounds.x() + min(max((bounds.width() - width) // 2 + settings.region_x, 0), bounds.width() - width)
        top = bounds.y() + min(max((bounds.height() - height) // 2 + settings.region_y, 0), bounds.height() - height)
        self._set_physical_geometry(left, top, width, height, remember=False)

    def _set_physical_geometry(self, left: int, top: int, width: int, height: int,
                               *, force: bool = False, remember: bool = True) -> None:
        rect = (left, top, width, height)
        if not force and remember and rect == self._native_capture_geometry:
            return
        if self._window_api is not None:
            # Position this HWND in the capture engine's physical coordinate
            # system. Let Qt handle the window's device-pixel ratio for painting.
            flags = 0x0004 | 0x0010 | 0x0200  # NOZORDER, NOACTIVATE, NOOWNERZORDER
            if self._window_api.SetWindowPos(int(self.winId()), 0, left, top, width, height, flags):
                if remember:
                    self._native_capture_geometry = rect
                return
        screens = QGuiApplication.screens()
        if not screens:
            return
        def physical_bounds(screen):
            geometry = screen.geometry()
            scale = max(1.0, screen.devicePixelRatio())
            return QRect(geometry.x(), geometry.y(), round(geometry.width() * scale), round(geometry.height() * scale))
        capture = QRect(*rect)
        def intersection_area(screen):
            overlap = physical_bounds(screen).intersected(capture)
            return max(0, overlap.width()) * max(0, overlap.height())
        screen = max(screens, key=intersection_area)
        geometry = screen.geometry()
        scale = max(1.0, screen.devicePixelRatio())
        self.setGeometry(geometry.x() + round((left - geometry.x()) / scale),
                         geometry.y() + round((top - geometry.y()) / scale),
                         max(1, round(width / scale)), max(1, round(height / scale)))
        if remember:
            self._native_capture_geometry = rect

    def _exclude_from_capture(self) -> None:
        if self._window_api is None:
            return
        try:
            # WDA_EXCLUDEFROMCAPTURE is best effort. Older Windows builds may
            # ignore it, but the overlay still remains input-transparent.
            self._window_api.SetWindowDisplayAffinity(int(self.effectiveWinId()), 0x11)
        except (AttributeError, OSError):
            pass

    def paintEvent(self, _event) -> None:
        source_width = float(self._payload.get("width", 0) or 0)
        source_height = float(self._payload.get("height", 0) or 0)
        if source_width <= 0 or source_height <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = painter.font()
        font.setPointSizeF(max(6.0, font.pointSizeF() * self._scale))
        painter.setFont(font)
        scale_x = self.width() / source_width
        scale_y = self.height() / source_height
        tracks = self._payload.get("tracks", [])
        from crosshair_reference import remaining_ms
        fresh = remaining_ms(self._payload,native_tick_ms()) > 0 and native_observation_age_ms(self._payload, native_tick_ms()) < self._observation_expiry_ms
        if not fresh or not isinstance(tracks, list):
            tracks = []
        else:
            tracks = [track for track in tracks if isinstance(track, dict)
                      and ('observation_age_ms' not in track or native_observation_age_ms(
                          dict(emitted_tick_ms=self._payload.get('emitted_tick_ms'),
                               observation_age_ms=track['observation_age_ms']), native_tick_ms()) < self._observation_expiry_ms)]

        palette = {
            "vapor": {
                "track": QColor("#7dbcf5"),
                "selected": QColor("#e887c5"),
                "bounds": QColor("#a481f1"),
                "hud": QColor(10, 16, 24, 220),
                "text": QColor("#dfe9f2"),
            },
            "contrast": {
                "track": QColor("#b9e7ff"),
                "selected": QColor("#ffe44d"),
                "bounds": QColor("#a481f1"),
                "hud": QColor(0, 0, 0, 232),
                "text": QColor("#ffffff"),
            },
        }[self._palette]
        # Explicit editor colors replace the selected palette elements while
        # preserving its carefully legible translucent HUD background.
        palette["track"] = QColor(self._track_color)
        palette["selected"] = QColor(self._selected_color)
        palette["bounds"] = QColor(self._fov_color)
        palette["text"] = QColor(self._text_color)
        selected_track = int(self._payload.get("selected_track", 0) or 0) if fresh else 0
        if not any(track.get('id') == selected_track for track in tracks):
            selected_track = 0
        if self._layout == "minimal":
            tracks = [track for track in tracks if isinstance(track, dict) and bool(track.get("selected", False))]

        if self._capture_bounds:
            painter.setPen(QPen(palette["bounds"], max(1.0, 1.5 * self._scale)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

        if self._show_fov and fresh:
            center_x = float(self._payload.get("reference_x", source_width / 2)) * scale_x
            center_y = float(self._payload.get("reference_y", source_height / 2)) * scale_y
            radius_scale = min(scale_x, scale_y)
            for radius, alpha in zip(self._fov_radii, (190, 155, 125)):
                scaled = max(0.0, float(radius) * radius_scale)
                if scaled <= 0:
                    continue
                color = QColor(palette["bounds"])
                color.setAlpha(alpha)
                painter.setPen(QPen(color, max(1.0, self._scale)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if self._fov_shape == "rectangle":
                    painter.drawRect(round(center_x - scaled), round(center_y - scaled), round(scaled * 2), round(scaled * 2))
                else:
                    painter.drawEllipse(QPointF(center_x, center_y), scaled, scaled)

        for track in tracks:
            if not isinstance(track, dict):
                continue
            selected = bool(track.get("selected", False))
            missed = int(track.get("missed", 0) or 0)
            color = QColor(palette["selected"] if selected else palette["track"])
            if missed:
                color.setAlpha(115)
            x = float(track.get("x", 0.0)) * scale_x
            y = float(track.get("y", 0.0)) * scale_y
            width = max(1.0, float(track.get("w", 0.0)) * scale_x)
            height = max(1.0, float(track.get("h", 0.0)) * scale_y)
            if self._show_boxes:
                painter.setPen(QPen(color, (3.0 if selected else 2.0) * self._scale))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(round(x), round(y), round(width), round(height))
            if self._labels:
                confidence = float(track.get("confidence", 0.0))
                label = str(track.get("label", "object"))
                track_id = int(track.get("id", 0))
                text = f"#{track_id}  {label}  {confidence:.0%}"
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(palette["hud"]))
                metrics = painter.fontMetrics()
                max_text_width = max(1, self.width() - 12)
                text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(1, max_text_width - 12))
                text_width = min(max_text_width, metrics.horizontalAdvance(text) + 12)
                text_height = metrics.height() + 6
                text_x = min(max(0, round(x)), max(0, self.width() - text_width))
                text_y = min(max(0, round(y) - text_height), max(0, self.height() - text_height))
                painter.drawRoundedRect(text_x, text_y, text_width, text_height, 4, 4)
                painter.setPen(color)
                painter.drawText(text_x + 6, text_y + metrics.ascent() + 3, text)
            if selected and self._motion and track.get('guide_valid', False):
                anchor_x = float(track.get("anchor_x", 0.0)) * scale_x
                anchor_y = float(track.get("anchor_y", 0.0)) * scale_y
                predicted_x = float(track.get("predicted_x", anchor_x / scale_x)) * scale_x
                predicted_y = float(track.get("predicted_y", anchor_y / scale_y)) * scale_y
                painter.setPen(QPen(color, 2.0 * self._scale))
                marker = 8.0 * self._scale
                painter.drawLine(round(anchor_x - marker), round(anchor_y), round(anchor_x + marker), round(anchor_y))
                painter.drawLine(round(anchor_x), round(anchor_y - marker), round(anchor_x), round(anchor_y + marker))
                painter.drawLine(round(anchor_x), round(anchor_y), round(predicted_x), round(predicted_y))

        if self._status:
            provider = self._provider
            fps = self._fps
            selected_missed = 0
            for track in tracks:
                if isinstance(track, dict) and int(track.get("id", 0) or 0) == selected_track:
                    selected_missed = int(track.get("missed", 0) or 0)
                    break
            if not fresh:
                lock_state = "STALE FRAME"
            elif selected_track and selected_missed:
                lock_state = f"RECOVERING #{selected_track}"
            elif selected_track and bool(self._payload.get("manual_lock", False)):
                lock_state = f"PINNED #{selected_track}"
            elif selected_track:
                lock_state = f"LOCK #{selected_track}"
            else:
                lock_state = "SCANNING"
            title = "VAPOR LOCK DIAGNOSTICS" if self._layout == "diagnostics" else "VAPOR LOCK"
            fps_text = f"{fps:.0f}" if fresh and fps is not None else "--"
            text = f"{title}  {provider.upper()}  {fps_text} INF FPS  {lock_state}"
            if self._layout == "diagnostics":
                text += (
                    f"  {len(tracks)} TRACKS"
                    f"  CAP {self._capture_ms:.1f} MS"
                    f"  INF {self._inference_ms:.1f} MS"
                    f"  TRK {self._tracking_ms:.1f} MS"
                )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette["hud"]))
            metrics = painter.fontMetrics()
            max_width = max(1, self.width() - 28)
            text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(1, max_width - 18))
            text_width = min(max_width, metrics.horizontalAdvance(text) + 18)
            text_height = metrics.height() + 12
            painter.drawRoundedRect(10, 10, text_width, text_height, 5, 5)
            painter.setPen(palette["text"])
            painter.drawText(19, 10 + metrics.ascent() + 6, text)


class AccelerationManager(QObject):
    """Detect and install optional GPU provider files without touching Windows."""

    status = Signal(str)
    completed = Signal(str, bool, str)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._read_errors)
        self.process.finished.connect(self._finished)
        self._provider = ""
        self._messages: list[str] = []

    @staticmethod
    def supports(provider: str) -> bool:
        if provider in {"cuda", "trt"}:
            try:
                ctypes.WinDLL("nvcuda.dll")
                return True
            except OSError:
                return False
        windows_version = sys.getwindowsversion()
        return provider == "dml" and windows_version.major >= 10 and windows_version.build >= 17763

    @classmethod
    def recommended_provider(cls) -> str:
        # DirectML is bundled on demand and supports modern Windows GPUs from
        # NVIDIA, AMD, and Intel. Auto therefore favors one portable path;
        # CUDA remains an explicit opt-in for customers who have benchmarked it.
        return "dml" if cls.supports("dml") else "cpu"

    @staticmethod
    def is_installed(provider: str) -> bool:
        if not getattr(sys, 'frozen', False):
            from native_engine_layout import source_engine, provider_ready
            if provider_ready(source_engine(ROOT, provider), provider):
                return True
        if provider == "cuda":
            from runtime_generations import cuda_installed
            return cuda_installed(ROOT)
        if provider == "dml":
            dml_root = ROOT / "engine" / "dml"
            return (dml_root / "VaporLockDml.exe").is_file() and (dml_root / "onnxruntime.dll").is_file() and (dml_root / "DirectML.dll").is_file()
        if provider == "trt":
            trt_root = ROOT / "engine" / "trt"
            return all((trt_root / name).is_file() for name in ("VaporLockTensorRt.exe", "onnxruntime.dll", "onnxruntime_providers_shared.dll", "tensorrt_rtx_1_6.dll", "tensorrt_onnxparser_rtx_1_6.dll"))
        return True

    @property
    def active(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def install(self, provider: str) -> None:
        if self.active or provider not in {"cuda", "dml", "trt"}:
            return
        packaged_installer = COMPONENT_ROOT / "VaporLockAccelerationSetup.exe"
        runtime = ROOT / "runtime" / "python.exe"
        python = runtime if runtime.is_file() else Path(sys.executable)
        installer = ROOT / "ui" / "acceleration_install.py"
        if packaged_installer.is_file():
            python = packaged_installer
            installer = None
        if installer is not None and not installer.is_file():
            self.completed.emit(provider, False, "The bundled acceleration installer is missing.")
            return
        self._provider = provider
        self._messages = []
        self.status.emit(f"Preparing {EngineBridge.provider_label(provider)} acceleration download...")
        arguments = ["--provider", provider, "--root", str(ROOT)]
        if installer is not None:
            arguments.insert(0, str(installer))
        self.process.start(str(python), arguments)

    @staticmethod
    def should_offer_setup() -> bool:
        marker = ROOT / "acceleration" / "setup-offered.json"
        provider = AccelerationManager.recommended_provider()
        return provider != "cpu" and not AccelerationManager.is_installed(provider) and not marker.is_file()

    @staticmethod
    def mark_setup_offered() -> None:
        marker = ROOT / "acceleration" / "setup-offered.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"offered": True}), encoding="utf-8")

    def _read_output(self) -> None:
        for line in bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace").splitlines():
            try:
                message = str(json.loads(line).get("message", "")).strip()
            except json.JSONDecodeError:
                message = line.strip()
            if message:
                self._messages.append(message)
                self.status.emit(message)

    def _read_errors(self) -> None:
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if text:
            self._messages.append(text[-500:])

    def _finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        message = self._messages[-1] if self._messages else "The installer stopped without a status message."
        self.completed.emit(self._provider, exit_code == 0 and self.is_installed(self._provider), message)


class ControllerBridgeManager(QObject):
    """Compatibility shim retained for profiles created before 0.0.6."""

    status = Signal(str)
    completed = Signal(bool, str)

    @property
    def active(self) -> bool:
        return False

    def install(self) -> None:
        self.completed.emit(False, "Use the Xbox-compatible bridge setup in Movement > Transport.")


@dataclass(frozen=True)
class ControllerHostSession:
    """A random, local-only bridge session prepared before native capture."""

    pipe_name: str
    status_file: Path
    stop_file: Path
    profile: str
    virtual_slot: int | None


class XboxCompatibleBridgeManager(QObject):
    """Start the opt-in, elevated Xbox One-compatible local bridge.

    The native inference process remains unelevated. This manager only starts
    the isolated host after an explicit UI action and reads its local status
    file; the host owns the virtual HID device and a random per-session pipe.
    """

    status = Signal(str)
    session_ready = Signal(object)
    completed = Signal(bool, str)
    PROTOCOL_VERSION = 2

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._launcher = QProcess(self)
        self._launcher.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._launcher.readyReadStandardOutput.connect(self._read_launcher_output)
        self._launcher.finished.connect(self._launcher_finished)
        self._poller = QTimer(self)
        self._poller.setInterval(125)
        self._poller.timeout.connect(self._poll_status)
        self._operation: str | None = None
        self._operation_started = 0.0
        self._status_file: Path | None = None
        self._stop_file: Path | None = None
        self._pending_session: ControllerHostSession | None = None
        self._session: ControllerHostSession | None = None
        self._last_status = ""
        self._owned_session_process = None
        self._retired_session_processes = []
        self._session_exit_unknown = False
        self._launcher_output_buffer = b''
        self._launcher_expected_executable = None

    @property
    def preparing(self) -> bool:
        return self._operation is not None

    @staticmethod
    def _host_executable() -> Path | None:
        candidates = (
            COMPONENT_ROOT / "controller-host" / "VaporLockControllerHost.exe",
            ROOT / "app" / "controller-host" / "VaporLockControllerHost.exe",
            ROOT / "controller_host" / "bin" / "Release" / "net10.0-windows10.0.26100.0" / "win-x64" / "VaporLockControllerHost.exe",
        )
        return next((candidate for candidate in candidates if candidate.is_file()), None)

    @staticmethod
    def _session_directory() -> Path:
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "VaporLock" / "controller-sessions"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _quote_powershell(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _launch_elevated(self, arguments: list[str], operation: str, status_file: Path,
                         pending_session: ControllerHostSession | None = None) -> None:
        require_native_effect('controller-host')
        if (self.preparing or not self.safely_stopped()
                or self._launcher.state() != QProcess.ProcessState.NotRunning):
            self.completed.emit(False, "The Xbox-compatible bridge is already preparing a session.")
            return
        executable = self._host_executable()
        if executable is None:
            self.completed.emit(False, "The Xbox-compatible controller component is missing from this Vapor Lock copy.")
            return
        self._operation = operation
        if operation == 'session':
            self._session_exit_unknown = True
        self._operation_started = time.monotonic()
        self._status_file = status_file
        self._pending_session = pending_session
        self._stop_file = pending_session.stop_file if pending_session is not None else None
        self._last_status = ""
        self._launcher_output_buffer = b''
        self._launcher_expected_executable = executable
        quoted_arguments = self._quote_powershell(subprocess.list2cmdline(arguments))
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "try { $process = Start-Process -FilePath " + self._quote_powershell(str(executable))
            + " -ArgumentList " + quoted_arguments + " -WindowStyle Hidden -Verb RunAs -PassThru; "
              "[Console]::WriteLine($process.Id) } "
              "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
        )
        self.status.emit("Windows will request administrator approval for the Xbox-compatible controller component.")
        self._launcher.start("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
        self._poller.start()

    def _read_launcher_output(self) -> None:
        self._launcher_output_buffer += bytes(self._launcher.readAllStandardOutput())
        if len(self._launcher_output_buffer) > 4096:
            self._launcher_output_buffer = b''
            self._session_exit_unknown = True
            return
        if b'\n' not in self._launcher_output_buffer:
            return
        record, self._launcher_output_buffer = self._launcher_output_buffer.split(b'\n', 1)
        output = record.decode("utf-8", errors="replace").strip()
        if ((self._operation == 'session' or self._session_exit_unknown)
                and self._owned_session_process is None
                and output.isascii() and output.isdigit() and len(output) <= 10):
            from vlui_owned_process import OwnedProcessExit
            try:
                self._owned_session_process = OwnedProcessExit(int(output), self._launcher_expected_executable)
                self._session_exit_unknown = False
                if self._operation != 'session':
                    self._retired_session_processes.append(self._owned_session_process)
                    self._owned_session_process = None
            except (OSError, ValueError):
                self._session_exit_unknown = True
        if output and not output.isdigit():
            self.status.emit(output[-300:])

    def _launcher_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self._operation is not None and exit_code != 0 and (self._status_file is None or not self._status_file.is_file()):
            self._finish(False, "Windows did not start the Xbox-compatible controller component. Approval may have been declined.")

    def _finish(self, successful: bool, message: str) -> None:
        self._poller.stop()
        operation = self._operation
        if operation == "session" and not successful:
            self.stop_session()
        self._operation = None
        self._status_file = None
        self._pending_session = None
        if operation == "session" and successful and self._session is not None:
            self.session_ready.emit(self._session)
            return
        self._stop_file = None
        self.completed.emit(successful, message)

    def _poll_status(self) -> None:
        if self._operation is None:
            self._poller.stop()
            return
        deadline = 360.0 if self._operation in {"install", "uninstall"} else 35.0
        if time.monotonic() - self._operation_started > deadline:
            self._finish(False, "The Xbox-compatible controller component did not report readiness in time.")
            return
        if self._status_file is None or not self._status_file.is_file():
            return
        try:
            data = json.loads(self._status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        state = str(data.get("state", "")).strip().lower()
        message = str(data.get("message", "")).strip() or "Xbox-compatible controller status updated."
        if message != self._last_status:
            self._last_status = message
            self.status.emit(message)
        if state == "failed":
            self._finish(False, message)
            return
        if self._operation == "install" and state == "installed":
            self._finish(True, message)
            return
        if self._operation == "uninstall" and state == "uninstalled":
            self._finish(True, message)
            return
        if self._operation == "session" and state == "ready" and self._pending_session is not None:
            protocol = data.get("protocol_version")
            if type(protocol) is not int or protocol != self.PROTOCOL_VERSION:
                self._finish(False, "The controller bridge and engine are from different builds. Restore the controller component bundled with this Vapor Lock release.")
                return
            raw_slot = data.get("virtual_slot")
            slot = int(raw_slot) if isinstance(raw_slot, int) and 0 <= raw_slot <= 3 else None
            self._session = ControllerHostSession(
                pipe_name=self._pending_session.pipe_name,
                status_file=self._pending_session.status_file,
                stop_file=self._pending_session.stop_file,
                profile=self._pending_session.profile,
                virtual_slot=slot,
            )
            self._finish(True, message)

    def install(self, profile: str, *, machine_trust_consent: bool = False) -> None:
        if machine_trust_consent is not True:
            raise ValueError("Controller setup requires explicit machine-trust consent.")
        token = uuid.uuid4().hex
        status_file = self._session_directory() / f"install-{token}.json"
        self._launch_elevated([
            "--profile", profile,
            "--status-file", str(status_file),
            "--install-only",
            "--machine-trust-consent",
        ], "install", status_file)

    def start_session(self, profile: str) -> None:
        token = uuid.uuid4().hex
        root = self._session_directory()
        session = ControllerHostSession(
            pipe_name=f"VaporLockController-{token}",
            status_file=root / f"session-{token}.json",
            stop_file=root / f"session-{token}.stop",
            profile=profile,
            virtual_slot=None,
        )
        try:
            session.stop_file.unlink(missing_ok=True)
            session.status_file.unlink(missing_ok=True)
        except OSError:
            pass
        self._launch_elevated([
            "--pipe", session.pipe_name,
            "--profile", session.profile,
            "--status-file", str(session.status_file),
            "--stop-file", str(session.stop_file),
        ], "session", session.status_file, session)

    def uninstall(self) -> None:
        self.stop_session()
        token = uuid.uuid4().hex
        status_file = self._session_directory() / f"uninstall-{token}.json"
        self._launch_elevated([
            "--status-file", str(status_file),
            "--uninstall",
        ], "uninstall", status_file)

    def stop_session(self) -> None:
        if self._owned_session_process is not None:
            self._retired_session_processes.append(self._owned_session_process)
            self._owned_session_process = None
        if self._stop_file is not None:
            try:
                self._stop_file.write_text("stop\n", encoding="ascii")
            except OSError:
                pass
        elif self._session is not None:
            try:
                self._session.stop_file.write_text("stop\n", encoding="ascii")
            except OSError:
                pass
        # A stop during the UAC/status-file preparation window must not leave
        # the control center waiting for a session that it has already asked
        # the local host to tear down.
        if self._operation == "session":
            self._poller.stop()
            self._operation = None
            self._status_file = None
        self._session = None
        self._pending_session = None
        self._stop_file = None

    def safely_stopped(self) -> bool:
        retained = []
        for process in self._retired_session_processes:
            if not process.stopped_and_released():
                retained.append(process)
        self._retired_session_processes = retained
        return (not self.preparing and self._session is None and self._pending_session is None
                and self._owned_session_process is None and not retained and not self._session_exit_unknown)


class EngineBridge(QObject):
    observation_packet = Signal(dict,int,int,int,bool)
    telemetry = Signal(dict)
    preview = Signal(bytes, int, int)
    preview_invalidated = Signal()
    overlay = Signal(dict)
    dataset = Signal(dict)
    message = Signal(str)
    state_changed = Signal(str)
    startup_rejected = Signal()
    live_setting_result = Signal(bool)

    # These settings are consumed from the native process's standard-input
    # command pipe on an independent worker. Runtime/model setup stays restart-only.
    LIVE_NATIVE_SETTINGS = frozenset({
        "confidence", "nms_iou", "nms_enabled", "nms_class_agnostic", "model_output_format", "model_coordinate_units",
        "max_detections", "resize_mode", "class_filter",
        "monitor", "region_x", "region_y", "region_width", "region_height",
        "capture_fps", "inference_fps", "frame_skip",
        "match_iou", "max_missed", "smoothing", "box_smoothing", "anchor_smoothing", "anchor_settle_radius", "predict_lost", "prediction_lead_ms", "prediction_limit_ms",
        "strategy", "acquisition_radius", "near_radius", "snap_radius", "retention_radius", "fov_shape", "switching", "lock_persistence_ms",
        "lost_target_timeout", "minimum_track_age", "anchor_mode", "anchor_x", "anchor_y", "class_policies",
        "watchlist", "occlusion_handling", "occlusion_grace_frames", "occlusion_reacquire_radius",
        "movement_enabled", "activation_mode", "activation_key", "activation_source", "controller_slot",
        "controller_binding", "gain", "max_step", "deadzone", "movement_smoothing", "output_rate",
        "motion_compensation_enabled", "motion_compensation_mode", "motion_compensation_vertical",
        "motion_compensation_activation_mode", "motion_compensation_activation_key",
        "motion_compensation_horizontal", "motion_compensation_strength", "motion_compensation_auto_min_tracks",
        "motion_compensation_auto_consensus", "motion_compensation_auto_smoothing", "motion_compensation_auto_max_rate",
        "event_action_enabled", "event_action_requires_activation", "event_action_mode", "event_action_button", "event_action_cooldown_ms",
        "virtual_controller_manual_priority",
        "virtual_controller_stick", "virtual_controller_strength", "invert_x", "invert_y", "response_curve",
        "preview_enabled", "preview_fps", "preview_quality", "overlay_enabled", "overlay_boxes", "overlay_motion", "overlay_fov", "overlay_segmentation_masks", "segmentation_mask_limit", "segmentation_mask_threshold",
        "dataset_enabled", "dataset_capture_confidence_enabled", "dataset_directory", "dataset_min_confidence", "dataset_interval_ms",
        "dataset_capture_requires_activation", "dataset_jpeg_quality", "dataset_max_images", "dataset_max_mib",
        "desktop_overlay_enabled",
    })
    LIVE_NATIVE_SETTINGS = LIVE_NATIVE_SETTINGS.union(AIM_NATIVE_SETTINGS)
    LIVE_LOCAL_SETTINGS = frozenset({
        "controller_output_opt_in",
        "desktop_overlay_opacity", "desktop_overlay_labels", "desktop_overlay_status", "desktop_overlay_motion",
        "desktop_overlay_visible", "desktop_overlay_layout", "desktop_overlay_scale", "desktop_overlay_capture_bounds",
        "desktop_overlay_palette", "desktop_overlay_track_color", "desktop_overlay_selected_color",
        "desktop_overlay_fov_color", "desktop_overlay_text_color", "hotkey_start_stop", "hotkey_toggle_overlay", "hotkey_emergency_stop",
        "hotkey_next_profile", "hotkey_toggle_target_lock", "hotkey_save_screenshot", "hotkey_calibration",
        "hotkey_profile_1", "hotkey_profile_2", "hotkey_profile_3",
    })
    LIVE_FIELD_ALIASES = {"desktop_overlay_enabled": "overlay_metadata"}

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._read_errors)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self._output_lines = EngineLineReader()
        self._overlay_gate = OverlayFrameGate()
        self._preview_gate = PreviewFrameGate()
        self._preview_observed_at = 0.0
        self._visual_epoch = 0
        self._confirmed_visual_epoch = 0
        self._stream_generation = 0
        self._pending_live_generation = None
        self._live_setting_timer = QTimer(self)
        self._live_setting_timer.setSingleShot(True)
        self._live_setting_timer.timeout.connect(self._live_setting_timeout)
        self._output_failed = False
        self._pending_visuals: dict[str, tuple[int, dict]] = {}
        self._visual_timer = QTimer(self)
        self._visual_timer.setSingleShot(True)
        self._visual_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._visual_timer.timeout.connect(self._flush_visuals)
        self._read_timer = QTimer(self)
        self._read_timer.setSingleShot(True)
        self._read_timer.timeout.connect(self._read_output)
        self.intentional_stop = False
        self._launch_generation = 0
        self._settings: Settings | None = None
        self._providers: list[str] = []
        self._provider_index = 0
        self._active_provider = ""
        self._has_telemetry = False
        self._last_error = ""
        self._fatal_error = False
        self._start_failure_scheduled = False
        self._fallback_pending = False
        self._manual_lock_event_name = ""
        self._manual_lock_event_handle: wintypes.HANDLE | None = None
        self._controller_host_session: ControllerHostSession | None = None
        self._observe_only = False
        self._cloud_session = None
        self.startup_rejected.connect(lambda: self._cloud_session.stop() if self._cloud_session else None)
        self.process.started.connect(lambda: self._cloud_session.native_started() if self._cloud_session else None)

    @property
    def active(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning or bool(self._cloud_session and self._cloud_session.active)

    @property
    def active_provider(self) -> str:
        return self._active_provider

    @property
    def session_running(self) -> bool:
        """True only after the native process has produced live telemetry."""
        return self.active and self._has_telemetry

    @property
    def observe_only(self) -> bool:
        return self._observe_only

    @property
    def overlay_metadata_active(self) -> bool:
        return bool(self.active and self._settings and self._settings.desktop_overlay_enabled and not False)

    @staticmethod
    def provider_label(provider: str) -> str:
        return {"auto": "Automatic", "cpu": "CPU", "cuda": "CUDA", "dml": "DirectML", "trt": "TensorRT RTX"}.get(provider, provider)

    @staticmethod
    def process_environment(provider: str) -> QProcessEnvironment:
        environment = QProcessEnvironment.systemEnvironment()
        return environment

    @staticmethod
    def launch_for(provider: str):
        if not getattr(sys, 'frozen', False):
            from native_engine_layout import source_engine, provider_ready, native_environment
            source = source_engine(ROOT, provider)
            if provider_ready(source, provider):
                environment = QProcessEnvironment()
                for key, value in native_environment(source.parent, dict(os.environ)).items():
                    environment.insert(key, value)
                return source, environment
        if provider != "cuda":
            return EngineBridge.executable_for(provider), EngineBridge.process_environment(provider)
        from runtime_generations import resolve_cuda, RuntimeIntegrityError
        try:
            runtime = resolve_cuda(ROOT)
            environment = QProcessEnvironment()
            for key, value in runtime.environment(dict(os.environ)).items(): environment.insert(key, value)
            return runtime.executable, environment
        except (OSError, ValueError, KeyError, RuntimeIntegrityError):
            return None, QProcessEnvironment.systemEnvironment()

    @staticmethod
    def executable_for(provider: str) -> Path | None:
        if provider == "cuda":
            return EngineBridge.launch_for(provider)[0]
        executable_name = "VaporLockTensorRt.exe" if provider == "trt" else "VaporLockDml.exe" if provider == "dml" else "VaporLock.exe"
        bundled = ROOT / "engine"
        executable = bundled / ("trt" if provider == "trt" else "dml" if provider == "dml" else "") / executable_name
        if executable.is_file():
            if provider == "cuda" and not (bundled / "onnxruntime_providers_cuda.dll").is_file():
                return None
            if provider == "dml" and not AccelerationManager.is_installed("dml"):
                return None
            if provider == "trt" and not AccelerationManager.is_installed("trt"):
                return None
            return executable
        if getattr(sys, "frozen", False):
            return None
        from native_engine_layout import source_engine, provider_ready
        source = source_engine(ROOT, provider)
        return source if provider_ready(source, provider) else None

    @staticmethod
    def candidate_providers(settings: Settings) -> list[str]:
        if settings.provider == "auto":
            # DML is the universal default for an actual model workload.
            # CUDA remains selectable manually and is never downloaded or
            # attempted merely because an NVIDIA driver is present.
            return (["dml"] if settings.inference_dml_device >= 0 else ["dml", "cpu"]) if settings.model_path else ["cpu"]
        return [settings.provider]

    @staticmethod
    def _event_api():
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        return kernel32

    def _release_manual_lock_event(self) -> None:
        handle = self._manual_lock_event_handle
        self._manual_lock_event_handle = None
        self._manual_lock_event_name = ""
        if handle is None or sys.platform != "win32":
            return
        try:
            self._event_api().CloseHandle(handle)
        except (AttributeError, OSError):
            pass

    def _prepare_manual_lock_event(self, settings: Settings) -> None:
        self._release_manual_lock_event()
        if False or not settings.manual_target_lock_enabled or sys.platform != "win32":
            return
        try:
            name = f"Local\\VaporLockManualLock-{uuid.uuid4().hex}"
            handle = self._event_api().CreateEventW(None, False, False, name)
            if not handle:
                self.message.emit("Windows could not create the manual target-lock control channel.")
                return
            self._manual_lock_event_name = name
            self._manual_lock_event_handle = handle
        except (AttributeError, OSError):
            self.message.emit("Windows could not prepare the manual target-lock control channel.")

    def trigger_manual_lock(self) -> bool:
        """Ask the active native session to pin or release its current target."""
        if (
            not self.active
            or self._settings is None
            or not self._settings.manual_target_lock_enabled
            or self._manual_lock_event_handle is None
            or sys.platform != "win32"
        ):
            return False
        try:
            return bool(self._event_api().SetEvent(self._manual_lock_event_handle))
        except (AttributeError, OSError):
            return False

    def apply_live_setting(self, field: str, value: object) -> bool:
        """Mirror a safe setting into the running native session, if any."""
        if field in CAPTURE_FIELDS:
            value = validated_capture_field(field, value)
        if self._observe_only and field in {
            "movement_enabled", "motion_compensation_enabled", "event_action_enabled",
        }:
            # Keep the bridge's session mirror consistent with the native
            # invariant. The profile itself may still be edited for a future
            # Control session while this Observe session is running.
            if self._settings is not None:
                setattr(self._settings, field, False)
            return self.active
        if self._settings is not None and field in Settings.__dataclass_fields__:
            setattr(self._settings, field, value)
        if not self.active:
            return False
        if field in self.LIVE_LOCAL_SETTINGS:
            return True
        if field not in self.LIVE_NATIVE_SETTINGS:
            return False
        # A virtual target has to be created before the session begins. Mouse
        # movement can be enabled live; virtual output needs a fresh session.
        if (
            field == "movement_enabled"
            and self._settings
            and self._settings.output_transport == "virtual-xbox"
            and self._settings.virtual_controller_mode != "slot0-proxy"
        ):
            return False
        native_field = self.LIVE_FIELD_ALIASES.get(field, field)
        if field == 'desktop_overlay_enabled':value=bool(value or getattr(self,'_api_observations',False))
        if field == "class_policies":
            wire_value = policies_wire(value)
        elif field == "dataset_directory":
            wire_value = str(resolve_dataset_root(str(value)))
        elif isinstance(value, bool):
            wire_value = "1" if value else "0"
        else:
            wire_value = str(value)
        if any(character in wire_value for character in "\r\n\t"):
            return False
        try:
            epoch = self._visual_epoch + 1
            command = f"{native_field}\t{wire_value}\t{epoch}\n".encode("utf-8")
            if self.process.write(command) != len(command):
                self.message.emit('Live setting could not be queued. Session stopped; profile changes are retained.')
                self.stop()
                return False
            self._visual_epoch = epoch
            self._pending_live_generation = self._stream_generation
            self._live_setting_timer.start(60000)
            self._pending_visuals.clear()
            self._visual_timer.stop()
            self.overlay.emit({})
            self.preview_invalidated.emit()
            return True
        except RuntimeError:
            self.message.emit('Live setting could not be queued. Session stopped; profile changes are retained.')
            self.stop()
            return False

    def _confirm_live_setting(self, epoch, generation):
        if (type(epoch) is int and epoch == self._visual_epoch
                and generation == self._stream_generation == self._pending_live_generation):
            self._live_setting_timer.stop()
            self._confirmed_visual_epoch = epoch
            self._pending_live_generation = None
            self.live_setting_result.emit(True)

    def _reject_live_setting(self, epoch, generation):
        if (type(epoch) is not int or not self._confirmed_visual_epoch < epoch <= self._visual_epoch
                or generation != self._stream_generation or generation != self._pending_live_generation):
            return
        self._live_setting_timer.stop()
        self._pending_live_generation = None
        self._pending_visuals.clear()
        self._visual_timer.stop()
        self.overlay.emit({})
        self.message.emit('Native engine rejected the live setting. Session stopped; profile changes are retained.')
        self.stop()
        self.live_setting_result.emit(False)

    def _live_setting_timeout(self):
        if self._pending_live_generation == self._stream_generation and self.active:
            self._pending_live_generation = None
            self.message.emit('Native engine did not confirm the live setting. Session stopped; profile changes are retained.')
            self.stop()
            self.live_setting_result.emit(False)

    def start(
        self,
        settings: Settings,
        controller_host_session: ControllerHostSession | None = None,
        observe_only: bool = False,
    ) -> None:
        if self.active:
            return
        validated_capture_settings(asdict(settings))
        if not observe_only and settings.output_transport == "virtual-xbox" and controller_host_session is None:
            self.message.emit("Prepare the Xbox-compatible controller bridge before starting this output transport.")
            self.state_changed.emit("Stopped")
            return
        self._launch_generation += 1
        self.intentional_stop = False
        self._start_failure_scheduled = False
        if settings.model_path:
            model = resolve_product_path(settings.model_path)
            if not model.is_file():
                self.message.emit("The selected model file is unavailable. Choose an existing .onnx or .cloud file.")
                self.state_changed.emit("Stopped")
                self.startup_rejected.emit()
                return
            if model.suffix.lower() not in (".onnx", ".cloud"):
                self.message.emit("Choose an ONNX (.onnx) or VaporLock (.cloud) model.")
                self.state_changed.emit("Stopped")
                self.startup_rejected.emit()
                return
        self._settings = Settings(**asdict(settings))
        self._observe_only = bool(observe_only)
        self._providers = self.candidate_providers(self._settings)
        self._provider_index = 0
        self._active_provider = ""
        self._has_telemetry = False
        self._last_error = ""
        self._fatal_error = False
        self._fallback_pending = False
        self._controller_host_session = controller_host_session
        self._prepare_manual_lock_event(self._settings)
        if self._settings.model_path and Path(self._settings.model_path).suffix.lower() == '.cloud':
            provider = 'dml' if self._settings.provider == 'auto' else self._settings.provider
            if provider not in ('cpu', 'dml', 'cuda'):
                self.message.emit('.cloud supports CPU, DirectML and CUDA in this alpha. TensorRT RTX remains available for ONNX.')
                self.state_changed.emit('Stopped');self._release_manual_lock_event();self.startup_rejected.emit();return
            self._providers = [provider]; self._active_provider = provider
            self.state_changed.emit('Loading .cloud model')
            try:
                from cloud_jobs import CloudLiveSession
                self._cloud_session = CloudLiveSession(self, self._settings, provider)
            except Exception as error:
                self.message.emit('.cloud runtime could not start (' + type(error).__name__ + ').')
                self.state_changed.emit('Stopped');self._release_manual_lock_event();self.startup_rejected.emit()
            return
        self._launch_provider()

    @staticmethod
    def arguments_for(
        settings: Settings,
        provider: str,
        manual_lock_event: str = "",
        controller_host_session: ControllerHostSession | None = None,
        observe_only: bool = False,
        api_observations: bool = False,
    ) -> list[str]:
        validate_batch3(asdict(settings))
        from crosshair_reference import wire as crosshair_wire
        calibration_wire = crosshair_wire(settings)
        from batch6_settings import device_for
        from cpu_placement import wire as cpu_wire
        from exclusion_zones import zones_wire
        validated_capture_settings(asdict(settings))
        from collection_limits import validate_limits
        validate_limits(asdict(settings))
        validate_thresholds(settings.controller_trigger_press, settings.controller_trigger_release)
        # Once the Xbox One bridge has reserved Controller 1, the physical
        # pad moves to another XInput slot. Primary proxy mode must therefore
        # scan the remaining slots even when an older profile named slot 1.
        controller_slot = (
            0
            if controller_host_session is not None
            and settings.output_transport == "virtual-xbox"
            and settings.virtual_controller_mode == "slot0-proxy"
            else settings.controller_slot
        )
        args = [
            "--headless", "--confidence", str(settings.confidence), "--nms-iou", str(settings.nms_iou),
            "--model-output-format", settings.model_output_format,
            "--model-coordinate-units", settings.model_coordinate_units,
            "--provider", provider,
            "--inference-device", str(device_for(settings, provider)),
            "--cpu-sets", cpu_wire(settings,provider),
            "--max-detections", str(settings.max_detections), "--resize-mode", settings.resize_mode,
            "--monitor", str(settings.monitor), "--region", f"{settings.region_x},{settings.region_y},{settings.region_width},{settings.region_height}",
            "--capture-fps", str(settings.capture_fps), "--inference-fps", str(settings.inference_fps),
            "--frame-skip", str(settings.frame_skip), "--match-iou", str(settings.match_iou),
            "--ort-intra-op-threads", str(settings.ort_intra_op_threads), "--ort-inter-op-threads", str(settings.ort_inter_op_threads),
            "--smoothing", str(settings.smoothing), "--box-smoothing", str(settings.box_smoothing), "--anchor-smoothing", str(settings.anchor_smoothing), "--anchor-settle-radius", str(settings.anchor_settle_radius), "--max-missed", str(settings.max_missed),
            "--prediction-lead-ms", str(settings.prediction_lead_ms), "--prediction-limit-ms", str(settings.prediction_limit_ms),
            "--strategy", str(settings.strategy), "--acquire-radius", str(settings.acquisition_radius),
            "--near-radius", str(settings.near_radius), "--snap-radius", str(settings.snap_radius),
            "--retain-radius", str(settings.retention_radius), "--fov-shape", settings.fov_shape, "--switching", str(settings.switching),
            "--lock-persistence-ms", str(settings.lock_persistence_ms), "--lost-target-timeout", str(settings.lost_target_timeout),
            "--min-track-age", str(settings.minimum_track_age), "--anchor-mode", settings.anchor_mode,
            "--anchor-x", str(settings.anchor_x), "--anchor-y", str(settings.anchor_y),
            "--watchlist", settings.watchlist,
            "--occlusion-grace-frames", str(settings.occlusion_grace_frames),
            "--occlusion-reacquire-radius", str(settings.occlusion_reacquire_radius),
            "--class-policies", policies_wire(settings.class_policies),
            "--exclusion-zones", zones_wire(settings.exclusion_zones),
            "--activation-mode", settings.activation_mode, "--activation-key", settings.activation_key,
            "--activation-source", settings.activation_source, "--controller-slot", str(controller_slot),
            "--controller-binding", settings.controller_binding,
            "--controller-trigger-press", str(settings.controller_trigger_press),
            "--controller-trigger-release", str(settings.controller_trigger_release),
            "--manual-lock-source", settings.manual_lock_source, "--manual-lock-key", settings.manual_lock_key,
            "--manual-lock-controller-binding", settings.manual_lock_controller_binding,
            "--gain", str(settings.gain), "--max-step", str(settings.max_step), "--deadzone", str(settings.deadzone),
            "--movement-smoothing", str(settings.movement_smoothing), "--output-rate", str(settings.output_rate),
            "--motion-compensation-mode", settings.motion_compensation_mode,
            "--motion-compensation-activation-mode", settings.motion_compensation_activation_mode,
            "--motion-compensation-activation-key", settings.motion_compensation_activation_key,
            "--motion-compensation-vertical", str(settings.motion_compensation_vertical),
            "--motion-compensation-horizontal", str(settings.motion_compensation_horizontal),
            "--motion-compensation-strength", str(settings.motion_compensation_strength),
            "--motion-compensation-auto-min-tracks", str(settings.motion_compensation_auto_min_tracks),
            "--motion-compensation-auto-consensus", str(settings.motion_compensation_auto_consensus),
            "--motion-compensation-auto-smoothing", str(settings.motion_compensation_auto_smoothing),
            "--motion-compensation-auto-max-rate", str(settings.motion_compensation_auto_max_rate),
            "--event-action-mode", settings.event_action_mode, "--event-action-button", settings.event_action_button,
            "--event-action-cooldown-ms", str(settings.event_action_cooldown_ms),
            "--output-transport", settings.output_transport,
            "--virtual-mouse-library", str(ROOT / "hardware" / "interception" / "x64" / "interception.dll"),
            "--virtual-controller-mode", settings.virtual_controller_mode,
            "--virtual-controller-stick", settings.virtual_controller_stick,
            "--virtual-controller-strength", str(settings.virtual_controller_strength),
            "--hardware-serial-port", settings.hardware_serial_port,
            "--hardware-serial-baud", str(settings.hardware_serial_baud),
            "--kmbox-net-address", settings.kmbox_net_address,
            "--kmbox-net-port", str(settings.kmbox_net_port),
            "--kmbox-net-device-code", settings.kmbox_net_device_code,
            "--kmbox-a-library", str(resolve_product_path(settings.kmbox_a_library_path)) if settings.kmbox_a_library_path else "",
            "--kmbox-a-vid", hex(settings.kmbox_a_vendor_id),
            "--kmbox-a-pid", hex(settings.kmbox_a_product_id),
            "--response-curve", settings.response_curve,
            "--preview-fps", str(settings.preview_fps), "--preview-quality", str(settings.preview_quality),
            "--dataset-min-confidence", str(settings.dataset_min_confidence), "--dataset-interval-ms", str(settings.dataset_interval_ms),
            "--dataset-jpeg-quality", str(settings.dataset_jpeg_quality),
            "--dataset-max-images", str(settings.dataset_max_images), "--dataset-max-mib", str(settings.dataset_max_mib),
            "--segmentation-mask-limit", str(settings.segmentation_mask_limit), "--segmentation-mask-threshold", str(settings.segmentation_mask_threshold),
        ]
        if settings.crosshair_enabled:args.append("--crosshair-enabled")
        if calibration_wire:args.extend(("--crosshair-template",calibration_wire))
        args.append("--predict-lost" if settings.predict_lost else "--no-predict-lost")
        for field in AIM_NATIVE_SETTINGS:
            value = getattr(settings, field)
            wire_value = str(value).lower() if isinstance(value, bool) else str(value)
            args.extend(("--aim-setting", f"{field}={wire_value}"))
        args.append("--occlusion-handling" if settings.occlusion_handling else "--no-occlusion-handling")
        args.append("--manual-target-lock" if settings.manual_target_lock_enabled else "--no-manual-target-lock")
        args.append("--cuda-graph-replay" if settings.cuda_graph_replay else "--no-cuda-graph-replay")
        args.append("--cuda-preprocess" if settings.cuda_preprocess else "--no-cuda-preprocess")
        args.append("--preview" if settings.preview_enabled else "--no-preview")
        args.append("--overlay" if settings.overlay_enabled else "--no-overlay")
        args.append("--overlay-boxes" if settings.overlay_boxes else "--no-overlay-boxes")
        args.append("--overlay-motion" if settings.overlay_motion else "--no-overlay-motion")
        args.append("--overlay-fov" if settings.overlay_fov else "--no-overlay-fov")
        args.append("--overlay-segmentation-masks" if settings.overlay_segmentation_masks else "--no-overlay-segmentation-masks")
        args.append("--nms" if settings.nms_enabled else "--no-nms")
        args.append("--class-agnostic-nms" if settings.nms_class_agnostic else "--class-aware-nms")
        args.append("--event-action" if settings.event_action_enabled and not False else "--no-event-action")
        args.append("--motion-compensation" if settings.motion_compensation_enabled and not False else "--no-motion-compensation")
        args.append("--event-action-requires-activation" if settings.event_action_requires_activation else "--event-action-independent")
        if (settings.desktop_overlay_enabled or api_observations) and not False:
            args.append("--overlay-metadata")
        args.append("--dataset" if settings.dataset_enabled else "--no-dataset")
        args.append("--dataset-capture-confidence" if settings.dataset_capture_confidence_enabled else "--no-dataset-capture-confidence")
        args.append("--dataset-requires-activation" if settings.dataset_capture_requires_activation else "--dataset-anytime")
        args.append("--virtual-controller-manual-priority" if settings.virtual_controller_manual_priority else "--no-virtual-controller-manual-priority")
        if settings.model_path:
            args.extend(("--model", str(resolve_product_path(settings.model_path))))
        if settings.class_filter:
            args.extend(("--class-filter", settings.class_filter))
        args.extend(("--dataset-directory", str(resolve_dataset_root(settings.dataset_directory))))
        if settings.invert_x:
            args.append("--invert-x")
        if settings.invert_y:
            args.append("--invert-y")
        if settings.movement_enabled:
            args.append("--movement")
        if observe_only:
            args.append("--observe-only")
        if manual_lock_event:
            args.extend(("--manual-lock-event", manual_lock_event))
        if controller_host_session is not None:
            args.extend(("--controller-host-pipe", controller_host_session.pipe_name))
            if controller_host_session.virtual_slot is not None:
                args.extend(("--controller-host-virtual-slot", str(controller_host_session.virtual_slot)))
        return args

    def _launch_provider(self) -> None:
        if self.intentional_stop or self.process.state() != QProcess.ProcessState.NotRunning or self._settings is None:
            return
        self._fallback_pending = False
        if self._settings.crosshair_enabled:
            from crosshair_ui import exclude_windows
            if not exclude_windows():
                self.message.emit("Crosshair detection requires capture exclusion for VaporLock windows. Turn it off to use screen centre.")
                self.state_changed.emit("Stopped");self._release_manual_lock_event();self.startup_rejected.emit();return
        while self._provider_index < len(self._providers):
            provider = self._providers[self._provider_index]
            executable, environment = self.launch_for('cpu' if self._cloud_session else provider)
            if executable is None:
                missing = f"The bundled {self.provider_label(provider)} engine is unavailable."
                if self._settings.provider != "auto":
                    self.message.emit(missing)
                    self.state_changed.emit("Stopped")
                    self._release_manual_lock_event()
                    self.startup_rejected.emit()
                    return
                self.message.emit(f"{missing} Trying the next compatible provider.")
                self._provider_index += 1
                continue
            self._reset_output_stream()
            self._active_provider = provider
            self._has_telemetry = False
            self._last_error = ""
            self.state_changed.emit(f"Starting {self.provider_label(provider)} engine")
            self.process.setProcessEnvironment(environment)
            try:
                arguments=self.arguments_for(
                    self._settings,
                    provider,
                    self._manual_lock_event_name,
                    self._controller_host_session,
                    self._observe_only,
                    api_observations=bool(getattr(self,'_api_observations',False)),
                )
            except (ValueError,OSError):
                self.message.emit("Saved execution settings are unavailable. Review CPU placement or select System default before starting.")
                self._active_provider=""
                self.state_changed.emit("Stopped")
                self._release_manual_lock_event()
                self.startup_rejected.emit()
                return
            if self._cloud_session and self._cloud_session.ready:
                arguments += ['--cloud-pipe', self._cloud_session.pipe, '--cloud-worker-pid', str(self._cloud_session.ready['pid'])]
            self.process.start(str(executable),arguments)
            return
        self.message.emit("No compatible bundled inference engine could be started.")
        self.state_changed.emit("Stopped")
        self._release_manual_lock_event()
        self.startup_rejected.emit()

    def _try_fallback(self, reason: str) -> bool:
        if (
            self.intentional_stop
            or self._settings is None
            or self._settings.provider != "auto"
            or self._has_telemetry
            or self._fatal_error
            or self._fallback_pending
            or self._provider_index + 1 >= len(self._providers)
        ):
            return False
        previous = self.provider_label(self._active_provider)
        self._provider_index += 1
        self._fallback_pending = True
        upcoming = self.provider_label(self._providers[self._provider_index])
        detail = f" ({reason})" if reason else ""
        self.message.emit(f"{previous} could not start the selected model{detail}. Trying {upcoming}.")
        generation = self._launch_generation
        QTimer.singleShot(0, lambda: self._launch_provider() if generation == self._launch_generation and not self.intentional_stop else None)
        return True

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_output()
        while self.process.bytesAvailable():
            self._read_output()
        self._reset_output_stream()
        if self._cloud_session:
            self._cloud_session.native_finished()
            return
        if self.intentional_stop:
            self.state_changed.emit("Stopped")
            self._release_manual_lock_event()
            self._controller_host_session = None
            return
        if self._fallback_pending:
            return
        if self._try_fallback(self._last_error or f"exit code {exit_code}"):
            return
        if not self._has_telemetry and self._last_error:
            self.message.emit(self._last_error)
        self.state_changed.emit("Stopped")
        self._release_manual_lock_event()
        self._controller_host_session = None

    def _failed_to_start(self, generation, cloud_session) -> None:
        if generation!=self._launch_generation or cloud_session is not self._cloud_session:
            return
        self._start_failure_scheduled = False
        if self._cloud_session:
            self._cloud_session.stop()
            self._cloud_session.native_finished()
            self.startup_rejected.emit()
            return
        if self.intentional_stop or self.active or self._fallback_pending:
            return
        if not self._try_fallback(self._last_error):
            self.state_changed.emit("Stopped")
            self._release_manual_lock_event()

    def stop(self) -> None:
        self._launch_generation += 1
        self.intentional_stop = True
        self._fallback_pending = False
        self._start_failure_scheduled = False
        self._settings = None
        if self._cloud_session: self._cloud_session.stop()
        if self.active:
            self._reset_output_stream()
            self.state_changed.emit("Stopping")
            self.process.kill()
            self.process.waitForFinished(1500)
        if not self.active:
            self._release_manual_lock_event()
            self._controller_host_session = None

    def _reset_output_stream(self) -> None:
        self._output_lines.reset()
        self._stream_generation += 1
        self._pending_live_generation = None
        self._live_setting_timer.stop()
        self._overlay_gate.reset()
        self._preview_gate.reset()
        self._preview_observed_at = 0.0
        self.preview_invalidated.emit()
        self._visual_epoch = 0
        self._confirmed_visual_epoch = 0
        self.overlay.emit({})
        self._output_failed = False
        self._pending_visuals.clear()
        self._visual_timer.stop()
        self._read_timer.stop()

    def _flush_visuals(self) -> None:
        pending, self._pending_visuals = self._pending_visuals, {}
        if self.intentional_stop or self._output_failed or not self.active:
            return
        # Overlay is independent of JPEG decode and has no preview-rate cap.
        for kind in ('overlay', 'preview'):
            if kind not in pending:
                continue
            generation, payload = pending[kind]
            if generation != self._stream_generation:
                continue
            if self.intentional_stop or self._output_failed or not self.active:
                break
            if kind == "overlay":
                self.overlay.emit(payload)
                continue
            if not self._preview_gate.accept(payload, self._visual_epoch, native_tick_ms(), self._overlay_gate.identity):
                continue
            try:
                width, height = int(payload["width"]), int(payload["height"])
                if width <= 0 or height <= 0:
                    raise ValueError("Invalid dimensions")
                jpeg = base64.b64decode(payload["jpeg"], validate=True)
                self._preview_observed_at = time.monotonic() - native_observation_age_ms(payload, native_tick_ms()) / 1000.0
                from crosshair_reference import remaining_ms
                self._preview_reference_deadline = time.monotonic()+remaining_ms(payload,native_tick_ms())/1000.
                self.preview.emit(jpeg, width, height)
            except (KeyError, ValueError, TypeError):
                self.message.emit("Received an invalid preview frame from the native engine.")

    def _read_output(self) -> None:
        self.process.setReadChannel(QProcess.ProcessChannel.StandardOutput)
        # Bound work per event-loop turn. JPEG decoding and painting happen only
        # for the newest visual record; lifecycle and dataset events stay ordered.
        chunk = bytes(self.process.read(4 * 1024 * 1024))
        if self.intentional_stop:
            return
        for line in self._output_lines.feed(chunk):
            if self.intentional_stop:
                return
            if line is None:
                self.message.emit("Discarded an oversized native engine message.")
                continue
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                text = line[:2048].decode("utf-8", errors="replace")
                self.message.emit(f"Engine message: {text}")
                continue
            if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
                self.message.emit("Received an invalid native engine message.")
                continue
            if payload.get("type") == "stats":
                if self._output_failed:
                    continue
                self._confirm_live_setting(payload.get('control_epoch'), self._stream_generation)
                if not self._has_telemetry:
                    self._has_telemetry = True
                    self.state_changed.emit(f"Running - {self.provider_label(self._active_provider)}")
                    self.message.emit(f"Native engine active: {self.provider_label(self._active_provider)}.")
                self.telemetry.emit(payload)
            elif payload.get("type") in {"preview", "overlay"}:
                if not self._output_failed:
                    if payload['type'] == 'overlay':
                        previous = self._overlay_gate.identity
                        packet_owner=(self._launch_generation,self._stream_generation,self._visual_epoch)
                        accepted=self._overlay_gate.accept(payload, self._visual_epoch, native_tick_ms())
                        # API invalidation/publication precedes display coalescing.
                        self.observation_packet.emit(payload,*packet_owner,accepted)
                        if (not accepted or self.intentional_stop or self._output_failed
                                or packet_owner!=(self._launch_generation,self._stream_generation,self._visual_epoch)):
                            continue
                        current = self._overlay_gate.identity
                        if previous is None or (previous[0], previous[1], previous[3]) != (current[0], current[1], current[3]):
                            self.preview_invalidated.emit()
                        self._confirm_live_setting(payload.get('control_epoch'), self._stream_generation)
                    self._pending_visuals[payload["type"]] = (self._stream_generation, payload)
            elif payload.get('type') == 'setting-rejected':
                if payload.get('code') == 'SETTING_REJECTED':
                    self._reject_live_setting(payload.get('control_epoch'), self._stream_generation)
            elif payload.get("type") == "dataset":
                try:
                    self.dataset.emit({'samples': payload.get('samples'), 'path': payload.get('path')})
                except (ValueError, TypeError, OverflowError):
                    self.message.emit("Received an invalid dataset count from the native engine.")
            elif payload.get("type") in {"error", "output-error"}:
                self._output_failed = True
                self._pending_visuals.clear()
                self._visual_timer.stop()
                self._last_error = str(payload.get("message", "Native engine error"))
                self._fatal_error = payload.get("type") == "output-error"
                self.message.emit(self._last_error)
            elif payload.get("type") in {"output", "notice"}:
                self.message.emit(str(payload.get("message", "Output transport ready.")))
        if self.process.bytesAvailable():
            # A continuous stream must not postpone painting until the pipe is
            # completely empty. Coalesce to the newest parsed visual without
            # imposing a separate 16 ms (62.5 Hz) overlay limit.
            if self._pending_visuals and not self._visual_timer.isActive():
                self._visual_timer.start(0)
            if not self._read_timer.isActive():
                self._read_timer.start(0)
        elif self._pending_visuals:
            self._visual_timer.start(0)

    def _read_errors(self) -> None:
        message = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if message:
            message = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", message).strip()
            if "Some nodes were not assigned to the preferred execution providers" in message:
                self.message.emit("DirectML initialized; ONNX Runtime kept shape-only operations on the CPU.")
            elif message:
                self.message.emit(message)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if self.intentional_stop:
            return
        self._output_failed = True
        self._pending_visuals.clear()
        self._visual_timer.stop()
        self._last_error = f"Native {self.provider_label(self._active_provider)} process error: {error.name}"
        self.message.emit(self._last_error)
        if error == QProcess.ProcessError.FailedToStart and not self._start_failure_scheduled:
            self._start_failure_scheduled = True
            generation=self._launch_generation;cloud_session=self._cloud_session
            QTimer.singleShot(0, lambda:self._failed_to_start(generation,cloud_session))


class HorizontalNavigationScrollArea(QScrollArea):
    """Keep the primary navigation horizontal when a compact window needs it."""

    def wheelEvent(self, event) -> None:
        bar = self.horizontalScrollBar()
        delta = event.angleDelta().x() or event.angleDelta().y()
        if delta and bar.maximum() > bar.minimum():
            bar.setValue(bar.value() - delta)
            event.accept()
            return
        super().wheelEvent(event)


class SecondarySettingsTabs(QFrame):
    """A horizontal secondary workspace bar with independent setting groups."""

    def __init__(self, minimum_height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsTabs")
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._buttons: list[QPushButton] = []
        self._titles: list[str] = []
        from mission_layout import CurrentPageStack
        self._stack = CurrentPageStack()
        self._stack.setObjectName("settingsTabStack")

        from mission_layout import FitVBox
        outer = FitVBox(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        tab_scroll = HorizontalNavigationScrollArea()
        self._tab_scroll = tab_scroll
        tab_scroll.setObjectName("secondaryTabScroll")
        tab_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tab_scroll.setWidgetResizable(True)
        tab_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail = QFrame()
        rail.setObjectName("secondaryTabRail")
        self._rail = rail
        from mission_layout import ActionFlow
        self._rail_layout = ActionFlow(rail)
        self._rail_layout.setContentsMargins(8, 0, 8, 0)
        self._rail_layout.setSpacing(2)
        tab_scroll.setWidget(rail)
        tab_scroll.viewport().installEventFilter(self)
        outer.addWidget(tab_scroll)
        outer.addWidget(self._stack, 1)

    def add_tab(self, title: str, page: QWidget) -> None:
        index = len(self._buttons)
        button = QPushButton(title)
        button.setObjectName("secondaryTabButton")
        button.setCheckable(True)
        button.setToolTip(title)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setMinimumWidth(max(100, button.fontMetrics().horizontalAdvance(title) + 44))
        button.clicked.connect(lambda _checked=False, tab_index=index: self.setCurrentIndex(tab_index))
        self._buttons.append(button)
        self._titles.append(title)
        self._rail_layout.addWidget(button)
        self.refresh_geometry()
        self._stack.addWidget(page)
        if index == 0:
            self.setCurrentIndex(0)

    def refresh_geometry(self) -> None:
        """Wrap complete translated task labels within the available rail."""
        if not self._buttons or getattr(self,'_refreshing_geometry',False):
            return
        self._refreshing_geometry=True
        try:
            self._rail.ensurePolished()
            for button in self._buttons:
                button.ensurePolished()
                button.setMinimumWidth(0)
                button.setMinimumWidth(max(100, button.sizeHint().width()))
                button.setToolTip(button.text())
                button.updateGeometry()
            width = max(1, self._tab_scroll.viewport().width())
            border_height = self._rail.height() - self._rail.contentsRect().height()
            self._rail_layout.invalidate()
            height = max(47, self._rail_layout.heightForWidth(width) + border_height)
            self._rail.setFixedHeight(height)
            self._tab_scroll.setFixedHeight(height + 2 * self._tab_scroll.frameWidth())
            self._rail_layout.activate()
        finally:
            self._refreshing_geometry=False

    def eventFilter(self, watched, event):
        if watched is self._tab_scroll.viewport() and event.type() == QEvent.Type.Resize:
            self.refresh_geometry()
        return super().eventFilter(watched, event)

    def currentIndex(self) -> int:
        return self._stack.currentIndex()

    def index_of(self, title: str) -> int:
        try:
            return self._titles.index(title)
        except ValueError:
            return -1

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < self._stack.count():
            return
        self._stack.setCurrentIndex(index)
        self._stack.updateGeometry()
        for button_index, button in enumerate(self._buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            button.blockSignals(False)
        self.refresh_geometry()


class WorkspaceBackdrop(QWidget):
    """Keep the workspace quiet so live imagery remains the visual focus."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._background = QPixmap(str(product_background_path()))
        self._layout_mode = "smoky"
        self._carbon = QColor(DEFAULT_UI_COLORS["carbon"])
        self._carbon_opacity = DEFAULT_UI_OPACITIES["carbon"]

    def set_appearance(self, appearance: UiAppearance, custom_palette: bool) -> None:
        if appearance.layout != self._layout_mode:
            assets = Path(sys._MEIPASS) / "assets" if getattr(sys, "frozen", False) else ROOT / "assets"
            background = product_background_path() if appearance.layout == "smoky" else assets / "themes" / f"{appearance.layout}.svg"
            self._background = QPixmap(str(background))
        self._layout_mode = appearance.layout
        palette = appearance_palette(appearance, custom_palette)
        opacities = appearance.opacities if custom_palette else DEFAULT_UI_OPACITIES
        self._carbon = QColor(normalized_ui_color(palette.get("carbon"), DEFAULT_UI_COLORS["carbon"]))
        self._carbon_opacity = normalized_ui_opacity(opacities.get("carbon"), DEFAULT_UI_OPACITIES["carbon"])
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        theme = theme_for(self._layout_mode)
        if not self._background.isNull():
            scaled = self._background.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            source_x = max(0, (scaled.width() - self.width()) // 2)
            source_y = max(0, (scaled.height() - self.height()) // 2)
            painter.drawPixmap(self.rect(), scaled, QRect(source_x, source_y, self.width(), self.height()))
        else:
            painter.fillRect(self.rect(), QColor(theme.canvas))
        # A static smoked film keeps text and controls readable without a blur
        # effect or a flat, opaque workspace.
        film = QColor(self._carbon)
        base_alpha = 91 if theme.atmospheric else 0
        film.setAlpha(round(base_alpha * self._carbon_opacity / 100.0))
        painter.fillRect(self.rect(), film)
        painter.end()
        super().paintEvent(event)


PAGE_TITLES = {
    "interface": "Interface & Language",
    "misc": "Misc",
    "games": "Games",
    "overview": "Overview",
    "capture": "Capture",
    "model": "Model Library",
    "detection": "Detection",
    "selection": "Targeting",
    "output": "Movement",
    "dataset": "Training Data",
    "profiles": "Profiles",
    "shortcuts": "Shortcuts",
    "automation": "API Suite",
    "diagnostics": "Status & Diagnostics",
}


class ControlCenter(QMainWindow):
    # HTTP worker threads never touch Qt widgets directly. Commands cross this
    # signal before changing capture state or settings on the UI thread.
    local_api_command = Signal(dict)

    def __init__(self, *, live_resources=None) -> None:
        super().__init__()
        from control_fit import install_control_fit
        install_control_fit()
        if _NATIVE_INJECTION is not None:
            from vlui_denied_services import DeniedNativeServices
            if type(_NATIVE_INJECTION.services) is not DeniedNativeServices:
                raise ValueError('NATIVE_SERVICE_FACTORY_REJECTED')
        self._live_owner = None
        self._live_coordinator = None
        self._live_close = None
        self._native_closing = False
        self._profile_transition_pending = False
        self.settings = Settings()
        self._tuning_lock = TuningLock(self)
        self._policy_start_pending = False
        self.ui_appearance = load_ui_appearance()
        self.ui_language = load_preferred_language()
        self.interface_translator = InterfaceTranslator()
        self._undo_history = []
        self._undo_in_progress = False
        self._last_edit = (None, 0.0)
        self._profile_change_suffix: str | None = None
        self._last_engine_state = "READY"
        # Session mode is intentionally not stored in profiles. A profile can
        # describe output tuning, but each launch still makes the operator's
        # current Observe/Control intent explicit in the command bar.
        self.session_mode = "control"
        self.performance_history = PerformanceHistory()
        self.session_report_cache = SessionReportCache()
        self._pipeline_timer = QTimer(self)
        self._pipeline_timer.setInterval(250)
        self._pipeline_timer.timeout.connect(self.refresh_pipeline_diagnostics)
        self._pipeline_timer.start()
        self._last_output_data = None
        self.dirty = False
        self.pages: dict[str, QWidget] = {}
        self.nav: dict[str, QPushButton] = {}
        self.current_page = "games"
        self._model_selection = None
        self._pipeline_received_at = 0.0
        self._model_probes = []
        self._probe_revision = 0
        self._benchmark_active = False
        self._benchmark_queue: list[str] = []
        self._benchmark_results: list[dict[str, float | str]] = []
        self._benchmark_settings: Settings | None = None
        self._benchmark_process: QProcess | None = None
        self._benchmark_timer: QTimer | None = None
        self._validation_active = False
        self._validation_process: QProcess | None = None
        self._validation_timer: QTimer | None = None
        self._active_profile_path: Path | None = None
        self._last_selected_track = 0
        self._last_manual_lock = False
        self._motion_compensation_presets: list[MotionCompensationPreset] = []
        self._calibration_active = False
        self._calibration_samples: list[tuple[float, float]] = []
        self._calibration_last_inference = 0
        self._calibration_deadline = 0.0
        self._calibration_original: dict[str, object] = {}
        self._calibration_dialog: QDialog | None = None
        self._calibration_status: QLabel | None = None
        self._calibration_name: QLineEdit | None = None
        self._calibration_identifier = ""
        self._calibration_timer: QTimer | None = None
        self._pending_controller_start: Settings | None = None
        self._registered_hotkey_ids: set[int] = set()
        self._hotkey_actions = {
            1: self.hotkey_toggle_capture,
            2: self.hotkey_toggle_overlay,
            3: self.hotkey_emergency_stop,
            4: self.hotkey_next_profile,
            5: self.hotkey_toggle_target_lock,
            6: self.hotkey_save_screenshot,
            7: lambda: self.hotkey_load_profile(1),
            8: lambda: self.hotkey_load_profile(2),
            9: lambda: self.hotkey_load_profile(3),
            10: self.open_setup_and_calibration,
        }
        self.setWindowTitle("Vapor Lock · Control Center")
        icon_path = product_icon_path()
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        work_area=self.screen().availableGeometry()
        self.resize(max(640,work_area.width()-16),max(480,work_area.height()-48))
        self.setMinimumSize(min(980,work_area.width()-16),min(660,work_area.height()-48))
        # Responsive child layouts must not leave QMainWindow with the former
        # landscape minimum after the chrome reflows for portrait/DPI changes.
        self.layout().setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        self.setStyleSheet(ui_stylesheet(self.ui_appearance, True))
        services = _NATIVE_INJECTION.services if _NATIVE_INJECTION is not None else None
        self.engine = services.engine(self) if services is not None else EngineBridge(self)
        self.engine_capabilities = services.capabilities(self) if services is not None else EngineCapabilities(self)
        self.engine_capabilities.changed.connect(self.refresh_cuda_preprocess_controls)
        self.acceleration = services.acceleration(self) if services is not None else AccelerationManager(self)
        self.controller_bridge = services.controller(self) if services is not None else ControllerBridgeManager(self)
        self.xbox_compatible_bridge = services.xbox(self) if services is not None else XboxCompatibleBridgeManager(self)
        self.desktop_overlay = services.overlay(self) if services is not None else DesktopOverlay()
        self._last_preview_frame = QImage()
        self._preview_observed_at = 0.0
        self._preview_stale = False
        self._preview_expiry_timer = QTimer(self)
        self._preview_expiry_timer.setSingleShot(True)
        self._preview_expiry_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._preview_expiry_timer.timeout.connect(self.refresh_preview_status)
        self.local_api = (services.api(self) if services is not None else
                          LocalControlApi(lambda command: self.local_api_command.emit(
                              self.stamp_local_api_command(command))))
        self.engine.telemetry.connect(self.apply_telemetry)
        self.engine.preview.connect(self.apply_preview)
        self.engine.preview_invalidated.connect(self.clear_preview)
        self.engine.overlay.connect(self.apply_desktop_overlay)
        self.engine.observation_packet.connect(self.apply_observations)
        self.engine.dataset.connect(self.apply_dataset)
        self.engine.message.connect(lambda text: self.log("INFO", text))
        self.engine.state_changed.connect(self.apply_state)
        self.engine.startup_rejected.connect(self.show_model_start_failure)
        self.engine.live_setting_result.connect(self.apply_live_setting_result)
        self.local_api_command.connect(self.apply_local_api_command)
        self.acceleration.status.connect(self.apply_acceleration_status)
        self.acceleration.completed.connect(self.acceleration_completed)
        self.controller_bridge.status.connect(self.apply_controller_bridge_status)
        self.controller_bridge.completed.connect(self.controller_bridge_completed)
        self.xbox_compatible_bridge.status.connect(self.apply_xbox_compatible_bridge_status)
        self.xbox_compatible_bridge.completed.connect(self.xbox_compatible_bridge_completed)
        self.xbox_compatible_bridge.session_ready.connect(self.xbox_compatible_bridge_ready)
        self.build_shell()
        self.apply_ui_appearance(save=False)
        self.build_pages()
        self.capture_interface_copy(reset=True)
        self.apply_interface_language()
        self.load_startup_profile()
        from crosshair_ui import install_window_guard
        install_window_guard(self)
        self.sync_local_api()
        self.refresh_local_api_snapshot()
        self.show_page("games")
        self.log("INFO", "Qt control center ready. Native engine is idle.")
        if _NATIVE_INJECTION is None:
            QTimer.singleShot(0, self.register_global_hotkeys)
        # Offscreen automation exercises the wizard directly; opening a modal
        # first-run surface from an unattended test window would deadlock CI.
        if _NATIVE_INJECTION is None and QApplication.platformName().casefold() != "offscreen":
            self._initialization_timer = QTimer(self)
            self._initialization_timer.setSingleShot(True)
            self._initialization_timer.timeout.connect(self.offer_initialization)
            self._initialization_timer.start(250)
        if live_resources is not None:
            from vlui_native_owner import NativeLiveOwner
            from vlui_native_shell import NativeLiveCoordinator
            self._live_owner = NativeLiveOwner(self)
            self._live_coordinator = NativeLiveCoordinator(self, self._live_owner, live_resources)
            from vlui_native_close import NativeCloseOwner
            self._live_close = NativeCloseOwner(self)

    def build_shell(self) -> None:
        # One continuous Vapor Lock image sits behind the entire command deck.
        # The command bar, navigation, and workspace each carry only a smoked
        # glass layer so the product feels like a single control surface.
        root = WorkspaceBackdrop()
        root.setObjectName("root")
        self.workspace_root = root
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        top = QFrame()
        top.setObjectName("commandBar")
        top.setFixedHeight(72)
        self.command_bar = top
        from mission_layout import MeasuredHeaderLayout
        top_layout = MeasuredHeaderLayout(top)
        top_layout.setContentsMargins(24, 10, 24, 10)
        top_layout.setSpacing(11)
        context_box = QVBoxLayout()
        context_box.setSpacing(0)
        context_kicker = QLabel("WORKSPACE")
        context_kicker.setObjectName("headerKicker")
        self.context_kicker = context_kicker
        self.header_page = QLabel("Control Center")
        self.header_page.setObjectName("headerPage")
        self.profile_summary = QLabel("Default profile")
        self.profile_summary.setObjectName("headerProfile")
        self.profile_summary.hide()
        from mission_layout import ElidedLabel
        self.header_model = ElidedLabel()
        self.header_model.setObjectName("headerProfile")
        self.header_model.setTextFormat(Qt.TextFormat.PlainText)
        self.header_model.setProperty("_vaporlock_i18n_skip", True)
        self.header_model.setMaximumWidth(190)
        context_box.addWidget(context_kicker)
        context_box.addWidget(self.header_page)
        context_box.addWidget(self.header_model)
        top_layout.addLayout(context_box, 1)
        quick_profile = QVBoxLayout()
        quick_profile.setSpacing(1)
        quick_profile_label = QLabel("QUICK PROFILE")
        quick_profile_label.setObjectName("headerKicker")
        self.quick_profile_label = quick_profile_label
        self.profile_quick_switch = QComboBox()
        self.profile_quick_switch.setObjectName("profileQuickSwitch")
        self.profile_quick_switch.setProperty("_vaporlock_i18n_skip", True)
        self.profile_quick_switch.setToolTip("Quickly load a saved profile from any page.")
        self.profile_quick_switch.currentIndexChanged.connect(self.quick_switch_profile)
        quick_profile.addWidget(quick_profile_label)
        quick_profile.addWidget(self.profile_quick_switch)
        top_layout.addLayout(quick_profile)
        quick_model=QVBoxLayout()
        quick_model.setSpacing(1)
        self.quick_model_label=QLabel('MODEL')
        self.quick_model_label.setObjectName('headerKicker')
        self.model_quick_switch=QComboBox()
        self.model_quick_switch.setAccessibleName('Detection model')
        self.model_quick_switch.setProperty('_vaporlock_i18n_skip',True)
        self.model_quick_switch.currentIndexChanged.connect(self._select_header_model)
        quick_model.addWidget(self.quick_model_label)
        quick_model.addWidget(self._tuning_lock.wrap(self.model_quick_switch))
        top_layout.addLayout(quick_model)
        self.settings_search_button = QToolButton()
        self.settings_search_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
        self.settings_search_button.setToolTip("Search settings (Ctrl+K)")
        self.settings_search_button.setAccessibleName("Search settings")
        self.settings_search_button.clicked.connect(self.open_settings_search)
        top_layout.addWidget(self.settings_search_button)
        self.settings_search_action = QAction(self)
        self.settings_search_action.setShortcut(QKeySequence("Ctrl+K"))
        self.settings_search_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.settings_search_action.triggered.connect(self.open_settings_search)
        self.addAction(self.settings_search_action)
        session_mode = QVBoxLayout()
        session_mode.setSpacing(1)
        session_mode_label = QLabel("SESSION MODE")
        session_mode_label.setObjectName("headerKicker")
        self.session_mode_label = session_mode_label
        self.session_mode_choice = QComboBox()
        self.session_mode_choice.setObjectName("sessionModeChoice")
        self.session_mode_choice.addItem("Control", "control")
        self.session_mode_choice.addItem("Observe only", "observe")
        self.session_mode_choice.setToolTip(
            "Control permits the configured output. Observe only runs capture, detection, tracking, previews, and recording without movement, clicks, or compensation."
        )
        self.session_mode_choice.currentIndexChanged.connect(self.change_session_mode)
        session_mode.addWidget(session_mode_label)
        session_mode.addWidget(self.session_mode_choice)
        top_layout.addLayout(session_mode)
        self.status = QLabel("READY")
        self.status.setObjectName("statusPill")
        self.status.setProperty("state", "ready")
        top_layout.addWidget(self.status)
        self.undo_button = QToolButton()
        self.undo_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.undo_button.setToolTip("Undo last tuning change")
        self.undo_button.setAccessibleName("Undo last tuning change")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo_tuning)
        top_layout.addWidget(self.undo_button)
        self.save_button = QToolButton()
        self.save_button.setObjectName('saveProfileCommand')
        self.save_button.setStyleSheet('QToolButton#saveProfileCommand {min-width:32px; max-width:16777215px; padding:4px 12px;}')
        self.save_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.save_button.setToolTip("Save profile")
        self.save_button.setAccessibleName("Save profile")
        self.save_button.setText("Save profile")
        self.save_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.save_button.clicked.connect(self.save_profile)
        top_layout.addWidget(self.save_button)
        top_layout.addWidget(self._tuning_lock.button())
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.start_button.setMinimumWidth(140)
        self.start_button.setToolTip("Start capture")
        self.start_button.clicked.connect(self.start_capture)
        top_layout.addWidget(self.start_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.clicked.connect(self.stop_capture)
        self.stop_button.setEnabled(False)
        top_layout.addWidget(self.stop_button)
        layout.addWidget(top)
        shell = QFrame()
        shell.setObjectName("workspaceShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.navigation_deck = self.make_navigation_deck()
        shell_layout.addWidget(self.navigation_deck)
        workspace = QWidget()
        workspace.setObjectName("workspaceBackdrop")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        self.stack = QStackedWidget()
        workspace_layout.addWidget(self.stack)
        shell_layout.addWidget(workspace, 1)
        layout.addWidget(shell, 1)
        layout.addWidget(self.make_telemetry_strip())
        self.refresh_profile_quick_switch()
        self.update_header_density()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "command_bar"):
            self.update_header_density()
        if hasattr(self, "session_cockpit_grid") and hasattr(self, "session_cockpit_cards"):
            self.update_overview_grid()
        if hasattr(self, "policy_reason_scroll"):
            self.size_policy_reason()
        self.update_targeting_layout()
        self.update_telemetry_layout()

    def queue_telemetry_layout(self) -> None:
        """Fit once after a state transition has updated its wrapped labels."""
        if getattr(self, '_telemetry_fit_pending', False):
            return
        self._telemetry_fit_pending = True
        QTimer.singleShot(0, self.fit_telemetry_after_state)

    def fit_telemetry_after_state(self) -> None:
        self._telemetry_fit_pending = False
        if not self._native_closing:
            self.update_telemetry_layout()

    def update_telemetry_layout(self) -> None:
        if not hasattr(self, 'telemetry_scroll'):
            return
        scale = float(getattr(self._live_owner, '_scale', '1'))
        available = max(1,self.telemetry_scroll.viewport().width())
        self.telemetry_strip.ensurePolished()
        # Wrapped captions may use their readable minimum. Preferred widths
        # otherwise force translated metrics into a needlessly tall single column.
        widths=[max(widget.minimumWidth(),widget.minimumSizeHint().width()) for widget in self.telemetry_widgets]
        gap=self.telemetry_strip.layout().spacing()
        margins=self.telemetry_strip.layout().contentsMargins()
        columns=1
        for candidate in range(5,1,-1):
            needed=sum(max(widths[column::candidate],default=0) for column in range(candidate))
            if needed+gap*(candidate-1)+margins.left()+margins.right()+4<=available:
                columns=candidate
                break
        grid = self.telemetry_strip.layout()
        if getattr(self, '_telemetry_columns', None) != columns:
            for widget in self.telemetry_widgets:
                grid.removeWidget(widget)
            for index, widget in enumerate(self.telemetry_widgets):
                grid.addWidget(widget, index // columns, index % columns)
            for column in range(6):
                grid.setColumnStretch(column, 1 if column < columns else 0)
            self._telemetry_columns = columns
        self.telemetry_strip.ensurePolished()
        self.telemetry_strip.setMinimumHeight(0)
        self.telemetry_strip.setMinimumWidth(0)
        grid.invalidate()
        grid.activate()
        height = max(30, self.telemetry_strip.minimumSizeHint().height() + 6,
                     grid.heightForWidth(max(100, available - 2)) + 6)
        self.telemetry_strip.setMinimumHeight(height)
        overflow = self.telemetry_strip.minimumSizeHint().width() > available
        self.telemetry_scroll.setFixedHeight(height + (self.telemetry_scroll.horizontalScrollBar().sizeHint().height() if overflow else 0))

    def update_targeting_layout(self) -> None:
        if not hasattr(self, "policy_deck_layout"):
            return
        scale = float(getattr(self._live_owner, '_scale', '1'))
        stacked = self.width() - self.navigation_deck.width() < round(1000 * scale)
        rules_active=(getattr(self,'current_page','')=='selection' and hasattr(self,'targeting_tabs')
                      and self.targeting_tabs.currentIndex()==self.targeting_tabs.index_of('Class rules'))
        self.policy_deck_layout.setDirection(QHBoxLayout.Direction.TopToBottom if stacked else QHBoxLayout.Direction.LeftToRight)
        self.policy_inspector.setMinimumWidth(0 if stacked else round(320 * scale))
        self.policy_inspector.setMaximumWidth(16777215)
        self.policy_inspector.setMinimumHeight(round((260 if rules_active else 440) * scale) if stacked else 0)
        if hasattr(self,'policy_preview_view'):
            self.policy_preview_view.setMinimumHeight(round((150 if rules_active and stacked else 230)*scale))
            self._shared_preview_shell.setMaximumHeight(round(230*scale) if rules_active and stacked else 16777215)
        main=self.policy_deck_layout.itemAt(0).widget()
        main_width=max(0,main.contentsRect().width()-40)
        headline_needed=sum(self.policy_headline_layout.itemAt(i).minimumSize().width()
                            for i in range(self.policy_headline_layout.count()))+self.policy_headline_layout.spacing()
        headline_stacked=stacked and (not rules_active or main_width<headline_needed)
        self.policy_headline_layout.setDirection(QHBoxLayout.Direction.TopToBottom if headline_stacked else QHBoxLayout.Direction.LeftToRight)
        radii_needed=sum(card.minimumSizeHint().width() for card in self.policy_radius_cards)+3*self.policy_radii_layout.horizontalSpacing()
        columns = 2 if stacked and (not rules_active or main_width<radii_needed) else 4
        if getattr(self, '_policy_metric_columns', None) != columns:
            for card in self.policy_radius_cards:
                self.policy_radii_layout.removeWidget(card)
            for index, card in enumerate(self.policy_radius_cards):
                self.policy_radii_layout.addWidget(card, index // columns, index % columns)
            for column in range(4):
                self.policy_radii_layout.setColumnStretch(column, 1 if column < columns else 0)
            self._policy_metric_columns = columns

    def size_policy_reason(self) -> None:
        width=max(200,self.policy_reason_scroll.viewport().width())
        self.policy_text.setMinimumHeight(0)
        self.policy_text.setMaximumHeight(16777215)
        height=max(self.policy_text.heightForWidth(width),self.policy_text.fontMetrics().boundingRect(QRect(0,0,width,100000),int(Qt.TextFlag.TextWordWrap),self.policy_text.text()).height())+2*self.policy_text.fontMetrics().lineSpacing()+8
        self.policy_text.setFixedHeight(max(20,height))
        self.policy_reason_scroll.setFixedHeight(min(150,max(30,height+6)))

    def update_header_density(self) -> None:
        compact = self.width() < 1160
        self.save_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if compact else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.save_button.setText(self.t('Save profile'))
        if hasattr(self, 'navigation_deck'):
            factor = float(getattr(self._live_owner, '_scale', '1'))
            base = 220 if self.ui_appearance.layout=='mission-control' else 210 if theme_for(self.ui_appearance.layout).high_contrast else 196
            self.navigation_deck.setFixedWidth(min(round(base * factor), max(base, round(self.width() * .3))))
        if hasattr(self, 'guide_link'):
            title = self.t('Settings guide')
            width = max(80, self.navigation_deck.width() - 40 - self.guide_link.iconSize().width())
            multiline = self.guide_link.fontMetrics().horizontalAdvance(title) > width and ' ' in title
            self.guide_link.setText(title.replace(' ', '\n', 1) if multiline else title)
            self.guide_link.setAccessibleName(title)
            self.guide_link.setMinimumHeight(max(36, self.guide_link.fontMetrics().lineSpacing() * (2 if multiline else 1) + 10))
        for label in (self.context_kicker, self.quick_profile_label, self.session_mode_label, self.quick_model_label):
            label.setVisible(not compact or self.ui_appearance.layout=='mission-control')
        self.command_bar.layout().setContentsMargins(*(14, 10, 14, 10) if compact else (24, 10, 24, 10))
        self.command_bar.layout().setSpacing(8 if compact else 11)
        if hasattr(self, "overview_release"):
            self.overview_release.setVisible(False)
        coordinator = getattr(self, '_live_coordinator', None)
        if coordinator is not None:
            coordinator._size_command_row()

    def update_overview_grid(self) -> None:
        columns = 1 if self.width() < 1100 else 3
        grid = self.session_cockpit_grid
        if getattr(self, "_overview_grid_columns", None) == columns and grid.count() == len(self.session_cockpit_cards):
            return
        for card in self.session_cockpit_cards:
            grid.removeWidget(card)
        for index, card in enumerate(self.session_cockpit_cards):
            grid.addWidget(card, index // columns, index % columns)
        for column in range(4):
            grid.setColumnStretch(column, 1 if column < columns else 0)
        self._overview_grid_columns = columns

    def make_navigation_deck(self) -> QWidget:
        deck = QFrame()
        deck.setObjectName("navigationDeck")
        deck.setFixedWidth(196)
        deck_layout = QVBoxLayout(deck)
        deck_layout.setContentsMargins(0, 0, 0, 0)
        deck_layout.setSpacing(0)
        identity = QWidget()
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(16, 16, 16, 10)
        identity_layout.setSpacing(6)
        logo = QLabel()
        logo.setObjectName("navigationLogo")
        logo.setAccessibleName("VaporLock")
        logo.setProperty("_vaporlock_i18n_skip", True)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        assets = Path(sys._MEIPASS) / "assets" if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1] / "assets"
        logo.setPixmap(QPixmap(str(assets / "VaporLock.png")).scaled(
            104, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        identity_layout.addWidget(logo)
        deck_layout.addWidget(identity)
        scroll = QScrollArea()
        scroll.setObjectName("navigationScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        content.setObjectName("navigationContent")
        content.setMinimumWidth(0)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 14, 8, 14)
        layout.setSpacing(2)
        for group, items in (
            ("LIBRARY", (("games", "Games"), ("profiles", "Profiles"), ("model", "Model Library"))),
            ("WORKSPACE", (("overview", "Overview"), ("detection", "Detection"), ("selection", "Targeting"), ("output", "Movement"))),
            ("TOOLS", (("dataset", "Training Data"), ("shortcuts", "Shortcuts"), ("automation", "API Suite"), ("diagnostics", "Status & Diagnostics"), ("misc", "Misc"))),
        ):
            label = QLabel(group)
            label.setObjectName("navGroup")
            layout.addWidget(label)
            for key, title in items:
                button = QPushButton(title)
                button.setCheckable(True)
                button.setFlat(True)
                button.setObjectName("navButton")
                button.setIcon(self.navigation_icon(key))
                button.setToolTip(title)
                button.setMinimumHeight(36)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                button.clicked.connect(lambda _checked=False, page=key: self.show_page(page))
                self.nav[key] = button
                layout.addWidget(button)
            if group != "TOOLS":
                layout.addSpacing(7)
        layout.addStretch(1)
        scroll.setWidget(content)
        deck_layout.addWidget(scroll)
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(8, 8, 8, 14)
        guide = QPushButton("Settings guide")
        guide.setObjectName("guideLink")
        guide.setFlat(True)
        guide.setMinimumHeight(36)
        guide.clicked.connect(lambda: self.open_settings_guide())
        guide.setToolTip("Open the bundled guide in your browser.")
        self.guide_link = guide
        guide.setProperty("missionIcon", "book")
        footer_layout.addWidget(guide)
        for title, attribute in (("Appearance", "interface_layout_choice"), ("Language", "language_choice")):
            button = QPushButton(title)
            button.setObjectName("navButton")
            button.setMinimumHeight(36)
            button.setProperty("missionIcon", "display" if title == "Appearance" else "globe")
            button.clicked.connect(lambda _checked=False, name=attribute: self.reveal_setting("interface", getattr(self, name)))
            footer_layout.addWidget(button)
        deck_layout.addWidget(footer)
        return deck

    def make_telemetry_strip(self) -> QWidget:
        strip = QFrame()
        self.telemetry_strip = strip
        strip.setObjectName("telemetryStrip")
        layout = QGridLayout(strip)
        layout.setSizeConstraint(QGridLayout.SizeConstraint.SetNoConstraint)
        self.telemetry_widgets = []
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        def add_metric(caption: str, value: str) -> QLabel:
            group = QWidget()
            group_layout = QHBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(6)
            label = QLabel(caption)
            label.setObjectName("hudLabel")
            label.setWordWrap(True)
            metric = QLabel(value)
            metric.setObjectName("hudValue")
            metric.setWordWrap(True)
            group_layout.addWidget(label)
            group_layout.addWidget(metric)
            layout.addWidget(group, 0, len(self.telemetry_widgets))
            self.telemetry_widgets.append(group)
            return metric

        self.hud_state = add_metric("Session state", self.t('Stopped'))
        self.hud_state.setObjectName('missionFooterState')
        self.hud_output = add_metric("Output state", self.t('Idle'))
        self.hud_output.setObjectName('missionFooterState')
        self.hud_rate = add_metric("RATE", "--")
        self.hud_inference = add_metric("INFERENCE", "--")
        self.hud_tracks = add_metric("TRACKS", "0")
        community = QLabel('<a style="color:#7dbcf5" href="https://discord.gg/eWVvtuRz4T">COMMUNITY</a>')
        community.setObjectName("hudLabel")
        community.setProperty("_vaporlock_i18n_skip", True)
        community.setOpenExternalLinks(_NATIVE_INJECTION is None)
        community.setEnabled(_NATIVE_INJECTION is None)
        self.community_link = community
        layout.addWidget(community, 0, len(self.telemetry_widgets))
        self.telemetry_widgets.append(community)
        edition = QLabel(PRODUCT_VERSION)
        edition.setObjectName("hudLabel")
        edition.setProperty("_vaporlock_i18n_skip", True)
        self.edition_summary = edition
        edition.setWordWrap(True)
        layout.addWidget(edition, 0, len(self.telemetry_widgets))
        self.telemetry_widgets.append(edition)
        for title,symbol,callback in (('Settings guide','book',self.open_settings_guide),('Diagnostics','pulse',lambda:self.show_page('diagnostics'))):
            link=QPushButton(title);link.setProperty('missionIcon',symbol);link.clicked.connect(callback)
            layout.addWidget(link,0,len(self.telemetry_widgets));self.telemetry_widgets.append(link)
        scroll = self.telemetry_scroll = QScrollArea()
        scroll.setObjectName('telemetryScroll')
        scroll.setAccessibleName('Session status')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        scroll.setWidget(strip)
        scroll.viewport().installEventFilter(self)
        strip.setAutoFillBackground(False)
        self.update_telemetry_layout()
        return scroll

    def navigation_icon(self, page: str):
        from mission_icons import icon, NAVIGATION
        palette = appearance_palette(self.ui_appearance, True)
        return icon(NAVIGATION[page], palette['ice'], palette['baby'], palette['steel'])

    def new_page(self, title: str, subtitle: str, include_heading: bool = True) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("pageContent")
        content.setMinimumWidth(0)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(*(22, 18, 22, 25) if self.ui_appearance.layout == "contrast" else (27, 12, 27, 12))
        outer.setSpacing(0)
        from mission_layout import FitColumn
        column = FitColumn()
        column.setObjectName("pageColumn")
        column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        from mission_layout import FitVBox
        layout = FitVBox(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        outer.addWidget(column)
        # The selected navigation identifies this workspace. Retain its name
        # for accessibility without a second visible title/introduction stack.
        scroll.setAccessibleName(self.t(title))
        scroll.setAccessibleDescription(self.t(subtitle))
        content.setAccessibleName(self.t(title))
        scroll.setWidget(content)
        return scroll, layout

    def section(self, layout: QVBoxLayout, title: str) -> None:
        header = QFrame()
        header.setObjectName("sectionHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 15, 0, 6)
        header_layout.setSpacing(9)
        dot = QFrame()
        dot.setObjectName("sectionDot")
        dot.setFixedSize(3, 12)
        label = QLabel(title.upper())
        label.setObjectName("sectionLabel")
        rule = QFrame()
        rule.setObjectName("sectionRule")
        rule.setFrameShape(QFrame.Shape.HLine)
        header_layout.addWidget(dot)
        header_layout.addWidget(label)
        header_layout.addWidget(rule, 1)
        layout.addWidget(header)

    @staticmethod
    def feature_control(control: QWidget) -> QWidget:
        """Retain the common setting-layout wrapper without edition restrictions."""
        return control

    def setting(self, layout: QVBoxLayout, title: str, detail: str, control: QWidget) -> None:
        from mission_layout import SettingCard
        card = SettingCard()
        card.setObjectName("settingsCard")
        card.setToolTip(detail)
        card.setMinimumHeight(56)
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 8, 14, 8)
        row.setSpacing(13)
        accent = QFrame()
        accent.setObjectName("settingAccent")
        accent.setMinimumHeight(28)
        row.addWidget(accent)
        text = QVBoxLayout()
        text.setSpacing(2)
        heading = QLabel(title)
        heading.setObjectName("fieldTitle")
        heading.setWordWrap(True)
        heading.setToolTip(detail)
        heading_row = QHBoxLayout()
        heading_row.addWidget(heading)
        add_guide_button(self, heading_row, title, control)
        heading_row.addStretch(1)
        text.addLayout(heading_row)
        row.addLayout(text, 1)
        if not control.toolTip():
            control.setToolTip(detail)
        if isinstance(control,QCheckBox) and control.text() in ('Enabled','Visible','Show optional update notices','Automatically install verified updates'):
            control.setProperty('missionSwitch',True)
            if control.text()=='Enabled':
                control.setProperty('missionStateCaption',True)
                control.setProperty('_vaporlock_i18n_skip',True)
                control.setText(self.t('On' if control.isChecked() else 'Off'))
                control.toggled.connect(lambda checked,widget=control:widget.setText(self.t('On' if checked else 'Off')))
        control.setMinimumWidth(250)
        card.setMinimumHeight(max(56, control.minimumHeight() + 16))
        row.addWidget(self._tuning_lock.wrap(control), 2)
        layout.addWidget(card)

    def settings_tabs(
        self,
        layout: QVBoxLayout,
        *titles: str,
        minimum_height: int = 460,
        owner_attribute: str | None = None,
        fixed_titles: tuple[str, ...] = (),
    ) -> dict[str, QVBoxLayout]:
        """Build left-rail, independently scrollable settings groups."""
        tabs = SecondarySettingsTabs(minimum_height)
        if owner_attribute:
            setattr(self, owner_attribute, tabs)
        pages: dict[str, QVBoxLayout] = {}
        for title in titles:
            from mission_layout import InventoryScroll
            scroll = InventoryScroll()
            scroll.setObjectName("settingsTabScroll")
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            from mission_layout import FitColumn
            content = FitColumn()
            content.setObjectName("settingsTabContent")
            from mission_layout import FitVBox
            inner = FitVBox(content)
            inner.setContentsMargins(17, 12, 17, 16)
            inner.setSpacing(4)
            if title in fixed_titles:
                scroll.deleteLater()
                tabs.add_tab(title, content)
            else:
                scroll.setWidget(content)
                tabs.add_tab(title, scroll)
            pages[title] = inner
        layout.addWidget(tabs, 1)
        return pages

    def metric_card(self, caption: str, symbol=None) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 13, 15, 13)
        value = QLabel("—")
        value.setObjectName("metricValue")
        label = QLabel(caption)
        label.setObjectName("metricCaption")
        label.setWordWrap(True)
        if symbol:
            heading=QHBoxLayout()
            glyph=QLabel();glyph.setProperty('missionGlyph',symbol);glyph.setFixedSize(30,30)
            heading.addWidget(glyph);heading.addWidget(label,1);layout.addLayout(heading)
        else:
            layout.addWidget(label)
        layout.addWidget(value)
        return card, value

    def t(self, source: str) -> str:
        return translate_text(source, self.ui_language)

    def tf(self, source: str, **values: object) -> str:
        return format_text(source, self.ui_language, **values)

    def localized_engine_state(self, state: str) -> str:
        if state == "READY":
            return self.t("Ready")
        if state.startswith("Running - "):
            return f"{self.t('Running')} - {state.split(' - ', 1)[1]}"
        if state.startswith("Starting "):
            return f"{self.t('Starting')} {state[len('Starting '):]}"
        return self.t(state)

    def update_profile_summary(self) -> None:
        cloud_suite=getattr(self,'_cloud_suite',None)
        if cloud_suite is not None:cloud_suite.invalidate_recipe_if_changed()
        if self._profile_change_suffix:
            self.profile_summary.setText(f"{self.settings.profile_name} · {self.t(self._profile_change_suffix)}")
        elif self.settings.profile_name.casefold() == "default":
            self.profile_summary.setText(self.t("Default profile"))
        else:
            self.profile_summary.setText(self.settings.profile_name)
        self.refresh_profile_quick_switch()

    def refresh_profile_quick_switch(self) -> None:
        if not hasattr(self, "profile_quick_switch"):
            return
        profiles = self.local_profile_files()
        current = self._active_profile_path.resolve() if self._active_profile_path is not None else None
        combo = self.profile_quick_switch
        combo.blockSignals(True)
        combo.clear()
        selected = -1
        for index, path in enumerate(profiles):
            combo.addItem(path.stem, str(path.resolve()))
            if current is not None and path.resolve() == current:
                selected = index
            elif current is None and path.stem.casefold() == self.settings.profile_name.casefold():
                selected = index
        if not profiles:
            combo.addItem(self.t("No saved profiles"), "")
            combo.setEnabled(False)
        else:
            combo.setEnabled(True)
            combo.setCurrentIndex(max(0, selected))
        combo.blockSignals(False)

    @tuning_guard
    def quick_switch_profile(self, index: int) -> None:
        if index < 0 or not hasattr(self, "profile_quick_switch"):
            return
        raw_path = str(self.profile_quick_switch.itemData(index) or "")
        if not raw_path:
            return
        candidate = Path(raw_path).resolve()
        try:
            candidate.relative_to(PROFILE_DIRECTORY.resolve())
        except ValueError:
            self.log("WARN", "Quick profile selection rejected a path outside the profile library.")
            return
        if self._active_profile_path is not None and candidate == self._active_profile_path.resolve():
            return
        if candidate.is_file() and self.load_profile_path(candidate):
            self.log("INFO", f"Quick profile loaded: {candidate.name}")

    def apply_ui_appearance(self, *, save: bool = True) -> None:
        if self.ui_appearance.layout not in THEMES:
            self.ui_appearance.layout = "smoky"
        contrast = theme_for(self.ui_appearance.layout).high_contrast
        self.ui_appearance.colors = {
            name: normalized_ui_color(self.ui_appearance.colors.get(name), fallback)
            for name, fallback in DEFAULT_UI_COLORS.items()
        }
        self.ui_appearance.opacities = {
            name: normalized_ui_opacity(self.ui_appearance.opacities.get(name), fallback)
            for name, fallback in DEFAULT_UI_OPACITIES.items()
        }
        if save and not save_ui_appearance(self.ui_appearance):
            self.log("WARN", "Windows could not save the interface appearance preference.")
        scale = getattr(self._live_owner, '_scale', '1')
        self.setStyleSheet(scaled_native_stylesheet(ui_stylesheet(self.ui_appearance, True), scale))
        if hasattr(self, "workspace_root"):
            self.workspace_root.set_appearance(self.ui_appearance, True)
        self.refresh_session_cockpit()
        palette = appearance_palette(self.ui_appearance, True)
        cloud_workspace=getattr(getattr(self,'_cloud_suite',None),'workspace',None)
        if cloud_workspace is not None:cloud_workspace.apply_palette()
        for page, button in self.nav.items():
            button.setIcon(self.navigation_icon(page))
            button.setIconSize(QSize(round(20*float(scale)), round(20*float(scale))))
        from mission_icons import apply as apply_icon
        for name, symbol in (("start_button", "start"), ("stop_button", "stop"),
                ("save_button", "save"), ("undo_button", "undo"),
                ("settings_search_button", "search"), ("testing_suite_button", "start")):
            button = getattr(self, name, None)
            if button is not None:
                apply_icon(button, symbol, palette, scale, primary=name=='start_button', danger=name=='stop_button')
        for button in self.findChildren(QPushButton):
            symbol = button.property('missionIcon')
            if symbol:
                apply_icon(button, symbol, palette, scale, primary=button.objectName()=='primaryButton' or bool(button.property('missionPrimary')), danger=button.objectName()=='dangerButton')
        from mission_icons import icon as semantic_icon
        for editor in self.findChildren(QLineEdit):
            if editor.property('missionSearch'):
                symbol=semantic_icon('search',palette['ice'],palette['baby'],palette['steel'])
                if not hasattr(editor,'_mission_search_action'):
                    editor._mission_search_action=editor.addAction(symbol,QLineEdit.ActionPosition.LeadingPosition)
                else:editor._mission_search_action.setIcon(symbol)
        for label in self.findChildren(QLabel):
            glyph = label.property('missionGlyph')
            if glyph:
                size=round(30*float(scale))
                label.setFixedSize(size,size)
                label.setPixmap(semantic_icon(glyph,palette['ice'],palette['baby'],palette['steel']).pixmap(size,size))
            symbol = label.property('missionIconLabel')
            if symbol:
                size = round(64 * float(scale))
                label.setPixmap(semantic_icon(symbol,palette['ice'],palette['baby'],palette['steel']).pixmap(size,size))
        if hasattr(self, "community_link"):
            self._tuning_lock.refresh()
            self.community_link.setText(f'<a style="color:{palette["baby"]}" href="https://discord.gg/eWVvtuRz4T">{self.t("Community").upper()}</a>')
        if hasattr(self, "palette_status"):
            customized = (palette != theme_for(self.ui_appearance.layout).palette() or self.ui_appearance.opacities != DEFAULT_UI_OPACITIES)
            self.palette_status.setText(self.t("Customized palette" if customized else "Theme defaults"))
        if hasattr(self, "command_bar"):
            self.command_bar.setFixedHeight(round((78 if contrast else 72) * float(scale)))
        if hasattr(self, "navigation_deck"):
            base = 210 if contrast else 196
            self.navigation_deck.setFixedWidth(min(round(base * float(scale)), max(base, round(self.width() * .3))))
        margins = (22, 18, 22, 25) if contrast else (27, 12, 27, 12)
        for content in self.findChildren(QWidget, "pageContent"):
            if content.layout() is not None:
                content.layout().setContentsMargins(*margins)
        for tabs in self.findChildren(SecondarySettingsTabs):
            tabs.refresh_geometry()
        self.update_targeting_layout()
        coordinator = getattr(self, '_live_coordinator', None)
        if coordinator is not None:
            coordinator.refresh_theme()
            coordinator._size_command_row()
        self.update_targeting_layout()

        self.update_telemetry_layout()
        self.update_header_density()

    def appearance_layout_changed(self, index: int) -> None:
        if not hasattr(self, "interface_layout_choice"):
            return
        layout_mode = str(self.interface_layout_choice.itemData(index) or "smoky")
        if layout_mode == self.ui_appearance.layout:
            return
        if layout_mode not in THEMES:
            return
        self.ui_appearance.custom_palettes[self.ui_appearance.layout] = {
            "colors": dict(self.ui_appearance.colors), "opacities": dict(self.ui_appearance.opacities)}
        self.ui_appearance.layout = layout_mode
        saved = self.ui_appearance.custom_palettes.get(layout_mode, {})
        colors = saved.get("colors", {}) if isinstance(saved.get("colors", {}), dict) else {}
        opacities = saved.get("opacities", {}) if isinstance(saved.get("opacities", {}), dict) else {}
        self.ui_appearance.colors = {name: normalized_ui_color(colors.get(name), fallback) for name, fallback in theme_for(layout_mode).palette().items()}
        self.ui_appearance.opacities = {name: normalized_ui_opacity(opacities.get(name), fallback) for name, fallback in DEFAULT_UI_OPACITIES.items()}
        self.apply_ui_appearance()
        for role, editor in getattr(self, "_ui_palette_inputs", {}).items():
            editor.setText(self.ui_appearance.colors[role])
        for role, editor in getattr(self, "_ui_palette_opacity_inputs", {}).items():
            editor.set_value(self.ui_appearance.opacities[role])

    def ui_palette_editor(self, role: str) -> QWidget:
        control = QWidget()
        control.setMinimumWidth(390)
        column = QVBoxLayout(control)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        palette = appearance_palette(self.ui_appearance, True)
        opacities = self.ui_appearance.opacities
        value = QLineEdit(palette.get(role, DEFAULT_UI_COLORS[role]))
        value.setMaxLength(7)
        value.setMinimumWidth(106)
        swatch = QToolButton()
        swatch.setText("…")
        swatch.setToolTip(self.t("Choose color"))

        def refresh_swatch() -> None:
            color = normalized_ui_color(value.text(), DEFAULT_UI_COLORS[role])
            swatch.setStyleSheet(f"QToolButton {{ background:{color}; border:1px solid {DEFAULT_UI_COLORS['ice']}; }}")

        def commit() -> None:
            candidate = QColor(value.text().strip())
            if not candidate.isValid():
                value.setText(self.ui_appearance.colors.get(role, DEFAULT_UI_COLORS[role]))
                refresh_swatch()
                return
            normalized = candidate.name(QColor.NameFormat.HexRgb)
            value.setText(normalized)
            self.ui_appearance.colors[role] = normalized
            refresh_swatch()
            self.apply_ui_appearance()

        def choose() -> None:
            selected = QColorDialog.getColor(QColor(value.text().strip()), self, self.t("Choose interface color"))
            if selected.isValid():
                value.setText(selected.name(QColor.NameFormat.HexRgb))
                commit()

        value.editingFinished.connect(commit)
        value.textChanged.connect(refresh_swatch)
        swatch.clicked.connect(choose)
        row.addWidget(value, 1)
        row.addWidget(swatch)
        opacity_row = QHBoxLayout()
        opacity_row.setContentsMargins(0, 0, 0, 0)
        opacity_label = QLabel("Opacity")
        opacity_label.setObjectName("policyControlLabel")
        opacity_label.setMinimumWidth(52)
        opacity = RangeControl(0, 100, opacities.get(role, DEFAULT_UI_OPACITIES[role]), 0, " %")
        opacity.setProperty("appliedOpacity", normalized_ui_opacity(opacity.spin.value()))

        def stage_opacity(number: float) -> None:
            self.ui_appearance.opacities[role] = normalized_ui_opacity(number)

        def commit_opacity() -> None:
            current = normalized_ui_opacity(opacity.spin.value())
            self.ui_appearance.opacities[role] = current
            if current == opacity.property("appliedOpacity"):
                return
            opacity.setProperty("appliedOpacity", current)
            self.apply_ui_appearance()

        # Restyling the complete control center is intentionally deferred. A
        # slider drag or repeated spin-button clicks can emit dozens of value
        # changes; applying once at interaction completion keeps the UI live.
        opacity.changed.connect(stage_opacity)
        opacity.slider.sliderReleased.connect(commit_opacity)
        opacity.spin.editingFinished.connect(commit_opacity)
        opacity_row.addWidget(opacity_label)
        opacity_row.addWidget(opacity, 1)
        column.addLayout(row)
        column.addLayout(opacity_row)
        self._ui_palette_inputs[role] = value
        self._ui_palette_opacity_inputs[role] = opacity
        refresh_swatch()
        return control

    def reset_ui_palette(self) -> None:
        self.ui_appearance.colors = theme_for(self.ui_appearance.layout).palette()
        self.ui_appearance.opacities = dict(DEFAULT_UI_OPACITIES)
        for role, editor in getattr(self, "_ui_palette_inputs", {}).items():
            editor.setText(self.ui_appearance.colors[role])
        for role, editor in getattr(self, "_ui_palette_opacity_inputs", {}).items():
            editor.set_value(DEFAULT_UI_OPACITIES[role])
            editor.setProperty("appliedOpacity", DEFAULT_UI_OPACITIES[role])
        self.apply_ui_appearance()
        QTimer.singleShot(0, self.rebuild_pages)

    def capture_interface_copy(self, *, reset: bool = False) -> None:
        if reset:
            self.interface_translator.capture(self, reset=True)
            return
        for page in self.pages.values():
            self.interface_translator.capture(page, reset=False)

    def apply_interface_language(self) -> None:
        self.ui_language = normalize_language(self.ui_language)
        self.refresh_target_confidence_summary()
        self.refresh_controller_bridge_controls()
        self._tuning_lock.refresh()
        for status in self.findChildren(QLabel, 'controllerThresholdStatus'):
            status.setText(self.t(status.property('thresholdMessage') or ''))
        for bulb in self.findChildren(QToolButton, 'guideBulb'):
            text = self.tf('Open guide: {topic}', topic=self.t(bulb.property('guideTitle')))
            bulb.setToolTip(text)
            bulb.setAccessibleName(text)
        application = QApplication.instance()
        if application is not None:
            apply_qt_language(application, self.ui_language)
        if self.ui_language == "zh-CN" and not getattr(self, "_chinese_fonts_loaded", False):
            fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for name in ("msyh.ttc", "msyhbd.ttc"):
                if (fonts / name).is_file():
                    QFontDatabase.addApplicationFont(str(fonts / name))
            self._chinese_fonts_loaded = True
        self.interface_translator.apply(self.ui_language)
        for control in self.findChildren(QCheckBox):
            if control.property('missionStateCaption'):control.setText(self.t('On' if control.isChecked() else 'Off'))
        for panel_name in ('game_cards', 'profile_cards'):
            panel = getattr(self, panel_name, None)
            if panel is not None:
                panel.refresh()
        for tabs in self.findChildren(SecondarySettingsTabs):
            tabs.refresh_geometry()
        if hasattr(self, "virtual_mouse_remove_button"):
            self.virtual_mouse_remove_button.setAccessibleName(self.t("Remove virtual mouse component"))
        if hasattr(self, 'dataset_capture_qualification_checkbox'):
            self.dataset_capture_qualification_checkbox.setAccessibleName(self.t('Only capture frames with a qualifying detection'))
        if hasattr(self, 'dataset_capture_confidence_editor'):
            self.dataset_capture_confidence_editor.setAccessibleName(self.t('Minimum capture confidence'))
        self.refresh_cuda_preprocess_controls()
        self.render_model_selection_status()
        for kicker in self.findChildren(QLabel):
            source_title = kicker.property("_vaporlock_i18n_page_title")
            if source_title:
                kicker.setText(f"VAPOR LOCK / {self.t(str(source_title)).upper()}")
        self.header_page.setText(self.t(PAGE_TITLES.get(self.current_page, "Control Center")))
        self.setWindowTitle(f"Vapor Lock · {self.t('Control Center')}")
        self.edition_summary.setText(PRODUCT_VERSION)
        self.edition_summary.setToolTip(f"Vapor Lock {PRODUCT_VERSION}")
        if hasattr(self, "overview_release"):
            self.overview_release.setText(f"Vapor Lock {PRODUCT_VERSION}")
        self.community_link.setText(
            f'<a style="color:{appearance_palette(self.ui_appearance, True)["baby"]}" href="https://discord.gg/eWVvtuRz4T">{self.t("Community").upper()}</a>'
        )
        self.update_profile_summary()
        self.status.setText(self.localized_engine_state(self._last_engine_state).upper())
        if hasattr(self, "language_choice"):
            self.language_choice.blockSignals(True)
            self.language_choice.setCurrentIndex(max(0, self.language_choice.findData(self.ui_language)))
            self.language_choice.blockSignals(False)
        if hasattr(self, "controller_tester"):
            self.controller_tester.refresh()
        self.refresh_readiness()
        if hasattr(self, "_last_collection_data"):
            self.apply_collection_status(self._last_collection_data)
        if hasattr(self, "dataset_effects_target"):
            from annotation_suite_ui import refresh_local_target
            refresh_local_target(self)
        if hasattr(self, 'ultralytics_run_summary'):
            from annotation_suite_ui import refresh_run_summary
            from annotation_progress import refresh_activity
            refresh_run_summary(self);refresh_activity(self)
            result=getattr(self,'_ultralytics_result_payload',None)
            if result:
                from annotation_suite_ui import cloud_event
                cloud_event(self,result,'',replay=True)
        for name in ('activity_hub', 'training_curves', 'inference_gpu_controls'):
            if hasattr(self,name): getattr(self,name).retranslate()
        cloud_suite=getattr(self,'_cloud_suite',None)
        if cloud_suite is not None:
            cloud_suite.refresh_mode();cloud_suite.render();cloud_suite.workspace.render()
        self.update_header_density()
        self.update_telemetry_layout()
        coordinator = getattr(self, '_live_coordinator', None)
        if coordinator is not None:
            coordinator._size_command_row()

    def language_changed(self, index: int) -> None:
        language = normalize_language(self.language_choice.itemData(index))
        if language == self.ui_language:
            return
        self.ui_language = language
        if not save_preferred_language(language):
            self.log("WARN", "Windows could not save the interface language preference.")
        self.apply_interface_language()

    @disposable_deny('external-open')
    def open_settings_guide(self, topic=None) -> bool:
        if getattr(self, '_manual_services', False):
            self.log('INFO', 'This operation is unavailable in the verification session.')
            return False
        try:
            document = guide_path(ROOT, self.ui_language)
            url = QUrl.fromLocalFile(str(document))
            if topic is not None:
                if guide_topic(ROOT, self.ui_language, topic) is None:
                    return False
                url.setFragment(topic)
            if not QDesktopServices.openUrl(url):
                raise OSError("Guide dispatch failed")
            return True
        except (OSError, ValueError):
            QMessageBox.warning(self, self.t("Settings guide"), self.t("The bundled guide could not be opened. Check the guides folder in your installation."))
            return False

    def build_pages(self) -> None:
        # Keep the native palette sequence stable across complete page rebuilds.
        RangeControl._accent_index = 0
        from vlui_numeric_native import NativeNumericRegistry
        self._numeric_registry = NativeNumericRegistry(self, RangeControl)
        from vlui_discrete_native import NativeDiscreteRegistry, fixed_bindings
        self._discrete_registry = NativeDiscreteRegistry(self, fixed_bindings())
        from profile_cards_ui import games_page
        from activity_hub import misc_page
        self.pages = {
            "games": games_page(self),
            "overview": self.overview_page(), "model": self.model_page(),
            "detection": self.detection_page(), "selection": self.selection_page(), "output": self.output_page(),
            "dataset": self.dataset_page(),
            "profiles": self.profiles_page(), "shortcuts": self.shortcuts_page(), "automation": self.automation_page(), "diagnostics": self.diagnostics_page(),
            "misc": misc_page(self),
        }
        self.pages['interface']=self._interface_page
        self.model_quick_switch.blockSignals(True)
        self.model_quick_switch.setModel(self.model_library.model())
        self.model_quick_switch.setCurrentIndex(self.model_library.currentIndex())
        self.model_quick_switch.blockSignals(False)
        for page in self.pages.values():
            self.stack.addWidget(page)
        self._numeric_registry.seal(self.stack)
        self._discrete_registry.seal(self.stack)
        self.apply_ui_appearance(save=False)

    def rebuild_pages(self) -> None:
        dialog=getattr(self,"_crosshair_dialog",None)
        if dialog:dialog.reject()
        for name in ('annotation_assist','observation_sharing'):
            controller=getattr(self,name,None)
            if controller:controller.dispose()
        worker_status = {name: getattr(self, name).text() for name in ("dataset_worker_status", "dataset_backend_status", "ultralytics_worker_status", "ultralytics_backend_status", "ultralytics_output_status", "training_status", "training_runtime_status") if hasattr(self, name)}
        log_text = self.log_view.toPlainText() if hasattr(self, "log_view") else ""
        frame = self._last_preview_frame if not self._last_preview_frame.isNull() else (self.preview_view._frame if hasattr(self, "preview_view") else QImage())
        tab_indices = {key: [tabs.currentIndex() for tabs in page.findChildren(SecondarySettingsTabs)] for key, page in self.pages.items()}
        inspector_index = self.targeting_inspector_stack.currentIndex()
        inspector_scrolls = [
            self.targeting_inspector_stack.widget(index).verticalScrollBar().value()
            for index in range(self.targeting_inspector_stack.count())
        ]
        scrolls = {key: page.verticalScrollBar().value() for key, page in self.pages.items() if isinstance(page, QScrollArea)}
        if self._benchmark_active or self._validation_active:
            self._benchmark_active = False
            if self._benchmark_process and self._benchmark_process.state() != QProcess.ProcessState.NotRunning:
                self._benchmark_process.kill()
            self.set_start_enabled(not self.engine.active)
        while self.stack.count():
            page = self.stack.widget(0)
            self.stack.removeWidget(page)
            page.deleteLater()
        self.build_pages()
        self.sync_background_controls()
        for name, text in worker_status.items():
            if hasattr(self, name):
                getattr(self, name).setText(text)
        self.capture_interface_copy()
        self.apply_interface_language()
        self.show_page(self.current_page)
        self.set_targeting_inspector_tab(inspector_index)
        for index, value in enumerate(inspector_scrolls):
            scroll = self.targeting_inspector_stack.widget(index)
            QTimer.singleShot(0, lambda scroll=scroll, value=value: scroll.verticalScrollBar().setValue(value))
        self.log_view.setPlainText(log_text)
        for attribute in ("preview_view", "policy_preview_view"):
            if hasattr(self, attribute):
                getattr(self, attribute).set_frame(frame)
        for key, page in self.pages.items():
            for tabs, index in zip(page.findChildren(SecondarySettingsTabs), tab_indices.get(key, [])):
                tabs.setCurrentIndex(index)
            if key in scrolls:
                QTimer.singleShot(0, lambda page=page, value=scrolls[key]: page.verticalScrollBar().setValue(value))

    def overview_page(self) -> QWidget:
        page, layout = self.new_page("Session overview", "Review capture, model and movement setup before starting a session.")
        from mission_layout import TaskColumns,TaskCard
        overview_columns=TaskColumns(proportions=(60,40))
        for panel in (overview_columns.left,overview_columns.right):panel.setObjectName('missionTaskColumn')
        preview_card=TaskCard('Live preview','display')
        config_card=TaskCard('Current configuration','gear')
        overview_columns.left_layout.addWidget(preview_card,1)
        overview_columns.right_layout.addWidget(config_card)
        self._overview_preview_slot=preview_card.body
        notice = QFrame()
        notice.setObjectName("signalPanel")
        notice_layout = QVBoxLayout(notice)
        notice_layout.setContentsMargins(10, 8, 10, 8)
        notice_layout.setSpacing(6)
        signal_copy = QVBoxLayout()
        signal_copy.setSpacing(3)
        signal_label = QLabel("LOCAL SESSION")
        signal_label.setObjectName("signalCaption")
        self.readiness = QLabel()
        self.readiness.setObjectName("signalValue")
        self.readiness.setWordWrap(True)
        self.readiness_detail = QLabel("Local session status")
        self.readiness_detail.setObjectName("detail")
        self.readiness_detail.setToolTip("The control center exchanges lightweight commands and telemetry with the native engine.")
        signal_copy.addWidget(signal_label)
        signal_copy.addWidget(self.readiness)
        notice_layout.addWidget(self.readiness)
        signal_label.setParent(notice);signal_label.hide()
        details = QHBoxLayout()
        details.setSpacing(24)
        provider = QVBoxLayout()
        provider.setSpacing(3)
        provider_label = QLabel("PROVIDER")
        provider_label.setObjectName("signalCaption")
        self.overview_provider = QLabel()
        self.overview_provider.setObjectName("signalValue")
        provider.addWidget(provider_label)
        provider.addWidget(self.overview_provider)
        model = QVBoxLayout()
        model.setSpacing(3)
        model_label = QLabel("MODEL")
        model_label.setObjectName("signalCaption")
        self.overview_model = QLabel()
        self.overview_model.setObjectName("signalValue")
        model.addWidget(model_label)
        model.addWidget(self.overview_model)
        details.addLayout(provider)
        details.addLayout(model)
        # Model/provider are shown as configuration rows below, not a second
        # oversized summary. Keep the existing readout objects for updates.
        for item in (provider_label,self.overview_provider,model_label,self.overview_model):
            item.setParent(notice);item.hide()
        release = QLabel(f"Vapor Lock {PRODUCT_VERSION}")
        release.setObjectName("signalCaption")
        release.setProperty("_vaporlock_i18n_skip", True)
        self.overview_release = release
        release.setParent(notice);release.hide()
        self.overview_configuration=QLabel()
        self.overview_configuration.setTextFormat(Qt.TextFormat.PlainText)
        self.overview_configuration.setWordWrap(True)
        config_card.body.addWidget(self.overview_configuration)
        config_card.body.addWidget(notice)
        cockpit = QGridLayout()
        cockpit.setHorizontalSpacing(9)
        cockpit.setVerticalSpacing(9)
        self.session_cockpit: dict[str, tuple[QLabel, QPushButton]] = {}

        def cockpit_card(key: str, caption: str, action: str, callback) -> QFrame:
            card=TaskCard(caption,{'pipeline':'model','frame':'display','output':'movement','setup':'gear'}[key])
            value=QLabel();value.setObjectName('detail');card.body.addWidget(value)
            value.setWordWrap(True)
            button = QPushButton(action)
            button.setObjectName("compactButton")
            button.clicked.connect(callback)
            card.layout().addWidget(button)
            self.session_cockpit[key] = (value, button)
            return card

        cards = [
            cockpit_card("frame", "Capture source", "Capture settings", lambda: self.show_page("capture")),
            cockpit_card("pipeline", "Model", "Model library", lambda: self.show_page("model")),
            cockpit_card("output", "Movement output", "Movement settings", lambda: self.show_page("output")),
        ]
        layout.addLayout(cockpit)
        self.session_cockpit_grid = cockpit
        self.session_cockpit_cards = cards
        self.update_overview_grid()
        layout.addWidget(overview_columns,1)
        setup_card=cockpit_card('setup','Initialization','Setup and calibration',self.open_setup_and_calibration)
        from mission_layout import ActionFlow
        setup_actions=QWidget();setup_flow=ActionFlow(setup_actions)
        setup_flow.addWidget(self.session_cockpit['setup'][1])
        # Keep the readiness text associated with its existing action.
        setup_flow.addWidget(self.session_cockpit['setup'][0])
        setup_card.deleteLater()
        layout.addWidget(setup_actions)
        self.testing_suite_button = QPushButton("Open testing grounds")
        self.testing_suite_button.setObjectName("openTestingGrounds")
        self.testing_suite_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.testing_suite_button.clicked.connect(self.open_testing_suite)
        self.testing_suite_button.setToolTip('Open the standalone practice range. Capture and movement remain under your control.')
        setup_flow.addWidget(self._tuning_lock.wrap(self.testing_suite_button))
        metrics = QGridLayout()
        state, self.metric_state = self.metric_card("Session state")
        fps, self.metric_fps = self.metric_card("Processing rate")
        tracks, self.metric_tracks = self.metric_card("Active tracks")
        target, self.metric_target = self.metric_card("Current lock")
        for index,card in enumerate((state, fps, tracks, target)):
            metrics.addWidget(card,index//2,index%2)
        config_card.body.addLayout(metrics)
        overview_layout=layout
        self._interface_page, interface_outer = self.new_page('Interface & Language','Configure the display language, theme and setup preferences.')
        from mission_layout import TaskColumns,TaskCard
        interface_columns=TaskColumns(proportions=(1,1))
        for panel in (interface_columns.left,interface_columns.right):panel.setObjectName('missionTaskColumn')
        interface_outer.addWidget(interface_columns)
        language_card=TaskCard('Language','globe')
        theme_card=TaskCard('Interface mode','display')
        interface_columns.left_layout.addWidget(language_card)
        interface_columns.right_layout.addWidget(theme_card)
        layout=language_card.body
        language = QComboBox()
        language.setObjectName("languageChoice")
        language.setProperty("_vaporlock_i18n_skip", True)
        for name, code in LANGUAGE_OPTIONS:
            language.addItem(name, code)
        language.setCurrentIndex(max(0, language.findData(self.ui_language)))
        language.currentIndexChanged.connect(self.language_changed)
        self.language_choice = language
        self.setting(layout, "Display language", "Choose the language used throughout Control Center.", language)
        layout=theme_card.body
        interface_layout = QComboBox()
        for identifier, theme in THEMES.items():
            interface_layout.addItem(theme.name, identifier)
        interface_layout.setCurrentIndex(max(0, interface_layout.findData(self.ui_appearance.layout)))
        interface_layout.currentIndexChanged.connect(self.appearance_layout_changed)
        self.interface_layout_choice = interface_layout
        self.setting(
            layout,
            "Interface mode",
            "Choose a theme with its own background and translucent panels. Preview and overlay colors stay separate.",
            interface_layout,
        )
        customization=TaskCard('Theme customization','palette')
        interface_outer.addWidget(customization)
        self._ui_palette_inputs={}
        self._ui_palette_opacity_inputs={}
        for role,title,_ in UI_COLOR_ROLES:
            self.setting(customization.body,title,UI_COLOR_ROLE_DETAILS[role],self.ui_palette_editor(role))
        reset_palette=QPushButton('Restore theme defaults')
        reset_palette.setProperty('missionIcon','restore')
        reset_palette.clicked.connect(self.reset_ui_palette)
        customization.body.addWidget(reset_palette)
        help_card=TaskCard('Help & setup','book')
        interface_outer.addWidget(help_card)
        layout=help_card.body
        setup_button = QPushButton("Run setup & calibration")
        setup_button.clicked.connect(self.open_setup_and_calibration)
        self.setting(
            layout,
            "Setup wizard",
            "Review the model, capture source, activation binding, and movement baseline. The wizard never starts capture automatically.",
            setup_button,
        )
        setup_status = QLabel(
            "Initialization complete. Capture is enabled."
            if initialization_complete()
            else "Initialization is required before the first capture session."
        )
        setup_status.setObjectName("detail")
        setup_status.setWordWrap(True)
        layout.addWidget(setup_status)
        interface_outer.addStretch(1)
        overview_layout.addStretch(1)
        self.refresh_readiness()
        return page

    @disposable_deny('child-process')
    def open_testing_suite(self) -> None:
        executable = ROOT / "testing-suite" / "Official Aim Testing Suite.exe"
        if not executable.is_file():
            QMessageBox.warning(self, self.t("Aim Testing Suite"), self.t(
                "The testing suite is missing. Repair or reinstall this Vapor Lock release."
            ))
            return
        try:
            # Start a fresh range with the character used for the shipped
            # baseline; thereafter leave the user's saved range choice alone.
            arguments = [] if (executable.parent / "settings.ini").is_file() else ["--model", "SWAT.glb"]
            started, _pid = QProcess.startDetached(str(executable), arguments, str(executable.parent))
        except OSError:
            started = False
        if not started:
            QMessageBox.warning(self, self.t("Aim Testing Suite"), self.t(
                "Windows could not start the testing suite. Check that the application folder is writable and the executable is available."
            ))

    def capture_page(self) -> QWidget:
        page, layout = self.new_page("Capture", "Set a physical display and its centered capture region for the next native session.")
        self._build_capture_controls(layout)
        return page

    def _build_capture_controls(self, layout: QVBoxLayout) -> None:
        """Build the single capture configuration surface used by Targeting."""
        self.section(layout, "Source")
        monitor = QComboBox()
        monitor.addItem("Virtual desktop (compatibility capture)", 0)
        for index, screen in enumerate(QGuiApplication.screens(), start=1):
            size = screen.size()
            monitor.addItem(f"Monitor {index} · {screen.name()} ({size.width()} x {size.height()})", index)
        monitor.setCurrentIndex(max(0, monitor.findData(self.settings.monitor)))
        monitor.currentIndexChanged.connect(lambda index: self.change("monitor", int(monitor.itemData(index))))
        self.setting(layout, "Capture display", "A physical monitor uses native DXGI Desktop Duplication. Virtual desktop is available as a compatibility path.", monitor)
        region = QLineEdit(f"{self.settings.region_x}, {self.settings.region_y}, {self.settings.region_width}, {self.settings.region_height}")
        region.setPlaceholderText("0, 0, 640, 640")
        region.editingFinished.connect(lambda: self.apply_region(region))
        self.setting(layout, "Centered capture region", "x offset, y offset, width, height. 0, 0, 640, 640 captures a centered 640 x 640 frame. Negative offsets move left/up; positive offsets move right/down.", region)
        self.section(layout, "Rate")
        self.setting(layout, "Capture rate", "Slider values top out at 120 fps; type a higher value when your source and hardware support it.", self.range("capture_fps", 1, 120, 0, " fps"))
        self.setting(layout, "Inference rate", "Slider values top out at 120 fps; type a higher value when your model can sustain it.", self.range("inference_fps", 1, 120, 0, " fps"))
        self.setting(layout, "Frame skip", "Additional captured frames skipped before an inference pass.", self.range("frame_skip", 0, 60))
        self.section(layout, "ONNX Runtime")
        self.setting(layout, "Intra-operation threads", "0 lets ONNX Runtime choose automatically. A positive value caps work inside each model operation.", self.range("ort_intra_op_threads", 0, 64))
        self.setting(layout, "Inter-operation threads", "0 lets ONNX Runtime choose automatically. A positive value caps parallel model graph work.", self.range("ort_inter_op_threads", 0, 32))
        cuda_graph = QCheckBox("Enable CUDA Graph replay")
        cuda_graph.setChecked(False)
        cuda_graph.setEnabled(False)
        self.setting(layout, "CUDA Graph replay", "Unavailable in this engine: persistent CUDA I/O buffers are required. Standard CUDA inference remains available.", cuda_graph)
        cuda_preprocess = self.cuda_preprocess_control("Use GPU preprocessing")
        self.setting(layout, "GPU preprocessing", "Requires a compatible CUDA or TensorRT RTX engine and a float NCHW model. Other models use CPU preprocessing.", cuda_preprocess)
        self.refresh_cuda_preprocess_controls()
        layout.addStretch(1)

    def _build_capture_inspector(self, layout: QVBoxLayout) -> None:
        """Build capture controls sized for the Targeting right inspector."""
        def card(title: str) -> QVBoxLayout:
            panel = QFrame()
            panel.setObjectName("policyInspectorCard")
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(4, 11, 4, 14)
            panel_layout.setSpacing(7)
            heading = QLabel(title)
            heading.setObjectName("policyCardTitle")
            panel_layout.addWidget(heading)
            layout.addWidget(panel)
            return panel_layout

        def labeled_control(parent: QVBoxLayout, title: str, control: QWidget, detail: str) -> None:
            label = QLabel(title)
            label.setObjectName("policyControlLabel")
            label.setToolTip(detail)
            control.setToolTip(detail)
            heading = QHBoxLayout()
            heading.addWidget(label)
            add_guide_button(self, heading, title, control)
            heading.addStretch(1)
            parent.addLayout(heading)
            parent.addWidget(self._tuning_lock.wrap(control))

        def compact_range(
            parent: QVBoxLayout,
            title: str,
            field: str,
            low: float,
            high: float,
            decimals: int = 0,
            suffix: str = "",
        ) -> None:
            control = self.range(field, low, high, decimals, suffix)
            control.setObjectName("policyInspectorRange")
            control.pass_slider_wheel_to_parent()
            labeled_control(parent, title, control, title)

        source = card("SOURCE")
        monitor = QComboBox()
        monitor.addItem("Virtual desktop (compatibility capture)", 0)
        for index, screen in enumerate(QGuiApplication.screens(), start=1):
            size = screen.size()
            monitor.addItem(f"Monitor {index} · {screen.name()} ({size.width()} x {size.height()})", index)
        monitor.setCurrentIndex(max(0, monitor.findData(self.settings.monitor)))
        monitor.currentIndexChanged.connect(lambda index: self.change("monitor", int(monitor.itemData(index))))
        labeled_control(source, "Capture display", monitor, "Select the display used by the native capture engine.")
        region = QLineEdit(f"{self.settings.region_x}, {self.settings.region_y}, {self.settings.region_width}, {self.settings.region_height}")
        region.setPlaceholderText("0, 0, 640, 640")
        region.editingFinished.connect(lambda: self.apply_region(region))
        labeled_control(source, "Centered region", region, "x offset, y offset, width, height.")
        from visual_geometry_ui import open_region_editor
        picker = QPushButton("Pixel capture region…")
        picker.setObjectName('openRegionEditor')
        picker.clicked.connect(lambda: open_region_editor(self))
        source.addWidget(self._tuning_lock.wrap(picker))

        rate = card("RATE")
        compact_range(rate, "Capture rate", "capture_fps", 1, 120, 0, " fps")
        compact_range(rate, "Inference rate", "inference_fps", 1, 120, 0, " fps")
        compact_range(rate, "Frame skip", "frame_skip", 0, 60)

        runtime = card("RUNTIME")
        compact_range(runtime, "Intra-operation threads", "ort_intra_op_threads", 0, 64)
        compact_range(runtime, "Inter-operation threads", "ort_inter_op_threads", 0, 32)
        cuda_graph = QCheckBox("CUDA Graph replay")
        cuda_graph.setChecked(False)
        cuda_graph.setEnabled(False)
        cuda_graph.setToolTip("Unavailable in this engine: persistent CUDA I/O buffers are required. Standard CUDA inference remains available.")
        runtime.addWidget(cuda_graph)
        cuda_preprocess = self.cuda_preprocess_control("CUDA preprocessing")
        runtime.addWidget(cuda_preprocess)
        self.refresh_cuda_preprocess_controls()
        layout.addStretch(1)

    def cuda_preprocess_control(self, title: str) -> QCheckBox:
        control = QCheckBox("GPU preprocessing" if title == "CUDA preprocessing" else title)
        control.setObjectName("cudaPreprocessChoice")
        control.setEnabled(False)
        control.toggled.connect(lambda state: self.change("cuda_preprocess", state))
        binding = {'CUDA preprocessing': 'discrete.cuda_preprocess.1',
                   'Use GPU preprocessing': 'discrete.cuda_preprocess.2'}[title]
        self._discrete_registry.register(binding, 'cuda_preprocess', control)
        return control

    def refresh_cuda_preprocess_controls(self) -> None:
        controls = self.findChildren(QCheckBox, "cudaPreprocessChoice")
        if not controls:
            return
        status = "other-provider"
        if self.settings.provider in {"cuda", "trt"}:
            status = self.engine_capabilities.request(
                *self.engine.launch_for(self.settings.provider)
            )
        detail = {
            "other-provider": "Select CUDA or TensorRT RTX to use GPU preprocessing. CPU and DirectML use CPU preprocessing.",
            "checking": "Checking this engine's GPU preprocessing support...",
            "available": "Requires a compatible CUDA or TensorRT RTX engine and a float NCHW model. Other models use CPU preprocessing.",
            "unavailable": "GPU preprocessing is not included in this engine. Inference uses CPU preprocessing.",
            "unverified": "GPU preprocessing support could not be verified. CPU preprocessing remains available.",
        }[status]
        self._cuda_preprocess_status = status
        for control in controls:
            blocked = control.blockSignals(True)
            control.setEnabled(status == "available")
            control.setChecked(status == "available" and self.settings.cuda_preprocess)
            control.setToolTip(self.t(detail))
            control.blockSignals(blocked)

    def apply_region(self, editor: QLineEdit) -> None:
        try:
            values = [int(value.strip()) for value in editor.text().split(",")]
            if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Capture region", "Enter x offset, y offset, width, height. Width and height must be positive.")
            editor.setText(f"{self.settings.region_x}, {self.settings.region_y}, {self.settings.region_width}, {self.settings.region_height}")
            return
        for field, value in zip(("region_x", "region_y", "region_width", "region_height"), values):
            self.change(field, value)
        editor.setText(", ".join(str(value) for value in values))

    def model_page(self) -> QWidget:
        page, layout = self.new_page("Model Library", "Choose an ONNX detection model and the native execution provider used for the next session.")
        from mission_layout import TaskColumns, TaskCard
        page_layout=layout
        columns=TaskColumns(proportions=(34,66))
        for panel in (columns.left,columns.right):panel.setObjectName('missionTaskColumn')
        local_card=TaskCard('Local model library','model')
        columns.left_layout.addWidget(local_card)
        columns.left_layout.addStretch(1)
        details_card=TaskCard('Model details','model')
        execution_card=TaskCard('Execution provider','chip')
        interpretation_card=TaskCard('Model interpretation','filter')
        actions_card=TaskCard('Model actions','gear')
        from mission_layout import option_scroll
        model_options=QWidget();model_options_layout=QVBoxLayout(model_options)
        model_options_layout.setContentsMargins(0,0,0,0)
        for card in (details_card,execution_card,interpretation_card):model_options_layout.addWidget(card)
        model_options_layout.addStretch(1)
        columns.right_layout.addWidget(option_scroll(model_options,'Model details and options'),1)
        columns.right_layout.addWidget(actions_card)
        layout=local_card.body
        library = QWidget()
        from mission_layout import ActionFlow
        library_row = QVBoxLayout(library)
        library_row.setContentsMargins(0, 0, 0, 0)
        self.model_library = QComboBox()
        self.populate_model_library()
        self.model_library.currentIndexChanged.connect(self.select_discovered_model)
        from PySide6.QtWidgets import QListView
        model_list=QListView()
        self.model_library_list=model_list
        model_list.setAccessibleName('Local model library')
        model_list.setModel(self.model_library.model())
        model_list.setMinimumHeight(280)
        model_list.selectionModel().currentChanged.connect(
            lambda current,previous:self.model_library.setCurrentIndex(current.row())
            if current.isValid() and not self.model_library.signalsBlocked() else None)
        self.model_library.currentIndexChanged.connect(self.sync_model_selection_views)
        model_list.setCurrentIndex(self.model_library.model().index(self.model_library.currentIndex(),0))
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_model_library)
        folder = QPushButton("Open folder")
        folder.clicked.connect(self.open_model_folder)
        refresh.setProperty("missionIcon", "refresh")
        folder.setProperty("missionIcon", "folder")
        self.model_library.hide()
        self.model_library.setParent(library)
        library_row.addWidget(model_list)
        library_actions = QWidget()
        library_action_row = ActionFlow(library_actions)
        for button in (refresh, folder):library_action_row.addWidget(button)
        page_layout.addWidget(self._tuning_lock.wrap(library_actions))
        page_layout.addWidget(columns,1)
        layout.addWidget(self._tuning_lock.wrap(library))
        layout=details_card.body
        control = QWidget()
        row = QHBoxLayout(control)
        row.setContentsMargins(0, 0, 0, 0)
        self.model_path = QLineEdit(self.settings.model_path)
        self.model_path.setPlaceholderText("No model selected: the engine uses a test signal")
        self.model_path.editingFinished.connect(lambda: self.change("model_path", self.model_path.text().strip()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_model)
        browse.setProperty('missionIcon', 'folder')
        row.addWidget(self.model_path, 1)
        library_action_row.addWidget(self._tuning_lock.wrap(browse))
        self.setting(layout, "Detection model", "A local ONNX or .cloud file. A model is optional only for the built-in test signal.", control)
        layout=execution_card.body
        provider_control = QWidget()
        provider_layout = QHBoxLayout(provider_control)
        provider_layout.setContentsMargins(0, 0, 0, 0)
        provider = QComboBox()
        choices = [("Automatic (DirectML preferred)", "auto"), ("DirectML", "dml"), ("CUDA", "cuda")]
        if _NATIVE_INJECTION is None and AccelerationManager.supports("trt"):
            choices.append(("TensorRT RTX (NVIDIA)", "trt"))
        choices.append(("CPU", "cpu"))
        for title, value in choices:
            provider.addItem(title, value)
        provider.setCurrentIndex(max(0, provider.findData(self.settings.provider)))
        self.provider_choice = provider
        provider.currentIndexChanged.connect(self.provider_changed)
        self.acceleration_button = QPushButton()
        self.acceleration_button.clicked.connect(self.install_recommended_acceleration)
        provider_layout.addWidget(provider, 1)
        provider_layout.addWidget(self.acceleration_button)
        self.acceleration_detail = QLabel()
        self.acceleration_detail.setObjectName("detail")
        self.acceleration_detail.setWordWrap(True)
        self.setting(layout, "Inference provider", "Automatic prefers DirectML. ONNX can fall back to CPU; .cloud uses the chosen provider without silent fallback. .cloud supports CPU, DirectML and CUDA; TensorRT is ONNX-only.", provider_control)
        layout.addWidget(self.acceleration_detail)
        from mission_layout import TaskDetails
        execution_details=TaskDetails('CPU / GPU placement')
        layout.addWidget(execution_details)
        from cpu_placement import add_controls as add_cpu_controls
        add_cpu_controls(self,execution_details.body,provider)
        from batch6_ui import add_gpu_controls
        add_gpu_controls(self, execution_details.body, provider)
        self.refresh_acceleration_controls()
        layout=interpretation_card.body
        resize = QComboBox()
        resize.addItem("Letterbox (preserve aspect ratio)", "letterbox")
        resize.addItem("Stretch to model input", "stretch")
        resize.setCurrentIndex(max(0, resize.findData(self.settings.resize_mode)))
        resize.currentIndexChanged.connect(lambda index: self.change("resize_mode", str(resize.itemData(index))))
        self._discrete_registry.register('discrete.resize_mode.1', 'resize_mode', resize)
        self.setting(layout, "Image resize mode", "Letterbox preserves source geometry. Stretch fills the model input for models trained that way.", resize)
        output_format = QComboBox()
        for title, value in (
            ("Automatic from model", "auto"),
            ("YOLO class scores (v8/v10/v11 style)", "yolo-class"),
            ("YOLO objectness x class score (v5/v7 style)", "yolo-objectness"),
            ("End-to-end boxes: x1, y1, x2, y2, score, class", "end-to-end"),
        ):
            output_format.addItem(title, value)
        output_format.setCurrentIndex(max(0, output_format.findData(self.settings.model_output_format)))
        output_format.currentIndexChanged.connect(lambda index: self.change("model_output_format", str(output_format.itemData(index))))
        self._discrete_registry.register('discrete.model_output_format.1', 'model_output_format', output_format)
        self.setting(layout, "Model output layout", "Automatic reads ONNX metadata and validates ambiguous output rows. Choose an explicit layout only for a custom export that needs a manual override.", output_format)
        coordinate_units = QComboBox()
        for title, value in (("Automatic from model", "auto"), ("Input pixels", "pixels"), ("Normalized (0 to 1)", "normalized")):
            coordinate_units.addItem(title, value)
        coordinate_units.setCurrentIndex(max(0, coordinate_units.findData(self.settings.model_coordinate_units)))
        coordinate_units.currentIndexChanged.connect(lambda index: self.change("model_coordinate_units", str(coordinate_units.itemData(index))))
        self._discrete_registry.register('discrete.model_coordinate_units.1', 'model_coordinate_units', coordinate_units)
        self.setting(layout, "Box coordinate units", "Automatic uses declared model metadata, or input pixels for standard YOLO exports.", coordinate_units)
        layout=actions_card.body
        actions_flow=ActionFlow()
        layout.addLayout(actions_flow)
        check_details=TaskDetails('Validation and benchmark status')
        layout.addWidget(check_details)
        inspect = QWidget()
        inspect_row = QHBoxLayout(inspect)
        inspect_row.setContentsMargins(0, 0, 0, 0)
        self.model_inspection = QLabel("Inspect ONNX session details or .cloud file integrity and architecture. Use Observe to check live behavior.")
        self.model_inspection.setObjectName("detail")
        self.model_inspection.setWordWrap(True)
        details_card.body.addWidget(self.model_inspection)
        inspect_button = QPushButton("Inspect")
        inspect_button.setProperty('missionIcon', 'search')
        inspect_button.clicked.connect(self.inspect_model)
        inspect_row.addWidget(inspect_button)
        inspect_button.setToolTip('ONNX: inspects native session details. .cloud: checks file integrity and architecture. Capture stays stopped.')
        actions_flow.addWidget(self._tuning_lock.wrap(inspect_button))
        inspect.deleteLater()
        validation = QWidget()
        validation_row = QHBoxLayout(validation)
        validation_row.setContentsMargins(0, 0, 0, 0)
        self.model_validation = QLabel("Run a short live model check before relying on a new export. Output, previews, and dataset writing stay disabled.")
        self.model_validation.setObjectName("detail")
        self.model_validation.setWordWrap(True)
        self.model_validation_button = QPushButton("Run validation")
        self.model_validation_button.setProperty('missionIcon', 'start')
        self.model_validation_button.clicked.connect(self.validate_model_runtime)
        validation_row.addWidget(self.model_validation, 1)
        validation_row.addWidget(self.model_validation_button)
        self.model_validation_button.setToolTip('validate the model against the configured capture region while capture is stopped. Output, preview and dataset writing remain disabled.')
        check_details.body.addWidget(self.model_validation)
        actions_flow.addWidget(self._tuning_lock.wrap(self.feature_control(self.model_validation_button)))
        validation.deleteLater()
        benchmark = QWidget()
        benchmark_row = QHBoxLayout(benchmark)
        benchmark_row.setContentsMargins(0, 0, 0, 0)
        self.model_benchmark = QLabel("Run a short live comparison of the bundled CUDA, DirectML, and CPU engines for this model.")
        self.model_benchmark.setObjectName("detail")
        self.model_benchmark.setWordWrap(True)
        self.model_benchmark_button = QPushButton("Benchmark providers")
        self.model_benchmark_button.setProperty('missionIcon', 'pulse')
        self.model_benchmark_button.clicked.connect(self.benchmark_providers)
        benchmark_row.addWidget(self.model_benchmark, 1)
        benchmark_row.addWidget(self.model_benchmark_button)
        self.model_benchmark_button.setToolTip('Measures the model on the configured capture region with output, preview and dataset writing disabled. Capture must be stopped first.')
        check_details.body.addWidget(self.model_benchmark)
        actions_flow.addWidget(self._tuning_lock.wrap(self.model_benchmark_button))
        benchmark.deleteLater()
        return page

    def detection_page(self) -> QWidget:
        page, layout = self.new_page("Detection", "Tune confidence, overlap suppression, and the class allow-list used by the native detector.")
        from mission_layout import TaskColumns, TaskCard
        page_layout=layout
        self.detection_context=QLabel()
        self.detection_context.setObjectName('detail')
        self.detection_context.setWordWrap(True)
        self.detection_context.setTextFormat(Qt.TextFormat.PlainText)
        context=QFrame();context.setObjectName('missionTaskCard')
        context_row=QHBoxLayout(context);context_row.setContentsMargins(14,10,14,10)
        context_row.addWidget(self.detection_context)
        page_layout.addWidget(context)
        columns=TaskColumns(proportions=(56,44))
        for panel in (columns.left,columns.right):panel.setObjectName('missionTaskColumn')
        filters_card=TaskCard('Detection filters','filter')
        class_card=TaskCard('Model output / class handling','tag')
        columns.left_layout.addWidget(filters_card);columns.left_layout.addStretch(1)
        columns.right_layout.addWidget(class_card);columns.right_layout.addStretch(1)
        page_layout.addWidget(columns)
        layout=filters_card.body
        self.setting(layout, "Confidence threshold", "Discard detections below this confidence.", self.range("confidence", 0.01, 0.99, 2))
        nms_enabled = QCheckBox("Enabled")
        nms_enabled.setChecked(self.settings.nms_enabled)
        nms_enabled.toggled.connect(lambda state: self.change("nms_enabled", state))
        self._discrete_registry.register('discrete.nms_enabled.1', 'nms_enabled', nms_enabled)
        self.setting(layout, "Non-maximum suppression", "Remove overlapping duplicate raw predictions after confidence filtering. Final end-to-end boxes are recognized automatically and skip this extra pass.", nms_enabled)
        self.setting(layout, "NMS overlap threshold", "Suppress duplicate boxes whose overlap is above this value.", self.range("nms_iou", 0.01, 0.99, 2))
        nms_classes = QCheckBox("Suppress overlaps across different classes")
        nms_classes.setChecked(self.settings.nms_class_agnostic)
        nms_classes.toggled.connect(lambda state: self.change("nms_class_agnostic", state))
        self._discrete_registry.register('discrete.nms_class_agnostic.1', 'nms_class_agnostic', nms_classes)
        self.setting(layout, "Class-agnostic NMS", "Off is standard class-aware NMS. Enable when your model emits the same object under several competing classes.", nms_classes)
        self.setting(layout, "Maximum detections", "Hard cap applied after duplicate suppression to keep tracking work bounded.", self.range("max_detections", 1, 5000))
        layout=class_card.body
        classes = QLineEdit(self.settings.class_filter)
        classes.setPlaceholderText("All classes (or: 0, 2, 5)")
        classes.editingFinished.connect(lambda: self.change("class_filter", classes.text().strip()))
        self.class_filter_edit = classes
        class_row = QWidget(); class_layout = QVBoxLayout(class_row); class_layout.setContentsMargins(0, 0, 0, 0)
        classes.setMinimumWidth(220)
        class_layout.addWidget(classes, 1)
        edit_classes = QPushButton('Select classes and aliases'); edit_classes.setObjectName('editModelClasses')
        edit_classes.setProperty('missionIcon','tag')
        edit_classes.clicked.connect(self.edit_model_classes); class_layout.addWidget(edit_classes)
        self.setting(layout, "Allowed class IDs", "Leave empty for every class, or enter comma-separated numeric class IDs.", class_row)
        rules=QPushButton('Open class rules')
        rules.setProperty('missionIcon','tag')
        rules.clicked.connect(lambda:self._open_targeting_class_rules())
        layout.addWidget(rules)
        page_layout.addStretch(1)
        return page

    def _open_targeting_class_rules(self):
        self.show_page('selection')
        for tabs in self.pages['selection'].findChildren(SecondarySettingsTabs):
            if 'Class rules' in tabs._titles:
                tabs.setCurrentIndex(tabs.index_of('Class rules'));break

    def selection_page(self) -> QWidget:
        self._mission_target_routes_ready=False
        subtitle = "Choose how a track is acquired, held through short gaps, and compared against a challenger."
        page, layout = self.new_page("Targeting", subtitle)
        deck = QFrame()
        deck.setObjectName("policyCommandDeck")
        deck_layout = QHBoxLayout(deck)
        self.policy_deck_layout = deck_layout
        deck_layout.setContentsMargins(0, 0, 0, 0)
        deck_layout.setSpacing(0)

        main_surface = QFrame()
        main_surface.setObjectName("policyMainSurface")
        main_layout = QVBoxLayout(main_surface)
        main_layout.setContentsMargins(20, 17, 20, 17)
        main_layout.setSpacing(10)
        deck_layout.addWidget(main_surface, 52)
        self._target_preview_layout=main_layout

        headline = QHBoxLayout()
        self.policy_headline_layout = headline
        headline.setSpacing(9)
        title_copy = QVBoxLayout()
        title_copy.setSpacing(2)
        active_workspace = QLabel("ACTIVE WORKSPACE")
        active_workspace.setObjectName("pageKicker")
        active_workspace.hide()
        title_copy.addWidget(active_workspace)
        policy_title = QLabel("Live preview")
        policy_title.setObjectName("policyWorkspaceTitle")
        title_copy.addWidget(policy_title)
        policy_detail = QLabel("Selection, retention, and anchor response remain visible beside the live preview.")
        policy_detail.setObjectName("policyWorkspaceSubtitle")
        policy_detail.setWordWrap(True)
        policy_detail.hide()
        title_copy.addWidget(policy_detail)
        headline.addLayout(title_copy, 1)

        def live_metric(title: str, detail: str, accent: str = "blue") -> tuple[QFrame, QLabel]:
            card = QFrame()
            card.setObjectName("policyTopMetric")
            if accent != "blue":
                card.setProperty("accent", accent)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 12, 8)
            card_layout.setSpacing(1)
            value = QLabel("--")
            value.setObjectName("policyTrackMetric" if title == "ACTIVE TRACK" else "policyConfidenceMetric")
            caption = QLabel(detail)
            caption.setObjectName("policyMetricDetail")
            card_layout.addWidget(value)
            card_layout.addWidget(caption)
            card.setToolTip(title.title())
            return card, value

        track_card, self.policy_live_track = live_metric("ACTIVE TRACK", "Active track")
        confidence_card, self.policy_live_confidence = live_metric("CONFIDENCE", "Confidence", "pink")
        live_metrics = QWidget()
        live_metrics_layout = QHBoxLayout(live_metrics)
        live_metrics_layout.setContentsMargins(0, 0, 0, 0)
        live_metrics_layout.addWidget(track_card)
        live_metrics_layout.addWidget(confidence_card)
        headline.addWidget(live_metrics)
        main_layout.addLayout(headline)

        preview_shell = QFrame()
        self._shared_preview_shell=preview_shell
        preview_shell.setObjectName("settingsCard")
        preview_layout = QVBoxLayout(preview_shell)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)
        self.policy_preview_view = PreviewCanvas()
        self.policy_preview_view.setObjectName("policyPreviewSurface")
        self.policy_preview_view.setMinimumHeight(230)
        preview_layout.addWidget(self.policy_preview_view)
        preview_footer = QFrame()
        preview_footer.setObjectName("policyPreviewFooter")
        footer_layout = QHBoxLayout(preview_footer)
        footer_layout.setContentsMargins(12, 8, 12, 8)
        footer_layout.setSpacing(10)
        preview_caption = QLabel("Live annotated preview")
        self.policy_preview_caption = preview_caption
        preview_caption.setObjectName("policyPreviewCaption")
        self.policy_preview_status = QLabel("Waiting for capture")
        self.policy_preview_status.setObjectName("policyPreviewStatus")
        footer_layout.addWidget(preview_caption)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.policy_preview_status)
        preview_layout.addWidget(preview_footer)
        main_layout.addWidget(preview_shell)

        radii = QGridLayout()
        self.policy_radii_layout = radii
        self.policy_radius_cards = []
        self._policy_metric_columns = None
        radii.setSpacing(8)

        def radius_metric(caption: str, field: str) -> QLabel:
            card = QFrame()
            card.setObjectName("metricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(2)
            caption_label = QLabel(caption)
            caption_label.setObjectName("policyMetricCaption")
            value = QLabel()
            value.setObjectName("policyMetricValue")
            card_layout.addWidget(caption_label)
            card_layout.addWidget(value)
            self.policy_radius_cards.append(card)
            radii.addWidget(card, 0, len(self.policy_radius_cards) - 1)
            return value

        self.policy_acquisition_metric = radius_metric("ACQUISITION", "acquisition_radius")
        self.policy_near_metric = radius_metric("NEAR RADIUS", "near_radius")
        self.policy_snap_metric = radius_metric("SNAP RADIUS", "snap_radius")
        self.policy_retention_metric = radius_metric("RETENTION", "retention_radius")
        main_layout.addLayout(radii)

        inspector = QFrame()
        self.policy_inspector = inspector
        inspector.setObjectName("policyInspector")
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(15, 14, 15, 14)
        inspector_layout.setSpacing(10)
        deck_layout.addWidget(inspector,48)

        inspector_tabs = QHBoxLayout()
        inspector_tabs.setContentsMargins(0, 0, 0, 0)
        inspector_tabs.setSpacing(2)
        self.targeting_inspector_buttons: list[QPushButton] = []
        for tab_index, title in enumerate(("Capture", "Policy", "Tuning")):
            button = QPushButton(title)
            button.setObjectName("secondaryTabButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, index=tab_index: self.set_targeting_inspector_tab(index))
            self.targeting_inspector_buttons.append(button)
            inspector_tabs.addWidget(button, 1)
        inspector_layout.addLayout(inspector_tabs)
        from mission_layout import CurrentPageStack
        self.targeting_inspector_stack = CurrentPageStack()
        inspector_layout.addWidget(self.targeting_inspector_stack, 1)

        def inspector_page() -> tuple[QScrollArea, QVBoxLayout]:
            from mission_layout import InventoryScroll
            scroll = InventoryScroll()
            scroll.setObjectName("settingsTabScroll")
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            content = QWidget()
            content.setObjectName("settingsTabContent")
            page_layout = QVBoxLayout(content)
            page_layout.setContentsMargins(0, 4, 0, 0)
            page_layout.setSpacing(10)
            scroll.setWidget(content)
            self.targeting_inspector_stack.addWidget(scroll)
            return scroll, page_layout

        _capture_scroll, capture_inspector = inspector_page()
        self._build_capture_inspector(capture_inspector)
        _policy_scroll, policy_inspector = inspector_page()
        inspector_head = QHBoxLayout()
        inspector_title = QLabel("Policy controls")
        inspector_title.setObjectName("policyInspectorTitle")
        reset = QPushButton("Reset")
        reset.setObjectName("policyReset")
        reset.clicked.connect(self.reset_target_policy)
        inspector_head.addWidget(inspector_title)
        inspector_head.addStretch(1)
        inspector_head.addWidget(reset)
        policy_inspector.addLayout(inspector_head)

        def inspector_card(title: str) -> QVBoxLayout:
            card = QFrame()
            card.setObjectName("policyInspectorCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(4, 11, 4, 14)
            card_layout.setSpacing(7)
            heading = QLabel(title)
            heading.setObjectName("policyCardTitle")
            card_layout.addWidget(heading)
            policy_inspector.addWidget(card)
            return card_layout

        strategy_card = inspector_card("STRATEGY")
        strategy = QComboBox()
        for text in ('Closest to frame center', 'Highest detection confidence', 'Largest visible object', 'Smallest visible object', 'Lowest movement'):
            strategy.addItem(text)
        strategy.setCurrentIndex(self.settings.strategy)
        strategy.currentIndexChanged.connect(lambda index: self.change('strategy', index))
        strategy_card.addWidget(self._tuning_lock.wrap(strategy))

        anchor_card = inspector_card("ANCHOR")
        anchor = QComboBox()
        for title, value in (("Box center", "center"), ("Top edge", "top"), ("Bottom edge", "bottom"), ("Upper quarter", "upper-quarter")):
            anchor.addItem(title, value)
        anchor.setCurrentIndex(max(0, anchor.findData(self.settings.anchor_mode)))
        anchor.currentIndexChanged.connect(lambda index: self.change("anchor_mode", str(anchor.itemData(index))))
        self._discrete_registry.register('discrete.anchor_mode.1', 'anchor_mode', anchor)
        anchor_card.addWidget(self._tuning_lock.wrap(anchor))

        def compact_range(title: str, field: str, low: float, high: float, decimals: int = 0, suffix: str = "") -> RangeControl:
            row = QFrame()
            row.setObjectName("policyQuickRow")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 1, 0, 0)
            row_layout.setSpacing(3)
            line = QHBoxLayout()
            label = QLabel(title)
            label.setObjectName("policyControlLabel")
            value = QLabel()
            value.setObjectName("policyControlValue")
            line.addWidget(label)
            line.addStretch(1)
            line.addWidget(value)
            control = self.range(field, low, high, decimals, suffix)
            control.setObjectName("policyInspectorRange")
            control.pass_slider_wheel_to_parent()
            control.changed.connect(lambda number, target=value, unit=suffix: target.setText(f"{number:g}{unit}"))
            value.setText(f"{float(getattr(self.settings, field)):g}{suffix}")
            add_guide_button(self, line, title, control)
            row_layout.addLayout(line)
            row_layout.addWidget(self._tuning_lock.wrap(control))
            anchor_card.addWidget(row)
            return control

        self.policy_anchor_x_control = compact_range("Horizontal offset", "anchor_x", -1.0, 1.0, 2)
        self.policy_anchor_y_control = compact_range("Vertical offset", "anchor_y", -1.0, 1.0, 2)
        from visual_geometry_ui import open_anchor_editor
        editor = QPushButton("Visual anchor editor…")
        editor.setObjectName('openAnchorEditor')
        editor.clicked.connect(lambda: open_anchor_editor(self))
        anchor_card.addWidget(self._tuning_lock.wrap(editor))

        lock_card = inspector_card("LOCK BEHAVIOR")
        occlusion = QCheckBox("Occlusion recovery")
        occlusion.setChecked(self.settings.occlusion_handling)
        occlusion.toggled.connect(lambda state: self.change("occlusion_handling", state))
        self._discrete_registry.register('discrete.occlusion_handling.1', 'occlusion_handling', occlusion)
        lock_card.addWidget(self._tuning_lock.wrap(occlusion))
        switching_row = QFrame()
        switching_layout = QVBoxLayout(switching_row)
        switching_layout.setContentsMargins(0, 0, 0, 0)
        switching_layout.setSpacing(3)
        switching_label = QHBoxLayout()
        switching_title = QLabel("Switch tolerance")
        switching_title.setObjectName("policyControlLabel")
        self.policy_switching_value = QLabel(f"{self.settings.switching:g}")
        self.policy_switching_value.setObjectName("policyControlValue")
        switching_label.addWidget(switching_title)
        switching_label.addStretch(1)
        switching_label.addWidget(self.policy_switching_value)
        self.policy_switching_control = self.range("switching", 0.0, 0.95, 2)
        self.policy_switching_control.setObjectName("policyInspectorRange")
        self.policy_switching_control.pass_slider_wheel_to_parent()
        self.policy_switching_control.changed.connect(lambda value: self.policy_switching_value.setText(f"{value:g}"))
        add_guide_button(self, switching_label, "Switch tolerance", self.policy_switching_control)
        switching_layout.addLayout(switching_label)
        switching_layout.addWidget(self._tuning_lock.wrap(self.policy_switching_control))
        lock_card.addWidget(switching_row)
        _tuning_scroll, tuning_inspector = inspector_page()
        self._build_tuning_inspector(tuning_inspector)
        for sidebar_scroll in (_capture_scroll, _policy_scroll, _tuning_scroll):
            content = sidebar_scroll.widget()
            if content is not None:
                for control in content.findChildren(RangeControl):
                    control.pass_slider_wheel_to_parent(sidebar_scroll)
        policy_inspector.addStretch(1)
        self.set_targeting_inspector_tab(0)
        layout.addWidget(deck,1)

        tabs = self.settings_tabs(
            layout,
            "Policy",
            "Tracking",
            "Compensation",
            "Preview",
            "Annotations",
            "Desktop overlay",
            "Class rules",
            "Manual lock",
            minimum_height=500,
            owner_attribute="targeting_tabs",
            fixed_titles=("Class rules",),
        )
        geometry = tabs["Policy"]
        continuity = tabs["Policy"]
        manual = tabs["Manual lock"]
        self.section(geometry, "Target geometry")
        zones_button = QPushButton("Edit exclusion zones")
        zones_button.setObjectName('editExclusionZones')
        from exclusion_editor import open_editor as edit_zones
        zones_button.clicked.connect(lambda: edit_zones(self))
        self.setting(geometry, "Target exclusion zones", "Exclude aiming points inside profile-saved capture-pixel rectangles. Stop capture before editing.", zones_button)
        for title, field in (("Acquisition confidence", "acquire_confidence"), ("Retention confidence", "retain_confidence")):
            self.setting(continuity, title, "0 follows Detection confidence. Detection filtering remains the minimum. New targets must meet both thresholds; the current target may use retention alone. Missing or stale observations never authorize movement.", batch3_spin(self, field))
        self.target_confidence_summary = QLabel()
        self.target_confidence_summary.setObjectName('detail')
        self.target_confidence_summary.setWordWrap(True)
        self.target_confidence_summary.setProperty('_vaporlock_i18n_skip', True)
        continuity.addWidget(self.target_confidence_summary)
        self.refresh_target_confidence_summary()
        confidence_help = QLabel('0 follows Detection confidence. Detection filtering remains the minimum. New targets must meet both thresholds; the current target may use retention alone. Missing or stale observations never authorize movement.')
        confidence_help.setObjectName('detail'); confidence_help.setWordWrap(True)
        continuity.addWidget(confidence_help)
        fov_shape = QComboBox()
        fov_shape.addItem("Circular FOV", "circle")
        fov_shape.addItem("Rectangular FOV", "rectangle")
        fov_shape.setCurrentIndex(max(0, fov_shape.findData(self.settings.fov_shape)))
        fov_shape.currentIndexChanged.connect(lambda index: self.change("fov_shape", str(fov_shape.itemData(index))))
        self._discrete_registry.register('discrete.fov_shape.1', 'fov_shape', fov_shape)
        self.setting(geometry, "Targeting FOV shape", "Circular uses true radial distance. Rectangular uses equal horizontal and vertical bounds.", fov_shape)
        self.setting(geometry, "Snap radius", "Immediate anchor-association zone. A visible target here is selected ahead of near and outer contenders, unless an explicit class priority or watchlist rule wins.", self.range("snap_radius", 0, 1000, 0, " px"))
        self.setting(geometry, "Near radius", "Contender holding zone. A new target here gets time to reach the minimum track age before an outer-FOV candidate can take the vacant anchor.", self.range("near_radius", 0, 5000, 0, " px"))
        self.setting(geometry, "Acquisition radius", "Wide new-target scan boundary. For a predictable hierarchy, keep this value at or above the Near and Snap radii.", self.range("acquisition_radius", 1, 10000, 0, " px"))
        self.setting(geometry, "Retention radius", "While a selected track is lost, reserve its last confirmed anchor through the lost-target hold interval. Nearby same-class recovery also respects the Occlusion recovery radius.", self.range("retention_radius", 1, 10000, 0, " px"))
        self.section(continuity, "Lock continuity")
        self.setting(continuity, "Minimum lock duration", "For this time after acquisition, the selected subject is retained even if another candidate briefly scores better.", self.range("lock_persistence_ms", 0, 30000, 0, " ms"))
        self.setting(continuity, "Lost-target hold", "Reserve identity after the last real observation. Missing targets never authorize movement.", self.range("lost_target_timeout_ms", 0, 10000, 0, " ms"))
        self.setting(continuity, "Minimum track age", "Matched observations required for acquisition, unless the explicit snap bypass is enabled.", self.range("minimum_track_age", 1, 120, 0, " frames"))
        snap_bypass = QCheckBox("Allow immediate snap acquisition")
        snap_bypass.setChecked(self.settings.snap_bypasses_confirmation)
        snap_bypass.toggled.connect(lambda state: self.change("snap_bypasses_confirmation", state))
        self._discrete_registry.register('discrete.snap_bypasses_confirmation.1', 'snap_bypasses_confirmation', snap_bypass)
        self.setting(continuity, "Snap confirmation bypass", "When enabled, a fresh target inside Snap may bypass minimum observation count. Disable to reject single-observation candidates everywhere.", snap_bypass)
        self.setting(continuity, "Near-candidate wait", "Maximum wait for immature near contenders. Repeated replacements do not restart this budget.", self.range("near_candidate_wait_ms", 0, 1000, 0, " ms"))
        watchlist = QLineEdit(self.settings.watchlist)
        watchlist.setPlaceholderText("Optional preferred class IDs, for example: 0, 2")
        watchlist.editingFinished.connect(lambda: self.change("watchlist", watchlist.text().strip()))
        self.setting(continuity, "Preferred class watchlist", "When populated, listed classes win ties and switching decisions. It does not hide other allowed classes, so an empty result can still recover safely.", watchlist)
        continuity.addStretch(1)
        self.section(tabs["Class rules"], "Class priorities and anchors")
        self.class_policy_editor = ClassPolicyEditor(self.settings.class_policies, lambda rows: self.change("class_policies", rows))
        tabs["Class rules"].addWidget(self.feature_control(self.class_policy_editor))
        self.section(manual, "Manual track lock")
        manual_lock = QCheckBox("Enable manual target pinning")
        manual_lock.setChecked(self.settings.manual_target_lock_enabled)
        manual_lock.toggled.connect(lambda state: self.change("manual_target_lock_enabled", state))
        self._discrete_registry.register('discrete.manual_target_lock_enabled.1', 'manual_target_lock_enabled', manual_lock)
        self.setting(manual, "Manual target lock", "Press the selected input once to pin the active target. Press again to return to automatic selection. A pinned target still releases when it exceeds the configured lost-target safety limits.", self.feature_control(manual_lock))
        lock_source = QComboBox()
        for title, value in (("Keyboard", "keyboard"), ("Xbox controller", "controller"), ("Keyboard or controller", "either")):
            lock_source.addItem(title, value)
        lock_source.setCurrentIndex(max(0, lock_source.findData(self.settings.manual_lock_source)))
        lock_source.currentIndexChanged.connect(lambda index: self.change("manual_lock_source", str(lock_source.itemData(index))))
        self._discrete_registry.register('discrete.manual_lock_source.1', 'manual_lock_source', lock_source)
        self.setting(manual, "Lock input source", "Choose the device used to pin or release the current target. Controller input uses the same controller selection configured under Output.", self.feature_control(lock_source))
        lock_key = QComboBox()
        for title, value in (("Middle mouse button", "middle"), ("Left mouse button", "left"), ("Right mouse button", "right"), ("Left Shift", "shift"), ("Left Control", "control"), ("Space", "space"), ("F", "f"), ("G", "g")):
            lock_key.addItem(title, value)
        lock_key.setCurrentIndex(max(0, lock_key.findData(self.settings.manual_lock_key)))
        lock_key.currentIndexChanged.connect(lambda index: self.change("manual_lock_key", str(lock_key.itemData(index))))
        self._discrete_registry.register('discrete.manual_lock_key.1', 'manual_lock_key', lock_key)
        self.setting(manual, "Keyboard lock input", "Single press to pin the current target; press again to release it.", self.feature_control(lock_key))
        lock_controller = QComboBox()
        for title, value in (
            ("A", "a"), ("B", "b"), ("X", "x"), ("Y", "y"),
            ("Left shoulder", "left_shoulder"), ("Right shoulder", "right_shoulder"),
            ("Left trigger", "left_trigger"), ("Right trigger", "right_trigger"),
            ("Left stick press", "left_thumb"), ("Right stick press", "right_thumb"),
            ("View / Back", "back"), ("Menu / Start", "start"),
            ("D-pad up", "dpad_up"), ("D-pad down", "dpad_down"),
            ("D-pad left", "dpad_left"), ("D-pad right", "dpad_right"),
        ):
            lock_controller.addItem(title, value)
        lock_controller.setCurrentIndex(max(0, lock_controller.findData(self.settings.manual_lock_controller_binding)))
        lock_controller.currentIndexChanged.connect(lambda index: self.change("manual_lock_controller_binding", str(lock_controller.itemData(index))))
        self._discrete_registry.register('discrete.manual_lock_controller_binding.1', 'manual_lock_controller_binding', lock_controller)
        self.setting(manual, "Controller lock input", "Single press to pin the current target; press again to release it.", self.feature_control(lock_controller))
        pin_button = QPushButton("Pin or release current target")
        pin_button.clicked.connect(self.hotkey_toggle_target_lock)
        self.setting(manual, "Current target action", "Send an immediate pin or release request to the active native engine. It works only while manual target lock is enabled and a live target is selected.", self.feature_control(pin_button))
        self.manual_lock_status = QLabel("Manual target lock is waiting for a live target.")
        self.manual_lock_status.setObjectName("detail")
        self.manual_lock_status.setWordWrap(True)
        manual.addWidget(self.manual_lock_status)
        manual.addStretch(1)
        self._build_tracking_controls(tabs["Tracking"])
        self._build_compensation_controls(tabs["Compensation"])
        self._build_preview_controls(tabs["Preview"], tabs["Annotations"], tabs["Desktop overlay"])
        # The top task rail owns the selected task; its existing controls live
        # beside the same preview. Policy's controls belong in Policy; other
        # tasks do not display a stale Capture/Policy/Tuning selection.
        task_tabs=self.targeting_tabs
        task_tabs.layout().removeWidget(task_tabs._tab_scroll)
        layout.insertWidget(0,task_tabs._tab_scroll)
        layout.removeWidget(task_tabs)
        inspector_layout.addWidget(task_tabs,1)
        task_tabs.setMinimumHeight(0)
        policy_scroll=task_tabs._stack.widget(0)
        policy_content=policy_scroll.takeWidget()
        policy_body=self.targeting_inspector_stack.widget(1).widget().layout()
        policy_body.insertWidget(max(0,policy_body.count()-1),policy_content)
        def show_task(index):
            self.targeting_inspector_stack.setVisible(index==0)
            task_tabs.setVisible(index!=0)
            for button in self.targeting_inspector_buttons:button.setVisible(index==0)
            if index==0:self.set_targeting_inspector_tab(1)
            self.update_targeting_layout()
            inspector.updateGeometry()
        task_tabs._stack.currentChanged.connect(show_task)
        self._mission_target_routes_ready=True
        show_task(task_tabs.currentIndex())
        self.refresh_policy_command_deck()
        return page

    def refresh_policy_command_deck(self) -> None:
        """Synchronize the live command-deck readouts without rebuilding the page."""
        values = (
            ("policy_acquisition_metric", self.settings.acquisition_radius),
            ("policy_near_metric", self.settings.near_radius),
            ("policy_snap_metric", self.settings.snap_radius),
            ("policy_retention_metric", self.settings.retention_radius),
        )
        for attribute, value in values:
            if hasattr(self, attribute):
                getattr(self, attribute).setText(f"{int(value)} px")
        if hasattr(self, "policy_switching_value"):
            self.policy_switching_value.setText(f"{self.settings.switching:.2f}")

    def set_targeting_inspector_tab(self, index: int) -> None:
        if not hasattr(self, "targeting_inspector_stack"):
            return
        if not 0 <= index < self.targeting_inspector_stack.count():
            return
        if getattr(self,'_mission_target_routes_ready',False):self.targeting_tabs.setCurrentIndex(0)
        self.targeting_inspector_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.targeting_inspector_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            button.blockSignals(False)

    def reset_target_policy(self) -> None:
        defaults = Settings()
        for field in (
            "strategy", "fov_shape", "snap_radius", "near_radius", "acquisition_radius", "retention_radius",
            "switching", "lock_persistence_ms", "lost_target_timeout", "minimum_track_age", "anchor_mode",
            "anchor_x", "anchor_y", "watchlist", "occlusion_handling", "lost_target_timeout_ms",
            "snap_bypasses_confirmation", "near_candidate_wait_ms",
        ):
            self.change(field, getattr(defaults, field))
        self.rebuild_pages()

    def _build_tracking_controls(self, layout: QVBoxLayout) -> None:
        """Keep track continuity beside policy controls instead of behind another page."""
        self.section(layout, "Identity continuity")
        self.setting(layout, "Match overlap", "Minimum overlap needed to preserve an existing identity.", self.range("match_iou", 0.01, 0.99, 2))
        self.setting(layout, "Track memory", "Elapsed retention time, independent of inference rate.", self.range("track_retention_ms", 0, 10000, 0, " ms"))
        self.setting(layout, "Velocity smoothing", "Retained fraction at a 60 Hz reference, normalized by observation time. Higher values add delay.", self.range("smoothing", 0.0, 0.95, 2))
        self.setting(layout, "Box position smoothing", "Stabilize displayed detection boxes. Anchor stabilization independently filters real observations so these filters do not stack their delays.", self.range("box_smoothing", 0.0, 0.95, 2))
        self.setting(layout, "Anchor stabilization", "Calm small box-anchor shifts before target policy and movement use them. This leaves the detection outline responsive while the lock settles cleanly.", self.range("anchor_smoothing", 0.0, 0.98, 2))
        self.setting(layout, "Reference settle radius", "Hold the anchor at the aiming reference within this tolerance. Use zero for no extra settling tolerance.", self.range("anchor_settle_radius", 0.0, 50.0, 2, " px"))
        occlusion = QCheckBox("Enabled")
        occlusion.setChecked(self.settings.occlusion_handling)
        occlusion.toggled.connect(lambda state: self.change("occlusion_handling", state))
        self._discrete_registry.register('discrete.occlusion_handling.2', 'occlusion_handling', occlusion)
        self.setting(layout, "Occlusion recovery", "Reconnect a briefly missing target to a nearby same-class detection before releasing the established lock.", occlusion)
        self.setting(layout, "Occlusion grace", "Recovery window after the last observation. Ambiguous identities and manual pins are not substituted.", self.range("occlusion_grace_ms", 0, 10000, 0, " ms"))
        self.setting(layout, "Reacquisition radius", "Maximum anchor distance for reconnecting a target after a detector gap.", self.range("occlusion_reacquire_radius", 0, 5000, 0, " px"))
        self.section(layout, "Prediction")
        predict_lost = QCheckBox("Predict track position between detections")
        predict_lost.setChecked(self.settings.predict_lost)
        predict_lost.toggled.connect(lambda state: self.change("predict_lost", state))
        self._discrete_registry.register('discrete.predict_lost.1', 'predict_lost', predict_lost)
        self.setting(layout, "Motion prediction", "Confidence-weighted prediction for fresh observations. Missing targets retain identity only.", self.feature_control(predict_lost))
        self.setting(layout, "Prediction lead", "Extra lead beyond measured observation age. Stops, reversals and uncertain identity reset the estimate.", self.feature_control(self.range("prediction_lead_ms", 0, 1000, 0, " ms")))
        self.setting(layout, "Prediction horizon", "Maximum observation age plus extra lead. The separate observation-expiry limit still applies.", self.feature_control(self.range("prediction_limit_ms", 1, 5000, 0, " ms")))
        self._build_prediction_bounds(layout)
        layout.addStretch(1)

    def _build_prediction_bounds(self, layout: QVBoxLayout) -> None:
        for field, title, detail, maximum, suffix in (
            ("prediction_max_speed", "Prediction speed bound", "Maximum estimated velocity per axis in capture pixels per second.", 10000, " px/s"),
            ("prediction_max_acceleration", "Prediction acceleration bound", "Maximum estimated acceleration per axis. Stops, reversals and reacquisition clear acceleration.", 50000, " px/s^2"),
            ("prediction_max_distance", "Maximum prediction offset", "Radial limit on how far prediction may lead the observed anchor.", 500, " px"),
            ("prediction_noise_floor", "Motion noise floor", "Ignore movement below this displacement across the recent 50 ms observation window.", 10, " px"),
        ):
            self.setting(layout, title, detail, self.feature_control(self.range(field, 0.0, maximum, 2, suffix)))

    def _build_preview_controls(self, stream: QVBoxLayout, annotations: QVBoxLayout, desktop: QVBoxLayout) -> None:
        self.section(stream, "Preview stream")
        enabled = QCheckBox("Enabled")
        enabled.setChecked(self.settings.preview_enabled)
        enabled.toggled.connect(lambda state: self.change("preview_enabled", state))
        self._discrete_registry.register('discrete.preview_enabled.1', 'preview_enabled', enabled)
        self.setting(stream, "Live preview", "Emit compressed annotated frames from the native engine only while a session is running. Disabling this removes preview encoding work from the capture pipeline.", enabled)
        self.setting(stream, "Preview rate", "Maximum annotated frames sent to this control center each second.", self.range("preview_fps", 1, 30, 0, " fps"))
        self.setting(stream, "JPEG quality", "Compression quality for debug frames. Higher values retain finer detail and use more interprocess bandwidth.", self.range("preview_quality", 30, 100, 0))
        snapshot = QPushButton("Save annotated PNG")
        snapshot.clicked.connect(self.save_annotated_snapshot)
        self.setting(stream, "Preview snapshot", "Save the most recent native annotated debug frame as a PNG in this Windows account's Vapor Lock screenshots folder.", self.feature_control(snapshot))
        stream.addStretch(1)

        self.section(annotations, "Annotations")
        overlay = QCheckBox("Show annotations")
        overlay.setChecked(self.settings.overlay_enabled)
        overlay.toggled.connect(lambda state: self.change("overlay_enabled", state))
        self._discrete_registry.register('discrete.overlay_enabled.1', 'overlay_enabled', overlay)
        self.setting(annotations, "Detection overlay", "Draw active tracking information over the debug preview. Source frames written to the training dataset are always unannotated.", overlay)
        boxes = QCheckBox("Show tracked boxes")
        boxes.setChecked(self.settings.overlay_boxes)
        boxes.toggled.connect(lambda state: self.change("overlay_boxes", state))
        self._discrete_registry.register('discrete.overlay_boxes.1', 'overlay_boxes', boxes)
        self.setting(annotations, "Track boxes", "Draw an outline for each retained track and highlight the selected lock.", boxes)
        motion = QCheckBox("Show prediction vector")
        motion.setChecked(self.settings.overlay_motion)
        motion.toggled.connect(lambda state: self.change("overlay_motion", state))
        self._discrete_registry.register('discrete.overlay_motion.1', 'overlay_motion', motion)
        self.setting(annotations, "Motion prediction", "Draw the current selected anchor and its configured prediction lead in the debug preview.", motion)
        fov = QCheckBox("Show snap, near, and acquisition guides")
        fov.setChecked(self.settings.overlay_fov)
        fov.toggled.connect(lambda state: self.change("overlay_fov", state))
        self._discrete_registry.register('discrete.overlay_fov.1', 'overlay_fov', fov)
        self.setting(annotations, "Targeting FOV", "Draw the active circular or rectangular target zones in the annotated preview.", fov)
        masks = QCheckBox("Show instance masks")
        masks.setChecked(self.settings.overlay_segmentation_masks)
        masks.toggled.connect(lambda state: self.change("overlay_segmentation_masks", state))
        self._discrete_registry.register('discrete.overlay_segmentation_masks.1', 'overlay_segmentation_masks', masks)
        self.setting(annotations, "Segmentation masks", "Draw decoded instance masks when the selected ONNX model provides prototype-mask output. Standard detection models continue to use boxes.", masks)
        self.setting(annotations, "Mask limit", "Maximum retained masks decoded for one preview frame. Lower values reduce post-processing work on crowded scenes.", self.range("segmentation_mask_limit", 0, 32, 0))
        self.setting(annotations, "Mask threshold", "Minimum decoded mask probability used for the visible instance boundary.", self.range("segmentation_mask_threshold", 0.01, 0.99, 2))
        annotations.addStretch(1)

        self.section(desktop, "On-screen overlay")
        desktop_enabled = QCheckBox("Enabled")
        desktop_enabled.setChecked(self.settings.desktop_overlay_enabled)
        desktop_enabled.toggled.connect(lambda state: self.change("desktop_overlay_enabled", state))
        self._discrete_registry.register('discrete.desktop_overlay_enabled.1', 'desktop_overlay_enabled', desktop_enabled)
        self.setting(desktop, "Click-through desktop overlay", "Draw live native track metadata directly over the captured region without opening Control Center. The overlay contains no captured pixels and is excluded from capture when Windows supports it.", self.feature_control(desktop_enabled))
        desktop_visible = QCheckBox("Visible")
        desktop_visible.setChecked(self.settings.desktop_overlay_visible)
        desktop_visible.toggled.connect(lambda state: self.change("desktop_overlay_visible", state))
        self._discrete_registry.register('discrete.desktop_overlay_visible.1', 'desktop_overlay_visible', desktop_visible)
        self.setting(desktop, "Desktop overlay visibility", "Show or hide the active overlay without stopping capture. A global shortcut can toggle this during a session.", self.feature_control(desktop_visible))
        desktop_layout = QComboBox()
        for title, value in (("Minimal selected target", "minimal"), ("Standard", "standard"), ("Diagnostics", "diagnostics")):
            desktop_layout.addItem(title, value)
        desktop_layout.setCurrentIndex(max(0, desktop_layout.findData(self.settings.desktop_overlay_layout)))
        desktop_layout.currentIndexChanged.connect(lambda index: self.change("desktop_overlay_layout", str(desktop_layout.itemData(index))))
        self._discrete_registry.register('discrete.desktop_overlay_layout.1', 'desktop_overlay_layout', desktop_layout)
        self.setting(desktop, "Overlay layout", "Minimal limits the screen to the selected subject. Standard shows all tracks. Diagnostics adds lock-recovery and track-count context.", self.feature_control(desktop_layout))
        self.setting(desktop, "Desktop overlay opacity", "Use the slider for a comfortable visible range, or type an exact opacity percentage.", self.feature_control(self.range("desktop_overlay_opacity", 25, 100, 0, " %")))
        self.setting(desktop, "Desktop overlay scale", "Scale label and annotation size. The slider covers the tested range; type a larger or smaller value for expert display tuning.", self.feature_control(self.range("desktop_overlay_scale", 0.75, 1.50, 2, " x")))
        desktop_palette = QComboBox()
        desktop_palette.addItem("Vapor", "vapor")
        desktop_palette.addItem("High contrast", "contrast")
        desktop_palette.setCurrentIndex(max(0, desktop_palette.findData(self.settings.desktop_overlay_palette)))
        desktop_palette.currentIndexChanged.connect(lambda index: self.change("desktop_overlay_palette", str(desktop_palette.itemData(index))))
        self._discrete_registry.register('discrete.desktop_overlay_palette.1', 'desktop_overlay_palette', desktop_palette)
        self.setting(desktop, "Overlay colors", "Choose the standard Vapor palette or a high-contrast palette for bright displays and projection surfaces.", self.feature_control(desktop_palette))
        self.setting(desktop, "Track color", "Color used for non-selected track boxes and labels. This custom value is stored with the current profile.", self.feature_control(self.color_editor("desktop_overlay_track_color")))
        self.setting(desktop, "Selected target color", "Color used for the active selected target and its prediction guide.", self.feature_control(self.color_editor("desktop_overlay_selected_color")))
        self.setting(desktop, "FOV and boundary color", "Color used for the outer targeting guide and capture-region boundary.", self.feature_control(self.color_editor("desktop_overlay_fov_color")))
        self.setting(desktop, "Status text color", "Color used for the compact desktop-overlay status strip.", self.feature_control(self.color_editor("desktop_overlay_text_color")))
        for title, field, detail in (
            ("Capture boundary", "desktop_overlay_capture_bounds", "Draw a thin guide around the exact selected capture region."),
            ("Desktop labels", "desktop_overlay_labels", "Show the track identity, model label, and confidence beside each live detection."),
            ("Desktop status", "desktop_overlay_status", "Show a compact Vapor Lock status strip in the upper-left corner of the overlay."),
            ("Desktop motion guide", "desktop_overlay_motion", "Optional selected-target anchor and prediction visualization."),
        ):
            control = QCheckBox("Enabled")
            control.setChecked(bool(getattr(self.settings, field)))
            control.toggled.connect(lambda state, name=field: self.change(name, state))
            self._discrete_registry.register({'desktop_overlay_capture_bounds': 'discrete.desktop_overlay_capture_bounds.1', 'desktop_overlay_labels': 'discrete.desktop_overlay_labels.1', 'desktop_overlay_status': 'discrete.desktop_overlay_status.1', 'desktop_overlay_motion': 'discrete.desktop_overlay_motion.1'}[field], field, control)
            self.setting(desktop, title, detail, self.feature_control(control))
        desktop.addStretch(1)

    def tracking_page(self) -> QWidget:
        subtitle = "Tune identity continuity and velocity estimation."
        page, layout = self.new_page("Tracking", subtitle)
        self.section(layout, "Identity continuity")
        self.setting(layout, "Match overlap", "Minimum overlap needed to preserve an existing identity.", self.range("match_iou", 0.01, 0.99, 2))
        self.setting(layout, "Track memory", "Elapsed retention time, independent of inference rate.", self.range("track_retention_ms", 0, 10000, 0, " ms"))
        self.setting(layout, "Velocity smoothing", "Retained fraction at a 60 Hz reference, normalized by observation time. Higher values add delay.", self.range("smoothing", 0.0, 0.95, 2))
        self.setting(layout, "Box position smoothing", "Stabilize displayed detection boxes. Anchor stabilization independently filters real observations so these filters do not stack their delays.", self.range("box_smoothing", 0.0, 0.95, 2))
        self.setting(layout, "Anchor stabilization", "Calm small box-anchor shifts before target policy and movement use them. This leaves the detection outline responsive while the lock settles cleanly.", self.range("anchor_smoothing", 0.0, 0.98, 2))
        self.setting(layout, "Reference settle radius", "Hold the anchor at the aiming reference within this tolerance. Use zero for no extra settling tolerance.", self.range("anchor_settle_radius", 0.0, 50.0, 2, " px"))
        occlusion = QCheckBox("Enabled")
        occlusion.setChecked(self.settings.occlusion_handling)
        occlusion.toggled.connect(lambda state: self.change("occlusion_handling", state))
        self.setting(layout, "Occlusion recovery", "Reconnect a briefly missing target to a nearby same-class detection before releasing the established lock.", occlusion)
        self.setting(layout, "Occlusion grace", "Recovery window after the last observation. Ambiguous identities and manual pins are not substituted.", self.range("occlusion_grace_ms", 0, 10000, 0, " ms"))
        self.setting(layout, "Reacquisition radius", "Maximum anchor distance for reconnecting a target after a detector gap.", self.range("occlusion_reacquire_radius", 0, 5000, 0, " px"))
        predict_lost = QCheckBox("Predict track position between detections")
        predict_lost.setChecked(self.settings.predict_lost)
        predict_lost.toggled.connect(lambda state: self.change("predict_lost", state))
        self.setting(layout, "Motion prediction", "Confidence-weighted prediction for fresh observations. Missing targets retain identity only.", self.feature_control(predict_lost))
        self.section(layout, "Prediction")
        self.setting(layout, "Prediction lead", "Extra lead beyond measured observation age. Stops, reversals and uncertain identity reset the estimate.", self.feature_control(self.range("prediction_lead_ms", 0, 1000, 0, " ms")))
        self.setting(layout, "Prediction horizon", "Maximum observation age plus extra lead. The separate observation-expiry limit still applies.", self.feature_control(self.range("prediction_limit_ms", 1, 5000, 0, " ms")))
        layout.addStretch(1)
        return page

    def output_page(self) -> QWidget:
        page, layout = self.new_page("Movement", "Configure activation and output paths.")
        tabs = self.settings_tabs(layout, "Control", "Transport", "Events", "Diagnostics", minimum_height=540)
        from mission_layout import TaskColumns,TaskCard
        control_columns=TaskColumns(proportions=(1,1))
        for panel in (control_columns.left,control_columns.right):panel.setObjectName('missionTaskColumn')
        tabs['Control'].addWidget(control_columns)
        activation_card=TaskCard('Activation','power');controller_card=TaskCard('Controller input','games')
        tester_card=TaskCard('Live controller tester','games');state_card=TaskCard('Current movement state','pulse')
        control_columns.left_layout.addWidget(activation_card);control_columns.left_layout.addWidget(tester_card)
        control_columns.right_layout.addWidget(controller_card);control_columns.right_layout.addWidget(state_card)
        if _NATIVE_INJECTION is not None:
            self.controller_tester = ControllerTester(lambda: self.settings, lambda: self.ui_language,
                reader_factory=_NATIVE_INJECTION.services.controller_reader)
        else:
            self.controller_tester = ControllerTester(lambda: self.settings, lambda: self.ui_language)
        tester_card.body.addWidget(self.controller_tester)
        self.output_diagnostic_label = QLabel(output_diagnostics(self._last_output_data))
        self.output_diagnostic_label.setWordWrap(True)
        self.output_diagnostic_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        diagnostic_card=TaskCard('Live output path','pulse')
        tabs['Diagnostics'].addWidget(diagnostic_card)
        self.setting(diagnostic_card.body, "Output decision", "Shows every gate between input activation and a Windows movement event. Counters reset with each observation session.", self.output_diagnostic_label)
        tabs["Diagnostics"].addStretch(1)
        activation = activation_card.body
        event_card=TaskCard('Event actions','power')
        transport_card=TaskCard('Output transport','movement')
        hardware_card=TaskCard('Hardware connection','connection')
        tabs['Events'].addWidget(event_card)
        tabs['Transport'].addWidget(transport_card)
        tabs['Transport'].addWidget(hardware_card)
        event_action = event_card.body
        transport_settings = transport_card.body
        hardware_settings = hardware_card.body
        enabled = QCheckBox("Enabled")
        enabled.setChecked(self.settings.movement_enabled)
        enabled.toggled.connect(lambda state: self.change("movement_enabled", state))
        self._discrete_registry.register('discrete.movement_enabled.1', 'movement_enabled', enabled)
        self.setting(activation, "Movement output", "Disabled by default. The native process only sends movement while activated and a target is selected.", enabled)
        mode = QComboBox()
        mode.addItem("Hold activation key", "hold")
        mode.addItem("Toggle activation key", "toggle")
        mode.setCurrentIndex(0 if self.settings.activation_mode == "hold" else 1)
        mode.currentIndexChanged.connect(lambda index: self.change("activation_mode", str(mode.itemData(index))))
        self._discrete_registry.register('discrete.activation_mode.1', 'activation_mode', mode)
        self.setting(activation, "Activation mode", "Hold requires the key to remain pressed. Toggle latches on each press.", mode)
        key = QComboBox()
        for title, value in (("Right mouse button", "right"), ("Left Shift", "shift"), ("Left Control", "control"), ("Space", "space")):
            key.addItem(title, value)
        key.setCurrentIndex(max(0, key.findData(self.settings.activation_key)))
        key.currentIndexChanged.connect(lambda index: self.change("activation_key", str(key.itemData(index))))
        self._discrete_registry.register('discrete.activation_key.1', 'activation_key', key)
        self.setting(activation, "Activation key", "Key that authorizes movement output.", key)
        activation=controller_card.body
        source = QComboBox()
        for title, value in (("Keyboard", "keyboard"), ("Xbox controller", "controller"), ("Keyboard or controller", "either")):
            source.addItem(title, value)
        source.setCurrentIndex(max(0, source.findData(self.settings.activation_source)))
        source.currentIndexChanged.connect(lambda index: self.change("activation_source", str(source.itemData(index))))
        self._discrete_registry.register('discrete.activation_source.1', 'activation_source', source)
        self.setting(activation, "Activation source", "Choose the device that authorizes movement output. Controller input uses Windows' built-in XInput support and needs no separate driver bundle.", source)
        controller_slot = QComboBox()
        controller_slot.addItem("Automatic first connected controller", 0)
        for slot in range(1, 5):
            controller_slot.addItem(f"Controller {slot}", slot)
        controller_slot.setCurrentIndex(max(0, controller_slot.findData(self.settings.controller_slot)))
        controller_slot.currentIndexChanged.connect(lambda index: self.change("controller_slot", int(controller_slot.itemData(index))))
        self._discrete_registry.register('discrete.controller_slot.1', 'controller_slot', controller_slot)
        self.setting(activation, "Controller selection", "Choose the Xbox controller used for activation and manual target pinning. Automatic selects the first connected controller.", controller_slot)
        controller_binding = QComboBox()
        for title, value in (
            ("A", "a"), ("B", "b"), ("X", "x"), ("Y", "y"),
            ("Left shoulder", "left_shoulder"), ("Right shoulder", "right_shoulder"),
            ("Left trigger", "left_trigger"), ("Right trigger", "right_trigger"),
            ("Left stick press", "left_thumb"), ("Right stick press", "right_thumb"),
            ("View / Back", "back"), ("Menu / Start", "start"),
            ("D-pad up", "dpad_up"), ("D-pad down", "dpad_down"),
            ("D-pad left", "dpad_left"), ("D-pad right", "dpad_right"),
        ):
            controller_binding.addItem(title, value)
        controller_binding.setCurrentIndex(max(0, controller_binding.findData(self.settings.controller_binding)))
        controller_binding.currentIndexChanged.connect(lambda index: self.change("controller_binding", str(controller_binding.itemData(index))))
        self._discrete_registry.register('discrete.controller_binding.1', 'controller_binding', controller_binding)
        self.setting(activation, "Controller activation", "The selected Xbox button or trigger acts exactly like the configured hold or toggle activation key. Activation reads the chosen binding. An enabled controller-output route is configured separately.", controller_binding)
        thresholds = QWidget()
        threshold_layout = QVBoxLayout(thresholds)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        threshold_edits = []
        for title, field in (("Press threshold", "controller_trigger_press"), ("Release threshold", "controller_trigger_release")):
            row = QHBoxLayout()
            label = QLabel(title)
            control = QSpinBox()
            control.setObjectName(field)
            control.setRange(1, 255)
            control.setValue(getattr(self.settings, field))
            control.setSuffix(" / 255")
            control.setAccessibleName(title)
            label.setBuddy(control)
            row.addWidget(label)
            row.addWidget(control)
            threshold_layout.addLayout(row)
            threshold_edits.append(control)
        threshold_hint = QLabel('Applied on next Start.')
        threshold_hint.setWordWrap(True)
        threshold_layout.addWidget(threshold_hint)
        threshold_status = WrappedStatusLabel()
        threshold_status.setObjectName('controllerThresholdStatus')
        threshold_status.setProperty('_vaporlock_i18n_skip', True)
        threshold_status.setWordWrap(True)
        threshold_status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        threshold_layout.addWidget(threshold_status)
        apply_thresholds = QPushButton("Apply thresholds")
        apply_thresholds.setObjectName('applyControllerThresholds')
        def apply_pair():
            try:
                self.apply_controller_thresholds(*(control.value() for control in threshold_edits))
            except ValueError:
                source = "Release must be no higher than press. No changes applied."
                threshold_status.setProperty('thresholdMessage', source)
                threshold_status.setText(self.t(source))
                return
            source = "Thresholds updated."
            threshold_status.setProperty('thresholdMessage', source)
            threshold_status.setText(self.t(source))
        apply_thresholds.clicked.connect(apply_pair)
        threshold_layout.addWidget(apply_thresholds)
        thresholds.setProperty('guideField', 'controller_trigger_press')
        self.setting(activation, "Trigger thresholds", "Raw trigger readings: 0–255. Press at or above Press; release below Release. Thresholds use 1–255. Applies to activation triggers on next Start; manual target lock is unchanged.", thresholds)

        self.controller_status = QLabel("Controller status appears during an active capture session.")
        self.controller_status.setObjectName("detail")
        self.controller_status.setWordWrap(True)
        state_card.body.addWidget(self.controller_status)
        self.controller_binding_status = QLabel()
        self.controller_binding_status.setObjectName("detail")
        self.controller_binding_status.setWordWrap(True)
        state_card.body.addWidget(self.controller_binding_status)
        self.refresh_controller_binding_status()
        activation.addStretch(1)
        self.section(event_action, "Snap-radius event action")
        event_enabled = QCheckBox("Enabled")
        event_enabled.setChecked(self.settings.event_action_enabled)
        event_enabled.toggled.connect(lambda state: self.change("event_action_enabled", state))
        self._discrete_registry.register('discrete.event_action_enabled.1', 'event_action_enabled', event_enabled)
        self.setting(event_action, "Mouse event", "Send the selected mouse action only while the current, confidence-qualified target is inside the Target Policy snap radius. Physical use of the same button always takes priority.", self.feature_control(event_enabled))
        require_activation = QCheckBox("Require aim activation")
        require_activation.setChecked(self.settings.event_action_requires_activation)
        require_activation.toggled.connect(lambda state: self.change("event_action_requires_activation", state))
        self._discrete_registry.register('discrete.event_action_requires_activation.1', 'event_action_requires_activation', require_activation)
        self.setting(event_action, "Activation gate", "Enabled by default. The event action runs only while aim activation is authorized: held in Hold mode or armed in Toggle mode.", self.feature_control(require_activation))
        event_mode = QComboBox()
        event_mode.addItem("Click repeatedly while inside", "click")
        event_mode.addItem("Hold while inside", "hold")
        event_mode.setCurrentIndex(max(0, event_mode.findData(self.settings.event_action_mode)))
        event_mode.currentIndexChanged.connect(lambda index: self.change("event_action_mode", str(event_mode.itemData(index))))
        self._discrete_registry.register('discrete.event_action_mode.1', 'event_action_mode', event_mode)
        self.setting(event_action, "Action mode", "Click sends a full press and release on its cooldown. Hold presses once on entry and releases when safe after the target leaves snap radius, becomes invalid, or capture stops. Vapor Lock never sends a release while you physically hold that same button.", self.feature_control(event_mode))
        event_button = QComboBox()
        for title, value in (("Left mouse button", "left"), ("Right mouse button", "right"), ("Middle mouse button", "middle")):
            event_button.addItem(title, value)
        event_button.setCurrentIndex(max(0, event_button.findData(self.settings.event_action_button)))
        event_button.currentIndexChanged.connect(lambda index: self.change("event_action_button", str(event_button.itemData(index))))
        self._discrete_registry.register('discrete.event_action_button.1', 'event_action_button', event_button)
        self.setting(event_action, "Mouse button", "Button sent to the active camera-control application.", self.feature_control(event_button))
        cooldown = self.range("event_action_cooldown_ms", 50, 5000, 0, " ms")
        self.setting(event_action, "Click cooldown", "Applies only in click mode. It limits repeated clicks while the target remains inside snap radius; hold mode has no cooldown.", self.feature_control(cooldown))
        event_action.addStretch(1)
        self.section(transport_settings, "Output transport")
        transport = QComboBox()
        self.output_transport_combo = transport
        transport.addItem("Relative mouse movement", "mouse")
        transport.addItem("Virtual mouse - driver-level input", "virtual-mouse")
        transport.addItem("Xbox One-compatible controller bridge", "virtual-xbox")
        transport.addItem("MAKCU serial - legacy API", "makcu-v1")
        transport.addItem("MAKCU serial - binary API", "makcu-v2")
        transport.addItem("Vapor Lock HID bridge (Arduino / RP2350 / Teensy)", "serial-hid-bridge")
        transport.addItem("KMBox Net v3/v4 - direct movement", "kmbox-net")
        transport.addItem("KMBox A - official DLL", "kmbox-a")
        transport.setCurrentIndex(max(0, transport.findData(self.settings.output_transport)))
        transport.currentIndexChanged.connect(lambda index: self.change("output_transport", str(transport.itemData(index))))
        self.setting(transport_settings, "Movement transport", "Choose the local path used for relative movement output. Hardware routes send movement only; no remapping, masking, device-identity, or keyboard operations are used.", transport)
        self.output_transport_status = QLabel()
        self.output_transport_status.setObjectName("detail")
        self.output_transport_status.setWordWrap(True)
        transport_settings.addWidget(self.output_transport_status)
        virtual_mouse_opt_in = QCheckBox("Enable virtual mouse transport")
        self.virtual_mouse_opt_in_checkbox = virtual_mouse_opt_in
        virtual_mouse_opt_in.setChecked(self.settings.virtual_mouse_opt_in)
        virtual_mouse_opt_in.toggled.connect(lambda state: self.change("virtual_mouse_opt_in", state))
        self.setting(transport_settings, "Virtual mouse opt-in", "Disabled by default. This route uses an administrator-installed local input driver so compatible software receives driver-level relative movement instead of Windows synthetic input.", virtual_mouse_opt_in)
        self.virtual_mouse_install_button = QPushButton("Install virtual mouse component")
        self.virtual_mouse_install_button.clicked.connect(self.install_virtual_mouse_component)
        self.setting(transport_settings, "Virtual mouse component", "Installs the bundled local driver after a Windows administrator prompt. A Windows restart is required before the virtual mouse transport can start.", self.virtual_mouse_install_button)
        self.virtual_mouse_remove_button = QPushButton("Remove")
        self.virtual_mouse_remove_button.setAccessibleName(self.t("Remove virtual mouse component"))
        self.virtual_mouse_remove_button.setObjectName("dangerButton")
        self.virtual_mouse_remove_button.clicked.connect(self.remove_virtual_mouse_component)
        self.setting(transport_settings, "Virtual mouse component removal", "Removes the shared Interception mouse and keyboard driver. Other applications using it will be affected. Administrator approval and a Windows restart are required.", self.virtual_mouse_remove_button)
        self.virtual_mouse_removal_status = QLabel("")
        self.virtual_mouse_removal_status.setWordWrap(True)
        transport_settings.addWidget(self.virtual_mouse_removal_status)
        controller_opt_in = QCheckBox("Enable Xbox-compatible controller output")
        controller_opt_in.setChecked(self.settings.controller_output_opt_in)
        controller_opt_in.toggled.connect(lambda state: self.change("controller_output_opt_in", state))
        self.setting(transport_settings, "Controller output opt-in", "Disabled by default. Enabling it allows Vapor Lock to request administrator approval only when you explicitly install or start the local Xbox-compatible component.", controller_opt_in)
        controller_mode = QComboBox()
        controller_mode.addItem("Primary controller bridge", "slot0-proxy")
        controller_mode.addItem("Separate controller", "separate")
        controller_mode.setCurrentIndex(max(0, controller_mode.findData(self.settings.virtual_controller_mode)))
        controller_mode.currentIndexChanged.connect(lambda index: self.change("virtual_controller_mode", str(controller_mode.itemData(index))))
        self.setting(transport_settings, "Controller route", "Primary bridge mirrors the physical controller through the Xbox-compatible controller so software that reads Controller 1 receives both normal controller input and Vapor Lock movement. Separate creates an independent controller.", controller_mode)
        controller_stick = QComboBox()
        controller_stick.addItem("Right stick", "right")
        controller_stick.addItem("Left stick", "left")
        controller_stick.setCurrentIndex(max(0, controller_stick.findData(self.settings.virtual_controller_stick)))
        controller_stick.currentIndexChanged.connect(lambda index: self.change("virtual_controller_stick", str(controller_stick.itemData(index))))
        self._discrete_registry.register('discrete.virtual_controller_stick.1', 'virtual_controller_stick', controller_stick)
        self.setting(transport_settings, "Controller output stick", "Stick that receives relative movement while the selected target is active.", controller_stick)
        self.setting(transport_settings, "Controller output strength", "Scale the relative movement signal before it is written to the selected controller stick.", self.range("virtual_controller_strength", 0.0, 4.0, 2))
        self.setting(transport_settings, "Controller output dead-zone compensation", "Normalized stick radius, from 0 to 0.5. 0 is off. Boosts only authorized nonzero virtual-controller output; neutral stays neutral. Distinct from capture-pixel dead zone and trigger thresholds. Start low to avoid excessive movement.", batch3_spin(self, 'controller_output_deadzone'))
        self.controller_deadzone_status = QLabel()
        self.controller_deadzone_status.setObjectName('detail'); self.controller_deadzone_status.setWordWrap(True)
        self.controller_deadzone_status.setProperty('_vaporlock_i18n_skip', True)
        transport_settings.addWidget(self.controller_deadzone_status)
        manual_priority = QCheckBox("Physical stick takes priority")
        manual_priority.setChecked(self.settings.virtual_controller_manual_priority)
        manual_priority.toggled.connect(lambda state: self.change("virtual_controller_manual_priority", state))
        self._discrete_registry.register('discrete.virtual_controller_manual_priority.1', 'virtual_controller_manual_priority', manual_priority)
        self.setting(transport_settings, "Manual stick priority", "When you deliberately move the selected physical stick, its input wins until you release it.", manual_priority)
        self.controller_install_button = QPushButton("Install controller component")
        self.controller_install_button.clicked.connect(self.install_controller_bridge)
        self.setting(transport_settings, "Controller component", "Setup adds a local signing certificate to Windows machine-wide Root and TrustedPublisher stores. Review the confirmation before installing or repairing. Selecting this transport does not install anything.", self.controller_install_button)
        self.controller_uninstall_button = QPushButton("Remove controller component")
        self.controller_uninstall_button.setObjectName("dangerButton")
        self.controller_uninstall_button.clicked.connect(self.uninstall_controller_bridge)
        self.setting(transport_settings, "Controller component removal", "Removes only positively identified VaporLock-managed devices, packages and trust added by setup. Preexisting, foreign or uncertain components are preserved. Administrator approval or a restart may be required.", self.controller_uninstall_button)
        self.controller_test_button = QPushButton("Open Windows controller test")
        self.controller_test_button.clicked.connect(self.open_controller_test)
        self.setting(transport_settings, "Controller test", "Inspect the connected controller's buttons and sticks.", self.controller_test_button)
        self.refresh_controller_bridge_controls()
        self.section(hardware_settings, "Hardware movement adapters")
        serial_port = QLineEdit(self.settings.hardware_serial_port)
        serial_port.setPlaceholderText("COM3")
        serial_port.editingFinished.connect(lambda: self.change("hardware_serial_port", serial_port.text().strip()))
        self.setting(hardware_settings, "Serial port", "Required for MAKCU and Vapor Lock HID bridge output. Use the Windows COM port assigned to the connected device.", serial_port)
        self.setting(hardware_settings, "Serial baud rate", "Used for the selected serial device. MAKCU legacy firmware commonly uses 115200; use the device's configured rate when it differs.", self.range("hardware_serial_baud", 1200, 1000000, 0))
        kmbox_address = QLineEdit(self.settings.kmbox_net_address)
        kmbox_address.setProperty('guideField', 'kmbox_net_address')
        kmbox_address.setPlaceholderText("192.168.2.88")
        kmbox_address.editingFinished.connect(lambda: self.change("kmbox_net_address", kmbox_address.text().strip()))
        self.setting(hardware_settings, "KMBox Net address", "IPv4 address shown by the KMBox Net device.", kmbox_address)
        self.setting(hardware_settings, "KMBox Net port", "UDP port shown by the KMBox Net device.", self.range("kmbox_net_port", 1, 65535, 0))
        kmbox_code = QLineEdit(self.settings.kmbox_net_device_code)
        kmbox_code.setProperty('guideField', 'kmbox_net_device_code')
        kmbox_code.setEchoMode(QLineEdit.EchoMode.Password)
        kmbox_code.setPlaceholderText("Eight-character device code")
        kmbox_code.editingFinished.connect(lambda: self.change("kmbox_net_device_code", kmbox_code.text().strip()))
        self.setting(hardware_settings, "KMBox Net device code", "Local device code used only to establish the connection. It is not sent to Vapor Lock services or written into diagnostics.", kmbox_code)
        library_row = QWidget()
        library_layout = QHBoxLayout(library_row)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.setSpacing(8)
        self.kmbox_a_library = QLineEdit(self.settings.kmbox_a_library_path)
        self.kmbox_a_library.setPlaceholderText("Official KMBox A DLL")
        self.kmbox_a_library.editingFinished.connect(lambda: self.change("kmbox_a_library_path", self.kmbox_a_library.text().strip()))
        kmbox_a_browse = QPushButton("Browse")
        kmbox_a_browse.setProperty('missionIcon', 'folder')
        kmbox_a_browse.clicked.connect(self.choose_kmbox_a_library)
        library_layout.addWidget(self.kmbox_a_library, 1)
        library_layout.addWidget(kmbox_a_browse)
        self.setting(hardware_settings, "KMBox A library", "Official KMBox A DLL used for the documented KM_init and KM_move calls.", library_row)
        self.setting(hardware_settings, "KMBox A vendor ID", "Vendor ID used when initializing the selected KMBox A device.", self.range("kmbox_a_vendor_id", 0, 65535, 0))
        self.setting(hardware_settings, "KMBox A product ID", "Product ID used when initializing the selected KMBox A device.", self.range("kmbox_a_product_id", 0, 65535, 0))
        hardware_note = QLabel("The native engine validates the selected adapter before capture begins and stops the session on a device write error. Hardware adapters send relative movement only.")
        hardware_note.setObjectName("detail")
        hardware_note.setWordWrap(True)
        hardware_settings.addWidget(hardware_note)
        hardware_settings.addStretch(1)
        return page

    def _build_tuning_inspector(self, safeguards: QVBoxLayout) -> None:
        def setting(parent: QVBoxLayout, title: str, detail: str, control: QWidget) -> None:
            row = QFrame()
            row.setObjectName("policyQuickRow")
            row.setToolTip(detail)
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(4, 3, 4, 10)
            row_layout.setSpacing(5)
            label = QLabel(title)
            label.setObjectName("fieldTitle")
            label.setWordWrap(True)
            label.setToolTip(detail)
            heading_row = QHBoxLayout()
            heading_row.addWidget(label)
            add_guide_button(self, heading_row, title, control)
            heading_row.addStretch(1)
            row_layout.addLayout(heading_row)
            if not control.toolTip():
                control.setToolTip(detail)
            row_layout.addWidget(self._tuning_lock.wrap(control))
            parent.addWidget(row)

        self.section(safeguards, "Motion safeguards")
        setting(safeguards, "Assistance gain", "Fraction corrected at the 60 Hz reference rate. Actual elapsed time normalizes response at other rates.", self.range("gain", 0.0, 4.0, 2))
        for title, field in (("Horizontal target assistance", "assistance_x"), ("Vertical target assistance", "assistance_y")):
            setting(safeguards, title, "Multiplies assistance gain on this axis. 1 preserves the current response; 0 disables target assistance on this axis. Motion compensation and physical input are separate.", batch3_spin(self, field))
        axis_help = QLabel('Multiplies assistance gain on this axis. 1 preserves the current response; 0 disables target assistance on this axis. Motion compensation and physical input are separate.')
        axis_help.setObjectName('detail'); axis_help.setWordWrap(True); safeguards.addWidget(axis_help)
        setting(safeguards, "Maximum step", "Hard cap for one movement update.", self.range("max_step", 1, 500, 0, " units"))
        setting(safeguards, "Dead zone", "Capture-pixel tolerance around the aiming reference, independent of input-unit calibration.", self.range("deadzone", 0, 500, 0, " px"))
        setting(safeguards, "Output smoothing", "Retained fraction at a 60 Hz reference, normalized by elapsed time. Higher values add delay.", self.range("movement_smoothing", 0.0, 0.95, 2))
        setting(safeguards, "Maximum output rate", "Hard cap for relative movement events sent each second. Enter a larger exact value for expert tuning.", self.range("output_rate", 1, 120, 0, " Hz"))
        setting(safeguards, "Maximum speed", "Radial input-unit limit per second, independent of output report rate. Maximum step remains a separate per-report cap.", self.range("max_speed", 0, 6000, 1, " units/s"))
        setting(safeguards, "Observation expiry", "Stop target assistance when the original capture is older than this limit, including inference latency. Repeated stale results cannot renew it. Maximum 2000 ms.", self.range("output_max_observation_age_ms", 1, 500, 0, " ms"))
        self.section(safeguards, "Aiming reference and calibration")
        reference = QComboBox()
        reference.addItem("Screen centre", "capture-center")
        reference.addItem("Crosshair offset", "crosshair-offset")
        reference.setCurrentIndex(max(0, reference.findData(self.settings.aim_reference)))
        reference.currentIndexChanged.connect(lambda index: self.change("aim_reference", str(reference.itemData(index))))
        self._discrete_registry.register('discrete.aim_reference.1', 'aim_reference', reference)
        setting(safeguards, "Aiming reference", "Screen centre uses physical display coordinates. Virtual desktop uses its bounding centre. Manual offsets remain relative to capture centre; crosshair detection overrides them while enabled.", reference)
        from crosshair_ui import add_controls as add_crosshair_controls
        add_crosshair_controls(self,safeguards,setting)
        setting(safeguards, "Reference horizontal offset", "Positive moves right from capture center; negative moves left.", self.range("aim_reference_x", -1000, 1000, 2, " px"))
        setting(safeguards, "Reference vertical offset", "Positive moves down from capture center; negative moves up.", self.range("aim_reference_y", -1000, 1000, 2, " px"))
        setting(safeguards, "Horizontal input scale", "Relative input units per capture pixel. Windows acceleration, application sensitivity and zoom can change this relationship.", self.range("input_units_per_pixel_x", 0.001, 10.0, 3, " units/px"))
        setting(safeguards, "Vertical input scale", "Relative input units per capture pixel. Controller output uses normalized velocity instead of assuming a pixel-to-stick displacement.", self.range("input_units_per_pixel_y", 0.001, 10.0, 3, " units/px"))
        calibrate_scale = QPushButton("Calculate input scale")
        calibrate_scale.clicked.connect(self.calibrate_input_scale)
        setting(safeguards, "Measured calibration", "Calculate a per-axis scale from an input displacement and the resulting image displacement measured in your authorized environment. No movement is sent by this dialog.", calibrate_scale)
        curve = QComboBox()
        curve.addItem("Linear", "linear")
        curve.addItem("Quadratic precision", "quadratic")
        curve.setCurrentIndex(max(0, curve.findData(self.settings.response_curve)))
        curve.currentIndexChanged.connect(lambda index: self.change("response_curve", str(curve.itemData(index))))
        self._discrete_registry.register('discrete.response_curve.1', 'response_curve', curve)
        setting(safeguards, "Response curve", "Quadratic response reduces small corrections while retaining larger pan commands.", self.feature_control(curve))
        invert_x = QCheckBox("Invert horizontal output")
        invert_x.setChecked(self.settings.invert_x)
        invert_x.toggled.connect(lambda state: self.change("invert_x", state))
        self._discrete_registry.register('discrete.invert_x.1', 'invert_x', invert_x)
        setting(safeguards, "Horizontal direction", "Reverse horizontal input for a setup using the opposite axis convention.", invert_x)
        invert_y = QCheckBox("Invert vertical output")
        invert_y.setChecked(self.settings.invert_y)
        invert_y.toggled.connect(lambda state: self.change("invert_y", state))
        self._discrete_registry.register('discrete.invert_y.1', 'invert_y', invert_y)
        setting(safeguards, "Vertical direction", "Reverse vertical input for a setup using the opposite axis convention.", invert_y)
        for label in safeguards.parentWidget().findChildren(QLabel, "sectionLabel"):
            label.setWordWrap(True)
        safeguards.addStretch(1)

    def _build_compensation_controls(self, compensation_settings: QVBoxLayout) -> None:
        self.section(compensation_settings, "Motion compensation")
        compensation_enabled = QCheckBox("Enabled")
        compensation_enabled.setChecked(self.settings.motion_compensation_enabled)
        compensation_enabled.toggled.connect(lambda state: self.change("motion_compensation_enabled", state))
        self._discrete_registry.register('discrete.motion_compensation_enabled.1', 'motion_compensation_enabled', compensation_enabled)
        self.setting(compensation_settings, "Compensation", "Enable inverse drift correction from the selected saved profile or automatic estimate. Compensation has its own activation and does not require target movement output to be enabled.", self.feature_control(compensation_enabled))
        compensation_activation = QComboBox()
        compensation_activation.addItem("Hold key", "hold")
        compensation_activation.addItem("Click once", "click")
        compensation_activation.addItem("Constant", "constant")
        compensation_activation.addItem("Automatic", "automatic")
        compensation_activation.setCurrentIndex(max(0, compensation_activation.findData(self.settings.motion_compensation_activation_mode)))
        compensation_activation.currentIndexChanged.connect(lambda index: self.change("motion_compensation_activation_mode", str(compensation_activation.itemData(index))))
        self._discrete_registry.register('discrete.motion_compensation_activation_mode.1', 'motion_compensation_activation_mode', compensation_activation)
        self.setting(compensation_settings, "Activation", "Hold runs while the key is held. Click sends exactly one correction each time the key is pressed; it never toggles. Constant ignores the key. Automatic runs only while a valid target or automatic drift source is available.", self.feature_control(compensation_activation))
        compensation_key = QComboBox()
        for title, value in (("Left mouse button", "left"), ("Right mouse button", "right"), ("Middle mouse button", "middle"), ("Left Shift", "shift"), ("Left Control", "control"), ("Space", "space")):
            compensation_key.addItem(title, value)
        compensation_key.setCurrentIndex(max(0, compensation_key.findData(self.settings.motion_compensation_activation_key)))
        compensation_key.currentIndexChanged.connect(lambda index: self.change("motion_compensation_activation_key", str(compensation_key.itemData(index))))
        self._discrete_registry.register('discrete.motion_compensation_activation_key.1', 'motion_compensation_activation_key', compensation_key)
        self.setting(compensation_settings, "Activation key", "Used by Hold and Click once. The default is left mouse button. It has no effect in Constant or Automatic mode.", self.feature_control(compensation_key))
        compensation_mode = QComboBox()
        compensation_mode.addItem("Saved preset", "preset")
        compensation_mode.addItem("Automatic", "automatic")
        compensation_mode.addItem("Preset + automatic", "combined")
        compensation_mode.setCurrentIndex(max(0, compensation_mode.findData(self.settings.motion_compensation_mode)))
        compensation_mode.currentIndexChanged.connect(lambda index: self.change("motion_compensation_mode", str(compensation_mode.itemData(index))))
        self._discrete_registry.register('discrete.motion_compensation_mode.1', 'motion_compensation_mode', compensation_mode)
        self.setting(compensation_settings, "Correction source", "Saved preset applies a named measured drift pattern. Automatic estimates shared drift from mature detections. Combined uses both.", self.feature_control(compensation_mode))
        calibrate = QPushButton("Calibrate from live estimate")
        calibrate.clicked.connect(self.start_motion_compensation_calibration)
        self.setting(compensation_settings, "Compensation calibration wizard", "Collects several valid automatic drift estimates from the active native session, calculates a robust measured preset, and restores your current compensation mode afterward.", self.feature_control(calibrate))
        self._build_motion_compensation_library(compensation_settings)
        self.section(compensation_settings, "Automatic estimate")
        self.setting(compensation_settings, "Minimum agreeing tracks", "Automatic compensation waits for this many visible, mature tracks before estimating shared image drift.", self.feature_control(self.range("motion_compensation_auto_min_tracks", 2, 12, 0)))
        self.setting(compensation_settings, "Agreement tolerance", "Maximum velocity disagreement allowed between detections considered part of the same shared movement.", self.feature_control(self.range("motion_compensation_auto_consensus", 1.0, 120.0, 2, " px/s")))
        self.setting(compensation_settings, "Automatic smoothing", "Stabilizes automatic drift estimates between updates. Higher values respond more slowly.", self.feature_control(self.range("motion_compensation_auto_smoothing", 0.0, 0.95, 2)))
        self.setting(compensation_settings, "Maximum automatic rate", "Caps automatic correction before it reaches the selected movement transport.", self.feature_control(self.range("motion_compensation_auto_max_rate", 1.0, 480.0, 2, " px/s")))
        self.setting(compensation_settings, "Compensation strength", "Scales the selected preset, automatic estimate, or their combined correction. Type a precise or higher expert value when needed.", self.feature_control(self.range("motion_compensation_strength", 0.0, 2.0, 3, " x")))
        compensation_settings.addStretch(1)

    @tuning_guard
    def calibrate_input_scale(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(self.t("Input scale calibration"))
        layout = QVBoxLayout(dialog)
        fields = QFormLayout()
        controls: dict[str, QDoubleSpinBox] = {}
        for axis in ("Horizontal", "Vertical"):
            for kind, suffix in (("input", " units"), ("observed", " px")):
                control = QDoubleSpinBox()
                control.setRange(0.001, 100000.0)
                control.setDecimals(6)
                control.setValue(100.0)
                control.setSuffix(suffix)
                control.setToolTip(self.t("Use displacement magnitudes measured with the same sensitivity and zoom used during assistance."))
                controls[f"{axis}_{kind}"] = control
                fields.addRow(self.t(f"{axis} {kind} displacement"), control)
        layout.addLayout(fields)
        result = QLabel()
        layout.addWidget(result)
        def refresh() -> None:
            x = controls["Horizontal_input"].value() / controls["Horizontal_observed"].value()
            y = controls["Vertical_input"].value() / controls["Vertical_observed"].value()
            result.setText(f"X: {x:.6f} units/px    Y: {y:.6f} units/px")
            apply.setEnabled(0.001 <= x <= 1000 and 0.001 <= y <= 1000)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        apply = actions.button(QDialogButtonBox.StandardButton.Apply)
        apply.clicked.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        for control in controls.values():
            control.valueChanged.connect(refresh)
        layout.addWidget(actions)
        refresh()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.change("input_units_per_pixel_x", controls["Horizontal_input"].value() / controls["Horizontal_observed"].value())
            self.change("input_units_per_pixel_y", controls["Vertical_input"].value() / controls["Vertical_observed"].value())
            self.rebuild_pages()

    def start_motion_compensation_calibration(self) -> None:
        if not self._compensation_sampling_ready():
            QMessageBox.information(self, self.t("Compensation calibration"), self.t(
                "Start an Observe session before calibration. Sampling must not send movement or clicks."))
            return
        if self._calibration_active:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Compensation calibration")
        dialog.setModal(False)
        dialog.setMinimumWidth(470)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)
        title = QLabel("Create a measured compensation preset")
        title.setObjectName("fieldTitle")
        layout.addWidget(title)
        detail = QLabel("Vapor Lock will temporarily collect automatic shared-drift estimates without enabling output, then save the median observed drift to the selected text library. Keep several stable detections visible during sampling.")
        detail.setObjectName("detail")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        name = QLineEdit()
        name.setPlaceholderText("camera-stable")
        layout.addWidget(name)
        status = QLabel("Ready to sample for 6 seconds.")
        status.setObjectName("detail")
        status.setWordWrap(True)
        layout.addWidget(status)
        controls = QHBoxLayout()
        begin = QPushButton("Start 6-second sample")
        begin.setObjectName("primaryButton")
        cancel = QPushButton("Cancel")
        controls.addWidget(begin)
        controls.addWidget(cancel)
        layout.addLayout(controls)
        self._calibration_dialog = dialog
        self._calibration_status = status
        self._calibration_name = name
        begin.clicked.connect(self.begin_motion_compensation_calibration)
        cancel.clicked.connect(self.cancel_motion_compensation_calibration)
        dialog.finished.connect(lambda _result: self.cancel_motion_compensation_calibration())
        dialog.show()

    def begin_motion_compensation_calibration(self) -> None:
        if self._calibration_dialog is None or self._calibration_name is None:
            return
        identifier = self._calibration_name.text().strip()
        if not identifier:
            self._calibration_status.setText("Enter a short preset identifier before sampling.")
            return
        self.begin_named_motion_compensation_calibration(identifier)

    @tuning_guard
    def begin_named_motion_compensation_calibration(self, identifier: str) -> bool:
        """Start a calibration sample from the UI or authenticated local API."""
        identifier = identifier.strip()
        if not valid_motion_identifier(identifier):
            self.log("WARN", "Compensation calibration needs a short, single-line preset identifier.")
            return False
        if not self._compensation_sampling_ready():
            message = self.t("Start an Observe session before calibration. Sampling must not send movement or clicks.")
            if self._calibration_status is not None:
                self._calibration_status.setText(message)
            self.log("WARN", message)
            return False
        if self._calibration_active:
            return False
        if not self.engine.apply_live_setting("motion_compensation_mode", "automatic"):
            self.log("WARN", "The native session could not start compensation sampling.")
            return False
        self._calibration_active = True
        self._calibration_identifier = identifier
        self._calibration_samples = []
        self._calibration_last_inference = int((self._last_output_data or {}).get("inference_total", 0))
        self._calibration_deadline = time.monotonic() + 6.0
        self._calibration_original = {
            field: getattr(self.settings, field)
            for field in ("motion_compensation_mode",)
        }
        # Observe-only is enforced in native code. The sampling source is a
        # session override, never a profile edit or an undo-history entry.
        if self._calibration_status is not None:
            self._calibration_status.setText("Sampling stable shared drift… 0 valid estimates.")
        timer = QTimer(self)
        timer.setInterval(125)
        timer.timeout.connect(self.finish_motion_compensation_calibration_if_due)
        self._calibration_timer = timer
        timer.start()
        self.log("INFO", f"Compensation calibration started for preset: {identifier}")
        return True

    def _compensation_sampling_ready(self) -> bool:
        return (self.engine.session_running and self.engine.observe_only
                and (self._last_output_data or {}).get("observe_only") is True)

    def _collect_compensation_sample(self, data: dict) -> None:
        if (not self._calibration_active or data.get("observe_only") is not True
                or data.get("motion_compensation_mode") != "automatic"
                or data.get("motion_compensation_auto_valid") is not True):
            return
        try:
            count = int(data["inference_total"])
            horizontal = float(data["motion_compensation_auto_correction_horizontal"])
            vertical = float(data["motion_compensation_auto_correction_vertical"])
            if count > self._calibration_last_inference and math.isfinite(horizontal) and math.isfinite(vertical):
                self._calibration_last_inference = count
                self._calibration_samples.append((horizontal, vertical))
        except (KeyError, TypeError, ValueError, OverflowError):
            pass

    def finish_motion_compensation_calibration_if_due(self) -> None:
        if not self._calibration_active:
            return
        if not self._compensation_sampling_ready():
            self.cancel_motion_compensation_calibration("Capture stopped before calibration completed.")
            return
        remaining = max(0.0, self._calibration_deadline - time.monotonic())
        if self._calibration_status is not None:
            self._calibration_status.setText(f"Sampling stable shared drift… {len(self._calibration_samples)} valid estimates, {remaining:.1f} seconds remaining.")
        if remaining > 0:
            return
        samples = list(self._calibration_samples)
        identifier = self._calibration_identifier
        self._finish_motion_compensation_calibration()
        if len(samples) < 4:
            if self._calibration_status is not None:
                self._calibration_status.setText("Not enough agreeing live estimates were available. Keep more stable detections visible and try again.")
            return
        # Native telemetry is the inverse correction. Presets intentionally
        # store observed drift, so invert the robust estimates before saving.
        observed_vertical = -median(vertical for _horizontal, vertical in samples)
        observed_horizontal = -median(horizontal for horizontal, _vertical in samples)
        preset = MotionCompensationPreset(identifier, observed_vertical, observed_horizontal)
        try:
            current, errors = load_motion_compensation(self.motion_compensation_path())
            if errors:
                raise ValueError("Fix library errors before calibration: " + " ".join(errors[:2]))
            replacement = [item for item in current if item.identifier.casefold() != identifier.casefold()]
            replacement.append(preset)
            save_motion_compensation(self.motion_compensation_path(), replacement)
        except (OSError, ValueError) as error:
            if self._calibration_status is not None:
                self._calibration_status.setText(f"Could not save the measured preset: {error}")
            return
        self.change("motion_compensation_preset", identifier)
        self.change("motion_compensation_vertical", observed_vertical)
        self.change("motion_compensation_horizontal", observed_horizontal)
        self.reload_motion_compensation_library(selected_identifier=identifier)
        result = f"Saved {identifier}: vertical {observed_vertical:.3f} px/s, horizontal {observed_horizontal:.3f} px/s from {len(samples)} estimates."
        self.log("INFO", f"Compensation calibration complete. {result}")
        if self._calibration_status is not None:
            self._calibration_status.setText(result)

    def _finish_motion_compensation_calibration(self) -> None:
        if self._calibration_timer is not None:
            self._calibration_timer.stop()
            self._calibration_timer.deleteLater()
            self._calibration_timer = None
        self._calibration_active = False
        self._calibration_identifier = ""
        if self.engine.session_running and self.engine.observe_only:
            for field in self._calibration_original:
                self.engine.apply_live_setting(field, getattr(self.settings, field))
        self._calibration_original = {}

    def cancel_motion_compensation_calibration(self, message: str = "Calibration cancelled.") -> None:
        was_active = self._calibration_active
        self._finish_motion_compensation_calibration()
        if self._calibration_status is not None and was_active:
            self._calibration_status.setText(message)


    def motion_compensation_path(self) -> Path:
        value = self.settings.motion_compensation_library.strip() or "profiles/motion_compensation.txt"
        return resolve_product_path(value)

    def _build_motion_compensation_library(self, layout: QVBoxLayout) -> None:
        self.section(layout, "Saved compensation library")
        library_row = QWidget()
        library_layout = QHBoxLayout(library_row)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.setSpacing(8)
        self.motion_compensation_library_edit = QLineEdit(self.settings.motion_compensation_library)
        self.motion_compensation_library_edit.setPlaceholderText("profiles/motion_compensation.txt")
        self.motion_compensation_library_edit.editingFinished.connect(self.set_motion_compensation_library)
        browse = QPushButton("Browse")
        browse.clicked.connect(self.choose_motion_compensation_library)
        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(self.reload_motion_compensation_library)
        library_layout.addWidget(self.motion_compensation_library_edit, 1)
        library_layout.addWidget(browse)
        library_layout.addWidget(reload_button)
        self.setting(layout, "Library file", "A plain text library using: identifier | vertical pixels per second | horizontal pixels per second. Positive values describe observed drift; Vapor Lock applies the opposite correction.", self.feature_control(library_row))
        self.motion_compensation_preset_choice = QComboBox()
        self.motion_compensation_preset_choice.currentIndexChanged.connect(self.select_motion_compensation_preset)
        self.setting(layout, "Saved preset", "Choose a measured compensation record. The stored vertical and horizontal values remain editable below.", self.feature_control(self.motion_compensation_preset_choice))
        self.motion_compensation_name = QLineEdit()
        self.motion_compensation_name.setPlaceholderText("cam1")
        self.setting(layout, "Preset identifier", "A short name used in the compensation library and profiles.", self.feature_control(self.motion_compensation_name))
        self.motion_compensation_vertical_edit = QDoubleSpinBox()
        self.motion_compensation_vertical_edit.setDecimals(6)
        self.motion_compensation_vertical_edit.setRange(-1_000_000.0, 1_000_000.0)
        self.motion_compensation_vertical_edit.setSingleStep(0.001)
        self.motion_compensation_vertical_edit.setSuffix(" px/s")
        self.setting(layout, "Observed vertical drift", "Positive means image drift downward. Negative means upward. Vapor Lock sends the inverse correction.", self.feature_control(self.motion_compensation_vertical_edit))
        self.motion_compensation_horizontal_edit = QDoubleSpinBox()
        self.motion_compensation_horizontal_edit.setDecimals(6)
        self.motion_compensation_horizontal_edit.setRange(-1_000_000.0, 1_000_000.0)
        self.motion_compensation_horizontal_edit.setSingleStep(0.001)
        self.motion_compensation_horizontal_edit.setSuffix(" px/s")
        self.setting(layout, "Observed horizontal drift", "Positive means image drift right. Negative means left. Vapor Lock sends the inverse correction.", self.feature_control(self.motion_compensation_horizontal_edit))
        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        update = QPushButton("Add or update")
        update.clicked.connect(self.save_motion_compensation_preset)
        remove = QPushButton("Remove")
        remove.setObjectName("dangerButton")
        remove.clicked.connect(self.remove_motion_compensation_preset)
        action_layout.addWidget(update)
        action_layout.addWidget(remove)
        self.setting(layout, "Library editing", "Add, update, or remove a named preset. The library stays user-readable and is saved atomically.", self.feature_control(actions))
        self.motion_compensation_status = QLabel()
        self.motion_compensation_status.setObjectName("detail")
        self.motion_compensation_status.setWordWrap(True)
        layout.addWidget(self.motion_compensation_status)
        self.reload_motion_compensation_library(log_result=False)

    def set_motion_compensation_library(self) -> None:
        value = self.motion_compensation_library_edit.text().strip() or "profiles/motion_compensation.txt"
        self.change("motion_compensation_library", value)
        self.reload_motion_compensation_library()

    def choose_motion_compensation_library(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose motion compensation library", str(self.motion_compensation_path()), "Text files (*.txt);;All files (*.*)")
        if not path:
            return
        self.motion_compensation_library_edit.setText(profile_path(path))
        self.set_motion_compensation_library()

    def reload_motion_compensation_library(self, _checked: bool = False, *, log_result: bool = True, selected_identifier: str | None = None) -> None:
        self._api_catalog_cache = None
        path = self.motion_compensation_path()
        if not path.exists():
            try:
                save_motion_compensation(path, [])
            except (OSError, ValueError) as error:
                self._motion_compensation_presets = []
                if hasattr(self, "motion_compensation_status"):
                    self.motion_compensation_status.setText(f"Could not create library: {error}")
                return
        presets, errors = load_motion_compensation(path)
        self._motion_compensation_presets = presets
        if not hasattr(self, "motion_compensation_preset_choice"):
            return
        wanted = selected_identifier if selected_identifier is not None else self.settings.motion_compensation_preset
        choice = self.motion_compensation_preset_choice
        choice.blockSignals(True)
        choice.clear()
        choice.addItem("No saved preset", "")
        for preset in presets:
            choice.addItem(preset.identifier, preset.identifier)
        choice.setCurrentIndex(max(0, choice.findData(wanted)))
        choice.blockSignals(False)
        selected = find_motion_preset(presets, wanted)
        if selected:
            self.motion_compensation_name.setText(selected.identifier)
            self.motion_compensation_vertical_edit.setValue(selected.vertical)
            self.motion_compensation_horizontal_edit.setValue(selected.horizontal)
        else:
            self.motion_compensation_name.clear()
            self.motion_compensation_vertical_edit.setValue(0.0)
            self.motion_compensation_horizontal_edit.setValue(0.0)
        if errors:
            self.motion_compensation_status.setText("Library loaded with warnings: " + " ".join(errors[:2]))
        else:
            self.motion_compensation_status.setText(f"{len(presets)} saved preset{'s' if len(presets) != 1 else ''} loaded from {profile_path(str(path))}.")
        if log_result:
            self.log("INFO" if not errors else "WARN", self.motion_compensation_status.text())

    @tuning_guard
    def select_motion_compensation_preset(self, index: int) -> None:
        if not hasattr(self, "motion_compensation_preset_choice"):
            return
        identifier = str(self.motion_compensation_preset_choice.itemData(index) or "")
        preset = find_motion_preset(self._motion_compensation_presets, identifier)
        self.change("motion_compensation_preset", identifier)
        if preset is None:
            return
        self.motion_compensation_name.setText(preset.identifier)
        self.motion_compensation_vertical_edit.setValue(preset.vertical)
        self.motion_compensation_horizontal_edit.setValue(preset.horizontal)
        self.change("motion_compensation_vertical", preset.vertical)
        self.change("motion_compensation_horizontal", preset.horizontal)

    def save_motion_compensation_preset(self) -> None:
        identifier = self.motion_compensation_name.text().strip()
        preset = MotionCompensationPreset(identifier, self.motion_compensation_vertical_edit.value(), self.motion_compensation_horizontal_edit.value())
        try:
            current, errors = load_motion_compensation(self.motion_compensation_path())
            if errors:
                raise ValueError("Fix library errors before saving: " + " ".join(errors[:2]))
            replacement = [item for item in current if item.identifier.casefold() != identifier.casefold()]
            replacement.append(preset)
            save_motion_compensation(self.motion_compensation_path(), replacement)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Motion compensation", str(error))
            return
        self.change("motion_compensation_preset", identifier)
        self.change("motion_compensation_vertical", preset.vertical)
        self.change("motion_compensation_horizontal", preset.horizontal)
        self.reload_motion_compensation_library(selected_identifier=identifier)

    def remove_motion_compensation_preset(self) -> None:
        identifier = self.motion_compensation_name.text().strip() or self.settings.motion_compensation_preset
        if not identifier:
            return
        try:
            current, errors = load_motion_compensation(self.motion_compensation_path())
            if errors:
                raise ValueError("Fix library errors before removing a preset.")
            save_motion_compensation(self.motion_compensation_path(), [item for item in current if item.identifier.casefold() != identifier.casefold()])
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Motion compensation", str(error))
            return
        if self.settings.motion_compensation_preset.casefold() == identifier.casefold():
            self.change("motion_compensation_preset", "")
            self.change("motion_compensation_vertical", 0.0)
            self.change("motion_compensation_horizontal", 0.0)
        self.reload_motion_compensation_library()

    def visuals_page(self) -> QWidget:
        page, layout = self.new_page("Visuals", "Inspect the exact selected-monitor crop that the native detector is evaluating, including its active tracks, anchor, and projected motion.")
        self.section(layout, "Live annotated preview")
        self.preview_view = PreviewCanvas()
        layout.addWidget(self.preview_view)
        tabs = self.settings_tabs(layout, "Preview stream", "Annotations", "Desktop overlay", minimum_height=500)
        stream = tabs["Preview stream"]
        annotations = tabs["Annotations"]
        desktop = tabs["Desktop overlay"]
        self.section(stream, "Preview stream")
        enabled = QCheckBox("Enabled")
        enabled.setChecked(self.settings.preview_enabled)
        enabled.toggled.connect(lambda state: self.change("preview_enabled", state))
        self.setting(stream, "Live preview", "Emit compressed annotated frames from the native engine only while a session is running. Disabling this removes preview encoding work from the capture pipeline.", enabled)
        self.setting(stream, "Preview rate", "Maximum annotated frames sent to this control center each second.", self.range("preview_fps", 1, 30, 0, " fps"))
        self.setting(stream, "JPEG quality", "Compression quality for debug frames. Higher values retain finer detail and use more interprocess bandwidth.", self.range("preview_quality", 30, 100, 0))
        snapshot = QPushButton("Save annotated PNG")
        snapshot.clicked.connect(self.save_annotated_snapshot)
        self.setting(stream, "Preview snapshot", "Save the most recent native annotated debug frame as a PNG in this Windows account's Vapor Lock screenshots folder.", self.feature_control(snapshot))
        stream.addStretch(1)
        self.section(annotations, "Annotations")
        overlay = QCheckBox("Show annotations")
        overlay.setChecked(self.settings.overlay_enabled)
        overlay.toggled.connect(lambda state: self.change("overlay_enabled", state))
        self.setting(annotations, "Detection overlay", "Draw active tracking information over the debug preview. Source frames written to the training dataset are always unannotated.", overlay)
        boxes = QCheckBox("Show tracked boxes")
        boxes.setChecked(self.settings.overlay_boxes)
        boxes.toggled.connect(lambda state: self.change("overlay_boxes", state))
        self.setting(annotations, "Track boxes", "Draw an outline for each retained track and highlight the selected lock.", boxes)
        motion = QCheckBox("Show prediction vector")
        motion.setChecked(self.settings.overlay_motion)
        motion.toggled.connect(lambda state: self.change("overlay_motion", state))
        self.setting(annotations, "Motion prediction", "Draw the current selected anchor and its configured prediction lead in the debug preview.", motion)
        fov = QCheckBox("Show snap, near, and acquisition guides")
        fov.setChecked(self.settings.overlay_fov)
        fov.toggled.connect(lambda state: self.change("overlay_fov", state))
        self.setting(annotations, "Targeting FOV", "Draw the active circular or rectangular target zones in the annotated preview.", fov)
        masks = QCheckBox("Show instance masks")
        masks.setChecked(self.settings.overlay_segmentation_masks)
        masks.toggled.connect(lambda state: self.change("overlay_segmentation_masks", state))
        self.setting(annotations, "Segmentation masks", "Draw decoded instance masks when the selected ONNX model provides prototype-mask output. Standard detection models continue to use boxes.", masks)
        self.setting(annotations, "Mask limit", "Maximum retained masks decoded for one preview frame. Lower values reduce post-processing work on crowded scenes.", self.range("segmentation_mask_limit", 0, 32, 0))
        self.setting(annotations, "Mask threshold", "Minimum decoded mask probability used for the visible instance boundary.", self.range("segmentation_mask_threshold", 0.01, 0.99, 2))
        annotations.addStretch(1)
        self.section(desktop, "On-screen overlay")
        desktop_enabled = QCheckBox("Enabled")
        desktop_enabled.setChecked(self.settings.desktop_overlay_enabled)
        desktop_enabled.toggled.connect(lambda state: self.change("desktop_overlay_enabled", state))
        self.setting(desktop, "Click-through desktop overlay", "Draw live native track metadata directly over the captured region without opening Control Center. The overlay contains no captured pixels and is excluded from capture when Windows supports it.", self.feature_control(desktop_enabled))
        desktop_visible = QCheckBox("Visible")
        desktop_visible.setChecked(self.settings.desktop_overlay_visible)
        desktop_visible.toggled.connect(lambda state: self.change("desktop_overlay_visible", state))
        self.setting(desktop, "Desktop overlay visibility", "Show or hide the active overlay without stopping capture. A global shortcut can toggle this during a session.", self.feature_control(desktop_visible))
        desktop_layout = QComboBox()
        for title, value in (("Minimal selected target", "minimal"), ("Standard", "standard"), ("Diagnostics", "diagnostics")):
            desktop_layout.addItem(title, value)
        desktop_layout.setCurrentIndex(max(0, desktop_layout.findData(self.settings.desktop_overlay_layout)))
        desktop_layout.currentIndexChanged.connect(lambda index: self.change("desktop_overlay_layout", str(desktop_layout.itemData(index))))
        self.setting(desktop, "Overlay layout", "Minimal limits the screen to the selected subject. Standard shows all tracks. Diagnostics adds lock-recovery and track-count context.", self.feature_control(desktop_layout))
        self.setting(desktop, "Desktop overlay opacity", "Use the slider for a comfortable visible range, or type an exact opacity percentage.", self.feature_control(self.range("desktop_overlay_opacity", 25, 100, 0, " %")))
        self.setting(desktop, "Desktop overlay scale", "Scale label and annotation size. The slider covers the tested range; type a larger or smaller value for expert display tuning.", self.feature_control(self.range("desktop_overlay_scale", 0.75, 1.50, 2, " x")))
        desktop_palette = QComboBox()
        desktop_palette.addItem("Vapor", "vapor")
        desktop_palette.addItem("High contrast", "contrast")
        desktop_palette.setCurrentIndex(max(0, desktop_palette.findData(self.settings.desktop_overlay_palette)))
        desktop_palette.currentIndexChanged.connect(lambda index: self.change("desktop_overlay_palette", str(desktop_palette.itemData(index))))
        self.setting(desktop, "Overlay colors", "Choose the standard Vapor palette or a high-contrast palette for bright displays and projection surfaces.", self.feature_control(desktop_palette))
        self.setting(desktop, "Track color", "Color used for non-selected track boxes and labels. This custom value is stored with the current profile.", self.feature_control(self.color_editor("desktop_overlay_track_color")))
        self.setting(desktop, "Selected target color", "Color used for the active selected target and its prediction guide.", self.feature_control(self.color_editor("desktop_overlay_selected_color")))
        self.setting(desktop, "FOV and boundary color", "Color used for the outer targeting guide and capture-region boundary.", self.feature_control(self.color_editor("desktop_overlay_fov_color")))
        self.setting(desktop, "Status text color", "Color used for the compact desktop-overlay status strip.", self.feature_control(self.color_editor("desktop_overlay_text_color")))
        desktop_bounds = QCheckBox("Show active capture boundary")
        desktop_bounds.setChecked(self.settings.desktop_overlay_capture_bounds)
        desktop_bounds.toggled.connect(lambda state: self.change("desktop_overlay_capture_bounds", state))
        self.setting(desktop, "Capture boundary", "Draw a thin guide around the exact selected capture region. This is useful when checking centered offsets and monitor alignment.", self.feature_control(desktop_bounds))
        desktop_labels = QCheckBox("Show track labels and confidence")
        desktop_labels.setChecked(self.settings.desktop_overlay_labels)
        desktop_labels.toggled.connect(lambda state: self.change("desktop_overlay_labels", state))
        self.setting(desktop, "Desktop labels", "Show the track identity, model label, and confidence beside each live detection.", self.feature_control(desktop_labels))
        desktop_status = QCheckBox("Show provider, rate, and lock state")
        desktop_status.setChecked(self.settings.desktop_overlay_status)
        desktop_status.toggled.connect(lambda state: self.change("desktop_overlay_status", state))
        self.setting(desktop, "Desktop status", "Show a compact Vapor Lock status strip in the upper-left corner of the overlay.", self.feature_control(desktop_status))
        desktop_motion = QCheckBox("Show selected anchor and prediction vector")
        desktop_motion.setChecked(self.settings.desktop_overlay_motion)
        desktop_motion.toggled.connect(lambda state: self.change("desktop_overlay_motion", state))
        self.setting(desktop, "Desktop motion guide", "Optional selected-target anchor and prediction visualization. It is disabled by default to keep the live overlay calm and readable.", self.feature_control(desktop_motion))
        desktop.addStretch(1)
        return page

    def dataset_page(self) -> QWidget:
        page, layout = self.new_page("Training Data", "Collect source frames, create reviewable labels, build a YOLO dataset, and optionally train a local model without leaving Vapor Lock.")
        tabs = self.settings_tabs(layout, "Collection", "Annotation Suite", "Ultralytics", ".cloud", "Review", minimum_height=360,
                                  owner_attribute="training_data_tabs",fixed_titles=('Ultralytics','Collection','Annotation Suite','.cloud','Review'))
        from cloud_suite_ui import mount as mount_cloud
        mount_cloud(self,tabs['.cloud'])
        from mission_layout import TaskColumns,TaskCard
        collection_columns = TaskColumns(proportions=(62,38))
        for panel in (collection_columns.left,collection_columns.right):panel.setObjectName('missionTaskColumn')
        tabs['Collection'].addWidget(collection_columns,1)
        capture_card=TaskCard('Capture settings','display')
        archive_card=TaskCard('Dataset archive','folder')
        status_card=TaskCard('Dataset status','database')
        collection_columns.left_layout.addWidget(capture_card)
        collection_columns.right_layout.addWidget(archive_card)
        collection_columns.right_layout.addWidget(status_card)
        collection_columns.right_layout.addStretch(1)
        collection = capture_card.body
        archive = archive_card.body
        annotation = tabs["Annotation Suite"]
        from mission_layout import TaskCard
        annotation_columns=TaskColumns(proportions=(1,1))
        for panel in (annotation_columns.left,annotation_columns.right):panel.setObjectName('missionTaskColumn')
        annotation.addWidget(annotation_columns,1)
        annotation_settings=TaskCard('Annotation settings','tag')
        annotation_effects=TaskCard('Image effects','image')
        annotation_source=TaskCard('Data source','folder')
        annotation_preview=TaskCard('Annotation preview','image')
        annotation_actions=TaskCard('Actions & status','start')
        for card in (annotation_settings,annotation_effects,annotation_source):annotation_columns.left_layout.addWidget(card)
        for card in (annotation_preview,annotation_actions):annotation_columns.right_layout.addWidget(card)
        annotation_columns.right_layout.addStretch(1)
        preview_note=QLabel('No preview displayed. Inspect the existing effect preview before running annotation.')
        preview_note.setObjectName('detail');preview_note.setWordWrap(True)
        annotation_preview.body.addWidget(preview_note)
        inspect_preview=QPushButton('Inspect preview');inspect_preview.setProperty('missionIcon','search')
        from annotation_suite_ui import inspect_local
        inspect_preview.clicked.connect(lambda:inspect_local(self))
        annotation_preview.body.addWidget(self.feature_control(inspect_preview))
        annotation=annotation_settings.body
        review = tabs["Review"]
        self.dataset_review_panel = review.parentWidget()
        enabled = QCheckBox("Enabled")
        enabled.setChecked(self.settings.dataset_enabled)
        enabled.toggled.connect(lambda state: self.change("dataset_enabled", state))
        self._discrete_registry.register('discrete.dataset_enabled.1', 'dataset_enabled', enabled)
        self.setting(collection, "Source capture", "When enabled during a native session, Vapor Lock writes clean source images only. Annotation is a separate background action, so the active detector never becomes the training authority.", enabled)
        capture_gate = QWidget()
        capture_gate_row = QHBoxLayout(capture_gate)
        capture_gate_row.setContentsMargins(0, 0, 0, 0)
        qualification = QCheckBox()
        qualification.setChecked(self.settings.dataset_capture_confidence_enabled)
        qualification.setAccessibleName(self.t('Only capture frames with a qualifying detection'))
        qualification.toggled.connect(lambda state: self.change('dataset_capture_confidence_enabled', state))
        self._discrete_registry.register('discrete.dataset_capture_confidence_enabled.1', 'dataset_capture_confidence_enabled', qualification)
        self.dataset_capture_qualification_checkbox = qualification
        qualification_label = QLabel('Only capture frames with a qualifying detection')
        qualification_label.setWordWrap(True)
        qualification_label.setBuddy(qualification)
        capture_gate_row.addWidget(qualification)
        capture_gate_row.addWidget(qualification_label, 1)
        self.setting(collection, 'Detection condition', 'Save the full current image. Targeting and later annotation use their own settings.', capture_gate)
        confidence = QDoubleSpinBox()
        confidence.setRange(1.0, 100.0)
        confidence.setDecimals(4)
        confidence.setSingleStep(1.0)
        confidence.setSuffix(' %')
        # Set before connecting: a loaded fractional cutoff keeps its exact
        # native double; four percent decimals represent six fraction decimals.
        confidence.setValue(self.settings.dataset_min_confidence * 100.0)
        confidence.setEnabled(self.settings.dataset_capture_confidence_enabled)
        confidence.setAccessibleName(self.t('Minimum capture confidence'))
        confidence.valueChanged.connect(lambda percent: self.change('dataset_min_confidence', percent / 100.0))
        self.dataset_capture_confidence_editor = confidence
        self._numeric_registry.register_capture_percent(confidence)
        self.setting(collection, 'Minimum capture confidence', 'Save the full current image when the highest confidence in the live detection feed meets this threshold. Detections filtered out of the live feed cannot trigger capture. Annotation has its own settings.', confidence)
        capture_help = QLabel('Save the full current image. Targeting and later annotation use their own settings.')
        capture_help.setWordWrap(True)
        capture_help.setObjectName('detail')
        collection.addWidget(capture_help)
        destination = QWidget()
        row = QHBoxLayout(destination)
        row.setContentsMargins(0, 0, 0, 0)
        directory = QLineEdit(self.settings.dataset_directory)
        directory.setPlaceholderText(str(ROOT / "datasets"))
        directory.editingFinished.connect(lambda: self.change("dataset_directory", directory.text().strip()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self.browse_dataset(directory))
        browse.setProperty('missionIcon', 'folder')
        row.addWidget(directory, 1)
        row.addWidget(browse)
        self.setting(collection, "Dataset folder", "Contains an images folder for captures and a labels folder created by the background annotator. Leave blank to use the packaged datasets folder.", destination)
        self.setting(collection, "Capture interval", "Minimum time between saved samples. This keeps data collection intentional and avoids near-duplicate frames.", self.range("dataset_interval_ms", 100, 1000000, 0, " ms"))
        self.setting(collection, "JPEG quality", "Compression quality for source training images.", self.range("dataset_jpeg_quality", 30, 100, 0))
        from collection_limits import add_controls
        add_controls(self, collection)
        requires_activation = QCheckBox("Only while transport activation is active")
        requires_activation.setChecked(self.settings.dataset_capture_requires_activation)
        requires_activation.toggled.connect(lambda state: self.change("dataset_capture_requires_activation", state))
        self._discrete_registry.register('discrete.dataset_capture_requires_activation.1', 'dataset_capture_requires_activation', requires_activation)
        self.setting(collection, "Capture gate", "Use the same hold or lock input selected for movement. Disable only when intentionally collecting an untimed source set.", requires_activation)
        self.dataset_status = QLabel("No samples saved in this session.")
        self.dataset_status.setObjectName("detail")
        self.dataset_status.setWordWrap(True)
        status_card.body.addWidget(self.dataset_status)
        self.dataset_destination_status = QLabel()
        self.dataset_destination_status.setTextFormat(Qt.TextFormat.PlainText)
        self.dataset_destination_status.setWordWrap(True)
        self.dataset_destination_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.dataset_destination_status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        status_card.body.addWidget(self.dataset_destination_status)
        self.refresh_dataset_destination()
        backend = QComboBox()
        for label, value in (("Auto (GPU preferred)", "auto"),
                             ("DirectML", "directml"), ("CPU", "cpu"),
                             ("CUDA", "cuda"),
                             ("TensorRT RTX (unavailable)", "tensorrt-rtx")):
            backend.addItem(label, value)
            if value == 'tensorrt-rtx':
                backend.model().item(backend.count() - 1).setEnabled(False)
        backend.setCurrentIndex(max(0, backend.findData(self.settings.dataset_annotation_provider)))
        backend.currentIndexChanged.connect(lambda index: self.change('dataset_annotation_provider', backend.itemData(index)))
        self.dataset_annotation_backend = backend
        self.setting(annotation, 'Annotation backend',
            'Auto tries DirectML, then CPU. CUDA is an explicit choice and requires verified CUDA runtime setup. Explicit GPU choices stop on failure. TensorRT RTX annotation is unavailable.', backend)
        from batch6_ui import add_batch_controls
        add_batch_controls(self, annotation)
        self.dataset_backend_status = QLabel('Annotation backend has not started.')
        self.dataset_backend_status.setWordWrap(True)
        self.dataset_backend_status.setObjectName('detail')
        annotation_actions.body.addWidget(self.dataset_backend_status)
        self.annotation_model_library = QComboBox()
        self.populate_annotation_model_library()
        self.annotation_model_library.currentIndexChanged.connect(self.select_annotation_model)
        annotation_row = QWidget()
        annotation_layout = QHBoxLayout(annotation_row)
        annotation_layout.setContentsMargins(0, 0, 0, 0)
        annotation_layout.addWidget(self.annotation_model_library, 1)
        annotation_refresh = QPushButton("Refresh")
        annotation_refresh.setProperty('missionIcon','refresh')
        annotation_refresh.clicked.connect(self.refresh_model_library)
        annotation_layout.addWidget(annotation_refresh)
        self.setting(annotation, "Annotation model", "Choose a discovered local ONNX model for later label creation. Use the current detection model when no separate model is selected.", annotation_row)
        self.setting(annotation, "Agreement passes", "Compare original, mirrored, and exposure-adjusted views. Keep only same-class boxes that agree across every view. One pass uses the original image.", self.range("dataset_annotation_passes", 1, 10, 0))
        self.setting(annotation, "Annotation confidence", "Discard model outputs below this confidence while creating reviewable YOLO labels.", self.range("dataset_annotation_confidence", 0.01, 0.99, 2))
        self.setting(annotation, "Agreement overlap", "Minimum same-class overlap required for repeated annotation passes to keep an object.", self.range("dataset_annotation_iou", 0.05, 0.99, 2))
        self.setting(annotation, "NMS overlap threshold", "Suppress duplicate boxes in annotation results. Lower values remove more overlaps. Different classes remain separate.", self.range("dataset_annotation_nms_iou", 0.01, 0.99, 2))
        from batch7_ui import add_annotation_mode
        add_annotation_mode(self,annotation)
        delete_empty = self.dataset_delete_empty = QCheckBox('Delete unannotated images')
        delete_empty.setEnabled(not self.settings.dataset_annotation_additive)
        delete_empty.setChecked(self.settings.dataset_annotation_delete_unannotated)
        delete_empty.toggled.connect(lambda state: self.change('dataset_annotation_delete_unannotated', state))
        self._discrete_registry.register('discrete.dataset_annotation_delete_unannotated.1', 'dataset_annotation_delete_unannotated', delete_empty)
        self.setting(annotation_source.body, 'Delete unannotated images',
            'Removes images with no final labels after every annotation pass succeeds. Failed, cancelled, and previously labelled images are preserved.', delete_empty)
        from annotation_suite_ui import add_effects_controls, add_ultralytics_tab
        add_effects_controls(self, annotation_effects.body, 'dataset')
        from mission_layout import WrappedPath
        source_note=WrappedPath(self.settings.dataset_directory or str(ROOT/'datasets'))
        source_note.setProperty('_vaporlock_i18n_skip',True)
        self.annotation_source_path=source_note
        annotation_source.body.insertWidget(1,source_note)
        annotation=annotation_actions.body
        annotate = QPushButton("Annotate captured images")
        annotate.setObjectName('primaryButton')
        annotate.setProperty('missionIcon','start')
        annotate.clicked.connect(self.annotate_dataset)
        self.dataset_annotation_button = annotate
        self.setting(annotation, "Run annotation", "Create YOLO labels in the background. The control center and active capture session remain usable while it runs.", annotate)
        self.setting(archive, "Validation split", "Fraction of annotated images placed in the validation split when the archive is built.", self.range("dataset_export_val_split", 0.01, 0.50, 2))
        package = QPushButton("Build Ultralytics ZIP")
        package.setProperty('missionIcon', 'package')
        package.clicked.connect(self.package_dataset)
        self.dataset_package_button = package
        self.setting(archive, "Dataset archive", "Build a ready-to-upload ZIP in this dataset folder with paired images, labels, data.yaml, and classes.txt.", package)
        archive.addStretch(1)
        stop_task = QPushButton("Stop background task")
        stop_task.setProperty('missionIcon','stop')
        stop_task.setProperty('tuningLockExempt', True)
        stop_task.setObjectName("dangerButton")
        stop_task.clicked.connect(self.stop_dataset_worker)
        stop_task.setEnabled(False)
        self.dataset_worker_stop_button = stop_task
        self.setting(annotation, "Background task", "Stop annotation or archive creation without closing Vapor Lock. Completed, failed, and cancelled tasks always release the worker for another run.", stop_task)
        self.dataset_worker_status = QLabel("Background annotation is idle.")
        self.dataset_worker_status.setObjectName("detail")
        self.dataset_worker_status.setWordWrap(True)
        annotation.addWidget(self.dataset_worker_status)
        annotation.addStretch(1)
        add_ultralytics_tab(self, tabs['Ultralytics'])
        training = self.ultralytics_training_layout
        collection.addStretch(1)

        from mission_layout import TaskRegions
        review_outer=review
        review_regions=TaskRegions()
        queue_region,canvas_region,class_region=review_regions.regions
        review_outer.addWidget(review_regions,1)
        queue_card=TaskCard('Review queue','image');class_card=TaskCard('Class library / editor','tag')
        queue_region.addWidget(queue_card,1);class_region.addWidget(class_card);class_region.addStretch(1)
        review=queue_card.body
        self.dataset_review_summary = QLabel("Refresh to inspect captured images and generated labels before training.")
        self.dataset_review_summary.setObjectName("detail")
        self.dataset_review_summary.setWordWrap(True)
        review.addWidget(self.dataset_review_summary)
        review_controls = QWidget()
        from mission_layout import ActionFlow
        review_controls_row = ActionFlow(review_controls)
        review_controls_row.setContentsMargins(0, 0, 0, 0)
        refresh_review = QPushButton("Refresh queue")
        refresh_review.clicked.connect(self.refresh_dataset_review)
        self.dataset_review_accept = QPushButton("Mark accepted")
        self.dataset_review_accept.clicked.connect(lambda: self.set_dataset_review_status("accepted", advance=True))
        self.dataset_review_reject = QPushButton("Exclude from training")
        self.dataset_review_reject.setObjectName("dangerButton")
        self.dataset_review_reject.clicked.connect(lambda: self.set_dataset_review_status("rejected"))
        self.dataset_review_delete = QPushButton("Delete annotation")
        self.dataset_review_delete.clicked.connect(self.delete_selected_review_annotation)
        self.dataset_review_clear = QPushButton("Clear decision")
        self.dataset_review_clear.clicked.connect(lambda: self.set_dataset_review_status(""))
        self.dataset_review_zoom_out = QToolButton()
        self.dataset_review_zoom_out.setText("-")
        self.dataset_review_zoom_out.setToolTip("Zoom review image out")
        self.dataset_review_zoom_fit = QPushButton("Fit image")
        self.dataset_review_zoom_fit.clicked.connect(lambda: self.dataset_review_preview.reset_zoom())
        self.dataset_review_zoom_in = QToolButton()
        self.dataset_review_zoom_in.setText("+")
        self.dataset_review_zoom_in.setToolTip("Zoom review image in")
        self.dataset_review_zoom_label = QLabel("100%")
        self.dataset_review_zoom_label.setObjectName("detail")
        self.dataset_review_zoom_out.clicked.connect(lambda: self.dataset_review_preview.set_zoom_percent(self.dataset_review_preview.zoom_percent() - 20))
        self.dataset_review_zoom_in.clicked.connect(lambda: self.dataset_review_preview.set_zoom_percent(self.dataset_review_preview.zoom_percent() + 20))
        refresh_review.setProperty('missionIcon','refresh')
        queue_card.body.addWidget(refresh_review)
        review_controls_row.addWidget(self.dataset_review_accept)
        review_controls_row.addWidget(self.dataset_review_reject)
        review_controls_row.addWidget(self.dataset_review_delete)
        review_controls_row.addWidget(self.dataset_review_clear)
        review_zoom = QWidget()
        review_zoom_row = QHBoxLayout(review_zoom)
        review_zoom_row.setContentsMargins(0,0,0,0)
        for widget in (QLabel("Image zoom"), self.dataset_review_zoom_out, self.dataset_review_zoom_label, self.dataset_review_zoom_in, self.dataset_review_zoom_fit):
            review_zoom_row.addWidget(widget)
        canvas_region.addWidget(review_zoom)
        review_outer.addWidget(self._tuning_lock.wrap(review_controls))
        for button,symbol in ((self.dataset_review_accept,'accept'),(self.dataset_review_reject,'exclude'),(self.dataset_review_delete,'trash'),(self.dataset_review_clear,'restore'),(self.dataset_review_zoom_fit,'expand')):
            button.setProperty('missionIcon',symbol)
        review_options = QWidget()
        review_options_row = QVBoxLayout(review_options)
        review_options_row.setContentsMargins(0, 0, 0, 0)
        self.dataset_review_class = QComboBox()
        self.dataset_review_class.currentIndexChanged.connect(self.review_class_changed)
        review_options_row.addWidget(QLabel("New annotation class"))
        review_options_row.addWidget(self.dataset_review_class, 1)
        self.dataset_review_approval_key = QComboBox()
        self.dataset_review_approval_key.addItem("Enter", "enter")
        self.dataset_review_approval_key.addItem("Space", "space")
        self.dataset_review_approval_key.setCurrentIndex(max(0, self.dataset_review_approval_key.findData(self.settings.dataset_review_approval_key)))
        self.dataset_review_approval_key.currentIndexChanged.connect(lambda index: self.change("dataset_review_approval_key", str(self.dataset_review_approval_key.itemData(index))))
        self._discrete_registry.register('discrete.dataset_review_approval_key.1', 'dataset_review_approval_key', self.dataset_review_approval_key)
        review_options_row.addWidget(QLabel("Approve + next"))
        review_options_row.addWidget(self.dataset_review_approval_key)
        review_options.setToolTip('Choose the class for new boxes. The selected approval key accepts and advances.')
        class_card.body.addWidget(self._tuning_lock.wrap(review_options))
        class_library = QWidget()
        class_library_row = QVBoxLayout(class_library)
        class_library_row.setContentsMargins(0, 0, 0, 0)
        self.dataset_review_new_class = QLineEdit()
        self.dataset_review_new_class.setPlaceholderText("New class name")
        add_class = QPushButton("Add class")
        add_class.clicked.connect(self.add_dataset_review_class)
        remove_class = QPushButton("Remove selected")
        remove_class.setObjectName("dangerButton")
        remove_class.clicked.connect(self.remove_dataset_review_class)
        self.dataset_review_slot_choice = QComboBox()
        for slot in range(1, 10):
            self.dataset_review_slot_choice.addItem(f"Numpad {slot}", slot)
        assign_slot = QPushButton("Assign selected")
        assign_slot.clicked.connect(self.assign_dataset_review_slot)
        class_library_row.addWidget(self.dataset_review_new_class, 1)
        class_library_row.addWidget(add_class)
        class_library_row.addWidget(remove_class)
        class_library_row.addSpacing(10)
        class_library_row.addWidget(self.dataset_review_slot_choice)
        class_library_row.addWidget(assign_slot)
        class_library.setToolTip('Numpad 1 through 9 selects the class mapped to its slot.')
        class_card.body.addWidget(self._tuning_lock.wrap(class_library))
        for button,symbol in ((add_class,'tag'),(remove_class,'trash'),(assign_slot,'keyboard')):button.setProperty('missionIcon',symbol)
        self.dataset_review_slot_summary = QLabel()
        self.dataset_review_slot_summary.setObjectName("detail")
        self.dataset_review_slot_summary.setWordWrap(True)
        class_card.body.addWidget(self.dataset_review_slot_summary)
        self.dataset_review_list = QListWidget()
        self.dataset_review_list.setMinimumWidth(180)
        self.dataset_review_list.setMinimumHeight(180)
        self.dataset_review_list.setItemDelegate(DatasetReviewQueueDelegate(self.dataset_review_list))
        self.dataset_review_list.currentRowChanged.connect(self.show_dataset_review_item)
        from polygon_editor import create_canvas, add_tools
        self.dataset_review_preview = create_canvas(DatasetReviewCanvas,self)
        add_tools(self,canvas_region,self.dataset_review_preview)
        self.dataset_review_preview.setObjectName("datasetReviewCanvas")
        self.dataset_review_preview.annotations_changed.connect(self.save_dataset_review_annotations)
        self.dataset_review_preview.selection_changed.connect(self.review_annotation_selection_changed)
        self.dataset_review_preview.zoom_changed.connect(lambda percent: self.dataset_review_zoom_label.setText(f"{percent}%"))
        queue_card.body.addWidget(self.dataset_review_list,1)
        canvas_region.insertWidget(1,self.dataset_review_preview,1)
        self.dataset_review_records: list[Path] = []
        self.dataset_review_decisions: dict[str, str] = {}
        self.populate_dataset_review_classes()
        self.install_dataset_review_shortcuts()

        training_outer=training
        training_columns=TaskColumns(proportions=(1,1))
        for panel in (training_columns.left,training_columns.right):panel.setObjectName('missionTaskColumn')
        training_outer.addWidget(training_columns,3)
        from mission_layout import InventoryLayout
        runtime_card=TaskCard('Training runtime','chip',layout_type=InventoryLayout)
        run_card=TaskCard('Run setup','model')
        optimization_card=TaskCard('Optimization','gear')
        output_card=TaskCard('Output','upload')
        run_actions=TaskCard('Run training','start',layout_type=InventoryLayout)
        run_actions.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        history_card=TaskCard('History & curves','chart')
        from mission_layout import option_scroll
        training_options=QWidget();training_options_layout=QVBoxLayout(training_options)
        training_options_layout.setContentsMargins(0,0,0,0)
        training_options_layout.addWidget(run_card)
        training_options_layout.addStretch(1)
        training_columns.left_layout.addWidget(option_scroll(training_options,'Training run setup'),1)
        advanced_options=QWidget();advanced_layout=QVBoxLayout(advanced_options)
        advanced_layout.setContentsMargins(0,0,0,0)
        for card in (optimization_card,output_card):advanced_layout.addWidget(card)
        advanced_layout.addStretch(1)
        training_columns.right_layout.addWidget(option_scroll(advanced_options,'Training optimization and output'),1)
        training_columns.left_layout.insertWidget(0,runtime_card)
        training_results=TaskColumns(proportions=(1,1))
        for panel in (training_results.left,training_results.right):panel.setObjectName('missionTaskColumn')
        training_results.left_layout.addWidget(run_actions)
        training_results.right_layout.addWidget(option_scroll(history_card,'Training history and curves'),1)
        training_outer.addWidget(training_results,2)
        training=runtime_card.body
        self.training_runtime_status = QLabel("Training runtime has not been checked.")
        self.training_runtime_status.setObjectName("detail")
        from mission_layout import ActionRow
        runtime_controls = ActionRow()
        runtime_row = ActionFlow(runtime_controls)
        runtime_row.setContentsMargins(0, 0, 0, 0)
        install_runtime = QPushButton("Install training runtime")
        install_runtime.setProperty('missionIcon','download')
        install_runtime.clicked.connect(self.install_training_runtime)
        self.training_install_button = install_runtime
        check_runtime = QPushButton("Check runtime")
        check_runtime.setProperty('missionIcon','refresh')
        check_runtime.clicked.connect(self.check_training_runtime)
        runtime_row.addWidget(install_runtime)
        runtime_row.addWidget(check_runtime)
        runtime_controls.setToolTip('Downloads the optional local Ultralytics runtime into this Vapor Lock copy only when requested. Stop ends an install before it becomes active.')
        training.addWidget(self._tuning_lock.wrap(runtime_controls))
        training.addWidget(self.training_runtime_status)
        training=run_card.body
        self.training_model_library = QComboBox()
        self.populate_training_model_library()
        self.training_model_library.currentIndexChanged.connect(self.select_training_model)
        training_model_row = QWidget()
        training_model_layout = QHBoxLayout(training_model_row)
        training_model_layout.setContentsMargins(0, 0, 0, 0)
        training_model_layout.addWidget(self.training_model_library, 1)
        training_models_refresh = QPushButton("Refresh")
        training_models_refresh.setProperty('missionIcon','refresh')
        training_models_refresh.clicked.connect(self.refresh_training_libraries)
        training_model_layout.addWidget(training_models_refresh)
        self.setting(training, "Base model", "Trainable .pt checkpoints and .yaml model definitions from the local models library. ONNX files are deployment exports and cannot be used as a training base.", training_model_row)
        self.training_dataset_library = QComboBox()
        self.populate_training_dataset_library()
        self.training_dataset_library.currentIndexChanged.connect(self.select_training_dataset)
        training_dataset_row = QWidget()
        training_dataset_layout = QHBoxLayout(training_dataset_row)
        training_dataset_layout.setContentsMargins(0, 0, 0, 0)
        training_dataset_layout.addWidget(self.training_dataset_library, 1)
        training_dataset_refresh = QPushButton("Refresh")
        training_dataset_refresh.setProperty('missionIcon','refresh')
        training_dataset_refresh.clicked.connect(self.refresh_training_libraries)
        training_dataset_layout.addWidget(training_dataset_refresh)
        self.setting(training, "Prepared dataset", "Choose a dataset build created in the Collection tab. Each choice points to a local data.yaml file.", training_dataset_row)
        self.setting(training, "Run name", "Name used for local checkpoints and optional Ultralytics Platform synchronization.", self.text_setting("training_name"))
        self.setting(output_card.body, "Platform project", "Optional account project path, such as username/project. Leave blank for fully local training.", self.text_setting("training_platform_project"))
        self.training_api_key = QLineEdit()
        self.training_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.training_api_key.setPlaceholderText("Optional Ultralytics API key for this run only")
        self.setting(output_card.body, "Platform API key", "Optional and never saved. When supplied with a Platform project, Ultralytics receives the run through its own authenticated training integration.", self.training_api_key)
        preflight = QPushButton("Run training preflight")
        preflight.setProperty('missionIcon','pulse')
        preflight.clicked.connect(self.run_training_preflight)
        self.training_preflight_button = preflight
        self.training_preflight = QLabel("Run a preflight before training to validate labels, image pairing, space, and available NVIDIA memory.")
        self.training_preflight.setObjectName("detail")
        self.training_preflight.setWordWrap(True)
        preflight_row = QWidget()
        preflight_layout = QHBoxLayout(preflight_row)
        preflight_layout.setContentsMargins(0, 0, 0, 0)
        preflight_layout.addWidget(self.training_preflight, 1)
        preflight_layout.addWidget(preflight)
        run_actions.body.addWidget(self.training_preflight)
        training_action_row=ActionRow();training_action_flow=ActionFlow(training_action_row)
        training_action_flow.addWidget(self._tuning_lock.wrap(preflight))
        preflight_row.deleteLater()
        run_actions.body.addWidget(training_action_row)
        self.setting(training, "Epochs", "Maximum full passes over the training set.", self.range("training_epochs", 1, 100000, 0))
        self.setting(training, "Image size", "Square training resolution. Larger values can improve detail at a substantial memory and time cost.", self.range("training_imgsz", 32, 4096, 0))
        self.setting(training, "Batch", "Use auto to let Ultralytics choose a memory-safe batch size, or enter a positive integer.", self.text_setting("training_batch"))
        device = QComboBox()
        for label, value in (("Automatic", "auto"), ("GPU 0", "0"), ("Automatic idle GPU", "-1"), ("CPU", "cpu")):
            device.addItem(label, value)
        device.setCurrentIndex(max(0, device.findData(self.settings.training_device)))
        device.currentIndexChanged.connect(lambda index: self.change("training_device", str(device.itemData(index))))
        self._discrete_registry.register('discrete.training_device.1', 'training_device', device)
        self.setting(training, "Training device", "Choose the local compute device used by Ultralytics. GPU training requires a compatible PyTorch runtime and driver.", device)
        training=optimization_card.body
        self.setting(training, "Data workers", "Number of background image-loading workers used during training.", self.range("training_workers", 0, 64, 0))
        self.setting(training, "Early-stop patience", "Stop after this many non-improving epochs. Use zero to disable early stopping.", self.range("training_patience", 0, 100000, 0))
        cache = QComboBox()
        for label, value in (("Disk cache", "disk"), ("Memory cache", "ram"), ("No cache", "false")):
            cache.addItem(label, value)
        cache.setCurrentIndex(max(0, cache.findData(self.settings.training_cache)))
        cache.currentIndexChanged.connect(lambda index: self.change("training_cache", str(cache.itemData(index))))
        self._discrete_registry.register('discrete.training_cache.1', 'training_cache', cache)
        self.setting(training, "Dataset cache", "Choose disk cache, memory cache, or no cache. Memory cache can substantially reduce training time but uses available RAM.", cache)
        optimizer = QComboBox()
        for label, value in (("Automatic", "auto"), ("SGD", "SGD"), ("Adam", "Adam"), ("AdamW", "AdamW"), ("RMSProp", "RMSProp")):
            optimizer.addItem(label, value)
        optimizer.setCurrentIndex(max(0, optimizer.findData(self.settings.training_optimizer)))
        optimizer.currentIndexChanged.connect(lambda index: self.change("training_optimizer", str(optimizer.itemData(index))))
        self._discrete_registry.register('discrete.training_optimizer.1', 'training_optimizer', optimizer)
        self.setting(training, "Optimizer", "Weight-update algorithm used by the training run.", optimizer)
        self.setting(training, "Initial learning rate", "Starting learning rate. This has a large effect on convergence and stability.", self.range("training_lr0", 0.000001, 10.0, 6))
        self.setting(training, "Final learning-rate factor", "End-of-schedule multiplier applied to the initial learning rate.", self.range("training_lrf", 0.000001, 1.0, 6))
        self.setting(training, "Weight decay", "Regularization strength used to reduce overfitting.", self.range("training_weight_decay", 0.0, 1.0, 6))
        self.setting(training, "Warmup epochs", "Gradually ramps learning rate at the beginning of training.", self.range("training_warmup_epochs", 0.0, 1000.0, 2))
        self.setting(training, "Close mosaic", "Disable mosaic augmentation this many epochs before the end of training.", self.range("training_close_mosaic", 0, 100000, 0))
        self.setting(training, "Dataset fraction", "Use a fraction of the prepared dataset for a faster experimental run.", self.range("training_fraction", 0.01, 1.0, 2))
        for title, field_name, detail in (("Mixed precision", "training_amp", "Use automatic mixed precision when supported by the selected device."), ("Pretrained weights", "training_pretrained", "Retain pretrained weights when the selected base checkpoint supports them."), ("Cosine schedule", "training_cos_lr", "Use cosine learning-rate decay."), ("Deterministic run", "training_deterministic", "Favor repeatability over some performance optimizations."), ("Export ONNX", "training_export_onnx", "Export the best checkpoint to ONNX and add it to the model library after training."), ("Dynamic ONNX", "training_dynamic_onnx", "Allow variable input dimensions in the exported ONNX model.")):
            control = QCheckBox("Enabled")
            control.setChecked(bool(getattr(self.settings, field_name)))
            control.toggled.connect(lambda state, name=field_name: self.change(name, state))
            self._discrete_registry.register({'training_amp': 'discrete.training_amp.1', 'training_pretrained': 'discrete.training_pretrained.1', 'training_cos_lr': 'discrete.training_cos_lr.1', 'training_deterministic': 'discrete.training_deterministic.1', 'training_export_onnx': 'discrete.training_export_onnx.1', 'training_dynamic_onnx': 'discrete.training_dynamic_onnx.1'}[field_name], field_name, control)
            self.setting(output_card.body if field_name in ('training_amp','training_export_onnx','training_dynamic_onnx') else training, title, detail, control)
        self.setting(training, "Random seed", "Seed used by the training run. Zero is a valid fixed seed.", self.range("training_seed", 0, 2147483647, 0))
        advanced = QPushButton("Advanced training settings")
        advanced.setProperty('missionIcon','gear')
        advanced.setObjectName('advanced_training_settings')
        advanced.clicked.connect(self.edit_advanced_training)
        self.setting(training, "Advanced training settings", "Configure supported augmentation and loss options with explicit overrides. Changes apply to the next training run.", advanced)
        self.training_start_button = QPushButton("Start local training")
        training=run_actions.body
        self.training_start_button.setProperty('missionIcon','start')
        self.training_start_button.setObjectName("primaryButton")
        self.training_start_button.clicked.connect(self.start_local_training)
        self.training_start_button.setToolTip('Runs locally in the background and leaves the control center responsive. The result checkpoint and optional ONNX export are added to the model library.')
        training_action_flow.addWidget(self._tuning_lock.wrap(self.training_start_button))
        self.training_stop_button = QPushButton("Stop training")
        self.training_stop_button.setProperty('missionIcon','stop')
        self.training_stop_button.setProperty('tuningLockExempt', True)
        self.training_stop_button.setObjectName("dangerButton")
        self.training_stop_button.setEnabled(False)
        self.training_stop_button.clicked.connect(self.stop_training_worker)
        self.training_stop_button.setToolTip('Stop the active local training process without closing Vapor Lock.')
        training_action_flow.addWidget(self.training_stop_button)
        self.training_status = QLabel("Local training is idle.")
        self.training_status.setObjectName("detail")
        self.training_status.setWordWrap(True)
        training.addWidget(self.training_status)
        training=history_card.body
        self.training_history = QLabel("No completed local training runs yet.")
        self.training_history.setObjectName("detail")
        self.training_history.setWordWrap(True)
        training.addWidget(self.training_history)
        self.refresh_training_history()
        from training_curves_ui import TrainingCurves
        self.training_curves = TrainingCurves(self, ROOT / 'training' / 'runs')
        training.addWidget(self.training_curves)
        training.addStretch(1)
        from mission_layout import bound_inventory
        bound_inventory(collection_columns.left_layout,'Capture options')
        bound_inventory(annotation_columns.left_layout,'Local annotation settings and effects')
        return page

    @tuning_guard
    def edit_advanced_training(self):
        from advanced_training import open_editor
        open_editor(self)

    def profiles_page(self) -> QWidget:
        page, layout = self.new_page("Profiles", "Save and restore complete native-engine configurations as readable JSON files.")
        from profile_cards_ui import add_profile_cards
        from mission_layout import TaskColumns,TaskCard
        columns = TaskColumns(proportions=(44,56))
        for panel in (columns.left,columns.right):panel.setObjectName('missionTaskColumn')
        layout.addWidget(columns)
        library_card=TaskCard('Profile library','profiles')
        active_card=TaskCard('Active profile','profiles')
        columns.left_layout.addWidget(library_card)
        columns.right_layout.addWidget(active_card)
        add_profile_cards(self, library_card.body)
        card_note=QLabel('Editing a card does not select that profile.')
        card_note.setWordWrap(True);card_note.setObjectName('detail');library_card.body.addWidget(card_note)
        columns.left_layout.addStretch(1)
        columns.right_layout.addStretch(1)
        layout = active_card.body
        name = QLineEdit(self.settings.profile_name)
        name.editingFinished.connect(lambda: self.change("profile_name", name.text().strip() or "Untitled"))
        self.setting(layout, "Profile name", "Name stored inside the profile file.", name)
        save = QPushButton("Save active profile")
        save.setObjectName('saveActiveProfile')
        save.setProperty('missionPrimary',True)
        save.setProperty('missionIcon', 'save')
        save.clicked.connect(self.save_profile)
        self.setting(layout, "Save active profile", "Save the active configuration. Editing a card does not select that profile.", save)
        open_button = QPushButton("Open profile…")
        open_button.setProperty('missionIcon', 'folder')
        open_button.clicked.connect(self.open_profile)
        self.setting(layout, "Open another profile", "Replace current settings with a local JSON profile.", open_button)
        save_as = QPushButton("Save profile as…")
        save_as.setProperty('missionIcon', 'save')
        save_as.clicked.connect(self.save_profile_as)
        self.setting(layout, "Save a copy", "Write the complete configuration to a new JSON file.", save_as)
        compare = QPushButton("Compare profiles…")
        compare.setProperty('missionIcon','search')
        compare.clicked.connect(self.compare_profiles)
        self.setting(layout, "Compare tuning", "Compare the current workspace or any two saved profiles field by field.", compare)
        layout.addSpacing(16)
        reset = QPushButton("Reset to defaults")
        reset.setProperty('missionIcon', 'restore')
        reset.setObjectName("dangerButton")
        reset.clicked.connect(self.reset_settings)
        self.setting(layout, "Reset configuration", "Return every editable setting to the bundled defaults.", reset)
        layout.addStretch(1)
        return page

    def shortcuts_page(self) -> QWidget:
        page, layout = self.new_page("Shortcuts", "Configure the shared calibration shortcut and Windows-wide controls stored with the current profile.")
        from mission_layout import TaskCard
        page_layout=layout
        setup=TaskCard('Setup & calibration','gear');global_controls=TaskCard('Global controls','keyboard');profiles=TaskCard('Profile hotkeys','profiles')
        for card in (setup,global_controls,profiles):page_layout.addWidget(card)
        layout=setup.body
        self.setting(
            layout,
            "Open setup and calibration",
            "Included with VaporLock. This opens the inert setup wizard and never starts capture by itself.",
            self.shortcut_editor("hotkey_calibration"),
        )
        layout=global_controls.body
        self.setting(layout, "Start or stop capture", "Starts a ready capture session or stops the currently running native engine.", self.feature_control(self.shortcut_editor("hotkey_start_stop")))
        self.setting(layout, "Show or hide desktop overlay", "Toggles a configured on-screen overlay while capture is active. Enabling it during an existing session starts the lightweight native overlay stream without restarting capture.", self.feature_control(self.shortcut_editor("hotkey_toggle_overlay")))
        self.setting(layout, "Emergency stop", "Immediately stops the native capture engine and disables all output for the current session.", self.feature_control(self.shortcut_editor("hotkey_emergency_stop")))
        self.setting(layout, "Switch to next profile", "Stops an active capture session before loading the next local profile, so the control center and native engine never disagree about active settings.", self.feature_control(self.shortcut_editor("hotkey_next_profile")))
        self.setting(layout, "Pin or release current target", "Sends an immediate request to the native engine. Manual target lock must be enabled and an active target must exist.", self.feature_control(self.shortcut_editor("hotkey_toggle_target_lock")))
        self.setting(layout, "Save annotated snapshot", "Writes the newest annotated native debug preview as a PNG. This shortcut never records desktop video or changes capture settings.", self.feature_control(self.shortcut_editor("hotkey_save_screenshot")))
        layout=profiles.body
        for slot in range(1, 4):
            self.setting(
                layout,
                f"Profile slot {slot}",
                "Bind one global shortcut directly to a saved local profile. An active session stops before the selected profile is loaded, keeping the native engine and control center synchronized.",
                self.feature_control(self.profile_hotkey_editor(slot)),
            )
        self.hotkey_status = QLabel("Checking global shortcut registration…")
        self.hotkey_status.setObjectName("detail")
        self.hotkey_status.setWordWrap(True)
        layout.addWidget(self.hotkey_status)
        reset = QPushButton("Restore default shortcuts")
        reset.clicked.connect(self.restore_default_hotkeys)
        self.setting(layout, "Restore defaults", "Restore the default Ctrl+Alt shortcut set for this profile.", self.feature_control(reset))
        for card in page.findChildren(QFrame,'settingsCard'):
            detail=QLabel(card.toolTip());detail.setObjectName('fieldDescription');detail.setWordWrap(True)
            labels=card.layout().itemAt(1).layout()
            if labels is not None:labels.addWidget(detail)
        layout.addStretch(1)
        return page

    def automation_page(self) -> QWidget:
        page, layout = self.new_page("API Suite", "Enable an authenticated local control API for dashboards, plugins, companion software, and trusted remote control.")
        from mission_layout import TaskColumns,TaskCard
        page_layout=layout
        columns=TaskColumns(proportions=(1,1))
        for panel in (columns.left,columns.right):panel.setObjectName('missionTaskColumn')
        connection=TaskCard('Connection','connection');access=TaskCard('Access','shield')
        columns.left_layout.addWidget(connection);columns.left_layout.addStretch(1)
        columns.right_layout.addWidget(access);columns.right_layout.addStretch(1)
        page_layout.addWidget(columns)
        layout=connection.body
        from observation_ui import install
        install(self,layout)
        self.section(layout, "Local control API")
        enabled = QCheckBox("Enabled")
        enabled.setChecked(self.settings.local_api_enabled)
        enabled.toggled.connect(lambda state: self.change("local_api_enabled", state))
        self.setting(layout, "Local API", "Off by default. When enabled, Vapor Lock accepts authenticated JSON commands only from this computer unless LAN access is explicitly enabled below.", self.feature_control(enabled))
        lan = QCheckBox("Allow other devices on this network")
        lan.setChecked(self.settings.local_api_lan_enabled)
        lan.toggled.connect(lambda state: self.change("local_api_lan_enabled", state))
        self.setting(access.body, "LAN access", "Optional HTTPS access with bearer authentication. Export the public certificate and trust it in your companion client; never disable certificate verification. Use a trusted private network.", self.feature_control(lan))
        port = QSpinBox()
        port.setRange(1024, 65535)
        port.setValue(self.settings.local_api_port)
        port.valueChanged.connect(lambda value: self.change("local_api_port", int(value)))
        self.setting(layout, "TCP port", "The port for localhost or optional LAN access. Changing it restarts the local API when enabled.", self.feature_control(port))
        layout=access.body
        token = QWidget()
        token_row = QVBoxLayout(token)
        token_row.setContentsMargins(0, 0, 0, 0)
        self.local_api_token = QLineEdit(self.local_api.token)
        self.local_api_token.setReadOnly(True)
        self.local_api_token.setMinimumWidth(0)
        copy = QPushButton("Copy")
        copy.setProperty('missionIcon','copy')
        copy.setEnabled(_NATIVE_INJECTION is None)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.local_api.token))
        regenerate = QPushButton("Regenerate")
        regenerate.setProperty('missionIcon','refresh')
        regenerate.setObjectName("dangerButton")
        regenerate.clicked.connect(self.regenerate_local_api_token)
        token_row.addWidget(self.local_api_token, 1)
        from mission_layout import ActionFlow
        token_actions=ActionFlow()
        token_actions.addWidget(copy)
        token_actions.addWidget(regenerate)
        token_row.addLayout(token_actions)
        self.setting(layout, "Bearer token", "A random local secret required by every API request. It is stored outside profiles, never logged, and is replaced immediately when regenerated.", self.feature_control(token))
        self.local_api_status = QLabel()
        self.local_api_status.setObjectName("detail")
        self.local_api_status.setWordWrap(True)
        connection.body.addWidget(self.local_api_status)
        certificate = QPushButton("Export public API certificate")
        certificate.setProperty('missionIcon','download')
        certificate.clicked.connect(self.export_local_api_certificate)
        layout.addWidget(self.feature_control(certificate))
        self.refresh_local_api_status()
        endpoints_card=TaskCard('Available endpoints','code');page_layout.addWidget(endpoints_card)
        layout=endpoints_card.body
        endpoints = QLabel("GET  /api/v1/health, /status, /settings, /profiles, /models, /compensation-presets, /catalog, /schema, /observations\nPOST /api/v1/session, /settings, /profile, /model, /compensation, /action\n\nRequests use Authorization: Bearer <token>. The API controls the workspace but deliberately excludes preview pixels, local paths, activation data, and hardware identifiers.")
        endpoints.setObjectName("detail")
        endpoints.setWordWrap(True)
        layout.addWidget(endpoints)
        layout.addStretch(1)
        return page

    def diagnostics_page(self) -> QWidget:
        page, layout = self.new_page("Status & Diagnostics", "Review lightweight telemetry emitted by the native engine.")
        layout.setSpacing(6)
        from mission_layout import MetricGrid,TaskColumns,TaskCard,ActionFlow
        state, self.diag_state = self.metric_card("Session state",'power')
        fps, self.diag_fps = self.metric_card("Processing rate",'pulse')
        latency, self.diag_latency = self.metric_card("Inference time",'clock')
        tracks, self.diag_tracks = self.metric_card("Active tracks",'target')
        preprocess, self.diag_preprocess = self.metric_card("Preprocess time",'image')
        model, self.diag_model = self.metric_card("Model execution",'chip')
        target, self.diag_target = self.metric_card("Current lock",'lock')
        freshness,self.diag_freshness=self.metric_card('Selected observation age','clock')
        layout.addWidget(MetricGrid((state,fps,latency,tracks,preprocess,model,target,freshness)))
        page_layout=layout
        columns=TaskColumns(proportions=(1,1))
        for panel in (columns.left,columns.right):panel.setObjectName('missionTaskColumn')
        layout.addWidget(columns)
        history=TaskCard('Local performance history','chart')
        engine_log=TaskCard('Engine log','pulse')
        history.body.setContentsMargins(14,14,14,14)
        engine_log.body.setContentsMargins(14,14,14,14)
        columns.left_layout.addWidget(history);columns.left_layout.addStretch(1)
        columns.right_layout.addWidget(engine_log)
        detail_columns=TaskColumns(proportions=(1,1))
        page_layout.addWidget(detail_columns)
        pipeline_card=TaskCard('Pipeline freshness','pulse')
        detail_columns.left_layout.addWidget(pipeline_card)
        layout=pipeline_card.body
        self.pipeline_diagnostics = QLabel(self.t('Unavailable'))
        self.pipeline_diagnostics.setTextFormat(Qt.TextFormat.PlainText)
        self.pipeline_diagnostics.setWordWrap(True)
        self.pipeline_diagnostics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.pipeline_diagnostics)
        pipeline_help = QLabel(self.t('For output state and stop reasons, open the native workspace → Movement → Output diagnostics.'))
        pipeline_help.setWordWrap(True)
        layout.addWidget(pipeline_help)
        layout=history.body
        self.performance_chart = PerformanceChart(self.performance_history)
        layout.addWidget(self.performance_chart)
        self.performance_summary = QLabel(self.performance_history.summary())
        self.performance_summary.setWordWrap(True)
        layout.addWidget(self.performance_summary)
        clear_history = QPushButton('Clear history')
        clear_history.clicked.connect(self.clear_performance_history)
        layout.addWidget(clear_history)
        layout=engine_log.body
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setMinimumHeight(180)
        layout.addWidget(self.log_view)
        controls = QWidget()
        row = ActionFlow(controls)
        row.setContentsMargins(0, 0, 0, 0)
        copy = QPushButton("Copy diagnostics")
        copy.setProperty('missionIcon','copy')
        copy.clicked.connect(self.copy_diagnostics)
        report = QPushButton("Save session report…")
        report.setProperty('missionIcon','save')
        report.clicked.connect(self.save_session_report)
        support = QPushButton('Export support bundle…')
        support.setProperty('missionIcon','download')
        support.setObjectName('exportSupportBundle')
        support.setProperty('tuningLockExempt', True)
        support.clicked.connect(self.export_support_bundle)
        clear = QPushButton("Clear log")
        clear.setProperty('missionIcon','trash')
        clear.clicked.connect(self.log_view.clear)
        row.addWidget(copy)
        row.addWidget(report)
        row.addWidget(support)
        row.addWidget(clear)
        page_layout.addWidget(controls)
        page_layout.addStretch(1)
        return page

    def range(self, field: str, low: float, high: float, decimals: int = 0, suffix: str = "") -> RangeControl:
        control = RangeControl(low, high, float(getattr(self.settings, field)), decimals, suffix)
        if field.startswith('cloud_'):
            control.spin.setRange(low,high)
        if field in {'dataset_annotation_nms_iou','ultralytics_annotation_nms_iou'}:
            control.spin.setRange(.01,.99)
        control.setProperty('guideField', field)
        if not field.startswith('cloud_'):
            self._numeric_registry.register(field, control, low, high, decimals)
        control.changed.connect(lambda value: self.change(field, int(value) if decimals == 0 else value))
        return control

    def text_setting(self, field: str) -> QLineEdit:
        control = QLineEdit(str(getattr(self.settings, field)))
        control.setProperty('guideField', field)
        def commit():
            try:
                self.change(field, control.text().strip())
            except ValueError as error:
                if field != 'training_extra_arguments' and not field.startswith('cloud_'):
                    raise
                control.setText(str(getattr(self.settings, field)))
                QMessageBox.information(self, 'Training', str(error) + '\n' + self.t('Not applied. The previous valid settings have been restored.'))
                control.setFocus()
        control.editingFinished.connect(commit)
        return control

    def color_editor(self, field: str) -> QWidget:
        """Use native color selection while retaining an exact editable hex value."""
        control = QWidget()
        row = QHBoxLayout(control)
        row.setContentsMargins(0, 0, 0, 0)
        value = QLineEdit(str(getattr(self.settings, field)))
        value.setMaxLength(9)
        value.setMinimumWidth(106)

        def commit() -> None:
            candidate = QColor(value.text().strip())
            if candidate.isValid():
                normalized = candidate.name(QColor.NameFormat.HexRgb)
                value.setText(normalized)
                self.change(field, normalized)
            else:
                value.setText(str(getattr(self.settings, field)))

        def choose() -> None:
            selected = QColorDialog.getColor(QColor(value.text().strip()), self, "Choose overlay color")
            if selected.isValid():
                value.setText(selected.name(QColor.NameFormat.HexRgb))
                commit()

        value.editingFinished.connect(commit)
        browse = QToolButton()
        browse.setText("…")
        browse.setToolTip("Choose color")
        browse.clicked.connect(choose)
        row.addWidget(value, 1)
        row.addWidget(browse)
        return control

    def shortcut_editor(self, field: str) -> QKeySequenceEdit:
        editor = QKeySequenceEdit(QKeySequence(str(getattr(self.settings, field))))
        editor.setClearButtonEnabled(True)
        editor.keySequenceChanged.connect(
            lambda sequence, setting=field: self.change(
                setting,
                sequence.toString(QKeySequence.SequenceFormat.PortableText),
            )
        )
        return editor

    def profile_hotkey_editor(self, slot: int) -> QWidget:
        control = QWidget()
        row = QHBoxLayout(control)
        row.setContentsMargins(0, 0, 0, 0)
        target_field = f"hotkey_profile_{slot}_target"
        key_field = f"hotkey_profile_{slot}"
        target = QComboBox()
        target.addItem("No profile assigned", "")
        for path in self.local_profile_files():
            target.addItem(path.stem, path.name)
        target.setCurrentIndex(max(0, target.findData(str(getattr(self.settings, target_field)))))
        target.currentIndexChanged.connect(lambda index, field=target_field, box=target: self.change(field, str(box.itemData(index))))
        editor = self.shortcut_editor(key_field)
        editor.setMinimumWidth(128)
        row.addWidget(target, 1)
        row.addWidget(editor)
        return control

    @tuning_guard
    def change(self, field: str, value: object) -> None:
        if field.startswith('cloud_'):
            from cloud_settings import validate as validate_cloud
            validate_cloud({**vars(self.settings),field:value})
        from crosshair_reference import FIELDS as CROSSHAIR_FIELDS, validate as validate_crosshair
        if field in CROSSHAIR_FIELDS:
            from batch4_guard import editable
            if not editable(self,stopped=True):return
            validate_crosshair({**vars(self.settings),field:value})
        from batch7_settings import FIELDS as FIELDS7, validate as validate7
        if field in (*FIELDS7,'dataset_annotation_replace','dataset_annotation_delete_unannotated'):
            from batch4_guard import editable
            if not editable(self,stopped=True):return
            try:validate7({**vars(self.settings),field:value})
            except ValueError:return
        from batch6_settings import FIELDS, validate
        if field in FIELDS:
            from batch4_guard import editable
            if not editable(self, stopped=True): return
            validate({field:value})
        if field == 'exclusion_zones':
            from exclusion_zones import validate_zones
            from batch4_guard import editable
            if not editable(self, stopped=True): return
            value = validate_zones(value)
        if field in BATCH3_FIELDS:
            validate_batch3({field: value})
            value = float(value)
        if field == 'model_class_aliases':
            from model_classes import validate_aliases
            validate_aliases(value)
        if field in {'dataset_max_images', 'dataset_max_mib'}:
            from collection_limits import validate_limits
            validate_limits({field: value})
        if field in {'controller_trigger_press', 'controller_trigger_release'}:
            validate_thresholds(value if field.endswith('_press') else self.settings.controller_trigger_press,
                                value if field.endswith('_release') else self.settings.controller_trigger_release)
        if field == 'training_extra_arguments':
            from training_security import load_extra_arguments
            load_extra_arguments(value)
        if field in {'dataset_annotation_nms_iou','ultralytics_annotation_nms_iou'}:
            if type(value) not in (int,float) or not .01 <= value <= .99:
                raise ValueError('Invalid annotation NMS threshold')
            value=float(value)
        if field == 'dataset_annotation_provider' and (type(value) is not str or value not in ANNOTATION_BACKENDS):
            raise ValueError('Invalid annotation backend')
        if field in CAPTURE_FIELDS:
            value = validated_capture_field(field, value)
        if field == 'ultralytics_key_protected':
            from ultralytics_credentials import validate_envelope
            validate_envelope(value)
        legacy_durations = {"max_missed": "track_retention_ms", "lost_target_timeout": "lost_target_timeout_ms", "occlusion_grace_frames": "occlusion_grace_ms"}
        if field in legacy_durations:
            setattr(self.settings, field, value)
            if field in {"provider", "cuda_preprocess"}:
                self.refresh_cuda_preprocess_controls()
            field, value = legacy_durations[field], max(0, round(float(value) * 1000 / 60))
        if field == "output_max_observation_age_ms":
            value = max(1, min(2000, int(value)))
        if field in {"input_units_per_pixel_x", "input_units_per_pixel_y"}:
            value = max(0.001, min(1000.0, float(value)))
        if field == "class_policies":
            value = validated_policies(value)
        if field in {'model_path', 'provider'}:
            self.stage_model_candidate(field, value)
            return
        if getattr(self.settings, field) != value:
            self.cancel_model_probes()
            if self._model_selection is not None: self._model_selection.invalidate()
            if self._live_owner is not None and field != 'profile_name':
                self._start_intent_generation = getattr(self, '_start_intent_generation', 0)+1
                self._policy_start_pending = False
                self._policy_start_generation = None
                self._pending_controller_start = None
            if not self._undo_in_progress:
                now = time.monotonic()
                if self._last_edit[0] != field or now - self._last_edit[1] > 0.6:
                    self._undo_history.append((field, deepcopy(getattr(self.settings, field))))
                    self._undo_history = self._undo_history[-100:]
                self._last_edit = (field, now)
                self.undo_button.setEnabled(True)
            setattr(self.settings, field, value)
            if field in {"confidence", "acquire_confidence", "retain_confidence"}:
                self.refresh_target_confidence_summary()
            if field.endswith(('_effects_enabled','_effects_extra_copies')):
                prefix=field.split('_effects_')[0]
                control=getattr(self,'_annotation_effect_counts',{}).get(prefix)
                if control is not None:
                    control.setEnabled(getattr(self.settings,prefix+'_effects_enabled') and getattr(self.settings,prefix+'_effects_extra_copies'))
            if field.startswith('dataset_') or field=='model_path':
                self._local_effect_preview=None
                if hasattr(self,'dataset_effects_target'):
                    from annotation_suite_ui import refresh_local_target
                    refresh_local_target(self)
            if field.startswith('ultralytics_') or (field=='model_path' and not self.settings.ultralytics_annotation_model):
                from annotation_suite_ui import refresh_run_summary
                refresh_run_summary(self)
                self._ultralytics_preview = None
                if hasattr(self, 'ultralytics_apply_button'):
                    self.ultralytics_apply_button.setEnabled(False)
                    self.ultralytics_inspect_button.setEnabled(False)
                if field == 'ultralytics_key_protected':
                    from annotation_suite_ui import refresh_key_status
                    refresh_key_status(self)
            if field == 'dataset_directory':
                self.refresh_dataset_destination()
            if field == 'dataset_capture_confidence_enabled' and hasattr(self, 'dataset_capture_confidence_editor'):
                self.dataset_capture_confidence_editor.setEnabled(value)
            self.dirty = True
            live_applied = self.engine.apply_live_setting(field, value) if self.engine.active else False
            self._profile_change_suffix = ("live change requested" if field in EngineBridge.LIVE_NATIVE_SETTINGS else "live changes applied") if live_applied else "changes apply next session" if self.engine.active else "unsaved changes"
            self.update_profile_summary()
            self.refresh_readiness()
            if field.startswith("desktop_overlay") or field in {
                "overlay_boxes", "overlay_fov", "fov_shape", "snap_radius", "near_radius", "acquisition_radius",
                "monitor", "region_x", "region_y", "region_width", "region_height",
                "output_max_observation_age_ms",
            }:
                self.update_desktop_overlay()
            if field.startswith("hotkey_"):
                QTimer.singleShot(0, self.register_global_hotkeys)
            if field in {
                "activation_source", "controller_binding", "manual_target_lock_enabled",
                "manual_lock_source", "manual_lock_controller_binding",
            }:
                self.refresh_controller_binding_status()
            if field in {"output_transport", "virtual_mouse_opt_in", "controller_output_opt_in", "virtual_controller_mode", "virtual_controller_identity"}:
                self.refresh_controller_bridge_controls()
            if field in {"local_api_enabled", "local_api_lan_enabled", "local_api_port"}:
                self.sync_local_api()
            self.refresh_local_api_snapshot()
            self.refresh_policy_command_deck()
            if self._live_owner is not None:
                self._live_owner.native_changed()

    def api_safe_settings(self) -> dict[str, object]:
        """Return readable settings without disclosing local system details."""
        blocked = {
            "crosshair_enabled", "crosshair_calibration",
            "dataset_annotation_additive", "inference_cpu_placement",
            "inference_dml_device", "inference_cuda_device", "inference_trt_device", "dataset_annotation_batch",
            "exclusion_zones",
            "model_class_aliases",
            "model_path", "dataset_directory", "dataset_annotation_model", "training_extra_arguments", "training_platform_project", "training_base_model", "training_dataset", "motion_compensation_library", "hardware_serial_port",
            "kmbox_net_address", "kmbox_net_device_code", "kmbox_a_library_path",
            "local_api_enabled", "local_api_lan_enabled", "local_api_port",
        }
        return {field: deepcopy(getattr(self.settings, field)) for field in Settings.__dataclass_fields__
                if field not in blocked and not field.startswith(('ultralytics_', 'cloud_'))}

    @staticmethod
    def api_write_only_fields() -> set[str]:
        """Allow integrations to configure transports without reading identifiers back."""
        return {
            "model_path", "dataset_directory", "dataset_annotation_model", "training_extra_arguments", "training_platform_project", "training_base_model", "training_dataset", "motion_compensation_library",
            "hardware_serial_port", "kmbox_net_address", "kmbox_net_device_code", "kmbox_a_library_path",
        }

    def api_model_catalog(self) -> list[dict[str, str]]:
        models_root = (ROOT / "models").resolve()
        try:
            models, _aliases = discover_local_models(models_root)
        except OSError:
            models = []
        return [
            {"id": str(model.relative_to(models_root)).replace("\\", "/"), "name": model.name}
            for model in models
        ]

    def api_compensation_catalog(self) -> list[dict[str, object]]:
        try:
            presets, _errors = load_motion_compensation(self.motion_compensation_path())
        except (OSError, ValueError):
            presets = []
        return [
            {"id": preset.identifier, "vertical": preset.vertical, "horizontal": preset.horizontal}
            for preset in presets
        ]

    def refresh_local_api_snapshot(self) -> None:
        from api_jobs import fingerprint
        self._api_job_stamp=fingerprint(self)
        if not hasattr(self, "local_api") or False or not self.settings.local_api_enabled:
            return
        status = {
            "edition": EDITION,
            "version": PRODUCT_VERSION,
            "state": self._last_engine_state,
            "session_mode": self.session_mode,
            "provider": self.engine.active_provider or self.settings.provider,
            "active": self.engine.session_running,
            "metrics": {
                key: self._last_output_data.get(key)
                for key in ("fps", "capture_fps", "inference_fps", "output_fps", "output_observation_age_ms", "inference_ms", "capture_ms", "preprocess_ms", "tracking_ms", "tracks", "selected_track", "selected_class", "output_active")
            } if isinstance(self._last_output_data, dict) else {},
        }
        cache = getattr(self, "_api_catalog_cache", None)
        if cache is None:
            cache = ([{"name": path.stem, "id": path.name} for path in self.local_profile_files()],
                     self.api_model_catalog(), self.api_compensation_catalog())
            self._api_catalog_cache = cache
        profiles, models, presets = cache
        mutable = sorted(self.remote_api_writable_fields())
        readable = sorted(self.api_safe_settings())
        self.local_api.update_snapshot({
            "status": status,
            "settings": self.api_safe_settings(),
            "profiles": profiles,
            "models": models,
            "compensation_presets": presets,
            "catalog": {
                "profiles": profiles,
                "models": models,
                "compensation_presets": presets,
            },
            "schema": {
                "protocol_version": "2",
                "observations": {"path":"/api/v1/observations","opt_in":"native-session-only","limit":256,"max_age_ms":150,"coordinate_space":"capture-pixels-xywh"},
                "readable_settings": readable,
                "writable_settings": mutable,
                "write_only_settings": sorted(self.api_write_only_fields()),
                "enum_values": SETTING_ENUMS,
                "command_status": "/api/v1/commands/{command_id}",
                "jobs": {"kinds":["annotation","training"],"start":"native-confirmation-required","status":"/api/v1/jobs/{job_id}","cancel":"exact-job-id","local_only":True},
                "actions": [
                    "toggle-overlay", "show-overlay", "hide-overlay", "emergency-stop", "save-snapshot",
                    "toggle-target-lock", "next-profile", "reset-default", "inspect-model",
                    "validate-model", "benchmark-providers",
                ],
                "session_actions": ["start", "stop", "toggle", "set-mode"],
                "session_modes": ["control", "observe"],
                "profile_actions": ["load", "save", "reset", "next"],
                "model_actions": ["select", "clear", "refresh", "inspect", "validate", "benchmark"],
                "compensation_actions": ["select", "reload", "calibrate"],
            },
        })

    @staticmethod
    def remote_api_writable_fields() -> set[str]:
        return set(Settings.__dataclass_fields__) - {
            "crosshair_enabled", "crosshair_calibration",
            "dataset_annotation_additive", "inference_cpu_placement",
            "inference_dml_device", "inference_cuda_device", "inference_trt_device", "dataset_annotation_batch",
            "local_api_enabled", "local_api_lan_enabled", "local_api_port",
        }

    def sync_local_api(self) -> None:
        if False or not self.settings.local_api_enabled:
            self.local_api.stop()
            self.refresh_local_api_status()
            return
        started, detail = self.local_api.start(
            allow_lan=self.settings.local_api_lan_enabled,
            port=self.settings.local_api_port,
        )
        self._api_catalog_cache = None
        self.refresh_local_api_snapshot()
        if not started:
            self.log("WARN", detail)
        self.refresh_local_api_status()

    def refresh_local_api_status(self) -> None:
        if not hasattr(self, "local_api_status"):
            return
        if self.local_api.running:
            scope = 'LAN' if self.settings.local_api_lan_enabled else 'this computer'
            self.local_api_status.setText(f'Running for {scope}: {self.local_api.address}')
            if self.settings.local_api_lan_enabled and self.local_api.certificate_fingerprint:
                self.local_api_status.setText(self.local_api_status.text() + f'\nCertificate SHA-256: {self.local_api.certificate_fingerprint}')
        elif self.local_api.error:
            self.local_api_status.setText(self.local_api.error)
        else:
            self.local_api_status.setText('Disabled. Enable Local API to start an authenticated endpoint.')

    @disposable_deny('token-network')
    def regenerate_local_api_token(self) -> None:
        answer = QMessageBox.question(
            self,
            "Regenerate API token",
            "Regenerate the local API token? Existing companion software will lose access until updated.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        was_enabled = self.settings.local_api_enabled
        self.local_api.stop()
        self.local_api = LocalControlApi(lambda command: self.local_api_command.emit(
            self.stamp_local_api_command(command)), token=regenerate_token())
        if hasattr(self,'observation_sharing'):self.observation_sharing.rebind()
        if hasattr(self, "local_api_token"):
            self.local_api_token.setText(self.local_api.token)
        if was_enabled:
            self.sync_local_api()
        self.refresh_local_api_snapshot()
        self.log("INFO", "The local API bearer token was regenerated.")

    @disposable_deny('certificate-export')
    def export_local_api_certificate(self) -> None:
        certificate = self.local_api.certificate_path
        if certificate is None or not self.local_api.running:
            QMessageBox.information(self, "Local API", "Enable Local API and LAN access before exporting its public certificate.")
            return
        destination, _ = QFileDialog.getSaveFileName(self, "Export public API certificate", "VaporLock-API.pem", "PEM certificates (*.pem)")
        if destination:
            try:
                Path(destination).write_bytes(certificate.read_bytes())
            except OSError as error:
                QMessageBox.warning(self, "Local API", str(error))

    def apply_local_api_command(self, command: dict) -> None:
        """Acknowledge dispatch separately from long-running capture/job completion."""
        identifier = command.get("command_id") if isinstance(command, dict) else None
        try:
            result = self._dispatch_local_api_command(command)
            if identifier:
                self.local_api.finish_command(identifier, result or {"state": "dispatched"})
        except Exception as error:
            self.log("WARN", f"Local API command failed: {type(error).__name__}")
            if identifier:
                self.local_api.finish_command(identifier, {"state": "rejected", "error": "command could not be applied"})

    def stamp_local_api_command(self, command):
        # Capture at enqueue, not dispatch: lock/unlock invalidates delayed edits.
        return {**command, '_tuning_epoch': self._tuning_epoch, '_job_stamp':getattr(self,'_api_job_stamp',None)}

    def _dispatch_local_api_command(self, command: dict) -> dict | None:
        if not False and isinstance(command,dict) and command.get('type')=='jobs' and isinstance(command.get('payload'),dict) and command['payload'].get('action')=='cancel':
            from api_jobs import registry
            return registry(self).request(command['payload'],None)
        if self._native_closing:
            return {'state': 'rejected', 'error': 'shutdown is pending'}
        """Apply a bounded API request on the Qt thread after authentication."""
        if False or not isinstance(command, dict):
            return {"state": "rejected", "error": "API is unavailable"}
        kind = command.get("type")
        payload = command.get("payload")
        emergency = kind == "action" and isinstance(payload, dict) and payload.get("action") == "emergency-stop"
        stopping = emergency or (kind == 'session' and isinstance(payload, dict)
                                and payload.get('action') == 'stop' and payload.get('mode') is None)
        if not stopping and command.get('_tuning_epoch') != self._tuning_epoch:
            return {'state': 'rejected', 'error': 'tuning authorization changed'}
        if self._tuning_locked and not emergency and (kind != "session" or not isinstance(payload, dict)
                or payload.get("action") not in {"start", "stop", "toggle"}
                or payload.get("mode") is not None):
            return {"state": "rejected", "error": "tuning is locked"}
        if not isinstance(payload, dict):
            return
        if kind == 'jobs':
            from api_jobs import registry
            return registry(self).request(payload,command.get('_job_stamp'))
        if kind == "session":
            action = str(payload.get("action", "")).strip().lower()
            requested_mode = str(payload.get("mode", "")).strip().lower()
            if requested_mode:
                if requested_mode not in {"control", "observe"} or not self.set_session_mode(requested_mode):
                    self.log("WARN", "Local API rejected a session mode change while capture is active or because the mode is invalid.")
                    return
            if action == "start" and not self.engine.active:
                self.start_capture()
            elif action == "stop":
                self.stop_capture()
            elif action == "toggle":
                self.hotkey_toggle_capture()
            elif action == "set-mode" and requested_mode:
                self.log("INFO", f"Local API selected {self.session_mode.replace('-', ' ')} session mode.")
            else:
                self.log("WARN", "Local API rejected an unsupported session action.")
            return
        if kind == "settings":
            values = payload.get("values")
            if not isinstance(values, dict) or len(values) > 128:
                self.log("WARN", "Local API rejected an invalid settings update.")
                return {"state": "rejected", "error": "invalid settings update"}
            rejected: list[str] = []
            prepared = {}
            for field, value in values.items():
                if not isinstance(field, str) or field not in self.remote_api_writable_fields():
                    rejected.append(str(field))
                    continue
                coerced = self.coerce_remote_setting(field, value)
                if coerced is None:
                    rejected.append(field)
                    continue
                prepared[field] = coerced
            if rejected:
                self.log("WARN", f"Local API rejected settings: {', '.join(rejected[:8])}")
                return {"state": "rejected", "fields": rejected, "error": "settings were not applied"}
            threshold_fields = {"controller_trigger_press", "controller_trigger_release"}
            if threshold_fields.intersection(prepared):
                press = prepared.get("controller_trigger_press", self.settings.controller_trigger_press)
                release = prepared.get("controller_trigger_release", self.settings.controller_trigger_release)
                validate_thresholds(press, release)
                self.apply_controller_thresholds(press, release)
            for field, value in sorted(prepared.items(), key=lambda pair: pair[0] in {"model_path", "provider"}):
                if field in threshold_fields:
                    continue
                self.change(field, value)
            if prepared:
                timer = getattr(self, "_api_controls_timer", None)
                if timer is None:
                    timer = self._api_controls_timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(self.rebuild_pages)
                timer.start(200)
            self.log("INFO", "Local API applied setting changes.")
            return {"state": "candidate-staged" if set(prepared) & {"model_path", "provider"} else "applied", "fields": list(prepared)}
        if kind == "profile":
            self.apply_local_api_profile(payload)
            return
        if kind == "model":
            self.apply_local_api_model(payload)
            return
        if kind == "compensation":
            self.apply_local_api_compensation(payload)
            return
        if kind == "action":
            action = str(payload.get("action", "")).strip().lower()
            actions = {
                "toggle-overlay": self.hotkey_toggle_overlay,
                "show-overlay": lambda: (self.change("desktop_overlay_enabled", True), self.change("desktop_overlay_visible", True)),
                "hide-overlay": lambda: self.change("desktop_overlay_visible", False),
                "emergency-stop": self.hotkey_emergency_stop,
                "save-snapshot": self.save_annotated_snapshot,
                "toggle-target-lock": self.hotkey_toggle_target_lock,
                "next-profile": self.hotkey_next_profile,
                "reset-default": self.reset_settings,
                "inspect-model": self.inspect_model,
                "validate-model": self.validate_model_runtime,
                "benchmark-providers": self.benchmark_providers,
            }
            callback = actions.get(action)
            if callback is None:
                self.log("WARN", "Local API rejected an unsupported action.")
            else:
                callback()

    @staticmethod
    def local_api_profile_path(identifier: object) -> Path | None:
        name = str(identifier).strip()
        if (not name or len(name) > 128 or Path(name).name != name
                or any(ord(character) < 32 or character in '<>:"/\\|?*' for character in name)
                or name.endswith((".", " ")) or Path(name).is_reserved()):
            return None
        candidate = (PROFILE_DIRECTORY / name).with_suffix(".json").resolve()
        try:
            candidate.relative_to(PROFILE_DIRECTORY.resolve())
        except ValueError:
            return None
        return candidate

    def apply_local_api_profile(self, payload: dict) -> None:
        action = str(payload.get("action", "load")).strip().lower()
        if action == "reset":
            self.reset_settings()
            return
        if action == "next":
            self.hotkey_next_profile()
            return
        candidate = self.local_api_profile_path(payload.get("id", ""))
        if candidate is None:
            self.log("WARN", "Local API rejected an invalid profile identifier.")
            return
        if action == "save":
            if self.engine.active:
                self.log("WARN", "Stop capture before saving a local API profile.")
                return
            PROFILE_DIRECTORY.mkdir(exist_ok=True)
            self.write_profile(candidate)
            self.refresh_local_api_snapshot()
            return
        if action != "load" or not candidate.is_file():
            self.log("WARN", "Local API requested an unavailable profile operation.")
            return
        self.load_profile_path(candidate)

    def apply_local_api_model(self, payload: dict) -> None:
        action = str(payload.get("action", "select")).strip().lower()
        if action == "clear":
            self.change("model_path", "")
            self.refresh_readiness()
            return
        if action == "refresh":
            self.refresh_model_library()
            self.refresh_local_api_snapshot()
            return
        direct_actions = {
            "inspect": self.inspect_model,
            "validate": self.validate_model_runtime,
            "benchmark": self.benchmark_providers,
        }
        if action in direct_actions:
            direct_actions[action]()
            return
        identifier = str(payload.get("id", "")).strip().replace("/", "\\")
        models_root = (ROOT / "models").resolve()
        candidate = (models_root / identifier).resolve()
        try:
            candidate.relative_to(models_root)
        except ValueError:
            self.log("WARN", "Local API rejected an invalid model identifier.")
            return
        if action != "select" or candidate.suffix.lower() not in (".onnx", ".cloud") or not candidate.is_file():
            self.log("WARN", "Local API requested an unavailable model operation.")
            return
        self.change("model_path", str(candidate))
        self.refresh_model_library()
        self.refresh_readiness()
        self.log("INFO", f"Local API requested model validation: {candidate.name}; output remains stopped.")

    def apply_local_api_compensation(self, payload: dict) -> None:
        action = str(payload.get("action", "select")).strip().lower()
        if action == "reload":
            self.reload_motion_compensation_library()
            self.refresh_local_api_snapshot()
            return
        if action == "calibrate":
            identifier = str(payload.get("id", "")).strip()
            self.begin_named_motion_compensation_calibration(identifier)
            return
        identifier = str(payload.get("id", "")).strip()
        preset = find_motion_preset(self.api_compensation_presets(), identifier)
        if action != "select" or preset is None:
            self.log("WARN", "Local API requested an unavailable compensation preset.")
            return
        self.change("motion_compensation_preset", preset.identifier)
        self.change("motion_compensation_vertical", preset.vertical)
        self.change("motion_compensation_horizontal", preset.horizontal)
        self.log("INFO", f"Local API selected compensation preset: {preset.identifier}")

    def api_compensation_presets(self) -> list[MotionCompensationPreset]:
        try:
            presets, _errors = load_motion_compensation(self.motion_compensation_path())
            return presets
        except (OSError, ValueError):
            return []

    def coerce_remote_setting(self, field: str, value: object) -> object | None:
        if field in BATCH3_FIELDS:
            try: validate_batch3({field: value})
            except ValueError: return None
            return float(value)
        if field in CAPTURE_FIELDS:
            try:
                return validated_capture_field(field, value)
            except ValueError:
                return None
        current = getattr(self.settings, field)
        if field in SETTING_ENUMS and value not in SETTING_ENUMS[field]:
            return None
        if field == "dataset_review_class_slots":
            return value if isinstance(value, list) and len(value) == 9 and all(type(item) is int and 0 <= item <= 10000 for item in value) else None
        if field == "class_policies":
            try:
                return validated_policies(value)
            except ValueError:
                return None
        if isinstance(current, bool):
            return value if isinstance(value, bool) else None
        if isinstance(current, int) and not isinstance(current, bool):
            return value if type(value) is int and -1_000_000 <= value <= 1_000_000 else None
        if isinstance(current, float):
            return float(value) if type(value) in (int, float) and math.isfinite(float(value)) and -1_000_000 <= float(value) <= 1_000_000 else None
        if isinstance(current, str):
            if not isinstance(value, str) or len(value) > 4096 or any(character in value for character in "\r\n\t"):
                return None
            if field.endswith("_color") and not QColor(value).isValid():
                return None
            return value
        return None

    @tuning_guard
    def undo_tuning(self) -> bool:
        if self._live_owner is not None and not self._live_owner.authorize('undoTuning'):
            return False
        if not self._undo_history:
            return False
        from batch6_settings import FIELDS
        from batch7_settings import FIELDS as FIELDS7, validate as validate7
        from crosshair_reference import FIELDS as CROSSHAIR_FIELDS
        FIELDS=(*FIELDS,*FIELDS7,*CROSSHAIR_FIELDS,'dataset_annotation_replace','dataset_annotation_delete_unannotated')
        try:validate7({**vars(self.settings),self._undo_history[-1][0]:self._undo_history[-1][1]})
        except ValueError:return False
        if self._undo_history[-1][0]=='exclusion_zones' or self._undo_history[-1][0] in FIELDS:
            from batch4_guard import editable
            if not editable(self,stopped=True): return False
        field, value = self._undo_history.pop()
        self._undo_in_progress = True
        try:
            self.change(field, value)
        finally:
            self._undo_in_progress = False
        self._last_edit = (None, 0.0)
        self.undo_button.setEnabled(bool(self._undo_history))
        self.rebuild_pages()
        return True

    def clear_performance_history(self) -> None:
        self.performance_history.clear()
        if hasattr(self, "performance_chart"):
            self.performance_chart.update()
            self.performance_summary.setText(self.performance_history.summary())

    def clear_tuning_undo(self) -> None:
        self._undo_history.clear()
        self._last_edit = (None, 0.0)
        self.undo_button.setEnabled(False)



    def offer_initialization(self) -> None:
        if not initialization_complete():
            self.open_setup_and_calibration(required=True)
            return
        self.offer_acceleration_setup()

    def set_start_enabled(self, requested: bool) -> None:
        self.start_button.setEnabled(bool(requested) and not False and not self.model_probe_busy() and not self._profile_transition_pending and not self._native_closing and not self._policy_start_pending and not self._pending_controller_start and not self.xbox_compatible_bridge.preparing)










    def offer_acceleration_setup(self) -> None:
        if AccelerationManager.should_offer_setup():
            AccelerationManager.mark_setup_offered()
            QTimer.singleShot(450, self.install_recommended_acceleration)

    @tuning_guard
    def open_setup_and_calibration(self, _checked: bool = False, *, required: bool = False) -> bool:
        if self.engine.active or self.xbox_compatible_bridge.preparing:
            self.log("INFO", "Stop capture before opening setup and calibration.")
            return False
        wizard = InitializationWizard(self.settings, self)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            if required:
                self.log("WARN", "Initialization was not completed. Capture remains unavailable until setup is applied.")
            return False
        if self._tuning_locked:
            return False
        values = wizard.values()
        preset = str(values.pop("calibration_preset"))
        for field, value in values.items():
            setattr(self.settings, field, value)
        presets = {
            "stable": {"gain": 0.18, "max_step": 10, "movement_smoothing": 0.52, "anchor_smoothing": 0.76, "anchor_settle_radius": 2.0},
            "balanced": {"gain": 0.32, "max_step": 18, "movement_smoothing": 0.36, "anchor_smoothing": 0.66, "anchor_settle_radius": 1.5},
            "responsive": {"gain": 0.48, "max_step": 32, "movement_smoothing": 0.20, "anchor_smoothing": 0.48, "anchor_settle_radius": 1.0},
        }
        for field, value in presets.get(preset, {}).items():
            setattr(self.settings, field, value)
        try:
            save_initialization_state()
        except OSError as error:
            QMessageBox.warning(self, "Initialization", f"Windows could not save setup completion: {error}")
            return False
        PROFILE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self.write_profile(PROFILE_DIRECTORY / "default.json")
        self.rebuild_pages()
        self.register_global_hotkeys()
        self.refresh_readiness()
        self.log("INFO", "Initialization and calibration settings applied. Capture remains idle until started.")
        self.offer_acceleration_setup()
        return True

    def set_session_mode(self, mode: str) -> bool:
        """Select a per-launch output policy without mutating the profile."""
        normalized = str(mode).strip().lower()
        if normalized not in {"control", "observe"} or self.engine.active or self.xbox_compatible_bridge.preparing:
            return False
        self.session_mode = normalized
        if hasattr(self, "session_mode_choice"):
            index = self.session_mode_choice.findData(normalized)
            if index >= 0 and self.session_mode_choice.currentIndex() != index:
                self.session_mode_choice.blockSignals(True)
                self.session_mode_choice.setCurrentIndex(index)
                self.session_mode_choice.blockSignals(False)
        self.refresh_readiness()
        self.refresh_local_api_snapshot()
        return True

    @tuning_guard
    def change_session_mode(self, index: int) -> None:
        requested = str(self.session_mode_choice.itemData(index) or "control")
        if self.set_session_mode(requested):
            detail = (
                "Observe only selected. Capture, detection, tracking, previews, and recording remain available; all output is blocked."
                if requested == "observe"
                else "Control selected. The configured movement and event outputs may run when capture starts."
            )
            self.log("INFO", detail)
            return
        current = self.session_mode_choice.findData(self.session_mode)
        self.session_mode_choice.blockSignals(True)
        self.session_mode_choice.setCurrentIndex(max(0, current))
        self.session_mode_choice.blockSignals(False)
        self.log("WARN", "Stop capture before changing session mode.")

    def session_settings(self) -> Settings:
        """Build an output-safe copy for the native process."""
        session = Settings(**asdict(self.settings))
        if self.session_mode == "observe":
            session.movement_enabled = False
            session.motion_compensation_enabled = False
            session.event_action_enabled = False
            # Output adapters may open drivers, serial devices, sockets, or a
            # controller bridge during preparation. Observe mode must not
            # touch any of them.
            session.output_transport = "mouse"
        return session

    def start_capture(self) -> None:
        if self.cloud_background_busy():
            self.reject_cloud_busy_start()
            return
        if self._native_closing or self._profile_transition_pending or self.model_probe_busy() or (_NATIVE_INJECTION is not None):
            self.log('INFO', 'Capture is unavailable during this native transition or verification session.')
            return
        if self.engine.active or self._policy_start_pending:
            return
        self._model_selection_status_copy = None
        self.render_model_selection_status()
        if self._model_selection is not None: self._model_selection.invalidate()
        if self._live_owner is not None:
            self._live_owner.capture_started()
        self._start_intent_generation = getattr(self, "_start_intent_generation", 0)+1
        self._start_capture_authorized(self._start_intent_generation)

    def _start_capture_authorized(self, intent=None) -> None:
        if self.cloud_background_busy():
            self.reject_cloud_busy_start()
            return
        if self._native_closing or self._profile_transition_pending or self.model_probe_busy() or _NATIVE_INJECTION is not None:
            return
        if intent is not None and intent != getattr(self, "_start_intent_generation", 0): return
        if not initialization_complete() or not self.settings.hotkey_calibration.strip():
            self.log("INFO", "Complete initialization and confirm the calibration shortcut before starting capture.")
            self.open_setup_and_calibration(required=True)
            return
        if self.acceleration.active:
            self.log("INFO", "Acceleration installation is still running. Capture will be available when it completes.")
            return
        cloud=Path(self.settings.model_path).suffix.lower()=='.cloud'
        requested = ('dml' if cloud else self.acceleration.recommended_provider()) if self.settings.provider == "auto" else self.settings.provider
        if cloud:
            from cloud_dependencies import ready_for
            if not ready_for(requested) or self.engine.executable_for('cpu') is None:
                self.log('INFO', '.cloud requires its bundled CPU, DirectML or CUDA component and native capture engine. TensorRT is not supported for .cloud.')
                self.show_model_start_failure()
                return
        elif requested in {"cuda", "dml"} and not self.acceleration.is_installed(requested):
            self.log("INFO", f"{self.engine.provider_label(requested)} is not installed yet. Install it from Model Library or use CPU.")
            self.show_model_start_failure()
            return
        self.synchronize_motion_compensation_for_start()
        session_settings = self.session_settings()
        observe_only = self.session_mode == "observe"
        if not observe_only and self.settings.output_transport == "virtual-mouse" and not self.settings.virtual_mouse_opt_in:
            if self._tuning_locked:
                self.log('INFO', self.t('Unlock tuning to change settings.'))
                return
            answer = QMessageBox.question(
                self,
                "Enable virtual mouse transport",
                "This optional transport uses a local administrator-installed input driver. Install it from Movement and restart Windows before starting capture. Enable this transport?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.log("INFO", "Virtual mouse transport remains disabled.")
                return
            self.change("virtual_mouse_opt_in", True)
        if not observe_only and self.settings.output_transport == "virtual-xbox":
            if not self.settings.controller_output_opt_in:
                if self._tuning_locked:
                    self.log('INFO', self.t('Unlock tuning to change settings.'))
                    return
                answer = QMessageBox.question(
                    self,
                    "Enable Xbox-compatible controller output",
                    "This creates a local Xbox-compatible controller only when you start this transport. "
                    "Windows will request administrator approval for the controller component. "
                    "The physical controller is not modified. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self.log("INFO", "Xbox-compatible controller output remains disabled.")
                    return
                self.change("controller_output_opt_in", True)
            if intent is not None and intent != getattr(self, "_start_intent_generation", 0): return
            self._pending_controller_start = Settings(**asdict(self.settings))
            self.status.setText("PREPARING CONTROLLER")
            self.metric_state.setText("Preparing controller")
            self.diag_state.setText("Preparing controller")
            self.set_start_enabled(False)
            self.stop_button.setEnabled(True)
            self.xbox_compatible_bridge.start_session(self.settings.virtual_controller_identity)
            return
        if intent is not None and intent != getattr(self, "_start_intent_generation", 0): return
        self.stop_button.setEnabled(False)
        self.engine.start(session_settings, observe_only=observe_only)

    def synchronize_motion_compensation_for_start(self) -> None:
        """Resolve the selected text-library preset immediately before launch."""
        if self._tuning_locked:
            return  # A locked launch uses the already-selected configuration.
        if False or not self.settings.motion_compensation_enabled:
            return
        if self.settings.motion_compensation_mode not in {"preset", "combined"}:
            return
        presets, errors = load_motion_compensation(self.motion_compensation_path())
        preset = find_motion_preset(presets, self.settings.motion_compensation_preset)
        if errors:
            self.log("WARN", "Motion compensation library has warnings: " + " ".join(errors[:2]))
        if not preset:
            self.log("WARN", "Motion compensation is enabled but no saved preset is selected; preset correction is zero.")
            return
        self.settings.motion_compensation_vertical = preset.vertical
        self.settings.motion_compensation_horizontal = preset.horizontal

    def stop_capture(self, *, _profile_transition=False) -> None:
        if hasattr(self,'observation_sharing'):self.observation_sharing.change(False)
        self.cancel_model_probes()
        if self._live_owner is not None and not _profile_transition:
            self._live_owner.user_stop()
        self._start_intent_generation = getattr(self, "_start_intent_generation", 0)+1
        self._policy_start_pending = False
        self._policy_start_generation = None
        self._pending_controller_start = None
        self.xbox_compatible_bridge.stop_session()
        if self.engine.active:
            self.engine.stop()
            return
        if self.xbox_compatible_bridge.preparing:
            self.status.setText("STOPPING")
            self.metric_state.setText("Stopping Xbox bridge")
            self.diag_state.setText("Stopping Xbox bridge")
            return
        self.status.setText("READY")
        self.metric_state.setText("Stopped")
        self.diag_state.setText("Stopped")
        self.set_start_enabled(True)
        self.stop_button.setEnabled(False)

    def xbox_compatible_bridge_ready(self, session: ControllerHostSession) -> None:
        pending = self._pending_controller_start
        self._pending_controller_start = None
        if (
            pending is None
            or pending.output_transport != "virtual-xbox"
        ):
            self.xbox_compatible_bridge.stop_session()
            self.stop_capture()
            return
        if pending.virtual_controller_mode == "slot0-proxy" and session.virtual_slot != 0:
            self.xbox_compatible_bridge.stop_session()
            slot = "no XInput slot" if session.virtual_slot is None else f"Controller {session.virtual_slot + 1}"
            message = (
                f"The Xbox One-compatible bridge received {slot}, so it cannot replace the camera application's primary controller. "
                "Turn off or unplug the physical controller, start capture again so Vapor Lock receives Controller 1, then reconnect the physical controller after the bridge is ready."
            )
            self.log("WARN", message)
            if hasattr(self, "output_transport_status"):
                self.output_transport_status.setText(message)
            self.status.setText("READY")
            self.metric_state.setText("Bridge slot needs setup")
            self.diag_state.setText("Bridge slot needs setup")
            self.set_start_enabled(True)
            self.stop_button.setEnabled(False)
            return
        self.log("INFO", "Xbox One-compatible controller bridge is ready. Starting native capture.")
        self.engine.start(pending, session)

    def apply_xbox_compatible_bridge_status(self, message: str) -> None:
        self.log("INFO", message)
        if hasattr(self, "output_transport_status"):
            self.output_transport_status.setText(message)

    def xbox_compatible_bridge_completed(self, successful: bool, message: str) -> None:
        self._pending_controller_start = None
        self.log("INFO" if successful else "WARN", message)
        if not successful or not self.engine.active:
            self.status.setText("READY")
            self.metric_state.setText("Stopped")
            self.diag_state.setText("Stopped")
            self.set_start_enabled(True)
            self.stop_button.setEnabled(False)
        self.refresh_controller_bridge_controls()

    def refresh_acceleration_controls(self) -> None:
        self.refresh_cuda_preprocess_controls()
        if not hasattr(self, "acceleration_button"):
            return
        if _NATIVE_INJECTION is not None:
            self.acceleration_button.setText(self.t('Not checked'))
            self.acceleration_button.setEnabled(False)
            self.set_acceleration_detail('Native capabilities are unverified.')
            return
        requested = self.acceleration.recommended_provider() if self.settings.provider == "auto" else self.settings.provider
        if requested == "cpu":
            self.acceleration_button.setText("CPU ready")
            self.acceleration_button.setEnabled(False)
            self.set_acceleration_detail("CPU inference is ready. Select CUDA or DirectML to install that provider when the computer supports it.")
            return
        if not self.acceleration.supports(requested):
            title = self.engine.provider_label(requested)
            self.acceleration_button.setText(f"{title} unavailable")
            self.acceleration_button.setEnabled(False)
            self.set_acceleration_detail(f"{title} is not supported by this Windows installation or its current display driver.")
            return
        title = self.engine.provider_label(requested)
        installed = self.acceleration.is_installed(requested)
        self.acceleration_button.setText(f"{title} {'ready' if installed else 'install'}")
        self.acceleration_button.setEnabled(not self.acceleration.active and not installed)
        self.set_acceleration_detail(
            f"{title} acceleration is {'installed and ready' if installed else 'available for this computer'}. "
            "Provider files stay inside Vapor Lock and can be downloaded or repaired here."
        )

    def set_acceleration_detail(self, message: str) -> None:
        self._acceleration_detail_base = message
        self.render_model_selection_status()

    def render_model_selection_status(self) -> None:
        from cloud_model_ui import interpretation
        interpretation(self)
        if not hasattr(self, 'acceleration_detail'): return
        notice = self.t('Changing the model or provider stops the current session. The new selection loads when you select Start.')
        status = getattr(self, '_model_selection_status_copy', None)
        parts = [self.t(getattr(self, '_acceleration_detail_base', '')), notice]
        if status is not None: parts.append(self.tf(status[0], **status[1]))
        self.acceleration_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.acceleration_detail.setProperty('_vaporlock_i18n_skip', True)
        self.acceleration_detail.setText('\n\n'.join(part for part in parts if part))

    def show_model_selection_status(self, source, values) -> None:
        self._model_selection_status_copy = (source, dict(values))
        self.render_model_selection_status()
        # Keep feedback beside the selection without moving the user's view.

    def provider_changed(self, index: int) -> None:
        self.change("provider", str(self.provider_choice.itemData(index)))
        self.refresh_acceleration_controls()

    @tuning_guard
    def apply_controller_thresholds(self, press, release):
        validate_thresholds(press, release)
        # Validate the whole pair before either edit. No await/event pumping:
        # order keeps every intermediate state valid for Undo and observers.
        order = [('controller_trigger_press', press), ('controller_trigger_release', release)]
        if press < self.settings.controller_trigger_release:
            order.reverse()
        for field, value in order:
            self.change(field, value)

    def refresh_target_confidence_summary(self):
        if not hasattr(self, 'target_confidence_summary'): return
        from batch3_settings import confidence_floors
        acquire, retain = confidence_floors(self.settings)
        self.target_confidence_summary.setText(self.tf('Effective confidence: acquire {acquire}; retain {retain}.', acquire=f'{acquire:.6g}', retain=f'{retain:.6g}'))

    def refresh_controller_binding_status(self) -> None:
        if not hasattr(self, "controller_binding_status"):
            return
        activation_uses_controller = self.settings.activation_source in {"controller", "either"}
        lock_uses_controller = (
            self.settings.manual_target_lock_enabled
            and self.settings.manual_lock_source in {"controller", "either"}
        )
        if activation_uses_controller and lock_uses_controller and self.settings.controller_binding == self.settings.manual_lock_controller_binding:
            self.controller_binding_status.setText(
                "Binding conflict: controller activation and manual target lock use the same input. "
                "Choose separate inputs so one press cannot both authorize output and pin or release a target."
            )
        elif activation_uses_controller or lock_uses_controller:
            self.controller_binding_status.setText(
                "Controller bindings are distinct. Vapor Lock listens only for the configured button or trigger inputs; analog sticks are intentionally ignored."
            )
        else:
            self.controller_binding_status.setText(
                "No controller binding is active for this profile. Analog movement is separate from the chosen activation binding."
            )

    def refresh_controller_bridge_controls(self) -> None:
        if hasattr(self, 'controller_deadzone_control'):
            self.controller_deadzone_control.setEnabled(self.settings.output_transport == 'virtual-xbox')
        if hasattr(self, 'controller_deadzone_status'):
            self.controller_deadzone_status.setText(self.t('0% is off. Raises nonzero virtual-stick output; neutral stays neutral. Start low. Physical input and trigger thresholds are separate.' if self.settings.output_transport == 'virtual-xbox' else 'Inactive for this transport. Saved value applies only to virtual-controller output.'))
        if not hasattr(self, "output_transport_status"):
            return
        if self.settings.output_transport == "virtual-mouse":
            self.output_transport_status.setText(
                "Virtual mouse transport is selected. Install the bundled component with administrator approval, restart Windows, then start capture."
            )
            if hasattr(self, "virtual_mouse_install_button"):
                self.virtual_mouse_install_button.setEnabled(True)
            if hasattr(self, "controller_install_button"):
                self.controller_install_button.setEnabled(True)
            if hasattr(self, "controller_uninstall_button"):
                self.controller_uninstall_button.setEnabled(True)
            return
        if self.settings.output_transport != "virtual-xbox":
            self.output_transport_status.setText("The selected transport is configured in this page and the Hardware tab. Device connection is verified when capture starts.")
            if hasattr(self, "controller_install_button"):
                self.controller_install_button.setEnabled(True)
            if hasattr(self, "controller_uninstall_button"):
                self.controller_uninstall_button.setEnabled(True)
            return
        if not self.settings.controller_output_opt_in:
            self.output_transport_status.setText(CONTROLLER_OUTPUT_CONSENT)
        elif self.xbox_compatible_bridge.preparing:
            self.output_transport_status.setText("The Xbox-compatible controller component is preparing. Windows may be waiting for approval.")
        elif self.settings.virtual_controller_mode == "slot0-proxy":
            self.output_transport_status.setText("Primary controller bridge is enabled. Start capture before the physical controller is connected when Controller 1 must be reserved for the bridge.")
        else:
            self.output_transport_status.setText("Separate Xbox-compatible controller output is enabled. Select the created controller in the destination application.")
        if hasattr(self, "controller_install_button"):
            self.controller_install_button.setEnabled(not self.xbox_compatible_bridge.preparing)
        if hasattr(self, "controller_uninstall_button"):
            self.controller_uninstall_button.setEnabled(not self.xbox_compatible_bridge.preparing)

    @disposable_deny('device-install')
    def install_virtual_mouse_component(self, *, native_guard=lambda: True) -> None:
        from virtual_mouse_component import launch_installation
        def permitted():
            return (not self._native_closing and not self.engine.active and not self._policy_start_pending
                    and self._pending_controller_start is None and not self.xbox_compatible_bridge.preparing
                    and not self._profile_transition_pending and not False
                    and not getattr(self, '_manual_services', False) and native_guard() is True)
        if not permitted():
            self.log("WARN", "Stop the active or pending session before installing the virtual mouse component.")
            return
        installer = ROOT / "hardware" / "interception" / "install-interception.exe"
        if not installer.is_file():
            QMessageBox.warning(self, "Virtual mouse", "The bundled virtual mouse installer is missing. Rebuild or repair this Vapor Lock copy.")
            return
        answer = QMessageBox.question(
            self,
            "Install virtual mouse component",
            "Windows will request administrator approval to install the local virtual mouse driver. Restart Windows before using the transport. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.log("INFO", "Virtual mouse component installation was cancelled.")
            return
        try:
            result = launch_installation(installer, guard=permitted)
        except (AttributeError, OSError, ValueError) as error:
            self.log("WARN", f"Windows could not start the virtual mouse installer: {error}")
            return
        if result <= 32:
            self.log("WARN", "Windows did not start the virtual mouse installer. Administrator approval may have been declined.")
            return
        self.log("INFO", "Virtual mouse installer started. Restart Windows after it completes, then return to this transport.")
        self.output_transport_status.setText("Virtual mouse component installer started. Restart Windows after it completes before starting capture.")

    @disposable_deny('device-install')
    def remove_virtual_mouse_component(self, *, native_guard=lambda: True):
        from virtual_mouse_component import launch_removal
        def busy():
            return (self._native_closing or self.engine.active or self._policy_start_pending or self._pending_controller_start is not None
                    or self.xbox_compatible_bridge.preparing or self._profile_transition_pending)
        def feedback(text, level="INFO"):
            self.virtual_mouse_removal_status.setText(self.t(text))
            self.log(level, self.t(text))
        if busy():
            feedback("Stop the active or pending session before removing the virtual mouse component.", "WARN")
            return 'BUSY'
        if False or getattr(self, '_manual_services', False) or native_guard() is not True:
            return 'STALE'
        dialog = QMessageBox(self)
        dialog.setWindowTitle(self.t("Remove virtual mouse component"))
        dialog.setText(self.t("Remove the shared Interception mouse and keyboard driver? Other applications using this driver will be affected. Windows will request administrator approval. Restart Windows after the removal tool completes."))
        remove = dialog.addButton(self.t("Remove"), QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        if dialog.clickedButton() is not remove:
            feedback("Virtual mouse component removal was cancelled.")
            return 'CANCELLED'
        if busy():
            feedback("Stop the active or pending session before removing the virtual mouse component.", "WARN")
            return 'BUSY'
        if False or getattr(self, '_manual_services', False) or native_guard() is not True:
            feedback("The native state changed. Try again", "WARN")
            return 'STALE'
        try:
            result = launch_removal(ROOT / "hardware" / "interception" / "install-interception.exe",
                guard=lambda: not busy() and not False
                    and not getattr(self, '_manual_services', False) and native_guard() is True)
        except (AttributeError, OSError, ValueError):
            feedback("The virtual mouse removal tool could not be started. Check the installed files and administrator approval.", "WARN")
            return 'MOUSE_REMOVAL_FAILED'
        if result <= 32:
            feedback("The virtual mouse removal tool could not be started. Check the installed files and administrator approval.", "WARN")
            return 'MOUSE_REMOVAL_FAILED'
        self.change("virtual_mouse_opt_in", False)
        checkbox = getattr(self, "virtual_mouse_opt_in_checkbox", None)
        if checkbox is not None:
            previous = checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(previous)
        if self.settings.output_transport == "virtual-mouse":
            self.change("output_transport", "mouse")
            if hasattr(self, "output_transport_combo"):
                control = self.output_transport_combo
                previous = control.blockSignals(True)
                control.setCurrentIndex(control.findData("mouse"))
                control.blockSignals(previous)
            self.output_transport_status.setText(self.t("Windows mouse input is selected. Profile changes are unsaved."))
        feedback("Virtual mouse removal started. Wait for the tool to finish, then restart Windows. Virtual mouse transport is disabled. If it was selected, Windows mouse input is now selected. Save any profile changes.")
        return 'MOUSE_REMOVAL_STARTED'

    @disposable_deny('child-device')
    def open_controller_test(self) -> None:
        """Open Windows' built-in controller panel without adding a dependency."""
        try:
            subprocess.Popen(
                ["control.exe", "joy.cpl"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            self.log("WARN", f"Windows could not open the controller test panel: {error}")

    @disposable_deny('device-install')
    def install_controller_bridge(self) -> None:
        if not self._confirm_controller_component(remove=False):
            self.log("INFO", "Controller component installation was cancelled.")
            return
        self.xbox_compatible_bridge.install(self.settings.virtual_controller_identity, machine_trust_consent=True)

    def _confirm_controller_component(self, *, remove: bool) -> bool:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        if remove:
            title = "Remove VaporLock-managed controller support?"
            body = "Removes only positively identified VaporLock-managed devices, driver packages and certificate trust added by setup. Preexisting, foreign and uncertain components are preserved. Windows will ask for administrator approval. A restart may be required."
            action = "Remove managed support"
        else:
            title = "Install or repair controller support?"
            body = "Installs or repairs VaporLock-managed controller components. Setup also adds a local controller signing certificate to the Windows machine-wide Root and TrustedPublisher certificate stores. This trust is not limited to these controller packages. Windows will ask for administrator approval."
            action = "Install / repair"
        dialog.setWindowTitle(self.t(title))
        dialog.setText(self.t(body))
        dialog.setDetailedText(self.t("Root means Trusted Root Certification Authorities; TrustedPublisher means Trusted Publishers. Setup preserves Windows signing policies and blocks unknown or conflicting controller components. Removal uses recorded ownership and does not sweep other applications' devices. Incomplete operations remain pending and may require a restart."))
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        proceed = dialog.addButton(self.t(action), QMessageBox.ButtonRole.AcceptRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        try:
            dialog.exec()
            return dialog.clickedButton() is proceed
        finally:
            dialog.deleteLater()

    @disposable_deny('device-install')
    def uninstall_controller_bridge(self) -> None:
        if self.engine.active or self.xbox_compatible_bridge.preparing:
            self.log("WARN", "Stop the active controller session before removing its local component.")
            return
        if not self._confirm_controller_component(remove=True):
            self.log("INFO", "Controller component removal was cancelled.")
            return
        self.xbox_compatible_bridge.uninstall()

    def choose_kmbox_a_library(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose official KMBox A DLL", str(ROOT), "Dynamic libraries (*.dll)")
        if path:
            self.change("kmbox_a_library_path", path)
            if hasattr(self, "kmbox_a_library"):
                self.kmbox_a_library.setText(path)

    def apply_controller_bridge_status(self, message: str) -> None:
        self.log("INFO", message)
        if hasattr(self, "output_transport_status"):
            self.output_transport_status.setText(message)

    def controller_bridge_completed(self, successful: bool, message: str) -> None:
        level = "INFO" if successful else "WARN"
        self.log(level, message)
        self.refresh_controller_bridge_controls()

    @disposable_deny('runtime-install')
    def install_recommended_acceleration(self) -> None:
        provider = self.acceleration.recommended_provider() if self.settings.provider == "auto" else self.settings.provider
        if provider == "cpu":
            return
        if self.acceleration.is_installed(provider):
            self.refresh_acceleration_controls()
            return
        self.acceleration_button.setEnabled(False)
        self.set_acceleration_detail(f"Installing {self.engine.provider_label(provider)} acceleration. This can take several minutes for CUDA.")
        self.acceleration.install(provider)

    def apply_acceleration_status(self, message: str) -> None:
        self.log("INFO", message)
        if hasattr(self, "acceleration_detail"):
            self.set_acceleration_detail(message)

    def acceleration_completed(self, provider: str, successful: bool, message: str) -> None:
        if provider in {"cuda", "trt"}:
            self.engine_capabilities.stop()
        title = self.engine.provider_label(provider)
        if successful:
            self.log("INFO", f"{title} acceleration is ready.")
            self.set_acceleration_detail(f"{title} acceleration installed successfully. It will be used on the next capture session.")
        else:
            self.log("WARN", f"{title} acceleration installation failed: {message}")
            self.set_acceleration_detail(f"{title} installation failed: {message}")
        self.refresh_acceleration_controls()

    def show_page(self, key: str) -> None:
        # Capture now lives beside Policy inside Targeting. Keep the former
        # route working for integrations and saved actions without maintaining
        # a second copy of the controls.
        requested_inspector = 0 if key == "capture" else None
        if requested_inspector is not None:
            key = "selection"
        self.current_page = key
        panel_name = {'games': 'game_cards', 'profiles': 'profile_cards'}.get(key)
        if panel_name and hasattr(self, panel_name):
            getattr(self, panel_name).refresh()
        self.stack.setCurrentWidget(self.pages[key])
        if hasattr(self,'_shared_preview_shell'):
            if key=='overview':self._overview_preview_slot.addWidget(self._shared_preview_shell)
            else:self._target_preview_layout.insertWidget(1,self._shared_preview_shell)
            self._shared_preview_shell.show()
        for name, button in self.nav.items():
            button.setChecked(name == key)
        if hasattr(self, "header_page"):
            self.header_page.setText(self.t(PAGE_TITLES.get(key, "Control Center")))
        self.update_targeting_layout()
        if requested_inspector is not None:
            self.set_targeting_inspector_tab(requested_inspector)

    def settings_search_targets(self) -> list[tuple[str, str, QLabel]]:
        """Index visible setting labels without maintaining a second schema."""
        targets: list[tuple[str, str, QLabel]] = []
        searchable_objects = {"fieldTitle", "policyControlLabel", "policyCardTitle", "pageTitle"}
        for page_key, page in self.pages.items():
            page_title = self.t(PAGE_TITLES.get(page_key, page_key.title()))
            seen: set[str] = set()
            for label in page.findChildren(QLabel):
                text = label.text().strip()
                if label.objectName() not in searchable_objects or not text:
                    continue
                identity = text.casefold()
                if identity in seen:
                    continue
                seen.add(identity)
                targets.append((page_title, text, label))
        return sorted(targets, key=lambda item: (item[0].casefold(), item[1].casefold()))

    def reveal_setting(self, page_key: str, target: QWidget) -> None:
        page = self.pages.get(page_key)
        if page is None:
            return
        self.show_page(page_key)
        from mission_layout import TaskDetails
        ancestor=target.parentWidget()
        while ancestor is not None and ancestor is not page:
            if isinstance(ancestor,TaskDetails):ancestor.reveal()
            ancestor=ancestor.parentWidget()
        if page is self.pages.get("selection"):
            for index in range(self.targeting_inspector_stack.count()):
                tab_page = self.targeting_inspector_stack.widget(index)
                if tab_page is target or tab_page.isAncestorOf(target):
                    self.set_targeting_inspector_tab(index)
                    break
        for tabs in page.findChildren(SecondarySettingsTabs):
            for index in range(tabs._stack.count()):
                tab_page = tabs._stack.widget(index)
                if tab_page is target or tab_page.isAncestorOf(target):
                    tabs.setCurrentIndex(index)
                    break
        scrolls = [
            scroll for scroll in page.findChildren(QScrollArea)
            if scroll.widget() is not None and (scroll.widget() is target or scroll.widget().isAncestorOf(target))
        ]
        if isinstance(page, QScrollArea):
            scrolls.append(page)
        QTimer.singleShot(0, lambda: [scroll.ensureWidgetVisible(target, 24, 24) for scroll in scrolls])

    def open_settings_search(self) -> None:
        targets = self.settings_search_targets()
        dialog = QDialog(self)
        dialog.setWindowTitle(self.t("Search settings"))
        dialog.setModal(True)
        dialog.resize(560, 430)
        layout = QVBoxLayout(dialog)
        query = QLineEdit()
        query.setProperty('tuningLockExempt', True)
        query.setPlaceholderText(self.t("Search settings"))
        results = QListWidget()
        layout.addWidget(query)
        layout.addWidget(results, 1)

        def populate(text: str = "") -> None:
            terms = [term for term in text.casefold().split() if term]
            results.clear()
            for target_index, (page_title, label, _target) in enumerate(targets):
                haystack = f"{page_title} {label}".casefold()
                if terms and not all(term in haystack for term in terms):
                    continue
                item = QListWidgetItem(f"{label}    {page_title}")
                item.setData(Qt.ItemDataRole.UserRole, target_index)
                results.addItem(item)
            if results.count():
                results.setCurrentRow(0)

        def activate(item: QListWidgetItem | None = None) -> None:
            item = item or results.currentItem()
            if item is None:
                return
            target_index = int(item.data(Qt.ItemDataRole.UserRole))
            page_title, _label, target = targets[target_index]
            page_key = next((key for key, title in PAGE_TITLES.items() if self.t(title) == page_title), "")
            if not page_key:
                page_key = next(
                    (key for key, page in self.pages.items() if page is target or page.isAncestorOf(target)),
                    "",
                )
            dialog.accept()
            if page_key:
                self.reveal_setting(page_key, target)

        query.textChanged.connect(populate)
        query.returnPressed.connect(lambda: activate())
        results.itemActivated.connect(activate)
        populate()
        query.setFocus()
        dialog.exec()

    @staticmethod
    def parse_global_hotkey(sequence: str) -> tuple[int, int] | None:
        tokens = [token.strip().upper() for token in sequence.split("+") if token.strip()]
        if not tokens:
            return None
        modifiers = 0
        modifier_values = {"ALT": 0x0001, "CTRL": 0x0002, "CONTROL": 0x0002, "SHIFT": 0x0004, "WIN": 0x0008, "META": 0x0008}
        key_tokens = []
        for token in tokens:
            if token in modifier_values:
                modifiers |= modifier_values[token]
            else:
                key_tokens.append(token)
        if len(key_tokens) != 1:
            return None
        key = key_tokens[0]
        named_keys = {
            "SPACE": 0x20, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B,
            "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
            "PAGEUP": 0x21, "PAGEDOWN": 0x22, "UP": 0x26, "DOWN": 0x28,
            "LEFT": 0x25, "RIGHT": 0x27,
        }
        if len(key) == 1 and (key.isalpha() or key.isdigit()):
            virtual_key = ord(key)
        elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
            virtual_key = 0x70 + int(key[1:]) - 1
        else:
            virtual_key = named_keys.get(key)
        if virtual_key is None:
            return None
        return modifiers | 0x4000, virtual_key

    def unregister_global_hotkeys(self) -> None:
        if sys.platform != "win32" or not self._registered_hotkey_ids:
            return
        try:
            window_id = int(self.winId())
            for hotkey_id in self._registered_hotkey_ids:
                ctypes.windll.user32.UnregisterHotKey(window_id, hotkey_id)
        except (AttributeError, OSError):
            pass
        self._registered_hotkey_ids.clear()

    @disposable_deny('global-hotkeys')
    def register_global_hotkeys(self) -> None:
        self.unregister_global_hotkeys()
        if sys.platform != "win32":
            if hasattr(self, "hotkey_status"):
                self.hotkey_status.setText("Global shortcuts require Windows.")
            return
        bindings = [(10, "Setup and calibration", self.settings.hotkey_calibration)]
        bindings.extend(((1, 'Start/stop capture', self.settings.hotkey_start_stop), (2, 'Desktop overlay', self.settings.hotkey_toggle_overlay), (3, 'Emergency stop', self.settings.hotkey_emergency_stop), (4, 'Next profile', self.settings.hotkey_next_profile), (5, 'Pin or release target', self.settings.hotkey_toggle_target_lock), (6, 'Annotated snapshot', self.settings.hotkey_save_screenshot), (7, 'Profile slot 1', self.settings.hotkey_profile_1), (8, 'Profile slot 2', self.settings.hotkey_profile_2), (9, 'Profile slot 3', self.settings.hotkey_profile_3)))
        failures: list[str] = []
        active: list[str] = []
        used: set[tuple[int, int]] = set()
        try:
            window_id = int(self.winId())
            for hotkey_id, title, sequence in bindings:
                parsed = self.parse_global_hotkey(sequence)
                if parsed is None:
                    if sequence:
                        failures.append(f"{title}: unsupported shortcut")
                    continue
                if parsed in used:
                    failures.append(f"{title}: duplicates another Vapor Lock shortcut")
                    continue
                used.add(parsed)
                modifiers, virtual_key = parsed
                if ctypes.windll.user32.RegisterHotKey(window_id, hotkey_id, modifiers, virtual_key):
                    self._registered_hotkey_ids.add(hotkey_id)
                    active.append(f"{title}: {sequence}")
                else:
                    failures.append(f"{title}: already used by Windows or another application")
        except (AttributeError, OSError):
            failures.append("Windows could not register global shortcuts")
        status = "Global shortcuts active: " + "; ".join(active) if active else "No global shortcuts are active."
        if failures:
            status += "  " + " | ".join(failures)
        if hasattr(self, "hotkey_status"):
            self.hotkey_status.setText(status)

    def restore_default_hotkeys(self) -> None:
        self.change("hotkey_calibration", "Ctrl+Alt+C")
        self.change("hotkey_start_stop", "Ctrl+Alt+S")
        self.change("hotkey_toggle_overlay", "Ctrl+Alt+O")
        self.change("hotkey_emergency_stop", "Ctrl+Alt+X")
        self.change("hotkey_next_profile", "Ctrl+Alt+P")
        self.change("hotkey_toggle_target_lock", "Ctrl+Alt+L")
        self.change("hotkey_save_screenshot", "Ctrl+Alt+F12")
        for slot in range(1, 4):
            self.change(f"hotkey_profile_{slot}", "")
            self.change(f"hotkey_profile_{slot}_target", "")
        self.rebuild_pages()

    def hotkey_toggle_capture(self) -> None:
        if self.acceleration.active:
            self.log("INFO", "Global start/stop ignored while acceleration installation is active.")
        elif self.engine.active or self.xbox_compatible_bridge.preparing:
            self.log("INFO", "Global shortcut stopped the active capture session.")
            self.stop_capture()
        else:
            self.log("INFO", "Global shortcut starting capture.")
            self.start_capture()

    def hotkey_toggle_overlay(self) -> None:
        if not self.settings.desktop_overlay_enabled:
            self.change("desktop_overlay_enabled", True)
            self.change("desktop_overlay_visible", True)
            self.log("INFO", "Desktop overlay enabled. Its native metadata stream is active for this session.")
            return
        self.change("desktop_overlay_visible", not self.settings.desktop_overlay_visible)
        self.log("INFO", f"Desktop overlay {'shown' if self.settings.desktop_overlay_visible else 'hidden'} by global shortcut.")

    def hotkey_emergency_stop(self) -> None:
        if self.engine.active or self.xbox_compatible_bridge.preparing:
            self.log("WARN", "Global emergency stop requested. Native capture and output are stopping.")
            self.stop_capture()
        else:
            self.log("INFO", "Global emergency stop received while the engine was already idle.")

    def local_profile_files(self) -> list[Path]:
        try:
            return sorted((path for path in PROFILE_DIRECTORY.glob("*.json") if path.is_file()), key=lambda path: path.name.casefold())
        except OSError:
            return []

    def load_preset_profile(self) -> bool:
        """Apply the shipped preset when this installation provides one."""
        preset = PROFILE_DIRECTORY / "default.json"
        return preset.is_file() and self.load_profile_path(preset)

    def profile_selection_file(self) -> Path:
        installation = hashlib.sha256(str(ROOT.resolve()).casefold().encode('utf-8')).hexdigest()[:24]
        return writable_native_path(INITIALIZATION_DIRECTORY / f'active-profile-{EDITION}-{installation}.json')

    def remember_active_profile(self) -> None:
        """Native-only installation preference; never a renderer-supplied path."""
        temporary = None
        try:
            path = self.profile_selection_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            selected = str(self._active_profile_path.resolve()) if self._active_profile_path is not None else None
            payload = {'schema_version': 1, 'product_root': str(ROOT.resolve()), 'profile_path': selected}
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False, suffix='.tmp') as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            writable_native_path(path)
            temporary.replace(path)
        except (OSError, ValueError):
            self.log('WARN', self.t('Profile was applied, but its startup selection could not be remembered.'))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load_startup_profile(self) -> bool:
        selection_present = False
        try:
            path = self.profile_selection_file()
            selection_present = path.exists()
            if path.stat().st_size > 65536:
                raise ValueError('PROFILE_SELECTION_TOO_LARGE')
            payload = json.loads(path.read_text(encoding='utf-8'))
            selected = payload.get('profile_path')
            if (payload.get('schema_version') == 1 and payload.get('product_root') == str(ROOT.resolve())
                    and selected is None):
                return self.load_preset_profile()
            if (payload.get('schema_version') == 1 and payload.get('product_root') == str(ROOT.resolve())
                    and type(selected) is str and len(selected) <= 32700):
                candidate = writable_native_path(Path(selected))
                if candidate.is_absolute() and candidate.suffix.lower() == '.json' and candidate.is_file():
                    if self.load_profile_path(candidate):
                        return True
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        restored = self.load_preset_profile()
        if selection_present:
            self.log('WARN', self.t('The previous profile could not be restored. The default profile is active.'
                if restored else 'The previous profile could not be restored. Default settings are active.'))
        return restored

    @tuning_guard
    def hotkey_next_profile(self) -> None:
        profiles = self.local_profile_files()
        if not profiles:
            self.log("INFO", "No local profiles are available to cycle.")
            return
        current_index = -1
        if self._active_profile_path is not None:
            current = self._active_profile_path.resolve()
            current_index = next((index for index, path in enumerate(profiles) if path.resolve() == current), -1)
        if current_index < 0:
            current_index = next((index for index, path in enumerate(profiles) if path.stem.casefold() == self.settings.profile_name.casefold()), -1)
        next_profile = profiles[(current_index + 1) % len(profiles)]
        if self.load_profile_path(next_profile):
            self.log("INFO", f"Global shortcut loaded profile: {next_profile.name}")

    @tuning_guard
    def hotkey_load_profile(self, slot: int) -> None:
        target = str(getattr(self.settings, f"hotkey_profile_{slot}_target", "")).strip()
        if not target:
            self.log("INFO", f"Profile slot {slot} is not assigned.")
            return
        candidate = (PROFILE_DIRECTORY / target).resolve()
        try:
            candidate.relative_to(PROFILE_DIRECTORY.resolve())
        except ValueError:
            self.log("WARN", f"Profile slot {slot} has an invalid profile target.")
            return
        if not candidate.is_file():
            self.log("WARN", f"Profile slot {slot} points to a missing profile: {target}")
            return
        if self.load_profile_path(candidate):
            self.log("INFO", f"Profile slot {slot} loaded profile: {candidate.name}")

    def hotkey_toggle_target_lock(self) -> None:
        if not self.settings.manual_target_lock_enabled:
            self.log("INFO", "Enable Manual target lock in Target Policy before requesting a pin or release.")
            return
        if not self.engine.active:
            self.log("INFO", "Start capture and acquire a target before requesting a pin or release.")
            return
        if not self._last_selected_track:
            self.log("INFO", "No current target is available to pin. The request was not sent.")
            return
        if self.engine.trigger_manual_lock():
            self.log("INFO", "Manual target pin or release requested from the native engine.")
        else:
            self.log("WARN", "The current engine session cannot accept a manual target-lock request. Restart capture after enabling manual target lock.")

    def save_annotated_snapshot(self) -> None:
        preview = getattr(self, "policy_preview_view", None)
        if preview is None:
            preview = getattr(self, "preview_view", None)
        if preview is None:
            self.log("INFO", "Open Targeting and wait for an annotated native preview before saving a snapshot.")
            return
        snapshot_directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Vapor Lock" / "screenshots"
        filename = f"vapor-lock-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}.png"
        destination = snapshot_directory / filename
        try:
            snapshot_directory.mkdir(parents=True, exist_ok=True)
            if preview.save_snapshot(destination):
                self.log("INFO", f"Saved annotated preview snapshot: {destination}")
            else:
                self.log("INFO", "No annotated preview is available yet. Start capture and wait for a frame before saving a snapshot.")
        except OSError as error:
            self.log("WARN", f"Could not save the annotated preview snapshot: {error}")

    def hotkey_save_screenshot(self) -> None:
        self.save_annotated_snapshot()

    def nativeEvent(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            try:
                native_message = wintypes.MSG.from_address(int(message))
                if native_message.message == 0x0312:  # WM_HOTKEY
                    action = self._hotkey_actions.get(int(native_message.wParam))
                    if action:
                        QTimer.singleShot(0, action)
                        return True, 0
            except (AttributeError, TypeError, ValueError):
                pass
        return super().nativeEvent(event_type, message)

    def browse_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose model", str(ROOT / "models"), "Supported models (*.onnx *.cloud);;ONNX (*.onnx);;VaporLock (*.cloud)")
        if path:
            self.model_path.setText(path)
            self.change("model_path", path)

    def discovered_local_models(self) -> list[Path]:
        models_root = ROOT / "models"
        models_root.mkdir(exist_ok=True)
        models, self._canonical_model_aliases = discover_local_models(models_root)
        return models

    def populate_model_library(self) -> None:
        models_root = ROOT / "models"
        models = self.discovered_local_models()
        self.model_library.blockSignals(True)
        self.model_library.clear()
        self.model_library.addItem("Select a local model (.onnx / .cloud)", "")
        for model in models:
            self.model_library.addItem(str(model.relative_to(models_root)), str(model))
        selected = self.model_library.findData(self._canonical_model_aliases.get(self.settings.model_path, self.settings.model_path))
        self.model_library.setCurrentIndex(max(0, selected))
        self.model_library.blockSignals(False)
        self.sync_model_selection_views()
        if hasattr(self, "annotation_model_library"):
            self.populate_annotation_model_library(models)

    def populate_annotation_model_library(self, models: list[Path] | None = None) -> None:
        if not hasattr(self, "annotation_model_library"):
            return
        models_root = ROOT / "models"
        models = self.discovered_local_models() if models is None else models
        self.annotation_model_library.blockSignals(True)
        self.annotation_model_library.clear()
        self.annotation_model_library.addItem("Use current detection model", "")
        for model in (p for p in models if p.suffix.lower()==".onnx"):
            self.annotation_model_library.addItem(str(model.relative_to(models_root)), str(model))
        selected = self.annotation_model_library.findData(self._canonical_model_aliases.get(self.settings.dataset_annotation_model, self.settings.dataset_annotation_model))
        self.annotation_model_library.setCurrentIndex(max(0, selected))
        self.annotation_model_library.blockSignals(False)

    def sync_model_selection_views(self, _index=None) -> None:
        """Synchronize presentation after signal-blocked model refresh/commit."""
        index=self.model_library.currentIndex()
        view=getattr(self,'model_library_list',None)
        if view is not None:
            selection=view.selectionModel();blocked=selection.blockSignals(True)
            try:view.setCurrentIndex(self.model_library.model().index(index,0))
            finally:selection.blockSignals(blocked)
        header=getattr(self,'model_quick_switch',None)
        if header is not None and header.model() is self.model_library.model():
            blocked=header.blockSignals(True)
            try:header.setCurrentIndex(index)
            finally:header.blockSignals(blocked)

    def select_discovered_model(self, index: int) -> None:
        path = str(self.model_library.itemData(index) or "")
        if path:
            self.model_path.setText(path)
            self.change("model_path", path)

    def _select_header_model(self,index: int) -> None:
        if hasattr(self,'model_library') and not self.model_library.signalsBlocked():
            self.model_library.setCurrentIndex(index)

    def select_annotation_model(self, index: int) -> None:
        path = str(self.annotation_model_library.itemData(index) or "")
        self.change("dataset_annotation_model", path)

    def refresh_model_library(self) -> None:
        self._api_catalog_cache = None
        self.populate_model_library()
        self.log("INFO", "Refreshed local ONNX model library")

    @disposable_deny('external-open')
    def open_model_folder(self) -> None:
        models_root = ROOT / "models"
        models_root.mkdir(exist_ok=True)
        os.startfile(models_root)  # type: ignore[attr-defined]


    @tuning_guard
    def stage_model_candidate(self, field, value):
        if self._profile_transition_pending or self._native_closing: return
        if self._model_selection is not None and self._model_selection.busy:
            self.stop_capture()
        if getattr(self.settings, field) == value: return
        from model_selection import ModelSelection
        if self._model_selection is None: self._model_selection = ModelSelection(self)
        if not self._model_selection.switch(field, value):
            self.log('INFO', self.t('Configuration could not be applied.'))
        if hasattr(self, 'model_path'): self.model_path.setText(self.settings.model_path)
        if hasattr(self, 'provider_choice'):
            self.provider_choice.blockSignals(True)
            self.provider_choice.setCurrentIndex(max(0, self.provider_choice.findData(self.settings.provider)))
            self.provider_choice.blockSignals(False)

    @tuning_guard
    def commit_model_selection(self, fields):
        allowed = {'model_path', 'provider'}
        if (not set(fields) <= allowed or self.engine.active or self._policy_start_pending
                or self._pending_controller_start is not None or self.xbox_compatible_bridge.preparing
                or not self.xbox_compatible_bridge.safely_stopped()):
            raise ValueError('Model configuration cannot be committed now')
        replacement = deepcopy(self.settings)
        for field,value in fields.items(): setattr(replacement,field,value)
        validated_native_settings(asdict(replacement), tuple((name, str(spec.type))
            for name,spec in Settings.__dataclass_fields__.items()))
        self.cancel_model_probes()
        self.settings = replacement
        self.clear_tuning_undo()
        self._local_effect_preview = self._ultralytics_preview = None
        self.dirty = True
        self._profile_change_suffix = 'unsaved changes'
        self.update_profile_summary()
        self.clear_preview()
        self._benchmark_results = []
        for name in ('model_validation', 'model_benchmark'):
            if hasattr(self, name):
                getattr(self, name).setText(self.t('Selection updated. Select Start to load it.'))
        # Update in place so a model change preserves focus, page and scroll.
        self.model_path.setText(self.settings.model_path)
        for combo, value in ((self.provider_choice, self.settings.provider),
                             (self.model_library, self._canonical_model_aliases.get(
                                 self.settings.model_path, self.settings.model_path))):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)
        self.sync_model_selection_views()
        self.refresh_acceleration_controls()
        self.refresh_readiness()
        self.refresh_local_api_snapshot()
        if hasattr(self, 'inference_gpu_controls'): self.inference_gpu_controls.populate(False)
        if self._live_owner is not None: self._live_owner.native_changed()

    def pipeline_display_metrics(self):
        # Publication can read twice in one Qt turn. Never advance its stamp
        # from a clock read; only a telemetry/timer event replaces this snapshot.
        return dict(getattr(self, '_pipeline_snapshot', dict.fromkeys(
            ('capture', 'inference', 'writes', 'age', 'inference_ms'))))

    def _update_pipeline_snapshot(self):
        from pipeline_tools import pipeline_metrics
        from engine_stream import native_tick_ms
        self._pipeline_snapshot = pipeline_metrics(self._last_output_data, active=self.engine.session_running,
            elapsed=time.monotonic()-self._pipeline_received_at, native_now=native_tick_ms())
        if hasattr(self,'diag_freshness'):
            age=self._pipeline_snapshot.get('age')
            self.diag_freshness.setText('—' if age is None else f'{age:.1f} ms')

    def refresh_pipeline_diagnostics(self):
        if hasattr(self, 'policy_preview_status'):
            self.refresh_preview_status()
        previous = self.pipeline_display_metrics()
        self._update_pipeline_snapshot()
        if hasattr(self, 'pipeline_diagnostics'):
            text = self.pipeline_diagnostic_text()
            if self.pipeline_diagnostics.text() != text:
                self.pipeline_diagnostics.setText(text)
        if (previous != self.pipeline_display_metrics()
                and getattr(self, '_live_owner', None) is not None and not self._native_closing):
            self._live_owner.native_changed()

    def pipeline_diagnostic_text(self):
        values = self.pipeline_display_metrics()
        def number(key, unit):
            return self.t('Unavailable') if values[key] is None else f'{values[key]:.1f} {unit}'
        return self.tf('Fresh frames: {capture} · Completed inferences: {inference}\nSelected observation age: {age} · Accepted output writes: {writes}\nAccepted writes are software reports, not measured physical delivery.',
            capture=number('capture','/s'), inference=number('inference','/s'),
            age=number('age','ms'), writes=number('writes','/s'))

    def model_probe_busy(self) -> bool:
        if self.cloud_background_busy():return True
        return (bool(getattr(self, '_model_probes', [])) or self._benchmark_active or self._validation_active
                or bool(getattr(self, '_model_selection', None) and self._model_selection.busy))

    def cloud_background_busy(self) -> bool:
        return any(job is not None and not job.done for job in
            (getattr(self,'_cloud_training_job',None),getattr(self,'_cloud_model_check_job',None),
             getattr(self,'_cloud_annotation_job',None)))

    def reject_cloud_busy_start(self) -> None:
        self._policy_start_pending=False
        self._start_intent_generation=getattr(self,'_start_intent_generation',0)+1
        self.set_start_enabled(not self.engine.active)
        self.log('INFO', self.t('Another background task is active.'))

    def cancel_model_probes(self) -> None:
        job=getattr(self,'_cloud_model_check_job',None)
        if job is not None and not job.done:job.stop()
        if getattr(self, '_model_selection', None) is not None: self._model_selection.cancel()
        self._probe_revision += 1
        self._benchmark_active = self._validation_active = False
        self._benchmark_queue = []
        for probe in tuple(self._model_probes):
            probe.cancel()
        for name in ('model_benchmark_button', 'model_validation_button'):
            button = getattr(self, name, None)
            if button is not None: button.setEnabled(True)

    def _probe_model(self, settings, provider, completed, *, inspection=False):
        from model_probe import ModelProbe, inert_settings
        executable, environment = self.engine.launch_for(provider)
        if executable is None:
            from model_probe import ProbeResult
            completed(ProbeResult(False, 'Native engine is unavailable'))
            return
        revision = self._probe_revision
        safe = inert_settings(settings)
        safe.model_path = str(resolve_product_path(settings.model_path))
        arguments = EngineBridge.arguments_for(safe, provider, observe_only=True)
        if inspection: arguments.append('--inspect-model')
        def finished(result):
            if probe in self._model_probes: self._model_probes.remove(probe)
            if revision == self._probe_revision and not self._native_closing and not False:
                completed(result)
            self.set_start_enabled(not self.engine.active)
        probe = ModelProbe(self, executable=executable, arguments=arguments,
                           environment=environment,
                           inspect=inspection, completed=finished)
        self._model_probes.append(probe)
        self.set_start_enabled(False)
        self.stop_button.setEnabled(True)
        probe.start()

    @disposable_deny('model-process')
    def inspect_model(self) -> None:
        if Path(self.settings.model_path).suffix.lower()=='.cloud':
            from cloud_model_ui import inspect
            inspect(self)
            return
        if self.model_probe_busy() or self.engine.active or self._policy_start_pending:
            self.model_inspection.setText(self.t('Stop the active operation before inspecting a model.'))
            return
        model = resolve_product_path(self.settings.model_path)
        if not model.is_file() or model.suffix.lower() != '.onnx':
            self.model_inspection.setText(self.t('Choose an existing ONNX model before inspecting it.'))
            return
        settings = deepcopy(self.settings)
        providers = list(self.engine.candidate_providers(settings))
        def next_provider(last=None):
            if not providers:
                self.model_inspection.setText(self.t(last.reason if last else 'Native engine is unavailable'))
                return
            provider = providers.pop(0)
            def finish(result):
                if result.ok:
                    self.model_inspection.setText(result.inspection)
                else: next_provider(result)
            self._probe_model(settings, provider, finish, inspection=True)
        self.model_inspection.setText(self.t('Inspecting model with the selected native provider…'))
        next_provider()

    @disposable_deny('model-process')
    def benchmark_providers(self) -> None:
        if self.engine.active:
            self.model_benchmark.setText("Stop the active capture session before benchmarking providers.")
            return
        if self.model_probe_busy() or self._policy_start_pending or self._profile_transition_pending:
            return
        model = resolve_product_path(self.settings.model_path)
        if model.suffix.lower() == '.cloud':
            self.model_benchmark.setText(self.t('For .cloud timing, select a provider and run Observe. Automatic provider comparison is ONNX-only in this alpha.'))
            return
        if not model.is_file() or model.suffix.lower() != ".onnx":
            self.model_benchmark.setText("Choose an existing ONNX model before benchmarking providers.")
            return
        candidates = [provider for provider in ("dml", "cuda", "trt", "cpu") if self.engine.executable_for(provider)]
        if not candidates:
            self.model_benchmark.setText("No bundled native engine is available for benchmarking.")
            return
        settings = Settings(**asdict(self.settings))
        settings.model_path = str(model)
        settings.capture_fps = max(120, settings.capture_fps)
        settings.inference_fps = max(120, settings.inference_fps)
        settings.frame_skip = 0
        settings.preview_enabled = False
        settings.dataset_enabled = False
        settings.movement_enabled = False
        settings.motion_compensation_enabled = False
        settings.event_action_enabled = False
        self._benchmark_settings = settings
        self._benchmark_queue = candidates
        self._benchmark_results = []
        self._benchmark_active = True
        self.model_benchmark_button.setEnabled(False)
        self.set_start_enabled(False)
        self.model_benchmark.setText("Benchmarking providers on the current capture region...")
        self._run_next_provider_benchmark()

    def _run_next_provider_benchmark(self) -> None:
        if not self._benchmark_active or self._benchmark_settings is None:
            return
        if not self._benchmark_queue:
            self._finish_provider_benchmark()
            return
        provider = self._benchmark_queue.pop(0)
        executable = self.engine.executable_for(provider)
        if executable is None:
            self._benchmark_results.append({"provider": provider, "error": "engine unavailable"})
            QTimer.singleShot(0, self._run_next_provider_benchmark)
            return

        def completed(result):
            if not self._benchmark_active: return
            self._benchmark_results.append({'provider': provider, **result.measurement} if result.ok
                                           else {'provider': provider, 'error': result.reason})
            self._run_next_provider_benchmark()
        self.model_benchmark.setText(f"Benchmarking {self.engine.provider_label(provider)}...")
        self._probe_model(self._benchmark_settings, provider, completed)

    def _finish_provider_benchmark(self) -> None:
        if not self._benchmark_active:
            return
        self._benchmark_active = False
        self._benchmark_settings = None
        self._benchmark_queue = []
        self.model_benchmark_button.setEnabled(True)
        self.set_start_enabled(not self.engine.active)
        successful = [item for item in self._benchmark_results if "inference_ms" in item]
        details: list[str] = []
        for item in self._benchmark_results:
            provider = self.engine.provider_label(str(item["provider"]))
            if "inference_ms" in item:
                details.append(f"{provider}: {float(item['inference_ms']):.1f} ms, {float(item['model_ms']):.1f} ms model, {float(item['fps']):.0f} inferences/s")
            else:
                details.append(f"{provider}: unavailable")
        if successful:
            fastest = min(successful, key=lambda item: float(item["inference_ms"]))
            fastest_name = self.engine.provider_label(str(fastest["provider"]))
            self.model_benchmark.setText(" | ".join(details) + f". Fastest: {fastest_name}.")
        else:
            self.model_benchmark.setText("No provider produced usable live timing data.")
        self.log("INFO", f"Provider benchmark complete: {' | '.join(details)}")

    @disposable_deny('model-process')
    def validate_model_runtime(self) -> None:
        """Prove an ONNX export can load and evaluate live frames safely."""
        if self.engine.active:
            self.model_validation.setText("Stop the active capture session before validating a model.")
            return
        if self.model_probe_busy() or self._policy_start_pending or self._profile_transition_pending:
            return
        model = resolve_product_path(self.settings.model_path)
        if model.suffix.lower() == '.cloud':
            self.model_validation.setText(self.t('Use Inspect for .cloud integrity and Observe for live behavior. This ONNX validation tool does not validate .cloud models.'))
            return
        if not model.is_file() or model.suffix.lower() != ".onnx":
            self.model_validation.setText("Choose an existing ONNX model before running validation.")
            return
        providers = [provider for provider in self.engine.candidate_providers(self.settings) if self.engine.executable_for(provider)]
        if not providers:
            self.model_validation.setText("No compatible bundled native engine is available for validation.")
            return
        self._validation_active = True
        self.model_validation_button.setEnabled(False)
        self.set_start_enabled(False)
        self.model_validation.setText("Preparing runtime validation…")
        validation_settings = Settings(**asdict(self.settings))
        validation_settings.model_path = str(model)
        validation_settings.capture_fps = max(30, validation_settings.capture_fps)
        validation_settings.inference_fps = max(30, validation_settings.inference_fps)
        validation_settings.preview_enabled = False
        validation_settings.dataset_enabled = False
        validation_settings.movement_enabled = False
        validation_settings.motion_compensation_enabled = False
        validation_settings.event_action_enabled = False
        self._run_model_validation(validation_settings, providers, [])

    def _run_model_validation(self, settings: Settings, providers: list[str], failures: list[str]) -> None:
        if not self._validation_active:
            return
        if not providers:
            self._validation_active = False
            self.model_validation_button.setEnabled(True)
            self.set_start_enabled(not self.engine.active)
            detail = "; ".join(failures) or "No provider could evaluate the model."
            self.model_validation.setText(f"Validation failed: {detail}")
            self.log("WARN", f"Model validation failed: {detail}")
            return
        provider = providers.pop(0)
        executable = self.engine.executable_for(provider)
        if executable is None:
            self._run_model_validation(settings, providers, failures + [f"{self.engine.provider_label(provider)} unavailable"])
            return
        def completed(result):
            if not self._validation_active: return
            if result.ok:
                measured = result.measurement
                self._validation_active = False
                self.model_validation_button.setEnabled(True)
                self.set_start_enabled(not self.engine.active)
                self.model_validation.setText(self.tf(
                    'Validated with {provider}: {frames} measured inferences after warm-up, {ms} ms average inference, {rate} inferences/s.',
                    provider=self.engine.provider_label(provider), frames=measured['frames'],
                    ms=f"{measured['inference_ms']:.1f}", rate=f"{measured['fps']:.1f}"))
            else:
                self._run_model_validation(settings, providers, failures + [f'{provider}: {result.reason}'])
        self.model_validation.setText(f"Validating {self.engine.provider_label(provider)}...")
        self._probe_model(settings, provider, completed)

    def browse_dataset(self, editor: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose dataset folder", editor.text().strip() or str(ROOT / "datasets"))
        if selected:
            editor.setText(selected)
            self.change("dataset_directory", selected)

    def dataset_worker_command(self, provider="auto") -> tuple[str, list[str]] | None:
        cuda = provider == 'cuda'
        packaged = COMPONENT_ROOT / ("VaporLockDatasetCuda.exe" if cuda else "VaporLockDatasetTools.exe")
        if packaged.is_file():
            return str(packaged), []
        source = ROOT / "ui" / "dataset_worker.py"
        if source.is_file():
            matched_python = ROOT / ('.venv-annotation-cuda' if cuda else '.venv-annotation-dml') / 'Scripts' / 'python.exe'
            if matched_python.is_file():
                return str(matched_python), ['-I', '-B', str(ROOT / 'tools' / ('run_dataset_cuda_worker.py' if cuda else 'run_dataset_worker.py'))]
            if not cuda: return sys.executable, [str(source)]
        return None

    def dataset_root_for_tools(self) -> Path:
        return resolve_dataset_root(self.settings.dataset_directory)

    def dataset_review_manifest_path(self) -> Path:
        return self.dataset_root_for_tools() / ".vaporlock-review.json"

    def load_dataset_review_decisions(self) -> dict[str, str]:
        path = self.dataset_review_manifest_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            decisions = raw.get("decisions", {}) if isinstance(raw, dict) else {}
            return {str(key): str(value) for key, value in decisions.items() if value in {"accepted", "rejected"}}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_dataset_review_decisions(self) -> None:
        path = self.dataset_review_manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        payload = {"schema_version": 1, "decisions": self.dataset_review_decisions}
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def dataset_review_key(self, image: Path) -> str:
        try:
            return image.relative_to(self.dataset_root_for_tools()).as_posix()
        except ValueError:
            return image.name

    @dataset_edit
    def refresh_dataset_review(self, selected_row: int | None = None) -> None:
        if not hasattr(self, "dataset_review_list"):
            return
        if self.dataset_review_preview.has_draft():
            self.dataset_review_preview.notice('Finish or cancel the current polygon edit first.')
            return
        previous_row = self.dataset_review_list.currentRow() if selected_row is None else selected_row
        root = self.dataset_root_for_tools()
        images = root / "images"
        self.dataset_review_decisions = self.load_dataset_review_decisions()
        self.dataset_review_records = sorted(
            (item for item in images.rglob("*") if item.is_file() and item.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}),
            key=lambda item: item.name.casefold(),
        ) if images.is_dir() else []
        self.populate_dataset_review_classes()
        self.dataset_review_list.blockSignals(True)
        self.dataset_review_list.clear()
        for image in self.dataset_review_records:
            decision = self.dataset_review_decisions.get(self.dataset_review_key(image), "")
            marker = "Accepted" if decision == "accepted" else "Excluded" if decision == "rejected" else "Needs review"
            item = QListWidgetItem(f"{marker} · {image.name}")
            item.setData(Qt.ItemDataRole.UserRole, str(image))
            item.setData(DatasetReviewQueueDelegate.decision_role, decision)
            self.dataset_review_list.addItem(item)
        self.dataset_review_list.blockSignals(False)
        accepted = sum(value == "accepted" for value in self.dataset_review_decisions.values())
        rejected = sum(value == "rejected" for value in self.dataset_review_decisions.values())
        self.dataset_review_summary.setText(
            f"{len(self.dataset_review_records)} captured images · {accepted} accepted · {rejected} excluded · "
            f"{max(0, len(self.dataset_review_records) - accepted - rejected)} awaiting review."
        )
        if self.dataset_review_records:
            self.dataset_review_list.setCurrentRow(max(0, min(previous_row, len(self.dataset_review_records) - 1)))
        else:
            self._review_document = None
            self.dataset_review_preview.set_review_image(QImage(), [])

    def dataset_review_label_path(self, image: Path) -> Path:
        root = self.dataset_root_for_tools()
        return (root / "labels" / image.relative_to(root / "images")).with_suffix(".txt")

    @staticmethod
    def parse_dataset_review_annotations(path: Path) -> list[dict[str, object]]:
        annotations: list[dict[str, object]] = []
        if not path.is_file():
            return annotations
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = raw.split()
            if not fields:
                continue
            try:
                class_id = int(fields[0])
                values = [float(value) for value in fields[1:]]
            except ValueError:
                continue
            if class_id < 0 or any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in values):
                continue
            if len(values) == 4:
                center_x, center_y, width, height = values
                annotations.append({
                    "kind": "box", "class_id": class_id,
                    "x": max(0.0, center_x - width / 2), "y": max(0.0, center_y - height / 2),
                    "w": min(width, 1.0), "h": min(height, 1.0),
                })
            elif len(values) >= 6 and len(values) % 2 == 0:
                annotations.append({
                    "kind": "polygon", "class_id": class_id,
                    "points": [QPointF(values[index], values[index + 1]) for index in range(0, len(values), 2)],
                })
        return annotations

    def write_dataset_review_annotations(self, image: Path, annotations: list[dict[str, object]]) -> None:
        path = self.dataset_review_label_path(image)
        ControlCenter.write_dataset_review_annotation_file(path, annotations)

    @staticmethod
    def write_dataset_review_annotation_file(path: Path, annotations: list[dict[str, object]]) -> None:
        lines: list[str] = []
        for annotation in annotations:
            class_id = max(0, int(annotation.get("class_id", 0)))
            if annotation.get("kind") == "box":
                x = max(0.0, min(1.0, float(annotation.get("x", 0.0))))
                y = max(0.0, min(1.0, float(annotation.get("y", 0.0))))
                width = max(0.0, min(1.0 - x, float(annotation.get("w", 0.0))))
                height = max(0.0, min(1.0 - y, float(annotation.get("h", 0.0))))
                if width > 0.0 and height > 0.0:
                    lines.append(f"{class_id} {x + width / 2:.6f} {y + height / 2:.6f} {width:.6f} {height:.6f}")
            elif annotation.get("kind") == "polygon":
                points = annotation.get("points", [])
                if len(points) >= 3:
                    coordinates = " ".join(f"{max(0.0, min(1.0, point.x())):.6f} {max(0.0, min(1.0, point.y())):.6f}" for point in points)
                    lines.append(f"{class_id} {coordinates}")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(path, "\n".join(lines) + ("\n" if lines else ""))

    def populate_dataset_review_classes(self) -> None:
        if not hasattr(self, "dataset_review_class"):
            return
        names = self.dataset_review_class_names()
        current = int(self.dataset_review_class.currentData() or 0)
        self.dataset_review_class.blockSignals(True)
        self.dataset_review_class.clear()
        for index, name in enumerate(names):
            self.dataset_review_class.addItem(f"{index} · {name}", index)
        self.dataset_review_class.setCurrentIndex(max(0, self.dataset_review_class.findData(current)))
        self.dataset_review_class.blockSignals(False)
        self.review_class_changed(self.dataset_review_class.currentIndex())
        self.update_dataset_review_slot_summary(names)

    def dataset_review_class_names(self) -> list[str]:
        root = self.dataset_root_for_tools()
        path = root / "classes.txt"
        try:
            names = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()] if path.is_file() else []
        except OSError:
            names = []
        highest = -1
        labels = root / "labels"
        if labels.is_dir():
            for label in labels.rglob("*.txt"):
                for annotation in self.parse_dataset_review_annotations(label):
                    highest = max(highest, int(annotation.get("class_id", -1)))
        while len(names) <= highest:
            names.append(f"Class {len(names)}")
        return names or ["Class 0"]

    def write_dataset_review_class_names(self, names: list[str]) -> None:
        path = self.dataset_root_for_tools() / "classes.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(path, "\n".join(name.strip() or f"Class {index}" for index, name in enumerate(names)) + "\n")

    @dataset_edit
    def add_dataset_review_class(self) -> None:
        if not hasattr(self, "dataset_review_new_class"):
            return
        name = self.dataset_review_new_class.text().strip()
        if not name:
            return
        names = self.dataset_review_class_names()
        names.append(name)
        try:
            self.write_dataset_review_class_names(names)
        except OSError as error:
            self.log("WARN", f"Could not add dataset class: {error}")
            return
        self.dataset_review_new_class.clear()
        self.populate_dataset_review_classes()
        self.dataset_review_class.setCurrentIndex(self.dataset_review_class.findData(len(names) - 1))

    @dataset_edit
    def remove_dataset_review_class(self) -> None:
        if not hasattr(self, "dataset_review_class"):
            return
        names = self.dataset_review_class_names()
        class_id = int(self.dataset_review_class.currentData() or 0)
        if len(names) <= 1:
            QMessageBox.information(self, "Class library", "A dataset needs at least one class.")
            return
        if not 0 <= class_id < len(names):
            return
        name = names[class_id]
        answer = QMessageBox.question(
            self,
            "Remove dataset class",
            f"Remove '{name}'? Its existing annotations will be deleted, and higher class IDs will be reindexed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        root = self.dataset_root_for_tools()
        try:
            for label_path in (root / "labels").rglob("*.txt") if (root / "labels").is_dir() else []:
                annotations = self.parse_dataset_review_annotations(label_path)
                remapped: list[dict[str, object]] = []
                for annotation in annotations:
                    annotation_class = int(annotation.get("class_id", 0))
                    if annotation_class == class_id:
                        continue
                    if annotation_class > class_id:
                        annotation = dict(annotation)
                        annotation["class_id"] = annotation_class - 1
                    remapped.append(annotation)
                self.write_dataset_review_annotation_file(label_path, remapped)
            names.pop(class_id)
            self.write_dataset_review_class_names(names)
        except OSError as error:
            self.log("WARN", f"Could not remove dataset class: {error}")
            QMessageBox.warning(self, "Class library", f"Could not remove the class: {error}")
            return
        slots = self.normalized_dataset_review_slots()
        self.change("dataset_review_class_slots", [
            0 if slot == class_id else slot - 1 if slot > class_id else slot
            for slot in slots
        ])
        self.populate_dataset_review_classes()
        self.dataset_review_class.setCurrentIndex(max(0, self.dataset_review_class.findData(min(class_id, len(names) - 1))))
        if hasattr(self, "dataset_review_list") and self.dataset_review_list.currentRow() >= 0:
            self.show_dataset_review_item(self.dataset_review_list.currentRow())

    def normalized_dataset_review_slots(self) -> list[int]:
        raw = self.settings.dataset_review_class_slots
        slots = [int(item) for item in raw] if isinstance(raw, list) else []
        slots = [item if item >= 0 else 0 for item in slots[:9]]
        slots.extend(range(len(slots), 9))
        return slots[:9]

    def update_dataset_review_slot_summary(self, names: list[str] | None = None) -> None:
        if not hasattr(self, "dataset_review_slot_summary"):
            return
        names = names or self.dataset_review_class_names()
        slots = self.normalized_dataset_review_slots()
        summary = []
        for index, class_id in enumerate(slots, 1):
            label = names[class_id] if 0 <= class_id < len(names) else f"Class {class_id}"
            summary.append(f"{index}: {label}")
        self.dataset_review_slot_summary.setText("Numpad slots  " + "  ·  ".join(summary))

    def assign_dataset_review_slot(self) -> None:
        if not hasattr(self, "dataset_review_class") or not hasattr(self, "dataset_review_slot_choice"):
            return
        slot = int(self.dataset_review_slot_choice.currentData() or 1)
        class_id = int(self.dataset_review_class.currentData() or 0)
        slots = self.normalized_dataset_review_slots()
        slots[slot - 1] = class_id
        self.change("dataset_review_class_slots", slots)
        self.update_dataset_review_slot_summary()

    def select_dataset_review_slot(self, slot: int) -> None:
        slots = self.normalized_dataset_review_slots()
        if not 1 <= slot <= len(slots) or not hasattr(self, "dataset_review_class"):
            return
        class_id = slots[slot - 1]
        target = self.dataset_review_class.findData(class_id)
        if target >= 0:
            self.dataset_review_class.setCurrentIndex(target)

    def review_class_changed(self, index: int) -> None:
        if hasattr(self, "dataset_review_preview") and hasattr(self, "dataset_review_class"):
            self.dataset_review_preview.set_active_class(int(self.dataset_review_class.itemData(index) or 0))

    def review_annotation_selection_changed(self, index: int) -> None:
        if index < 0 or not hasattr(self, "dataset_review_preview"):
            return
        annotations = self.dataset_review_preview.annotations()
        if index >= len(annotations) or not hasattr(self, "dataset_review_class"):
            return
        class_id = int(annotations[index].get("class_id", 0))
        target = self.dataset_review_class.findData(class_id)
        if target < 0:
            self.dataset_review_class.addItem(f"{class_id} · Class {class_id}", class_id)
            target = self.dataset_review_class.findData(class_id)
        self.dataset_review_class.setCurrentIndex(target)

    def save_dataset_review_annotations(self, annotations: list[dict[str, object]]) -> None:
        from review_document import save
        return save(self,annotations)

    def delete_selected_review_annotation(self) -> None:
        if hasattr(self, "dataset_review_preview"):
            self.dataset_review_preview.delete_selected()

    def install_dataset_review_shortcuts(self) -> None:
        if getattr(self, "_dataset_review_event_filter_installed", False):
            return
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
            self._dataset_review_event_filter_installed = True

    def eventFilter(self, watched, event):
        if hasattr(self, 'telemetry_scroll') and watched is self.telemetry_scroll.viewport() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.queue_telemetry_layout()
        if event.type() == QEvent.Type.KeyPress and self.dataset_review_active() and self.dataset_review_shortcut_focus():
            key = event.key()
            canvas=self.dataset_review_preview
            if canvas.has_draft():
                if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Space,Qt.Key.Key_Up,Qt.Key.Key_Down):
                    if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter):canvas.finish()
                    elif self.focusWidget() is canvas:canvas.keyPressEvent(event)
                    return True
            if key == Qt.Key.Key_Up:
                self.move_dataset_review_row(-1)
                return True
            if key == Qt.Key.Key_Down:
                self.move_dataset_review_row(1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.approve_dataset_review_hotkey("enter")
                return True
            if key == Qt.Key.Key_Space:
                self.approve_dataset_review_hotkey("space")
                return True
            native_key = event.nativeVirtualKey() if hasattr(event, "nativeVirtualKey") else 0
            if 0x61 <= native_key <= 0x69:
                self.select_dataset_review_slot(native_key - 0x60)
                return True
            if bool(event.modifiers() & Qt.KeyboardModifier.KeypadModifier) and Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
                self.select_dataset_review_slot(int(key) - int(Qt.Key.Key_1) + 1)
                return True
        return super().eventFilter(watched, event)

    def dataset_review_active(self) -> bool:
        return self.current_page == "dataset" and hasattr(self, "dataset_review_preview") and self.dataset_review_preview.isVisible()

    def dataset_review_shortcut_focus(self) -> bool:
        if QApplication.activeModalWidget() is not None:
            return False
        focus = self.focusWidget()
        if focus is None:
            return False
        for name in ('dataset_review_list', 'dataset_review_preview'):
            root = getattr(self, name, None)
            if root is not None and (focus is root or root.isAncestorOf(focus)):
                return True
        return False

    def move_dataset_review_row(self, direction: int) -> None:
        if not self.dataset_review_active() or not self.dataset_review_records:
            return
        current = self.dataset_review_list.currentRow()
        self.dataset_review_list.setCurrentRow(max(0, min(len(self.dataset_review_records) - 1, current + direction)))

    def approve_dataset_review_hotkey(self, key: str) -> None:
        if self.dataset_review_active() and self.settings.dataset_review_approval_key == key:
            self.set_dataset_review_status("accepted", advance=True)

    def show_dataset_review_item(self, row: int) -> None:
        from review_document import show
        return show(self,row)

    def _legacy_show_dataset_review_item(self, row: int) -> None:
        if row < 0 or row >= len(getattr(self, "dataset_review_records", [])):
            return
        image_path = self.dataset_review_records[row]
        image = QImage(str(image_path))
        if image.isNull():
            self.dataset_review_preview.set_review_image(QImage(), [])
            return
        self.dataset_review_preview.set_review_image(image, self.parse_dataset_review_annotations(self.dataset_review_label_path(image_path)))

    @dataset_edit
    def set_dataset_review_status(self, status: str, *, advance: bool = False) -> None:
        if self.dataset_review_preview.has_draft():
            self.dataset_review_preview.notice('Finish or cancel the current polygon edit first.')
            return
        row = self.dataset_review_list.currentRow() if hasattr(self, "dataset_review_list") else -1
        if row < 0 or row >= len(getattr(self, "dataset_review_records", [])):
            return
        key = self.dataset_review_key(self.dataset_review_records[row])
        if status:
            self.dataset_review_decisions[key] = status
        else:
            self.dataset_review_decisions.pop(key, None)
        self.save_dataset_review_decisions()
        self.refresh_dataset_review(row + 1 if advance else row)

    def edit_dataset_review_labels(self) -> None:
        from batch7_ui import edit_label_text
        edit_label_text(self)

    def training_history_path(self) -> Path:
        return ROOT / "training" / "history.json"

    def load_training_history(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self.training_history_path().read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def append_training_history(self, record: dict[str, object]) -> None:
        history = self.load_training_history()
        history.insert(0, record)
        path = self.training_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(history[:40], indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        self.refresh_training_history()

    def refresh_training_history(self) -> None:
        if not hasattr(self, "training_history"):
            return
        history = self.load_training_history()
        if not history:
            self.training_history.setText("No completed local training runs yet.")
            return
        summaries = []
        for item in history[:4]:
            label = str(item.get("name", "training run"))
            state = str(item.get("status", "unknown"))
            stamp = str(item.get("time", ""))[:16].replace("T", " ")
            summaries.append(f"{label} · {state}" + (f" · {stamp}" if stamp else ""))
        self.training_history.setText("Recent runs: " + " | ".join(summaries))

    def training_worker_command(self) -> tuple[str, list[str]] | None:
        packaged = COMPONENT_ROOT / "VaporLockTrainingTools.exe"
        if packaged.is_file():
            return str(packaged), []
        source = ROOT / "ui" / "training_worker.py"
        if source.is_file():
            return sys.executable, [str(source)]
        return None

    def training_model_candidates(self) -> list[Path]:
        models_root = ROOT / "models"
        models_root.mkdir(exist_ok=True)
        return sorted((candidate for candidate in models_root.rglob("*") if candidate.is_file() and candidate.suffix.lower() in {".pt", ".yaml", ".yml"}), key=lambda candidate: candidate.name.casefold())

    def training_dataset_candidates(self) -> list[Path]:
        root = self.dataset_root_for_tools()
        if not root.is_dir():
            return []
        candidates = [candidate for candidate in root.rglob("data.yaml") if candidate.is_file()]
        return sorted(candidates, key=lambda candidate: candidate.parent.name.casefold(), reverse=True)

    def populate_training_model_library(self) -> None:
        if not hasattr(self, "training_model_library"):
            return
        models_root = ROOT / "models"
        self.training_model_library.blockSignals(True)
        self.training_model_library.clear()
        self.training_model_library.addItem("Select a trainable .pt or .yaml model", "")
        for model in self.training_model_candidates():
            self.training_model_library.addItem(str(model.relative_to(models_root)), str(model))
        self.training_model_library.setCurrentIndex(max(0, self.training_model_library.findData(self.settings.training_base_model)))
        self.training_model_library.blockSignals(False)

    def populate_training_dataset_library(self) -> None:
        if not hasattr(self, "training_dataset_library"):
            return
        root = self.dataset_root_for_tools()
        self.training_dataset_library.blockSignals(True)
        self.training_dataset_library.clear()
        self.training_dataset_library.addItem("Build a dataset in Collection first", "")
        for data in self.training_dataset_candidates():
            try:
                label = str(data.parent.relative_to(root))
            except ValueError:
                label = data.parent.name
            self.training_dataset_library.addItem(label, str(data))
        self.training_dataset_library.setCurrentIndex(max(0, self.training_dataset_library.findData(self.settings.training_dataset)))
        self.training_dataset_library.blockSignals(False)

    def select_training_model(self, index: int) -> None:
        self.change("training_base_model", str(self.training_model_library.itemData(index) or ""))

    def select_training_dataset(self, index: int) -> None:
        self.change("training_dataset", str(self.training_dataset_library.itemData(index) or ""))

    def refresh_training_libraries(self) -> None:
        self.populate_training_model_library()
        self.populate_training_dataset_library()

    @disposable_deny('training-process')
    def start_training_worker(self, arguments: list[str], api_key: str = "") -> None:
        from api_jobs import admission
        if not admission(self,'training'):
            self.log('WARN','Stop conflicting work or unlock tuning before starting a training task.');return
        from training_security import MAX_RECORD, Redactor, authentication_environment, credential_packet
        try:
            packet = credential_packet(api_key.strip() if arguments and arguments[0] == 'train' else '')
        except ValueError as error:
            QMessageBox.information(self, 'Training', str(error))
            return
        redactor = Redactor(api_key.strip())
        current = getattr(self, "_training_worker", None)
        if current is not None:
            try:
                if current.state() != QProcess.ProcessState.NotRunning:
                    QMessageBox.information(self, "Training", "A local training task is already running.")
                    return
            except RuntimeError:
                self._training_worker = None
        command = self.training_worker_command()
        if command is None:
            QMessageBox.warning(self, "Training", "The packaged training worker is missing. Rebuild the application.")
            return
        executable, prefix = command
        process = QProcess(self)
        self._training_worker = process
        self._training_worker_buffer = b""
        self._training_worker_stderr = b""
        run_record: dict[str, object] | None = None
        run_completed = False
        environment = QProcessEnvironment.systemEnvironment()
        for name in environment.keys():
            if authentication_environment(name):
                environment.remove(name)
        process.setProcessEnvironment(environment)
        for button in (getattr(self, "training_install_button", None), getattr(self, "training_start_button", None)):
            if button is not None:
                button.setEnabled(False)
        if hasattr(self, "training_stop_button"):
            self.training_stop_button.setEnabled(True)

        def consume() -> None:
            nonlocal run_record, run_completed
            self._training_worker_buffer += bytes(process.readAllStandardOutput())
            if len(self._training_worker_buffer) > MAX_RECORD * 128:
                self._training_worker_buffer = b''
                self.training_status.setText('An oversized training message was omitted.')
                return
            while b"\n" in self._training_worker_buffer:
                line, self._training_worker_buffer = self._training_worker_buffer.split(b"\n", 1)
                try:
                    if len(line) > MAX_RECORD:
                        continue
                    payload = redactor.payload(json.loads(line.decode("utf-8", errors="replace")))
                    if not isinstance(payload, dict):
                        continue
                except (ValueError, RecursionError):
                    continue
                from api_jobs import event as api_job_event
                api_job_event(self,process,payload)
                event = str(payload.get("type", ""))
                if event == "installing":
                    self.training_runtime_status.setText(str(payload.get("message", "Installing training runtime…")))
                    self.training_status.setText("Preparing the local training runtime…")
                elif event == "install-progress":
                    self.training_runtime_status.setText(str(payload.get("message", "Installing training runtime…")))
                elif event in {"ready", "runtime"}:
                    ready = bool(payload.get("ready", event == "ready"))
                    profile = str(payload.get("profile", "")).strip()
                    detail = str(payload.get("detail", "")).strip()
                    if ready:
                        self.training_runtime_status.setText("Local training runtime is ready" + (f" ({profile})." if profile else "."))
                        self.training_status.setText("The local training runtime is installed, verified, and ready.")
                    else:
                        self.training_runtime_status.setText("Local training runtime is not installed." + (f" {detail}" if detail else ""))
                        self.training_status.setText("Install or repair the training runtime before starting a run.")
                elif event == "training-phase":
                    message = str(payload.get("message", "Preparing local training…"))
                    self.training_status.setText(message)
                elif event == "training":
                    run_record = {
                        "name": str(payload.get("name", "training run")),
                        "dataset": str(payload.get("dataset", "")),
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "status": "running",
                    }
                    self.training_status.setText(f"Training {payload.get('name', 'model')} in the background…")
                    self.log("INFO", "Local Ultralytics training started.")
                elif event == "training-progress":
                    message = str(payload.get("message", "Training is progressing."))
                    self.training_status.setText(message)
                elif event == "training-heartbeat":
                    self.training_status.setText(str(payload.get("message", "Training is active.")))
                elif event == "training-log":
                    message = str(payload.get("message", "")).strip()
                    if message:
                        self.training_status.setText(message[-300:])
                elif event == "preflight":
                    problems = [str(item) for item in payload.get("problems", [])]
                    warnings = [str(item) for item in payload.get("warnings", [])]
                    summary = (
                        f"{payload.get('train_images', 0)} training · {payload.get('validation_images', 0)} validation · "
                        f"{payload.get('labeled', 0)} labeled · {payload.get('classes', 0)} classes. "
                    )
                    if int(payload.get("gpu_total_mb", 0)) > 0:
                        summary += f"NVIDIA memory: {payload.get('gpu_free_mb', 0)} / {payload.get('gpu_total_mb', 0)} MB free. "
                    summary += str(payload.get("recommendation", ""))
                    if problems:
                        summary += " Problems: " + "; ".join(problems[:2])
                    elif warnings:
                        summary += " Notes: " + "; ".join(warnings[:2])
                    self.training_preflight.setText(summary)
                elif event == "trained":
                    run_completed = True
                    checkpoint = Path(str(payload.get("checkpoint", ""))).name
                    exported = Path(str(payload.get("onnx", ""))).name if payload.get("onnx") else ""
                    self.training_status.setText(f"Training complete: {checkpoint}" + (f" · exported {exported}" if exported else ""))
                    self.log("INFO", f"Local training completed: {payload.get('checkpoint', '')}")
                    if payload.get("platform_url"):
                        self.training_status.setText(self.training_status.text() + f" · {payload['platform_url']}")
                    if payload.get("onnx"):
                        if payload.get("onnx_validated"):
                            self.training_status.setText(self.training_status.text() + " · ONNX structure verified")
                        else:
                            self.training_status.setText(self.training_status.text() + " · ONNX export needs inspection")
                    if run_record is not None:
                        run_record.update({
                            "status": "completed", "checkpoint": str(payload.get("checkpoint", "")),
                            "onnx": str(payload.get("onnx", "")), "onnx_validated": bool(payload.get("onnx_validated", False)),
                        })
                        self.append_training_history(run_record)
                        run_record = None
                    self.refresh_model_library()
                    self.refresh_training_libraries()
                elif event == "error":
                    self.training_status.setText(f"Training task stopped: {payload.get('message', 'unknown error')}")
                    self.log("WARN", f"Training worker: {payload.get('message', 'unknown error')}")

        def consume_stderr() -> None:
            # SDK details come through sanitized structured stdout. Raw startup
            # stderr can contain partial credentials, so retain only its presence.
            if bytes(process.readAllStandardError()):
                self._training_worker_stderr = b'Training process reported a startup or runtime error.'

        def finished(code: int, _status: QProcess.ExitStatus) -> None:
            nonlocal run_record
            if bool(process.property("vaporlock_cleaned")):
                return
            process.setProperty("vaporlock_cleaned", True)
            consume()
            consume_stderr()
            if run_record is not None:
                run_record["status"] = "cancelled" if bool(process.property("vaporlock_user_cancelled")) else "failed"
                self.append_training_history(run_record)
                if not run_completed and bool(process.property("vaporlock_user_cancelled")):
                    self.training_status.setText("Training cancelled. Any checkpoint already saved remains in training/runs.")
                run_record = None
            if code != 0 and not self.training_status.text().startswith("Training task stopped"):
                self.training_status.setText("Training task stopped. Review Status & Diagnostics for details.")
            if code != 0 and self._training_worker_stderr.strip():
                detail = self._training_worker_stderr.decode("utf-8", errors="replace").strip().splitlines()[-1]
                self.log("WARN", f"Training process: {detail[-500:]}")
            if self._training_worker is process:
                self._training_worker = None
            for button in (getattr(self, "training_install_button", None), getattr(self, "training_start_button", None)):
                if button is not None:
                    button.setEnabled(True)
            if hasattr(self, "training_stop_button"):
                self.training_stop_button.setEnabled(False)
            process.deleteLater()

        process.readyReadStandardOutput.connect(consume)
        process.readyReadStandardError.connect(consume_stderr)
        process.finished.connect(finished)
        def failed(error) -> None:
            self.log("WARN", f"Training worker process error: {error.name}")
            if error == QProcess.ProcessError.FailedToStart:
                finished(-1, QProcess.ExitStatus.CrashExit)
        process.errorOccurred.connect(failed)
        def handoff() -> None:
            offset = 0
            while offset < len(packet):
                written = process.write(packet[offset:])
                if written <= 0:
                    process.kill()
                    self.training_status.setText('Training task stopped: credential handoff failed.')
                    return
                offset += written
            process.closeWriteChannel()
        process.started.connect(handoff)
        from api_jobs import bind
        bind(self,process,'training')
        process.start(executable, prefix + ["--root", str(ROOT), *arguments])

    def sync_background_controls(self) -> None:
        for attribute, buttons, stop in (
            ("_training_worker", ("training_install_button", "training_start_button"), "training_stop_button"),
            ("_dataset_worker", ("dataset_annotation_button", "dataset_package_button"), "dataset_worker_stop_button"),
        ):
            process = getattr(self, attribute, None)
            try:
                busy = process is not None and process.state() != QProcess.ProcessState.NotRunning
            except RuntimeError:
                busy = False
            for name in buttons:
                if hasattr(self, name):
                    getattr(self, name).setEnabled(not busy)
            if hasattr(self, stop):
                getattr(self, stop).setEnabled(busy)
            if attribute == "_dataset_worker" and hasattr(self, "dataset_review_panel"):
                self.dataset_review_panel.setEnabled(not False and (not busy or bool(process.property('vaporlock_assist_worker'))))
                self.dataset_annotation_backend.setEnabled(not busy)
                from annotation_suite_ui import fingerprint
                preview=getattr(self,'_ultralytics_preview',None)
                allowed=not False and not False
                self.ultralytics_annotation_backend.setEnabled(not busy and allowed)
                self.ultralytics_preview_button.setEnabled(not busy and allowed)
                self.ultralytics_stop_button.setEnabled(busy and bool(process.property('vaporlock_cloud_worker')))
                ready=not busy and allowed and bool(preview and preview['fingerprint']==fingerprint(self.settings))
                self.ultralytics_inspect_button.setEnabled(ready)
                self.ultralytics_apply_button.setEnabled(ready)

    def check_training_runtime(self) -> None:
        self.training_runtime_status.setText("Checking local training runtime…")
        self.start_training_worker(["status"])

    def install_training_runtime(self) -> None:
        self.training_runtime_status.setText("Preparing local training runtime…")
        self.start_training_worker(["install"])

    def run_training_preflight(self) -> None:
        dataset = resolve_product_path(self.settings.training_dataset)
        if not dataset.is_file() or dataset.name.lower() != "data.yaml":
            QMessageBox.information(self, "Training", "Build and select a prepared dataset first.")
            return
        self.training_preflight.setText("Checking dataset labels, image pairing, disk space, and available NVIDIA memory…")
        self.start_training_worker(["preflight", "--data", str(dataset)])

    def start_local_training(self) -> None:
        model = resolve_product_path(self.settings.training_base_model)
        dataset = resolve_product_path(self.settings.training_dataset)
        if not model.is_file() or model.suffix.lower() not in {".pt", ".yaml", ".yml"}:
            QMessageBox.information(self, "Training", "Choose a trainable .pt checkpoint or .yaml model definition from the model library.")
            return
        if not dataset.is_file() or dataset.name.lower() != "data.yaml":
            QMessageBox.information(self, "Training", "Build and select a prepared dataset first.")
            return
        api_key = self.training_api_key.text().strip() if hasattr(self, "training_api_key") else ""
        platform_project = self.settings.training_platform_project.strip()
        from training_security import load_extra_arguments, validate_platform
        try:
            load_extra_arguments(self.settings.training_extra_arguments)
            validate_platform(platform_project, api_key)
        except ValueError as error:
            QMessageBox.information(self, 'Training', str(error))
            return
        if bool(api_key) != bool(platform_project):
            QMessageBox.information(self, "Training", "Enter both a Platform project path and API key, or leave both blank for fully local training.")
            return
        arguments = [
            "train", "--model", str(model), "--data", str(dataset), "--name", self.settings.training_name,
            "--platform-project", self.settings.training_platform_project, "--epochs", str(self.settings.training_epochs),
            "--imgsz", str(self.settings.training_imgsz), "--batch", self.settings.training_batch,
            "--device", self.settings.training_device, "--workers", str(self.settings.training_workers),
            "--patience", str(self.settings.training_patience), "--optimizer", self.settings.training_optimizer,
            "--lr0", str(self.settings.training_lr0), "--lrf", str(self.settings.training_lrf),
            "--weight-decay", str(self.settings.training_weight_decay), "--warmup-epochs", str(self.settings.training_warmup_epochs),
            "--close-mosaic", str(self.settings.training_close_mosaic), "--cache", self.settings.training_cache,
            "--seed", str(self.settings.training_seed), "--fraction", str(self.settings.training_fraction),
            "--extra", self.settings.training_extra_arguments,
            "--amp" if self.settings.training_amp else "--no-amp",
            "--pretrained" if self.settings.training_pretrained else "--no-pretrained",
            "--cos-lr" if self.settings.training_cos_lr else "--no-cos-lr",
            "--deterministic" if self.settings.training_deterministic else "--no-deterministic",
            "--export-onnx" if self.settings.training_export_onnx else "--no-export-onnx",
            "--dynamic-onnx" if self.settings.training_dynamic_onnx else "--no-dynamic-onnx",
        ]
        self.training_status.setText("Starting local training…")
        self.start_training_worker(arguments, api_key)

    def stop_training_worker(self) -> None:
        process = getattr(self, "_training_worker", None)
        if process is None:
            return
        try:
            if process.state() == QProcess.ProcessState.NotRunning:
                self._training_worker = None
                return
            self.training_status.setText("Stopping local training…")
            process.setProperty("vaporlock_user_cancelled", True)
            # The training installer launches its private Python runtime as a
            # child process. taskkill /T ends the whole local worker tree, so
            # cancelling cannot leave pip downloading in the background.
            worker_pid = int(process.processId())
            if worker_pid > 0 and sys.platform == "win32":
                subprocess.Popen(
                    ["taskkill", "/PID", str(worker_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                process.terminate()
                self.schedule_worker_kill(process, 5000)
        except RuntimeError:
            self._training_worker = None

    @disposable_deny('dataset-process')
    def start_dataset_worker(self, arguments: list[str], *, stdin_payload=None, workflow='local', preview_fingerprint=None) -> bool:
        from api_jobs import admission
        if not admission(self,'dataset'):
            self.log('WARN','Stop conflicting work or unlock tuning before starting a dataset task.');return False
        current = getattr(self, "_dataset_worker", None)
        if current is not None:
            try:
                if current.state() != QProcess.ProcessState.NotRunning:
                    QMessageBox.information(self, "Training data", "A background dataset task is already running.")
                    return False
            except RuntimeError:
                self._dataset_worker = None
        requested_provider = arguments[arguments.index('--provider')+1] if '--provider' in arguments else 'cpu'
        command = self.dataset_worker_command(requested_provider)
        if command is None:
            QMessageBox.warning(self, "Training data", "The packaged dataset worker is missing. Rebuild the application.")
            return False
        executable, prefix = command
        process = QProcess(self)
        self._dataset_worker = process
        self._dataset_worker_buffer = b""
        assist=bool(arguments and arguments[0]=='assist')
        assist_owner=self.annotation_assist if assist else None
        process.setProperty('vaporlock_assist_worker',assist)
        cloud = workflow == 'ultralytics'
        process.setProperty('vaporlock_cloud_worker', cloud)
        if cloud:
            from annotation_output import plain
            cloud_root=plain(arguments[arguments.index('--dataset')+1])
            cancel_path=cloud_root/('.vaporlock-ultralytics-stop-'+uuid.uuid4().hex)
            arguments=[*arguments,'--cancel-file',str(cancel_path)]
            process.setProperty('vaporlock_cloud_cancel_path',str(cancel_path))
        worker_status = self.ultralytics_worker_status if cloud else self.dataset_worker_status
        backend_status = self.ultralytics_backend_status if cloud else self.dataset_backend_status
        process.setProperty('vaporlock_started_at',time.monotonic())
        if cloud:
            self._ultralytics_job_target=self.settings.ultralytics_owner+'/'+self.settings.ultralytics_dataset
            self._ultralytics_elapsed_snapshot=None;self._ultralytics_final_seconds=None;self._ultralytics_result_payload=None
            self.ultralytics_activity_clock.clear();self.ultralytics_result_details.clear()
            from annotation_suite_ui import refresh_run_summary
            refresh_run_summary(self)
        annotation_requested = bool(arguments and arguments[0] in ('annotate','platform-preview'))
        if annotation_requested:
            backend_status.setText(self.t('Initializing annotation backend…'))
        self.dataset_annotation_backend.setEnabled(False)
        for button in (getattr(self, "dataset_annotation_button", None), getattr(self, "dataset_package_button", None)):
            if button is not None:
                button.setEnabled(False)
        if hasattr(self, "dataset_worker_stop_button"):
            self.dataset_worker_stop_button.setEnabled(True)
        if hasattr(self, "dataset_review_panel") and not assist:
            self.dataset_review_panel.setEnabled(False)
        for name in ('ultralytics_preview_button','ultralytics_apply_button','ultralytics_annotation_backend'):
            if hasattr(self,name):getattr(self,name).setEnabled(False)
        self.ultralytics_stop_button.setEnabled(cloud)
        if cloud:
            worker_status.setText(self.t('Ultralytics task is running…'))

        def handoff() -> None:
            if self._dataset_worker is not process or self._native_closing:
                if stdin_payload is not None:stdin_payload.clear()
                process.closeWriteChannel();process.terminate();return
            if stdin_payload is None:
                process.closeWriteChannel()
                return
            data=json.dumps(stdin_payload,ensure_ascii=True).encode('ascii')
            stdin_payload.clear()
            if len(data)>2048 or process.write(data)!=len(data):
                process.kill()
            process.closeWriteChannel()
        process.started.connect(handoff)

        def consume() -> None:
            if self._dataset_worker is not process:
                process.readAllStandardOutput();return
            worker_status = self.ultralytics_worker_status if cloud else self.dataset_worker_status
            backend_status = self.ultralytics_backend_status if cloud else self.dataset_backend_status
            self._dataset_worker_buffer += bytes(process.readAllStandardOutput())
            if len(self._dataset_worker_buffer)>1048576:
                self._dataset_worker_buffer=b''
                process.kill()
                return
            while b"\n" in self._dataset_worker_buffer:
                line, self._dataset_worker_buffer = self._dataset_worker_buffer.split(b"\n", 1)
                try:
                    payload = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if type(payload) is not dict:continue
                from api_jobs import event as api_job_event
                api_job_event(self,process,payload)
                event = payload.get("type")
                if assist and event=='assist_result':
                    assist_owner.receive(process,payload);continue
                if assist and event in ('assist_error','provider_error'):
                    assist_owner.error_code=payload.get('code')
                if cloud and type(event) is str and event.startswith('cloud_'):
                    from annotation_suite_ui import cloud_event
                    cloud_event(self,payload,preview_fingerprint)
                    continue
                if event == 'annotation_batch':
                    from batch6_ui import show_batch_report
                    show_batch_report(self,backend_status,worker_status,payload)
                    continue
                if event == 'provider':
                    process.setProperty('vaporlock_provider_status', True)
                    selected = payload.get('selected')
                    if selected == 'cpu':
                        text = self.t('Annotation backend: CPU.')
                        if payload.get('requested') == 'auto':
                            text += ' ' + self.t('GPU unavailable; using CPU.')
                    else:
                        text = self.tf('Annotation backend: {backend} · Adapter {device}. Some operations may run on CPU.',
                            backend='DirectML' if selected == 'directml' else 'CUDA', device=payload.get('device_index', 0))
                    backend_status.setText(text)
                    self.log('INFO',text)
                    initialization=payload.get('initialization_ms')
                    from annotation_progress import bounded
                    if bounded(initialization):self.log('INFO',self.tf('Annotation model initialization: {seconds}s.',seconds=round(initialization/1000,2)))
                elif event == 'annotation_stage':
                    from annotation_progress import show_stage
                    show_stage(self,payload,cloud=cloud)
                elif event == 'provider_error':
                    process.setProperty('vaporlock_provider_status', True)
                    process.setProperty('vaporlock_reported_error', True)
                    messages = {
                        'CUDA_RUNTIME_UNAVAILABLE': 'CUDA runtime is unavailable. Install or repair CUDA in Model Library, then retry annotation.',
                        'CUDA_BINDING_UNAVAILABLE': 'CUDA runtime is unavailable. Install or repair CUDA in Model Library, then retry annotation.',
                        'NOT_INSTALLED': 'Annotation stopped: the selected backend is not installed. Select Auto, DirectML or CPU.',
                        'INITIALIZATION_FAILED': 'Annotation stopped: the selected GPU could not initialize. Check the graphics driver or select Auto or CPU.',
                        'PROVIDER_NOT_RETAINED': 'Annotation stopped: the requested GPU backend was not retained. No labels were changed.',
                        'TENSORRT_ANNOTATION_UNAVAILABLE': 'Annotation stopped: TensorRT RTX annotation is unavailable in this release. Select Auto, DirectML or CPU.',
                    }
                    worker_status.setText(self.t(messages.get(payload.get('code'), 'Annotation stopped: no usable backend is available.')))
                    backend_status.setText(worker_status.text())
                elif event == "progress":
                    worker_status.setText(self.tf("Working: {current}/{total} · {file}", current=payload.get('current', 0), total=payload.get('total', 0), file=payload.get('file', '')))
                    if 'timing' in payload:
                        from annotation_progress import show_stage,show_timing
                        show_stage(self,dict(type='annotation_stage',stage='annotating',current=payload.get('current'),total=payload.get('total'),passes=payload.get('passes'),pass_current=payload.get('passes')),cloud=cloud)
                        show_timing(self,payload,cloud=cloud)
                elif event == "started":
                    worker_status.setText(self.tf("Annotating {images} source images in the background.", images=payload.get('images', 0)))
                elif event == "complete":
                    worker_status.setText(self.tf('Annotation complete: {processed} processed · {annotated} annotated · {removed} removed · {protected} protected · {failed} failed.',
                        processed=payload.get('images', 0), annotated=payload.get('annotated', 0),
                        removed=payload.get('removed', 0), protected=payload.get('protected', 0), failed=payload.get('failed', 0)))
                    if payload.get('additive'):
                        worker_status.setText(worker_status.text()+'\n'+self.tf('Labels: {preserved} preserved, {added} added, {suppressed} overlapping predictions suppressed.',preserved=payload.get('preserved_labels',0),added=payload.get('added_labels',0),suppressed=payload.get('suppressed_labels',0)))
                    if payload.get('cleanup_pending'):
                        worker_status.setText(worker_status.text() + '\n' + self.t('Annotation committed. Quarantine cleanup is pending and will be retried before the next dataset task.'))
                    self.log("INFO", "Background dataset annotation completed.")
                    process.setProperty('vaporlock_annotation_summary',worker_status.text())
                elif event == 'effects_complete':
                    worker_status.setText((process.property('vaporlock_annotation_summary') or '')+'\n'+self.tf('Image effects complete: {count} images overwritten · no extra copies.',count=payload.get('replaced',0)))
                    if payload.get('cleanup_pending') or payload.get('report_pending'):
                        worker_status.setText(worker_status.text()+'\n'+self.t('Images were replaced. Temporary-file cleanup or report writing is pending.'))
                elif event == 'variations_ready':
                    worker_status.setText((process.property('vaporlock_annotation_summary') or '')+'\n'+self.tf('Image effects complete: {count} extra copies created in {folder}.',count=payload.get('generated',0),folder=payload.get('folder','')))
                elif event == 'variation_progress':
                    worker_status.setText((process.property('vaporlock_annotation_summary') or '')+'\n'+self.tf('Applying image effects: {count} images.',count=payload.get('current',0)))
                elif event in ('effects_error','effects_skipped'):
                    process.setProperty('vaporlock_reported_error',True)
                    if payload.get('outcome_uncertain'):
                        message=self.t('Labels were committed. Image effects stopped; review recovery before retrying.')
                    elif event=='effects_skipped':
                        message=self.t('Image effects were skipped because annotation cleanup is pending.')
                    else:message=self.t('Image effects failed. Annotation results remain saved; incomplete copy output must not be used.')
                    worker_status.setText((process.property('vaporlock_annotation_summary') or '')+'\n'+message)
                elif event == "packaged":
                    self.dataset_worker_status.setText(self.tf("Ultralytics ZIP ready: {archive}", archive=Path(str(payload.get('archive', ''))).name))
                    if payload.get("warning"):
                        self.dataset_worker_status.setText(self.dataset_worker_status.text() + " " + str(payload["warning"]))
                    self.log("INFO", f"Dataset archive created: {payload.get('archive', '')}")
                elif event == "error":
                    process.setProperty("vaporlock_reported_error", True)
                    message = self.t(str(payload.get('message', 'unknown error')))
                    if not cloud or not process.property('vaporlock_cloud_outcome'):
                        summary=process.property('vaporlock_annotation_summary') or ''
                        worker_status.setText((summary+'\n' if summary else '')+self.tf("Dataset task stopped: {message}", message=message))
                        if summary:worker_status.setText(worker_status.text()+'\n'+self.t('Image effects stopped. Labels remain saved; interrupted image changes may require recovery.'))
                    self.log("WARN", f"Dataset worker: {payload.get('message', 'unknown error')}")

        def finished(code: int, _status: QProcess.ExitStatus) -> None:
            if bool(process.property("vaporlock_cleaned")):
                return
            process.setProperty("vaporlock_cleaned", True)
            try:
                if self._dataset_worker is not process:return
                consume()
                worker_status = self.ultralytics_worker_status if cloud else self.dataset_worker_status
                backend_status = self.ultralytics_backend_status if cloud else self.dataset_backend_status
                if annotation_requested and not process.property('vaporlock_provider_status'):
                    backend_status.setText(self.t('Annotation backend did not start.'))
                if code != 0 and not process.property("vaporlock_reported_error"):
                    if cloud:
                        if process.property('vaporlock_cloud_outcome'):
                            warning=self.t('Cloud worker exited unexpectedly after reporting its result. Check the operation report.')
                            self.ultralytics_result_details.setText(self.ultralytics_result_details.text()+'\n'+warning)
                            self.log('WARN',warning)
                        else:
                            worker_status.setText(self.t('Cloud task ended without a final result. Pending writes may be uncertain; inspect the local operation report.'))
                    else:
                        summary=process.property('vaporlock_annotation_summary') or ''
                        worker_status.setText(summary+'\n'+self.t('Image effects stopped. Labels remain saved; interrupted image changes may require recovery.') if summary
                            else self.t("Dataset task stopped. Review the Status & Diagnostics log."))
            except Exception:
                try:self.log('WARN','Dataset worker status could not be refreshed. Review its saved report.')
                except RuntimeError:pass
            finally:
                # Release ownership and credentials even if a view was deleted
                # or a status callback failed. UI restoration comes afterwards.
                owned=self._dataset_worker is process
                if owned:
                    self._dataset_worker=None
                    try:
                        if cloud:
                            started=process.property('vaporlock_started_at')
                            if isinstance(started,(int,float)):
                                self._ultralytics_final_seconds=round(time.monotonic()-started,1)
                                from annotation_progress import refresh_activity
                                refresh_activity(self)
                    except RuntimeError:pass
                if stdin_payload is not None:stdin_payload.clear()
                try:
                    cancel_identity=process.property('vaporlock_cloud_cancel_identity')
                    if cancel_identity:
                        from dataset_transaction import HeldFile
                        try:
                            with HeldFile(process.property('vaporlock_cloud_cancel_path')) as marker:
                                if marker.snapshot()==cancel_identity:marker.delete()
                        except (OSError,ValueError):pass
                finally:
                    if assist:
                        try:assist_owner.finished(process,code)
                        except RuntimeError:assist_owner.release()
                    try:process.deleteLater()
                    except RuntimeError:pass
                    if owned:
                        try:
                            self.sync_background_controls()
                            if not assist and hasattr(self,'dataset_review_panel'):self.refresh_dataset_review()
                        except RuntimeError:pass

        process.readyReadStandardOutput.connect(consume)
        process.finished.connect(finished)
        def failed(error) -> None:
            if self._dataset_worker is process:self.log("WARN", f"Dataset worker process error: {error.name}")
            if error == QProcess.ProcessError.FailedToStart:
                if self._dataset_worker is process and arguments and arguments[0] == 'annotate':
                    self.dataset_backend_status.setText(self.t('Annotation backend did not start.'))
                finished(-1, QProcess.ExitStatus.CrashExit)
        process.errorOccurred.connect(failed)
        # Cloud subprocess errors can contain signed URLs from third-party
        # libraries. Discard raw stderr rather than forwarding it to logs.
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        from api_jobs import bind
        bind(self,process,'annotation')
        process.start(executable, prefix + arguments)
        return True

    @staticmethod
    def schedule_worker_kill(process: QProcess, delay: int) -> None:
        timer = QTimer(process)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: process.kill() if process.state() != QProcess.ProcessState.NotRunning else None)
        timer.start(delay)

    def stop_dataset_worker(self) -> None:
        process = getattr(self, "_dataset_worker", None)
        if process is None:
            return
        try:
            if process.state() == QProcess.ProcessState.NotRunning:
                self._dataset_worker = None
                return
            status=self.ultralytics_worker_status if process.property('vaporlock_cloud_worker') else self.dataset_worker_status
            summary=process.property('vaporlock_annotation_summary') or ''
            status.setText(summary+'\n'+self.t('Stopping image effects. Labels remain saved; interrupted image changes may require recovery.') if summary
                else self.t("Stopping background dataset task…"))
            if process.property('vaporlock_cloud_worker'):
                self._ultralytics_preview=None
                status.setText(status.text()+'\n'+self.t('Completed cloud changes are retained. Review the operation report before another Apply.'))
                from annotation_output import write_new
                from dataset_transaction import snapshot_file
                marker=Path(process.property('vaporlock_cloud_cancel_path'))
                try:
                    if not marker.exists():
                        write_new(marker,b'cancel\n')
                        process.setProperty('vaporlock_cloud_cancel_identity',snapshot_file(marker))
                except (ValueError,OSError):
                    status.setText(self.t('Stop request could not be saved. The task may still be running; review its report.'))
                    return
                # Let the current bounded request settle and flush its receipt.
                # Forced timeout preserves any pending mutation as uncertain.
                process.setProperty('vaporlock_user_cancelled',True)
                self.schedule_worker_kill(process,30000)
                return
            process.setProperty('vaporlock_user_cancelled',True)
            process.terminate()
            self.schedule_worker_kill(process, 2000)
        except RuntimeError:
            self._dataset_worker = None

    def annotate_dataset(self) -> None:
        root = self.dataset_root_for_tools()
        model_setting = self.settings.dataset_annotation_model or self.settings.model_path
        model = resolve_product_path(model_setting)
        if not model.is_file():
            QMessageBox.information(self, "Training data", "Choose an ONNX annotation model, or load a current ONNX model first.")
            return
        arguments = ["annotate", "--batch-size", str(self.settings.dataset_annotation_batch), "--provider", self.settings.dataset_annotation_provider, "--dataset", str(root), "--model", str(model), "--passes", str(self.settings.dataset_annotation_passes),
                     "--confidence", str(self.settings.dataset_annotation_confidence), "--consensus-iou", str(self.settings.dataset_annotation_iou),
                     "--nms-iou", str(self.settings.dataset_annotation_nms_iou)]
        if self.settings.dataset_annotation_additive:
            arguments.append("--additive")
        elif self.settings.dataset_annotation_replace:
            arguments.append("--replace")
        if self.settings.dataset_annotation_delete_unannotated:
            arguments.append('--delete-unannotated')
        from annotation_suite_ui import effect_arguments, confirm_local_effects
        try:
            arguments.extend(effect_arguments(self.settings,'dataset'))
        except ValueError:
            QMessageBox.warning(self,self.t('Random image effects'),self.t('Choose at least one image effect.'))
            return
        if not confirm_local_effects(self,root):return
        if self.settings.dataset_effects_enabled:
            preview=self._local_effect_preview
            arguments.extend(['--variation-seed',str(preview['seed']),'--expected-source-signature',preview['signature'],
                '--expected-model-sha256',preview['modelSHA256'],
                '--expected-preview-samples',json.dumps({sample['name']:sample['sourceSHA256'] for sample in preview['samples']},separators=(',',':'))])
        self.dataset_worker_status.setText(self.tf("Starting background annotation from {dataset}…", dataset=root.name))
        self.start_dataset_worker(arguments)

    def package_dataset(self) -> None:
        root = self.dataset_root_for_tools()
        self.dataset_worker_status.setText(self.t("Building Ultralytics ZIP…"))
        self.start_dataset_worker(["package", "--dataset", str(root), "--val-split", str(self.settings.dataset_export_val_split)])

    def refresh_readiness(self) -> None:
        provider = self.engine.provider_label(self.settings.provider)
        if self.settings.model_path and resolve_product_path(self.settings.model_path).is_file():
            self.readiness.setText(self.tf("Model selected: {model} · {provider}", model=Path(self.settings.model_path).name, provider=provider))
            model_name = Path(self.settings.model_path).name
        elif self.settings.model_path:
            self.readiness.setText(self.t("Selected model file is unavailable"))
            model_name = self.t("UNAVAILABLE")
        else:
            self.readiness.setText(self.t("Test signal mode: no ONNX model selected"))
            model_name = self.t("Test signal").upper()
        if hasattr(self, "overview_provider"):
            self.overview_provider.setText(provider.upper())
        if hasattr(self, "overview_model"):
            self.overview_model.setText(model_name)
        if hasattr(self, "header_model"):
            self.header_model.setText(self.t("Model") + ": " + model_name)
            self.header_model.setToolTip(self.settings.model_path or model_name)
            self.header_model.hide()
        if hasattr(self,'model_library') and hasattr(self,'model_quick_switch'):
            self.model_quick_switch.blockSignals(True)
            self.model_quick_switch.setCurrentIndex(self.model_library.currentIndex())
            self.model_quick_switch.setToolTip(self.settings.model_path or model_name)
            self.model_quick_switch.blockSignals(False)
        if hasattr(self,'detection_context'):
            self.detection_context.setText(self.t('Model')+': '+model_name+' · '+self.t('Backend')+': '+provider+' · '+self.t('Session mode')+': '+self.t('Observe only' if self.session_mode=='observe' else 'Control'))
        if hasattr(self,'overview_configuration'):
            values=(('Profile',self.settings.profile_name),('Model',model_name),('Backend',provider),
                    ('Session mode',self.t('Observe only' if self.session_mode=='observe' else 'Control')),
                    ('Confidence threshold',f'{self.settings.confidence:.2f}'),
                    ('NMS overlap threshold',f'{self.settings.nms_iou:.2f}' if self.settings.nms_enabled else self.t('Off')),
                    ('Acquisition radius',f'{self.settings.acquisition_radius} px'),
                    ('Retention radius',f'{self.settings.retention_radius} px'))
            self.overview_configuration.setText('\n'.join(self.t(label)+': '+str(value) for label,value in values))
        self.refresh_session_cockpit()

    def refresh_session_cockpit(self) -> None:
        if not hasattr(self, "session_cockpit"):
            return
        palette = appearance_palette(self.ui_appearance, True)

        def display(key: str, text: str, color_role: str) -> None:
            label, _button = self.session_cockpit[key]
            label.setText(text)
            label.setStyleSheet(f"color:{palette.get(color_role, DEFAULT_UI_COLORS[color_role])};")

        model = resolve_product_path(self.settings.model_path) if self.settings.model_path else None
        provider = self.engine.provider_label(self.settings.provider).upper()
        if model is not None and model.is_file():
            display("pipeline", f"{self.t('READY')} / {provider}", "ice")
            self.session_cockpit["pipeline"][0].setToolTip(model.name)
        elif model is not None:
            display("pipeline", self.t("REVIEW / MODEL MISSING"), "pink")
            self.session_cockpit["pipeline"][0].setToolTip(model.name)
        else:
            display("pipeline", f"{self.t('TEST SIGNAL')} / {provider}", "highlight")
            self.session_cockpit["pipeline"][0].setToolTip("")

        width, height = self.settings.region_width, self.settings.region_height
        monitor = self.t("Virtual desktop").upper() if self.settings.monitor == 0 else self.tf("Display {number}", number=self.settings.monitor).upper()
        if width > 0 and height > 0:
            display("frame", f"{width} x {height} / {monitor}", "baby")
        else:
            display("frame", self.t("REVIEW / INVALID REGION"), "pink")

        if self.session_mode == "observe":
            display("output", self.t("OBSERVE / OUTPUT BLOCKED"), "ultraviolet")
        elif self.settings.movement_enabled or (
            not False and (self.settings.event_action_enabled or self.settings.motion_compensation_enabled)
        ):
            display("output", self.t("CONTROL / CONFIGURED"), "highlight")
        else:
            display("output", self.t("CONTROL / OUTPUT OFF"), "steel")

        if initialization_complete() and self.settings.hotkey_calibration.strip():
            display("setup", self.t("READY / BINDING CONFIRMED"), "ice")
        else:
            display("setup", self.t("ACTION REQUIRED"), "pink")

    def profile_comparison_payload(self, path: str) -> dict[str, object]:
        if not path:
            return asdict(self.settings)
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def profile_comparison_value(value: object) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return str(value)

    def compare_profiles(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(self.t("Compare profiles"))
        dialog.resize(820, 620)
        layout = QVBoxLayout(dialog)
        choices = QHBoxLayout()
        left = QComboBox()
        right = QComboBox()
        for combo in (left, right):
            combo.addItem(self.t("Current workspace"), "")
            for path in self.local_profile_files():
                combo.addItem(path.stem, str(path))
            choices.addWidget(combo, 1)
        if right.count() > 1:
            right.setCurrentIndex(1)
        layout.addLayout(choices)
        summary = QLabel()
        summary.setObjectName("signalCaption")
        differences = QPlainTextEdit()
        differences.setReadOnly(True)
        layout.addWidget(summary)
        layout.addWidget(differences, 1)
        close = QPushButton(self.t("Close"))
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

        def refresh() -> None:
            first = self.profile_comparison_payload(str(left.currentData() or ""))
            second = self.profile_comparison_payload(str(right.currentData() or ""))
            rows: list[str] = []
            for field_name in sorted(set(first) | set(second)):
                first_value = first.get(field_name, "<unset>")
                second_value = second.get(field_name, "<unset>")
                if first_value == second_value:
                    continue
                label = field_name.replace("_", " ").title()
                rows.append(
                    f"{label}\n  A  {self.profile_comparison_value(first_value)}\n  B  {self.profile_comparison_value(second_value)}"
                )
            summary.setText(f"{len(rows)} changed settings")
            differences.setPlainText("\n\n".join(rows) if rows else self.t("Profiles match"))

        left.currentIndexChanged.connect(refresh)
        right.currentIndexChanged.connect(refresh)
        refresh()
        dialog.exec()

    def save_profile(self) -> None:
        if self._live_owner is not None:
            self._live_owner.save_native_profile()
            return
        PROFILE_DIRECTORY.mkdir(exist_ok=True)
        self.write_profile(self._active_profile_path or PROFILE_DIRECTORY / "default.json")

    def save_profile_as(self) -> None:
        if self._live_owner is not None:
            self._live_owner.save_native_profile(picker=True)
            return
        PROFILE_DIRECTORY.mkdir(exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save Vapor Lock profile", str(PROFILE_DIRECTORY / "new-profile.json"), "JSON profiles (*.json)")
        if path:
            self.write_profile(Path(path).with_suffix(".json"))

    def write_profile(self, path: Path, *, native_guard=None) -> bool:
        if self._live_owner is not None and native_guard is None:
            owner = self._live_owner
            stamp = owner.stamp()
            native_guard = lambda: (owner.authorize('saveProfile') and owner.stamp() == stamp
                and owner._transition is None)
        temporary = None
        try:
            path = writable_native_path(path)
            if native_guard is not None and native_guard() is not True:
                return False
            payload = asdict(self.settings)
            payload["model_path"] = profile_path(self.settings.model_path)
            payload["dataset_directory"] = profile_path(self.settings.dataset_directory)
            payload["dataset_annotation_model"] = profile_path(self.settings.dataset_annotation_model)
            payload['ultralytics_annotation_model']=profile_path(self.settings.ultralytics_annotation_model)
            payload['ultralytics_output_directory']=profile_path(self.settings.ultralytics_output_directory)
            payload["training_base_model"] = profile_path(self.settings.training_base_model)
            payload["training_dataset"] = profile_path(self.settings.training_dataset)
            for cloud_field in ("cloud_project","cloud_output","cloud_initial_model","cloud_checkpoint"):
                payload[cloud_field]=profile_path(getattr(self.settings,cloud_field))
            payload["motion_compensation_library"] = profile_path(self.settings.motion_compensation_library)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as output:
                temporary = Path(output.name)
                json.dump(payload, output, indent=2, allow_nan=False)
            try:
                if native_guard is not None and native_guard() is not True:
                    return False
                writable_native_path(path)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            self._api_catalog_cache = None
            self._active_profile_path = path.resolve()
            self.remember_active_profile()
            self.dirty = False
            self._profile_change_suffix = None
            self.update_profile_summary()
            self.log("INFO", self.tf('Saved profile: {path}', path=str(path)))
            if hasattr(self, 'profile_cards'):
                self.profile_cards.refresh()
            return True
        except (OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
            QMessageBox.warning(self, "Profile error", str(error))
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def open_profile(self) -> None:
        if self._live_owner is not None:
            self._live_owner.open_native_profile()
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open Vapor Lock profile", str(PROFILE_DIRECTORY), "JSON profiles (*.json)")
        if not path:
            return
        self.load_profile_path(Path(path))

    @tuning_guard
    def load_profile_path(self, path: Path) -> bool:
        if self._live_owner is not None:
            return self._live_owner.load_native_profile(path)
        try:
            replacement = self.prepare_profile_path(path)
            if self.engine.active or self._policy_start_pending or self.xbox_compatible_bridge.preparing:
                self.stop_capture(_profile_transition=True)
            if self.engine.active or self.xbox_compatible_bridge.preparing:
                self.log('WARN', 'Profile retained while capture is stopping.')
                return False
            return self.commit_profile_replacement(replacement, Path(path))
        except (OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
            QMessageBox.warning(self, "Profile error", str(error))
            return False

    def prepare_profile_path(self, path: Path) -> Settings:
        from vlui_profile_snapshot import read_profile_snapshot
        snapshot = read_profile_snapshot(writable_native_path(Path(path)))
        return self.prepare_profile_bytes(snapshot.data)

    def prepare_profile_bytes(self, data: bytes) -> Settings:
        try:
            if type(data) is not bytes or len(data) > 1048576:
                raise ValueError('Profile exceeds the native size limit.')
            def unique_members(items):
                value = {}
                for key, child in items:
                    if key in value:
                        raise ValueError('Duplicate profile member.')
                    value[key] = child
                return value
            def profile_constant(_value):
                raise ValueError('Profile contains a nonfinite number.')
            raw = json.loads(data.decode('utf-8', errors='strict'), object_pairs_hook=unique_members,
                             parse_constant=profile_constant)
            if not isinstance(raw, dict):
                raise ValueError("A profile must contain a settings object.")
            if "model" in raw:
                model = raw.get("model", {})
                capture = raw.get("capture", {})
                tracking = raw.get("tracking", {})
                selection = raw.get("selection", {})
                movement = raw.get("movement", {})
                performance = raw.get("performance", {})
                overlay = raw.get("overlay", {})
                visuals = raw.get("visuals", {})
                dataset = raw.get("dataset", {})
                region = capture.get("region", [0, 0, 640, 640])
                if isinstance(region, str):
                    try:
                        region = [int(value.strip()) for value in region.split(",")]
                    except ValueError:
                        region = [0, 0, 640, 640]
                if not isinstance(region, list) or len(region) != 4:
                    region = [0, 0, 640, 640]
                strategy_names = {"center": 0, "confidence": 1, "largest": 2, "smallest": 3, "movement": 4}
                raw = {
                    "profile_name": raw.get("general", {}).get("profileName", "Default"),
                    "model_path": model.get("path", ""), "provider": model.get("provider", "auto"), "class_filter": model.get("classFilter", ""),
                    "confidence": model.get("confidenceThreshold", 0.55), "nms_iou": model.get("iouThreshold", 0.45),
                    "nms_enabled": raw.get("detection", {}).get("nmsEnabled", True), "nms_class_agnostic": raw.get("detection", {}).get("classAgnosticNms", False),
                    "model_output_format": model.get("outputFormat", "auto"), "max_detections": raw.get("detection", {}).get("maxDetections", 300), "resize_mode": model.get("resizeMode", "letterbox"),
                    "monitor": capture.get("monitor", 0 if capture.get("surface") == "all" else 1), "region_x": region[0], "region_y": region[1], "region_width": region[2], "region_height": region[3], "capture_fps": capture.get("fps", 30),
                    "inference_fps": performance.get("inferenceFps", 30), "frame_skip": performance.get("frameSkipping", 0), "ort_intra_op_threads": performance.get("ortIntraOpThreads", 0), "ort_inter_op_threads": performance.get("ortInterOpThreads", 0), "cuda_graph_replay": performance.get("cudaGraphReplay", True), "cuda_preprocess": performance.get("cudaPreprocess", True),
                    "match_iou": tracking.get("matchIou", 0.30), "max_missed": tracking.get("maxLostFrames", 18), "smoothing": tracking.get("smoothing", 0.35), "box_smoothing": tracking.get("boxSmoothing", 0.25), "anchor_smoothing": tracking.get("anchorSmoothing", 0.65), "anchor_settle_radius": tracking.get("anchorSettleRadius", 2.0), "predict_lost": tracking.get("predictLost", tracking.get("predictionSeconds", 0.05) > 0), "prediction_lead_ms": tracking.get("predictionLeadMs", 50), "prediction_limit_ms": tracking.get("predictionLimitMs", 500), "occlusion_handling": tracking.get("occlusionHandling", True), "occlusion_grace_frames": tracking.get("occlusionGraceFrames", 12), "occlusion_reacquire_radius": tracking.get("occlusionReacquireRadius", 180),
                    "strategy": strategy_names.get(selection.get("strategy", "center"), 0), "acquisition_radius": selection.get("acquisitionRadius", 420),
                    "near_radius": selection.get("nearRadius", 180), "snap_radius": selection.get("snapRadius", 48), "retention_radius": selection.get("retentionRadius", 540), "fov_shape": selection.get("fovShape", "circle"), "switching": selection.get("switchingHysteresis", 0.18), "lock_persistence_ms": selection.get("lockPersistenceMs", 900), "lost_target_timeout": selection.get("lostTargetTimeoutFrames", 12), "minimum_track_age": selection.get("minimumTrackAge", 2), "anchor_mode": selection.get("anchorMode", "center"), "anchor_x": selection.get("anchorOffsetX", 0.0), "anchor_y": selection.get("anchorOffsetY", 0.0), "watchlist": selection.get("watchlist", ""),
                    "movement_enabled": movement.get("enabled", False), "activation_mode": movement.get("activationMode", "hold"),
                    "activation_key": {"RightMouseButton": "right", "LeftShift": "shift", "LeftControl": "control", "Space": "space"}.get(movement.get("activationKey", "RightMouseButton"), "right"),
                    "gain": movement.get("gain", 0.18), "max_step": movement.get("maxStep", 24), "deadzone": movement.get("deadzone", 8), "movement_smoothing": movement.get("smoothing", 0.35), "output_rate": movement.get("outputRate", 120), "invert_x": movement.get("invertX", False), "invert_y": movement.get("invertY", False), "response_curve": movement.get("responseCurve", "linear"),
                    "preview_enabled": visuals.get("enabled", True), "preview_fps": visuals.get("fps", 5), "preview_quality": visuals.get("quality", 80),
                    "overlay_enabled": overlay.get("enabled", True), "overlay_boxes": overlay.get("boxes", True), "overlay_motion": overlay.get("motion", True), "overlay_fov": overlay.get("fov", True), "overlay_segmentation_masks": overlay.get("segmentationMasks", True), "segmentation_mask_limit": overlay.get("segmentationMaskLimit", 8), "segmentation_mask_threshold": overlay.get("segmentationMaskThreshold", 0.50),
                    "dataset_enabled": dataset.get("enabled", False), "dataset_capture_confidence_enabled": dataset.get("captureConfidenceEnabled", False), "dataset_directory": dataset.get("directory", ""), "dataset_min_confidence": dataset.get("minConfidence", 0.50), "dataset_interval_ms": dataset.get("intervalMs", 1000), "dataset_capture_requires_activation": dataset.get("captureRequiresActivation", True), "dataset_jpeg_quality": dataset.get("jpegQuality", 92), "dataset_annotation_model": dataset.get("annotationModel", ""), "dataset_annotation_passes": dataset.get("annotationPasses", 2), "dataset_annotation_confidence": dataset.get("annotationConfidence", 0.55), "dataset_annotation_iou": dataset.get("annotationIou", 0.85), "dataset_annotation_replace": dataset.get("annotationReplace", False), "dataset_export_val_split": dataset.get("exportValSplit", 0.15),
                }
            allowed = {field: raw[field] for field in Settings.__dataclass_fields__ if field in raw}
            # Preserve the old snap exception, now visible and explicitly switchable.
            allowed.setdefault("snap_bypasses_confirmation", True)
            allowed.setdefault("max_speed", float(allowed.get("max_step", 24)) * 60.0)
            from ultralytics_credentials import validate_envelope
            validate_envelope(allowed.get('ultralytics_key_protected', ''))
            for path_field in ("model_path", "dataset_directory", "dataset_annotation_model", "ultralytics_annotation_model", "ultralytics_output_directory", "training_base_model", "training_dataset", "motion_compensation_library", "cloud_project", "cloud_output", "cloud_initial_model", "cloud_checkpoint"):
                if path_field in allowed and allowed[path_field]:
                    allowed[path_field] = str(resolve_product_path(str(allowed[path_field])))
            allowed["class_policies"] = validated_policies(allowed.get("class_policies", []))
            defaults = Settings()
            for name, value in allowed.items():
                expected = getattr(defaults, name)
                valid = (type(value) is type(expected) or
                         (type(expected) is float and type(value) in (float, int)))
                if (not valid or (type(value) is int and abs(value) > 2**53-1)
                        or (type(value) in (int, float) and not math.isfinite(value))):
                    raise ValueError(f"Invalid profile value for {name}.")
                if name in SETTING_ENUMS and value not in SETTING_ENUMS[name]:
                    raise ValueError(f"Invalid profile choice for {name}.")
            replacement = Settings(**allowed)
            from training_security import load_extra_arguments
            load_extra_arguments(replacement.training_extra_arguments)
            if any(not .01 <= getattr(replacement,name) <= .99 for name in ('dataset_annotation_nms_iou','ultralytics_annotation_nms_iou')):
                raise ValueError('Invalid annotation NMS threshold.')
            from annotation_suite_ui import effect_options
            effect_options(replacement,'dataset').validate()
            effect_options(replacement,'ultralytics').validate()
            if (not 1<=replacement.ultralytics_annotation_passes<=10 or not 1<=replacement.ultralytics_image_limit<=10000
                    or not .01<=replacement.ultralytics_annotation_confidence<=.99 or not .05<=replacement.ultralytics_annotation_iou<=.99):
                raise ValueError('Invalid Ultralytics profile settings.')
            if not 1 <= replacement.capture_fps <= 2**53-1:
                raise ValueError('Profile capture rate must be a positive safe integer.')
            validated_native_settings(asdict(replacement), tuple((name, str(spec.type))
                for name, spec in Settings.__dataclass_fields__.items()))
            return replacement
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            raise

    @tuning_guard
    def commit_profile_replacement(self, replacement: Settings, path: Path | None) -> bool:
        if self.model_probe_busy(): return False
        if self.engine.active or self.xbox_compatible_bridge.preparing or self._policy_start_pending:
            return False
        worker=getattr(self,'_dataset_worker',None)
        if worker is not None and worker.state()!=QProcess.ProcessState.NotRunning:
            self.log('WARN','Stop the background dataset task before switching profiles.')
            return False
        from cloud_settings import busy as cloud_busy
        if cloud_busy(self):
            self.log('WARN','Stop .cloud training before switching profiles.');return False
        if self._model_selection is not None: self._model_selection.invalidate()
        self.settings = replacement
        self._model_selection_status_copy = None
        self._ultralytics_preview = None
        self._api_catalog_cache = None
        self.clear_tuning_undo()
        self._active_profile_path = path.resolve() if path is not None else None
        self.remember_active_profile()
        self.dirty = path is None
        self._profile_change_suffix = 'unsaved changes' if path is None else None
        self.update_profile_summary()
        self.rebuild_pages()
        self.refresh_readiness()
        self.update_desktop_overlay()
        self.sync_local_api()
        self.refresh_local_api_snapshot()
        if self._live_owner is not None:
            self._live_owner.native_changed()
        return True

    @tuning_guard
    def reset_settings(self) -> None:
        if self.model_probe_busy(): return
        if self._live_owner is not None:
            self._live_owner.reset_native_settings()
            return
        if self.load_preset_profile():
            self.log("INFO", "Restored the preset default profile.")
            return
        if self.engine.active:
            self.stop_capture()
        self.settings = Settings()
        self.clear_tuning_undo()
        self._active_profile_path = None
        self.dirty = True
        self._profile_change_suffix = "unsaved changes"
        self.update_profile_summary()
        self.rebuild_pages()
        self.refresh_readiness()
        self.update_desktop_overlay()
        self.sync_local_api()
        self.refresh_local_api_snapshot()

    def show_model_start_failure(self) -> None:
        self.show_model_selection_status(
            'Could not start the selected model or provider. The session remains stopped. See Status & Diagnostics for details.', {})

    def update_model_start_feedback(self, state: str) -> None:
        previous = getattr(self, '_last_engine_state', '')
        if state.startswith('Starting'):
            self.show_model_selection_status('Loading the selected model…', {})
        elif state.startswith('Running'):
            self._model_selection_status_copy = None
            self.render_model_selection_status()
        elif state in {'Stopping', 'Stopped'} and previous.startswith('Starting'):
            if self.engine.intentional_stop:
                self._model_selection_status_copy = None
                self.render_model_selection_status()
            elif state == 'Stopped':
                self.show_model_start_failure()

    def apply_state(self, state: str) -> None:
        self._crosshair_telemetry=None
        from crosshair_ui import refresh_status as crosshair_status
        crosshair_status(self)
        self.update_model_start_feedback(state)
        self._last_engine_state = state
        if not state.startswith('Running'):
            self._cpu_placement_observation=None
            if hasattr(self,'refresh_cpu_placement_status'):self.refresh_cpu_placement_status()
            self._inference_device_observation=None
        if hasattr(self,'inference_gpu_controls'):self.inference_gpu_controls.refresh_actual()
        localized_state = self.localized_engine_state(state)
        if hasattr(self,'hud_state'):self.hud_state.setText(localized_state)
        self.status.setText(localized_state.upper())
        status_state = (
            "running" if state.startswith("Running")
            else "stopping" if state in {"Starting", "Stopping"}
            else "error" if "error" in state.lower() or "failed" in state.lower()
            else "ready"
        )
        self.status.setProperty("state", status_state)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        active = state not in {"Stopped", "READY"}
        self.set_start_enabled(not active)
        self.stop_button.setEnabled(active and state != "Stopping")
        if hasattr(self, "session_mode_choice"):
            self.session_mode_choice.setEnabled(not active)
        self.metric_state.setText(localized_state)
        self.diag_state.setText(localized_state)
        if state == "Stopped":
            self.clear_preview()
            if self._calibration_active:
                self.cancel_motion_compensation_calibration("Capture stopped before calibration completed.")
            self.hud_rate.setText("--")
            self.hud_inference.setText("--")
            self.hud_tracks.setText("0")
            self.hud_output.setText(self.t("Idle").upper())
            # These cards describe the current session, unlike the retained
            # report cache and history. A stopped session has no live sample.
            for label in (self.metric_fps, self.diag_fps, self.diag_latency,
                          self.diag_preprocess, self.diag_model, self.diag_freshness):
                label.setText("—")
            for label in (self.metric_tracks, self.diag_tracks):
                label.setText("0")
            for label in (self.metric_target, self.diag_target):
                label.setText(self.t("No lock"))
        if state == "Stopped":
            self._last_output_data = None
            self.output_diagnostic_label.setText(output_diagnostics(None))
            self._pending_controller_start = None
            self.xbox_compatible_bridge.stop_session()
            self._last_selected_track = 0
            self._last_manual_lock = False
            if hasattr(self, "manual_lock_status"):
                self.manual_lock_status.setText("Manual target lock is waiting for a live target.")
        elif state.startswith("Running"):
            self.clear_performance_history()
            self.session_report_cache.reset()
        self.refresh_session_cockpit()
        self.update_desktop_overlay()
        self.refresh_local_api_snapshot()
        self.log("INFO", state)
        self.queue_telemetry_layout()

    def apply_telemetry(self, data: dict) -> None:
        from crosshair_ui import refresh_status as crosshair_status
        crosshair_status(self,data)
        from cpu_placement import observe as observe_cpu
        observe_cpu(self,data)
        from batch6_ui import observe_device
        observe_device(self,data)
        self.apply_collection_status(data)
        self.session_report_cache.record(data, self.engine.active_provider, self.engine._launch_generation)
        self._last_output_data = data
        self._pipeline_received_at = time.monotonic()
        self._update_pipeline_snapshot()
        if hasattr(self, 'pipeline_diagnostics'): self.pipeline_diagnostics.setText(self.pipeline_diagnostic_text())
        self._collect_compensation_sample(data)
        self.output_diagnostic_label.setText(output_diagnostics(data))
        if not False and self.performance_history.add(data):
            self.performance_chart.update()
            self.performance_summary.setText(self.performance_history.summary())
        fps = f"{float(data.get('fps', 0.0)):.1f} fps"
        tracks = str(data.get("tracks", 0))
        selected_track = int(data.get("selected_track", 0))
        selected_missed = int(data.get("selected_missed", 0))
        manual_lock = bool(data.get("manual_lock", False))
        selected = "No lock" if selected_track == 0 else f"Track #{selected_track} · class {int(data.get('selected_class', -1))}"
        if selected_track and selected_missed:
            selected += f" · recovering ({selected_missed} missed)"
        elif selected_track and manual_lock:
            selected += " · pinned"
        inference = f"{float(data.get('inference_ms', 0.0)):.1f} ms"
        preprocess = f"{float(data.get('preprocess_ms', 0.0)):.1f} ms"
        model = f"{float(data.get('model_ms', 0.0)):.1f} ms"
        running_state = f"{self.t('Running')} - {self.engine.provider_label(self.engine.active_provider)}"
        self.metric_state.setText(running_state)
        self.metric_fps.setText(fps)
        self.metric_tracks.setText(tracks)
        self.metric_target.setText(selected)
        self.hud_rate.setText(fps)
        self.hud_inference.setText(inference)
        self.hud_tracks.setText(tracks)
        self.hud_output.setText(self.t("Active").upper() if bool(data.get("output_active", False)) else self.t("Idle").upper())
        if hasattr(self, "policy_live_track"):
            self.policy_live_track.setText(str(selected_track) if selected_track else "--")
        if hasattr(self, "policy_live_confidence"):
            confidence = float(data.get("selected_confidence", 0.0))
            self.policy_live_confidence.setText(f"{confidence:.2f}" if selected_track else "--")
        self.diag_state.setText(running_state)
        self.diag_fps.setText(fps)
        self.diag_latency.setText(inference)
        self.diag_tracks.setText(tracks)
        self.diag_preprocess.setText(preprocess)
        self.diag_model.setText(model)
        self.diag_target.setText(selected)
        self._last_selected_track = selected_track
        self._last_manual_lock = manual_lock
        self.refresh_preview_status()
        self.desktop_overlay.set_runtime_metrics(
            self.engine.provider_label(self.engine.active_provider),
            data.get("inference_fps"),
            float(data.get("capture_ms", 0.0)),
            float(data.get("inference_ms", 0.0)),
            float(data.get("tracking_ms", 0.0)),
        )
        if hasattr(self, "manual_lock_status"):
            if not self.settings.manual_target_lock_enabled:
                self.manual_lock_status.setText("Manual target lock is disabled for the next session.")
            elif not selected_track:
                self.manual_lock_status.setText("No active target is available to pin yet.")
            elif selected_missed:
                self.manual_lock_status.setText(f"Track #{selected_track} is being retained through a detection gap ({selected_missed} missed frame{'s' if selected_missed != 1 else ''}).")
            elif manual_lock:
                self.manual_lock_status.setText(f"Track #{selected_track} is pinned. Use the configured input or target action to release it.")
            else:
                self.manual_lock_status.setText(f"Track #{selected_track} is selected automatically. Use the configured input or target action to pin it.")
        if hasattr(self, "controller_status"):
            controller_needed = (
                self.settings.activation_source != "keyboard"
                or (self.settings.manual_target_lock_enabled and self.settings.manual_lock_source != "keyboard")
                or (
                    self.settings.output_transport == "virtual-xbox"
                    and self.settings.virtual_controller_mode == "slot0-proxy"
                )
            )
            if not controller_needed:
                self.controller_status.setText("Keyboard activation is active. No controller bindings are used for this session.")
            elif bool(data.get("controller_connected", False)):
                self.controller_status.setText("Xbox controller connected. Vapor Lock is listening for its selected button or trigger bindings only.")
            else:
                self.controller_status.setText("No selected Xbox controller is connected. Keyboard activation remains available when enabled.")
        if hasattr(self, "output_transport_status") and data.get("output_transport") == "virtual-xbox":
            mode = str(data.get("virtual_controller_mode", self.settings.virtual_controller_mode))
            virtual_slot = int(data.get("virtual_controller_slot", 0) or 0)
            physical_slot = int(data.get("physical_controller_slot", 0) or 0)
            identity = "Xbox One-compatible"
            if mode == "slot0-proxy":
                if bool(data.get("controller_proxy_ready", False)):
                    self.output_transport_status.setText(
                        f"{identity} primary-controller bridge is active: Vapor Lock is Controller {virtual_slot}; the physical controller is Controller {physical_slot}. Physical buttons, triggers, and sticks are mirrored through the virtual controller, while deliberate movement on the chosen stick takes priority."
                    )
                elif virtual_slot != 1:
                    slot_text = f"Controller {virtual_slot}" if virtual_slot else "an unavailable slot"
                    self.output_transport_status.setText(
                        f"{identity} bridge is not active: Vapor Lock received {slot_text}, but controller-only camera software normally reads Controller 1. Stop capture, close the camera application, disconnect or turn off the physical controller, start capture to reserve Controller 1 for Vapor Lock, reconnect the physical controller, then reopen the camera application."
                    )
                elif not physical_slot:
                    self.output_transport_status.setText(
                        f"{identity} bridge has reserved Controller 1. Reconnect the physical controller now; it will be mirrored through the virtual controller as soon as Windows assigns it another slot."
                    )
                else:
                    self.output_transport_status.setText(
                        f"{identity} bridge is Controller 1 and the physical controller is Controller {physical_slot}. Waiting for a stable controller report before proxying input."
                    )
            else:
                    self.output_transport_status.setText(
                        f"{identity} output is connected for this session. It is a separate controller; Vapor Lock does not take over the physical controller."
                    )
        self.refresh_local_api_snapshot()

    def update_desktop_overlay(self, session_active: bool | None = None) -> None:
        if session_active is None:
            session_active = self.engine.overlay_metadata_active
        self.desktop_overlay.apply_settings(self.settings, session_active)

    def apply_live_setting_result(self, accepted: bool) -> None:
        self._profile_change_suffix = 'live changes applied' if accepted else 'live change not applied'
        self.update_profile_summary()

    def apply_observations(self,payload,launch,stream,epoch,accepted):
        if hasattr(self,'observation_sharing'):self.observation_sharing.accept(payload,launch,stream,epoch,accepted)

    def apply_desktop_overlay(self, payload: dict) -> None:
        if not payload and hasattr(self,'observation_sharing'):self.observation_sharing.cache.clear()
        if False or not self.settings.desktop_overlay_enabled:
            return
        if self.desktop_overlay._session_active != self.engine.overlay_metadata_active:
            self.update_desktop_overlay()
        self.desktop_overlay.set_payload(payload)

    def refresh_preview_status(self) -> None:
        if not hasattr(self, 'policy_preview_status'):
            return
        caption = 'Live annotated preview'
        live = False
        if not self.engine.active or self.engine.intentional_stop:
            text = 'Stopped'
        elif not self.engine.session_running or self._last_preview_frame.isNull():
            text = 'Waiting for fresh frames' if getattr(self, '_preview_stale', False) else 'Waiting for capture'
        else:
            age = time.monotonic() - self._preview_observed_at
            cadence = float(self.settings.preview_fps)
            native_budget = float(self.settings.output_max_observation_age_ms) / 1000.0
            native_budget = max(.000001,min(native_budget,getattr(self,"_preview_reference_deadline",math.inf)-self._preview_observed_at))
            if not all(math.isfinite(value) for value in (age, cadence, native_budget)) or age < 0 or cadence <= 0 or native_budget <= 0:
                self.clear_preview(stale=True)
                return
            grace = min(2.0, max(.5, 2.0 / cadence))
            live = age < min(grace, native_budget)
            if age >= grace:
                self.clear_preview(stale=True)
                return
            if age >= native_budget:
                caption = 'Snapshot — waiting for fresh frames'
                text = 'Waiting for fresh frames'
                for attribute in ('policy_live_track', 'policy_live_confidence'):
                    if hasattr(self, attribute):getattr(self, attribute).setText('--')
                remaining = grace-age
            elif self._last_selected_track:
                text = 'Target pinned' if self._last_manual_lock else 'Selection stable'
                remaining = min(grace,native_budget)-age
            else:
                text = 'Scanning for a target'
                remaining = min(grace,native_budget)-age
            self._preview_expiry_timer.start(max(1, math.ceil(remaining*1000)))
        if not live:
            for attribute in ('policy_live_track', 'policy_live_confidence'):
                if hasattr(self, attribute):getattr(self, attribute).setText('--')
        if self._last_preview_frame.isNull():
            placeholder = text if self.engine.active and not self.engine.intentional_stop else 'Start capture to receive the live annotated preview'
            for attribute in ('preview_view', 'policy_preview_view'):
                view = getattr(self, attribute, None)
                if view is not None and view.text() != self.t(placeholder):view.setText(self.t(placeholder))
        caption = self.t(caption)
        if hasattr(self, 'policy_preview_caption') and self.policy_preview_caption.text() != caption:
            self.policy_preview_caption.setText(caption)
        text = self.t(text)
        if self.policy_preview_status.text() != text:
            self.policy_preview_status.setText(text)

    def clear_preview(self, stale: bool = False) -> None:
        self._last_preview_frame = QImage()
        self._preview_observed_at = 0.0
        self._preview_stale = stale
        self._preview_expiry_timer.stop()
        for attribute in ("preview_view", "policy_preview_view"):
            if hasattr(self, attribute):
                getattr(self, attribute).set_frame(QImage())
        for attribute in ('policy_live_track', 'policy_live_confidence'):
            if hasattr(self, attribute):
                getattr(self, attribute).setText('--')
        self.refresh_preview_status()

    def apply_preview(self, jpeg: bytes, width: int, height: int) -> None:
        image = QImage.fromData(jpeg, "JPG")
        if image.isNull():
            self.log("WARN", "The native engine sent a preview image that Qt could not decode.")
            return
        self._last_preview_frame = image
        self._preview_observed_at = self.engine._preview_observed_at
        self._preview_reference_deadline = getattr(self.engine,"_preview_reference_deadline",math.inf)
        self._preview_stale = False
        for attribute in ("preview_view", "policy_preview_view"):
            if hasattr(self, attribute):
                view = getattr(self, attribute)
                view.set_frame(image)
                view.setToolTip(f"Native preview: {width} x {height}")
        self.refresh_preview_status()

    def refresh_dataset_destination(self) -> None:
        if not hasattr(self, 'dataset_destination_status'):
            return
        try:
            text = self.tf('Current dataset folder: {path}', path=str(self.dataset_root_for_tools()))
        except (OSError, ValueError):
            text = self.t('Collection storage is unavailable. Review the dataset folder.')
        receipt = getattr(self, '_last_dataset_receipt', None)
        if receipt:
            text += '\n' + self.tf('Last saved image: {path}', path=receipt)
        self.dataset_destination_status.setText(text)
        if hasattr(self,'annotation_source_path'):self.annotation_source_path.setText(text)

    def apply_dataset(self, data: dict) -> None:
        if type(data) is not dict:
            return
        samples, path = data.get('samples'), data.get('path')
        if type(samples) is not int or not 0 <= samples <= 2**53-1:
            return
        if type(path) is str and 0 < len(path) <= 32768 and not any(c in path for c in '\r\n\t\x00'):
            self._last_dataset_receipt = path
            self.refresh_dataset_destination()
        if hasattr(self, "dataset_status") and not hasattr(self, "_last_collection_data"):
            self.dataset_status.setText(self.tf("Saved source images: {saved}.", saved=samples))
        self.log("INFO", f"Saved source training image {samples}" + (f': {path}' if path == getattr(self, '_last_dataset_receipt', None) else ''))

    def apply_collection_status(self, data: dict) -> None:
        self.refresh_dataset_destination()
        if not hasattr(self, "dataset_status"):
            return
        unknown = self.t("Collection status is temporarily unavailable.")
        booleans = ("collection_pending", "collection_encoding", "collection_root_ready",
            "collection_cleanup_pending", "collection_retirement_pending", "collection_qualification_unavailable")
        counts = ("collection_saved", "collection_dropped", "collection_owned_bgra_bytes", "collection_highwater_bgra_bytes")
        errors = ("none", "invalid-configuration", "frame-limit", "file-cleanup-pending",
            "root-cleanup-pending", "root-unavailable", "sample-not-committed", "writer-unavailable")
        if (type(data) is not dict or data.get("collection_status_known") is not True
                or any(type(data.get(key)) is not bool for key in booleans)
                or any(type(data.get(key)) is not int or not 0 <= data[key] <= 2**53-1 for key in counts)
                or type(data.get("collection_error")) is not str or data["collection_error"] not in errors):
            self.dataset_status.setText(unknown)
            self._last_collection_data = {"collection_status_known": False}
            return
        self._last_collection_data = {key:data[key] for key in (*booleans, *counts, "collection_error")}
        self._last_collection_data["collection_status_known"] = True
        text = self.tf("Saved images {saved} · Skipped frames {dropped} · Queue {pending}/1 · Encoding {encoding}/1",
            saved=data["collection_saved"], dropped=data["collection_dropped"],
            pending=int(data["collection_pending"]), encoding=int(data["collection_encoding"]))
        saved_bytes = data.get('collection_saved_bytes')
        if type(saved_bytes) is int and 0 <= saved_bytes <= 2**53-1:
            text += '\n' + self.tf('New JPEG storage this session: {mib} MiB.', mib=f'{saved_bytes / 1048576:.2f}')
        limit_reason = data.get('collection_limit_reason')
        if limit_reason in ('images', 'storage'):
            text += '\n' + self.t('Image limit reached. Capture continues.' if limit_reason == 'images'
                else 'Next image would exceed the storage limit. Capture continues.')
            text += '\n' + self.t('Raise or disable the limit to allow saving when capture conditions are met.')
        if data["collection_cleanup_pending"] or data["collection_retirement_pending"]:
            text += "\n" + self.t("Collection is still finishing cleanup.")
        elif data["collection_error"] == "invalid-configuration":
            text += "\n" + self.t("Capture settings are invalid. Review confidence, interval and quality.")
        elif data["collection_error"] == "root-unavailable":
            text += "\n" + self.t("Collection storage is unavailable. Review the dataset folder.")
        elif data["collection_error"] != "none":
            text += "\n" + self.t("Collection sample could not be saved.")
        elif data["collection_qualification_unavailable"]:
            text += "\n" + self.t("Capture confidence is unavailable for this model or frame.")
        self.dataset_status.setText(text)

    def log(self, level: str, message: str) -> None:
        if not hasattr(self, "log_view"):
            return
        self.log_view.appendPlainText(f"{level:<6} {message}")

    @disposable_deny('clipboard')
    def copy_diagnostics(self) -> None:
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self.log("INFO", "Diagnostics copied to clipboard")

    def session_report_payload(self) -> dict[str, object]:
        metric_names = {
            "fps", "capture_ms", "inference_ms", "preprocess_ms", "model_ms", "postprocess_ms",
            "capture_total", "inference_total", "synthetic_inference_total",
            "inference_explicit_skip_total", "inference_rate_limited_total", "capture_no_fresh_poll_total",
            "tracking_ms", "detections", "tracks", "selected_track", "selected_class", "selected_missed",
            "output_active", "output_reason", "output_transport", "output_sent_total", "output_failed_total",
            "observe_only", "motion_compensation_active", "event_action_reason",
        }
        metrics = {
            key: value for key, value in (self._last_output_data or {}).items()
            if key in metric_names and isinstance(value, (str, int, float, bool))
        }
        recorded = self.session_report_cache.snapshot()
        if recorded['context']:
            metrics = recorded['metrics']
        samples = list(self.performance_history.samples)
        performance: dict[str, object] = {"sample_count": len(samples)}
        if samples:
            rows = [sample[1] for sample in samples]
            for index, name in enumerate(PerformanceHistory.FIELDS):
                performance[name] = {
                    "average_ms": round(sum(row[index] for row in rows) / len(rows), 4),
                    "peak_ms": round(max(row[index] for row in rows), 4),
                }
        return {
            "schema_version": 2,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "product": {"version": PRODUCT_VERSION, "edition": EDITION},
            "session": {
                "state": self._last_engine_state,
                "mode": self.session_mode,
                "provider": self.engine.active_provider or self.settings.provider,
            },
            "profile": {"name": self.settings.profile_name, "settings": self.api_safe_settings()},
            "latest_metrics": metrics,
            "measurement_session": dict(recorded['context'],
                live=bool(self.engine.session_running and recorded['context'].get('generation') == self.engine._launch_generation),
                profile_settings_are_current=True),
            "performance": performance,
        }

    @disposable_deny('session-report-export')
    def export_support_bundle(self) -> None:
        from support_bundle import open_dialog
        open_dialog(self, {'version': PRODUCT_VERSION, 'edition': EDITION}, ROOT)

    @tuning_guard
    def edit_model_classes(self) -> None:
        from class_editor import open_editor
        open_editor(self)

    @disposable_deny('session-report-export')
    def save_session_report(self) -> None:
        suggested = ROOT / f"VaporLock-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Save Vapor Lock session report",
            str(suggested),
            "JSON reports (*.json)",
        )
        if not destination:
            return
        path = Path(destination).with_suffix(".json")
        try:
            path.write_text(json.dumps(self.session_report_payload(), indent=2), encoding="utf-8")
            self.log("INFO", f"Session report saved: {path.name}")
        except OSError as error:
            QMessageBox.warning(self, "Session report", f"The report could not be saved: {error}")







    def closeEvent(self, event) -> None:
        cloud_workspace=getattr(getattr(self,'_cloud_suite',None),'workspace',None)
        if cloud_workspace is not None and not self._native_closing and not cloud_workspace.allow_close():
            event.ignore();return
        if hasattr(self,'annotation_assist'):self.annotation_assist.cancel()
        if hasattr(self,'observation_sharing'):self.observation_sharing.change(False)
        installer_task = getattr(self, '_installer_task', None)
        if installer_task is not None:
            installer_task.cancel()
        self.cancel_model_probes()
        if self._live_close is None:
            from vlui_native_close import NativeCloseOwner
            self._live_close = NativeCloseOwner(self)
        self._benchmark_active = False
        if self._benchmark_timer:
            self._benchmark_timer.stop()
        self._live_close.handle(event)










def main() -> None:
    live_resources, interface_unavailable = None, False
    dependency_check = len(sys.argv) == 3 and sys.argv[1] == "--verify-qt-startup"
    try:
        from vlui_resource_root import native_manifest_identity
        from vlui_fixed_resources import load_fixed_resources
        from vlui_qt_host import register_fixed_scheme
        live_resources = load_fixed_resources(reviewed_manifest_sha256=native_manifest_identity())
        register_fixed_scheme()
    except (OSError, ValueError, ImportError):
        live_resources, interface_unavailable = None, True
    if dependency_check and interface_unavailable:
        raise RuntimeError("The bundled HTML interface failed resource admission.")
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    app = QApplication(sys.argv)
    from control_fit import install_control_fit
    install_control_fit()
    apply_qt_language(app, load_preferred_language())
    app._vaporlock_dialog_localizer = DialogLocalizer(app)
    app.setApplicationName("Vapor Lock")
    icon_path = product_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(APP_STYLESHEET)
    if len(sys.argv) == 3 and sys.argv[1] == "--verify-qt-startup":
        from startup_verification import verify_qt_startup
        verify_qt_startup(app, Path(sys.argv[2]), EDITION, PRODUCT_VERSION)
        return
    from single_instance import InstanceGuard, focus_existing
    try:
        instance = InstanceGuard(ROOT, EDITION)
    except OSError:
        QMessageBox.warning(None, 'Vapor Lock', translate_text('Vapor Lock could not establish window ownership. Close the other window or try again after it exits.', load_preferred_language()))
        return
    if not instance.primary:
        instance.close()
        return
    focus_timer = QTimer(app)
    focus_timer.setInterval(200)
    focus_timer.timeout.connect(lambda: focus_existing(app, getattr(app, '_vaporlock_main_window', None)) if instance.take_focus_request() else None)
    focus_timer.start()
    app.aboutToQuit.connect(instance.close)
    window = ControlCenter(live_resources=live_resources)
    app._vaporlock_main_window = window
    if interface_unavailable:
        window.log("WARN", window.t("The live interface is unavailable. Use native controls or reload it."))
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    # Native adapters must import this exact entrypoint module.
    sys.modules["vapor_lock_gui"] = sys.modules[__name__]
    main()
