"""Dependency-only packaged startup check; never constructs product controls."""
import ctypes
import json
from pathlib import Path
import sys

from PySide6.QtCore import Qt, qVersion, QLibraryInfo
from PySide6 import QtWebEngineCore, QtWebEngineWidgets, QtWebChannel
from vlui_resource_root import native_manifest_identity
from vlui_fixed_resources import load_fixed_resources
from vlui_context import _types
from vlui_schema import load_bundled_contract
from vlui_component_contract import load_component_contract
from PySide6.QtWidgets import QApplication, QLabel


def verify_qt_startup(application: QApplication, destination: Path, edition: str, version: str) -> None:
    from product_build import CLOUD_ROLE, CLOUD_STARTUP_SHA256
    resources = load_fixed_resources(reviewed_manifest_sha256=native_manifest_identity())
    contract_status = {"contextTypes": len(_types()),
                       "baseComponents": len(load_bundled_contract().components),
                       "components": len(load_component_contract().schema.components)}
    probe = QLabel("Vapor Lock")
    probe.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    probe.resize(320, 120)
    probe.show()
    application.processEvents()
    rendered = not probe.grab().isNull()
    probe.close()
    modules = {}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint
    for name in (
        "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "qwindows.dll", "icuuc.dll",
        "Qt6WebEngineCore.dll", "Qt6WebEngineWidgets.dll", "Qt6WebChannel.dll", "Qt6Network.dll",
        "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    ):
        handle = kernel.GetModuleHandleW(name)
        filename = ctypes.create_unicode_buffer(32768)
        if not handle or not kernel.GetModuleFileNameW(handle, filename, len(filename)):
            raise RuntimeError(f"Required runtime was not loaded: {name}")
        modules[name] = filename.value
    destination.write_text(json.dumps({
        "qt_version": qVersion(), "platform": application.platformName(), "rendered": rendered,
        "edition": edition, "version": version, "product_controls_created": False,
        "bundle_root": str(getattr(sys, "_MEIPASS", "")), "modules": modules,
        "resource_manifest_sha256": native_manifest_identity(), "browser_resources": len(resources),
        "native_contracts": contract_status, "renderer_committed": False,
        "cloud_role": CLOUD_ROLE, "cloud_startup_sha256": CLOUD_STARTUP_SHA256,
    }, indent=2) + "\n", encoding="utf-8")
