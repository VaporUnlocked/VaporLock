"""Bounded settings shared by profiles, native launch and local controls."""
import math

FIELDS = {
    'assistance_x': (0.0, 4.0, 1.0),
    'assistance_y': (0.0, 4.0, 1.0),
    'controller_output_deadzone': (0.0, 0.5, 0.0),
    'acquire_confidence': (0.0, 1.0, 0.0),
    'retain_confidence': (0.0, 1.0, 0.0),
}


def validate(settings):
    for key, (low, high, default) in FIELDS.items():
        value = settings.get(key, default)
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError('Invalid assistance or confidence setting.')


def confidence_floors(settings):
    validate(vars(settings))
    retain = max(settings.confidence, settings.retain_confidence)
    return max(retain, settings.acquire_confidence), retain


def spin(center, field):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget, QHBoxLayout, QDoubleSpinBox
    from vapor_lock_gui import ConfigSlider
    low, high, _ = FIELDS[field]
    factor = 100 if field == 'controller_output_deadzone' else 1
    control = QWidget(); row = QHBoxLayout(control); row.setContentsMargins(0, 0, 0, 0)
    control.setObjectName(field)
    slider = ConfigSlider(Qt.Orientation.Horizontal); slider.setRange(round(low*1000), round(high*1000))
    slider.setObjectName(field+'_slider'); slider.setProperty('barAccent', 'baby')
    value = QDoubleSpinBox(); value.setObjectName(field+'_value'); value.setDecimals(6)
    value.setRange(low*factor, high*factor); value.setSingleStep(.01*factor)
    value.setSuffix(' %' if factor == 100 else ' ×' if field.startswith('assistance_') else '')
    value.setKeyboardTracking(False); value.setMinimumWidth(120)
    titles = dict(assistance_x='Horizontal target assistance', assistance_y='Vertical target assistance',
        controller_output_deadzone='Controller output dead-zone compensation',
        acquire_confidence='Acquisition confidence', retain_confidence='Retention confidence')
    value.setAccessibleName(center.t(titles[field])); slider.setAccessibleName(center.t(titles[field]))
    value.setValue(getattr(center.settings, field)*factor)
    slider.setValue(round(getattr(center.settings, field)*1000))
    def changed(number):
        slider.blockSignals(True); slider.setValue(round(number/factor*1000)); slider.blockSignals(False)
        center.change(field, number/factor)
    value.valueChanged.connect(changed)
    slider.valueChanged.connect(lambda number: value.setValue(number/1000*factor))
    row.addWidget(slider, 1); row.addWidget(value)
    if field == 'controller_output_deadzone':
        center.controller_deadzone_control = control
    return control
