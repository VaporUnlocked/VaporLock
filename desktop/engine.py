"""Own the native capture process and its optional local Cloud worker."""

from __future__ import annotations

import base64
from collections import deque
from copy import deepcopy
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import threading
import time

from engine_stream import EngineLineReader, OverlayFrameGate, PreviewFrameGate
from engine_stream import native_observation_age_ms, native_tick_ms
from native_engine_layout import native_environment, provider_ready, source_engine
from crosshair_reference import remaining_ms

from .settings import AIM_NATIVE_SETTINGS, ProductPaths, Settings, policies_wire, validate_settings


PROVIDER_LABELS = {'cpu': 'CPU', 'dml': 'DirectML', 'cuda': 'CUDA', 'trt': 'TensorRT RTX'}
_MAX_PREVIEW_AGE = 2.0
_START_TIMEOUT = 120.0
_STOP_TIMEOUT = 1.5
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


@dataclass(frozen=True, slots=True)
class PreviewFrame:
    jpeg: bytes
    width: int
    height: int
    frame_id: int
    observed_at: float
    live_until: float


@dataclass(frozen=True, slots=True)
class EngineSnapshot:
    state: str
    provider: str
    observe_only: bool
    stats: dict
    preview: PreviewFrame | None
    overlay: dict
    dataset: dict
    messages: tuple[str, ...]
    error: str


def candidate_providers(settings: Settings) -> tuple[str, ...]:
    if settings.provider != 'auto':
        return (settings.provider,)
    if not settings.model_path:
        return ('cpu',)
    if Path(settings.model_path).suffix.lower() == '.cloud' or settings.inference_dml_device >= 0:
        return ('dml',)
    return ('dml', 'cpu')


def _launch_for(root: Path, provider: str) -> tuple[Path, dict] | None:
    if not getattr(sys, 'frozen', False):
        source = source_engine(root, provider)
        if provider_ready(source, provider):
            return source, native_environment(source.parent, os.environ)
    if provider == 'cuda':
        from runtime_generations import RuntimeIntegrityError, resolve_cuda

        try:
            runtime = resolve_cuda(root)
        except (OSError, ValueError, KeyError, RuntimeIntegrityError):
            return None
        return runtime.executable, runtime.environment(os.environ)

    directories = {'cpu': (), 'dml': ('dml',), 'trt': ('trt',)}
    executables = {'cpu': 'VaporLock.exe', 'dml': 'VaporLockDml.exe', 'trt': 'VaporLockTensorRt.exe'}
    executable = root.joinpath('engine', *directories[provider], executables[provider])
    if not executable.is_file() or not provider_ready(executable, provider):
        return None
    if provider == 'trt' and not all(
        (executable.parent / dll).is_file()
        for dll in ('onnxruntime.dll', 'onnxruntime_providers_shared.dll')
    ):
        return None
    return executable, native_environment(executable.parent, os.environ)


