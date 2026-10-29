"""Explicit disposable dependency installation before native persistence imports.

Installing paths is source-level plumbing, not authority to launch. A reviewed
child launcher must supply its confined environment and exact runtime clearance.
No environment or cached third-party globals are patched here.
"""
from dataclasses import dataclass
import sys

from vlui_runtime_state import DisposableStatePaths


@dataclass(frozen=True, slots=True)
class DisposableNativeInjection:
    paths: DisposableStatePaths
    services: object
    fixture_edition: str


_installed = None


def install_disposable_native_injection(injection):
    global _installed
    if type(injection) is not DisposableNativeInjection or _installed is not None:
        raise ValueError('NATIVE_INJECTION_REJECTED')
    if any(name in sys.modules for name in ('vapor_lock_gui', 'localization', 'local_control_api',
                'dataset_storage')):
        raise ValueError('NATIVE_INJECTION_TOO_LATE')
    if type(injection.paths) is not DisposableStatePaths or injection.fixture_edition not in {'free', 'premium'}:
        raise ValueError('NATIVE_INJECTION_REJECTED')
    injection.paths.verify()
    # The specific DENY factory type is checked by the Qt entrypoint. It must be
    # constructed before ControlCenter, never replace services after construction.
    if injection.services is None:
        raise ValueError('NATIVE_INJECTION_REJECTED')
    _installed = injection


def native_injection():
    return _installed


def require_native_effect(capability):
    if _installed is not None:
        raise PermissionError('DISPOSABLE_NATIVE_EFFECT_DENIED')


def writable_native_path(path):
    return _installed.paths.writable(path) if _installed is not None else path
