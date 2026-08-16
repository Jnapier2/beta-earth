"""v0.51.0 explicit faction pledge and Sprawl 15 civic-duty contracts.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""
from __future__ import annotations

import unittest

from tests.support import PredictableRandom, load_test_catalog

from beta_earth.application.engine import GameEngine
from beta_earth.domain.clock import ManualClock
from beta_earth.domain.model import CreatureState, GameState
from beta_earth.domain.sovereignty import CANONICAL_FACTION_IDS
from beta_earth.domain.state_migrations import CURRENT_STATE_SCHEMA, migration_path


class PledgeAndCivicV0510Tests(unittest.TestCase):
    EXPECTED_RANKS = {
        "armageddon": "Chosen",
        "syndicate": "Thug",
        "final_bloodline": "Activist",
        "guardian_angel": "Test Subject",
        "redemption": "Human",
        "bounty_hunters": "Green",
        "security_uf": "Recruit",
    }

    def setUp(self) -> None:
        self.clock = ManualClock()
        self.engine = GameEngine(load_test_catalog(), self.clock, PredictableRandom())
        self.state = self.engine.new_game("Pledge Witness")

    def _make_eligible(self, faction_id: str) -> None:
        standing = self.state.foundation.sovereignty.factions[faction_id]
        standing.public_standing = 10
        standing.access_flags.add("candidate_contact")

    def _advance(self, seconds: float = 10.0) -> None:
        self.clock.advance(seconds)

    def test_content_and_new_state_contract(self) -> None:
        activation = self.engine.catalog.foundation_activation
        self.assertEqual(set(CANONICAL_FACTION_IDS), set(activation.pledge_routes))
        self.assertEqual(
            self.EXPECTED_RANKS,
            {key: value.recruit_title for key, value in activation.pledge_routes.items()},
        )
        self.assertEqual(20, CURRENT_STATE_SCHEMA)
        self.assertEqual(20, self.state.schema_version)
        self.assertEqual(3, self.state.foundation.schema_version)
        self.assertIsNone(self.state.foundation.sovereignty.allegiance_id)
        self.assertIsNone(self.state.foundation.sovereignty.pending_allegiance_id)
        mission = self.state.foundation.quests[activation.civic_mission.id]
        self.assertEqual("inactive", mission.status)
        self.assertEqual({"accept"}, mission.active_objective_ids)
        self.assertEqual({"supply", "watch", "relief"}, set(activation.civic_mission.plans))

    def test_pledge_requires_candidacy_and_confirmation(self) -> None:
        locked = self.engine.execute(self.state, "faction pledge security")
        self.assertFalse(locked.changed)
        self.assertIn("Pledge unavailable", locked.text)
        self.assertIsNone(self.state.foundation.sovereignty.pending_allegiance_id)

        self._make_eligible("security_uf")
        staged = self.engine.execute(self.state, "faction pledge security")
        self.assertTrue(staged.changed)
        self.assertIn("Action? [Y/N]", staged.text)
        self.assertEqual("security_uf", self.state.foundation.sovereignty.pending_allegiance_id)
        self.assertIsNone(self.state.foundation.sovereignty.allegiance_id)

        cancelled = self.engine.execute(self.state, "faction n")
        self.assertTrue(cancelled.changed)
        self.assertIsNone(self.state.foundation.sovereignty.pending_allegiance_id)
        self.assertIsNone(self.state.foundation.sovereignty.allegiance_id)

        self.engine.execute(self.state, "faction pledge security")
        confirmed = self.engine.execute(self.state, "faction y")
        standing = self.state.foundation.sovereignty.factions["security_uf"]
        self.assertTrue(confirmed.changed)
        self.assertEqual("security_uf", self.state.foundation.sovereignty.allegiance_id)
        self.assertEqual(1, standing.rank)
        self.assertEqual("Recruit", standing.rank_title)
        self.assertIn("member", standing.access_flags)
        self.assertIn("pledge:security_uf", self.state.foundation.sovereignty.pledge_receipt_ids)
        self.assertGreater(self.state.character.roundtime_until, self.clock.now())
        self.assertIn("guild enrollment", confirmed.text.casefold())
        projection = self.engine.client_state(self.state)["foundation"]
        self.assertTrue(projection["membership_granted"])
        self.assertFalse(projection.get("guild_membership_granted", False))

    def test_all_faction_entry_ranks_are_content_driven(self) -> None:
        for faction_id, expected_rank in self.EXPECTED_RANKS.items():
            state = self.engine.new_game(f"{faction_id.replace(chr(95), chr(32)).title()} Witness")
            standing = state.foundation.sovereignty.factions[faction_id]
            standing.public_standing = 10
            standing.access_flags.add("candidate_contact")
            definition = self.engine.catalog.creation.factions[faction_id]
            staged = self.engine.execute(state, f"faction pledge {definition.name}")
            self.assertTrue(staged.changed, faction_id)
            confirmed = self.engine.execute(state, "faction y")
            self.assertTrue(confirmed.changed, faction_id)
            self.assertEqual(expected_rank, standing.rank_title)
            self.assertEqual(faction_id, state.foundation.sovereignty.allegiance_id)

    def test_pledge_is_blocked_under_hostile_pressure(self) -> None:
        self._make_eligible("security_uf")
        definition_id = next(iter(self.engine.catalog.creatures))
        definition = self.engine.catalog.creatures[definition_id]
        self.state.creatures[self.state.character.room_id] = [
            CreatureState("pledge:pressure", definition_id, definition.max_health)
        ]
        blocked = self.engine.execute(self.state, "faction pledge security")
        self.assertFalse(blocked.changed)
        self.assertIn("Hostile pressure", blocked.text)

    def test_civic_chain_applies_one_bounded_receipt(self) -> None:
        self._make_eligible("security_uf")
        self.engine.execute(self.state, "faction pledge security")
        self.engine.execute(self.state, "faction y")
        self._advance()
        territory = self.state.foundation.territories["sprawl_15"]
        before = territory.to_dict()
        trust_before = self.state.foundation.sovereignty.local_trust.get("sprawl_15", 0)
        standing_before = self.state.foundation.sovereignty.factions["security_uf"].public_standing
        insight_before = self.state.character.experience.field_pool

        status = self.engine.execute(self.state, "civic status")
        self.assertFalse(status.changed)
        for command in (
            "civic accept",
            "civic inspect",
            "civic plan relief",
            "civic execute",
            "civic close",
        ):
            result = self.engine.execute(self.state, command)
            self.assertTrue(result.changed, command)
            self._advance()

        mission = self.state.foundation.quests["sprawl_15_first_civic_duty"]
        self.assertEqual("completed", mission.status)
        self.assertEqual("relief", mission.selected_resolution_id)
        self.assertIn("first_civic_duty_closed", territory.world_modifiers)
        self.assertEqual(before["supply"] + 2, territory.supply)
        self.assertEqual(before["prosperity"] + 2, territory.prosperity)
        self.assertEqual(before["tension"] - 7, territory.tension)
        self.assertEqual(trust_before + 4, self.state.foundation.sovereignty.local_trust["sprawl_15"])
        self.assertEqual(
            standing_before + 2,
            self.state.foundation.sovereignty.factions["security_uf"].public_standing,
        )
        self.assertEqual(insight_before + 6, self.state.character.experience.field_pool)
        self.assertIsNone(territory.owner_id)
        self.assertEqual(0, territory.population)
        repeated = self.engine.execute(self.state, "civic accept")
        self.assertFalse(repeated.changed)
        self.assertIn("already closed", repeated.text)

    def test_civic_execution_requires_sprawl_and_no_hostile_pressure(self) -> None:
        for command in ("civic accept", "civic inspect", "civic plan supply"):
            self.engine.execute(self.state, command)
            self._advance()
        definition_id = next(iter(self.engine.catalog.creatures))
        definition = self.engine.catalog.creatures[definition_id]
        self.state.creatures[self.state.character.room_id] = [
            CreatureState("civic:pressure", definition_id, definition.max_health)
        ]
        blocked = self.engine.execute(self.state, "civic execute")
        self.assertFalse(blocked.changed)
        self.assertIn("Hostile pressure", blocked.text)
        self.state.creatures.clear()

        outside = next(
            room_id
            for room_id, room in self.engine.catalog.rooms.items()
            if "sprawl 15" not in room.title.casefold()
        )
        self.state.character.room_id = outside
        wrong_place = self.engine.execute(self.state, "civic execute")
        self.assertFalse(wrong_place.changed)
        self.assertIn("Return to a Sprawl 15", wrong_place.text)

    def test_schema_19_migrates_and_round_trips(self) -> None:
        raw = self.state.to_dict()
        raw["schema_version"] = 19
        raw["foundation"]["schema_version"] = 2
        sovereignty = raw["foundation"]["sovereignty"]
        sovereignty.pop("pending_allegiance_id", None)
        sovereignty.pop("allegiance_confirmed_turn", None)
        sovereignty.pop("pledge_receipt_ids", None)
        for standing in sovereignty["factions"].values():
            standing.pop("rank_title", None)
        upgraded = GameState.from_dict(raw)
        self.assertEqual(20, upgraded.schema_version)
        self.assertEqual(3, upgraded.foundation.schema_version)
        self.assertIsNone(upgraded.foundation.sovereignty.pending_allegiance_id)
        self.assertTrue(
            all(
                standing.rank_title == "Unranked"
                for standing in upgraded.foundation.sovereignty.factions.values()
            )
        )
        self.assertEqual(
            ["v19_to_v20_activate_pledge_and_civic_duty"],
            [step.name for step in migration_path(19)],
        )
        restored = GameState.from_dict(upgraded.to_dict())
        self.assertEqual(upgraded.to_dict(), restored.to_dict())


if __name__ == "__main__":
    unittest.main()
