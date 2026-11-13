"""Community client regression tests: offline access and retained safety boundaries."""
from pathlib import Path
from contextlib import contextmanager
import ast
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ui'))
RETIRED={'licensing','license_monitor','license_recovery','offline_lease','worker_entitlement',
         'start_policy','update_notifier','statistics_reporter','official_models','release_client',
         'release_archive','installer_terms','catalog_state'}

@contextmanager
def fixture():
    parent=ROOT/'build/public-python-tests';parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as name:yield Path(name)

class PublicClientTests(unittest.TestCase):
    def test_community_heading_translations_include_uppercase_sections(self):
        from localization import translate_text
        headings=('Class priorities and anchors','Manual track lock','Motion compensation',
            'Preview snapshot','Global controls','Profile hotkeys','Local control API','Session details')
        for language in ('zh-CN','de','ru','fr'):
            for heading in headings:
                with self.subTest(language=language,heading=heading):
                    translated=translate_text(heading,language)
                    self.assertNotEqual(translated,heading)
                    self.assertNotIn('premium',translated.casefold())
                    self.assertEqual(translate_text(heading.upper(),language),translated.upper())
            description=translate_text('Configure the display language, theme and setup preferences.',language)
            self.assertNotEqual(description,'Configure the display language, theme and setup preferences.')

    def test_interface_help_has_full_width_and_release_labels_have_no_empty_edition(self):
        source=(ROOT/'ui/vapor_lock_gui.py').read_text(encoding='utf-8')
        self.assertIn("interface_outer.addWidget(help_card)",source)
        self.assertNotIn('lower.left_layout.addWidget(help_card)',source)
        self.assertNotIn('Configure the existing display language, theme, updates and setup preferences.',source)
        self.assertNotIn('Vapor Lock {PRODUCT_VERSION} / {edition}',source)
        self.assertNotIn('Vapor Lock {PRODUCT_VERSION} · {edition}',source)
        self.assertNotIn("Vapor Lock {PRODUCT_VERSION} · {''}",source)

    def test_active_browser_copy_has_no_premium_controls_or_stale_release_values(self):
        copy=json.loads((ROOT/'ui_system/runtime/annotation-native-copy.json').read_text(encoding='utf-8'))
        control='Configure the shared calibration shortcut and Windows-wide controls stored with the current profile.'
        for language in ('en','zh-CN','de','ru','fr'):
            self.assertEqual(copy[language]['Vapor Lock 0.1.3 edition'],'Vapor Lock')
            self.assertNotIn('premium',copy[language][control].casefold())

    def test_all_active_python_compiles(self):
        for path in (ROOT/'ui').glob('*.py'):
            with self.subTest(path=path.name):compile(path.read_bytes(),str(path),'exec')

    def test_configured_frame_rate_reaches_the_native_engine(self):
        from frame_rate import MAX_FRAME_RATE
        from vapor_lock_gui import ControlCenter, EngineBridge
        from vlui_live_protocol import CommandRejected, validate_command
        from vlui_numeric_catalog import parse_numeric_value

        def profile(capture, inference):
            return ControlCenter.prepare_profile_bytes(None, json.dumps({
                "capture_fps": capture, "inference_fps": inference,
            }).encode())

        def capture_command(rate):
            return validate_command("setCaptureRate", json.dumps({"requestId": "1", "fps": rate}))

        for capture, inference in ((256, 240), (144, 360), (MAX_FRAME_RATE, MAX_FRAME_RATE)):
            with self.subTest(capture=capture, inference=inference):
                arguments = EngineBridge.arguments_for(profile(capture, inference), "cpu")
                self.assertEqual(arguments[arguments.index("--capture-fps") + 1], str(capture))
                self.assertEqual(arguments[arguments.index("--inference-fps") + 1], str(inference))
                self.assertEqual(capture_command(capture).payload["fps"], capture)
                self.assertEqual(parse_numeric_value("numeric.inference_fps", str(inference), "expert"), inference)

        self.assertEqual(profile(256, 0).inference_fps, 1)
        for rate in (False, 0, -1, MAX_FRAME_RATE + 1):
            with self.subTest(rate=rate):
                with self.assertRaises(ValueError):
                    profile(rate, 256)
                with self.assertRaises(CommandRejected):
                    capture_command(rate)
        for rate in (False, -1, MAX_FRAME_RATE + 1):
            with self.subTest(inference=rate), self.assertRaises(ValueError):
                profile(256, rate)

    def test_measured_rate_recommendation_keeps_user_ceilings(self):
        from pipeline_tools import recommended_rates

        results = [{"provider": "dml", "fps": 240, "inference_ms": 3, "frames": 480, "seconds": 2}]
        self.assertEqual(recommended_rates(results)["inference_fps"], 216)
        proposal = recommended_rates(results, capture_limit=120, inference_limit=144)
        self.assertEqual((proposal["capture_fps"], proposal["inference_fps"]), (120, 144))

    def test_provider_measurements_default_to_centered_640_crop(self):
        from default_profile import DEFAULT_PROFILE_JSON
        from model_probe import performance_probe_settings
        from vapor_lock_gui import ControlCenter, EngineBridge

        def native_region(settings):
            arguments = EngineBridge.arguments_for(settings, "dml", observe_only=True)
            return arguments[arguments.index("--region") + 1]

        default = ControlCenter.prepare_profile_bytes(None, DEFAULT_PROFILE_JSON)
        self.assertEqual(native_region(default), "0,0,640,640")
        custom = ControlCenter.prepare_profile_bytes(None, json.dumps({
            "region_x": -12, "region_y": 9, "region_width": 100, "region_height": 80,
            "frame_skip": 3, "capture_fps": 88, "inference_fps": 103,
        }).encode())
        standard = performance_probe_settings(custom)
        profiled = performance_probe_settings(custom, use_current_region=True)
        self.assertEqual(native_region(standard), "0,0,640,640")
        self.assertEqual(native_region(profiled), "-12,9,100,80")
        self.assertEqual(native_region(custom), "-12,9,100,80")
        self.assertEqual((standard.frame_skip, profiled.frame_skip, custom.frame_skip), (0, 0, 3))
        self.assertEqual((standard.capture_fps, standard.inference_fps), (88, 103))

    def test_engine_capabilities_accepts_legacy_and_cuda_graph_responses(self):
        from engine_capabilities import EngineCapabilities

        response = {"type": "capabilities", "protocol_version": 1, "cuda_preprocess": False}
        self.assertEqual(EngineCapabilities.decode(json.dumps(response).encode()), (False, False))
        response["cuda_graph_replay"] = True
        self.assertEqual(EngineCapabilities.decode(json.dumps(response).encode()), (False, True))
        response["cuda_graph_replay"] = 1
        self.assertIsNone(EngineCapabilities.decode(json.dumps(response).encode()))

    def test_enforcement_removed_not_only_disabled(self):
        for name in RETIRED:self.assertFalse((ROOT/'ui'/(name+'.py')).exists(),name)
        for path in (ROOT/'ui').glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):self.assertNotIn(node.module,RETIRED,path.name)
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                    self.assertNotIn(node.func.id,{'require_worker_license','require_worker_operation','decide_start_policy'},path.name)
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                    self.assertNotEqual(node.func.attr,'premium_operation_allowed',path.name)

    def test_main_has_no_activation_or_publisher_services(self):
        import vapor_lock_gui as gui
        self.assertFalse(gui.IS_FREE_EDITION)
        self.assertFalse(hasattr(gui,'ActivationDialog'))
        for method in ('start_license_monitor','check_license_monitor','show_license_recovery','show_update_notice'):
            self.assertFalse(hasattr(gui.ControlCenter,method))
        object_marker=object()
        self.assertIs(gui.ControlCenter.feature_control(object_marker),object_marker)

    def test_local_resources_resolve_in_source_tree(self):
        from vlui_fixed_resources import load_fixed_resources
        from vlui_resource_root import browser_resource_root,native_manifest_identity,browser_resource_manifest
        from vlui_context import _types
        from vlui_schema import load_bundled_contract
        from vlui_component_contract import load_component_contract
        _types.cache_clear()
        self.assertEqual(len(_types()),72)
        self.assertEqual(len(load_bundled_contract().components),49)
        self.assertEqual(len(load_component_contract().schema.components),49)
        self.assertEqual(browser_resource_root(),ROOT)
        data=json.loads(browser_resource_manifest().read_text())
        self.assertEqual(len(data['resources']),25)
        for row in data['resources']:
            self.assertTrue(row['file'].startswith('ui_system/runtime/'))
            raw=(ROOT/row['file']).read_bytes()
            self.assertEqual(len(raw),row['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),row['sha256'])
        self.assertEqual(len(load_fixed_resources(reviewed_manifest_sha256=native_manifest_identity())),25)

    def test_cloud_keeps_private_protocol_and_rejects_wrong_pipe(self):
        from cloud_access import edition,pipe_name
        from cloud_contract import CloudInputError
        self.assertEqual(edition(),'premium')
        name='VaporLock.Cloud.premium.'+'f'*32
        self.assertEqual(pipe_name(name),name)
        for bad in ('VaporLock.Cloud.free.'+'f'*32,name+'x','\\\\server\\pipe'):
            with self.assertRaises(CloudInputError):pipe_name(bad)

    def test_cloud_source_runtime_is_self_contained_and_isolated(self):
        import cloud_dependencies as deps
        with fixture() as temporary:
            (temporary/'ui').mkdir();exe=temporary/'.venv/Scripts/python.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'not executed')
            common=temporary/'.venv/Lib/site-packages';common.mkdir(parents=True)
            dml=temporary/'.venv-cloud-dml/Lib/site-packages';dml.mkdir(parents=True)
            with patch.object(deps,'__file__',str(temporary/'ui/cloud_dependencies.py')), patch.object(sys,'_base_executable',str(exe)):
                args=deps.command()
                self.assertEqual(Path(args[0]),exe)
                self.assertEqual(args[1:5],['-I','-S','-B','-u'])
                self.assertEqual(deps.component_roots('directml'),(dml,common))
                with self.assertRaises(ValueError):deps.component_roots('../../other')

    def test_source_engine_multi_and_single_config(self):
        from native_engine_layout import source_engine,provider_ready
        with fixture() as temporary:
            exe=temporary/'build/windows-release/Release/dml/VaporLockDml.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'not executed')
            self.assertEqual(source_engine(temporary,'dml'),exe)
            self.assertFalse(provider_ready(exe,'dml'))
            for name in ('onnxruntime.dll','DirectML.dll'):(exe.parent/name).write_bytes(b'not loaded')
            self.assertTrue(provider_ready(exe,'dml'))
            exe.unlink();single=temporary/'build/windows-release/dml/VaporLockDml.exe';single.parent.mkdir();single.write_bytes(b'not executed')
            self.assertEqual(source_engine(temporary,'dml'),single)

    def test_legacy_synthetic_engine_cannot_admit_cpu_models_or_cuda(self):
        from native_engine_layout import provider_ready, source_engine
        from runtime_generations import RuntimeIntegrityError, current_binding

        with fixture() as temporary:
            release = temporary / 'build/windows-release/Release'
            release.mkdir(parents=True)
            engine = release / 'VaporLock.exe'
            dos_header = bytearray(64)
            dos_header[:2] = b'MZ'
            dos_header[60:64] = (64).to_bytes(4, 'little')
            engine.write_bytes(dos_header + b'PE\x00\x00\x64\x86'
                               + 'ONNX Runtime was not found when Vapor Lock was built.'.encode('utf-16-le'))
            for name in ('onnxruntime.dll', 'onnxruntime_providers_shared.dll', 'onnxruntime_providers_cuda.dll'):
                (release / name).write_bytes(b'not loaded')
            self.assertEqual(source_engine(temporary, 'cpu'), engine)
            self.assertTrue(provider_ready(engine, 'cpu'))
            self.assertFalse(provider_ready(engine, 'cpu', require_model=True))
            self.assertFalse(provider_ready(engine, 'cuda'))
            with self.assertRaisesRegex(RuntimeIntegrityError, 'lacks CUDA ONNX support'):
                current_binding(temporary, require_cuda=True)

    def test_native_environment_excludes_inherited_dll_paths(self):
        from native_engine_layout import native_environment
        value=native_environment(Path('C:/source/engine'),{'PATH':'C:/untrusted','PYTHONPATH':'C:/injected','CUDA_PATH':'C:/cuda','USERPROFILE':'safe'})
        self.assertNotIn('C:/untrusted',value['PATH']);self.assertNotIn('PYTHONPATH',value);self.assertNotIn('CUDA_PATH',value)
        self.assertEqual(value['USERPROFILE'],'safe')

    def test_runtime_setup_distinguishes_download_from_engine_readiness(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import vapor_lock_gui as gui

        manager = gui.AccelerationManager(None)
        manager._provider = "trt"
        progress, completions = [], []
        manager.status.connect(progress.append)
        manager.completed.connect(lambda *result: completions.append(result))
        manager._read_status_line(b'{"message":"Downloading TensorRT RTX..."}\n')
        manager._read_status_line(b'{"message":"TensorRT RTX runtime files are installed.","terminal":true}\n')
        manager._finished(0, gui.QProcess.ExitStatus.NormalExit)
        self.assertEqual(progress, ["Downloading TensorRT RTX..."])
        self.assertEqual(completions, [("trt", True, "TensorRT RTX runtime files are installed.")])

        acceleration = Mock()
        acceleration.is_installed.return_value = False
        acceleration.native_build_message.return_value = "Build VaporLockTensorRt.exe."
        center = SimpleNamespace(
            acceleration=acceleration, engine=SimpleNamespace(provider_label=lambda _provider: "TensorRT RTX"),
            engine_capabilities=Mock(), log=Mock(), settings=SimpleNamespace(provider="trt"),
            refresh_acceleration_controls=Mock(), set_acceleration_detail=Mock(),
        )
        gui.ControlCenter.acceleration_completed(center, *completions[0])
        center.log.assert_called_once_with("INFO", "TensorRT RTX runtime files are installed. Build VaporLockTensorRt.exe.")
        center.set_acceleration_detail.assert_called_once_with(center.log.call_args.args[1])

        manager._read_status_line(b'{"message":"Checksum mismatch.","terminal":true}\n')
        manager._finished(1, gui.QProcess.ExitStatus.NormalExit)
        self.assertEqual(completions[-1], ("trt", False, "Checksum mismatch."))
        self.assertEqual(progress, ["Downloading TensorRT RTX..."])

    def test_source_tensorrt_setup_retains_verified_build_inputs(self):
        from zipfile import ZipFile
        import acceleration_install as setup

        with fixture() as temporary:
            def download(archive_path):
                with ZipFile(archive_path, "w") as archive:
                    prefix = setup.TENSORRT_RTX_SDK_DIRECTORY
                    for member in (*setup.TENSORRT_RTX_SDK_REQUIRED, "include/NvInferRuntime.h"):
                        archive.writestr(f"{prefix}/{member}", member)
                    archive.writestr(f"{prefix}/samples/unneeded.exe", "not extracted")

            with patch.object(setup, "download_tensor_rt_rtx", side_effect=download) as downloader, patch.object(setup, "report"):
                setup.install_tensor_rt_rtx(temporary)
                setup.install_tensor_rt_rtx(temporary)
                downloader.assert_called_once()
            sdk = setup.tensor_rt_sdk_directory(temporary)
            self.assertTrue(setup.tensor_rt_sdk_present(temporary))
            self.assertTrue((sdk / "include/NvInferRuntime.h").is_file())
            self.assertFalse((sdk / "samples").exists())
            self.assertFalse((temporary / "engine/trt").exists())

    @unittest.skipUnless(os.name=='nt', 'Windows Job/pipe smoke')
    def test_source_annotation_worker_owned_ready_close(self):
        import queue,time
        from cloud_dependencies import command
        from cloud_annotation_child import AnnotationChild
        child=AnnotationChild(command(annotation=True),cwd=ROOT)
        seen=[]
        try:
            child.submit({'operation':'annotation','device':'cpu'})
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                try:event=child.events.get_nowait()
                except queue.Empty:event=None
                if event:
                    seen.append(event['type'])
                    if event['type']=='annotation_ready':child.submit({'command':'close'})
                    if event['type']=='error':self.fail(event.get('message'))
                if child.poll() is not None and child.begin_exit_cleanup():break
                time.sleep(.01)
            self.assertIn('annotation_ready',seen);self.assertIn('annotation_closed',seen)
            self.assertEqual(child.poll(),0);self.assertTrue(child.begin_exit_cleanup())
        finally:
            if child.poll() is None:child.terminate();child.process.wait(timeout=5)
            child.close()

    def test_local_runtime_binding_needs_no_release_catalog(self):
        from runtime_generations import current_binding
        with fixture() as temporary:
            engine=temporary/'build/windows-release/Release/VaporLock.exe';engine.parent.mkdir(parents=True);engine.write_bytes(b'not executed')
            result=current_binding(temporary)
            self.assertEqual(result['engine_files'],{'engine/VaporLock.exe':hashlib.sha256(b'not executed').hexdigest()})
            self.assertNotIn('edition',result)

if __name__=='__main__':unittest.main()
