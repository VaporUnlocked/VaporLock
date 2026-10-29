"""Bounded, non-mutating discovery for the model tools list."""
import os
from pathlib import Path
import time


def discover_entries(models, profiles, *, maximum=1024, seconds=1.0):
    entries=[]; visited=0; deadline=time.monotonic()+seconds; limited=False
    for root,kind,suffix,depth in ((Path(models),'Model','.onnx',5),(Path(profiles),'Profile','.json',0)):
        stack=[(root,0)]
        while stack:
            directory,level=stack.pop()
            try:
                if getattr(directory.lstat(),'st_file_attributes',0)&0x400 or directory.is_symlink(): continue
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        visited+=1
                        if visited>4096 or len(entries)>=maximum or time.monotonic()>deadline:
                            limited=True; return sorted(entries,key=lambda x:(x[0],x[1].casefold())),limited
                        try:
                            info=entry.stat(follow_symlinks=False)
                            if entry.is_symlink() or getattr(info,'st_file_attributes',0)&0x400: continue
                            if entry.is_dir(follow_symlinks=False) and level<depth:
                                stack.append((Path(entry.path),level+1))
                            elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in (('.onnx','.cloud') if kind=='Model' else (suffix,)):
                                path=Path(entry.path); label=str(path.relative_to(root))
                                if len(label)<=512: entries.append((kind,label,path))
                        except OSError: continue
            except OSError: continue
    return sorted(entries,key=lambda x:(x[0],x[1].casefold())),limited


def discover_local_models(models_root):
    """Discover local ONNX/Cloud models without a publisher catalog or service."""
    entries, _limited = discover_entries(models_root, Path(models_root) / '.no-profiles', maximum=10000, seconds=2.0)
    return [path for kind, _label, path in entries if kind == 'Model'], {}
