"""Native dataset review and Cloud training screens."""
from collections import deque
from pathlib import Path
import json
import os
import re
import secrets

from PySide6.QtCore import (QByteArray, QBuffer, QIODevice, QPointF, QProcess, QTimer,
                            QRectF, QSize, Qt, Signal)
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from dataset_paths import display_path, reject_reparse
from dataset_transaction import AnnotationCommitUncertain
from review_document import ReviewDocument, serialize, to_annotations


IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
MAX_REVIEW_IMAGES = 10_000


def review_image(data):
    source = QBuffer()
    source.setData(QByteArray(data))
    source.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(source)
    reader.setAutoTransform(False)
    size = reader.size()
    if size.isEmpty() or size.width() * size.height() > 64_000_000:
        raise ValueError('The image exceeds the review size limit or has an unsupported format.')
    if max(size.width(), size.height()) > 1920:
        reader.setScaledSize(size.scaled(QSize(1920, 1920), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError(reader.errorString())
    return image


class ReviewCanvas(QWidget):
    box_drawn = Signal(object)
    vertex = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('datasetCanvas')
        self.setMinimumSize(320, 240)
        self.image = QImage()
        self.annotations = []
        self.selected = -1
        self.start = self.end = None
        self.drawing = False
        self.tracing = False
        self.vertices = []

    def frame(self):
        if self.image.isNull():
            return QRectF()
        area = self.rect().adjusted(12, 12, -12, -12)
        if area.isEmpty():
            return QRectF()
        size = self.image.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF(area.center().x() - size.width() / 2, area.center().y() - size.height() / 2,
                      size.width(), size.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#202a25'))
        frame = self.frame()
        if frame.isEmpty():
            painter.setPen(QColor('#e5e9db'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Choose an image to review')
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(frame, self.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        def point(x, y):
            return QPointF(frame.left() + x * frame.width(), frame.top() + y * frame.height())

        for index, item in enumerate(self.annotations):
            selected = index == self.selected
            suggested = item.get('suggested', False)
            color = '#ffca73' if selected else '#87bdf0' if suggested else '#8ef0ce'
            style = Qt.PenStyle.DashLine if suggested else Qt.PenStyle.SolidLine
            painter.setPen(QPen(QColor(color), 3 if selected else 2, style))
            if item['kind'] == 'polygon':
                painter.drawPolygon(QPolygonF([point(p.x(), p.y()) for p in item['points']]))
            else:
                x, y, w, h = (item[k] for k in ('x', 'y', 'w', 'h'))
                painter.drawRect(QRectF(point(x, y), point(x + w, y + h)))
                if item.get('mask') is not None:
                    mask, area = item['mask']
                    painter.drawImage(QRectF(point(*area[:2]), point(*area[2:])), mask)
                if item.get('wrapper'):
                    painter.drawPolygon(QPolygonF([point(*p) for p in item['wrapper']]))
        if self.vertices:
            painter.setPen(QPen(QColor('#ffca73'), 2))
            points = [point(*vertex) for vertex in self.vertices]
            painter.drawPolyline(QPolygonF(points))
            for vertex in points:
                painter.drawEllipse(vertex, 3, 3)
        if self.start is not None and self.end is not None:
            painter.setPen(QPen(QColor('#ffca73'), 2, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(self.start, self.end).normalized())

    def set_drawing(self, enabled):
        self.drawing = enabled
        self.start = self.end = None
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def mousePressEvent(self, event):
        frame = self.frame()
        if self.tracing and event.button() == Qt.MouseButton.LeftButton and frame.contains(event.position()):
            self.vertex.emit(((event.position().x() - frame.x()) / frame.width(),
                              (event.position().y() - frame.y()) / frame.height()))
            return
        if self.drawing and event.button() == Qt.MouseButton.LeftButton and frame.contains(event.position()):
            self.start = self.end = event.position()
            self.update()

    def mouseMoveEvent(self, event):
        if self.start is None:
            return
        frame = self.frame()
        self.end = QPointF(min(max(frame.left(), event.position().x()), frame.right()),
                           min(max(frame.top(), event.position().y()), frame.bottom()))
        self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.mouseMoveEvent(event)
        box = QRectF(self.start, self.end).normalized()
        frame = self.frame()
        self.start = self.end = None
        self.update()
        if not frame.isEmpty() and box.width() >= 3 and box.height() >= 3:
            self.box_drawn.emit({'kind': 'box', 'x': (box.x() - frame.x()) / frame.width(),
                                 'y': (box.y() - frame.y()) / frame.height(),
                                 'w': box.width() / frame.width(), 'h': box.height() / frame.height()})


class DatasetReview(QWidget):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.root = self.document = None
        self.records = []
        self.annotations = []
        self.loaded = -1
        self.dirty = False
        self.worker = None
        self.worker_output = b''
        self.worker_error = ''
        self.worker_stopping = False
        self.suggestions = []
        self.suggestion_request = None
        self.suggestion_result = None
        self.annotation_model = ''
        self.annotation_arguments = []
        self.assistance_arguments = []

        folder = QPushButton('Open dataset')
        folder.clicked.connect(self.choose_dataset)
        self.location = QLabel('Choose a dataset with an images folder')
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(folder)
        header.addWidget(self.location, 1)
        self.auto = QPushButton('Annotate dataset')
        self.auto.clicked.connect(self.auto_annotate)
        header.addWidget(self.auto)
        self.cancel_auto = QPushButton('Stop')
        self.cancel_auto.clicked.connect(self.stop_auto_annotation)
        header.addWidget(self.cancel_auto)

        self.images = QListWidget()
        self.images.currentRowChanged.connect(self.select_image)
        self.canvas = ReviewCanvas()
        self.canvas.box_drawn.connect(self.add_box)
        self.items = QListWidget()
        self.items.setMaximumHeight(155)
        self.items.currentRowChanged.connect(self.select_annotation)
        self.predictions = QListWidget()
        self.predictions.setMaximumHeight(96)
        self.predictions.currentRowChanged.connect(self.select_prediction)

        self.add = QPushButton('Draw box', checkable=True)
        self.add.toggled.connect(self.canvas.set_drawing)
        self.remove = QPushButton('Remove selected')
        self.remove.clicked.connect(self.remove_annotation)
        self.negative = QPushButton('Mark negative')
        self.negative.clicked.connect(self.mark_negative)
        self.apply = QPushButton('Apply edit')
        self.apply.clicked.connect(self.edit_annotation)
        self.detect = QPushButton('Suggest boxes for this image')
        self.detect.clicked.connect(self.suggest_image)
        self.accept = QPushButton('Add selected suggestion')
        self.accept.clicked.connect(self.accept_suggestion)
        self.save = QPushButton('Save labels')
        self.save.setObjectName('primary')
        self.save.clicked.connect(self.save_labels)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)

        self.classes = QComboBox()
        self.geometry = {}
        inspector = QFormLayout()
        inspector.addRow('Class', self.classes)
        for key, title in (('x', 'Left'), ('y', 'Top'), ('w', 'Width'), ('h', 'Height')):
            value = QDoubleSpinBox()
            value.setRange(0, 100)
            value.setDecimals(4)
            value.setSuffix('%')
            self.geometry[key] = value
            inspector.addRow(title, value)

        properties = QWidget()
        fields = QVBoxLayout(properties)
        fields.addWidget(QLabel('Annotations'))
        fields.addWidget(self.items)
        fields.addWidget(self.add)
        fields.addLayout(inspector)
        fields.addWidget(self.apply)
        fields.addWidget(self.remove)
        fields.addWidget(self.detect)
        fields.addWidget(self.predictions)
        fields.addWidget(self.accept)
        fields.addStretch()
        fields.addWidget(self.negative)
        fields.addWidget(self.save)

        splitter = QSplitter()
        for widget in (self.images, self.canvas, properties):
            splitter.addWidget(widget)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([155, 680, 240])
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status)
        self.update_actions()

    def choose_dataset(self):
        directory = QFileDialog.getExistingDirectory(self, 'Open dataset', display_path(self.root or self.paths.root))
        if directory:
            self.open_dataset(directory)

    def open_dataset(self, directory):
        if not self.confirm_navigation():
            return False
        try:
            root = reject_reparse(directory)
            folder = reject_reparse(root / 'images')
            records = []
            stems = set()
            for entry in folder.iterdir():
                if entry.suffix.lower() not in IMAGE_TYPES or not entry.is_file():
                    continue
                if entry.stem.casefold() in stems:
                    raise ValueError('Images sharing a filename stem also share a label. Rename the duplicates before review.')
                if len(records) >= MAX_REVIEW_IMAGES:
                    raise ValueError('Select a dataset with at most 10,000 images.')
                records.append(entry)
                stems.add(entry.stem.casefold())
            if not records:
                raise ValueError('No supported images were found directly in the dataset images folder.')
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return False
        self.root = root
        self.records = sorted(records, key=lambda path: path.name.casefold())
        self.location.setText(display_path(root))
        self.images.blockSignals(True)
        self.images.clear()
        self.images.addItems([record.name for record in self.records])
        self.images.blockSignals(False)
        self.loaded = -1
        self.dirty = False
        self.images.setCurrentRow(0)
        return True

    def confirm_navigation(self):
        if self.worker is not None:
            self.status.setText('Stop automatic annotation and wait for it to finish.')
            return False
        if not self.dirty:
            return True
        choice = QMessageBox.question(self, 'Unsaved labels', 'Save the changes to this image?',
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                     QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if choice == QMessageBox.StandardButton.Save:
            return self.save_labels()
        return choice == QMessageBox.StandardButton.Discard

    def select_image(self, index):
        if not 0 <= index < len(self.records):
            return
        if not self.confirm_navigation():
            self.images.blockSignals(True)
            self.images.setCurrentRow(self.loaded)
            self.images.blockSignals(False)
            return
        self.loaded = index
        self.dirty = False
        self.add.setChecked(False)
        self.document = None
        self.canvas.image = QImage()
        self.classes.clear()
        self.annotations = []
        self.suggestions = []
        self.predictions.clear()
        try:
            document = ReviewDocument(self.root, self.records[index])
            self.canvas.image = review_image(document.image_bytes)
            self.document = document
            self.annotations = to_annotations(document.rows)
            self.classes.addItems([f'{number}  {name}' for number, name in enumerate(document.names)])
            self.status.setText('Drag to draw a box, or select an annotation to edit its values.')
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
        self.refresh_annotations()

    def refresh_annotations(self, index=-1):
        self.canvas.annotations = [*self.annotations, *self.suggestions]
        self.items.blockSignals(True)
        self.items.clear()
        for number, annotation in enumerate(self.annotations, 1):
            label = self.document.names[annotation['class_id']]
            self.items.addItem(f"{number}. {label} · {annotation['kind']}")
        self.items.setCurrentRow(index)
        self.items.blockSignals(False)
        self.select_annotation(index)

    def select_annotation(self, index):
        self.predictions.blockSignals(True)
        self.predictions.setCurrentRow(-1)
        self.predictions.blockSignals(False)
        self.canvas.selected = index
        self.canvas.update()
        if 0 <= index < len(self.annotations):
            annotation = self.annotations[index]
            self.classes.setCurrentIndex(annotation['class_id'])
            for key, field in self.geometry.items():
                field.setValue(annotation.get(key, 0) * 100)
        self.update_actions()

    def update_actions(self):
        editable = self.worker is None and self.document is not None and not self.document.needs_refresh
        index = self.items.currentRow()
        selected = editable and 0 <= index < len(self.annotations)
        box = selected and self.annotations[index]['kind'] == 'box'
        for field in self.geometry.values():
            field.setEnabled(box)
        self.classes.setEnabled(editable)
        self.add.setEnabled(editable)
        self.negative.setEnabled(editable)
        self.remove.setEnabled(selected)
        self.apply.setEnabled(selected)
        self.save.setEnabled(editable and self.dirty)
        self.auto.setEnabled(self.root is not None and self.worker is None)
        self.cancel_auto.setEnabled(self.worker is not None and not self.worker_stopping)
        self.images.setEnabled(self.worker is None)
        self.detect.setEnabled(editable)
        self.accept.setEnabled(editable and self.predictions.currentRow() >= 0)

    def add_box(self, box):
        if self.document is None or self.document.needs_refresh or self.classes.currentIndex() < 0:
            return
        self.annotations.append({**box, 'class_id': self.classes.currentIndex()})
        self.dirty = True
        self.add.setChecked(False)
        self.refresh_annotations(len(self.annotations) - 1)

    def edit_annotation(self):
        index = self.items.currentRow()
        if not 0 <= index < len(self.annotations):
            return
        candidate = {**self.annotations[index], 'class_id': self.classes.currentIndex()}
        if candidate['kind'] == 'box':
            candidate.update({key: field.value() / 100 for key, field in self.geometry.items()})
        try:
            serialize([candidate])
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.annotations[index] = candidate
        self.dirty = True
        self.refresh_annotations(index)

    def remove_annotation(self):
        index = self.items.currentRow()
        if 0 <= index < len(self.annotations):
            del self.annotations[index]
            self.dirty = True
            self.refresh_annotations(min(index, len(self.annotations) - 1))

    def mark_negative(self):
        self.annotations = []
        self.dirty = True
        self.status.setText('Save labels to keep this image with an empty label file.')
        self.refresh_annotations()

    def save_labels(self):
        document = self.document
        if not self.dirty or document is None:
            return not self.dirty
        try:
            result = document.save(serialize(self.annotations), lambda: self.document is document)
        except AnnotationCommitUncertain:
            self.status.setText('The save needs recovery. Reopen the dataset before making further edits.')
            self.update_actions()
            return False
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return False
        self.dirty = False
        self.add.setChecked(False)
        self.annotations = to_annotations(document.rows)
        self.refresh_annotations()
        self.status.setText('Labels saved. Reopen the dataset to finish cleanup.' if result['cleanup_pending']
                            else 'Labels saved.' if self.annotations else 'Negative saved. The image is still in the dataset.')
        return True

    def auto_annotate(self):
        if self.root is None or not self.confirm_navigation():
            return
        chosen = self.choose_model()
        if chosen is None:
            return
        model, application, arguments = chosen
        if QMessageBox.question(self, 'Add model predictions',
                                "Add ONNX predictions to this dataset's label files? Existing rows and images are kept.") != QMessageBox.StandardButton.Yes:
            return
        self.suggestion_request = None
        self.dirty = False
        self.launch_annotation(application, [*arguments, 'annotate', '--additive', '--provider', 'cpu',
                                             '--dataset', display_path(self.root), '--model', display_path(model),
                                             *self.annotation_arguments])

    def apply_settings(self, settings):
        model = settings.dataset_annotation_model or settings.model_path
        self.annotation_model = str(self.paths.root / model) if model and Path(model).suffix.lower() == '.onnx' else ''
        confidence = settings.dataset_annotation_confidence
        nms_iou = settings.dataset_annotation_nms_iou
        self.assistance_arguments = ['--confidence', str(confidence), '--nms-iou', str(nms_iou)]
        self.annotation_arguments = [*self.assistance_arguments, '--passes', str(settings.dataset_annotation_passes),
                                     '--consensus-iou', str(settings.dataset_annotation_iou),
                                     '--batch-size', str(settings.dataset_annotation_batch)]

    def choose_model(self):
        selected, _ = QFileDialog.getOpenFileName(self, 'Choose annotation model',
                                                 self.annotation_model or str(self.paths.root), 'ONNX model (*.onnx)')
        if not selected:
            return None
        try:
            model = reject_reparse(selected)
            if not model.is_file() or model.suffix.lower() != '.onnx':
                raise ValueError('Choose an existing ONNX annotation model.')
            application = self.paths.component_root / 'VaporLockDatasetTools.exe'
            arguments = []
            if not application.is_file():
                application = self.paths.root / '.venv-cloud-cpu' / 'Scripts' / 'python.exe'
                script = self.paths.root / 'ui' / 'dataset_worker.py'
                if not application.is_file() or not script.is_file():
                    raise ValueError('Prepare the CPU runtime using BuildCommunity.ps1 -CloudCPU to use ONNX suggestions.')
                arguments = ['-E', '-s', '-B', str(script)]
            return model, application, arguments
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return None

    def suggest_image(self):
        if self.document is None or self.worker is not None:
            return
        chosen = self.choose_model()
        if chosen is None:
            return
        model, application, arguments = chosen
        request = secrets.token_hex(16)
        image_digest = self.document.image_snapshot['sha256']
        self.suggestion_request = (request, image_digest)
        self.suggestion_result = None
        self.launch_annotation(application, [*arguments, 'assist', '--provider', 'cpu', '--request-id', request,
                                             '--image', display_path(self.document.image), '--image-sha256', image_digest,
                                             '--model', display_path(model), *self.assistance_arguments])

    def launch_annotation(self, application, arguments):
        process = QProcess(self)
        self.worker = process
        self.worker_output = b''
        self.worker_error = ''
        self.worker_stopping = False
        self.add.setChecked(False)
        process.setWorkingDirectory(str(self.paths.root))
        process.readyReadStandardOutput.connect(self.read_worker)
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.errorOccurred.connect(lambda error: self.worker_failed(process, error))
        process.finished.connect(self.worker_finished)
        self.status.setText('Starting ONNX annotation…')
        self.update_actions()
        process.start(str(application), arguments)

    def read_worker(self):
        if self.worker is None:
            return
        self.worker_output += bytes(self.worker.readAllStandardOutput())
        records = self.worker_output.split(b'\n')
        self.worker_output = records.pop()
        if len(self.worker_output) > 320 * 1024:
            self.worker_output = b''
            self.worker_error = 'The annotation worker emitted an oversized status record.'
            self.stop_auto_annotation()
        for record in records:
            if len(record) > 320 * 1024:
                continue
            try:
                event = json.loads(record)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'error':
                self.worker_error = str(event.get('message', 'Annotation failed.'))[:1024]
            if event.get('type') == 'assist_result':
                from assist_geometry import validate

                if self.suggestion_request == (event.get('request_id'), event.get('image_sha256')):
                    try:
                        self.suggestion_result = validate(event.get('candidates'))
                    except ValueError:
                        self.worker_error = 'The annotation worker returned invalid suggestions.'
            if event.get('type') == 'annotation_stage':
                status = 'Initializing ONNX model…'
                if 'current' in event and 'total' in event:
                    status = f"Annotating {event['current']} / {event['total']}"
                self.status.setText(status)

    def stop_auto_annotation(self):
        process = self.worker
        if process is not None and not self.worker_stopping:
            self.worker_stopping = True
            process.terminate()
            QTimer.singleShot(2000, lambda: process.kill() if self.worker is process else None)
            self.status.setText('Stopping annotation…')
            self.update_actions()

    def worker_failed(self, process, error):
        if self.worker is process and error == QProcess.ProcessError.FailedToStart:
            self.worker_error = process.errorString()
            self.worker_finished(1, QProcess.ExitStatus.CrashExit)

    def worker_finished(self, code, status):
        process = self.worker
        if process is None:
            return
        self.read_worker()
        self.worker = None
        process.deleteLater()
        success = code == 0 and status == QProcess.ExitStatus.NormalExit
        if self.suggestion_request is not None:
            self.suggestion_request = None
            if success and self.suggestion_result is not None:
                self.show_suggestions(self.suggestion_result)
                self.status.setText(f'{len(self.suggestions)} suggestions. Add the ones you want to save.')
            else:
                self.status.setText(self.worker_error or 'Image suggestions stopped or failed.')
            self.suggestion_result = None
            self.update_actions()
            return
        self.select_image(self.loaded)
        if success:
            self.status.setText('Predictions added. Review the label changes before training.')
        else:
            self.status.setText(self.worker_error or 'Annotation stopped or failed. Reopen the dataset to check its labels.')

    def show_suggestions(self, candidates):
        self.suggestions = []
        self.predictions.clear()
        for item in candidates:
            x, y, w, h = item['box']
            proposal = {'kind': item['kind'], 'class_id': item['class_id'],
                        'x': x, 'y': y, 'w': w, 'h': h, 'suggested': True}
            if item['kind'] == 'polygon':
                proposal['points'] = [QPointF(*point) for point in item['points']]
            self.suggestions.append(proposal)
            self.predictions.addItem(f"Class {item['class_id']} · {item['confidence']:.0%}")
        self.refresh_annotations(self.items.currentRow())
        if self.suggestions:
            self.predictions.setCurrentRow(0)

    def select_prediction(self, index):
        self.canvas.selected = len(self.annotations) + index if index >= 0 else self.items.currentRow()
        if 0 <= index < len(self.suggestions):
            class_id = self.suggestions[index]['class_id']
            if class_id < self.classes.count():
                self.classes.setCurrentIndex(class_id)
        self.canvas.update()
        self.update_actions()

    def accept_suggestion(self):
        index = self.predictions.currentRow()
        if not 0 <= index < len(self.suggestions):
            return
        proposal = self.suggestions.pop(index)
        proposal.pop('suggested')
        proposal['class_id'] = self.classes.currentIndex()
        self.predictions.takeItem(index)
        self.annotations.append(proposal)
        self.dirty = True
        self.refresh_annotations(len(self.annotations) - 1)


class CloudProjects(QWidget):
    train_requested = Signal(str)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.session = None
        self.job = None
        self.capability = None
        self.pending = deque()
        self.import_uncertain = None
        self.import_label_uncertain = None
        self.cancelled = False
        self.imported = 0
        self.import_group = ''
        self.preferred_project = ''
        self.profile_controls = ('train', 'cpu')

        header = QHBoxLayout()
        self.create = self.button('New project', self.create_project, header)
        self.open = self.button('Open project', self.choose_project, header)
        self.import_images = self.button('Import images', self.choose_images, header)
        self.import_yolo = self.button('Import YOLO folder', self.choose_yolo, header)
        self.stop = self.button('Stop import or assist', self.stop_job, header)
        self.train = self.button('Train this project', self.train_project, header)

        self.images = QListWidget()
        self.images.currentRowChanged.connect(self.select_image)
        self.canvas = ReviewCanvas()
        self.canvas.box_drawn.connect(self.add_box)
        self.canvas.vertex.connect(self.add_vertex)
        self.targets = QListWidget()
        self.targets.setMaximumHeight(145)
        self.targets.currentRowChanged.connect(self.select_target)
        self.split = QComboBox()
        for label, key in (('Training', 'train'), ('Validation', 'val'), ('Test', 'test')):
            self.split.addItem(label, key)
        self.group = QLineEdit()
        self.group.setMaxLength(512)
        self.group.setPlaceholderText('Optional recording or session name')
        self.device = QComboBox()
        self.device.addItem('CPU', 'cpu')
        self.device.addItem('NVIDIA CUDA', 'cuda')

        inspector = QWidget()
        fields = QVBoxLayout(inspector)
        form = QFormLayout()
        form.addRow('Import split', self.split)
        form.addRow('Capture group', self.group)
        form.addRow('Mask assistant', self.device)
        fields.addLayout(form)
        fields.addWidget(self.targets)
        self.draw = self.button('Draw target box', None, fields, checkable=True)
        self.draw.toggled.connect(self.set_draw)
        self.trace = self.button('Trace encasing polygon', None, fields, checkable=True)
        self.trace.toggled.connect(self.set_trace)
        self.finish = self.button('Finish polygon', self.finish_polygon, fields)
        self.assist = self.button('Generate mask', self.generate_mask, fields)
        self.remove = self.button('Remove target', self.remove_target, fields)
        self.negative = self.button('Mark no targets', self.mark_negative, fields)
        fields.addStretch()
        self.reconcile = self.button('Reconcile save', self.reconcile_save, fields)
        self.save = self.button('Save draft', self.save_draft, fields)
        self.approve = self.button('Approve and next', self.approve_image, fields)
        self.approve.setObjectName('primary')

        splitter = QSplitter()
        for widget in (self.images, self.canvas, inspector):
            splitter.addWidget(widget)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([140, 680, 250])
        self.status = QLabel('Open or create a Cloud project.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status)
        self.update_actions()

    @staticmethod
    def button(title, action, layout, *, checkable=False):
        button = QPushButton(title, checkable=checkable)
        if action is not None:
            button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def create_project(self):
        directory = QFileDialog.getExistingDirectory(self, 'Select parent for the new project', str(self.paths.root))
        if not directory:
            return
        name, ok = QInputDialog.getText(self, 'New Cloud project', 'Project name')
        if not ok or not name.strip() or not self.confirm_navigation():
            return
        try:
            from cloud_editor_store import EditorStore
            self.activate(EditorStore.create(directory, name.strip()))
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))

    def choose_project(self):
        directory = QFileDialog.getExistingDirectory(self, 'Open a Cloud project', self.preferred_project or str(self.paths.root))
        if directory and self.confirm_navigation():
            try:
                from cloud_editor_store import EditorStore
                self.activate(EditorStore(directory))
            except (OSError, RuntimeError, ValueError) as error:
                self.status.setText(str(error))

    def apply_settings(self, settings):
        project = self.paths.root / settings.cloud_project if settings.cloud_project else None
        if project is not None and project.name.lower() == 'project.json':
            project = project.parent
        self.preferred_project = str(project) if project is not None else ''
        current = (self.split.currentData(), self.device.currentData())
        if self.job is not None or current != self.profile_controls:
            return
        for field, value in ((self.split, settings.cloud_import_split), (self.device, settings.cloud_annotation_device)):
            index = field.findData(value)
            if index >= 0:
                field.setCurrentIndex(index)
        self.profile_controls = (self.split.currentData(), self.device.currentData())

    def activate(self, store):
        from cloud_annotation_state import AnnotationSession
        try:
            replacement = AnnotationSession(store)
            if self.session is not None:
                self.session.close(discard=True)
        except BaseException:
            store.close()
            raise
        self.session = replacement
        self.refresh_image()
        self.status.setText(f'Opened {display_path(store.root)}')

    def train_project(self):
        if self.session is None or not self.confirm_navigation():
            return
        path = display_path(self.session.store.root)
        self.session.close(discard=True)
        self.session = None
        self.refresh_image()
        self.train_requested.emit(path)

    def confirm_navigation(self):
        if self.job is not None:
            self.status.setText('Stop the project job and wait for it to finish before leaving.')
            return False
        if self.import_uncertain is not None or self.import_label_uncertain is not None or self.session is not None and self.session.uncertain:
            self.status.setText('Reconcile the project save before leaving.')
            return False
        if self.canvas.vertices:
            self.status.setText('Finish the current polygon or turn off tracing to discard it.')
            return False
        if self.session is None or not self.session.dirty:
            return True
        choice = QMessageBox.question(self, 'Unsaved Cloud annotations', 'Save a draft before leaving this image?',
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                     QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if choice == QMessageBox.StandardButton.Save:
            return self.save_draft()
        return choice == QMessageBox.StandardButton.Discard

    def select_image(self, index):
        session = self.session
        if session is None or index < 0 or index == session.cursor:
            return
        if self.confirm_navigation():
            try:
                session.navigate(index, discard=True)
                self.refresh_image()
                return
            except (OSError, RuntimeError, ValueError) as error:
                self.status.setText(str(error))
        self.images.blockSignals(True)
        self.images.setCurrentRow(session.cursor)
        self.images.blockSignals(False)

    def refresh_image(self):
        self.draw.setChecked(False)
        self.trace.setChecked(False)
        self.canvas.image = QImage()
        session = self.session
        self.images.blockSignals(True)
        self.images.clear()
        if session is not None:
            for index, record in enumerate(session.manifest['images'], 1):
                summary = 'Reviewed' if record['reviewed'] else 'Draft'
                self.images.addItem(f"{index} · {record['split']} · {summary}")
            if session.draft is not None:
                self.images.setCurrentRow(session.cursor)
                try:
                    from annotation_output import read_bytes
                    self.canvas.image = review_image(read_bytes(session.store.root / session.draft['file']))
                except (OSError, RuntimeError, ValueError) as error:
                    self.status.setText(str(error))
        self.images.blockSignals(False)
        self.refresh_targets()

    @staticmethod
    def mask_image(record, target):
        from cloud_mask_edit import MaskRaster
        width, height = record['width'], record['height']
        raster = MaskRaster(width, height, target['box'], target['mask'])
        image = QImage(bytes(raster.rgba()), raster.w, raster.h, QImage.Format.Format_RGBA8888).copy()
        return image, (raster.x / width, raster.y / height,
                       (raster.x + raster.w) / width, (raster.y + raster.h) / height)

    def refresh_targets(self, selected=-1):
        self.targets.blockSignals(True)
        self.targets.clear()
        self.canvas.annotations = []
        record = self.session.draft if self.session is not None else None
        if record is not None:
            w, h = record['width'], record['height']
            for index, target in enumerate(record['annotations'], 1):
                l, t, r, b = target['box']
                encased = bool(target.get('mask') or target.get('wrapper'))
                item = {'kind': 'box', 'x': l / w, 'y': t / h, 'w': (r - l) / w, 'h': (b - t) / h}
                if target.get('wrapper'):
                    item['wrapper'] = [(x / w, y / h) for x, y in target['wrapper']]
                if index - 1 == selected and target.get('mask'):
                    try:
                        item['mask'] = self.mask_image(record, target)
                    except (MemoryError, ValueError):
                        self.status.setText('The mask preview could not be rendered. Its project record was kept.')
                self.canvas.annotations.append(item)
                self.targets.addItem(f"Target {index} · {'encased' if encased else 'box only'}")
        self.targets.setCurrentRow(selected)
        self.targets.blockSignals(False)
        self.canvas.selected = selected
        self.canvas.update()
        self.update_actions()

    def select_target(self, index):
        self.trace.setChecked(False)
        self.refresh_targets(index)

    def target(self):
        index = self.targets.currentRow()
        targets = self.session.draft['annotations'] if self.session and self.session.draft else ()
        return targets[index] if 0 <= index < len(targets) else None

    def update_actions(self):
        session = self.session
        waiting = self.job is not None
        uncertain = bool(self.import_uncertain or self.import_label_uncertain or session and session.uncertain)
        available = bool(session and not waiting and not uncertain)
        editable = bool(available and session.draft and not self.canvas.image.isNull())
        selected = editable and self.target() is not None
        for button in (self.create, self.open):
            button.setEnabled(not waiting and not uncertain)
        for button in (self.import_images, self.import_yolo, self.train):
            button.setEnabled(available)
        for widget in (self.split, self.group, self.device, self.images):
            widget.setEnabled(available)
        for button in (self.trace, self.assist, self.remove):
            button.setEnabled(selected)
        self.draw.setEnabled(editable)
        self.finish.setEnabled(selected and len(self.canvas.vertices) >= 3)
        self.negative.setEnabled(editable and not session.draft['annotations'])
        self.save.setEnabled(editable and session.dirty and not self.trace.isChecked())
        self.approve.setEnabled(editable and not self.trace.isChecked())
        self.stop.setEnabled(waiting and not self.cancelled)
        self.reconcile.setEnabled(uncertain and not waiting)

    def set_draw(self, enabled):
        if enabled:
            self.trace.setChecked(False)
        self.canvas.set_drawing(enabled)
        self.update_actions()

    def set_trace(self, enabled):
        if enabled:
            self.draw.setChecked(False)
        self.canvas.tracing = enabled
        self.canvas.vertices.clear()
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.canvas.update()
        self.update_actions()

    def add_box(self, box):
        if self.session is None or self.job is not None:
            return
        try:
            record = self.session.draft
            w, h = record['width'], record['height']
            self.session.add_box([box['x'] * w, box['y'] * h,
                                  min(w, (box['x'] + box['w']) * w), min(h, (box['y'] + box['h']) * h)])
            self.draw.setChecked(False)
            self.refresh_targets(len(record['annotations']) - 1)
            self.status.setText('Trace the encasing polygon or generate a mask for this target.')
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))

    def add_vertex(self, point):
        record, target = self.session.draft, self.target()
        if target is None:
            return
        x, y = point[0] * record['width'], point[1] * record['height']
        l, t, r, b = target['box']
        if not l <= x <= r or not t <= y <= b:
            self.status.setText('Place polygon vertices inside the selected target box.')
            return
        if len(self.canvas.vertices) >= 128:
            self.status.setText('Finish the polygon before adding more vertices.')
            return
        self.canvas.vertices.append(point)
        self.canvas.update()
        self.update_actions()

    def finish_polygon(self):
        from annotation_labels import polygon_valid
        from cloud_editor_contract import annotation
        try:
            if not polygon_valid(self.canvas.vertices):
                raise ValueError('Trace a closed area with distinct, non-crossing vertices.')
            index = self.targets.currentRow()
            record = self.session.draft
            width, height = record['width'], record['height']
            candidate = {**self.target(), 'wrapper': [[x * width, y * height] for x, y in self.canvas.vertices]}
            candidate.pop('mask', None)
            annotation(candidate, width, height, complete=True)
            self.session._changed(candidate['id'])
            record['annotations'][index] = candidate
            self.trace.setChecked(False)
            self.refresh_targets(index)
            self.status.setText('Encasing traced. Approve the image when all targets are complete.')
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))

    def remove_target(self):
        try:
            self.session.remove(self.target()['id'])
            self.trace.setChecked(False)
            self.refresh_targets()
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))

    def mark_negative(self):
        try:
            self.session.negative()
            self.status.setText('Approve the image to record it as a reviewed negative.')
            self.update_actions()
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))

    def save_draft(self):
        try:
            self.session.save_draft()
            self.refresh_image()
            self.status.setText('Draft saved.')
            return True
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))
            self.update_actions()
            return False

    def approve_image(self):
        try:
            self.session.approve()
            self.refresh_image()
            self.status.setText('Image approved for training.')
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))
            self.update_actions()

    def reconcile_save(self):
        try:
            if self.import_uncertain is not None:
                from cloud_editor_import import reconcile_import
                imported = reconcile_import(self.session.store, self.import_uncertain)
                self.import_uncertain = None
                self.reload_imports()
                self.status.setText('Image import confirmed. Inspect its draft labels.' if imported else 'Image import did not publish a project record.')
                return
            if self.import_label_uncertain is not None:
                operation, record, revision, previous_digest = self.import_label_uncertain
                applied = self.session.store.reconcile(operation, record, revision, previous_digest)
                self.import_label_uncertain = None
                self.reload_imports()
                self.status.setText('Imported labels confirmed.' if applied is not None else 'The image was imported without labels.')
                return
            self.session.reconcile()
            self.refresh_image()
            self.status.setText('Project save reconciled.')
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))

    def choose_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Import images into the selected split', str(self.paths.root),
                                               'Images (*.png *.jpg *.jpeg *.bmp *.webp)')
        if paths:
            self.begin_import((Path(path), None, self.split.currentData()) for path in paths)

    def choose_yolo(self):
        path = QFileDialog.getExistingDirectory(self, 'Open a YOLO dataset folder', str(self.paths.root))
        if not path:
            return
        try:
            root = reject_reparse(path)
            images = reject_reparse(root / 'images')
            layouts = [(images, root / 'labels', self.split.currentData())]
            layouts.extend((images / split, root / 'labels' / split, split) for split in ('train', 'val', 'test'))
            entries = []
            for folder, labels, split in layouts:
                if not reject_reparse(folder).is_dir():
                    continue
                stems = set()
                for image in folder.iterdir():
                    if image.suffix.lower() in IMAGE_TYPES and image.is_file():
                        if image.stem.casefold() in stems:
                            raise ValueError(f'{folder.name} has images that share a label filename.')
                        if len(entries) >= MAX_REVIEW_IMAGES:
                            raise ValueError('Import at most 10,000 images at once.')
                        entries.append((image, labels / image.with_suffix('.txt').name, split))
                        stems.add(image.stem.casefold())
            self.begin_import(sorted(entries, key=lambda entry: str(entry[0]).casefold()))
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))

    def begin_import(self, entries):
        if not self.confirm_navigation():
            return
        try:
            self.pending = deque(entries)
            if not self.pending or len(self.pending) > MAX_REVIEW_IMAGES:
                raise ValueError('Choose between 1 and 10,000 supported images.')
            if self.session.dirty:
                self.session._read_current()
                self.refresh_image()
            self.import_group = self.group.text().strip()
            if self.import_group and len({split for _, _, split in self.pending}) != 1:
                raise ValueError('Choose one split when applying a shared capture group.')
            self.imported = 0
            self.start_job()
            self.next_import()
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))
            if self.job is not None:
                self.stop_job()

    def start_job(self):
        from cloud_annotation_job import AnnotationJob
        if self.job is not None:
            raise ValueError('Wait for the current annotation job.')
        self.cancelled = False
        self.job = AnnotationJob(self, self.device.currentData())
        self.job.received.connect(self.job_event)
        self.job.completed.connect(self.job_finished)
        self.update_actions()

    def next_import(self):
        from cloud_annotation_artifact import ImageCapability
        if self.cancelled or not self.pending:
            self.job.stop()
            return
        image, label, split = self.pending.popleft()
        image = reject_reparse(image)
        data_root = Path(os.environ.get('LOCALAPPDATA', self.paths.root))
        staging = reject_reparse(data_root / 'VaporLockDesktop' / 'cloud-annotation-staging')
        staging.mkdir(parents=True, exist_ok=True)
        capability = ImageCapability(staging)
        self.capability = (capability, image, label, split)
        try:
            self.job.submit(capability.command(image))
        except BaseException:
            capability.close()
            self.capability = None
            raise
        self.status.setText(f'Importing {image.name} · {len(self.pending)} queued')

    @staticmethod
    def yolo_annotations(label, width, height):
        from annotation_labels import parse_labels, polygon_valid, MAX_BYTES
        from annotation_output import read_bytes
        import secrets

        if label is None or not reject_reparse(label).is_file():
            return []
        rows = parse_labels(read_bytes(label, MAX_BYTES))
        if len(rows) > 300:
            raise ValueError(f'{label.name} exceeds the Cloud limit of 300 targets per image.')
        output = []
        for row in rows:
            if row['class_id'] != 0:
                raise ValueError(f'{label.name} contains classes other than Cloud class 0.')
            x, y, right, bottom = row['box']
            target = {'id': secrets.token_hex(16), 'class_id': 0, 'source': 'yolo',
                      'box': [max(0, x * width), max(0, y * height),
                              min(width, right * width), min(height, bottom * height)]}
            if row['points'] is not None:
                if not polygon_valid(row['points']):
                    raise ValueError(f'{label.name} contains a crossing or invalid polygon.')
                target['wrapper'] = [[px * width, py * height] for px, py in row['points']]
            output.append(target)
        return output

    def publish_import(self, event):
        from cloud_editor_contract import digest, image_record
        from cloud_editor_import import append_image, ImageImportUncertain
        from cloud_editor_store import CommitUncertain
        import secrets

        capability, image, label, split = self.capability
        artifacts = capability.read(event)
        try:
            annotations = self.yolo_annotations(label, event['width'], event['height'])
            if annotations:
                # Cloud imports apply EXIF orientation. YOLO rows describe the
                # source pixel axes, so require canonical orientation for pairs.
                from io import BytesIO
                from PIL import Image
                from annotation_output import read_bytes

                with Image.open(BytesIO(read_bytes(image))) as source:
                    if source.getexif().get(274, 1) != 1 or source.size != (event['width'], event['height']):
                        raise ValueError(f'{image.name} has an EXIF transform. Normalize it and its YOLO labels before importing.')
            store = self.session.store
            _, revision = store.load()
            try:
                result = append_image(store, event, artifacts, revision=revision,
                                      split=split, group=self.import_group or None)
            except ImageImportUncertain as error:
                self.import_uncertain = error
                raise
            if result['duplicate']:
                return
            self.imported += 1
            if annotations:
                original = result['record']
                draft = {**original, 'annotations': annotations}
                image_record(draft)
                operation = secrets.token_hex(16)
                previous_digest = digest(original)
                try:
                    store.commit(draft, revision=result['revision'], previous_digest=previous_digest,
                                 operation=operation)
                except CommitUncertain:
                    self.import_label_uncertain = (operation, draft, result['revision'], previous_digest)
                    raise
                except OSError as error:
                    raise ValueError(f'{image.name} was imported. Check the project for its draft labels before retrying.') from error
        finally:
            self.capability = None
            capability.cleanup()

    def reload_imports(self):
        session = self.session
        session.manifest, session.revision = session.store.load()
        session.cursor = min(session.cursor, max(0, len(session.manifest['images']) - 1))
        session._read_current()
        self.refresh_image()

    def generate_mask(self):
        from cloud_annotation_protocol import HQ_SAM_SHA256
        identity = None
        try:
            self.trace.setChecked(False)
            target = self.target()
            self.imported = 0
            self.start_job()
            identity = self.session.request(target['id'], HQ_SAM_SHA256)
            self.job.submit({'command': 'mask', 'identity': identity.wire(),
                             'path': str(self.session.store.root / self.session.draft['file']), 'points': [], 'labels': []})
            self.status.setText('Generating an encasing mask…')
        except (OSError, RuntimeError, ValueError) as error:
            if identity is not None:
                self.session.result(identity, error=str(error))
            if self.job is not None:
                self.stop_job()
            self.status.setText(str(error))

    def job_event(self, event):
        kind = event['type']
        if self.cancelled:
            return
        try:
            if kind == 'decoded' and self.capability is not None:
                self.publish_import(event)
                self.next_import()
            elif kind == 'decode_failed':
                raise ValueError(event['message'])
            elif kind in ('mask_proposed', 'mask_failed'):
                from cloud_editor_contract import ProposalIdentity
                proposal = ProposalIdentity.from_wire(event['identity'])
                self.session.result(proposal, event.get('mask'), error=event.get('message', ''))
                self.status.setText(self.session.failures.get(proposal.annotation, 'Mask generated. Approve when ready.'))
                self.refresh_targets(self.targets.currentRow())
                self.job.stop()
        except (OSError, RuntimeError, ValueError) as error:
            self.stop_job()
            self.status.setText(str(error))

    def stop_job(self):
        self.cancelled = True
        self.pending.clear()
        if self.session is not None and self.session.pending:
            self.session.cancel()
        if self.job is not None:
            self.job.stop()
        self.update_actions()

    def job_finished(self, success, message):
        job = self.job
        self.job = None
        if job is not None:
            job.deleteLater()
        if self.capability is not None:
            self.capability[0].close()
            self.capability = None
        self.pending.clear()
        if self.session.pending:
            self.session.cancel()
        if self.imported and self.import_uncertain is None and self.import_label_uncertain is None:
            try:
                self.reload_imports()
                if success and not self.cancelled:
                    self.status.setText(f'Imported {self.imported} images. Review each image before training.')
            except (OSError, RuntimeError, ValueError) as error:
                self.status.setText(str(error))
        if message and not self.cancelled:
            self.status.setText(message)
        self.update_actions()


