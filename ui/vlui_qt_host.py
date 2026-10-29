"""Fixed-document Qt host. Import/construct only under an accepted native run scope.

This module is source preparation; its Qt methods have not been exercised. The
host receives private native owners, never creates engine/license services, and
exports one closed display facade. Native recovery and Stop live outside its view.
"""
from PySide6.QtCore import (QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice,
                           QMetaMethod, QObject, QTimer, Qt, QUrl, Signal, Slot)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile,
    QWebEngineSettings, QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestJob,
    QWebEngineUrlScheme, QWebEngineUrlSchemeHandler)
from PySide6.QtWebEngineWidgets import QWebEngineView

from vlui_host_admission import DOCUMENT, HostAdmission
from vlui_live_commands import NativeCommandLane
from vlui_live_protocol import COMMANDS, CommandRejected, validate_command
from vlui_live_ui_state import LiveUIHistory, decode_ui_state_wire
import json


CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self'; connect-src 'self'; font-src 'none'; object-src 'none'; "
       "frame-src 'none'; child-src 'none'; worker-src 'none'; base-uri 'none'; "
       "form-action 'none'; media-src 'none'; manifest-src 'none'; frame-ancestors 'none'")
_SCHEME_NAME = b'vlapp'
_FACADE_NAME = 'vluiLive'
_BOOTSTRAP = 'globalThis.__vlConnectNativeChannel() === true'
_RESOURCE_TYPES = {0: 'MainFrame', 2: 'Stylesheet', 3: 'Script', 4: 'Image', 13: 'Xhr', 21: 'Xhr'}


def register_fixed_scheme():
    """Call before QApplication and before any WebEngine object; verify every flag."""
    if QCoreApplication.instance() is not None:
        raise ValueError('SCHEME_REGISTRATION_TOO_LATE')
    flags = QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.FetchApiAllowed
    current = QWebEngineUrlScheme.schemeByName(_SCHEME_NAME)
    if not bytes(current.name()):
        current = QWebEngineUrlScheme(_SCHEME_NAME)
        current.setSyntax(QWebEngineUrlScheme.Syntax.Host)
        current.setDefaultPort(-1)
        current.setFlags(flags)
        QWebEngineUrlScheme.registerScheme(current)
        current = QWebEngineUrlScheme.schemeByName(_SCHEME_NAME)
    if (bytes(current.name()) != _SCHEME_NAME or current.syntax() != QWebEngineUrlScheme.Syntax.Host
            or current.defaultPort() != -1 or current.flags() != flags):
        raise ValueError('SCHEME_IDENTITY_REJECTED')


def _url(url):
    return url.toString(QUrl.ComponentFormattingOption.FullyEncoded)


class _Interceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, admission, ticket, parent):
        super().__init__(parent)
        self._admission, self._ticket = admission, ticket

    def interceptRequest(self, info):
        # Chromium may invoke this away from the GUI thread. Only the locked pure
        # policy is touched here; no QObject/controller callback or GUI access.
        if not self._admission.current(self._ticket):
            info.block(True)
            return
        allowed = self._admission.request(self._ticket, _url(info.requestUrl()),
            bytes(info.requestMethod()), _RESOURCE_TYPES.get(info.resourceType().value, 'DENIED'),
            _url(info.firstPartyUrl()), _url(info.initiator()), download=info.isDownload())
        info.block(not allowed)


class _Handler(QWebEngineUrlSchemeHandler):
    def __init__(self, admission, ticket, parent):
        super().__init__(parent)
        self._admission, self._ticket = admission, ticket

    def requestStarted(self, job):
        if not self._admission.current(self._ticket):
            return
        resource = self._admission.resource(self._ticket, _url(job.requestUrl()),
                                            bytes(job.requestMethod()), _url(job.initiator()))
        if resource is None:
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        # Byte map has already checked exact SHA/MIME/size; no filesystem fallback.
        buffer = QBuffer(job)
        buffer.setData(QByteArray(resource.data))
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        job.setAdditionalResponseHeaders({QByteArray(b'Content-Security-Policy'): QByteArray(CSP.encode('ascii')),
            QByteArray(b'X-Content-Type-Options'): QByteArray(b'nosniff'),
            QByteArray(b'Cache-Control'): QByteArray(b'no-store')})
        job.reply(QByteArray(resource.mime.encode('ascii')), buffer)


