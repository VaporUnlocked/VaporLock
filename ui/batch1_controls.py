"""Native tuning controls shared by Free and Premium; no product services."""
from functools import wraps
import weakref

from PySide6.QtCore import QEvent, QObject, Qt, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QAbstractSpinBox, QDoubleSpinBox, QComboBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSlider, QToolButton)
from shiboken6 import isValid


class PreciseSpinBox(QDoubleSpinBox):
    def textFromValue(self, value):
        text = super().textFromValue(value)
        point = self.locale().decimalPoint()
        return text.rstrip('0').rstrip(point) if point in text else text


class WrappedStatusLabel(QLabel):
    """Keep translated validation copy whole in a narrow settings column."""
    def setText(self, text):
        super().setText(text)
        self._fit_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self):
        height = max(0, self.heightForWidth(max(1, self.width()))) if self.text() else 0
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)


def tuning_guard(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        if getattr(self, '_tuning_locked', False):
            self.log('INFO', self.t('Unlock tuning to change settings.'))
            return False
        return method(self, *args, **kwargs)
    return guarded


def symbol_color(center):
    from vapor_lock_gui import appearance_palette
    return appearance_palette(center.ui_appearance, True)['ice']


def symbol_icon(kind, locked=False, color='#edf6ff'):
    """Small vector-drawn icons, independent of emoji/font availability."""
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.8))
    if kind == 'lock':
        painter.drawRoundedRect(5, 11, 14, 10, 2, 2)
        painter.drawArc(8 if locked else 12, 2, 8, 14, 0, 180 * 16)
        painter.drawLine(12, 15, 12, 18)
    else:
        painter.drawEllipse(6, 2, 12, 12)
        painter.drawLine(9, 14, 9, 18)
        painter.drawLine(15, 14, 15, 18)
        painter.drawLine(9, 18, 15, 18)
        painter.drawLine(10, 21, 14, 21)
    painter.end()
    return QIcon(pixmap)


class TuningLock(QObject):
    def __init__(self, center):
        super().__init__(center)
        self.center = center
        self.wrappers = []
        center._tuning_locked = False
        center._tuning_epoch = 0
        QApplication.instance().installEventFilter(self)

    def wrap(self, control):
        if control.property('tuningLockExempt'):
            return control
        # Disable a dedicated parent, never overwrite a child's license/runtime
        # enabled state. A later child refresh cannot defeat a locked ancestor.
        wrapper = QFrame()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(control)
        wrapper.setEnabled(not self.center._tuning_locked)
        self.wrappers.append(weakref.ref(wrapper))
        return wrapper

    def toggle(self):
        center = self.center
        if not center._tuning_locked:
            # Cancel outstanding tuning before closing the mutation lane.
            if getattr(center, '_calibration_active', False):
                center.cancel_motion_compensation_calibration()
            if hasattr(center, 'cancel_model_probes'):
                center.cancel_model_probes()
        center._tuning_epoch += 1
        center._tuning_locked = not center._tuning_locked
        self.wrappers = [ref for ref in self.wrappers if ref() is not None and isValid(ref())]
        for ref in self.wrappers:
            ref().setEnabled(not center._tuning_locked)
        self.refresh()
        if center._live_owner is not None:
            center._live_owner.tuning_lock_changed()

    def refresh(self):
        center = self.center
        button = getattr(center, 'tuning_lock_button', None)
        if button is None:
            return
        title = center.t('Unlock tuning' if center._tuning_locked else 'Lock tuning')
        button.setToolTip(title)
        button.setAccessibleName(title)
        button.setChecked(center._tuning_locked)
        color = symbol_color(center)
        from mission_icons import apply as apply_icon
        from vapor_lock_gui import appearance_palette
        apply_icon(button, 'lock' if center._tuning_locked else 'unlock',
                   appearance_palette(center.ui_appearance, True), getattr(center._live_owner, '_scale', '1'))
        for bulb in center.findChildren(QToolButton, 'guideBulb'):
            bulb.setIcon(symbol_icon('bulb', color=color))

    def button(self):
        button = QToolButton()
        button.setObjectName('tuningLock')
        button.setCheckable(True)
        button.setIconSize(QSize(22, 22))
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setProperty('_vaporlock_i18n_skip', True)
        button.clicked.connect(self.toggle)
        self.center.tuning_lock_button = button
        self.refresh()
        return button

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and (
                event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Escape)
                or event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            return False
        # Covers editable controls outside standard setting rows (profile names,
        # quick profile switch, session mode). Navigation tabs and guide buttons
        # are not editing controls. Core mutation handlers enforce the same lock.
        if self.center._tuning_locked and event.type() in {
            QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick, QEvent.Type.KeyPress,
            QEvent.Type.Wheel, QEvent.Type.InputMethod, QEvent.Type.Drop}:
            item = watched
            editable = False
            for _ in range(32):
                if item is self.center:
                    return editable
                if item is None or not hasattr(item, 'parentWidget'):
                    break
                if item.property('tuningLockExempt'):
                    return False
                if isinstance(item, (QAbstractSpinBox, QComboBox, QLineEdit, QSlider)):
                    editable = True
                if isinstance(item, QPlainTextEdit) and not item.isReadOnly():
                    editable = True
                item = item.parentWidget()
        return False
