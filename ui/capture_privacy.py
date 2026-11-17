"""Profile-owned opaque redaction rectangles in final capture coordinates."""
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

SCALE = 10000
MAX_MASKS = 16


def validate_masks(masks):
    if type(masks) is not list or len(masks) > MAX_MASKS:
        raise ValueError("Invalid capture privacy masks.")
    result = []
    for mask in masks:
        if (type(mask) is not list or len(mask) != 4 or
                any(type(n) is not int or not 0 <= n <= SCALE for n in mask)
                or mask[0] >= mask[2] or mask[1] >= mask[3]):
            raise ValueError("Invalid capture privacy mask.")
        result.append(mask.copy())
    return result


def wire(masks):
    return " ".join(str(value) for mask in validate_masks(masks) for value in mask)


def redact_image(image, masks):
    masks = validate_masks(masks)
    if not masks or image.isNull():
        return image
    result = image.convertToFormat(QImage.Format.Format_ARGB32)
    painter = QPainter(result)
    try:
        width, height = result.width(), result.height()
        for left, top, right, bottom in masks:
            x0, y0 = left * width // SCALE, top * height // SCALE
            x1 = (right * width + SCALE - 1) // SCALE
            y1 = (bottom * height + SCALE - 1) // SCALE
            painter.fillRect(QRect(x0, y0, x1 - x0, y1 - y0), QColor("black"))
    finally:
        painter.end()
    return result


class MaskCanvas(QWidget):
    def __init__(self, image, masks, update_status, parent):
        super().__init__(parent)
        self.image, self.masks, self.update_status = image, masks, update_status
        self.start = self.end = None
        self.setMinimumSize(440, 265)
        self.setAccessibleName("Capture privacy mask drawing area")
        self.setCursor(Qt.CursorShape.CrossCursor)

    def frame(self):
        if self.image.isNull():
            width, height = 16, 9
        else:
            width, height = self.image.width(), self.image.height()
        scale = min(self.width() / width, self.height() / height)
        width, height = width * scale, height * scale
        return QRectF((self.width()-width)/2, (self.height()-height)/2, width, height)

    def paintEvent(self, _event):
        painter = QPainter(self)
        frame = self.frame()
        painter.fillRect(self.rect(), QColor("#101625"))
        if self.image.isNull():
            painter.fillRect(frame, QColor("#283549"))
        else:
            painter.drawImage(frame, self.image)
        painter.setPen(QPen(QColor("#73c9ff"), 1))
        for left, top, right, bottom in self.masks:
            rect = QRectF(frame.left() + left * frame.width() / SCALE,
                          frame.top() + top * frame.height() / SCALE,
                          (right-left) * frame.width() / SCALE, (bottom-top) * frame.height() / SCALE)
            painter.fillRect(rect, QColor("black"))
            painter.drawRect(rect)
        if self.start is not None and self.end is not None:
            rect = QRectF(self.start, self.end).normalized().intersected(frame)
            painter.fillRect(rect, QColor(24, 137, 190, 95))
            painter.drawRect(rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and len(self.masks) < MAX_MASKS:
            if self.frame().contains(event.position()):
                self.start = self.end = QPointF(event.position())

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.end = QPointF(event.position())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        frame = self.frame()
        mask = QRectF(self.start, event.position()).normalized().intersected(frame)
        self.start = self.end = None
        if mask.width() >= 3 and mask.height() >= 3:
            from math import ceil, floor
            top_left = [(mask.left()-frame.left()) / frame.width(), (mask.top()-frame.top()) / frame.height()]
            bottom_right = [(mask.right()-frame.left()) / frame.width(), (mask.bottom()-frame.top()) / frame.height()]
            self.masks.append([max(0, floor(value*SCALE)) for value in top_left]
                              + [min(SCALE, ceil(value*SCALE)) for value in bottom_right])
        self.update_status()
        self.update()


def open_editor(center):
    dialog = QDialog(center)
    dialog.setWindowTitle(center.t("Capture privacy masks"))
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(center.t("Drag rectangles over areas to cover. Applying masks stops any active capture.")))
    image = center._last_preview_frame.copy()
    if image.isNull() and center.settings.capture_source == "desktop":
        try:
            from batch4_geometry import enumerate_displays, region_pixels
            from crosshair_reference import capture_desktop_pixels
            settings = center.settings
            display = next(item for item in enumerate_displays() if item.index == settings.monitor)
            x, y, width, height = region_pixels(display, settings.region_x, settings.region_y,
                                                settings.region_width, settings.region_height)
            image = capture_desktop_pixels(display.left + x, display.top + y, width, height)
        except (StopIteration, OSError, ValueError):
            image = QImage()
    masks = validate_masks(center.settings.capture_privacy_masks)
    status = QLabel()
    canvas = MaskCanvas(image, masks, lambda: status.setText(f"{len(masks)} / {MAX_MASKS}"), dialog)
    layout.addWidget(canvas, 1)
    row = QHBoxLayout()
    undo = QPushButton(center.t("Remove latest mask"))
    clear = QPushButton(center.t("Clear masks"))

    def edit(clear_all=False):
        if clear_all:
            masks.clear()
        elif masks:
            masks.pop()
        canvas.update_status()
        canvas.update()

    undo.clicked.connect(lambda: edit())
    clear.clicked.connect(lambda: edit(True))
    row.addWidget(undo)
    row.addWidget(clear)
    row.addStretch()
    row.addWidget(status)
    layout.addLayout(row)
    canvas.update_status()
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.resize(720, 520)
    if dialog.exec() == QDialog.DialogCode.Accepted and masks != center.settings.capture_privacy_masks:
        if center.engine.active:
            center.stop_capture()
            if center.engine.active:
                center.log("WARN", "Wait for capture to stop, then apply privacy masks again.")
                return
        center.change("capture_privacy_masks", masks)
