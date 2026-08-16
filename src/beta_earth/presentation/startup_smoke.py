"""End-to-end loopback HUD startup smoke test.

Asset-ID: BE-NEXT-STARTUP-SMOKE | Version: 0.47.0 | Status: current.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from beta_earth.presentation.hud_server import create_server

NOTICE = "Copyright © 2026 Gateway Information Group LLC. All rights reserved."


def _request(
    connection: http.client.HTTPConnection,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, bytes]:
    body = None
    headers = {"Host": "127.0.0.1"}
    if token:
        headers["X-Beta-Earth-Token"] = token
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    for attempt in range(2):
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            status = response.status
            response.close()
            return status, data
        except (ConnectionResetError, TimeoutError):
            connection.close()
            if attempt:
                raise
    raise RuntimeError("unreachable loopback request state")


def run_startup_smoke(project_root: Path) -> dict[str, Any]:
    """Launch the real local HUD server and verify its critical surfaces."""

    with tempfile.TemporaryDirectory(prefix="Beta Earth Startup Smoke ") as temporary:
        runtime = Path(temporary) / "runtime with spaces"
        server = create_server(project_root, runtime_dir=runtime, seed=41, port=0)
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        started = time.monotonic()
        thread.start()
        host, port = server.server_address
        # Endpoint inspection can briefly delay larger local responses on Windows.
        # Keep the test bounded without treating a slow security scan as a launch failure.
        connection = http.client.HTTPConnection(host, port, timeout=30.0)
        checks: list[dict[str, Any]] = []
        try:
            for path, marker in (
                ("/", b"__BE_SESSION_TOKEN__"),
                ("/styles.css", b"Copyright"),
                ("/app.js", b"Copyright"),
            ):
                status, body = _request(connection, "GET", path)
                if status != 200:
                    raise RuntimeError(f"GET {path} returned HTTP {status}")
                if path == "/" and marker in body:
                    raise RuntimeError("HUD token placeholder was not replaced")
                if path != "/" and marker not in body:
                    raise RuntimeError(f"GET {path} missing expected rights marker")
                checks.append({"surface": path, "status": status, "bytes": len(body)})

            token = server.session_token
            status, body = _request(connection, "GET", "/api/status", token=token)
            document = json.loads(body.decode("utf-8"))
            if status != 200 or not document.get("ok") or document.get("started"):
                raise RuntimeError("initial HUD status contract failed")
            checks.append({"surface": "/api/status", "status": status, "started": False})

            status, body = _request(
                connection,
                "POST",
                "/api/session",
                token=token,
                payload={"player": "Startup Smoke"},
            )
            document = json.loads(body.decode("utf-8"))
            if status != 200 or not document.get("ok") or not document.get("state"):
                raise RuntimeError("HUD session-open contract failed")
            checks.append({"surface": "/api/session", "status": status, "created": document.get("created")})

            status, body = _request(
                connection,
                "POST",
                "/api/command",
                token=token,
                payload={"command": "look"},
            )
            document = json.loads(body.decode("utf-8"))
            if status != 200 or not document.get("ok") or not document.get("output"):
                raise RuntimeError("HUD command contract failed")
            checks.append({"surface": "/api/command", "status": status, "command": "look"})

            status, body = _request(
                connection,
                "POST",
                "/api/diagnostics/export",
                token=token,
                payload={},
            )
            document = json.loads(body.decode("utf-8"))
            if status != 200 or not document.get("ok"):
                raise RuntimeError("HUD diagnostic export contract failed")
            support = document.get("support_export", {})
            if support.get("item_count") != 20:
                raise RuntimeError("HUD diagnostic export did not contain exactly 20 files")
            checks.append({"surface": "/api/diagnostics/export", "status": status, "items": 20})

            status, _ = _request(
                connection,
                "POST",
                "/api/shutdown",
                token=token,
                payload={},
            )
            if status != 200:
                raise RuntimeError("HUD shutdown contract failed")
            checks.append({"surface": "/api/shutdown", "status": status})
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("HUD server thread did not stop within the bounded timeout")
        return {
            "status": "passed",
            "host": host,
            "port_mode": "ephemeral-loopback",
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "checks": checks,
            "runtime_mode": "temporary isolated path containing spaces",
            "network_scope": "127.0.0.1 only; no external request",
            "copyright_notice": NOTICE,
        }
