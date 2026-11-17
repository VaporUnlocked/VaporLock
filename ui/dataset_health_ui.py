"""Show a read-only dataset scan without blocking the Control Center."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout,
)

from cloud_contract import CloudInputError
from cloud_jobs import CloudJob
from dataset_paths import display_path


class DatasetHealthDialog(QDialog):
    def __init__(self, center, source, kind, model_size):
        super().__init__(center)
        self.center, self.job = center, None
        self.kind, self.model_size = kind, model_size
        self.close_pending = False
        self.report = ""
        self.setWindowTitle(center.t("Dataset health"))
        self.resize(720, 530)
        layout = QVBoxLayout(self)
        self.source = QLineEdit(display_path(source))
        self.source.setReadOnly(True)
        self.source.setAccessibleName(center.t("Dataset folder"))
        self.browse_button = QPushButton(center.t("Browse…"))
        self.browse_button.clicked.connect(self.browse)
        location = QHBoxLayout()
        location.addWidget(self.source, 1)
        location.addWidget(self.browse_button)
        layout.addLayout(location)

        self.status = QLabel(center.t("Choose a dataset folder and run the check."))
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.results = QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setAccessibleName(center.t("Dataset health report"))
        layout.addWidget(self.results, 1)

        self.scan_button = QPushButton(center.t("Run check"))
        self.scan_button.setDefault(True)
        self.scan_button.clicked.connect(self.scan)
        self.cancel_button = QPushButton(center.t("Cancel check"))
        self.cancel_button.hide()
        self.cancel_button.clicked.connect(self.cancel)
        close = QPushButton(center.t("Close"))
        close.clicked.connect(self.reject)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.scan_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(close)
        layout.addLayout(actions)

    def browse(self):
        current = Path(self.source.text())
        start = str(current.parent if current.is_file() else current)
        picked = QFileDialog.getExistingDirectory(self, self.center.t("Choose dataset folder"), start)
        if picked:
            self.source.setText(picked)
            self.results.clear()
            self.status.setText(self.center.t("Ready to run the check."))

    def scan(self):
        from api_jobs import admission
        if self.job is not None:
            return
        if not self.source.text():
            self.status.setText(self.center.t("Choose a dataset folder and run the check."))
            return
        if not admission(self.center, "dataset-health"):
            self.status.setText(self.center.t("Finish the current session or background task before running the check."))
            return
        self.results.clear()
        self.report = ""
        try:
            self.job = CloudJob(self, dict(operation="dataset_health", source=self.source.text(),
                                           kind=self.kind, model_size=self.model_size))
        except (CloudInputError, OSError, RuntimeError) as error:
            message = str(error) if isinstance(error, CloudInputError) else "Dataset health check could not start."
            self.status.setText(self.center.t(message))
            return
        self.center._cloud_model_check_job = self.job
        self.center.set_start_enabled(False)
        self.job.received.connect(self.receive)
        self.job.completed.connect(self.complete)
        self.scan_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText(self.center.t("Checking saved images and labels…"))

    def receive(self, event):
        if event["type"] == "dataset_health_progress":
            self.progress.setRange(0, max(1, event["total"]))
            self.progress.setValue(event["scanned"])
        elif event["type"] == "dataset_health_checked":
            self.report = event["report"]

    def cancel(self):
        if self.job is not None:
            self.cancel_button.setEnabled(False)
            self.status.setText(self.center.t("Stopping the check…"))
            self.job.stop()

    def complete(self, success, message):
        job, self.job = self.job, None
        if self.center._cloud_model_check_job is job:
            self.center._cloud_model_check_job = None
            self.center.set_start_enabled(not self.center.engine.active)
        job.deleteLater()
        if self.close_pending:
            super().reject()
            return
        cancelled = not self.cancel_button.isEnabled()
        self.progress.hide()
        self.cancel_button.hide()
        self.scan_button.setEnabled(True)
        self.browse_button.setEnabled(True)
        if success:
            self.results.setPlainText(self.report)
            self.status.setText(self.center.t("Check complete."))
        else:
            self.status.setText(self.center.t("Check cancelled." if cancelled else message))

    def reject(self):
        if self.job is None:
            super().reject()
        else:
            self.close_pending = True
            self.cancel()
