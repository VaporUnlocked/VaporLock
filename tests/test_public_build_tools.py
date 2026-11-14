"""Source-only manifest/build argument tests; no downloads or product run."""
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('public_manifests', Path(__file__).resolve().parents[1] / 'tools/refresh_public_manifests.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

BUILD_SPEC = importlib.util.spec_from_file_location('public_build', Path(__file__).resolve().parents[1] / 'tools/build_public_client.py')
build = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(build)


class NativeDirectMLRestore(unittest.TestCase):
    def test_extracts_native_sdk_and_reuses_it(self):
        package = BytesIO()
        transitive_header = build.ORT_DML_INCLUDE + 'onnxruntime_cxx_inline.h'
        with zipfile.ZipFile(package, 'w') as archive:
            for member in (*build.ORT_DML_FILES, '../../outside.h',
                           'runtimes/win-arm64/native/onnxruntime.dll'):
                archive.writestr(member, b'test')
        package.seek(0)
        with tempfile.TemporaryDirectory() as folder, patch.object(build, 'urlopen', return_value=package) as download:
            args = (Path(folder), 'Microsoft.ML.OnnxRuntime.DirectML', build.ORT_DML_VERSION, build.ORT_DML_FILES)
            restored = build.restore_nuget(*args)
            self.assertTrue((restored / transitive_header).is_file())
            self.assertFalse((restored / 'runtimes/win-arm64').exists())
            self.assertEqual(build.restore_nuget(*args), restored)
            download.assert_called_once()

    def test_missing_runtime_leaves_no_incomplete_cache(self):
        package = BytesIO()
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('include/DirectML.h', b'test')
        package.seek(0)
        with tempfile.TemporaryDirectory() as folder, patch.object(build, 'urlopen', return_value=package):
            cache = Path(folder)
            with self.assertRaisesRegex(ValueError, 'bin/x64-win/DirectML.dll'):
                build.restore_nuget(cache, 'Microsoft.AI.DirectML', build.DIRECTML_VERSION, build.DIRECTML_FILES)
            self.assertEqual(list(cache.iterdir()), [])

    def test_native_onnx_sdk_uses_verified_existing_cuda_archive(self):
        import hashlib

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'cuda-cache.nupkg'
            with zipfile.ZipFile(archive, 'w') as package:
                for member in build.ORT_BASE_FILES:
                    package.writestr(member, b'test')
                package.writestr(build.ORT_DML_NATIVE + 'onnxruntime_providers_cuda.dll', b'optional')
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            args = (root / 'build-cache', build.ORT_BASE_PACKAGE, build.ORT_BASE_VERSION, build.ORT_BASE_FILES)
            with patch.object(build, 'urlopen') as download:
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    build.restore_nuget(*args, local_archive=archive, sha256='0' * 64)
                sdk = build.restore_nuget(*args, local_archive=archive, sha256=checksum)
                download.assert_not_called()
            self.assertTrue((sdk / 'buildTransitive/native/include/onnxruntime_cxx_api.h').is_file())
            self.assertTrue((sdk / (build.ORT_DML_NATIVE + 'onnxruntime.dll')).is_file())
            self.assertFalse((sdk / (build.ORT_DML_NATIVE + 'onnxruntime_providers_cuda.dll')).exists())


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
