"""Local-only tuning instruments; no capture, output injection, or networking."""

from collections import deque
import ctypes
import math
import time

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, QEvent, QSize
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QProgressBar,
    QTableWidget, QHeaderView, QSpinBox, QDoubleSpinBox, QPushButton,
    QGridLayout, QFrame, QRadioButton, QApplication, QAbstractSpinBox, QStyle,
)
from localization import format_text, translate_text
from control_fit import install_control_fit, fit_editor_table


ANCHORS = ("center", "top", "bottom", "upper-quarter")


class InferenceMeasurement:
    """Measure fresh native work after warm-up, not repeated capture statistics."""

    def __init__(self):
        self.baseline = None
        self.latest = None

    def observe(self, payload):
        try:
            if any(type(payload[name]) not in (int,float) for name in (
                    "inference_total", "uptime_seconds", "inference_work_ms", "model_work_ms")):
                return
            values = tuple(float(payload[name]) for name in (
                "inference_total", "uptime_seconds", "inference_work_ms", "model_work_ms"))
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        if not all(math.isfinite(value) and value >= 0 for value in values) or not values[0].is_integer():
            return
        if self.latest is not None and (values[0] <= self.latest[0] or values[1] <= self.latest[1]
                                       or values[2] < self.latest[2] or values[3] < self.latest[3]):
            return
        self.latest = values
        if self.baseline is None and values[0] >= 8:
            self.baseline = values

    def result(self):
        if self.baseline is None or self.latest is None:
            return None
        count, seconds, inference, model = (last - first for first, last in zip(self.baseline, self.latest))
        if count < 8 or seconds < 2 or inference <= 0:
            return None
        return {"frames": int(count), "inference_ms": inference / count, "model_ms": model / count,
                "fps": count / seconds, "seconds": seconds}


BUTTONS = {
    "dpad_up": 0x1, "dpad_down": 0x2, "dpad_left": 0x4, "dpad_right": 0x8,
    "start": 0x10, "back": 0x20, "left_thumb": 0x40, "right_thumb": 0x80,
    "left_shoulder": 0x100, "right_shoulder": 0x200,
    "a": 0x1000, "b": 0x2000, "x": 0x4000, "y": 0x8000,
}


def validated_policies(rows):
    if not isinstance(rows, list) or len(rows) > 128:
        raise ValueError("Class policies must be a list of at most 128 rules.")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid class policy.")
        class_id, priority = row.get("class_id"), row.get("priority", 0)
        mode = row.get("anchor_mode", "center")
        x, y = row.get("anchor_x", 0.0), row.get("anchor_y", 0.0)
        if type(class_id) is not int or not 0 <= class_id <= 1_000_000 or class_id in seen:
            raise ValueError("Class IDs must be unique integers between 0 and 1000000.")
        if type(priority) is not int or not -1_000_000 <= priority <= 1_000_000 or mode not in ANCHORS:
            raise ValueError("Invalid priority or anchor mode.")
        if any(type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1 for v in (x, y)):
            raise ValueError("Class anchor offsets must be finite values between -1 and 1.")
        seen.add(class_id)
        result.append(dict(class_id=class_id, priority=priority, anchor_mode=mode, anchor_x=float(x), anchor_y=float(y)))
    return result


def policies_wire(rows):
    rows = validated_policies(rows)
    return " ".join([str(len(rows))] + [
        f"{r['class_id']} {r['priority']} {ANCHORS.index(r['anchor_mode'])} {r['anchor_x']} {r['anchor_y']}"
        for r in rows
    ])


