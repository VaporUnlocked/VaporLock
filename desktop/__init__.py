"""VaporLock desktop application and its local service adapters."""

from pathlib import Path
import sys


# Source workers are also launched as standalone scripts. Keep their existing
# module names available to the desktop without importing the old interface.
_workers = Path(__file__).resolve().parents[1] / 'ui'
if _workers.is_dir() and str(_workers) not in sys.path:
    sys.path.insert(0, str(_workers))
