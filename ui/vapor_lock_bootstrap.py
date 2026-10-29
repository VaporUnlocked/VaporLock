"""Windows DLL bootstrap for the frozen Vapor Lock control center."""

from __future__ import annotations

import os
from pathlib import Path
import sys


_DLL_DIRECTORY_HANDLES = []

if getattr(sys, "frozen", False):
    product_root = Path(sys.executable).resolve().parent
    internal_root = Path(getattr(sys, "_MEIPASS", product_root / "_internal"))
    for directory in (internal_root / "PySide6", internal_root / "shiboken6", internal_root):
        if directory.is_dir():
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
            os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"

from vapor_lock_gui import main


if __name__ == "__main__":
    main()
