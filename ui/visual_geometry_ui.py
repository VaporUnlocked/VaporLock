"""Visual editors for existing anchor and capture settings, without screen capture."""
import math
from pathlib import Path
import sys

from PySide6.QtCore import QEvent,Qt,QRectF,QPointF,Signal
from PySide6.QtGui import QColor,QGuiApplication,QImage,QPainter,QPen
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QComboBox,QSpinBox,QDialogButtonBox)
from batch4_geometry import anchor_point,anchor_offsets,region_pixels,region_offsets,enumerate_displays
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


class TargetingSample(QWidget):
    """Show a sample detection using the capture frame's physical pixels."""

    picked = Signal(float, float)
    ZONES = (
        ("retention_radius", "Retention", "#a29ad0"),
        ("acquisition_radius", "Acquisition", "#80bcf2"),
        ("near_radius", "Near", "#73d0c1"),
        ("snap_radius", "Snap", "#b3ef88"),
    )
    SAMPLE_SIDE_PX = 246
    SETTINGS_FIELDS = frozenset({
        "anchor_mode", "anchor_x", "anchor_y", "monitor", "region_x", "region_y",
        "region_width", "region_height", "fov_shape", "crosshair_enabled", "capture_source",
        "aim_reference", "aim_reference_x", "aim_reference_y",
        *(field for field, _, _ in ZONES),
    })

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        self.artwork = QImage(str(resource_root / "assets/targeting-sample.png"))
        self.dragging = False
        self.fit_capture = False
        self.displays = ()
        self.video_geometry = None
        self.setObjectName("targetingSample")
        self.setAccessibleName("Sample detection box, aim point and targeting zones")
        self.setMinimumWidth(180)
        self.setFixedHeight(328)
        app = QGuiApplication.instance()
        app.screenAdded.connect(self.refresh_displays)
        app.screenRemoved.connect(self.refresh_displays)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_displays()

    def refresh_displays(self):
        try:
            self.displays = enumerate_displays()
        except OSError:
            self.displays = ()
        self.update()

    def event(self, event):
        handled = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self.update()
        return handled

    def set_fit_capture(self, enabled):
        self.fit_capture = enabled
        self.update()

    def set_video_geometry(self, telemetry=None):
        geometry = None
        if telemetry and telemetry.get("capture_backend") in ("window", "video-device", "video-file"):
            width, height, x, y = (telemetry.get(key) for key in
                                   ("capture_width", "capture_height", "reference_x", "reference_y"))
            if (all(type(dimension) is int and dimension > 0 for dimension in (width, height))
                    and all(type(value) in (int, float) and math.isfinite(value) for value in (x, y))):
                geometry = (width, height, x, y)
        if geometry != self.video_geometry:
            self.video_geometry = geometry
            self.update()

    def geometry_for_sample(self):
        settings = self.settings
        width, height = max(1, settings.region_width), max(1, settings.region_height)
        desktop = settings.capture_source == "desktop"
        if not desktop:
            if self.video_geometry:
                width, height, reference_x, reference_y = self.video_geometry
            else:
                reference_x, reference_y = width / 2, height / 2
        else:
            display = next((display for display in self.displays if display.index == settings.monitor), None)
            if display is None:
                reference_x, reference_y = width / 2 - settings.region_x, height / 2 - settings.region_y
            else:
                left, top, width, height = region_pixels(display, settings.region_x, settings.region_y, width, height)
                reference_x, reference_y = display.width / 2 - left, display.height / 2 - top
        if settings.aim_reference == "crosshair-offset" and (not desktop or not settings.crosshair_enabled):
            reference_x, reference_y = width / 2 + settings.aim_reference_x, height / 2 + settings.aim_reference_y

        # Qt lays out in logical units. At actual size, each capture pixel
        # occupies one physical display pixel, including on a scaled desktop.
        scale = 1 / self.devicePixelRatioF()
        if self.fit_capture:
            scale = min(self.width() / width, self.height() / height)
        frame = QRectF(0, 0, width * scale, height * scale)
        frame.moveCenter(QRectF(self.rect()).center())
        reference = self.point(frame, reference_x / width, reference_y / height)
        side = min(self.SAMPLE_SIDE_PX, width, height) * scale
        art = QRectF(0, 0, side, side)
        art.moveCenter(frame.center())
        # Bounds of the skeleton in the square image, with room for the box outline.
        box = QRectF(self.point(art, 410 / 1254, 108 / 1254),
                     self.point(art, 827 / 1254, 1192 / 1254))
        return frame, art, box, reference, scale

    @staticmethod
    def point(box: QRectF, x: float, y: float) -> QPointF:
        return QPointF(box.left() + x * box.width(), box.top() + y * box.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        frame, art, box, reference, scale = self.geometry_for_sample()
        painter.setClipRect(frame)
        painter.drawImage(art, self.artwork)
        painter.setPen(QPen(QColor("#80bcf2"), 1.7))
        painter.drawRect(box)
        painter.setPen(QPen(QColor("#d1dce8"), 1))
        painter.drawLine(reference - QPointF(5, 0), reference + QPointF(5, 0))
        painter.drawLine(reference - QPointF(0, 5), reference + QPointF(0, 5))
        for field, _label, color in self.ZONES:
            radius = getattr(self.settings, field) * scale
            if radius <= 0:
                continue
            style = Qt.PenStyle.SolidLine if field == "snap_radius" else Qt.PenStyle.DashLine
            painter.setPen(QPen(QColor(color), 1, style))
            if self.settings.fov_shape == "rectangle":
                painter.drawRect(QRectF(reference.x() - radius, reference.y() - radius, 2 * radius, 2 * radius))
            else:
                painter.drawEllipse(reference, radius, radius)
        x, y = anchor_point(self.settings.anchor_mode, self.settings.anchor_x, self.settings.anchor_y)
        marker = self.point(box, x, y)
        dot = QPen(QColor("#b3ef88"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        dot.setCosmetic(True)
        painter.setPen(dot)
        painter.drawPoint(marker)
        painter.end()

    def pick(self, position):
        _, _, box, _, _ = self.geometry_for_sample()
        x = max(0.0, min(1.0, (position.x() - box.x()) / box.width()))
        y = max(0.0, min(1.0, (position.y() - box.y()) / box.height()))
        self.picked.emit(*anchor_offsets(self.settings.anchor_mode, x, y))

    def mousePressEvent(self, event):
        _, _, box, _, _ = self.geometry_for_sample()
        if event.button() == Qt.MouseButton.LeftButton and box.contains(event.position()):
            self.dragging = True
            self.pick(event.position())

    def mouseMoveEvent(self, event):
        if self.dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.pick(event.position())

    def mouseReleaseEvent(self, event):
        self.dragging = False


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
