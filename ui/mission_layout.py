"""Responsive native task groups; all controls remain owned by their callers."""
from PySide6.QtCore import QSize, QRect, Qt, QEvent, QTimer
from PySide6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, QStackedWidget, QLayout, QLabel, QToolButton,QScrollArea,QTextEdit,QSizePolicy,QStyle
from PySide6.QtGui import QTextDocument,QTextOption
import math

class WrappedPath(QTextEdit):
    """Fully visible, selectable plain text, including unbroken path components."""
    def __init__(self,text='',parent=None):
        super().__init__(parent)
        self.setObjectName('wrappedPath')
        self.setReadOnly(True);self.setTabChangesFocus(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet('QTextEdit#wrappedPath {border:0; padding:0; background:transparent;}')
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.document().setDocumentMargin(0)
        policy=QSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True);self.setSizePolicy(policy)
        self.setText(text)
    def setText(self,text):
        self.setPlainText(text);self.setAccessibleName(text)
        self._fit()
    def text(self):return self.toPlainText()
    def hasHeightForWidth(self):return True
    def heightForWidth(self,width):
        document=QTextDocument()
        document.setDefaultFont(self.font());document.setDocumentMargin(0)
        option=document.defaultTextOption();option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        document.setDefaultTextOption(option);document.setPlainText(self.toPlainText())
        document.setTextWidth(max(1,width))
        return math.ceil(document.size().height())+2
    def sizeHint(self):return QSize(200,self.heightForWidth(200))
    def minimumSizeHint(self):return QSize(0,self.fontMetrics().height()+2)
    def _fit(self):
        height=self.heightForWidth(self.viewport().width())
        if height!=self.minimumHeight():self.setMinimumHeight(height);self.updateGeometry()
    def resizeEvent(self,event):
        super().resizeEvent(event);self._fit()
    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):self._fit()

class InventoryScroll(QScrollArea):
    """A long inventory gets available space without imposing Qt's 480px hint."""
    def sizeHint(self):return QSize(320,120)

def option_scroll(content, name):
    """One bounded owner for a long inventory; surrounding actions stay outside."""
    from PySide6.QtWidgets import QScrollArea,QSizePolicy
    scroll=InventoryScroll();scroll.setObjectName('settingsTabScroll')
    scroll.setAccessibleName(name);scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setMinimumSize(0,100)
    scroll.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
    scroll.setWidget(content)
    content.setAutoFillBackground(False)
    scroll.viewport().setAutoFillBackground(False)
    return scroll

class FitColumn(QFrame):
    """Report wrapped minimum height, not the preferred height of flexible panes."""
    def hasHeightForWidth(self):return True
    def heightForWidth(self,width):
        layout=self.layout()
        return max(self.minimumSizeHint().height(),layout.minimumHeightForWidth(width)) if layout else 0

class FitVBox(QVBoxLayout):
    def heightForWidth(self,width):
        # Do not call QBoxLayout.minimumHeightForWidth from this virtual: Qt
        # can re-enter heightForWidth while computing its own cached hints.
        margins=self.contentsMargins();available=max(0,width-margins.left()-margins.right())
        items=[self.itemAt(index) for index in range(self.count()) if not self.itemAt(index).isEmpty()]
        total=margins.top()+margins.bottom()+max(0,len(items)-1)*max(0,self.spacing())
        for item in items:
            height=item.minimumSize().height()
            widget=item.widget()
            if isinstance(widget,(FitColumn,CurrentPageStack)):
                height=max(height,widget.heightForWidth(available))
            elif widget is not None and isinstance(widget.layout(),FitVBox):
                height=max(height,widget.layout().heightForWidth(available))
            elif widget is not None and widget.hasHeightForWidth():
                height=max(height,widget.heightForWidth(available))
            elif item.hasHeightForWidth():height=max(height,item.minimumHeightForWidth(available))
            total+=max(0,height)
        return total
    def minimumHeightForWidth(self,width):return self.heightForWidth(width)

class InventoryContent(FitColumn):
    def event(self,event):
        result=super().event(event)
        if event.type() in (QEvent.Type.LayoutRequest,QEvent.Type.Resize) and self.layout():
            self.setMinimumHeight(self.layout().heightForWidth(self.contentsRect().width()))
        return result

class InventoryLayout(FitVBox):
    """Place complete inventory cards at their current wrapped heights."""
    def setGeometry(self,rect):
        QLayout.setGeometry(self,rect)
        margins=self.contentsMargins();width=max(0,rect.width()-margins.left()-margins.right())
        y=rect.y()+margins.top()
        for index in range(self.count()):
            item=self.itemAt(index)
            if item.isEmpty():continue
            widget=item.widget();height=item.minimumSize().height()
            if widget is not None and widget.hasHeightForWidth():height=max(height,widget.heightForWidth(width))
            elif item.hasHeightForWidth():height=max(height,item.heightForWidth(width))
            item.setGeometry(QRect(rect.x()+margins.left(),y,width,height))
            y+=height+max(0,self.spacing())

