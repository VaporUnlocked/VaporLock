"""Native, non-persisted consent for the read-only observation projection."""
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QCheckBox,QLabel
from observation_api import Observations


class Sharing:
    def __init__(self,center,layout):
        self.c=center;self.enabled=False;self.generation=0;self.was_active=False;self.owner=None
        self.cache=Observations();center.local_api.observation_cache=self.cache
        self.toggle=QCheckBox(center.t('Share observations for this session'))
        self.toggle.setProperty('tuningLockExempt',True)
        self.toggle.setObjectName('observationSharing');self.toggle.toggled.connect(self.change)
        center.setting(layout,'Observation sharing',
            'Session-only opt-in. Authenticated clients can read recent boxes, classes and confidence. Stop, profile/model changes turn sharing off.',center.feature_control(self.toggle))
        help=QLabel(center.t('Session-only opt-in. Authenticated clients can read recent boxes, classes and confidence. Stop, profile/model changes turn sharing off.'))
        help.setWordWrap(True);help.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(help)
        self.status=QLabel();self.status.setTextFormat(Qt.TextFormat.PlainText);self.status.setWordWrap(True);layout.addWidget(self.status)
        self.timer=QTimer(center);self.timer.setObjectName('observationSharingTimer');self.timer.setInterval(50);self.timer.timeout.connect(self.sync);self.timer.start()

    def identity(self):
        c=self.c
        return (id(c.settings),str(c._active_profile_path),c.settings.model_path,0)

    def stamp(self):
        e=self.c.engine
        return (getattr(e,'_launch_generation',0),getattr(e,'_stream_generation',0),getattr(e,'_visual_epoch',0),self.generation)

    def change(self,value):
        from batch4_guard import editable
        c=self.c
        value=bool(value and not False and editable(c,stopped=True) and True)
        self.enabled=value;self.generation+=1;self.owner=self.identity() if value else None
        self.toggle.blockSignals(True);self.toggle.setChecked(value);self.toggle.blockSignals(False)
        self.cache.configure(False,False,self.stamp());self.sync()

    def sync(self):
        c=self.c;e=c.engine
        if self.enabled and (not True or getattr(c,'_native_closing',False)
                or False or self.owner!=self.identity()
                or (self.was_active and not e.active)):
            self.enabled=False;self.generation+=1
            self.toggle.blockSignals(True);self.toggle.setChecked(False);self.toggle.blockSignals(False)
        enabled=self.enabled and c.settings.local_api_enabled and not getattr(c,'_native_closing',False)
        if bool(getattr(e,'_api_observations',False))!=bool(enabled):
            e._api_observations=bool(enabled)
            if e.active:e.apply_live_setting('desktop_overlay_enabled',c.settings.desktop_overlay_enabled)
        self.was_active=e.active
        self.cache.configure(enabled,e.session_running and not e.intentional_stop,self.stamp(),c.settings.output_max_observation_age_ms)
        state=self.cache.read()
        if state['state']=='fresh':self.status.setText(c.tf('Sharing enabled · fresh observations: {count}.',count=len(state['observations'])))
        else:self.status.setText(c.t({'disabled':'Observation sharing is off.','waiting':'Sharing enabled · waiting for fresh observations.',
            'stale':'Sharing enabled · observations expired.'}[state['state']]))

    def accept(self,payload,launch,stream,epoch,accepted):
        self.sync();stamp=self.stamp()
        if stamp[:3]!=(launch,stream,epoch):return
        self.cache.publish(payload,stamp,accepted=accepted)

    def rebind(self):
        self.change(False);self.c.local_api.observation_cache=self.cache

    def dispose(self):
        self.timer.stop();self.timer.timeout.disconnect(self.sync);self.timer.deleteLater();self.change(False)


def install(center,layout):center.observation_sharing=Sharing(center,layout)
