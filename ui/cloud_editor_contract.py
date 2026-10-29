"""Owned annotation contracts. Standard library only; no image/model execution.

The training reader remains unchanged. Newly authored records deliberately fit
its existing envelope, including records that have not yet been reviewed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
import hashlib
import json
import math
import re

from cloud_contract import (CloudInputError, MAX_JSON, MAX_IMAGES, MAX_ANNOTATIONS,
                            MAX_PROJECT_JSON, MAX_IMAGE_PIXELS, check_mask,
                            finite, integer, json_value, relative_image)

MAX_DATABASE = 512 * 1024 ** 2
MAX_IMAGE_TOTAL = 100 * 1024 ** 3
MAX_MASK_WORK = 2_000_000_000
HASH = re.compile(r'[0-9a-f]{64}')
TOKEN = re.compile(r'[0-9a-f]{32}')


def encoded(value, maximum=MAX_JSON):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':'), sort_keys=True).encode('utf-8')
    except (ValueError, TypeError, RecursionError) as exc:
        raise CloudInputError('Invalid annotation metadata.') from exc
    json_value(raw, maximum)
    return raw


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def identifier(value, label='Identity', maximum=128):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or any(ord(c) < 32 for c in value)):
        raise CloudInputError(label + ' is invalid.')
    return value


def dimensions(record):
    w = integer(record.get('width'), 1, 32768, 'Image width')
    h = integer(record.get('height'), 1, 32768, 'Image height')
    if w * h > MAX_IMAGE_PIXELS:
        raise CloudInputError('Image exceeds the supported pixel limit.')
    return w, h


def annotation(value, width, height, *, complete):
    if type(value) is not dict:
        raise CloudInputError('Invalid annotation.')
    identifier(value.get('id'), 'Annotation identity')
    if type(value.get('class_id')) is not int or value['class_id'] != 0:
        raise CloudInputError('This .cloud suite uses class0/player.')
    box = value.get('box')
    if type(box) is not list or len(box) != 4:
        raise CloudInputError('An annotation needs a selection box.')
    for i, v in enumerate(box):
        finite(v, 0, width if i % 2 == 0 else height, 'Selection box')
    if box[2] <= box[0] or box[3] <= box[1]:
        raise CloudInputError('Selection box must have positive area.')
    mask = value.get('mask')
    if mask is not None:
        work = check_mask(mask, width, height)
        # Every foreground pixel center must remain inside the fixed box.
        x0, y0 = mask['origin']; mh, mw = mask['shape']; cursor = 0
        left, top = math.ceil(box[0] - .5), math.ceil(box[1] - .5)
        right, bottom = math.ceil(box[2] - .5), math.ceil(box[3] - .5)
        for i, run in enumerate(mask['counts']):
            end = cursor + run
            if i % 2:
                position = cursor
                while position < end:
                    row, col = divmod(position, mw)
                    stop = min(end, (row + 1) * mw)
                    if (not top <= y0 + row < bottom or x0 + col < left
                            or x0 + col + stop - position > right):
                        raise CloudInputError('Mask foreground leaves its selection box.')
                    position = stop
            cursor = end
        return work
    wrapper = value.get('wrapper')
    if wrapper is not None:
        if type(wrapper) is not list or not 3 <= len(wrapper) <= 2048:
            raise CloudInputError('Invalid original polygon.')
        for p in wrapper:
            if type(p) is not list or len(p) != 2:
                raise CloudInputError('Invalid original polygon point.')
            finite(p[0], box[0], box[2], 'Polygon X')
            finite(p[1], box[1], box[3], 'Polygon Y')
        # Existing polygons stay compatible with the current training intake.
        return width * height
    if complete:
        raise CloudInputError('Generate or draw a nonempty mask before approving.')
    return 0


def image_record(value):
    if type(value) is not dict:
        raise CloudInputError('Invalid image record.')
    identifier(value.get('id'), 'Image identity')
    relative_image(value.get('file'))
    width, height = dimensions(value)
    identifier(value.get('group_id'), 'Capture group', 512)
    if value.get('split') not in ('train', 'val', 'test') or type(value.get('reviewed')) is not bool:
        raise CloudInputError('Invalid split or review state.')
    if 'pixel_sha256' in value and (type(value['pixel_sha256']) is not str
                                    or not HASH.fullmatch(value['pixel_sha256'])):
        raise CloudInputError('Invalid canonical image digest.')
    labels = value.get('annotations')
    if type(labels) is not list or len(labels) > 300:
        raise CloudInputError('Too many annotations in one image.')
    seen = set(); work = 0
    for item in labels:
        work += annotation(item, width, height, complete=value['reviewed'])
        if item['id'] in seen:
            raise CloudInputError('Duplicate annotation identity.')
        seen.add(item['id'])
    encoded(value)
    return work


def manifest(value):
    if (type(value) is not dict or type(value.get('schema_version')) is not int
            or value['schema_version'] not in (1, 2) or value.get('classes') != ['player']
            or type(value.get('images')) is not list or len(value['images']) > MAX_IMAGES):
        raise CloudInputError('Unsupported .cloud project.')
    size = len(encoded({k: v for k, v in value.items() if k != 'images'}))
    ids = set(); paths = set(); groups = {}; count = 0; work = 0
    for record in value['images']:
        work += image_record(record); size += len(encoded(record))
        if record['id'] in ids or record['file'].casefold() in paths:
            raise CloudInputError('Duplicate project image identity or path.')
        ids.add(record['id']); paths.add(record['file'].casefold())
        count += len(record['annotations'])
        groups.setdefault(record['group_id'], set()).add(record['split'])
        if size > MAX_PROJECT_JSON or count > MAX_ANNOTATIONS or work > MAX_MASK_WORK:
            raise CloudInputError('Project metadata or annotation budget exceeded.')
    if any(len(v) > 1 for v in groups.values()):
        raise CloudInputError('A capture group cannot cross dataset splits.')
    return value


@dataclass(frozen=True)
class ProposalIdentity:
    session: str
    project: str
    revision: int
    image: str
    pixel_sha256: str
    width: int
    height: int
    annotation: str
    box: tuple
    generation: int
    request: str
    backend: str
    checkpoint_sha256: str

    def __post_init__(self):
        for name in ('session', 'project', 'request'):
            if type(getattr(self, name)) is not str or not TOKEN.fullmatch(getattr(self, name)):
                raise CloudInputError('Invalid proposal session identity.')
        for name in ('pixel_sha256', 'checkpoint_sha256'):
            if type(getattr(self, name)) is not str or not HASH.fullmatch(getattr(self, name)):
                raise CloudInputError('Invalid proposal content identity.')
        identifier(self.image); identifier(self.annotation)
        integer(self.revision, 0, 2**53 - 1, 'Project revision')
        integer(self.generation, 0, 2**53 - 1, 'Edit generation')
        dimensions({'width': self.width, 'height': self.height})
        if self.backend != 'light_hq_sam' or type(self.box) is not tuple:
            raise CloudInputError('Unsupported mask assistant identity.')
        annotation({'id': self.annotation, 'class_id': 0, 'box': list(self.box)},
                   self.width, self.height, complete=False)

    def wire(self):
        value = asdict(self); value['box'] = list(self.box)
        return value

    @classmethod
    def from_wire(cls, value):
        if type(value) is not dict or set(value) != set(cls.__dataclass_fields__):
            raise CloudInputError('Invalid proposal identity fields.')
        result = deepcopy(value)
        if type(result['box']) is not list:
            raise CloudInputError('Invalid proposal box.')
        result['box'] = tuple(result['box'])
        return cls(**result)
