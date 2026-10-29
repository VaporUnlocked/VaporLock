"""Native Review tools. Draft geometry is never a dataset write."""
from copy import deepcopy
from PySide6.QtCore import QPointF, Qt,QEvent
from PySide6.QtGui import QColor,QPainter,QPainterPath,QPen
from PySide6.QtWidgets import QComboBox,QHBoxLayout,QLabel,QPushButton,QVBoxLayout,QWidget
from annotation_labels import polygon_valid


def create_canvas(base,center):
    class PolygonCanvas(base):
        def __init__(self):
            super().__init__();self.mode='box';self.draft=[];self.vertex=-1;self.drag=False
            self._saved=[];self._editing_index=-1;self.status=None
            self.assistance=None;self.draft_kind='polygon'
        def notice(self,text):
            if self.status:self.status.setText(center.t(text))
        def can_edit(self):
            from review_document import allowed
            return allowed(center) and getattr(center,'_review_document',None) is not None
        def annotations(self):return deepcopy(self._annotations)
        def set_review_image(self,image,annotations):
            if self.assistance:self.assistance.cancel()
            self.cancel();super().set_review_image(image,deepcopy(annotations));self._saved=deepcopy(annotations)
        def accept_saved(self,annotations):
            self._annotations=deepcopy(annotations);self._saved=deepcopy(annotations)
            self.draft=[];self._editing_index=-1;self.vertex=-1;self.drag=False;self.update()
            self.notice('Labels saved.')
            if self.assistance:self.assistance.drafting=False
        def restore_saved(self):
            self._annotations=deepcopy(self._saved);self.cancel()
        def has_draft(self):return bool(self.draft or self._pending is not None)
        def set_tool(self,mode):
            if self.has_draft():self.notice('Finish or cancel the current polygon edit first.');return False
            self.mode=mode;self.vertex=-1;self.update();return True
        def cancel(self):
            pending=self.has_draft()
            self.draft=[];self._editing_index=-1;self.vertex=-1;self.drag=False;self._pending=None;self._dragging=False;self.update()
            if pending:self.notice('Edit cancelled. Saved labels are unchanged.')
            self.draft_kind='polygon'
            if self.assistance:self.assistance.drafting=False

        def begin_suggestion(self,item):
            self.mode='assist';self.draft_kind=item['kind'];self._editing_index=-1
            if self.draft_kind=='polygon':self.draft=[QPointF(*p) for p in item['points']]
            else:
                x,y,w,h=item['box'];self.draft=[QPointF(x,y),QPointF(x+w,y),QPointF(x+w,y+h),QPointF(x,y+h)]
            self.vertex=0
            tool=center.findChild(QComboBox,'reviewGeometryTool')
            if tool:tool.blockSignals(True);tool.setCurrentIndex(tool.findData('assist'));tool.blockSignals(False)
            self.notice('Draft vertices: Finish saves; Cancel restores.');self.update()

        def move_vertex(self,point):
            if self.draft_kind=='box':
                opposite=self.draft[(self.vertex+2)%4];x,y=min(point.x(),opposite.x()),min(point.y(),opposite.y())
                x2,y2=max(point.x(),opposite.x()),max(point.y(),opposite.y())
                self.draft=[QPointF(x,y),QPointF(x2,y),QPointF(x2,y2),QPointF(x,y2)]
                self.vertex=(0 if point.x()<=opposite.x() else 1) if point.y()<=opposite.y() else (3 if point.x()<=opposite.x() else 2)
            else:self.draft[self.vertex]=point
        def _emit_annotations(self):
            if not self.can_edit():self.restore_saved();self.notice('Editing is unavailable. Refresh before saving.');return
            super()._emit_annotations()
        def finish(self):
            if self.assistance and self.assistance.drafting and not self.assistance.valid():
                self.assistance.cancel();return
            if not self.can_edit():self.cancel();self.notice('Editing is unavailable. Refresh before saving.');return
            if not self.draft:return
            points=[(p.x(),p.y()) for p in self.draft]
            if not polygon_valid(points) or not polygon_valid([(round(x,6),round(y,6)) for x,y in points]):
                self.notice('Polygon needs distinct, non-crossing vertices and visible area.');return
            if self._editing_index>=0:
                self._annotations[self._editing_index]['points']=deepcopy(self.draft)
                self._selected=self._editing_index
            else:
                if self.draft_kind=='box':
                    a,b=self.draft[0],self.draft[2]
                    self._annotations.append(dict(kind='box',class_id=self._active_class,x=a.x(),y=a.y(),w=b.x()-a.x(),h=b.y()-a.y()))
                else:self._annotations.append(dict(kind='polygon',class_id=self._active_class,points=deepcopy(self.draft)))
                self._selected=len(self._annotations)-1
            self._emit_annotations();self.update()
        def begin_edit(self,index):
            if not 0<=index<len(self._annotations) or self._annotations[index].get('kind')!='polygon':return False
            self._editing_index=index;self._selected=index;self.draft=deepcopy(self._annotations[index]['points'])
            self.draft_kind='polygon'
            self.selection_changed.emit(index);self.notice('Draft vertices: Finish saves; Cancel restores.');return True
        def add_vertex(self):
            if not self.can_edit() or self.mode not in ('edit','assist') or self.draft_kind!='polygon' or len(self.draft)<3 or len(self.draft)>=256:return
            i=max(0,self.vertex);a=self.draft[i];b=self.draft[(i+1)%len(self.draft)]
            self.draft.insert(i+1,(a+b)/2);self.vertex=i+1;self.update()
        def remove_vertex(self):
            if self.draft_kind=='box':return
            if not self.can_edit() or not 0<=self.vertex<len(self.draft):return
            self.draft.pop(self.vertex);self.vertex=min(self.vertex,len(self.draft)-1);self.update()
        def delete_selected(self):
            if not self.can_edit():return
            if self.has_draft():self.notice('Finish or cancel the current polygon edit first.');return
            super().delete_selected()
        def mousePressEvent(self,event):
            if event.button()==Qt.MouseButton.MiddleButton:return super().mousePressEvent(event)
            if not self.can_edit():return
            if self.mode=='box':return super().mousePressEvent(event)
            self.setFocus();point=self._normalized_point(event.position())
            if point is None or event.button()!=Qt.MouseButton.LeftButton:return
            if self.mode=='polygon':
                if len(self.draft)<256:self.draft.append(point);self.vertex=len(self.draft)-1
                self.notice('Draft vertices: Finish saves; Cancel restores.');self.update();return
            if self.mode=='assist' and not self.draft:
                if self.assistance:self.assistance.hover(point);self.assistance.accept()
                return
            if not self.draft:
                if not self.begin_edit(self._selected_at(event.position())):return
            distances=[(self._screen_point(p)-event.position()).manhattanLength() for p in self.draft]
            index=min(range(len(distances)),key=distances.__getitem__)
            if distances[index]<=16:self.vertex=index;self.drag=True
            self.update()
        def mouseMoveEvent(self,event):
            if self.drag and self.can_edit():
                point=self._normalized_point(event.position())
                if point is not None:self.move_vertex(point)
                self.update();return
            if self.mode=='assist' and self.assistance and not self._panning:
                self.assistance.hover(self._normalized_point(event.position()))
            super().mouseMoveEvent(event)
        def leaveEvent(self,event):
            if self.mode=='assist' and self.assistance:self.assistance.hover(None)
            super().leaveEvent(event)
        def mouseReleaseEvent(self,event):
            if self.drag:self.drag=False;return
            if self.mode=='box' or event.button()==Qt.MouseButton.MiddleButton:super().mouseReleaseEvent(event)
        def keyPressEvent(self,event):
            if event.key()==Qt.Key.Key_Escape:self.cancel();event.accept();return
            if self.mode!='box':
                if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):self.finish()
                elif event.key() in (Qt.Key.Key_Backspace,Qt.Key.Key_Delete):self.remove_vertex()
                elif event.key()==Qt.Key.Key_Tab and event.modifiers() & Qt.KeyboardModifier.ControlModifier and self.draft:
                    self.vertex=(self.vertex+1)%len(self.draft);self.update()
                elif self.draft and 0<=self.vertex<len(self.draft) and event.key() in (Qt.Key.Key_Left,Qt.Key.Key_Right,Qt.Key.Key_Up,Qt.Key.Key_Down):
                    if not self.can_edit():return
                    dx={Qt.Key.Key_Left:-1,Qt.Key.Key_Right:1}.get(event.key(),0)/max(1,self._image.width())
                    dy={Qt.Key.Key_Up:-1,Qt.Key.Key_Down:1}.get(event.key(),0)/max(1,self._image.height())
                    p=self.draft[self.vertex];self.move_vertex(QPointF(max(0,min(1,p.x()+dx)),max(0,min(1,p.y()+dy))));self.update()
                else:return super().keyPressEvent(event)
                event.accept();return
            super().keyPressEvent(event)
        def event(self,event):
            if (event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Tab
                    and event.modifiers() & Qt.KeyboardModifier.ControlModifier
                    and getattr(self,'mode','box')!='box' and getattr(self,'draft',None)):
                self.keyPressEvent(event);return True
            return super().event(event)
        def paintEvent(self,event):
            super().paintEvent(event)
            points=self.draft
            suggestion=False
            a=self.assistance
            if not points and self.mode=='assist' and a and 0<=a.selected<len(a.items):
                item=a.items[a.selected];suggestion=True
                if item['kind']=='polygon':points=[QPointF(*p) for p in item['points']]
                else:
                    x,y,w,h=item['box'];points=[QPointF(x,y),QPointF(x+w,y),QPointF(x+w,y+h),QPointF(x,y+h)]
            if not points:return
            painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor('#ffffff' if suggestion else '#ffce64'),2,Qt.PenStyle.DashDotLine if suggestion else Qt.PenStyle.DashLine))
            path=QPainterPath(self._screen_point(points[0]))
            for point in points[1:]:path.lineTo(self._screen_point(point))
            if len(points)>=3:path.closeSubpath()
            painter.drawPath(path)
            if suggestion:
                painter.drawText(self._screen_point(points[0]),center.t('Suggestion'));painter.end();return
            for i,p in enumerate(points):
                painter.setBrush(QColor('#ffffff') if i==self.vertex else QColor('#ffce64'))
                painter.setPen(QPen(QColor('#07111f'),2));painter.drawEllipse(self._screen_point(p),5,5)
            painter.end()
    return PolygonCanvas()


