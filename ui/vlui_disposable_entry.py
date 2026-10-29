"""Source entry for the later exact disposable Qt recipe. No executable main.

The accepted direction does not authorize calling this factory. A completed
source review and separately scoped native launcher must precede its invocation.
The supervising launcher owns the Python/Qt subprocess tree and termination proof.
"""
import os

from vlui_fixed_resources import load_fixed_resources
from vlui_native_injection import DisposableNativeInjection, install_disposable_native_injection
from vlui_runtime_state import DisposableStatePaths


def construct_disposable_client(*, paths, fixture_edition, reviewed_manifest_sha256):
    if type(paths) is not DisposableStatePaths or fixture_edition not in {'free', 'premium'}:
        raise ValueError('NATIVE_RECIPE_REJECTED')
    paths.verify()
    expected = {'LOCALAPPDATA': paths.directory('child-localappdata'), 'APPDATA': paths.directory('child-appdata'),
                'TEMP': paths.directory('temp'), 'TMP': paths.directory('temp')}
    if any(os.environ.get(key) != str(value) for key, value in expected.items()):
        raise ValueError('NATIVE_RECIPE_ENVIRONMENT_REJECTED')
    paths.child_environment(dict(os.environ))  # verifies forbidden Qt/plugin/debug flags without installing them
    resources = load_fixed_resources(reviewed_manifest_sha256=reviewed_manifest_sha256)
    from vlui_denied_services import DeniedNativeServices
    install_disposable_native_injection(DisposableNativeInjection(paths, DeniedNativeServices(), fixture_edition))
    from vlui_qt_host import register_fixed_scheme
    register_fixed_scheme()
    from PySide6.QtWidgets import QApplication
    from vapor_lock_gui import ControlCenter
    application = QApplication([])
    center = ControlCenter(live_resources=resources)
    return application, center
