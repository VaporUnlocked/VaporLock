"""Opt-in crosshair controls and guarded in-memory calibration."""
from PySide6.QtCore import Qt,QTimer,QRectF,Signal,QObject,QEvent
from PySide6.QtGui import QImage,QPainter,QColor,QPen
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QCheckBox,QComboBox,QSpinBox,QDialogButtonBox,QApplication,QFileDialog,QScrollArea,QFrame)
from batch4_guard import editable,EditGuard
from batch4_geometry import enumerate_displays
from crosshair_reference import geometry,sample_template,capture_patch

def exclude_handle(handle):
    import ctypes
    from ctypes import wintypes as W
    u=ctypes.WinDLL('user32',use_last_error=True)
    u.SetWindowDisplayAffinity.argtypes=[W.HWND,W.DWORD];u.SetWindowDisplayAffinity.restype=W.BOOL
    return bool(handle and u.SetWindowDisplayAffinity(int(handle),0x11))

def exclude_windows():
    for widget in QApplication.topLevelWidgets():
        if widget.isWindow() and (widget.isVisible() or type(widget).__name__ in ('DesktopOverlay','ControlCenter','Calibration')):
            if isinstance(widget,QFileDialog) and not widget.testOption(QFileDialog.Option.DontUseNativeDialog):return False
            if not exclude_handle(widget.winId()):return False
    return True

class CaptureExclusionGuard(QObject):
    """Set affinity before showing each new/recreated Qt top-level handle."""
    def __init__(self,parent,required,failed):
        super().__init__(parent);self.required=required;self.failed=failed;self.busy=False
        QApplication.instance().installEventFilter(self)
    def eventFilter(self,widget,event):
        if self.busy or event.type() not in (QEvent.Type.Show,QEvent.Type.WinIdChange) or not isinstance(widget,QWidget) or not widget.isWindow():return False
        if not self.required():return False
        self.busy=True
        try:
            handle=widget.effectiveWinId()
            # Destruction emits WinIdChange with no handle; do not recreate it.
            if not handle and event.type()==QEvent.Type.WinIdChange:return False
            native_dialog=isinstance(widget,QFileDialog) and not widget.testOption(QFileDialog.Option.DontUseNativeDialog)
            if native_dialog or not exclude_handle(handle):self.failed()
        except (OSError,RuntimeError):self.failed()
        finally:self.busy=False
        return False
    def close(self):QApplication.instance().removeEventFilter(self)

def install_window_guard(center):
    def required():
        dialog=getattr(center,'_crosshair_dialog',None)
        return center.settings.crosshair_enabled or bool(dialog and not dialog.closed and dialog.timer.isActive())
    def failed():
        dialog=getattr(center,'_crosshair_dialog',None)
        if dialog:dialog.reject()
        # Stop through the established owner path, including pending starts and
        # native/controller authority. No queued inference may keep the reference.
        if center.engine.active or center._policy_start_pending or center._pending_controller_start is not None:
            center.stop_capture()
        center.log('WARN',center.t('Capture stopped: a VaporLock window could not be excluded. Disable crosshair detection before retrying.'))
    center._crosshair_window_guard=CaptureExclusionGuard(center,required,failed)

class Canvas(QWidget):
    changed=Signal()
    def __init__(self):
        super().__init__();self.setObjectName('crosshairCanvas');self.setMinimumSize(330,330)
        self.image=QImage();self.point=(63,63);self.mask=set();self.mode='aim'
    def frame(self):
        size=min(self.width(),self.height());return QRectF((self.width()-size)/2,(self.height()-size)/2,size,size)
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#161b22'))
        if self.image.isNull():return
        r=self.frame();p.drawImage(r,self.image);s=r.width()/127
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(0,255,180,150))
        for x,y in self.mask:p.drawRect(QRectF(r.x()+x*s,r.y()+y*s,s,s))
        x,y=self.point;p.setPen(QPen(QColor('#ffcc40'),2));p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(r.x()+(x-2)*s,r.y()+(y-2)*s,5*s,5*s))
    def mousePressEvent(self,event):
        if self.image.isNull() or not self.frame().contains(event.position()):return
        r=self.frame();x=min(126,int((event.position().x()-r.x())*127/r.width()));y=min(126,int((event.position().y()-r.y())*127/r.height()))
        self.select_at(x,y)
    def select_at(self,x,y):
        if self.image.isNull() or not 0<=x<127 or not 0<=y<127:return
        if self.mode=='aim':self.point=(x,y);self.mask.clear()
        elif self.mode=='erase':self.mask.discard((x,y))
        elif self.mode=='pixel':
            if abs(x-self.point[0])<=31 and abs(y-self.point[1])<=31:self.mask.add((x,y))
        else:
            color=self.image.pixelColor(x,y);rgb=(color.red(),color.green(),color.blue())
            for py in range(max(0,self.point[1]-31),min(127,self.point[1]+32)):
                for px in range(max(0,self.point[0]-31),min(127,self.point[0]+32)):
                    c=self.image.pixelColor(px,py)
                    if max(abs(a-b) for a,b in zip(rgb,(c.red(),c.green(),c.blue())))<=24:self.mask.add((px,py))
        self.update();self.changed.emit()

