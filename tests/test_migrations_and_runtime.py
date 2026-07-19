from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from beta_earth.application.ports import PlayerDataError, RevisionConflict
from beta_earth.infrastructure.json_store import JsonPlayerRepository

from .support import build_test_service


def schema2_save(player: str, *, completed: bool) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "player_id": player,
        "display_name": player,
        "stage": "active",
        "room_id": "caroline_house",
        "identity": "female",
        "stats": {"strength": 10, "agility": 10, "intellect": 10, "spirit": 10, "resilience": 10},
        "flags": ["trusted_by_caroline"] if completed else [],
        "visited_rooms": ["caroline_house"],
        "quest_log": {
            "active": [],
            "completed": ["caroline_route_reading"] if completed else [],
        },
        "last_message": "Legacy schema 2 save",
        "revision": 9,
    }


class MigrationAndRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name) / "players"
        self.state_dir.mkdir(parents=True)
        self.repository = JsonPlayerRepository(self.state_dir)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_schema2_completed_mission_migrates_reward_once_with_backup_and_journal(self) -> None:
        path = self.state_dir / "LegacyWinner.json"
        path.write_text(json.dumps(schema2_save("LegacyWinner", completed=True)), encoding="utf-8")
        first = self.repository.load("LegacyWinner")
        self.assertEqual(first.cred, 5)
        self.assertEqual(first.inventory.quantity("route_tracer_calibration"), 1)
        self.assertEqual(first.revision, 9)
        migrated = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["schema_version"], "4.0")
        self.assertEqual(migrated["cred"], 5)
        backups = list((self.state_dir / "_migration_backups").glob("LegacyWinner.schema-2.0.*.json"))
        self.assertEqual(len(backups), 1)
        journal_lines = (self.state_dir / "_migration_journal.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(journal_lines), 1)
        event = json.loads(journal_lines[0])
        self.assertEqual(event["from_schema"], "2.0")
        self.assertEqual(event["to_schema"], "4.0")

        second = self.repository.load("LegacyWinner")
        self.assertEqual(second.cred, 5)
        self.assertEqual(second.inventory.quantity("route_tracer_calibration"), 1)
        self.assertEqual(len(list((self.state_dir / "_migration_backups").glob("LegacyWinner.schema-*.json"))), 1)
        self.assertEqual(len((self.state_dir / "_migration_journal.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_schema2_incomplete_mission_does_not_receive_future_reward(self) -> None:
        path = self.state_dir / "LegacyNew.json"
        path.write_text(json.dumps(schema2_save("LegacyNew", completed=False)), encoding="utf-8")
        state = self.repository.load("LegacyNew")
        self.assertEqual(state.cred, 0)
        self.assertEqual(state.inventory.stacks, ())

    def test_schema3_alias_fields_migrate_without_loss(self) -> None:
        raw = schema2_save("AliasPlayer", completed=False)
        raw.update(
            {
                "schema_version": "3.0",
                "wallet_cred": 7,
                "inventory": {"route_tracer_calibration": 1},
                "transactions": ["lane_safe_filter_trade"],
            }
        )
        path = self.state_dir / "AliasPlayer.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        state = self.repository.load("AliasPlayer")
        self.assertEqual(state.cred, 7)
        self.assertEqual(state.inventory.quantity("route_tracer_calibration"), 1)
        self.assertEqual(state.completed_transactions, ("lane_safe_filter_trade",))

    def test_current_schema_inconsistency_is_rejected_not_repaired(self) -> None:
        raw = schema2_save("BrokenCurrent", completed=False)
        raw.update(
            {
                "schema_version": "4.0",
                "visited_rooms": [],
                "cred": 0,
                "inventory": [],
                "completed_transactions": [],
            }
        )
        (self.state_dir / "BrokenCurrent.json").write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(PlayerDataError):
            self.repository.load("BrokenCurrent")

    def test_oversized_save_is_rejected_before_json_decode(self) -> None:
        (self.state_dir / "Huge.json").write_bytes(b"{" + b"x" * (256 * 1024 + 1))
        with self.assertRaises(PlayerDataError):
            self.repository.load("Huge")

    def test_reset_revision_never_returns_to_an_old_value(self) -> None:
        service = build_test_service(self.state_dir)
        initial = service.get_snapshot("reset-aba")
        changed = service.execute("reset-aba", "gender female", expected_revision=initial.state.revision)
        reset = service.reset("reset-aba", expected_revision=changed.state.revision)
        self.assertGreater(reset.state.revision, changed.state.revision)
        with self.assertRaises(RevisionConflict):
            service.execute("reset-aba", "gender male", expected_revision=initial.state.revision)


if __name__ == "__main__":
    unittest.main()
