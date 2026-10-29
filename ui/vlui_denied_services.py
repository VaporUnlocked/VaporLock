"""Explicit idle DENY adapters for the later reviewed disposable Qt recipe.

They create no process, network/token, DLL/device, installation or license monitor.
Unknown capability stays unverified. No fake provider/capture/job success is emitted.
"""
from PySide6.QtCore import QObject, QProcessEnvironment, Signal


class DeniedEngine(QObject):
    telemetry = Signal(dict)
    preview = Signal(bytes, int, int)
    preview_invalidated = Signal()
    startup_rejected = Signal()
    live_setting_result = Signal(bool)
    overlay = Signal(dict)
    observation_packet = Signal(dict,int,int,int,bool)
    dataset = Signal(int)
    message = Signal(str)
    state_changed = Signal(str)
    active = False
    session_running = False
    active_provider = ''
    observe_only = False
    overlay_metadata_active = False
    _controller_host_session = None

    def start(self, *_args, **_kwargs): self.message.emit('Capture is unavailable in this verification session.')
    def stop(self): return None
    def apply_live_setting(self, field, value): return False
    def trigger_manual_lock(self): return False
    def candidate_providers(self, settings): return []
    def executable_for(self, provider): return None
    def process_environment(self, provider): return QProcessEnvironment()
    @staticmethod
    def provider_label(provider): return {'auto': 'Automatic', 'cpu': 'CPU', 'cuda': 'CUDA', 'dml': 'DirectML', 'trt': 'TensorRT'}.get(provider, 'Unknown')


class DeniedCapabilities(QObject):
    changed = Signal()
    def request(self, *_args): return 'unverified'
    def stop(self): return None
    def cleanup_complete(self): return True


class DeniedAcceleration(QObject):
    status = Signal(str)
    completed = Signal(str, bool, str)
    active = False
    def install(self, *_args): self.status.emit('Installation is unavailable in this verification session.')
    def is_installed(self, provider): return False
    def recommended_provider(self): return 'cpu'
    def supports(self, provider): return False


class DeniedController(QObject):
    status = Signal(str)
    completed = Signal(bool, str)
    session_ready = Signal(object)
    preparing = False
    _session = None
    _pending_session = None
    def start_session(self, *_args): self.status.emit('Controller output is unavailable in this verification session.')
    def stop_session(self): return None
    def install(self, *_args): self.status.emit('Installation is unavailable in this verification session.')
    def uninstall(self, *_args): self.status.emit('Installation is unavailable in this verification session.')
    def safely_stopped(self): return True  # owns no launcher/device/session, not an active host ack


class DeniedOverlay(QObject):
    def apply_settings(self, *_args): return None
    def set_payload(self, *_args): return None
    def set_runtime_metrics(self, *_args): return None
    def hide(self): return None


class DeniedAPI(QObject):
    running = False
    address = ''
    token = ''
    certificate_fingerprint = ''
    certificate_path = None
    error = 'Local API is unavailable in this verification session.'
    _thread = None
    def start(self, *_args, **_kwargs): return False, self.error
    def stop(self): return None
    def request_stop_async(self): return None
    def cleanup_complete(self): return True
    def finish_command(self, *_args): return None
    def update_snapshot(self, *_args): return None








class DeniedReader:
    def sample(self): return {}


class DeniedNativeServices:
    engine = staticmethod(DeniedEngine)
    capabilities = staticmethod(DeniedCapabilities)
    acceleration = staticmethod(DeniedAcceleration)
    controller = staticmethod(DeniedController)
    xbox = staticmethod(DeniedController)
    overlay = staticmethod(DeniedOverlay)
    api = staticmethod(DeniedAPI)
    controller_reader = staticmethod(DeniedReader)
