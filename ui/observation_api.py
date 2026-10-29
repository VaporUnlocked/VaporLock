"""Bounded projection of native observations; no pixels or output authority."""
from copy import deepcopy
import math,secrets,threading
from engine_stream import native_tick_ms,native_observation_age_ms

LIMIT=256



def number(value):
    return type(value) in (int,float) and math.isfinite(value)


class Observations:
    def __init__(self):
        self.lock=threading.RLock();self.enabled=False;self.active=False
        self.stamp=None;self.launch=None;self.session=None;self.data=None;self.identity=None
        self.ttl=150;self.last_publish=-1000;self.pending_boundary=None

    def configure(self,enabled,active,stamp,ttl=150):
        with self.lock:
            if self.launch!=(stamp[0] if stamp else None):
                self.launch=stamp[0] if stamp else None;self.session=secrets.token_hex(12) if active else None
            if not enabled or not active or self.stamp!=stamp:
                self.data=None;self.identity=None;self.last_publish=-1000;self.pending_boundary=None
            if active and self.session is None:self.session=secrets.token_hex(12)
            self.enabled=bool(enabled);self.active=bool(active);self.stamp=stamp
            self.ttl=min(150,max(1,int(ttl)))

    def clear(self):
        with self.lock:self.data=None;self.last_publish=-1000

    def publish(self,payload,stamp,now=None,*,accepted=True):
        now=native_tick_ms() if now is None else now
        with self.lock:
            if not self.enabled or not self.active or stamp!=self.stamp:return False
            if not payload:self.clear();return False
            try:
                identity=tuple(payload[k] for k in ('session','source_generation','config_revision','frame_id'))
                if any(type(v) is not int or not 0<v<2**53 for v in identity):raise ValueError()
                if type(payload.get('control_epoch')) is not int:raise ValueError()
                if payload['control_epoch']!=stamp[2]:return False
                old=self.identity
                if old is not None:
                    old=self.identity
                    if (identity[0]!=old[0] or identity[1]<old[1] or identity[2]<old[2]
                            or identity[3]<old[3] or (identity[3]==old[3] and identity!=self.pending_boundary)):return False
                changed=old is not None and identity[:3]!=old[:3]
                self.identity=identity
                if not accepted:self.clear();return False
                if changed:self.clear()
                if payload.get('source_boundary'):
                    self.clear();self.pending_boundary=identity;return False
                self.pending_boundary=None
                width,height=payload['width'],payload['height']
                if any(type(v) is not int or not 0<v<=16384 for v in (width,height)) or width*height>16777216:raise ValueError()
                age=native_observation_age_ms(payload,now)
                if not math.isfinite(age) or age>=self.ttl:raise ValueError()
                tracks=payload['tracks']
                if type(tracks) is not list or len(tracks)>4096:raise ValueError()
                selected=payload['selected_track']
                if type(selected) is not int or selected<0:raise ValueError()
                records=[];seen=set()
                for track in tracks:
                    ident,cls=track['id'],track['class_id']
                    if (type(ident) is not int or not 0<ident<2**53 or ident in seen
                            or type(cls) is not int or not 0<=cls<1000000):raise ValueError()
                    seen.add(ident)
                    if type(track.get('has_observation')) is not bool:raise ValueError()
                    if not track['has_observation']:continue
                    values=[track[k] for k in ('observed_x','observed_y','observed_w','observed_h','confidence','observation_age_ms')]
                    if not all(number(v) for v in values):raise ValueError()
                    x,y,w,h,confidence,track_age=values
                    if w<=0 or h<=0 or not 0<=confidence<=1 or track_age<0:raise ValueError()
                    missed=track['missed']
                    if type(missed) is not int or missed<0:raise ValueError()
                    if missed or track_age+now-payload['emitted_tick_ms']>=self.ttl:continue
                    left,top=max(0.,x),max(0.,y);right,bottom=min(width,x+w),min(height,y+h)
                    if right<=left or bottom<=top:continue
                    records.append(dict(track_id=ident,class_id=cls,confidence=confidence,
                        box=[left,top,right-left,bottom-top],_age=track_age))
                records.sort(key=lambda r:r['track_id'])
                # Empty results and identity changes clear immediately, ahead of rate limiting.
                if records and not changed and now-self.last_publish<50:return False
                self.identity=identity;self.last_publish=now
                self.data=dict(session_id=self.session,source_generation=identity[1],config_revision=identity[2],
                    frame_id=identity[3],width=width,height=height,coordinate_space='capture-pixels-xywh',
                    selected_track=selected,observations=records[:LIMIT],truncated=len(records)>LIMIT,
                    _emitted=payload['emitted_tick_ms'],_age=payload['observation_age_ms'],
                    _reference={k:payload[k] for k in ('reference_inside','crosshair_detected','crosshair_age_ms','emitted_tick_ms') if k in payload})
                return True
            except (KeyError,TypeError,ValueError,OverflowError):
                self.clear();return False

    def read(self,now=None):
        now=native_tick_ms() if now is None else now
        with self.lock:
            empty=dict(state='disabled' if not self.enabled else 'waiting',observations=[],selected_track=0)
            if not self.enabled or not self.active or self.data is None:return empty
            data=deepcopy(self.data);emitted=data.pop('_emitted');age=data.pop('_age')
            if type(now) is not int or now<emitted or age+now-emitted>=self.ttl:
                return dict(state='stale',observations=[],selected_track=0)
            from crosshair_reference import remaining_ms
            if remaining_ms(data.pop("_reference"),now)<=0:data["selected_track"]=0
            valid=[]
            for item in data['observations']:
                item_age=item.pop('_age')+now-emitted
                if item_age<self.ttl:item['age_ms']=round(item_age,3);valid.append(item)
            data['observations']=valid
            if not any(r['track_id']==data['selected_track'] for r in valid):data['selected_track']=0
            return dict(data,state='fresh',age_ms=round(age+now-emitted,3),max_age_ms=self.ttl)
