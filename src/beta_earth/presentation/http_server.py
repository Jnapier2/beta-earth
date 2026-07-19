from __future__ import annotations

import json
import logging
import mimetypes
import os
import socket
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from beta_earth import __version__
from beta_earth.application.ports import PlayerDataError, RevisionConflict
from beta_earth.application.service import GameService, InvalidCommand
from beta_earth.domain.identity import PLAYER_INPUT_MAX_LENGTH, canonical_player_id, display_name_from_input
from beta_earth.observability import RunContext

from .view_models import snapshot_to_dict

_MAX_BODY_BYTES = 16_384
_MAX_PATH_CHARS = 4_096
_MAX_STATIC_BYTES = 2 * 1024 * 1024
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class RequestBodyTooLarge(ValueError):
    """Raised before reading a request body that exceeds the local API bound."""


class BetaEarthHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    allow_reuse_port = False
    request_queue_size = 32

    def server_bind(self) -> None:
        # Windows SO_REUSEADDR can allow confusing cross-process binds. Claim the
        # loopback address exclusively so Beta Earth cannot share another app's port.
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(
        self,
        address: tuple[str, int],
        service: GameService,
        static_dir: Path,
        logger: logging.Logger,
        run: RunContext,
        *,
        max_concurrency: int = 16,
    ) -> None:
        if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or not 1 <= max_concurrency <= 128:
            raise ValueError("max_concurrency must be an integer from 1 to 128")
        super().__init__(address, BetaEarthRequestHandler)
        self.service = service
        self.static_dir = static_dir.resolve()
        self.logger = logger
        self.run = run
        self.max_concurrency = max_concurrency
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._metrics_lock = threading.Lock()
        self._active_requests = 0
        self._rejected_requests = 0
        self._handled_requests = 0

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        if not self._slots.acquire(blocking=False):
            with self._metrics_lock:
                self._rejected_requests += 1
            self._reject_busy(request)
            self.shutdown_request(request)
            return
        with self._metrics_lock:
            self._active_requests += 1
        try:
            super().process_request(request, client_address)
        except Exception:
            with self._metrics_lock:
                self._active_requests -= 1
            self._slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._metrics_lock:
                self._active_requests -= 1
                self._handled_requests += 1
            self._slots.release()

    def metrics(self) -> dict[str, int]:
        with self._metrics_lock:
            return {
                "active_requests": self._active_requests,
                "handled_requests": self._handled_requests,
                "rejected_requests": self._rejected_requests,
                "max_concurrency": self.max_concurrency,
                "request_backlog": self.request_queue_size,
            }

    @staticmethod
    def _reject_busy(request: socket.socket) -> None:
        payload = b'{"error":"server_busy","message":"The local HUD is busy. Retry after the current requests finish."}'
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            + f"Content-Length: {len(payload)}\r\n".encode("ascii")
            + b"Cache-Control: no-store\r\nRetry-After: 1\r\nConnection: close\r\n\r\n"
            + payload
        )
        try:
            request.sendall(response)
        except OSError:
            pass


