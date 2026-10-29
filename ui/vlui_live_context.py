"""Private live context owner for the first connected native Settings slice.

The only input is a bound native source callback. This module imports no Qt/client,
performs no native operation, and accepts no renderer-produced facts or epochs.
Unconnected operation/source/job groups remain explicitly unknown.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from types import MappingProxyType
from threading import get_ident
from uuid import uuid4

from vlui_context import validate_context_bytes
from vlui_live_preferences import encode_preferences
from vlui_live_protocol import MAX_SEQUENCE, valid_display_label
from vlui_c014_settings import validated_capture_settings
from vlui_numeric_catalog import FIELDS as NUMERIC_FIELDS, native_numeric_value, validate_numeric_projection
from vlui_discrete_catalog import FIELDS as DISCRETE_FIELDS, parse_value as parse_discrete_value, validate_projection as validate_discrete_projection


_INPUT = ('model_output_format', 'model_coordinate_units', 'resize_mode')
_CAPTURE = ('monitor', 'region_x', 'region_y', 'region_width', 'region_height',
            'capture_fps', 'inference_fps', 'frame_skip')
_PROVIDERS = frozenset(('auto', 'dml', 'cuda', 'trt', 'cpu'))
_ANNOTATION_PROVIDERS = frozenset(('auto', 'directml', 'cuda', 'tensorrt-rtx', 'cpu'))
_REGISTRY_LIMIT = 4096


class ProducerRejected(ValueError):
    def __init__(self, code='CONTEXT_REJECTED'):
        super().__init__(code)


def require(condition, code='CONTEXT_REJECTED'):
    if not condition:
        raise ProducerRejected(code)


def canonical(value, depth=0):
    require(depth <= 8)
    kind = type(value)
    if kind is bool or value is None:
        return (kind.__name__, value)
    if kind is int:
        require(abs(value) <= MAX_SEQUENCE)
        return ('int', value)
    if kind is float:
        require(math.isfinite(value))
        return ('float', value)
    if kind is str:
        require(len(value) <= 32768)
        try:
            value.encode('utf-8', errors='strict')
        except UnicodeError:
            raise ProducerRejected() from None
        return ('str', value)
    if kind is list:
        require(len(value) <= 128)
        return ('list', tuple(canonical(child, depth + 1) for child in value))
    if kind is dict:
        require(len(value) <= 256 and all(type(key) is str for key in value))
        return ('dict', tuple((key, canonical(value[key], depth + 1)) for key in sorted(value)))
    raise ProducerRejected()


def ref(state='unknown', display_id=None):
    return {'state': state, 'displayId': display_id}


def validated_native_settings(settings, field_specs):
    """Detached native data preflight shared by reader and private producer.

    This validates data only. It mints no identity, receipt or effect permission.
    """
    require(type(settings) is dict and set(settings) == {name for name, _kind in field_specs})
    settings = deepcopy(settings)
    for name, kind in field_specs:
        value = settings[name]
        expected = {'str': str, 'bool': bool, 'int': int, 'float': float,
                    'list[dict]': list, 'list[int]': list, 'dict': dict}[kind]
        if kind == 'float':
            require(type(value) in (float, int))
            require(type(value) is not int or abs(value) <= MAX_SEQUENCE)
            require(math.isfinite(value))
            settings[name] = float(value)
        else:
            require(type(value) is expected)
        if kind == 'list[int]': require(all(type(v) is int for v in value))
        if kind == 'list[dict]': require(all(type(v) is dict for v in value))
    # The caller supplies the exact code-owned dataclass fields. Validate each
    # value without applying the generic 256-member mapping cap to that schema.
    require(len(field_specs) <= 288)
    for value in settings.values():canonical(value)
    from cloud_settings import validate as validate_cloud
    validate_cloud(settings)
    from batch6_settings import validate
    validate(settings)
    from batch7_settings import validate as validate7
    validate7(settings)
    from crosshair_reference import validate as validate_crosshair
    validate_crosshair(settings)
    require(type(settings['provider']) is str and settings['provider'] in _PROVIDERS)
    require(type(settings['dataset_annotation_provider']) is str
            and settings['dataset_annotation_provider'] in _ANNOTATION_PROVIDERS)
    require(type(settings['capture_fps']) is int and 1 <= settings['capture_fps'] <= MAX_SEQUENCE)
    for name in ('dataset_annotation_nms_iou','ultralytics_annotation_nms_iou'):
        require(.01 <= settings[name] <= .99)
    from controller_thresholds import validate_thresholds
    validate_thresholds(settings.get('controller_trigger_press', 30), settings.get('controller_trigger_release', 30))
    from batch3_settings import validate as validate_batch3
    validate_batch3(settings)
    from exclusion_zones import validate_zones
    validate_zones(settings.get('exclusion_zones', []))
    validated_capture_settings(settings)
    from collection_limits import validate_limits
    validate_limits(settings)
    from model_classes import validate_aliases
    validate_aliases(settings.get('model_class_aliases', []))
    return settings


def unknown_configuration():
    return {'model': ref(), 'configuration': ref(), 'inputSettings': ref(),
            'captureSettings': ref(), 'preferredProvider': 'unknown',
            'runtime': {'revision': ref(), 'state': 'unknown'}}


def unknown_job(training=True):
    if training:
        return {'job': ref(), 'operation': 'unknown', 'run': ref(), 'baseModel': ref(),
                'dataset': ref(), 'configuration': ref(), 'requestedDevice': ref(),
                'state': 'unknown', 'outcome': 'unknown'}
    return {'job': ref(), 'operation': 'unknown', 'dataset': ref(), 'annotationModel': ref(),
            'configuration': ref(), 'state': 'unknown', 'outcome': 'unknown'}


def unknown_groups(current):
    inspection = {'operation': 'inspect', 'scope': 'model-structure', 'operationId': ref(),
                  'captured': unknown_configuration(), 'state': 'unknown', 'outcome': 'unknown',
                  'freshness': 'unknown', 'staleReason': 'unknown', 'actualProvider': 'unknown'}
    validation = {**deepcopy(inspection), 'operation': 'validate', 'scope': 'model-runtime'}
    return {
        'local-training': {'prospective': {'run': ref(), 'baseModel': ref(), 'dataset': ref(),
            'configuration': ref(), 'requestedDevice': ref(), 'runtimeState': 'unknown',
            'preflight': {'scope': 'dataset-check', 'requestId': ref(), 'configuration': ref(),
                'baseModel': ref(), 'dataset': ref(), 'state': 'unknown', 'outcome': 'unknown',
                'freshness': 'unknown', 'staleReason': 'unknown'}}, 'activeJob': unknown_job()},
        'training-status': {'selected': {'attribution': 'unknown', **unknown_job()}},
        'model-inspection': {'current': deepcopy(current), 'inspectionResult': inspection,
                              'validationResult': validation},
        'provider-benchmark': {'current': deepcopy(current), 'captured': unknown_configuration(),
            'benchmark': ref(), 'comparedProviders': [], 'state': 'unknown', 'completedCount': None,
            'successfulCount': None, 'outcome': 'unknown', 'freshness': 'unknown', 'staleReason': 'unknown'},
        'official-models': {'current': deepcopy(current), 'selectedTrainingBaseModel': ref()},
        'data-collection': {'captureSource': ref(), 'captureAvailability': 'unknown', 'dataset': ref(),
            'annotationModel': ref(), 'prospectiveConfiguration': ref(), 'activeJob': unknown_job(False)},
        'data-review': {'dataset': ref(), 'image': ref(), 'selectionRevision': 1,
            'selectedBox': {**ref(), 'ordinal': None}, 'activeClass': ref(), 'decision': 'unknown',
            'unsavedState': 'unknown', 'zoomPercent': None},
    }


@dataclass(frozen=True, slots=True)
class NativeSource:
    """Produced by the bound native adapter; never a public deserialization type."""
    source_revision: int
    display_revision: int
    operation_epoch: int
    selection_epoch: int
    settings: dict
    model_state: str
    model_key: object
    model_label: str | None
    preferences: dict
    input_display: dict
    capture_display: dict
    configuration_labels: dict


class _Candidate:
    __slots__ = ('__weakref__',)


class _Receipt:
    __slots__ = ('__weakref__',)


class LiveContextProducer:
    """Native callbacks capture private generation token; serialized IDs do not.

