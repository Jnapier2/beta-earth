"""v0.51.0 live sovereignty, party, quest, and territory contracts.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""
from __future__ import annotations

import unittest

from tests.support import PredictableRandom, load_test_catalog

from beta_earth.application.engine import GameEngine
from beta_earth.domain.clock import ManualClock
from beta_earth.domain.model import CompanionProgressState, CreatureState, GameState
from beta_earth.domain.sovereignty import CANONICAL_FACTION_IDS
from beta_earth.domain.state_migrations import migration_path


class FoundationActivationV0500Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock()
        self.engine = GameEngine(load_test_catalog(), self.clock, PredictableRandom())
        self.state = self.engine.new_game("Foundation Witness")

    def test_new_game_seeds_live_foundations_without_claiming_membership(self) -> None:
        foundation = self.state.foundation
        self.assertEqual(3, foundation.schema_version)
        self.assertEqual(set(CANONICAL_FACTION_IDS), set(foundation.sovereignty.factions))
        self.assertIsNone(foundation.sovereignty.allegiance_id)
        territory = foundation.territories["sprawl_15"]
        self.assertIsNone(territory.owner_id)
        self.assertEqual(0, territory.population)
        self.assertIn("gameplay_interpretation", territory.world_modifiers)
        projection = self.engine.client_state(self.state)["foundation"]
        self.assertEqual("sprawl_15_pledge_civic_loop", projection["activation"])
        self.assertFalse(projection["membership_granted"])

    def test_authored_records_apply_once_without_silent_enlistment(self) -> None:
        self.state.story.records.update(
            {
                "route_security_selected",
                "security_candidacy_accepted",
                "medicine_delivered_unowned",
            }
        )
        self.engine.reconcile_state(self.state)
        first = self.engine.execute(self.state, "sovereignty")
        standing = self.state.foundation.sovereignty.factions["security_uf"]
        territory = self.state.foundation.territories["sprawl_15"]
        self.assertFalse(first.changed)
        self.assertEqual(16, standing.public_standing)
        self.assertIn("route_observed", standing.access_flags)
        self.assertIn("candidate_contact", standing.access_flags)
        self.assertIsNone(self.state.foundation.sovereignty.allegiance_id)
        self.assertEqual(43, territory.supply)
        self.assertEqual(3, len(self.state.foundation.applied_story_record_ids))

        second = self.engine.execute(self.state, "faction security")
        self.assertFalse(second.changed)
        self.assertEqual(16, standing.public_standing)
        self.assertEqual(43, territory.supply)
        self.assertIn("candidacy never silently enlists", second.text.casefold())

    def test_party_contract_tracks_sol_and_bounded_story_details(self) -> None:
        self.state.character.companion_id = "sol"
        self.state.character.companion_progress["sol"] = CompanionProgressState(
            order="guard"
        )
        self.engine.reconcile_state(self.state)
        self.engine.execute(self.state, "party status")
        party = self.state.foundation.party
        self.assertEqual("defensive", party.formation)
        self.assertEqual(["player", "sol"], party.member_ids)
        self.assertEqual({"sol": "player"}, party.protection_assignments)

        self.state.flags.update(
            {
                "field_cohort_detail_active",
                "field_cohort_formation_offensive",
                "field_cohort_tokens_verified",
            }
        )
        self.engine.reconcile_state(self.state)
        result = self.engine.execute(self.state, "party status")
        party = self.state.foundation.party
        self.assertFalse(result.changed)
        self.assertEqual("offensive", party.formation)
        self.assertEqual(
            ["neutral_cohort", "player", "sera_vann"], party.member_ids
        )
        self.assertEqual("player", party.commander_id)
        self.assertIn("route_tokens_verified", party.intelligence_reports)
        self.assertIn("Foundation party state", result.text)

        self.state.flags.add("field_cohort_detail_complete")
        self.engine.reconcile_state(self.state)
        self.engine.execute(self.state, "party status")
        self.assertEqual(["player", "sol"], self.state.foundation.party.member_ids)

    def test_active_story_is_mirrored_not_replaced(self) -> None:
        quest_id = self.state.story.active_quest_id
        stage_id = self.state.story.active_stage_id
        self.assertIsNotNone(quest_id)
        assert quest_id is not None and stage_id is not None
        machine = self.state.foundation.quests[quest_id]
        self.assertEqual("active", machine.status)
        self.assertEqual({f"stage:{stage_id}"}, machine.active_objective_ids)
        self.assertEqual(quest_id, self.state.story.active_quest_id)
        self.assertEqual(stage_id, self.state.story.active_stage_id)

    def test_territory_maintenance_is_bounded_and_blocked_under_pressure(self) -> None:
        territory = self.state.foundation.territories["sprawl_15"]
        before = territory.supply
        first = self.engine.execute(self.state, "territory support supply")
        self.assertTrue(first.changed)
        self.assertGreater(territory.supply, before)
        self.assertGreater(territory.maintenance_ready_turns["supply"], self.state.turn)
        self.clock.advance(5)
        second = self.engine.execute(self.state, "territory support supply")
        self.assertFalse(second.changed)
        self.assertIn("becomes available", second.text)

        pressured = self.engine.new_game("Pressure Witness")
        definition_id = next(iter(self.engine.catalog.creatures))
        definition = self.engine.catalog.creatures[definition_id]
        pressured.creatures[pressured.character.room_id] = [
            CreatureState("test:pressure", definition_id, definition.max_health)
        ]
        blocked = self.engine.execute(pressured, "territory support defense")
        self.assertFalse(blocked.changed)
        self.assertIn("Hostile pressure", blocked.text)

    def test_schema_18_security_alias_migrates_to_security_uf(self) -> None:
        raw = self.state.to_dict()
        raw["schema_version"] = 18
        raw["foundation"]["schema_version"] = 1
        raw["foundation"].pop("applied_story_record_ids", None)
        raw["foundation"]["sovereignty"]["allegiance_id"] = "security"
        raw["foundation"]["sovereignty"]["previous_affiliations"] = ["security"]
        factions = raw["foundation"]["sovereignty"]["factions"]
        factions["security"] = factions.pop("security_uf")
        for territory in raw["foundation"]["territories"].values():
            territory.pop("maintenance_ready_turns", None)
        upgraded = GameState.from_dict(raw)
        self.assertEqual(20, upgraded.schema_version)
        self.assertEqual("security_uf", upgraded.foundation.sovereignty.allegiance_id)
        self.assertIn("security_uf", upgraded.foundation.sovereignty.previous_affiliations)
        self.assertIn("security_uf", upgraded.foundation.sovereignty.factions)
        self.assertNotIn("security", upgraded.foundation.sovereignty.factions)
        self.assertEqual(
            ["v18_to_v19_activate_foundations", "v19_to_v20_activate_pledge_and_civic_duty"],
            [step.name for step in migration_path(18)],
        )


if __name__ == "__main__":
    unittest.main()
