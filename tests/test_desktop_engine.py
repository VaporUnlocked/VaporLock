import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop.engine import EngineController, _CloudWorker, candidate_providers, native_arguments
from desktop.settings import ProductPaths, Settings


class DesktopEngineTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.paths = ProductPaths.from_root(self.root)

    def test_auto_only_falls_back_before_a_model_starts(self):
        self.assertEqual(candidate_providers(Settings()), ('cpu',))
        self.assertEqual(candidate_providers(Settings(model_path='model.onnx')), ('dml', 'cpu'))
        self.assertEqual(candidate_providers(Settings(model_path='model.onnx', inference_dml_device=0)), ('dml',))
        self.assertEqual(candidate_providers(Settings(model_path='model.cloud')), ('dml',))
        self.assertEqual(candidate_providers(Settings(provider='cuda')), ('cuda',))

    def test_observe_drops_hardware_authority_before_the_native_boundary(self):
        settings = Settings(output_transport='virtual-xbox', movement_enabled=True, event_action_enabled=True,
                            motion_compensation_enabled=True, kmbox_net_address='192.0.2.1',
                            kmbox_net_device_code='1234', hardware_serial_port='COM3', controller_slot=1)
        controller = SimpleNamespace(pipe_name='local-test-pipe', virtual_slot=1)
        observe = native_arguments(settings, self.paths, 'cpu', controller_host_session=controller)
        self.assertIn('--observe-only', observe)
        self.assertNotIn('--movement', observe)
        self.assertNotIn('--event-action', observe)
        self.assertNotIn('--motion-compensation', observe)
        self.assertNotIn('--controller-host-pipe', observe)
        self.assertNotIn('192.0.2.1', observe)
        self.assertNotIn('1234', observe)
        self.assertNotIn('COM3', observe)
        self.assertEqual(observe[observe.index('--output-transport') + 1], 'mouse')
        self.assertEqual(observe[observe.index('--controller-slot') + 1], '1')

        control = native_arguments(settings, self.paths, 'cpu', observe_only=False, controller_host_session=controller)
        self.assertNotIn('--observe-only', control)
        self.assertIn('--movement', control)
        self.assertIn('--event-action', control)
        self.assertIn('--motion-compensation', control)
        self.assertEqual(control[control.index('--controller-host-pipe') + 1], 'local-test-pipe')
        self.assertEqual(control[control.index('--controller-slot') + 1], '0')

    def test_start_guards_user_and_worker_prerequisites(self):
        controller = EngineController(self.paths)
        with self.assertRaisesRegex(ValueError, 'existing .* model'):
            controller.start(Settings(model_path='missing.onnx'))
        with self.assertRaisesRegex(ValueError, 'excluded from capture'):
            controller.start(Settings(crosshair_enabled=True))
        with self.assertRaisesRegex(ValueError, 'controller bridge'):
            controller.start(Settings(output_transport='virtual-xbox'), observe_only=False)
        with self.assertRaisesRegex(ValueError, 'virtual mouse driver'):
            controller.start(Settings(output_transport='virtual-mouse'), observe_only=False)
        self.assertEqual(controller.snapshot().state, 'idle')

    def test_native_pipe_lifecycle_and_cancel(self):
        controller = EngineController(self.paths)
        launched = []
        popen = subprocess.Popen
        script = (
            'import json,sys,time; '
            'print(json.dumps({"type":"stats","fps":23}), flush=True); '
            'print(json.dumps({"type":"dataset","samples":2,"path":"dataset"}), flush=True); '
            'print("test diagnostic", file=sys.stderr, flush=True); '
            'sys.exit(0 if sys.stdin.readline() == "stop\\n" else 3)'
        )

        def launch(_args, **kwargs):
            process = popen([sys.executable, '-c', script], **kwargs)
            launched.append(process)
            return process

        with patch('desktop.engine._launch_for', return_value=(self.root / 'VaporLock.exe', dict(os.environ))), \
                patch('desktop.engine.subprocess.Popen', side_effect=launch):
            try:
                controller.start(Settings())
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    state = controller.snapshot()
                    if state.state == 'running' and state.dataset and state.messages:
                        break
                    sleep(.01)
                self.assertEqual(state.state, 'running')
                self.assertEqual(state.stats['fps'], 23)
                self.assertEqual(state.dataset, {'samples': 2, 'path': 'dataset'})
                self.assertEqual(state.messages, ('test diagnostic',))
                self.assertEqual(state.provider, 'cpu')
                self.assertTrue(state.observe_only)
                with self.assertRaisesRegex(ValueError, 'Stop the active capture'):
                    controller.start(Settings())
            finally:
                self.assertTrue(controller.close())
            self.assertEqual(launched[0].poll(), 0)
            self.assertEqual(controller.snapshot().state, 'idle')

    def test_preview_does_not_cross_source_boundary(self):
        controller = EngineController(self.paths)
        preview = {'type': 'preview', 'schema': 2, 'control_epoch': 0, 'session': 1, 'source_generation': 1,
                   'frame_id': 1, 'config_revision': 1, 'width': 4, 'height': 4,
                   'observation_age_ms': 0, 'emitted_tick_ms': 500, 'jpeg': base64.b64encode(b'frame').decode()}
        overlay = dict(preview, type='overlay', frame_id=2, source_generation=2, capture_left=0, capture_top=0,
                       tracks=[], selected_track=0, reference_x=0, reference_y=0, source_boundary=True)
        with patch('desktop.engine.native_tick_ms', return_value=500):
            controller._read_record(json.dumps(preview).encode())
            self.assertEqual(controller.snapshot().preview.jpeg, b'frame')
            controller._read_record(json.dumps(overlay).encode())
            self.assertIsNone(controller.snapshot().preview)
            self.assertEqual(controller.snapshot(include_overlay=False).overlay, {})
            self.assertEqual(controller.snapshot().overlay['frame_id'], 2)
            controller._read_record(json.dumps(dict(preview, frame_id=3)).encode())
            self.assertIsNone(controller.snapshot().preview)
            controller._read_record(json.dumps(dict(preview, frame_id=3, source_generation=2)).encode())
            self.assertEqual(controller.snapshot().preview.frame_id, 3)

    def test_cloud_readiness_requires_the_owned_worker_pid(self):
        messages = []
        events = queue.Queue()
        child = SimpleNamespace(events=events, process=SimpleNamespace(pid=42), send=messages.append)
        with patch('cloud_dependencies.command', return_value=['cloud-worker']), \
                patch('cloud_process.OwnedChild', return_value=child):
            cloud = _CloudWorker(self.root, self.root / 'model.cloud', 'dml', 0)
        canceled = threading.Event()
        canceled.set()
        self.assertFalse(cloud.wait_ready(canceled))
        self.assertEqual(messages, [])

        canceled.clear()
        events.put({'type': 'ready', 'edition': cloud.edition, 'pid': 99})
        with patch('cloud_contract.worker_event'), self.assertRaisesRegex(RuntimeError, 'worker identity'):
            cloud.wait_ready(canceled)
        self.assertEqual(messages[0]['native_pipe'], cloud.pipe)
        self.assertEqual(messages[0]['device'], 'directml:0')
        self.assertEqual(cloud.arguments, ['--cloud-pipe', cloud.pipe, '--cloud-worker-pid', '42'])
        cloud.attach(100)
        self.assertEqual(messages[-1], {'command': 'client', 'pid': 100})


if __name__ == '__main__':
    unittest.main()
