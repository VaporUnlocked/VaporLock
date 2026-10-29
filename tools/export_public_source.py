"""Allowlisted, source-only public export. Never runs product code or publishes.

The exporter intentionally does not reuse private backup/deployment machinery.
Secrets found by the conservative scanner are reported by type/path, never value.
This check complements human review; it does not certify ownership or licensing.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile


ROOT_FILES = frozenset({
    ".gitignore",
    "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "PRIVACY.md",
    "requirements-community.txt", "requirements-vlui.txt", "BuildCommunity.ps1",
    "CMakeLists.txt", "CMakePresets.json", "product_version.txt", "RunGui.bat",
    "Start.bat", "Background.png", "VaporLock-Settings-Guide.html",
})
REQUIRED_ROOT_FILES = frozenset({"README.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
    "requirements-community.txt", "BuildCommunity.ps1", "CMakeLists.txt"})
CLIENT_DIRS = frozenset({"app", "capture", "config", "controller_host", "controller_proxy",
    "core", "detection", "gui", "hardware", "output", "overlay", "plugins",
    "selection", "tracking", "ui", "acceleration"})
ASSET_DIRS = frozenset({"assets", "guides"})
TOOLS = frozenset({"export_public_source.py", "build_public_client.py",
    "build_community_bundle.py", "package_community_release.py",
    "refresh_public_manifests.py", "build_settings_guides.py", "build_controller_host.ps1",
    "fetch_sam_weights.py"})
# These are the active client resources, not whole historical handoff branches.
# Index entries are checked against this set; an edited index cannot broaden it.
UI_RUNTIME_FILES = frozenset("ui_system/runtime/" + name for name in (
    "resources.json", "component-map-014.json", "native-presentation-014.json",
    "annotation-translations.json", "live-adapter.js", "live.html", "qwebchannel.js",
    "native-component-renderer-014.js", "native-components-014.js",
    "annotation-native-copy.json", "native-renderer-014.css",
    "bundled/components.json", "bundled/context-contract.json", "bundled/context-index.json",
    "bundled/context-relations.json", "bundled/SmokeyGlass.png",
    "bundled/arrow-down-light.svg", "bundled/arrow-up-light.svg", "bundled/check-dark.svg",
    "bundled/native-canvases.js", "bundled/native-component-copy.json",
    "bundled/native-component-renderer.css", "bundled/native-component-views.js",
    "bundled/native-demo.css", "bundled/native-policy.js", "bundled/trusted-context.mjs"))
UI_SCHEMA_NAMES = frozenset({"index.json", "manifest.schema.json", "style.schema.json",
    "layout.schema.json", "semantics.json", "css-policy.json", "tokens.json",
    "presentation-defaults.json"})
UI_FILES = (UI_RUNTIME_FILES
    | frozenset("ui_system/proposals/contract-v1-r5/" + name for name in UI_SCHEMA_NAMES)
    | frozenset("ui_system/contracts/component-contract-v1-r1/" + name
        for name in UI_SCHEMA_NAMES | {"components-v1.json"})
    | {"ui_system/handoffs/foundation-v1-r1/components-v1.json"})
CONTROLLER_TEMPLATES = frozenset({
    "controller_host/profiles/vapor-lock-xbox-one-windows.json"})
SOURCE_DATA_FILES = frozenset({
    "third_party/qt/LICENSE-GPL-3.0.txt", "third_party/qt/LICENSE-LGPL-3.0.txt",
    "controller_proxy/README.md", "hardware/README.md",
    "hardware/interception/LICENSE-LGPL-3.0.txt", "ui/requirements.txt",
    "controller_host/hidmaestro_integration/THIRD-PARTY-NOTICES.txt",
    "controller_host/hidmaestro_integration/LICENSE",
    "assets/cloud-annotation/LICENSE.txt", "assets/cloud-annotation/LICENSE-TinyViT.txt",
    "assets/cloud-annotation/NOTICE.txt", "cloud_source/4.0.0/config/s2_player.json",
    "cloud_source/4.0.0/training_source/cloudstudio/_vendor/mobile_sam/LICENSE",
    "cloud_source/4.0.0/training_source/cloudstudio/_vendor/mobile_sam/LICENSE-TinyViT.txt",
    "cloud_source/4.0.0/training_source/cloudstudio/_vendor/mobile_sam/NOTICE.txt",
    "cloud_source/4.0.0/training_source/cloudstudio/_vendor/mobile_sam/PROVENANCE.json",
    "cloud_source/4.0.0/training_source/cloudstudio/_vendor/light_hq_sam/LICENSE",
    "cloud_source/4.0.0/training_source/cloudstudio/_vendor/light_hq_sam/LICENSE-TinyViT.txt",
    "cloud_source/4.0.0/training_source/cloudstudio/_vendor/light_hq_sam/NOTICE.txt",
    "cloud_source/4.0.0/training_source/cloudstudio/_vendor/light_hq_sam/PROVENANCE.json",
})
PRIVATE_DATA_NAME = re.compile(r"(?:^|[-_. ])(?:passwords?|passwd|credentials?|secrets?|"
    r"(?:user[-_])?key[-_]registry|activation|installation[-_]identity|"
    r"(?:offline[-_])?lease(?:[-_]state)?|license[-_]state|local[-_]api|admin[-_]client)(?:[-_. ]|$)", re.I)
PUBLIC_TEST_FILES = frozenset("tests/" + name for name in (
    "cloud_pipe_fixture.cpp", "cloud_owned_pipe_fixture.cpp", "bgra_tensor_resize_tests.cpp",
    "onnx_cache_eligibility_tests.cpp", "tensorrt_cache_tests.cpp", "inference_cadence_tests.cpp",
    "capture_qualification_tests.cpp", "native_tuning_tests.cpp", "native_public_access_tests.cpp",
    "hardware_output_tests.cpp", "frame_rendering_tests.cpp", "async_telemetry_tests.cpp",
    "dataset_writer_tests.cpp", "cpu_placement_tests.cpp", "inference_metadata_tests.cpp",
    "tensorrt_rtx_smoke.cpp", "aim_output_tests.h", "compensation_output_tests.h",
    "controller_pipe_tests.h", "feature_batch3_tests.h", "feature_batch5_tests.h",
    "crosshair_reference_tests.h", "capture_recovery_tests.h",
    "test_public_source_export.py", "test_public_client_access.py", "test_public_build_tools.py", "test_community_bundle.py", "test_community_package.py"))
CLOUD_BRANCHES = ("runtime_sdk", "training_source", "config")
CLOUD_FILES = frozenset({"IMPORT_MANIFEST.json", "requirements-runtime.txt",
    "requirements-training.txt", "RUN_CLOUD.md", "TRAIN_CLOUD.md", "run_cloud.py", "train_cloud.py"})
DOC_BRANCHES = ("settings-guide",)
DOC_FILES = frozenset({"PUBLIC_SOURCE_EXPORT_SCOPE.txt"})
SUITE_ROOT_FILES = frozenset({"CMakeLists.txt", "build.ps1", "package.ps1", "README.md",
    "LICENSE",
    "BUNDLED_LICENSES.txt", "LICENSES.txt", "MODEL_GUIDE.txt", "PORTABLE-README.txt",
    "THIRD_PARTY_NOTICES.md", "dependency-manifest.json"})
SUITE_ASSETS = frozenset({"assets/characters/SWAT.glb", "assets/fonts/Interface.ttf",
    "assets/fonts/OFL.txt", "assets/Suite.ico", "assets/characters/Ghost-MW2019-NOTICE.txt"})
SOURCE_EXT = frozenset({".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".cuh", ".cu", ".inc",
    ".inl", ".ipp", ".tcc", ".cs", ".csproj", ".props", ".targets", ".cmake", ".ps1", ".bat",
    ".cmd", ".sh", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".html", ".rc",
    ".in", ".qrc", ".ui", ".spec", ".inf", ".manifest", ".def", ".resx", ".ino", ".asm",
    ".s", ".hlsl", ".hlsli", ".glsl", ".vert", ".frag", ".proto", ".fbs"})
ASSET_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico",
    ".ttf", ".otf", ".woff", ".woff2"})
DATA_EXT = frozenset({".json", ".txt", ".md", ".rst", ".yml", ".yaml", ".toml", ".xml"})
BLOCKED_PARTS = frozenset({".git", ".codex", ".codex-remote-attachments", "__pycache__",
    "node_modules", "bin", "obj", "build", "build-temp", "dist", "release", "releases",
    "reports", "logs", "runs", "datasets", "downloads", "output-data", "backups", "private",
    "credentials", "secrets", "keys", "database", "databases", "server_runtime", "live-data",
    "customer-data", "customer_data", "license_service", "discord_key_bot", "installer",
    "boss use only - no access granted", "pentest folder"})
BLOCKED_FILES = frozenset({"AGENTS.md", "qwebchannel.extraction.json", "license-state.json",
    "activation.json", "installation-identity.json", "admin-client.json", "local-api.json"})
BLOCKED_FILES_CASEFOLD = frozenset(name.casefold() for name in BLOCKED_FILES)
RETIREMENT_WORDS = re.compile(r"(?:^|_)(?:license|licensing|premium_gate|enforcement|installation_authority|"
    r"start_policy|release_catalog|release_store|release_import|release_history|admin|administrator|"
    r"deployment|cutover|publish|announcement|statistics|revocation)(?:_|\.|$)", re.I)
NOTICE_NAME = re.compile(r"^(?:licen[cs]e|copying|notice|third.party.notices|authors|copyright)(?:[.\-_].*)?$", re.I)
SDK_NOTICE_ROOTS = ("onnxruntime-gpu", "onnxruntime-directml-1.24.4", "cuda-runtime",
    "cuda-toolkit-13.2.2", "tensorrt-rtx-1.6.1.120")
MAX_FILE_BYTES = 128 * 1024 * 1024
TEXT_SCAN_BYTES = 32 * 1024 * 1024
LIMITATIONS = [
    "Source only; Python/.NET/C++ build toolchains, downloaded SDKs and runtime binaries are not included.",
    "No personal models, profiles, datasets, customer records, credentials or service configuration are exported.",
    "The one explicitly named controller compatibility JSON is authored component source, not a saved user profile.",
    "No website, administrator suite, bot, licensing service, updater or hosted distribution infrastructure is exported.",
    "Controller embedded resource-*.bin and generated dependency DLLs are excluded; obtain lawful dependencies before building that optional component.",
    "The original Ghost-MW2019.glb asset is excluded; the community Aim Testing Suite uses its separately licensed SWAT builtin and original procedural view geometry.",
    "Third-party licenses retain their own terms; the project license cannot impose extra restrictions on separately licensed components.",
    "Pattern scanning is an additional safeguard, not proof that all possible secrets or licensing conflicts are absent. Review the generated inventory before publication.",
    "Historical enforcement/proof tests are excluded. ONNX cache/metadata native tests require synthetic ONNX fixtures not included in this source-only archive; generating those fixtures is not validated by export verification.",
]


class ExportError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> PurePosixPath:
    if "\\" in value or ":" in value or "\x00" in value:
        raise ExportError("Unsafe archive path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ExportError("Unsafe archive path")
    reserved = {"con", "prn", "aux", "nul"} | {f"{base}{i}" for base in ("com", "lpt") for i in range(1, 10)}
    if any(p.endswith((".", " ")) or p.split(".")[0].casefold() in reserved for p in relative.parts):
        raise ExportError("Unsafe Windows archive alias")
    return relative


def _plain(path: Path) -> os.stat_result:
    item = path.lstat()
    if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
        raise ExportError("Symlink/reparse paths are not exportable")
    return item


def _plain_ancestors(path: Path) -> None:
    for item in reversed((path, *path.parents)):
        _plain(item)


def _blocked(parts: tuple[str, ...], *, directory: bool = False) -> bool:
    folders = parts if directory else parts[:-1]
    return (any(p.casefold() in BLOCKED_PARTS or p.casefold().startswith((".venv", "venv", ".env")) for p in parts)
            or any(PRIVATE_DATA_NAME.search(p) for p in folders))


def _branch_allowed(branch: str, roots: tuple[str, ...]) -> bool:
    return any(branch == root or branch.startswith(root + "/") or root.startswith(branch + "/")
               for root in roots)


def _exact_member(relative: str, members: frozenset | set, *, directory: bool) -> bool:
    return any(name.startswith(relative + "/") for name in members) if directory else relative in members


def allowed(relative: str, *, directory: bool = False) -> bool:
    path = safe_relative(relative)
    parts = path.parts
    if _blocked(parts, directory=directory) or path.name.casefold() in BLOCKED_FILES_CASEFOLD:
        return False
    suffix = path.suffix.casefold()
    if not directory and suffix in DATA_EXT and PRIVATE_DATA_NAME.search(path.name):
        return False
    if not directory and suffix in {".key", ".pem", ".pfx", ".p12", ".db", ".sqlite", ".sqlite3",
            ".exe", ".dll", ".pyd", ".lib", ".a", ".so", ".bin", ".pt", ".pth", ".onnx",
            ".cloud", ".cloudtrain", ".engine", ".plan", ".zip", ".pyc", ".pyo"}:
        return False
    root = parts[0]
    if len(parts) == 1:
        return (directory and root in CLIENT_DIRS | ASSET_DIRS | {"ui_system", "cloud_source", "docs", "tests", "tools", "third_party"}) or relative in ROOT_FILES
    if root in CLIENT_DIRS:
        if parts[:2] == ("controller_host", "profiles"):
            return _exact_member(relative, CONTROLLER_TEMPLATES, directory=directory)
        if any(p.casefold() in {"models", "profiles", "data", "state"} for p in parts[1:-1]):
            return False
        return directory or suffix in SOURCE_EXT | ASSET_EXT or relative in SOURCE_DATA_FILES
    if root in ASSET_DIRS:
        return directory or suffix in ASSET_EXT | {".html"} or relative in SOURCE_DATA_FILES
    if root == "ui_system":
        return _exact_member(relative, UI_FILES, directory=directory)
    if root == "cloud_source":
        if parts[1] != "4.0.0":
            return False
        if len(parts) == 2:
            return directory
        if len(parts) == 3 and parts[2] in CLOUD_FILES:
            return not directory
        branch = "/".join(parts[2:] if directory else parts[2:-1])
        return _branch_allowed(branch, CLOUD_BRANCHES) and (directory or suffix in SOURCE_EXT or relative in SOURCE_DATA_FILES)
    if root == "tools":
        return not directory and len(parts) == 2 and parts[1] in TOOLS
    if root == "docs":
        if len(parts) == 2 and parts[1] in DOC_FILES:
            return not directory
        branch = "/".join(parts[1:] if directory else parts[1:-1])
        return _branch_allowed(branch, DOC_BRANCHES) and (directory or suffix in SOURCE_EXT)
    if root == "tests":
        return _exact_member(relative, PUBLIC_TEST_FILES, directory=directory)
    if root == "third_party":
        if len(parts) == 2:
            return directory and parts[1] in ("monocypher", "qt")
        if parts[1] == 'qt':
            return not directory and relative in SOURCE_DATA_FILES
        return parts[1] == "monocypher" and (directory or suffix in SOURCE_EXT | {".md", ".txt"} or bool(NOTICE_NAME.match(path.name)))
    return False


def _walk_selected(root: Path, predicate) -> list[tuple[str, Path]]:
    result = []
    _plain_ancestors(root)
    with ExitStack() as held:
        if os.name == "nt":
            for parent in reversed(root.parents):
                held.enter_context(_windows_handle(parent, directory=True))

        def descend(base):
            # Retain and validate the directory before enumerating it. A later
            # reparse check after os.walk has descended would be too late.
            if os.name == "nt":
                held.enter_context(_windows_handle(base, directory=True, deny_directory_write=True))
                scan_root = base
            else:
                descriptor = os.open(base, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0))
                held.callback(os.close, descriptor)
                named, opened = _plain(base), os.fstat(descriptor)
                if not stat.S_ISDIR(opened.st_mode) or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ExportError("Selected directory identity changed before enumeration")
                scan_root = descriptor
            with os.scandir(scan_root) as entries:
                candidates = sorted(entries, key=lambda entry: entry.name)
            for entry in candidates:
                candidate = base / entry.name
                relative = candidate.relative_to(root).as_posix()
                select_directory = predicate(relative, directory=True)
                select_file = predicate(relative, directory=False)
                if not (select_directory or select_file):
                    continue
                item = _plain(candidate)
                if stat.S_ISDIR(item.st_mode) and select_directory:
                    descend(candidate)
                elif select_file:
                    if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
                        raise ExportError("Selected files must be regular single-link files")
                    result.append((relative, candidate))

        descend(root)
    return sorted(result)


def suite_allowed(relative: str, *, directory: bool = False) -> bool:
    path = safe_relative(relative)
    if _blocked(path.parts, directory=directory) or path.name.casefold() in BLOCKED_FILES_CASEFOLD:
        return False
    if not directory and path.suffix.casefold() in DATA_EXT and PRIVATE_DATA_NAME.search(path.name):
        return False
    if directory:
        return path.parts[0] in {"source", "vendor"} or any(p.startswith(relative + "/") for p in SUITE_ASSETS)
    if relative in SUITE_ROOT_FILES | SUITE_ASSETS:
        return True
    if path.parts[0] in {"source", "vendor"}:
        return path.suffix.casefold() in SOURCE_EXT | {".txt", ".md", ".cmake"} or path.name == "CMakeLists.txt" or bool(NOTICE_NAME.match(path.name))
    return False


def select_files(source: Path, suite: Path | None = None) -> list[tuple[str, Path]]:
    selected = _walk_selected(source, allowed)
    _check_ui_resource_closure(selected)
    _check_cloud_source_closure(selected)
    # Do not recurse SDK installations. Include only these explicitly named notices.
    for sdk in SDK_NOTICE_ROOTS:
        for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "NOTICE", "NOTICE.txt", "ThirdPartyNotices.txt"):
            path = source / "third_party" / sdk / name
            if path.exists():
                _plain_ancestors(path)
                selected.append((path.relative_to(source).as_posix(), path))
    if suite is not None:
        selected.extend(("aim-testing-suite/" + relative, path) for relative, path in _walk_selected(suite, suite_allowed))
    names = [relative.casefold() for relative, _ in selected]
    if len(names) != len(set(names)):
        raise ExportError("Case-insensitive archive path collision")
    return sorted(selected)


def _check_cloud_source_closure(selected):
    members=dict(selected)
    prefix='cloud_source/4.0.0/'
    descriptor=members.get(prefix+'IMPORT_MANIFEST.json')
    if descriptor is None:
        return
    with read_plain(descriptor) as raw:
        manifest=json.loads(raw)
    for entry in manifest['files']:
        name=str(safe_relative(entry['path']))
        if not name.startswith(('runtime_sdk/','training_source/')):
            raise ExportError('Unexpected Cloud source manifest branch')
        path=members.get(prefix+name)
        if path is None:
            raise ExportError('Cloud source export is missing a declared member: '+name)
        with read_plain(path) as data:
            if len(data)!=entry['bytes'] or sha256(data)!=entry['sha256']:
                raise ExportError('Cloud source export member identity differs: '+name)


def _check_ui_resource_closure(selected: list[tuple[str, Path]]) -> None:
    """Validate only the three active indexes and their exact selected payloads.

    Internal context-index provenance references are descriptive JSON; they do
    not cause any historical proposal, proof, handoff or review file to be read.
    """
    selected_paths = dict(selected)
    indexes = (
        ("ui_system/runtime/resources.json", "resources", "file", "",
            UI_RUNTIME_FILES - {"ui_system/runtime/resources.json"}),
        ("ui_system/proposals/contract-v1-r5/index.json", "files", "path",
            "ui_system/proposals/contract-v1-r5/",
            {"ui_system/proposals/contract-v1-r5/" + name for name in UI_SCHEMA_NAMES - {"index.json"}}),
        ("ui_system/contracts/component-contract-v1-r1/index.json", "files", "path",
            "ui_system/contracts/component-contract-v1-r1/",
            {"ui_system/contracts/component-contract-v1-r1/" + name
                for name in (UI_SCHEMA_NAMES | {"components-v1.json"}) - {"index.json"}}),
    )
    for index_name, field, path_field, prefix, expected in indexes:
        if index_name not in selected_paths:
            continue
        with read_plain(selected_paths[index_name]) as raw:
            if len(raw) > 128 * 1024:
                raise ExportError("Active UI index exceeds its source bound")
            try:
                document = json.loads(raw)
                entries = document[field]
            except (ValueError, KeyError, TypeError):
                raise ExportError("Malformed active UI resource index: " + index_name) from None
        if not isinstance(entries, list) or len(entries) != len(expected):
            raise ExportError("Active UI resource closure changed: " + index_name)
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get(path_field), str):
                raise ExportError("Malformed active UI resource entry: " + index_name)
            relative = prefix + entry[path_field]
            safe_relative(relative)
            if relative not in expected or relative in seen or relative not in selected_paths:
                raise ExportError("Active UI resource closure changed: " + index_name)
            seen.add(relative)
            with read_plain(selected_paths[relative]) as payload:
                if (type(entry.get("bytes")) is not int or len(payload) != entry["bytes"]
                        or sha256(payload) != entry.get("sha256")):
                    raise ExportError("Active UI resource identity mismatch: " + relative)


PATTERNS = (
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
    ("discord-webhook", re.compile(rb"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/[0-9]{12,}/[A-Za-z0-9_\-]{24,}")),
    ("ultralytics-api-token", re.compile(rb"\bul_[0-9a-fA-F]{32,}\b")),
    ("github-access-token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,})\b")),
    ("aws-access-key", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
)
ASSIGNMENT = re.compile(rb"(?im)\b(?:api[_-]?key|secret|password|passwd|bot[_-]?token|access[_-]?token)\s*[=:]\s*[\"']([^\"'\r\n]{12,})[\"']")
PLACEHOLDER = re.compile(rb"(?i)(?:example|placeholder|replace|your[_ -]|changeme|test|dummy|redact|<|\$\{|os\.environ|keyring|password|token|secret)")


def secret_findings(data: bytes) -> list[str]:
    findings = [name for name, pattern in PATTERNS if pattern.search(data)]
    if len(data) <= TEXT_SCAN_BYTES:
        for match in ASSIGNMENT.finditer(data):
            if not PLACEHOLDER.search(match.group(1)):
                findings.append("credential-like-literal")
                break
    return sorted(set(findings))


@contextmanager
def _windows_handle(path: Path, *, directory: bool, writable: bool = False, create: bool = False,
                    deny_directory_write: bool = False):
    """Open final object itself; deny deletion while validating canonical identity."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    class Info(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("creation", wintypes.FILETIME),
            ("access", wintypes.FILETIME), ("write", wintypes.FILETIME),
            ("volume", wintypes.DWORD), ("high_size", wintypes.DWORD),
            ("low_size", wintypes.DWORD), ("links", wintypes.DWORD),
            ("high_index", wintypes.DWORD), ("low_index", wintypes.DWORD)]
    # READ_ATTRIBUTES alone is a metadata-only open on Windows and does not
    # establish the sharing exclusion needed to prevent directory renames.
    access = 0x81 if directory else 0xC0000000 if writable else 0x80000000
    sharing = 3 if directory and not deny_directory_write else 1
    flags = 0x00200000 | (0x02000000 if directory else 0x08000000)
    handle = kernel.CreateFileW(str(path), access, sharing, None, 1 if create else 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot retain public export input")
    try:
        info = Info()
        buffer = ctypes.create_unicode_buffer(32768)
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ExportError("Cannot verify retained source identity")
        length = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not 0 < length < len(buffer):
            raise ExportError("Cannot verify retained source path")
        actual = buffer.value
        if actual.startswith("\\\\?\\"):
            actual = actual[4:]
        if info.attributes & 0x400 or os.path.normcase(actual.rstrip("\\")) != os.path.normcase(str(path).rstrip("\\")):
            raise ExportError("Retained source is a reparse or alternate path")
        if bool(info.attributes & 0x10) != directory or (not directory and info.links != 1):
            raise ExportError("Retained source type/link count is invalid")
        yield handle
    finally:
        kernel.CloseHandle(handle)


def _output_identity(path: Path, file, *, links: int, frozen: tuple | None = None) -> tuple:
    """Join the retained file to its current namespace without reopening bytes."""
    opened, named = os.fstat(file.fileno()), _plain(path)
    if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != links or named.st_nlink != links
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
        raise ExportError("Retained export output identity changed")
    identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    if frozen is not None and identity != frozen:
        raise ExportError("Retained export output changed after verification")
    return identity


@contextmanager
def _create_retained_output(path: Path, *, remove_alias_on_success: bool = False):
    """Create once and hold parent/file identities through verification/receipt."""
    _plain_ancestors(path.parent)
    with ExitStack() as stack:
        cleanup = {"normal": False, "identity": None}
        def remove_verified_alias():
            if remove_alias_on_success and cleanup["normal"]:
                named = _plain(path)
                if (named.st_dev, named.st_ino) != cleanup["identity"]:
                    raise ExportError("Completed export alias changed; retained for inspection")
                path.unlink()
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            for parent in reversed(path.parents):
                stack.enter_context(_windows_handle(parent, directory=True))
            # Registered before the file lease: native/CRT file handles close
            # first, then our checked extra alias, then the retained parents.
            stack.callback(remove_verified_alias)
            native = stack.enter_context(_windows_handle(path, directory=False, writable=True, create=True))
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            duplicate = wintypes.HANDLE()
            current = kernel.GetCurrentProcess()
            if not kernel.DuplicateHandle(current, native, current, ctypes.byref(duplicate), 0, False, 2):
                raise ExportError("Cannot retain created export output")
            descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_RDWR | os.O_BINARY)
        else:
            parent_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0))
            stack.callback(os.close, parent_descriptor)
            stack.callback(remove_verified_alias)
            descriptor = os.open(path.name, os.O_CREAT | os.O_EXCL | os.O_RDWR
                | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_descriptor)
        with os.fdopen(descriptor, "w+b") as file:
            cleanup["identity"] = _output_identity(path, file, links=1)[:2]
            yield file
            cleanup["normal"] = True