class CloudTraining(QWidget):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.start_dir = paths.root
        self.job = None
        self.result = None
        self.profile_values = None
        self.profile_seeds = {}
        self.mode_seeds = {}
        self.seed_mode = 'fresh'
        self.pending_profile = None
        self.project = QLineEdit()
        self.output = QLineEdit()
        self.run = QLineEdit('s2-training')
        self.run.setMaxLength(64)
        self.mode = QComboBox()
        for title, key in (('From scratch', 'fresh'), ('Initialize from .cloud', 'initialize'),
                           ('Resume from checkpoint', 'resume')):
            self.mode.addItem(title, key)
        self.seed = QLineEdit()
        self.device = QComboBox()
        self.device.addItem('Automatic (CUDA runtime)', 'auto')
        self.device.addItem('CPU', 'cpu')
        self.device.addItem('CUDA', 'cuda:0')
        self.device.setCurrentIndex(1)
        self.epochs = self.number(1, 10000, 300)
        self.batch = self.number(-1, 256, 8)
        self.batch.setSpecialValueText('Automatic')
        self.patience = self.number(0, 10000, 75)

        self.form = QWidget()
        form = QFormLayout(self.form)
        form.addRow('Cloud project', self.path_field(self.project, self.choose_project))
        form.addRow('Output folder', self.path_field(self.output, lambda: self.choose_directory(self.output, 'Choose output folder')))
        form.addRow('New run name', self.run)
        form.addRow('Recipe', self.mode)
        self.seed_row = self.path_field(self.seed, self.choose_seed)
        form.addRow('Model or checkpoint', self.seed_row)
        self.seed_label = form.labelForField(self.seed_row)
        form.addRow('Device', self.device)
        form.addRow('Epochs', self.epochs)
        form.addRow('Batch size', self.batch)
        form.addRow('Patience', self.patience)

        self.check = QPushButton('Check project')
        self.check.clicked.connect(self.check_project)
        self.start = QPushButton('Start training')
        self.start.setObjectName('primary')
        self.start.clicked.connect(self.start_training)
        self.stop = QPushButton('Stop')
        self.stop.clicked.connect(self.stop_job)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.document().setMaximumBlockCount(150)
        actions = QHBoxLayout()
        actions.addWidget(self.check)
        actions.addStretch()
        actions.addWidget(self.stop)
        actions.addWidget(self.start)

        layout = QGridLayout(self)
        layout.addWidget(self.form, 0, 0)
        layout.addWidget(self.log, 0, 1)
        layout.addWidget(self.progress, 1, 0, 1, 2)
        layout.addLayout(actions, 2, 0, 1, 2)
        layout.setColumnStretch(1, 1)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.update_mode()
        self.update_job_actions()
        self.profile_values = self.form_values()

    @staticmethod
    def number(low, high, value):
        field = QSpinBox()
        field.setRange(low, high)
        field.setValue(value)
        return field

    @staticmethod
    def path_field(field, callback):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        button = QPushButton('Browse')
        button.clicked.connect(callback)
        layout.addWidget(button)
        return row

    def choose_directory(self, field, title):
        directory = QFileDialog.getExistingDirectory(self, title, field.text() or str(self.start_dir))
        if directory:
            field.setText(directory)

    def choose_project(self):
        self.choose_directory(self.project, 'Choose Cloud project folder')

    def choose_seed(self):
        resume = self.mode.currentData() == 'resume'
        path, _ = QFileDialog.getOpenFileName(self, 'Choose training checkpoint' if resume else 'Choose model',
                                              self.seed.text() or str(self.start_dir),
                                              'Cloud checkpoint (*.cloudtrain)' if resume else 'Cloud model (*.cloud)')
        if path:
            self.seed.setText(path)

    def update_mode(self):
        mode = self.mode.currentData()
        if mode != self.seed_mode:
            if self.seed_mode != 'fresh':
                self.mode_seeds[self.seed_mode] = self.seed.text()
            self.seed_mode = mode
            self.seed.setText(self.mode_seeds.get(mode, self.profile_seeds.get(mode, '')))
        self.seed_row.setVisible(mode != 'fresh')
        self.seed_label.setVisible(mode != 'fresh')
        for field in (self.epochs, self.batch, self.patience):
            field.setEnabled(mode != 'resume')

    def form_values(self):
        return (self.project.text(), self.output.text(), self.run.text(), self.mode.currentData(),
                self.device.currentData(), self.epochs.value(), self.batch.value(), self.patience.value())

    def apply_settings(self, settings):
        if self.job is not None:
            self.pending_profile = settings
            return

        def path_text(value):
            return str(self.paths.root / value) if value else ''

        def option(field, value):
            index = field.findData(value)
            if index < 0:
                field.addItem(value, value)
                index = field.count() - 1
            field.setCurrentIndex(index)

        mode = self.mode.currentData()
        if mode != 'fresh':
            self.mode_seeds[mode] = self.seed.text()
        previous_seeds = self.profile_seeds
        self.profile_seeds = {'initialize': path_text(settings.cloud_initial_model),
                              'resume': path_text(settings.cloud_checkpoint)}
        for kind, value in self.profile_seeds.items():
            if self.mode_seeds.get(kind, '') == previous_seeds.get(kind, ''):
                self.mode_seeds[kind] = value

        defaults = (path_text(settings.cloud_project), path_text(settings.cloud_output), settings.cloud_run_name,
                    settings.cloud_mode, 'cuda:0' if settings.cloud_device == 'cuda' else settings.cloud_device,
                    settings.cloud_epochs, settings.cloud_batch or -1, settings.cloud_patience)
        current = self.form_values()
        setters = (self.project.setText, self.output.setText, self.run.setText,
                   lambda value: option(self.mode, value), lambda value: option(self.device, value),
                   self.epochs.setValue, self.batch.setValue, self.patience.setValue)
        # Preserve individual edits even if a new profile supplies values for
        # the other fields. Seed drafts are kept separately for each mode.
        if mode != 'fresh':
            self.seed.setText(self.mode_seeds.get(mode, ''))
        for value, prior, default, setter in zip(current, self.profile_values, defaults, setters):
            if value == prior:
                setter(default)
        self.profile_values = defaults

    def update_job_actions(self):
        busy = self.job is not None
        self.form.setEnabled(not busy)
        self.check.setEnabled(not busy)
        self.start.setEnabled(not busy)
        self.stop.setEnabled(busy)

    def project_path(self):
        value = self.project.text().strip()
        if not value:
            raise ValueError('Choose a Cloud project folder.')
        return str(reject_reparse(value))

    def check_project(self):
        try:
            self.launch({'operation': 'check_project', 'project': self.project_path()})
        except (OSError, RuntimeError, ValueError) as error:
            self.log.appendPlainText(str(error))

    def start_training(self):
        try:
            project = self.project_path()
            output = self.output.text().strip()
            if not output:
                raise ValueError('Choose an existing output folder.')
            output = reject_reparse(output)
            if not output.is_dir():
                raise ValueError('Choose an existing output folder.')
            name = self.run.text().strip()
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
                raise ValueError('Use 1–64 letters, digits, underscores, or hyphens for the run name.')
            mode = self.mode.currentData()
            overrides = {'device': self.device.currentData()}
            if mode != 'resume':
                if self.batch.value() == 0:
                    raise ValueError('Use Automatic or a positive batch size.')
                overrides.update(epochs=self.epochs.value(), batch_size=self.batch.value(), patience=self.patience.value())
            request = {'operation': 'train', 'project': project, 'output_parent': str(output),
                       'run_name': name, 'mode': mode, 'overrides': overrides}
            if mode != 'fresh':
                seed = self.seed.text().strip()
                if not seed or not reject_reparse(seed).is_file():
                    raise ValueError('Choose the model or checkpoint for this recipe.')
                request['checkpoint' if mode == 'resume' else 'initial_model'] = str(reject_reparse(seed))
            self.launch(request)
        except (OSError, RuntimeError, ValueError) as error:
            self.log.appendPlainText(str(error))

    def launch(self, request):
        if self.job is not None:
            return
        from cloud_jobs import CloudJob
        self.result = None
        self.progress.setRange(0, 0)
        self.log.appendPlainText('Starting training…' if request['operation'] == 'train' else 'Checking project…')
        try:
            self.job = CloudJob(self, request, training=request['operation'] == 'train')
        except Exception:
            self.progress.setRange(0, 1)
            raise
        self.job.received.connect(self.on_event)
        self.job.completed.connect(self.on_complete)
        self.update_job_actions()

    def stop_job(self):
        if self.job is not None:
            try:
                self.job.stop()
            except (OSError, RuntimeError, ValueError) as error:
                self.log.appendPlainText(str(error))
                return
            self.stop.setEnabled(False)
            self.log.appendPlainText('Stop requested…')

    def on_event(self, event):
        kind = event.get('type')
        if kind == 'project_checked' or kind == 'phase' and 'counts' in event:
            self.log.appendPlainText(' · '.join(f'{key} {value}' for key, value in event['counts'].items()))
        elif kind == 'configuration':
            self.log.appendPlainText(' · '.join(f'{key} {value}' for key, value in event['recipe'].items()))
        elif kind == 'progress':
            epoch = event.get('epoch')
            if epoch is not None and event.get('epochs', 0) > 0:
                self.progress.setRange(0, int(event['epochs']))
                self.progress.setValue(int(epoch))
            summary = ' · '.join(f'{key} {event[key]}' for key in ('epoch', 'batch', 'loss') if key in event)
            if summary:
                self.log.appendPlainText(summary)
        elif kind == 'trained':
            self.result = event
            self.log.appendPlainText(f"{event['status'].replace('_', ' ').capitalize()} after {event['epochs_completed']} epochs.")
            for key in ('best_cloud', 'last_cloud', 'checkpoint'):
                if event[key]:
                    self.log.appendPlainText(display_path(event[key]))

    def on_complete(self, success, message):
        job = self.job
        self.job = None
        if job is not None:
            job.deleteLater()
        self.progress.setRange(0, 1)
        self.progress.setValue(int(success))
        if message:
            self.log.appendPlainText(message)
        self.update_job_actions()
        if self.pending_profile is not None:
            settings, self.pending_profile = self.pending_profile, None
            self.apply_settings(settings)