class ClassPolicyEditor(QWidget):
    def __init__(self, rows, commit, parent=None):
        super().__init__(parent)
        install_control_fit()
        self.commit = commit
        self.records = []
        self.selected = -1
        self._mode = None
        self._reflowing = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.reflow)
        self.parking = QWidget(self)
        self.parking.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Class ID", "Priority", "Anchor", "X offset", "Y offset"])
        self.table.setProperty('fitEmbeddedControls', True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.setMinimumHeight(100)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.currentCellChanged.connect(lambda row, *_: self.select(row))
        from mission_layout import CurrentPageStack, InventoryScroll, ActionRow, ActionFlow
        self.stack = CurrentPageStack()
        self.stack.addWidget(self.table)
        self.scroll = InventoryScroll()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName('Class rules')
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.body)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setAutoFillBackground(False)
        self.scroll.viewport().setAutoFillBackground(False)
        self.body.setAutoFillBackground(False)
        self.stack.addWidget(self.scroll)
        layout.addWidget(self.stack, 1)
        self.actions = ActionRow()
        buttons = ActionFlow(self.actions)
        for text, callback in (("Add class", self.add_row), ("Remove selected", self.remove_row), ("Apply rules", self.apply)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addWidget(self.actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for row in validated_policies(rows):
            self.add_row(row)
        self.table.model().headerDataChanged.connect(lambda *_: self.queue_reflow())

    def minimumSizeHint(self):
        return QSize(0, max(100,self.stack.currentWidget().minimumHeight())
                     + self.actions.minimumHeight() + self.status.minimumSizeHint().height() + 16)

    def queue_reflow(self):
        if hasattr(self, '_timer') and not self._timer.isActive():
            self._timer.start(0)

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FontChange,
                            QEvent.Type.StyleChange, QEvent.Type.LanguageChange, QEvent.Type.LayoutRequest):
            self.queue_reflow()
        return result

    def eventFilter(self, widget, event):
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange,QEvent.Type.LocaleChange):
            self.queue_reflow()
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            for index, record in enumerate(self.records):
                if widget is record['card'] or widget in record['cells'] or any(
                        isinstance(c, QAbstractSpinBox) and widget is c.lineEdit() for c in record['cells']):
                    self.select(index, synchronize=False)
                    break
        return False

    def select(self, index, *, synchronize=True):
        if self._reflowing or not 0 <= index < len(self.records):
            return
        self.selected = index
        for number, record in enumerate(self.records):
            record['ordinal'].setChecked(number == index)
        if synchronize and self._mode == 'table' and self.table.currentRow() != index:
            self.table.setCurrentCell(index, 0)

    def reflow(self):
        if self._reflowing:
            return
        self._reflowing = True
        try:
            from control_fit import fit_numeric
            headers = [self.table.horizontalHeaderItem(c).text() for c in range(5)]
            widths = [self.table.horizontalHeader().sectionSizeHint(c) for c in range(5)]
            for record in self.records:
                for col, control in enumerate(record['cells']):
                    if isinstance(control, QAbstractSpinBox):
                        fit_numeric(control)
                    hint = control.minimumSizeHint().expandedTo(control.minimumSize()).expandedTo(control.sizeHint())
                    widths[col] = max(widths[col], hint.width() + 2)
                    record['labels'][col].setText(headers[col])
                    record['labels'][col].setBuddy(control)
            needed = (sum(widths) + self.table.verticalHeader().width() + 2*self.table.frameWidth()
                      + self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent))
            mode = 'table' if self.width() >= needed else 'wrapped'
            restore = None
            if mode != self._mode:
                focused = QApplication.focusWidget()
                edits = []
                for record in self.records:
                    for control in record['cells']:
                        if isinstance(control, QAbstractSpinBox):
                            edit = control.lineEdit()
                            edits.append((control, control.value(), edit.text(), edit.cursorPosition(),
                                          edit.selectionStart(), len(edit.selectedText())))
                        control.setParent(self.parking)
                self.table.clearContents()
                self.table.setRowCount(len(self.records))
                for row, record in enumerate(self.records):
                    record['card'].setVisible(mode == 'wrapped')
                    for col, control in enumerate(record['cells']):
                        if mode == 'table':
                            holder = QWidget()
                            inner = QHBoxLayout(holder)
                            inner.setContentsMargins(0, 0, 0, 0)
                            inner.addWidget(control)
                            self.table.setCellWidget(row, col, holder)
                        else:
                            record['fields'][col].layout().addWidget(control)
                        control.show()
                self.stack.setCurrentWidget(self.table if mode == 'table' else self.scroll)
                self._mode = mode
                restore = focused, edits
            if mode == 'table':
                fit_editor_table(self.table)
            else:
                available = max(1, self.scroll.viewport().width() - 20)
                columns = 3 if sum(widths[:3]) + 16 <= available else 2 if max(
                    widths[0] + widths[1], widths[2] + widths[3], widths[4]) + 8 <= available else 1
                for row, record in enumerate(self.records):
                    record['ordinal'].setText(str(row + 1))
                    grid = record['grid']
                    if record.get('columns') != columns:
                        for field in record['fields']:
                            grid.removeWidget(field)
                        for col, field in enumerate(record['fields']):
                            r, c = divmod(col, columns)
                            grid.addWidget(field, r + 1, c)
                        for col in range(3):
                            grid.setColumnStretch(col, 1 if col < columns else 0)
                        record['columns'] = columns
                    for col,control in enumerate(record['cells']):
                        control.setAccessibleName(f'{row + 1}: {headers[col]}')
                    record['card'].setMinimumHeight(grid.minimumSize().height())
                visible_records=self.records[:2]
                self.scroll.setMinimumHeight(max(100,sum(record['card'].minimumHeight()
                    for record in visible_records)+max(0,len(visible_records)-1)*8))
            ordered = [c for record in self.records for c in record['cells']]
            for first, second in zip(ordered, ordered[1:]):
                self.setTabOrder(first, second)
            for index, record in enumerate(self.records):
                record['ordinal'].setChecked(index == self.selected)
            if mode == 'table' and 0 <= self.selected < len(self.records):
                self.table.setCurrentCell(self.selected, 0)
            if restore is not None:
                focused, edits = restore
                if focused is not None and focused.isEnabled():
                    focused.setFocus()
                for control, value, text, cursor, start, length in edits:
                    blocked = control.blockSignals(True)
                    control.setValue(value)
                    control.lineEdit().setText(text)
                    if start >= 0:
                        control.lineEdit().setSelection(start, length)
                    else:
                        control.lineEdit().setCursorPosition(cursor)
                    control.blockSignals(blocked)
        finally:
            self._reflowing = False

    def add_row(self, row=None):
        if len(self.records) >= 128:
            return
        if not isinstance(row, dict):
            used = {record['cells'][0].value() for record in self.records}
            row = {"class_id": next(i for i in range(129) if i not in used)}
        index = len(self.records)
        card = QFrame()
        card.setObjectName('settingsCard')
        card.installEventFilter(self)
        grid = QGridLayout(card)
        grid.setContentsMargins(8, 8, 8, 8)
        ordinal = QRadioButton(str(index + 1))
        ordinal.setAutoExclusive(False)
        ordinal.clicked.connect(lambda _=False, token=card: self.select(next(
            i for i, record in enumerate(self.records) if record['card'] is token)))
        grid.addWidget(ordinal, 0, 0)
        record = dict(card=card, grid=grid, ordinal=ordinal, cells=[], fields=[], labels=[])
        for col, field in enumerate(("class_id", "priority", "anchor_mode", "anchor_x", "anchor_y")):
            if col == 2:
                control = QComboBox()
                for anchor in ANCHORS:
                    control.addItem(anchor, anchor)
                control.setCurrentIndex(ANCHORS.index(row.get(field, 'center')))
            elif col < 2:
                control = QSpinBox()
                control.setRange(0 if col == 0 else -1_000_000, 1_000_000)
                control.setValue(row.get(field, 0))
            else:
                control = QDoubleSpinBox()
                control.setRange(-1, 1)
                control.setDecimals(6)
                control.setSingleStep(0.01)
                control.setValue(row.get(field, 0.0))
            control.setParent(self.parking)
            control.installEventFilter(self)
            if isinstance(control, QAbstractSpinBox):
                control.lineEdit().installEventFilter(self)
            box = QWidget(card)
            inner = QVBoxLayout(box)
            inner.setContentsMargins(0, 0, 0, 0)
            label = QLabel(self.table.horizontalHeaderItem(col).text())
            label.setProperty('_vaporlock_i18n_skip', True)
            label.setWordWrap(True)
            inner.addWidget(label)
            record['cells'].append(control)
            record['fields'].append(box)
            record['labels'].append(label)
        self.records.append(record)
        self.list_layout.insertWidget(self.list_layout.count() - 1, card)
        self.table.setRowCount(len(self.records))
        self._mode = None
        self.select(index)
        self.queue_reflow()

    def remove_row(self):
        if 0 <= self.selected < len(self.records):
            record = self.records.pop(self.selected)
            for control in record['cells']:
                control.setParent(self.parking)
                control.deleteLater()
            record['card'].deleteLater()
            self._mode = None
            self.selected = min(self.selected, len(self.records) - 1)
            for remaining in self.records:
                for control in remaining['cells']:
                    control.setParent(self.parking)
            self.table.clearContents()
            self.table.setRowCount(len(self.records))
            self.queue_reflow()

    def apply(self):
        rows = []
        for record in self.records:
            cells = record['cells']
            for control in cells:
                if isinstance(control, QAbstractSpinBox):
                    control.interpretText()
            rows.append(dict(class_id=cells[0].value(), priority=cells[1].value(),
                             anchor_mode=cells[2].currentData(), anchor_x=cells[3].value(), anchor_y=cells[4].value()))
        try:
            self.commit(validated_policies(rows))
            self.status.setText("Rules applied")
        except ValueError as error:
            self.status.setText(str(error))


