from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from beta_earth.application.ports import RevisionConflict
from beta_earth.infrastructure.economy_loader import JsonEconomyRepository, validate_economy_document
from beta_earth.infrastructure.world_loader import JsonWorldRepository
from beta_earth.presentation.view_models import snapshot_to_dict

from .support import ROOT, build_test_service, complete_route_mission


class EconomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_test_service(Path(self.temp.name) / "players")
        self.world = JsonWorldRepository(ROOT / "data" / "world.json").load()
        self.catalog = JsonEconomyRepository(ROOT / "data" / "economy.json").load(self.world)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_route_reward_and_one_time_barter_are_atomic(self) -> None:
        snapshot = complete_route_mission(self.service, "barter-player")
        self.assertEqual(snapshot.state.cred, 5)
        self.assertEqual(snapshot.state.inventory.quantity("route_tracer_calibration"), 1)

        for command in ("go east", "go east", "go north"):
            snapshot = self.service.execute(
                "barter-player", command, expected_revision=snapshot.state.revision
            )
        commands = [action.command for action in snapshot.actions]
        self.assertIn("barter lane-safe filter", commands)
        before_revision = snapshot.state.revision

        traded = self.service.execute(
            "barter-player",
            "barter lane-safe filter",
            expected_revision=before_revision,
        )
        self.assertEqual(traded.state.cred, 2)
        self.assertEqual(traded.state.inventory.quantity("lane_safe_filter"), 1)
        self.assertEqual(traded.state.inventory.quantity("route_tracer_calibration"), 1)
        self.assertIn("lane_safe_filter_trade", traded.state.completed_transactions)
        self.assertNotIn("barter lane-safe filter", [action.command for action in traded.actions])

        with self.assertRaises(RevisionConflict):
            self.service.execute(
                "barter-player",
                "barter lane-safe filter",
                expected_revision=before_revision,
            )
        current = self.service.get_snapshot("barter-player")
        self.assertEqual(current.state.cred, 2)
        self.assertEqual(current.state.inventory.quantity("lane_safe_filter"), 1)

    def test_economy_projection_and_current_action_share_the_same_offer(self) -> None:
        snapshot = complete_route_mission(self.service, "projection-player")
        for command in ("go east", "go east", "go north"):
            snapshot = self.service.execute(
                "projection-player", command, expected_revision=snapshot.state.revision
            )
        payload = snapshot_to_dict(snapshot, version="test")
        commands = [item["command"] for item in payload["current_options"]]
        offers = payload["economy"]["room_offers"]
        self.assertEqual([item["command"] for item in offers], ["barter lane-safe filter"])
        self.assertIn(offers[0]["command"], commands)
        self.assertTrue(offers[0]["affordable"])

    def test_economy_validator_rejects_missing_items_and_command_conflicts(self) -> None:
        raw = json.loads((ROOT / "data" / "economy.json").read_text(encoding="utf-8"))
        missing = copy.deepcopy(raw)
        missing["barter_offers"]["lane_safe_filter_trade"]["grants"][0]["item_id"] = "missing_item"
        issues = validate_economy_document(missing, self.world)
        self.assertTrue(any("references missing item" in issue for issue in issues))

        conflict = copy.deepcopy(raw)
        conflict["barter_offers"]["lane_safe_filter_trade"]["command"] = "look"
        issues = validate_economy_document(conflict, self.world)
        self.assertTrue(any("reserved command" in issue for issue in issues))

    def test_economy_loader_rejects_boolean_costs_instead_of_coercing(self) -> None:
        raw = json.loads((ROOT / "data" / "economy.json").read_text(encoding="utf-8"))
        raw["barter_offers"]["lane_safe_filter_trade"]["cost_cred"] = True
        issues = validate_economy_document(raw, self.world)
        self.assertTrue(any("cost_cred" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
