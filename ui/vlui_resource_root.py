"""Code-owned resource locations and native build identity; no input selectors."""
from pathlib import Path
import sys




def bundled_data_root():
    if getattr(sys, 'frozen', False):
        directory = getattr(sys, '_MEIPASS', None)
        if type(directory) is not str or not Path(directory).is_absolute():
            raise ValueError('BUNDLED_RESOURCE_ROOT_REJECTED')
        return Path(directory)
    return Path(__file__).resolve().parents[1]


def browser_resource_root():
    if (bundled_data_root().parent / 'information/alpha-manifest.json').is_file():
        return bundled_data_root()
    return bundled_data_root()


def browser_resource_manifest():
    if (bundled_data_root().parent / 'information/alpha-manifest.json').is_file():
        return bundled_data_root() / 'vlui/runtime/resources.json'
    return browser_resource_root() / ('vlui/runtime/resources.json' if getattr(sys, 'frozen', False)
                                      else 'ui_system/runtime/resources.json')


def native_manifest_identity():
    if getattr(sys, 'frozen', False) or (bundled_data_root().parent / 'information/alpha-manifest.json').is_file():
        from product_build import VLUI_RESOURCE_MANIFEST_SHA256
        value = VLUI_RESOURCE_MANIFEST_SHA256
    else:
        import hashlib
        value = hashlib.sha256(browser_resource_manifest().read_bytes()).hexdigest()
    if type(value) is not str or len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
        raise ValueError('BUNDLED_RESOURCE_IDENTITY_REJECTED')
    return value
