"""Qt-owned, bounded local job lifecycle. HTTP sees detached safe snapshots."""
from dataclasses import asdict
import hashlib,json,re,secrets,time
from PySide6.QtCore import QProcess,QTimer,Qt
from PySide6.QtWidgets import QMessageBox
from batch4_guard import editable

KINDS=('annotation','training')
ACTIVE={'awaiting-confirmation','starting','running','cancelling'}


def fingerprint(center):
    # Digest is internal. It is not an API credential or a reusable approval.
    payload=[asdict(center.settings),str(center._active_profile_path),id(center.settings),
             center._tuning_epoch,
             bool(center.training_api_key.text()) if hasattr(center,'training_api_key') else False]
    return hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()


def admission(center,capability):
    return _idle_admission(center)


def cloud_admission(center):
    """Local .cloud suite admission uses the same busy-state rules."""
    from vapor_lock_gui import EDITION
    from cloud_access import edition
    from cloud_contract import CloudInputError
    try:
        if edition()!=EDITION or False!=(EDITION=='free'):return False
    except CloudInputError:return False
    return _idle_admission(center)


def _idle_admission(center):
    if not editable(center,stopped=True):return False
    if center.cloud_background_busy():return False
    for name in ('_dataset_worker','_training_worker','_cloud_training_job','_cloud_model_check_job'):
        process=getattr(center,name,None)
        try:
            if process is not None and process.state()!=QProcess.ProcessState.NotRunning:return False
        except RuntimeError:pass
    registry=getattr(center,'_api_jobs',None)
    launching=getattr(center,'_api_job_launch',None)
    if launching is not None and launching['fingerprint']!=fingerprint(center):return False
    if registry and any(r['state'] in ACTIVE and r is not getattr(center,'_api_job_launch',None) for r in registry.records.values()):return False
    return True


def registry(center):
    if not hasattr(center,'_api_jobs'):center._api_jobs=Jobs(center)
    return center._api_jobs


def event(center,process,payload):
    jobs=getattr(center,'_api_jobs',None)
    if jobs:jobs.event(process,payload)


def bind(center,process,kind):
    record=getattr(center,'_api_job_launch',None)
    if record is not None:registry(center).bind(record,process,kind)


