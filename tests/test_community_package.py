"""Local portable-archive checks with generated inert files only."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC=importlib.util.spec_from_file_location('community_package',Path(__file__).resolve().parents[1]/'tools/package_community_release.py')
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PackageTests(unittest.TestCase):
    def test_controller_inventory_requires_a_complete_current_build(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            host = package/'app/controller-host'
            for member in module.CONTROLLER_REQUIRED:
                path = host/member
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'inert data')
            with self.assertRaises(ValueError):
                module.inventory(package, version='2.0.1')
            metadata = {
                'schema_version': 1,
                'version': '2.0.1',
                'files': [
                    {'path': member, 'sha256': module.digest(host/member)}
                    for member in sorted(module.CONTROLLER_REQUIRED)
                ],
            }
            (host/module.CONTROLLER_BUILD).write_text(json.dumps(metadata), encoding='utf-8')
            self.assertEqual(len(module.inventory(package, version='2.0.1')), len(metadata['files']) + 1)
            with self.assertRaises(ValueError):
                module.inventory(package, version='2.0.2')
            (host/'coreclr.dll').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                module.inventory(package, version='2.0.1')

    def test_code_models_remain_but_user_model_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=root/'app/cloud/cloud_source/4.0.0/training_source/cloudstudio/models/network.py'
            source.parent.mkdir(parents=True)
            source.write_text('pass\n')
            self.assertEqual(len(module.inventory(root)),1)
            private=root/'models/private.cloud'
            private.parent.mkdir();private.write_bytes(b'private')
            with self.assertRaises(ValueError):module.inventory(root)

    def test_base_and_cuda_archives_have_exact_split_and_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'package';root.mkdir()
            for name in ('app/client.txt','runtime/cloud/cuda/provider.txt','information/RELEASE_MANIFEST.json','information/RELEASE_CHECKSUMS.sha256'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'inert data')
            rows=module.inventory(root)
            for cuda in (False,True):
                output=Path(folder)/('cuda.zip' if cuda else 'base.zip')
                result=module.archive(root,output,rows,cuda=cuda)
                self.assertTrue(result['all_members_readback'])
                with zipfile.ZipFile(output) as archive:
                    names=archive.namelist()
                    self.assertEqual('VaporLock/runtime/cloud/cuda/provider.txt' in names,cuda)
                    self.assertEqual('VaporLock/app/client.txt' in names,not cuda)

    def test_copy_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'a';target=Path(folder)/'b'
            source.write_bytes(b'new');target.write_bytes(b'preserve')
            with self.assertRaises(ValueError):module.copied(source,target)
            self.assertEqual(target.read_bytes(),b'preserve')


if __name__=='__main__':unittest.main()