class Gamepad(ctypes.Structure):
    _fields_ = [("buttons", ctypes.c_uint16), ("left_trigger", ctypes.c_uint8),
                ("right_trigger", ctypes.c_uint8), ("lx", ctypes.c_int16), ("ly", ctypes.c_int16),
                ("rx", ctypes.c_int16), ("ry", ctypes.c_int16)]


class PadState(ctypes.Structure):
    _fields_ = [("packet", ctypes.c_uint32), ("pad", Gamepad)]


class ControllerReader:
    def __init__(self):
        self.library = None
        self.get_state = None
        self.next_probe = {}
        if not hasattr(ctypes, "WinDLL"):
            return
        for name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
            try:
                self.library = ctypes.WinDLL(name, winmode=0x800)  # System32 only.
                self.get_state = self.library.XInputGetState
                self.get_state.argtypes = [ctypes.c_uint32, ctypes.POINTER(PadState)]
                self.get_state.restype = ctypes.c_uint32
                break
            except (OSError, AttributeError):
                continue

    def sample(self):
        connected = {}
        now = time.monotonic()
        if self.get_state:
            for slot in range(1, 5):
                if now < self.next_probe.get(slot, 0):
                    continue
                state = PadState()
                if self.get_state(slot - 1, ctypes.byref(state)) == 0:
                    connected[slot] = state.pad
                else:
                    self.next_probe[slot] = now + 2.0
        return connected


