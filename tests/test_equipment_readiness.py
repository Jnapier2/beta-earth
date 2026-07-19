from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from beta_earth.presentation.view_models import snapshot_to_dict

from .support import build_test_service, complete_route_mission


class EquipmentReadinessPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_test_service(Path(self.temp.name) / "players")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_did_readiness_starts_locked_and_is_not_a_command_source(self) -> None:
        snapshot = self.service.get_snapshot("DidLocked")
        payload = snapshot_to_dict(snapshot, version="test")
        self.assertEqual(payload["did_readiness"]["tier"], "locked")
        self.assertFalse(payload["did_readiness"]["combat_modifiers_enabled"])
        self.assertNotIn("did_readiness", {action.command for action in snapshot.actions})
        self.assertNotIn("equip route-tracer calibration", {action.command for action in snapshot.actions})

    def test_inspecting_limiter_sets_baseline_readiness_preview(self) -> None:
        player = "DidBaseline"
        snapshot = self.service.get_snapshot(player)
        for command in ("gender female", "balancedstats", "begin", "inspect limiter"):
            snapshot = self.service.execute(player, command, expected_revision=snapshot.state.revision)
        payload = snapshot_to_dict(snapshot, version="test")
        self.assertEqual(payload["did_readiness"]["tier"], "baseline")
        self.assertIn("DID limiter inspected", payload["did_readiness"]["reasons"])
        self.assertFalse(payload["did_readiness"]["combat_modifiers_enabled"])

    def test_route_reward_calibrates_read_only_did_slot(self) -> None:
        snapshot = complete_route_mission(self.service, "DidCalibrated")
        payload = snapshot_to_dict(snapshot, version="test")
        readiness = payload["did_readiness"]
        self.assertEqual(readiness["tier"], "calibrated")
        self.assertFalse(readiness["combat_modifiers_enabled"])
        slots = {slot["slot_id"]: slot for slot in readiness["slots"]}
        self.assertEqual(slots["did_core"]["equipped_item_id"], "route_tracer_calibration")
        self.assertEqual(slots["field_filter"]["equipped_item_id"], None)

    def test_lane_safe_filter_promotes_field_prepared_preview_only(self) -> None:
        player = "DidFieldPrepared"
        snapshot = complete_route_mission(self.service, player)
        for command in ("go east", "go east", "go north", "barter lane-safe filter"):
            snapshot = self.service.execute(player, command, expected_revision=snapshot.state.revision)
        payload = snapshot_to_dict(snapshot, version="test")
        readiness = payload["did_readiness"]
        self.assertEqual(readiness["tier"], "field_prepared")
        self.assertFalse(readiness["combat_modifiers_enabled"])
        self.assertIn("Lane-Safe Filter carried", readiness["reasons"])
        self.assertNotIn("attack", [action["command"] for action in payload["current_options"]])


if __name__ == "__main__":
    unittest.main()
