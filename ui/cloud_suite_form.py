"""Compact native .cloud form; normal inputs need no nested inventory scroller."""
from PySide6.QtCore import QSize,Qt,QRect
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QSpinBox,QFileDialog,QProgressBar,QGridLayout,QFrame,QLayout,QToolButton,QStyle,QStyleOptionFrame,QPlainTextEdit,QDialog
from mission_layout import FitColumn,FitVBox,TaskColumns,TaskDetails,ActionFlow,WrappedPath,ElidedLabel

class CompactContext(ElidedLabel):
    """Long selected paths/status cannot impose their text width on the form."""
    def sizeHint(self):return QSize(160,self.fontMetrics().height()+2)
    def minimumSizeHint(self):return QSize(0,self.fontMetrics().height()+2)

class TrainingCard(FitColumn):
    """Include the chosen card's native frame when reserving content space."""
    def heightForWidth(self,width):
        layout=self.layout()
        if layout is None:return super().heightForWidth(width)
        margins=self.contentsMargins()
        content=max(1,width-margins.left()-margins.right())
        return max(self.minimumSizeHint().height(),
            margins.top()+margins.bottom()+layout.minimumHeightForWidth(content))

class NativeFieldFlow(ActionFlow):
    """Measure and place a field with the same current native HFW contract."""
    def _arrange(self,rect,place):
        x=rect.x();y=rect.y();height=0;gap=self.spacing()
        for item in self._items:
            if item.isEmpty():continue
            widget=item.widget()
            if widget is not None:
                size=widget.sizeHint().expandedTo(widget.minimumSizeHint()).expandedTo(widget.minimumSize())
                if widget.hasHeightForWidth():size.setHeight(max(size.height(),widget.heightForWidth(size.width())))
            else:size=item.sizeHint().expandedTo(item.minimumSize())
            if x>rect.x() and x+size.width()>rect.x()+rect.width():
                x=rect.x();y+=height+gap;height=0
            if place:item.setGeometry(QRect(x,y,size.width(),size.height()))
            x+=size.width()+gap;height=max(height,size.height())
        return y-rect.y()+height

class FormField(QWidget):
    """Keep complete native label/guard heights in the enclosing flow."""
    def hasHeightForWidth(self):return True
    def heightForWidth(self,width):
        layout=self.layout()
        if layout is None:return 0
        margins=layout.contentsMargins();available=max(1,width-margins.left()-margins.right())
        rows=[layout.itemAt(i) for i in range(layout.count()) if not layout.itemAt(i).isEmpty()]
        heights=[]
        for item in rows:
            h=max(item.sizeHint().height(),item.minimumSize().height())
            widget=item.widget()
            if widget is not None:
                widget.ensurePolished()
                h=max(h,widget.minimumHeight(),widget.minimumSizeHint().height(),widget.sizeHint().height())
                cm=widget.contentsMargins()
                if isinstance(widget,QLabel):
                    h=max(h,widget.fontMetrics().height()+cm.top()+cm.bottom())
                inner=widget.layout()
                if inner is not None and inner.count()==1 and inner.itemAt(0).widget() is not None:
                    child=inner.itemAt(0).widget();child.ensurePolished();lm=inner.contentsMargins()
                    native=max(child.minimumHeight(),child.minimumSizeHint().height(),child.sizeHint().height())
                    h=max(h,native+cm.top()+cm.bottom()+lm.top()+lm.bottom())
            if item.hasHeightForWidth():h=max(h,item.heightForWidth(available))
            heights.append(h)
        return margins.top()+margins.bottom()+max(heights,default=0)
    def sizeHint(self):
        size=super().sizeHint();size.setHeight(self.heightForWidth(max(1,size.width())));return size
    def minimumSizeHint(self):
        size=super().minimumSizeHint();size.setHeight(self.heightForWidth(max(1,self.width())));return size

def fit_path_edit(edit):
    edit.ensurePolished();option=QStyleOptionFrame();edit.initStyleOption(option)
    text=edit.fontMetrics().horizontalAdvance('model.cloud')+4
    size=edit.style().sizeFromContents(QStyle.ContentsType.CT_LineEdit,option,QSize(text,edit.fontMetrics().height()),edit)
    edit.setMinimumWidth(max(edit.minimumSizeHint().width(),size.width()))

