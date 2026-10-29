"""Closed, bounded native UI command grammar. No Qt/core/dispatch side effects."""
from dataclasses import dataclass
from decimal import Decimal
import json
import re
from types import MappingProxyType

from vlui_numeric_catalog import parse_numeric_value
from vlui_discrete_catalog import parse_value as parse_discrete_value


MAX_REQUEST_BYTES = 4096
MAX_SEQUENCE = 2**53 - 1
LOCALES = frozenset(('en', 'zh-CN', 'de', 'ru', 'fr'))
SCALES = frozenset(('1', '1.5', '2'))
ROLES = frozenset(('steel', 'cobalt', 'ice', 'baby', 'ultraviolet', 'carbon',
                   'pink', 'highlight', 'border', 'button_text'))
MODES = frozenset(('smoky', 'contrast', 'slate-light', 'slate-light-contrast',
                   'teal-dark', 'teal-dark-contrast', 'violet-dark', 'violet-dark-contrast',
                   'amber-dark', 'amber-dark-contrast', 'rose-dark', 'rose-dark-contrast'))
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
_REQUEST = re.compile(r'[1-9][0-9]{0,15}\Z')
_HEX = re.compile(r'#[0-9a-f]{6}\Z')
_FIELDS = MappingProxyType({
    'ready': ('protocol',), 'setLocale': ('requestId', 'locale'),
    'setInterfaceMode': ('requestId', 'mode'),
    'setPaletteColor': ('requestId', 'role', 'color'),
    'setPaletteOpacity': ('requestId', 'role', 'opacity'),
    'resetPalette': ('requestId',), 'setProfileName': ('requestId', 'name'),
    'selectProfile': ('requestId', 'profileId'), 'openProfile': ('requestId',),
    'saveProfile': ('requestId',), 'saveProfileAs': ('requestId',),
    'resetSettings': ('requestId',), 'undoTuning': ('requestId',),
    'setCaptureRate': ('requestId', 'fps'), 'setTextScale': ('requestId', 'scale'),
    'setNumericSetting': ('requestId', 'fieldId', 'value', 'input'),
    'setDiscreteSetting': ('requestId', 'bindingId', 'value'),
    'openSettingsGuide': ('requestId',),
    'removeVirtualMouse': ('requestId',),
})
COMMANDS = frozenset(_FIELDS)
RESULT_STATUSES = frozenset(('completed', 'cancelled', 'rejected', 'failed'))
RESULT_CODES = frozenset(('OK', 'SAVED_DEFAULT', 'SAVED_AS', 'CANCELLED', 'INVALID',
                          'RETIRED', 'DUPLICATE', 'BUSY', 'DENIED', 'STALE',
                          'IO_ERROR', 'CONTEXT_REJECTED', 'GUIDE_SENT', 'GUIDE_FAILED',
                          'MOUSE_REMOVAL_STARTED', 'MOUSE_REMOVAL_FAILED'))


class CommandRejected(ValueError):
    def __init__(self):
        super().__init__('INVALID')


def _require(condition):
    if not condition:
        raise CommandRejected()


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result)
        result[key] = value
    return result


def _integer(token):
    _require(len(token) <= 17)
    result = int(token)
    _require(abs(result) <= MAX_SEQUENCE)
    return result


def _constant(_token):
    raise CommandRejected()


def _bounded_graph(value, depth=0, *, max_items=16):
    _require(depth <= 4)
    if type(value) is dict:
        _require(len(value) <= 16)
        for key, child in value.items():
            _require(type(key) is str)
            _bounded_graph(child, depth + 1, max_items=max_items)
    elif type(value) is list:
        _require(len(value) <= max_items)
        for child in value:
            _bounded_graph(child, depth + 1, max_items=max_items)


def decode_object_bytes(raw: bytes, limit=MAX_REQUEST_BYTES, *, max_items=16):
    """Exact immutable bytes, strict JSON keys/constants/Unicode, closed caller schema."""
    _require(type(raw) is bytes and 0 < len(raw) <= limit and not raw.startswith(b'\xef\xbb\xbf'))
    try:
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_pairs,
                           parse_int=_integer, parse_float=Decimal, parse_constant=_constant)
        _require(type(value) is dict)
        _bounded_graph(value, max_items=max_items)
        # JSON escaped surrogate values are invalid even when source UTF-8 was valid.
        def well_formed(item):
            if type(item) is str:
                item.encode('utf-8', errors='strict')
            elif type(item) is dict:
                for key, child in item.items():
                    well_formed(key)
                    well_formed(child)
            elif type(item) is list:
                for child in item:
                    well_formed(child)
        well_formed(value)
    except (ValueError, UnicodeError, ArithmeticError, RecursionError, OverflowError):
        raise CommandRejected() from None
    return value


