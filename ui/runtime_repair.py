"""Verify and restore the native components of a portable Community release."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile


COMPONENTS = {
    "CPU": ("engine", ("VaporLock.exe", "onnxruntime.dll", "onnxruntime_providers_shared.dll")),
    "DirectML": ("engine/dml", ("VaporLockDml.exe", "onnxruntime.dll", "onnxruntime_providers_shared.dll", "DirectML.dll")),
    "TensorRT RTX": ("engine/trt", ("VaporLockTensorRt.exe", "onnxruntime.dll", "onnxruntime_providers_shared.dll",
                                   "tensorrt_rtx_1_6.dll", "tensorrt_onnxparser_rtx_1_6.dll")),
    "Elevated Relative Mouse": ("engine", ("VaporLockMouseHost.exe",)),
}
REDISTRIBUTABLES = (
    "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll",
    "msvcp140_2.dll", "msvcp140_codecvt_ids.dll", "concrt140.dll",
)


def _ordinary_path(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Linked runtime path rejected: {part.name}")
    return path


def _manifest_rows(root, version, component):
    path = _ordinary_path(root / "information/RELEASE_MANIFEST.json")
    with path.open("rb") as stream:
        data = stream.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("The installed release manifest is too large.")
    manifest = json.loads(data)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("version") != version or manifest.get("edition") != "community"
            or not isinstance(manifest.get("files"), list)):
        raise ValueError(f"Runtime repair needs the installed Community {version} release manifest.")
    directory, required = COMPONENTS[component]
    paths = {f"{directory}/{name}" for name in required}
    possible = paths | {f"{directory}/{name}" for name in REDISTRIBUTABLES}
    rows = {}
    for row in manifest["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("Invalid installed release manifest.")
        name = row["path"]
        if name not in possible:
            continue
        size, checksum = row.get("bytes"), row.get("sha256")
        if (name in rows or type(size) is not int or size <= 0
                or not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)):
            raise ValueError("Invalid native component in the installed manifest.")
        rows[name] = (size, checksum)
    if not paths.issubset(rows):
        raise ValueError("The selected component is incomplete in the installed release manifest.")
    return rows


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _status(path, size, checksum):
    try:
        info = _ordinary_path(path).stat()
    except FileNotFoundError:
        return "Missing"
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Invalid runtime file: {path.name}")
    if info.st_size != size or _digest(path) != checksum:
        return "Different from this release"
    return "Verified"


def inspect_runtime(root, version, component):
    return {name: _status(root / name, *identity)
            for name, identity in _manifest_rows(root, version, component).items()}


def repair_runtime(root, version, component, archive):
    rows = _manifest_rows(root, version, component)
    targets = [name for name, identity in rows.items() if _status(root / name, *identity) != "Verified"]
    if not targets:
        return 0
    scratch = Path(tempfile.mkdtemp(prefix=".runtime-repair-", dir=root))
    backups = []
    keep_backup = False
    try:
        with zipfile.ZipFile(archive) as package:
            names = [entry.filename.casefold() for entry in package.infolist()]
            for number, name in enumerate(targets):
                member = "VaporLock/" + name
                size, checksum = rows[name]
                if names.count(member.casefold()) != 1:
                    raise ValueError(f"The archive needs exactly one copy of {Path(name).name}.")
                info = package.getinfo(member)
                if info.file_size != size or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError(f"{Path(name).name} differs from the installed release manifest.")
                staged = scratch / f"{number}.new"
                with package.open(info) as source, staged.open("xb") as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
                if staged.stat().st_size != size or _digest(staged) != checksum:
                    raise ValueError(f"{Path(name).name} failed SHA-256 verification.")
        for number, name in enumerate(targets):
            destination = _ordinary_path(root / name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup = scratch / f"{number}.old" if destination.exists() else None
            if backup is not None:
                os.replace(destination, backup)
            backups.append((destination, backup))
            os.replace(scratch / f"{number}.new", destination)
    except BaseException:
        try:
            for destination, backup in reversed(backups):
                if backup is not None:
                    os.replace(backup, destination)
                else:
                    destination.unlink(missing_ok=True)
        except OSError as error:
            keep_backup = True
            raise OSError(f"Runtime rollback is incomplete. Backups are in {scratch}.") from error
        raise
    finally:
        if not keep_backup:
            shutil.rmtree(scratch, ignore_errors=True)
    return len(targets)
