"""Disposable filesystem tests for public-export boundaries; no product launch."""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("public_export", Path(__file__).resolve().parents[1] / "tools/export_public_source.py")
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for relative in exporter.REQUIRED_ROOT_FILES:
            self.put(relative, "Public build input\n")
        self.put("app/main.cpp", "int main() { return 0; }\n")
        self.output = self.root / "public.zip"

    def put(self, relative, value):
        file = self.source / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value, encoding="utf-8")
        return file

    def test_excludes_private_service_distribution_and_generated_inputs(self):
        for relative in ("Boss Use Only - No Access Granted/passwords.txt", "license_service/core.py",
                "server_runtime/secret.txt", "Website/api/admin.php", "installer/client.py", "models/best.cloud",
                "profiles/default.json", ".venv/Lib/leak.py", "build/copy/main.cpp", "dist/client.exe",
                "tools/announce_0200.py", "docs/INTERNAL_HANDOVER.txt", "app/__pycache__/cached.py",
                "controller_host/hidmaestro_integration/resources/resource-0000.bin"):
            self.put(relative, "MUST NEVER EXPORT")
        self.put("ui_system/runtime/live.html", "<html></html>")
        self.put("cloud_source/4.0.0/training_source/cloudstudio/models/network.py", "pass\n")
        self.put("third_party/monocypher/LICENSE.md", "Third party license\n")
        names = {name for name, _ in exporter.select_files(self.source)}
        self.assertIn("app/main.cpp", names)
        self.assertIn("ui_system/runtime/live.html", names)
        self.assertIn("cloud_source/4.0.0/training_source/cloudstudio/models/network.py", names)
        self.assertIn("third_party/monocypher/LICENSE.md", names)
        self.assertFalse(any("MUST" in file.read_text() for name, file in exporter.select_files(self.source)))

    def test_archive_paths_reject_traversal_and_windows_aliases(self):
        for name in ("../x", "/x", "app/../../x", "C:/x", "app\\x", "app//x", "app/./x"):
            with self.subTest(name=name), self.assertRaises(exporter.ExportError):
                exporter.safe_relative(name)

    def test_private_client_filenames_are_blocked_case_insensitively(self):
        for name in ("LOCAL-API.JSON", "Local-Api.Json", "ACTIVATION.JSON",
                "LICENSE-STATE.JSON", "Installation-Identity.Json", "Admin-Client.Json", "agents.MD"):
            self.put("ui/" + name, "private configuration")
            self.assertFalse(exporter.allowed("ui/" + name))
        selected = {name for name, _ in exporter.select_files(self.source)}
        self.assertFalse(any(name.casefold().endswith("local-api.json") for name in selected))

    def test_client_data_is_explicit_source_only_not_generic_json_or_text(self):
        for name in ("ui/passwords.TXT", "ui/CREDENTIALS.json", "config/secrets.txt",
                "ui/user_key_registry_backup.json", "ui/activation.backup.json",
                "ui/offline-lease-state.json", "config/random-records.json", "ui/support-notes.txt",
                "assets/private-records.json", "cloud_source/4.0.0/config/arbitrary.json"):
            self.put(name, "private user record")
            self.assertFalse(exporter.allowed(name))
        self.put("ui/requirements.txt", "PySide6\n")
        self.put("assets/cloud-annotation/NOTICE.txt", "Third-party notice\n")
        selected = {name for name, _ in exporter.select_files(self.source)}
        self.assertIn("ui/requirements.txt", selected)
        self.assertIn("assets/cloud-annotation/NOTICE.txt", selected)
        self.assertFalse(any("private user record" in path.read_text() for _, path in exporter.select_files(self.source)))

    def test_retained_output_allows_verified_hardlink_without_reopening_bytes(self):
        partial = self.root / "retained.incomplete"
        final = self.root / "retained.zip"
        with exporter._create_retained_output(partial) as file:
            file.write(b"retained archive identity")
            file.flush()
            frozen = exporter._output_identity(partial, file, links=1)
            os.link(partial, final)
            exporter._output_identity(final, file, links=2, frozen=frozen)

    def test_archive_mutation_between_readback_and_commit_is_blocked_or_rejected(self):
        original_inventory = exporter.inventory
        calls, denied = 0, []
        def mutate(rows):
            nonlocal calls
            calls += 1
            if calls == 2:
                try:
                    self.output.with_suffix(".zip.incomplete").write_bytes(b"changed archive")
                except PermissionError:
                    denied.append(True)
            return original_inventory(rows)
        with patch.object(exporter, "inventory", side_effect=mutate):
            if os.name == "nt":
                receipt = exporter.export_source(self.source, self.output)
                self.assertEqual([True], denied)
                self.assertEqual(receipt["archive_sha256"], exporter.sha256(self.output.read_bytes()))
            else:
                with self.assertRaises(exporter.ExportError):
                    exporter.export_source(self.source, self.output)
                self.assertFalse(self.output.with_suffix(".zip.verified.json").exists())

    def test_archive_path_swap_at_final_link_is_blocked_or_rejected(self):
        original_link = os.link
        denied = []
        def swap_before_link(source, destination, *args, **kwargs):
            if Path(destination) == self.output:
                source = Path(source)
                try:
                    source.rename(source.with_suffix(".swapped"))
                except PermissionError:
                    denied.append(True)
                else:
                    source.write_bytes(b"substituted archive")
            return original_link(source, destination, *args, **kwargs)
        with patch.object(exporter.os, "link", side_effect=swap_before_link):
            if os.name == "nt":
                receipt = exporter.export_source(self.source, self.output)
                self.assertEqual([True], denied)
                self.assertEqual(receipt["archive_sha256"], exporter.sha256(self.output.read_bytes()))
            else:
                with self.assertRaises(exporter.ExportError):
                    exporter.export_source(self.source, self.output)
                self.assertFalse(self.output.with_suffix(".zip.verified.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows no-delete directory lease check")
    def test_source_directory_is_retained_before_enumeration(self):
        ui = self.source / "ui"
        self.put("ui/client.py", "# source\n")
        original_scandir = os.scandir
        denied = []
        def swap_before_scandir(path):
            if Path(path) == ui:
                try:
                    ui.rename(self.source / "moved-ui")
                except PermissionError:
                    denied.append(True)
                else:
                    self.fail("Source directory could move before its enumeration")
            return original_scandir(path)
        with patch.object(exporter.os, "scandir", side_effect=swap_before_scandir):
            selected = {name for name, _ in exporter.select_files(self.source)}
        self.assertEqual([True], denied)
        self.assertIn("ui/client.py", selected)

    def test_active_ui_files_do_not_export_historical_notes_or_proofs(self):
        active = ("ui_system/runtime/live.html",
            "ui_system/proposals/contract-v1-r5/style.schema.json",
            "ui_system/contracts/component-contract-v1-r1/components-v1.json",
            "ui_system/handoffs/foundation-v1-r1/components-v1.json")
        omitted = ("ui_system/runtime/internal-review.txt",
            "ui_system/runtime/bundled/private-proof.json",
            "ui_system/renderer/unused-example.js",
            "ui_system/proposals/contract-v1-r5/review-notes.txt",
            "ui_system/proposals/trusted-display-context-v1-r3/proposal.txt",
            "ui_system/handoffs/foundation-v1-r1/private-report.json",
            "ui_system/handoffs/native-components-v1-r2/acceptance-proof.json")
        for name in active:
            self.put(name, "active source resource")
        for name in omitted:
            self.put(name, "MUST NEVER EXPORT")
        selected = {name for name, _ in exporter.select_files(self.source)}
        self.assertTrue(set(active) <= selected)
        self.assertTrue(set(omitted).isdisjoint(selected))

    def runtime_manifest(self):
        entries = []
        for name in sorted(exporter.UI_RUNTIME_FILES - {"ui_system/runtime/resources.json"}):
            payload = "public resource: " + name
            self.put(name, payload)
            encoded = payload.encode("utf-8")
            entries.append({"file": name, "bytes": len(encoded), "sha256": exporter.sha256(encoded)})
        self.put("ui_system/runtime/resources.json", json.dumps({"resources": entries}))
        return entries

    def test_runtime_manifest_requires_exact_exported_payload_closure(self):
        entries = self.runtime_manifest()
        selected = {name for name, _ in exporter.select_files(self.source)}
        self.assertTrue(exporter.UI_RUNTIME_FILES <= selected)
        # An old provenance index must not become a new read/export authority.
        entries[0]["file"] = "ui_system/handoffs/foundation-v1-r1/private-proof.json"
        self.put(entries[0]["file"], "private proof")
        self.put("ui_system/runtime/resources.json", json.dumps({"resources": entries}))
        with self.assertRaises(exporter.ExportError):
            exporter.select_files(self.source)

    def test_runtime_manifest_detects_missing_or_changed_payload(self):
        entries = self.runtime_manifest()
        selected = entries[0]["file"]
        self.put(selected, "changed source")
        with self.assertRaises(exporter.ExportError):
            exporter.select_files(self.source)
        (self.source / selected).unlink()
        with self.assertRaises(exporter.ExportError):
            exporter.select_files(self.source)

    def test_controller_authored_template_is_distinct_from_saved_profiles(self):
        template = "controller_host/profiles/vapor-lock-xbox-one-windows.json"
        self.put(template, '{"id":"vapor-lock-xbox-one-windows"}')
        for name in ("profiles/default.json", "controller_host/profiles/customer.json",
                "controller_host/profiles/saved/private.json"):
            self.put(name, "MUST NEVER EXPORT")
        selected = {name for name, _ in exporter.select_files(self.source)}
        self.assertIn(template, selected)
        self.assertFalse(any(name in selected for name in (
            "profiles/default.json", "controller_host/profiles/customer.json",
            "controller_host/profiles/saved/private.json")))

    def test_public_tests_use_an_exact_allowlist(self):
        active = ("tests/native_public_access_tests.cpp", "tests/native_tuning_tests.cpp",
            "tests/aim_output_tests.h", "tests/capture_recovery_tests.h",
            "tests/test_public_client_access.py", "tests/test_public_build_tools.py")
        omitted = ("tests/native_license_guard_tests.cpp", "tests/start_policy_bridge.cpp",
            "tests/test_offline_lease.py", "tests/test_worker_lease.py",
            "tests/test_backup_receipt_semantics.py", "tests/private_fixture.py",
            "tests/fixtures/private-proof.json")
        for name in (*active, *omitted):
            self.put(name, "test source")
        selected = {name for name, _ in exporter.select_files(self.source)}
        self.assertTrue(set(active) <= selected)
        self.assertTrue(set(omitted).isdisjoint(selected))

    def test_secret_scan_never_echoes_the_value(self):
        token = "ul_" + "a" * 40
        self.put("ui/config.py", "api_key = '" + token + "'\n")
        with self.assertRaises(exporter.ExportError) as raised:
            exporter.export_source(self.source, self.output)
        self.assertIn("ultralytics-api-token", str(raised.exception))
        self.assertNotIn(token, str(raised.exception))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".zip.incomplete").exists())
        self.assertEqual([], exporter.secret_findings(b'api_key = "YOUR_EXAMPLE_KEY_HERE"'))

    def test_selected_hardlinks_are_rejected(self):
        file = self.source / "app/main.cpp"
        os.link(file, self.source / "app/alias.cpp")
        with self.assertRaises(exporter.ExportError):
            exporter.select_files(self.source)

    def test_selected_symlink_is_rejected_without_reading_target(self):
        target = self.root / "not-authorized"
        target.mkdir()
        (target / "secret.py").write_text("not authorized", encoding="utf-8")
        try:
            os.symlink(target, self.source / "ui", target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation is unavailable to this test token")
        with self.assertRaises(exporter.ExportError):
            exporter.select_files(self.source)

    def test_successful_zip_is_completely_readback_verified_and_no_overwrite(self):
        self.put("tools/export_public_source.py", "# export helper\n")
        receipt = exporter.export_source(self.source, self.output)
        self.assertTrue(receipt["all_members_readback_verified"])
        self.assertTrue(receipt["source_reinventory_verified"])
        self.assertFalse(receipt["publication_performed"])
        with zipfile.ZipFile(self.output) as archive:
            manifest = json.loads(archive.read("SOURCE_MANIFEST.json"))
            self.assertEqual(receipt["file_count"], len(manifest["files"]))
            for entry in manifest["files"]:
                self.assertEqual(entry["sha256"], exporter.sha256(archive.read(entry["path"])))
        self.assertTrue(self.output.with_suffix(".zip.verified.json").is_file())
        with self.assertRaises(exporter.ExportError):
            exporter.export_source(self.source, self.output)

    def test_source_addition_prevents_completion(self):
        original_select = exporter.select_files
        calls = 0
        def mutate_then_select(source, suite=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.put("app/added.cpp", "int additional;\n")
            return original_select(source, suite)
        with patch.object(exporter, "select_files", side_effect=mutate_then_select):
            with self.assertRaises(exporter.ExportError):
                exporter.export_source(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_suite_excludes_uncleared_asset_and_generated_binaries(self):
        self.assertFalse(exporter.suite_allowed("assets/characters/Ghost-MW2019.glb"))
        self.assertTrue(exporter.suite_allowed("assets/characters/SWAT.glb"))
        self.assertTrue(exporter.suite_allowed("source/main.cpp"))
        self.assertFalse(exporter.suite_allowed("build/main.cpp"))
        self.assertFalse(exporter.suite_allowed("Official Aim Testing Suite.exe"))

    def test_output_must_be_outside_source(self):
        with self.assertRaises(exporter.ExportError):
            exporter.export_source(self.source, self.source / "export.zip")

    def test_authored_credential_protection_code_kept_but_private_directories_denied(self):
        self.assertTrue(exporter.allowed('ui/ultralytics_credentials.py'))
        self.assertFalse(exporter.allowed('ui/credentials-backup/helper.py'))
        self.assertFalse(exporter.allowed('ui/credentials-backup', directory=True))
        self.assertFalse(exporter.allowed('ui/passwords-backup.txt'))
        self.assertFalse(exporter.suite_allowed('source/passwords.txt'))
        self.assertFalse(exporter.suite_allowed('vendor/credentials-backup.txt'))
        self.assertFalse(exporter.suite_allowed('source/LOCAL-API.JSON'))

    def test_declared_cloud_sdk_member_cannot_be_silently_omitted(self):
        self.put('cloud_source/4.0.0/IMPORT_MANIFEST.json',json.dumps({'files':[
            {'path':'training_source/cloudstudio/_vendor/mobile_sam/PROVENANCE.json','bytes':2,'sha256':exporter.sha256(b'{}')}]}))
        with self.assertRaises(exporter.ExportError):exporter.select_files(self.source)
        self.put('cloud_source/4.0.0/training_source/cloudstudio/_vendor/mobile_sam/PROVENANCE.json','{}')
        names={name for name,_ in exporter.select_files(self.source)}
        self.assertIn('cloud_source/4.0.0/training_source/cloudstudio/_vendor/mobile_sam/PROVENANCE.json',names)

    def test_required_qt_license_texts_are_explicitly_included(self):
        self.assertTrue(exporter.allowed('third_party/qt',directory=True))
        self.assertTrue(exporter.allowed('third_party/qt/LICENSE-GPL-3.0.txt'))
        self.assertTrue(exporter.allowed('third_party/qt/LICENSE-LGPL-3.0.txt'))
        self.assertFalse(exporter.allowed('third_party/qt/arbitrary.txt'))


if __name__ == "__main__":
    unittest.main()
