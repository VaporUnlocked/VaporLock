"""Community client regression tests: offline access and retained safety boundaries."""
from pathlib import Path
from contextlib import contextmanager
import ast
import hashlib
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
    def test_all_active_python_compiles(self):
        for folder in ('desktop', 'ui'):
            for path in (ROOT / folder).glob('*.py'):
                with self.subTest(path=path.name):
                    compile(path.read_bytes(), str(path), 'exec')

    def test_enforcement_removed_not_only_disabled(self):
        for name in RETIRED:self.assertFalse((ROOT/'ui'/(name+'.py')).exists(),name)
        for path in (*((ROOT / 'desktop').glob('*.py')), *((ROOT / 'ui').glob('*.py'))):
            tree=ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):self.assertNotIn(node.module,RETIRED,path.name)
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                    self.assertNotIn(node.func.id,{'require_worker_license','require_worker_operation','decide_start_policy'},path.name)
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                    self.assertNotEqual(node.func.attr,'premium_operation_allowed',path.name)

    def test_desktop_entrypoint_does_not_import_legacy_renderers(self):
        modules = ('vapor_lock_gui', 'vlui_qt_host', 'vlui_native_shell', 'QtWebEngineWidgets')
        for name in ('__main__.py', 'window.py', 'controls.py', 'workspaces.py'):
            source = (ROOT / 'desktop' / name).read_text(encoding='utf-8')
            with self.subTest(name=name):
                for module in modules:
                    self.assertNotIn(module, source)

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

    def test_native_environment_excludes_inherited_dll_paths(self):
        from native_engine_layout import native_environment
        value=native_environment(Path('C:/source/engine'),{'PATH':'C:/untrusted','PYTHONPATH':'C:/injected','CUDA_PATH':'C:/cuda','USERPROFILE':'safe'})
        self.assertNotIn('C:/untrusted',value['PATH']);self.assertNotIn('PYTHONPATH',value);self.assertNotIn('CUDA_PATH',value)
        self.assertEqual(value['USERPROFILE'],'safe')

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
