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

    def test_yolo_health_reports_split_leakage_and_label_quality(self):
        from PIL import Image
        from dataset_health import scan_yolo

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ('images/train', 'images/val', 'labels/train', 'labels/val'):
                (root / folder).mkdir(parents=True)
            (root / 'classes.txt').write_text('Player\nVehicle\n', encoding='utf8')
            (root / '.vaporlock-review.json').write_text(json.dumps(
                {'decisions': {'images/train/excluded.png': 'rejected'}}))
            source = Image.new('RGB', (128, 96), (50, 100, 140))
            source.save(root / 'images/train/original.png')
            source.save(root / 'images/val/copy.png', compress_level=0)
            source.save(root / 'images/train/excluded.png')
            Image.new('RGB', source.size, (53, 103, 143)).save(root / 'images/val/near.png')
            Image.new('RGB', source.size, (123, 23, 30)).save(root / 'images/train/missing.png')
            (root / 'labels/train/original.txt').write_text(
                10 * '0 .5 .5 .5 .5\n' + '1 .5 .5 .001 .001\n', encoding='utf8')
            (root / 'labels/val/copy.txt').touch()
            (root / 'labels/val/near.txt').write_text('0 nope\n', encoding='utf8')
            progress = []
            report = scan_yolo(root, 640, lambda *counts: progress.append(counts))
            for expected in ('Exact duplicate groups crossing splits: 1',
                             'Possible near-duplicate frames crossing splits: 1',
                             'Images missing label files: 1',
                             'Unreadable or invalid label files: 1',
                             'Tiny targets: 1', 'Vehicle', 'underrepresented',
                             '1 labeled or reviewed negatives', '1 excluded by review'):
                self.assertIn(expected, report)
            self.assertEqual(progress[-1], (4, 4))

    def test_cloud_health_can_inspect_split_issues_before_training(self):
        from PIL import Image
        from cloud_project import ProjectLease
        from dataset_health import scan_cloud

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'images').mkdir()
            records = []
            for name, split, reviewed in [('a', 'train', True), ('b', 'val', True),
                                          ('c', 'test', False)]:
                relative = f'images/{name}.png'
                Image.new('RGB', (64, 64)).save(root / relative)
                records.append(dict(id=name, file=relative, split=split, reviewed=reviewed,
                                    width=64, height=64, group_id='recording', annotations=[]))
            project = root / 'project.json'
            project.write_text(json.dumps(dict(schema_version=1, classes=['player'], images=records)))
            before = project.read_bytes()
            with self.assertRaisesRegex(ValueError, 'occurs in multiple splits'):
                with ProjectLease(root):
                    pass
            report = scan_cloud(root, 640, lambda *_: None)
            self.assertIn('Exact duplicate groups crossing splits: 1', report)
            self.assertIn('1 reviewed recording groups cross dataset splits.', report)
            self.assertIn('Unreviewed project images: 1', report)
            self.assertEqual(project.read_bytes(), before)

    def test_health_worker_events_are_bounded_and_validated(self):
        from cloud_contract import worker_event

        operation = 'dataset_health'
        progress = dict(type='dataset_health_progress', scanned=1, total=3)
        result = dict(type='dataset_health_checked', report='Dataset health')
        self.assertFalse(worker_event(progress, operation))
        self.assertTrue(worker_event(result, operation))
        with self.assertRaises(ValueError):
            worker_event({**progress, 'scanned': 4}, operation)
        with self.assertRaises(ValueError):
            worker_event({**result, 'report': 'x' * (96 * 1024 + 1)}, operation)

    def test_evaluation_groups_use_ground_truth_and_keep_unlabeled_visibility(self):
        from cloud_contract import worker_event
        from cloud_results import EVALUATION_METRICS
        from evaluation_slices import EvaluationSlices

        evaluator = EvaluationSlices()
        evaluator.add(dict(width=1280, height=720, annotations=[
            {'box': [0, 0, 20, 60], 'visibility': 'heavy'},
            {'box': [20, 20, 110, 170], 'visibility': 'partial'},
            {'box': [40, 30, 300, 500]},
        ]), [(0, 1, .75), (1, 2, .9)])
        groups = evaluator.results()
        self.assertEqual(groups['small'], dict(targets=1, matched=0, mean_iou=None))
        self.assertEqual(groups['medium'], dict(targets=1, matched=1, mean_iou=.75))
        self.assertEqual(groups['unlabeled'], dict(targets=1, matched=1, mean_iou=.9))
        event = dict(type='evaluated', split='val', confidence=.35, images=1,
                     metrics=dict.fromkeys(EVALUATION_METRICS), slices=groups)
        self.assertTrue(worker_event(event, 'evaluate'))
        groups['heavy']['targets'] = 0
        with self.assertRaisesRegex(ValueError, 'totals disagree'):
            worker_event(event, 'evaluate')

    def test_review_priorities_match_classes_and_retire_when_image_changes(self):
        from dataset_review_priority import image_revision, predictions_disagree, review_order, signals_for

        detection = dict(category='player', box=[.2, .3, .1, .2])
        self.assertFalse(predictions_disagree([detection], [dict(detection)]))
        self.assertTrue(predictions_disagree([detection], [dict(detection, category='vehicle')]))
        self.assertTrue(predictions_disagree([], [detection]))
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'frame.png'
            image.write_bytes(b'first')
            stamp = image_revision(image)
            records = {'frame.png': dict(revision=stamp, signals=['uncertain', 'disagreement'])}
            signals = signals_for(records, 'frame.png', image)
            self.assertEqual(signals, ('disagreement', 'uncertain'))
            self.assertLess(review_order('', signals), review_order('', ()))
            self.assertLess(review_order('', ()), review_order('accepted', signals))
            image.write_bytes(b'new pixels')
            self.assertEqual(signals_for(records, 'frame.png', image), ())

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