No signal/action publication is performed here: integrated host publishes the
two bytes only after native host admission. All registry mutations are committed
with the complete validated snapshot, not while preparing a speculative candidate.
"""
    def __init__(self, *, read_native_source, field_specs: tuple[tuple[str, str], ...]):
        require(callable(read_native_source))
        require(type(field_specs) is tuple and len(field_specs) == 251)
        require(all(type(item) is tuple and len(item) == 2 and type(item[0]) is str
                    and item[1] in {'str', 'bool', 'float', 'int', 'list[dict]', 'list[int]', 'dict'} for item in field_specs))
        require(len({name for name, _kind in field_specs}) == len(field_specs))
        require(('dataset_capture_confidence_enabled', 'bool') in field_specs)
        require(('controller_trigger_press', 'int') in field_specs)
        require(('controller_trigger_release', 'int') in field_specs)
        require(('dataset_annotation_provider', 'str') in field_specs)
        require(('dataset_annotation_delete_unannotated', 'bool') in field_specs)
        require(('dataset_annotation_nms_iou', 'float') in field_specs)
        require(('ultralytics_annotation_nms_iou', 'float') in field_specs)
        require(set(_INPUT + _CAPTURE + ('model_path', 'profile_name', 'provider')).issubset(name for name, _kind in field_specs))
        self._source = read_native_source
        self._owner_thread = get_ident()
        self._fields = field_specs
        self._registry = {}
        self._generation = 0
        self._ticket = None
        self._retired = False
        self._sequence = 0
        self._pending = {}
        self._last = None
        self._source_stamp = None
        self._receipt = None

    def replace_renderer(self):
        require(get_ident() == self._owner_thread)
        require(not self._retired and self._generation < MAX_SEQUENCE, 'RETIRED')
        self._generation += 1
        self._ticket = object()
        self._sequence = 0
        self._pending.clear()
        self._last = None
        self._source_stamp = None
        self._receipt = None
        # Projection identity is per renderer generation; native entity keys and
        # actual Settings/operation ownership remain in the bound controller.
        self._registry.clear()
        return self._ticket, self._generation

    def current(self, ticket):
        return (get_ident() == self._owner_thread and not self._retired
                and ticket is self._ticket and self._ticket is not None)

    def _read(self):
        source = self._source()
        require(type(source) is NativeSource)
        for value in (source.source_revision, source.display_revision, source.operation_epoch, source.selection_epoch):
            require(type(value) is int and 1 <= value <= MAX_SEQUENCE)
        # Freeze by detached canonical capture before any asynchronous work.
        settings = validated_native_settings(source.settings, self._fields)
        require(type(source.model_state) is str and source.model_state in {'unknown', 'none', 'known'})
        if source.model_state == 'known':
            # The native catalog registers an opaque identity object; a path/UUID/
            # display ID decoded from renderer or profile JSON is not that object.
            require(type(source.model_key) is object and valid_display_label(source.model_label))
        else:
            require(source.model_key is None and source.model_label is None)
        require(type(source.preferences) is dict and type(source.input_display) is dict
                and type(source.capture_display) is dict and type(source.configuration_labels) is dict)
        require(set(source.configuration_labels) == {'configuration', 'input', 'capture'}
                and all(valid_display_label(value) for value in source.configuration_labels.values()))
        try:
            hash(source.model_key)
        except TypeError:
            raise ProducerRejected() from None
        prefs = deepcopy(source.preferences)
        try:
            validate_discrete_projection(prefs['discrete'])
            for record in prefs['discrete']:
                try:
                    actual = parse_discrete_value(record['id'], settings[DISCRETE_FIELDS[record['id']][1]])
                except ValueError:
                    require(record['value'] is None and record['reason'] == 'invalid')
                else:
                    require(type(record['value']) is type(actual) and record['value'] == actual)
            validate_numeric_projection(prefs['numeric'])
            require(all(record['value'] == native_numeric_value(record['id'], settings[NUMERIC_FIELDS[record['id']].field])
                        for record in prefs['numeric']))
        except (KeyError, ValueError):
            raise ProducerRejected() from None
        require('preferenceVersion' not in prefs and 'rendererGeneration' not in prefs and 'sequence' not in prefs)
        stamp = (source.source_revision, source.display_revision, source.operation_epoch, source.selection_epoch,
                 canonical(settings), source.model_state, source.model_key, source.model_label,
                 canonical(prefs), canonical(source.input_display), canonical(source.capture_display),
                 canonical(source.configuration_labels))
        return source, settings, prefs, stamp

    def prepare(self, ticket):
        require(self.current(ticket), 'RETIRED')
        require(not self._pending and self._sequence < MAX_SEQUENCE, 'BUSY')
        source, settings, prefs, stamp = self._read()
        if self._source_stamp is not None:
            prior = self._source_stamp
            require(stamp[0] >= prior[0] and stamp[1] >= prior[1], 'STALE')
            require(stamp[2] >= prior[2] and stamp[3] >= prior[3], 'STALE')
            require(stamp[0:2] != prior[0:2] or stamp == prior, 'STALE')
            previous_settings = dict(prior[4][1])
            current_settings = dict(stamp[4][1])
            previous_settings.pop('profile_name', None)
            current_settings.pop('profile_name', None)
            semantic_changed = (previous_settings != current_settings or stamp[5:7] != prior[5:7])
            require(not semantic_changed or stamp[2] > prior[2], 'STALE')
            require(stamp[5:7] == prior[5:7] or stamp[3] > prior[3], 'STALE')
        registry = dict(self._registry)
        def identity(role, native_key):
            key = (role, native_key)
            if key not in registry:
                require(len(registry) < _REGISTRY_LIMIT)
                registry[key] = str(uuid4())
            return registry[key]
        model_id = identity('model', source.model_key) if source.model_state == 'known' else None
        input_key = tuple((name, canonical(settings[name])) for name in _INPUT)
        capture_key = tuple((name, canonical(settings[name])) for name in _CAPTURE)
        complete_key = (source.model_state, source.model_key,
                        tuple((name, canonical(settings[name])) for name, _kind in self._fields if name != 'profile_name'))
        input_id = identity('configuration', ('input', input_key))
        capture_id = identity('configuration', ('capture', capture_key))
        configuration_id = identity('configuration', ('complete', complete_key))
        current = {'model': ref(source.model_state, model_id), 'configuration': ref('known', configuration_id),
                   'inputSettings': ref('known', input_id), 'captureSettings': ref('known', capture_id),
                   'preferredProvider': 'tensorrt' if settings['provider'] == 'trt' else settings['provider'],
                   'runtime': {'revision': ref(), 'state': 'unknown'}}
        labels = [{'displayId': configuration_id, 'role': 'configuration', 'label': source.configuration_labels['configuration']},
                  {'displayId': input_id, 'role': 'configuration', 'label': source.configuration_labels['input']},
                  {'displayId': capture_id, 'role': 'configuration', 'label': source.configuration_labels['capture']}]
        if model_id is not None:
            labels.append({'displayId': model_id, 'role': 'model', 'label': source.model_label})
        sequence = self._sequence + 1
        document = {'contextVersion': 1, 'rendererGeneration': self._generation, 'sequence': sequence,
                    'mode': 'live', 'groups': unknown_groups(current),
                    'displayProjection': {'locale': prefs['locale'], 'entityLabels': labels,
                        'configurationSummaries': [{'configurationId': configuration_id,
                            'inputSettings': deepcopy(source.input_display),
                            'captureSettings': deepcopy(source.capture_display)}]}}
        raw = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        snapshot = validate_context_bytes(raw)
        preferences = encode_preferences({'preferenceVersion': 3, 'rendererGeneration': self._generation,
                                          'sequence': sequence, **prefs})
        require(self.current(ticket), 'RETIRED')
        candidate = _Candidate()
        self._pending[candidate] = (ticket, stamp, registry, raw, preferences, snapshot)
        return candidate

    def commit(self, ticket, candidate):
        require(self.current(ticket), 'RETIRED')
        require(type(candidate) is _Candidate, 'STALE')
        record = self._pending.pop(candidate, None)
        require(record is not None and record[0] is ticket, 'STALE')
        _source, _settings, _prefs, current_stamp = self._read()
        require(self.current(ticket) and current_stamp == record[1], 'STALE')
        self._registry = record[2]
        self._sequence += 1
        self._source_stamp = record[1]
        self._last = (record[3], record[4], record[5])
        self._receipt = _Receipt()
        return self._receipt

    def verify_host_commit(self, receipt, generation, sequence):
        return (get_ident() == self._owner_thread and not self._retired
                and type(receipt) is _Receipt and receipt is self._receipt
                and generation == self._generation and sequence == self._sequence and self._last is not None)

    def committed_bytes(self, ticket, receipt):
        require(self.current(ticket) and receipt is self._receipt and self._last is not None, 'STALE')
        return self._last[0], self._last[1]

    def native_observation(self):
        require(get_ident() == self._owner_thread)
        return MappingProxyType({'generation': self._generation, 'sequence': self._sequence,
                                 'registrySize': len(self._registry), 'pending': len(self._pending),
                                 'retired': self._retired, 'actualOperationsConnected': False})

    def retire(self):
        require(get_ident() == self._owner_thread)
        self._retired = True
        self._pending.clear()
        self._receipt = None
