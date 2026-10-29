"""Private-byte validated display preferences, separate from unchanged R3 context."""
import json
import re
from types import MappingProxyType
from vlui_numeric_catalog import validate_numeric_projection
from vlui_discrete_catalog import validate_projection as validate_discrete_projection

from vlui_live_protocol import (CommandRejected, LOCALES, MAX_SEQUENCE, MODES, ROLES,
                                SCALES, decode_object_bytes, valid_display_id,
                                valid_display_label)


# The full bounded projection includes 128 names of up to 96 Unicode scalars
# and 78 numeric records. This display cap never changes the 4 KiB command cap.
MAX_PREFERENCES_BYTES = 96 * 1024
GROUPS = frozenset(('local-training', 'training-status', 'model-inspection',
                    'provider-benchmark', 'official-models', 'data-collection', 'data-review'))
_ROOT_KEYS = frozenset(('preferenceVersion', 'rendererGeneration', 'sequence', 'locale',
                        'profile', 'profileName', 'catalog', 'appearance', 'textScale', 'captureRate',
                        'nativeStatus', 'coverage', 'numeric', 'discrete'))


class PreferencesRejected(ValueError):
    def __init__(self):
        super().__init__('PREFERENCES_REJECTED')


def require(condition):
    if not condition:
        raise PreferencesRejected()


def closed(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def positive(value):
    return type(value) is int and 1 <= value <= MAX_SEQUENCE


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


def validate_preferences_bytes(raw: bytes):
    try:
        value = decode_object_bytes(raw, MAX_PREFERENCES_BYTES, max_items=128)
    except CommandRejected:
        raise PreferencesRejected() from None
    require(type(value) is dict and set(value) in (_ROOT_KEYS, _ROOT_KEYS | {'pipeline'}))
    if 'pipeline' in value:
        closed(value['pipeline'], ('capture','inference','writes','age','inference_ms'))
        require(all(v is None or (type(v) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,9})\.[0-9]', v)
                                  and float(v) <= 1_000_000_000)
                    for v in value['pipeline'].values()))
    require(type(value['preferenceVersion']) is int and value['preferenceVersion'] == 3)
    require(positive(value['rendererGeneration']) and positive(value['sequence']))
    require(type(value['locale']) is str and value['locale'] in LOCALES)
    require(type(value['textScale']) is str and value['textScale'] in SCALES)
    require(positive(value['captureRate']))
    require(valid_display_label(value['profileName']))
    profile = value['profile']
    closed(profile, ('state', 'displayId', 'name', 'dirty', 'undoAvailable'))
    require(type(profile['state']) is str and profile['state'] in {'unknown', 'none', 'selected'})
    require(type(profile['dirty']) is bool and type(profile['undoAvailable']) is bool)
    if profile['state'] == 'selected':
        require(valid_display_id(profile['displayId']) and valid_display_label(profile['name']))
    else:
        require(profile['displayId'] is None and profile['name'] is None)
    catalog = value['catalog']
    require(type(catalog) is list and len(catalog) <= 128)
    seen = set()
    for item in catalog:
        closed(item, ('displayId', 'name'))
        require(valid_display_id(item['displayId']) and valid_display_label(item['name']))
        require(item['displayId'] not in seen)
        seen.add(item['displayId'])
    if profile['state'] == 'selected':
        require(profile['displayId'] in seen)
        selected = next(item for item in catalog if item['displayId'] == profile['displayId'])
        require(selected['name'] == profile['name'])
    appearance = value['appearance']
    closed(appearance, ('mode', 'colors', 'opacities'))
    require(type(appearance['mode']) is str and appearance['mode'] in MODES)
    closed(appearance['colors'], ROLES)
    closed(appearance['opacities'], ROLES)
    for role in ROLES:
        color = appearance['colors'][role]
        opacity = appearance['opacities'][role]
        require(type(color) is str and len(color) == 7 and color[0] == '#'
                and all(char in '0123456789abcdef' for char in color[1:]))
        require(type(opacity) is int and 0 <= opacity <= 100)
    status = value['nativeStatus']
    closed(status, ('state', 'startPending'))
    require(type(status['state']) is str and status['state'] in
            {'unknown', 'idle', 'starting', 'running', 'stopping', 'failed'})
    require(type(status['startPending']) is bool)
    closed(value['coverage'], GROUPS)
    require(all(type(state) is str and state in {'partial', 'unconnected'}
                for state in value['coverage'].values()))
    try:
        validate_numeric_projection(value['numeric'])
        validate_discrete_projection(value['discrete'])
    except ValueError:
        raise PreferencesRejected() from None
    return _freeze(value)


def encode_preferences(native_object: dict) -> bytes:
    """Native builder validates complete bytes before publication; no partial DTO."""
    try:
        raw = json.dumps(native_object, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8', errors='strict')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise PreferencesRejected() from None
    validate_preferences_bytes(raw)
    return raw
