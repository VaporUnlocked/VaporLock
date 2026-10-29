"""Native host admission state, independent of Qt and renderer-supplied authority."""
from dataclasses import dataclass
import hashlib
import re
from threading import RLock
from types import MappingProxyType


ORIGIN = 'vlapp://client'
DOCUMENT = ORIGIN + '/ui/live.html'
_URL = re.compile(r'vlapp://client/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_.-]+\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_MIMES = {'MainFrame': 'text/html', 'Script': 'text/javascript',
          'Stylesheet': 'text/css', 'Xhr': 'application/json',
          'Image': ('image/png', 'image/svg+xml')}
_LOAD_STATES = frozenset(('DOCUMENT_LOADING', 'CHANNEL_BOOTSTRAP', 'ADMITTED'))


class AdmissionRejected(ValueError):
    def __init__(self):
        super().__init__('HOST_REJECTED')


def _require(condition):
    if not condition:
        raise AdmissionRejected()


def canonical_resource_url(value):
    if type(value) is not str or not _URL.fullmatch(value):
        return False
    path = value[len(ORIGIN) + 1:]
    return all(part not in {'.', '..'} for part in path.split('/'))


@dataclass(frozen=True, slots=True)
class Resource:
    url: str
    mime: str
    request_type: str
    data: bytes
    sha256: str

    def __post_init__(self):
        _require(canonical_resource_url(self.url))
        _require(type(self.request_type) is str and self.request_type in _MIMES)
        _require(type(self.mime) is str and self.mime in
                 ((_MIMES[self.request_type],) if type(_MIMES[self.request_type]) is str else _MIMES[self.request_type]))
        _require(type(self.data) is bytes and 0 < len(self.data) <= 3145728)
        _require(type(self.sha256) is str and _HASH.fullmatch(self.sha256) is not None)
        _require(hashlib.sha256(self.data).hexdigest() == self.sha256)
        _require((self.url == DOCUMENT) == (self.request_type == 'MainFrame'))


