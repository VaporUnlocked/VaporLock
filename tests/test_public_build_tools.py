"""Source-only manifest/build argument tests; no downloads or product run."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('public_manifests', Path(__file__).resolve().parents[1] / 'tools/refresh_public_manifests.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PublicManifests(unittest.TestCase):
    def test_refresh_uses_current_source_and_no_private_intake_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in ('cloud_source/4.0.0/runtime_sdk/cloud_sdk/__init__.py',
                             'cloud_source/4.0.0/training_source/cloudstudio/__init__.py',
                             'ui_system/runtime/view.js'):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('value = 1\n')
            resources = root / 'ui_system/runtime/resources.json'
            resources.write_text(json.dumps({'resourceVersion': 1, 'resources': [
                {'file': 'ui_system/runtime/view.js', 'bytes': 0, 'sha256': 'old'}]}))
            result = module.refresh(root)
            self.assertEqual(result, {'cloud_files': 2, 'ui_resources': 1})
            manifest = json.loads((root / 'cloud_source/4.0.0/IMPORT_MANIFEST.json').read_bytes())
            self.assertEqual(set(manifest), {'files', 'version'})
            first = json.loads(resources.read_bytes())['resources'][0]['sha256']
            (root / 'ui_system/runtime/view.js').write_text('value = 2\n')
            module.refresh(root)
            self.assertNotEqual(first, json.loads(resources.read_bytes())['resources'][0]['sha256'])

    def test_refresh_rejects_resource_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for branch in ('runtime_sdk', 'training_source'):
                path = root / 'cloud_source/4.0.0' / branch / 'source.py'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('pass\n')
            resources = root / 'ui_system/runtime/resources.json'
            resources.parent.mkdir(parents=True)
            resources.write_text(json.dumps({'resources': [{'file': '../private.txt'}]}))
            with self.assertRaises(ValueError):
                module.refresh(root)

    def test_relative_members_reject_windows_aliases(self):
        for value in ('C:private.txt', 'C:/private.txt', '\\private.txt', '/private.txt',
                      '../private.txt', 'ui/../private.txt', 'ui\\file.js', 'ui//file.js'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.relative_member(value)

    def test_manifest_hardlink_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / 'original.json'
            original.write_bytes(b'preserve')
            alias = root / 'alias.json'
            os.link(original, alias)
            untouched = root / 'other.json'
            untouched.write_bytes(b'unchanged')
            with self.assertRaises((ValueError, OSError, RuntimeError)):
                module.write_outputs([(untouched, 'new'), (alias, 'bad')])
            self.assertEqual(original.read_bytes(), b'preserve')
            self.assertEqual(untouched.read_bytes(), b'unchanged')


if __name__ == '__main__':
    unittest.main()
