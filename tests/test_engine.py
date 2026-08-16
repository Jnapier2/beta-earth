from __future__ import annotations

import unittest

from tests.support import (
    PredictableRandom,
    ScriptedRandom,
    load_additive_test_catalog,
    load_test_catalog,
)

from beta_earth.application.engine import GameEngine, normalize_player_name
from beta_earth.domain.clock import ManualClock
from beta_earth.domain.model import (
    STATE_SCHEMA_VERSION,
    CreatureState,
    GameState,
    ItemState,
    Stance,
    Wound,
)
from beta_earth.domain.progression import award_field_insight, pulse_experience


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock()
        self.engine = GameEngine(load_test_catalog(), self.clock, PredictableRandom())
        self.state = self.engine.new_game("Test Runner")

    def act(self, command: str, advance: float = 0.0):
        if advance:
            self.clock.advance(advance)
        return self.engine.execute(self.state, command)

    def test_new_game_is_equipped_and_in_start_room(self) -> None:
        self.assertEqual("intake_concourse", self.state.character.room_id)
        main_hand = self.engine._inventory_item(
            self.state, self.state.character.equipped["main_hand"]
        )
        body = self.engine._inventory_item(
            self.state, self.state.character.equipped["body"]
        )
        self.assertEqual("service_blade", main_hand.definition_id if main_hand else None)
        self.assertEqual("field_coat", body.definition_id if body else None)
        self.assertEqual(
            "spawn:item:intake-transit-token",
            self.state.room_items["intake_concourse"][0].instance_id,
        )
        self.assertEqual(
            "spawn:creature:drill-rust-mite",
            self.state.creatures["drill_gallery"][0].instance_id,
        )

    def test_name_normalization_and_validation(self) -> None:
        self.assertEqual(("river fox", "River Fox"), normalize_player_name(" River   Fox "))
        with self.assertRaises(ValueError):
            normalize_player_name("!")

    def test_movement_sets_roundtime_but_look_remains_available(self) -> None:
        moved = self.act("east")
        self.assertTrue(moved.changed)
        self.assertEqual("drill_gallery", self.state.character.room_id)
        blocked = self.act("attack mite")
        self.assertIn("still recovering", blocked.text)
        looked = self.act("look")
        self.assertIn("Practice Gallery", looked.text)

    def test_roundtime_expires_on_clock(self) -> None:
        self.act("east")
        self.assertIn("1 sec", self.act("roundtime").text)
        self.clock.advance(1.01)
        self.assertIn("ready", self.act("roundtime").text)

    def test_all_six_stances_are_reachable(self) -> None:
        for stance in Stance:
            self.clock.advance(2)
            result = self.act(f"stance {stance.value}")
            self.assertIn(stance.value, result.text)
            self.assertEqual(stance, self.state.character.stance)

    def test_target_then_attack_uses_default_target(self) -> None:
        self.act("east")
        self.clock.advance(2)
        targeted = self.act("target mite")
        self.assertIn("focus", targeted.text)
        result = self.act("attack")
        self.assertIn("Roll", result.text)
        self.assertTrue(result.changed)

    def test_combat_defeat_awards_field_experience_and_loot(self) -> None:
        self.act("east")
        for _ in range(4):
            self.clock.advance(10)
            result = self.act("attack mite")
            if "collapses" in result.text:
                break
        else:
            self.fail("predictable combat did not defeat the mite")
        self.assertEqual(18, self.state.character.experience.field_pool)
        self.assertEqual([], self.state.creatures["drill_gallery"])

    def test_experience_absorbs_on_pulse(self) -> None:
        self.state.character.experience.field_pool = 20
        self.clock.advance(15)
        result = self.act("look")
        self.assertIn("absorb 8 insight", result.text)
        self.assertEqual(8, self.state.character.experience.absorbed)
        self.assertEqual(12, self.state.character.experience.field_pool)

    def test_empty_experience_pulse_does_not_mutate_read_only_state(self) -> None:
        experience = self.state.character.experience
        before = experience.last_pulse_at
        self.clock.advance(15)
        result = self.engine.execute(self.state, "look")
        self.assertFalse(result.changed)
        self.assertEqual(before, experience.last_pulse_at)

    def test_first_award_after_idle_starts_a_fresh_absorption_window(self) -> None:
        experience = self.state.character.experience
        self.clock.advance(60 * 60)
        award_field_insight(experience, 20, self.clock.now())
        self.assertEqual(0, pulse_experience(experience, self.clock.now()))
        self.clock.advance(15)
        self.assertEqual(8, pulse_experience(experience, self.clock.now()))

    def test_offline_absorption_cap_cannot_be_replayed_immediately(self) -> None:
        experience = self.state.character.experience
        experience.field_pool = 10_000
        self.clock.advance(10 * 60 * 60)
        self.act("look")
        first = experience.absorbed
        self.act("look")
        self.assertEqual(1_920, first)
        self.assertEqual(first, experience.absorbed)

    def test_search_reveal_happens_once(self) -> None:
        for command in ("north", "east", "withdraw down"):
            result = self.act(command, advance=2)
            self.assertNotIn("cannot go", result.text)
        first = self.act("search", advance=3)
        self.assertIn("blue-black splinter", first.text)
        self.assertIn(
            "glass_splinter",
            [item.definition_id for item in self.state.room_items["service_tunnel"]],
        )
        second = self.act("search", advance=4)
        self.assertIn("nothing new", second.text)
        self.assertEqual(
            1,
            sum(
                item.definition_id == "glass_splinter"
                for item in self.state.room_items["service_tunnel"]
            ),
        )

    def test_inventory_equipment_and_drop_loop(self) -> None:
        self.act("get token")
        self.clock.advance(2)
        self.assertIn("transit token", self.act("inventory").text)
        dropped = self.act("drop token")
        self.assertIn("set down", dropped.text)
        self.assertNotIn(
            "transit_token",
            [item.definition_id for item in self.state.character.inventory],
        )

    def test_duplicate_items_have_independent_identity_and_ordinals(self) -> None:
        self.state.character.inventory.append(
            ItemState("test:second-service-blade", "service_blade")
        )
        result = self.act("drop second blade")
        self.assertIn("set down", result.text)
        self.assertIn(
            "starter:test runner:service-blade",
            self.state.character.equipped.values(),
        )
        self.assertTrue(
            any(
                item.instance_id == "test:second-service-blade"
                for item in self.state.room_items["intake_concourse"]
            )
        )

    def test_defeat_persists_incapacitation_before_delayed_recovery(self) -> None:
        rng = ScriptedRandom([50, 100, 100, 100, 100, 100, 5])
        engine = GameEngine(load_test_catalog(), self.clock, rng)
        state = engine.new_game("Fragile Runner")
        state.character.room_id = "drill_gallery"
        state.character.health = 1
        state.character.experience.field_pool = 20
        result = engine.execute(state, "attack mite")
        self.assertIn("remain present at the scene", result.text)
        self.assertEqual("drill_gallery", state.character.room_id)
        self.assertEqual(1, state.character.health)
        self.assertEqual(20, state.character.experience.field_pool)
        self.assertIsNotNone(state.incapacitation)
        self.assertIn("Incapacitated recovery: 10 sec.", result.text)
        blocked = engine.execute(state, "get mite")
        self.assertIn("incapacitated", blocked.text)
        signaled = engine.execute(state, "signal")
        self.assertIn("request is recorded", signaled.text)
        self.assertTrue(state.incapacitation.help_requested)
        early = engine.execute(state, "recover")
        self.assertIn("not available", early.text)
        self.clock.advance(10)
        recovered = engine.execute(state, "recover")
        self.assertIn("recovery beacon", recovered.text)
        self.assertEqual("intake_concourse", state.character.room_id)
        self.assertGreater(state.character.health, 1)
        self.assertLess(state.character.experience.field_pool, 20)
        self.assertIsNone(state.incapacitation)
        self.assertIn("Roundtime: 5 sec.", recovered.text)

    def test_state_round_trip(self) -> None:
        self.act("get token")
        restored = GameState.from_dict(self.state.to_dict())
        self.assertEqual(self.state.to_dict(), restored.to_dict())

    def test_schema_two_state_is_upgraded_and_future_schema_is_rejected(self) -> None:
        legacy = self.state.to_dict()
        legacy["schema_version"] = 2
        legacy.pop("incapacitation")
        upgraded = GameState.from_dict(legacy)
        self.assertEqual(STATE_SCHEMA_VERSION, upgraded.schema_version)
        self.assertIsNone(upgraded.incapacitation)

        future = self.state.to_dict()
        future["schema_version"] = STATE_SCHEMA_VERSION + 1
        with self.assertRaisesRegex(ValueError, "unsupported state schema"):
            GameState.from_dict(future)

    def test_older_content_version_is_additively_reconciled(self) -> None:
        upgraded_engine = GameEngine(
            load_additive_test_catalog(), self.clock, PredictableRandom()
        )
        self.state.room_items.pop("relay_overlook")
        self.state.creatures.pop("relay_overlook")
        events = upgraded_engine.reconcile_state(self.state)
        self.assertEqual("0.51.2", self.state.content_version)
        self.assertIn("relay_overlook", self.state.room_items)
        self.assertEqual(
            ["spawn:item:relay-upgrade-token"],
            [item.instance_id for item in self.state.room_items["relay_overlook"]],
        )
        self.assertEqual(
            ["spawn:creature:relay-upgrade-mite"],
            [
                creature.instance_id
                for creature in self.state.creatures["relay_overlook"]
            ],
        )
        self.assertEqual("content.additive_reconciliation", events[0].kind)

    def test_undeclared_additive_content_version_is_rejected(self) -> None:
        upgraded_engine = GameEngine(
            load_additive_test_catalog(declared=False),
            self.clock,
            PredictableRandom(),
        )
        before = self.state.to_dict()
        with self.assertRaisesRegex(ValueError, "no declared additive migration"):
            upgraded_engine.reconcile_state(self.state)
        self.assertEqual(before, self.state.to_dict())

    def test_v010_save_additively_receives_batch_two_surface_room(self) -> None:
        self.state.content_version = "0.1.0"
        self.state.room_items.pop("salvage_row")
        self.state.creatures.pop("salvage_row")
        events = self.engine.reconcile_state(self.state)
        self.assertEqual("0.51.1", self.state.content_version)
        self.assertEqual(
            [
                "spawn:item:salvage-blank-credit-chip",
                "spawn:item:salvage-composite-repair-strip",
                "spawn:item:salvage-clinic-case",
                "spawn:item:salvage-signal-spike",
            ],
            [item.instance_id for item in self.state.room_items["salvage_row"]],
        )
        self.assertEqual([], self.state.creatures["salvage_row"])
        self.assertEqual("content.additive_reconciliation", events[0].kind)

    def test_v020_save_additively_receives_calibration_cell(self) -> None:
        self.state.content_version = "0.2.0"
        coat = self.engine._equipped_item_state(self.state, "body")
        self.assertIsNotNone(coat)
        assert coat is not None
        coat.durability = None
        self.state.room_items.pop("calibration_cell")
        self.state.creatures.pop("calibration_cell")
        events = self.engine.reconcile_state(self.state)
        self.assertEqual("0.51.1", self.state.content_version)
        self.assertEqual(
            [
                "spawn:item:calibration-knife",
                "spawn:item:weighted-test-rig",
            ],
            [
                item.instance_id
                for item in self.state.room_items["calibration_cell"]
            ],
        )
        self.assertEqual(
            ["spawn:creature:calibration-frame"],
            [
                creature.instance_id
                for creature in self.state.creatures["calibration_cell"]
            ],
        )
        self.assertEqual(40, coat.durability)
        self.assertEqual("content.additive_reconciliation", events[0].kind)

    def test_v030_save_additively_receives_repair_material(self) -> None:
        self.state.content_version = "0.3.0"
        salvage = self.state.room_items["salvage_row"]
        salvage[:] = [
            item
            for item in salvage
            if item.definition_id != "composite_repair_strip"
        ]

        events = self.engine.reconcile_state(self.state)

        self.assertEqual("0.51.1", self.state.content_version)
        self.assertEqual(
            ["spawn:item:salvage-composite-repair-strip"],
            [
                item.instance_id
                for item in self.state.room_items["salvage_row"]
                if item.definition_id == "composite_repair_strip"
            ],
        )
        self.assertEqual("content.additive_reconciliation", events[0].kind)

    def test_newer_content_version_is_rejected(self) -> None:
        self.state.content_version = "9.0.0"
        with self.assertRaisesRegex(ValueError, "newer"):
            self.engine.reconcile_state(self.state)

    def test_contextual_help_and_spelling_suggestion(self) -> None:
        self.assertIn("strike a foe", self.act("help attack").text)
        self.assertIn("Did you mean", self.act("help atack").text)

    def test_global_help_exposes_wait_command(self) -> None:
        self.assertIn("WAIT", self.act("help").text)
        self.assertIn(
            "wait - check whether you have recovered",
            self.act("help wait").text,
        )

    def test_corrupt_creature_identity_is_rejected(self) -> None:
        state = self.engine.new_game("Empty Creature")
        state.creatures["drill_gallery"][0].instance_id = ""
        with self.assertRaisesRegex(ValueError, "empty creature instance"):
            self.engine.validate_state(state)

        state = self.engine.new_game("Duplicate Creature")
        creature = state.creatures["drill_gallery"][0]
        state.creatures["shuttered_arcade"].append(
            CreatureState(creature.instance_id, creature.definition_id, creature.health)
        )
        with self.assertRaisesRegex(ValueError, "duplicate creature instance"):
            self.engine.validate_state(state)

        state = self.engine.new_game("Live Defeated")
        live_id = state.creatures["drill_gallery"][0].instance_id
        state.defeated_creatures.add(live_id)
        with self.assertRaisesRegex(ValueError, "live creatures as defeated"):
            self.engine.validate_state(state)

    def test_corrupt_character_progression_and_wounds_are_rejected(self) -> None:
        state = self.engine.new_game("Bad Health")
        state.character.health = 0
        with self.assertRaisesRegex(ValueError, "health is outside"):
            self.engine.validate_state(state)

        state = self.engine.new_game("Bad Insight")
        state.character.experience.field_pool = -1
        with self.assertRaisesRegex(ValueError, "experience values"):
            self.engine.validate_state(state)

        state = self.engine.new_game("Bad Wound")
        state.character.wounds = [Wound("arm", 6, 0)]
        with self.assertRaisesRegex(ValueError, "wound severity"):
            self.engine.validate_state(state)

        state = self.engine.new_game("Bad Turn")
        state.turn = -1
        with self.assertRaisesRegex(ValueError, "turn cannot be negative"):
            self.engine.validate_state(state)

        state = self.engine.new_game("Bad Durability")
        coat = self.engine._equipped_item_state(state, "body")
        self.assertIsNotNone(coat)
        assert coat is not None
        coat.durability = 41
        with self.assertRaisesRegex(ValueError, "durability is outside"):
            self.engine.validate_state(state)

    def test_corrupt_equipment_slot_and_target_are_rejected(self) -> None:
        state = self.engine.new_game("Bad Equipment")
        blade_id = state.character.equipped["main_hand"]
        state.character.equipped = {"body": blade_id}
        with self.assertRaisesRegex(ValueError, "incompatible slot"):
            self.engine.validate_state(state)

        state = self.engine.new_game("Bad Target")
        state.target_id = "missing:creature"
        with self.assertRaisesRegex(ValueError, "not live in the current room"):
            self.engine.validate_state(state)


if __name__ == "__main__":
    unittest.main()
