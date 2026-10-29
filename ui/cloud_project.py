"""Read-only, bounded Cloud project intake and protected new-run output.

No project is upgraded, repaired, relabelled, or saved by this adapter. Windows
handles deny input writes/replacement for the lifetime of a running job.
"""
from __future__ import annotations

from contextlib import ExitStack, closing
import os
from pathlib import Path
import re
import sqlite3
import time

from cloud_contract import (CloudInputError, MAX_ANNOTATIONS, MAX_IMAGES, MAX_IMAGE_BYTES,
                            MAX_IMAGE_PIXELS, MAX_PROJECT_JSON, check_mask, finite,
                            integer, json_value, relative_image)
from dataset_paths import reject_reparse, display_path, local_path
from cloud_sqlite_paths import snapshot_uri


def protected_directory(path):
    """Hold directory identity without sharing write access to its reparse data."""
    import ctypes
    from dataset_transaction import HeldFile
    held = HeldFile(path, directory=True)
    original = held.handle
    stronger = None
    try:
        # LIST_DIRECTORY | READ_ATTRIBUTES, SHARE_READ only, OPEN_EXISTING,
        # BACKUP_SEMANTICS | OPEN_REPARSE_POINT. File creation inside the held
        # directory remains possible; opening the directory for reparse writes
        # does not. Ancestors retain the existing no-delete directory contract.
        # READ_ATTRIBUTES alone does not participate in Windows data-sharing
        # checks. LIST_DIRECTORY is essential: without it a write handle can
        # still set a junction even while the attribute-only handle is held.
        stronger = held.api.CreateFileW(str(held.path), 0x81, 1, None, 3, 0x02200000, None)
        if stronger == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        held.handle = stronger
        info = held.info()
        if not info.attributes & 0x10 or info.attributes & 0x400:
            raise CloudInputError('A held Cloud directory became a reparse point.')
        return held
    except BaseException:
        held.close(); raise
    finally:
        if stronger is not None and stronger != ctypes.c_void_p(-1).value:
            held.api.CloseHandle(original)