def mount(suite,layout):
    c=suite.center
    strip=QFrame();strip.setObjectName('missionTaskCard')
    body=QVBoxLayout(strip);body.setContentsMargins(10,6,10,6);body.setSpacing(1)
    suite.status=CompactContext();suite.status.setTextFormat(Qt.TextFormat.PlainText);suite.status.setProperty('_vaporlock_i18n_skip',True);body.addWidget(suite.status)
    row=FitColumn();flow=ActionFlow(row)
    suite.check=QPushButton(c.t('Check'));suite.check.setProperty('_vaporlock_i18n_skip',True);suite.check.clicked.connect(suite.preflight)
    suite.train=QPushButton(c.t('Train .cloud'));suite.train.clicked.connect(suite.start)
    suite.stop_button=QPushButton(c.t('Stop .cloud training'));suite.stop_button.clicked.connect(suite.stop)
    for button,symbol in ((suite.check,'search'),(suite.train,'start'),(suite.stop_button,'stop')):button.setProperty('missionIcon',symbol)
    for button in (suite.check,suite.train):flow.addWidget(c._tuning_lock.wrap(button))
    flow.addWidget(suite.stop_button);body.addWidget(row)
    suite.bar=QProgressBar();body.addWidget(suite.bar)
    suite.open_button=QPushButton(c.t('Open output folder'));suite.open_button.clicked.connect(suite.open_output)
    # Results already owns every full output path. Keep the existing folder
    # action outside its text scroller instead of repeating both in Train.
    (suite.workspace.pages['Results'] if hasattr(suite,'workspace') else body).addWidget(suite.open_button)
    suite.open_button.setProperty('missionIcon','folder')
    layout.addWidget(strip)
    card=TrainingCard();card.setObjectName('missionTaskCard');card.body=FitVBox(card);card.body.setContentsMargins(10,4,10,4);card.body.setSpacing(1)
    layout.addWidget(card)
    local=QLabel(c.t('Format: class0 / player'));local.setWordWrap(True);card.body.addWidget(local)
    columns=TaskColumns(proportions=(1,1));card.body.addWidget(columns)
    # The outer task card already groups these fields. The extra styled inner
    # panel borders add native frame insets that the row layout doesn't reserve.
    # Keep plain structural columns with no extra decoration/inset.
    for panel in (columns.left,columns.right):panel.setObjectName('cloudTrainingFields');panel.setFrameShape(QFrame.Shape.NoFrame)
    columns.layout().setSpacing(2)
    columns.left_layout.setSpacing(2);columns.right_layout.setSpacing(2)
    suite.widgets={};suite.input_holders=[];suite.path_edits=[];suite.path_buttons=[]
    def field(parent,label,control):
        holder=FormField();box=QHBoxLayout(holder);box.setContentsMargins(0,0,0,0);box.setSpacing(6)
        title=QLabel(c.t(label));title.setBuddy(control);box.addWidget(title)
        guard=c._tuning_lock.wrap(control);guard.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        box.addWidget(guard);parent.addWidget(holder)
        suite.input_holders.append(holder)
        return holder
    def path(parent,name,label,filter=None,directory=False):
        row=QWidget();line=QHBoxLayout(row);line.setContentsMargins(0,0,0,0);line.setSpacing(6)
        edit=c.text_setting(name);edit.setAccessibleName(c.t(label));suite.path_edits.append(edit)
        button=QToolButton();button.setToolTip(c.t('Browse…'));button.setAccessibleName(c.t('Browse…')+' '+c.t(label))
        from mission_icons import apply as apply_icon
        from cloud_workspace_form import theme_palette
        _,colors=theme_palette(c);apply_icon(button,'folder',colors,getattr(getattr(c,'_live_owner',None),'_scale','1'))
        suite.path_buttons.append(button)
        title=QLabel(c.t(label));title.setWordWrap(True);title.setBuddy(edit);line.addWidget(title)
        button.setProperty('missionIcon','folder')
        def browse():
            from batch4_guard import editable
            if not editable(c):return
            current=getattr(c.settings,name)
            chosen=QFileDialog.getExistingDirectory(c,c.t(label),current) if directory else QFileDialog.getOpenFileName(c,c.t(label),current,filter)[0]
            if chosen:c.change(name,chosen);edit.setText(getattr(c.settings,name))
        button.clicked.connect(browse);line.addWidget(edit,1);line.addWidget(button)
        holder=c._tuning_lock.wrap(row);holder.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        parent.addWidget(holder);suite.input_holders.append(holder);suite.widgets[name]=holder
    if hasattr(suite,'workspace'):
        context=QWidget();context_box=QHBoxLayout(context);context_box.setContentsMargins(0,0,0,0);context_box.setSpacing(6)
        suite.project_context=CompactContext();suite.project_context.setMinimumWidth(0);suite.project_context.setProperty('_vaporlock_i18n_skip',True)
        context_box.addWidget(suite.project_context,1)
        choose=suite.project_choose=QPushButton(c.t('Choose project'));choose.setProperty('missionIcon','folder')
        choose.clicked.connect(lambda:suite.workspace.stack.setCurrentIndex(0));context_box.addWidget(choose)
        columns.left_layout.addWidget(context)
    else:path(columns.left_layout,'cloud_project','Project',directory=True)
    from cloud_workspace_form import ToolSelector
    mode=suite.mode_combo=ToolSelector();mode.setAccessibleName(c.t('Training mode'))
    for label,value in (('From scratch','fresh'),('Initialize from .cloud','initialize'),('Resume .cloudtrain','resume')):mode.addItem(c.t(label),value)
    mode.setCurrentIndex(max(0,mode.findData(c.settings.cloud_mode)))
    mode.currentIndexChanged.connect(lambda _: (c.change('cloud_mode',mode.currentData()),suite.refresh_mode()))
    choices=FitColumn();choice_flow=NativeFieldFlow(choices)
    columns.left_layout.addWidget(choices)
    field(choice_flow,'Mode',mode)
    path(columns.left_layout,'cloud_initial_model','Initial .cloud model','*.cloud (*.cloud)')
    path(columns.left_layout,'cloud_checkpoint','Training checkpoint','*.cloudtrain (*.cloudtrain)')
    suite.fresh_panel=FitColumn();parameters=ActionFlow(suite.fresh_panel);parameters.setSpacing(4)
    suite.input_holders.append(suite.fresh_panel)
    suite.parameter_spins={}
    for name,label,low,high in (('cloud_epochs','Epochs',1,10000),('cloud_patience','Patience',0,10000),('cloud_batch','Batch',0,256)):
        field_row=QWidget();line=QHBoxLayout(field_row);line.setContentsMargins(0,0,0,0);line.setSpacing(4)
        title=QLabel(c.t(label));line.addWidget(title)
        spin=QSpinBox();spin.setRange(low,high);spin.setValue(getattr(c.settings,name));spin.setMinimumWidth(75);spin.setAccessibleName(c.t(label));title.setBuddy(spin)
        suite.parameter_spins[name]=spin
        if name=='cloud_batch':spin.setSpecialValueText(c.t('Automatic'))
        if name=='cloud_patience':spin.setToolTip(c.t('Validation checks without improvement before stopping; 0 disables early stopping.'))
        spin.valueChanged.connect(lambda value,key=name:c.change(key,value))
        guard=c._tuning_lock.wrap(spin);guard.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        line.addWidget(guard);parameters.addWidget(field_row)
    columns.right_layout.addWidget(suite.fresh_panel)
    device=ToolSelector();device.setAccessibleName(c.t('Training device'))
    suite.device_combo=device
    for label,value in (('Automatic','auto'),('CPU','cpu'),('NVIDIA CUDA','cuda')):device.addItem(c.t(label),value)
    if device.findData(c.settings.cloud_device)<0:device.addItem(c.settings.cloud_device,c.settings.cloud_device)
    device.setCurrentIndex(max(0,device.findData(c.settings.cloud_device)))
    device.currentIndexChanged.connect(lambda _:c.change('cloud_device',device.currentData()))
    field(choice_flow,'Device',device)
    device.setToolTip(c.t('DirectML is available for inference; this trainer uses CPU or NVIDIA CUDA.'))
    suite.recipe_button=QPushButton(c.t('Recipe'));suite.recipe_button.setProperty('missionIcon','search')
    suite.recipe_button.clicked.connect(suite.show_recipe)
    suite.recipe_dialog=QDialog(c);suite.recipe_dialog.setModal(False);suite.recipe_dialog.setWindowTitle(c.t('Verified recipe'))
    report=QVBoxLayout(suite.recipe_dialog)
    suite.recipe_label=QPlainTextEdit();suite.recipe_label.setReadOnly(True);suite.recipe_label.setTabChangesFocus(True)
    suite.recipe_label.setAccessibleName(c.t('Verified recipe'));report.addWidget(suite.recipe_label,1)
    suite.recipe_close=QPushButton(c.t('Close'));suite.recipe_close.clicked.connect(suite.recipe_dialog.close);report.addWidget(suite.recipe_close)
    columns.left_layout.addStretch(1);columns.right_layout.addStretch(1)
    destination=suite.destination_details=TaskDetails(c.t('Run destination'));card.body.addWidget(destination)
    destination.layout().removeWidget(destination.toggle_button)
    detail_actions=QWidget();detail_flow=ActionFlow(detail_actions)
    detail_flow.addWidget(destination.toggle_button);detail_flow.addWidget(suite.recipe_button)
    destination.layout().insertWidget(0,detail_actions)
    path(destination.body,'cloud_output','Output folder',directory=True)
    run_row=QWidget();run_layout=QHBoxLayout(run_row);run_layout.setContentsMargins(0,0,0,0);run_layout.setSpacing(6)
    run_edit=c.text_setting('cloud_run_name');run_edit.setMinimumWidth(0);run_edit.setAccessibleName(c.t('Run name'))
    run_label=QLabel(c.t('Run name'));run_label.setBuddy(run_edit);run_layout.addWidget(run_label);run_layout.addWidget(run_edit,1)
    run_guard=c._tuning_lock.wrap(run_row);run_guard.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    destination.body.addWidget(run_guard);suite.input_holders.append(run_guard);suite.widgets['cloud_run_name']=run_guard
    layout.addStretch(1)
    suite.refresh_mode();suite.render()
