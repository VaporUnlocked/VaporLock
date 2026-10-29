"""Private typed command lane and post-callback admission, independent of Qt/core."""
from dataclasses import dataclass
from threading import get_ident
from types import MappingProxyType

from vlui_live_protocol import CommandRejected, command_result, validate_command


_MUTATIONS = frozenset(('setLocale', 'setInterfaceMode', 'setPaletteColor', 'setPaletteOpacity',
                        'resetPalette', 'setProfileName', 'selectProfile', 'openProfile',
                        'saveProfile', 'saveProfileAs', 'resetSettings', 'undoTuning',
                        'setCaptureRate', 'setTextScale', 'setNumericSetting', 'setDiscreteSetting', 'openSettingsGuide',
                        'removeVirtualMouse'))
_FREE_APPEARANCE = frozenset(('setLocale', 'setTextScale', 'setInterfaceMode', 'setPaletteColor',
                             'setPaletteOpacity', 'resetPalette'))


@dataclass(frozen=True, slots=True)
class NativeCommandStamp:
    """Owned controller facts; no serializer/constructor is exposed on the channel."""
    settings_revision: int
    authorization_revision: int
    cancellation_epoch: int
    selection_epoch: int
    profile_target: object
    model_target: object


class _Transaction:
    __slots__ = ()


class NativeCommandLane:
    """One native generation, one mutation; handler may finish after a native await.

Every native handler must call may_effect immediately before a real effect and
after each await/dialog/callback. Native controllers/hotkeys/API share the stamp
owner. This lane alone cannot prove their effect implementation or authorization.
"""
    def __init__(self, *, host_current, native_stamp, native_authorize, native_handlers, emit_result):
        if (not all(callable(callback) for callback in (host_current, native_stamp, native_authorize, emit_result))
                or type(native_handlers) is not dict or set(native_handlers) != _MUTATIONS
                or not all(callable(handler) for handler in native_handlers.values())):
            raise ValueError('COMMAND_OWNER_REJECTED')
        self._thread = get_ident()
        self._current = host_current
        self._stamp = native_stamp
        self._authorize = native_authorize
        self._handlers = MappingProxyType(dict(native_handlers))
        self._emit = emit_result
        self._retired = False
        self._last_request = 0
        self._active = None
        self._record = None

    def _native_thread(self):
        if get_ident() != self._thread:
            raise ValueError('COMMAND_OWNER_REJECTED')

    def _get_stamp(self):
        stamp = self._stamp()
        if type(stamp) is not NativeCommandStamp:
            raise ValueError('COMMAND_OWNER_REJECTED')
        for value in (stamp.settings_revision, stamp.authorization_revision,
                      stamp.cancellation_epoch, stamp.selection_epoch):
            if type(value) is not int or not 1 <= value <= 2**53 - 1:
                raise ValueError('COMMAND_OWNER_REJECTED')
        if any(value is not None and type(value) is not object for value in (stamp.profile_target, stamp.model_target)):
            raise ValueError('COMMAND_OWNER_REJECTED')
        return stamp

    def receive(self, name, wire):
        self._native_thread()
        try:
            command = validate_command(name, wire)
        except CommandRejected:
            return 'INVALID'  # no guessed/untrusted request ID in a result signal
        if command.name == 'ready':
            return 'INVALID'  # ready is the host bootstrap's separate private path
        if self._retired or self._current() is not True:
            self._emit(command_result(command.request_id, 'rejected', 'RETIRED'))
            return 'RETIRED'
        if command.request_id <= self._last_request:
            self._emit(command_result(command.request_id, 'rejected', 'DUPLICATE'))
            return 'DUPLICATE'
        self._last_request = command.request_id
        if self._active is not None:
            self._emit(command_result(command.request_id, 'rejected', 'BUSY'))
            return 'BUSY'
        if self._authorize(command.name) is not True:
            self._emit(command_result(command.request_id, 'rejected', 'DENIED'))
            return 'DENIED'
        transaction = _Transaction()
        try:
            captured = self._get_stamp()
            self._active = transaction
            self._record = (command, captured)
            # Explicit closed mutations (ready is host-only); numeric field IDs
            # resolve only through the fixed product catalog, never reflection.
            self._handlers[command.name](command, transaction, self)
        except BaseException:
            # Retain no live lane if a native programming exception escapes. Do
            # not convert arbitrary exception text into public success/diagnostics.
            self._active = None
            self._record = None
            self._retired = True
            raise
        return 'QUEUED'

    def may_effect(self, transaction):
        self._native_thread()
        if (self._retired or type(transaction) is not _Transaction or transaction is not self._active
                or self._record is None or self._current() is not True):
            return False
        command, captured = self._record
        return self._authorize(command.name) is True and self._get_stamp() == captured

    def finish(self, transaction, *, status='completed', code='OK', effect_committed=False):
        self._native_thread()
        if type(transaction) is not _Transaction or transaction is not self._active or self._record is None:
            return False
        command, _captured = self._record
        current = self.may_effect(transaction)
        if status == 'completed' and not effect_committed:
            status, code = 'failed', 'IO_ERROR'  # do not infer persistence success
        if not current and not effect_committed and status != 'cancelled':
            status, code = 'rejected', 'STALE'
        # Once a guarded effect has actually committed, report it honestly even
        # if display state changed afterward; do not invent rollback. Only the
        # original still-live host may receive the receipt.
        result = command_result(command.request_id, status, code)
        self._active = None
        self._record = None
        if not self._retired and self._current() is True:
            self._emit(result)
        return True

    def user_stop_cancel_transition(self):
        """Called by independent native Stop; internal profile Stop never calls it."""
        self._native_thread()
        if self._active is not None:
            transaction = self._active
            self.finish(transaction, status='cancelled', code='CANCELLED')

    def retire(self):
        self._native_thread()
        self._retired = True
        self._active = None
        self._record = None

    def native_observation(self):
        self._native_thread()
        return MappingProxyType({'retired': self._retired, 'lastRequest': self._last_request,
                                 'mutationPending': self._active is not None})