class HostAdmission:
    """One permanent native generation; Qt callbacks capture the private ticket.

Initial navigation/interceptor/handler callbacks may arrive in different orders.
Each first-document route is consumed once under the same native load intent.
No serialized owner/generation/URL supplied in a channel request establishes it.
"""
    __slots__ = ('_lock', '_resources', '_state', '_generation', '_ticket',
                 '_seen_initial', '_ready', '_sequence', '_reason', '_commit_verifier')

    def __init__(self, generation: int, resources: tuple[Resource, ...], *, native_commit_verifier=None):
        _require(type(generation) is int and 1 <= generation <= 2**53 - 1)
        _require(type(resources) is tuple and 1 <= len(resources) <= 64)
        _require(all(type(resource) is Resource for resource in resources))
        _require(len({r.url for r in resources}) == len(resources))
        _require(sum(len(r.data) for r in resources) <= 16 * 1024 * 1024)
        _require(sum(r.url == DOCUMENT for r in resources) == 1)
        _require(native_commit_verifier is None or callable(native_commit_verifier))
        self._resources = MappingProxyType({resource.url: resource for resource in resources})
        self._lock = RLock()
        self._state = 'NEW'
        self._generation = generation
        self._ticket = object()
        self._seen_initial = set()
        self._ready = False
        self._sequence = 0
        self._reason = None
        self._commit_verifier = native_commit_verifier

    def begin_native_load(self):
        with self._lock:
            _require(self._state == 'NEW')
            self._state = 'LOAD_PENDING'
            return self._ticket

    def current(self, private_ticket):
        """Use before dereferencing Qt objects and again after every callback."""
        with self._lock:
            return private_ticket is self._ticket and self._state not in {'RETIRED', 'DISPOSING', 'CLOSED'}

    def _initial_route(self, route, ticket, url):
        if (ticket is not self._ticket or self._state not in {'LOAD_PENDING', 'DOCUMENT_LOADING'}
                or url != DOCUMENT or route in self._seen_initial):
            return False
        self._seen_initial.add(route)
        # This transition is native and thread-safe, before subresources need loading.
        # It invokes no Qt method/controller. All three initial routes share the ticket.
        self._state = 'DOCUMENT_LOADING'
        return True

    def navigation(self, ticket, url, *, main_frame, native_initial):
        with self._lock:
            if type(url) is str and type(main_frame) is bool and type(native_initial) is bool and main_frame and native_initial:
                if self._initial_route('navigation', ticket, url):
                    return True
            if ticket is self._ticket:
                self._retire('NAVIGATION_REJECTED')
            return False

    def request(self, ticket, url, method, request_type, first_party, initiator, *, download=False):
        with self._lock:
            if not self.current(ticket) or type(download) is not bool or download:
                return False
            if type(request_type) is not str or type(first_party) is not str or type(initiator) is not str:
                return False
            if type(method) is not bytes or method != b'GET' or not canonical_resource_url(url):
                return False
            resource = self._resources.get(url)
            if resource is None or resource.request_type != request_type:
                return False
            if resource.request_type == 'MainFrame':
                if first_party not in {'', DOCUMENT} or initiator not in {'', ORIGIN, ORIGIN + '/'}:
                    return False
                return self._initial_route('interceptor', ticket, url)
            return (self._state in _LOAD_STATES and first_party == DOCUMENT
                    and initiator in {ORIGIN, ORIGIN + '/'})

    def resource(self, ticket, url, method, initiator):
        """Independent scheme-handler check; Qt Job supplies no resource-type field."""
        with self._lock:
            if not self.current(ticket) or type(method) is not bytes or method != b'GET':
                return None
            if type(initiator) is not str:
                return None
            if not canonical_resource_url(url):
                return None
            resource = self._resources.get(url)
            if resource is None:
                return None
            if resource.request_type == 'MainFrame':
                if initiator not in {'', ORIGIN, ORIGIN + '/'} or not self._initial_route('handler', ticket, url):
                    return None
            elif self._state not in _LOAD_STATES or initiator not in {ORIGIN, ORIGIN + '/'}:
                return None
            return resource

    def loaded(self, ticket, *, success, requested_url, current_url):
        with self._lock:
            if (ticket is not self._ticket or self._state != 'DOCUMENT_LOADING' or type(success) is not bool
                    or not success or requested_url != DOCUMENT or current_url != DOCUMENT
                    or self._seen_initial != {'navigation', 'interceptor', 'handler'}):
                if ticket is self._ticket:
                    self._retire('LOAD_REJECTED')
                return False
            self._state = 'CHANNEL_BOOTSTRAP'
            return True

    def channel_ready(self, ticket):
        with self._lock:
            if ticket is not self._ticket or self._state != 'CHANNEL_BOOTSTRAP' or self._ready:
                return False
            self._ready = True
            return True

    def admit_initial_commit(self, ticket, private_validated_commit):
        """Receipt must come from the native producer's private callback, never JS.

The integrated producer must validate/brand this receipt before invoking this
method. bool/sequence/mode from a serialized message is not accepted here.
"""
        with self._lock:
            if (ticket is not self._ticket or self._state != 'CHANNEL_BOOTSTRAP' or not self._ready
                    or private_validated_commit is None or self._commit_verifier is None):
                return False
            # Bound native verifier performs private branded receipt lookup against
            # this generation/sequence. No Qt operation/await occurs under this lock.
            if self._commit_verifier(private_validated_commit, self._generation, 1) is not True:
                return False
            self._sequence = 1
            self._state = 'ADMITTED'
            return True

    def commands_admitted(self, ticket):
        with self._lock:
            return ticket is self._ticket and self._state == 'ADMITTED' and self._ready and self._sequence > 0

    def _retire(self, reason):
        if self._state not in {'RETIRED', 'DISPOSING', 'CLOSED'}:
            self._state = 'RETIRED'
            self._reason = reason
            self._ready = False

    def retire(self, ticket, reason='NATIVE_RETIRED'):
        with self._lock:
            if ticket is self._ticket:
                self._retire(reason)

    def native_state(self):
        with self._lock:
            return MappingProxyType({'state': self._state, 'generation': self._generation,
                                     'ready': self._ready, 'sequence': self._sequence,
                                     'reason': self._reason})
