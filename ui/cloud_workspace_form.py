"""Candidate native .cloud sub-workspaces; no new global navigation."""
from PySide6.QtCore import Qt,QEvent,QTimer,QSize,QRect
from PySide6.QtGui import QColor,QPalette
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
                              QSpinBox,QLineEdit,QButtonGroup,QMenu,QToolButton,QFrame,QStyle,QStyleOptionComboBox,QStyleOptionButton)
from mission_layout import ActionFlow,CurrentPageStack,InventoryScroll,FitColumn,FitVBox,WrappedPath,ElidedLabel


class ToolSelector(QComboBox):
    """Full current option at native size; the popup keeps all option labels."""
    def __init__(self):
        super().__init__();self._measured_width=None
        self._fit_timer=QTimer(self);self._fit_timer.setSingleShot(True);self._fit_timer.timeout.connect(self.fit_current)
        self.currentIndexChanged.connect(self.queue_fit);self.model().dataChanged.connect(self.queue_fit)

    def queue_fit(self,*_):
        timer=getattr(self,'_fit_timer',None)
        if timer is not None and not timer.isActive():timer.start(0)

    def event(self,event):
        result=super().event(event)
        if event.type() in (QEvent.Type.Polish,QEvent.Type.Show,QEvent.Type.FontChange,QEvent.Type.StyleChange,
                            QEvent.Type.ApplicationFontChange,QEvent.Type.PaletteChange):self.queue_fit()
        return result

    def sizeHint(self):
        size=super().sizeHint()
        if getattr(self,'_measured_width',None) is not None:size.setWidth(self._measured_width)
        return size

    def minimumSizeHint(self):return self.sizeHint()

    def fit_current(self):
        if self.currentIndex()<0:return
        option=QStyleOptionComboBox();self.initStyleOption(option)
        def measured(index):
            text=self.itemText(index);picture=self.itemIcon(index)
            content=self.fontMetrics().horizontalAdvance(text)+(self.iconSize().width()+5 if not picture.isNull() else 0)+4
            option.currentText=text;option.currentIcon=picture
            size=self.style().sizeFromContents(QStyle.ContentsType.CT_ComboBox,option,QSize(content,self.fontMetrics().height()),self)
            option.rect=QRect(0,0,size.width(),max(self.height(),size.height()))
            field=self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,self)
            return size.width()+max(0,content-field.width())
        width=measured(self.currentIndex());popup=max(measured(i) for i in range(self.count()))
        if self._measured_width!=width:
            self._measured_width=width;self.setFixedWidth(width);self.updateGeometry()
        if self.view().minimumWidth()!=popup:self.view().setMinimumWidth(popup)


class FittedActionButton(QPushButton):
    """Reserve actual native caption/icon contents, including literal &&."""
    def sizeHint(self):
        size=super().sizeHint();option=QStyleOptionButton();self.initStyleOption(option)
        option.rect=QRect(0,0,size.width(),size.height())
        content=self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents,option,self)
        text=self.text().replace('&&','\0').replace('&','').replace('\0','&')
        needed=self.fontMetrics().horizontalAdvance(text)+(self.iconSize().width()+4 if not self.icon().isNull() else 0)
        size.setWidth(size.width()+max(0,needed-content.width())+2)
        return size
    def minimumSizeHint(self):return self.sizeHint()


def theme_palette(c):
    from vapor_lock_gui import appearance_palette,DEFAULT_UI_COLORS
    from interface_themes import theme_for
    colors=appearance_palette(c.ui_appearance,True)
    surface=theme_for(c.ui_appearance.layout).canvas if c.ui_appearance.colors==DEFAULT_UI_COLORS else colors['carbon']
    palette=QPalette(c.palette())
    for role in (QPalette.ColorRole.Base,QPalette.ColorRole.Window):palette.setColor(role,QColor(surface))
    for role in (QPalette.ColorRole.Text,QPalette.ColorRole.WindowText,QPalette.ColorRole.ButtonText):palette.setColor(role,QColor(colors['ice']))
    palette.setColor(QPalette.ColorRole.Highlight,QColor(colors['baby']))
    palette.setColor(QPalette.ColorRole.HighlightedText,QColor(colors['button_text']))
    return palette,colors


