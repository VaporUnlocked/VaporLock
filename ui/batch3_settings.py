"""Bounded settings shared by profiles, native launch and local controls."""
import math

FIELDS = {
    'assistance_x': (0.0, 4.0, 1.0),
    'assistance_y': (0.0, 4.0, 1.0),
    'controller_output_deadzone': (0.0, 0.5, 0.0),
    'acquire_confidence': (0.0, 1.0, 0.0),
    'retain_confidence': (0.0, 1.0, 0.0),
}


def validate(settings):
    for key, (low, high, default) in FIELDS.items():
        value = settings.get(key, default)
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError('Invalid assistance or confidence setting.')


def confidence_floors(settings):
    validate(vars(settings))
    retain = max(settings.confidence, settings.retain_confidence)
    return max(retain, settings.acquire_confidence), retain
