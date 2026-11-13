"""The desktop's grouped settings editor."""

from dataclasses import fields
import json

from PySide6.QtCore import QLocale, QTimer, Qt, Signal
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from desktop.settings import PROFILE_PATH_FIELDS, SETTING_ENUMS, Settings


QUICK_FIELDS = (
    'model_path', 'provider', 'confidence', 'monitor', 'capture_fps',
    'inference_fps', 'preview_enabled',
)
LABELS = {
    'model_path': 'Model file', 'provider': 'Inference backend',
    'monitor': 'Display', 'confidence': 'Confidence threshold',
    'capture_fps': 'Capture FPS limit', 'inference_fps': 'Inference FPS limit',
    'nms_iou': 'NMS overlap threshold', 'ort_intra_op_threads': 'ONNX intra-op threads',
    'ort_inter_op_threads': 'ONNX inter-op threads',
    'dataset_directory': 'Dataset folder', 'preview_enabled': 'Show preview',
}
OPTION_LABELS = {
    'provider': {'auto': 'Automatic', 'dml': 'DirectML', 'cuda': 'CUDA',
                 'trt': 'TensorRT RTX', 'cpu': 'CPU'},
}
GROUPS = ('Capture', 'Models', 'Tracking', 'Output', 'Preview', 'Data & training', 'Shortcuts & sharing')


def label_for(name):
    return LABELS.get(name, name.replace('_', ' ').capitalize())


def group_for(name):
    if name.startswith(('capture_', 'region_')) or name == 'monitor':
        return 'Capture'
    if name.startswith(('model_', 'nms_', 'ort_', 'inference_', 'cuda_')) or name in (
        'provider', 'confidence', 'class_filter', 'resize_mode', 'max_detections', 'frame_skip',
    ):
        return 'Models'
    if name.startswith(('preview_', 'overlay_', 'desktop_overlay_', 'segmentation_mask_')):
        return 'Preview'
    if name.startswith(('dataset_', 'cloud_', 'training_', 'ultralytics_')):
        return 'Data & training'
    if name.startswith(('hotkey_', 'local_api_')):
        return 'Shortcuts & sharing'
    if name.startswith((
        'movement_', 'activation_', 'controller_', 'virtual_', 'output_', 'event_action_',
        'hardware_', 'kmbox_', 'motion_compensation_', 'input_units_', 'assistance_', 'invert_',
    )) or name in ('gain', 'max_step', 'deadzone', 'max_speed', 'response_curve'):
        return 'Output'
    return 'Tracking'


class SettingRow(QFrame):
    changed = Signal()

    def __init__(self, name, default, paths):
        super().__init__()
        self.name, self.default, self.paths = name, default, paths
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        caption = QLabel(label_for(name))
        caption.setObjectName('fieldLabel')
        caption.setWordWrap(True)
        layout.addWidget(caption, 2)
        if name in SETTING_ENUMS:
            self.editor = QComboBox()
            for value in SETTING_ENUMS[name]:
                title = OPTION_LABELS.get(name, {}).get(value, str(value).replace('_', ' '))
                self.editor.addItem(title, value)
            self.editor.currentIndexChanged.connect(self.changed)
        elif name == 'monitor':
            self.editor = QComboBox()
            self.editor.addItem('All displays', 0)
            try:
                from batch4_geometry import enumerate_displays

                displays = enumerate_displays()
            except OSError:
                displays = ()
            for display in displays:
                self.editor.addItem(f'{display.index}  |  {display.width} × {display.height}', display.index)
            if not displays:
                for index in range(1, 33):
                    self.editor.addItem(str(index), index)
            self.editor.currentIndexChanged.connect(self.changed)
        elif isinstance(default, bool):
            self.editor = QCheckBox('Enabled')
            self.editor.toggled.connect(self.changed)
        elif isinstance(default, (list, dict)):
            self.editor = QPlainTextEdit()
            self.editor.setFixedHeight(76)
            self.editor.textChanged.connect(self.changed)
        else:
            self.editor = QLineEdit()
            if isinstance(default, int):
                self.editor.setValidator(QIntValidator(-2147483648, 2147483647, self.editor))
            elif isinstance(default, float):
                validator = QDoubleValidator(self.editor)
                validator.setLocale(QLocale.c())
                self.editor.setValidator(validator)
            elif name.endswith(('_protected', '_device_code')):
                self.editor.setEchoMode(QLineEdit.EchoMode.Password)
            self.editor.textEdited.connect(self.changed)
        self.editor.setAccessibleName(caption.text())
        caption.setBuddy(self.editor)
        layout.addWidget(self.editor, 3)
        if name in PROFILE_PATH_FIELDS:
            browse = QPushButton('Browse')
            browse.setAccessibleName(f'Browse {caption.text()}')
            browse.clicked.connect(self.browse)
            layout.addWidget(browse)
        self.set_value(default)

    def browse(self):
        options = QFileDialog.Option.DontUseNativeDialog
        if self.name in ('dataset_directory', 'ultralytics_output_directory', 'cloud_project', 'cloud_output'):
            path = QFileDialog.getExistingDirectory(self, label_for(self.name), str(self.paths.root), options)
        else:
            file_filter = 'Models (*.onnx *.cloud);;All files (*)' if self.name == 'model_path' else 'All files (*)'
            path, _ = QFileDialog.getOpenFileName(self, label_for(self.name), str(self.paths.root), file_filter,
                                                  options=options)
        if path:
            self.editor.setText(self.paths.portable(path))
            self.changed.emit()

    def set_value(self, value):
        editor = self.editor
        previous = editor.blockSignals(True)
        try:
            if isinstance(editor, QComboBox):
                index = editor.findData(value)
                if index < 0 and self.name == 'monitor':
                    editor.addItem(f'{value}  |  Unavailable', value)
                    index = editor.count() - 1
                editor.setCurrentIndex(index)
            elif isinstance(editor, QCheckBox):
                editor.setChecked(value)
            elif isinstance(editor, QPlainTextEdit):
                editor.setPlainText(json.dumps(value, ensure_ascii=False))
            else:
                editor.setText(str(value))
        finally:
            editor.blockSignals(previous)

    def value(self):
        try:
            if isinstance(self.editor, QComboBox):
                return self.editor.currentData()
            if isinstance(self.editor, QCheckBox):
                return self.editor.isChecked()
            if isinstance(self.editor, QPlainTextEdit):
                return json.loads(self.editor.toPlainText())
            text = self.editor.text()
            if isinstance(self.default, (int, float)):
                return type(self.default)(text)
            return text
        except (ValueError, TypeError) as error:
            raise ValueError(f'{label_for(self.name)} has an invalid value.') from error


