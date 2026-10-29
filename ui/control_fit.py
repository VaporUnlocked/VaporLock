"""Font/style-aware editor sizing; no value, signal, permission or range changes."""
import weakref

from PySide6.QtCore import QObject, QEvent, QLocale, QSize, QTimer
from PySide6.QtWidgets import (QApplication, QAbstractSpinBox, QDoubleSpinBox,
                              QSpinBox, QComboBox, QStyle, QStyleOptionSpinBox, QStyleOptionComboBox, QTableWidget)
from shiboken6 import isValid


def header_combo_size(control, required_text=(), *, bounded_name=False):
    """Measure the closed editor, including its styled arrow and frame.

    User profile names may be long; the full name stays in the popup/tooltip.
    Translated fixed captions are never truncated to the profile-name budget.
    All callers use the same policy, regardless of window density or scaling.
    """
    control.ensurePolished()
    metrics = control.fontMetrics()
    required = max((metrics.horizontalAdvance(text) for text in required_text), default=0)
    text_width = max((metrics.horizontalAdvance(control.itemText(i))
                      for i in range(control.count())), default=0)
    if bounded_name:
        text_width = min(text_width, metrics.horizontalAdvance('M' * 18))
    content = QSize(max(required, text_width) + 4, metrics.height() + 4)
    option = QStyleOptionComboBox()
    control.initStyleOption(option)
    return control.style().sizeFromContents(QStyle.ContentsType.CT_ComboBox,
                                             option, content, control)


def numeric_size(control):
    """Reserve full precision even when textFromValue trims endpoint zeroes."""
    locale = QLocale(control.locale())
    if not control.isGroupSeparatorShown():
        locale.setNumberOptions(locale.numberOptions() | QLocale.NumberOption.OmitGroupSeparator)
    decimals = control.decimals() if isinstance(control, QDoubleSpinBox) else None
    values = (control.minimum(), control.maximum(), control.value())
    numbers = [locale.toString(value, 'f', decimals) if decimals is not None
               else locale.toString(value) for value in values]
    # Digits need not be tabular in a user-selected Windows font.
    metrics = control.fontMetrics()
    widest = max('0123456789', key=metrics.horizontalAdvance)
    numbers += [''.join(widest if char.isdecimal() else char for char in number)
                for number in numbers]
    texts = [control.prefix() + number + control.suffix() for number in numbers]
    texts.extend((control.text(), control.specialValueText()))
    content = QSize(max(metrics.horizontalAdvance(text) for text in texts) + 4,
                    metrics.height() + 4)
    option = QStyleOptionSpinBox()
    control.initStyleOption(option)
    styled = control.style().sizeFromContents(QStyle.ContentsType.CT_SpinBox, option, content, control)
    return styled.expandedTo(control.sizeHint()).expandedTo(control.minimumSizeHint())


def fit_numeric(control):
    desired = numeric_size(control)
    # Remember caller-supplied minima independently so switching back from a
    # larger font/theme may shrink the editor to its newly measured size.
    previous = getattr(control, '_vapor_fit_minimum', None)
    base = getattr(control, '_vapor_fit_base', control.minimumSize())
    current = control.minimumSize()
    if previous is not None:
        if current.width() != previous.width():
            base.setWidth(current.width())
        if current.height() != previous.height():
            base.setHeight(current.height())
    desired = desired.expandedTo(base)
    control._vapor_fit_base = QSize(base)
    control._vapor_fit_minimum = QSize(desired)
    if current != desired:
        control.setMinimumSize(desired)


def fit_editor_table(table):
    """Fit embedded controls, then share surplus width after their real minima."""
    header = table.horizontalHeader()
    widths = [header.sectionSizeHint(column) for column in range(table.columnCount())]
    for row in range(table.rowCount()):
        height = table.verticalHeader().minimumSectionSize()
        for column in range(table.columnCount()):
            control = table.cellWidget(row, column)
            if control is None:
                continue
            if isinstance(control, (QSpinBox, QDoubleSpinBox)):
                fit_numeric(control)
            for numeric in control.findChildren(QAbstractSpinBox):
                if isinstance(numeric,(QSpinBox,QDoubleSpinBox)):
                    fit_numeric(numeric)
            hint = control.sizeHint().expandedTo(control.minimumSizeHint()).expandedTo(control.minimumSize())
            widths[column] = max(widths[column], hint.width() + 2)
            height = max(height, hint.height() + 2)
        if table.rowHeight(row) != height:
            table.setRowHeight(row, height)
    if not widths:
        return
    gutter = table.verticalHeader().width() + 2 * table.frameWidth()
    gutter += table.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, table)
    needed = sum(widths) + gutter
    if table.minimumWidth() != needed:
        table.setMinimumWidth(needed)
    surplus = max(0, table.viewport().width() - sum(widths))
    for column, width in enumerate(widths):
        extra = surplus * (column + 1) // len(widths) - surplus * column // len(widths)
        if table.columnWidth(column) != width + extra:
            table.setColumnWidth(column, width + extra)


class ControlFit(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.pending = weakref.WeakSet()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.flush)
        app.installEventFilter(self)

    def queue(self, widget):
        self.pending.add(widget)
        if not self.timer.isActive():
            self.timer.start(0)

    def eventFilter(self, widget, event):
        if event.type() in (QEvent.Type.Polish, QEvent.Type.Show, QEvent.Type.FontChange,
                            QEvent.Type.StyleChange, QEvent.Type.LocaleChange,
                            QEvent.Type.EnabledChange, QEvent.Type.FocusIn, QEvent.Type.FocusOut,
                            QEvent.Type.LayoutRequest):
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                self.queue(widget)
            elif isinstance(widget, QTableWidget) and widget.property('fitEmbeddedControls'):
                self.queue(widget)
            if isinstance(widget, (QAbstractSpinBox, QComboBox)):
                parent = widget.parentWidget()
                for _ in range(6):
                    if parent is None:
                        break
                    if isinstance(parent,QTableWidget) and parent.property('fitEmbeddedControls'):
                        self.queue(parent)
                        break
                    parent=parent.parentWidget()
        elif event.type() == QEvent.Type.Resize and isinstance(widget, QTableWidget) and widget.property('fitEmbeddedControls'):
            self.queue(widget)
        return False

    def flush(self):
        pending = list(self.pending)
        self.pending.clear()
        for widget in pending:
            if not isValid(widget):
                continue
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                fit_numeric(widget)
            else:
                fit_editor_table(widget)


def install_control_fit():
    app = QApplication.instance()
    if app is not None and not hasattr(app, '_vaporlock_control_fit'):
        app._vaporlock_control_fit = ControlFit(app)
    return getattr(app, '_vaporlock_control_fit', None)
