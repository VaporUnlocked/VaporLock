"""Native Games and user Profiles cards. No automatic launch or online catalog."""
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QImage,QPixmap
from PySide6.QtWidgets import (QWidget,QFrame,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,
    QPushButton,QLineEdit,QDialog,QDialogButtonBox,QPlainTextEdit,QFormLayout,QFileDialog,QMessageBox,QLayout,QSizePolicy)
from batch4_guard import editable,EditGuard
from profile_library import ProfileLibrary,checked_path,read_bounded,normalize_image,MAX_IMAGE_BYTES,empty_metadata
from vlui_profile_snapshot import read_profile_snapshot,_identity
from official_games import validated_catalog
from visual_geometry_ui import label,dialog_base


def library():
    from vapor_lock_gui import PROFILE_DIRECTORY
    return ProfileLibrary(PROFILE_DIRECTORY)


def confirm_switch(center):
    if not editable(center,'openProfile'):return False
    if not center.dirty:return True
    guard=EditGuard(center,'openProfile')
    answer=QMessageBox.question(center,center.t('Unsaved changes'),
        center.t('Loading another profile discards unsaved settings. Continue?'),
        QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
    return answer==QMessageBox.StandardButton.Discard and guard.current()


def open_card(center,path,identity):
    if not editable(center,'openProfile'):return
    try:
        path=library().profile(path)
        if _identity(path.stat())!=identity:raise ValueError('Profile changed')
        if not confirm_switch(center):return
        if _identity(path.stat())!=identity:raise ValueError('Profile changed')
        center.load_profile_path(path)
    except (OSError,ValueError):
        center.log('WARN',center.t('Profile changed or unavailable. Refresh the library.'))


def metadata_token(lib,path):
    target=lib.meta_path(path)
    try:
        info=target.stat()
        return (_identity(info),hashlib.sha256(read_bounded(target,8192)).digest() if info.st_size<=8192 else None)
    except FileNotFoundError:return None


def edit_card(center,path):
    if not editable(center,'saveProfile'):return
    lib=library()
    try:
        path=lib.profile(path);snapshot=read_profile_snapshot(path);token=metadata_token(lib,path)
        original=lib.read(path)
    except (OSError,ValueError):
        center.log('WARN',center.t('Profile changed or unavailable. Refresh the library.'));return
    guard=EditGuard(center,'saveProfile')
    dialog,layout=dialog_base(center,'Profile card','profileCardEditor')
    # Card metadata does not affect the capture pipeline; stopped-only copy is not applicable.
    first=layout.takeAt(0).widget();first.deleteLater()
    layout.addWidget(label(center.tf('Editing card: {name}. This does not change the active profile or engine settings.',name=path.name)))
    form=QFormLayout();layout.addLayout(form)
    description=QPlainTextEdit(original['description']);description.setObjectName('cardDescription');description.setMaximumHeight(120)
    form.addRow(center.t('Description (600 characters)'),description)
    tags=QLineEdit(', '.join(original['tags']));tags.setMaxLength(400);tags.setObjectName('cardTags')
    form.addRow(center.t('Tags (comma separated, up to 12)'),tags)
    image_status=label(center.t('Custom image' if original['image'] else 'No custom image'));layout.addWidget(image_status)
    actions=QHBoxLayout();layout.addLayout(actions)
    choose=QPushButton(center.t('Choose image…'));choose.setObjectName('chooseCardImage');actions.addWidget(choose)
    remove=QPushButton(center.t('Remove card image'));remove.setObjectName('removeCardImage');actions.addWidget(remove)
    layout.addWidget(label(center.t('Static PNG/JPEG, up to 8 MiB and 4096 pixels per side. A small copy is stored with your profiles; the original is untouched.')))
    status=label('');status.setObjectName('editStatus');layout.addWidget(status)
    pending=None;image_name=original['image']
    def select():
        nonlocal pending,image_name
        raw,_=QFileDialog.getOpenFileName(dialog,center.t('Choose image…'),'','Images (*.png *.jpg *.jpeg)')
        if not raw:return
        try:
            if str(raw).startswith(('\\\\','//')):raise ValueError('Local images only')
            pending=normalize_image(read_bounded(Path(raw),MAX_IMAGE_BYTES));image_name=''
            image_status.setText(center.t('New image ready to save'))
        except (OSError,ValueError):status.setText(center.t('Image could not be used. Choose a bounded static PNG or JPEG.'))
    def clear():
        nonlocal pending,image_name
        pending=None;image_name='';image_status.setText(center.t('No custom image'))
    choose.clicked.connect(select);remove.clicked.connect(clear)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
    def current():
        return guard.current() and snapshot.matches_path(path) and metadata_token(lib,path)==token
    def save():
        try:
            value=dict(original,description=description.toPlainText(),tags=[t.strip() for t in tags.text().split(',') if t.strip()],image=image_name)
            lib.save(path,value,image_data=pending,guard=current)
        except (OSError,ValueError,TypeError):
            status.setText(center.t('Card could not be saved. Check limits, or close and reopen if the profile changed.'));return
        dialog.accept()
        panel=getattr(center,'profile_cards',None)
        if panel:panel.refresh()
    buttons.accepted.connect(save);buttons.rejected.connect(dialog.reject);dialog.exec()


class CardLibrary(QWidget):
    PAGE_SIZE=12
    def __init__(self,center,*,games=False):
        super().__init__();self.center=center;self.games=games;self.rows=[];self.page=0;self.cards=[];self.columns=0
        self.setObjectName('gamesCards' if games else 'profileCards')
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum)
        top=QHBoxLayout();layout.addLayout(top)
        self.search=QLineEdit();self.search.setObjectName('gameSearch' if games else 'profileSearch')
        self.search.setProperty('missionSearch',True)
        self.search.setProperty('tuningLockExempt',True)
        self.search.setMaxLength(160);self.search.setPlaceholderText(center.t('Search names, descriptions and tags'))
        self.search.setAccessibleName(center.t('Search names, descriptions and tags'));top.addWidget(self.search,1)
        self.refresh_button=QPushButton(center.t('Refresh'));self.refresh_button.clicked.connect(self.refresh);top.addWidget(self.refresh_button)
        self.refresh_button.setProperty('missionIcon','refresh')
        self.empty_panel=QFrame();self.empty_panel.setObjectName('missionTaskPanel')
        empty_layout=QVBoxLayout(self.empty_panel);empty_layout.setContentsMargins(24,32,24,32);empty_layout.setSpacing(16)
        self.empty_icon=QLabel();self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_icon.setProperty('missionIconLabel','games' if games else 'profiles')
        empty_layout.addWidget(self.empty_icon)
        self.status=label('');self.status.setObjectName('cardLibraryStatus');self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.status)
        self.empty_detail=label(center.t('Official game defaults are separate from your own saved profiles.') if games else '')
        self.empty_detail.setAlignment(Qt.AlignmentFlag.AlignCenter);empty_layout.addWidget(self.empty_detail)
        layout.addWidget(self.empty_panel)
        self.body=QWidget();self.grid=QGridLayout(self.body);self.grid.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.grid.setContentsMargins(0,0,0,0);self.grid.setSpacing(16);layout.addWidget(self.body)
        footer=QHBoxLayout();layout.addLayout(footer)
        self.previous=QPushButton(center.t('Previous'));self.next=QPushButton(center.t('Next'))
        footer.addWidget(self.previous);footer.addStretch(1);footer.addWidget(self.next)
        self.previous.clicked.connect(lambda:self.paginate(-1));self.next.clicked.connect(lambda:self.paginate(1))
        self.search.textChanged.connect(self.filter);self.refresh()
    def paginate(self,step):self.page+=step;self.render()
    def filter(self):self.page=0;self.render()
    def refresh(self):
        self.search.setPlaceholderText(self.center.t('Search names, descriptions and tags'))
        self.search.setAccessibleName(self.center.t('Search names, descriptions and tags'))
        self.refresh_button.setText(self.center.t('Refresh'))
        self.previous.setText(self.center.t('Previous'));self.next.setText(self.center.t('Next'))
        try:
            if self.games:self.rows=[(g,None) for g in validated_catalog()]
            else:self.rows=library().list()
            self.error=False
        except (OSError,ValueError,TypeError):self.rows=[];self.error=True
        self.page=0;self.render()
    def resizeEvent(self,event):
        super().resizeEvent(event);self.arrange()
    def arrange(self):
        columns=max(1,min(3,self.width()//300))
        if columns==self.columns:return
        self.columns=columns
        for card in self.cards:self.grid.removeWidget(card)
        for i,card in enumerate(self.cards):self.grid.addWidget(card,i//columns,i%columns)
        for i in range(3):self.grid.setColumnStretch(i,1 if i<columns else 0)
        self.grid.invalidate();self.body.updateGeometry();self.layout().invalidate();self.updateGeometry()
    def render(self):
        for card in self.cards:self.grid.removeWidget(card);card.hide();card.deleteLater()
        self.cards=[];query=self.search.text().casefold().strip()
        def words(row):
            first,meta=row
            return (first['name']+' '+first['description']) if self.games else first.stem+' '+meta['description']+' '+' '.join(meta['tags'])
        rows=[row for row in self.rows if query in words(row).casefold()]
        count=len(rows);self.page=min(max(0,self.page),max(0,(count-1)//self.PAGE_SIZE))
        if self.error:message='Library unavailable or too large. Open profiles individually or reduce the local list to 128.'
        elif not self.rows:message='No official game defaults are available yet.' if self.games else 'No saved profiles. Use Save as to create one.'
        elif not rows:message='No matching profiles or games.'
        else:message=''
        self.status.setText(self.center.t(message));self.status.setVisible(bool(message))
        self.empty_panel.setVisible(bool(message))
        self.empty_detail.setVisible(self.games and not self.error and not self.rows and not query)
        from mission_icons import icon
        from vapor_lock_gui import appearance_palette
        palette=appearance_palette(self.center.ui_appearance,True)
        self.empty_icon.setPixmap(icon('games' if self.games else 'profiles',palette['ice'],palette['baby'],palette['steel']).pixmap(64,64))
        for row in rows[self.page*self.PAGE_SIZE:(self.page+1)*self.PAGE_SIZE]:self.cards.append(self.card(*row))
        self.columns=0;self.arrange()
        self.previous.setEnabled(self.page>0);self.next.setEnabled((self.page+1)*self.PAGE_SIZE<count)
        self.previous.setVisible(count>self.PAGE_SIZE);self.next.setVisible(count>self.PAGE_SIZE)
    def card(self,item,meta):
        c=self.center;frame=QFrame();frame.setObjectName('settingsCard');frame.setMinimumWidth(270)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum)
        layout=QVBoxLayout(frame);layout.setContentsMargins(14,14,14,14);layout.setSpacing(9)
        image=QLabel();image.setAlignment(Qt.AlignmentFlag.AlignCenter);image.setFixedHeight(150)
        image.setMinimumWidth(1);image.setText('')
        image.setObjectName('libraryCardImage');layout.addWidget(image)
        image_data=b'';invalid=False
        try:
            if self.games:image_data=item['image']
            elif meta['image']:image_data=library().image_bytes(meta['image'])
            if image_data:
                normalized=normalize_image(image_data);picture=QImage.fromData(normalized,'PNG')
                image.setPixmap(QPixmap.fromImage(picture).scaled(240,150,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        except (OSError,ValueError):invalid=True
        if not image_data or invalid:
            image.hide()
        title=item['name'] if self.games else item.name
        heading=label(title);heading.setObjectName('missionCardHeading');heading.setStyleSheet('font-size:20px; font-weight:600;');layout.addWidget(heading)
        if self.games:layout.addWidget(label(c.t('Official defaults')))
        if not self.games:
            active=c._active_profile_path is not None and Path(c._active_profile_path).absolute()==item.absolute()
            badge=label(c.t('Active profile' if active else 'Saved profile'));layout.addWidget(badge)
        detail=label(item['description'] if self.games else meta['description']);layout.addWidget(detail)
        if not self.games and meta['tags']:layout.addWidget(label(' · '.join(meta['tags'])))
        if invalid:layout.addWidget(label(c.t('Card artwork is unavailable.')))
        if not self.games:
            try:
                # Differentiate corrupt metadata from a card with no metadata.
                target=library().meta_path(item)
                if target.exists():
                    from profile_library import validate_metadata,unique_json
                    validate_metadata(unique_json(read_bounded(target,8192)))
            except (OSError,ValueError,TypeError,RecursionError):layout.addWidget(label(c.t('Card details are invalid. Edit the card to replace them.')))
        layout.addStretch(1)
        if self.games:
            action=QPushButton(c.t('Create my profile…'));action.setObjectName('createGameProfile')
            action.setProperty('missionIcon','profiles')
            action.setAccessibleName(c.tf('Create profile for {name}',name=item['name']))
            action.clicked.connect(lambda _=False,g=item:create_game_profile(c,g))
            layout.addWidget(c._tuning_lock.wrap(action))
        else:
            try:identity=_identity(item.stat())
            except OSError:identity=None
            action=QPushButton(c.t('Load profile'));action.setObjectName('loadProfileCard')
            action.setProperty('missionIcon','folder')
            action.setAccessibleName(c.tf('Load profile: {name}',name=item.name))
            action.clicked.connect(lambda _=False,p=item,i=identity:open_card(c,p,i))
            edit=QPushButton(c.t('Edit card…'));edit.setObjectName('editProfileCard')
            edit.setProperty('missionIcon','edit')
            edit.setAccessibleName(c.tf('Edit card: {name}',name=item.name))
            edit.clicked.connect(lambda _=False,p=item:edit_card(c,p))
            layout.addWidget(c._tuning_lock.wrap(action));layout.addWidget(c._tuning_lock.wrap(edit))
        from mission_icons import apply as apply_icon
        from vapor_lock_gui import appearance_palette
        for button in frame.findChildren(QPushButton):
            symbol=button.property('missionIcon')
            if symbol:apply_icon(button,symbol,appearance_palette(c.ui_appearance,True))
        return frame


def create_game_profile(center,entry):
    if not editable(center,'openProfile',stopped=True):
        center.log('INFO',center.t('Stop capture and background tasks before creating a game profile.'));return
    from vapor_lock_gui import Settings,PROFILE_DIRECTORY
    try:
        # Only entries actually present in the bundled catalog may become official defaults.
        catalog=validated_catalog();entry=next(g for g in catalog if g==entry)
        settings=Settings(profile_name=entry['name'])
        for key,value in entry['settings'].items():setattr(settings,key,value)
        data=json.dumps(asdict(settings),ensure_ascii=False,allow_nan=False).encode('utf-8')
        center.prepare_profile_bytes(data)
    except (ValueError,TypeError,StopIteration):return
    guard=EditGuard(center,'openProfile',stopped=True)
    dialog,layout=dialog_base(center,'Create game profile','gameProfileDialog')
    name=entry['id']+'-'+uuid4().hex[:8]+'.json'
    layout.addWidget(label(center.tf('New user profile: {name}',name=name)))
    layout.addWidget(label(center.t('Starts from fresh defaults, then applies the settings below. Existing profiles are preserved. Choose a model and review capture/output settings afterward; nothing starts automatically.')))
    if center.dirty:layout.addWidget(label(center.t('Loading this profile will discard unsaved workspace settings. Cancel to save them first.')))
    details=QPlainTextEdit();details.setReadOnly(True);details.setPlainText(json.dumps(entry['settings'],ensure_ascii=False,indent=2));layout.addWidget(details)
    status=label('');status.setObjectName('editStatus');layout.addWidget(status)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText(center.t('Create and load profile'))
    def commit():
        temporary=None;target=None
        try:
            if not guard.current() or entry not in validated_catalog():raise ValueError('Changed')
            root=checked_path(PROFILE_DIRECTORY);root.mkdir(parents=True,exist_ok=True)
            target=checked_path(root,name)
            with tempfile.NamedTemporaryFile(dir=root,suffix='.tmp',delete=False) as stream:
                temporary=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
            if not guard.current():raise ValueError('Changed')
            checked_path(root,name);checked_path(root,temporary.name)
            # Hard-link publication is an atomic create-if-absent, never replace.
            os.link(temporary,target);temporary.unlink();temporary=None
        except (OSError,ValueError):
            status.setText(center.t('Profile could not be created. No existing profile was overwritten.'));return
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        try:
            # Publishing our own catalog entry changes only the owner's settings
            # revision. Re-admit that known synchronous change, preserving every
            # authorization/cancellation/selection and captured settings check.
            if guard.owner:
                guard.stamp=replace(guard.stamp,settings_revision=guard.owner.stamp().settings_revision)
            meta=empty_metadata();meta['game_id']=entry['id'];meta['description']=entry['description']
            library().save(target,meta,image_data=normalize_image(entry['image']) if entry['image'] else None,guard=guard.current)
        except (OSError,ValueError):center.log('WARN',center.t('Profile saved; card details could not be saved.'))
        # Existing native immutable snapshot + stop-before-replace ownership.
        if guard.current():center.load_profile_path(target)
        dialog.accept()
        panel=getattr(center,'profile_cards',None)
        if panel:panel.refresh()
    buttons.accepted.connect(commit);buttons.rejected.connect(dialog.reject);dialog.exec()


def games_page(center):
    page,layout=center.new_page('Games','Official game defaults. Create your own profile before customizing.')
    center.game_cards=CardLibrary(center,games=True);layout.addWidget(center.game_cards)
    from mission_layout import ActionFlow
    actions=ActionFlow();layout.addLayout(actions)
    for title,key,symbol in (('Model Library','model','model'),('Capture settings','capture','display'),('Profiles','profiles','profiles')):
        button=QPushButton(center.t(title));button.setProperty('missionIcon',symbol);button.clicked.connect(lambda _=False,k=key:center.show_page(k));actions.addWidget(button)
    layout.addStretch(1);return page


def add_profile_cards(center,layout):
    center.profile_cards=CardLibrary(center);layout.addWidget(center.profile_cards)
