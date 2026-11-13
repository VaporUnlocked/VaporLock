"""Capture and edit a small, in-memory crosshair calibration template."""

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from batch4_geometry import enumerate_displays, region_pixels
from crosshair_reference import capture_patch, geometry, sample_template

from .capture_affinity import CaptureExclusionGuard, exclude_windows


PATCH_SIZE = 127
PATCH_CENTER = PATCH_SIZE // 2
TEMPLATE_RADIUS = 31
SEARCH_RADIUS = 16
ACTIVE_STATES = {'starting', 'running', 'stopping'}


def _crop(settings):
    return (settings.monitor, settings.region_x, settings.region_y,
            settings.region_width, settings.region_height)


def _sample_in_capture(template, region):
    left, top, width, height = region
    x, y = template['point']
    if not (left <= x < left + width and top <= y < top + height):
        return False
    return all(left + SEARCH_RADIUS <= x + dx < left + width - SEARCH_RADIUS
               and top + SEARCH_RADIUS <= y + dy < top + height - SEARCH_RADIUS
               for dx, dy, *_ in template['samples'])


class _PixelEditor(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName('Magnified crosshair sample')
        self.image = QImage()
        self.point = (PATCH_CENTER, PATCH_CENTER)
        self.mask = set()
        self.mode = 'aim'

    def reset(self, image):
        self.image = image
        self.point = (PATCH_CENTER, PATCH_CENTER)
        self.mask.clear()
        self.update()

    def frame(self):
        size = min(self.width(), self.height())
        return QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)

    def pixel_at(self, position):
        frame = self.frame()
        x = position.x() - frame.left()
        y = position.y() - frame.top()
        size = frame.width()
        if self.image.isNull() or not (0 <= x < size and 0 <= y < size):
            return None
        return (min(PATCH_SIZE - 1, int(x / size * PATCH_SIZE)),
                min(PATCH_SIZE - 1, int(y / size * PATCH_SIZE)))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#202a25'))
        if self.image.isNull():
            painter.setPen(QColor('#e5e9db'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Capture a sample to begin')
            return
        frame = self.frame()
        painter.drawImage(frame, self.image)
        scale = frame.width() / PATCH_SIZE
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(73, 232, 141, 170))
        for x, y in self.mask:
            painter.drawRect(QRectF(frame.left() + x * scale, frame.top() + y * scale, scale, scale))
        x, y = self.point
        center = QPointF(frame.left() + (x + .5) * scale, frame.top() + (y + .5) * scale)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('#fff6dc'), 3))
        painter.drawEllipse(center, 7, 7)
        painter.setPen(QPen(QColor('#d95428'), 2))
        painter.drawEllipse(center, 7, 7)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pixel = self.pixel_at(event.position())
            if pixel is not None:
                self.select(*pixel)

    def select(self, x, y):
        if self.image.isNull() or not (0 <= x < PATCH_SIZE and 0 <= y < PATCH_SIZE):
            return
        pixel = (x, y)
        if self.mode == 'aim':
            self.point = pixel
            self.mask.clear()
        elif self.mode == 'erase':
            self.mask.discard(pixel)
        elif (abs(x - self.point[0]) <= TEMPLATE_RADIUS
              and abs(y - self.point[1]) <= TEMPLATE_RADIUS):
            if self.mode == 'colour':
                self._select_colour(x, y)
            else:
                self.mask.add(pixel)
        self.update()
        self.changed.emit()

    def _select_colour(self, x, y):
        color = self.image.pixelColor(x, y).getRgb()[:3]
        aim_x, aim_y = self.point
        for row in range(max(0, aim_y - TEMPLATE_RADIUS), min(PATCH_SIZE, aim_y + TEMPLATE_RADIUS + 1)):
            for column in range(max(0, aim_x - TEMPLATE_RADIUS), min(PATCH_SIZE, aim_x + TEMPLATE_RADIUS + 1)):
                sample = self.image.pixelColor(column, row).getRgb()[:3]
                if max(abs(left - right) for left, right in zip(color, sample)) <= 24:
                    self.mask.add((column, row))

    def clear(self):
        self.mask.clear()
        self.update()
        self.changed.emit()


