"""Visual editors for existing anchor and capture settings, without screen capture."""
from PySide6.QtCore import Qt,QRectF,QPointF,Signal
from PySide6.QtGui import QPainter,QPen,QColor,QPainterPath
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QComboBox,QDoubleSpinBox,QSpinBox,QDialogButtonBox)
from batch4_geometry import anchor_point,anchor_offsets,ANCHOR_BASES,region_pixels,region_offsets,enumerate_displays
from batch4_guard import editable,EditGuard


def label(text):
    item=QLabel(text);item.setWordWrap(True);item.setTextFormat(Qt.TextFormat.PlainText)
    item.setProperty('_vaporlock_i18n_skip',True)
    return item


def dialog_base(center,title,name):
    from vapor_lock_gui import appearance_palette
    dialog=QDialog(center);dialog.setObjectName(name);dialog.setWindowTitle(center.t(title))
    dialog.resize(740,650)
    colors=appearance_palette(center.ui_appearance,True)
    dialog.setStyleSheet(f'QDialog#{name} {{background:{colors["carbon"]};color:{colors["ice"]};}}')
    layout=QVBoxLayout(dialog)
    layout.addWidget(label(center.t('Stop capture before applying. Cancel leaves your settings unchanged.')))
    return dialog,layout


class AnchorCanvas(QWidget):
    picked=Signal(float,float)
    def __init__(self,parent=None):
        super().__init__(parent);self.setMinimumSize(240,280)
        self.setObjectName('anchorCanvas');self.mode='center';self.x=self.y=0.
    def box(self):
        # Display full native reachable domain: x=-.5..1.5, y=-1..2.
        height=min((self.height()-28)/3,(self.width()-28)*.75)
        width=height*.5
        return QRectF((self.width()-width)/2,(self.height()-height)/2,width,height)
    def set_anchor(self,mode,x,y):
        anchor_point(mode,x,y);self.mode,self.x,self.y=mode,x,y;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor('#161b22'));r=self.box()
        p.setPen(QPen(QColor('#7a8792'),1,Qt.PenStyle.DashLine))
        p.drawLine(QPointF(0,r.center().y()),QPointF(self.width(),r.center().y()))
        p.drawLine(QPointF(r.center().x(),0),QPointF(r.center().x(),self.height()))
        p.setPen(QPen(QColor('white'),1.5));p.setBrush(QColor('#080a0d'));p.drawRect(r)
        # Original geometric silhouette; no third-party image or game artwork.
        p.save();p.translate(r.topLeft());p.scale(r.width(),r.height())
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('white'))
        p.drawEllipse(QRectF(.36,.04,.28,.14))
        body=QPainterPath(QPointF(.31,.22))
        for x,y in ((.69,.22),(.88,.48),(.75,.54),(.63,.36),(.64,.57),(.76,.95),(.59,.95),
                    (.5,.65),(.41,.95),(.24,.95),(.36,.57),(.37,.36),(.25,.54),(.12,.48)):
            body.lineTo(x,y)
        body.closeSubpath();p.drawPath(body);p.restore()
        x,y=anchor_point(self.mode,self.x,self.y);point=QPointF(r.x()+x*r.width(),r.y()+y*r.height())
        p.setPen(QPen(QColor('black'),5));p.drawEllipse(point,7,7)
        p.setPen(QPen(QColor('white'),2));p.drawEllipse(point,7,7)
        p.drawLine(point+QPointF(-13,0),point+QPointF(13,0));p.drawLine(point+QPointF(0,-13),point+QPointF(0,13))
        p.end()
    def pick(self,event):
        r=self.box();point=event.position()
        self.picked.emit(*anchor_offsets(self.mode,(point.x()-r.x())/r.width(),(point.y()-r.y())/r.height()))
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.pick(event)
    def mouseMoveEvent(self,event):
        if event.buttons()&Qt.MouseButton.LeftButton:self.pick(event)


