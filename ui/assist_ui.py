"""Owned model assistance in Review. Hover and draft selection never save."""
from contextlib import ExitStack
import hashlib,re,tempfile,uuid
from pathlib import Path
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox
from assist_geometry import validate,hit


class Assistance:
    def __init__(self,center,layout,canvas):
        self.c=center;self.canvas=canvas;self.items=[];self.selected=-1;self.process=None
        self.generation=None;self.pending=None;self.guard=None;self.temp=None;self.stamp=None;self.state='idle';self.drafting=False;self.closed=False
        panel=QWidget();panel.setObjectName('annotationAssist');column=QVBoxLayout(panel);column.setContentsMargins(0,0,0,0)
        self.details=QLabel();self.details.setTextFormat(Qt.TextFormat.PlainText);self.details.setWordWrap(True);column.addWidget(self.details)
        from mission_layout import ActionFlow
        row=ActionFlow();column.addLayout(row)
        self.load_button=QPushButton(center.t('Load suggestions'));self.load_button.clicked.connect(self.load);row.addWidget(self.load_button)
        self.stop_button=QPushButton(center.t('Cancel suggestions'));self.stop_button.clicked.connect(self.cancel);row.addWidget(self.stop_button)
        self.choose=QComboBox();self.choose.setAccessibleName(center.t('Model suggestion'));self.choose.currentIndexChanged.connect(self.select);row.addWidget(self.choose)
        self.use_button=QPushButton(center.t('Use suggestion'));self.use_button.clicked.connect(self.accept);row.addWidget(self.use_button)
        self.status=QLabel(center.t('Load suggestions, hover to preview, click to draft. Finish saves using the selected dataset class.'))
        self.status.setTextFormat(Qt.TextFormat.PlainText);self.status.setWordWrap(True);column.addWidget(self.status)
        self.selection=QLabel();self.selection.setTextFormat(Qt.TextFormat.PlainText);self.selection.setWordWrap(True);column.addWidget(self.selection)
        layout.addWidget(center.feature_control(panel));self.panel=panel;canvas.assistance=self;canvas.setMouseTracking(True)
        self.timer=QTimer(center);self.timer.setObjectName('annotationAssistTimer');self.timer.setInterval(100);self.timer.timeout.connect(self.sync);self.timer.start()

    def identity(self):
        c=self.c;s=c.settings
        return (id(getattr(c,'_review_document',None)),id(s),str(c._active_profile_path),c._tuning_epoch,
            0,s.dataset_directory,s.dataset_annotation_model or s.model_path,
            s.dataset_annotation_provider,s.dataset_annotation_confidence,s.dataset_annotation_nms_iou)

    def valid(self):
        c=self.c
        from batch4_guard import editable
        return (self.stamp==self.identity() and not c.engine.active and not c._policy_start_pending
            and getattr(c,'_dataset_worker',None) in (None,self.process)
            and getattr(c,'_training_worker',None) is None
            and editable(c) and True
            and getattr(c,'_review_document',None) is not None and not c._review_document.needs_refresh)

    def clear(self):
        self.items=[];self.selected=-1;self.pending=None
        self.choose.blockSignals(True);self.choose.clear();self.choose.blockSignals(False)
        if self.drafting:self.canvas.cancel()
        self.drafting=False;self.canvas.update()

    def release(self):
        if self.guard:self.guard.close();self.guard=None
        if self.temp:self.temp.cleanup();self.temp=None

    def cancel(self):
        self.generation=None;self.clear();self.state='cancelled'
        if self.process is not None:
            if getattr(self.c,'_dataset_worker',None) is self.process:self.c.stop_dataset_worker()
        else:self.release()
        self.status.setText(self.c.t('Suggestions cancelled. Saved labels are unchanged.'))

    def dispose(self):
        self.timer.stop();self.timer.timeout.disconnect(self.sync);self.timer.deleteLater();self.cancel();self.closed=True

    def sync(self):
        if self.stamp is not None and (self.items or self.process is not None or self.drafting) and not self.valid():self.cancel()
        c=self.c;s=c.settings
        model=Path(s.dataset_annotation_model or s.model_path).name
        self.details.setText(c.tf('Uses Annotation Suite: {model} · {backend} · confidence {confidence} · NMS {nms}.',
            model=model or c.t('No model selected'),backend=s.dataset_annotation_provider,
            confidence=s.dataset_annotation_confidence,nms=s.dataset_annotation_nms_iou))
        self.load_button.setEnabled(self.process is None and not self.canvas.has_draft())
        self.use_button.setEnabled(self.selected>=0 and not self.canvas.has_draft() and self.process is None)
        self.stop_button.setEnabled(self.process is not None or bool(self.items))
        editable=bool(self.canvas.draft_kind=='polygon' and self.canvas.can_edit())
        self.canvas.insert_button.setEnabled(editable and self.canvas.mode in ('edit','assist') and 3<=len(self.canvas.draft)<256)
        self.canvas.remove_button.setEnabled(editable and 0<=self.canvas.vertex<len(self.canvas.draft))
        self.selection_text()

    def selection_text(self):
        c=self.c
        if 0<=self.selected<len(self.items):
            item=self.items[self.selected]
            self.selection.setText(c.tf('{geometry} · model class {class_id} · {confidence}% → dataset class {destination}',
                geometry=c.t('Polygon' if item['kind']=='polygon' else 'Box'),class_id=item['class_id'],
                confidence=round(item['confidence']*100,1),destination=c.dataset_review_class.currentText()))
        else:self.selection.setText(c.t('No candidate here.') if self.state=='ready' else '')

    def load(self):
        from review_document import allowed
        from vapor_lock_gui import resolve_product_path
        from dataset_transaction import HeldFile
        from annotation_output import held_directory
        c=self.c;doc=getattr(c,'_review_document',None)
        if self.process is not None or self.canvas.has_draft() or not allowed(c) or doc is None:return
        self.clear();self.release();self.stamp=self.identity();self.generation=uuid.uuid4().hex;self.state='loading';self.error_code=None
        try:
            if len(doc.image_bytes)>32*1024**2 or self.canvas._image.width()*self.canvas._image.height()>16*1024**2:raise ValueError()
            model=resolve_product_path(c.settings.dataset_annotation_model or c.settings.model_path)
            if model.suffix.lower()!='.onnx' or not model.is_file():raise ValueError()
            self.guard=ExitStack();self.guard.enter_context(held_directory(model.parent));held=self.guard.enter_context(HeldFile(model,read_only=True))
            info=held.info()
            if ((info.size_high<<32)|info.size_low)>1024**3:raise ValueError()
            self.guard.enter_context(held_directory(doc.image.parent));image_guard=self.guard.enter_context(HeldFile(doc.image,read_only=True))
            info=image_guard.info();saved=doc.image_snapshot
            if (info.volume,(info.index_high<<32)|info.index_low,(info.size_high<<32)|info.size_low,
                    (info.written.dwHighDateTime<<32)|info.written.dwLowDateTime)!=(saved['volume'],saved['index'],saved['size'],saved['written']):raise ValueError()
            self.temp=tempfile.TemporaryDirectory(prefix='vaporlock-assist-');image=Path(self.temp.name)/'image.bin'
            image.write_bytes(doc.image_bytes);self.image_hash=hashlib.sha256(doc.image_bytes).hexdigest()
            s=c.settings
            args=['assist','--image',str(image),'--image-sha256',self.image_hash,'--request-id',self.generation,
                '--model',str(model),'--provider',s.dataset_annotation_provider,'--confidence',str(s.dataset_annotation_confidence),
                '--nms-iou',str(s.dataset_annotation_nms_iou)]
            if not c.start_dataset_worker(args):raise ValueError()
            self.process=c._dataset_worker
            if self.process is None:raise ValueError()
            self.status.setText(c.t('Loading suggestions… Hovering will not run inference again.'))
        except (OSError,ValueError):
            self.state='failed';self.release();self.generation=None
            self.status.setText(c.t('Suggestions unavailable. Check the image, model and Annotation Suite backend.'))
        self.sync()

    def receive(self,process,payload):
        if self.closed or process is not self.process or self.generation is None or not self.valid():return
        try:
            if payload.get('request_id')!=self.generation or payload.get('image_sha256')!=self.image_hash:raise ValueError()
            if not re.fullmatch('[0-9a-f]{64}',str(payload.get('model_sha256',''))):raise ValueError()
            if any(type(payload.get(k)) is not int or not 0<=payload[k]<=65536 for k in ('skipped','truncated')):raise ValueError()
            self.pending=(validate(payload['candidates']),payload['skipped'],payload['truncated'],payload['model_sha256'])
        except (ValueError,KeyError,TypeError):self.pending=None;self.generation=None

    def finished(self,process,code):
        if process is not self.process:return
        pending=self.pending;self.pending=None;self.process=None
        if self.closed:self.release();return
        if code!=0 or process.property('vaporlock_user_cancelled') or self.generation is None or pending is None or not self.valid():
            cancelled=self.state=='cancelled' or bool(process.property('vaporlock_user_cancelled'))
            self.clear();self.release();self.state='cancelled' if cancelled else ('unsupported' if getattr(self,'error_code',None)=='UNSUPPORTED_OR_INVALID' else 'failed')
            self.status.setText(self.c.t('Suggestions cancelled. Saved labels are unchanged.' if cancelled else
                ('Unsupported model/image geometry or changed input. Check the Engine Log.' if self.state=='unsupported' else
                 'Suggestions stopped or unavailable. Saved labels are unchanged.')));return
        self.items,skipped,truncated,self.model_hash=pending;self.state='ready'
        if self.temp:self.temp.cleanup();self.temp=None
        self.choose.blockSignals(True);self.choose.clear();self.choose.addItem(self.c.t('Hover over an object'),-1)
        for index,item in enumerate(self.items):
            self.choose.addItem(self.c.tf('Suggestion {number}: model class {class_id}, {confidence}%',
                number=index+1,class_id=item['class_id'],confidence=round(item['confidence']*100,1)),index)
        self.choose.blockSignals(False)
        self.status.setText(self.c.tf('{count} suggestions; {skipped} invalid/complex masks omitted; {truncated} beyond limits omitted. Model classes are advisory. Masks with holes or multiple islands are not offered.',count=len(self.items),skipped=skipped,truncated=truncated))
        self.sync()

    def select(self,index):
        if self.canvas.has_draft():return
        self.selected=self.choose.itemData(index) if index>=0 else -1
        if self.selected is None:self.selected=-1
        self.canvas.update();self.sync()

    def hover(self,point):
        if self.canvas.has_draft() or self.process is not None:return
        if not self.valid():self.cancel();return
        self.selected=hit(self.items,point.x(),point.y()) if point is not None else -1
        self.choose.blockSignals(True);self.choose.setCurrentIndex(self.selected+1);self.choose.blockSignals(False)
        self.selection_text();self.canvas.update()

    def accept(self):
        if not self.valid() or not self.canvas.can_edit() or self.canvas.has_draft() or not 0<=self.selected<len(self.items):return
        self.canvas.begin_suggestion(self.items[self.selected]);self.drafting=True;self.canvas.setFocus()


def install(center,layout,canvas):center.annotation_assist=Assistance(center,layout,canvas)
