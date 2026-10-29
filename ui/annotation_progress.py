"""Bounded, credential-free worker progress and native presentation."""
import math
import time

STAGES = {
    'initializing': 'Initializing annotation model', 'annotating': 'Annotating images',
    'scan': 'Checking cloud images', 'download': 'Downloading images',
    'check': 'Checking preview images', 'labels': 'Updating cloud labels',
    'effects': 'Preparing image effects', 'archive': 'Creating upload archive',
    'upload': 'Uploading images', 'ingest': 'Waiting for cloud import',
    'verify': 'Verifying replacement images', 'delete': 'Removing verified originals',
    'inspect': 'Checking imported images',
}
COUNTS = ('current','total','pass_current','passes','bytes_current','bytes_total')
ERRORS = {
    'RATE_LIMIT':'Ultralytics rate limit reached. Wait before starting another task.',
    'NETWORK_UNAVAILABLE':'Cannot reach Ultralytics. Check the connection and operation report.',
    'REPLACEMENT_IMPORT_PENDING':'Cloud import is still pending. Originals have not been removed.',
    'REPLACEMENT_IMPORT_FAILED':'Cloud import failed. Originals have not been removed.',
    'REPLACEMENT_VERIFICATION_FAILED':'Uploaded images did not pass verification. Originals were preserved.',
    'REPLACEMENT_IMPORT_INCOMPLETE':'Some uploaded images are missing. Originals were preserved.',
    'IMAGE_STATE_CHANGED':'An image changed during this task. Further writes stopped.',
    'TARGET_CHANGED':'The cloud dataset changed during this task. Further writes stopped.',
    'TARGET_BUSY':'Ultralytics is already processing this dataset. Wait for that task to finish.',
    'AUTHENTICATION_FAILED':'The API key was rejected. Check the saved key.',
    'PERMISSION_DENIED':'The API key does not have permission for this operation.',
    'CANCELLED':'Task cancelled. Completed changes remain saved.',
    'OUTCOME_UNCERTAIN':'A cloud write could not be confirmed. Check the report before retrying.',
}

def error_text(center,code):
    if type(code) is not str or code not in ERRORS:return center.t('Cloud operation stopped. Check the saved operation report for details.')
    return center.t(ERRORS[code])+' ['+code+']'

def bounded(value, limit=10**12):
    return type(value) in (int,float) and math.isfinite(value) and 0 <= value <= limit

class Progress:
    def __init__(self, emit):
        self.emit=emit;self.started=time.monotonic();self.stage_started=self.started
        self.last=0.;self.stage=None;self.counts={};self.waiting=False

    def __call__(self, stage=None, *, force=False, waiting=False, **counts):
        now=time.monotonic()
        if stage is not None:
            if type(stage) is not str or stage not in STAGES:return
            if stage!=self.stage:
                self.stage=stage;self.stage_started=now;self.counts={};force=True
        if self.stage is None:return
        self.counts.update({k:int(v) for k,v in counts.items() if k in COUNTS and bounded(v)})
        if now-self.last<.5 and not force:return
        self.last=now;self.waiting=bool(waiting)
        self.emit('cloud_stage',stage=self.stage,elapsed_ms=round((now-self.started)*1000),
            stage_ms=round((now-self.stage_started)*1000),waiting=self.waiting,**self.counts)

def stage_text(center,payload):
    stage=payload.get('stage')
    if type(stage) is not str or stage not in STAGES:return None
    if any(k in payload and (type(payload[k]) is not int or not bounded(payload[k])) for k in COUNTS):return None
    fields={k:int(payload[k]) for k in COUNTS if bounded(payload.get(k))}
    text=center.t(STAGES[stage])
    if fields.get('total'):
        text+=' · '+str(min(fields.get('current',0),fields['total']))+'/'+str(fields['total'])
    if fields.get('passes'):
        text+=' · '+center.tf('Pass {current}/{total}',current=fields.get('pass_current',0),total=fields['passes'])
    if fields.get('bytes_total'):
        text+=' · '+center.tf('{current}/{total} MiB',current=round(fields.get('bytes_current',0)/1048576,1),total=round(fields['bytes_total']/1048576,1))
    if payload.get('waiting') is True:text+=' · '+center.t('Waiting; no new completion confirmed')
    return text

def show_stage(center,payload,*,cloud):
    text=stage_text(center,payload)
    if text is None:return
    worker=getattr(center,'_dataset_worker',None)
    label=center.ultralytics_worker_status if cloud else center.dataset_worker_status
    label.setText(text)
    if worker is None:return
    now=time.monotonic();previous=worker.property('vaporlock_progress_stage')
    count=tuple(payload.get(k,0) for k in COUNTS)
    if previous!=payload['stage']:
        worker.setProperty('vaporlock_stage_started',now)
        worker.setProperty('vaporlock_progress_stage',payload['stage'])
    changed=worker.property('vaporlock_progress_counts')!=list(count)
    if changed:
        worker.setProperty('vaporlock_last_completion',now)
        worker.setProperty('vaporlock_progress_counts',list(count))
    last=worker.property('vaporlock_last_progress_log') or 0.
    final=bounded(payload.get('current')) and payload.get('current')==payload.get('total') and payload.get('total',0)>0
    if previous!=payload['stage'] or now-last>=2 or final and changed:
        worker.setProperty('vaporlock_last_progress_log',now)
        worker.setProperty('vaporlock_last_progress_text',text)
        elapsed=round(now-(worker.property('vaporlock_started_at') or now),1)
        center.log('INFO',center.tf('Dataset task: {status} · {seconds}s elapsed.',status=text,seconds=elapsed))

def show_timing(center,payload,*,cloud):
    timing=payload.get('timing')
    if type(timing) is not dict:return
    keys=('load_ms','preprocess_ms','inference_ms','postprocess_ms','consensus_ms')
    if any(not bounded(timing.get(k)) for k in keys):return
    worker=getattr(center,'_dataset_worker',None)
    if worker is None:return
    now=time.monotonic();last=worker.property('vaporlock_last_timing_log') or 0.
    if now-last<2 and payload.get('current')!=payload.get('total'):return
    worker.setProperty('vaporlock_last_timing_log',now)
    center.log('INFO',center.tf('Annotation timing (ms): read {read}, prepare {prepare}, inference {inference}, decode {decode}, agreement {agreement}.',
        read=round(timing['load_ms']),prepare=round(timing['preprocess_ms']),inference=round(timing['inference_ms']),
        decode=round(timing['postprocess_ms']),agreement=round(timing['consensus_ms'])))

def refresh_activity(center):
    from PySide6.QtCore import QProcess
    worker=getattr(center,'_dataset_worker',None)
    if not hasattr(center,'ultralytics_activity_clock'):return
    if worker is not None and worker.state()!=QProcess.ProcessState.NotRunning and worker.property('vaporlock_cloud_worker'):
        now=time.monotonic();started=worker.property('vaporlock_started_at') or now
        stage=worker.property('vaporlock_stage_started') or started;last=worker.property('vaporlock_last_completion') or started
        center._ultralytics_elapsed_snapshot=dict(total=int(now-started),stage=int(now-stage),last=int(now-last))
    final=getattr(center,'_ultralytics_final_seconds',None)
    if final is not None:
        center.ultralytics_activity_clock.setText(center.tf('Task duration: {seconds}s.',seconds=final));return
    snapshot=getattr(center,'_ultralytics_elapsed_snapshot',None)
    if snapshot:
        center.ultralytics_activity_clock.setText(center.tf('Elapsed {total}s · Stage {stage}s · Last progress {last}s ago',**snapshot))
