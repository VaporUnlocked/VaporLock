"""Candidate parent controller for dedicated .cloud authoring and mask review."""
from copy import deepcopy
from pathlib import Path
import secrets
import json

from PySide6.QtCore import QObject,QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog,QInputDialog,QMessageBox

from cloud_contract import CloudInputError
from cloud_editor_store import EditorStore
from cloud_annotation_state import AnnotationSession
from dataset_paths import display_path,reject_reparse


class CloudWorkspace(QObject):
    def __init__(self,suite):
        super().__init__(suite.center)
        self.suite=suite;self.center=suite.center;self.session=None;self.job=None
        self.image=QImage();self.message='Open or create a .cloud project.'
        self.decodes={};self.import_queue=[];self.import_group=None;self.import_split='train'
        self.points={};self._prompt_undo={};self.after_stop=None;self.profile=None;self.loading=False
        self.import_uncertain=None
        from cloud_annotation_intents import PublicationLatch
        self.intents=PublicationLatch();self.import_generation=None;self.image_identity=None
        self.cancel_requested=False;self._manual_undo=None;self._targets_signature=None
        self._pending_session=None;self._close_pending=False
        self._pending_training=None;self.training_selection=None
        self.import_cursor=None;self.staging_warning=False
        self.status_details=''
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(200)

    def tick(self):
        self.render()
        # Keep the embedding cache while the editor is visible. Leaving it must
        # not leave an idle GPU worker blocking the program's normal Start.
        if (hasattr(self,'canvas') and not self.canvas.isVisible() and self.job and not self.job.done
                and not self.job.closing and not self.decodes and self.import_generation is None
                and not (self.session and self.session.pending)):
            self.job.stop()

    def mount(self,layout,train_mount):
        from cloud_workspace_form import mount
        mount(self,layout,train_mount)

    def apply_palette(self):
        if not hasattr(self,'canvas'):return
        from cloud_workspace_form import theme_palette
        from mission_icons import icon,apply as apply_icon
        palette,colors=theme_palette(self.center)
        for surface in self.surfaces:surface.setPalette(palette)
        self.canvas.setPalette(palette)
        if self.canvas.record:self.canvas.refresh_selected_mask();self.canvas.update()
        for menu in (self.actions_menu,self.project_menu):
            menu.setPalette(palette)
            for action in menu.actions():
                symbol=action.property('missionIcon')
                if symbol:action.setIcon(icon(symbol,colors['ice'],colors['baby'],colors['steel']))
        for index in range(self.tool_combo.count()):
            symbol={'select':'search','box':'target','foreground':'accept','background':'exclude','brush':'edit','erase':'exclude'}[self.tool_combo.itemData(index)]
            self.tool_combo.setItemIcon(index,icon(symbol,colors['ice'],colors['baby'],colors['steel']))
        self.tool_combo.queue_fit()
        for button in getattr(self.suite,'path_buttons',()):
            apply_icon(button,'folder',colors,getattr(getattr(self.center,'_live_owner',None),'_scale','1'))
        self.suite.refresh_mode()

    def reason(self):
        c=self.center
        from batch4_guard import editable
        if not editable(c):return 'Unlock tuning and finish the current workspace transition before editing.'
        if c.engine.active or getattr(c,'_policy_start_pending',False):return 'Stop the active session first.'
        if (getattr(c,'_model_probes',[]) or getattr(c,'_benchmark_active',False) or getattr(c,'_validation_active',False)
                or getattr(c,'_calibration_active',False) or getattr(getattr(c,'acceleration',None),'active',False)
                or getattr(getattr(c,'xbox_compatible_bridge',None),'preparing',False)
                or getattr(c,'_pending_controller_start',None) is not None
                or getattr(c.engine,'_controller_host_session',None) is not None):
            return 'Another background task is active.'
        from PySide6.QtCore import QProcess
        for name in ('_dataset_worker','_training_worker','_cloud_training_job','_cloud_model_check_job'):
            job=getattr(c,name,None)
            if job is not None and job.state()!=QProcess.ProcessState.NotRunning:return 'Another background task is active.'
        if self.session and self.profile!=str(c._active_profile_path):
            return 'The profile changed. Reopen this project before making further edits.'
        if self.session:
            from dataset_paths import lock_identity
            from vapor_lock_gui import resolve_product_path
            selected=resolve_product_path(c.settings.cloud_project)
            if selected.name=='project.json':selected=selected.parent
            if lock_identity(selected)!=lock_identity(self.session.store.root):
                return 'The selected project changed. Reopen it before editing.'
        return ''

    def admit(self,*,allow_profile_change=False,allow_reconcile=False):
        from cloud_access import edition
        from vapor_lock_gui import EDITION
        if edition()!=EDITION:raise CloudInputError('Conflicting .cloud build edition.')
        if self.import_uncertain and not allow_reconcile:
            raise CloudInputError('Reconcile the previous image import before making further edits.')
        if self.session and self.session.uncertain and not allow_reconcile:
            raise CloudInputError('Reconcile the previous save before continuing.')
        if self.job and not self.job.done and self.job.closing:
            raise CloudInputError('Waiting for annotation cancellation to finish.')
        if self.job and not self.job.done:
            peer=self.job.child.peer
            if self.job.rejected or self.job.child.failure or (peer is not None and (peer.rejected or peer.terminal=='error')):
                raise CloudInputError('Annotation worker rejected its session. Cancel it before retrying.')
        reason=self.reason()
        if reason and not (allow_profile_change and reason.startswith('The profile changed.')):
            raise CloudInputError(reason)
        return True

    def show_error(self,message):
        if str(message)=='Legacy JSON projects need an explicit imported copy; the original was not changed.':
            message='Use Select training project on the Project tab to train this existing project. Annotation editing requires a new annotation project; the original was not changed.'
        self.message=str(message);self.render()

    def pending_owner(self):return self._pending_session,self._pending_training

    def discard_pending(self,owner):
        session,training=owner
        if session is not None and self._pending_session is session:
            session.close(discard=True);self._pending_session=None
        if self._pending_training is training:self._pending_training=None

    def selection_changed(self):
        self.suite.checked=None;self.suite.recipe=None;self.suite.outputs=None;self.suite.percent=None
        self.suite.message='Project selection changed. Check the selected project before training.'
        if hasattr(self.suite,'recipe_label'):self.suite.refresh_mode()

    def show_details(self):
        dialog=QMessageBox(self.center)
        dialog.setWindowTitle(self.center.t('Annotation details'))
        from PySide6.QtCore import Qt
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(self.status_details or self.center.t(self.message))
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok);dialog.exec()

    def action(self,callback):
        try:self.admit();callback()
        except Exception as exc:self.show_error(str(exc))

    def leave_draft(self):
        if self.import_uncertain:
            self.show_error('Reconcile the previous image import before leaving this project.');return False
        if self.session is None:return True
        if self.session.uncertain:
            self.show_error('Reconcile the previous save before leaving this project.');return False
        if not self.session.dirty:return True
        result=QMessageBox.question(self.center,self.center.t('.cloud project'),
            self.center.t('Save this image as an unreviewed draft before leaving? Approve image & next is the action that marks it reviewed.'),
            QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if result==QMessageBox.StandardButton.Cancel:return False
        if result==QMessageBox.StandardButton.Save:
            try:self.admit();self.session.save_draft()
            except Exception as exc:self.show_error(str(exc));return False
        return True

    def _open(self,path,*,check_draft=True):
        self.admit(allow_profile_change=True)
        if check_draft and not self.leave_draft():return
        store=EditorStore(path)
        try:replacement=AnnotationSession(store)
        except BaseException:store.close();raise
        self.cancel()
        if self.job and not self.job.done:
            self._pending_session=replacement
            self.after_stop=(lambda:self._activate_project(replacement),self.continuation_stamp())
            return
        self._activate_project(replacement)

    def _activate_project(self,replacement):
        if self.session:self.session.close(discard=True)
        self._pending_session=None;self.session=replacement;self.profile=str(self.center._active_profile_path)
        self.training_selection=None;self._pending_training=None;self.selection_changed()
        self.center.change('cloud_project',display_path(replacement.store.root))
        self.points={};self.image=QImage();self.message='Project opened.'
        self.image_identity=None;self.loading=False;self.refresh_document();self.load_image()

    def open_project(self):
        try:
            self.admit(allow_profile_change=True)
            path=QFileDialog.getExistingDirectory(self.center,self.center.t('Open .cloud project'),self.center.settings.cloud_project)
            if path:self._open(path)
        except Exception as exc:self.show_error(str(exc))

    def open_profile_project(self):
        try:self._open(self.center.settings.cloud_project)
        except Exception as exc:self.show_error(str(exc))

    def create_project(self):
        try:
            self.admit(allow_profile_change=True)
            parent=QFileDialog.getExistingDirectory(self.center,self.center.t('New project parent folder'))
            if not parent:return
            name,ok=QInputDialog.getText(self.center,self.center.t('New project'),self.center.t('Project name'))
            if not ok or not name.strip() or not self.leave_draft():return
            with EditorStore.create(parent,name.strip()) as created:path=display_path(created.root)
            self._open(path,check_draft=False)
        except Exception as exc:self.show_error(str(exc))

    def select_training_project(self):
        try:
            self.admit(allow_profile_change=True)
            stamp=self.continuation_stamp()
            path=QFileDialog.getExistingDirectory(self.center,self.center.t('Select training project'),self.center.settings.cloud_project)
            if not path:return
            if stamp!=self.continuation_stamp():
                raise CloudInputError('The workspace changed while selecting a folder. Choose the action again.')
            self.select_training_path(path)
        except Exception as exc:self.show_error(str(exc))

    def select_training_path(self,path):
        self.admit(allow_profile_change=True)
        if self.canvas.drawing:raise CloudInputError('Finish or cancel the current stroke first.')
        selected=reject_reparse(path)
        if not selected.is_dir():raise CloudInputError('Choose an existing project folder.')
        if not self.leave_draft():return False
        self.cancel()
        selection=dict(path=display_path(selected))
        self._pending_training=selection
        if self.job and not self.job.done:
            self.after_stop=(lambda:self.activate_training_selection(selection),self.continuation_stamp())
            self.message='Closing annotation before selecting the training project…';self.render();return False
        self.activate_training_selection(selection);return True

    def activate_training_selection(self,selection):
        self.admit(allow_profile_change=True)
        if self._pending_training is not selection:
            raise CloudInputError('The workspace changed while stopping. Choose the action again.')
        selected=reject_reparse(selection['path'])
        if not selected.is_dir():raise CloudInputError('Choose an existing project folder.')
        from cloud_settings import validate
        value=display_path(selected);validate({**vars(self.center.settings),'cloud_project':value})
        self._release_editor();self._pending_training=None;self.training_selection=value
        self.center.change('cloud_project',value);self.selection_changed()
        self.message='Training project selected. It has not been checked. Open or create an annotation project to edit images.'
        self.stack.setCurrentIndex(2);self.refresh_document();self.suite.render()

    def ensure_job(self):
        self.admit()
        if self.job and not self.job.done:
            if self.job.closing:raise CloudInputError('Wait for annotation cancellation to finish.')
            return self.job
        from cloud_annotation_job import AnnotationJob
        self.job=AnnotationJob(self.center,self.sam_device.currentData())
        self.cancel_requested=False
        self.center._cloud_annotation_job=self.job
        self.job.received.connect(self.worker_event);self.job.completed.connect(self.worker_done)
        self.center.set_start_enabled(False)
        return self.job

    def decode(self,path,purpose):
        if len(self.decodes)>=1:raise CloudInputError('Wait for the current image to finish loading.')
        from cloud_annotation_artifact import ImageCapability
        from vapor_lock_gui import INITIALIZATION_DIRECTORY
        parent=reject_reparse(INITIALIZATION_DIRECTORY/'cloud-annotation-staging')
        parent.mkdir(exist_ok=True)
        cap=ImageCapability(parent)
        self.decodes[cap.request]=(cap,purpose,self.intents.capture())
        try:self.ensure_job().submit(cap.command(path))
        except BaseException:
            self.decodes.pop(cap.request);cap.close();raise

    def load_image(self):
        self.image=QImage();self.image_identity=None
        if hasattr(self,'canvas'):self.canvas.clear()
        if not self.session or not self.session.draft:self.loading=False;return
        if self.decodes:self.loading=True;return
        try:
            self.admit();record=self.session.draft
            from cloud_contract import MAX_IMAGE_BYTES
            self.session.store.guard.hold_file(self.session.store.root/record['file'],MAX_IMAGE_BYTES)
            stamp=(self.session.session,record['id'],self.session.revision)
            self.loading=True;self.message='Loading annotation image…'
            self.decode(self.session.store.root/record['file'],('preview',stamp))
        except Exception as exc:self.loading=False;self.show_error(str(exc))

    def retry_image(self):
        if not self.decodes:self.load_image()

    def image_matches(self):
        r=self.session.draft if self.session else None
        return bool(r and not self.image.isNull() and self.image_identity==
            (self.session.session,r['id'],r.get('pixel_sha256'),r['width'],r['height']))

    def import_images(self):
        try:
            self.admit()
            if not self.session:raise CloudInputError('Open or create a .cloud project first.')
            if self.import_queue or self.decodes:raise CloudInputError('Wait for the current image operation to finish.')
            if not self.leave_draft():return
            paths,_=QFileDialog.getOpenFileNames(self.center,self.center.t('Import images'),'',
                'Images (*.png *.jpg *.jpeg *.bmp *.webp)')
            if not paths:return
            if len(paths)>500:raise CloudInputError('Choose at most 500 images in one import batch.')
            self.import_queue=[str(reject_reparse(p)) for p in paths]
            self.import_cursor=self.session.draft['id'] if self.session.draft else None
            self.import_generation=self.intents.capture()
            self.import_group=secrets.token_hex(16);self.import_split=self.split.currentData()
            self.next_import()
        except Exception as exc:self.show_error(str(exc))

    def next_import(self):
        try:self._next_import()
        except Exception as exc:self.import_queue=[];self.import_generation=None;self.show_error(str(exc))

    def _next_import(self):
        if self.import_generation is None or not self.intents.accepts(self.import_generation):return
        if not self.import_queue:
            self.import_generation=None
            self.message='Image import completed. New images are unreviewed.'
            self.refresh_document();self.load_image();return
        self.admit();path=self.import_queue.pop(0)
        self.message='Importing image…';self.decode(path,('import',self.session.session))

    def worker_event(self,value):
        try:
            kind=value['type']
            if kind in ('decoded','decode_failed'):
                entry=self.decodes.pop(value['request'],None)
                if entry is None:return
                cap,purpose,generation=entry
                if not self.intents.accepts(generation):
                    self.decodes[value['request']]=entry;return  # retained until exact child cleanup
                try:
                    if kind=='decode_failed':raise CloudInputError(value['message'])
                    artifacts=cap.read(value)
                    if purpose[0]=='import':
                        if not self.session or self.session.session!=purpose[1]:return
                        self.admit()
                        from cloud_editor_import import append_image
                        result=append_image(self.session.store,value,artifacts,revision=self.session.revision,
                            split=self.import_split,group=self.import_group)
                        self.suite.checked=None
                        self.session=AnnotationSession(self.session.store)
                        # Keep the active project session identity for this explicit
                        # serial import queue; no SAM requests run while importing.
                        self.session.session=purpose[1]
                        cursor=next((i for i,r in enumerate(self.session.manifest['images']) if r['id']==self.import_cursor),0)
                        if cursor:self.session.navigate(cursor,discard=True)
                        self.refresh_document();QTimer.singleShot(0,self.next_import)
                    else:
                        expected=(self.session.session,self.session.draft['id'],self.session.revision) if self.session and self.session.draft else None
                        if expected!=purpose[1]:
                            self.load_image();return
                        record=self.session.draft
                        if ((value['width'],value['height'])!=(record['width'],record['height'])
                                or value['pixel_sha256']!=record.get('pixel_sha256')):
                            raise CloudInputError('Image pixels disagree with the saved project. Import a separate verified copy before annotation.')
                        self.image=QImage(artifacts['rgb'],value['width'],value['height'],value['width']*3,QImage.Format.Format_RGB888).copy()
                        self.image_identity=(self.session.session,record['id'],record.get('pixel_sha256'),record['width'],record['height'])
                        self.loading=False;self.refresh_document();self.message='Draw a box to generate its mask, or select a target to correct it.'
                finally:
                    if cap.verified:
                        try:cap.cleanup()
                        except Exception:self.staging_warning=True;cap.close()
                    else:cap.close()
            elif kind in ('mask_proposed','mask_failed'):
                from cloud_editor_contract import ProposalIdentity
                if self.session:
                    self.admit()
                    proposal=ProposalIdentity.from_wire(value['identity'])
                    accepted=self.session.result(proposal,value.get('mask'),value.get('message',''))
                    if accepted:
                        self.message=('Mask ready. Press Enter to approve this image, or edit the mask.'
                            if not self.session.approval_reason() else 'Mask updated. Review the remaining targets.') if kind=='mask_proposed' else value['message']
                        self.refresh_document(reset=False)
            elif kind=='error':self.show_error(value['message'])
        except Exception as exc:
            from cloud_editor_import import ImageImportUncertain
            if isinstance(exc,ImageImportUncertain):self.import_uncertain=exc
            self.import_queue=[];self.import_generation=None;self.loading=False;self.show_error(str(exc))
        self.render()

    def worker_done(self,ok,message):
        for cap,_,_ in self.decodes.values():cap.close()
        self.decodes={};self.import_queue=[];self.import_generation=None;self.loading=False
        if self.session:self.session.cancel()
        self.center.set_start_enabled(not self.center.engine.active)
        if message and not self.job.closing:self.message=message
        if self.cancel_requested:self.message='Annotation cancelled. No image was approved.'
        callback=self.after_stop;self.after_stop=None
        self.render()
        if callback:
            function,stamp=callback
            owner=self.pending_owner()
            if ok and stamp==self.continuation_stamp():
                work=self.continuation_work_stamp()
                QTimer.singleShot(0,lambda:self.run_continuation(function,stamp,owner,work))
            else:
                self.discard_pending(owner)
                self.message='The workspace changed while stopping. Choose the action again.';self.render()

    def run_continuation(self,function,stamp,owner,work):
        try:
            if (self._close_pending or stamp!=self.continuation_stamp()
                    or work!=self.continuation_work_stamp()):
                raise CloudInputError('The workspace changed while stopping. Choose the action again.')
            self.admit(allow_profile_change=self._pending_session is not None or self._pending_training is not None)
            function()
        except Exception as exc:
            self.discard_pending(owner)
            self.show_error(str(exc))

    def remember_prompts(self,previous):
        if not self.session or not self.session.undo_stack:return
        snapshot=self.session.undo_stack[-1]
        self._prompt_undo[id(snapshot)]=(snapshot,deepcopy(previous))
        live={id(value) for value in self.session.undo_stack}
        self._prompt_undo={key:value for key,value in self._prompt_undo.items() if key in live}

    def request_mask(self,identity,*,record_undo=True,previous_points=None):
        self.admit()
        if self.loading or self.import_queue or self.decodes:raise CloudInputError('Wait for the image operation to finish.')
        from cloud_annotation_protocol import HQ_SAM_SHA256
        proposal=self.session.request(identity,HQ_SAM_SHA256,record_undo=record_undo)
        if record_undo:self.remember_prompts(self.points if previous_points is None else previous_points)
        points=self.points.get(identity,[])
        try:self.ensure_job().submit(dict(command='mask',identity=proposal.wire(),
            path=str(self.session.store.root/self.session.draft['file']),
            points=[p for p,l in points],labels=[l for p,l in points]))
        except Exception as exc:self.session.result(proposal,error=str(exc));raise
        self.message='Generating mask…';self.refresh_document(reset=False)

    def add_box(self,box):
        def apply():
            previous=deepcopy(self.points)
            identity=self.session.add_box(box);self.remember_prompts(previous);self.canvas.selection=identity
            self.points[identity]=[];self.request_mask(identity,record_undo=False)
        self.action(apply)

    def edit_started(self,identity):
        try:
            self.admit();self.session._changed(identity);self.remember_prompts(self.points)
            self._manual_undo=self.session.undo_stack[-1]
            self.session.failures[identity]='Finish the manual mask stroke or undo it before approving.'
        except Exception as exc:self.canvas.abort_gesture();self.show_error(str(exc))

    def mask_edited(self,identity,mask):
        # edit_started already captured this stroke's one undo boundary.
        try:self.admit();self.session.set_mask(identity,mask,record_undo=False);self._manual_undo=None
        except Exception as exc:self.manual_aborted(identity);self.show_error(str(exc))
        self.refresh_document(reset=False)

    def manual_aborted(self,identity):
        if (self.session and self._manual_undo is not None and self.session.undo_stack
                and self.session.undo_stack[-1] is self._manual_undo):
            self.session.undo();self.message='Manual stroke cancelled. The previous mask was retained.'
        self._manual_undo=None
        self.refresh_document(reset=False)

    def refine(self,identity,point,label):
        def apply():
            previous=deepcopy(self.points)
            prompts=self.points.setdefault(identity,[])
            if len(prompts)>=256:raise CloudInputError('At most 256 refinement points are supported.')
            prompts.append((point,label));self.request_mask(identity,previous_points=previous)
        self.action(apply)

    def regenerate(self):
        if self.canvas.selection:self.action(lambda:self.request_mask(self.canvas.selection))

    def set_tool(self,mode):
        if mode!='select' and not self.canvas.mutable:
            self.tool_combo.blockSignals(True)
            self.tool_combo.setCurrentIndex(self.tool_combo.findData(self.canvas.mode))
            self.tool_combo.blockSignals(False);self.tool_combo.queue_fit();return
        self.canvas.mode=mode
        self.tool_combo.blockSignals(True);self.tool_combo.setCurrentIndex(self.tool_combo.findData(mode));self.tool_combo.blockSignals(False)
        self.tool_combo.queue_fit()
        self.brush_size.setVisible(mode in ('brush','erase'))
    def set_radius(self,value):self.canvas.radius=value;self.canvas.update()
    def device_changed(self):
        self.center.change('cloud_annotation_device',self.sam_device.currentData())
        if self.job and not self.job.done:self.cancel()
    def canvas_selected(self,identity):self.refresh_target_choice(identity)

    def choose_target(self,index):
        identity=self.target_choice.itemData(index)
        if identity:self.canvas.select_target(identity)

    def refresh_target_choice(self,selected=None):
        if not hasattr(self,'target_choice'):return
        self.target_choice.blockSignals(True);self.target_choice.clear()
        if self.session and self.session.draft:
            for i,a in enumerate(self.session.draft['annotations']):
                self.target_choice.addItem(self.center.tf('Target {number} · {state}',number=i+1,state=self.center.t(self.target_state(a))),a['id'])
        self.target_choice.setCurrentIndex(max(0,self.target_choice.findData(selected or self.canvas.selection)))
        self.target_choice.blockSignals(False)

    def refresh_document(self,reset=True):
        if not hasattr(self,'canvas'):return
        session=self.session;record=session.draft if session else None
        self.image_choice.blockSignals(True);self.image_choice.clear()
        if session:
            for i,r in enumerate(session.manifest['images']):
                state='✓' if r['reviewed'] else '○'
                self.image_choice.addItem(f'{i+1}. {state} '+Path(r['file']).name,i)
            self.image_choice.setCurrentIndex(session.cursor)
        self.image_choice.blockSignals(False)
        if self.image_matches() and not self.canvas.drawing and not self.loading:
            self.canvas.set_document(self.image,record,reset_view=reset)
            self.canvas.prompts=deepcopy(self.points)
        elif not self.canvas.drawing:self.canvas.clear()
        self.refresh_target_choice()
        self.render()

    def choose_image(self,index):
        try:
            self.admit()
            if not self.session or not self.leave_draft():self.refresh_document(reset=False);return
            self.session.navigate(index,discard=True);self.points={};self.image=QImage();self.canvas.clear();self.load_image()
        except Exception as exc:self.show_error(str(exc))

    def previous(self):
        if self.session:self.choose_image(max(0,self.session.cursor-1))
    def skip(self):
        if self.session:self.choose_image(min(len(self.session.manifest['images'])-1,self.session.cursor+1))

    def approve(self):
        try:
            self.admit(allow_reconcile=True)
            if self.import_uncertain:
                from cloud_editor_import import reconcile_import
                committed=reconcile_import(self.session.store,self.import_uncertain)
                self.session=AnnotationSession(self.session.store);self.import_uncertain=None
                self.image=QImage();self.points={}
                self.message='Image import confirmed.' if committed else 'The import did not commit. Its unreferenced image was retained; import again when ready.'
                self.refresh_document();self.load_image();return
            if self.canvas.drawing or self.loading or self.import_queue or self.decodes:
                raise CloudInputError('Finish loading or editing the current image before approving.')
            if not self.image_matches():raise CloudInputError('Load and verify this image before approving it.')
            if not self.session:raise CloudInputError('Open a project first.')
            recovering=self.session.uncertain is not None
            recovering_review=bool(self.session.uncertain and self.session.uncertain[3])
            if recovering:advanced=self.session.reconcile()
            else:advanced=self.session.approve()
            self.suite.checked=None
            if advanced:
                self.points={};self.image=QImage();self.canvas.clear();self.load_image()
            else:
                self.message=('The previous save did not commit. Your draft is retained; approve again to save it.'
                    if recovering and self.session.dirty else 'Draft save confirmed. This image remains unreviewed.'
                    if recovering and not recovering_review else 'Image saved. The annotation queue is complete.')
                self.refresh_document(reset=False)
        except Exception as exc:self.show_error(str(exc))

    def save_draft(self):
        self.action(lambda:self.session.save_draft());self.suite.checked=None;self.refresh_document(reset=False)
    def undo(self):
        def apply():
            snapshot=self.session.undo_stack[-1] if self.session.undo_stack else None
            if snapshot is None:return
            touched=snapshot[4];history=self._prompt_undo.pop(id(snapshot),None)
            self.session.undo()
            if history is not None and history[0] is snapshot:
                previous=history[1]
                if touched is None:self.points=previous
                elif touched in previous:self.points[touched]=previous[touched]
                else:self.points.pop(touched,None)
            self.message='Last annotation edit undone.'
        self.action(apply);self.refresh_document(reset=False)
    def delete_target(self):
        def apply():
            previous=deepcopy(self.points)
            identity=self.canvas.selection;self.session.remove(identity);self.remember_prompts(previous);self.points.pop(identity,None)
            self.message='Target removed. An empty image is not approved as a negative automatically.'
        if self.canvas.selection:self.action(apply);self.refresh_document(reset=False)
    def mark_negative(self):
        def apply():
            self.session.negative();self.remember_prompts(self.points);self.message='No targets confirmed. Press Enter to approve this image.'
        self.action(apply);self.refresh_document(reset=False)

    def cancel(self):
        self.intents.revoke();self.after_stop=None;self.import_generation=None
        self.discard_pending(self.pending_owner())
        if self.session:self.session.cancel()
        self.import_queue=[]
        waiting=bool(self.job and not self.job.done)
        self.cancel_requested=waiting
        if waiting:self.job.stop()
        if hasattr(self,'canvas'):self.canvas.abort_gesture()
        self.message='Cancelling annotation; waiting for the worker to stop…' if waiting else 'Annotation cancelled. No image was approved.'
        self.render()

    def target_state(self,value):
        identity=value['id']
        if self.session and identity in self.session.pending:return 'Generating mask'
        if self.session and identity in self.session.failures:return 'Needs correction'
        return 'Mask ready' if value.get('mask') or value.get('wrapper') else 'Needs mask'

    def handoff_reason(self):
        if self.import_uncertain:return 'Reconcile the previous image import before continuing.'
        if self.session and self.session.uncertain:return 'Reconcile the previous save before continuing.'
        if self.import_queue or self.decodes or self.loading or (self.session and self.session.pending):
            return 'Finish or cancel annotation work before training.'
        if hasattr(self,'canvas') and self.canvas.drawing:return 'Finish or cancel the current stroke first.'
        if self.job and not self.job.done and self.job.closing:return 'Waiting for annotation cancellation to finish.'
        return ''

    def continuation_stamp(self):
        from dataclasses import asdict
        c=self.center
        return (id(c.settings),str(c._active_profile_path),0,
            json.dumps({k:v for k,v in asdict(c.settings).items() if k.startswith('cloud_')},sort_keys=True),
            self.intents.capture(),self.session.session if self.session else None)

    def continuation_work_stamp(self):
        # Capture AFTER the acknowledged worker's cleanup/cancellation. New
        # edits, native gestures or a new worker in the queued-event gap must
        # preserve their owner rather than be discarded by an older handoff.
        s=self.session;j=self.job
        document=(s.session,s.generation,s.revision,s.saving,id(s.uncertain),s.closed,
            tuple(sorted(s.pending)),tuple(sorted(s.failures.items()))) if s else None
        worker=(id(j),j.done,j.closing) if j else None
        return (document,worker,self.canvas.drawing,tuple(self.decodes),
            self.import_generation,id(self.import_uncertain))

    def prepare_training(self,callback):
        reason=self.handoff_reason()
        if reason:self.show_error(reason);return False
        if self.session:
            if not self.leave_draft():return False
        if self.job and not self.job.done:
            self.intents.revoke()
            self.after_stop=(lambda:self._finish_training(callback),self.continuation_stamp())
            self.job.stop();self.message='Closing annotation before training…';self.render();return False
        self._release_editor()
        return True

    def _finish_training(self,callback):
        self._release_editor();callback()

    def _release_editor(self):
        if self.session:self.session.close(discard=True);self.session=None
        self.image=QImage();self.image_identity=None;self.points={};self.refresh_document()

    def allow_close(self):
        if self._close_pending:return True
        if hasattr(self,'canvas') and self.canvas.drawing:
            self.show_error('Finish or cancel the current stroke first.');return False
        if self.import_uncertain or (self.session and self.session.uncertain):
            self.show_error('Reconcile the pending save or import before closing.');return False
        if not self.leave_draft():return False
        self._close_pending=True;self.cancel()
        return True

    def close_complete(self):
        if self.job and not self.job.done:return False
        if self.decodes:return False
        if self._close_pending:self._release_editor();self.timer.stop()
        return True

    def check_project(self):self.suite.preflight()

    def render(self):
        if not hasattr(self,'annotation_status'):return
        try:
            reason=self.reason();closing=bool(self.job and not self.job.done and self.job.closing)
            blocked=bool(reason or self.loading or self.import_queue or self.decodes or self.import_uncertain or closing)
            mutation_blocked=blocked or bool(self.session and self.session.uncertain)
            self.canvas.mutable=not mutation_blocked and self.image_matches()
            for control in self.controls:control.setEnabled(not mutation_blocked)
            for control in self.image_controls:control.setEnabled(not mutation_blocked and self.image_matches())
            for index in range(self.tool_combo.count()):
                self.tool_combo.model().item(index).setEnabled(self.tool_combo.itemData(index)=='select' or not mutation_blocked and self.image_matches())
            if reason and self.session and self.session.pending:self.cancel()
            self.approve_button.setEnabled((bool(self.import_uncertain) and not reason and not closing) or
                (not blocked and self.image_matches() and self.session is not None and not self.canvas.drawing
                and (self.session.uncertain is not None or not self.session.approval_reason())))
            label='Reconcile image import' if self.import_uncertain else 'Reconcile save' if self.session and self.session.uncertain else 'Approve image & next'
            self.approve_button.setText(self.center.t(label).replace(' & ',' && '))
            self.cancel_button.setEnabled(bool(self.job and not self.job.done) or self.canvas.drawing)
            blockers=[]
            if reason:blockers.append(self.center.t(reason))
            if closing:blockers.append(self.center.t('Waiting for annotation cancellation to finish.'))
            if self.staging_warning:blockers.append(self.center.t('Some temporary image files were retained for inspection.'))
            if self.session and self.session.draft:
                approval=self.session.approval_reason()
                if approval:blockers.append(self.center.t(approval))
                selected=next(((i,a) for i,a in enumerate(self.session.draft['annotations']) if a['id']==self.canvas.selection),None)
                if selected:
                    i,target=selected;error=self.session.failures.get(target['id'])
                    if error:blockers.append(self.center.tf('Target {number}: {reason}',number=i+1,reason=self.center.t(error)))
            self.status_details='\n'.join(dict.fromkeys([self.center.t(self.message),*blockers]))
            self.annotation_status.setText(next(iter(blockers),self.center.t(self.message)))
            self.annotation_status.setToolTip(self.status_details)
            rows=self.session.manifest['images'] if self.session else []
            self.image_position.setText(self.center.tf('Image {current} / {total}',current=self.session.cursor+1 if rows else 0,total=len(rows)))
            target=self.canvas.target()
            labels=self.session.draft['annotations'] if self.session and self.session.draft else []
            pending=sum(a['id'] in self.session.pending for a in labels) if self.session else 0
            failed=sum(a['id'] in self.session.failures for a in labels) if self.session else 0
            ready=sum(self.target_state(a)=='Mask ready' for a in labels)
            summary=self.center.tf('{ready} ready · {pending} pending · {failed} need correction',ready=ready,pending=pending,failed=failed) if labels else ''
            device=self.sam_device.currentText()
            self.target_status_label.setText(device+(' · '+summary if summary else ''))
            signature=(self.center.ui_language,tuple((a['id'],self.target_state(a)) for a in (self.session.draft['annotations'] if self.session and self.session.draft else [])))
            if signature!=self._targets_signature:self._targets_signature=signature;self.refresh_target_choice()
            self.project_path.setText(display_path(self.session.store.root) if self.session else self.center.settings.cloud_project)
            if self.session:
                rows=self.session.manifest['images'];reviewed=sum(r['reviewed'] for r in rows)
                self.project_summary.setText(self.center.tf('{reviewed} reviewed / {total} images',reviewed=reviewed,total=len(rows)))
            else:
                selected=bool(self.training_selection and self.center.settings.cloud_project==self.training_selection)
                self.project_summary.setText(self.center.t('Training-only project selected. Open an annotation project to edit images.' if selected else 'Open or create a .cloud project.'))
            output=self.suite.outputs or {}
            lines=[]
            for key,label in (('output','Run folder'),('best_cloud','Best model'),('last_cloud','Latest model'),('checkpoint','Resume checkpoint')):
                if output.get(key):lines.append(self.center.t(label)+': '+str(output[key]))
            self.results.setText(self.center.t(self.suite.message));self.results_paths.setText('\n'.join(lines));self.results_paths.setVisible(bool(lines))
        except RuntimeError:pass  # Theme/locale rebuild replaces the widgets.
