"""Native disposable state paths. No Qt, client import, service, or environment mutation.

This module supplies paths before persistence construction. It is not a browser
filesystem capability and does not authorize a runtime launch by itself.
"""
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from uuid import UUID


_WORKSPACE = Path(__file__).resolve().parents[1]
_RUNS = _WORKSPACE / 'build' / 'verification' / 'ui-v2'
_REPARSE = 0x400
_DIRECTORIES = ('product', 'profiles', 'models', 'preferences', 'cache', 'qt',
                'temp', 'child-localappdata', 'child-appdata')


class StatePathRejected(ValueError):
    """Fixed public category; private path details never leave a native observer."""


def _reject_links(path: Path) -> None:
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & _REPARSE:
            raise StatePathRejected('STATE_PATH_REJECTED')


def _beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class DisposableStatePaths:
    """Privately constructed native run roots; every persistence API receives one."""
    root: Path
    owner: UUID

    @classmethod
    def create(cls, owner: UUID):
        if type(owner) is not UUID or owner.version != 4:
            raise StatePathRejected('STATE_PATH_REJECTED')
        _reject_links(_RUNS)
        run_root = _RUNS / f'p014-b-runtime-{owner}'
        if not _beneath(run_root, _WORKSPACE) or run_root.exists():
            raise StatePathRejected('STATE_PATH_REJECTED')
        _RUNS.mkdir(parents=True, exist_ok=True)
        _reject_links(_RUNS)
        run_root.mkdir(exist_ok=False)
        instance = cls(run_root, owner)
        for name in _DIRECTORIES:
            (run_root / name).mkdir(exist_ok=False)
        instance.verify()
        return instance

    def verify(self) -> None:
        if type(self.owner) is not UUID or self.owner.version != 4:
            raise StatePathRejected('STATE_PATH_REJECTED')
        expected = _RUNS / f'p014-b-runtime-{self.owner}'
        if self.root != expected or not self.root.is_absolute():
            raise StatePathRejected('STATE_PATH_REJECTED')
        _reject_links(self.root)
        if self.root.resolve(strict=True) != expected or not self.root.is_dir():
            raise StatePathRejected('STATE_PATH_REJECTED')
        for name in _DIRECTORIES:
            directory = self.root / name
            _reject_links(directory)
            if not directory.is_dir() or directory.resolve(strict=True) != directory:
                raise StatePathRejected('STATE_PATH_REJECTED')

    def directory(self, name: str) -> Path:
        if type(name) is not str or name not in _DIRECTORIES:
            raise StatePathRejected('STATE_PATH_REJECTED')
        self.verify()
        return self.root / name

    def writable(self, path: Path) -> Path:
        """Check immediately before each operation; do not authorize based on label."""
        self.verify()
        candidate = Path(path)
        if not candidate.is_absolute() or '..' in candidate.parts:
            raise StatePathRejected('STATE_PATH_REJECTED')
        _reject_links(candidate)
        resolved = candidate.resolve(strict=False)
        if not _beneath(resolved, self.root) or resolved == self.root:
            raise StatePathRejected('STATE_PATH_REJECTED')
        if not any(_beneath(resolved, self.root / name) for name in _DIRECTORIES):
            raise StatePathRejected('STATE_PATH_REJECTED')
        return resolved

    @property
    def appearance_file(self) -> Path:
        return self.writable(self.directory('preferences') / 'ui-appearance.json')

    @property
    def interface_preferences_file(self) -> Path:
        return self.writable(self.directory('preferences') / 'interface-preferences.json')

    @property
    def initialization_file(self) -> Path:
        return self.writable(self.directory('preferences') / 'initialization.json')

    def child_environment(self, reviewed_base: dict[str, str]):
        """Return detached environment for a later reviewed launcher; never install it."""
        self.verify()
        if type(reviewed_base) is not dict or any(type(k) is not str or type(v) is not str
                                                  for k, v in reviewed_base.items()):
            raise StatePathRejected('STATE_PATH_REJECTED')
        forbidden = {'QTWEBENGINE_REMOTE_DEBUGGING', 'QTWEBENGINE_DISABLE_SANDBOX',
                     'QTWEBENGINEPROCESS_PATH', 'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH',
                     'QML_IMPORT_PATH'}
        for key, value in reviewed_base.items():
            upper = key.upper()
            if upper in forbidden or upper == 'QTWEBENGINE_CHROMIUM_FLAGS' and value:
                raise StatePathRejected('STATE_PATH_REJECTED')
        result = dict(reviewed_base)
        for key in tuple(result):
            if key.upper() in {'LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP', 'QTWEBENGINE_CHROMIUM_FLAGS'}:
                del result[key]
        result.update(LOCALAPPDATA=str(self.directory('child-localappdata')),
                      APPDATA=str(self.directory('child-appdata')),
                      TEMP=str(self.directory('temp')), TMP=str(self.directory('temp')),
                      QTWEBENGINE_CHROMIUM_FLAGS='')
        return MappingProxyType(result)

    def native_receipt(self):
        """Native-only path evidence; never pass to a QWebChannel property/signal."""
        self.verify()
        return MappingProxyType({'owner': str(self.owner), 'root': str(self.root),
                                 'directories': MappingProxyType({name: str(self.root / name)
                                                                 for name in _DIRECTORIES}),
                                 'environmentInstalled': False, 'runtimeLaunched': False})
