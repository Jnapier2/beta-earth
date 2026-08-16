"""HUD projection, loopback transport, security, and static-client contracts."""

from __future__ import annotations

import http.client
import json
import re
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from tests.support import PROJECT_ROOT

from beta_earth.presentation.hud_server import (
    MAX_COMMAND_CHARS,
    HudController,
    create_server,
)


class HudProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.controller = HudController(
            PROJECT_ROOT,
            Path(self.temporary.name),
            seed=41,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_opening_projection_contains_every_hud_foundation(self) -> None:
        document = self.controller.open_session("HUD Pilot")
        self.assertTrue(document["created"])
        state = document["state"]
        self.assertEqual("beta-earth-client-state-v1", state["schema"])
        self.assertEqual(
            {"strength", "agility", "perception", "combat_skill"},
            set(state["character"]["attributes"]),
        )
        self.assertIsInstance(state["character"]["inventory"], list)
        self.assertTrue(state["room"]["description"])
        self.assertEqual(
            sorted(state["room"]["inspectables"]),
            state["room"]["inspectables"],
        )
        self.assertEqual(
            set(state["room"]["exits"]),
            {
                entry["direction"]
                for entry in state["room"]["exit_details"]
                if not entry["locked"]
            },
        )
        self.assertTrue(
            any(entry["locked"] and entry["lock_reason"] for entry in state["room"]["exit_details"])
        )
        self.assertIsInstance(state["navigation"]["connections"], list)
        self.assertEqual(75, len(document["commands"]))
        build = state["character"]["build"]
        self.assertEqual("pending", build["status"])
        self.assertEqual(15, len(build["classes"]))
        self.assertEqual(7, len({item["faction_id"] for item in build["classes"]}))
        self.assertEqual("setup", state["directive"]["kind"])
        self.assertTrue(state["story"]["active"])
        self.assertEqual("second_breath", state["story"]["quest_id"])
        readiness = state["story"]["readiness"]
        self.assertEqual(0, readiness["completed"])
        self.assertEqual(6, readiness["total"])
        self.assertEqual(6, len(readiness["items"]))
        self.assertEqual(0, state["journal"]["sovereignty_count"])
        self.assertEqual(["Sol"], [npc["name"] for npc in state["room"]["npcs"]])
        partner = state["economy"]["companion"]
        self.assertEqual("Sol", partner["name"])
        self.assertEqual("partner", partner["assist_kind"])
        self.assertTrue(partner["story_bound"])
        self.assertEqual(3, len(partner["order_commands"]))
        foundation = state["beginner_experience"]
        self.assertEqual(120, foundation["target_minutes"])
        self.assertEqual(10, foundation["target_level"])
        self.assertFalse(foundation["ready_for_capstone"])
        self.assertEqual(26, foundation["starter_room_count"])
        self.assertEqual(5, len(foundation["chapters"]))
        self.assertEqual(10, len(foundation["competencies"]))
        self.assertEqual(
            {
                "learned_in_level",
                "required_per_level",
                "remaining",
                "awaiting_absorption",
            },
            set(state["character"]["level_progress"]),
        )

    def test_live_support_export_is_created_and_refreshes_after_saved_actions(self) -> None:
        opened = self.controller.open_session("Support Pilot")
        support = opened["state"]["support_export"]
        self.assertEqual("current", support["status"])
        self.assertEqual("session-open", support["trigger"])
        self.assertEqual(20, support["item_count"])
        output = Path(self.temporary.name) / support["relative_path"]
        self.assertTrue(output.is_file())
        self.assertTrue(output.with_suffix(".zip.sha256.txt").is_file())
        with zipfile.ZipFile(output, "r") as archive:
            player_summary = json.loads(
                archive.read("generated/player_state_summary.json")
            )
            self.assertEqual(1, len(player_summary["slots"]))
            exported_text = b"\n".join(archive.read(name) for name in archive.namelist())
            self.assertNotIn(b"Support Pilot", exported_text)
            self.assertNotIn(b"support pilot", exported_text)

        changed = self.controller.execute("build class soldier")
        refreshed = changed["state"]["support_export"]
        self.assertEqual("state-changing-command", refreshed["trigger"])
        self.assertEqual("current", refreshed["status"])
        self.assertRegex(refreshed["sha256"], r"^[0-9a-f]{64}$")

    def test_inventory_projection_is_actionable_without_leaking_mutable_objects(self) -> None:
        state = self.controller.open_session("Inventory HUD")["state"]
        item = state["character"]["inventory"][0]
        required = {
            "instance_id",
            "definition_id",
            "name",
            "description",
            "bulk",
            "slot",
            "equipped",
            "equipped_slot",
            "attack_bonus",
            "defense_bonus",
            "damage",
            "roundtime",
            "armor",
            "durability",
            "max_durability",
            "repair_family",
            "repair_value",
        }
        self.assertEqual(set(), required - set(item))
        item["name"] = "client-only mutation"
        snapshot = self.controller.snapshot()["state"]
        self.assertNotEqual(
            "client-only mutation",
            snapshot["character"]["inventory"][0]["name"],
        )

    def test_read_only_snapshot_and_look_do_not_churn_revision(self) -> None:
        opened = self.controller.open_session("Read Only HUD")
        revision = opened["state"]["revision"]
        self.assertEqual(revision, self.controller.snapshot()["state"]["revision"])
        result = self.controller.execute("look")
        self.assertFalse(result["changed"])
        self.assertEqual(revision, result["state"]["revision"])

    def test_controller_rejects_switching_and_oversized_commands(self) -> None:
        self.controller.open_session("First Pilot")
        with self.assertRaisesRegex(ValueError, "already has a character"):
            self.controller.open_session("Second Pilot")
        with self.assertRaisesRegex(ValueError, "512-character"):
            self.controller.execute("x" * (MAX_COMMAND_CHARS + 1))


class HudHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.server = create_server(
            PROJECT_ROOT,
            runtime_dir=Path(self.temporary.name),
            seed=41,
            port=0,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        token: str | None = None,
        host: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-Beta-Earth-Token"] = token
        if host is not None:
            headers["Host"] = host
        for attempt in range(2):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
            try:
                connection.request(
                    method,
                    path,
                    body=json.dumps(body).encode("utf-8") if body is not None else None,
                    headers=headers,
                )
                response = connection.getresponse()
                payload = response.read()
                status = response.status
                response_headers = {
                    key.casefold(): value for key, value in response.getheaders()
                }
                return status, response_headers, payload
            except (ConnectionResetError, TimeoutError):
                if attempt:
                    raise
            finally:
                connection.close()
        raise RuntimeError("unreachable loopback request state")

    def test_static_shell_security_and_authorized_command_round_trip(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertEqual("DENY", headers["x-frame-options"])
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
        self.assertEqual("nosniff", headers["x-content-type-options"])
        source = body.decode("utf-8")
        match = re.search(
            r'<meta name="beta-earth-token" content="([0-9a-f]{64})">',
            source,
        )
        self.assertIsNotNone(match)
        session_token = match.group(1)
        self.assertNotIn("__BE_SESSION_TOKEN__", source)

        status, _, body = self.request(
            "POST",
            "/api/session",
            body={"player": "HTTP HUD"},
        )
        self.assertEqual(403, status)
        self.assertFalse(json.loads(body)["ok"])

        status, _, body = self.request(
            "POST",
            "/api/session",
            body={"player": "HTTP HUD"},
            token=session_token,
        )
        self.assertEqual(200, status)
        opened = json.loads(body)
        self.assertEqual("Earth — Sprawl 15, Intake Concourse", opened["state"]["room"]["title"])

        status, _, body = self.request(
            "POST",
            "/api/command",
            body={"command": "look"},
            token=session_token,
        )
        self.assertEqual(200, status)
        command = json.loads(body)
        self.assertEqual("look", command["command"])
        self.assertIn("Intake Concourse", command["output"])

    def test_diagnostic_export_endpoint_requires_token_and_returns_upload_path(self) -> None:
        token = self.server.session_token
        self.request(
            "POST", "/api/session", body={"player": "Diagnostic HTTP"}, token=token
        )
        status, _, body = self.request(
            "POST", "/api/diagnostics/export", body={}
        )
        self.assertEqual(403, status)
        self.assertFalse(json.loads(body)["ok"])

        status, _, body = self.request(
            "POST", "/api/diagnostics/export", body={}, token=token
        )
        self.assertEqual(200, status)
        document = json.loads(body)
        self.assertTrue(document["ok"])
        self.assertEqual("current", document["support_export"]["status"])
        self.assertTrue(document["support_export"]["filename"].startswith("UPLOAD_THIS_"))

    def test_transport_rejects_foreign_host_and_unknown_paths(self) -> None:
        status, _, body = self.request("GET", "/", host="malicious.invalid")
        self.assertEqual(421, status)
        self.assertFalse(json.loads(body)["ok"])

        status, _, body = self.request("GET", "/../content/world.json")
        self.assertEqual(404, status)
        self.assertFalse(json.loads(body)["ok"])

    def test_status_requires_token_and_does_not_create_a_character(self) -> None:
        status, _, _ = self.request("GET", "/api/status")
        self.assertEqual(403, status)
        status, _, body = self.request(
            "GET",
            "/api/status",
            token=self.server.session_token,
        )
        self.assertEqual(200, status)
        status_document = json.loads(body)
        self.assertFalse(status_document["started"])


class HudStaticAssetTests(unittest.TestCase):
    def test_hud_has_all_requested_panels_and_accessibility_landmarks(self) -> None:
        source = (PROJECT_ROOT / "hud" / "index.html").read_text(encoding="utf-8")
        for label in (
            "Character Status",
            "Navigation",
            "World Feed",
            "Combat",
            "Inventory",
            "Progress &amp; Journal",
            "First Watch",
            "People present",
            "Sovereignty",
        ):
            self.assertIn(label, source)
        self.assertIn('class="skip-link"', source)
        self.assertIn('aria-live="polite"', source)
        self.assertIn('id="command-input"', source)
        for element_id in (
            "directive-choice-list",
            "room-npc-list",
            "story-card",
            "story-relationship-list",
            "journal-sovereignty",
            "guide-companion",
            "guide-companion-action",
            "story-card-heading",
            "story-route-interest",
            "story-readiness",
            "story-readiness-summary",
            "story-readiness-grid",
            "support-export-button",
            "support-export-status",
            "combat-partner-card",
            "combat-partner-name",
            "combat-partner-level",
            "combat-partner-role",
            "combat-partner-health-value",
            "combat-partner-health-fill",
            "combat-partner-xp-value",
            "combat-partner-xp-fill",
            "combat-partner-status",
            "foundation-card",
            "foundation-title",
            "foundation-status",
            "foundation-summary",
            "foundation-level",
            "foundation-time",
            "foundation-rooms",
            "foundation-progress-fill",
            "foundation-active-chapter",
            "foundation-class-assignment",
            "foundation-class-title",
            "foundation-class-objective",
            "foundation-chapters",
            "foundation-competencies",
        ):
            self.assertIn(f'id="{element_id}"', source)
        self.assertNotRegex(source, r"<script(?![^>]+src=)")

    def test_styles_include_responsive_contrast_and_reduced_motion_modes(self) -> None:
        source = (PROJECT_ROOT / "hud" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 860px)", source)
        self.assertIn("@media (max-width: 560px)", source)
        self.assertIn("@media (prefers-reduced-motion: reduce)", source)
        self.assertIn("body.high-contrast", source)
        self.assertIn("button:focus-visible", source)

    def test_client_uses_safe_text_and_authenticated_same_origin_calls(self) -> None:
        source = (PROJECT_ROOT / "hud" / "app.js").read_text(encoding="utf-8")
        self.assertIn('"X-Beta-Earth-Token": token', source)
        self.assertIn(".textContent =", source)
        self.assertNotIn(".innerHTML", source)
        self.assertIn('api("/api/command"', source)
        self.assertIn('api("/api/diagnostics/export"', source)
        self.assertIn("guide-companion", source)
        self.assertIn("story-readiness-grid", source)
        self.assertIn("readiness.items", source)
        self.assertIn("renderCombatPartner", source)
        self.assertIn("renderBeginnerExperience", source)
        self.assertIn("data-companion-order", (PROJECT_ROOT / "hud" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("localStorage", source)


if __name__ == "__main__":
    unittest.main()
