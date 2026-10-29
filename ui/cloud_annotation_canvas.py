"""Native source-pixel annotation canvas. Receives decoded pixels, never files.

No project writes, image codecs, worker/model imports or automatic review here.
"""
from copy import deepcopy

from PySide6.QtCore import Qt, QPointF, QRectF, QEvent, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget, QSizePolicy

from cloud_mask_edit import MaskRaster


class AnnotationCanvas(QWidget):
    boxDrawn = Signal(list)
    maskEdited = Signal(str,dict)
    editStarted = Signal(str)
    refinePoint = Signal(str,list,int)
    selected = Signal(str)
    drawingChanged = Signal(bool)
    approveRequested = Signal()
    error = Signal(str)
    gestureAborted = Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('cloudAnnotationCanvas')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(180);self.setMinimumWidth(0)
        self.setAccessibleName('.cloud annotation image')
        self.image=QImage();self.record=None;self.selection=None
        self._mode='box';self.radius=8;self.zoom=1.;self.pan=QPointF()
        self.start=None;self.end=None;self.panning=None;self.stroke=None;self.last=None
        self.overlays={};self._mutable=True;self.prompts={};self.hover=None;self.gesture_target=None
        self.setMouseTracking(True)

    @property
    def mutable(self):return self._mutable

    @mutable.setter
    def mutable(self,value):
        if type(value) is not bool:raise ValueError('Invalid annotation editing admission.')
        if value==self._mutable:return
        self._mutable=value
        if not value:self.abort_gesture();self.update()

    @property
    def mode(self):return self._mode

    @mode.setter
    def mode(self,value):
        if value not in ('box','select','foreground','background','brush','erase'):
            raise ValueError('Invalid annotation tool.')
        if value!=self._mode:self.abort_gesture()
        self._mode=value;self.update()

    def select_target(self,identity):
        if not any(a['id']==identity for a in (self.record or {}).get('annotations',[])):
            raise ValueError('Unknown annotation target.')
        self.abort_gesture();self.selection=identity;self.refresh_selected_mask();self.update()

    @property
    def drawing(self):return self.start is not None or self.stroke is not None

    def set_document(self,image,record,*,reset_view=True):
        if (not isinstance(image,QImage) or image.isNull() or image.width()!=record['width']
                or image.height()!=record['height']):
            raise ValueError('Decoded image does not match the annotation record.')
        self.abort_gesture();self.image=image.copy();self.record=deepcopy(record)
        if not any(a['id']==self.selection for a in record['annotations']):
            self.selection=record['annotations'][0]['id'] if record['annotations'] else None
        self.overlays={}
        self.refresh_selected_mask()
        if reset_view:self.fit()
        self.update()

    def clear(self):
        self.abort_gesture();self.image=QImage();self.record=None;self.selection=None;self.overlays={};self.update()

    def set_annotations(self,record):
        if self.drawing:
            raise ValueError('Finish the current stroke before replacing its canvas state.')
        self.set_document(self.image,record,reset_view=False)

    def fit(self):
        self.abort_gesture()
        self.zoom=1.;self.pan=QPointF();self.update()

    def view(self):
        if self.image.isNull():return 1.,QPointF()
        scale=max(.0001,min((self.width()-16)/self.image.width(),(self.height()-16)/self.image.height()))*self.zoom
        offset=QPointF((self.width()-self.image.width()*scale)/2,
                       (self.height()-self.image.height()*scale)/2)+self.pan
        return scale,offset

    def point(self,position):
        scale,offset=self.view();p=(position-offset)/scale
        return [max(0.,min(float(self.image.width()),p.x())),
                max(0.,min(float(self.image.height()),p.y()))]

    def target(self):
        return next((a for a in (self.record or {}).get('annotations',[]) if a['id']==self.selection),None)

    def refresh_selected_mask(self):
        # Keep all exact masks in the document, but cache only the selected
        # target's display raster. 300 targets must not allocate 300 full images.
        self.overlays={};a=self.target()
        if a and a.get('mask') is not None:
            raster=MaskRaster(self.record['width'],self.record['height'],a['box'],a['mask'])
            self.overlays[a['id']]=self._overlay(raster)

    def _overlay(self,raster):
        color=self.palette().highlight().color();color.setAlpha(115)
        rgba=raster.rgba((color.red(),color.green(),color.blue(),color.alpha()))
        return (QImage(rgba,raster.w,raster.h,raster.w*4,QImage.Format.Format_RGBA8888).copy(),raster.x,raster.y)

    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),self.palette().base())
        if self.image.isNull():return
        scale,offset=self.view();painter.translate(offset);painter.scale(scale,scale)
        painter.drawImage(QPointF(),self.image)
        def outline(draw,width=1.,accent=None,style=Qt.PenStyle.SolidLine):
            # Image pixels have no relationship to chrome text contrast. The
            # paired dark/light halo stays visible over either extreme; the
            # selected accent is additional semantic feedback, never the only edge.
            layers=[(QColor(0,0,0),width+2),(QColor(255,255,255),width)]
            if accent is not None:layers=[(QColor(0,0,0),width+2),(QColor(255,255,255),width+1),(accent,width)]
            for color,size in layers:
                pen=QPen(color,size/scale,style);pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen);draw()
        for a in self.record['annotations']:
            overlay=self.overlays.get(a['id'])
            if overlay and not (self.stroke and a['id']==self.selection):
                painter.drawImage(QPointF(overlay[1],overlay[2]),overlay[0])
            selected=a['id']==self.selection;accent=self.palette().highlight().color() if selected else None
            box=a['box'];rect=QRectF(box[0],box[1],box[2]-box[0],box[3]-box[1])
            outline(lambda:painter.drawRect(rect),2.5 if selected else 1.,accent)
            if a.get('wrapper') and not a.get('mask'):
                from PySide6.QtGui import QPolygonF
                polygon=QPolygonF([QPointF(*p) for p in a['wrapper']]);outline(lambda:painter.drawPolygon(polygon),1.,accent)
        if self.stroke:
            overlay=self._overlay(self.stroke);painter.drawImage(QPointF(overlay[1],overlay[2]),overlay[0])
        if self.start is not None:
            rect=QRectF(QPointF(*self.start),QPointF(*self.end)).normalized()
            outline(lambda:painter.drawRect(rect),1.5,self.palette().highlight().color(),Qt.PenStyle.DashLine)
        for point,label in self.prompts.get(self.selection,[]):
            p=QPointF(*point);r=4/scale
            def marker():
                painter.drawEllipse(p,r,r);painter.drawLine(p+QPointF(-r/2,0),p+QPointF(r/2,0))
                if label:painter.drawLine(p+QPointF(0,-r/2),p+QPointF(0,r/2))
            outline(marker,1.5)
        if self.hover and self.mode in ('brush','erase') and self.target():
            outline(lambda:painter.drawEllipse(QPointF(*self.hover),self.radius,self.radius))

    def mousePressEvent(self,event):
        if self.image.isNull():return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button()==Qt.MouseButton.MiddleButton:
            if not self.drawing:self.panning=event.position()
            return
        if event.button()!=Qt.MouseButton.LeftButton or (not self.mutable and self.mode!='select'):return
        p=self.point(event.position())
        if self.mode=='box':
            self.start=p;self.end=p;self.drawingChanged.emit(True)
        elif self.mode=='select':
            for a in reversed(self.record['annotations']):
                b=a['box']
                if b[0]<=p[0]<b[2] and b[1]<=p[1]<b[3]:
                    self.select_target(a['id']);self.selected.emit(self.selection);break
        elif self.mode in ('foreground','background'):
            if self.target():self.refinePoint.emit(self.selection,p,1 if self.mode=='foreground' else 0)
        elif self.mode in ('brush','erase'):
            a=self.target()
            if a is None:self.error.emit('Select a target before editing its mask.');return
            if a.get('wrapper') and not a.get('mask'):
                self.error.emit('Generate a mask from this box before using the brush. The original polygon has been preserved.');return
            try:
                self.stroke=MaskRaster(self.image.width(),self.image.height(),a['box'],a.get('mask'))
                self.gesture_target=self.selection
                self.editStarted.emit(self.selection);self.drawingChanged.emit(True)
                if not self.mutable or self.stroke is None:return
                self.last=p;self.stroke.stroke(p,p,self.radius,erase=self.mode=='erase')
            except ValueError as exc:self.error.emit(str(exc));self.abort_gesture()
        self.update()

    def mouseMoveEvent(self,event):
        self.hover=self.point(event.position()) if not self.image.isNull() else None
        if self.panning is not None:
            self.pan+=event.position()-self.panning;self.panning=event.position();self.update();return
        if self.start is not None:self.end=self.point(event.position())
        elif self.stroke:
            p=self.point(event.position());self.stroke.stroke(self.last,p,self.radius,erase=self.mode=='erase');self.last=p
        self.update()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.MiddleButton:self.panning=None;return
        if event.button()!=Qt.MouseButton.LeftButton:return
        if not self.mutable:self.abort_gesture();self.update();return
        if self.start is not None:
            end=self.point(event.position());a=self.start;box=[min(a[0],end[0]),min(a[1],end[1]),max(a[0],end[0]),max(a[1],end[1])]
            self.start=None;self.end=None;self.drawingChanged.emit(False)
            if box[2]-box[0]>=1 and box[3]-box[1]>=1:self.boxDrawn.emit(box)
        elif self.stroke:
            try:
                self.stroke.stroke(self.last,self.point(event.position()),self.radius,erase=self.mode=='erase')
                mask=self.stroke.encode();identity=self.gesture_target
                self.stroke=None;self.drawingChanged.emit(False);self.maskEdited.emit(identity,mask)
            except ValueError as exc:
                self.error.emit(str(exc));self.abort_gesture()
        self.update()

    def abort_gesture(self):
        abandoned=self.gesture_target if self.stroke is not None else None
        self.start=None;self.end=None;self.stroke=None;self.last=None;self.panning=None;self.gesture_target=None
        self.drawingChanged.emit(False)
        if abandoned:self.gestureAborted.emit(abandoned)

    def wheelEvent(self,event):
        if self.image.isNull() or self.drawing:return
        source=self.point(event.position());self.zoom=max(1.,min(16.,self.zoom*(1.15 if event.angleDelta().y()>0 else 1/1.15)))
        scale,offset=self.view();self.pan+=event.position()-(offset+QPointF(*source)*scale)
        self.update();event.accept()

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            self.abort_gesture();self.update();event.accept();return
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            if not event.isAutoRepeat() and self.mutable and not self.drawing:self.approveRequested.emit()
            event.accept();return
        super().keyPressEvent(event)

    def resizeEvent(self,event):
        if self.drawing:self.abort_gesture()
        super().resizeEvent(event)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange,QEvent.Type.ApplicationPaletteChange) and hasattr(self,'record'):
            self.refresh_selected_mask();self.update()
