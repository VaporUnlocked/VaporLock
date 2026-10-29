"""Authenticated local control API for Vapor Lock.

The service is intentionally off by default and listens on loopback unless the
owner explicitly enables LAN access. It is a complete settings and session
control surface for owner-built integrations, not a streaming or file-transfer
service: it never exposes preview frames, paths, activation
data, or hardware identifiers through this module.
"""

from __future__ import annotations

from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import ssl
import threading
import time
from typing import Any, Callable


DEFAULT_PORT = 47631
MAX_BODY_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 3.0
MAX_CONNECTIONS = 16


def lan_tls_context() -> tuple[ssl.SSLContext, Path, str]:
    """Create a per-user LAN identity; export only its public certificate."""
    from datetime import datetime, timedelta, timezone
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directory = token_store_path().parent / "local-api-tls"
    certificate_path, key_path = directory / "certificate.pem", directory / "private-key.pem"
    hostname = socket.gethostname()
    names = [x509.DNSName("localhost"), x509.DNSName(hostname)]
    addresses = {"127.0.0.1"}
    try:
        addresses.update(item[4][0] for item in socket.getaddrinfo(hostname, None, socket.AF_INET))
    except OSError:
        pass
    names.extend(x509.IPAddress(ipaddress.ip_address(value)) for value in sorted(addresses))
    now = datetime.now(timezone.utc)
    try:
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        current_names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if certificate.not_valid_after_utc <= now + timedelta(days=7) or set(current_names) != set(names):
            raise ValueError("LAN certificate renewal required")
        if not key_path.is_file():
            raise ValueError("LAN identity is incomplete")
    except (OSError, ValueError, x509.ExtensionNotFound):
        directory.mkdir(parents=True, exist_ok=True)
        private_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Vapor Lock Local API")])
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                       .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
                       .add_extension(x509.SubjectAlternativeName(names), critical=False)
                       .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                       .sign(private_key, hashes.SHA256()))
        for path, data in ((key_path, private_key.private_bytes(serialization.Encoding.PEM,
                               serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
                           (certificate_path, certificate.public_bytes(serialization.Encoding.PEM))):
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate_path, key_path)
    return context, certificate_path, certificate.fingerprint(hashes.SHA256()).hex()


class _BoundedHttpServer(ThreadingHTTPServer):
    """Bound unauthenticated connections before allocating request threads."""

    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.tls_context = None
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self._connections_lock:
            self._connections.add(request)
        try:
            request.settimeout(REQUEST_TIMEOUT_SECONDS)
            super().process_request(request, client_address)
        except Exception:
            with self._connections_lock:
                self._connections.discard(request)
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        original = request
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        deadline = threading.Timer(REQUEST_TIMEOUT_SECONDS, expire)
        deadline.daemon = True
        deadline.start()
        try:
            if self.tls_context is not None:
                # Handshake on a bounded worker, never on the accept thread.
                request = self.tls_context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
                with self._connections_lock:
                    self._connections.discard(original)
                    self._connections.add(request)
                request.do_handshake()
            super().process_request_thread(request, client_address)
        except (OSError, ssl.SSLError):
            self.shutdown_request(request)
        finally:
            deadline.cancel()
            with self._connections_lock:
                self._connections.discard(request)
                self._connections.discard(original)
            self._slots.release()

    def handle_error(self, request, client_address):
        # A disconnected/malformed LAN client must not dump its address or
        # request context to stderr, including during application shutdown.
        return

    def server_close(self):
        with self._connections_lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        super().server_close()


def token_store_path() -> Path:
    """Store the API secret outside portable profiles and release files."""
    import os

    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Vapor Lock"
    return root / "local-api.json"


def load_or_create_token(path: Path | None = None) -> str:
    path = path or token_store_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        token = str(loaded.get("token", ""))
        if len(token) >= 32:
            return token
    except (OSError, ValueError, TypeError):
        pass
    token = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"token": token}), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # The in-memory token still protects the current session if the local
        # state directory is unavailable.
        pass
    return token


def regenerate_token(path: Path | None = None) -> str:
    """Replace the local API secret without ever placing it in a profile."""
    path = path or token_store_path()
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"token": token}), encoding="utf-8")
    temporary.replace(path)
    return token


