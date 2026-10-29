"""Share the original native command bar between the HTML and native workspaces."""
from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMenu,
                             QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                             QStackedWidget, QStyle, QToolButton, QVBoxLayout, QWidget)
import weakref
from vlui_live_ui_state import LiveUIHistory

from vlui_qt_host import FixedDocumentSession


_NOTICE_CODES = frozenset(('NAVIGATION_REJECTED', 'LOAD_REJECTED', 'FACADE_METAOBJECT_REJECTED',
    'BOOTSTRAP_TIMEOUT', 'BOOTSTRAP_REJECTED', 'CONTEXT_REJECTED', 'RENDERER_TERMINATED',
    'NATIVE_COMMAND_FAILED', 'FACADE_RENAMED', 'FACADE_DESTROYED', 'FACADE_DELETE_REQUESTED',
    'PAGE_DESTROYED', 'PROFILE_DESTROYED_EARLY', 'POPUP_REJECTED', 'AUTHENTICATION_REJECTED',
    'DISPOSAL_PENDING', 'NATIVE_RETIRED'))

class NativeStopButton(QPushButton):
    """Keep emergency Stop available, including focused bare Enter.

    The original engine handlers disable their ordinary Stop button when idle
    or stopping. This shared emergency control must remain usable throughout
    pending Start, renderer failure, worker cancellation and asynchronous close.
    """
    def setEnabled(self, _enabled):
        super().setEnabled(True)

    def setDisabled(self, _disabled):
        super().setDisabled(False)

    def keyPressEvent(self,event):
        if (event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter)
                and event.modifiers() in (Qt.KeyboardModifier.NoModifier,Qt.KeyboardModifier.KeypadModifier)):
            if self.isEnabled() and self.hasFocus() and not event.isAutoRepeat():self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self,event):
        if (event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter)
                and event.modifiers() in (Qt.KeyboardModifier.NoModifier,Qt.KeyboardModifier.KeypadModifier)):
            event.accept()
            return
        super().keyReleaseEvent(event)


