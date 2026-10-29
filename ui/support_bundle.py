"""Generated local support snapshots; no profile/log/file collector or upload."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
import zipfile
from session_diagnostics import METRICS, timing_summary

MAX_BYTES = 131072
NUMERIC = METRICS - {'output_reason', 'output_transport', 'event_action_reason',
    'capture_backend', 'capture_recovery_reason'}
PROVIDERS = ('cpu', 'dml', 'cuda', 'trt')
BOOL_METRICS = frozenset(('output_active', 'observe_only', 'motion_compensation_active', 'high_resolution_pacing'))
README = ('Vapor Lock support bundle\nSaved locally. Nothing was uploaded.\n'
          'Includes only the JSON preview selected by the user.\n'
          'No raw logs, profiles, model names/files, media, credentials or local paths.\n'
          'Component presence is not a runtime compatibility or health check.\n')


def make_snapshot(product, recorded, *, session=True, components=None, live=False):
    version = product.get('version')
    edition = product.get('edition')
    payload = {'schema_version': 1, 'product': {
        'version': version if type(version) is str and re.fullmatch(r'[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[A-Za-z0-9]{1,12}(?:\.[0-9]{1,6})?)?', version) else 'unknown',
        'edition': edition if edition in ('free', 'premium') else 'unknown'}}
    if session:
        context = recorded.get('context', {}) if type(recorded) is dict else {}
        source = recorded.get('metrics', {}) if type(recorded) is dict else {}
        context = context if type(context) is dict else {}
        source = source if type(source) is dict else {}
        metrics = {}
        for key in NUMERIC:
            value = source.get(key)
            if key in BOOL_METRICS and type(value) is bool:
                metrics[key] = value
            elif key not in BOOL_METRICS and type(value) in (int, float) and math.isfinite(value) and -1 <= value <= 2**53-1:
                metrics[key] = value
        for key, choices in (
            ('capture_backend', ('gdi', 'dxgi', 'wgc')),
            ('output_transport', ('mouse', 'virtual-mouse', 'virtual-xbox', 'makcu-v1', 'makcu-v2', 'serial-hid-bridge', 'kmbox-net', 'kmbox-a')),
        ):
            if type(source.get(key)) is str and source[key] in choices:
                metrics[key] = source[key]
        timing = timing_summary(source.get('timing'))
        if timing is not None:
            metrics['timing'] = timing
        provider = context.get('provider')
        payload['session'] = {'availability': 'live' if context and live is True else 'last-recorded' if context else 'unavailable',
            'provider': provider if type(provider) is str and provider in PROVIDERS else 'unknown', 'metrics': metrics}
    if components is not None:
        payload['components'] = {key: components[key] for key in PROVIDERS if type(components.get(key)) is bool}
        payload['component_meaning'] = 'engine-file-present-only'
    text = json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False, sort_keys=True) + '\n'
    if len(text.encode('utf8')) > MAX_BYTES:
        raise ValueError('Support snapshot exceeds its limit')
    return text


def save_snapshot(destination, preview):
    """Save exactly the frozen preview; no rediscovery between preview and Save."""
    if type(preview) is not str or len(preview.encode('utf8')) > MAX_BYTES:
        raise ValueError('Invalid support snapshot')
    path = Path(destination)
    fd, temporary = tempfile.mkstemp(prefix='.vaporlock-support-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w+b') as handle:
            with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('support.json', preview)
                archive.writestr('README.txt', README)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def open_dialog(center, product, root):
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QPlainTextEdit, QDialogButtonBox, QFileDialog
    from PySide6.QtCore import Qt
    dialog = QDialog(center)
    dialog.setObjectName('supportBundleDialog')
    dialog.setWindowTitle(center.t('Export support bundle…'))
    dialog.resize(700, 550)
    from vapor_lock_gui import appearance_palette
    colors = appearance_palette(center.ui_appearance, True)
    dialog.setStyleSheet(f'QDialog#supportBundleDialog {{background:{colors["carbon"]}; color:{colors["ice"]};}}')
    layout = QVBoxLayout(dialog)
    hint = QLabel(center.t('Saved locally; nothing is uploaded. Excludes logs, profiles, paths, keys, model files and images.'))
    hint.setWordWrap(True); hint.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(hint)
    session = QCheckBox(center.t('Session and performance summary')); session.setChecked(True)
    components = QCheckBox(center.t('Component availability')); components.setChecked(True)
    layout.addWidget(session); layout.addWidget(components)
    component_hint = QLabel(center.t('Component checks show file presence only, not runtime compatibility. Unreported components are unknown.'))
    component_hint.setWordWrap(True); layout.addWidget(component_hint)
    preview = QPlainTextEdit(); preview.setReadOnly(True); preview.setObjectName('supportBundlePreview')
    layout.addWidget(preview)
    status = QLabel(); status.setWordWrap(True); layout.addWidget(status)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText(center.t('Save ZIP'))
    layout.addWidget(buttons)
    recorded = center.session_report_cache.snapshot()
    live = bool(center.engine.session_running and recorded['context'].get('generation') == center.engine._launch_generation)
    # Never run engine/runtime discovery or load DLLs just to export a report.
    # CUDA generation selection has no fixed path: omit it (unknown).
    available = {provider: (Path(root) / relative).is_file() for provider, relative in (
        ('cpu', 'engine/VaporLock.exe'), ('dml', 'engine/dml/VaporLockDml.exe'),
        ('trt', 'engine/trt/VaporLockTensorRt.exe'))}
    def refresh():
        preview.setPlainText(make_snapshot(product, recorded, session=session.isChecked(),
            components=available if components.isChecked() else None, live=live))
        buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(session.isChecked() or components.isChecked())
    def save():
        frozen = preview.toPlainText()
        chooser = QFileDialog(dialog, center.t('Save ZIP'))
        chooser.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        chooser.setNameFilter('ZIP (*.zip)'); chooser.setDefaultSuffix('zip')
        chooser.selectFile(str(Path(root) / 'VaporLock-support.zip'))
        if chooser.exec() != QDialog.DialogCode.Accepted or not chooser.selectedFiles(): return
        destination = chooser.selectedFiles()[0]
        try:
            save_snapshot(Path(destination), frozen)
        except (OSError, ValueError):
            status.setText(center.t('The support bundle could not be saved. Choose another destination.'))
            return
        dialog.accept()
    session.toggled.connect(refresh); components.toggled.connect(refresh)
    buttons.accepted.connect(save); buttons.rejected.connect(dialog.reject)
    refresh(); dialog.exec()
