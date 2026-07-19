from __future__ import annotations

import unittest

from beta_earth.domain.models import PlayerState, SetupStage
from beta_earth.domain.rules import available_actions, validate_action_surface
from beta_earth.infrastructure.economy_loader import JsonEconomyRepository
from beta_earth.infrastructure.quest_loader import JsonQuestRepository
from beta_earth.infrastructure.world_loader import JsonWorldRepository, validate_world_document

from .support import ROOT


class DomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = JsonWorldRepository(ROOT / "data" / "world.json").load()
        self.economy = JsonEconomyRepository(ROOT / "data" / "economy.json").load(self.world)
        self.quests = JsonQuestRepository(ROOT / "data" / "quests.json").load(self.world, self.economy)

    def test_world_has_valid_cross_references(self) -> None:
        self.assertEqual(self.world.start_room, "caroline_house")
        self.assertGreaterEqual(len(self.world.rooms), 6)
        for room in self.world.rooms.values():
            for target in room.exits.values():
                self.assertIn(target, self.world.rooms)

    def test_initial_action_surface_is_nonempty_unique_and_ordered(self) -> None:
        state = PlayerState("tester", "Tester", SetupStage.IDENTITY, self.world.start_room)
        actions = available_actions(state, self.world, self.quests, self.economy)
        self.assertEqual(actions[0].command, "gender female")
        self.assertEqual(validate_action_surface(actions), ())
        self.assertEqual([action.shortcut for action in actions], [1, 2, 3, 4])

    def test_world_validator_rejects_missing_exit_target(self) -> None:
        raw = {
            "schema_version": "1.0",
            "title": "Broken",
            "start_room": "a",
            "rooms": {
                "a": {
                    "name": "A",
                    "zone": "Z",
                    "description": "D",
                    "canon_refs": ["Gameplay adaptation test"],
                    "exits": {"east": "missing"},
                }
            },
        }
        issues = validate_world_document(raw)
        self.assertTrue(any("missing room" in issue for issue in issues))

    def test_world_validator_rejects_unknown_keys_and_reserved_commands(self) -> None:
        raw = {
            "schema_version": "1.0",
            "title": "Broken",
            "start_room": "a",
            "unexpected": True,
            "rooms": {
                "a": {
                    "name": "A",
                    "zone": "Z",
                    "description": "D",
                    "canon_refs": ["Gameplay adaptation test"],
                    "interactions": [
                        {
                            "id": "bad",
                            "label": "Bad",
                            "command": "look",
                            "description": "Bad",
                            "message": "Bad",
                        }
                    ],
                }
            },
        }
        issues = validate_world_document(raw)
        self.assertTrue(any("unknown key" in issue for issue in issues))
        self.assertTrue(any("reserved command" in issue for issue in issues))
    def test_world_validator_rejects_type_coercion_inputs(self) -> None:
        raw = {
            "schema_version": "1.0",
            "title": 123,
            "start_room": "a",
            "rooms": {
                "a": {
                    "name": "A",
                    "zone": "Z",
                    "description": "D",
                    "canon_refs": ["Gameplay adaptation test"],
                    "exits": {"east": ["not", "a", "room"]},
                    "interactions": [
                        {
                            "id": "typed",
                            "label": "Typed",
                            "command": "typed command",
                            "description": "D",
                            "message": "M",
                            "once": "false",
                        }
                    ],
                }
            },
        }
        issues = validate_world_document(raw)
        self.assertTrue(any("world title" in issue for issue in issues))
        self.assertTrue(any("target must be a non-empty string" in issue for issue in issues))
        self.assertTrue(any("once must be a boolean" in issue for issue in issues))