def binding_down(pad, binding):
    if pad is None:
        return False
    if binding in ("left_trigger", "right_trigger"):
        return getattr(pad, binding) >= 30
    return bool(pad.buttons & BUTTONS.get(binding, 0))


class StickView(QWidget):
    def __init__(self, language=lambda: "en"):
        super().__init__()
        self.language = language
        self.values = (0, 0, 0, 0)
        self.setMinimumHeight(130)
        self.setMaximumHeight(160)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = min(43, self.height() / 3)
        language = self.language()
        for i, label in enumerate(("Left stick", "Right stick")):
            center = QPointF(self.width() * (0.25 + i * 0.5), self.height() / 2 - 8)
            painter.setPen(QPen(QColor("#5f7fa9"), 1))
            painter.setBrush(QColor("#101827"))
            painter.drawEllipse(center, radius, radius)
            painter.drawLine(center + QPointF(-radius, 0), center + QPointF(radius, 0))
            painter.drawLine(center + QPointF(0, -radius), center + QPointF(0, radius))
            painter.setBrush(QColor("#7dbcf5" if i == 0 else "#e887c5"))
            x, y = self.values[i * 2:i * 2 + 2]
            painter.drawEllipse(center + QPointF(x / 32768 * radius, -y / 32768 * radius), 5, 5)
            painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            caption=f"{translate_text(label, language)}: {x}, {y}"
            painter.drawText(QRectF(i*self.width()/2+4,center.y()+radius+5,self.width()/2-8,self.height()-center.y()-radius-5),
                             Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop|Qt.TextFlag.TextWordWrap,caption)


