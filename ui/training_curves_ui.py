"""Read-only local run curves, separate from cloud annotation."""
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton
from visual_geometry_ui import label
from training_curves import METRICS, run_names, read_run


class CurvePlot(QWidget):
    def __init__(self, center):
        super().__init__(); self.center=center; self.rows=[]; self.metric_key=''; self.empty_message=''; self.setMinimumHeight(240)
    def paintEvent(self,event):
        from interface_themes import painted_role
        p=QPainter(self); p.fillRect(self.rect(),QColor(painted_role(self,'surface','#161b22'))); p.setPen(QColor(painted_role(self,'text','#dfe9f2')))
        data=[(epoch,values.get(self.metric_key)) for epoch,values in self.rows]
        valid=[value for _,value in data if value is not None]
        if not valid:
            p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,self.center.t(self.empty_message or 'No finite values for this metric.')); return
        low,high=min(0,min(valid)),max(valid)
        if high==low: high=low+1
        first,last=data[0][0],data[-1][0]; span=max(1,last-first)
        left,top,width,height=80,26,max(1,self.width()-100),self.height()-68
        p.drawText(4,top+5,f'{high:.5g}'); p.drawText(4,top+height,f'{low:.5g}')
        p.drawText(left,self.height()-12,self.center.tf('Epoch {first}–{last}',first=first,last=last))
        p.drawLine(left,top,left,top+height); p.drawLine(left,top+height,left+width,top+height)
        p.setPen(QPen(QColor('#7dbcf5'),2)); previous=None; previous_epoch=None
        for epoch,value in data:
            if value is None: previous=None; continue
            point=QPointF(left+(epoch-first)/span*width,top+height*(1-(value-low)/(high-low)))
            if previous is not None and epoch==previous_epoch+1: p.drawLine(previous,point)
            p.drawEllipse(point,2,2); previous=point; previous_epoch=epoch
        p.end()


class TrainingCurves(QWidget):
    def __init__(self,center,root):
        super().__init__(); self.center=center; self.root=root; self.data=None; self.list_unavailable=False; self.empty_message=''
        self.setObjectName('trainingCurves')
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.intro=label(''); layout.addWidget(self.intro)
        self.run_label=label(''); layout.addWidget(self.run_label)
        row=QHBoxLayout(); self.runs=QComboBox(); self.runs.setObjectName('curveRun'); row.addWidget(self.runs,1)
        self.refresh_button=QPushButton(); row.addWidget(self.refresh_button); layout.addLayout(row)
        self.metric_label=label(''); layout.addWidget(self.metric_label)
        self.metrics=QComboBox(); self.metrics.setObjectName('curveMetric'); layout.addWidget(self.metrics)
        self.runs.setProperty('tuningLockExempt',True); self.metrics.setProperty('tuningLockExempt',True)
        self.plot=CurvePlot(center); layout.addWidget(self.plot); self.status=label(''); layout.addWidget(self.status)
        self.refresh_button.clicked.connect(self.refresh); self.runs.currentIndexChanged.connect(self.load)
        self.metrics.currentIndexChanged.connect(self.refresh_plot)
        self.timer=QTimer(self); self.timer.setInterval(3000); self.timer.timeout.connect(self.poll)
        self.retranslate()
    def allowed(self):
        return not False and not False
    def retranslate(self):
        c=self.center
        # A styled panel also renders correctly when inspected as a standalone
        # widget; the plot keeps its own explicitly painted background.
        from vapor_lock_gui import appearance_palette
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.setStyleSheet('QWidget#trainingCurves {background:'+appearance_palette(c.ui_appearance,True)['carbon']+';}')
        self.intro.setText(c.t('Local training curves · managed runs only, up to 128 runs and 10,000 epochs. Refreshes every 3 seconds while visible. Missing values remain gaps.'))
        self.refresh_button.setText(c.t('Refresh runs')); self.runs.setAccessibleName(c.t('Local training run'))
        self.run_label.setText(c.t('Local training run')); self.metric_label.setText(c.t('Training metric'))
        self.metrics.setAccessibleName(c.t('Training metric'))
        for i in range(self.metrics.count()): self.metrics.setItemText(i,c.t(METRICS[self.metrics.itemData(i)]))
        if self.empty_message: self.status.setText(c.t(self.empty_message))
        self.plot.update()
        self.refresh_plot()
    def showEvent(self,event):
        super().showEvent(event); self.refresh(); self.timer.start()
    def hideEvent(self,event): self.timer.stop(); super().hideEvent(event)
    def poll(self):
        if self.isVisible(): self.load()
    def refresh(self):
        if not self.allowed(): self.load(); return
        selected=self.runs.currentData()
        self.list_unavailable=False
        try: names=run_names(self.root)
        except (OSError,ValueError):
            self.list_unavailable=True; names=[]
        self.runs.blockSignals(True); self.runs.clear()
        for name in names: self.runs.addItem(name,name)
        self.runs.setCurrentIndex(max(0,self.runs.findData(selected))); self.runs.blockSignals(False); self.load()
    def load(self):
        self.data=None
        if not self.allowed(): self.clear('The training view is unavailable.'); return
        if self.list_unavailable: self.clear('Training runs are unavailable. Check the folder and refresh.'); return
        name=self.runs.currentData()
        if name is None: self.clear('No local training runs yet.'); return
        try: self.data=read_run(self.root,name)
        except FileNotFoundError:
            self.clear('No results saved for this run yet.'); return
        except (OSError,ValueError,UnicodeError,OverflowError):
            self.clear('Run data unavailable or changing. Refresh after the next epoch.'); return
        key=self.metrics.currentData(); self.metrics.blockSignals(True); self.metrics.clear()
        for metric in self.data['metrics']: self.metrics.addItem(self.center.t(METRICS[metric]),metric)
        self.metrics.setCurrentIndex(max(0,self.metrics.findData(key))); self.metrics.blockSignals(False); self.refresh_plot()
    def clear(self,message):
        self.empty_message=message
        self.plot.empty_message=message
        self.plot.rows=[]; self.plot.update(); self.status.setText(self.center.t(message))
        self.plot.hide()
        self.metrics.hide();self.metric_label.hide()
        self.runs.setVisible(self.runs.count()>0);self.run_label.setVisible(self.runs.count()>0)
    def refresh_plot(self):
        if not self.allowed(): self.clear('The training view is unavailable.'); return
        if self.data is None: return
        self.plot.empty_message=''
        self.plot.rows=self.data['rows']; self.plot.metric_key=self.metrics.currentData(); self.plot.update()
        if not self.data['metrics']: self.clear('No supported metrics saved for this run yet.'); return
        if not self.plot.rows: self.clear('No complete epoch rows yet.'); return
        self.empty_message=''
        for widget in (self.plot,self.metrics,self.metric_label,self.runs,self.run_label):widget.show()
        values=[(e,v.get(self.plot.metric_key)) for e,v in self.plot.rows if v.get(self.plot.metric_key) is not None]
        text=self.center.t('No finite values for this metric.')
        if values:
            epoch,value=values[-1]; text=self.center.tf('Latest recorded value: {value} · epoch {epoch}',value=f'{value:.6g}',epoch=epoch)
        if self.data['partial']: text+=' · '+self.center.t('Incomplete final row omitted.')
        self.status.setText(text)
