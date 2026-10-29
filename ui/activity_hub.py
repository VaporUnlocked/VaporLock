"""Observation and identity-bound cancellation of existing operations only."""
import os
from PySide6.QtCore import QTimer, QProcess
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFrame, QLabel, QTreeWidget, QTreeWidgetItem, QHeaderView
from visual_geometry_ui import label
from process_resources import ResourceSampler
from mission_layout import ActivityRow


def process_identity(process):
    try:
        if process is not None and process.state()!=QProcess.ProcessState.NotRunning:
            return (process,int(process.processId()))
    except RuntimeError: pass
    return None


def jobs(center):
    """Fresh snapshots: no saved control can authorize a replacement job."""
    c=center
    text=lambda name: getattr(c,name).text()[:1000] if hasattr(c,name) else ''
    capture=(process_identity(getattr(c.engine,'process',None)),getattr(c.engine,'_launch_generation',None),
        getattr(c,'_start_intent_generation',0),id(getattr(c,'_pending_controller_start',None)))
    active=bool(c.engine.active or c._policy_start_pending or c._pending_controller_start is not None or c.xbox_compatible_bridge.preparing)
    result=[('capture','Capture',active,capture,text('readiness_detail'),'selection',c.stop_capture)]
    probes=tuple(getattr(c,'_model_probes',()))
    selection=getattr(c,'_model_selection',None)
    token=(getattr(c,'_probe_revision',0),probes,selection,getattr(selection,'_generation',None))
    result.append(('probes','Model checks',c.model_probe_busy(),token,text('model_runtime_status'),'model',c.cancel_model_probes))
    acceleration=process_identity(getattr(c.acceleration,'process',None))
    result.append(('acceleration','Acceleration setup',bool(acceleration),acceleration,str(getattr(c,'_acceleration_detail_base',''))[:1000],'model',None))
    for key,title,attribute,status in (('training','Local training','_training_worker','training_status'),
                                      ('dataset','Dataset task','_dataset_worker','dataset_worker_status')):
        process=getattr(c,attribute,None); identity=process_identity(process)
        cloud=bool(identity and key=='dataset' and process.property('vaporlock_cloud_worker'))
        if cloud: status='ultralytics_worker_status'; title='Ultralytics task'
        cancel=c.stop_training_worker if key=='training' else c.stop_dataset_worker
        registry=getattr(c,'_api_jobs',None)
        if registry:
            from api_jobs import ACTIVE
            kind='training' if key=='training' else 'annotation'
            record=next((r for r in registry.records.values() if r['kind']==kind and r['state'] in ACTIVE),None)
            if record is not None:
                identifier=record['id']
                detail=c.t('API job awaiting confirmation.') if record['state']=='awaiting-confirmation' else text(status)
                result.append((key,title,True,('api',identifier),detail,'dataset',lambda ident=identifier:registry.cancel(ident)))
                continue
        result.append((key,title,bool(identity),identity,text(status),'dataset',cancel))
    return result


def cancel_snapshot(center,key,identity):
    for k,_,active,current,_,_,cancel in jobs(center):
        if k==key and active and current==identity and cancel is not None:
            cancel(); return True
    return False