class ControllerTester(QWidget):
    def __init__(self, settings, language=lambda: "en", parent=None, *, reader_factory=ControllerReader):
        super().__init__(parent)
        self.settings = settings
        self.language = language
        self.reader = reader_factory()
        from controller_thresholds import TriggerLatch
        self._activation_trigger = TriggerLatch()
        layout = QVBoxLayout(self)
        self.connection = QLabel("Waiting for controller input")
        self.connection.setWordWrap(True)
        layout.addWidget(self.connection)
        self.sticks = StickView(self.language)
        layout.addWidget(self.sticks)
        self.triggers = []
        for title in ("Left trigger", "Right trigger"):
            bar = QProgressBar()
            bar.setRange(0, 255)
            self.triggers.append((title, bar))
            layout.addWidget(bar)
        self.buttons = QLabel()
        self.buttons.setWordWrap(True)
        layout.addWidget(self.buttons)
        self.binding = QLabel()
        self.binding.setWordWrap(True)
        layout.addWidget(self.binding)
        layout.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self):
        pads = self.reader.sample()
        settings = self.settings()
        slot = settings.controller_slot or next(iter(pads), 0)
        pad = pads.get(slot)
        language = self.language()
        slots = ", ".join(map(str, pads)) or translate_text("none", language)
        selected = str(slot) if slot else translate_text("automatic", language)
        suffix = translate_text(" (disconnected)", language) if pad is None else ""
        self.connection.setText(format_text("Connected slots: {slots} | Selected: {selected}{suffix}", language, slots=slots, selected=selected, suffix=suffix))
        self.sticks.values = (pad.lx, pad.ly, pad.rx, pad.ry) if pad else (0, 0, 0, 0)
        self.sticks.update()
        for (title, bar), field in zip(self.triggers, ("left_trigger", "right_trigger")):
            bar.setFormat(format_text("{title}: %v / 255", language, title=translate_text(title, language)))
            bar.setValue(getattr(pad, field) if pad else 0)
        buttons = ", ".join(name.replace("_", " ") for name, mask in BUTTONS.items() if pad and pad.buttons & mask) or translate_text("none", language)
        self.buttons.setText(format_text("Buttons: {buttons}", language, buttons=buttons))
        press = getattr(settings, 'controller_trigger_press', 30)
        release = getattr(settings, 'controller_trigger_release', 30)
        if settings.controller_binding in ('left_trigger', 'right_trigger'):
            active = self._activation_trigger.sample(
                getattr(pad, settings.controller_binding) if pad else None, press, release,
                (slot, settings.controller_slot, settings.controller_binding, settings.activation_source))
        else:
            self._activation_trigger.sample(None)
            active = binding_down(pad, settings.controller_binding)
        lock = binding_down(pad, settings.manual_lock_controller_binding)
        self.binding.setText(format_text(
            "Activation binding: {activation} | Lock binding: {lock}", language,
            activation=translate_text("pressed" if active else "released", language),
            lock=translate_text("pressed" if lock else "released", language),
        ))