@contextmanager
def read_plain(path: Path):
    _plain_ancestors(path)
    with ExitStack() as stack:
        if os.name == "nt":
            import msvcrt
            for parent in reversed(path.parents):
                stack.enter_context(_windows_handle(parent, directory=True))
        before = _plain(path)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
            raise ExportError("Selected file is not an eligible regular source input")
        if os.name == "nt":
            # Duplicate the checked native handle before transferring ownership to fdopen.
            import ctypes
            from ctypes import wintypes
            native = stack.enter_context(_windows_handle(path, directory=False))
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            duplicate = wintypes.HANDLE()
            current = kernel.GetCurrentProcess()
            if not kernel.DuplicateHandle(current, native, current, ctypes.byref(duplicate), 0, False, 2):
                raise ExportError("Cannot duplicate retained source handle")
            descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
        else:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
                raise ExportError("Source identity changed while opening")
            data = handle.read(MAX_FILE_BYTES + 1)
            after = _plain(path)
            if len(data) != before.st_size or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                raise ExportError("Source changed while reading")
            _plain_ancestors(path)
            yield data


def inventory(selected: list[tuple[str, Path]]) -> list[dict]:
    rows = []
    for relative, path in selected:
        with read_plain(path) as data:
            risks = secret_findings(data)
            if risks:
                raise ExportError("Secret-risk scan blocked export: " + relative + " [" + ", ".join(risks) + "]")
            rows.append({"path": relative, "bytes": len(data), "sha256": sha256(data)})
    return rows