class _CalibrationDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle('Crosshair calibration')
        self.settings = settings
        self.crop = _crop(settings)
        self.displays = enumerate_displays()
        self.display = next((display for display in self.displays if display.index == self.crop[0]), None)
        if self.display is None or self.display.index == 0 or min(self.display.width, self.display.height) < PATCH_SIZE:
            raise ValueError('Choose a physical display under Capture before calibrating.')
        self.region = region_pixels(self.display, *self.crop[1:])
        self.origin = None
        self.candidate = None
        self.template = None
        self.seconds_left = 0
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self._build()
        self.guard = CaptureExclusionGuard(self, self.timer.isActive, self._exclusion_failed)

    def _build(self):
        available = self.screen().availableGeometry()
        self.resize(min(630, available.width() - 16), min(730, available.height() - 32))
        layout = QVBoxLayout(self)
        heading = QLabel('Calibrate a crosshair')
        heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        instructions = QLabel(
            f'Display {self.display.index} · {self.display.width} × {self.display.height} pixels\n'
            'Capture a sample with the crosshair visible. Mark the aim point, then mark the crosshair pixels.'
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.capture_button = QPushButton('Capture in 3 seconds')
        self.capture_button.clicked.connect(self._countdown)
        layout.addWidget(self.capture_button)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        editor_layout = QVBoxLayout(content)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor = _PixelEditor()
        self.editor.changed.connect(self._refresh_template)
        editor_layout.addWidget(self.editor, 1)
        controls = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.setAccessibleName('Pixel editing mode')
        for title, key in (('Aim point', 'aim'), ('Select color', 'colour'),
                           ('Add pixel', 'pixel'), ('Erase pixel', 'erase')):
            self.mode.addItem(title, key)
        self.mode.currentIndexChanged.connect(self._change_mode)
        controls.addWidget(self.mode, 1)
        clear_button = QPushButton('Clear selection')
        clear_button.clicked.connect(self.editor.clear)
        controls.addWidget(clear_button)
        editor_layout.addLayout(controls)

        coordinate_row = QHBoxLayout()
        self.x = QSpinBox()
        self.y = QSpinBox()
        for title, spin in (('Pixel X', self.x), ('Pixel Y', self.y)):
            spin.setRange(0, PATCH_SIZE - 1)
            spin.setValue(PATCH_CENTER)
            spin.setAccessibleName(title)
            coordinate_row.addWidget(QLabel(title))
            coordinate_row.addWidget(spin)
        mark = QPushButton('Mark pixel')
        mark.clicked.connect(lambda: self.editor.select(self.x.value(), self.y.value()))
        coordinate_row.addWidget(mark, 1)
        editor_layout.addLayout(coordinate_row)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        self.status = QLabel('Only the selected template pixels will be saved.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText('Save calibration')
        self.save_button.setObjectName('primary')
        self.save_button.setEnabled(False)
        buttons.accepted.connect(self._commit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _change_mode(self):
        self.editor.mode = self.mode.currentData()

    def _check_state(self):
        engine = getattr(self.parentWidget(), 'engine', None)
        if engine is not None and engine.snapshot().state in ACTIVE_STATES:
            raise ValueError('Stop capture before calibrating.')
        if _crop(self.settings) != self.crop or enumerate_displays() != self.displays:
            raise ValueError('The display or capture region changed. Reopen calibration.')

    def _exclude(self):
        if not exclude_windows():
            raise ValueError('VaporLock windows could not be excluded from the sample.')

    def _countdown(self):
        self._clear_sample()
        try:
            self._check_state()
            self._exclude()
        except (OSError, RuntimeError, ValueError) as error:
            self._capture_failed(str(error))
            return
        self.capture_button.setEnabled(False)
        self.seconds_left = 3
        self.status.setText('Capturing in 3 seconds.')
        self.timer.start()

    def _tick(self):
        if not self.timer.isActive():
            return
        try:
            self._check_state()
            self.seconds_left -= 1
            if self.seconds_left > 0:
                self.status.setText(f'Capturing in {self.seconds_left} seconds.')
                return
            self.timer.stop()
            self._exclude()
            image, origin = capture_patch(self.display)
            self._check_state()
            if (image.width() != PATCH_SIZE or image.height() != PATCH_SIZE
                    or not 0 <= origin[0] <= self.display.width - PATCH_SIZE
                    or not 0 <= origin[1] <= self.display.height - PATCH_SIZE):
                raise ValueError('The captured patch is outside the selected display.')
            self.editor.reset(image)
            self.mode.setCurrentIndex(0)
            self.x.setValue(PATCH_CENTER)
            self.y.setValue(PATCH_CENTER)
            self.origin = origin
            self.capture_button.setEnabled(True)
            self._refresh_template()
        except (OSError, RuntimeError, ValueError) as error:
            self._capture_failed(str(error))

    def _clear_sample(self):
        self.origin = None
        self.candidate = None
        self.save_button.setEnabled(False)
        self.editor.reset(QImage())

    def _capture_failed(self, message):
        self.timer.stop()
        self._clear_sample()
        self.capture_button.setEnabled(True)
        self.status.setText(message)

    def _exclusion_failed(self):
        self._capture_failed('A VaporLock window could not be excluded from the sample.')

    def _refresh_template(self):
        self.candidate = None
        self.save_button.setEnabled(False)
        if self.origin is None or self.timer.isActive():
            return
        if not self.editor.mask:
            self.status.setText('Mark the aim point, then choose Add pixel or Select color.')
            return
        try:
            candidate = sample_template(self.editor.image, self.editor.point, self.editor.mask,
                                        geometry(self.settings, self.display), self.origin)
            if not _sample_in_capture(candidate, self.region):
                raise ValueError('The crosshair search needs 16 pixels of space around the sample inside capture.')
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.candidate = candidate
        self.save_button.setEnabled(True)
        self.status.setText(f'Template ready · {len(candidate["samples"])} saved samples.')

    def _commit(self):
        if self.candidate is None or self.timer.isActive():
            return
        try:
            self._check_state()
        except (OSError, RuntimeError, ValueError) as error:
            self._capture_failed(str(error))
            return
        self.template = self.candidate
        self.accept()

    def done(self, result):
        self.timer.stop()
        self._clear_sample()
        self.guard.close()
        super().done(result)


def calibrate(parent, settings) -> dict | None:
    """Return a calibrated template, or None if the operation is cancelled."""
    try:
        dialog = _CalibrationDialog(parent, settings)
    except (OSError, RuntimeError, ValueError) as error:
        QMessageBox.warning(parent, 'Crosshair calibration', str(error))
        return None
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.template
        return None
    finally:
        dialog.deleteLater()