class PerformanceHistory:
    FIELDS = ("capture_ms", "preprocess_ms", "model_ms", "tracking_ms")

    def __init__(self):
        self.samples = deque(maxlen=1200)
        self.last_sample = -math.inf

    def add(self, data, now=None):
        now = time.monotonic() if now is None else now
        if now - self.last_sample < 0.24:
            return False
        values = tuple(data.get(key) for key in self.FIELDS)
        if any(type(value) not in (float, int) or not math.isfinite(value) or value < 0 for value in values):
            return False
        self.samples.append((now, values))
        self.last_sample = now
        return True

    def clear(self):
        self.samples.clear()
        self.last_sample = -math.inf

    def summary(self):
        if not self.samples:
            return "Waiting for timing samples"
        rows = [sample[1] for sample in self.samples]
        return "\n".join(f"{name}: sampled average {sum(r[i] for r in rows) / len(rows):.2f} ms | sampled peak {max(r[i] for r in rows):.2f} ms"
                         for i, name in enumerate(("Capture", "Preprocess", "Model execution", "Tracking")))


class PerformanceChart(QWidget):
    def __init__(self, history):
        super().__init__()
        self.history = history
        self.setMinimumHeight(180)
        self.setMaximumHeight(300)

    def paintEvent(self, event):
        painter = QPainter(self)
        from interface_themes import painted_role
        painter.fillRect(self.rect(), QColor(painted_role(self,'surface','#101827')))
        rows = list(self.history.samples)
        if not rows:
            painter.setPen(QColor("#aab7c3"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No timing samples")
            return
        peak = max(1.0, max(max(row[1]) for row in rows))
        left, top, width, height = 52, 24, max(1, self.width() - 68), self.height() - 52
        painter.setPen(QColor("#aab7c3"))
        painter.drawText(4, top + 4, f"{peak:.0f} ms")
        painter.drawText(left, self.height() - 8, f"{len(rows)} samples | {rows[-1][0] - rows[0][0]:.0f} seconds")
        span = max(1.0, rows[-1][0] - rows[0][0])
        for field, color in enumerate(("#7dbcf5", "#a481f1", "#b9e7ff", "#e887c5")):
            painter.setPen(QPen(QColor(color), 1.5))
            previous = None
            for timestamp, values in rows:
                point = QPointF(left + (timestamp - rows[0][0]) / span * width, top + height * (1 - values[field] / peak))
                if previous is not None:
                    painter.drawLine(previous, point)
                previous = point


def output_diagnostics(data):
    if not data:
        return "No active observation session"
    fields = (
        ("Controller", f"slot {data.get('physical_controller_slot', 0)}" if data.get("controller_connected") else "not connected / not sampled"),
        ("Activation", "authorized" if data.get("activation_authorized") else "not authorized"),
        ("Target", f"track #{data['selected_track']}" if data.get("selected_track") else "none"),
        ("Output permission", "enabled" if data.get("output_active") else "blocked"),
        ("Current gate", str(data.get("output_reason", "unavailable")).replace("-", " ")),
        ("Output accepted / failed", f"{data.get('output_sent_total', 0)} / {data.get('output_failed_total', 0)}"),
        ("Motion compensation", "disabled" if not data.get("motion_compensation_enabled") else ("active" if data.get("motion_compensation_active") else "waiting for activation")),
        ("Compensation mode", str(data.get("motion_compensation_mode", "preset")).replace("-", " ")),
        ("Automatic estimate", "not used" if data.get("motion_compensation_mode") == "preset" else (f"{data.get('motion_compensation_auto_tracks', 0)} agreeing tracks" if data.get("motion_compensation_auto_valid") else "waiting for agreeing tracks")),
        ("Snap event", "enabled" if data.get("event_action_enabled") else "disabled"),
        ("Snap activation", "not used" if not data.get("event_action_enabled") else ("independent" if not data.get("event_action_requires_activation", True) else ("authorized" if data.get("event_action_activation_authorized", False) else "waiting for aim activation"))),
        ("Physical mouse guard", "not needed" if not data.get("event_action_enabled") or not data.get("event_action_activation_authorized", False) else ("ready" if data.get("event_action_physical_monitor", False) else "unavailable; snap events paused")),
        ("Snap state", str(data.get("event_action_reason", "unavailable")).replace("-", " ")),
        ("Snap event actions / failed", f"{data.get('event_action_sent_total', 0)} / {data.get('event_action_failed_total', 0)}"),
    )
    return "\n".join(f"{name}: {value}" for name, value in fields)
