from __future__ import annotations

import errno
import json
import logging
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from beta_earth.infrastructure.instance_guard import SingleInstanceGuard
from beta_earth.observability import RunContext
from beta_earth.presentation.http_server import create_server
from run_beta_earth import create_available_server

from .support import ROOT, build_test_service


class PlatformAndResilienceTests(unittest.TestCase):
    def test_single_instance_handoff_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            name = f"BetaEarthTest{os.getpid()}_{id(self)}"
            first = SingleInstanceGuard(state, name=name)
            second = SingleInstanceGuard(state, name=name)
            self.assertTrue(first.acquire())
            first.publish(url="http://127.0.0.1:4455/?player=Test", run_id="run-test", pid=os.getpid())
            self.assertFalse(second.acquire())
            self.assertEqual(second.existing_url(), "http://127.0.0.1:4455/?player=Test")
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_instance_handoff_waits_for_delayed_metadata_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            name = f"BetaEarthWait{os.getpid()}_{id(self)}"
            owner = SingleInstanceGuard(state, name=name)
            follower = SingleInstanceGuard(state, name=name)
            self.assertTrue(owner.acquire())
            self.assertFalse(follower.acquire())

            def publish_later() -> None:
                time.sleep(0.05)
                owner.publish(url="http://127.0.0.1:4455/?player=Delayed", run_id="run-delay", pid=os.getpid())

            thread = threading.Thread(target=publish_later)
            thread.start()
            try:
                self.assertEqual(
                    follower.wait_for_existing_url(timeout=1.0, poll_interval=0.01),
                    "http://127.0.0.1:4455/?player=Delayed",
                )
            finally:
                thread.join(timeout=2)
                owner.release()

    def test_new_instance_owner_clears_stale_handoff_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            metadata = state / "runtime_instance.json"
            metadata.write_text(
                json.dumps({"url": "http://127.0.0.1:4455/?player=Stale", "token": "old"}),
                encoding="utf-8",
            )
            guard = SingleInstanceGuard(state, name=f"BetaEarthClear{os.getpid()}_{id(self)}")
            self.assertTrue(guard.acquire())
            try:
                self.assertFalse(metadata.exists())
            finally:
                guard.release()

    def test_default_port_is_os_assigned_without_scanning_other_apps(self) -> None:
        logger = logging.getLogger(f"port-auto-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())

        class FakeServer:
            server_address = ("127.0.0.1", 53177)

        sentinel = FakeServer()
        with patch("run_beta_earth.create_server", return_value=sentinel) as mocked:
            server, port = create_available_server(0, object(), ROOT / "static", logger, RunContext.create())
        self.assertIs(server, sentinel)
        self.assertEqual(port, 53177)
        self.assertEqual(mocked.call_args.args[1], 0)

    def test_explicit_port_retry_is_bounded_and_only_for_address_in_use(self) -> None:
        logger = logging.getLogger(f"port-select-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        sentinel = object()
        busy = OSError(errno.EADDRINUSE, "already in use")
        with patch("run_beta_earth.create_server", side_effect=[busy, sentinel]) as mocked:
            server, port = create_available_server(4455, object(), ROOT / "static", logger, RunContext.create(), span=2)
        self.assertIs(server, sentinel)
        self.assertEqual(port, 4456)
        self.assertEqual([call.args[1] for call in mocked.call_args_list], [4455, 4456])

    @unittest.skipIf(os.name == "nt", "POSIX stale lock recovery test")
    def test_stale_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "runtime.lock").write_text(json.dumps({"pid": 999_999_999, "token": "stale"}), encoding="utf-8")
            guard = SingleInstanceGuard(state, name=f"Stale{id(self)}")
            self.assertTrue(guard.acquire())
            guard.release()

    def test_health_route_reports_bounded_local_server_and_security_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = build_test_service(Path(temp_dir) / "players")
            logger = logging.getLogger(f"platform-test-{id(self)}")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            server = create_server("127.0.0.1", 0, service, ROOT / "static", logger, max_concurrency=4)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/api/health"
                with urllib.request.urlopen(url, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    headers = response.headers
                self.assertEqual(payload["transport"], "loopback")
                self.assertEqual(payload["server"]["max_concurrency"], 4)
                self.assertEqual(payload["server"]["request_backlog"], 32)
                self.assertTrue(payload["server"]["exclusive_address_binding"])
                self.assertEqual(payload["server"]["bound_port"], server.server_address[1])
                self.assertIn("object-src 'none'", headers["Content-Security-Policy"])
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["Cache-Control"], "no-store")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_post_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = build_test_service(Path(temp_dir) / "players")
            logger = logging.getLogger(f"unknown-post-{id(self)}")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            server = create_server("127.0.0.1", 0, service, ROOT / "static", logger)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with urllib.request.urlopen(base + "/api/state?player=Strict", timeout=5) as response:
                    state = json.loads(response.read().decode("utf-8"))
                request = urllib.request.Request(
                    base + "/api/command",
                    data=json.dumps(
                        {
                            "player": "Strict",
                            "command": "gender female",
                            "expected_revision": state["player"]["revision"],
                            "unexpected": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

if __name__ == "__main__":
    unittest.main()
