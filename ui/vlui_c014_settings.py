"""C014 native setting domains; no Qt, paths, effects or capture authority."""
import math

CAPTURE_FIELDS = frozenset(('dataset_enabled', 'dataset_capture_confidence_enabled',
    'dataset_capture_requires_activation', 'dataset_min_confidence',
    'dataset_interval_ms', 'dataset_jpeg_quality'))
_BOOLS = frozenset(('dataset_enabled', 'dataset_capture_confidence_enabled',
                   'dataset_capture_requires_activation'))
_INTEGERS = {'dataset_interval_ms': (100, 1000000), 'dataset_jpeg_quality': (30, 100)}


def validated_capture_field(field, value):
    if field in _BOOLS:
        valid = type(value) is bool
    elif field in _INTEGERS:
        low, high = _INTEGERS[field]
        valid = type(value) is int and low <= value <= high
    elif field == 'dataset_min_confidence':
        valid = type(value) in (int, float) and 0.01 <= value <= 1.0 and math.isfinite(value)
        if valid: value = float(value)
    else:
        raise ValueError('UNKNOWN_CAPTURE_FIELD')
    if not valid:
        raise ValueError('INVALID_CAPTURE_SETTING')
    return value


def validated_capture_settings(settings):
    if type(settings) is not dict or not CAPTURE_FIELDS <= settings.keys():
        raise ValueError('INCOMPLETE_CAPTURE_SETTINGS')
    return {field: validated_capture_field(field, settings[field]) for field in sorted(CAPTURE_FIELDS)}
