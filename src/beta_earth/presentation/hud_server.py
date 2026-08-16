"""Asset-ID: BE-NEXT-HUD-SERVER | Version: 0.51.1 | Status: current.

Loopback-only browser HUD adapter for the authoritative local game session.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import secrets
import sqlite3
import threading
import webbrowser
import logging
import os
import platform
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence

from beta_earth import __version__
from beta_earth.application.diagnostics import export_support_bundle
from beta_earth.application.engine import GameEngine
from beta_earth.application.service import GameApplication, GameSession
from beta_earth.domain.clock import SystemClock
from beta_earth.infrastructure.content_loader import ContentError, load_catalog
from beta_earth.infrastructure.sqlite_store import SQLiteStateStore, StoreConflict
from beta_earth.infrastructure.startup_support import (
    configure_runtime_logging,
    write_startup_failure,
)


MAX_REQUEST_BYTES = 4096
MAX_COMMAND_CHARS = 512
LOOPBACK_HOST = "127.0.0.1"
ALLOWED_HOST_NAMES = frozenset({"127.0.0.1", "localhost"})
STATIC_TYPES = {
    "/styles.css": "text/css; charset=utf-8",
    "/app.js": "text/javascript; charset=utf-8",
    "/media/field-signal.wav": "audio/wav",
    "/media/sfx/signal-tick.wav": "audio/wav",
    "/media/sfx/signal-select.wav": "audio/wav",
    "/media/sfx/signal-confirm.wav": "audio/wav",
    "/media/sfx/signal-warning.wav": "audio/wav",
    "/media/sfx/signal-error.wav": "audio/wav",
    "/media/sfx/signal-impact.wav": "audio/wav",
    "/media/sfx/signal-recovery.wav": "audio/wav",
}


@dataclass
class HudController:
    """Own exactly one character session and serialize all state transitions."""

    project_root: Path
    runtime_dir: Path
    seed: int | None = None
    computer_id: str = "PC-LOCAL-UNASSIGNED"
    telemetry_dir: Path | None = None

    def __post_init__(self) -> None:
        self.project_root = self.project_root.resolve()
        self.runtime_dir = self.runtime_dir.resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.catalog = load_catalog(self.project_root / "content")
        self.store = SQLiteStateStore(self.runtime_dir / "beta_earth.sqlite3")
        self.engine = GameEngine(
            self.catalog,
            SystemClock(),
            random.Random(self.seed) if self.seed is not None else random.Random(),
        )
        self.application = GameApplication(
            self.engine,
            self.store,
            receipt_context={
                "os_family": platform.system() or "unknown",
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "launch_surface": "hud",
                "computer_label": self.computer_id.strip() or "PC-LOCAL-UNASSIGNED",
                "native_windows_launcher": bool(
                    platform.system() == "Windows"
                    and os.environ.get("BETA_EARTH_LAUNCHED_BY_BAT") == "1"
                ),
            },
        )
        self.session: GameSession | None = None
        self._lock = threading.RLock()
        self._diagnostic_lock = threading.Lock()
        self._support_export: dict[str, object] = {
            "status": "not-generated",
            "automatic": True,
            "stale": True,
            "message": "Your support package will be created when a character opens.",
        }
        self.computer_id = self.computer_id.strip() or "PC-LOCAL-UNASSIGNED"
        self.telemetry_dir = (
            self.telemetry_dir.resolve()
            if self.telemetry_dir is not None
            else (self.project_root / "runtime" / "computers" / self.computer_id).resolve()
        )
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)
        default_runtime = (self.project_root / "runtime").resolve()
        if self.computer_id == "PC-LOCAL-UNASSIGNED" and self.runtime_dir != default_runtime:
            support_root = self.runtime_dir / "exports" / "support"
        else:
            support_root = self.project_root / "exports" / "support" / self.computer_id
        self.support_destination = support_root / (
            f"UPLOAD_THIS_BetaEarth_{self.computer_id}_Diagnostics_v{__version__}.zip"
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _refresh_support_export(self, trigger: str) -> dict[str, object]:
        with self._diagnostic_lock:
            try:
                count, errors, output = export_support_bundle(
                    self.project_root,
                    self.support_destination,
                    runtime_database=self.runtime_dir / "beta_earth.sqlite3",
                    telemetry_dir=self.telemetry_dir,
                )
                self._support_export = {
                    "status": "partial" if errors else "current",
                    "automatic": True,
                    "stale": False,
                    "trigger": trigger,
                    "filename": output.name,
                    "computer_label": self.computer_id.strip() or "PC-LOCAL-UNASSIGNED",
                    "relative_path": (
                        output.relative_to(self.project_root).as_posix()
                        if output.is_relative_to(self.project_root)
                        else f"exports/support/{output.name}"
                    ),
                    "item_count": count,
                    "item_limit": 20,
                    "sha256": self._file_sha256(output),
                    "sidecar_filename": output.name + ".sha256.txt",
                    "message": "Ready to upload." if not errors else "Created, but some checks need review.",
                    "collector_errors": list(errors),
                }
            except Exception as exc:
                self._support_export = {
                    "status": "error",
                    "automatic": True,
                    "stale": True,
                    "trigger": trigger,
                    "message": f"Support package could not be created: {type(exc).__name__}: {exc}"[:400],
                }
            return dict(self._support_export)

    def export_diagnostics(self) -> dict[str, object]:
        with self._lock:
            export_status = self._refresh_support_export("manual-hud-refresh")
            return {"ok": export_status.get("status") != "error", "support_export": export_status}

    def _client_state(self) -> dict[str, object]:
        if self.session is None:
            raise ValueError("Open a character before requesting state.")
        state = self.engine.client_state(self.session.state)
        state["support_export"] = dict(self._support_export)
        return state

    def command_catalog(self) -> list[dict[str, object]]:
        return [
            {
                "name": spec.name,
                "aliases": list(spec.aliases),
                "summary": spec.summary,
                "recovery": spec.recovery.value,
            }
            for spec in self.engine.parser.specs
        ]

    def open_session(self, player_name: object) -> dict[str, object]:
        if not isinstance(player_name, str):
            raise ValueError("Character name must be text.")
        with self._lock:
            if self.session is not None:
                requested = player_name.strip().casefold()
                opened = self.session.state.character.name.casefold()
                if requested and requested != opened:
                    raise ValueError(
                        "This HUD already has a character open. Quit before switching."
                    )
                session = self.session
            else:
                session = self.application.open_session(player_name)
                self.session = session
            if session.created:
                opening = self.engine.welcome(session.state)
            else:
                opening = (
                    f"Welcome back, {session.state.character.name}.\n\n"
                    f"{self.engine.render_room(session.state)}"
                )
            self._refresh_support_export("session-open")
            return {
                "ok": True,
                "created": session.created,
                "output": opening,
                "state": self._client_state(),
                "commands": self.command_catalog(),
                "version": __version__,
            }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            if self.session is None:
                return {
                    "ok": True,
                    "started": False,
                    "version": __version__,
                    "commands": self.command_catalog(),
                }
            return {
                "ok": True,
                "started": True,
                "version": __version__,
                "state": self._client_state(),
                "commands": self.command_catalog(),
            }

    def execute(self, raw: object) -> dict[str, object]:
        if not isinstance(raw, str):
            raise ValueError("Command must be text.")
        command = raw.strip()
        if not command:
            raise ValueError("Enter a command.")
        if len(command) > MAX_COMMAND_CHARS:
            raise ValueError(
                f"Command exceeds the {MAX_COMMAND_CHARS}-character local limit."
            )
        with self._lock:
            if self.session is None:
                raise ValueError("Open a character before issuing commands.")
            result = self.session.execute(command)
            if result.changed:
                self._refresh_support_export("state-changing-command")
            return {
                "ok": True,
                "command": command,
                "output": result.text,
                "changed": result.changed,
                "quit": result.quit,
                "state": self._client_state(),
            }


class HudHttpServer(ThreadingHTTPServer):
    """Typed server container used by the request handler."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        controller: HudController,
        static_root: Path,
        session_token: str,
    ) -> None:
        self.controller = controller
        self.static_root = static_root.resolve()
        self.session_token = session_token
        super().__init__(address, HudRequestHandler)