class Calibration(QDialog):
    def __init__(self,center):
        super().__init__(center);self.c=center;self.guard=EditGuard(center,stopped=True)
        self.topology=enumerate_displays();self.display=next((d for d in self.topology if d.index==center.settings.monitor),None)
        self.setObjectName('crosshairCalibration');self.setWindowTitle(center.t('Calibrate crosshair'))
        available=self.screen().availableGeometry()
        # Layout coordinates only: reserve room for the native frame/taskbar.
        # Capture and template geometry continue to use physical display pixels.
        self.resize(min(680,available.width()-24),min(740,available.height()-48))
        self.origin=(0,0);self.result_data=None;self.remaining=0;self.closed=False
        root=QVBoxLayout(self)
        self.scroll=QScrollArea();self.scroll.setObjectName('crosshairScroll')
        self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setMinimumSize(0,0);self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content=QWidget();layout=QVBoxLayout(content);layout.setContentsMargins(0,0,0,0)
        self.scroll.setWidget(content);root.addWidget(self.scroll,1)
        self.help=QLabel(center.t('Select a physical display. Capture a small central sample, confirm the aim point, then mark crosshair pixels. Only the bounded template is saved.'))
        self.help.setWordWrap(True);layout.addWidget(self.help)
        if self.display:
            source=QLabel(center.tf('Display {display}: {width} × {height} pixels; capture {crop_width} × {crop_height}.',display=self.display.index,width=self.display.width,height=self.display.height,crop_width=center.settings.region_width,crop_height=center.settings.region_height));source.setWordWrap(True);layout.addWidget(source)
        self.capture=QPushButton(center.t('Capture in 3 seconds'));self.capture.setObjectName('crosshairCapture');layout.addWidget(self.capture)
        self.canvas=Canvas();self.canvas.setAccessibleName(center.t('Crosshair sample and mask'));layout.addWidget(self.canvas,1)
        legend=QLabel(center.t('Gold ring: aim point. Green pixels: template. Search stays within 16 pixels of the saved point; keep the full pattern and search area inside capture.'))
        legend.setWordWrap(True);layout.addWidget(legend)
        self.mode=QComboBox();self.mode.setObjectName('crosshairEditMode')
        for text,key in [('Aim point','aim'),('Select colour','colour'),('Add pixel','pixel'),('Erase pixel','erase')]:self.mode.addItem(center.t(text),key)
        layout.addWidget(self.mode);self.mode.currentIndexChanged.connect(lambda _:setattr(self.canvas,'mode',self.mode.currentData()))
        row=QHBoxLayout();self.x=QSpinBox();self.y=QSpinBox()
        for text,spin in [('Pixel X',self.x),('Pixel Y',self.y)]:
            spin.setRange(0,126);spin.setValue(63);spin.setAccessibleName(center.t(text));row.addWidget(QLabel(center.t(text)));row.addWidget(spin)
        layout.addLayout(row)
        self.aim_summary=QLabel();layout.addWidget(self.aim_summary)
        self.pixel=QPushButton(center.t('Mark selected pixel'));self.pixel.setObjectName('crosshairMark');layout.addWidget(self.pixel)
        self.pixel.clicked.connect(self.mark_keyboard)
        self.clear=QPushButton(center.t('Clear mask'));layout.addWidget(self.clear);self.clear.clicked.connect(self.clear_mask)
        self.status=QLabel();self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);root.addWidget(self.status)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel);root.addWidget(self.buttons)
        for flag,text in ((QDialogButtonBox.StandardButton.Apply,'Apply'),(QDialogButtonBox.StandardButton.Cancel,'Cancel')):
            self.buttons.button(flag).setText(center.t(text))
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.commit);self.buttons.rejected.connect(self.reject)
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick)
        self.capture.clicked.connect(self.countdown);self.canvas.changed.connect(self.changed);self.changed()
    def valid(self):
        return not self.closed and self.guard.current() and self.topology==enumerate_displays()
    def clear_mask(self):self.canvas.mask.clear();self.canvas.update();self.changed()
    def mark_keyboard(self):
        self.canvas.select_at(self.x.value(),self.y.value())
    def changed(self):
        self.result_data=None
        self.aim_summary.setText(self.c.tf('Aim point: X {x}, Y {y}',x=self.canvas.point[0],y=self.canvas.point[1]))
        if not self.canvas.image.isNull():
            try:self.result_data=sample_template(self.canvas.image,self.canvas.point,self.canvas.mask,geometry(self.c.settings,self.display),self.origin)
            except ValueError:pass
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(self.result_data is not None and not self.timer.isActive())
        self.status.setText(self.c.t('Confirm the aim point and select a distinct foreground with a contrasting boundary.') if self.result_data is None else self.c.t('Template ready. Apply saves calibration without enabling detection.'))
    def countdown(self):
        try:
            if not self.valid() or self.display is None or self.display.index==0:raise ValueError()
            if not exclude_windows():raise ValueError()
        except (ValueError,OSError):self.status.setText(self.c.t('Calibration unavailable. Stop, unlock, select a physical display and reopen.'));return
        self.remaining=3;self.capture.setEnabled(False);self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(False)
        self.status.setText(self.c.tf('Capturing in {seconds} seconds. Cancel to stop.',seconds=self.remaining));self.timer.start()
    def tick(self):
        if self.closed or not self.timer.isActive():return
        try:
            if not self.valid():raise ValueError()
            self.remaining-=1
            if self.remaining>0:self.status.setText(self.c.tf('Capturing in {seconds} seconds. Cancel to stop.',seconds=self.remaining));return
            self.timer.stop()
            if not exclude_windows():raise ValueError()
            self.canvas.image,self.origin=capture_patch(self.display);self.canvas.point=(63,63);self.canvas.mask.clear();self.canvas.update();self.changed()
        except (ValueError,OSError):self.timer.stop();self.result_data=None;self.status.setText(self.c.t('Calibration cancelled because state or display changed. Reopen to retry.'))
        finally:
            if not self.timer.isActive():self.capture.setEnabled(True)
    def commit(self):
        try:
            if self.timer.isActive() or not self.valid() or self.result_data is None:raise ValueError()
            self.c.change('crosshair_calibration',self.result_data)
            if self.c.settings.crosshair_calibration!=self.result_data:raise ValueError()
            self.accept()
        except (ValueError,OSError):self.status.setText(self.c.t('Calibration cancelled because state or display changed. Reopen to retry.'))
    def done(self,result):
        self.closed=True;self.timer.stop();self.canvas.image=QImage();self.canvas.mask.clear();self.result_data=None
        super().done(result)

