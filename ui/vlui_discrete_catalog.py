"""Inactive closed discrete grammar; no Qt imports or command dispatch."""
from types import MappingProxyType

from vlui_discrete_fields import BINDING_ROWS

FIELDS = MappingProxyType({row[0]: row for row in BINDING_ROWS})
CUDA_REASONS = frozenset(('cuda-other-provider', 'cuda-checking', 'cuda-unavailable', 'cuda-unverified'))
REASONS = frozenset(('available', 'unavailable', 'premium', 'denied', 'invalid')) | CUDA_REASONS


def parse_value(binding_id, value):
    if type(binding_id) is not str or binding_id not in FIELDS:
        raise ValueError('DISCRETE_VALUE_REJECTED')
    _id, _field, kind, value_type, choices, _premium = FIELDS[binding_id]
    if kind == 'boolean':
        if type(value) is not bool:
            raise ValueError('DISCRETE_VALUE_REJECTED')
    else:
        expected = str if value_type == 'str' else int
        if type(value) is not expected or value not in choices:
            raise ValueError('DISCRETE_VALUE_REJECTED')
    return value


def validate_projection(records):
    if type(records) is not list or len(records) != len(FIELDS):
        raise ValueError('DISCRETE_PROJECTION_REJECTED')
    for binding_id, record in zip(FIELDS, records):
        if (type(record) is not dict or set(record) != {'id', 'value', 'enabled', 'reason', 'optionEnabled'}
                or record['id'] != binding_id or type(record['enabled']) is not bool
                or type(record['reason']) is not str or record['reason'] not in REASONS
                or record['enabled'] != (record['reason'] == 'available')):
            raise ValueError('DISCRETE_PROJECTION_REJECTED')
        options = record['optionEnabled']
        if record['reason'] in CUDA_REASONS and FIELDS[binding_id][1] != 'cuda_preprocess':
            raise ValueError('DISCRETE_PROJECTION_REJECTED')
        expected = 0 if FIELDS[binding_id][2] == 'boolean' else len(FIELDS[binding_id][4])
        if type(options) is not list or len(options) != expected or any(type(flag) is not bool for flag in options):
            raise ValueError('DISCRETE_PROJECTION_REJECTED')
        if not record['enabled'] and any(options):
            raise ValueError('DISCRETE_PROJECTION_REJECTED')
        if record['reason'] == 'invalid':
            if record['value'] is not None:
                raise ValueError('DISCRETE_PROJECTION_REJECTED')
        else:
            parse_value(binding_id, record['value'])
    return records