class ProjectLease:
    def __init__(self, selected):
        self.selected = selected
        self.stack = ExitStack(); self.held_dirs = set(); self.held_files = set()
        self._directory_handles = {}
        self.root = None; self.manifest = None; self.counts = None

    def _directory(self, path):
        path = local_path(path)
        chain = (path, *path.parents)
        # Windows guards already hold these exact directory identities without
        # write/delete sharing. Repeating filesystem discovery for every file
        # adds no authority. Check EVERY ancestor's still-live handle; never
        # reuse this fast path after stack.close() or on an unheld child.
        if os.name == 'nt' and all(
                (held := self._directory_handles.get(os.path.normcase(str(parent)))) is not None
                and held.handle is not None for parent in chain):
            return path
        path = reject_reparse(path)
        for parent in reversed((path, *path.parents)):
            key = os.path.normcase(str(parent))
            if key not in self.held_dirs or (os.name == 'nt'
                    and self._directory_handles[key].handle is None):
                if not parent.is_dir():
                    raise CloudInputError('A project folder is missing.')
                if os.name == 'nt':
                    from dataset_transaction import HeldFile
                    self._directory_handles[key] = self.stack.enter_context(
                        HeldFile(parent, directory=True) if parent == Path(parent.anchor)
                        else protected_directory(parent))
                self.held_dirs.add(key)
        return path

    def hold_file(self, path, maximum):
        path = reject_reparse(path); self._directory(path.parent)
        if not path.is_file() or path.stat().st_size > maximum or path.stat().st_nlink != 1:
            raise CloudInputError('A project input is missing, hard linked, or exceeds its file limit.')
        key = os.path.normcase(str(path))
        if key not in self.held_files:
            if os.name == 'nt':
                from dataset_transaction import HeldFile
                held = self.stack.enter_context(HeldFile(path, read_only=True))
                info = held.info()
                if (info.size_high << 32) | info.size_low > maximum:
                    raise CloudInputError('Project input changed or exceeds its file limit.')
            self.held_files.add(key)
        return path

    def _read(self, path, maximum):
        path = self.hold_file(path, maximum)
        with path.open('rb') as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise CloudInputError('Project metadata exceeds its file limit.')
        return json_value(data, maximum)

    def _sqlite(self, descriptor):
        identifier = descriptor.get('project_id')
        if (set(descriptor) != {'schema_version', 'storage', 'database', 'project_id'}
                or descriptor.get('schema_version') != 2
                or descriptor.get('storage') != 'cloud-project-sqlite-v1'
                or type(identifier) is not str or not re.fullmatch('[0-9a-f]{32}', identifier)
                or descriptor.get('database') != 'project-data-' + identifier + '.sqlite3'):
            raise CloudInputError('Unsupported Cloud project storage descriptor.')
        database = self.hold_file(self.root / descriptor['database'], 512 * 1024 * 1024)
        for suffix in ('-wal', '-shm'):
            if os.path.lexists(str(database) + suffix):
                raise CloudInputError('Close the Cloud project editor and finish its database transaction first.')
        journal = Path(str(database) + '-journal')
        if os.path.lexists(journal):
            # The owned editor uses TRUNCATE and keeps an empty journal identity.
            # Pin it read-only after the DB; never recover/create/change it here.
            self.hold_file(journal, 0)
            if journal.stat().st_size != 0:
                raise CloudInputError('Close the Cloud project editor and finish its database transaction first.')
        deadline = time.monotonic() + 30
        # immutable is safe here only because the same database identity is held
        # without write/delete sharing. WAL/SHM and nonempty journals were
        # rejected; an existing empty journal is independently held read-only.
        uri = snapshot_uri(database)
        # sqlite3.Connection.__exit__ commits/rolls back; it does not close the
        # native connection. Do not leave file identities alive until cyclic GC.
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2 * 1024 * 1024)
            db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 32)
            db.execute('PRAGMA trusted_schema=OFF'); db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            if (db.execute('PRAGMA application_id').fetchone()[0] != 1129071696
                    or db.execute('PRAGMA user_version').fetchone()[0] != 1):
                raise CloudInputError('Unsupported Cloud project database.')
            for table in ('state', 'images'):
                row = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchone()
                if row != ('table',):
                    raise CloudInputError('Invalid Cloud project database schema.')
            row = db.execute('SELECT project_id,revision,length(CAST(metadata AS BLOB)) FROM state WHERE singleton=1').fetchone()
            if (row is None or row[0] != identifier or type(row[1]) is not int or row[1] < 0
                    or type(row[2]) is not int or not 1 <= row[2] <= 2 * 1024 * 1024):
                raise CloudInputError('Project database identity or metadata is invalid.')
            metadata = db.execute('SELECT metadata FROM state WHERE singleton=1').fetchone()[0]
            manifest = json_value(metadata); total = row[2]; records = []
            if type(manifest) is not dict:
                raise CloudInputError('Invalid Cloud project metadata.')
            count, size, largest = db.execute('SELECT count(*),coalesce(sum(length(CAST(record AS BLOB))),0),coalesce(max(length(CAST(record AS BLOB))),0) FROM images').fetchone()
            if count > MAX_IMAGES or total + size > MAX_PROJECT_JSON or largest > 2 * 1024 * 1024:
                raise CloudInputError('Cloud project record or metadata budget exceeded.')
            for identifier, raw in db.execute('SELECT id,record FROM images ORDER BY ordinal'):
                record = json_value(raw)
                if type(record) is not dict or record.get('id') != identifier:
                    raise CloudInputError('Inconsistent Cloud project image identity.')
                records.append(record)
            manifest['images'] = records
            return manifest

    def __enter__(self):
        try:
            selected = reject_reparse(self.selected)
            if selected.is_file():
                if selected.name != 'project.json':
                    raise CloudInputError('Choose a Cloud project folder or its project.json file.')
                selected = selected.parent
            self.root = self._directory(selected)
            descriptor = self._read(self.root / 'project.json', MAX_PROJECT_JSON)
            if type(descriptor) is not dict:
                raise CloudInputError('Choose a Cloud project with reviewed instance masks. A YOLO dataset alone cannot be trained here.')
            self.manifest = self._sqlite(descriptor) if 'storage' in descriptor else descriptor
            self.counts = self._records()
            return self
        except BaseException:
            self.stack.close(); raise

    def _records(self):
        manifest = self.manifest
        if (type(manifest.get('schema_version')) is not int or manifest.get('schema_version') not in (1, 2) or manifest.get('classes') != ['player']
                or type(manifest.get('images')) is not list or len(manifest['images']) > MAX_IMAGES):
            raise CloudInputError('Choose a supported class0/player Cloud project with reviewed instance masks.')
        counts = dict(train=0, val=0, test=0, negatives=0, unreviewed=0, annotations=0)
        ids = set(); paths = set(); groups = {}; mask_pixels = 0; total_image_bytes = 0
        for record in manifest['images']:
            if type(record) is not dict:
                raise CloudInputError('Invalid project image record.')
            identifier = record.get('id'); file = record.get('file'); split = record.get('split')
            if type(identifier) is not str or not 1 <= len(identifier) <= 128 or identifier in ids:
                raise CloudInputError('Missing or duplicate project image identity.')
            ids.add(identifier)
            relative = relative_image(file)
            # Windows is case insensitive, so two spellings cannot name two samples.
            key = file.casefold()
            if key in paths:
                raise CloudInputError('Duplicate project image path.')
            paths.add(key)
            width = integer(record.get('width'), 1, 32768, 'Image width')
            height = integer(record.get('height'), 1, 32768, 'Image height')
            if width * height > MAX_IMAGE_PIXELS:
                raise CloudInputError('A project image exceeds the pixel limit.')
            if (split not in ('train', 'val', 'test') or type(record.get('reviewed')) is not bool
                    or type(record.get('group_id')) is not str or not 1 <= len(record['group_id']) <= 512
                    or type(record.get('annotations')) is not list):
                raise CloudInputError('Invalid project split, review, group or annotation state.')
            if not record['reviewed']:
                counts['unreviewed'] += 1
                continue
            path = self.hold_file(self.root.joinpath(*relative.parts), MAX_IMAGE_BYTES)
            total_image_bytes += path.stat().st_size
            if total_image_bytes > 100 * 1024 ** 3:
                raise CloudInputError('Project source bytes exceed the supported job limit.')
            counts[split] += 1
            groups.setdefault(record['group_id'], set()).add(split)
            annotations = record['annotations']; counts['annotations'] += len(annotations)
            if counts['annotations'] > MAX_ANNOTATIONS or len(annotations) > 300:
                raise CloudInputError('Project annotation budget exceeded.')
            if not annotations:
                counts['negatives'] += 1
            for annotation in annotations:
                if type(annotation) is not dict or type(annotation.get('class_id')) is not int or annotation['class_id'] != 0:
                    raise CloudInputError('A reviewed annotation must use class0/player.')
                box = annotation.get('box')
                if type(box) is not list or len(box) != 4:
                    raise CloudInputError('A reviewed annotation needs a main box.')
                for n, v in enumerate(box):
                    finite(v, 0, width if n % 2 == 0 else height, 'Annotation box')
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise CloudInputError('An annotation box must have positive area.')
                if annotation.get('mask') is not None:
                    mask_pixels += check_mask(annotation['mask'], width, height)
                else:
                    wrapper = annotation.get('wrapper')
                    if type(wrapper) is not list or not 3 <= len(wrapper) <= 2048:
                        raise CloudInputError('A reviewed positive needs an instance mask or its original encasing polygon.')
                    for point in wrapper:
                        if type(point) is not list or len(point) != 2:
                            raise CloudInputError('Invalid encasing polygon.')
                        finite(point[0], box[0], box[2], 'Encasing X'); finite(point[1], box[1], box[3], 'Encasing Y')
                    mask_pixels += width * height
                if mask_pixels > 2_000_000_000:
                    raise CloudInputError('Project aggregate mask workload exceeds its supported limit.')
        if not counts['train'] or not counts['val']:
            raise CloudInputError('At least one reviewed training image and a separate reviewed validation image are required.')
        if any(len(splits) > 1 for splits in groups.values()):
            raise CloudInputError('A capture/session group occurs in multiple splits. Separate train and validation recordings.')
        return counts

    def check_image_headers(self):
        # Called only in the bounded worker, after record and allocation checks.
        from PIL import Image
        for record in self.manifest['images']:
            if not record['reviewed']:
                continue
            with Image.open(self.root / record['file']) as image:
                if (image.size != (record['width'], record['height']) or getattr(image, 'n_frames', 1) != 1
                        or image.format not in ('PNG', 'JPEG', 'BMP', 'WEBP')):
                    raise CloudInputError('A source image header disagrees with its project record or is unsupported.')

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