def open_calibration(center):
    if not editable(center,stopped=True):return
    try:dialog=Calibration(center)
    except (ValueError,OSError):return
    center._crosshair_dialog=dialog
    try:dialog.exec()
    finally:center._crosshair_dialog=None;dialog.deleteLater()
    refresh_status(center)

def add_controls(center,layout,setting=None):
    setting=setting or center.setting
    toggle=QCheckBox(center.t('Detect my crosshair'));toggle.setObjectName('crosshairEnabled');toggle.setChecked(center.settings.crosshair_enabled)
    def change(value):
        center.change('crosshair_enabled',value)
        toggle.blockSignals(True);toggle.setChecked(center.settings.crosshair_enabled);toggle.blockSignals(False);refresh_status(center)
    toggle.toggled.connect(change)
    setting(layout,'Crosshair detection','Optional. Off by default. When enabled, matching replaces manual offsets; loss uses screen centre.',toggle)
    button=QPushButton(center.t('Calibrate crosshair'));button.setObjectName('crosshairCalibrate');button.clicked.connect(lambda:open_calibration(center))
    setting(layout,'Crosshair calibration','Calibrated dots, crosses and custom patterns. Animated or transparent designs may need recalibration.',button)
    center.crosshair_status=QLabel();center.crosshair_status.setWordWrap(True);center.crosshair_status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(center.crosshair_status)
    timer=QTimer(center.crosshair_status);timer.setObjectName('crosshairStatusTimer');timer.setInterval(100)
    timer.timeout.connect(lambda:refresh_status(center));timer.start()
    refresh_status(center)

def refresh_status(center,data=None):
    if not hasattr(center,'crosshair_status'):return
    generation=getattr(center.engine,'_launch_generation',0)
    if data is not None:center._crosshair_telemetry=(generation,dict(data))
    saved=getattr(center,'_crosshair_telemetry',None)
    data=saved[1] if saved and saved[0]==generation else None
    if not center.settings.crosshair_enabled:
        text='Off — manual crop-relative offset' if center.settings.aim_reference=='crosshair-offset' else 'Off — screen centre'
    elif not center.engine.session_running:text='Enabled — waiting for capture' if center.settings.crosshair_calibration else 'Calibration required — using screen centre'
    else:
        key=(data or {}).get('crosshair_status','searching')
        text={'tracking':'Tracking calibrated crosshair','searching':'Acquiring crosshair — using screen centre','lost':'Crosshair lost or ambiguous — using screen centre','recalibrate':'Calibration mismatch — using screen centre','outside-capture':'Target assistance paused — reference outside capture'}.get(key,'Acquiring crosshair — using screen centre')
        if key=='search-outside-capture':text='Search area outside capture — using screen centre'
        if data and data.get('reference_inside') is False:text='Target assistance paused — reference outside capture'
        if key=='screen-gap':text='Target assistance paused — reference between displays'
        elif key=='tracking':
            from crosshair_reference import remaining_ms
            from engine_stream import native_tick_ms
            if remaining_ms(data,native_tick_ms())<=0:text='Target assistance paused — waiting for a fresh reference'
    if center.engine.session_running and data and data.get('reference_inside') is False:
        text='Target assistance paused — reference between displays' if data.get('crosshair_status')=='screen-gap' else 'Target assistance paused — reference outside capture'
    center.crosshair_status.setText(center.t(text))