class LocalControlApi:
    """Threaded, token-protected local HTTP endpoint with a snapshot model."""

    def __init__(self, command_handler: Callable[[dict[str, Any]], None], *, token: str | None = None) -> None:
        self._command_handler = command_handler
        self._token = token or load_or_create_token()
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stop_owners = []
        self._host = ""
        self._port = 0
        self._error = ""
        self.certificate_path: Path | None = None
        self.certificate_fingerprint = ""

    @property
    def token(self) -> str:
        return self._token

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def address(self) -> str:
        if not self.running:
            return ""
        scheme = "https" if self.certificate_path is not None else "http"
        return f"{scheme}://{self._host}:{self._port}/api/v1"

    @property
    def error(self) -> str:
        return self._error

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._snapshot = deepcopy(snapshot)

    def update_jobs(self, records):
        with self._lock:
            self._jobs = deepcopy(records[:32])

    def jobs(self):
        with self._lock:
            return deepcopy(getattr(self,'_jobs',[]))

    def observations(self):
        cache=getattr(self,'observation_cache',None)
        if cache is None:return dict(state='disabled',observations=[],selected_track=0)
        return cache.read()

    def queue_command(self, kind: str, payload: dict) -> str | None:
        now = time.monotonic()
        with self._lock:
            records = getattr(self, "_commands", {})
            records = {key: item for key, item in records.items() if now - item[0] < 600}
            if sum(item[1]["state"] == "queued" for item in records.values()) >= 128:
                return None
            if len(records) >= 256:
                oldest = next((key for key, item in records.items() if item[1]["state"] != "queued"), None)
                if oldest:
                    del records[oldest]
            identifier = secrets.token_hex(12)
            records[identifier] = (now, {"state": "queued", "type": kind})
            self._commands = records
        try:
            self._command_handler({"type": kind, "payload": payload, "command_id": identifier})
        except Exception:
            self.finish_command(identifier, {"state": "rejected", "error": "dispatch unavailable"})
        return identifier

    def finish_command(self, identifier: str, result: dict) -> None:
        with self._lock:
            records = getattr(self, "_commands", {})
            if identifier in records:
                stamp, original = records[identifier]
                records[identifier] = (stamp, {**original, **deepcopy(result)})

    def command_status(self, identifier: str) -> dict | None:
        with self._lock:
            record = getattr(self, "_commands", {}).get(identifier)
            return deepcopy(record[1]) if record and time.monotonic() - record[0] < 600 else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._snapshot)

    def start(self, *, allow_lan: bool, port: int) -> tuple[bool, str]:
        requested_host = "0.0.0.0" if allow_lan else "127.0.0.1"
        requested_port = int(port)
        if not 1024 <= requested_port <= 65535:
            return False, "Choose a TCP port from 1024 through 65535."
        if self.running and self._host == requested_host and self._port == requested_port:
            return True, self.address
        self.stop()
        self.certificate_path = None
        self.certificate_fingerprint = ""
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "VaporLockLocalApi/1"
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                # Do not emit request URLs or headers. They can contain an
                # authorization token when users experiment with tools.
                return

            def _send(self, status: HTTPStatus, payload: dict[str, Any] | None = None) -> None:
                self.close_connection = True
                encoded = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(int(status))
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                if encoded:
                    self.wfile.write(encoded)

            def _authorized(self) -> bool:
                expected = f"Bearer {api.token}"
                supplied = self.headers.get("Authorization", "")
                if not secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
                    self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "authorization required"})
                    return False
                return True

            def _body(self) -> dict[str, Any] | None:
                if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) > 1:
                    self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "unsupported request framing"})
                    return None
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid content length"})
                    return None
                if size < 0 or size > MAX_BODY_BYTES:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "request body is too large"})
                    return None
                try:
                    value = json.loads(self.rfile.read(size).decode("utf-8")) if size else {}
                except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                    self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "body must be a JSON object"})
                    return None
                if not isinstance(value, dict):
                    self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "body must be a JSON object"})
                    return None
                return value

            def do_OPTIONS(self) -> None:
                self._send(HTTPStatus.NO_CONTENT)

            def do_GET(self) -> None:
                if not self._authorized():
                    return
                route = self.path.split("?", 1)[0]
                snapshot = api.snapshot()
                if route == "/api/v1/health":
                    self._send(HTTPStatus.OK, {"ok": True, "service": "vapor-lock-local-api", "protocol_version": "1"})
                elif route == "/api/v1/status":
                    self._send(HTTPStatus.OK, {"ok": True, "status": snapshot.get("status", {})})
                elif route == "/api/v1/settings":
                    self._send(HTTPStatus.OK, {"ok": True, "settings": snapshot.get("settings", {})})
                elif route == "/api/v1/profiles":
                    self._send(HTTPStatus.OK, {"ok": True, "profiles": snapshot.get("profiles", [])})
                elif route == "/api/v1/models":
                    self._send(HTTPStatus.OK, {"ok": True, "models": snapshot.get("models", [])})
                elif route == "/api/v1/compensation-presets":
                    self._send(HTTPStatus.OK, {"ok": True, "presets": snapshot.get("compensation_presets", [])})
                elif route == "/api/v1/catalog":
                    self._send(HTTPStatus.OK, {"ok": True, "catalog": snapshot.get("catalog", {})})
                elif route == "/api/v1/schema":
                    self._send(HTTPStatus.OK, {"ok": True, "schema": snapshot.get("schema", {})})
                elif route == '/api/v1/jobs':
                    self._send(HTTPStatus.OK, {'ok':True,'jobs':api.jobs()})
                elif route == '/api/v1/observations':
                    self._send(HTTPStatus.OK, {'ok':True,**api.observations()})
                elif route.startswith('/api/v1/jobs/'):
                    identifier=route.rsplit('/',1)[-1]
                    record=next((r for r in api.jobs() if r['id']==identifier),None)
                    self._send(HTTPStatus.OK if record else HTTPStatus.NOT_FOUND,
                        {'ok':True,'job':record} if record else {'ok':False,'error':'unknown or expired job'})
                elif route.startswith("/api/v1/commands/"):
                    result = api.command_status(route.rsplit("/", 1)[-1])
                    self._send(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                               {"ok": bool(result), "command": result} if result else {"ok": False, "error": "unknown or expired command"})
                else:
                    self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown route"})

            def do_POST(self) -> None:
                if not self._authorized():
                    return
                payload = self._body()
                if payload is None:
                    return
                route = self.path.split("?", 1)[0]
                command_type = {
                    "/api/v1/session": "session",
                    "/api/v1/settings": "settings",
                    "/api/v1/profile": "profile",
                    "/api/v1/model": "model",
                    "/api/v1/compensation": "compensation",
                    "/api/v1/action": "action",
                    "/api/v1/jobs": "jobs",
                }.get(route)
                if command_type is None:
                    self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown route"})
                    return
                identifier = api.queue_command(command_type, payload)
                if identifier is None:
                    self._send(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "command queue is full"})
                    return
                self._send(HTTPStatus.ACCEPTED, {"ok": True, "accepted": command_type, "command_id": identifier})

        try:
            tls = lan_tls_context() if allow_lan else None
            server = _BoundedHttpServer((requested_host, requested_port), Handler)
            if tls is not None:
                server.tls_context, self.certificate_path, self.certificate_fingerprint = tls
            server.daemon_threads = True
        except (OSError, ValueError) as error:
            self._error = f"Could not start the local API: {error}"
            return False, self._error
        self._server = server
        self._host = requested_host
        self._port = requested_port
        self._error = ""
        self._thread = threading.Thread(target=server.serve_forever, name="VaporLockLocalApi", daemon=True)
        self._thread.start()
        return True, self.address

    def stop(self) -> None:
        cache=getattr(self,'observation_cache',None)
        if cache is not None:cache.configure(False,False,None)
        server = self._server
        self._server = None
        self._host = ""
        self._port = 0
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def request_stop_async(self) -> None:
        """Retain server/thread ownership while a native close waits for real cleanup."""
        cache=getattr(self,'observation_cache',None)
        if cache is not None:cache.configure(False,False,None)
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        self._host = ''
        self._port = 0
        if server is None and thread is None:
            return
        owner = {'server': server, 'thread': thread, 'cleanup': None, 'failed': False}
        def cleanup():
            try:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None and thread is not threading.current_thread():
                    thread.join()
            except Exception:
                owner['failed'] = True
        worker = threading.Thread(target=cleanup, name='VaporLockAPIStop', daemon=True)
        owner['cleanup'] = worker
        self._stop_owners.append(owner)
        worker.start()

    def cleanup_complete(self) -> bool:
        def requests_pending(owner):
            server = owner['server']
            if server is None:
                return False
            with server._connections_lock:
                return bool(server._connections)
        self._stop_owners = [owner for owner in self._stop_owners if owner['failed']
            or owner['cleanup'].is_alive() or requests_pending(owner)
            or owner['thread'] is not None and owner['thread'].is_alive()]
        return not self.running and self._thread is None and not self._stop_owners
