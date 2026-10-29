"""Bounded .cloud model inspection in the existing model workspace."""
from pathlib import Path


def inspect(center):
    if center.model_probe_busy() or center.engine.active or center._policy_start_pending:return
    from cloud_jobs import CloudJob
    try:
        from vapor_lock_gui import resolve_product_path
        model=resolve_product_path(center.settings.model_path)
        if not model.is_file() or model.suffix.lower()!='.cloud':raise ValueError('Choose an existing .cloud model.')
        job=CloudJob(center,{'operation':'check_model','model':str(model)})
        center._cloud_model_check_job=job
        center.set_start_enabled(False)
    except Exception as error:
        center.model_inspection.setText('.cloud inspection could not start ('+type(error).__name__+').');return
    center.model_inspection.setText(center.t('Checking .cloud model…'))
    revision=center._probe_revision
    selected=center.settings.model_path
    def current():return revision==center._probe_revision and selected==center.settings.model_path and center._cloud_model_check_job is job
    def event(value):
        if not current():return
        if value['type']=='model_checked':
            info=value['model'];config=info['config']
            center.model_inspection.setText(center.tf('.cloud integrity checked · {architecture} · {size} px · {tensors} FP32 tensors · class0 / player. Runtime behavior is not validated by this check.',architecture=info['architecture'],size=config['input_size'],tensors=info['tensors']))
        elif value['type']=='error':center.model_inspection.setText(value['message'])
    job.received.connect(event)
    job.completed.connect(lambda ok,message:_completed(center,ok,message,current))


def _completed(center,success,message,current):
    if not success and current():center.model_inspection.setText(message)
    center.set_start_enabled(not center.engine.active)


def interpretation(center):
    cloud=Path(center.settings.model_path).suffix.lower()=='.cloud'
    from PySide6.QtWidgets import QWidget
    for widget in center.findChildren(QWidget):
        if widget.property('guideField') in ('resize_mode','model_output_format','model_coordinate_units','cuda_preprocess','cuda_graph_replay'):
            if cloud:
                if widget.property('cloudOriginalEnabled') is None:
                    widget.setProperty('cloudOriginalEnabled',widget.isEnabled());widget.setProperty('cloudOriginalTooltip',widget.toolTip())
                widget.setEnabled(False)
                widget.setToolTip(center.t('.cloud uses its model-declared letterbox, box decoding and mask geometry. These ONNX options are preserved for other models.'))
            elif widget.property('cloudOriginalEnabled') is not None:
                widget.setEnabled(bool(widget.property('cloudOriginalEnabled')))
                widget.setToolTip(widget.property('cloudOriginalTooltip'));widget.setProperty('cloudOriginalEnabled',None)
    for name,reason in (('model_validation_button','Use Inspect for .cloud integrity and Observe for live behavior. This ONNX validation tool does not validate .cloud models.'),
                        ('model_benchmark_button','For .cloud timing, select a provider and run Observe. Automatic provider comparison is ONNX-only in this alpha.')):
        button=getattr(center,name,None)
        if button is not None:
            if cloud:
                if button.property('cloudOriginalEnabled') is None:
                    button.setProperty('cloudOriginalEnabled',button.isEnabled())
                    button.setProperty('cloudOriginalTooltip',button.toolTip())
                button.setEnabled(False);button.setToolTip(center.t(reason))
            elif button.property('cloudOriginalEnabled') is not None:
                button.setEnabled(bool(button.property('cloudOriginalEnabled')) and not center.model_probe_busy())
                button.setToolTip(button.property('cloudOriginalTooltip'))
                button.setProperty('cloudOriginalEnabled',None)
    if cloud and hasattr(center,'model_inspection') and center.settings.provider=='trt':
        center.model_inspection.setText(center.t('.cloud supports CPU, DirectML and CUDA in this alpha. TensorRT is not supported.'))
