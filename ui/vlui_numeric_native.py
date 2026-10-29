"""Private numeric-control registration on the owning GUI thread."""
import weakref

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDoubleSpinBox, QWidget
from shiboken6 import isValid

from vlui_numeric_catalog import FIELDS, native_numeric_value, parse_numeric_value


class NativeNumericRegistry:
    def __init__(self, center, control_type):
        self._center = weakref.ref(center)
        self._control_type = control_type
        self._controls = {field_id: [] for field_id in FIELDS}
        self._epoch = 0
        self._identity = object()
        self._root = None
        self._paths = {}
        self._retained_ancestry = {}
        self._tokens = {}
        self._registration_count = {field_id: 0 for field_id in FIELDS}
        self._ambiguous = set()

    def _owner(self):
        center = self._center()
        if (center is None or not isValid(center) or center.thread() != QThread.currentThread()
                or getattr(center, '_numeric_registry', None) is not self):
            raise ValueError('NUMERIC_OWNER_REJECTED')
        return center

    def register(self, field, control, low, high, decimals):
        self._owner()
        field_id = 'numeric.' + field
        if field_id not in FIELDS:
            return
        spec = FIELDS[field_id]
        if (field_id == 'numeric.dataset_min_confidence' or self._root is not None
                or type(control) is not self._control_type or not isValid(control)
                or (low, high, decimals) != (spec.slider_minimum, spec.slider_maximum, spec.decimals)):
            raise ValueError('NUMERIC_REGISTRATION_REJECTED')
        controls = [record for record in self._controls[field_id] if record[0]() is not None and isValid(record[0]())]
        if self._registration_count[field_id] >= 8:
            raise ValueError('NUMERIC_REGISTRATION_REJECTED')
        self._registration_count[field_id] += 1
        if self._registration_count[field_id] > 1:
            # Ambiguity is permanent for this registry generation. Removing a
            # duplicate must never substitute another widget for the mapped row.
            self._ambiguous.add(field_id)
        self._epoch += 1
        reference, token = weakref.ref(control), object()
        controls.append((reference, token, self._epoch))
        self._tokens[id(control)] = (reference, token)
        self._controls[field_id] = controls

    @staticmethod
    def _capture_percent_shape(control):
        return (type(control) is QDoubleSpinBox and isValid(control)
                and control.minimum() == 1.0 and control.maximum() == 100.0
                and control.decimals() == 4 and control.singleStep() == 1.0
                and control.suffix() == ' %' and not control.isReadOnly())

    def register_capture_percent(self, control):
        self._owner()
        field_id = 'numeric.dataset_min_confidence'
        if (self._root is not None or not self._capture_percent_shape(control)
                or self._registration_count[field_id] >= 8):
            raise ValueError('NUMERIC_REGISTRATION_REJECTED')
        self._registration_count[field_id] += 1
        if self._registration_count[field_id] > 1:
            self._ambiguous.add(field_id)
        self._epoch += 1
        reference, token = weakref.ref(control), object()
        self._controls[field_id].append((reference, token, self._epoch))
        self._tokens[id(control)] = (reference, token)

    def seal(self, root):
        center = self._owner()
        if self._root is not None or root is not center.stack or not isValid(root):
            raise ValueError('NUMERIC_REGISTRATION_REJECTED')
        self._root = weakref.ref(root)
        for records in self._controls.values():
            for reference, token, _epoch in records:
                current = reference(); path = []
                for _ in range(64):
                    if not isinstance(current, QWidget) or not isValid(current):
                        raise ValueError('NUMERIC_REGISTRATION_REJECTED')
                    self._retained_ancestry[id(current)] = current
                    path.append(weakref.ref(current))
                    if current is root:
                        self._paths[token] = tuple(path)
                        break
                    current = current.parentWidget()
                else:
                    raise ValueError('NUMERIC_REGISTRATION_REJECTED')

    def _owned(self, control):
        center = self._owner()
        percent = any(reference() is control for reference, _token, _epoch
                      in self._controls['numeric.dataset_min_confidence'])
        if (not isValid(control) or (not self._capture_percent_shape(control) if percent
                                     else type(control) is not self._control_type)):
            return False
        root = self._root() if self._root is not None else None
        if root is None or not isValid(root) or root is not center.stack:
            return False
        registration = self._tokens.get(id(control))
        token = registration[1] if registration is not None and registration[0]() is control else None
        path = self._paths.get(token)
        if path is None:
            return False
        current = control
        for reference in path:
            if reference() is not current or not isinstance(current, QWidget) or not isValid(current):
                return False
            if current is root:
                break
            current = current.parentWidget()
        current = root
        for _ in range(64):
            if current is center:
                return True
            if not isinstance(current, QWidget):
                return False
            current = current.parentWidget()
        return False

    def controls(self, field_id):
        self._owner()
        return tuple(control for reference, _token, _epoch in self._controls[field_id]
                     if (control := reference()) is not None and self._owned(control))

    def signature(self):
        self._owner()
        return (self._identity, self._epoch, frozenset(self._ambiguous), tuple((field_id, tuple((token, epoch, control.isEnabled())
            for reference, token, epoch in self._controls[field_id]
            if (control := reference()) is not None and self._owned(control))) for field_id in FIELDS))

    def target(self, field_id):
        self._owner()
        records = self._controls[field_id]
        if field_id in self._ambiguous or self._registration_count[field_id] != 1 or len(records) != 1:
            return None
        control = records[0][0]()
        return control if control is not None and self._owned(control) else None

    def reason(self, field_id, *, free, denied):
        if denied or self._owner()._native_closing:
            return 'denied'
        target = self.target(field_id)
        return 'available' if target is not None and target.isEnabled() else 'unavailable'

    def projection(self, settings, *, free, denied):
        records = []
        for field_id, spec in FIELDS.items():
            value = native_numeric_value(field_id, settings[spec.field])
            reason = 'invalid' if value is None else self.reason(field_id, free=free, denied=denied)
            records.append({'id': field_id, 'value': value, 'enabled': reason == 'available', 'reason': reason})
        return records

    def apply(self, field_id, value, input_kind, *, free, denied, guard):
        admitted = parse_numeric_value(field_id, value, input_kind)
        return self._apply_admitted(field_id, admitted, free=free, denied=denied, guard=guard)

    def apply_capture_rate(self, fps, *, free, denied, guard):
        # Dedicated product capability, not an alias through the generic parser.
        if type(fps) is not int or not 1 <= fps <= 120:
            raise ValueError('INVALID_CAPTURE_RATE')
        return self._apply_admitted('numeric.capture_fps', fps, free=free, denied=denied, guard=guard)

    def _apply_admitted(self, field_id, admitted, *, free, denied, guard):
        center = self._owner()
        if self.reason(field_id, free=free, denied=denied) != 'available':
            return 'DENIED'
        target = self.target(field_id)
        registration = self.signature()
        if target is None or guard() is not True:
            return 'STALE'
        # No await or event-loop pumping between this final owned-widget check
        # and the existing callback. Neither display ID nor input-kind authorizes it.
        if (registration != self.signature()
                or self.reason(field_id, free=free, denied=denied) != 'available'
                or target is not self.target(field_id) or not target.isEnabled()):
            return 'STALE'
        spec = FIELDS[field_id]
        center.change(spec.field, admitted)
        actual = getattr(center.settings, spec.field)  # fixed product field only
        if actual != admitted or native_numeric_value(field_id, actual) is None:
            raise ValueError('NUMERIC_NATIVE_READBACK_REJECTED')
        if field_id == 'numeric.dataset_min_confidence':
            blocked = target.blockSignals(True)
            try: target.setValue(actual * 100.0)
            finally: target.blockSignals(blocked)
        else:
            target.set_value(actual)
        return 'OK'