def open_anchor_editor(center):
    if not editable(center):return
    guard=EditGuard(center,stopped=True)
    dialog,layout=dialog_base(center,'Visual anchor editor','anchorEditor')
    layout.addWidget(label(center.t('Global anchor preview. Click or drag the marker. Offsets are fractions of box size; positive is right/down. Class-specific anchors override this global setting.')))
    canvas=AnchorCanvas();canvas.setAccessibleName(center.t('Global anchor preview'));layout.addWidget(canvas,1)
    form=QFormLayout();layout.addLayout(form)
    mode=QComboBox();mode.setObjectName('anchorMode')
    for title,key in (('Box center','center'),('Top edge','top'),('Bottom edge','bottom'),('Upper quarter','upper-quarter')):
        mode.addItem(center.t(title),key)
    mode.setCurrentIndex(mode.findData(center.settings.anchor_mode));form.addRow(center.t('Anchor mode'),mode)
    controls=[];edited=set()
    for title,key in (('Horizontal offset','anchor_x'),('Vertical offset','anchor_y')):
        spin=QDoubleSpinBox();spin.setObjectName(key);spin.setDecimals(6);spin.setRange(-1,1)
        spin.setSingleStep(.01);spin.setKeyboardTracking(False);spin.setValue(getattr(center.settings,key))
        spin.setAccessibleName(center.t(title));form.addRow(center.t(title),spin);controls.append(spin)
        spin.valueChanged.connect(lambda _value,name=key:edited.add(name))
        spin.lineEdit().textEdited.connect(lambda _text,name=key:edited.add(name))
        if spin.value()!=getattr(center.settings,key):
            spin.setToolTip(center.tf('Saved exact value: {value}. Editing replaces it with the displayed precision.',value=repr(getattr(center.settings,key))))
    if any(abs(guard.values[key])>1 for key in ('anchor_x','anchor_y')):
        layout.addWidget(label(center.t('Saved offsets outside -1 to 1 are clamped by the engine. The preview shows that clamp; untouched saved values are preserved.')))
    summary=label('');layout.addWidget(summary)
    def refresh():
        canvas.set_anchor(mode.currentData(),controls[0].value(),controls[1].value())
        x,y=anchor_point(canvas.mode,canvas.x,canvas.y)
        summary.setText(center.tf('Point from box top-left: X {x}%, Y {y}%. Outside-box positions are valid.',x=f'{100*x:g}',y=f'{100*y:g}'))
    for spin in controls:spin.valueChanged.connect(refresh)
    mode.currentIndexChanged.connect(refresh)
    def picked(x,y):
        edited.update(('anchor_x','anchor_y'))
        controls[0].setValue(x);controls[1].setValue(y)
    canvas.picked.connect(picked);refresh()
    status=label('');status.setObjectName('editStatus');layout.addWidget(status)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
    def commit():
        for spin in controls:spin.interpretText()
        if not guard.current():
            status.setText(center.t('Stop capture and close/reopen this editor if settings or displays changed.'));return
        values={'anchor_mode':mode.currentData(),
                'anchor_x':controls[0].value() if 'anchor_x' in edited else guard.values['anchor_x'],
                'anchor_y':controls[1].value() if 'anchor_y' in edited else guard.values['anchor_y']}
        try:anchor_point(values['anchor_mode'],values['anchor_x'],values['anchor_y'])
        except (ValueError,TypeError,OverflowError):
            status.setText(center.t('Invalid anchor. Choose a mode and finite offsets.'));return
        # No event-loop yield between final guard and the stopped-session updates.
        for key,value in values.items():center.change(key,value)
        center.rebuild_pages();dialog.accept()
    buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(commit)
    buttons.rejected.connect(dialog.reject);dialog.exec()


class RegionCanvas(QWidget):
    picked=Signal(int,int,int,int)
    def __init__(self,parent=None):
        super().__init__(parent);self.setMinimumSize(240,230);self.setObjectName('regionCanvas')
        self.display=None;self.region=(0,0,1,1);self.start=None;self.displays=()
    def frame(self):
        d=self.display
        if d is None:return QRectF()
        scale=min((self.width()-24)/d.width,(self.height()-24)/d.height)
        return QRectF((self.width()-d.width*scale)/2,(self.height()-d.height*scale)/2,d.width*scale,d.height*scale)
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#161b22'))
        if self.display:
            r=self.frame();p.setPen(QPen(QColor('#b8c3ce'),1));p.setBrush(QColor('#24323f'));p.drawRect(r)
            if self.display.index==0:
                # Show holes in the virtual desktop and each physical monitor.
                p.fillRect(r,QColor('#10141a'))
                scale=r.width()/self.display.width
                for d in self.displays:
                    if d.index==0:continue
                    monitor=QRectF(r.x()+(d.left-self.display.left)*scale,r.y()+(d.top-self.display.top)*scale,d.width*scale,d.height*scale)
                    p.setBrush(QColor('#24323f'));p.drawRect(monitor)
                    p.drawText(monitor,Qt.AlignmentFlag.AlignCenter,str(d.index))
            x,y,w,h=self.region;s=r.width()/self.display.width
            p.setPen(QPen(QColor('#80dcf8'),2));p.setBrush(QColor(128,220,248,50))
            p.drawRect(QRectF(r.x()+x*s,r.y()+y*s,w*s,h*s))
        p.end()
    def pixel(self,point):
        r=self.frame();d=self.display
        return (min(d.width-1,max(0,int((point.x()-r.x())/r.width()*d.width))),
                min(d.height-1,max(0,int((point.y()-r.y())/r.height()*d.height))))
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.display and self.frame().contains(event.position()):
            self.start=self.pixel(event.position());self.picked.emit(*self.start,1,1)
    def mouseMoveEvent(self,event):
        if event.buttons()&Qt.MouseButton.LeftButton and self.start and self.display:
            x,y=self.pixel(event.position());sx,sy=self.start
            self.picked.emit(min(x,sx),min(y,sy),abs(x-sx)+1,abs(y-sy)+1)
    def mouseReleaseEvent(self,event):self.start=None


