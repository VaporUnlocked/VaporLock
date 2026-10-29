"""Transactional per-image storage for large annotation projects.

The small project.json descriptor identifies one SQLite database. All dataset
metadata and image records are committed together in that database. FULL
synchronous rollback-journal transactions keep acknowledged edits durable;
reviewing one image never serializes or rewrites the other images.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

FORMAT = "cloud-project-sqlite-v1"
MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_PROJECT_BYTES = 8 * 1024 * 1024 * 1024
STALE = "The project changed in another window or editor. Reopen it before saving."


def _json(value):
    result = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(result.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ValueError("A single image record or project metadata exceeds 64 MiB.")
    return result


class ProjectStore:
    def __init__(self, path, identifier):
        self.path, self.identifier = Path(path), identifier

    @contextmanager
    def connection(self, *, create=False):
        if not create and (not self.path.is_file() or self.path.stat().st_size > MAX_PROJECT_BYTES):
            raise ValueError("Project database is missing or exceeds the 8 GiB supported limit.")
        # mode=rw prevents a missing database from being silently recreated.
        uri = self.path.resolve().as_uri() + ("?mode=rwc" if create else "?mode=rw")
        db = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=2000")
            yield db
        finally:
            db.close()

    def initialize(self, manifest):
        with self.connection(create=True) as db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA application_id=1129071696")
            db.execute("PRAGMA user_version=1")
            db.execute("CREATE TABLE state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), project_id TEXT NOT NULL, revision INTEGER NOT NULL, metadata TEXT NOT NULL)")
            db.execute("CREATE TABLE images (id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL UNIQUE, record TEXT NOT NULL)")
            metadata = {k: v for k, v in manifest.items() if k != "images"}
            with db:
                db.execute("INSERT INTO state VALUES(1, ?, 0, ?)", (self.identifier, _json(metadata)))
                db.executemany("INSERT INTO images VALUES(?, ?, ?)",
                    ((row["id"], i, _json(row)) for i, row in enumerate(manifest["images"])))

    def _state(self, db):
        if db.execute("PRAGMA application_id").fetchone()[0] != 1129071696 or db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported project database format.")
        row = db.execute("SELECT project_id, revision, metadata FROM state WHERE singleton=1").fetchone()
        if row is None or row[0] != self.identifier or type(row[1]) is not int or row[1] < 0:
            raise ValueError("Project descriptor and database do not match.")
        return row

    def load(self):
        try:
            with self.connection() as db:
                db.execute("BEGIN")
                _, revision, metadata = self._state(db)
                if len(metadata) > MAX_RECORD_BYTES:
                    raise ValueError("Project metadata is too large.")
                value = json.loads(metadata)
                if not isinstance(value, dict):
                    raise ValueError("Invalid stored project metadata.")
                images = []
                for identifier, raw in db.execute("SELECT id, record FROM images ORDER BY ordinal"):
                    if len(raw) > MAX_RECORD_BYTES:
                        raise ValueError("Stored image record exceeds its size limit.")
                    row = json.loads(raw)
                    if not isinstance(row, dict) or row.get("id") != identifier:
                        raise ValueError("Stored image record identifier is inconsistent.")
                    images.append(row)
                value["images"] = images
                return value, revision
        except (sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            raise ValueError("Project database could not be read: " + str(exc)) from exc

    def commit(self, manifest, expected_revision, *, records=None):
        """Commit a selected record set, or one complete bulk operation, with CAS."""
        metadata = _json({k: v for k, v in manifest.items() if k != "images"})
        rows = None if records is None else [(row["id"], _json(row)) for row in records]
        with self.connection() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                _, actual, _ = self._state(db)
                if actual != expected_revision:
                    raise RuntimeError(STALE)
                if rows is None:
                    db.execute("DELETE FROM images")
                    db.executemany("INSERT INTO images VALUES(?, ?, ?)",
                        ((row["id"], i, _json(row)) for i, row in enumerate(manifest["images"])))
                else:
                    for identifier, raw in rows:
                        result = db.execute("UPDATE images SET record=? WHERE id=?", (raw, identifier))
                        if result.rowcount != 1:
                            raise RuntimeError("The edited image no longer exists. Reopen the project.")
                db.execute("UPDATE state SET revision=?, metadata=? WHERE singleton=1", (actual+1, metadata))
                db.commit()
                return actual+1
            except BaseException:
                db.rollback()
                raise

    def backup(self, path):
        """Explicit SQLite snapshot, useful before an infrequent bulk operation."""
        with self.connection() as source:
            target = sqlite3.connect(path)
            try:
                source.backup(target)
            finally:
                target.close()


@contextmanager
def migration_lock(root):
    """OS-owned upgrade lock; automatically released even after a process crash."""
    import os
    lock = open(Path(root) / "project.lock", "a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another window is upgrading this project. Reopen it after that operation finishes.") from exc
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another window is upgrading this project. Reopen it after that operation finishes.") from exc
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