class _ManualLock:
    def __init__(self):
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        api.CreateEventW.restype = wintypes.HANDLE
        api.SetEvent.argtypes = (wintypes.HANDLE,)
        api.SetEvent.restype = wintypes.BOOL
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        api.CloseHandle.restype = wintypes.BOOL
        self.api = api
        self.name = 'Local\\VaporLockManualLock-' + secrets.token_hex(16)
        self.handle = api.CreateEventW(None, False, False, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def trigger(self) -> bool:
        return bool(self.api.SetEvent(self.handle))

    def close(self) -> None:
        self.api.CloseHandle(self.handle)


class _CloudWorker:
    def __init__(self, root: Path, model: Path, provider: str, device_index: int):
        from cloud_access import edition
        from cloud_dependencies import command
        from cloud_process import OwnedChild

        self.edition = edition()
        self.pipe = f'VaporLock.Cloud.{self.edition}.{secrets.token_hex(16)}'
        device = 'cpu' if provider == 'cpu' else f'{"directml" if provider == "dml" else provider}:{max(0, device_index)}'
        self.request = {'operation': 'inference', 'model': str(model), 'device': device,
                        'backend': 'torch' if provider == 'cuda' else 'ort', 'native_pipe': self.pipe}
        self.child = OwnedChild(command(), cwd=root, wall_seconds=86400)

    def wait_ready(self, cancel: threading.Event) -> bool:
        from cloud_contract import worker_event

        if cancel.is_set():
            return False
        self.child.send(self.request)
        deadline = time.monotonic() + _START_TIMEOUT
        while not cancel.is_set():
            try:
                event = self.child.events.get_nowait()
            except queue.Empty:
                if self.child.poll() is not None:
                    raise RuntimeError(self.child.failure or 'The Cloud worker exited before loading the model.')
                if time.monotonic() >= deadline:
                    raise RuntimeError('The Cloud worker did not load the model within 120 seconds.')
                cancel.wait(0.025)
                continue
            if event.get('type') == 'error':
                raise RuntimeError(event['message'])
            if event.get('type') != 'ready':
                raise RuntimeError('The Cloud worker returned an unexpected startup event.')
            worker_event(event, 'inference')
            if event.get('edition') != self.edition or event.get('pid') != self.child.process.pid:
                raise RuntimeError('Cloud worker identity did not match the launched process.')
            return True
        return False

    @property
    def arguments(self) -> list[str]:
        return ['--cloud-pipe', self.pipe, '--cloud-worker-pid', str(self.child.process.pid)]

    def attach(self, native_pid: int) -> None:
        self.child.send({'command': 'client', 'pid': native_pid})

    def error(self) -> str:
        try:
            event = self.child.events.get_nowait()
        except queue.Empty:
            event = {}
        if event.get('type') == 'error':
            return event['message']
        if event.get('type') == 'inference_closed' or self.child.poll() is not None:
            return self.child.failure or 'The Cloud worker closed the inference session.'
        return ''

    def close(self) -> None:
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.job.close()
                self.child.process.wait()
        self.child.close()


class EngineController:
    """A single owned capture session, polled by the desktop without GUI callbacks."""

    def __init__(self, root: Path | ProductPaths):
        self.paths = root if isinstance(root, ProductPaths) else ProductPaths.from_root(root)
        self._lock = threading.Lock()
        self._thread = None
        self._manual_lock = None
        self._cancel = threading.Event()
        self._messages = deque(maxlen=100)
        self._state = 'idle'
        self._provider = ''
        self._observe_only = True
        self._error = ''
        self._reset_stream()

    def _reset_stream(self) -> None:
        self._stats = {}
        self._dataset = {}
        self._preview = None
        self._overlay = {}
        self._overlay_time = 0.0
        self._overlay_gate = OverlayFrameGate()
        self._preview_gate = PreviewFrameGate()
        self._run_error = ''
        self._fatal = False

    def snapshot(self, *, include_overlay: bool = True) -> EngineSnapshot:
        with self._lock:
            now = time.monotonic()
            preview = self._preview
            if preview is not None and now - preview.observed_at >= self._preview_age:
                preview = None
            overlay = self._overlay if include_overlay and now - self._overlay_time < self._overlay_age else {}
            return EngineSnapshot(self._state, self._provider, self._observe_only, deepcopy(self._stats),
                                  preview, deepcopy(overlay), self._dataset.copy(), tuple(self._messages), self._error)

    _preview_age = _MAX_PREVIEW_AGE
    _overlay_age = .15

    def start(self, settings: Settings, *, observe_only: bool = True, capture_excluded: bool = False,
              controller_host_session=None) -> None:
        settings = deepcopy(settings)
        validate_settings(settings)
        if type(observe_only) is not bool:
            raise ValueError('Select Observe or Control before starting capture.')
        model = self.paths.resolve(settings.model_path) if settings.model_path else None
        if model is not None and (model.suffix.lower() not in ('.onnx', '.cloud') or not model.is_file()):
            raise ValueError('Choose an existing .onnx or .cloud model file.')
        if model is not None and model.suffix.lower() == '.cloud' and settings.provider == 'trt':
            raise ValueError('Choose CPU, DirectML or CUDA for a .cloud model.')
        if settings.crosshair_enabled and capture_excluded is not True:
            raise ValueError('Crosshair detection requires VaporLock windows to be excluded from capture.')
        if not observe_only and settings.output_transport == 'virtual-xbox' and controller_host_session is None:
            raise ValueError('Prepare the Xbox-compatible controller bridge before starting Control.')
        if not observe_only and settings.output_transport == 'virtual-mouse' and not settings.virtual_mouse_opt_in:
            raise ValueError('Enable the virtual mouse driver in settings before starting Control.')

        with self._lock:
            if self._thread is not None:
                raise ValueError('Stop the active capture session before starting another.')
            self._reset_stream()
            self._error = ''
            self._observe_only = observe_only
            self._preview_age = min(_MAX_PREVIEW_AGE, max(.5, 2.0 / max(1, settings.preview_fps)))
            self._overlay_age = max(.001, settings.output_max_observation_age_ms / 1000.0)
            self._state = 'starting'
            self._provider = ''
            self._cancel.clear()
            self._thread = threading.Thread(target=self._run, args=(settings, model, observe_only, controller_host_session),
                                            name='VaporLock capture session', daemon=True)
            try:
                self._thread.start()
            except RuntimeError:
                self._thread = None
                self._state = 'idle'
                raise

    def stop(self) -> None:
        with self._lock:
            if self._thread is None:
                return
            self._cancel.set()
            self._state = 'stopping'
            self._overlay = {}
            self._preview = None

    def close(self, timeout: float = 2.0) -> bool:
        self.stop()
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def trigger_manual_lock(self) -> bool:
        with self._lock:
            return bool(self._state == 'running' and self._manual_lock and self._manual_lock.trigger())

    def _message(self, text: str) -> None:
        with self._lock:
            self._messages.append(text[:2048])

    def _run(self, settings: Settings, model: Path | None, observe_only: bool, controller_host_session) -> None:
        cloud = None
        manual_lock = None
        error = ''
        try:
            providers = candidate_providers(settings)
            if settings.manual_target_lock_enabled and os.name == 'nt' and not self._cancel.is_set():
                try:
                    manual_lock = _ManualLock()
                    with self._lock:
                        self._manual_lock = manual_lock
                except OSError as failure:
                    self._message(f'Manual lock could not start: {failure}')

            if model is not None and model.suffix.lower() == '.cloud' and not self._cancel.is_set():
                provider = providers[0]
                with self._lock:
                    self._provider = provider
                self._message('Loading the Cloud model.')
                device_index = getattr(settings, f'inference_{provider}_device', -1)
                cloud = _CloudWorker(self.paths.root, model, provider, device_index)
                if not cloud.wait_ready(self._cancel):
                    return

            for provider in providers:
                if self._cancel.is_set():
                    return
                with self._lock:
                    self._provider = provider
                    self._reset_stream()
                launch = _launch_for(self.paths.root, 'cpu' if cloud else provider)
                if launch is None:
                    error = f'The {PROVIDER_LABELS[provider]} engine is unavailable.'
                    self._message(error)
                    continue
                args = native_arguments(settings, self.paths, provider, observe_only=observe_only,
                                        manual_lock_event=manual_lock.name if manual_lock else '',
                                        controller_host_session=controller_host_session)
                if cloud:
                    args.extend(cloud.arguments)
                error = self._run_native(*launch, args, cloud)
                with self._lock:
                    if self._stats or self._fatal:
                        break
                if error:
                    self._message(error)
        except (OSError, ValueError, RuntimeError, ImportError) as failure:
            error = str(failure) or type(failure).__name__
        finally:
            if cloud is not None:
                while True:
                    try:
                        cloud.close()
                        break
                    except (OSError, ValueError, RuntimeError) as failure:
                        with self._lock:
                            if self._state != 'stopping':
                                self._messages.append(f'Waiting for the Cloud worker to stop: {failure}'[:2048])
                            self._state = 'stopping'
                        time.sleep(.25)
            with self._lock:
                if manual_lock is not None:
                    manual_lock.close()
                self._manual_lock = None
                self._preview = None
                self._overlay = {}
                self._error = '' if self._cancel.is_set() else error
                if self._error and (not self._messages or self._messages[-1] != self._error):
                    self._messages.append(self._error[:2048])
                self._state = 'error' if self._error else 'idle'
                self._thread = None

    def _run_native(self, executable: Path, environment: dict, args: list[str], cloud: _CloudWorker | None) -> str:
        with self._lock:
            if self._cancel.is_set():
                return ''
            try:
                process = subprocess.Popen([str(executable), *args], cwd=executable.parent, env=environment,
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           bufsize=0, close_fds=True,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as failure:
                return f'Could not launch {executable.name}: {failure}'
        readers = []
        session_error = ''
        start_deadline = time.monotonic() + _START_TIMEOUT
        try:
            for target, pipe in ((self._read_stdout, process.stdout), (self._read_stderr, process.stderr)):
                reader = threading.Thread(target=target, args=(pipe,), daemon=True)
                reader.start()
                readers.append(reader)
            if cloud and not self._cancel.is_set():
                cloud.attach(process.pid)
            while process.poll() is None:
                if self._cancel.wait(.025):
                    break
                with self._lock:
                    if self._run_error:
                        break
                    started = bool(self._stats)
                if cloud:
                    session_error = cloud.error()
                    if session_error:
                        break
                if not started and time.monotonic() >= start_deadline:
                    session_error = f'{executable.name} did not begin capture within 120 seconds.'
                    break
        finally:
            if process.poll() is None:
                try:
                    process.stdin.write(b'stop\n')
                    process.stdin.flush()
                    process.wait(timeout=_STOP_TIMEOUT)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            while process.poll() is None:
                try:
                    process.kill()
                except OSError as failure:
                    session_error = f'Waiting for the native engine to stop: {failure}'
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    continue
            exit_code = process.wait()
            process.stdin.close()
            for reader in readers:
                reader.join()
            process.stdout.close()
            process.stderr.close()
        with self._lock:
            if self._cancel.is_set():
                return ''
            return self._run_error or session_error or f'{executable.name} exited (code {exit_code}).'

    def _read_stdout(self, stream) -> None:
        lines = EngineLineReader()
        with stream:
            while chunk := stream.read(65536):
                for line in lines.feed(chunk):
                    if not self._cancel.is_set():
                        self._read_record(line)

    def _read_stderr(self, stream) -> None:
        lines = EngineLineReader(16384)
        with stream:
            while chunk := stream.read(16384):
                for line in lines.feed(chunk):
                    if line and not self._cancel.is_set():
                        message = _ANSI.sub('', line.decode('utf-8', errors='replace')).strip()
                        if message:
                            self._message(message)

    def _read_record(self, line: bytes | None) -> None:
        if line is None:
            self._message('Discarded an oversized engine message.')
            return
        try:
            payload = json.loads(line)
        except (ValueError, RecursionError):
            self._message(line[:2048].decode('utf-8', errors='replace'))
            return
        if not isinstance(payload, dict):
            return

        kind = payload.get('type')
        with self._lock:
            if self._cancel.is_set():
                return
            if kind in ('error', 'output-error'):
                self._run_error = str(payload.get('message', 'Native engine error.'))[:2048]
                self._fatal |= kind == 'output-error'
                self._preview = None
                self._overlay = {}
            elif kind in ('notice', 'output'):
                self._messages.append(str(payload.get('message', ''))[:2048])
            elif kind == 'dataset' and type(payload.get('samples')) is int and isinstance(payload.get('path'), str):
                self._dataset = {'samples': payload['samples'], 'path': payload['path']}
            elif not self._run_error:
                if kind == 'stats':
                    self._stats = payload
                    self._state = 'running'
                elif kind in ('overlay', 'preview'):
                    self._read_visual(kind, payload)

    def _read_visual(self, kind: str, payload: dict) -> None:
        now = native_tick_ms()
        if kind == 'overlay':
            previous = self._overlay_gate.identity
            if not self._overlay_gate.accept(payload, 0, now):
                return
            current = self._overlay_gate.identity
            if previous is None or previous[:2] != current[:2] or previous[3] != current[3]:
                self._preview = None
            self._overlay = payload
            self._overlay_time = time.monotonic() - native_observation_age_ms(payload, now) / 1000.0
            return
        if not self._preview_gate.accept(payload, 0, now, self._overlay_gate.identity):
            return
        received_at = time.monotonic()
        observed_at = received_at - native_observation_age_ms(payload, now) / 1000.0
        if received_at - observed_at >= self._preview_age:
            return
        try:
            jpeg = base64.b64decode(payload['jpeg'], validate=True)
        except (KeyError, TypeError, ValueError):
            self._messages.append('Discarded an invalid engine preview.')
            return
        live_until = min(observed_at + self._overlay_age, received_at + remaining_ms(payload, now) / 1000.0)
        self._preview = PreviewFrame(jpeg, payload['width'], payload['height'], payload['frame_id'], observed_at,
                                     live_until)


def native_arguments(settings: Settings, paths: ProductPaths, provider: str, *, observe_only: bool = True,
                     manual_lock_event: str = '', controller_host_session=None) -> list[str]:
    """Encode a settings snapshot for the native CLI."""
    from batch6_settings import device_for
    from cpu_placement import wire as cpu_wire
    from crosshair_reference import wire as crosshair_wire
    from exclusion_zones import zones_wire

    calibration_wire = crosshair_wire(settings)
    # Once the Xbox One bridge has reserved Controller 1, the physical
    # pad moves to another XInput slot. Primary proxy mode must therefore
    # scan the remaining slots even when an older profile named slot 1.
    controller_slot = (
        0
        if controller_host_session is not None and not observe_only
        and settings.output_transport == "virtual-xbox"
        and settings.virtual_controller_mode == "slot0-proxy"
        else settings.controller_slot
    )
    args = [
        "--headless", "--confidence", str(settings.confidence), "--nms-iou", str(settings.nms_iou),
        "--model-output-format", settings.model_output_format,
        "--model-coordinate-units", settings.model_coordinate_units,
        "--provider", provider,
        "--inference-device", str(device_for(settings, provider)),
        "--cpu-sets", cpu_wire(settings, provider),
        "--max-detections", str(settings.max_detections), "--resize-mode", settings.resize_mode,
        "--monitor", str(settings.monitor), "--region", f"{settings.region_x},{settings.region_y},{settings.region_width},{settings.region_height}",
        "--capture-fps", str(settings.capture_fps), "--inference-fps", str(settings.inference_fps),
        "--frame-skip", str(settings.frame_skip), "--match-iou", str(settings.match_iou),
        "--ort-intra-op-threads", str(settings.ort_intra_op_threads), "--ort-inter-op-threads", str(settings.ort_inter_op_threads),
        "--smoothing", str(settings.smoothing), "--box-smoothing", str(settings.box_smoothing), "--anchor-smoothing", str(settings.anchor_smoothing), "--anchor-settle-radius", str(settings.anchor_settle_radius), "--max-missed", str(settings.max_missed),
        "--prediction-lead-ms", str(settings.prediction_lead_ms), "--prediction-limit-ms", str(settings.prediction_limit_ms),
        "--strategy", str(settings.strategy), "--acquire-radius", str(settings.acquisition_radius),
        "--near-radius", str(settings.near_radius), "--snap-radius", str(settings.snap_radius),
        "--retain-radius", str(settings.retention_radius), "--fov-shape", settings.fov_shape, "--switching", str(settings.switching),
        "--lock-persistence-ms", str(settings.lock_persistence_ms), "--lost-target-timeout", str(settings.lost_target_timeout),
        "--min-track-age", str(settings.minimum_track_age), "--anchor-mode", settings.anchor_mode,
        "--anchor-x", str(settings.anchor_x), "--anchor-y", str(settings.anchor_y),
        "--watchlist", settings.watchlist,
        "--occlusion-grace-frames", str(settings.occlusion_grace_frames),
        "--occlusion-reacquire-radius", str(settings.occlusion_reacquire_radius),
        "--class-policies", policies_wire(settings.class_policies),
        "--exclusion-zones", zones_wire(settings.exclusion_zones),
        "--activation-mode", settings.activation_mode, "--activation-key", settings.activation_key,
        "--activation-source", settings.activation_source, "--controller-slot", str(controller_slot),
        "--controller-binding", settings.controller_binding,
        "--controller-trigger-press", str(settings.controller_trigger_press),
        "--controller-trigger-release", str(settings.controller_trigger_release),
        "--manual-lock-source", settings.manual_lock_source, "--manual-lock-key", settings.manual_lock_key,
        "--manual-lock-controller-binding", settings.manual_lock_controller_binding,
        "--gain", str(settings.gain), "--max-step", str(settings.max_step), "--deadzone", str(settings.deadzone),
        "--movement-smoothing", str(settings.movement_smoothing), "--output-rate", str(settings.output_rate),
        "--motion-compensation-mode", settings.motion_compensation_mode,
        "--motion-compensation-activation-mode", settings.motion_compensation_activation_mode,
        "--motion-compensation-activation-key", settings.motion_compensation_activation_key,
        "--motion-compensation-vertical", str(settings.motion_compensation_vertical),
        "--motion-compensation-horizontal", str(settings.motion_compensation_horizontal),
        "--motion-compensation-strength", str(settings.motion_compensation_strength),
        "--motion-compensation-auto-min-tracks", str(settings.motion_compensation_auto_min_tracks),
        "--motion-compensation-auto-consensus", str(settings.motion_compensation_auto_consensus),
        "--motion-compensation-auto-smoothing", str(settings.motion_compensation_auto_smoothing),
        "--motion-compensation-auto-max-rate", str(settings.motion_compensation_auto_max_rate),
        "--event-action-mode", settings.event_action_mode, "--event-action-button", settings.event_action_button,
        "--event-action-cooldown-ms", str(settings.event_action_cooldown_ms),
        "--output-transport", "mouse" if observe_only else settings.output_transport,
        "--virtual-mouse-library", str(paths.root / "hardware" / "interception" / "x64" / "interception.dll"),
        "--virtual-controller-mode", settings.virtual_controller_mode,
        "--virtual-controller-stick", settings.virtual_controller_stick,
        "--virtual-controller-strength", str(settings.virtual_controller_strength),
        "--hardware-serial-port", "" if observe_only else settings.hardware_serial_port,
        "--hardware-serial-baud", str(settings.hardware_serial_baud),
        "--kmbox-net-address", "" if observe_only else settings.kmbox_net_address,
        "--kmbox-net-port", str(settings.kmbox_net_port),
        "--kmbox-net-device-code", "" if observe_only else settings.kmbox_net_device_code,
        "--kmbox-a-library", str(paths.resolve(settings.kmbox_a_library_path))
        if settings.kmbox_a_library_path and not observe_only else "",
        "--kmbox-a-vid", hex(settings.kmbox_a_vendor_id),
        "--kmbox-a-pid", hex(settings.kmbox_a_product_id),
        "--response-curve", settings.response_curve,
        "--preview-fps", str(settings.preview_fps), "--preview-quality", str(settings.preview_quality),
        "--dataset-min-confidence", str(settings.dataset_min_confidence), "--dataset-interval-ms", str(settings.dataset_interval_ms),
        "--dataset-jpeg-quality", str(settings.dataset_jpeg_quality),
        "--dataset-max-images", str(settings.dataset_max_images), "--dataset-max-mib", str(settings.dataset_max_mib),
        "--segmentation-mask-limit", str(settings.segmentation_mask_limit), "--segmentation-mask-threshold", str(settings.segmentation_mask_threshold),
    ]
    if settings.crosshair_enabled:
        args.append("--crosshair-enabled")
    if calibration_wire:
        args.extend(("--crosshair-template", calibration_wire))
    args.append("--predict-lost" if settings.predict_lost else "--no-predict-lost")
    for field in AIM_NATIVE_SETTINGS:
        value = getattr(settings, field)
        wire_value = str(value).lower() if isinstance(value, bool) else str(value)
        args.extend(("--aim-setting", f"{field}={wire_value}"))
    args.append("--occlusion-handling" if settings.occlusion_handling else "--no-occlusion-handling")
    args.append("--manual-target-lock" if settings.manual_target_lock_enabled else "--no-manual-target-lock")
    args.append("--cuda-graph-replay" if settings.cuda_graph_replay else "--no-cuda-graph-replay")
    args.append("--cuda-preprocess" if settings.cuda_preprocess else "--no-cuda-preprocess")
    args.append("--preview" if settings.preview_enabled else "--no-preview")
    args.append("--overlay" if settings.overlay_enabled else "--no-overlay")
    args.append("--overlay-boxes" if settings.overlay_boxes else "--no-overlay-boxes")
    args.append("--overlay-motion" if settings.overlay_motion else "--no-overlay-motion")
    args.append("--overlay-fov" if settings.overlay_fov else "--no-overlay-fov")
    args.append("--overlay-segmentation-masks" if settings.overlay_segmentation_masks else "--no-overlay-segmentation-masks")
    args.append("--nms" if settings.nms_enabled else "--no-nms")
    args.append("--class-agnostic-nms" if settings.nms_class_agnostic else "--class-aware-nms")
    args.append("--event-action" if settings.event_action_enabled and not observe_only else "--no-event-action")
    args.append("--motion-compensation" if settings.motion_compensation_enabled and not observe_only else "--no-motion-compensation")
    args.append("--event-action-requires-activation" if settings.event_action_requires_activation else "--event-action-independent")
    if settings.desktop_overlay_enabled:
        args.append("--overlay-metadata")
    args.append("--dataset" if settings.dataset_enabled else "--no-dataset")
    args.append("--dataset-capture-confidence" if settings.dataset_capture_confidence_enabled else "--no-dataset-capture-confidence")
    args.append("--dataset-requires-activation" if settings.dataset_capture_requires_activation else "--dataset-anytime")
    args.append("--virtual-controller-manual-priority" if settings.virtual_controller_manual_priority else "--no-virtual-controller-manual-priority")
    if settings.model_path:
        args.extend(("--model", str(paths.resolve(settings.model_path))))
    if settings.class_filter:
        args.extend(("--class-filter", settings.class_filter))
    args.extend(("--dataset-directory", str(paths.resolve(settings.dataset_directory or 'datasets'))))
    if settings.invert_x:
        args.append("--invert-x")
    if settings.invert_y:
        args.append("--invert-y")
    if settings.movement_enabled and not observe_only:
        args.append("--movement")
    if observe_only:
        args.append("--observe-only")
    if manual_lock_event:
        args.extend(("--manual-lock-event", manual_lock_event))
    if controller_host_session is not None and not observe_only:
        args.extend(("--controller-host-pipe", controller_host_session.pipe_name))
        if controller_host_session.virtual_slot is not None:
            args.extend(("--controller-host-virtual-slot", str(controller_host_session.virtual_slot)))
    return args
