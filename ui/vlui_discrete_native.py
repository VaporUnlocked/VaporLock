"""Inactive candidate: fixed semantic values and sealed native widget ownership.

This file is outside the application import paths. It grants no runtime access.
The completed immutable catalog, exact construction hooks and permission matrix
must be reviewed before adoption.
"""
from dataclasses import dataclass
from types import MappingProxyType
import weakref

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QWidget
from shiboken6 import isValid


DEDICATED_FIELDS = frozenset(('virtual_mouse_opt_in', 'controller_output_opt_in',
                             'output_transport', 'virtual_controller_mode'))


@dataclass(frozen=True, slots=True)
class Binding:
    id: str
    field: str
    kind: str
    value_type: type
    choices: tuple
    option_values: tuple
    premium: bool

    def parse(self, value):
        if self.kind == 'boolean':
            if type(value) is not bool:
                raise ValueError('DISCRETE_VALUE_REJECTED')
        elif (self.kind != 'choice' or type(value) is not self.value_type
                or not any(type(option) is type(value) and option == value for option in self.choices)):
            raise ValueError('DISCRETE_VALUE_REJECTED')
        return value


class NativeDiscreteRegistry:
    def __init__(self, center, fixed_bindings):
        # Only a code-owned fixed catalog may be supplied at construction.
        self._bindings = MappingProxyType(dict(fixed_bindings))
        if not 0 < len(self._bindings) <= 128:
            raise ValueError('DISCRETE_CATALOG_REJECTED')
        for key, spec in self._bindings.items():
            if (type(spec) is not Binding or key != spec.id or spec.kind not in {'boolean', 'choice'}
                    or type(spec.premium) is not bool or not spec.field.isidentifier()
                    or spec.field.startswith(('local_api', 'hotkey_'))
                    or spec.field in DEDICATED_FIELDS
                    or not key.startswith('discrete.') or len(key) > 96):
                raise ValueError('DISCRETE_CATALOG_REJECTED')
            if spec.kind == 'choice' and (spec.value_type not in (int, str)
                    or not 1 <= len(spec.choices) <= 32 or spec.option_values != spec.choices
                    or any(type(v) is not spec.value_type or type(v) is str and not 1 <= len(v) <= 32 for v in spec.choices)
                    or len(set(spec.choices)) != len(spec.choices)):
                raise ValueError('DISCRETE_CATALOG_REJECTED')
        self._center = weakref.ref(center)
        self._root = None
        self._identity = object()
        self._epoch = 0
        self._counts = dict.fromkeys(self._bindings, 0)
        self._ambiguous = set()
        self._records = {}
        self._retained_ancestry = {}

    def _owner(self):
        center = self._center()
        if (center is None or not isValid(center) or center.thread() != QThread.currentThread()
                or getattr(center, '_discrete_registry', None) is not self):
            raise ValueError('DISCRETE_OWNER_REJECTED')
        return center

    def _options(self, control, spec):
        model = control.model()
        if (not isValid(model) or model.parent() is not control
                or control.isEditable() or control.count() != len(spec.option_values)):
            raise ValueError('DISCRETE_OPTIONS_REJECTED')
        records = []
        for index, value in enumerate(spec.option_values):
            data = control.itemData(index)
            actual = index if data is None and spec.value_type is int else data
            if type(actual) is not type(value) or actual != value:
                raise ValueError('DISCRETE_OPTIONS_REJECTED')
            flags = model.flags(model.index(index, control.modelColumn(), control.rootModelIndex()))
            selectable = bool(flags & Qt.ItemFlag.ItemIsEnabled and flags & Qt.ItemFlag.ItemIsSelectable)
            records.append((value, selectable))
        return model, tuple(records)

    def register(self, binding_id, field, control):
        control.setProperty('guideField', field)
        self._owner()
        spec = self._bindings[binding_id]
        expected_type = QCheckBox if spec.kind == 'boolean' else QComboBox
        if (self._root is not None or field != spec.field or type(control) is not expected_type
                or not isValid(control) or self._counts[binding_id] >= 8):
            raise ValueError('DISCRETE_REGISTRATION_REJECTED')
        self._counts[binding_id] += 1
        self._epoch += 1
        if self._counts[binding_id] != 1:
            self._ambiguous.add(binding_id)
            return
        if spec.kind == 'boolean':
            if control.isTristate():
                raise ValueError('DISCRETE_REGISTRATION_REJECTED')
            model = None
        else:
            model, _options = self._options(control, spec)
        self._records[binding_id] = (weakref.ref(control), object(), weakref.ref(model) if model is not None else None, None)

    def seal(self, root):
        center = self._owner()
        if self._root is not None or root is not center.stack or not isValid(root):
            raise ValueError('DISCRETE_REGISTRATION_REJECTED')
        self._root = weakref.ref(root)
        for key, (reference, token, model, _path) in tuple(self._records.items()):
            current, path = reference(), []
            for _ in range(64):
                if not isinstance(current, QWidget) or not isValid(current):
                    raise ValueError('DISCRETE_REGISTRATION_REJECTED')
                self._retained_ancestry[id(current)] = current
                path.append(weakref.ref(current))
                if current is root:
                    self._records[key] = (reference, token, model, tuple(path))
                    break
                current = current.parentWidget()
            else:
                raise ValueError('DISCRETE_REGISTRATION_REJECTED')

    def target(self, key):
        center = self._owner()
        if key in self._ambiguous or self._counts[key] != 1 or key not in self._records:
            return None
        reference, _token, model, path = self._records[key]
        control = reference()
        root = self._root() if self._root is not None else None
        if control is None or root is None or root is not center.stack or path is None:
            return None
        current = control
        for reference in path:
            if reference() is not current or not isinstance(current, QWidget) or not isValid(current):
                return None
            if current is root: break
            current = current.parentWidget()
        current = root
        for _ in range(64):
            if current is center: break
            if not isinstance(current, QWidget) or not isValid(current): return None
            current = current.parentWidget()
        else:
            return None
        if model is not None and (model() is None or not isValid(model()) or model() is not control.model()):
            return None
        return control

    def availability(self, key, *, free, denied, value=None):
        center = self._owner()
        spec = self._bindings[key]
        if denied or center._native_closing: return 'denied'
        control = self.target(key)
        if spec.field == 'cuda_preprocess':
            status = getattr(center, '_cuda_preprocess_status', None)
            if status in ('other-provider', 'checking', 'unavailable', 'unverified'):
                return 'cuda-' + status
            if status != 'available': return 'unavailable'
        if control is None or not control.isEnabled(): return 'unavailable'
        if spec.kind == 'choice':
            try:
                _model, options = self._options(control, spec)
            except ValueError:
                return 'unavailable'
            if not any(enabled for _value, enabled in options): return 'unavailable'
            if value is not None and not any(type(v) is type(value) and v == value and enabled for v, enabled in options):
                return 'unavailable'
        elif control.isTristate():
            return 'unavailable'
        return 'available'

    def signature(self):
        self._owner()
        states = []
        for key, spec in self._bindings.items():
            control = self.target(key)
            if control is None:
                states.append((key, None))
                continue
            options = None
            if spec.kind == 'choice':
                try: _model, options = self._options(control, spec)
                except ValueError: options = 'invalid'
            states.append((key, self._records[key][1], control.isEnabled(), options,
                           control.isTristate() if spec.kind == 'boolean' else control.isEditable(),
                           getattr(self._owner(), '_cuda_preprocess_status', None) if spec.field == 'cuda_preprocess' else None))
        return self._identity, self._epoch, frozenset(self._ambiguous), tuple(states)

    def projection(self, settings, *, free, denied):
        records = []
        for key, spec in self._bindings.items():
            try:
                value = spec.parse(settings[spec.field])
                reason = self.availability(key, free=free, denied=denied)
            except ValueError:
                value, reason = None, 'invalid'
            options = [] if spec.kind == 'boolean' else [False] * len(spec.choices)
            if reason == 'available' and spec.kind == 'choice':
                _model, current_options = self._options(self.target(key), spec)
                options = [enabled for _value, enabled in current_options]
            records.append({'id': key, 'value': value, 'enabled': reason == 'available',
                            'reason': reason, 'optionEnabled': options})
        return records

    def apply(self, key, value, *, free, denied, guard):
        center = self._owner()
        spec = self._bindings[key]
        value = spec.parse(value)
        if self.availability(key, free=free, denied=denied, value=value) != 'available':
            return 'DENIED'
        control, signature = self.target(key), self.signature()
        if control is None or guard() is not True:
            return 'STALE'
        if (signature != self.signature() or self.target(key) is not control
                or self.availability(key, free=free, denied=denied, value=value) != 'available'):
            return 'STALE'
        center.change(spec.field, value)
        actual = spec.parse(getattr(center.settings, spec.field))
        if type(actual) is not type(value) or actual != value:
            raise ValueError('DISCRETE_READBACK_REJECTED')
        # Synchronize every explicitly cataloged mirror, preserving original
        # signal-block state. Each mirror keeps its own sealed binding identity.
        for mirror_key, mirror_spec in self._bindings.items():
            if mirror_spec.field != spec.field: continue
            mirror = self.target(mirror_key)
            if mirror is None: continue
            blocked = mirror.blockSignals(True)
            try:
                if mirror_spec.kind == 'boolean':
                    mirror.setChecked(actual if mirror_spec.field != 'cuda_preprocess' or mirror.isEnabled() else False)
                else: mirror.setCurrentIndex(mirror_spec.option_values.index(actual))
            finally:
                mirror.blockSignals(blocked)
        return 'OK'


def fixed_bindings():
    from vlui_discrete_fields import BINDING_ROWS
    types = {'str': str, 'int': int, 'bool': bool}
    return {key: Binding(key, field, kind, types[value_type], choices, choices, premium)
        for key, field, kind, value_type, choices, premium in BINDING_ROWS}