class BetaEarthRequestHandler(BaseHTTPRequestHandler):
    server: BetaEarthHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = f"BetaEarthHUD/{__version__}"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def handle_expect_100(self) -> bool:
        """Reject invalid or oversized mutations before a client sends the body."""

        if self.command != "POST":
            self.close_connection = True
            self._send_json(HTTPStatus.EXPECTATION_FAILED, {"error": "expectation_failed"})
            return False
        if not self._request_is_local():
            self.close_connection = True
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "local_request_required"})
            return False
        try:
            self._validated_json_body_length()
        except RequestBodyTooLarge as exc:
            self.close_connection = True
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large", "message": str(exc)})
            return False
        except ValueError as exc:
            self.close_connection = True
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": str(exc)})
            return False
        self.send_response_only(HTTPStatus.CONTINUE)
        self.end_headers()
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._request_is_local():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "local_request_required"})
            return
        if len(self.path) > _MAX_PATH_CHARS:
            self._send_json(HTTPStatus.REQUEST_URI_TOO_LONG, {"error": "path_too_long"})
            return
        try:
            self._handle_get()
        except PlayerDataError as exc:
            self.server.logger.warning("Player save rejected: %s", exc)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": "player_state_error",
                    "message": "The local player save could not be loaded safely. Use a new profile or restore a known-good save.",
                },
            )
        except Exception:
            self.server.logger.exception("Unhandled GET request failure")
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "internal_error", "message": "The local server could not load this resource."},
            )

    def _handle_get(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/":
            self._serve_static("index.html")
            return
        if parsed.path.startswith("/assets/"):
            self._serve_static(parsed.path.removeprefix("/assets/"))
            return
        if parsed.path == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "version": __version__,
                    "transport": "loopback",
                    "action_source": "application_service",
                    "quest_source": "typed_static_catalog",
                    "economy_source": "typed_static_catalog",
                    "run": self.server.run.as_public_dict(),
                    "server": {
                        **self.server.metrics(),
                        "bound_host": str(self.server.server_address[0]),
                        "bound_port": int(self.server.server_address[1]),
                        "exclusive_address_binding": True,
                    },
                },
            )
            return
        if parsed.path == "/api/state":
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=8)
            player = _clean_player(query.get("player", ["Traveler"])[0])
            display_name = _clean_display_name(query.get("name", [player])[0], fallback=player)
            snapshot = self.server.service.get_snapshot(player, display_name=display_name)
            self._send_json(HTTPStatus.OK, snapshot_to_dict(snapshot, version=__version__))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._request_is_local():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "local_request_required"})
            return
        if len(self.path) > _MAX_PATH_CHARS:
            self._send_json(HTTPStatus.REQUEST_URI_TOO_LONG, {"error": "path_too_long"})
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path not in {"/api/command", "/api/reset"}:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        body: dict[str, Any] = {}
        try:
            body = self._read_json_body()
            allowed_fields = {"player", "expected_revision", "name"} if parsed.path == "/api/reset" else {
                "player", "expected_revision", "command"
            }
            unknown = sorted(set(body) - allowed_fields)
            if unknown:
                raise ValueError(f"Unknown request fields: {', '.join(unknown)}")
            player = _clean_player(_body_string(body, "player", default="Traveler"))
            expected_revision = _required_revision(body.get("expected_revision"))
            if parsed.path == "/api/reset":
                snapshot = self.server.service.reset(
                    player,
                    display_name=_clean_display_name(_body_string(body, "name", default=player), fallback=player),
                    expected_revision=expected_revision,
                )
            else:
                snapshot = self.server.service.execute(
                    player,
                    _body_string(body, "command"),
                    expected_revision=expected_revision,
                )
            self._send_json(HTTPStatus.OK, snapshot_to_dict(snapshot, version=__version__))
        except RevisionConflict as exc:
            player = _clean_player(_body_string(body, "player", default="Traveler"))
            snapshot = self.server.service.get_snapshot(player)
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "revision_conflict", "message": str(exc), "state": snapshot_to_dict(snapshot, version=__version__)},
            )
        except InvalidCommand as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_command", "message": str(exc)})
        except RequestBodyTooLarge as exc:
            self.close_connection = True
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large", "message": str(exc)})
        except PlayerDataError as exc:
            self.server.logger.warning("Player save rejected: %s", exc)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": "player_state_error",
                    "message": "The local player save could not be loaded safely. Use a new profile or restore a known-good save.",
                },
            )
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.close_connection = True
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": str(exc)})
        except Exception:
            self.server.logger.exception("Unhandled request failure")
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "internal_error", "message": "The local server could not complete the request."},
            )

    def _request_is_local(self) -> bool:
        try:
            parsed_host = urllib.parse.urlsplit(f"//{self.headers.get('Host', '')}")
            expected_port = int(self.server.server_address[1])
            if parsed_host.hostname not in _LOCAL_HOSTS or parsed_host.port != expected_port:
                return False
            origin = self.headers.get("Origin")
            if not origin:
                return True
            parsed_origin = urllib.parse.urlsplit(origin)
            return (
                parsed_origin.scheme == "http"
                and parsed_origin.hostname in _LOCAL_HOSTS
                and parsed_origin.port == expected_port
                and parsed_origin.username is None
                and parsed_origin.password is None
            )
        except ValueError:
            return False

    def _validated_json_body_length(self) -> int:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 1:
            raise ValueError("Request body must not be empty")
        if length > _MAX_BODY_BYTES:
            raise RequestBodyTooLarge(f"Request body exceeds {_MAX_BODY_BYTES} bytes")
        return length

    def _read_json_body(self) -> dict[str, Any]:
        length = self._validated_json_body_length()
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Request body ended before Content-Length bytes were received")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON request must be an object")
        return value

    def _serve_static(self, relative_name: str) -> None:
        candidate = (self.server.static_dir / relative_name).resolve()
        try:
            candidate.relative_to(self.server.static_dir)
        except ValueError:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return
        if not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if candidate.stat().st_size > _MAX_STATIC_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "asset_too_large"})
            return
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write(content)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if status in {HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.SERVICE_UNAVAILABLE}:
            self.send_header("Retry-After", "1")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self._write(content)

    def _write(self, content: bytes) -> None:
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")

    def log_message(self, format: str, *args: object) -> None:
        self.server.logger.debug("HTTP loopback - %s", format % args)


def create_server(
    host: str,
    port: int,
    service: GameService,
    static_dir: Path,
    logger: logging.Logger,
    run: RunContext | None = None,
    *,
    max_concurrency: int = 16,
) -> BetaEarthHTTPServer:
    if host not in _LOCAL_HOSTS:
        raise ValueError("The clean rebuild binds to loopback only")
    return BetaEarthHTTPServer((host, port), service, static_dir, logger, run or RunContext.create(), max_concurrency=max_concurrency)


def _clean_player(value: str) -> str:
    if len(value) > PLAYER_INPUT_MAX_LENGTH:
        raise ValueError(f"player must not exceed {PLAYER_INPUT_MAX_LENGTH} characters")
    return canonical_player_id(value)


def _clean_display_name(value: str, *, fallback: str) -> str:
    return display_name_from_input(value, fallback=fallback)


def _body_string(body: dict[str, Any], field_name: str, *, default: str | None = None) -> str:
    value = body.get(field_name, default)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if default is None and not value.strip():
        raise ValueError(f"{field_name} is required")
    if len(value) > 1_000:
        raise ValueError(f"{field_name} is too long")
    return value


def _required_revision(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected_revision is required and must be an integer")
    if value < 0:
        raise ValueError("expected_revision cannot be negative")
    return value
