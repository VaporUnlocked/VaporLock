"""Native controls for independent local and Ultralytics annotation workflows."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import secrets

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QPixmap, QTextOption
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget, QScrollArea, QSizePolicy, QTextEdit)

from annotation_variations import EFFECTS, VariationOptions
from ultralytics_credentials import key_state, protect_key, restore_key
from vlui_native_injection import native_injection


def _boolean(center, layout, prefix, suffix, title, detail):
    field = prefix + '_effects_' + suffix
    control = QCheckBox(title)
    control.setChecked(getattr(center.settings, field))
    control.toggled.connect(lambda value: center.change(field, value))
    center._discrete_registry.register('discrete.'+field+'.1',field,control)
    center.setting(layout,title,detail,center.feature_control(control))
    return control


def add_effects_controls(center, layout, prefix):
    center.section(layout,'Random image effects')
    explanation=QLabel(center.t('Image effects replace existing pixels after annotation. Accepted labels and image dimensions stay unchanged.'))
    explanation.setWordWrap(True);layout.addWidget(explanation)
    enabled=_boolean(center,layout,prefix,'enabled','Enable random image effects',
        'Apply effects after annotation. Existing images are overwritten by default; label coordinates do not change.')
    extra=_boolean(center,layout,prefix,'extra_copies','Create extra copies',
        'Off by default. Enable to create additional images instead of replacing the existing image.')
    count=center.range(prefix+'_effects_copies',1,10,0)
    center.setting(layout,'Additional copies per image','Used only when Create extra copies is enabled.',center.feature_control(count))
    def update_count(*_args):count.setEnabled(enabled.isChecked() and extra.isChecked())
    enabled.toggled.connect(update_count);extra.toggled.connect(update_count);update_count()
    center._annotation_effect_counts=getattr(center,'_annotation_effect_counts',{})
    center._annotation_effect_counts[prefix]=count
    strength=QComboBox()
    for title,value in (('Mild','mild'),('Medium','medium'),('Strong','strong')):
        strength.addItem(title,value)
    strength.setCurrentIndex(max(0,strength.findData(getattr(center.settings,prefix+'_effects_strength'))))
    strength.currentIndexChanged.connect(lambda i:center.change(prefix+'_effects_strength',strength.itemData(i)))
    center._discrete_registry.register('discrete.'+prefix+'_effects_strength.1',prefix+'_effects_strength',strength)
    center.setting(layout,'Effect strength','Randomise the selected effects within this strength.',center.feature_control(strength))
    effects=QWidget(); row=QVBoxLayout(effects);row.setContentsMargins(0,0,0,0)
    for name,title in (('blur','Blur'),('colour','Colour'),('exposure','Brightness / contrast'),('noise','Noise'),('compression','JPEG compression')):
        field=prefix+'_effects_'+name; checkbox=QCheckBox(title)
        checkbox.setChecked(getattr(center.settings,field)); checkbox.toggled.connect(lambda value,field=field:center.change(field,value))
        center._discrete_registry.register('discrete.'+field+'.1',field,checkbox);row.addWidget(checkbox)
    center.setting(layout,'Effects','Choose at least one. No crop, rotation, resize or flip is applied.',center.feature_control(effects))
    if prefix=='ultralytics':
        unavailable=QLabel(center.t('Cloud replacement verifies the new image before deleting the original. Labels, split and custom metadata are preserved; image IDs and filenames change.'))
        unavailable.setWordWrap(True);layout.addWidget(unavailable)
    else:
        from mission_layout import WrappedPath
        center.dataset_effects_target=WrappedPath()
        layout.addWidget(center.dataset_effects_target)
        button=QPushButton('Preview effects');button.clicked.connect(lambda:inspect_local(center))
        layout.addWidget(center.feature_control(button));refresh_local_target(center)


def fingerprint(settings):
    fields={k:v for k,v in asdict(settings).items() if k.startswith('ultralytics_')}
    if not settings.ultralytics_annotation_model:fields['fallback_model']=settings.model_path
    from vapor_lock_gui import resolve_product_path
    model=resolve_product_path(settings.ultralytics_annotation_model or settings.model_path)
    try:
        info=model.stat()
        fields['model_identity']=(str(model),info.st_ino,info.st_size,info.st_mtime_ns)
    except OSError:fields['model_identity']=None
    return hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def refresh_key_status(center):
    state=key_state(center.settings.ultralytics_key_protected)
    text={'missing':'No key configured','available':'Key available for this profile',
          'unavailable':'Key unavailable here — re-enter it'}[state]
    if hasattr(center,'ultralytics_key_status'):
        center.ultralytics_key_status.setText(center.t(text))
    return state


def edit_key(center, remove=False):
    if native_injection() is not None or getattr(center,'_native_closing',False):
        return False
    process=getattr(center,'_dataset_worker',None)
    if process is not None:
        from PySide6.QtCore import QProcess
        if process.state()!=QProcess.ProcessState.NotRunning:return False
    stamp=fingerprint(center.settings);profile=getattr(center,'_active_profile_path',None)
    if remove:
        center.change('ultralytics_key_protected','');refresh_key_status(center);return True
    # Native, modal and never part of the native/HTML component tree.
    dialog=QDialog(center);dialog.setWindowTitle(center.t('Ultralytics API key'));dialog.setMinimumWidth(440)
    layout=QVBoxLayout(dialog)
    help_text=QLabel(center.t('Saved encrypted with this profile. Another Windows account or machine may require re-entry.'))
    help_text.setWordWrap(True);layout.addWidget(help_text)
    editor=QLineEdit();editor.setEchoMode(QLineEdit.EchoMode.Password);editor.setMaxLength(43)
    editor.setAccessibleName(center.t('Ultralytics API key'));layout.addWidget(editor)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    if dialog.exec()!=QDialog.DialogCode.Accepted:
        editor.clear();return False
    try:
        if (getattr(center,'_native_closing',False) or False
                or fingerprint(center.settings)!=stamp or getattr(center,'_active_profile_path',None)!=profile
                or getattr(getattr(center,'_live_owner',None),'_transition',None) is not None):
            return False
        protected=protect_key(editor.text().strip())
        center.change('ultralytics_key_protected',protected)
        refresh_key_status(center);return True
    except (ValueError,OSError):
        QMessageBox.warning(center,center.t('Ultralytics API key'),center.t('Enter a valid Ultralytics API key.'))
        return False
    finally:
        editor.clear()


def add_ultralytics_tab(center, layout):
    outer=layout
    tabs=center.settings_tabs(outer,'Annotation','Training',minimum_height=160,owner_attribute='ultralytics_tabs',fixed_titles=('Annotation','Training'))
    center.ultralytics_training_layout=tabs['Training']
    layout=tabs['Annotation']
    from mission_layout import TaskColumns, TaskCard
    columns=TaskColumns(proportions=(48,52))
    for panel in (columns.left,columns.right):panel.setObjectName('missionTaskColumn')
    layout.addWidget(columns,1)
    layout=columns.left_layout
    anchors={}
    def section(title, attribute):
        nonlocal layout
        target=TaskCard(title,'image' if title=='Preview and apply' else 'connection' if title=='Ultralytics connection' else 'tag')
        (columns.right_layout if title=='Preview and apply' else columns.left_layout).addWidget(target)
        layout=target.body
        target.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        target.setAccessibleName(center.t(title))
        anchors[title]=target
        setattr(center,attribute,target)
    def reveal_section(name):
        center.ultralytics_tabs.setCurrentIndex(0)
        target=anchors.get(name)
        if target is None:return
        navigation.setCurrentIndex(navigation.findData(name))
        def reveal():
            target.setFocus(Qt.FocusReason.OtherFocusReason)
            parent=target.parentWidget()
            while parent is not None:
                if isinstance(parent,QScrollArea):
                    parent.widget().layout().activate()
                    bar=parent.verticalScrollBar()
                    bar.setValue(bar.value()+target.mapTo(parent.viewport(),QPoint(0,0)).y()-16)
                parent=parent.parentWidget()
        # Both nested scroll areas must finish laying out the newly selected tab.
        QTimer.singleShot(0,reveal)
    navigation=QComboBox()
    for title in ('Ultralytics connection','Annotation settings','Random image effects','Preview and apply'):
        navigation.addItem(center.t(title),title)
    navigation.setAccessibleName(center.t('Jump to section'))
    navigation.activated.connect(lambda i:reveal_section(navigation.itemData(i)))
    navigation_row=QWidget();navigation_layout=QVBoxLayout(navigation_row)
    navigation_layout.setContentsMargins(0,0,0,0)
    navigation_label=QLabel(center.t('Jump to section'));navigation_label.setBuddy(navigation)
    navigation_layout.addWidget(navigation_label);navigation_layout.addWidget(navigation)
    layout.addWidget(navigation_row)
    center.ultralytics_section_navigation=navigation
    center._ultralytics_section_anchors=anchors
    activity=QWidget();activity_layout=QVBoxLayout(activity);activity_layout.setContentsMargins(0,0,0,0)
    activity_layout.addWidget(QLabel(center.t('Ultralytics annotation task')))
    center.ultralytics_activity_target=QLabel();center.ultralytics_activity_target.setWordWrap(True)
    center.ultralytics_activity_target.setProperty('_vaporlock_i18n_skip',True)
    activity_layout.addWidget(center.ultralytics_activity_target)
    center.ultralytics_worker_status=QLabel('Preview is read-only. Apply requires confirmation.');center.ultralytics_worker_status.setWordWrap(True)
    center.ultralytics_worker_status.setProperty('_vaporlock_i18n_skip',True)
    center.ultralytics_activity_clock=QLabel();center.ultralytics_activity_clock.setWordWrap(True)
    center.ultralytics_activity_clock.setProperty('_vaporlock_i18n_skip',True)
    activity_layout.addWidget(center.ultralytics_worker_status);activity_layout.addWidget(center.ultralytics_activity_clock)
    from mission_layout import ActionFlow
    actions=ActionFlow()
    center.ultralytics_stop_button=QPushButton('Stop background task');center.ultralytics_stop_button.setObjectName('dangerButton')
    center.ultralytics_stop_button.setProperty('missionIcon','stop')
    center.ultralytics_stop_button.setEnabled(False);center.ultralytics_stop_button.clicked.connect(center.stop_dataset_worker)
    details=QPushButton('View run details');details.clicked.connect(lambda:reveal_section('Preview and apply'))
    details.setProperty('missionIcon','search')
    actions.addWidget(center.ultralytics_stop_button);actions.addWidget(details);activity_layout.addLayout(actions)
    activity_card=TaskCard('Annotation activity','clock')
    activity_card.body.addWidget(activity)
    columns.right_layout.addWidget(activity_card)
    section('Ultralytics connection','ultralytics_connection_section')
    for title,field,detail in (
            ('Workspace','ultralytics_owner','The owner or team handle from the Ultralytics dataset URL.'),
            ('Dataset','ultralytics_dataset','The dataset name from the Ultralytics dataset URL.')):
        editor=QLineEdit(getattr(center.settings,field));editor.setMaxLength(128)
        editor.editingFinished.connect(lambda field=field,editor=editor:center.change(field,editor.text().strip()))
        center.setting(layout,title,detail,center.feature_control(editor))
    credentials=QWidget();row=QVBoxLayout(credentials);row.setContentsMargins(0,0,0,0)
    center.ultralytics_key_status=QLabel();center.ultralytics_key_status.setWordWrap(True)
    row.addWidget(center.ultralytics_key_status)
    save_note=QLabel(center.t('Save the profile to keep key changes.'));save_note.setWordWrap(True);row.addWidget(save_note)
    for title,remove in (('Set/change key',False),('Remove key',True)):
        button=QPushButton(title);button.clicked.connect(lambda _checked=False,remove=remove:edit_key(center,remove))
        row.addWidget(button)
    center.setting(layout,'API key','Saved encrypted with this profile. Availability does not confirm authentication.',center.feature_control(credentials))
    refresh_key_status(center)
    directory=QWidget();row=QHBoxLayout(directory);row.setContentsMargins(0,0,0,0)
    editor=QLineEdit(center.settings.ultralytics_output_directory)
    editor.editingFinished.connect(lambda:center.change('ultralytics_output_directory',editor.text().strip()))
    browse=QPushButton('Browse…')
    browse.setProperty('missionIcon','folder')
    def choose():
        path=QFileDialog.getExistingDirectory(center,center.t('Ultralytics working folder'),editor.text())
        if path:editor.setText(path);center.change('ultralytics_output_directory',path)
    browse.clicked.connect(choose);row.addWidget(editor,1);row.addWidget(browse)
    center.setting(layout,'Working folder','Stores local previews and operation reports. This does not change the Collection dataset folder.',center.feature_control(directory))
    section('Annotation settings','ultralytics_annotation_section')
    model=QComboBox();model.addItem('Use current detection model','')
    for path in center.discovered_local_models():
        model.addItem(path.name,str(path))
    model.setCurrentIndex(max(0,model.findData(center.settings.ultralytics_annotation_model)))
    model.currentIndexChanged.connect(lambda i:center.change('ultralytics_annotation_model',str(model.itemData(i) or '')))
    center.setting(layout,'Annotation model','Choose a local ONNX model matching the cloud dataset task and classes.',center.feature_control(model))
    provider=QComboBox()
    for title,value in (('Auto (GPU preferred)','auto'),('DirectML','directml'),('CUDA','cuda'),('CPU','cpu')):
        provider.addItem(title,value)
    provider.setCurrentIndex(max(0,provider.findData(center.settings.ultralytics_annotation_provider)))
    provider.currentIndexChanged.connect(lambda i:center.change('ultralytics_annotation_provider',provider.itemData(i)))
    center._discrete_registry.register('discrete.ultralytics_annotation_provider.1','ultralytics_annotation_provider',provider)
    center.ultralytics_annotation_backend=provider
    center.setting(layout,'Annotation backend','Auto tries DirectML, then CPU. CUDA is an explicit choice and requires verified CUDA runtime setup. Explicit GPU choices stop on failure. TensorRT RTX annotation is unavailable.',center.feature_control(provider))
    for title,field,low,high,decimals in (
            ('Agreement passes','ultralytics_annotation_passes',1,10,0),
            ('Annotation confidence','ultralytics_annotation_confidence',.01,.99,2),
            ('Agreement overlap','ultralytics_annotation_iou',.05,.99,2),
            ('NMS overlap threshold','ultralytics_annotation_nms_iou',.01,.99,2),
            ('Image limit','ultralytics_image_limit',1,10000,0)):
        detail='Each pass runs inference again with a different view. Ten passes require ten inferences per image.' if field=='ultralytics_annotation_passes' else 'Used only by this Ultralytics workflow.'
        if field=='ultralytics_annotation_nms_iou':detail='Suppress duplicate boxes in annotation results. Lower values remove more overlaps. Different classes remain separate.'
        if field=='ultralytics_annotation_iou':detail='Minimum overlap to match the same object between annotation passes. This is separate from duplicate suppression.'
        center.setting(layout,title,detail,center.feature_control(center.range(field,low,high,decimals)))
    effects_card=TaskCard('Image effects','image')
    columns.left_layout.addWidget(effects_card)
    layout=effects_card.body
    effects_start=layout.count()
    add_effects_controls(center,layout,'ultralytics')
    effects_heading=layout.itemAt(effects_start).widget()
    effects_heading.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    effects_heading.setAccessibleName(center.t('Random image effects'))
    anchors['Random image effects']=effects_heading
    center.ultralytics_effects_section=effects_heading
    section('Preview and apply','ultralytics_results_section')
    center.ultralytics_result_details=QLabel();center.ultralytics_result_details.setWordWrap(True)
    center.ultralytics_result_details.setProperty('_vaporlock_i18n_skip',True)
    center.ultralytics_result_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    center.ultralytics_result_details.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Minimum)
    layout.addWidget(center.ultralytics_result_details)
    center.ultralytics_run_summary=QLabel();center.ultralytics_run_summary.setWordWrap(True);layout.addWidget(center.ultralytics_run_summary)
    center.ultralytics_run_summary.setProperty('_vaporlock_i18n_skip',True)
    refresh_run_summary(center)
    for title,attr,callback in (
            ('Preview annotations','ultralytics_preview_button',lambda:run_cloud(center,False)),
            ('Inspect preview','ultralytics_inspect_button',lambda:inspect_cloud(center)),
            ('Apply labels and selected effects','ultralytics_apply_button',lambda:run_cloud(center,True))):
        button=QPushButton(title);button.clicked.connect(callback);setattr(center,attr,button)
        button.setProperty('missionIcon',{'ultralytics_preview_button':'start','ultralytics_inspect_button':'search','ultralytics_apply_button':'upload'}[attr])
        if attr=='ultralytics_apply_button':button.setEnabled(False)
        if attr=='ultralytics_inspect_button':button.setEnabled(False)
        if attr=='ultralytics_stop_button':button.setEnabled(False);button.setObjectName('dangerButton')
        layout.addWidget(center.feature_control(button))
    center.ultralytics_backend_status=QLabel('Annotation backend has not started.');center.ultralytics_backend_status.setWordWrap(True)
    center.ultralytics_output_status=QLabel();center.ultralytics_output_status.setWordWrap(True)
    center.ultralytics_output_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(center.ultralytics_backend_status);layout.addWidget(center.ultralytics_output_status)
    layout.addStretch(1)
    from mission_layout import bound_inventory
    bound_inventory(columns.left_layout,'Ultralytics connection, annotation settings and effects',keep=1)
    columns.right_layout.addStretch(1)
    if not hasattr(center,'_ultralytics_activity_timer'):
        from annotation_progress import refresh_activity
        center._ultralytics_activity_timer=QTimer(center);center._ultralytics_activity_timer.setInterval(1000)
        center._ultralytics_activity_timer.timeout.connect(lambda:refresh_activity(center));center._ultralytics_activity_timer.start()


def refresh_run_summary(center):
    if not hasattr(center,'ultralytics_run_summary'):return
    s=center.settings
    target=s.ultralytics_owner+'/'+s.ultralytics_dataset
    activity_target=getattr(center,'_ultralytics_job_target',None) or target
    center.ultralytics_activity_target.setText(center.tf('Target: {target}',target=activity_target))
    model=Path(s.ultralytics_annotation_model or s.model_path).name
    mode=center.t('Image effects disabled') if not s.ultralytics_effects_enabled else center.t('Create extra copies' if s.ultralytics_effects_extra_copies else 'Overwrite existing images')
    center.ultralytics_run_summary.setText(center.t('Settings for next run') + '\n' + center.tf('Target: {target}\nModel: {model} · Backend: {backend} · Passes: {passes}\n{mode}',
        target=target,model=model,backend=s.ultralytics_annotation_provider,passes=s.ultralytics_annotation_passes,mode=mode))


def effect_options(settings,prefix):
    return VariationOptions(enabled=getattr(settings,prefix+'_effects_enabled'),
        extra_copies=getattr(settings,prefix+'_effects_extra_copies'),
        copies=getattr(settings,prefix+'_effects_copies'),strength=getattr(settings,prefix+'_effects_strength'),
        effects=tuple(name for name in EFFECTS if getattr(settings,prefix+'_effects_'+name)))


def effect_arguments(settings,prefix):
    if not getattr(settings,prefix+'_effects_enabled'):
        return []
    effects=[name for name in EFFECTS if getattr(settings,prefix+'_effects_'+name)]
    if not effects:
        raise ValueError('Choose at least one image effect.')
    result=['--variations-enabled','--variation-strength',getattr(settings,prefix+'_effects_strength'),
            '--variation-effects',','.join(effects),'--variation-copies',str(getattr(settings,prefix+'_effects_copies'))]
    if getattr(settings,prefix+'_effects_extra_copies'):result.append('--extra-copies')
    return result


def refresh_local_target(center):
    if not hasattr(center,'dataset_effects_target'):return
    mode=center.t('Image effects disabled') if not center.settings.dataset_effects_enabled else center.t(
        'Create extra copies' if center.settings.dataset_effects_extra_copies else 'Overwrite existing images')
    center.dataset_effects_target.setText(str(center.dataset_root_for_tools())+'\n'+mode)


def _context(center,local):
    fields={k:v for k,v in asdict(center.settings).items() if k.startswith(('dataset_','ultralytics_')) or k=='model_path'}
    model_field=center.settings.dataset_annotation_model if local else center.settings.ultralytics_annotation_model
    from vapor_lock_gui import resolve_product_path
    model=resolve_product_path(model_field or center.settings.model_path)
    try:
        info=model.stat();model_identity=(str(model),info.st_ino,info.st_size,info.st_mtime_ns)
    except OSError:model_identity=None
    fields.update(model_identity=model_identity,profile=str(getattr(center,'_active_profile_path',None)),
        owner_generation=getattr(center,'_start_intent_generation',0),
        closing=bool(getattr(center,'_native_closing',False)),rejected=bool(False))
    return hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _model_hash(center,local):
    from vapor_lock_gui import resolve_product_path
    from annotation_output import plain,held_directory
    from dataset_transaction import HeldFile
    field=center.settings.dataset_annotation_model if local else center.settings.ultralytics_annotation_model
    path=plain(resolve_product_path(field or center.settings.model_path))
    value=hashlib.sha256();size=0
    with held_directory(path.parent),HeldFile(path) as held:
        for block in held.blocks():
            size+=len(block)
            if size>1024**3:raise ValueError('Annotation model too large.')
            value.update(block)
    return value.hexdigest()


class WrappedPreviewPath(QTextEdit):
    """Copyable exact plain path; wrap unbroken components to the viewport."""
    def __init__(self,value):
        super().__init__();self.setReadOnly(True);self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.setStyleSheet('background:transparent; border:0; padding:0;')
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self.setPlainText(str(value));self._fit()

    def _fit(self,*_args):
        height=max(self.fontMetrics().lineSpacing()+8,int(self.document().size().height()+9))
        if self.height()!=height:self.setFixedHeight(height)


def preview_dialog(center,preview,*,confirm=False,cloud=False):
    """Native-only inspector. No keys, paths from HTML, or external resource URLs."""
    dialog=QDialog(center);dialog.setWindowTitle(center.t('Representative effects preview' if preview['local'] else 'Annotation preview'))
    dialog.resize(900,660);layout=QVBoxLayout(dialog)
    scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();body=QVBoxLayout(content)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    def text(value):
        label=QLabel(value);label.setTextFormat(Qt.TextFormat.PlainText);label.setWordWrap(True)
        label.setMinimumWidth(0);label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);body.addWidget(label)
    body.addWidget(WrappedPreviewPath(preview['target']))
    if preview.get('folder'):body.addWidget(WrappedPreviewPath(preview['folder']))
    options=preview['options']
    mode=center.t('Image effects disabled') if not options.enabled else center.t('Create extra copies' if options.extra_copies else 'Overwrite existing images')
    text(mode+'\n'+center.tf('Seed: {seed} · Strength: {strength} · Copies per image: {copies}',
        seed=preview['seed'],strength=center.t(options.strength.title()),copies=options.copies if options.enabled and options.extra_copies else 0))
    titles={'blur':'Blur','colour':'Colour','exposure':'Brightness / contrast','noise':'Noise','compression':'JPEG compression'}
    text(center.t('Effects')+': '+', '.join(center.t(titles[name]) for name in options.effects))
    if options.enabled and options.extra_copies:text(center.t('Shows the first extra copy for each sampled image.'))
    if preview['local']:
        text(center.tf('Up to {count} images. Empty or failed annotations receive no effects.',count=preview['sources']))
        if options.enabled and options.extra_copies:
            text(center.tf('Up to {count} additional images (estimate). Only accepted annotations receive copies.',count=preview['copies']))
        text(center.t('Overlays show current saved labels. The next annotation run determines which images receive effects. Counts are estimates.'))
    else:
        text(center.tf('Preview: {labels} label updates · {replace} image replacements · {copies} extra copies.',
            labels=preview['labels'],replace=preview['replace'],copies=preview['copies']))
        if options.enabled and options.extra_copies:
            text(center.tf('Additional uploads to {target}: {count} images.',target=preview['target'],count=preview['copies']))
        text(center.t('Splits')+': '+', '.join(k+': '+str(v) for k,v in preview['splits'].items()))
    text(center.t('Classes')+': '+(', '.join(preview['classes']) or center.t('No saved labels available')))
    if cloud and confirm:
        text(center.t('Another editor’s labels may be overwritten if they change during Apply. Stop other editing and imports before continuing.'))
        text(center.t('Cancellation stops further writes. Completed cloud changes are not undone.'))
    if preview['replace'] and not preview['local']:
        text(center.t('Cloud replacement verifies the new image before deleting the original. Labels, split and custom metadata are preserved; image IDs and filenames change.'))
    chooser=QComboBox();chooser.setAccessibleName(center.t('Preview sample'))
    chooser.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    chooser.setMinimumContentsLength(12);chooser.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
    for sample in preview['samples']:chooser.addItem(sample['name'])
    body.addWidget(chooser);panels=QWidget();columns=QVBoxLayout(panels);pictures=[]
    # Stack samples vertically; scroll instead of clipping at compact/200% DPI.
    for title in ('Before effects','After effects'):
        columns.addWidget(QLabel(center.t(title)));image=QLabel();image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        columns.addWidget(image);pictures.append(image)
    detail=QLabel();detail.setWordWrap(True);body.addWidget(panels);body.addWidget(detail)
    def show_sample(index):
        if not 0<=index<len(preview['samples']):return
        sample=preview['samples'][index]
        for label,key in zip(pictures,('before','after')):
            pixmap=QPixmap()
            if not pixmap.loadFromData(sample[key],'PNG'):raise ValueError('Invalid preview thumbnail.')
            label.setPixmap(pixmap)
        detail.setText(center.tf('Sample: {width} × {height} · {count} labels',width=sample['dimensions'][0],height=sample['dimensions'][1],count=sample['labelCount']))
    chooser.currentIndexChanged.connect(show_sample);show_sample(chooser.currentIndex())
    if not preview['samples'] or preview['unavailable']:text(center.t('Some preview samples or saved-label overlays are unavailable. Refresh the preview before continuing.'))
    body.addStretch(1);scroll.setWidget(content);layout.addWidget(scroll)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel if confirm else QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    if confirm:
        title='Apply labels and selected effects' if cloud else 'Annotate and create copies' if options.extra_copies else 'Annotate and overwrite'
        accept=buttons.addButton(center.t(title),QDialogButtonBox.ButtonRole.AcceptRole)
        accept.setAutoDefault(False);accept.setEnabled(bool(preview['samples']) and not preview['unavailable'])
        accept.clicked.connect(dialog.accept)
        cancel=buttons.button(QDialogButtonBox.StandardButton.Cancel);cancel.setDefault(True);cancel.setFocus()
    layout.addWidget(buttons)
    return dialog


def _idle(center):
    from PySide6.QtCore import QProcess
    process=getattr(center,'_dataset_worker',None)
    return (native_injection() is None and not getattr(center,'_native_closing',False)
        and not False
        and (process is None or process.state()==QProcess.ProcessState.NotRunning))


def inspect_local(center):
    if not _idle(center):return False
    from annotation_preview import local_preview,source_signature
    try:
        stamp=_context(center,True);root=center.dataset_root_for_tools()
        preview=local_preview(root,effect_options(center.settings,'dataset'))
        preview['modelSHA256']=_model_hash(center,True)
        preview_dialog(center,preview).exec()
        if (_idle(center) and _context(center,True)==stamp and source_signature(root)==preview['signature']
                and _model_hash(center,True)==preview['modelSHA256']):
            center._local_effect_preview={**preview,'context':stamp};return True
    except (ValueError,OSError):
        QMessageBox.warning(center,center.t('Random image effects'),center.t('Refresh the preview after changing the dataset or settings.'))
    return False


def inspect_cloud(center):
    if not _idle(center):return False
    from annotation_preview import cloud_preview
    saved=getattr(center,'_ultralytics_preview',None)
    try:
        if saved is None or saved['fingerprint']!=fingerprint(center.settings):raise ValueError('Stale preview.')
        preview_dialog(center,cloud_preview(saved['folder'],saved['planSHA256'])).exec();return True
    except (ValueError,OSError):
        QMessageBox.warning(center,center.t('Ultralytics'),center.t('Refresh the preview after changing the dataset or settings.'))
        return False


def confirm_local_effects(center,root):
    if not center.settings.dataset_effects_enabled:return True
    if not _idle(center):return False
    from annotation_preview import local_preview,source_signature
    try:
        stamp=_context(center,True);preview=getattr(center,'_local_effect_preview',None)
        if not preview or preview['context']!=stamp or source_signature(root)!=preview['signature']:
            preview={**local_preview(root,effect_options(center.settings,'dataset')),'context':stamp}
            preview['modelSHA256']=_model_hash(center,True)
        accepted=preview_dialog(center,preview,confirm=True).exec()==QDialog.DialogCode.Accepted
        if (accepted and _idle(center) and _context(center,True)==stamp and source_signature(root)==preview['signature']
                and _model_hash(center,True)==preview['modelSHA256']):
            center._local_effect_preview=preview;return True
    except (ValueError,OSError):
        QMessageBox.warning(center,center.t('Random image effects'),center.t('Refresh the preview after changing the dataset or settings.'))
    return False


def cloud_event(center,payload,stamp,*,replay=False):
    event=payload['type']
    if event=='cloud_stage':
        from annotation_progress import show_stage
        show_stage(center,payload,cloud=True);return
    if event=='cloud_preview_ready':
        if fingerprint(center.settings)!=stamp:return
        center._ultralytics_preview={**payload,'fingerprint':stamp}
        center.ultralytics_inspect_button.setEnabled(True)
        from dataset_paths import display_path
        center.ultralytics_output_status.setText(display_path(payload['folder']))
        center.ultralytics_worker_status.setText(center.tf('Preview: {labels} label updates · {replace} image replacements · {copies} extra copies.',
            labels=payload['annotated'],replace=payload['replace'],copies=payload['generated']))
        if payload['replace']:
            center.ultralytics_worker_status.setText(center.ultralytics_worker_status.text()+'\n'+center.t('Cloud replacement verifies the new image before deleting the original. Labels, split and custom metadata are preserved; image IDs and filenames change.'))
    elif event=='cloud_progress':
        from annotation_progress import show_stage
        stage={'apply':'labels','replace':'delete','download':'download'}.get(payload.get('stage'))
        if stage:show_stage(center,{**payload,'stage':stage},cloud=True)
    elif event in ('cloud_complete','cloud_stopped'):
        from annotation_progress import bounded
        if any(type(payload.get(k)) is not int or not bounded(payload[k],1000000) for k in ('confirmed','skipped','queued','unknown')):return
        replacement=payload.get('replacementState')
        if replacement is not None and (type(replacement) is not dict or any(type(replacement.get(k)) is not int or not bounded(replacement[k],1000000) for k in ('replaced','uploaded'))):return
        worker=getattr(center,'_dataset_worker',None)
        if not replay:
            if worker is not None:worker.setProperty('vaporlock_cloud_outcome',True)
            center._ultralytics_preview=None
            center._ultralytics_result_payload=dict(payload)
        center.ultralytics_worker_status.setText(center.tf('Cloud result: {confirmed} confirmed annotations · {skipped} skipped · {queued} copies queued · {unknown} uncertain.',
            confirmed=payload['confirmed'],skipped=payload['skipped'],queued=payload['queued'],unknown=payload['unknown']))
        if event=='cloud_stopped':
            center.ultralytics_worker_status.setText(center.ultralytics_worker_status.text()+'\n'+center.t('Cloud task stopped. Confirmed changes remain saved. Review the operation report before another Apply.'))
            from annotation_progress import error_text
            center.ultralytics_worker_status.setText(center.ultralytics_worker_status.text()+'\n'+error_text(center,payload.get('code')))
        replacement=payload.get('replacementState')
        if type(replacement) is dict:
            center.ultralytics_worker_status.setText(center.ultralytics_worker_status.text()+'\n'+center.tf(
                'Image replacement: {replaced} originals removed · {uploaded} replacements verified.',
                replaced=replacement['replaced'],uploaded=replacement['uploaded']))
            if event=='cloud_stopped':
                center.ultralytics_worker_status.setText(center.ultralytics_worker_status.text()+'\n'+center.t(
                    'Interrupted replacements can leave both images. Original downloads and the replacement report remain in the working folder.'))
        upload=payload.get('uploadState')
        if type(upload) is dict and upload.get('sessionCreated') is True:
            center.ultralytics_worker_status.setText(center.ultralytics_worker_status.text()+'\n'+center.tf(
                'Upload stages — archive stored: {storage} · session completed: {complete} · dataset import queued: {queued}.',
                storage=center.t('Yes' if upload.get('storageConfirmed') is True else 'Not confirmed'),
                complete=center.t('Yes' if upload.get('sessionCompleted') is True else 'Not confirmed'),
                queued=center.t('Yes' if upload.get('ingestQueued') is True else 'Not confirmed')))
        full_text=center.ultralytics_worker_status.text()
        center.ultralytics_result_details.setText(full_text)
        center.ultralytics_worker_status.setText(full_text.split('\n',1)[0])
        if not replay:center.log('WARN' if event=='cloud_stopped' else 'INFO',full_text)


def run_cloud(center, apply):
    from vapor_lock_gui import ROOT, resolve_product_path
    if native_injection() is not None or False or False or center._native_closing:
        return False
    process=getattr(center,'_dataset_worker',None)
    if process is not None:
        from PySide6.QtCore import QProcess
        if process.state()!=QProcess.ProcessState.NotRunning:return False
    try:
        from ultralytics_annotation import target_path
        target_path(center.settings.ultralytics_owner,center.settings.ultralytics_dataset)
        key=restore_key(center.settings.ultralytics_key_protected)
        stamp=fingerprint(center.settings)
        if apply:
            preview=getattr(center,'_ultralytics_preview',None)
            if not preview or preview['fingerprint']!=stamp:
                raise ValueError('Preview changed. Run Preview again.')
            from annotation_preview import cloud_preview
            context=_context(center,False);samples=cloud_preview(preview['folder'],preview['planSHA256'])
            accepted=preview_dialog(center,samples,confirm=True,cloud=True).exec()==QDialog.DialogCode.Accepted
            if (not accepted or not _idle(center) or _context(center,False)!=context
                    or fingerprint(center.settings)!=stamp):return False
            from annotation_output import read_bytes,digest
            plan_data=read_bytes(Path(preview['folder'])/'platform-preview.json',4*1024*1024)
            if digest(plan_data)!=preview['planSHA256'] or json.loads(plan_data)['modelSHA256']!=_model_hash(center,False):return False
            arguments=['platform-apply','--dataset',preview['folder'],'--plan-sha256',preview['planSHA256'],'--options-fingerprint',stamp]
            center._ultralytics_preview=None;center.ultralytics_apply_button.setEnabled(False)
            center.ultralytics_inspect_button.setEnabled(False)
            center.ultralytics_output_status.setText(str(Path(preview['folder'])/'platform-apply.jsonl'))
        else:
            model=resolve_product_path(center.settings.ultralytics_annotation_model or center.settings.model_path)
            if not model.is_file() or model.suffix.lower()!='.onnx':
                raise ValueError('Choose an ONNX annotation model, or load a current ONNX model first.')
            working=resolve_product_path(center.settings.ultralytics_output_directory) if center.settings.ultralytics_output_directory else ROOT/'ultralytics-work'
            if not working.exists():working.mkdir(parents=True)
            arguments=['platform-preview','--dataset',str(working),'--model',str(model),
                '--expected-model-sha256',_model_hash(center,False),
                '--owner',center.settings.ultralytics_owner,'--cloud-dataset',center.settings.ultralytics_dataset,
                '--provider',center.settings.ultralytics_annotation_provider,'--passes',str(center.settings.ultralytics_annotation_passes),
                '--confidence',str(center.settings.ultralytics_annotation_confidence),'--consensus-iou',str(center.settings.ultralytics_annotation_iou),
                '--nms-iou',str(center.settings.ultralytics_annotation_nms_iou),
                '--limit',str(center.settings.ultralytics_image_limit),'--options-fingerprint',stamp,*effect_arguments(center.settings,'ultralytics')]
            center._ultralytics_preview=None;center.ultralytics_apply_button.setEnabled(False)
        return center.start_dataset_worker(arguments,stdin_payload={'apiKey':key},workflow='ultralytics',preview_fingerprint=stamp)
    except (ValueError,OSError):
        QMessageBox.warning(center,center.t('Ultralytics'),center.t('Check the dataset, model, working folder and saved API key, then run Preview again.'))
        return False