class ActivityHub(QWidget):
    def __init__(self,center):
        super().__init__(); self.center=center; self.setObjectName('activityHub'); self.sampler=ResourceSampler(); self.cards={}; self.tokens={}; self.pressed_tokens={}
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.intro=label(''); layout.addWidget(self.intro)
        for key in ('capture','probes','acceleration','training','dataset'):
            card=ActivityRow(); card.setObjectName('signalPanel'); column=QHBoxLayout(card)
            glyph=QLabel();glyph.setProperty('missionGlyph',{'capture':'display','probes':'search','acceleration':'chip','training':'chart','dataset':'database'}[key]);column.addWidget(glyph)
            title=label(''); status=label(''); column.addWidget(title,1); column.addWidget(status,2)
            row=QHBoxLayout(); go=QPushButton(); stop=QPushButton(); stop.setObjectName('hubStop_'+key)
            stop.setProperty('tuningLockExempt',True); stop.setObjectName('hubStop_'+key)
            go.setProperty('missionIcon','link');stop.setProperty('missionIcon','stop')
            row.addWidget(go); row.addWidget(stop); column.addLayout(row); layout.addWidget(card)
            go.clicked.connect(lambda _=False,k=key:self.navigate(k))
            stop.pressed.connect(lambda k=key:self.pressed_tokens.__setitem__(k,self.tokens.get(k)))
            stop.clicked.connect(lambda _=False,k=key:self.cancel_pressed(k))
            self.cards[key]=(title,status,go,stop)
        self.resources_heading=label(''); layout.addWidget(self.resources_heading)
        self.resource_scope=label(''); layout.addWidget(self.resource_scope)
        self.resources=QTreeWidget();self.resources.setColumnCount(5);self.resources.setRootIsDecorated(False)
        self.resources.setObjectName('processResourceValues');self.resources.setMinimumHeight(180)
        self.resources.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.resources.header().setStretchLastSection(True);layout.addWidget(self.resources)
        self.timer=QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self.refresh)
        self.destroyed.connect(lambda:self.sampler.close()); self.retranslate()
    def retranslate(self):
        self.resources.setHeaderLabels([self.center.t(x) for x in ('Process','PID','CPU','RAM (MiB)','Private (MiB)')])
        self.intro.setText(self.center.t('Existing activity only. Open its controls to start work. Cancelling does not undo completed files.'))
        self.resources_heading.setText(self.center.t('Owned process resources'))
        self.resource_scope.setText(self.center.t('Direct processes only; worker descendants and GPU usage are not included. CPU 100% means all logical processors. RAM is working set; private memory is committed private bytes. Updates once per second while visible.'))
        self.refresh()
    def navigate(self,key):
        for k,_,_,_,_,page,_ in jobs(self.center):
            if k==key:
                self.center.show_page(page)
                if key in ('training','dataset') and hasattr(self.center,'training_data_tabs'):
                    tabs=self.center.training_data_tabs
                    process=getattr(self.center,'_dataset_worker',None)
                    cloud=process_identity(process) and process.property('vaporlock_cloud_worker')
                    index=tabs.index_of('Ultralytics' if key=='training' or cloud else 'Annotation Suite')
                    if index>=0: tabs.setCurrentIndex(index)
                    if key=='training': self.center.ultralytics_tabs.setCurrentIndex(self.center.ultralytics_tabs.index_of('Training'))
                    elif cloud: self.center.ultralytics_tabs.setCurrentIndex(self.center.ultralytics_tabs.index_of('Annotation'))
                return
    def cancel_pressed(self,key):
        token=self.pressed_tokens.pop(key,None)
        if token is not None: return cancel_snapshot(self.center,key,token)
        return False
    def showEvent(self,event):
        super().showEvent(event); self.refresh(); self.timer.start()
    def hideEvent(self,event):
        self.timer.stop(); self.sampler.close(); super().hideEvent(event)
    def refresh(self):
        c=self.center
        for key,title,active,identity,detail,page,cancel in jobs(c):
            heading,status,go,stop=self.cards[key]
            process=getattr(c,'_training_worker' if key=='training' else '_dataset_worker',None) if key in ('training','dataset') else None
            stopping=bool(active and process is not None and process.property('vaporlock_user_cancelled'))
            state=c.t('Stopping…' if stopping else 'Active' if active else 'Idle')
            origin=' · API' if isinstance(identity,tuple) and identity and identity[0]=='api' else ''
            heading.setText(c.t(title)+origin+' · '+state); status.setText(detail or state)
            go.setText(c.t('Go to controls')); go.setAccessibleName(c.t('Go to controls')+' · '+c.t(title))
            stop.setText(c.t('Cancel task')); stop.setAccessibleName(c.t('Cancel task')+' · '+c.t(title))
            stop.setVisible(cancel is not None); stop.setEnabled(active and cancel is not None)
            self.tokens[key]=identity if active else None
        if not self.isVisible(): return
        identities={'Control Center':(self,os.getpid())}
        for title,process in (('Native engine',getattr(c.engine,'process',None)),
            ('Training launcher',getattr(c,'_training_worker',None)),('Dataset launcher',getattr(c,'_dataset_worker',None)),
            ('Acceleration setup',getattr(c.acceleration,'process',None))):
            identity=process_identity(process)
            if identity:
                generation=getattr(c.engine,'_launch_generation',0) if title=='Native engine' else 0
                identities[title]=(*identity,generation)
        rows=[]
        for name,sample in self.sampler.sample(identities).items():
            if sample is None: rows.append([c.t(name),c.t('Unavailable'),'—','—','—']); continue
            cpu=c.t('Sampling…') if sample['cpu'] is None else f"{sample['cpu']:.1f}%"
            rows.append([c.t(name),str(sample['pid']),cpu,f"{sample['ram']/1048576:.1f}",f"{sample['private']/1048576:.1f}"])
        while self.resources.topLevelItemCount()>len(rows):self.resources.takeTopLevelItem(self.resources.topLevelItemCount()-1)
        for index,values in enumerate(rows):
            item=self.resources.topLevelItem(index)
            if item is None:item=QTreeWidgetItem(self.resources)
            for column,value in enumerate(values):item.setText(column,value)


def misc_page(center):
    page,layout=center.new_page('Misc','Activity and local process resources.')
    center.activity_hub=ActivityHub(center); layout.addWidget(center.activity_hub); layout.addStretch()
    return page
