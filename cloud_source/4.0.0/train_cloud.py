"""Run the released trainer with the current S2 architecture explicitly selected.

Usage from the handoff root:
    python train_cloud.py PROJECT --output NEW_RUN --config config/s2_player.json --from-scratch
    python train_cloud.py PROJECT --output EXISTING_RUN --resume EXISTING_RUN/last.cloudtrain

This small launcher changes no training implementation. Resume and Improve
still use the release's compatibility checks. A legacy checkpoint or parent
cannot be converted into S2 by changing its architecture label.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "training_source"))


def training_arguments(argv: list[str]) -> list[str]:
    from cloudstudio.config import MASK_ARCHITECTURE

    selector = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    selector.add_argument("--architecture")
    selected, _ = selector.parse_known_args(argv)
    if selected.architecture not in (None, MASK_ARCHITECTURE):
        selector.error(f"This handoff trains S2 only: --architecture {MASK_ARCHITECTURE}.")
    # Explicitly supplying S2 also prevents Improve from silently inheriting
    # an S1 parent's architecture. Do not force a preset: Resume restores the
    # original experiment's settings unless the caller explicitly changes them.
    return ["train", *argv, "--architecture", MASK_ARCHITECTURE]


def main(argv: list[str] | None = None) -> int:
    from cloudstudio.cli import main as released_main

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        arguments = ["--help"]
    return released_main(training_arguments(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
