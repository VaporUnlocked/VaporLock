"""Regression coverage for the public .cloud Studio recipe and result boundaries."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ui'))


class CloudStudioTests(unittest.TestCase):
    def test_recipe_roundtrip_preserves_cloud_controls(self):
        from cloud_recipe import load_recipe, resolve_recipe, save_recipe

        expected = resolve_recipe('player', {
            'input_size': 640, 'mask_size': 128, 'batch_size': -1,
            'seed': 4294967295, 'mask_boundary_weight': .75, 'learning_rate': .00035,
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recipe.json'
            save_recipe(str(path), 'player', expected)
            self.assertEqual(load_recipe(str(path)), ('player', expected))
            path.write_text(json.dumps({'format': 'cloudstudio-training-settings', 'version': 1,
                                       'preset': 'player', 'settings': {'input_size': 320}}))
            _, legacy = load_recipe(str(path))
            self.assertEqual(legacy['architecture'], 'cloudvision_s1_v2')
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            resolve_recipe('player', {'init_model_path': 'external.cloud'})

    def test_inspection_is_terminal_and_validates_mask_suppression_counts(self):
        from cloud_contract import worker_event

        value = dict(type='image_inspected', width=1920, height=1080, device='cpu', primary_count=2,
                     detections=[], invalid_contours=0, empty_masks=2,
                     timing_ms={'preprocess': 1., 'inference': 5., 'postprocess': 1., 'total': 7.})
        self.assertTrue(worker_event(value, 'inspect'))
        with self.assertRaisesRegex(ValueError, 'counts'):
            worker_event({**value, 'empty_masks': 1}, 'inspect')

    def test_video_end_of_stream_requires_a_matching_seek(self):
        from cloud_annotation_protocol import AnnotationPeer

        peer = AnnotationPeer(7, 'premium', 'cpu')
        peer.receive(dict(type='annotation_ready', protocol=1, edition='premium', pid=7, device='cpu'))
        request = dict(command='decode', request='a'*32, capability='b'*32,
                       path=str(ROOT / 'video.avi'), output=str(ROOT / 'output'))
        end = dict(type='decode_exhausted', request=request['request'], capability=request['capability'])
        peer.expect({**request, 'video_time_ms': 500})
        self.assertFalse(peer.receive(end))
        peer.expect(request)
        with self.assertRaisesRegex(ValueError, 'capability'):
            peer.receive(end)


if __name__ == '__main__':
    unittest.main()