def export_source(source: Path, output: Path, *, suite: Path | None = None) -> dict:
    source = Path(os.path.abspath(source))
    output = Path(os.path.abspath(output))
    if output == source or source in output.parents or output.suffix.casefold() != ".zip":
        raise ExportError("ZIP destination must be outside the source tree")
    if not output.parent.is_dir():
        raise ExportError("ZIP destination parent must already exist")
    _plain_ancestors(output.parent)
    if output.exists() or output.with_suffix(output.suffix + ".verified.json").exists():
        raise ExportError("Refusing to overwrite an existing export")
    selected = select_files(source, suite)
    missing = REQUIRED_ROOT_FILES - {relative for relative, _ in selected}
    if missing:
        raise ExportError("Required public root files are missing: " + ", ".join(sorted(missing)))
    rows = inventory(selected)
    manifest = {"schema_version": 1, "kind": "noncommercial-client-source-export", "files": rows,
        "file_count": len(rows), "total_source_bytes": sum(row["bytes"] for row in rows),
        "limitations": LIMITATIONS, "publication_performed": False, "product_runtime_tested": False}
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    partial = output.with_suffix(output.suffix + ".incomplete")
    receipt_path = output.with_suffix(output.suffix + ".verified.json")
    receipt_partial = receipt_path.with_suffix(receipt_path.suffix + ".incomplete")
    with _create_retained_output(partial, remove_alias_on_success=True) as file:
        with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for (relative, path), row in zip(selected, rows):
                with read_plain(path) as data:
                    if sha256(data) != row["sha256"] or secret_findings(data):
                        raise ExportError("Source changed after public inventory: " + relative)
                    archive.writestr(relative, data)
            archive.writestr("SOURCE_MANIFEST.json", manifest_bytes)
        file.flush()
        os.fsync(file.fileno())
        frozen = _output_identity(partial, file, links=1)
        file.seek(0)
        with zipfile.ZipFile(file, "r") as archive:
            expected = {row["path"]: row for row in rows}
            if set(archive.namelist()) != set(expected) | {"SOURCE_MANIFEST.json"}:
                raise ExportError("ZIP member inventory mismatch")
            for relative, row in expected.items():
                data = archive.read(relative)
                if len(data) != row["bytes"] or sha256(data) != row["sha256"]:
                    raise ExportError("ZIP source readback mismatch: " + relative)
            if archive.read("SOURCE_MANIFEST.json") != manifest_bytes:
                raise ExportError("ZIP manifest readback mismatch")
        _output_identity(partial, file, links=1, frozen=frozen)
        if inventory(select_files(source, suite)) != rows:
            raise ExportError("Selected source inventory changed before export completion")
        digest = hashlib.sha256()
        file.seek(0)
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
        _output_identity(partial, file, links=1, frozen=frozen)
        # No path reopening between write/readback/hash/commit. On Windows the
        # retained RW handle denies other writers and deletion throughout.
        os.link(partial, output)
        _output_identity(partial, file, links=2, frozen=frozen)
        _output_identity(output, file, links=2, frozen=frozen)
        receipt = {"schema_version": 1, "archive_sha256": digest.hexdigest(),
            "manifest_sha256": sha256(manifest_bytes), "file_count": len(rows),
            "all_members_readback_verified": True, "source_reinventory_verified": True,
            "secret_risk_patterns_passed": True, "publication_performed": False,
            "product_runtime_tested": False, "limitations": LIMITATIONS}
        receipt_bytes = (json.dumps(receipt, indent=2) + "\n").encode("utf-8")
        with _create_retained_output(receipt_partial, remove_alias_on_success=True) as receipt_file:
            receipt_file.write(receipt_bytes)
            receipt_file.flush()
            os.fsync(receipt_file.fileno())
            receipt_frozen = _output_identity(receipt_partial, receipt_file, links=1)
            receipt_file.seek(0)
            if receipt_file.read() != receipt_bytes:
                raise ExportError("Export receipt readback mismatch")
            _output_identity(output, file, links=2, frozen=frozen)
            os.link(receipt_partial, receipt_path)
            _output_identity(receipt_path, receipt_file, links=2, frozen=receipt_frozen)
            _output_identity(output, file, links=2, frozen=frozen)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--aim-suite", type=Path)
    args = parser.parse_args()
    try:
        result = export_source(args.source, args.output, suite=args.aim_suite)
    except (ExportError, OSError, zipfile.BadZipFile) as exc:
        print("Public source export stopped: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
