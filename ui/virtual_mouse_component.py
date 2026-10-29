"""Fixed bundled Interception actions; identities stay held through UAC launch."""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes as W
from pathlib import Path

from dataset_transaction import HeldFile

INSTALLER_SHA256 = 'e137863a79da797f08e7a137280ff2a123809044a888fd75ce9c973198915abe'


def launch_installation(installer, *, guard):
    return _launch(installer, '/install', guard=guard)


def launch_removal(installer, *, guard):
    return _launch(installer, '/uninstall', guard=guard)


def _launch(installer, argument, *, guard):
    if argument not in ('/install', '/uninstall'):
        raise ValueError('Unsupported virtual mouse component action.')
    installer = Path(installer).absolute()
    result = 0
    try:
        with ExitStack() as held:
            for parent in reversed(installer.parents):
                held.enter_context(HeldFile(parent, directory=True))
            executable = held.enter_context(HeldFile(installer))
            if executable.snapshot()['sha256'] != INSTALLER_SHA256:
                raise ValueError('The bundled virtual mouse component tool failed integrity verification.')
            if guard() is not True:
                return 0
            shell = ctypes.WinDLL('shell32', use_last_error=True)
            shell.ShellExecuteW.argtypes = [W.HWND, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, ctypes.c_int]
            shell.ShellExecuteW.restype = W.HINSTANCE
            # Fixed argument, with no shell interpolation or renderer-provided path.
            result = shell.ShellExecuteW(None, 'runas', str(installer), argument, str(installer.parent), 1) or 0
    except OSError:
        if result <= 32:
            raise
        # A failed CloseHandle is retained by HeldFile. Report the accepted
        # launch honestly even when releasing that protection reports an error.
    return result
