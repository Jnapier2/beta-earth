from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from beta_earth.application.ports import PlayerDataError
from beta_earth.domain.models import PlayerState, QuestLog, SetupStage
from beta_earth.infrastructure.json_store import JsonPlayerRepository


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name) / "players"
        self.repository = JsonPlayerRepository(self.state_dir)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_v010_save_loads_with_safe_defaults(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / "Legacy.json"
        path.write_text(
            json.dumps(
                {
                    "player_id": "Legacy",
                    "display_name": "Legacy",
                    "stage": "active",
                    "room_id": "sprawl_crossroads",
                    "identity": "unspecified",
                    "stats": {"strength": 10, "agility": 10, "intellect": 10, "spirit": 10, "resilience": 10},
                    "flags": ["met_caroline"],
                    "last_message": "Old save",
                    "revision": 7,
                }
            ),
            encoding="utf-8",
        )
        state = self.repository.load("Legacy")
        self.assertIsNotNone(state)
        self.assertEqual(state.quest_log, QuestLog())
        self.assertEqual(state.visited_rooms, ("sprawl_crossroads",))
        self.assertEqual(state.revision, 7)

    def test_new_save_writes_schema_and_quest_state(self) -> None:
        state = PlayerState(
            "player",
            "Player",
            SetupStage.ACTIVE,
            "caroline_house",
            identity="female",
            flags=("met_caroline",),
            visited_rooms=("caroline_house",),
            quest_log=QuestLog(active=("caroline_route_reading",)),
        )
        persisted = self.repository.save(state, expected_revision=-1)
        raw = json.loads((self.state_dir / "player.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], "4.0")
        self.assertEqual(raw["quest_log"]["active"], ["caroline_route_reading"])
        self.assertEqual(persisted.revision, 0)

    def test_corrupt_save_fails_explicitly(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "player.json").write_text("{not valid", encoding="utf-8")
        with self.assertRaises(PlayerDataError):
            self.repository.load("player")

    def test_unsupported_schema_is_not_silently_downgraded(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "player.json").write_text(
            json.dumps({"schema_version": "99.0", "stage": "identity", "room_id": "caroline_house"}),
            encoding="utf-8",
        )
        with self.assertRaises(PlayerDataError):
            self.repository.load("player")
    def test_bool_stat_is_rejected_instead_of_coerced(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "player.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "player_id": "player",
                    "display_name": "Player",
                    "stage": "identity",
                    "room_id": "caroline_house",
                    "stats": {"strength": True},
                    "flags": [],
                    "visited_rooms": [],
                    "quest_log": {"active": [], "completed": []},
                    "last_message": "",
                    "revision": 0,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(PlayerDataError):
            self.repository.load("player")


    def test_non_integer_revision_is_rejected_instead_of_coerced(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "player.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "player_id": "player",
                    "display_name": "Player",
                    "stage": "identity",
                    "room_id": "caroline_house",
                    "stats": {},
                    "flags": [],
                    "visited_rooms": [],
                    "quest_log": {"active": [], "completed": []},
                    "last_message": "",
                    "revision": 1.5,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(PlayerDataError):
            self.repository.load("player")

    def test_non_string_identity_fields_are_rejected(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "player.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "player_id": "player",
                    "display_name": ["Player"],
                    "stage": "identity",
                    "room_id": "caroline_house",
                    "stats": {},
                    "flags": [],
                    "visited_rooms": [],
                    "quest_log": {"active": [], "completed": []},
                    "last_message": "",
                    "revision": 0,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(PlayerDataError):
            self.repository.load("player")

