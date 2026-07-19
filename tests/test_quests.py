from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from beta_earth.application.service import InvalidCommand
from beta_earth.domain.models import Interaction, PlayerState, QuestLog, SetupStage
from beta_earth.domain.quests import QuestStatus, build_quest_journal, shortest_route
from beta_earth.infrastructure.economy_loader import JsonEconomyRepository
from beta_earth.infrastructure.quest_loader import JsonQuestRepository, validate_quest_document
from beta_earth.infrastructure.world_loader import JsonWorldRepository

from .support import ROOT, build_test_service, start_active


class QuestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_test_service(Path(self.temp.name) / "players")
        self.world = JsonWorldRepository(ROOT / "data" / "world.json").load()
        self.economy = JsonEconomyRepository(ROOT / "data" / "economy.json").load(self.world)
        self.catalog = JsonQuestRepository(ROOT / "data" / "quests.json").load(self.world, self.economy)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_first_quest_is_gated_by_talking_to_caroline(self) -> None:
        active = start_active(self.service)
        self.assertNotIn("accept route mission", [action.command for action in active.actions])
        met = self.service.execute("player", "talk Caroline", expected_revision=active.state.revision)
        commands = [action.command for action in met.actions]
        self.assertIn("accept route mission", commands)
        self.assertEqual(commands[0], "accept route mission")

    def test_complete_route_mission_end_to_end(self) -> None:
        snapshot = start_active(self.service)
        sequence = (
            "talk Caroline",
            "accept route mission",
            "go east",
            "listen threshold",
            "go east",
            "go south",
            "mark return route",
            "go north",
            "go west",
            "go west",
            "report route to Caroline",
        )
        for command in sequence:
            snapshot = self.service.execute("player", command, expected_revision=snapshot.state.revision)
        self.assertIn("caroline_route_reading", snapshot.state.quest_log.completed)
        self.assertIn("trusted_by_caroline", snapshot.state.flags)
        self.assertIn("route_tracer_calibrated", snapshot.state.flags)
        self.assertIn("debrief lane with Caroline", [action.command for action in snapshot.actions])
        self.assertEqual(snapshot.quest_journal.active, ())
        self.assertEqual(snapshot.quest_journal.completed[0].status, QuestStatus.COMPLETED)

    def test_journal_tracer_promotes_the_next_real_action(self) -> None:
        snapshot = start_active(self.service)
        snapshot = self.service.execute("player", "talk Caroline", expected_revision=snapshot.state.revision)
        snapshot = self.service.execute("player", "accept route mission", expected_revision=snapshot.state.revision)
        self.assertEqual(snapshot.actions[0].command, "go east")
        self.assertEqual(snapshot.actions[0].mission_id, "caroline_route_reading")
        entry = snapshot.quest_journal.active[0]
        self.assertEqual(entry.tracer.recommended_command, "go east")
        self.assertEqual(entry.tracer.route[0].room_id, "front_threshold")

    def test_turn_in_is_not_available_before_objectives_complete(self) -> None:
        snapshot = start_active(self.service)
        snapshot = self.service.execute("player", "talk Caroline", expected_revision=snapshot.state.revision)
        snapshot = self.service.execute("player", "accept route mission", expected_revision=snapshot.state.revision)
        with self.assertRaises(InvalidCommand):
            self.service.execute("player", "report route to Caroline", expected_revision=snapshot.state.revision)

    def test_shortest_route_is_deterministic(self) -> None:
        route = shortest_route(self.world, "dead_tree_lane", "caroline_house")
        self.assertEqual([step.direction for step in route], ["north", "west", "west"])

    def test_journal_ignores_unknown_legacy_quest_without_crashing(self) -> None:
        state = PlayerState(
            "legacy",
            "Legacy",
            SetupStage.ACTIVE,
            "caroline_house",
            visited_rooms=("caroline_house",),
            quest_log=QuestLog(active=("removed_quest",)),
        )
        journal = build_quest_journal(state, self.world, self.catalog)
        self.assertEqual(journal.active, ())

    def test_quest_validator_rejects_flag_objective_without_world_grant(self) -> None:
        broken = {
            "schema_version": "1.0",
            "quests": {
                "broken": {
                    "title": "Broken",
                    "summary": "Broken quest",
                    "giver": "Nobody",
                    "tier": 1,
                    "offer_room": "caroline_house",
                    "requires_flags": [],
                    "accept_label": "Accept",
                    "accept_command": "accept broken",
                    "accept_message": "Accepted",
                    "objectives": [
                        {
                            "id": "missing",
                            "label": "Missing",
                            "description": "Missing",
                            "kind": "flag",
                            "target": "never_granted",
                            "room_id": "caroline_house",
                        }
                    ],
                    "turn_in_room": "caroline_house",
                    "turn_in_label": "Turn in",
                    "turn_in_command": "turn in broken",
                    "turn_in_message": "Done",
                    "reward_flags": [],
                    "canon_refs": ["Gameplay adaptation test"],
                }
            },
        }
        issues = validate_quest_document(broken, self.world)
        self.assertTrue(any("not granted" in issue for issue in issues))

    def test_quest_validator_rejects_self_reward_dependency(self) -> None:
        raw = json.loads((ROOT / "data" / "quests.json").read_text(encoding="utf-8"))
        quest = raw["quests"]["caroline_route_reading"]
        quest["requires_flags"] = ["completion_only"]
        quest["reward_flags"] = ["completion_only"]
        issues = validate_quest_document(raw, self.world)
        self.assertTrue(any("own completion reward" in issue for issue in issues))

    def test_quest_validator_rejects_unavoidable_reward_dependency_cycle(self) -> None:
        source = json.loads((ROOT / "data" / "quests.json").read_text(encoding="utf-8"))["quests"]["caroline_route_reading"]
        first = copy.deepcopy(source)
        first.update(
            {
                "requires_flags": ["reward_b"],
                "reward_flags": ["reward_a"],
                "accept_command": "accept dependency a",
                "turn_in_command": "complete dependency a",
            }
        )
        second = copy.deepcopy(source)
        second.update(
            {
                "requires_flags": ["reward_a"],
                "reward_flags": ["reward_b"],
                "accept_command": "accept dependency b",
                "turn_in_command": "complete dependency b",
            }
        )
        raw = {"schema_version": "1.0", "quests": {"dependency_a": first, "dependency_b": second}}
        issues = validate_quest_document(raw, self.world)
        self.assertTrue(any("dependency cycle" in issue for issue in issues))

    def test_cross_catalog_validation_rejects_ungrantable_world_requirement(self) -> None:
        room = self.world.rooms["caroline_house"]
        broken_interaction = Interaction(
            id="requires_missing_flag",
            label="Impossible interaction",
            command="attempt impossible interaction",
            description="Test an impossible requirement.",
            message="This should never load.",
            requires_flags=("never_granted_anywhere",),
        )
        broken_room = replace(room, interactions=(*room.interactions, broken_interaction))
        broken_world = replace(self.world, rooms={**self.world.rooms, room.id: broken_room})
        raw = json.loads((ROOT / "data" / "quests.json").read_text(encoding="utf-8"))
        issues = validate_quest_document(raw, broken_world)
        self.assertTrue(any("no current content can grant" in issue for issue in issues))

    def test_quest_validator_rejects_type_coercion_inputs(self) -> None:
        broken = {
            "schema_version": "1.0",
            "quests": {
                "broken": {
                    "title": 123,
                    "summary": "Broken quest",
                    "giver": "Nobody",
                    "tier": 1,
                    "offer_room": "caroline_house",
                    "requires_flags": {},
                    "accept_label": "Accept",
                    "accept_command": "accept broken",
                    "accept_message": "Accepted",
                    "objectives": [
                        {
                            "id": "visit",
                            "label": "Visit",
                            "description": "Visit",
                            "kind": "visit_room",
                            "target": "caroline_house",
                            "room_id": "caroline_house",
                        }
                    ],
                    "turn_in_room": "caroline_house",
                    "turn_in_label": "Turn in",
                    "turn_in_command": "turn in broken",
                    "turn_in_message": "Done",
                    "reward_summary": "None",
                    "reward_flags": [],
                    "canon_refs": ["Gameplay adaptation test"],
                }
            },
        }
        issues = validate_quest_document(broken, self.world)
        self.assertTrue(any("title must be a non-empty string" in issue for issue in issues))
        self.assertTrue(any("requires_flags must be an array" in issue for issue in issues))