class _Page(QWebEnginePage):
    def __init__(self, profile, admission, ticket, denied, parent):
        super().__init__(profile, parent)
        self._admission, self._ticket, self._denied = admission, ticket, denied

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if not self._admission.current(self._ticket):
            return False
        allowed = self._admission.navigation(self._ticket, _url(url), main_frame=is_main_frame,
            native_initial=navigation_type in (QWebEnginePage.NavigationType.NavigationTypeTyped,
                                                QWebEnginePage.NavigationType.NavigationTypeOther))
        if not allowed:
            self._denied('NAVIGATION_REJECTED')
        return allowed

    def createWindow(self, window_type):
        return None

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        return []

    def javaScriptAlert(self, security_origin, message):
        return None

    def javaScriptConfirm(self, security_origin, message):
        return False

    def javaScriptPrompt(self, security_origin, message, default_value):
        return False, ''

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        # Renderer-controlled strings are neither native notices nor diagnostics.
        return None


class _Facade(QObject):
    snapshotPublished = Signal(str)
    preferencesPublished = Signal(str)
    commandFinished = Signal(str)

    def __init__(self, invoke, retired, parent):
        super().__init__(parent)
        self._invoke, self._retired = invoke, retired
        self.setObjectName(_FACADE_NAME)
        # Installed before registration, including inherited QObject surface.
        self.objectNameChanged.connect(lambda _name: self._retired('FACADE_RENAMED'))
        self.destroyed.connect(lambda *_args: self._retired('FACADE_DESTROYED'))

    @Slot()
    def deleteLater(self):
        self._retired('FACADE_DELETE_REQUESTED')
        super().deleteLater()

    def event(self, event):
        # The inherited QObject metaobject retains its base deleteLater slot.
        # Its DeferredDelete event also retires before Qt destroys the object,
        # including when the base method is selected by channel method index.
        if event.type() == QEvent.Type.DeferredDelete:
            self._retired('FACADE_DELETE_REQUESTED')
        return super().event(event)

    @Slot(str)
    def ready(self, wire): self._invoke('ready', wire)
    @Slot(str)
    def setLocale(self, wire): self._invoke('setLocale', wire)
    @Slot(str)
    def setInterfaceMode(self, wire): self._invoke('setInterfaceMode', wire)
    @Slot(str)
    def setPaletteColor(self, wire): self._invoke('setPaletteColor', wire)
    @Slot(str)
    def setPaletteOpacity(self, wire): self._invoke('setPaletteOpacity', wire)
    @Slot(str)
    def resetPalette(self, wire): self._invoke('resetPalette', wire)
    @Slot(str)
    def setProfileName(self, wire): self._invoke('setProfileName', wire)
    @Slot(str)
    def selectProfile(self, wire): self._invoke('selectProfile', wire)
    @Slot(str)
    def openProfile(self, wire): self._invoke('openProfile', wire)
    @Slot(str)
    def saveProfile(self, wire): self._invoke('saveProfile', wire)
    @Slot(str)
    def saveProfileAs(self, wire): self._invoke('saveProfileAs', wire)
    @Slot(str)
    def resetSettings(self, wire): self._invoke('resetSettings', wire)
    @Slot(str)
    def undoTuning(self, wire): self._invoke('undoTuning', wire)
    @Slot(str)
    def setCaptureRate(self, wire): self._invoke('setCaptureRate', wire)
    @Slot(str)
    def setTextScale(self, wire): self._invoke('setTextScale', wire)
    @Slot(str)
    def setNumericSetting(self, wire): self._invoke('setNumericSetting', wire)
    @Slot(str)
    def setDiscreteSetting(self, wire): self._invoke('setDiscreteSetting', wire)
    @Slot(str)
    def openSettingsGuide(self, wire): self._invoke('openSettingsGuide', wire)
    @Slot(str)
    def removeVirtualMouse(self, wire): self._invoke('removeVirtualMouse', wire)


def _facade_returns_void(value):
    """Accept exact Qt text or byte-buffer spellings; never coerce objects."""
    if type(value) is str:
        return value in ('void', '')
    if type(value) in (bytes, bytearray):
        return len(value) <= 4 and bytes(value) in (b'void', b'')
    if type(value) is memoryview:
        # A bounded contiguous character view, not a typed/multidimensional
        # buffer or an arbitrary __bytes__/__str__ conversion.
        return (value.ndim == 1 and value.itemsize == 1
            and value.format in ('B', 'b', 'c') and value.c_contiguous
            and value.nbytes <= 4 and value.tobytes() in (b'void', b''))
    return False


