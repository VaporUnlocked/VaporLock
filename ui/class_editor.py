"""Explicit class selector. One bounded metadata reader; no runtime model load."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import queue
import re
import threading
from model_classes import inspect_classes, validate_aliases, clean_label

_reader_lock = threading.Lock()


def filter_ids(text):
    if type(text) is not str or len(text) > 8192: raise ValueError('Invalid class IDs')
    if not text.strip(): return set()
    pieces = [value.strip() for value in text.split(',')]
    if len(pieces) > 1024 or any(not re.fullmatch(r'0|[1-9][0-9]{0,5}', value) for value in pieces):
        raise ValueError('Invalid class IDs')
    return {int(value) for value in pieces}


def open_editor(center):
    if getattr(center, '_tuning_locked', False): return
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
        QTableWidget, QTableWidgetItem, QHeaderView, QDialogButtonBox, QSpinBox, QPushButton)
    from vapor_lock_gui import ROOT, appearance_palette
    stamp = asdict(center.settings); epoch = getattr(center, '_tuning_epoch', 0)
    settings_owner = center.settings
    profile_path = getattr(center, '_active_profile_path', None)
    intent_generation = getattr(center, '_start_intent_generation', 0)
    try:
        selected = filter_ids(center.settings.class_filter)
        validate_aliases(center.settings.model_class_aliases)
    except ValueError:
        center.log('WARN', center.t('Review the numeric class IDs before opening the class editor.'))
        return
    dialog = QDialog(center); dialog.setObjectName('classEditorDialog')
    dialog.setWindowTitle(center.t('Select classes and aliases')); dialog.resize(740, 540)
    colors = appearance_palette(center.ui_appearance, True)
    dialog.setStyleSheet(f'QDialog#classEditorDialog {{background:{colors["carbon"]};color:{colors["ice"]};}}')
    layout = QVBoxLayout(dialog)
    hint = QLabel(center.t('Aliases affect display only. Class IDs and annotations stay unchanged.'))
    hint.setWordWrap(True); layout.addWidget(hint)
    all_classes = QCheckBox(center.t('All classes')); all_classes.setChecked(not selected); layout.addWidget(all_classes)
    table = QTableWidget(0, 3); table.setObjectName('modelClassTable')
    table.setHorizontalHeaderLabels([center.t('Class ID'), center.t('Original class name'), center.t('Display alias')])
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    layout.addWidget(table)
    add_row = QHBoxLayout(); number = QSpinBox(); number.setRange(0, 999999)
    number.setAccessibleName(center.t('Class ID'))
    add = QPushButton(center.t('Add class ID')); add_row.addWidget(number); add_row.addWidget(add); add_row.addStretch(1)
    layout.addLayout(add_row)
    status = QLabel(center.t('Reading class names…')); status.setWordWrap(True); status.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(status)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel); layout.addWidget(buttons)
    apply = buttons.button(QDialogButtonBox.StandardButton.Apply); apply.setEnabled(False)
    cancelled = threading.Event(); results = queue.Queue(maxsize=1); identity = None
    aliases = {}; names = {}

    def add_id(class_id, chosen=False):
        for row in range(table.rowCount()):
            if int(table.item(row, 0).text()) == class_id: return
        # Up to 1024 metadata IDs + 1024 configured IDs + 128 alias IDs.
        # Reserve room for their full union; never silently truncate saved state.
        if table.rowCount() >= 2304:
            status.setText(center.t('Class table is full. Remove unused selections before adding more IDs.'))
            return
        row = table.rowCount(); table.insertRow(row)
        item = QTableWidgetItem(str(class_id))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
            (Qt.ItemFlag.NoItemFlags if all_classes.isChecked() else Qt.ItemFlag.ItemIsUserCheckable))
        item.setCheckState(Qt.CheckState.Checked if chosen or all_classes.isChecked() else Qt.CheckState.Unchecked)
        table.setItem(row, 0, item)
        original = QTableWidgetItem(names.get(class_id, center.t('Name unavailable')))
        original.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable); table.setItem(row, 1, original)
        alias = QTableWidgetItem(aliases.get(str(class_id), ''))
        if identity is None: alias.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        table.setItem(row, 2, alias)

    def fill():
        table.blockSignals(True)
        table.setRowCount(0)
        for class_id in sorted(set(names) | selected | {int(key) for key in aliases}): add_id(class_id, class_id in selected)
        table.blockSignals(False)
        apply.setEnabled(True)
    def selection_changed(item):
        if item.column() != 0 or all_classes.isChecked(): return
        class_id = int(item.text())
        if item.checkState() == Qt.CheckState.Checked: selected.add(class_id)
        else: selected.discard(class_id)
    def refresh_mode():
        table.blockSignals(True)
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                (Qt.ItemFlag.NoItemFlags if all_classes.isChecked() else Qt.ItemFlag.ItemIsUserCheckable))
            item.setCheckState(Qt.CheckState.Checked if all_classes.isChecked() or int(item.text()) in selected else Qt.CheckState.Unchecked)
        table.blockSignals(False)
    def add_manual():
        all_classes.setChecked(False)
        selected.add(number.value())
        add_id(number.value(), True)
        refresh_mode()
    all_classes.toggled.connect(refresh_mode)
    table.itemChanged.connect(selection_changed)
    def current():
        return (not getattr(center, '_tuning_locked', False) and not getattr(center, '_native_closing', False)
            and not getattr(center, '_profile_transition_pending', False)
            and settings_owner is center.settings and profile_path == getattr(center, '_active_profile_path', None)
            and intent_generation == getattr(center, '_start_intent_generation', 0)
            and epoch == getattr(center, '_tuning_epoch', 0) and stamp == asdict(center.settings))
    def commit():
        if not current():
            status.setText(center.t('Settings changed. Close and reopen the class editor.')); apply.setEnabled(False); return
        chosen = [int(table.item(row, 0).text()) for row in range(table.rowCount()) if table.item(row, 0).checkState() == Qt.CheckState.Checked]
        if not all_classes.isChecked() and not chosen:
            status.setText(center.t('Select at least one class or choose All classes.')); return
        try:
            new_filter = '' if all_classes.isChecked() else ', '.join(map(str, sorted(chosen)))
            filter_ids(new_filter)
            updated = deepcopy(center.settings.model_class_aliases)
            if identity is not None:
                info = Path(identity['path']).stat()
                if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != identity['identity']: raise ValueError('Model changed')
                replacement = {}
                for row in range(table.rowCount()):
                    alias = clean_label(table.item(row, 2).text())
                    if alias: replacement[table.item(row, 0).text()] = alias
                updated = [entry for entry in updated if entry['model'] != identity['sha256']]
                if replacement: updated.append({'model': identity['sha256'], 'aliases': replacement})
                validate_aliases(updated)
            center.change('model_class_aliases', updated)
            center.change('class_filter', new_filter)
            center.class_filter_edit.setText(center.settings.class_filter)
        except (ValueError, OSError):
            status.setText(center.t('Aliases could not be applied. Use short plain-text names and reopen if the model changed.')); return
        dialog.accept()
    apply.clicked.connect(commit); buttons.rejected.connect(dialog.reject)
    add.clicked.connect(add_manual)
    add.setEnabled(False)
    def poll():
        nonlocal identity, aliases, names
        if not current():
            cancelled.set(); timer.stop(); apply.setEnabled(False)
            status.setText(center.t('Settings changed. Close and reopen the class editor.')); return
        try: result = results.get_nowait()
        except queue.Empty: return
        timer.stop()
        identity = result
        if result is not None:
            names = result['names']
            aliases = next((entry['aliases'] for entry in center.settings.model_class_aliases if entry['model'] == result['sha256']), {})
        status.setText(center.t('Select classes by ID or name. Empty numeric filter means All classes.' if names else
            'Class names are unavailable. Numeric class IDs remain usable.'))
        fill(); add.setEnabled(True)
    timer = QTimer(dialog); timer.setInterval(100); timer.timeout.connect(poll)
    def read(path):
        try:
            try: result = inspect_classes(path, cancelled)
            except (OSError, ValueError, OverflowError, MemoryError): result = None
            if not cancelled.is_set(): results.put_nowait(result)
        finally: _reader_lock.release()
    if center.settings.model_path and _reader_lock.acquire(blocking=False):
        try:
            path = Path(center.settings.model_path)
            if not path.is_absolute(): path = ROOT / path
            threading.Thread(target=read, args=(path,), daemon=True, name='VaporLock-class-metadata').start()
        except (OSError, ValueError, RuntimeError):
            _reader_lock.release(); results.put_nowait(None)
    else: results.put_nowait(None)
    dialog.finished.connect(lambda _result: cancelled.set())
    timer.start(); dialog.exec(); cancelled.set(); timer.stop()
