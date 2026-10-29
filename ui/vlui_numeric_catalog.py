"""Closed numeric domains shared by the typed command and native display lane."""
from dataclasses import dataclass
from decimal import Decimal
import math
import re
from types import MappingProxyType

from vlui_numeric_fields import FIELD_ROWS

_DECIMAL = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,18})?\Z')
INPUTS = frozenset(('slider', 'expert'))
REASONS = frozenset(('available', 'premium', 'unavailable', 'invalid', 'denied'))


@dataclass(frozen=True, slots=True)
class NumericField:
    field: str
    kind: str
    minimum: int | float
    maximum: int | float
    slider_minimum: int | float
    slider_maximum: int | float
    decimals: int
    premium: bool


FIELDS = MappingProxyType({'numeric.' + row[0]: NumericField(*row) for row in FIELD_ROWS})
assert len(FIELDS) == 87 and len(FIELD_ROWS) == 87


def _decimal(value):
    if type(value) is not str or not 1 <= len(value) <= 32 or _DECIMAL.fullmatch(value) is None:
        raise ValueError('INVALID_NUMERIC_SETTING')
    return Decimal(value)


def parse_numeric_value(field_id, value, input_kind):
    """Input kind chooses an admitted value domain; it grants no authority."""
    if type(field_id) is not str or field_id not in FIELDS or type(input_kind) is not str or input_kind not in INPUTS:
        raise ValueError('INVALID_NUMERIC_SETTING')
    spec = FIELDS[field_id]
    if spec.field == 'capture_fps':
        raise ValueError('DEDICATED_CAPTURE_COMMAND_REQUIRED')
    number = _decimal(value)
    precision = max(6, spec.decimals) if spec.kind == 'float' and input_kind == 'expert' else spec.decimals
    if -number.as_tuple().exponent > precision:
        raise ValueError('INVALID_NUMERIC_SETTING')
    low, high = (spec.slider_minimum, spec.slider_maximum) if input_kind == 'slider' else (spec.minimum, spec.maximum)
    # A native clamp/validator is still authoritative; both domains must meet it.
    if spec.field != 'training_seed':
        low, high = max(low, spec.minimum), min(high, spec.maximum)
    if not Decimal(str(low)) <= number <= Decimal(str(high)):
        raise ValueError('INVALID_NUMERIC_SETTING')
    return int(number) if spec.kind == 'int' else float(number)


def valid_numeric_display(field_id, value):
    if type(field_id) is not str or field_id not in FIELDS:
        return False
    try:
        number = _decimal(value)
    except ValueError:
        return False
    spec = FIELDS[field_id]
    if spec.kind == 'int' and number != number.to_integral_value():
        return False
    low, high = spec.minimum, spec.maximum
    if spec.field == 'training_seed':
        low, high = min(low, spec.slider_minimum), max(high, spec.slider_maximum)
    return Decimal(str(low)) <= number <= Decimal(str(high))


def native_numeric_value(field_id, value):
    spec = FIELDS[field_id]
    if (spec.kind == 'int' and type(value) is not int
            or spec.kind == 'float' and type(value) not in (int, float)
            or type(value) is bool or not math.isfinite(value)):
        return None
    text = format(Decimal(str(value)), 'f')
    return text if valid_numeric_display(field_id, text) else None


def validate_numeric_projection(records):
    if type(records) is not list or len(records) != len(FIELDS):
        raise ValueError('NUMERIC_PROJECTION_REJECTED')
    for record, field_id in zip(records, FIELDS):
        if (type(record) is not dict or set(record) != {'id', 'value', 'enabled', 'reason'}
                or record['id'] != field_id or type(record['enabled']) is not bool
                or type(record['reason']) is not str or record['reason'] not in REASONS
                or record['value'] is not None and not valid_numeric_display(field_id, record['value'])
                or record['enabled'] != (record['reason'] == 'available')
                or record['enabled'] and record['value'] is None
                or record['value'] is None and record['reason'] != 'invalid'):
            raise ValueError('NUMERIC_PROJECTION_REJECTED')
    return records
