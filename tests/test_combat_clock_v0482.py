"""v0.51.0 shared combat clock, intent AI, and tactical-state contracts.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""
from __future__ import annotations

import unittest

# Import test support first; it adds the project src directory to sys.path.
from tests.support import PredictableRandom, load_test_catalog

from beta_earth.application.engine import GameEngine
from beta_earth.domain.battlefield import (
    TacticalEffectState,
    creature_actor_id,
    player_actor_id,
)
from beta_earth.domain.clock import ManualClock
from beta_earth.domain.model import CompanionProgressState, CreatureState, GameState, Stance


class SharedCombatClockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(1_000.0)
        self.catalog = load_test_catalog()
        self.engine = GameEngine(
            self.catalog,
            self.clock,
            PredictableRandom(),
        )
        self.state = self.engine.new_game("Field Clock Tester")
        self.state.character.stance = Stance.OFFENSIVE

    def ready(self) -> None:
        remaining = self.engine._hard_recovery_remaining(
            self.state, self.clock.now()
        )
        if remaining:
            self.clock.advance(remaining)

    def test_soft_commands_never_start_or_advance_field_time(self) -> None:
        self.state.character.room_id = "drill_gallery"
        before = self.state.to_dict()
        for command in ("look", "health", "assess mite", "effects", "roundtime"):
            result = self.engine.execute(self.state, command)
            self.assertFalse(result.changed, result.text)
        self.assertEqual(before, self.state.to_dict())

        attack = self.engine.execute(self.state, "attack mite")
        self.assertTrue(attack.changed, attack.text)
        battle_time = self.state.battle.time
        health = self.state.character.health
        self.clock.advance(100)
        look = self.engine.execute(self.state, "look")
        self.assertFalse(look.changed, look.text)
        self.assertEqual(battle_time, self.state.battle.time)
        self.assertEqual(health, self.state.character.health)

    def test_enemy_resolves_only_when_its_own_readiness_matures(self) -> None:
        self.state.character.room_id = "calibration_cell"
        first = self.engine.execute(self.state, "attack frame")
        self.assertIn("[Intent]", first.text)
        self.assertFalse(
            any(event.kind == "combat.enemy_attack_resolved" for event in first.events)
        )
        self.assertEqual(4.0, self.state.battle.time)

        self.ready()
        second = self.engine.execute(self.state, "attack frame")
        enemy_events = [
            event
            for event in second.events
            if event.kind == "combat.enemy_attack_resolved"
        ]
        self.assertEqual(1, len(enemy_events), second.text)
        self.assertTrue(enemy_events[0].payload["independent_clock"])
        self.assertIn("[Defender ready]", second.text)
        self.assertEqual(8.0, self.state.battle.time)

    def test_group_members_receive_real_independent_actions(self) -> None:
        self.state.character.room_id = "pressure_court"
        for creature in self.state.creatures["pressure_court"]:
            creature.health = 500
        combined_events = []
        for _ in range(2):
            result = self.engine.execute(self.state, "attack skirmisher")
            combined_events.extend(result.events)
            self.ready()
        action_events = [
            event
            for event in combined_events
            if event.kind
            in {
                "combat.enemy_attack_resolved",
                "combat.pressure_action",
                "combat.support_action",
            }
        ]
        actor_ids = {
            event.payload.get("attacker")
            or event.payload.get("actor")
            or event.payload.get("supporter")
            for event in action_events
        }
        self.assertGreaterEqual(len(actor_ids - {None}), 3)
        attack_events = [
            event
            for event in combined_events
            if event.kind == "combat.attack_resolved"
        ]
        self.assertTrue(attack_events)
        self.assertTrue(
            all(event.payload["opponent_count"] == 1 for event in attack_events)
        )
        self.assertTrue(
            all(event.payload["pressure_penalty"] == 0 for event in attack_events)
        )

    def test_sol_uses_an_independent_recovery_clock(self) -> None:
        self.state.character.room_id = "drill_gallery"
        self.state.creatures["drill_gallery"][0].health = 500
        self.state.character.companion_id = "sol"
        self.state.character.companion_progress["sol"] = CompanionProgressState(
            level=5,
            experience=0,
            max_health=52,
            health=52,
            order="balanced",
        )
        result = self.engine.execute(self.state, "attack mite")
        sol_events = [
            event
            for event in result.events
            if event.kind == "combat.companion_attack_resolved"
        ]
        self.assertEqual(1, len(sol_events), result.text)
        self.assertTrue(sol_events[0].payload["independent_clock"])
        self.assertIn("[Sol ready]", result.text)
        self.assertNotIn("follows your attack", result.text)

    def test_intent_is_telegraphed_before_resolution(self) -> None:
        self.state.character.room_id = "drill_gallery"
        result = self.engine.execute(self.state, "attack mite")
        self.assertIn("[Intent]", result.text)
        self.assertIn("[Aggressor ready]", result.text)
        self.assertLess(result.text.index("[Intent]"), result.text.index("[Aggressor ready]"))

    def test_all_seven_enemy_profiles_are_data_driven(self) -> None:
        profiles = {
            definition.behavior_profile
            for definition in self.catalog.creatures.values()
        }
        self.assertEqual(
            {
                "aggressor",
                "defender",
                "skirmisher",
                "controller",
                "support",
                "hunter",
                "commander",
            },
            profiles,
        )
        self.assertTrue(
            all(2 <= definition.action_interval <= 9 for definition in self.catalog.creatures.values())
        )

    def test_support_healing_can_be_interrupted(self) -> None:
        self.state.character.room_id = "pressure_court"
        creatures = self.state.creatures["pressure_court"]
        guard = next(item for item in creatures if item.definition_id == "pressure_guard")
        mender = next(item for item in creatures if item.definition_id == "pressure_mender")
        guard.health -= 10
        result = self.engine.execute(self.state, "attack mender")
        self.assertTrue(
            any(event.kind == "combat.enemy_healing_interrupted" for event in result.events),
            result.text,
        )
        self.assertIn("repair field collapses", result.text)
        self.assertGreater(mender.health, 0)

    def test_hunter_reads_three_repeated_actions_then_counters(self) -> None:
        self.state.character.room_id = "signal_yard"
        sentinel = self.state.creatures["signal_yard"][0]
        sentinel.health = 500
        results = []
        for _ in range(4):
            results.append(self.engine.execute(self.state, "attack sentinel"))
            self.ready()
        self.assertTrue(
            any("prepares its counter" in result.text for result in results),
            "\n".join(result.text for result in results),
        )
        self.assertTrue(
            any(
                event.kind == "combat.enemy_attack_resolved"
                and event.payload.get("intent") == "exploit_pattern"
                for result in results
                for event in result.events
            )
        )

    def test_commander_directs_group_focus(self) -> None:
        self.state.character.room_id = "pressure_court"
        self.state.creatures["pressure_court"] = [
            CreatureState("test:commander", "patrol_interceptor", 500),
            CreatureState("test:support", "pressure_mender", 500),
        ]
        synchronized = self.engine.combat_scheduler.synchronize(
            self.state, self.clock.now()
        )
        self.assertIn("concentrate", " ".join(synchronized.lines).lower())
        resolved = self.engine.combat_scheduler.advance(
            self.state,
            self.clock.now(),
            elapsed=5,
            player_command="stance",
            origin_room_id="pressure_court",
        )
        self.assertIn("[Formation focus]", "\n".join(resolved.lines))
        focus_events = [
            event
            for event in resolved.events
            if event.kind == "combat.pressure_action"
            and event.payload.get("intent") == "direct_focus"
        ]
        self.assertEqual(1, len(focus_events))
        self.assertGreaterEqual(
            len(focus_events[0].payload.get("focused_allies", [])), 2
        )
        # A ready ally may immediately spend the focus bonus on its own action.
        self.assertIn(
            creature_actor_id("test:commander"), self.state.battle.effects
        )

    def test_tactical_effect_is_visible_consumed_and_bounded(self) -> None:
        self.state.character.room_id = "calibration_cell"
        self.engine.combat_scheduler.synchronize(self.state, self.clock.now())
        target = self.state.creatures["calibration_cell"][0]
        actor_id = creature_actor_id(target.instance_id)
        self.state.battle.effects.setdefault(actor_id, {})["exposed"] = TacticalEffectState(
            name="exposed",
            magnitude=2,
            expires_at=self.state.battle.time + 6,
            source_actor_id=player_actor_id(),
        )
        effects = self.engine.execute(self.state, "effects")
        self.assertIn("exposed", effects.text)
        attack = self.engine.execute(self.state, "attack frame")
        self.assertIn("[Exposed]", attack.text)
        self.assertNotIn("exposed", self.state.battle.effects.get(actor_id, {}))

    def test_schema_16_migrates_and_schema_17_round_trips_battlefield(self) -> None:
        raw = self.state.to_dict()
        raw["schema_version"] = 16
        raw.pop("battle", None)
        migrated = GameState.from_dict(raw)
        self.assertEqual(0.0, migrated.battle.time)
        self.assertIsNone(migrated.battle.encounter)

        self.state.character.room_id = "drill_gallery"
        self.engine.execute(self.state, "attack mite")
        restored = GameState.from_dict(self.state.to_dict())
        self.assertEqual(self.state.battle.to_dict(), restored.battle.to_dict())

    def test_scheduler_caps_runaway_transcripts(self) -> None:
        self.state.character.room_id = "pressure_court"
        self.state.creatures["pressure_court"] = [
            CreatureState(f"test:mite:{index}", "rust_mite", 500)
            for index in range(12)
        ]
        self.engine.combat_scheduler.synchronize(self.state, self.clock.now())
        result = self.engine.combat_scheduler.advance(
            self.state,
            self.clock.now(),
            elapsed=20,
            player_command="attack",
            origin_room_id="pressure_court",
        )
        bounded = [
            event for event in result.events if event.kind == "combat.scheduler_bounded"
        ]
        self.assertEqual(1, len(bounded))
        self.assertEqual(8, bounded[0].payload["cap"])
        resolved = [
            event
            for event in result.events
            if event.kind
            in {
                "combat.enemy_attack_resolved",
                "combat.pressure_action",
                "combat.support_action",
            }
        ]
        self.assertLessEqual(len(resolved), 8)

    def test_victory_review_counts_finishing_action(self) -> None:
        self.state.character.room_id = "drill_gallery"
        self.state.creatures["drill_gallery"][0].health = 1
        result = self.engine.execute(self.state, "attack mite")
        self.assertIn("Victory review:", result.text)
        self.assertIn("Distinct player tactics recorded: 1", result.text)
        self.assertIsNone(self.state.battle.encounter)
        self.assertTrue(self.state.battle.last_victory_review)
        self.assertTrue(
            any(event.kind == "combat.victory_review" for event in result.events)
        )

    def test_pinned_state_penalizes_and_is_consumed_by_withdrawal(self) -> None:
        self.state.character.room_id = "pressure_court"
        self.engine.combat_scheduler.synchronize(self.state, self.clock.now())
        self.state.battle.effects.setdefault(player_actor_id(), {})["pinned"] = TacticalEffectState(
            name="pinned",
            magnitude=6,
            expires_at=self.state.battle.time + 8,
            source_actor_id=creature_actor_id("test:pinner"),
        )
        status = self.engine.execute(self.state, "withdraw status")
        self.assertFalse(status.changed)
        self.assertIn("pinned 6", status.text)
        self.assertIn("pinned", self.state.battle.effects[player_actor_id()])
        direction = next(iter(self.catalog.rooms["pressure_court"].exits))
        attempt = self.engine.execute(self.state, f"withdraw {direction}")
        self.assertIn("Pinned pressure", attempt.text)
        self.assertNotIn("pinned", self.state.battle.effects.get(player_actor_id(), {}))

    def test_queued_hard_action_advances_field_time_once(self) -> None:
        self.state.character.room_id = "drill_gallery"
        self.state.creatures["drill_gallery"][0].health = 500
        first = self.engine.execute(self.state, "attack mite")
        self.assertTrue(first.changed)
        first_time = self.state.battle.time
        queued = self.engine.execute(self.state, "queue attack mite")
        self.assertTrue(queued.changed, queued.text)
        self.ready()
        executed = self.engine.execute(self.state, "look")
        self.assertEqual(
            1,
            sum(event.kind == "action.queue_executed" for event in executed.events),
        )
        self.assertEqual(first_time + 4, self.state.battle.time)

    def test_perception_controls_timing_precision(self) -> None:
        self.state.character.room_id = "calibration_cell"
        self.engine.execute(self.state, "attack frame")
        self.state.character.perception = 8
        low = self.engine.execute(self.state, "roundtime")
        self.assertTrue(any(word in low.text for word in ("soon", "imminent", "recovering")))
        self.state.character.perception = 16
        high = self.engine.execute(self.state, "roundtime")
        self.assertRegex(high.text, r"\b\d+s\b")


if __name__ == "__main__":
    unittest.main()
