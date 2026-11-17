"""Model Library dialog for checking and restoring shipped engine files."""

from pathlib import Path
from zipfile import BadZipFile

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from batch4_guard import editable
from runtime_repair import COMPONENTS, inspect_runtime, repair_runtime


def open_repair(center, root, version, *, packaged):
    if not editable(center, stopped=True) or center.acceleration.active:
        center.log("INFO", center.t("Stop capture and runtime setup before opening Runtime repair."))
        return

    dialog = QDialog(center)
    dialog.setWindowTitle(center.t("Runtime repair"))
    dialog.setMinimumWidth(530)
    layout = QVBoxLayout(dialog)
    description = QLabel(center.t(
        "Check a bundled engine, then restore changed or missing files using the matching Community Windows ZIP."
        if packaged else
        "Source builds use the local native compiler. Run BuildCommunity.ps1 -NativeDirectML to rebuild CPU and DirectML."
    ))
    description.setWordWrap(True)
    layout.addWidget(description)
    picker = QComboBox()
    for component in COMPONENTS:
        picker.addItem(center.t(component), component)
    provider = {"dml": "DirectML", "trt": "TensorRT RTX"}.get(center.settings.provider, "CPU")
    picker.setCurrentIndex(picker.findData(provider))
    layout.addWidget(picker)
    results = QPlainTextEdit()
    results.setReadOnly(True)
    layout.addWidget(results)
    status = QLabel()
    status.setWordWrap(True)
    layout.addWidget(status)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    check = QPushButton(center.t("Check files"))
    restore = QPushButton(center.t("Repair from ZIP…"))
    buttons.addButton(check, QDialogButtonBox.ButtonRole.ActionRole)
    if packaged:
        buttons.addButton(restore, QDialogButtonBox.ButtonRole.ActionRole)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    def refresh():
        restore.setEnabled(False)
        component = picker.currentData()
        try:
            if packaged:
                files = inspect_runtime(root, version, component)
            else:
                from native_engine_layout import source_engine, supports_capture_controls, supports_mouse_host
                key = {"CPU": "cpu", "DirectML": "dml", "TensorRT RTX": "trt"}.get(component)
                native = source_engine(root, key or "cpu")
                if key is None:
                    native = native.with_name("VaporLockMouseHost.exe") if native else None
                supported = supports_capture_controls if key else supports_mouse_host
                state = "Missing" if native is None or not native.is_file() else (
                    "Current capture protocol" if supported(native) else "Rebuild required"
                )
                files = {str(native or component): state}
        except (OSError, ValueError) as error:
            results.setPlainText(str(error))
            return
        results.setPlainText("\n".join(
            f"{Path(path).name if packaged else path}  ·  {center.t(state)}" for path, state in files.items()
        ))
        restore.setEnabled(packaged and any(state != "Verified" for state in files.values()))

    def repair():
        archive, _ = QFileDialog.getOpenFileName(
            dialog, center.t("Select the Community Windows ZIP"), "", center.t("ZIP archives (*.zip)")
        )
        if not archive:
            return
        if not editable(center, stopped=True) or center.acceleration.active:
            status.setText(center.t("Stop capture and runtime setup before restoring files."))
            return
        try:
            count = repair_runtime(root, version, picker.currentData(), Path(archive))
        except (OSError, ValueError, BadZipFile, KeyError, RuntimeError) as error:
            status.setText(center.t("Repair failed. ") + str(error))
        else:
            status.setText(center.t("Runtime files restored") + f" · {count}")
            center.refresh_acceleration_controls()
            refresh()

    check.clicked.connect(refresh)
    picker.currentIndexChanged.connect(lambda _: (status.clear(), refresh()))
    restore.clicked.connect(repair)
    center._runtime_repair_open = True
    try:
        refresh()
        dialog.exec()
    finally:
        center._runtime_repair_open = False