def add_tools(center,layout,canvas):
    from mission_layout import ActionFlow
    panel=QWidget();column=QVBoxLayout(panel);column.setContentsMargins(0,0,0,0);row=ActionFlow();column.addLayout(row)
    mode=QComboBox();mode.setObjectName('reviewGeometryTool');mode.setAccessibleName(center.t('Annotation tool'))
    for title,value in (('Box','box'),('Polygon','polygon'),('Edit vertices','edit'),('Assist','assist')):mode.addItem(center.t(title),value)
    def change(index):
        if not canvas.set_tool(mode.itemData(index)):
            mode.blockSignals(True);mode.setCurrentIndex(mode.findData(canvas.mode));mode.blockSignals(False)
    mode.currentIndexChanged.connect(change);row.addWidget(mode)
    for title,callback in (('Finish',canvas.finish),('Cancel edit',canvas.cancel),('Insert vertex',canvas.add_vertex),('Remove vertex',canvas.remove_vertex)):
        button=QPushButton(center.t(title));button.clicked.connect(callback);row.addWidget(button)
        if title=='Insert vertex':canvas.insert_button=button
        if title=='Remove vertex':canvas.remove_button=button
    canvas.status=QLabel(center.t('Polygon: click to add vertices. Edit: drag vertices; Ctrl+Tab selects, arrows move. Enter finishes; Escape cancels.'))
    canvas.status.setWordWrap(True);canvas.status.setTextFormat(Qt.TextFormat.PlainText);column.addWidget(canvas.status)
    layout.addWidget(center.feature_control(panel));center.review_polygon_tools=panel
    from assist_ui import install
    install(center,layout,canvas)
