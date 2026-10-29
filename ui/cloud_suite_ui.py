"""Dedicated local .cloud suite using the approved native task-card layout."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import secrets

from PySide6.QtCore import QObject,QTimer
from PySide6.QtWidgets import (QWidget,QHBoxLayout,QVBoxLayout,QLabel,QPushButton,QFileDialog,
                              QComboBox,QMessageBox,QProgressBar)


class CloudSuite(QObject):
    def __init__(self,center):
        super().__init__(center);self.center=center;self.job=None;self.message='Choose a reviewed .cloud project to begin.'
        self.outputs=None;self.recipe=None;self.percent=None;self.widgets={};self.checked=None
        self.timer=QTimer(self);self.timer.setInterval(400);self.timer.timeout.connect(self.render);self.timer.start()

    def fingerprint(self):
        from dataclasses import asdict
        import json
        return json.dumps({k:v for k,v in asdict(self.center.settings).items() if k.startswith('cloud_')},sort_keys=True)+str(self.center._active_profile_path)

    def mount(self,layout):
        previous=getattr(self,'recipe_dialog',None)
        if previous is not None:previous.close();previous.deleteLater()
        from cloud_suite_form import mount
        from cloud_workspace_ui import CloudWorkspace
        if not hasattr(self,'workspace'):self.workspace=CloudWorkspace(self)
        self.workspace.mount(layout,lambda train_layout:mount(self,train_layout))

    def refresh_mode(self):
        mode=self.center.settings.cloud_mode
        from cloud_suite_form import fit_path_edit
        for edit in getattr(self,'path_edits',()):fit_path_edit(edit)
        if hasattr(self,'parameter_spins'):
            batch=self.parameter_spins['cloud_batch'];automatic=self.center.t('Automatic')
            if batch.specialValueText()!=automatic:
                batch.setSpecialValueText(automatic)
                from control_fit import fit_numeric
                fit_numeric(batch)
        if hasattr(self,'train'):self.train.setText(self.center.t('Resume .cloudtrain' if mode=='resume' else 'Train .cloud'))
        self.fresh_panel.setVisible(mode!='resume')
        self.widgets['cloud_initial_model'].setVisible(mode=='initialize')
        self.widgets['cloud_checkpoint'].setVisible(mode=='resume')
        guidance=self.center.t('Resume inherits the verified checkpoint recipe. Use Check project / checkpoint to inspect it.') if mode=='resume' else self.center.t('Compatible S2 weights initialize a new run with a fresh optimizer.') if mode=='initialize' else self.center.t('From scratch uses fresh S2 weights. No YOLO model or pretrained foundation is required.')
        self.mode_combo.setToolTip(guidance)
        current=bool(self.recipe and self.checked==self.fingerprint())
        self.recipe_label.setPlainText(self.recipe_text() if current else '')
        self.recipe_button.setVisible(current)
        self.recipe_button.setText(self.center.t('Recipe'))
        self.recipe_button.setAccessibleName(self.center.t('Verified recipe'))
        self.recipe_button.setToolTip(self.center.t('Verified recipe'))
        self.recipe_dialog.setWindowTitle(self.center.t('Verified recipe'))
        self.recipe_label.setAccessibleName(self.center.t('Verified recipe'))
        self.recipe_close.setText(self.center.t('Close'))
        if not current:self.recipe_dialog.close()

    def invalidate_recipe_if_changed(self):
        if self.recipe is not None and self.checked!=self.fingerprint():
            self.recipe=None
            if hasattr(self,'recipe_button'):self.refresh_mode()

    def show_recipe(self):
        self.invalidate_recipe_if_changed()
        if not self.recipe or self.checked!=self.fingerprint():return
        self.refresh_mode()
        dialog=self.recipe_dialog
        screen=self.center.screen();area=screen.availableGeometry() if screen else self.center.geometry()
        if not dialog.isVisible():
            dialog.resize(min(580,max(1,area.width()-24)),min(320,max(1,area.height()-24)))
            location=self.center.geometry().center()-dialog.rect().center()
            dialog.move(max(area.left(),min(location.x(),area.right()-dialog.width()+1)),
                        max(area.top(),min(location.y(),area.bottom()-dialog.height()+1)))
        dialog.show();dialog.activateWindow();self.recipe_label.setFocus()

    def recipe_text(self):
        labels={'epochs':'Epochs','batch_size':'Images per batch','device':'Training device','patience':'Early stopping patience',
            'input_size':'Input size','channels':'Channels','mask_size':'Mask size','architecture':'Architecture','completed_epochs':'Completed epochs'}
        values=[self.center.t(labels[k])+': '+str(v) for k,v in (self.recipe or {}).items() if k in labels]
        return '\n'.join(' · '.join(values[index:index+2]) for index in range(0,len(values),2))

    def reason(self):
        c=self.center
        if getattr(c,'_tuning_locked',False):return c.t('Unlock tuning to change settings.')
        if c.engine.active or getattr(c,'_policy_start_pending',False):return c.t('Stop the active session first.')
        if getattr(c,'_native_closing',False) or False or getattr(c,'_profile_transition_pending',False):return c.t('Workspace is busy or access needs verification.')
        from PySide6.QtCore import QProcess
        for name in ('_dataset_worker','_training_worker','_cloud_training_job','_cloud_model_check_job'):
            job=getattr(c,name,None)
            if job is not None and job.state()!=QProcess.ProcessState.NotRunning:return c.t('Another background task is active.')
        if hasattr(self,'workspace'):
            reason=self.workspace.handoff_reason()
            if reason:return c.t(reason)
        return ''

    def admission(self):
        from api_jobs import cloud_admission
        from cloud_settings import validate
        if not cloud_admission(self.center):
            self.message=self.reason() or self.center.t('Workspace is busy or access needs verification.');self.render();return False
        validate(vars(self.center.settings));return True

    def launch(self,request,training=False):
        from cloud_jobs import CloudJob
        self.recipe=None;self.refresh_mode()  # A new request must never inherit an old verified report.
        try:
            self.job=CloudJob(self.center,request,training=training)
            self.center._cloud_training_job=self.job
            self.center.set_start_enabled(False)
        except Exception as error:
            self.message='.cloud worker could not start ('+type(error).__name__+').';self.render();return
        if training:self.outputs=None
        self.percent=None;self.checked=self.fingerprint();self.message='Checking .cloud inputs…'
        self.job.received.connect(self.handle_event);self.job.completed.connect(self.completed);self.render()

    def preflight(self):
        try:
            if hasattr(self,'workspace') and not self.workspace.prepare_training(self.preflight):return
            if not self.admission():return
            s=self.center.settings
            from vapor_lock_gui import resolve_product_path
            request={'operation':'check_resume' if s.cloud_mode=='resume' else 'check_project','project':str(resolve_product_path(s.cloud_project))}
            if s.cloud_mode=='resume':request['checkpoint']=str(resolve_product_path(s.cloud_checkpoint))
            self.launch(request)
        except Exception as error:QMessageBox.information(self.center,'.cloud',str(error))

    def start(self):
        try:
            if hasattr(self,'workspace') and not self.workspace.prepare_training(self.start):return
            if not self.admission():return
            from vapor_lock_gui import ROOT,resolve_product_path
            s=self.center.settings;output=resolve_product_path(s.cloud_output) if s.cloud_output else ROOT/'cloud-training'
            if not s.cloud_output:output.mkdir(exist_ok=True)
            name=s.cloud_run_name+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(2)
            request={'operation':'train','project':str(resolve_product_path(s.cloud_project)),'output_parent':str(output),'run_name':name,
                     'mode':s.cloud_mode,'overrides':{'device':s.cloud_device}}
            if s.cloud_mode=='resume':request['checkpoint']=str(resolve_product_path(s.cloud_checkpoint))
            else:
                request['overrides'].update(epochs=s.cloud_epochs,batch_size=-1 if s.cloud_batch==0 else s.cloud_batch,patience=s.cloud_patience)
                if s.cloud_mode=='initialize':request['initial_model']=str(resolve_product_path(s.cloud_initial_model))
            self.launch(request,training=True)
        except Exception as error:QMessageBox.information(self.center,'.cloud',str(error))

    def stop(self):
        if self.job and not self.job.done:
            self.message='Stop requested. Waiting for the .cloud worker…' if self.job.training else 'Stopping .cloud check…'
            self.job.stop();self.render()

    def handle_event(self,value):
        kind=value['type']
        if kind=='project_checked':self.message=self.center.tf('Project checked: {train} training · {val} validation · {test} test · {annotations} instances · {negatives} reviewed negatives · {unreviewed} unreviewed excluded',**value['counts'])
        elif kind in ('resume_checked','configuration'):
            if kind=='resume_checked' and self.checked!=self.fingerprint():return
            self.recipe=value['recipe'];self.message=self.center.t('Training recipe verified.')
            self.refresh_mode()
        elif kind=='phase':self.message='Checking .cloud inputs…' if value['phase']=='checking' else 'Starting .cloud training…'
        elif kind=='progress':
            epoch=value.get('epoch');epochs=value.get('epochs');batch=value.get('batch');batches=value.get('batches')
            self.message=self.center.tf('Epoch {epoch}/{epochs} · batch {batch}/{batches}',epoch=epoch or '—',epochs=epochs or '—',batch=batch or '—',batches=batches or '—')
            if 'loss' in value:self.message+=' · '+self.center.t('Loss')+': '+format(value['loss'],'.5g')
            if 'resolved_batch_size' in value:self.message+=' · '+self.center.t('Images per batch')+': '+str(value['resolved_batch_size'])
            if 'effective_batch_size' in value:self.message+=' · '+self.center.t('Target effective batch')+': '+str(value['effective_batch_size'])
            if 'device' in value:self.message+=' · '+self.center.t('Training device')+': '+str(value['device'])
            if type(epoch) is int and type(epochs) is int and epochs>0:self.percent=max(0,min(99,int(100*epoch/epochs)))
        elif kind=='trained':
            self.outputs=value;self.message={'completed':'Training completed.','early_stopped':'Training stopped early after validation.','stopped':'Training stopped. Saved outputs are listed below.'}[value['status']];self.percent=100 if value['status']=='completed' else self.percent
        elif kind=='error':self.message=value['message']
        self.render()

    def completed(self,success,message):
        if not success:self.message=message or '.cloud processing failed. Source inputs were not changed. Check this run’s saved files before resuming.'
        self.render()
        self.center.set_start_enabled(not self.center.engine.active)

    def render(self):
        if not hasattr(self,'status'):return
        c=self.center;active=bool(self.job and not self.job.done)
        try:
            if not active and self.checked is not None and self.checked!=self.fingerprint():
                self.checked=None;self.recipe=None;self.refresh_mode();self.message='Inputs changed. Check the selected project or checkpoint again.'
            reason=self.reason()
            detail=c.t(self.message)+(('\n'+reason) if reason and not active else '')
            summary=reason if reason and not active else c.t(self.message)
            self.status.setText(' · '.join(line.strip() for line in summary.splitlines() if line.strip()))
            self.status.setToolTip(detail);self.status.setAccessibleName(detail)
            self.bar.setRange(0,0 if active and self.percent is None else 100)
            self.bar.setVisible(active or self.percent is not None)
            if self.percent is not None:self.bar.setValue(self.percent)
            self.check.setEnabled(not active and not reason);self.train.setEnabled(not active and not reason);self.stop_button.setEnabled(active)
            self.check.setVisible(not active);self.train.setVisible(not active);self.stop_button.setVisible(active)
            self.check.setText(c.t('Check'))
            check_scope=c.t('Check project / checkpoint');self.check.setAccessibleName(check_scope);self.check.setToolTip(check_scope)
            self.train.setText(c.t('Resume .cloudtrain' if c.settings.cloud_mode=='resume' else 'Train .cloud'))
            training=not self.job or self.job.training
            self.stop_button.setText(c.t('Stop training' if training else 'Cancel'))
            stop_scope=c.t('Stop .cloud training' if training else 'Cancel check')
            self.stop_button.setAccessibleName(stop_scope);self.stop_button.setToolTip(stop_scope)
            self.open_button.setEnabled(bool(self.outputs))
            for holder in self.input_holders:holder.setEnabled(not active)
            self.open_button.setVisible(bool(self.outputs))
            from vapor_lock_gui import ROOT,resolve_product_path
            from dataset_paths import display_path
            parent=resolve_product_path(c.settings.cloud_output) if c.settings.cloud_output else ROOT/'cloud-training'
            destination=c.t('Output folder')+': '+display_path(parent/(c.settings.cloud_run_name+'-…'))
            self.destination_details.toggle_button.setToolTip(destination)
            self.destination_details.toggle_button.setAccessibleDescription(destination)
            if hasattr(self,'project_context'):
                from dataset_paths import display_path
                selected=Path(display_path(c.settings.cloud_project)) if c.settings.cloud_project else None
                if selected and selected.name=='project.json':selected=selected.parent
                self.project_context.setText(selected.name if selected else c.t('Choose a project on the Project tab.'))
                self.project_context.setToolTip(c.settings.cloud_project)
        except RuntimeError:pass  # a theme/language rebuild will mount fresh widgets

    def open_output(self):
        if not self.outputs:return
        import os
        path=Path(self.outputs['output'])
        if path.is_dir():os.startfile(path)


def mount(center,layout):
    suite=getattr(center,'_cloud_suite',None)
    if suite is None:suite=CloudSuite(center);center._cloud_suite=suite
    suite.mount(layout)
