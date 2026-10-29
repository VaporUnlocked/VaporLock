"""VaporLock boundaries around the unmodified owner-supplied Cloud SDK.

This module deliberately uses only the standard library. It never imports Torch,
unpickles a checkpoint, decodes an image, or evaluates model-supplied code.
The SDK remains authoritative for network semantics and tensor names/shapes.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import zipfile

HEADER = struct.Struct('<8sIIQ32s')
FRAME = struct.Struct('<8sQQIIIIffI')
FRAME_MAGIC = b'VLCFRM01'
MAX_DESCRIPTOR = 1024 * 1024
MAX_WEIGHTS = 128 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
MAX_FRAME_PIXELS = 4096 * 2160
MAX_DETECTIONS = 256
MAX_IMAGES = 20_000
MAX_ANNOTATIONS = 100_000
MAX_PROJECT_JSON = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_777_216
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_CHECKPOINT = 512 * 1024 * 1024


class CloudInputError(ValueError):
    """An input cannot be safely admitted; no source data was modified."""


def bundled_dependency_error(error):
    """Only a bounded dotted import name, never exception text or traceback."""
    seen = set()
    for _ in range(8):
        if error is None or id(error) in seen: break
        seen.add(id(error))
        if isinstance(error, ImportError):
            name = getattr(error, 'name', None)
            if type(name) is str and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,159}', name):
                return '.cloud bundled dependency is unavailable: ' + name + '.'
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return None


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CloudInputError('Duplicate JSON field.')
        value[key] = item
    return value


def json_value(raw, maximum=MAX_JSON):
    if not isinstance(raw, (str, bytes)) or len(raw) > maximum:
        raise CloudInputError('JSON exceeds its input limit.')
    if isinstance(raw, str) and len(raw.encode('utf-8')) > maximum:
        raise CloudInputError('JSON exceeds its input limit.')
    def constant(_):
        raise CloudInputError('Nonfinite JSON value.')
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CloudInputError('Invalid bounded JSON.') from exc
    pending = [(value, 0)]; nodes = 0
    while pending:
        item, depth = pending.pop(); nodes += 1
        if depth > 32 or nodes > 2_000_000:
            raise CloudInputError('JSON nesting or item budget exceeded.')
        if isinstance(item, dict):
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise CloudInputError('Nonfinite JSON value.')
    return value


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise CloudInputError(label + ' is outside the supported range.')
    return value


def finite(value, low, high, label):
    try:
        converted = float(value) if type(value) in (int, float) else float('nan')
    except (OverflowError, ValueError):
        converted = float('nan')
    if not math.isfinite(converted) or not low <= converted <= high:
        raise CloudInputError(label + ' is outside the supported range.')
    return converted


def model_config(value):
    if type(value) is not dict:
        raise CloudInputError('Invalid .cloud configuration.')
    architecture = value.get('architecture', 'cloudvision_s1_v2')
    allowed = {'architecture', 'input_size', 'channels', 'vertices', 'roi_size', 'mask_size'}
    if set(value) - allowed or architecture not in ('cloudvision_s1_v2', 'cloudvision_s2_v1'):
        raise CloudInputError('Unsupported .cloud architecture or configuration.')
    size = integer(value.get('input_size', 320), 64, 1024, 'Input size')
    channels = integer(value.get('channels', 48), 16, 128, 'Channels')
    if size % 32 or channels % 8:
        raise CloudInputError('Invalid .cloud architecture dimensions.')
    for key, default, options in (('vertices', 32, (16, 32, 64)), ('roi_size', 8, (4, 8, 16)),
                                   ('mask_size', 64, (32, 64, 128))):
        if type(value.get(key, default)) is not int or value.get(key, default) not in options:
            raise CloudInputError('Invalid .cloud capacity.')
    return architecture


def inspect_model_stream(stream, size):
    """Check lengths, descriptor and digest before the authoritative SDK loader."""
    if not HEADER.size < size <= HEADER.size + MAX_DESCRIPTOR + MAX_WEIGHTS:
        raise CloudInputError('The .cloud model exceeds its file limit or is truncated.')
    raw = stream.read(HEADER.size)
    if len(raw) != HEADER.size:
        raise CloudInputError('Truncated .cloud header.')
    magic, version, descriptor_size, weights_size, checksum = HEADER.unpack(raw)
    if (magic != b'CLOUDDET' or version != 1 or not 0 < descriptor_size <= MAX_DESCRIPTOR
            or not 0 < weights_size <= MAX_WEIGHTS or size != HEADER.size + descriptor_size + weights_size):
        raise CloudInputError('Invalid .cloud header or declared lengths.')
    descriptor_bytes = stream.read(descriptor_size)
    if len(descriptor_bytes) != descriptor_size:
        raise CloudInputError('Truncated .cloud descriptor.')
    descriptor = json_value(descriptor_bytes, MAX_DESCRIPTOR)
    if type(descriptor) is not dict or set(descriptor) != {'architecture', 'config', 'metadata', 'tensors'}:
        raise CloudInputError('Invalid .cloud descriptor fields.')
    architecture = model_config(descriptor['config'])
    if descriptor['architecture'] != architecture or type(descriptor['metadata']) is not dict:
        raise CloudInputError('Inconsistent .cloud architecture or metadata.')
    tensors = descriptor['tensors']
    if type(tensors) is not list or not 1 <= len(tensors) <= 128:
        raise CloudInputError('Invalid .cloud tensor directory.')
    names = set(); offset = 0
    for entry in tensors:
        if type(entry) is not dict or set(entry) != {'name', 'shape', 'dtype', 'offset', 'nbytes'}:
            raise CloudInputError('Invalid .cloud tensor fields.')
        name, shape = entry['name'], entry['shape']
        if type(name) is not str or not re.fullmatch(r'[A-Za-z0-9_.]{1,96}', name) or name in names:
            raise CloudInputError('Invalid or duplicate .cloud tensor name.')
        names.add(name)
        if type(shape) is not list or not 1 <= len(shape) <= 4:
            raise CloudInputError('Invalid .cloud tensor shape.')
        count = 1
        for dimension in shape:
            count *= integer(dimension, 1, MAX_WEIGHTS // 4, 'Tensor dimension')
            if count * 4 > MAX_WEIGHTS:
                raise CloudInputError('Tensor allocation exceeds its limit.')
        if (entry['dtype'] != '<f4' or type(entry['offset']) is not int or entry['offset'] != offset
                or type(entry['nbytes']) is not int or entry['nbytes'] != count * 4):
            raise CloudInputError('Invalid .cloud tensor layout.')
        offset += count * 4
    if offset != weights_size:
        raise CloudInputError('Tensor directory does not match the payload.')
    digest = hashlib.sha256(descriptor_bytes); remaining = weights_size
    while remaining:
        part = stream.read(min(1024 * 1024, remaining))
        if not part:
            raise CloudInputError('Truncated .cloud weights.')
        digest.update(part); remaining -= len(part)
    if stream.read(1) or digest.digest() != checksum:
        raise CloudInputError('The .cloud integrity check failed.')
    return {'architecture': architecture, 'config': descriptor['config'], 'bytes': size,
            'synthetic': descriptor['metadata'].get('synthetic') is True,
            'tensors': len(tensors)}


def relative_image(value):
    if (type(value) is not str or not 1 <= len(value) <= 1024 or '\\' in value
            or ':' in value or any(ord(c) < 32 for c in value)):
        raise CloudInputError('Invalid project image path.')
    path = PurePosixPath(value)
    if (path.is_absolute() or value != path.as_posix() or any(p in ('', '.', '..') for p in path.parts)
            or len(path.parts) < 2 or path.parts[0] != 'images'):
        raise CloudInputError('Project images must stay inside the project images folder.')
    return path


def check_mask(value, width, height):
    if type(value) is not dict or set(value) != {'encoding', 'size', 'origin', 'shape', 'counts'}:
        raise CloudInputError('A reviewed positive needs an exact .cloud mask.')
    if value['encoding'] != 'cloud-rle-v1' or value['size'] != [height, width]:
        raise CloudInputError('Mask size or encoding does not match the image.')
    for key in ('origin', 'shape'):
        if type(value[key]) is not list or len(value[key]) != 2:
            raise CloudInputError('Invalid mask geometry.')
        for item in value[key]:
            integer(item, 0, 32768, 'Mask geometry')
    x, y = value['origin']; h, w = value['shape']; counts = value['counts']
    if x + w > width or y + h > height or h * w > MAX_IMAGE_PIXELS:
        raise CloudInputError('Mask crop leaves the image or exceeds its allocation limit.')
    if type(counts) is not list or not 1 <= len(counts) <= 1_000_000:
        raise CloudInputError('Mask run budget exceeded.')
    for index, count in enumerate(counts):
        integer(count, 0 if index == 0 else 1, MAX_IMAGE_PIXELS, 'Mask run')
    if sum(counts) != h * w or not sum(counts[1::2]):
        raise CloudInputError('A reviewed positive needs a nonempty exact mask.')
    return h * w


def inspect_checkpoint_archive(stream, size):
    """Pre-unpickle resource envelope only. Does not approve checkpoint contents."""
    if not 0 < size <= MAX_CHECKPOINT:
        raise CloudInputError('Resume checkpoint exceeds its supported file limit.')
    try:
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= 4096:
                raise CloudInputError('Resume checkpoint has too many storage entries.')
            total = 0; names = set(); roots = set(); pickle_count = 0
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (entry.filename in names or entry.flag_bits & 1 or entry.compress_type != zipfile.ZIP_STORED
                        or path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                        or ':' in entry.filename or len(path.parts) < 2
                        or ((entry.external_attr >> 16) & 0o170000) == 0o120000):
                    raise CloudInputError('Unsupported resume checkpoint archive layout.')
                names.add(entry.filename); roots.add(path.parts[0])
                total += entry.file_size
                if entry.file_size < 0 or total > MAX_CHECKPOINT:
                    raise CloudInputError('Resume checkpoint storage exceeds its allocation limit.')
                if path.name == 'data.pkl':
                    pickle_count += 1
                    if entry.file_size > 8 * 1024 * 1024:
                        raise CloudInputError('Resume checkpoint metadata exceeds its limit.')
            if len(roots) != 1 or pickle_count != 1:
                raise CloudInputError('Unsupported resume checkpoint structure.')
    except (zipfile.BadZipFile, OSError) as exc:
        raise CloudInputError('Not a supported restricted-loader .cloudtrain checkpoint.') from exc


def frame_header(raw):
    if len(raw) != FRAME.size:
        raise CloudInputError('Truncated frame header.')
    magic, sequence, captured_ns, width, height, stride, length, confidence, iou, maximum = FRAME.unpack(raw)
    if magic != FRAME_MAGIC or not sequence or not captured_ns:
        raise CloudInputError('Invalid frame identity.')
    integer(width, 1, 4096, 'Frame width'); integer(height, 1, 4096, 'Frame height')
    if width * height > MAX_FRAME_PIXELS or stride != width * 4 or length != stride * height:
        raise CloudInputError('Invalid BGRA frame layout.')
    finite(confidence, 0, 1, 'Confidence'); finite(iou, 0, 1, 'NMS IoU')
    integer(maximum, 1, MAX_DETECTIONS, 'Maximum detections')
    return sequence, captured_ns, width, height, length, confidence, iou, maximum


def _runtime_model_summary(info):
    if type(info) is not dict or set(info) != {'architecture', 'config', 'bytes', 'synthetic', 'tensors'}:
        raise CloudInputError('Invalid .cloud runtime model summary.')
    if model_config(info['config']) != info['architecture'] or type(info['synthetic']) is not bool:
        raise CloudInputError('Invalid .cloud runtime architecture summary.')
    integer(info['bytes'], HEADER.size + 1, HEADER.size + MAX_DESCRIPTOR + MAX_WEIGHTS, 'Model bytes')
    integer(info['tensors'], 1, 128, 'Model tensor count')


def worker_event(value, operation):
    """Operation-specific IPC schema before any event enters a parent queue."""
    if type(value) is not dict or type(value.get('type')) is not str:
        raise CloudInputError('Invalid .cloud worker event.')
    kind = value['type']
    if kind == 'error':
        if set(value) != {'type', 'message'} or type(value['message']) is not str or len(value['message']) > 1024:
            raise CloudInputError('Invalid .cloud worker error.')
        return True
    if (operation=='check_resume' and kind=='resume_checked') or (operation=='train' and kind=='configuration'):
        if set(value)!={'type','recipe'} or type(value['recipe']) is not dict:
            raise CloudInputError('Invalid .cloud training recipe event.')
        recipe=value['recipe']
        if set(recipe)-{'epochs','batch_size','device','patience','input_size','channels','mask_size','architecture','completed_epochs'}:
            raise CloudInputError('Unknown .cloud recipe fields.')
        for key,low,high in (('epochs',1,10000),('batch_size',-1,256),('patience',0,10000),('input_size',64,1024),('channels',16,128),('mask_size',32,128),('completed_epochs',0,10000)):
            if key in recipe:integer(recipe[key],low,high,'Recipe '+key)
        if 'device' in recipe and (type(recipe['device']) is not str or re.fullmatch(r'auto|cpu|cuda(?::[0-9]{1,2})?',recipe['device']) is None):raise CloudInputError('Invalid recipe device.')
        if 'architecture' in recipe and recipe['architecture'] not in ('cloudvision_s1_v2','cloudvision_s2_v1'):raise CloudInputError('Invalid recipe architecture.')
        return kind=='resume_checked'
    if operation=='check_model' and kind=='model_checked':
        if set(value)!={'type','model'}:raise CloudInputError('Invalid model-check result.')
        _runtime_model_summary(value['model'])
        return True
    if operation == 'check_project' and kind == 'project_checked':
        if set(value) != {'type', 'counts'}:
            raise CloudInputError('Invalid .cloud project-check result.')
        counts = value['counts']
        if type(counts) is not dict or set(counts) != {'train', 'val', 'test', 'negatives', 'unreviewed', 'annotations'}:
            raise CloudInputError('Invalid .cloud project counts.')
        for count in counts.values():
            integer(count, 0, MAX_ANNOTATIONS, 'Project count')
        return True
    if operation == 'train':
        allowed = {'phase': {'type', 'phase', 'counts'},
                   'progress': {'type', 'event', 'epoch', 'epochs', 'batch', 'batches', 'step', 'device',
                                'train_images', 'val_images', 'loss', 'detector_loss', 'box_loss', 'mask_loss',
                                'learning_rate', 'resolved_batch_size', 'effective_batch_size'},
                   'trained': {'type', 'status', 'epochs_completed', 'output', 'best_cloud', 'last_cloud', 'checkpoint'}}
        if kind not in allowed or set(value) - allowed[kind]:
            raise CloudInputError('Invalid .cloud training event fields.')
        for key, item in value.items():
            if isinstance(item, (dict, list)) and key != 'counts':
                raise CloudInputError('Unexpected nested training event.')
            if type(item) is str and len(item) > (32000 if key in ('output', 'best_cloud', 'last_cloud', 'checkpoint') else 128):
                raise CloudInputError('Training event text exceeds its limit.')
            if key not in ('type', 'status', 'output', 'best_cloud', 'last_cloud', 'checkpoint', 'phase', 'event', 'device', 'counts'):
                finite(item, -1e12, 1e12, 'Training metric')
        if kind == 'phase' and value.get('phase') not in ('checking', 'starting'):
            raise CloudInputError('Invalid .cloud training phase.')
        if kind == 'phase' and 'counts' in value:
            worker_event({'type': 'project_checked', 'counts': value['counts']}, 'check_project')
        if kind == 'trained' and (set(value) != allowed[kind] or value.get('status') not in ('completed', 'early_stopped', 'stopped')):
            raise CloudInputError('Invalid .cloud training completion.')
        if kind == 'trained':
            if type(value['output']) is not str or not value['output']:
                raise CloudInputError('Invalid .cloud run output path.')
            for key in ('best_cloud', 'last_cloud', 'checkpoint'):
                if value[key] is not None and (type(value[key]) is not str or not value[key]):
                    raise CloudInputError('Invalid .cloud artifact result path.')
            integer(value['epochs_completed'], 0, 10000, 'Completed epochs')
        return kind == 'trained'
    if operation == 'inference':
        if kind == 'ready':
            if (set(value) - {'type', 'edition', 'backend', 'device', 'model', 'pid'}
                    or not {'type', 'edition', 'backend', 'device', 'model'} <= set(value)
                    or type(value['edition']) is not str or value['edition'] not in ('free','premium')
                    or value['backend'] not in ('numpy', 'torch', 'ort')):
                raise CloudInputError('Invalid .cloud runtime readiness.')
            if 'pid' in value: integer(value['pid'], 1, 2**32 - 1, 'Runtime process')
            if type(value['device']) is not str or re.fullmatch(r'cpu|cuda(?::[0-9]{1,2})?|directml(?::[0-9]{1,2})?', value['device']) is None:
                raise CloudInputError('Invalid .cloud runtime device.')
            _runtime_model_summary(value['model'])
            return False
        if kind == 'inference_closed' and set(value) == {'type'}:
            return True
        if kind != 'detections' or set(value) != {'type', 'sequence', 'captured_ns', 'width', 'height', 'detections', 'timing_ms'}:
            raise CloudInputError('Invalid .cloud detection result.')
        integer(value['sequence'], 1, 2**64 - 1, 'Result sequence')
        integer(value['captured_ns'], 1, 2**64 - 1, 'Result capture time')
        width = integer(value['width'], 1, 4096, 'Result width'); height = integer(value['height'], 1, 4096, 'Result height')
        if width * height > MAX_FRAME_PIXELS or type(value['detections']) is not list or len(value['detections']) > MAX_DETECTIONS:
            raise CloudInputError('Invalid .cloud detection result size.')
        aggregate_mask_pixels = 0
        for detection in value['detections']:
            if (type(detection) is not dict or set(detection) - {'class_id', 'confidence', 'primary_box', 'mask', 'wrapper'}
                    or type(detection.get('class_id')) is not int or detection['class_id'] != 0):
                raise CloudInputError('Invalid .cloud detection class or fields.')
            finite(detection.get('confidence'), 0, 1, 'Detection confidence')
            box = detection.get('primary_box')
            if type(box) is not list or len(box) != 4:
                raise CloudInputError('Invalid .cloud detection box.')
            for i, coordinate in enumerate(box):
                finite(coordinate, 0, width if i % 2 == 0 else height, 'Detection coordinate')
            if box[2] <= box[0] or box[3] <= box[1]:
                raise CloudInputError('Invalid .cloud detection box area.')
            if 'mask' in detection:
                aggregate_mask_pixels += check_mask(detection['mask'], width, height)
            wrapper = detection.get('wrapper', [])
            if type(wrapper) is not list or len(wrapper) > 2048:
                raise CloudInputError('Invalid .cloud wrapper preview.')
            for point in wrapper:
                if type(point) is not list or len(point) != 2:
                    raise CloudInputError('Invalid .cloud wrapper point.')
                finite(point[0], 0, width, 'Wrapper X'); finite(point[1], 0, height, 'Wrapper Y')
        if aggregate_mask_pixels > MAX_FRAME_PIXELS * 4:
            raise CloudInputError('Detection masks exceed the per-frame allocation budget.')
        timing = value['timing_ms']
        if type(timing) is not dict or set(timing) != {'preprocess', 'inference', 'postprocess', 'total'}:
            raise CloudInputError('Invalid .cloud timing result.')
        for elapsed in timing.values():
            finite(elapsed, 0, 60000, 'Runtime timing')
        return False
    raise CloudInputError('Unexpected .cloud worker event.')