def _inspect_facade(facade):
    """Inspect the installed inherited metaobject before exposing it. Fail closed.

This is an actual runtime check, not inferred proof from Python decorators. The
native receipt can retain the result; it is never published to the renderer.
"""
    meta = facade.metaObject()
    properties = tuple(meta.property(i).name() for i in range(meta.propertyCount()))
    signals, slots = set(), set()
    for i in range(meta.methodCount()):
        method = meta.method(i)
        if method.access() != QMetaMethod.Access.Public:
            continue
        signature = bytes(method.methodSignature()).decode('ascii', errors='strict')
        if method.methodType() == QMetaMethod.MethodType.Signal:
            signals.add(signature)
        elif method.methodType() == QMetaMethod.MethodType.Slot:
            if not _facade_returns_void(method.typeName()):
                raise ValueError('FACADE_METAOBJECT_REJECTED')
            slots.add(signature)
        else:
            raise ValueError('FACADE_METAOBJECT_REJECTED')
    expected_signals = {'snapshotPublished(QString)', 'preferencesPublished(QString)', 'commandFinished(QString)',
                        'destroyed(QObject*)', 'destroyed()', 'objectNameChanged(QString)'}
    if (properties != ('objectName',) or slots != {name + '(QString)' for name in COMMANDS} | {'deleteLater()'}
            or signals != expected_signals):
        raise ValueError('FACADE_METAOBJECT_REJECTED')
    return {'properties': properties, 'signals': tuple(sorted(signals)), 'slots': tuple(sorted(slots))}


