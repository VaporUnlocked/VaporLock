"""Owned SQLite editor persistence with revision CAS and explicit reconciliation.

No codecs, SDK, model imports or implicit legacy migration. Each write opens a
write-compatible, no-delete identity handle. A training ProjectLease therefore
returns a busy outcome rather than losing its protected snapshot.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import ctypes
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import time

from cloud_contract import (CloudInputError, MAX_IMAGE_BYTES, MAX_IMAGES,
                            MAX_JSON, MAX_PROJECT_JSON, json_value)
from cloud_editor_contract import (MAX_DATABASE, MAX_IMAGE_TOTAL, TOKEN,
                                  digest, encoded, image_record, manifest)
from cloud_project import ProjectLease, protected_directory
from dataset_paths import reject_reparse, display_path
from cloud_sqlite_paths import editor_uri

FORMAT = 'cloud-project-sqlite-v1'
STALE = 'The project changed in another editor. Reopen it before saving.'


class CommitUncertain(OSError):
    """The caller must reconcile operation identity before retry/advancement."""


def _identity(path):
    info = path.stat()
    if not path.is_file() or info.st_nlink != 1:
        raise CloudInputError('Project files must be regular files without hard links.')
    return info.st_dev, info.st_ino


@contextmanager
def _write_identity(path, *, create=False, empty=False):
    if os.name != 'nt':
        if create:
            with path.open('xb'):pass
        if empty and path.stat().st_size:
            raise CloudInputError('A nonempty project journal needs recovery.')
        yield None
        return
    from dataset_transaction import HeldFile
    api = HeldFile.__new__(HeldFile)
    from dataset_transaction import _api
    api.api = _api(); api.path = path; api.handle = None
    handle = api.api.CreateFileW(str(path), 0xC0000000, 3, None, 1 if create else 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise CloudInputError('Project is busy or protected by training. Finish that job before editing.')
    api.handle = handle
    try:
        info = api.info()
        if info.attributes & 0x410 or info.links != 1:
            raise CloudInputError('Project database identity is unsafe.')
        if empty and (info.size_high or info.size_low):
            raise CloudInputError('A nonempty project journal needs recovery before editing. Its bytes were preserved.')
        if path.stat().st_ino != ((info.index_high << 32) | info.index_low):
            raise CloudInputError('Held project path identity disagrees.')
        yield api
    except BaseException as primary:
        try: api.close()
        except BaseException as cleanup: primary.add_note('Identity cleanup also failed: ' + type(cleanup).__name__)
        api = None
        raise
    finally:
        if api is not None:
            try: api.close()
            except BaseException as cleanup:
                raise CommitUncertain('Project identity cleanup was not acknowledged; reconcile before continuing.') from cleanup


@contextmanager
def _journal_identity(database):
    for suffix in ('-wal', '-shm'):
        if os.path.lexists(str(database) + suffix):
            raise CloudInputError('Close the other editor and recover its WAL before editing.')
    journal = reject_reparse(Path(str(database) + '-journal'))
    # TRUNCATE keeps this identity in place for the connection's entire life.
    # Existing nonempty journals are never recovered implicitly by this writer.
    with _write_identity(journal, create=not os.path.lexists(journal), empty=True) as held:
        identity = _identity(journal)
        primary = None
        try:
            yield
        except BaseException as exc:
            primary = exc
            raise
        finally:
            # Never truncate/remove a journal to make a failed save look clean.
            try:
                info = held.info() if held is not None else None
                if (_identity(journal) != identity or (info is not None and (info.size_high or info.size_low))
                        or journal.stat().st_size):
                    raise OSError('Project journal identity or length is unresolved.')
            except BaseException as cleanup:
                if isinstance(primary, CommitUncertain):
                    primary.add_note('Journal validation also failed: ' + type(cleanup).__name__)
                else:
                    raise CommitUncertain('The project journal remains unresolved; preserve it and reconcile before editing.') from cleanup


def _header(path, held):
    if held is None:
        with path.open('rb') as stream:return stream.read(100)
    from ctypes import wintypes as W
    if not held.api.SetFilePointerEx(held.handle, 0, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    data = ctypes.create_string_buffer(100); count = W.DWORD()
    if not held.api.ReadFile(held.handle, data, 100, ctypes.byref(count), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return data.raw[:count.value]


class EditorStore:
    def __init__(self, selected):
        self.guard = ProjectLease(selected); self.closed = False
        self.database = None
        try:
            root = reject_reparse(selected)
            if root.is_file():
                if root.name != 'project.json':
                    raise CloudInputError('Choose a .cloud project or project.json.')
                root = root.parent
            self.root = self.guard._directory(root)
            value = self.guard._read(self.root / 'project.json', MAX_JSON)
            if type(value) is not dict or 'storage' not in value:
                raise CloudInputError('Legacy JSON projects need an explicit imported copy; the original was not changed.')
            self.project_id = value.get('project_id')
            if (set(value) != {'schema_version', 'storage', 'database', 'project_id'}
                    or value.get('schema_version') != 2 or value.get('storage') != FORMAT
                    or type(self.project_id) is not str or not TOKEN.fullmatch(self.project_id)
                    or value.get('database') != 'project-data-' + self.project_id + '.sqlite3'):
                raise CloudInputError('Unsupported .cloud storage descriptor.')
            self.database = reject_reparse(self.root / value['database'])
            self.database_identity = _identity(self.database)
            self.load()
        except BaseException:
            self.close()
            raise

    @classmethod
    def create(cls, parent, name):
        from cloud_editor_contract import identifier
        identifier(name, 'Project name', 200)
        parent = reject_reparse(parent)
        if not parent.is_dir():
            raise CloudInputError('Choose an existing parent folder.')
        guard = ProjectLease(parent)
        try:
            guard._directory(parent)
            # Always make a new owned folder; never populate an existing folder.
            root = parent / ('cloud-project-' + secrets.token_hex(8))
            root.mkdir(exist_ok=False); guard._directory(root)
            (root / 'images').mkdir(exist_ok=False)
            project_id = secrets.token_hex(16)
            database = root / ('project-data-' + project_id + '.sqlite3')
            with _write_identity(database, create=True, empty=True), _journal_identity(database):
                db = sqlite3.connect(editor_uri(database), uri=True, timeout=2)
                try:
                    if db.execute('PRAGMA journal_mode=TRUNCATE').fetchone()[0].lower() != 'truncate':
                        raise CloudInputError('Cannot protect the new project rollback journal.')
                    db.execute('PRAGMA synchronous=FULL')
                    db.execute('PRAGMA application_id=1129071696'); db.execute('PRAGMA user_version=1')
                    db.execute('CREATE TABLE state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), project_id TEXT NOT NULL, revision INTEGER NOT NULL, metadata TEXT NOT NULL)')
                    db.execute('CREATE TABLE images (id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL UNIQUE, record TEXT NOT NULL)')
                    now = datetime.now(timezone.utc).isoformat()
                    metadata = {'schema_version': 2, 'classes': ['player'], 'name': name,
                                'created_at': now, 'updated_at': now}
                    with db:
                        db.execute('INSERT INTO state VALUES (1, ?, 0, ?)', (project_id, encoded(metadata).decode('utf-8')))
                finally:
                    db.close()
            descriptor = dict(schema_version=2, storage=FORMAT, project_id=project_id, database=database.name)
            # Publish only a fully initialized database; no existing file replaced.
            with (root / 'project.json').open('xb') as stream:
                stream.write(encoded(descriptor)); stream.flush(); os.fsync(stream.fileno())
        finally:
            guard.stack.close()
        return cls(root)

    @contextmanager
    def connection(self):
        if self.closed:
            raise CloudInputError('Project editor is closed.')
        path = reject_reparse(self.database)
        if _identity(path) != self.database_identity or path.stat().st_size > MAX_DATABASE:
            raise CloudInputError('Project database was replaced or exceeds its limit.')
        with _write_identity(path) as held, _journal_identity(path):
            if _identity(path) != self.database_identity:
                raise CloudInputError('Project database identity changed.')
            header = _header(path, held)
            if len(header) != 100 or header[:16] != b'SQLite format 3\0' or header[18:20] != b'\1\1':
                raise CloudInputError('Only a closed rollback-journal database can be edited.')
            # Never immutable on a mutable editor database. Unknown/hot journal
            # recovery is deliberately separate from ordinary authoring.
            uri = editor_uri(path)
            db = sqlite3.connect(uri, uri=True, timeout=2)
            try:
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_JSON)
                db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 32)
                db.execute('PRAGMA trusted_schema=OFF'); db.execute('PRAGMA synchronous=FULL')
                if db.execute('PRAGMA journal_mode=TRUNCATE').fetchone()[0].lower() != 'truncate':
                    raise CloudInputError('Cannot protect the project rollback journal.')
                pagesize = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute('PRAGMA max_page_count=' + str(MAX_DATABASE // pagesize))
                deadline = time.monotonic() + 10
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                try:
                    yield db
                except BaseException as primary:
                    try: db.close()
                    except BaseException as cleanup: primary.add_note('Connection cleanup also failed: ' + type(cleanup).__name__)
                    db = None
                    raise
            finally:
                if db is not None:
                    try: db.close()
                    except BaseException as exc:
                        raise CommitUncertain('Connection cleanup did not acknowledge completion; reconcile the save before continuing.') from exc

    def _snapshot(self, db):
        if (db.execute('PRAGMA application_id').fetchone()[0] != 1129071696
                or db.execute('PRAGMA user_version').fetchone()[0] != 1):
            raise CloudInputError('Unsupported project database.')
        for name in ('state', 'images'):
            if db.execute('SELECT type FROM sqlite_master WHERE name=?', (name,)).fetchone() != ('table',):
                raise CloudInputError('Invalid project storage tables.')
        if db.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:
            raise CloudInputError('Project databases cannot contain triggers or views.')
        row = db.execute('SELECT project_id,revision,metadata FROM state WHERE singleton=1').fetchone()
        if (row is None or row[0] != self.project_id or type(row[1]) is not int
                or not 0 <= row[1] < 2**53):
            raise CloudInputError('Project database identity or revision is invalid.')
        value = json_value(row[2]); records = []
        if type(value) is not dict or 'images' in value:
            raise CloudInputError('Invalid project metadata.')
        size = len(encoded(value))
        count, total, largest = db.execute('SELECT count(*),coalesce(sum(length(CAST(record AS BLOB))),0),coalesce(max(length(CAST(record AS BLOB))),0) FROM images').fetchone()
        if count > MAX_IMAGES or size + total > MAX_PROJECT_JSON or largest > MAX_JSON:
            raise CloudInputError('Project metadata exceeds the supported envelope.')
        for identity, raw in db.execute('SELECT id,record FROM images ORDER BY ordinal'):
            record = json_value(raw)
            if type(record) is not dict or record.get('id') != identity:
                raise CloudInputError('Stored image identity disagrees with its record.')
            records.append(record)
        value['images'] = records
        return manifest(value), row[1]

    def load(self):
        with self.connection() as db:
            db.execute('BEGIN')
            return self._snapshot(db)

    def commit(self, record, *, revision, previous_digest, operation):
        """One atomic image/review commit. The operation token makes lost ACKs safe."""
        image_record(record)
        if type(operation) is not str or not TOKEN.fullmatch(operation):
            raise CloudInputError('Invalid save operation identity.')
        raw = encoded(record).decode('utf-8')
        with self.connection() as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                value, actual = self._snapshot(db)
                if actual != revision:
                    raise CloudInputError(STALE)
                if actual >= 2**53 - 1:
                    raise CloudInputError('The project revision limit has been reached; no edit was written.')
                index = next((i for i, r in enumerate(value['images']) if r['id'] == record['id']), None)
                if index is None or digest(value['images'][index]) != previous_digest:
                    raise CloudInputError(STALE)
                old = value['images'][index]
                for key in ('file', 'width', 'height', 'pixel_sha256'):
                    if old.get(key) != record.get(key):
                        raise CloudInputError('Image identity cannot change during annotation save.')
                value['images'][index] = deepcopy(record); manifest(value)
                metadata = {k: v for k, v in value.items() if k != 'images'}
                metadata['updated_at'] = datetime.now(timezone.utc).isoformat()
                metadata['_vaporlock_last_commit'] = dict(operation=operation, image=record['id'], digest=digest(record))
                db.execute('UPDATE images SET record=? WHERE id=?', (raw, record['id']))
                db.execute('UPDATE state SET revision=?,metadata=? WHERE singleton=1',
                           (actual + 1, encoded(metadata).decode('utf-8')))
                try:
                    db.commit()
                except BaseException as exc:
                    # A failed COMMIT acknowledgment is not evidence of rollback.
                    raise CommitUncertain('Save outcome needs reconciliation. Your draft has been retained.') from exc
                return actual + 1
            except BaseException as primary:
                try: db.rollback()
                except BaseException as cleanup: primary.add_note('Rollback cleanup also failed: ' + type(cleanup).__name__)
                raise

    def reconcile(self, operation, record, previous_revision, previous_digest):
        value, revision = self.load()
        marker = value.get('_vaporlock_last_commit')
        expected = dict(operation=operation, image=record['id'], digest=digest(record))
        stored = next((r for r in value['images'] if r['id'] == record['id']), None)
        if marker == expected and revision == previous_revision + 1 and stored == record:
            return revision
        if revision == previous_revision and stored is not None and digest(stored) == previous_digest:
            return None  # Proven not committed: retain draft, permit an explicit retry.
        raise CloudInputError('Save could not be confirmed. Reopen the project and compare the retained draft before retrying.')

    def close(self):
        self.closed = True
        self.guard.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
