from pathlib import Path
from contextlib import contextmanager
import importlib.util,json,tempfile,unittest,uuid
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('community_bundle',ROOT/'tools/build_community_bundle.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
@contextmanager
def fixture():
    parent=ROOT/'build'/('community-bundle-tests-'+uuid.uuid4().hex[:10]);parent.mkdir(parents=True)
    with tempfile.TemporaryDirectory(dir=parent) as name:yield Path(name)
class BundleTests(unittest.TestCase):
    def test_dependency_allowlist_excludes_prior_clients_and_customer_state(self):
        rows=[{'path':p,'bytes':0,'sha256':'0'*64} for p in ('app/VaporLockControlCenter.exe','app/VaporLockDatasetTools.exe','models/private.cloud','profiles/default.json','runtime/cloud/cpu/torch/__init__.py','app/cloud/assets/cloud-annotation/LICENSE.txt','server_runtime/key.pem')]
        admitted=builder.dependency_rows({'files':rows})
        self.assertEqual([r['path'] for r in admitted],[rows[4]['path'],rows[5]['path']])
    def test_manifest_paths_never_escape_or_alias(self):
        for path in ('../private','runtime/cloud/cpu/../secret','C:/private','runtime\\cloud\\cpu\\x','/private','runtime/cloud/cpu//x'):
            with self.subTest(path=path),self.assertRaises(ValueError):builder.dependency_rows({'files':[{'path':path}]})
        with self.assertRaises(ValueError):builder.dependency_rows({'files':[{'path':'runtime/cloud/cpu/A','bytes':0,'sha256':'0'*64},{'path':'runtime/cloud/cpu/a','bytes':0,'sha256':'0'*64}]})
    def test_copy_rejects_wrong_original_or_modified_bytes(self):
        with fixture() as temporary:
            source=temporary/'in';source.write_bytes(b'expected');identity=builder.record(source)
            builder.copy_verified(source,temporary/'copy',identity)
            self.assertEqual((temporary/'copy').read_bytes(),b'expected')
            source.write_bytes(b'changed!')
            with self.assertRaises(RuntimeError):builder.copy_verified(source,temporary/'never-created',identity)
            self.assertFalse((temporary/'never-created').exists())
    def test_component_inventory_detects_added_and_modified_files(self):
        with fixture() as temporary:
            (temporary/'module.py').write_text('pass\n');identity=builder.inventory(temporary,'sdk')
            builder.validate_inventory(temporary,identity['sha256'],'sdk')
            (temporary/'module.py').write_text('evil\n')
            with self.assertRaises(Exception):builder.validate_inventory(temporary,identity['sha256'],'sdk')
    def test_resources_package_has_no_server_workspace_dependency(self):
        with fixture() as temporary:
            identity=builder.stage_resources(temporary)
            self.assertEqual(len(identity),64)
            data=json.loads((temporary/'vlui/runtime/resources.json').read_bytes())
            for row in data['resources']:
                self.assertTrue(row['file'].startswith('vlui/payload/'))
                self.assertEqual(builder.record(temporary/row['file']),{k:row[k] for k in ('bytes','sha256')})
    def test_actual_import_audit_uses_separate_gui_snapshot(self):
        with fixture() as temporary:
            worker='ui/cloud_access.py';gui='ui/localization.py'
            builder.write(temporary/'stage.json',{'sources':{worker:builder.record(ROOT/worker)}})
            builder.write(temporary/'gui-plan.json',{'sources':{gui:builder.record(ROOT/gui)}})
            for phase,source,name in (('CloudTools','source',worker),('VaporLockControlCenter','gui-source',gui)):
                folder=temporary/phase/'work';folder.mkdir(parents=True)
                (folder/'Analysis-00.toc').write_text(repr([(str(temporary/source/Path(name).name),'PYMODULE')]))
            result=builder.component_sources(temporary)
            self.assertEqual(len(result),2)
            self.assertIn(gui,result['VaporLockControlCenter/work/Analysis-00.toc'])
            self.assertIn(worker,result['CloudTools/work/Analysis-00.toc'])

    def test_stdlib_zip_canonicalization_preserves_module_bytes(self):
        import zipfile
        with fixture() as temporary:
            for name,order in (('one.zip',('z.pyc','a.pyc')),('two.zip',('a.pyc','z.pyc'))):
                with zipfile.ZipFile(temporary/name,'w') as archive:
                    for member in order:
                        info=zipfile.ZipInfo(member,(1980,1,1,0,0,0));archive.writestr(info,member.encode())
                builder.canonical_base_archive(temporary/name)
            self.assertEqual((temporary/'one.zip').read_bytes(),(temporary/'two.zip').read_bytes())
            with zipfile.ZipFile(temporary/'one.zip') as archive:self.assertEqual(archive.read('z.pyc'),b'z.pyc')

    def test_frozen_source_snapshot_has_no_retired_enforcement(self):
        with fixture() as temporary:
            sources=builder.source_snapshot(temporary/'source')
            self.assertGreater(len(sources),100)
            for name in builder.RETIRED:self.assertFalse((temporary/'source'/(name+'.py')).exists())
if __name__=='__main__':unittest.main()