class HudRequestHandler(BaseHTTPRequestHandler):
    """Small, explicit HTTP surface; no directory serving or external requests."""

    server: HudHttpServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _host_allowed(self) -> bool:
        raw = self.headers.get("Host", "")
        host = raw.rsplit(":", 1)[0].strip().strip("[]").casefold()
        return host in ALLOWED_HOST_NAMES

    def _security_headers(self, *, cache: str = "no-store") -> None:
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "media-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        ))
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        ))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        cache: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers(cache=cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
            self.wfile.flush()

    def _send_json(self, status: int, document: dict[str, object]) -> None:
        body = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _reject_if_bad_host(self) -> bool:
        if self._host_allowed():
            return False
        self._send_json(421, {"ok": False, "error": "Invalid local Host header."})
        return True

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Beta-Earth-Token", "")
        return bool(supplied) and secrets.compare_digest(
            supplied,
            self.server.session_token,
        )

    def _discard_bounded_body(self) -> None:
        """Drain a small rejected request so Windows can deliver the response."""

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if 0 < length <= MAX_REQUEST_BYTES:
            self.rfile.read(length)

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().casefold() != "application/json":
            raise ValueError("API requests require application/json.")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("API request length is required.")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("API request length is invalid.") from exc
        if not 0 <= length <= MAX_REQUEST_BYTES:
            raise ValueError(
                f"API request exceeds the {MAX_REQUEST_BYTES}-byte local limit."
            )
        try:
            document = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("API request body is not valid UTF-8 JSON.") from exc
        if not isinstance(document, dict):
            raise ValueError("API request body must be a JSON object.")
        return document

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if self._reject_if_bad_host():
            return
        path = self.path.split("?", 1)[0]
        if path == "/favicon.ico":
            self._send_bytes(204, b"", "image/x-icon")
            return
        if path == "/api/status":
            if not self._authorized():
                self._send_json(403, {"ok": False, "error": "Local session token required."})
                return
            self._send_json(200, self.server.controller.snapshot())
            return
        if path == "/":
            source = (self.server.static_root / "index.html").read_text(encoding="utf-8")
            body = source.replace(
                "__BE_SESSION_TOKEN__",
                self.server.session_token,
            ).encode("utf-8")
            self._send_bytes(200, body, "text/html; charset=utf-8")
            return
        content_type = STATIC_TYPES.get(path)
        if content_type is not None:
            body = (self.server.static_root / path.removeprefix("/")).read_bytes()
            self._send_bytes(
                200,
                body,
                content_type,
                cache="no-cache, max-age=0",
            )
            return
        self._send_json(404, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:
        if self._reject_if_bad_host():
            return
        if not self._authorized():
            self._discard_bounded_body()
            self._send_json(403, {"ok": False, "error": "Local session token required."})
            return
        path = self.path.split("?", 1)[0]
        try:
            document = self._read_json()
            if path == "/api/session":
                payload = self.server.controller.open_session(document.get("player"))
            elif path == "/api/command":
                payload = self.server.controller.execute(document.get("command"))
            elif path == "/api/diagnostics/export":
                payload = self.server.controller.export_diagnostics()
            elif path == "/api/shutdown":
                payload = {"ok": True, "message": "Beta Earth is closing."}
            else:
                self._send_json(404, {"ok": False, "error": "Not found."})
                return
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)[:400]})
            return
        except StoreConflict as exc:
            self._send_json(409, {"ok": False, "error": f"Save conflict: {exc}"[:400]})
            return
        except (ContentError, OSError, RuntimeError, sqlite3.Error) as exc:
            self._send_json(500, {"ok": False, "error": f"Local game error: {exc}"[:400]})
            return
        self._send_json(200, payload)
        if path == "/api/shutdown":
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Beta Earth: Sovereignty Next HUD",
        description="Run the loopback-only first-party browser HUD.",
    )
    parser.add_argument("--player", help="character name to open immediately")
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help="explicit runtime directory (defaults to <project>/runtime)",
    )
    parser.add_argument(
        "--computer-id",
        default="PC-LOCAL-UNASSIGNED",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--telemetry-dir",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--seed", type=int, help="deterministic combat seed")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="loopback port (default: choose an available ephemeral port)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="print the local URL without opening the default browser",
    )
    return parser