class Jobs:
    def __init__(self,center):
        self.center=center;self.records={};self.timer=QTimer(center)
        self.timer.timeout.connect(self.tick);self.timer.start(1000)
    def public(self,record):
        return {key:record[key] for key in ('id','kind','state','current','total','result') if key in record}
    def publish(self):
        self.center.local_api.update_jobs([self.public(r) for r in self.records.values()])
    def terminal(self,record,state,result):
        pending=record['state']=='awaiting-confirmation'
        record.update(state=state,result=result,settled=time.monotonic());record.pop('process',None)
        dialog=record.pop('dialog',None)
        if dialog is not None:
            if dialog.isVisible():dialog.reject()
            dialog.deleteLater()
        if pending:
            status=self.center.dataset_worker_status if record['kind']=='annotation' else self.center.training_status
            status.setText(self.center.t('API request ended without starting a worker.'))
        self.publish()
    def tick(self):
        now=time.monotonic()
        for record in list(self.records.values()):
            if record['state']=='awaiting-confirmation' and (now-record['created']>120 or record['fingerprint']!=fingerprint(self.center)):
                self.terminal(record,'rejected','confirmation-expired-or-configuration-changed')
            elif record['state'] not in ACTIVE and now-record.get('settled',now)>86400:
                self.records.pop(record['id'],None)
        self.publish()
    def request(self,payload,stamp):
        if type(payload) is not dict:return {'state':'rejected','error':'invalid job request'}
        if payload.get('action')=='cancel' and set(payload)=={'action','id'}:
            return self.cancel(payload['id'])
        if set(payload)!={'action','kind'} or payload['action']!='start' or payload['kind'] not in KINDS:
            return {'state':'rejected','error':'invalid job request'}
        c=self.center;kind=payload['kind'];capability='dataset' if kind=='annotation' else 'training'
        if stamp!=fingerprint(c) or not admission(c,capability):return {'state':'rejected','error':'configuration changed or worker busy'}
        if kind=='training' and (c.settings.training_platform_project.strip() or (hasattr(c,'training_api_key') and c.training_api_key.text().strip())
                or not c.settings.training_base_model.lower().endswith('.pt')):
            return {'state':'rejected','error':'choose a local PT model without Platform project or credentials'}
        if len(self.records)>=32:
            oldest=next((key for key,r in self.records.items() if r['state'] not in ACTIVE),None)
            if oldest is None:return {'state':'rejected','error':'job history is full'}
            self.records.pop(oldest)
        identifier=secrets.token_hex(12)
        record=dict(id=identifier,kind=kind,state='awaiting-confirmation',created=time.monotonic(),fingerprint=stamp)
        self.records[identifier]=record;self.publish()
        status=c.dataset_worker_status if kind=='annotation' else c.training_status
        status.setText(c.t('API job awaiting confirmation.'))
        dialog=QMessageBox(c);dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,True)
        dialog.setWindowTitle(c.t('API job confirmation'))
        dialog.setIcon(QMessageBox.Icon.Question);dialog.setTextFormat(Qt.TextFormat.PlainText)
        s=c.settings
        text=c.t('An authenticated API client requested a local job. Start using the current settings?')+'\n'+c.t('Annotation' if kind=='annotation' else 'Local training')
        if kind=='annotation':
            text+='\n'+c.t('Existing labels')+': '+c.t('Additive' if s.dataset_annotation_additive else 'Replace' if s.dataset_annotation_replace else 'Create new')
            text+='\n'+c.t('Delete unannotated images')+': '+str(s.dataset_annotation_delete_unannotated)
            text+='\n'+c.t('Image effects')+': '+str(s.dataset_effects_enabled)
        else:text+='\n'+c.t('Epochs')+': '+str(s.training_epochs)
        # Local-only display may show paths; public status never includes these.
        text+='\n'+str(s.dataset_directory if kind=='annotation' else s.training_dataset)
        text+='\n'+str((s.dataset_annotation_model or s.model_path) if kind=='annotation' else s.training_base_model)
        dialog.setText(text);dialog.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QMessageBox.StandardButton.No);record['dialog']=dialog
        dialog.finished.connect(lambda result:self.confirm(record,result==int(QMessageBox.StandardButton.Yes)))
        dialog.open()
        return {'state':'awaiting-confirmation','job_id':identifier}
    def confirm(self,record,accepted):
        if record['state']!='awaiting-confirmation':return
        c=self.center
        if not accepted:self.terminal(record,'rejected','declined');return
        if record['fingerprint']!=fingerprint(c):self.terminal(record,'rejected','configuration-changed');return
        dialog=record.pop('dialog',None)
        if dialog is not None:dialog.deleteLater()
        c._api_job_launch=record
        try:
            capability='dataset' if record['kind']=='annotation' else 'training'
            if not admission(c,capability):self.terminal(record,'rejected','worker-busy-or-unauthorized');return
            record['state']='starting';self.publish()
            if record['kind']=='annotation':c.annotate_dataset()
            else:c.start_local_training()
            if record.get('process') is None:self.terminal(record,'failed','worker-not-started')
        except Exception:self.terminal(record,'failed','worker-not-started')
        finally:c._api_job_launch=None
    def bind(self,record,process,kind):
        if record['kind']!=kind or record['state']!='starting':raise ValueError('Invalid API worker ownership')
        record['process']=process
        process.started.connect(lambda:self.started(record,process))
        process.finished.connect(lambda code,status:self.finished(record,process,code))
        process.errorOccurred.connect(lambda error:self.terminal(record,'failed','worker-not-started') if error==QProcess.ProcessError.FailedToStart and record.get('process') is process else None)
    def started(self,record,process):
        if record.get('process') is process and record['state']=='starting':record['state']='running';self.publish()
    def event(self,process,payload):
        record=next((r for r in self.records.values() if r.get('process') is process),None)
        if record is None or type(payload) is not dict:return
        for key in ('current','total'):
            value=payload.get(key)
            if type(value) is int and 0<=value<=10000000:record[key]=value
        event=payload.get('type')
        if event in ('complete','trained'):record['complete']=True
        if event in ('error','provider_error','effects_error','effects_skipped'):record['failed']=True
        if payload.get('cleanup_pending') or payload.get('report_pending') or payload.get('outcome_uncertain'):record['uncertain']=True
        self.publish()
    def finished(self,record,process,code):
        if record.get('process') is not process:return
        if record.get('uncertain'):state='uncertain'
        elif code==0 and record.get('complete') and not record.get('failed'):state='completed'
        elif process.property('vaporlock_user_cancelled'):state='uncertain' if record['kind']=='annotation' else 'cancelled'
        else:state='failed'
        self.terminal(record,state,'inspect-native-status' if state in ('failed','uncertain','cancelled') else 'complete')
    def cancel(self,identifier):
        if type(identifier) is not str or not re.fullmatch('[0-9a-f]{24}',identifier):return {'state':'rejected','error':'invalid job id'}
        record=self.records.get(identifier)
        if record is None:return {'state':'rejected','error':'unknown or expired job'}
        if record['state']=='awaiting-confirmation':self.terminal(record,'cancelled','cancelled-before-start');return {'state':'cancelled','job_id':identifier}
        if record['state'] not in ACTIVE:return {'state':record['state'],'job_id':identifier}
        c=self.center;attribute='_dataset_worker' if record['kind']=='annotation' else '_training_worker';process=record.get('process')
        if process is None or getattr(c,attribute,None) is not process:return {'state':'rejected','error':'worker ownership changed'}
        try:
            if process.state()==QProcess.ProcessState.NotRunning:return {'state':'rejected','error':'worker is settling'}
            (c.stop_dataset_worker if record['kind']=='annotation' else c.stop_training_worker)()
            if not process.property('vaporlock_user_cancelled'):return {'state':'rejected','error':'stop request failed'}
        except RuntimeError:return {'state':'rejected','error':'worker is settling'}
        record['state']='cancelling';self.publish();return {'state':'cancelling','job_id':identifier}
