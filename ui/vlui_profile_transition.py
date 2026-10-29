"""Private stop-before-replace transaction, with no Qt or client imports.

Native callbacks supply the exact capture/controller session, pending Start,
background-job state and the actual single commit. A stop request is not proof
that a session has stopped. Old settings/history survive every failed admission.
"""
import math
from threading import get_ident


class StopBeforeReplace:
    def __init__(self, *, may_effect, conflict, capture_session, cancel_pending_start,
                 request_stop, safely_stopped, commit, completed, clock):
        if not all(callable(value) for value in (may_effect, conflict, capture_session,
                cancel_pending_start, request_stop, safely_stopped, commit, completed, clock)):
            raise ValueError('TRANSITION_OWNER_REJECTED')
        self._thread = get_ident()
        self._current, self._conflict, self._session = may_effect, conflict, capture_session
        self._cancel_start, self._stop, self._stopped = cancel_pending_start, request_stop, safely_stopped
        self._commit, self._completed, self._clock = commit, completed, clock
        self._state, self._captured, self._deadline, self._last_time = 'NEW', None, None, None

    def _now(self):
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('TRANSITION_CLOCK_REJECTED')
        if self._last_time is not None and value < self._last_time:
            raise ValueError('TRANSITION_CLOCK_REJECTED')
        self._last_time = value
        return value

    def _finish(self, code, committed=False):
        self._state = 'COMMITTED' if committed else 'CANCELLED'
        self._completed(code, committed)
        return self._state

    def begin(self):
        if get_ident() != self._thread or self._state != 'NEW':
            raise ValueError('TRANSITION_OWNER_REJECTED')
        if self._current() is not True:
            return self._finish('STALE')
        if self._conflict() is True:
            return self._finish('BUSY')
        self._captured = self._session()  # exact native session identity, not a JS ID
        if self._current() is not True:
            return self._finish('STALE')
        self._deadline = self._now() + 5.0
        self._state = 'STOPPING'
        self._cancel_start()
        # Internal cancellation fences Start without cancelling this transition.
        if self._current() is not True:
            return self._finish('STALE')
        self._stop(self._captured)
        return self.poll()

    def poll(self):
        if get_ident() != self._thread:
            raise ValueError('TRANSITION_OWNER_REJECTED')
        if self._state != 'STOPPING':
            return self._state
        now = self._now()
        if self._current() is not True:
            return self._finish('STALE')
        if self._conflict() is True:
            return self._finish('BUSY')
        if self._session() is not self._captured:
            return self._finish('STALE')
        if self._stopped(self._captured) is not True:
            return self._finish('IO_ERROR') if now >= self._deadline else self._state
        # Recheck after observing stop, immediately before the single commit.
        if now >= self._deadline or self._current() is not True or self._session() is not self._captured:
            return self._finish('STALE')
        self._state = 'COMMITTING'
        committed = self._commit() is True
        return self._finish('OK' if committed else 'IO_ERROR', committed)

    def cancel(self):
        if get_ident() != self._thread:
            raise ValueError('TRANSITION_OWNER_REJECTED')
        if self._state in {'NEW', 'STOPPING'}:
            return self._finish('CANCELLED')
        return self._state

    def native_state(self):
        return self._state
