"""Browse .cloud runs, reuse artifacts, and evaluate reviewed splits."""
from pathlib import Path
import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from cloud_results import EVALUATION_METRICS, METRICS, artifacts, cloud_run_names, read_run
from mission_layout import ActionFlow
from training_curves_ui import TrainingCurves


class CloudResults(QWidget):
    def __init__(self, suite):
        super().__init__()
        self.suite = suite
        self.t = suite.center.t
        self.model = None
        self.selected = {}
        self.custom_model = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.curves = TrainingCurves(suite.center, suite.output_parent(), metrics=METRICS, reader=read_run,
                                     list_runs=cloud_run_names)
        layout.addWidget(self.curves)
        self.curves.runs.currentIndexChanged.connect(self.update_run)
        self.curves.refresh_button.clicked.connect(lambda: self.refresh_runs())
        actions = QWidget()
        flow = ActionFlow(actions)
        self.open_run = self.button(flow, 'Open run folder', self.open_folder)
        self.use_model = self.button(flow, 'Train from best model', lambda: suite.use_result(self.selected['best_cloud'], 'initialize'))
        self.resume = self.button(flow, 'Resume checkpoint', lambda: suite.use_result(self.selected['checkpoint'], 'resume'))
        self.publish = self.button(flow, 'Add best model to library', lambda: suite.publish_model(self.selected['best_cloud']))
        layout.addWidget(actions)
        form = QFormLayout()
        self.model_label = QLabel()
        self.model_label.setTextFormat(Qt.TextFormat.PlainText)
        self.model_label.setWordWrap(True)
        self.model_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(self.t('Model to analyze'), self.model_label)
        self.split = QComboBox()
        self.split.addItem(self.t('Validation'), 'val')
        self.split.addItem(self.t('Test'), 'test')
        form.addRow(self.t('Reviewed split'), self.split)
        self.confidence = QDoubleSpinBox()
        self.confidence.setRange(0, 1)
        self.confidence.setDecimals(3)
        self.confidence.setSingleStep(.05)
        self.confidence.setValue(.35)
        form.addRow(self.t('Confidence'), self.confidence)
        layout.addLayout(form)
        actions = QWidget()
        flow = ActionFlow(actions)
        self.choose = self.button(flow, 'Choose .cloud model…', self.choose_model)
        self.evaluate = self.button(flow, 'Evaluate model', self.start_evaluation)
        self.export = self.button(flow, 'Export ONNX graph pair…', self.choose_export_folder)
        self.cancel = self.button(flow, 'Cancel operation', suite.stop)
        layout.addWidget(actions)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        inspection = QGroupBox(self.t('Image inspection'))
        controls = QFormLayout(inspection)
        self.iou = QDoubleSpinBox()
        self.iou.setRange(0, 1)
        self.iou.setDecimals(3)
        self.iou.setSingleStep(.05)
        self.iou.setValue(.5)
        controls.addRow(self.t('Box NMS overlap'), self.iou)
        self.maximum = QSpinBox()
        self.maximum.setRange(1, 256)
        self.maximum.setValue(32)
        controls.addRow(self.t('Maximum detections'), self.maximum)
        self.min_height = QSpinBox()
        self.min_height.setRange(0, 32768)
        self.min_height.setSuffix(' px')
        controls.addRow(self.t('Minimum detection height'), self.min_height)
        actions = QWidget()
        flow = ActionFlow(actions)
        self.inspect = self.button(flow, 'Inspect image…', self.choose_image)
        self.save_report = self.button(flow, 'Save inspection JSON…', self.export_inspection)
        controls.addRow(actions)
        self.inspection_summary = QLabel()
        self.inspection_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.inspection_summary.setWordWrap(True)
        controls.addRow(self.inspection_summary)
        layout.addWidget(inspection)
        self.update_run()
        self.show_evaluation(suite.evaluation)
        self.show_inspection(suite.inspection)

    def button(self, flow, label, slot):
        button = QPushButton(self.t(label))
        button.clicked.connect(slot)
        flow.addWidget(button)
        return button

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_runs()

    def refresh_runs(self, selected=None):
        root = self.suite.output_parent()
        if root != self.curves.root:
            self.curves.root = root
            self.curves.runs.clear()
        self.curves.refresh()
        if selected:
            index = self.curves.runs.findData(Path(selected).name)
            if index >= 0:
                self.curves.runs.setCurrentIndex(index)
        self.update_run(reset_model=bool(selected))

    def update_run(self, index=None, *, reset_model=False):
        if index is not None or reset_model:
            self.custom_model = None
        name = self.curves.runs.currentData()
        try:
            self.selected = artifacts(self.curves.root, name) if name else {}
        except (OSError, ValueError):
            self.selected = {}
        self.set_model(self.custom_model or self.selected.get('best_cloud') or self.selected.get('last_cloud'))

    def set_model(self, path):
        self.model = Path(path) if path else None
        self.model_label.setText(self.model.name if self.model else self.t('Choose a model or select a saved run.'))
        self.model_label.setToolTip(str(self.model) if self.model else '')
        self.update_actions()

    def update_actions(self):
        active = self.suite.job is not None and not self.suite.job.done
        analyzing = active and self.suite.job.request['operation'] in ('evaluate', 'inspect', 'export')
        idle = not active and not self.suite.reason()
        self.open_run.setEnabled(bool(self.selected))
        self.use_model.setEnabled(idle and 'best_cloud' in self.selected)
        self.resume.setEnabled(idle and 'checkpoint' in self.selected)
        self.publish.setEnabled(idle and 'best_cloud' in self.selected)
        self.choose.setEnabled(idle)
        self.evaluate.setEnabled(idle and self.model is not None and bool(self.suite.center.settings.cloud_project))
        self.inspect.setEnabled(idle and self.model is not None)
        self.export.setEnabled(idle and self.model is not None)
        self.save_report.setEnabled(bool(self.suite.inspection))
        self.cancel.setVisible(analyzing)

    def open_folder(self):
        from profile_library import checked_path
        name = self.curves.runs.currentData()
        if name:
            try:
                os.startfile(checked_path(self.curves.root, name))
            except (OSError, ValueError) as error:
                self.summary.setText(str(error))

    def choose_model(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t('Choose .cloud evaluation model'), '', '.cloud (*.cloud)')
        if path:
            self.custom_model = Path(path)
            self.set_model(self.custom_model)

    def start_evaluation(self):
        if self.model:
            self.suite.evaluate(self.model, self.split.currentData(), self.confidence.value())

    def choose_export_folder(self):
        if self.model:
            path = QFileDialog.getExistingDirectory(self, self.t('Choose ONNX export parent folder'))
            if path:
                self.suite.export_model(self.model, path)

    def show_evaluation(self, value):
        if not value:
            self.summary.clear()
            return
        lines = [value['model'], self.suite.center.tf('{split} · {images} reviewed images · confidence {confidence}',
                 split=self.t('Validation' if value['split'] == 'val' else 'Test'),
                 images=value['images'], confidence=f"{value['confidence']:.3g}")]
        lines.extend(f"{self.t(label)}  {value['metrics'][key]:.4g}"
                     for key, label in EVALUATION_METRICS.items() if value['metrics'][key] is not None)
        self.summary.setText('\n'.join(lines))

    def choose_image(self):
        if not self.model:
            return
        path, _ = QFileDialog.getOpenFileName(self, self.t('Inspect image'), '', 'Images (*.png *.jpg *.jpeg *.bmp *.webp)')
        if path:
            self.suite.inspect_image(self.model, path, self.confidence.value(), self.iou.value(),
                                     self.maximum.value(), self.min_height.value())

    def show_inspection(self, value):
        if not value:
            self.inspection_summary.clear()
        else:
            self.inspection_summary.setText(self.suite.center.tf(
                '{image} · {model}\n{width} × {height} · {device} · {ms} ms\n'
                '{boxes} candidate boxes · {targets} detections · {empty} empty masks · {invalid} invalid contours',
                image=value['image'], model=value['model'], width=value['width'], height=value['height'],
                device=value['device'], ms=f"{value['timing_ms']['total']:.1f}", boxes=value['primary_count'],
                targets=len(value['detections']), empty=value['empty_masks'], invalid=value['invalid_contours']))
        self.update_actions()

    def export_inspection(self):
        if not self.suite.inspection:
            return
        path, _ = QFileDialog.getSaveFileName(self, self.t('Save image inspection'), 'cloud-inspection.json', 'JSON (*.json)')
        if path:
            from dataset_paths import reject_reparse
            from dataset_storage import atomic_text
            try:
                atomic_text(reject_reparse(path), json.dumps(self.suite.inspection, indent=2, allow_nan=False) + '\n')
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, self.t('Save image inspection'), str(error))
