"""Typed editor for the existing training hyperparameter allowlist."""
from dataclasses import asdict
import json
from training_security import _BOUNDS, _BOOL, load_extra_arguments

# Suggested override values, not promises about SDK defaults. Unchecked fields
# are absent from the worker arguments, including false and zero overrides.
SUGGESTED = dict(momentum=.937, box=7.5, cls=.5, dfl=1.5, pose=12, kobj=1,
    multi_scale=0, hsv_h=.015, hsv_s=.7, hsv_v=.4, degrees=0, translate=.1,
    scale=.5, shear=0, perspective=0, flipud=0, fliplr=.5, bgr=0, mosaic=1,
    mixup=0, cutmix=0, copy_paste=0, erasing=.4,
    rect=False, overlap_mask=True, single_cls=False)
GROUPS = (
    ('Optimization and loss', ('momentum','box','cls','dfl','pose','kobj')),
    ('Image augmentation', ('multi_scale','hsv_h','hsv_s','hsv_v','degrees','translate',
        'scale','shear','perspective','flipud','fliplr','bgr','mosaic','mixup','cutmix','copy_paste','erasing')),
    ('Dataset behavior', ('rect','overlap_mask','single_cls')),
)


def open_editor(center):
    from vapor_lock_gui import appearance_palette
    if False or False or getattr(center, '_tuning_locked', False): return
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
        QDoubleSpinBox, QScrollArea, QWidget, QDialogButtonBox, QGroupBox)
    owner = center.settings; stamp = asdict(owner)
    epoch = getattr(center, '_tuning_epoch', 0)
    profile = getattr(center, '_active_profile_path', None)
    generation = getattr(center, '_start_intent_generation', 0)
    try: values = load_extra_arguments(owner.training_extra_arguments)
    except ValueError:
        center.log('WARN', center.t('Advanced training settings are invalid. Load a valid profile before editing.')); return
    dialog = QDialog(center); dialog.setObjectName('advancedTrainingDialog')
    dialog.setWindowTitle(center.t('Advanced training settings')); dialog.resize(740, 600)
    colors = appearance_palette(center.ui_appearance, True)
    dialog.setStyleSheet(f'QDialog#advancedTrainingDialog {{background:{colors["carbon"]};color:{colors["ice"]};}}')
    layout = QVBoxLayout(dialog)
    hint = QLabel(center.t('Check an option to override it. Unchecked options use the training SDK defaults. Suggested values are not active until checked. Changes apply to the next training run.'))
    hint.setWordWrap(True); layout.addWidget(hint)
    area = QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QScrollArea.Shape.NoFrame)
    body = QWidget(); content = QVBoxLayout(body); area.setWidget(body); layout.addWidget(area)
    controls = {}; edited = set()
    for title, keys in GROUPS:
        group = QGroupBox(center.t(title)); rows = QVBoxLayout(group); content.addWidget(group)
        for key in keys:
            row = QHBoxLayout(); rows.addLayout(row)
            override = QCheckBox(key); override.setObjectName('override_'+key)
            override.setToolTip(center.t('Unchecked: use the training SDK default.'))
            override.setChecked(key in values); row.addWidget(override, 1)
            if key in _BOOL:
                value = QCheckBox(center.t('Enabled')); value.setChecked(values.get(key, SUGGESTED[key]))
            else:
                value = QDoubleSpinBox(); value.setDecimals(6); value.setRange(*_BOUNDS[key])
                value.setSingleStep(.0001 if key == 'perspective' else .01)
                value.setKeyboardTracking(False); value.setValue(values.get(key, SUGGESTED[key]))
                value.valueChanged.connect(lambda _number, name=key: edited.add(name))
                value.lineEdit().textEdited.connect(lambda _text, name=key: edited.add(name))
                if key in values and value.value() != values[key]:
                    value.setToolTip(center.tf('Saved exact value: {value}. Editing replaces it with the displayed precision.', value=repr(values[key])))
            value.setObjectName('value_'+key); value.setAccessibleName(key)
            value.setEnabled(override.isChecked()); override.toggled.connect(value.setEnabled)
            row.addWidget(value); controls[key] = override, value
            mode = QLabel(center.t('Override' if override.isChecked() else 'Use SDK default'))
            mode.setObjectName('trainingOverrideMode')
            mode.setWordWrap(True); mode.setFixedWidth(160)
            row.addWidget(mode)
            override.toggled.connect(lambda checked, label=mode: label.setText(center.t('Override' if checked else 'Use SDK default')))
    content.addStretch(1)
    status = QLabel(); status.setWordWrap(True); status.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(status)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
    layout.addWidget(buttons); apply = buttons.button(QDialogButtonBox.StandardButton.Apply)
    def commit():
        current = (not getattr(center, '_tuning_locked', False) and not getattr(center, '_native_closing', False)
            and not getattr(center, '_profile_transition_pending', False) and not False
            and owner is center.settings and stamp == asdict(center.settings)
            and profile == getattr(center, '_active_profile_path', None)
            and epoch == getattr(center, '_tuning_epoch', 0)
            and generation == getattr(center, '_start_intent_generation', 0))
        if not current:
            status.setText(center.t('Settings changed. Close and reopen advanced training settings.')); apply.setEnabled(False); return
        for key, (_, control) in controls.items():
            if key not in _BOOL: control.interpretText()
        result = {key: (control.isChecked() if key in _BOOL else control.value())
            for key, (override, control) in controls.items() if override.isChecked()}
        # Preserve valid historical precision when an editor was not changed.
        for key in result:
            if key not in _BOOL and key in values and key not in edited: result[key] = values[key]
        raw = json.dumps(result, separators=(',', ':'), sort_keys=True)
        load_extra_arguments(raw)
        center.change('training_extra_arguments', raw)
        dialog.accept()
    apply.clicked.connect(commit); buttons.rejected.connect(dialog.reject)
    dialog.exec()
