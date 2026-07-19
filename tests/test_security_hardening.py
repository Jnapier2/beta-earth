from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from beta_earth.application.ports import PlayerDataError
from beta_earth.domain.identity import canonical_player_id
from beta_earth.domain.models import Inventory, PlayerState, SetupStage
from beta_earth.infrastructure.economy_loader import EconomyValidationError, JsonEconomyRepository
from beta_earth.infrastructure.instance_guard import SingleInstanceGuard, _process_start_signature
from beta_earth.infrastructure.json_document import DEFAULT_MAX_DOCUMENT_BYTES
from beta_earth.infrastructure.json_store import JsonPlayerRepository
from beta_earth.infrastructure.quest_loader import JsonQuestRepository, QuestValidationError
from beta_earth.infrastructure.world_loader import JsonWorldRepository, WorldValidationError, validate_world_document
from beta_earth.timekeeping import USER_TIMEZONE, USER_TIMEZONE_SOURCE, user_now, utc_now

from .support import ROOT


class SecurityHardeningTests(unittest.TestCase):
    def test_inventory_mapping_rejects_ambiguous_quantities(self) -> None:
        for value in (True, "1", -1, 10_000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Inventory.from_mapping({"route_tracer_calibration": value})  # type: ignore[arg-type]
        self.assertEqual(Inventory.from_mapping({"unused": 0}).stacks, ())

    def test_profile_identity_is_canonical_and_windows_safe(self) -> None:
        unsafe = canonical_player_id(" A?B ")
        self.assertNotEqual(unsafe, "AB")
        self.assertEqual(unsafe, canonical_player_id(" A?B "))
        self.assertNotEqual(canonical_player_id("CON"), "Player_CON")
        self.assertNotEqual(canonical_player_id(".."), canonical_player_id("??"))
        self.assertEqual(canonical_player_id("Safe_Profile"), "Safe_Profile")
        with self.assertRaises(ValueError):
            PlayerState("A?B", "Player", SetupStage.IDENTITY, "caroline_house")

    def test_repository_rejects_lossy_profile_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = JsonPlayerRepository(Path(temp_dir))
            with self.assertRaises(PlayerDataError):
                repository.load("A?B")
            self.assertFalse(any(Path(temp_dir).glob("*.json")))

    def test_schema3_conflicting_aliases_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            payload = {
                "schema_version": "3.0",
                "player_id": "AliasConflict",
                "display_name": "Alias Conflict",
                "stage": "identity",
                "room_id": "caroline_house",
                "identity": None,
                "stats": {"strength": 10, "agility": 10, "intellect": 10, "spirit": 10, "resilience": 10},
                "flags": [],
                "visited_rooms": [],
                "quest_log": {"active": [], "completed": []},
                "cred": 5,
                "wallet_cred": 7,
                "inventory": [],
                "completed_transactions": [],
                "transactions": [],
                "last_message": "",
                "revision": 0,
            }
            (state_dir / "AliasConflict.json").write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(PlayerDataError):
                JsonPlayerRepository(state_dir).load("AliasConflict")

    def test_migration_writes_canonical_model_not_transitional_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            payload = {
                "schema_version": "2.0",
                "player_id": "CanonicalMigration",
                "display_name": "Canonical Migration",
                "stage": "active",
                "room_id": "caroline_house",
                "identity": "female",
                "stats": {"strength": 10, "agility": 10, "intellect": 10, "spirit": 10, "resilience": 10},
                "flags": [" trusted_by_caroline "],
                "visited_rooms": [" caroline_house "],
                "quest_log": {"active": [], "completed": [" caroline_route_reading "]},
                "last_message": "Legacy",
                "revision": 4,
            }
            path = state_dir / "CanonicalMigration.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            state = JsonPlayerRepository(state_dir).load("CanonicalMigration")
            self.assertIn("trusted_by_caroline", state.flags)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["flags"], ["trusted_by_caroline"])
            self.assertEqual(raw["visited_rooms"], ["caroline_house"])
            self.assertEqual(raw["quest_log"]["completed"], ["caroline_route_reading"])

    def test_current_display_name_is_rejected_instead_of_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            state = PlayerState("LongName", "Valid", SetupStage.IDENTITY, "caroline_house")
            repository = JsonPlayerRepository(state_dir)
            repository.save(state, expected_revision=-1)
            path = state_dir / "LongName.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["display_name"] = "X" * 41
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(PlayerDataError):
                repository.load("LongName")

    def test_catalog_readers_reject_oversized_documents_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            huge = Path(temp_dir) / "huge.json"
            huge.write_bytes(b"{" + b"x" * DEFAULT_MAX_DOCUMENT_BYTES)
            with self.assertRaises(WorldValidationError):
                JsonWorldRepository(huge).load()
            world = JsonWorldRepository(ROOT / "data" / "world.json").load()
            with self.assertRaises(EconomyValidationError):
                JsonEconomyRepository(huge).load(world)
            economy = JsonEconomyRepository(ROOT / "data" / "economy.json").load(world)
            with self.assertRaises(QuestValidationError):
                JsonQuestRepository(huge).load(world, economy)

    def test_world_validator_enforces_catalog_and_room_bounds(self) -> None:
        rooms = {
            f"room_{index}": {
                "name": "Room",
                "zone": "Zone",
                "description": "Description",
                "canon_refs": ["Canon"],
            }
            for index in range(257)
        }
        raw = {"schema_version": "1.0", "title": "Bounded", "start_room": "room_0", "rooms": rooms}
        issues = validate_world_document(raw)
        self.assertTrue(any("exceeds 256 rooms" in issue for issue in issues))

    @unittest.skipIf(os.name == "nt", "POSIX process signature test")
    def test_lockfile_records_process_start_signature_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            guard = SingleInstanceGuard(Path(temp_dir), name=f"Signature{id(self)}")
            self.assertTrue(guard.acquire())
            try:
                payload = json.loads((Path(temp_dir) / "runtime.lock").read_text(encoding="utf-8"))
                self.assertIn("process_start", payload)
                if Path(f"/proc/{os.getpid()}/stat").exists():
                    self.assertEqual(payload["process_start"], _process_start_signature(os.getpid()))
            finally:
                guard.release()

    def test_timekeeping_is_timezone_safe_and_aware(self) -> None:
        self.assertIsNotNone(USER_TIMEZONE)
        self.assertTrue(USER_TIMEZONE_SOURCE)
        self.assertIsNotNone(user_now().utcoffset())
        self.assertIsNotNone(utc_now().utcoffset())

    def test_browser_defers_online_refresh_while_command_is_active(self) -> None:
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("let pendingNetworkRefresh = false", script)
        self.assertIn("if (busy) {", script)
        self.assertIn("pendingNetworkRefresh = true", script)
        self.assertIn("flushPendingNetworkRefresh()", script)
        self.assertIn('didReadiness: document.querySelector("#did-readiness")', script)
        self.assertIn('didTier: document.querySelector("#did-tier")', script)
        self.assertNotIn('window.addEventListener("online", () => loadState(', script)


if __name__ == "__main__":
    unittest.main()