class NativeEmergencyStopFilter(QObject):
    """Native Stop for this window and its owned dialogs, including modal ones."""
    def __init__(self, center):
        super().__init__(center)
        self._center = center
        self._application = QApplication.instance()
        if self._application is None:
            raise RuntimeError('NATIVE_APPLICATION_REQUIRED')
        self._application.installEventFilter(self)
        # Keep Stop available throughout pending shutdown. Qt emits destroyed
        # before destroying QObject children, so removal uses this live filter.
        center.destroyed.connect(self.dispose)

    def dispose(self, *_args):
        application, self._application = self._application, None
        if application is not None:
            application.removeEventFilter(self)

    def _owns_widget(self, watched):
        if not isinstance(watched, QWidget):
            return False
        for _ in range(64):
            if watched is self._center:
                return True
            watched = watched.parentWidget()
            if watched is None:
                return False
        return False

    def eventFilter(self, watched, event):
        if (event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
                or not self._owns_widget(watched)
                or event.key() != Qt.Key.Key_F12
                or event.modifiers() != (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
            return False
        event.accept()
        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            self._center.stop_capture()
        return True


class HeaderGeometryObserver(QObject):
    """Measure after Qt commits viewport/font changes, coalescing notifications."""
    def __init__(self, callback, parent):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(callback)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange,
                            QEvent.Type.StyleChange, QEvent.Type.LocaleChange, QEvent.Type.LayoutRequest):
            if not self.timer.isActive(): self.timer.start(0)
        return False


class NativeLiveCoordinator:
    def __init__(self, center, owner, resources, *, html_workspace_enabled=False):
        self._center, self._owner, self._resources = center, owner, resources
        # The A client uses the complete native workspace. The HTML prototype
        # has only a subset of its commands and must not replace it on startup
        # (including when an existing profile selects Smokey Glass). Explicit
        # opt-in is reserved for development callers, never a saved preference.
        self._html_workspace_enabled = html_workspace_enabled
        self._session = None
        self._disposing = None
        self._closing = False
        self._failed = False
        self._observed_revision = None
        self._last_focus = None
        self._ui_history = LiveUIHistory()
        self._recovery_focus = False
        root = center.centralWidget()
        self._layout = root.layout()
        self.deck = center.command_bar
        self.deck.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.start = center.start_button  # original callback and Start gates
        old_stop = center.stop_button
        self.stop = NativeStopButton(old_stop.text(), self.deck)
        self.stop.setObjectName(old_stop.objectName())
        self.stop.setIcon(old_stop.icon())
        self.stop.setToolTip(center.t('Stop') + ' · Ctrl+Shift+F12')
        self.deck.layout().replaceWidget(old_stop, self.stop)
        center.stop_button = self.stop
        old_stop.hide()
        old_stop.deleteLater()
        self.stop.clicked.connect(center.stop_capture)  # exactly one connection

        # Keep the original backdrop and command bar. Only the
        # workspace bodies switch; rejection cannot disable the shared controls.
        self._legacy = QWidget(root)
        self._legacy.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        legacy_layout = QVBoxLayout(self._legacy)
        legacy_layout.setContentsMargins(0, 0, 0, 0)
        legacy_layout.setSpacing(0)
        while self._layout.count() > 1:
            item = self._layout.takeAt(1)
            widget = item.widget()
            if widget is None:
                raise RuntimeError('NATIVE_WORKSPACE_LAYOUT_REQUIRED')
            legacy_layout.addWidget(widget, 1 if widget.objectName() == 'workspaceShell' else 0)
        self._stack = QStackedWidget(root)
        self._stack.addWidget(self._legacy)
        self._layout.addWidget(self._stack, 1)

        # Overflow scrolls within the existing row. Start, Stop and recovery
        # stay pinned at the right at compact widths and 200% text size.
        header_layout = self.deck.layout()
        metadata = QWidget(self.deck)
        metadata.setObjectName('commandMetadata')
        from mission_layout import ActionFlow
        metadata_layout = ActionFlow(metadata)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(header_layout.spacing())
        while header_layout.count() > 3:
            item = header_layout.takeAt(0)
            if item.widget() is not None:
                metadata_layout.addWidget(item.widget())
            elif item.layout() is not None:
                nested = item.layout()
                nested.setParent(None)
                group=QWidget(metadata)
                group.setProperty('headerBreadcrumb',nested.indexOf(center.header_page)>=0)
                group.setLayout(nested)
                metadata_layout.addWidget(group)
        self._header_scroll = QScrollArea(self.deck)
        self._header_scroll.setWidgetResizable(True)
        self._header_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._header_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._header_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._header_scroll.setMinimumWidth(0)
        self._header_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._header_scroll.setStyleSheet('QScrollArea {background:transparent; border:0;}')
        self._header_scroll.viewport().setAutoFillBackground(False)
        self._header_scroll.setWidget(metadata)
        # setWidget enables the content's palette fill; turn it off afterwards
        # so this section shares the command bar's glass background.
        metadata.setAutoFillBackground(False)
        header_layout.insertWidget(0, self._header_scroll, 1)

        self.menu_button = QToolButton(self.deck)
        self.menu_button.setText('⋯')
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.menu = QMenu(self.menu_button)
        self.search_settings=self.menu.addAction(center.t('Search settings'),center.settings_search_button.click)
        self.undo_tuning=self.menu.addAction(center.t('Undo last tuning change'),center.undo_button.click)
        self.workspace = self.menu.addAction(center.t('Open native workspace'), self.open_native)
        self.return_live = self.menu.addAction(center.t('Return to live interface'), self.open_live)
        self.retry = self.menu.addAction(center.t('Reload interface'), self._request_reload)
        for action in (self.workspace, self.return_live, self.retry):
            action.setVisible(self._html_workspace_enabled)
        self.menu.addSeparator()
        self.details_action = self.menu.addAction(center.t('Session details'), self.show_details)
        self.menu_button.setMenu(self.menu)
        self.menu.aboutToShow.connect(self._size_command_row)
        header_layout.addWidget(self.menu_button)
        self._command_actions = QWidget(self.deck)
        self._command_actions.setObjectName('commandActions')
        actions = ActionFlow(self._command_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        metadata_layout.removeWidget(center.save_button)
        actions.addWidget(center.save_button)
        # At this point the original tuning lock precedes Start and Stop.
        lock = header_layout.itemAt(1).widget()
        for control in (lock, self.start, self.stop, self.menu_button):
            actions.addWidget(control)
        header_layout.addWidget(self._command_actions)
        self._header_fit = HeaderGeometryObserver(self._size_command_row, self.deck)
        for widget in (self.deck, self._header_scroll.viewport(), center.profile_quick_switch,
                       center.session_mode_choice, center.model_quick_switch, metadata, self._command_actions):
            widget.installEventFilter(self._header_fit)
        for control in (self.start, self.stop, self.menu_button):
            header_layout.setAlignment(control, Qt.AlignmentFlag.AlignVCenter)

        self.notice = QLabel(root)
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setContentsMargins(16, 6, 16, 6)
        self._layout.insertWidget(2, self.notice)
        self.notice.hide()
        self._details = QDialog(center)
        self._details.setObjectName('nativeSessionDetails')
        self._details.setWindowTitle(center.t('Session details'))
        details_layout = QVBoxLayout(self._details)
        details_body = QWidget(self._details)
        details_body_layout = QVBoxLayout(details_body)
        self.capture_state = QLabel(details_body)
        self.status = QLabel(details_body)
        self.profile = QLabel(details_body)
        for label in (self.capture_state, self.status, self.profile):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                         | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            details_body_layout.addWidget(label)
        details_body_layout.addStretch(1)
        self._details_scroll = QScrollArea(self._details)
        self._details_scroll.setWidgetResizable(True)
        self._details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._details_scroll.viewport().setAutoFillBackground(False)
        self._details_scroll.setWidget(details_body)
        details_layout.addWidget(self._details_scroll)
        self._details.finished.connect(lambda _result: self.menu_button.setFocus()
                                       if not self._closing else None)
        self._stop_filter = NativeEmergencyStopFilter(center)
        self._focus_shortcut = QShortcut(QKeySequence('F6'), center)
        self._focus_shortcut.activated.connect(self.cycle_focus)
        self._reverse_focus_shortcut = QShortcut(QKeySequence('Shift+F6'), center)
        self._reverse_focus_shortcut.activated.connect(self.cycle_focus)
        # Escape has no Stop registration; editors/dialogs retain normal cancel.
        self._refresh_timer = QTimer(center)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._poll_native)
        self._refresh_timer.start()
        owner.bind_display_notifier(self.publish)
        QApplication.instance().focusChanged.connect(self._native_focus_changed)
        if not self._html_workspace_enabled:
            # Keep the native sidebar beside the complete command/workspace
            # column. Move the existing widgets; callbacks and ownership stay
            # attached to the same instances, including the emergency controls.
            column = QWidget(root)
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(0)
            while self._layout.count():
                item = self._layout.takeAt(0)
                column_layout.addWidget(item.widget(), 1 if item.widget() is self._stack else 0)
            body = QWidget(root)
            body_layout = QHBoxLayout(body)
            body_layout.setContentsMargins(0, 0, 0, 0)
            body_layout.setSpacing(0)
            body_layout.addWidget(center.navigation_deck)
            body_layout.addWidget(column, 1)
            self._layout.addWidget(body, 1)
            self._layout = column_layout
        self.apply_scale(owner._scale)
        self.refresh_theme()
        self._stack.setCurrentWidget(self._legacy)
        self.refresh_native()
        if self._html_workspace_enabled:
            self.reload()

    def refresh_theme(self):
        # Resolve the same palette as the native controls, including saved custom
        # colours. Popups stay opaque so text does not depend on the window below.
        from vapor_lock_gui import appearance_palette
        colors = appearance_palette(self._center.ui_appearance, True)
        self.menu.setStyleSheet(
            f'QMenu {{background:{colors["carbon"]}; color:{colors["ice"]}; border:1px solid {colors["border"]};}}'
            'QMenu::item {padding:8px 16px;}'
            f'QMenu::item:selected {{background:{colors["baby"]}; color:{colors["button_text"]};}}'
            f'QMenu::item:disabled {{color:{colors["steel"]};}}')
        self._details.setStyleSheet(
            f'QDialog#nativeSessionDetails {{background:{colors["carbon"]};}}'
            f'QLabel {{color:{colors["ice"]}; background:transparent;}}')

    def refresh_native(self):
        center = self._center
        factor = float(self._owner._scale)
        if self._html_workspace_enabled:
            snapshot = self._owner.read_native_source()
            self._observed_revision = (snapshot.source_revision, snapshot.display_revision)
            state = snapshot.preferences['nativeStatus']['state']
            profile_name = snapshot.preferences['profileName']
        else:
            # Customer native controls do not need an HTML contract projection,
            # filesystem profile scan or model stat on every telemetry tick.
            state = self._owner._status()['state']
            profile_name = self._owner._label(center.settings.profile_name, 'Profile')
        signature = (center.width(), factor, center.ui_appearance.layout, state, profile_name,
                     center.session_mode,
                     self._closing, center._profile_transition_pending, center.engine.active,
                     center._policy_start_pending, bool(center._pending_controller_start),
                     center.xbox_compatible_bridge.preparing, self._disposing is not None,
                     self._failed, self._session, center.t('Start'), center.t('Profile name'),
                     center.t('Observe only'), self.start.isEnabled(), self.stop.isEnabled())
        if not self._html_workspace_enabled and signature == getattr(self, '_native_signature', None):
            return
        self._native_signature = signature
        self.capture_state.setText(center.t('Capture') + ': ' + center.t(state))
        self.status.setText(center.t('Observe only' if center.session_mode == 'observe' else 'Control'))
        profile_text = center.t('Profile name') + ': ' + profile_name
        self.profile.setText(profile_text)
        self.profile.setAccessibleName(profile_text)
        center.profile_quick_switch.setToolTip(profile_text)
        center.profile_quick_switch.setAccessibleDescription(profile_text)
        self.start.setText(center.t('Start'))
        self.stop.setText(center.t('Stop'))
        self.stop.setToolTip(center.t('Stop') + ' · Ctrl+Shift+F12')
        self.stop.setAccessibleDescription('Ctrl+Shift+F12')
        self.workspace.setText(center.t('Open native workspace'))
        self.return_live.setText(center.t('Return to live interface'))
        self.retry.setText(center.t('Reload interface'))
        details_text = center.t('Session details')
        self.details_action.setText(details_text)
        self.menu_button.setToolTip(details_text)
        self.menu_button.setAccessibleName(details_text)
        self._details.setWindowTitle(details_text)
        # Never enable Start over a disabled original native guard.
        if (self._closing or center._profile_transition_pending
                or self._owner._native._NATIVE_INJECTION is not None
                or center.engine.active
                or center._policy_start_pending or center._pending_controller_start
                or center.xbox_compatible_bridge.preparing):
            self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.retry.setEnabled(not self._closing and self._disposing is None)
        self.return_live.setEnabled(not self._closing and not self._failed
            and self._session is not None and self._session.commands_current())
        self.workspace.setEnabled(not self._closing)
        center.session_mode_choice.setEnabled(not self._closing and not center.engine.active
            and not center.xbox_compatible_bridge.preparing and not center._profile_transition_pending
            and not center._policy_start_pending and not center._pending_controller_start)
        self._size_command_row()

    def _size_command_row(self):
        """Reflow complete command groups; no horizontal toolbar scrolling."""
        if self._closing: return
        scroll = self._header_scroll
        center=self._center
        from control_fit import header_combo_size
        for combo, captions, bounded in (
                (center.profile_quick_switch, (center.t('Profile name'), center.t('No saved profiles')), True),
                (center.model_quick_switch, (center.model_quick_switch.itemText(0),), True),
                (center.session_mode_choice, (), False)):
            desired = header_combo_size(combo, captions, bounded_name=bounded)
            combo.setFixedWidth(desired.width())
            combo.setMinimumHeight(desired.height())
            combo.view().setMinimumWidth(desired.width())
        mission=center.ui_appearance.layout=='mission-control'
        # Utilities remain the same guarded controls, reachable through the menu.
        # Give the profile/model/mode/save controls the requested row priority.
        for widget in (center.settings_search_button,center.undo_button,center.status):widget.setVisible(not mission)
        self.search_settings.setText(center.t('Search settings'))
        self.search_settings.setEnabled(center.settings_search_button.isEnabled())
        self.undo_tuning.setText(center.t('Undo last tuning change'))
        self.undo_tuning.setEnabled(center.undo_button.isEnabled())
        self.search_settings.setVisible(mission);self.undo_tuning.setVisible(mission)
        center.save_button.ensurePolished()
        if mission:
            center.save_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            center.save_button.setText(center.t('Save profile'))
        center.save_button.setMinimumWidth(center.save_button.sizeHint().width())
        content = scroll.widget()
        for widget in content.findChildren(QWidget):
            # Selected navigation and the model selector already name this
            # context. Owner's no-duplicate-intro directive applies to all themes.
            if widget.property('headerBreadcrumb'):widget.setVisible(False)
        controls = content.findChildren(QWidget) + [self.start, self.stop, self.menu_button]
        for widget in controls:
            widget.ensurePolished()
        # Include recovery's popup indicator and styled padding.
        self.menu_button.setFixedWidth(max(round(32 * float(self._owner._scale)), self.menu_button.sizeHint().width()))
        margins = self.deck.layout().contentsMargins()
        available=max(1,self.deck.width()-margins.left()-margins.right())
        flow=content.layout()
        natural=flow.naturalWidth()
        action_flow=self._command_actions.layout()
        actions_width=action_flow.naturalWidth()
        stacked=natural+actions_width+self.deck.layout().spacing()>available
        self.deck.layout().setDirection(QHBoxLayout.Direction.TopToBottom if stacked else QHBoxLayout.Direction.LeftToRight)
        # Commit the direction before measuring the viewport. The old estimate
        # could reserve a different number of wrapped rows than Qt actually laid
        # out, clipping the last row at portrait work areas and larger fonts.
        self._command_actions.setFixedWidth(min(available,actions_width))
        self.deck.layout().activate()
        viewport_width=max(1,scroll.viewport().width())
        scroll_height=flow.heightForWidth(viewport_width)+2*scroll.frameWidth()
        scroll.setFixedHeight(scroll_height)
        base = round((78 if self._center.ui_appearance.layout == 'contrast' else 72) * float(self._owner._scale))
        action_width=min(available,actions_width)
        self._command_actions.setFixedWidth(action_width)
        actions_height=action_flow.heightForWidth(action_width)
        self._command_actions.setFixedHeight(actions_height)
        content_height = scroll_height + actions_height + self.deck.layout().spacing() if stacked else max(actions_height, scroll_height)
        height = max(base, margins.top() + margins.bottom() + content_height)
        if self.deck.height() != height:
            self.deck.setFixedHeight(height)
        signature = (height, scroll_height, actions_height, actions_width, stacked, viewport_width)
        if signature != getattr(self, '_header_geometry_signature', None):
            self._header_geometry_signature = signature
            # QWidgetItem caches height-for-width independently of this widget's
            # fixed height. Invalidate the parent when the settled child rows
            # change, including transitions that leave the fixed height equal.
            self.deck.updateGeometry()

    def show_details(self):
        self.refresh_native()
        self._details.show()
        self._details.raise_()
        self._details.activateWindow()
        self.profile.setFocus(Qt.FocusReason.OtherFocusReason)

    def _request_reload(self):
        self._recovery_focus = True
        return self.reload()

    def publish(self):
        self.refresh_native()
        if self._session is not None and not self._closing:
            self._session.publish()

    def _poll_native(self):
        if not self._html_workspace_enabled:
            self.refresh_native()
            return
        source = self._owner.read_native_source()
        if (source.source_revision, source.display_revision) != self._observed_revision:
            self.publish()
        else:
            self.refresh_native()

    def _notice(self, code):
        if code not in _NOTICE_CODES:
            code = 'NATIVE_COMMAND_FAILED'
        if code == 'DISPOSAL_PENDING':
            text = 'Interface cleanup is pending. Native Stop remains available.'
        elif code == 'NATIVE_RETIRED' and not self._closing:
            return
        else:
            self._failed = True
            text = 'The live interface is unavailable. Use native controls or reload it.'
        self.notice.setText(self._center.t(text))
        self.notice.show()
        if self._failed:
            self._stack.setCurrentWidget(self._legacy)
        self.refresh_native()
        self.stop.setFocus(Qt.FocusReason.OtherFocusReason)

    def _disposed(self, session):
        if session is self._disposing:
            self._disposing = None
        if session is self._session:
            self._session = None
        session.deleteLater()  # child cleanup observed before releasing session/timers
        self.refresh_native()

    def reload(self):
        if not self._html_workspace_enabled or self._closing or self._disposing is not None:
            return False
        if self._session is not None:
            old = self._session
            self._disposing, self._session = old, None
            self._stack.removeWidget(old.view)
            old.retire()
            # One disposing owner retained. Recovery needs an explicit retry once
            # it actually closes; never reset Settings, undo or native jobs.
            return False
        self._failed = False
        self.notice.hide()
        session = FixedDocumentSession(resources=self._resources, producer=self._owner.producer,
            native_stamp=self._owner.stamp, native_authorize=self._owner.authorize,
            native_handlers=self._owner.handlers, native_notice=self._notice,
            disposed=self._disposed, parent=self._center, ui_history=self._ui_history,
            recovered=self._recovered)
        observation = session.native_observation()
        if observation['retired'] or session.view is None:
            self._failed = True
            if not observation['closed']:
                self._disposing = session
            self.refresh_native()
            return False
        self._session = session
        self._stack.addWidget(session.view)
        # Constructing/reloading a renderer cannot take over the user's current
        # workspace. Development callers must explicitly open it after load.
        self._stack.setCurrentWidget(self._legacy)
        session.load()
        self.refresh_native()
        return True

    def _recovered(self, session):
        if self._closing or session is not self._session or not session.commands_current():return
        # Only an explicit recovery/Return control may hand focus to the new view.
        # A deliberate native Stop focus is preserved, including failure landing.
        if (self._recovery_focus and self._center.focusWidget() is self.menu_button
                and self._stack.currentWidget() is session.view):
            session.view.setFocus()
            session.restore_live_focus()
        self._recovery_focus = False

    def open_native(self):
        self._stack.setCurrentWidget(self._legacy)
        restored = self._last_focus() if self._last_focus is not None else None
        try:
            if restored is not None and restored.isVisible() and restored.isEnabled() and self._legacy.isAncestorOf(restored):
                restored.setFocus()
                return
        except RuntimeError:
            self._last_focus = None
        self._center.header_page.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._center.header_page.setFocus()

    def _native_focus_changed(self, _old, current):
        if not self._closing and current is not None:
            for viewport in (self._header_scroll, self._details_scroll):
                content = viewport.widget()
                if content is not None and (content is current or content.isAncestorOf(current)):
                    viewport.ensureWidgetVisible(current, 8, 8)
            if self._legacy.isAncestorOf(current):
                self._last_focus = weakref.ref(current)

    def open_live(self):
        if self._closing or self._session is None or self._failed or not self._session.commands_current():
            return
        if self._center.ui_appearance.layout != 'smoky':
            session, stamp = self._session, self._owner.stamp()
            answer = QMessageBox.question(self._center, self._center.t('Return to live interface'),
                self._center.t('Use Smokey Glass for the live interface? Your saved palette remains available.'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return
            if self._session is not session or self._owner.stamp() != stamp:
                return
            index = self._center.interface_layout_choice.findData('smoky')
            self._center.appearance_layout_changed(index)
        if self._session is not None and self._session.commands_current():
            focus = self._center.focusWidget()
            if focus is not None and self._legacy.isAncestorOf(focus):
                self._last_focus = weakref.ref(focus)
            self._stack.setCurrentWidget(self._session.view)
            self._session.view.setFocus()
            self._session.restore_live_focus()

    def cycle_focus(self):
        if self.deck.isAncestorOf(self._center.focusWidget()) or self._center.focusWidget() is self.deck:
            if self._session is not None and self._stack.currentWidget() is self._session.view:
                self._session.view.setFocus()
            else:
                self._legacy.setFocus()
        else:
            self.stop.setFocus()

    def apply_scale(self, scale):
        factor = {'1': 1.0, '1.5': 1.5, '2': 2.0}[scale]
        # The original theme owns the original bar's appearance and height.
        self._center.apply_ui_appearance(save=False)
        font = QFont(self._center.font())
        font.setPointSizeF(10 * factor)
        self._legacy.setFont(font)
        self._details.setFont(font)
        self.menu.setFont(font)
        self.menu_button.setFixedWidth(round(32 * factor))
        self._details.resize(round(520 * factor), round(260 * factor))

    def user_stop(self):
        if self._session is not None:
            self._session.user_stop()

    def begin_close(self):
        self._closing = True
        self._details.close()
        QApplication.instance().focusChanged.disconnect(self._native_focus_changed)
        self._center.stop_capture()
        if self._session is not None:
            self._disposing, self._session = self._session, None
            self._disposing.retire()
        self.refresh_native()

    def disposal_complete(self):
        return self._session is None and self._disposing is None


