"""Canonical local Windows file paths, including deep annotation outputs.

No namespace expansion, UNC support or resolving away reparse points. The
normal and extended spellings share one internal path and lock identity.
"""
import ntpath
import os
from pathlib import Path
import re


def local_path(value):
    if os.name!='nt':return Path(os.path.abspath(value))
    raw=os.fspath(value)
    if type(raw) is not str or not raw or len(raw)>32000 or any(ord(c)<32 for c in raw):
        raise ValueError('Invalid local annotation path.')
    raw=raw.replace('/','\\')
    if raw.startswith('\\\\?\\'):
        raw=raw[4:]
        if not re.match(r'^[A-Za-z]:\\',raw):raise ValueError('Unsupported annotation namespace.')
    elif raw.startswith(('\\\\','\\??\\','\\.\\')):
        raise ValueError('Annotation paths require a local drive.')
    if any(part in ('.','..') for part in raw.split('\\')):
        raise ValueError('Ambiguous annotation path component.')
    raw_drive,raw_tail=ntpath.splitdrive(raw)
    if raw_drive and not raw_tail.startswith('\\'):
        raise ValueError('Drive-relative annotation path rejected.')
    for part in raw_tail.split('\\'):
        if part and (part.endswith(('.',' ')) or any(c in '<>:"|?*' for c in part)
                or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',part,re.I)):
            raise ValueError('Ambiguous annotation path component.')
    normal=ntpath.abspath(raw);drive,tail=ntpath.splitdrive(normal)
    if re.fullmatch('[A-Za-z]:',drive) is None or not tail.startswith('\\'):
        raise ValueError('Annotation paths require an absolute local drive.')
    for part in tail.split('\\'):
        if not part:continue
        if (part.endswith(('.',' ')) or any(c in '<>:"|?*' for c in part)
                or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',part,re.I)):
            raise ValueError('Ambiguous annotation path component.')
    return Path('\\\\?\\'+normal)


def display_path(value):
    text=str(value)
    return text[4:] if os.name=='nt' and text.startswith('\\\\?\\') else text


def lock_identity(value):
    return os.path.normcase(str(local_path(value)))


def reject_reparse(value):
    path=local_path(value)
    for part in (path,*path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('Annotation paths must not contain links or reparse points.')
    return path
