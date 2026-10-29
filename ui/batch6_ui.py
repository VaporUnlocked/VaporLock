"""Small additions to the existing native execution and annotation controls."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QPushButton, QLabel
from batch4_guard import editable
from inference_devices import devices


class InferenceGpuControls(QWidget):
    def __init__(self, center):
        super().__init__(); self.center=center; self.controls={}; self.catalog={}
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.help=QLabel();self.help.setWordWrap(True);layout.addWidget(self.help)
        for provider in ('dml','cuda','trt'):
            combo=QComboBox();combo.setObjectName('inferenceGpu_'+provider)
            combo.currentIndexChanged.connect(lambda _index,p=provider:self.commit(p))
            self.controls[provider]=combo;layout.addWidget(combo)
        self.refresh_button=QPushButton();self.refresh_button.clicked.connect(self.refresh);layout.addWidget(self.refresh_button)
        self.actual=QLabel();self.actual.setWordWrap(True);self.actual.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.actual)
        self.retranslate();self.populate(False)

    def retranslate(self):
        c=self.center
        self.help.setText(c.t('Inference GPU is separate from Capture display. Changes apply on the next Start. Automatic provider fallback is disabled when a specific DirectML adapter is selected.'))
        self.refresh_button.setText(c.t('Refresh GPUs'))
        self.refresh_actual()

    def refresh_actual(self):
        c=self.center; observed=getattr(c,'_inference_device_observation',None)
        text=c.t('Active inference device: unavailable')
        if (observed is not None and c.engine.session_running
                and observed[0]==c.engine._launch_generation and observed[1]==c.engine.active_provider):
            generation,provider,index,name=observed
            text=(c.t('Active inference device: CPU') if provider=='cpu' else
                c.tf('Active inference device: {provider} · {device} · index {index}',
                     provider=c.engine.provider_label(provider),device=name,index=index))
        if self.actual.text()!=text:self.actual.setText(text)

    def populate(self, discover):
        c=self.center; provider=c.settings.provider
        chosen='dml' if provider=='auto' else provider
        for name,combo in self.controls.items():
            combo.blockSignals(True);combo.clear();combo.addItem(c.t('Automatic'),-1)
            saved=getattr(c.settings,'inference_'+name+'_device')
            if discover and name==chosen:self.catalog[name]=devices(name)
            rows=self.catalog.get(name,())
            for index,label in rows:combo.addItem(f'{"DirectML adapter" if name=="dml" else "CUDA GPU"} {index} — {label}',index)
            if saved>=0 and combo.findData(saved)<0:
                state='Unavailable — choose another GPU or refresh' if name in self.catalog else 'Saved selection — refresh to verify'
                combo.addItem(c.tf('{state} · index {index}',state=c.t(state),index=saved),saved)
            combo.setCurrentIndex(max(0,combo.findData(saved)));combo.blockSignals(False)
            combo.setVisible(name==chosen);combo.setAccessibleName(c.t('Inference GPU')+' '+name.upper())
        self.refresh_button.setEnabled(chosen!='cpu')
        self.refresh_actual()

    def refresh(self):self.populate(True)

    def showEvent(self,event):
        super().showEvent(event)
        # Discovery happens on the explicit Refresh action, not during startup.
        self.populate(False)

    def commit(self,provider):
        value=self.controls[provider].currentData()
        if value is None:return
        if editable(self.center,stopped=True):self.center.change('inference_'+provider+'_device',value)
        self.populate(False)


def add_gpu_controls(center,layout,provider):
    controls=InferenceGpuControls(center);center.inference_gpu_controls=controls
    center.setting(layout,'Inference GPU','Use provider-specific GPU indices. A missing explicit GPU stops initialization.',controls)
    provider.currentIndexChanged.connect(lambda _index:controls.populate(False))


def observe_device(center,data):
    provider=center.engine.active_provider;index=data.get('inference_device_index');name=data.get('inference_device')
    valid=(center.engine.session_running and provider in ('cpu','dml','cuda','trt')
        and type(index) is int and ((provider=='cpu' and index==-1) or (provider!='cpu' and 0<=index<64))
        and type(name) is str and len(name)<=256 and not any(ord(c)<32 for c in name)
        and (bool(name) or provider=='cpu'))
    center._inference_device_observation=(center.engine._launch_generation,provider,index,name) if valid else None
    if hasattr(center,'inference_gpu_controls'):center.inference_gpu_controls.refresh_actual()


def add_batch_controls(center,layout):
    combo=QComboBox();combo.setObjectName('annotationBatchSize')
    for value in (1,2,4,8):combo.addItem(str(value),value)
    combo.setCurrentIndex(max(0,combo.findData(center.settings.dataset_annotation_batch)))
    def change(index):
        if editable(center,stopped=True):center.change('dataset_annotation_batch',combo.itemData(index))
        combo.blockSignals(True);combo.setCurrentIndex(combo.findData(center.settings.dataset_annotation_batch));combo.blockSignals(False)
    combo.currentIndexChanged.connect(change)
    center.setting(layout,'Images per inference batch','Local annotation only. Fixed single-image models use 1; progress reports the effective batch. Larger batches need more memory and may not be faster.',center.feature_control(combo))


def show_batch_report(center,backend_status,worker_status,payload):
    requested=payload.get('requested');effective=payload.get('effective')
    if type(requested) is not int or requested not in (1,2,4,8) or type(effective) is not int or not 1<=effective<=requested:return False
    text=center.tf('Annotation batch: requested {requested}, effective {effective}.',requested=requested,effective=effective)
    if effective!=requested:text+=' '+center.t('The model uses a fixed batch size.')
    backend_status.setText(backend_status.text()+'\n'+text)
    worker_status.setText(text);center.log('INFO',text)
    return True