def bound_inventory(layout,name,keep=0):
    content=InventoryContent();body=InventoryLayout(content);body.setContentsMargins(0,0,0,0)
    while layout.count()>keep:
        item=layout.takeAt(keep)
        # addItem(QWidgetItem) does not move the widget into the new parent.
        # Use the ownership-aware APIs or cards paint over the pinned controls.
        if item.widget() is not None:body.addWidget(item.widget())
        elif item.layout() is not None:body.addLayout(item.layout())
        else:body.addItem(item)
    scroll=option_scroll(content,name);layout.addWidget(scroll,1)
    return scroll

class TaskDetails(QWidget):
    """Keep advanced existing controls available without obscuring primary tasks."""
    def __init__(self,title,parent=None):
        super().__init__(parent)
        column=QVBoxLayout(self);column.setContentsMargins(0,0,0,0);column.setSpacing(8)
        button=QToolButton();button.setText(title);button.setCheckable(True)
        button.setObjectName('missionDetails')
        button.setStyleSheet('QToolButton#missionDetails {min-width:0; max-width:16777215px; padding:6px 10px;}')
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setArrowType(Qt.ArrowType.RightArrow)
        content=QWidget();self.body=QVBoxLayout(content);self.body.setContentsMargins(0,0,0,0)
        column.addWidget(button);column.addWidget(content);content.hide()
        button.toggled.connect(content.setVisible)
        button.toggled.connect(lambda checked:button.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        self.toggle_button=button
    def reveal(self):self.toggle_button.setChecked(True)

class MetricGrid(QWidget):
    """Four compact metrics across when the available text width permits it."""
    def __init__(self, cards, parent=None):
        super().__init__(parent);self.cards=cards;self.columns=0
        grid=QGridLayout(self);grid.setContentsMargins(0,0,0,0);grid.setSpacing(12)
        self.arrange()
    def arrange(self):
        width=max(180,max(card.minimumSizeHint().width() for card in self.cards))
        columns=4 if self.width()>=4*width+36 else 2 if self.width()>=2*width+12 else 1
        if columns==self.columns:return
        grid=self.layout()
        for card in self.cards:grid.removeWidget(card)
        for index in range(4):grid.setColumnStretch(index,1 if index<columns else 0)
        for index,card in enumerate(self.cards):grid.addWidget(card,index//columns,index%columns)
        self.columns=columns
    def resizeEvent(self,event):
        super().resizeEvent(event);self.arrange()

class ActionFlow(QLayout):
    """Wrap complete controls without shrinking their translated captions."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items=[]
        self.setContentsMargins(0,0,0,0);self.setSpacing(8)
    def addItem(self,item):self._items.append(item)
    def count(self):return len(self._items)
    def itemAt(self,index):return self._items[index] if 0<=index<len(self._items) else None
    def takeAt(self,index):return self._items.pop(index) if 0<=index<len(self._items) else None
    def expandingDirections(self):return Qt.Orientation(0)
    def hasHeightForWidth(self):return True
    def heightForWidth(self,width):return self._arrange(QRect(0,0,width,0),False)
    def minimumHeightForWidth(self,width):return self.heightForWidth(width)
    def minimumSize(self):
        size=QSize()
        for item in self._items:
            if not item.isEmpty():size=size.expandedTo(self.itemSize(item))
        return size
    @staticmethod
    def itemSize(item):return item.sizeHint().expandedTo(item.minimumSize())
    def naturalWidth(self):
        items=[item for item in self._items if not item.isEmpty()]
        return sum(self.itemSize(item).width() for item in items)+max(0,len(items)-1)*self.spacing()
    def sizeHint(self):return self.minimumSize()
    def setGeometry(self,rect):
        super().setGeometry(rect);self._arrange(rect,True)
    def _arrange(self,rect,place):
        x=rect.x();y=rect.y();height=0;gap=self.spacing()
        for item in self._items:
            if item.isEmpty():continue
            size=self.itemSize(item)
            if x>rect.x() and x+size.width()>rect.x()+rect.width():
                x=rect.x();y+=height+gap;height=0
            if place:item.setGeometry(QRect(x,y,size.width(),size.height()))
            x+=size.width()+gap;height=max(height,size.height())
        return y-rect.y()+height


class MeasuredHeaderLayout(QHBoxLayout):
    """The coordinator sets the measured header height after child reflow.

    Do not expose QBoxLayout's second, cached height-for-width result to the
    outer workspace column. Child ActionFlows keep their own wrapping contract.
    """
    def hasHeightForWidth(self):return False

class ActionRow(QWidget):
    """Reserve the full wrapped action height even in an aligned parent layout."""
    def event(self,event):
        result=super().event(event)
        if event.type() in (QEvent.Type.Resize,QEvent.Type.LayoutRequest) and self.layout():
            self.setMinimumHeight(self.layout().heightForWidth(self.contentsRect().width()))
        return result

class ElidedLabel(QLabel):
    """Accessible full text with an explicit ellipsis in compact headers."""
    def setText(self,text):
        self._full_text=text;self.setAccessibleName(text);self._elide()
    def resizeEvent(self,event):
        super().resizeEvent(event);self._elide()
    def _elide(self):
        super().setText(self.fontMetrics().elidedText(getattr(self,'_full_text',''),Qt.TextElideMode.ElideRight,self.contentsRect().width()))

class TaskColumns(FitColumn):
    def __init__(self, parent=None, proportions=(2,3)):
        super().__init__(parent)
        self.setObjectName('missionTaskColumns')
        self.proportions=proportions
        row=QHBoxLayout(self);row.setContentsMargins(0,0,0,0);row.setSpacing(16)
        self.left=QFrame();self.right=QFrame()
        self.left.setObjectName('missionTaskPanel');self.right.setObjectName('missionTaskPanel')
        self.left_layout=FitVBox(self.left);self.right_layout=FitVBox(self.right)
        for inner in (self.left_layout,self.right_layout):
            inner.setContentsMargins(0,0,0,0);inner.setSpacing(12)
        row.addWidget(self.left,proportions[0]);row.addWidget(self.right,proportions[1])
    def minimumSizeHint(self):return QSize(0,super().minimumSizeHint().height())
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.arrange()
    def arrange(self):
        # Measured minimum controls take precedence over the nominal ratio.
        needed=sum(max(300, w.minimumSizeHint().width()) for w in (self.left,self.right))+16
        stacked=self.width()<max(640,needed)
        row=self.layout();row.setDirection(QHBoxLayout.Direction.TopToBottom if stacked else QHBoxLayout.Direction.LeftToRight)
        for i in range(2):row.setStretch(i,0 if stacked else self.proportions[i])

class TaskCard(FitColumn):
    """A named task group containing the caller's existing controls."""
    def __init__(self, title, symbol=None, parent=None, *, layout_type=FitVBox):
        super().__init__(parent)
        self.setObjectName('missionTaskCard')
        self.body=layout_type(self)
        self.body.setContentsMargins(18,18,18,18);self.body.setSpacing(10)
        heading=QHBoxLayout()
        if symbol:
            glyph=QLabel();glyph.setProperty('missionGlyph',symbol)
            glyph.setFixedSize(30,30);glyph.setAccessibleName(title)
            heading.addWidget(glyph)
        label=QLabel(title);label.setObjectName('missionCardTitle');label.setWordWrap(True)
        heading.addWidget(label,1)
        self.body.addLayout(heading)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.setMinimumHeight(self.body.heightForWidth(self.contentsRect().width()))
    def event(self,event):
        result=super().event(event)
        if event.type()==QEvent.Type.LayoutRequest and hasattr(self,'body'):
            # Child controls reflow after the parent's first resize. Recompute
            # then, otherwise Qt keeps the old height and overlaps adjacent rows.
            self.setMinimumHeight(self.body.heightForWidth(self.contentsRect().width()))
        return result
    def minimumSizeHint(self):
        # A settings editor needs its own minimum width plus both card insets.
        # Returning zero let weighted columns squeeze controls through a pane.
        width=316 if self.findChildren(SettingCard) else 0
        return QSize(width,super().minimumSizeHint().height())

class TaskRegions(QWidget):
    """Review keeps its canvas above side-by-side tools in portrait orientation."""
    def __init__(self, proportions=(28,48,24), parent=None):
        super().__init__(parent);self.proportions=proportions
        row=QGridLayout(self);row.setContentsMargins(0,0,0,0);row.setSpacing(16)
        self.regions=[]
        self.panels=[];self._portrait=None
        for proportion in proportions:
            panel=QWidget();column=FitVBox(panel);column.setContentsMargins(0,0,0,0);column.setSpacing(10)
            self.panels.append(panel);self.regions.append(column)
        self.arrange()
    def minimumSizeHint(self):return QSize(0,super().minimumSizeHint().height())
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.arrange()
    def arrange(self):
        needed=sum(max(limit,panel.minimumSizeHint().width()) for panel,limit in zip(self.panels,(220,400,220)))+32
        portrait=self.width()<needed
        if portrait==self._portrait:return
        self._portrait=portrait;grid=self.layout()
        for panel in self.panels:grid.removeWidget(panel)
        for index in range(3):grid.setColumnStretch(index,0);grid.setRowStretch(index,0)
        if portrait:
            grid.addWidget(self.panels[1],0,0,1,2)
            grid.addWidget(self.panels[0],1,0);grid.addWidget(self.panels[2],1,1)
            grid.setColumnStretch(0,1);grid.setColumnStretch(1,1)
            grid.setRowStretch(0,3);grid.setRowStretch(1,2)
        else:
            for index,(panel,ratio) in enumerate(zip(self.panels,self.proportions)):
                grid.addWidget(panel,0,index);grid.setColumnStretch(index,ratio)
            grid.setRowStretch(0,1)

class CurrentPageStack(QStackedWidget):
    def hasHeightForWidth(self):return True
    def heightForWidth(self,width):
        page=self.currentWidget()
        if page is None:return 0
        if isinstance(page,QScrollArea):return max(100,page.minimumHeight())
        layout=page.layout()
        return max(page.minimumSizeHint().height(),layout.minimumHeightForWidth(width) if layout else 0)
    def minimumSizeHint(self):
        page=self.currentWidget()
        # A bounded option scroller must not force an outer page to grow to its
        # full content height. Non-scroll pages retain their real minimums.
        from PySide6.QtWidgets import QScrollArea
        if isinstance(page,QScrollArea):return QSize(0,80)
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()
    def sizeHint(self):
        page=self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

class SettingCard(FitColumn):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._fit_timer=QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self.arrange)
    def queue_fit(self):
        timer=getattr(self,'_fit_timer',None)
        if timer is not None and not timer.isActive():timer.start(0)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.queue_fit()
    def event(self,event):
        result=super().event(event)
        if event.type() in (QEvent.Type.LayoutRequest,QEvent.Type.FontChange,QEvent.Type.StyleChange,QEvent.Type.Show):
            self.queue_fit()
        return result
    def arrange(self):
        row=self.layout()
        if row is None or row.count()!=3:return
        control=row.itemAt(2).widget()
        if control is None:return
        control.setMinimumHeight(max(control.minimumHeight(),control.sizeHint().height()))
        minimum=control.minimumSizeHint().expandedTo(control.minimumSize())
        if control.layout():minimum=minimum.expandedTo(control.layout().minimumSize())
        # The tuning-lock wrapper must reserve its child's explicit minimum.
        # Otherwise a translated label can squeeze the wrapper below the child.
        if control.minimumWidth()!=minimum.width():control.setMinimumWidth(minimum.width())
        margins=row.contentsMargins()
        accent=row.itemAt(0).widget()
        accent_width=max(accent.minimumWidth(),accent.minimumSizeHint().width())
        needed=(minimum.width()+row.itemAt(1).minimumSize().width()
                +accent_width+2*max(0,row.spacing())
                +margins.left()+margins.right())
        # A vertical inventory scrollbar can change width at this breakpoint.
        # Keep a small style-derived exit margin so reflow cannot toggle it
        # continuously between two equally valid layouts.
        exit_margin=self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)+2
        was_stacked=row.direction()==QHBoxLayout.Direction.TopToBottom
        stacked=self.width()<needed+(exit_margin if was_stacked else 0)
        direction=QHBoxLayout.Direction.TopToBottom if stacked else QHBoxLayout.Direction.LeftToRight
        if row.direction()!=direction:row.setDirection(direction)
        row.itemAt(0).widget().setVisible(not stacked)
        for index,stretch in ((1,0 if stacked else 1),(2,0 if stacked else 2)):
            if row.stretch(index)!=stretch:row.setStretch(index,stretch)
        height=row.minimumHeightForWidth(self.width())
        self.setMinimumHeight(max(56,row.minimumSize().height(),height))
    def minimumSizeHint(self):return QSize(0,super().minimumSizeHint().height())

class ActivityRow(QFrame):
    """Activity controls stay beside their state and reflow only when necessary."""
    def resizeEvent(self,event):
        super().resizeEvent(event)
        row=self.layout()
        if row is None:return
        needed=sum(row.itemAt(index).minimumSize().width() for index in range(row.count()))+60
        stacked=self.width()<needed
        row.setDirection(QHBoxLayout.Direction.TopToBottom if stacked else QHBoxLayout.Direction.LeftToRight)
        for index in (1,2):row.setStretch(index,0 if stacked else index)