class SettingsPanel(QWidget):
    changed = Signal()

    def __init__(self, paths, settings):
        super().__init__()
        self.paths = paths
        self.settings = settings
        self.defaults = Settings()
        self.names = tuple(field.name for field in fields(Settings) if field.name != 'profile_name')
        self.rows = {}
        self.invalid = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Find a setting')
        self.search.setAccessibleName('Find a setting')
        self.search.setClearButtonEnabled(True)
        self.group = QComboBox()
        self.group.setAccessibleName('Settings category')
        self.group.addItems(GROUPS)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.group)
        layout.addLayout(filters)
        self.scroller = QScrollArea()
        self.scroller.setWidgetResizable(True)
        self.scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroller, 1)
        self.search_delay = QTimer(self)
        self.search_delay.setSingleShot(True)
        self.search_delay.setInterval(90)
        self.search_delay.timeout.connect(self.rebuild)
        self.search.textChanged.connect(lambda: self.search_delay.start())
        self.group.currentTextChanged.connect(self.rebuild)

    def set_settings(self, settings):
        self.settings = settings
        self.invalid.clear()
        if self.rows:
            self.rebuild()

    def rebuild(self):
        self.search_delay.stop()
        old = self.scroller.takeWidget()
        if old is not None:
            old.deleteLater()
        body = QWidget()
        body.setObjectName('settingsBody')
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 8, 12, 8)
        layout.setSpacing(3)
        query = self.search.text().casefold().replace('_', ' ').split()
        self.group.setEnabled(not query)
        self.rows = {}
        for name in self.names:
            category = group_for(name)
            searchable = f'{category} {name.replace("_", " ")} {label_for(name)}'.casefold()
            if query and not all(token in searchable for token in query):
                continue
            if not query and category != self.group.currentText():
                continue
            row = SettingRow(name, getattr(self.defaults, name), self.paths)
            row.set_value(getattr(self.settings, name))
            if name in self.invalid:
                if isinstance(row.editor, QPlainTextEdit):
                    row.editor.setPlainText(self.invalid[name])
                else:
                    row.editor.setText(self.invalid[name])
            row.changed.connect(lambda name=name, row=row: self.commit_row(name, row))
            self.rows[name] = row
            layout.addWidget(row)
        if not self.rows:
            layout.addWidget(QLabel('No settings match your search.'))
        layout.addStretch(1)
        self.scroller.setWidget(body)

    def commit_row(self, name, row):
        try:
            value = row.value()
        except ValueError:
            editor = row.editor
            if isinstance(editor, (QPlainTextEdit, QLineEdit)):
                self.invalid[name] = editor.toPlainText() if isinstance(editor, QPlainTextEdit) else editor.text()
            return
        self.invalid.pop(name, None)
        setattr(self.settings, name, value)
        self.changed.emit()

    def validate_visible(self):
        if self.invalid:
            name = next(iter(self.invalid))
            raise ValueError(f'{label_for(name)} has an invalid value.')
        for name, row in self.rows.items():
            setattr(self.settings, name, row.value())
