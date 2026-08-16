"""v0.51.1 HUD truth-alignment contracts.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from html.parser import HTMLParser

from tests.support import PROJECT_ROOT
from beta_earth.presentation.hud_server import HudController


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)


class HudTruthAlignmentV0511Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.controller = HudController(PROJECT_ROOT, Path(self.temporary.name), seed=511)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_client_state_exposes_authoritative_battlefield_and_foundation_truth(self) -> None:
        state = self.controller.open_session("Truth Pilot")["state"]
        self.assertIn("battlefield", state)
        self.assertFalse(state["battlefield"]["soft_commands_advance_time"])
        self.assertIn("foundation", state)
        foundation = state["foundation"]
        self.assertEqual(7, len(foundation["factions"]))
        self.assertIn("party", foundation)
        self.assertIn("territory", foundation)
        self.assertIn("civic_mission", foundation)
        expected_entry_titles = {
            "armageddon": "Chosen",
            "syndicate": "Thug",
            "final_bloodline": "Activist",
            "guardian_angel": "Test Subject",
            "redemption": "Human",
            "bounty_hunters": "Green",
            "security_uf": "Recruit",
        }
        self.assertEqual(
            expected_entry_titles,
            {item["id"]: item["pledge_entry_rank_title"] for item in foundation["factions"]},
        )
        self.assertTrue(all(item["pledge_statement"] for item in foundation["factions"]))

    def test_pending_pledge_projection_retains_entry_rank_without_granting_allegiance(self) -> None:
        self.controller.open_session("Pledge HUD")
        assert self.controller.session is not None
        sovereignty = self.controller.session.state.foundation.sovereignty
        standing = sovereignty.factions["security_uf"]
        standing.public_standing = 10
        standing.access_flags.add("candidate_contact")
        sovereignty.pending_allegiance_id = "security_uf"
        foundation = self.controller.engine.client_state(self.controller.session.state)["foundation"]
        self.assertEqual("security_uf", foundation["pending_allegiance_id"])
        self.assertIsNone(foundation["allegiance_id"])
        security = next(item for item in foundation["factions"] if item["id"] == "security_uf")
        self.assertEqual("Recruit", security["pledge_entry_rank_title"])
        self.assertEqual("Unranked", security["rank_title"])
        self.assertFalse(foundation["membership_granted"])

    def test_static_html_has_unique_complete_truth_alignment_landmarks(self) -> None:
        html = (PROJECT_ROOT / "hud" / "index.html").read_text(encoding="utf-8")
        parser = _IdCollector()
        parser.feed(html)
        ids = parser.ids
        self.assertEqual([], [item for item, count in Counter(ids).items() if count > 1])
        for element_id in (
            "combat-now-card",
            "combat-now-player",
            "combat-now-sol",
            "combat-now-threat",
            "battlefield-summary",
            "battlefield-timeline",
            "tactical-live-region",
            "enemy-count-label",
            "combat-partner-intent",
            "combat-partner-ready",
            "combat-partner-target",
            "withdrawal-summary",
            "sovereignty-card",
            "sovereignty-title",
            "sovereignty-rank",
            "faction-standing-list",
            "sprawl-state-card",
            "sprawl-supply",
            "sprawl-defense",
            "sprawl-prosperity",
            "sprawl-tension",
            "civic-mission-step",
            "focus-context-label",
            "focus-after-contact",
            "focus-coaching",
            "command-tray-toggle",
        ):
            self.assertIn(element_id, ids)

    def test_client_literal_id_references_exist_and_no_unsafe_html_is_used(self) -> None:
        html = (PROJECT_ROOT / "hud" / "index.html").read_text(encoding="utf-8")
        source = (PROJECT_ROOT / "hud" / "app.js").read_text(encoding="utf-8")
        parser = _IdCollector()
        parser.feed(html)
        ids = set(parser.ids)
        references = set(re.findall(r'''["']#([A-Za-z][A-Za-z0-9_-]*)["']''', source))
        self.assertEqual(set(), references - ids)
        self.assertNotIn(".innerHTML", source)
        self.assertNotIn("eval(", source)
        self.assertIn("renderBattlefield(state)", source)
        self.assertIn("renderFoundation(state)", source)
        self.assertIn("pendingAllegiance", source)
        self.assertIn("soft_commands_advance_time", source)
        self.assertIn("data-combat-projection", source)

    def test_css_keeps_primary_combat_actions_sticky_and_protects_transcript(self) -> None:
        source = (PROJECT_ROOT / "hud" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("v0.51.1 — HUD Truth Alignment", source)
        self.assertIsNotNone(re.search(r"\.combat-primary-deck\s*\{[^}]*position:\s*sticky", source, re.S))
        self.assertIsNotNone(re.search(r"\.enemy-list\s*\{[^}]*max-height:\s*none", source, re.S))
        self.assertIn("@media (max-width: 1180px) and (min-width: 861px)", source)
        self.assertIsNotNone(re.search(r"@media \(max-width: 860px\)[\s\S]*?\.transcript\s*\{[^}]*min-height:\s*(?:150|180)px", source, re.S))
        self.assertIn("body.command-tray-expanded .action-toolbar", source)

    def test_public_audio_is_self_contained_and_rights_cleared(self) -> None:
        html = (PROJECT_ROOT / "hud" / "index.html").read_text(encoding="utf-8")
        server = (
            PROJECT_ROOT / "src/beta_earth/presentation/hud_server.py"
        ).read_text(encoding="utf-8")
        self.assertIn('src="/media/field-signal.wav"', html)
        self.assertIn('type="audio/wav"', html)
        self.assertIn("Field Signal", html)
        self.assertIn('"/media/field-signal.wav": "audio/wav"', server)
        self.assertNotIn("neon-1.mp3", html + server)


if __name__ == "__main__":
    unittest.main()
