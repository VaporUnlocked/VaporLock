"""Local dataset locking and recoverable annotation commits."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import uuid
from dataset_paths import reject_reparse,lock_identity,local_path

_owned = threading.local()
JOURNAL = ".vaporlock-annotation-transaction.json"


class DatasetBusyError(OSError):
    pass


@contextmanager
def dataset_lock(root: Path):
    root = reject_reparse(root)
    identity=lock_identity(root)
    root.mkdir(parents=True, exist_ok=True)
    held = getattr(_owned, "roots", set())
    if identity in held:
        yield
        return
    with (root / ".vaporlock-work.lock").open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DatasetBusyError("This dataset is being processed. Stop the background job before editing it.") from exc
        _owned.roots = held | {identity}
        try:
            yield
        finally:
            _owned.roots = held
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def annotation_path(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or (relative not in ("classes.txt", ".vaporlock-review.json")
            and not (Path(relative).parts[0] == "labels" and path.suffix == ".txt")):
        raise ValueError("Invalid annotation transaction path.")
    return path


def recover_annotations(root: Path) -> None:
    if os.name == 'nt':
        from dataset_transaction import recover_transaction
        recover_transaction(root)
        return
    journal = root / JOURNAL
    if not journal.is_file():
        return
    if journal.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Invalid annotation recovery journal.')
    payload = json.loads(journal.read_text(encoding="utf-8"))
    if payload.get('schema') == 2:
        from dataset_transaction import recover_transaction
        recover_transaction(root)
        return
    if journal.stat().st_size > 32 * 1024 * 1024 or type(payload) is not dict:
        raise ValueError('Invalid annotation recovery journal.')
    if payload.get("state") != "committed":
        # Legacy pending journals lack file identity evidence. Keep their bytes
        # for owner recovery; never overwrite newer data using untrusted text.
        raise ValueError('An older pending annotation transaction needs recovery review. All files were preserved.')
    journal.unlink()


def commit_annotations(root: Path, updates: dict[Path, str], removals=(), *, expected_updates=None, checkpoint=lambda _stage: None):
    with dataset_lock(root):
        recover_annotations(root)
        if os.name == 'nt':
            from dataset_transaction import commit_transaction
            return commit_transaction(root, updates, removals, expected_updates=expected_updates, checkpoint=checkpoint)
        if expected_updates:
            raise OSError('Protected metadata updates require Windows file identity protection.')
        if removals:
            raise OSError('Image removal requires Windows file identity protection.')
        before = {}
        for path in updates:
            relative = path.relative_to(root).as_posix()
            annotation_path(root, relative)
            before[relative] = path.read_text(encoding="utf-8") if path.exists() else None
        journal = root / JOURNAL
        atomic_text(journal, json.dumps({"state": "pending", "before": before}, ensure_ascii=False))
        try:
            for path, text in updates.items():
                atomic_text(path, text)
            atomic_text(journal, '{"state":"committed"}')
        except BaseException:
            recover_annotations(root)
            raise
        journal.unlink()
        return {'removed': 0, 'cleanup_pending': False}
