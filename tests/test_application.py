from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from beta_earth.application.ports import RevisionConflict
from beta_earth.application.service import GameError, GameService, InvalidCommand

from beta_earth.domain.models import PlayerState
from beta_earth.infrastructure.economy_loader import JsonEconomyRepository
from beta_earth.infrastructure.quest_loader import JsonQuestRepository
from beta_earth.infrastructure.world_loader import JsonWorldRepository

from .support import ROOT, FixedRandom, build_test_service


class ApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name) / "players"
        self.service = build_test_service(self.state_dir)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_character_creation_and_first_room_actions(self) -> None:
        initial = self.service.get_snapshot("player")
        identity = self.service.execute("player", "gender female", expected_revision=initial.state.revision)
        self.assertEqual(identity.actions[0].command, "rollstats")
        stats = self.service.execute("player", "rollstats", expected_revision=identity.state.revision)
        self.assertEqual(stats.state.stats.strength, 14)
        ready = self.service.execute("player", "begin", expected_revision=stats.state.revision)
        commands = [action.command for action in ready.actions]
        self.assertIn("talk Caroline", commands)
        self.assertIn("go east", commands)
        self.assertEqual(ready.room.id, "caroline_house")
        self.assertIn("caroline_house", ready.state.visited_rooms)

    def test_only_visible_current_commands_execute(self) -> None:
        initial = self.service.get_snapshot("player")
        with self.assertRaises(InvalidCommand):
            self.service.execute("player", "go east", expected_revision=initial.state.revision)

    def test_revision_conflict_prevents_duplicate_execution(self) -> None:
        initial = self.service.get_snapshot("player")
        self.service.execute("player", "gender female", expected_revision=initial.state.revision)
        with self.assertRaises(RevisionConflict):
            self.service.execute("player", "gender male", expected_revision=initial.state.revision)

    def test_state_persists_across_service_instances(self) -> None:
        initial = self.service.get_snapshot("player")
        changed = self.service.execute("player", "gender nonbinary", expected_revision=initial.state.revision)
        second = build_test_service(self.state_dir)
        loaded = second.get_snapshot("player")
        self.assertEqual(loaded.state.identity, "nonbinary")
        self.assertEqual(loaded.state.revision, changed.state.revision)

    def test_command_aliases_still_require_a_visible_action(self) -> None:
        initial = self.service.get_snapshot("player")
        with self.assertRaises(InvalidCommand):
            self.service.execute("player", "e", expected_revision=initial.state.revision)


    def test_concurrent_first_profile_creation_reloads_the_winner(self) -> None:
        class RacingRepository:
            def __init__(self) -> None:
                self.winner: PlayerState | None = None

            def load(self, player_id: str) -> PlayerState | None:
                return self.winner

            def save(self, state: PlayerState, expected_revision: int) -> PlayerState:
                self.winner = replace(state, revision=0)
                raise RevisionConflict("simulated competing first write")

            def delete(self, player_id: str, expected_revision: int | None = None) -> None:
                self.winner = None

        repository = RacingRepository()
        service = GameService(
            JsonWorldRepository(ROOT / "data" / "world.json"),
            JsonEconomyRepository(ROOT / "data" / "economy.json"),
            JsonQuestRepository(ROOT / "data" / "quests.json"),
            repository,
            FixedRandom(),
        )
        snapshot = service.get_snapshot("racing-player")
        self.assertEqual(snapshot.state.revision, 0)
        self.assertEqual(snapshot.actions[0].command, "gender female")

    def test_stale_reset_cannot_delete_newer_state(self) -> None:
        initial = self.service.get_snapshot("reset-player")
        changed = self.service.execute(
            "reset-player", "gender female", expected_revision=initial.state.revision
        )
        with self.assertRaises(RevisionConflict):
            self.service.reset("reset-player", expected_revision=initial.state.revision)
        preserved = self.service.get_snapshot("reset-player")
        self.assertEqual(preserved.state.revision, changed.state.revision)
        self.assertEqual(preserved.state.identity, "female")

    def test_current_revision_reset_starts_a_fresh_profile(self) -> None:
        initial = self.service.get_snapshot("fresh-reset")
        changed = self.service.execute(
            "fresh-reset", "gender nonbinary", expected_revision=initial.state.revision
        )
        reset = self.service.reset("fresh-reset", expected_revision=changed.state.revision)
        self.assertEqual(reset.state.stage.value, "identity")
        self.assertIsNone(reset.state.identity)
        self.assertEqual(reset.state.revision, changed.state.revision + 1)

    def test_unknown_quest_reference_fails_explicitly(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "player.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "player_id": "player",
                    "display_name": "Player",
                    "stage": "active",
                    "room_id": "caroline_house",
                    "identity": "female",
                    "stats": {"strength": 10, "agility": 10, "intellect": 10, "spirit": 10, "resilience": 10},
                    "flags": [],
                    "visited_rooms": ["caroline_house"],
                    "quest_log": {"active": ["missing_quest"], "completed": []},
                    "last_message": "Broken reference",
                    "revision": 0,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(GameError):
            self.service.get_snapshot("player")

