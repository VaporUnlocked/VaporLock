"""Verify the desktop profile codec independently of the graphical shell."""

import json
from pathlib import Path
import tempfile
import unittest

from desktop.settings import (
    MAX_PROFILE_BYTES,
    ProductPaths,
    Settings,
    decode_profile,
    read_profile,
    save_profile,
    validate_settings,
)


class DesktopSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.paths = ProductPaths.from_root(self.directory / "VaporLock")
        self.profile = self.paths.profiles / "test.json"

    def decode(self, value):
        return decode_profile(json.dumps(value).encode("utf-8"), self.paths)

    def test_default_settings_are_valid(self):
        validate_settings(Settings())
        self.assertEqual(self.decode({}).profile_name, "Default")
        self.assertEqual(self.paths.portable(""), "")

    def test_nested_profile_migrates_paths_settings_and_legacy_defaults(self):
        settings = self.decode({
            "general": {"profileName": "Bench"},
            "model": {"path": "models/bench.onnx", "provider": "dml", "confidenceThreshold": .7},
            "capture": {"surface": "all", "region": "10, 20, 800, 600", "fps": 45},
            "tracking": {"maxLostFrames": 9, "predictionSeconds": 0},
            "detection": {"nmsEnabled": False},
            "selection": {"strategy": "confidence", "lostTargetTimeoutFrames": 6},
            "movement": {"activationKey": "LeftShift", "maxStep": 7},
            "dataset": {"directory": "datasets/bench"},
        })
        self.assertEqual(settings.profile_name, "Bench")
        self.assertEqual(Path(settings.model_path), self.paths.resolve("models/bench.onnx"))
        self.assertEqual(Path(settings.dataset_directory), self.paths.resolve("datasets/bench"))
        self.assertEqual(settings.provider, "dml")
        self.assertEqual(settings.confidence, .7)
        self.assertEqual(settings.monitor, 0)
        self.assertEqual(
            (settings.region_x, settings.region_y, settings.region_width, settings.region_height),
            (10, 20, 800, 600),
        )
        self.assertEqual(settings.capture_fps, 45)
        self.assertEqual(settings.strategy, 1)
        self.assertEqual(settings.track_retention_ms, 150)
        self.assertEqual(settings.lost_target_timeout_ms, 100)
        self.assertFalse(settings.predict_lost)
        self.assertFalse(settings.nms_enabled)
        self.assertEqual(settings.activation_key, "shift")
        self.assertEqual(settings.max_speed, 420.0)
        self.assertTrue(settings.snap_bypasses_confirmation)

    def test_flat_roundtrip_preserves_internal_and_external_paths(self):
        external = self.directory / "training" / "dataset.yaml"
        settings = self.decode({
            "profile_name": "Проверка",
            "model_path": "models/work.cloud",
            "training_dataset": str(external),
            "cloud_project": "datasets/project",
            "max_speed": 801.5,
            "snap_bypasses_confirmation": False,
            "ultralytics_key_protected": "dpapi-ul-v1:YQ==",
            "class_policies": [{"class_id": 8, "priority": 2, "anchor_mode": "top"}],
        })

        save_profile(self.profile, settings, self.paths)
        raw = json.loads(self.profile.read_text(encoding="utf-8"))
        self.assertEqual(Path(raw["model_path"]), Path("models/work.cloud"))
        self.assertEqual(Path(raw["cloud_project"]), Path("datasets/project"))
        self.assertEqual(Path(raw["training_dataset"]), external)
        self.assertEqual(read_profile(self.profile, self.paths), settings)
        self.assertEqual(list(self.paths.profiles.glob("*.tmp")), [])

    def test_rejects_invalid_values_without_overwriting_a_profile(self):
        settings = Settings()
        save_profile(self.profile, settings, self.paths)
        original = self.profile.read_bytes()
        settings.capture_fps = 0
        with self.assertRaises(ValueError):
            save_profile(self.profile, settings, self.paths)
        self.assertEqual(self.profile.read_bytes(), original)
        for data in (
            b'{"profile_name":"A","profile_name":"B"}',
            b'{"confidence":NaN}',
            b'{"model_path":123}',
            b'{"model":[]}',
            b'{"dataset_review_class_slots":[true]}',
            b'{"class_policies":[{"class_id":1},{"class_id":1}]}',
            b'{"ultralytics_key_protected":"plaintext"}',
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_profile(data, self.paths)

    def test_rejects_profile_larger_than_read_limit(self):
        self.paths.profiles.mkdir(parents=True)
        self.profile.write_bytes(b" " * (MAX_PROFILE_BYTES + 1))
        with self.assertRaises(ValueError):
            read_profile(self.profile, self.paths)


if __name__ == "__main__":
    unittest.main()