class WorkspacePanel(QWidget):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.review = DatasetReview(paths, self)
        self.projects = CloudProjects(paths, self)
        self.training = CloudTraining(paths, self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.review, 'Dataset review')
        self.tabs.addTab(self.projects, '.cloud projects')
        self.tabs.addTab(self.training, '.cloud training')
        self.projects.train_requested.connect(self.open_training_project)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

    def apply_settings(self, settings):
        self.review.apply_settings(settings)
        self.projects.apply_settings(settings)
        self.training.apply_settings(settings)

    def open_dataset(self, directory):
        self.tabs.setCurrentWidget(self.review)
        return self.review.open_dataset(directory)

    def open_training_project(self, directory):
        self.tabs.setCurrentWidget(self.training)
        if self.training.job is not None:
            return False
        self.training.project.setText(str(directory))
        return True

    def can_close(self):
        if self.training.job is not None:
            self.tabs.setCurrentWidget(self.training)
            QMessageBox.information(self, 'Cloud job running', 'Stop the Cloud job and wait for it to finish before closing.')
            return False
        if not self.review.confirm_navigation():
            self.tabs.setCurrentWidget(self.review)
            return False
        if not self.projects.confirm_navigation():
            self.tabs.setCurrentWidget(self.projects)
            return False
        return True

    def close_project(self):
        if self.projects.session is not None:
            self.projects.session.close(discard=True)
            self.projects.session = None