class FixedDocumentSession(QObject):
    """One native-owned generation. Caller retains this through honest disposal.

producer is the same private controller owner across recovery; generation replacement
invalidates earlier producer tickets. Command handlers are bound native functions.
"""
    def __init__(self, *, resources, producer, native_stamp, native_authorize,
                 native_handlers, native_notice, disposed, parent, ui_history=None, recovered=None):
        super().__init__(parent)
        self._notice, self._disposed = native_notice, disposed
        self._producer = producer
        self._producer_ticket, generation = producer.replace_renderer()
        if ui_history is not None and type(ui_history) is not LiveUIHistory:raise ValueError('UI_HISTORY_REJECTED')
        self._ui_history = ui_history
        self._ui_generation = generation
        self._ui_restore = ui_history.begin(generation) if ui_history is not None else None
        self._ui_recovered = recovered
        self._ui_query_pending = False
        self._admission = HostAdmission(generation, resources, native_commit_verifier=producer.verify_host_commit)
        self._ticket = self._admission.begin_native_load()
        self._retired = False
        self._disposing = False
        self._closed = False
        self._load_requested = False
        self._load_connected = False
        self._profile = self._page = self.view = self._channel = self._facade = None
        self._handler = self._interceptor = self._metaobject_receipt = None
        self._lane = NativeCommandLane(host_current=self.commands_current, native_stamp=native_stamp,
            native_authorize=native_authorize, native_handlers=native_handlers, emit_result=self._emit_result)
        self._bootstrap_timer = QTimer(self)
        self._bootstrap_timer.setSingleShot(True)
        self._bootstrap_timer.setInterval(30000)
        self._bootstrap_timer.timeout.connect(lambda: self.retire('BOOTSTRAP_TIMEOUT'))
        self._disposal_timer = QTimer(self)
        self._disposal_timer.setSingleShot(True)
        self._disposal_timer.setInterval(5000)
        self._disposal_timer.timeout.connect(self._disposal_timeout)
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(200)
        self._ui_timer.timeout.connect(self._poll_ui_state)
        try:
            self._construct(parent)
        except Exception:
            # The native deck is already constructed. Keep this partial owner
            # and all actual Qt references until their destruction is observed.
            self.retire('LOAD_REJECTED')

    def _construct(self, parent):
        self._profile = QWebEngineProfile(self)  # no storage name: off the record
        self._profile.destroyed.connect(self._profile_destroyed)
        if not self._profile.isOffTheRecord():
            raise ValueError('PROFILE_REJECTED')
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        self._profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self._profile.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime)
        self._profile.setSpellCheckEnabled(False)
        self._profile.cookieStore().setCookieFilter(lambda _request: False)
        self._interceptor = _Interceptor(self._admission, self._ticket, self._profile)
        self._handler = _Handler(self._admission, self._ticket, self._profile)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._profile.installUrlSchemeHandler(_SCHEME_NAME, self._handler)
        self._profile.downloadRequested.connect(lambda request: self._deny(lambda: request.cancel()))
        self._page = _Page(self._profile, self._admission, self._ticket, self.retire, self)
        self._page.destroyed.connect(self._page_destroyed)
        self.view = QWebEngineView(self._page, parent)  # never creates a default-profile page
        self.view.destroyed.connect(self._view_destroyed)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.setAcceptDrops(False)
        self._channel = None
        self._facade = None
        self._metaobject_receipt = None
        settings = self._page.settings()
        for attribute in ('JavascriptCanOpenWindows', 'JavascriptCanAccessClipboard', 'JavascriptCanPaste',
                'LocalStorageEnabled', 'LocalContentCanAccessRemoteUrls', 'LocalContentCanAccessFileUrls',
                'HyperlinkAuditingEnabled', 'ScreenCaptureEnabled', 'WebGLEnabled', 'AllowRunningInsecureContent',
                'PluginsEnabled', 'FullScreenSupportEnabled', 'PdfViewerEnabled', 'NavigateOnDropEnabled'):
            settings.setAttribute(getattr(QWebEngineSettings.WebAttribute, attribute), False)
        self._page.setAudioMuted(True)
        self._page.permissionRequested.connect(lambda permission: self._deny(lambda: permission.deny()))
        self._page.fileSystemAccessRequested.connect(lambda request: self._deny(lambda: request.reject()))
        self._page.desktopMediaRequested.connect(lambda request: self._deny(lambda: request.cancel()))
        self._page.fullScreenRequested.connect(lambda request: self._deny(lambda: request.reject()))
        self._page.certificateError.connect(lambda error: self._deny(lambda: error.rejectCertificate()))
        self._page.selectClientCertificate.connect(lambda selection: self._deny(lambda: selection.selectNone()))
        self._page.authenticationRequired.connect(lambda *_args: self.retire('AUTHENTICATION_REJECTED'))
        self._page.proxyAuthenticationRequired.connect(lambda *_args: self.retire('AUTHENTICATION_REJECTED'))
        self._page.newWindowRequested.connect(lambda _request: self.retire('POPUP_REJECTED'))
        self._page.renderProcessTerminated.connect(lambda *_args: self.retire('RENDERER_TERMINATED'))
        self._page.loadFinished.connect(self._loaded)
        self._load_connected = True

    def load(self):
        if not self._current() or self._channel is not None or self._load_requested:
            return False
        self._load_requested = True
        self._bootstrap_timer.start()
        self._page.load(QUrl(DOCUMENT))
        return True

    def _current(self):
        return not self._retired and self._admission.current(self._ticket)

    def _deny(self, native_denial):
        if self._current():
            native_denial()

    def commands_current(self):
        return self._current() and self._admission.commands_admitted(self._ticket)

    def _loaded(self, success):
        if not self._current():
            return
        if not self._admission.loaded(self._ticket, success=success,
                requested_url=_url(self._page.requestedUrl()), current_url=_url(self._page.url())):
            self.retire('LOAD_REJECTED')
            return
        self._channel = QWebChannel(self._page)
        self._facade = _Facade(self._invoke, self.retire, self._channel)
        try:
            self._metaobject_receipt = _inspect_facade(self._facade)
        except (ValueError, TypeError, UnicodeError):
            self.retire('FACADE_METAOBJECT_REJECTED')
            return
        if not self._current():
            return
        self._channel.registerObject(_FACADE_NAME, self._facade)
        self._page.setWebChannel(self._channel, 0)
        ticket = self._ticket
        def completed(result):
            # Qt invokes callbacks during destruction as well. Never dereference
            # the page/facade when this permanent ticket has retired.
            if not self._current() or ticket is not self._ticket:
                return
            if result is not True:
                self.retire('BOOTSTRAP_REJECTED')
        self._page.mainFrame().runJavaScript(_BOOTSTRAP, completed)

    def _invoke(self, name, wire):
        if not self._current():
            return
        if name == 'ready':
            try:
                validate_command('ready', wire)
            except CommandRejected:
                self.retire('BOOTSTRAP_REJECTED')
                return
            if not self._admission.channel_ready(self._ticket):
                self.retire('BOOTSTRAP_REJECTED')
                return
            self.publish(initial=True)
        elif self.commands_current():
            try:
                self._lane.receive(name, wire)
            except Exception:
                self.retire('NATIVE_COMMAND_FAILED')

    def publish(self, *, initial=False):
        if not self._current() or (not initial and not self.commands_current()):
            return False
        try:
            candidate = self._producer.prepare(self._producer_ticket)
            if not self._current():
                return False
            receipt = self._producer.commit(self._producer_ticket, candidate)
            if initial and not self._admission.admit_initial_commit(self._ticket, receipt):
                self.retire('CONTEXT_REJECTED')
                return False
            context, preferences = self._producer.committed_bytes(self._producer_ticket, receipt)
            if not self.commands_current():
                return False
            self._facade.snapshotPublished.emit(context.decode('utf-8', errors='strict'))
            if not self.commands_current():
                return False
            self._facade.preferencesPublished.emit(preferences.decode('utf-8', errors='strict'))
            self._bootstrap_timer.stop()
            if initial and self._ui_history is not None:self._ui_timer.start()
            return True
        except Exception:
            self.retire('CONTEXT_REJECTED')
            return False

    def _emit_result(self, raw):
        if self.commands_current():
            self._facade.commandFinished.emit(raw.decode('utf-8', errors='strict'))

    def user_stop(self):
        self._lane.user_stop_cancel_transition()

    def restore_live_focus(self):
        if self.commands_current() and self._page is not None:
            self._page.mainFrame().runJavaScript('globalThis.__vlRestoreLiveFocus?.() === true', 0)

    def _poll_ui_state(self):
        if not self.commands_current() or self._page is None or self._ui_query_pending:return
        self._ui_query_pending=True
        if self._ui_restore is not None:
            script='globalThis.__vlRestoreUIState?.('+json.dumps(self._ui_restore,separators=(',',':'))+') ?? false'
            restoring=True
        else:
            script='(()=>{const s=JSON.stringify(globalThis.__vlReadUIState?.() ?? null);return typeof s==="string"&&s.length<=1024?s:null})()'
            restoring=False
        def observed(value):
            if not self.commands_current() or self._page is None:return
            self._ui_query_pending=False
            if restoring:
                if value in ('applied','superseded'):
                    self._ui_restore=None
                    if value=='applied' and self._ui_recovered is not None:self._ui_recovered(self)
            else:
                self._ui_history.observe(self._ui_generation,decode_ui_state_wire(value))
        self._page.mainFrame().runJavaScript(script,0,observed)

    def retire(self, reason='NATIVE_RETIRED'):
        if self._retired:
            return
        self._retired = True  # before signals/disconnects/Qt or controller access
        self._admission.retire(self._ticket, reason)
        self._lane.retire()
        self._bootstrap_timer.stop()
        self._ui_timer.stop()
        self._notice(reason)  # fixed native code; no renderer string interpreted
        try:
            self.dispose()
        except Exception:
            # An unexpected Qt disposal failure never grants cleanup/recovery.
            # Timer and native parent retain this owner and its real references.
            self._notice('DISPOSAL_PENDING')

    def dispose(self):
        if not self._retired:
            self.retire()
            return
        if self._disposing or self._closed:
            return
        self._disposing = True
        self._disposal_timer.start()
        if self._page is not None:
            self._page.setWebChannel(None, 0)
            if self._load_connected:
                self._page.loadFinished.disconnect(self._loaded)
                self._load_connected = False
            self._page.triggerAction(QWebEnginePage.WebAction.Stop)
            if self.view is not None:
                self.view.hide()
                self.view.deleteLater()
            self._page.deleteLater()
        elif self.view is not None:
            self.view.hide()
            self.view.deleteLater()
        else:
            self._release_profile()
        # profile remains alive until the actual page.destroyed notification.

    def _page_destroyed(self, *_args):
        self._page = None
        self._channel = None
        self._facade = None
        if not self._retired:
            self.retire('PAGE_DESTROYED')
        self._release_profile()

    def _view_destroyed(self, *_args):
        self.view = None
        if not self._retired:
            self.retire('PAGE_DESTROYED')
        self._release_profile()

    def _release_profile(self):
        if self._page is None and self.view is None and self._profile is not None:
            self._profile.deleteLater()
        elif self._page is None and self.view is None and self._profile is None and not self._closed:
            self._closed = True
            self._disposal_timer.stop()
            self._disposed(self)

    def _profile_destroyed(self, *_args):
        self._profile = None
        self._handler = None
        self._interceptor = None
        self._closed = self._page is None and self.view is None
        if not self._closed:
            self.retire('PROFILE_DESTROYED_EARLY')
            self._notice('DISPOSAL_PENDING')
            return
        self._disposal_timer.stop()
        self._disposed(self)

    def _disposal_timeout(self):
        if not self._closed:
            self._notice('DISPOSAL_PENDING')
            # Caller retains this session and every live reference. Timeout is
            # neither successful cleanup nor permission to create another page.

    def native_observation(self):
        return {'admission': dict(self._admission.native_state()), 'retired': self._retired,
                'disposing': self._disposing, 'closed': self._closed,
                'metaobject': self._metaobject_receipt}