def mount(owner,layout,train_mount):
    c=owner.center;t=c.t;owner.controls=[];owner.image_controls=[];owner.surfaces=[]
    from mission_icons import icon
    palette,colors=theme_palette(c)
    row=QWidget();flow=ActionFlow(row);group=QButtonGroup(row);group.setExclusive(True)
    stack=CurrentPageStack();owner.stack=stack;owner.pages={};owner.tab_buttons={}
    for index,name in enumerate(('Project','Annotate','Train','Results')):
        button=QPushButton(t(name));button.setCheckable(True);button.setObjectName('secondaryTabButton')
        group.addButton(button,index);flow.addWidget(button)
        owner.tab_buttons[name]=button
        content=FitColumn();box=FitVBox(content);box.setContentsMargins(0,6,0,0);box.setSpacing(8)
        if name=='Annotate':box.setSpacing(4);box.setContentsMargins(0,2,0,0)
        content.setPalette(palette)
        owner.surfaces.append(content)
        if name in ('Annotate','Results'):stack.addWidget(content)
        else:
            scroll=InventoryScroll();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.Shape.NoFrame);scroll.setPalette(palette)
            scroll.viewport().setPalette(palette);scroll.viewport().setAutoFillBackground(True)
            owner.surfaces.extend((scroll,scroll.viewport()))
            scroll.setWidget(content);stack.addWidget(scroll)
        owner.pages[name]=box
        button.clicked.connect(lambda checked=False,i=index:stack.setCurrentIndex(i))
        if index==0:button.setChecked(True)
    stack.currentChanged.connect(lambda index:group.button(index).setChecked(True) if group.button(index) else None)
    layout.addWidget(row);layout.addWidget(stack,1)
    def actions(parent,items):
        host=QWidget();line=ActionFlow(host)
        for name,callback,mutating in items:
            button=QPushButton(t(name));button.setAutoDefault(False);button.clicked.connect(callback);line.addWidget(button)
            icons={'New project':'image','Open annotation project…':'folder','Select training project…':'folder','Open profile project':'folder','Import images…':'upload',
                'Fit image':'expand','Regenerate mask':'refresh','Undo':'undo','Delete target':'trash','No targets':'exclude',
                'Save image draft':'save','Check saved project':'search','Open run folder':'folder','Retry image':'refresh'}
            button.setProperty('missionIcon',icons.get(name,'edit'))
            if mutating:owner.controls.append(button)
            if name in ('Regenerate mask','Undo','Delete target','No targets','Save image draft'):
                owner.image_controls.append(button)
        parent.addWidget(host)
    project=owner.pages['Project']
    project_row=QWidget();project_flow=ActionFlow(project_row)
    owner.project_actions=FittedActionButton(t('Project actions'));owner.project_actions.setAutoDefault(False)
    owner.project_actions.setProperty('missionIcon','folder');project_menu=owner.project_menu=QMenu(owner.project_actions)
    for label,callback in (('New project',owner.create_project),('Open annotation project…',owner.open_project),
                           ('Select training project…',owner.select_training_project),('Open profile project',owner.open_profile_project)):
        action=project_menu.addAction(t(label));symbol='image' if label=='New project' else 'folder'
        action.setProperty('missionIcon',symbol);action.setIcon(icon(symbol,colors['ice'],colors['baby'],colors['steel']))
        action.triggered.connect(callback);owner.controls.append(action)
    owner.project_actions.setMenu(project_menu);owner.controls.append(owner.project_actions);project_flow.addWidget(owner.project_actions)
    owner.import_button=FittedActionButton(t('Import images…'));owner.import_button.setAutoDefault(False)
    owner.import_button.setProperty('missionIcon','upload');owner.import_button.clicked.connect(owner.import_images)
    owner.controls.append(owner.import_button);project_flow.addWidget(owner.import_button);project.addWidget(project_row)
    owner.project_path=WrappedPath();project.addWidget(owner.project_path)
    owner.project_summary=QLabel();owner.project_summary.setProperty('_vaporlock_i18n_skip',True)
    owner.project_summary.setWordWrap(True);project.addWidget(owner.project_summary)
    import_row=QWidget();import_layout=QVBoxLayout(import_row);import_layout.setContentsMargins(0,0,0,0);import_layout.setSpacing(3)
    import_layout.addWidget(QLabel(t('Import split')));owner.split=QComboBox()
    for name in ('train','val','test'):owner.split.addItem(t({'train':'Training','val':'Validation','test':'Test'}[name]),name)
    owner.split.setCurrentIndex(max(0,owner.split.findData(c.settings.cloud_import_split)))
    owner.split.currentIndexChanged.connect(lambda _:c.change('cloud_import_split',owner.split.currentData()))
    import_layout.addWidget(owner.split);owner.controls.append(owner.split)
    project.addWidget(import_row)
    device_row=QWidget();device_layout=QVBoxLayout(device_row);device_layout.setContentsMargins(0,0,0,0);device_layout.setSpacing(3)
    device_label=QLabel(t('Mask assistant device'));device_layout.addWidget(device_label)
    owner.sam_device=QComboBox();owner.sam_device.addItem(t('SAM: CPU'),'cpu');owner.sam_device.addItem(t('SAM: NVIDIA CUDA'),'cuda')
    owner.sam_device.setCurrentIndex(max(0,owner.sam_device.findData(c.settings.cloud_annotation_device)))
    owner.sam_device.setAccessibleName(t('Mask assistant device'));device_label.setBuddy(owner.sam_device)
    owner.sam_device.currentIndexChanged.connect(owner.device_changed);device_layout.addWidget(owner.sam_device,1)
    owner.controls.append(owner.sam_device);project.addWidget(device_row)
    note=QLabel(t('Images are copied into this project. Originals are kept. Each import batch stays together in one dataset split.'))
    note.setWordWrap(True);project.addWidget(note);project.addStretch(1)
    annotate=owner.pages['Annotate']
    queue=QWidget();queue_layout=QHBoxLayout(queue);queue_layout.setContentsMargins(0,0,0,0)
    owner.image_choice=QComboBox();owner.image_choice.setMinimumWidth(0);owner.image_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    owner.image_choice.setAccessibleName(t('Image queue'));owner.image_choice.activated.connect(owner.choose_image)
    owner.controls.append(owner.image_choice)
    owner.target_choice=QComboBox();owner.target_choice.setProperty('_vaporlock_i18n_skip',True)
    owner.target_choice.setMinimumWidth(0);owner.target_choice.setAccessibleName(t('Selected target'))
    owner.target_choice.activated.connect(owner.choose_target)
    queue_layout.addWidget(owner.image_choice,2);queue_layout.addWidget(owner.target_choice,1);annotate.addWidget(queue)
    counts=QWidget();counts_row=QHBoxLayout(counts);counts_row.setContentsMargins(0,0,0,0)
    owner.image_position=QLabel();owner.target_status_label=ElidedLabel()
    counts_row.addWidget(owner.image_position);counts_row.addWidget(owner.target_status_label,1);annotate.addWidget(counts)
    tools=QWidget();tool_flow=ActionFlow(tools)
    tool_field=QWidget();tool_row=QHBoxLayout(tool_field);tool_row.setContentsMargins(0,0,0,0)
    label=QLabel(t('Tool'));tool_row.addWidget(label)
    owner.tool_combo=ToolSelector();owner.tool_combo.setAccessibleName(t('Annotation tool'));label.setBuddy(owner.tool_combo)
    for name,mode in (('Select','select'),('Box','box'),('Foreground +','foreground'),('Background −','background'),('Brush','brush'),('Erase','erase')):
        symbol={'select':'search','box':'target','foreground':'accept','background':'exclude','brush':'edit','erase':'exclude'}[mode]
        owner.tool_combo.addItem(icon(symbol,colors['ice'],colors['baby'],colors['steel']),t(name),mode)
    owner.tool_combo.setCurrentIndex(owner.tool_combo.findData('box'))
    owner.tool_combo.currentIndexChanged.connect(lambda _:owner.set_tool(owner.tool_combo.currentData()))
    tool_row.addWidget(owner.tool_combo);tool_flow.addWidget(tool_field)
    owner.brush_size=QSpinBox();owner.brush_size.setRange(1,128);owner.brush_size.setValue(8);owner.brush_size.setSuffix(' px')
    owner.brush_size.setAccessibleName(t('Brush radius'));owner.brush_size.valueChanged.connect(owner.set_radius)
    tool_flow.addWidget(owner.brush_size);owner.controls.append(owner.brush_size);owner.brush_size.hide()
    undo=QPushButton();undo.setAccessibleName(t('Undo'));undo.setToolTip(t('Undo'));undo.setProperty('missionIcon','undo')
    undo.setAutoDefault(False);undo.clicked.connect(owner.undo);tool_flow.addWidget(undo)
    owner.controls.append(undo);owner.image_controls.append(undo);owner.undo_button=undo
    menu=owner.actions_menu=QMenu(tools)
    menu.setPalette(palette)
    for name,callback,mutating,image_required,symbol in (
        ('Fit image',lambda:owner.canvas.fit(),False,False,'expand'),
        ('Regenerate mask',owner.regenerate,True,True,'refresh'),
        ('Delete target',owner.delete_target,True,True,'trash'),
        ('No targets',owner.mark_negative,True,True,'exclude'),
        ('Save image draft',owner.save_draft,True,True,'save'),
        ('Retry image',owner.retry_image,True,False,'refresh')):
        action=menu.addAction(icon(symbol,colors['ice'],colors['baby'],colors['steel']),t(name));action.triggered.connect(callback)
        action.setProperty('missionIcon',symbol)
        if mutating:owner.controls.append(action)
        if image_required:owner.image_controls.append(action)
    owner.actions_button=QPushButton(t('Actions'));owner.actions_button.setAutoDefault(False)
    owner.actions_button.setAccessibleName(t('Annotation actions'));owner.actions_button.setMenu(menu)
    menu.addSeparator()
    owner.details_action=menu.addAction(icon('issue',colors['ice'],colors['baby'],colors['steel']),t('Details'))
    owner.details_action.setProperty('missionIcon','issue');owner.details_action.triggered.connect(owner.show_details)
    annotate.addWidget(tools)
    from cloud_annotation_canvas import AnnotationCanvas
    canvas=owner.canvas=AnnotationCanvas();canvas.setPalette(palette)
    canvas.setAccessibleName(t('.cloud annotation image'))
    canvas.boxDrawn.connect(owner.add_box);canvas.maskEdited.connect(owner.mask_edited);canvas.editStarted.connect(owner.edit_started)
    canvas.refinePoint.connect(owner.refine);canvas.approveRequested.connect(owner.approve);canvas.selected.connect(owner.canvas_selected)
    canvas.drawingChanged.connect(lambda drawing:owner.render());canvas.error.connect(owner.show_error)
    canvas.gestureAborted.connect(owner.manual_aborted)
    bottom=QWidget();bottom_flow=ActionFlow(bottom)
    for name,callback in (('Approve image & next',owner.approve),('Skip',owner.skip),('Cancel annotation',owner.cancel),('Previous',owner.previous)):
        button=FittedActionButton(t(name).replace(' & ',' && '));button.setAutoDefault(False);button.clicked.connect(callback);bottom_flow.addWidget(button)
        button.setProperty('missionIcon',{'Previous':'restore','Skip':'start','Approve image & next':'accept','Cancel annotation':'stop'}[name])
        if name=='Approve image & next':owner.approve_button=button
        if name=='Cancel annotation':owner.cancel_button=button
        if name in ('Previous','Skip'):owner.controls.append(button)
    annotate.addWidget(bottom)
    status=QWidget();status_row=QHBoxLayout(status);status_row.setContentsMargins(0,0,0,0)
    owner.annotation_status=ElidedLabel();owner.annotation_status.setTextFormat(Qt.TextFormat.PlainText)
    status_row.addWidget(owner.annotation_status,1)
    status_row.addWidget(owner.actions_button);annotate.addWidget(status)
    annotate.addWidget(canvas,1)
    train_mount(owner.pages['Train'])
    results=owner.pages['Results'];text_content=FitColumn();text_layout=FitVBox(text_content);text_layout.setContentsMargins(0,0,0,0)
    owner.results=QLabel();owner.results.setProperty('_vaporlock_i18n_skip',True);owner.results.setWordWrap(True);text_layout.addWidget(owner.results)
    owner.results_paths=WrappedPath();text_layout.addWidget(owner.results_paths);text_layout.addStretch(1)
    owner.results_scroll=InventoryScroll();owner.results_scroll.setWidgetResizable(True);owner.results_scroll.setWidget(text_content)
    owner.results_scroll.setFrameShape(QFrame.Shape.NoFrame);owner.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    for surface in (text_content,owner.results_scroll,owner.results_scroll.viewport()):surface.setPalette(palette);owner.surfaces.append(surface)
    results.addWidget(owner.results_scroll,1)
    actions(results,[('Check saved project',owner.check_project,True)])
    owner.refresh_document();owner.render()