def create_server(
    project_root: Path,
    *,
    runtime_dir: Path | None = None,
    seed: int | None = None,
    port: int = 0,
    computer_id: str = "PC-LOCAL-UNASSIGNED",
    telemetry_dir: Path | None = None,
) -> HudHttpServer:
    root = project_root.resolve()
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535.")
    controller = HudController(
        root,
        runtime_dir or (root / "runtime"),
        seed,
        computer_id,
        telemetry_dir,
    )
    return HudHttpServer(
        (LOOPBACK_HOST, port),
        controller,
        root / "hud",
        secrets.token_hex(32),
    )

def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path,
) -> int:
    args = build_parser().parse_args(argv)
    root = project_root.resolve()
    runtime_dir = (args.runtime_dir or (root / "runtime")).resolve()
    telemetry_dir = (
        args.telemetry_dir.resolve()
        if args.telemetry_dir is not None
        else (root / "runtime" / "computers" / args.computer_id).resolve()
    )
    logger = configure_runtime_logging(telemetry_dir)

    server: HudHttpServer | None = None
    try:
        try:
            server = create_server(
                root,
                runtime_dir=runtime_dir,
                seed=args.seed,
                port=args.port,
                computer_id=args.computer_id,
                telemetry_dir=telemetry_dir,
            )
            if args.player:
                server.controller.open_session(args.player)
        except (ContentError, ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            write_startup_failure(root, telemetry_dir, exc, phase="hud-create-server")
            logger.exception("HUD startup failed")
            print(f"HUD startup failed: {exc}")
            print("Run BetaEarthSovereignty.bat --self-test, then BetaEarthSovereignty_ExportDiagnostics.bat.")
            return 2

        host, port = server.server_address
        url = f"http://{host}:{port}/"
        logger.info(
            "HUD ready url=%s computer=%s restrictions=none shared_runtime=%s",
            url,
            args.computer_id,
            runtime_dir,
        )
        print(f"Beta Earth HUD ready at {url}")
        print(f"Computer label: {args.computer_id} (diagnostics only)")
        print("Additional HUD launches are allowed. Keep this window open while playing.")
        print("Press Ctrl+C to stop safely.")
        if not args.no_browser and not webbrowser.open(url, new=1):
            print("The browser did not open automatically. Open the local URL above.")
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            print("\nStopping this local HUD. Progress is already saved.")
            logger.info("HUD interrupted by user")
        finally:
            server.server_close()
            logger.info("HUD stopped")
        return 0
    finally:
        if server is not None:
            try:
                server.server_close()
            except OSError:
                logging.getLogger("beta_earth").warning("HUD server close raised an OS error")
