"""Code-owned SQLite routes with normal locking and long local Windows paths."""
import os
from pathlib import Path
from urllib.parse import quote

from dataset_paths import display_path,reject_reparse


def _uri(path,query):
    # Path text is escaped as a path, never appended as a URI query. No caller
    # supplies a VFS or a query. The Windows variant retains ordinary locking.
    raw=os.fspath(path)
    if type(raw) is not str or any(ord(c)<32 or 127<=ord(c)<=159 for c in raw):
        raise ValueError('Invalid SQLite project path.')
    path=reject_reparse(raw)
    if os.name=='nt':
        # Keep the complete validated extended LOCAL DRIVE spelling. Encoding
        # all separators keeps the native prefix in filename data, not a URI
        # authority/query. SQLite decodes it exactly once before its locking VFS.
        return 'file:'+quote(str(path),safe='')+'?'+query+'&vfs=win32-longpath'
    return Path(display_path(path)).as_uri()+'?'+query


def editor_uri(path):return _uri(path,'mode=rw')


def snapshot_uri(path):return _uri(path,'mode=ro&immutable=1')
