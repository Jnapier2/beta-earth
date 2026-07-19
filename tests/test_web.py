from __future__ import annotations

import http.client
import json
import logging
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from beta_earth.presentation.http_server import create_server

from .support import ROOT, build_test_service


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        service = build_test_service(Path(self.temp.name) / "players")
        logger = logging.getLogger(f"web-test-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        self.server = create_server("127.0.0.1", 0, service, ROOT / "static", logger)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def test_hud_page_contains_current_option_and_mission_surfaces(self) -> None:
        with urllib.request.urlopen(self.base + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
        self.assertIn("Current options available", html)
        self.assertIn("Selectable command text", html)
        self.assertIn("MISSION TRACER", html)
        self.assertIn("/assets/app.js", html)

    def test_state_and_command_routes_share_current_actions(self) -> None:
        state = self._get_state("WebPlayer")
        self.assertEqual(state["current_options"][0]["command"], "gender female")
        self.assertEqual(state["selectable_commands"][0], "gender female")
        advanced = self._post(
            "/api/command",
            {
                "player": "WebPlayer",
                "command": "gender female",
                "expected_revision": state["player"]["revision"],
            },
        )
        self.assertEqual(advanced["current_options"][0]["command"], "rollstats")

    def test_quest_journal_and_mission_action_share_api_state(self) -> None:
        player = "QuestWeb"
        state = self._get_state(player)
        for command in ("gender female", "balancedstats", "begin", "talk Caroline", "accept route mission"):
            state = self._post(
                "/api/command",
                {"player": player, "command": command, "expected_revision": state["player"]["revision"]},
            )
        self.assertEqual(state["quest_journal"]["active_count"], 1)
        self.assertEqual(state["quest_journal"]["active"][0]["tracer"]["recommended_command"], "go east")
        self.assertEqual(state["current_options"][0]["command"], "go east")
        self.assertEqual(state["current_options"][0]["mission_id"], "caroline_route_reading")

    def test_stale_revision_returns_conflict_without_replay(self) -> None:
        state = self._get_state("ConflictPlayer")
        payload = {
            "player": "ConflictPlayer",
            "command": "gender female",
            "expected_revision": state["player"]["revision"],
        }
        self._post("/api/command", payload)
        request = urllib.request.Request(
            self.base + "/api/command",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 409)

    def test_non_loopback_host_header_is_rejected(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.putrequest("GET", "/api/health", skip_host=True)
        connection.putheader("Host", "malicious.example")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()

    def test_malformed_host_header_is_rejected_without_crashing_server(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.putrequest("GET", "/api/health", skip_host=True)
        connection.putheader("Host", "127.0.0.1:not-a-port")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()
        health = self._get_state("StillAlive")
        self.assertEqual(health["current_options"][0]["command"], "gender female")



    def test_oversized_expect_continue_body_is_rejected_before_read(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.putrequest("POST", "/api/command")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(16_385))
        connection.putheader("Expect", "100-continue")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        self.assertEqual(response.getheader("Connection"), "close")
        response.read()
        connection.close()

    def test_unsafe_profile_inputs_do_not_collapse_into_safe_profile_ids(self) -> None:
        unsafe = self._get_state("A%3FB")
        safe = self._get_state("AB")
        self.assertNotEqual(unsafe["player"]["id"], safe["player"]["id"])

    def test_static_assets_are_not_stale_cached(self) -> None:
        with urllib.request.urlopen(self.base + "/assets/app.js", timeout=5) as response:
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            self.assertIn("javascript", response.headers.get_content_type())

    def test_post_types_are_not_silently_coerced(self) -> None:
        state = self._get_state("TypedPost")
        for payload in (
            {"player": {"bad": True}, "command": "gender female", "expected_revision": state["player"]["revision"]},
            {"player": "TypedPost", "command": "gender female", "expected_revision": "0"},
        ):
            request = urllib.request.Request(
                self.base + "/api/command",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 400)


    def test_missing_revision_is_rejected_for_state_changing_posts(self) -> None:
        for path, payload in (
            ("/api/command", {"player": "MissingRevision", "command": "gender female"}),
            ("/api/reset", {"player": "MissingRevision"}),
        ):
            request = urllib.request.Request(
                self.base + path,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 400)

    def test_reset_is_revision_guarded(self) -> None:
        state = self._get_state("ResetWeb")
        changed = self._post(
            "/api/command",
            {
                "player": "ResetWeb",
                "command": "gender female",
                "expected_revision": state["player"]["revision"],
            },
        )
        stale_request = urllib.request.Request(
            self.base + "/api/reset",
            data=json.dumps(
                {"player": "ResetWeb", "expected_revision": state["player"]["revision"]}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(stale_request, timeout=5)
        self.assertEqual(caught.exception.code, 409)
        preserved = json.loads(caught.exception.read().decode("utf-8"))["state"]
        self.assertEqual(preserved["player"]["revision"], changed["player"]["revision"])
        self.assertEqual(preserved["player"]["identity"], "female")

        reset = self._post(
            "/api/reset",
            {"player": "ResetWeb", "expected_revision": changed["player"]["revision"]},
        )
        self.assertEqual(reset["player"]["stage"], "identity")
        self.assertEqual(reset["current_options"][0]["command"], "gender female")

    def test_hud_script_preserves_completed_missions_and_safe_reconnect_callbacks(self) -> None:
        with urllib.request.urlopen(self.base + "/assets/app.js", timeout=5) as response:
            script = response.read().decode("utf-8")
        self.assertIn("completed-missions", script)
        self.assertIn('retry.addEventListener("click", () => loadState())', script)
        self.assertIn('window.addEventListener("online", () => {', script)
        self.assertIn("pendingNetworkRefresh = true", script)
        self.assertIn("flushPendingNetworkRefresh()", script)
        self.assertIn("expected_revision: snapshot.player.revision", script)

    def _get_state(self, player: str) -> dict[str, object]:
        with urllib.request.urlopen(self.base + f"/api/state?player={player}", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
