"""Local Ultralytics runtime and training through the existing worker."""

import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer
from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QWidget,
)

from training_security import MAX_RECORD, Redactor, clean_environment, credential_packet, load_extra_arguments


ADVANCED_OPTIONS = (
    'optimizer', 'lr0', 'lrf', 'weight_decay', 'warmup_epochs', 'close_mosaic',
    'cache', 'seed', 'fraction',
)
BOOLEAN_OPTIONS = ('amp', 'pretrained', 'cos_lr', 'deterministic', 'export_onnx', 'dynamic_onnx')


def training_options(settings):
    result = []
    for key in ADVANCED_OPTIONS:
        result.extend(('--' + key.replace('_', '-'), str(getattr(settings, 'training_' + key))))
    for key in BOOLEAN_OPTIONS:
        prefix = '--' if getattr(settings, 'training_' + key) else '--no-'
        result.append(prefix + key.replace('_', '-'))
    extra = settings.training_extra_arguments.strip()
    if extra:
        load_extra_arguments(extra)
        result.extend(('--extra', extra))
    return result


class YoloTraining(QWidget):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.worker = None
        self.operation = ''
        self.buffer = b''
        self.discard_line = False
        self.receipt = None
        self.reported_error = False
        self.diagnostic = ''
        self.stopping = False
        self.runtime_ready = False
        self.profile_values = {}
        self.advanced = []

        self.runtime = QLabel('Runtime status has not been checked.')
        from PySide6.QtCore import Qt
        self.runtime.setTextFormat(Qt.TextFormat.PlainText)
        self.runtime.setWordWrap(True)
        self.status = QPushButton('Check runtime')
        self.status.clicked.connect(lambda: self.launch('status'))
        self.install = QPushButton('Install runtime')
        self.install.clicked.connect(lambda: self.launch('install'))
        runtime_actions = QHBoxLayout()
        runtime_actions.addWidget(self.runtime, 1)
        runtime_actions.addWidget(self.status)
        runtime_actions.addWidget(self.install)

        self.model = QLineEdit()
        self.data = QLineEdit()
        self.name = QLineEdit('vaporlock-training')
        self.name.setMaxLength(64)
        self.epochs = self.number(1, 10000, 100)
        self.image_size = self.number(32, 8192, 640)
        self.batch = QLineEdit('auto')
        self.batch.setMaxLength(8)
        self.device = QLineEdit('auto')
        self.device.setMaxLength(32)
        self.workers = self.number(0, 64, 8)
        self.patience = self.number(0, 10000, 100)
        self.form = QWidget()
        form = QFormLayout(self.form)
        form.addRow('Base model', self.path_field(self.model, 'Choose base model', 'YOLO model (*.pt *.yaml *.yml)'))
        form.addRow('Dataset', self.path_field(self.data, 'Choose prepared YOLO dataset', 'Dataset (data.yaml)'))
        form.addRow('Run name', self.name)
        form.addRow('Epochs', self.epochs)
        form.addRow('Image size', self.image_size)
        form.addRow('Batch size', self.batch)
        form.addRow('Device', self.device)
        form.addRow('Data workers', self.workers)
        form.addRow('Patience', self.patience)
        self.fields = {
            'base_model': self.model, 'dataset': self.data, 'name': self.name, 'epochs': self.epochs,
            'imgsz': self.image_size, 'batch': self.batch, 'device': self.device, 'workers': self.workers,
            'patience': self.patience,
        }
        self.profile_values = self.current_values()

        self.log = QPlainTextEdit(readOnly=True)
        self.log.document().setMaximumBlockCount(300)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.preflight = QPushButton('Check dataset')
        self.preflight.clicked.connect(self.check_dataset)
        self.train = QPushButton('Start training')
        self.train.setObjectName('primary')
        self.train.clicked.connect(self.start_training)
        self.stop = QPushButton('Stop')
        self.stop.clicked.connect(self.stop_worker)
        commands = QHBoxLayout()
        commands.addWidget(self.preflight)
        commands.addStretch()
        commands.addWidget(self.stop)
        commands.addWidget(self.train)

        layout = QGridLayout(self)
        layout.addLayout(runtime_actions, 0, 0, 1, 2)
        layout.addWidget(self.form, 1, 0)
        layout.addWidget(self.log, 1, 1)
        layout.addWidget(self.progress, 2, 0, 1, 2)
        layout.addLayout(commands, 3, 0, 1, 2)
        layout.setColumnStretch(1, 1)
        self.update_actions()

    @staticmethod
    def number(low, high, value):
        field = QSpinBox()
        field.setRange(low, high)
        field.setValue(value)
        return field

    def path_field(self, field, title, file_filter):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        button = QPushButton('Browse')

        def choose():
            selected, _ = QFileDialog.getOpenFileName(self, title, field.text() or str(self.paths.root), file_filter)
            if selected:
                field.setText(selected)

        button.clicked.connect(choose)
        layout.addWidget(button)
        return widget

    def current_values(self):
        return {key: field.value() if isinstance(field, QSpinBox) else field.text() for key, field in self.fields.items()}

    def apply_settings(self, settings):
        current = self.current_values()
        defaults = {key: getattr(settings, 'training_' + key) for key in self.fields}
        for key in ('base_model', 'dataset'):
            value = defaults[key]
            if value and not value.startswith('ul://'):
                defaults[key] = str(self.paths.root / value)
        for key, field in self.fields.items():
            if current[key] != self.profile_values[key]:
                continue
            if isinstance(field, QSpinBox):
                field.setValue(defaults[key])
            else:
                field.setText(defaults[key])
        self.profile_values = defaults
        self.advanced = training_options(settings)

    def update_actions(self):
        busy = self.worker is not None
        self.form.setEnabled(not busy)
        self.status.setEnabled(not busy)
        self.install.setEnabled(not busy)
        self.preflight.setEnabled(not busy)
        self.train.setEnabled(not busy and self.runtime_ready)
        self.stop.setEnabled(busy and not self.stopping)

    def dataset_path(self):
        text = self.data.text().strip()
        path = self.paths.root / text
        if not text or path.name.lower() != 'data.yaml' or not path.is_file():
            raise ValueError('Choose the data.yaml file for a prepared YOLO dataset.')
        return str(path)

    def check_dataset(self):
        try:
            self.launch('preflight', '--data', self.dataset_path())
        except (OSError, ValueError) as error:
            self.log.appendPlainText(str(error))

    def train_arguments(self):
        data = self.dataset_path()
        model = self.model.text().strip()
        if model.startswith('ul://'):
            raise ValueError('Choose a local .pt checkpoint or YAML model definition for local training.')
        model = self.paths.root / model
        if not model.is_file() or model.suffix.lower() not in ('.pt', '.yaml', '.yml'):
            raise ValueError('Choose a local .pt checkpoint or YAML model definition.')
        batch = self.batch.text().strip().lower()
        if batch != 'auto' and (not batch.isdecimal() or not 1 <= int(batch) <= 4096):
            raise ValueError('Use auto or a positive batch size up to 4096.')
        device = self.device.text().strip().lower()
        if not device:
            raise ValueError('Enter a training device such as auto, cpu, or 0.')
        return ['--model', str(model), '--data', data, '--name', self.name.text(),
                '--epochs', str(self.epochs.value()), '--imgsz', str(self.image_size.value()),
                '--batch', batch, '--device', device, '--workers', str(self.workers.value()),
                '--patience', str(self.patience.value()), *self.advanced]

    def start_training(self):
        try:
            self.launch('train', *self.train_arguments())
        except (OSError, ValueError) as error:
            self.log.appendPlainText(str(error))

    def worker_command(self):
        executable = self.paths.component_root / 'VaporLockTrainingTools.exe'
        if executable.is_file():
            return str(executable), []
        script = self.paths.root / 'ui' / 'training_worker.py'
        if not script.is_file() or getattr(sys, 'frozen', False):
            raise ValueError('The YOLO training worker is missing from this installation.')
        return sys.executable, ['-E', '-s', '-B', str(script)]

    def launch(self, operation, *arguments):
        if self.worker is not None:
            return
        try:
            program, prefix = self.worker_command()
        except ValueError as error:
            self.log.appendPlainText(str(error))
            return
        process = QProcess(self)
        self.worker = process
        self.operation = operation
        self.buffer = b''
        self.discard_line = False
        self.receipt = None
        self.reported_error = False
        self.diagnostic = ''
        self.stopping = False
        environment = QProcessEnvironment()
        for key, value in clean_environment(dict(os.environ)).items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(self.paths.root))
        process.started.connect(lambda: self.worker_started(process, operation))
        process.readyReadStandardOutput.connect(self.read_output)
        process.readyReadStandardError.connect(self.read_error)
        process.errorOccurred.connect(lambda error: self.worker_failed(process, error))
        process.finished.connect(self.worker_finished)
        self.progress.setRange(0, 0)
        self.log.appendPlainText({'status': 'Checking the private runtime.', 'install': 'Installing the private runtime.',
                                  'preflight': 'Checking the local dataset.', 'train': 'Starting local training.'}[operation])
        self.update_actions()
        process.start(program, [*prefix, '--root', str(self.paths.root), operation, *arguments])

    def worker_started(self, process, operation):
        if operation == 'train':
            packet = credential_packet('')
            if process.write(packet) != len(packet):
                self.log.appendPlainText('The local training worker could not receive its start request.')
                self.stop_worker()
        process.closeWriteChannel()

    def read_output(self):
        if self.worker is None:
            return
        lines = (self.buffer + bytes(self.worker.readAllStandardOutput())).split(b'\n')
        self.buffer = lines.pop()
        for line in lines:
            if not self.discard_line and len(line) <= MAX_RECORD:
                try:
                    event = json.loads(line)
                except ValueError:
                    pass
                else:
                    if isinstance(event, dict):
                        self.show_event(event)
            self.discard_line = False
        if len(self.buffer) > MAX_RECORD:
            self.buffer = b''
            self.discard_line = True

    def read_error(self):
        if self.worker is not None:
            received = bytes(self.worker.readAllStandardError()).decode('utf-8', errors='replace')
            self.diagnostic = (self.diagnostic + received)[-4096:]

    def show_event(self, event):
        kind = event.get('type')
        message = event.get('message')
        if isinstance(message, str) and message:
            self.log.appendPlainText(message[:2048])
        if kind == 'error':
            self.reported_error = True
        if kind in ('ready', 'runtime', 'preflight', 'trained'):
            self.receipt = event
        if kind == 'preflight':
            self.log.appendPlainText(f"Training {event.get('train_images', 0)} | Validation {event.get('validation_images', 0)}"
                                     f" | Labeled {event.get('labeled', 0)} | Classes {event.get('classes', 0)}")
            for key in ('problems', 'warnings'):
                values = event.get(key)
                if isinstance(values, list):
                    for item in values[:12]:
                        self.log.appendPlainText(str(item)[:1024])
            recommendation = event.get('recommendation')
            if isinstance(recommendation, str):
                self.log.appendPlainText(recommendation[:1024])
        if kind == 'training-progress':
            current, total = event.get('current'), event.get('total')
            if type(current) is int and type(total) is int and 0 <= current <= total <= 10000:
                self.progress.setRange(0, total)
                self.progress.setValue(current)

    def stop_worker(self):
        process = self.worker
        if process is None or self.stopping:
            return
        self.stopping = True
        self.update_actions()
        self.log.appendPlainText('Stopping the worker and its owned processes.')
        process.terminate()
        QTimer.singleShot(2000, lambda: process.kill() if self.worker is process else None)

    def worker_failed(self, process, error):
        if self.worker is process and error == QProcess.ProcessError.FailedToStart:
            self.log.appendPlainText(process.errorString())
            self.reported_error = True
            self.worker_finished(1, QProcess.ExitStatus.CrashExit)

    def worker_finished(self, code, status):
        process = self.worker
        if process is None:
            return
        self.read_output()
        self.read_error()
        self.worker = None
        process.deleteLater()
        receipt = self.receipt or {}
        expected = {'status': 'runtime', 'install': 'ready', 'preflight': 'preflight', 'train': 'trained'}[self.operation]
        success = (not self.stopping and not self.reported_error and code == 0
                   and status == QProcess.ExitStatus.NormalExit and receipt.get('type') == expected)
        if self.operation in ('status', 'install'):
            if success:
                self.runtime_ready = expected == 'ready' or receipt.get('ready') is True
                self.runtime.setText(str(receipt.get('detail') or ('Runtime ready.' if self.runtime_ready else 'Runtime unavailable.')))
            elif self.operation == 'status':
                self.runtime_ready = False
                self.runtime.setText('Runtime check failed.')
        if success and self.operation == 'train':
            for key, title in (('checkpoint', 'Checkpoint'), ('onnx', 'ONNX export'), ('run', 'Training run')):
                if isinstance(receipt.get(key), str):
                    self.log.appendPlainText(f'{title} {receipt[key]}')
            if receipt.get('onnx') and receipt.get('onnx_validated') is False:
                self.log.appendPlainText('The ONNX export was created but its structure could not be verified.')
        if not success and not self.reported_error:
            if self.stopping:
                self.log.appendPlainText('Worker stopped.')
            elif self.diagnostic.strip():
                self.log.appendPlainText(Redactor().text(self.diagnostic.strip().splitlines()[-1]))
            else:
                self.log.appendPlainText('The worker ended without a completion report.')
        self.progress.setRange(0, 1)
        self.progress.setValue(int(success))
        self.update_actions()