class NewRunLease:
    """Creates one new empty run; never reuses or recursively removes a run."""
    def __init__(self, parent, name, project_root):
        if type(name) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
            raise CloudInputError('Run name must be 1–64 letters, digits, underscores or hyphens.')
        self.parent = reject_reparse(parent); self.name = name
        self.project_root = reject_reparse(project_root)
        self.stack = ExitStack(); self.root = None

    def __enter__(self):
        try:
            parent = self.parent
            if not parent.is_dir():
                raise CloudInputError('Choose an existing output parent folder.')
            proposed = reject_reparse(parent / self.name)
            project = os.path.normcase(str(self.project_root)); destination = os.path.normcase(str(proposed))
            try:
                common = os.path.commonpath([project, destination])
            except ValueError:
                common = None  # Different local drives cannot overlap.
            if common in (project, destination):
                raise CloudInputError('Training output must be separate from the project and its ancestors.')
            for part in reversed((parent, *parent.parents)):
                reject_reparse(part)
                if os.name == 'nt':
                    from dataset_transaction import HeldFile
                    self.stack.enter_context(HeldFile(part, directory=True) if part == Path(part.anchor)
                                             else protected_directory(part))
            proposed.mkdir(exist_ok=False)
            self.root = reject_reparse(proposed)
            if os.name == 'nt':
                from dataset_transaction import HeldFile
                self.stack.enter_context(protected_directory(self.root))
            if next(self.root.iterdir(), None) is not None:
                raise CloudInputError('The new run folder was changed before it could be protected.')
            return self.root
        except BaseException:
            self.stack.close(); raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)
