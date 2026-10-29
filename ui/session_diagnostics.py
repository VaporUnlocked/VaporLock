"""Bounded, credential-free measurements tied to one actual engine run."""
import math
from copy import deepcopy
from datetime import datetime

METRICS = frozenset({
    'capture_wall_ms','capture_acquire_ms','capture_config_retired_total','cloud_fresh_capture_pacing',
    'fps','capture_fps','inference_fps','capture_ms','inference_ms','preprocess_ms',
    'model_ms','postprocess_ms','tracking_ms','capture_total','inference_total',
    'synthetic_inference_total','inference_explicit_skip_total','inference_rate_limited_total',
    'capture_no_fresh_poll_total','detections','tracks','selected_track','selected_class',
    'selected_missed','output_active','output_reason','output_transport','output_sent_total',
    'output_failed_total','output_observation_age_ms','observe_only','motion_compensation_active',
    'event_action_reason','configured_capture_fps','configured_inference_fps',
    'configured_frame_skip','capture_width','capture_height','uptime_seconds',
    'inference_obsolete_total','high_resolution_pacing',
    'capture_backend','capture_recovery_reason','capture_recovery_probes',
    'capture_recovery_transitions','capture_dxgi_timeouts','capture_dxgi_frames',
})
STAGES = ('capture','preprocess','model','postprocess','tracking','loop_interval',
          'observation_interval','completion_interval','observation_age')
VALUES = ('mean_ms','p50_ms','p95_ms','p99_ms','max_ms')

def timing_summary(value):
    if (type(value) is not dict or type(value.get('schema_version')) is not int or value.get('schema_version') != 1
            or value.get('scope') != 'session-after-2s' or value.get('bucket_ms') != .25):
        return None
    result = dict(schema_version=1,scope='session-after-2s',bucket_ms=.25)
    if value.get('percentiles') == 'bucket-upper-bound' and value.get('overflow_from_ms') == 127.75:
        result.update(percentiles='bucket-upper-bound',overflow_from_ms=127.75)
    for stage in (*STAGES,*(name for name in ('capture_wall','capture_acquire') if name in value)):
        item=value.get(stage)
        if type(item) is not dict or type(item.get('count')) is not int or not 0 <= item['count'] <= 2**53-1:
            return None
        if any(type(item.get(k)) not in (int,float) or not 0 <= item[k] <= 86400000
               or not math.isfinite(item[k]) for k in VALUES):
            return None
        if not item['p50_ms'] <= item['p95_ms'] <= item['p99_ms'] <= item['max_ms']:
            return None
        result[stage] = {k:item[k] for k in ('count',*VALUES)}
        overflow=item.get('overflow_count')
        if type(overflow) is int and 0 <= overflow <= item['count']:
            result[stage]['overflow_count']=overflow
    return result

class SessionReportCache:
    def __init__(self):
        self.reset()

    def reset(self):
        self.metrics = {}
        self.context = {}

    def record(self, data, provider, generation):
        if type(data) is not dict or provider not in ('cpu','dml','cuda','trt') or type(generation) is not int:
            return
        metrics = {}
        for key in METRICS:
            value=data.get(key)
            if type(value) in (int,float) and abs(value) <= 2**53-1 and math.isfinite(value):
                metrics[key]=value
            elif type(value) is bool:
                metrics[key]=value
            elif type(value) is str and len(value) <= 128 and all(ord(c)>=32 for c in value):
                metrics[key]=value
        timing=timing_summary(data.get('timing'))
        if timing is not None:metrics['timing']=timing
        device=data.get('inference_device')
        if type(device) is str and len(device)<=128 and all(ord(c)>=32 for c in device):
            metrics['inference_device']=device
        self.metrics=metrics
        self.context=dict(provider=provider,generation=generation,
            received_at=datetime.now().astimezone().isoformat(timespec='milliseconds'))

    def snapshot(self):
        return deepcopy(dict(context=self.context,metrics=self.metrics))
