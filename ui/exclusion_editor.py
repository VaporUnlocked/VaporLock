"""Native capture-pixel exclusion editor; no screenshots or live output."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget, QHBoxLayout, QFormLayout, QComboBox, QSpinBox, QPushButton, QDialogButtonBox
from batch4_guard import editable, EditGuard
from visual_geometry_ui import dialog_base, label
from exclusion_zones import validate_zones, MAX_ZONES, MAX_PIXEL


class ZoneCanvas(QWidget):
    def __init__(self, width, height):
        super().__init__(); self.frame_width=width; self.frame_height=height; self.zones=[]; self.selected=-1
        self.setMinimumHeight(210); self.setObjectName('zoneCanvas')
    def paintEvent(self,event):
        p=QPainter(self); p.fillRect(self.rect(),QColor('#161b22'))
        fw,fh=max(1,self.frame_width-1),max(1,self.frame_height-1)
        scale=min((self.width()-24)/fw,(self.height()-24)/fh)
        rect=QRectF((self.width()-fw*scale)/2,(self.height()-fh*scale)/2,fw*scale,fh*scale)
        p.setPen(QPen(QColor('white'),1)); p.drawRect(rect); p.setClipRect(rect)
        for index,z in enumerate(self.zones):
            box=QRectF(rect.x()+z['x']*scale,rect.y()+z['y']*scale,(z['width']-1)*scale,(z['height']-1)*scale)
            p.setBrush(QColor(240,145,166,55)); p.setPen(QPen(QColor('#f091a6'),3 if index==self.selected else 1,Qt.PenStyle.DashLine))
            p.drawRect(box); p.drawText(box,Qt.AlignmentFlag.AlignCenter,str(index+1))
        p.end()


def open_editor(center):
    if not editable(center,stopped=True): return
    guard=EditGuard(center,stopped=True); zones=validate_zones(center.settings.exclusion_zones)
    dialog,layout=dialog_base(center,'Target exclusion zones','exclusionEditor')
    layout.addWidget(label(center.t('A target is excluded when its observed or smoothed aiming point is inside a zone, including its border. Prediction cannot move into a zone. Detections remain visible.')))
    layout.addWidget(label(center.t('Coordinates are pixels from the capture top-left. Zones keep their pixel positions when capture size changes; only the visible intersection is shown.')))
    layout.addWidget(label(center.t('Right = left + width − 1; bottom = top + height − 1. Borders are included. Independent motion compensation is unchanged.')))
    w,h=center.settings.region_width,center.settings.region_height
    # Capture size 0 means full display. Resolve using native physical geometry.
    from batch4_geometry import enumerate_displays, region_pixels
    try:
        topology=enumerate_displays()
        display=next(d for d in topology if d.index==center.settings.monitor)
        _,_,w,h=region_pixels(display,center.settings.region_x,center.settings.region_y,w,h)
    except (OSError,ValueError,StopIteration):
        topology=None
        if w<=0 or h<=0: return
    layout.addWidget(label(center.tf('Capture diagram: {width} × {height} px',width=w,height=h)))
    canvas=ZoneCanvas(w,h); layout.addWidget(canvas)
    row=QHBoxLayout(); choice=QComboBox(); choice.setObjectName('zoneChoice'); choice.setAccessibleName(center.t('Target exclusion zones'))
    row.addWidget(choice,1)
    add=QPushButton(center.t('Add zone')); add.setObjectName('addZone'); row.addWidget(add)
    remove=QPushButton(center.t('Remove zone')); remove.setObjectName('removeZone'); row.addWidget(remove); layout.addLayout(row)
    form=QFormLayout(); boxes={}
    for key,title in (('x','Left (px)'),('y','Top (px)'),('width','Width (px)'),('height','Height (px)')):
        box=QSpinBox(); box.setObjectName('zone_'+key); box.setRange(0 if key in ('x','y') else 1,MAX_PIXEL)
        box.setKeyboardTracking(False); box.setAccessibleName(center.t(title)); boxes[key]=box; form.addRow(center.t(title),box)
    layout.addLayout(form); region_status=label(''); layout.addWidget(region_status)
    feedback=label(''); feedback.setObjectName('zoneFeedback'); layout.addWidget(feedback)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Apply).setText(center.t('Apply'))
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(center.t('Cancel'))
    layout.addWidget(buttons); buttons.rejected.connect(dialog.reject)
    def paint():
        canvas.zones=zones; canvas.selected=choice.currentIndex(); canvas.update()
        add.setEnabled(len(zones)<MAX_ZONES); remove.setEnabled(bool(zones))
        i=choice.currentIndex(); state='No exclusion zones.'
        if i>=0:
            z=zones[i]
            state=('Inactive: outside this capture.' if z['x']>=w or z['y']>=h else
                   'Partly clipped to this capture.' if z['x']+z['width']>w or z['y']+z['height']>h else 'Inside this capture.')
        region_status.setText(center.t(state))
    def select():
        i=choice.currentIndex()
        for key,box in boxes.items():
            box.blockSignals(True); box.setEnabled(i>=0); box.setValue(zones[i][key] if i>=0 else (0 if key in ('x','y') else 1)); box.blockSignals(False)
        paint()
    def refresh(index):
        choice.blockSignals(True); choice.clear()
        for i in range(len(zones)): choice.addItem(center.tf('Zone {number}',number=i+1))
        choice.setCurrentIndex(index); choice.blockSignals(False); select()
    def change():
        i=choice.currentIndex()
        if i>=0: zones[i]={key:box.value() for key,box in boxes.items()}; paint()
    def add_zone():
        if len(zones)<MAX_ZONES:
            zones.append(dict(x=0,y=0,width=max(1,w//4),height=max(1,h//4))); refresh(len(zones)-1)
    def remove_zone():
        i=choice.currentIndex()
        if i>=0: zones.pop(i); refresh(min(i,len(zones)-1))
    def commit():
        try:
            for box in boxes.values(): box.interpretText()
            value=validate_zones(zones)
            if not guard.current(): raise ValueError('Stale')
            if topology is not None and enumerate_displays()!=topology: raise ValueError('Display changed')
            center.change('exclusion_zones',value)
            if center.settings.exclusion_zones!=value: raise ValueError('Rejected')
            dialog.accept()
        except (OSError,ValueError): feedback.setText(center.t('Apply was not completed. Check rectangle bounds, stop active work and reopen if settings changed.'))
    choice.currentIndexChanged.connect(select); add.clicked.connect(add_zone); remove.clicked.connect(remove_zone)
    for box in boxes.values(): box.valueChanged.connect(change)
    buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(commit)
    refresh(0 if zones else -1); dialog.exec()