def valid_display_label(value):
    if type(value) is not str or not 1 <= len(value) <= 96:
        return False
    for char in value:
        scalar = ord(char)
        if (scalar <= 31 or 127 <= scalar <= 159 or scalar == 1564 or 8206 <= scalar <= 8207
                or 8232 <= scalar <= 8238 or 8294 <= scalar <= 8297 or 0xD800 <= scalar <= 0xDFFF):
            return False
    return True


def valid_display_id(value):
    return type(value) is str and _UUID.fullmatch(value) is not None


def request_number(value):
    _require(type(value) is str and _REQUEST.fullmatch(value) is not None)
    number = int(value)
    _require(number <= MAX_SEQUENCE)
    return number


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    request_id: int | None
    payload: object


def validate_command(name: str, wire: str) -> Command:
    _require(type(name) is str and name in COMMANDS and type(wire) is str)
    # QString input bounded before UTF-8 conversion; no need to encode an arbitrary-sized value.
    _require(0 < len(wire) <= MAX_REQUEST_BYTES)
    try:
        raw = wire.encode('utf-8', errors='strict')
    except UnicodeError:
        raise CommandRejected() from None
    value = decode_object_bytes(raw)
    _require(set(value) == set(_FIELDS[name]))
    if name == 'ready':
        _require(type(value['protocol']) is int and value['protocol'] == 1)
        return Command(name, None, MappingProxyType(value))
    number = request_number(value['requestId'])
    if name == 'setLocale':
        _require(type(value['locale']) is str and value['locale'] in LOCALES)
    elif name == 'setInterfaceMode':
        _require(type(value['mode']) is str and value['mode'] in MODES)
    elif name in ('setPaletteColor', 'setPaletteOpacity'):
        _require(type(value['role']) is str and value['role'] in ROLES)
        if name == 'setPaletteColor':
            _require(type(value['color']) is str and _HEX.fullmatch(value['color']) is not None)
        else:
            _require(type(value['opacity']) is int and 0 <= value['opacity'] <= 100)
    elif name == 'setProfileName':
        _require(valid_display_label(value['name']))
    elif name == 'selectProfile':
        _require(valid_display_id(value['profileId']))
    elif name == 'setCaptureRate':
        _require(type(value['fps']) is int and 1 <= value['fps'] <= 120)
    elif name == 'setTextScale':
        _require(type(value['scale']) is str and value['scale'] in SCALES)
    elif name == 'setNumericSetting':
        try:
            parse_numeric_value(value['fieldId'], value['value'], value['input'])
        except (ValueError, ArithmeticError):
            raise CommandRejected() from None
    if name == 'setDiscreteSetting':
        try:
            parse_discrete_value(value['bindingId'], value['value'])
        except ValueError:
            raise CommandRejected() from None
    return Command(name, number, MappingProxyType(value))


def command_result(request_id: int, status: str, code: str) -> bytes:
    _require(type(request_id) is int and 1 <= request_id <= MAX_SEQUENCE)
    _require(type(status) is str and status in RESULT_STATUSES)
    _require(type(code) is str and code in RESULT_CODES)
    _require((status == 'completed' and code in {'OK', 'SAVED_DEFAULT', 'SAVED_AS', 'GUIDE_SENT', 'MOUSE_REMOVAL_STARTED'})
             or (status == 'cancelled' and code == 'CANCELLED')
             or (status == 'rejected' and code in {'INVALID', 'RETIRED', 'DUPLICATE', 'BUSY', 'DENIED', 'STALE', 'CONTEXT_REJECTED'})
             or (status == 'failed' and code in {'IO_ERROR', 'CONTEXT_REJECTED', 'GUIDE_FAILED', 'MOUSE_REMOVAL_FAILED'}))
    return json.dumps({'requestId': str(request_id), 'status': status, 'code': code},
                      separators=(',', ':'), ensure_ascii=True).encode('ascii')
