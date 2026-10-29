"""Batch 7 additions within existing native pages."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox,QDialog,QDialogButtonBox,QLabel,QVBoxLayout,QPlainTextEdit,QWidget
from batch4_guard import editable


def add_annotation_mode(center,layout):
    combo=QComboBox();combo.setObjectName('annotationLabelMode')
    for title,value in (('Create new labels','new'),('Add to existing labels','additive'),('Replace existing labels','replace')):
        combo.addItem(center.t(title),value)
    def current():return 'additive' if center.settings.dataset_annotation_additive else 'replace' if center.settings.dataset_annotation_replace else 'new'
    combo.setCurrentIndex(combo.findData(current()))
    def change(index):
        if editable(center,stopped=True):
            mode=combo.itemData(index)
            center.change('dataset_annotation_additive',False)
            center.change('dataset_annotation_replace',mode=='replace')
            if mode=='additive':
                center.change('dataset_annotation_delete_unannotated',False)
                center.change('dataset_annotation_additive',True)
        combo.blockSignals(True);combo.setCurrentIndex(combo.findData(current()));combo.blockSignals(False)
        if hasattr(center,'dataset_delete_empty'):
            center.dataset_delete_empty.blockSignals(True)
            center.dataset_delete_empty.setChecked(center.settings.dataset_annotation_delete_unannotated)
            center.dataset_delete_empty.setEnabled(not center.settings.dataset_annotation_additive)
            center.dataset_delete_empty.blockSignals(False)
    combo.currentIndexChanged.connect(change)
    container=QWidget();column=QVBoxLayout(container);column.setContentsMargins(0,0,0,0);column.addWidget(combo)
    help=QLabel(center.t('Additive keeps existing labels and disables image deletion. Enabled image effects can still replace pixels.'))
    help.setWordWrap(True);column.addWidget(help)
    center.setting(layout,'Existing labels','Additive preserves original rows and class IDs. Predictions with bounding-box IoU at or above the NMS threshold are suppressed against existing labels regardless of class; polygons use their enclosing boxes. Image effects can still replace pixels.',center.feature_control(container))
    center.annotation_label_mode=combo


def edit_label_text(center):
    from review_document import allowed,parse_labels,to_annotations,save
    document=getattr(center,'_review_document',None)
    if document is None or not allowed(center) or center.dataset_review_preview.has_draft():return
    dialog=QDialog(center);dialog.setWindowTitle(center.t('Edit labels'));dialog.resize(620,420)
    column=QVBoxLayout(dialog);editor=QPlainTextEdit(document.label_bytes.decode('utf-8'));column.addWidget(editor)
    status=QLabel();status.setWordWrap(True);status.setTextFormat(Qt.TextFormat.PlainText);column.addWidget(status)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);column.addWidget(buttons)
    def accept():
        try:
            if document is not getattr(center,'_review_document',None):raise ValueError('Review image changed.')
            rows=parse_labels(editor.toPlainText().encode('utf-8'))
            from annotation_labels import validate_manual_rows
            validate_manual_rows(rows)
            if not save(center,to_annotations(rows)):raise ValueError('Save refused. Refresh before editing.')
            dialog.accept()
        except (ValueError,UnicodeError):status.setText(center.t('Labels are invalid or the source changed. Refresh before editing.'))
    buttons.accepted.connect(accept);buttons.rejected.connect(dialog.reject);dialog.exec()