def open_region_editor(center):
    if not editable(center):return
    try:displays=enumerate_displays()
    except (OSError,ValueError):
        center.log('WARN',center.t('Physical display information is unavailable.'));return
    guard=EditGuard(center,stopped=True)
    dialog,layout=dialog_base(center,'Pixel capture region','regionEditor')
    layout.addWidget(label(center.t('Drag on the display diagram or enter exact physical pixels. Left/top are relative to the selected display, not the desktop. No screenshot is taken.')))
    monitor=QComboBox();monitor.setObjectName('regionMonitor');monitor.setPlaceholderText(center.t('Choose a display'))
    for d in displays:
        title=center.t('Virtual desktop (compatibility capture)') if d.index==0 else center.tf('Monitor {index}',index=d.index)
        monitor.addItem(f'{title} · {d.device} · {d.width} × {d.height}',d.index)
    layout.addWidget(monitor)
    canvas=RegionCanvas();canvas.setAccessibleName(center.t('Capture display diagram'));canvas.displays=displays;layout.addWidget(canvas,1)
    form=QFormLayout();layout.addLayout(form);controls=[]
    for title,key in (('Left (px)','left'),('Top (px)','top'),('Width (px)','width'),('Height (px)','height')):
        spin=QSpinBox();spin.setRange(0,1000000);spin.setKeyboardTracking(False);spin.setObjectName('region_'+key)
        spin.setAccessibleName(center.t(title));form.addRow(center.t(title),spin);controls.append(spin)
    summary=label('');layout.addWidget(summary);status=label('');status.setObjectName('editStatus');layout.addWidget(status)
    busy=False
    def refresh(*_):
        nonlocal busy
        if busy or monitor.currentIndex()<0:return
        busy=True
        d=displays[monitor.currentIndex()]
        # Keep origin fixed when dimensions grow: clamp width/height to remaining area.
        controls[0].setRange(0,d.width-1);controls[1].setRange(0,d.height-1)
        controls[2].setRange(1,d.width-controls[0].value());controls[3].setRange(1,d.height-controls[1].value())
        vals=tuple(c.value() for c in controls);canvas.display=d;canvas.region=vals;canvas.update()
        x,y,w,h=region_offsets(d,*vals)
        summary.setText(center.tf('Centered offsets: {x}, {y} px · Desktop origin: {left}, {top} px',x=x,y=y,left=d.left+vals[0],top=d.top+vals[1]))
        busy=False
    def set_values(vals):
        nonlocal busy
        busy=True
        for c,v in zip(controls,vals):c.setRange(0,1000000);c.setValue(v)
        busy=False;refresh()
    def display_changed(*_):
        selected=monitor.currentIndex()>=0
        for c in controls:c.setEnabled(selected)
        canvas.setEnabled(selected)
        status.setText('' if selected else center.t('The saved display is unavailable. Select a display explicitly.'))
        if not selected:
            canvas.display=None;canvas.update();summary.clear();return
        d=displays[monitor.currentIndex()]
        set_values(region_pixels(d,center.settings.region_x,center.settings.region_y,center.settings.region_width,center.settings.region_height))
    for c in controls:c.valueChanged.connect(refresh)
    monitor.setCurrentIndex(monitor.findData(center.settings.monitor))
    monitor.currentIndexChanged.connect(display_changed);canvas.picked.connect(lambda *vals:set_values(vals));display_changed()
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
    def commit():
        for c in controls:c.interpretText()
        try:
            if monitor.currentIndex()<0:
                status.setText(center.t('The saved display is unavailable. Select a display explicitly.'));return
            if not guard.current() or enumerate_displays()!=displays:raise ValueError('Changed')
            d=displays[monitor.currentIndex()];vals=region_offsets(d,*(c.value() for c in controls))
        except (OSError,ValueError):
            status.setText(center.t('Stop capture and close/reopen this editor if settings or displays changed.'));return
        for key,value in zip(('monitor','region_x','region_y','region_width','region_height'),(d.index,*vals)):
            center.change(key,value)
        center.rebuild_pages();dialog.accept()
    buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(commit)
    buttons.rejected.connect(dialog.reject);dialog.exec()
