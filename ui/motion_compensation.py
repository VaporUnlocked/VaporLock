"""Named motion-compensation presets stored in a small user-editable text file."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
_MAX_RATE = 1_000_000.0


def valid_motion_identifier(identifier: str) -> bool:
    return isinstance(identifier, str) and _IDENTIFIER.fullmatch(identifier) is not None


@dataclass(frozen=True)
class MotionCompensationPreset:
    identifier: str
    vertical: float
    horizontal: float


def parse_motion_compensation(text: str) -> tuple[list[MotionCompensationPreset], list[str]]:
    """Parse ``identifier | vertical | horizontal`` records without guessing."""
    presets: list[MotionCompensationPreset] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3:
            errors.append(f"Line {line_number}: expected identifier | vertical | horizontal.")
            continue
        identifier, vertical_text, horizontal_text = parts
        key = identifier.casefold()
        if not valid_motion_identifier(identifier):
            errors.append(f"Line {line_number}: identifier must use letters, numbers, spaces, dots, dashes, or underscores.")
            continue
        if key in seen:
            errors.append(f"Line {line_number}: duplicate identifier '{identifier}'.")
            continue
        try:
            vertical = float(vertical_text)
            horizontal = float(horizontal_text)
        except ValueError:
            errors.append(f"Line {line_number}: vertical and horizontal values must be numbers.")
            continue
        if not all(math.isfinite(value) and abs(value) <= _MAX_RATE for value in (vertical, horizontal)):
            errors.append(f"Line {line_number}: values must be finite and between -{_MAX_RATE:g} and {_MAX_RATE:g}.")
            continue
        seen.add(key)
        presets.append(MotionCompensationPreset(identifier, vertical, horizontal))
    presets.sort(key=lambda preset: preset.identifier.casefold())
    return presets, errors


def load_motion_compensation(path: Path) -> tuple[list[MotionCompensationPreset], list[str]]:
    try:
        return parse_motion_compensation(path.read_text(encoding="utf-8"))
    except OSError as error:
        return [], [f"Could not read {path.name}: {error}"]


def _number_text(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def render_motion_compensation(presets: list[MotionCompensationPreset]) -> str:
    lines = [
        "# Vapor Lock motion compensation library",
        "# identifier | vertical pixels per second | horizontal pixels per second",
        "# Positive values describe observed image drift. Vapor Lock sends the opposite correction.",
        "# Add presets here or use Targeting > Compensation in Control Center.",
        "",
    ]
    lines.extend(
        f"{preset.identifier} | {_number_text(preset.vertical)} | {_number_text(preset.horizontal)}"
        for preset in sorted(presets, key=lambda item: item.identifier.casefold())
    )
    return "\n".join(lines) + "\n"


def save_motion_compensation(path: Path, presets: list[MotionCompensationPreset]) -> None:
    parsed, errors = parse_motion_compensation(render_motion_compensation(presets))
    if errors or len(parsed) != len(presets):
        raise ValueError("Motion compensation library contains invalid or duplicate presets.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(render_motion_compensation(parsed), encoding="utf-8")
    temporary.replace(path)


def find_motion_preset(presets: list[MotionCompensationPreset], identifier: str) -> MotionCompensationPreset | None:
    wanted = identifier.casefold()
    return next((preset for preset in presets if preset.identifier.casefold() == wanted), None)
