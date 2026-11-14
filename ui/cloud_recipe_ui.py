"""Edit the full Cloud trainer recipe from its own schema."""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QLabel,
                               QLineEdit, QMessageBox, QScrollArea,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from cloud_recipe import MODEL_FIELDS, load_recipe, resolve_recipe, save_recipe, schema


class RecipeDialog(QDialog):
    def __init__(self, suite):
        super().__init__(suite.center)
        self.suite = suite
        self.t = suite.center.t
        self.controls = {}
        self.setWindowTitle(self.t('.cloud training recipe'))
        self.resize(760, 580)
        layout = QVBoxLayout(self)
        preset = self.preset = QComboBox()
        preset.addItem(self.t('S2 Player'), 'player')
        preset.addItem(self.t('S1 Legacy'), 'legacy')
        top = QFormLayout()
        top.addRow(self.t('Preset'), preset)
        layout.addLayout(top)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        specs = schema()
        forms = {}
        for group in specs.GROUPS:
            page = QWidget()
            forms[group] = QFormLayout(page)
            forms[group].setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, self.t(group))
        for setting in specs.SETTINGS:
            if setting.name == 'allow_no_validation':
                continue
            control = self.create_control(setting)
            control.setToolTip(self.t(setting.help))
            control.setAccessibleName(self.t(setting.label))
            forms[setting.group].addRow(self.t(setting.label), control)
            self.controls[setting.name] = control
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        for label, slot in (('Import recipe…', self.import_recipe), ('Export recipe…', self.export_recipe),
                            ('Reset preset', self.reset_preset)):
            action = buttons.addButton(self.t(label), QDialogButtonBox.ButtonRole.ActionRole)
            action.clicked.connect(slot)
        layout.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        preset.currentIndexChanged.connect(self.reset_preset)
        self.controls['architecture'].currentIndexChanged.connect(self.update_model_controls)
        settings = suite.center.settings
        self.load_values(settings.cloud_recipe_preset, resolve_recipe(settings.cloud_recipe_preset, suite.draft_overrides()))

    def create_control(self, setting):
        if setting.name == 'device' or setting.name == 'seed':
            return QLineEdit()
        if setting.kind == 'bool':
            return QCheckBox()
        if setting.kind == 'choice':
            control = QComboBox()
            for entry in setting.choices:
                label, value = entry if isinstance(entry, tuple) else (str(entry), entry)
                control.addItem(self.t(label), value)
            return control
        control = QSpinBox() if setting.kind == 'int' else QDoubleSpinBox()
        if setting.kind == 'float':
            control.setDecimals(setting.decimals)
        control.setRange(setting.minimum, setting.maximum)
        control.setSingleStep(setting.step)
        if setting.name == 'batch_size':
            control.setSpecialValueText(self.t('Automatic'))
        return control

    def load_values(self, preset, values):
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(self.preset.findData(preset))
        self.preset.blockSignals(False)
        for name, control in self.controls.items():
            value = values[name]
            if isinstance(control, QCheckBox):
                control.setChecked(value)
            elif isinstance(control, QComboBox):
                control.setCurrentIndex(control.findData(value))
            elif isinstance(control, QLineEdit):
                control.setText(str(value))
            else:
                control.setValue(value)
        self.update_model_controls()

    def update_model_controls(self):
        initialized = self.suite.center.settings.cloud_mode == 'initialize'
        self.message.setText(self.t('Model dimensions are inherited from the initial .cloud file.') if initialized else '')
        self.message.setVisible(initialized)
        s2 = self.controls['architecture'].currentData() == 'cloudvision_s2_v1'
        for key in MODEL_FIELDS:
            available = key not in ('vertices', 'roi_size') if s2 else key != 'mask_size'
            self.controls[key].setEnabled(not initialized and available)
        for key in ('mask_bce_weight', 'mask_dice_weight', 'mask_boundary_weight',
                    'background_rois_per_image', 'negative_mask_weight', 'mask_target_samples'):
            self.controls[key].setEnabled(initialized or s2)
        self.controls['contour_edge_weight'].setEnabled(initialized or not s2)

    def values(self):
        values = {}
        for name, control in self.controls.items():
            if isinstance(control, QCheckBox):
                values[name] = control.isChecked()
            elif isinstance(control, QComboBox):
                values[name] = control.currentData()
            elif isinstance(control, QLineEdit):
                values[name] = int(control.text()) if name == 'seed' else control.text().strip()
            else:
                values[name] = control.value()
        return self.preset.currentData(), values

    def reset_preset(self):
        preset = self.preset.currentData()
        self.load_values(preset, resolve_recipe(preset, {}))

    def import_recipe(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t('Import .cloud training recipe'), '', 'JSON (*.json)')
        if path:
            try:
                self.load_values(*load_recipe(path))
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, self.windowTitle(), str(error))

    def export_recipe(self):
        path, _ = QFileDialog.getSaveFileName(self, self.t('Export .cloud training recipe'), 'cloud-recipe.json', 'JSON (*.json)')
        if path:
            try:
                save_recipe(path, *self.values())
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, self.windowTitle(), str(error))

    def accept(self):
        try:
            self.suite.apply_recipe(*self.values())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        super().accept()
