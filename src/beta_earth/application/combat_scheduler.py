"""Independent command-resolved combat scheduling for player, Sol, and hostiles."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from beta_earth.domain.battle_ai import (
    ATTACK_INTENTS,
    CHARGED_INTENTS,
    EnemyDecisionContext,
    choose_enemy_intent,
    enemy_recovery_seconds,
    intent_telegraph,
    sol_recovery_seconds,
    timing_description,
)
from beta_earth.domain.battlefield import (
    BattleState,
    CombatActorState,
    EncounterStatsState,
    TacticalEffectState,
    companion_actor_id,
    creature_actor_id,
    player_actor_id,
)
from beta_earth.domain.combat import (
    equipped_item,
    open_d100,
    resolve_creature_attack,
)
from beta_earth.domain.events import DomainEvent
from beta_earth.domain.model import DefenseMode, GameState, Wound
from beta_earth.domain.progression import award_field_insight, roundtime_remaining
from beta_earth.domain.recovery import active_bleeding, apply_impact_condition

if TYPE_CHECKING:
    from beta_earth.application.engine import GameEngine
    from beta_earth.domain.content import CreatureDefinition
    from beta_earth.domain.model import CreatureState


@dataclass(frozen=True, slots=True)
class BattleResolution:
    lines: tuple[str, ...] = ()
    events: tuple[DomainEvent, ...] = ()
    changed: bool = False


@dataclass(frozen=True, slots=True)
class PlayerAttackModifiers:
    offense: int = 0
    damage: int = 0
    defense_delta: int = 0
    armor_delta: int = 0
    lines: tuple[str, ...] = ()
    events: tuple[DomainEvent, ...] = ()
    changed: bool = False


class CombatScheduler:
    """One canonical action economy for every actor in a text-first battle."""

    MAX_TOTAL_ACTIONS_PER_COMMAND = 8
    MAX_ACTIONS_PER_ACTOR_PER_COMMAND = 2

    def __init__(self, engine: "GameEngine") -> None:
        self.engine = engine

    @property
    def catalog(self):
        return self.engine.catalog

    @property
    def rng(self):
        return self.engine.rng

    @staticmethod
    def _actor_name(state: GameState, actor_id: str, catalog) -> str:
        if actor_id == player_actor_id():
            return state.character.name
        if actor_id.startswith("companion:"):
            companion_id = actor_id.split(":", 1)[1]
            definition = catalog.economy.mercenaries.get(companion_id)
            return definition.name if definition is not None else companion_id
        if actor_id.startswith("creature:"):
            instance_id = actor_id.split(":", 1)[1]
            for creatures in state.creatures.values():
                creature = next(
                    (item for item in creatures if item.instance_id == instance_id),
                    None,
                )
                if creature is not None:
                    return catalog.creatures[creature.definition_id].name
            return instance_id
        return actor_id

    def _live_creature(self, state: GameState, actor_id: str) -> "CreatureState | None":
        if not actor_id.startswith("creature:"):
            return None
        instance_id = actor_id.split(":", 1)[1]
        return next(
            (
                creature
                for creature in self.engine._live_creatures(state)
                if creature.instance_id == instance_id
            ),
            None,
        )

    def _active_sol(self, state: GameState, now: float):
        companion, progress = self.engine._active_companion_context(
            state,
            now,
            recover_if_ready=True,
        )
        if (
            companion is None
            or progress is None
            or companion.id != "sol"
            or companion.assist_kind != "partner"
        ):
            return None, None
        return companion, progress

    @staticmethod
    def _effect(
        battle: BattleState,
        actor_id: str,
        name: str,
    ) -> TacticalEffectState | None:
        effect = battle.effects.get(actor_id, {}).get(name)
        if effect is None or effect.expires_at <= battle.time:
            return None
        return effect

    @classmethod
    def _effect_active(cls, battle: BattleState, actor_id: str, name: str) -> bool:
        return cls._effect(battle, actor_id, name) is not None

    @staticmethod
    def _apply_effect(
        battle: BattleState,
        *,
        actor_id: str,
        name: str,
        magnitude: int,
        duration: float,
        source_actor_id: str,
        uses: int = 1,
    ) -> None:
        effect = TacticalEffectState(
            name=name,
            magnitude=magnitude,
            expires_at=battle.time + max(1.0, duration),
            source_actor_id=source_actor_id,
            uses_remaining=max(1, uses),
        )
        actor_effects = battle.effects.setdefault(actor_id, {})
        prior = actor_effects.get(name)
        if prior is not None and prior.expires_at > battle.time:
            effect = TacticalEffectState(
                name=name,
                magnitude=(
                    max(prior.magnitude, magnitude)
                    if magnitude >= 0
                    else min(prior.magnitude, magnitude)
                ),
                expires_at=max(prior.expires_at, effect.expires_at),
                source_actor_id=source_actor_id,
                uses_remaining=min(20, max(prior.uses_remaining, uses)),
            )
        actor_effects[name] = effect

    @staticmethod
    def _consume_effect(
        battle: BattleState,
        actor_id: str,
        name: str,
    ) -> TacticalEffectState | None:
        actor_effects = battle.effects.get(actor_id)
        if not actor_effects:
            return None
        effect = actor_effects.get(name)
        if effect is None:
            return None
        if effect.expires_at <= battle.time:
            actor_effects.pop(name, None)
            if not actor_effects:
                battle.effects.pop(actor_id, None)
            return None
        if effect.uses_remaining <= 1:
            actor_effects.pop(name, None)
        else:
            actor_effects[name] = TacticalEffectState(
                name=effect.name,
                magnitude=effect.magnitude,
                expires_at=effect.expires_at,
                source_actor_id=effect.source_actor_id,
                uses_remaining=effect.uses_remaining - 1,
            )
        if not actor_effects:
            battle.effects.pop(actor_id, None)
        return effect

    @staticmethod
    def _expire_effects(battle: BattleState) -> None:
        for actor_id, actor_effects in tuple(battle.effects.items()):
            for name, effect in tuple(actor_effects.items()):
                if effect.expires_at <= battle.time:
                    actor_effects.pop(name, None)
            if not actor_effects:
                battle.effects.pop(actor_id, None)

    @staticmethod
    def _player_repeating(battle: BattleState) -> bool:
        history = battle.player_action_history
        return len(history) >= 3 and len(set(history[-3:])) == 1

    def _enemy_context(
        self,
        state: GameState,
        creature: "CreatureState",
        definition: "CreatureDefinition",
        actor: CombatActorState,
        now: float,
    ) -> EnemyDecisionContext:
        live = self.engine._live_creatures(state)
        injured = any(
            ally.health < self.catalog.creatures[ally.definition_id].max_health
            for ally in live
        )
        support = next(
            (
                ally
                for ally in live
                if self.catalog.creatures[ally.definition_id].behavior_profile
                == "support"
            ),
            None,
        )
        support_unprotected = bool(
            support is not None
            and not self._effect_active(
                state.battle,
                creature_actor_id(support.instance_id),
                "protected",
            )
        )
        _companion, progress = self._active_sol(state, now)
        return EnemyDecisionContext(
            profile=definition.behavior_profile,
            actions_taken=actor.actions_taken,
            own_health_ratio=creature.health / max(1, definition.max_health),
            ally_count=len(live),
            injured_ally=injured,
            unprotected_support=support_unprotected,
            player_wounded=bool(state.character.wounds),
            player_prone=state.character.prone,
            player_repeating=self._player_repeating(state.battle),
            player_recent_heal=bool(
                state.battle.player_action_history
                and state.battle.player_action_history[-1]
                in {"stabilize", "recover", "rest"}
            ),
            sol_available=progress is not None and progress.health > 0,
            sol_order=progress.order if progress is not None else "balanced",
        )

    def _choose_enemy_target(
        self,
        state: GameState,
        definition: "CreatureDefinition",
        intent: str,
        now: float,
    ) -> str:
        _companion, progress = self._active_sol(state, now)
        sol_id = companion_actor_id("sol")
        if progress is None or progress.health <= 0:
            return player_actor_id()
        if definition.behavior_profile == "commander" and intent == "direct_focus":
            return sol_id if progress.order == "assault" else player_actor_id()
        if (
            definition.behavior_profile in {"aggressor", "hunter"}
            and progress.order == "assault"
            and progress.health * 2 <= progress.max_health
        ):
            return sol_id
        return player_actor_id()

    def _select_enemy_intent(
        self,
        state: GameState,
        creature: "CreatureState",
        definition: "CreatureDefinition",
        actor: CombatActorState,
        now: float,
    ) -> None:
        intent = choose_enemy_intent(
            self._enemy_context(state, creature, definition, actor, now)
        )
        actor.current_intent = intent
        actor.target_id = self._choose_enemy_target(state, definition, intent, now)
        actor.telegraph_shown = False

    @staticmethod
    def _select_sol_intent(order: str) -> str:
        return {
            "balanced": "balanced_setup",
            "guard": "guard_intercept",
            "assault": "assault_strike",
        }.get(order, "balanced_setup")

    def _target_name(self, state: GameState, actor: CombatActorState) -> str:
        if actor.target_id is None:
            return "the open lane"
        return self._actor_name(state, actor.target_id, self.catalog)

    def _telegraph(self, state: GameState, actor: CombatActorState) -> str | None:
        if actor.current_intent is None or actor.telegraph_shown:
            return None
        if actor.kind == "creature":
            creature = self._live_creature(state, actor.actor_id)
            if creature is None:
                return None
            definition = self.catalog.creatures[creature.definition_id]
            text = intent_telegraph(
                actor_name=definition.name,
                profile=definition.behavior_profile,
                intent=actor.current_intent,
                target_name=self._target_name(state, actor),
            )
        elif actor.kind == "companion":
            text = {
                "balanced_setup": "Sol studies the hostile formation and prepares an Akari-line setup without taking your finishing choice.",
                "guard_intercept": "Sol shifts onto the dangerous angle and prepares to intercept the next telegraphed attack.",
                "assault_strike": "Sol lowers into Assault and prepares an independent finishing drive.",
            }.get(actor.current_intent, "Sol prepares his next independent action.")
        else:
            return None
        actor.telegraph_shown = True
        return f"[Intent] {text}"

    def _ensure_actor_intents(self, state: GameState, now: float) -> None:
        battle = state.battle
        for actor in battle.actors.values():
            if actor.kind == "creature" and actor.current_intent is None:
                creature = self._live_creature(state, actor.actor_id)
                if creature is None:
                    continue
                definition = self.catalog.creatures[creature.definition_id]
                self._select_enemy_intent(state, creature, definition, actor, now)
            elif actor.kind == "companion" and actor.current_intent is None:
                _companion, progress = self._active_sol(state, now)
                if progress is not None:
                    actor.current_intent = self._select_sol_intent(progress.order)
                    actor.target_id = None
                    actor.telegraph_shown = False

    def synchronize(self, state: GameState, now: float) -> BattleResolution:
        """Start or reconcile one encounter without advancing battlefield time."""

        battle = state.battle
        live = self.engine._live_creatures(state)
        room_id = state.character.room_id
        lines: list[str] = []
        events: list[DomainEvent] = []
        changed = False

        if state.incapacitation is not None:
            return BattleResolution()
        if not live:
            if battle.room_id == room_id and battle.encounter is not None:
                return self.complete_encounter(state)
            if battle.room_id is not None:
                battle.room_id = None
                battle.actors.clear()
                battle.effects.clear()
                battle.player_action_history.clear()
                battle.encounter = None
                changed = True
            return BattleResolution(changed=changed)

        if battle.room_id != room_id or battle.encounter is None:
            battle.encounter_serial += 1
            battle.room_id = room_id
            battle.actors.clear()
            battle.effects.clear()
            battle.player_action_history.clear()
            battle.encounter = EncounterStatsState(
                encounter_id=f"encounter:{battle.encounter_serial}",
                room_id=room_id,
                started_at=battle.time,
            )
            battle.actors[player_actor_id()] = CombatActorState(
                actor_id=player_actor_id(),
                kind="player",
                next_action_at=battle.time,
            )
            lines.append(
                f"[Combat clock] Encounter {battle.encounter_serial} begins at field time {battle.time:.1f}s. "
                "Soft information commands do not advance it."
            )
            events.append(
                DomainEvent(
                    "combat.encounter_started",
                    {
                        "encounter_id": battle.encounter.encounter_id,
                        "room_id": room_id,
                        "hostiles": len(live),
                        "battle_time": battle.time,
                    },
                )
            )
            changed = True

        live_ids = {creature.instance_id for creature in live}
        for actor_id in tuple(battle.actors):
            if actor_id.startswith("creature:") and actor_id.split(":", 1)[1] not in live_ids:
                battle.actors.pop(actor_id, None)
                battle.effects.pop(actor_id, None)
                changed = True

        for creature in live:
            actor_id = creature_actor_id(creature.instance_id)
            if actor_id not in battle.actors:
                definition = self.catalog.creatures[creature.definition_id]
                actor = CombatActorState(
                    actor_id=actor_id,
                    kind="creature",
                    next_action_at=battle.time + definition.action_interval,
                    recovery_duration=float(definition.action_interval),
                )
                battle.actors[actor_id] = actor
                self._select_enemy_intent(state, creature, definition, actor, now)
                changed = True

        companion, progress = self._active_sol(state, now)
        sol_id = companion_actor_id("sol")
        if companion is not None and progress is not None and progress.health > 0:
            if sol_id not in battle.actors:
                interval = sol_recovery_seconds(progress.order)
                battle.actors[sol_id] = CombatActorState(
                    actor_id=sol_id,
                    kind="companion",
                    next_action_at=battle.time + interval,
                    current_intent=self._select_sol_intent(progress.order),
                    recovery_duration=float(interval),
                )
                changed = True
        elif sol_id in battle.actors:
            battle.actors.pop(sol_id, None)
            battle.effects.pop(sol_id, None)
            changed = True

        self._ensure_actor_intents(state, now)
        for actor in sorted(
            battle.actors.values(), key=lambda item: (item.next_action_at, item.actor_id)
        ):
            telegraph = self._telegraph(state, actor)
            if telegraph:
                lines.append(telegraph)
                changed = True
        return BattleResolution(tuple(lines), tuple(events), changed)

    def status_lines(self, state: GameState, now: float) -> tuple[str, ...]:
        battle = state.battle
        if battle.room_id != state.character.room_id or battle.encounter is None:
            return ("Battlefield clock: no active encounter.",)
        lines = [
            f"Battlefield clock: {battle.time:.1f}s · encounter {battle.encounter.encounter_id}",
            (
                "You: ready"
                if roundtime_remaining(
                    max(
                        state.character.roundtime_until,
                        state.character.stunned_until,
                    ),
                    now,
                )
                == 0
                else f"You: server recovery {roundtime_remaining(max(state.character.roundtime_until, state.character.stunned_until), now)}s"
            ),
        ]
        for actor in sorted(
            (
                actor
                for actor in battle.actors.values()
                if actor.kind != "player"
            ),
            key=lambda item: (item.next_action_at, item.actor_id),
        ):
            name = self._actor_name(state, actor.actor_id, self.catalog)
            remaining = max(0.0, actor.next_action_at - battle.time)
            timing = timing_description(remaining, state.character.perception)
            intent = (actor.current_intent or "recovering").replace("_", " ")
            target = self._target_name(state, actor)
            lines.append(f"{name}: {intent} toward {target} · {timing}")
        active_effects = []
        for actor_id, effects in sorted(battle.effects.items()):
            names = [
                name
                for name, effect in sorted(effects.items())
                if effect.expires_at > battle.time
            ]
            if names:
                active_effects.append(
                    f"{self._actor_name(state, actor_id, self.catalog)}: {', '.join(names)}"
                )
        if active_effects:
            lines.append("Tactical states:")
            lines.extend(f"  {line}" for line in active_effects)
        return tuple(lines)

    def projection(self, state: GameState, now: float) -> dict[str, object]:
        """Return a read-only battlefield projection for first-party clients."""

        battle = state.battle
        active = bool(
            battle.encounter is not None
            and battle.room_id == state.character.room_id
        )
        actors: list[dict[str, object]] = []
        if active:
            for actor in sorted(
                battle.actors.values(),
                key=lambda item: (item.next_action_at, item.actor_id),
            ):
                remaining = max(0.0, actor.next_action_at - battle.time)
                effects = [
                    {
                        "name": name,
                        "magnitude": effect.magnitude,
                        "remaining_field_seconds": max(
                            0.0, effect.expires_at - battle.time
                        ),
                        "uses_remaining": effect.uses_remaining,
                        "source_actor_id": effect.source_actor_id,
                    }
                    for name, effect in sorted(
                        battle.effects.get(actor.actor_id, {}).items()
                    )
                    if effect.expires_at > battle.time
                ]
                actors.append(
                    {
                        "actor_id": actor.actor_id,
                        "kind": actor.kind,
                        "name": self._actor_name(
                            state, actor.actor_id, self.catalog
                        ),
                        "intent": actor.current_intent,
                        "target_id": actor.target_id,
                        "target_name": self._target_name(state, actor),
                        "ready_in_field_seconds": remaining,
                        "timing_text": timing_description(
                            remaining, state.character.perception
                        ),
                        "recovery_duration": actor.recovery_duration,
                        "interrupted_for_field_seconds": max(
                            0.0, actor.interrupted_until - battle.time
                        ),
                        "actions_taken": actor.actions_taken,
                        "effects": effects,
                    }
                )
        return {
            "active": active,
            "battle_time": battle.time,
            "room_id": battle.room_id if active else None,
            "encounter_id": (
                battle.encounter.encounter_id
                if active and battle.encounter is not None
                else None
            ),
            "soft_commands_advance_time": False,
            "max_actions_per_command": self.MAX_TOTAL_ACTIONS_PER_COMMAND,
            "max_actions_per_actor_per_command": (
                self.MAX_ACTIONS_PER_ACTOR_PER_COMMAND
            ),
            "actors": actors,
            "last_victory_review": list(battle.last_victory_review),
            "server_observed_at": now,
        }

    def player_attack_modifiers(
        self,
        state: GameState,
        target_instance_id: str,
        *,
        ignore_player_opening: bool = False,
    ) -> PlayerAttackModifiers:
        battle = state.battle
        if battle.room_id != state.character.room_id:
            return PlayerAttackModifiers()
        lines: list[str] = []
        events: list[DomainEvent] = []
        offense = 0
        damage = 0
        defense_delta = 0
        armor_delta = 0
        changed = False
        player_id = player_actor_id()
        target_id = creature_actor_id(target_instance_id)

        opening = (
            None
            if ignore_player_opening
            else self._consume_effect(battle, player_id, "opening")
        )
        if ignore_player_opening:
            # A legacy player-owned Sol window carries its own bounded bonus.
            # Discard a duplicate scheduler opening so one setup cannot stack twice.
            self._consume_effect(battle, player_id, "opening")
        if opening is not None:
            offense += max(0, opening.magnitude)
            damage += max(1, opening.magnitude // 6)
            lines.append(
                f"[Opening] You convert a prepared lane for +{max(0, opening.magnitude)} offense."
            )
            changed = True
        suppressed = self._consume_effect(battle, player_id, "suppressed")
        if suppressed is not None:
            offense -= abs(suppressed.magnitude)
            lines.append(
                f"[Suppressed] Control pressure reduces this attack by {abs(suppressed.magnitude)} offense."
            )
            changed = True
        off_balance = self._consume_effect(battle, target_id, "off_balance")
        if off_balance is not None:
            defense_delta -= abs(off_balance.magnitude)
            lines.append(
                f"[Off-balance] The target loses {abs(off_balance.magnitude)} defense for this action."
            )
            changed = True
        exposed = self._consume_effect(battle, target_id, "exposed")
        if exposed is not None:
            armor_delta -= max(1, abs(exposed.magnitude))
            lines.append(
                f"[Exposed] The target's opened armor loses {max(1, abs(exposed.magnitude))} protection."
            )
            changed = True
        protected = self._consume_effect(battle, target_id, "protected")
        if protected is not None:
            defense_delta += abs(protected.magnitude)
            armor_delta += max(1, abs(protected.magnitude) // 4)
            lines.append(
                f"[Protected] An ally's cover adds {abs(protected.magnitude)} defense to this exchange."
            )
            changed = True
        if changed:
            events.append(
                DomainEvent(
                    "combat.tactical_states_consumed",
                    {
                        "target": target_instance_id,
                        "offense": offense,
                        "damage": damage,
                        "defense_delta": defense_delta,
                        "armor_delta": armor_delta,
                    },
                )
            )
        return PlayerAttackModifiers(
            offense=offense,
            damage=damage,
            defense_delta=defense_delta,
            armor_delta=armor_delta,
            lines=tuple(lines),
            events=tuple(events),
            changed=changed,
        )

    def after_player_attack(
        self,
        state: GameState,
        *,
        target_instance_id: str,
        hit: bool,
        severity: int,
        damage: int,
        weapon_profile: str,
    ) -> BattleResolution:
        battle = state.battle
        actor_id = creature_actor_id(target_instance_id)
        actor = battle.actors.get(actor_id)
        if actor is None:
            return BattleResolution()
        lines: list[str] = []
        events: list[DomainEvent] = []
        changed = False
        stats = battle.encounter

        if hit and actor.current_intent in CHARGED_INTENTS:
            focused = self._effect_active(battle, actor_id, "focused")
            threshold = 3 if focused else 2
            if severity >= threshold:
                interrupted_intent = actor.current_intent
                actor.current_intent = "recover"
                actor.target_id = None
                actor.interrupted_until = max(actor.interrupted_until, battle.time + 2)
                actor.next_action_at = max(actor.next_action_at, actor.interrupted_until)
                actor.telegraph_shown = False
                if stats is not None:
                    stats.interrupted_charges += 1
                    stats.tactical_successes.append("charged attack interrupted")
                lines.append(
                    f"[Pattern broken] Your impact interrupts {interrupted_intent.replace('_', ' ')} before release."
                )
                events.append(
                    DomainEvent(
                        "combat.intent_interrupted",
                        {
                            "target": target_instance_id,
                            "intent": interrupted_intent,
                            "severity": severity,
                            "focused": focused,
                        },
                    )
                )
                changed = True
        if hit and damage > 0 and weapon_profile == "heavy" and severity >= 3:
            self._apply_effect(
                battle,
                actor_id=actor_id,
                name="exposed",
                magnitude=2,
                duration=7,
                source_actor_id=player_actor_id(),
            )
            if stats is not None:
                stats.armor_openings += 1
                stats.tactical_successes.append("armor opened")
            lines.append(
                "[Armor opened] Heavy leverage removes 2 armor from the next committed strike."
            )
            events.append(
                DomainEvent(
                    "combat.armor_opened",
                    {"target": target_instance_id, "magnitude": 2},
                )
            )
            changed = True
        if hit and actor.current_intent == "repair_ally" and severity >= 2:
            actor.current_intent = "recover"
            actor.next_action_at = max(actor.next_action_at, battle.time + 2)
            actor.telegraph_shown = False
            if stats is not None:
                stats.enemy_healing_prevented += 1
                stats.tactical_successes.append("enemy healing prevented")
            lines.append(
                "[Field insight] The repair field collapses before it can restore the formation."
            )
            events.append(
                DomainEvent(
                    "combat.enemy_healing_interrupted",
                    {"target": target_instance_id},
                )
            )
            changed = True
        return BattleResolution(tuple(lines), tuple(events), changed)

    def _record_player_command(self, state: GameState, command: str) -> None:
        battle = state.battle
        battle.player_action_history.append(command)
        del battle.player_action_history[:-8]
        if battle.encounter is not None:
            battle.encounter.player_actions.append(command)
            del battle.encounter.player_actions[:-8]

    def advance(
        self,
        state: GameState,
        now: float,
        *,
        elapsed: float,
        player_command: str,
        origin_room_id: str,
    ) -> BattleResolution:
        """Advance combat time by one completed hard action's recovery cost."""

        if elapsed <= 0:
            return BattleResolution()
        battle = state.battle
        recorded_command = bool(
            battle.encounter is not None
            and battle.room_id == origin_room_id
        )
        if recorded_command:
            # Record before synchronization so a finishing blow is represented
            # in the victory review even when reconciliation closes the encounter.
            self._record_player_command(state, player_command)
        sync = self.synchronize(state, now)
        lines = list(sync.lines)
        events = list(sync.events)
        changed = sync.changed
        if battle.encounter is None or battle.room_id != origin_room_id:
            return BattleResolution(tuple(lines), tuple(events), changed)

        if not recorded_command:
            self._record_player_command(state, player_command)
        player = battle.actors.setdefault(
            player_actor_id(),
            CombatActorState(actor_id=player_actor_id(), kind="player"),
        )
        start = battle.time
        end = start + float(elapsed)
        player.current_intent = player_command
        player.recovery_duration = float(elapsed)
        player.next_action_at = end
        player.telegraph_shown = True
        changed = True
        events.append(
            DomainEvent(
                "combat.clock_advanced",
                {
                    "from": start,
                    "to": end,
                    "elapsed": float(elapsed),
                    "player_command": player_command,
                },
            )
        )

        if state.character.room_id != origin_room_id:
            battle.time = end
            player.current_intent = None
            battle.room_id = None
            battle.actors.clear()
            battle.effects.clear()
            battle.player_action_history.clear()
            battle.encounter = None
            return BattleResolution(tuple(lines), tuple(events), True)

        total_actions = 0
        per_actor: dict[str, int] = {}
        while total_actions < self.MAX_TOTAL_ACTIONS_PER_COMMAND:
            due = [
                actor
                for actor in battle.actors.values()
                if actor.kind != "player"
                and actor.next_action_at <= end + 1e-9
                and per_actor.get(actor.actor_id, 0)
                < self.MAX_ACTIONS_PER_ACTOR_PER_COMMAND
            ]
            if not due or state.incapacitation is not None:
                break
            actor = min(due, key=lambda item: (item.next_action_at, item.actor_id))
            battle.time = max(start, actor.next_action_at)
            if actor.interrupted_until > battle.time:
                actor.next_action_at = actor.interrupted_until
                actor.current_intent = "recover"
                actor.telegraph_shown = False
                continue
            if actor.kind == "companion":
                result = self._resolve_sol_action(state, actor, now)
            else:
                result = self._resolve_enemy_action(state, actor, now)
            lines.extend(result.lines)
            events.extend(result.events)
            changed = changed or result.changed
            total_actions += 1
            per_actor[actor.actor_id] = per_actor.get(actor.actor_id, 0) + 1
            if actor.actor_id not in battle.actors:
                continue
            actor.actions_taken += 1
            if actor.kind == "companion":
                _companion, progress = self._active_sol(state, now)
                if progress is None or progress.health <= 0:
                    actor.current_intent = "recover"
                    actor.target_id = None
                    actor.recovery_duration = 6
                    actor.next_action_at = battle.time + 6
                else:
                    recovery = sol_recovery_seconds(progress.order)
                    actor.recovery_duration = float(recovery)
                    actor.next_action_at = battle.time + recovery
                    actor.current_intent = self._select_sol_intent(progress.order)
                    actor.target_id = None
            else:
                creature = self._live_creature(state, actor.actor_id)
                if creature is not None:
                    definition = self.catalog.creatures[creature.definition_id]
                    recovery = enemy_recovery_seconds(
                        definition.action_interval,
                        actor.current_intent or "quick_strike",
                    )
                    actor.recovery_duration = float(recovery)
                    actor.next_action_at = battle.time + recovery
                    self._select_enemy_intent(state, creature, definition, actor, now)
            actor.telegraph_shown = False
            telegraph = self._telegraph(state, actor)
            if telegraph:
                lines.append(telegraph)

        if total_actions >= self.MAX_TOTAL_ACTIONS_PER_COMMAND:
            overdue = [
                actor
                for actor in battle.actors.values()
                if actor.kind != "player" and actor.next_action_at <= end
            ]
            for actor in overdue:
                actor.next_action_at = end + 1
            lines.append(
                "[Combat clock bounded] The transcript reaches its per-command action cap; remaining ready actors retain one deferred opportunity."
            )
            events.append(
                DomainEvent(
                    "combat.scheduler_bounded",
                    {"deferred_actors": len(overdue), "cap": self.MAX_TOTAL_ACTIONS_PER_COMMAND},
                )
            )
            changed = True

        battle.time = end
        player.current_intent = None
        player.next_action_at = end
        self._expire_effects(battle)
        if state.incapacitation is None:
            self._ensure_actor_intents(state, now)
            for actor in sorted(
                battle.actors.values(), key=lambda item: (item.next_action_at, item.actor_id)
            ):
                telegraph = self._telegraph(state, actor)
                if telegraph:
                    lines.append(telegraph)
                    changed = True
        else:
            remaining = max(
                0, math.ceil(state.incapacitation.recover_at - now)
            )
            lines.append(f"Incapacitated recovery: {remaining} sec.")

        if not self.engine._live_creatures(state):
            finished = self.complete_encounter(state)
            lines.extend(finished.lines)
            events.extend(finished.events)
            changed = changed or finished.changed
        return BattleResolution(tuple(lines), tuple(events), changed)

    def _resolve_sol_action(
        self,
        state: GameState,
        actor: CombatActorState,
        now: float,
    ) -> BattleResolution:
        companion, progress = self._active_sol(state, now)
        if companion is None or progress is None or progress.health <= 0:
            return BattleResolution()
        live = self.engine._live_creatures(state)
        if not live:
            return BattleResolution()
        target = min(
            live,
            key=lambda creature: (
                0
                if self.catalog.creatures[creature.definition_id].behavior_profile
                in {"support", "commander"}
                else 1,
                creature.health
                / max(1, self.catalog.creatures[creature.definition_id].max_health),
                creature.instance_id,
            ),
        )
        definition = self.catalog.creatures[target.definition_id]
        order = progress.order
        lines = [
            f"[Sol ready] {companion.name} acts on his own {order} recovery clock against {definition.name}."
        ]
        events: list[DomainEvent] = []
        stats = state.battle.encounter
        if stats is not None:
            stats.sol_actions += 1

        if order == "guard":
            guard_added = max(5, 5 + progress.level // 2)
            state.character.guard_points = min(
                1000, state.character.guard_points + guard_added
            )
            self._apply_effect(
                state.battle,
                actor_id=player_actor_id(),
                name="protected",
                magnitude=6,
                duration=6,
                source_actor_id=actor.actor_id,
            )
            hostile_actors = [
                hostile
                for hostile in state.battle.actors.values()
                if hostile.kind == "creature"
                and hostile.target_id == player_actor_id()
                and hostile.current_intent in ATTACK_INTENTS
            ]
            interrupted = None
            if hostile_actors:
                interrupted = min(
                    hostile_actors,
                    key=lambda hostile: (hostile.next_action_at, hostile.actor_id),
                )
                self._apply_effect(
                    state.battle,
                    actor_id=interrupted.actor_id,
                    name="suppressed",
                    magnitude=6,
                    duration=6,
                    source_actor_id=actor.actor_id,
                )
                interrupted.next_action_at += 1
            state.flags.add(f"companion_opening:{target.instance_id}")
            self._apply_effect(
                state.battle,
                actor_id=player_actor_id(),
                name="opening",
                magnitude=10,
                duration=7,
                source_actor_id=actor.actor_id,
            )
            progress.setup_actions = min(100_000_000, progress.setup_actions + 1)
            lines.append(
                f"[Partner synchrony] Sol adds {guard_added} guard, protects your lane, and creates an opening without taking a damage action."
            )
            if interrupted is not None:
                lines.append(
                    f"His interception delays {self._actor_name(state, interrupted.actor_id, self.catalog)} by 1 field second."
                )
            events.append(
                DomainEvent(
                    "combat.companion_guard_action",
                    {
                        "companion_id": companion.id,
                        "guard_added": guard_added,
                        "target": target.instance_id,
                        "delayed_actor": interrupted.actor_id if interrupted else None,
                    },
                )
            )
            if stats is not None:
                stats.partner_synchrony += 1
                stats.tactical_successes.append("Sol guard opening")
            return BattleResolution(tuple(lines), tuple(events), True)

        roll = open_d100(self.rng)
        offense = (
            64
            + progress.level * 3
            + companion.attack_power
            + (10 if order == "assault" else 0)
        )
        endroll = roll + offense - definition.defense
        hit = endroll >= 100
        damage = 0
        finishing = False
        reserved = False
        if hit:
            raw_damage = max(
                1,
                5
                + progress.level // 3
                + (2 if order == "assault" else 0)
                + self.rng.randint(0, 2)
                - definition.armor // 3,
            )
            if order == "balanced":
                damage = min(raw_damage, max(0, target.health - 1))
                reserved = raw_damage > damage
            else:
                damage = min(raw_damage, max(0, target.health))
            target.health -= damage
            finishing = damage > 0 and target.health <= 0
        lines.append(
            f"[Partner roll {roll:+d} + Offense {offense} - Defense {definition.defense} = {endroll}]"
        )
        if hit:
            progress.damage_dealt = min(
                100_000_000, progress.damage_dealt + damage
            )
            if finishing:
                progress.finishing_strikes = min(
                    100_000_000, progress.finishing_strikes + 1
                )
                lines.append(
                    f"Sol deals {damage} damage; your explicit Assault order authorized the finishing strike on his independent clock."
                )
            else:
                state.flags.add(f"companion_opening:{target.instance_id}")
                self._apply_effect(
                    state.battle,
                    actor_id=player_actor_id(),
                    name="opening",
                    magnitude=12 if order == "balanced" else 8,
                    duration=7,
                    source_actor_id=actor.actor_id,
                )
                progress.setup_actions = min(
                    100_000_000, progress.setup_actions + 1
                )
                if reserved:
                    state.flags.add(
                        f"companion_finish_window:{target.instance_id}"
                    )
                    progress.finish_reservations = min(
                        100_000_000, progress.finish_reservations + 1
                    )
                    lines.append(
                        f"Sol deals {damage} damage, stops short of the finish, and reserves the final line for you."
                    )
                else:
                    lines.append(
                        f"Sol deals {damage} damage and turns the target into your next opening."
                    )
        else:
            state.flags.add(f"companion_opening:{target.instance_id}")
            self._apply_effect(
                state.battle,
                actor_id=player_actor_id(),
                name="opening",
                magnitude=8,
                duration=6,
                source_actor_id=actor.actor_id,
            )
            progress.setup_actions = min(
                100_000_000, progress.setup_actions + 1
            )
            lines.append(
                "Sol's strike misses cleanly, but his footwork fixes the target's attention and leaves you an opening."
            )
        events.append(
            DomainEvent(
                "combat.companion_attack_resolved",
                {
                    "companion_id": companion.id,
                    "target": target.instance_id,
                    "order": order,
                    "roll": roll,
                    "offense": offense,
                    "defense": definition.defense,
                    "endroll": endroll,
                    "hit": hit,
                    "damage": damage,
                    "finishing_strike": finishing,
                    "player_finisher_reserved": reserved,
                    "independent_clock": True,
                },
            )
        )
        if not finishing:
            events.append(
                DomainEvent(
                    "combat.companion_setup_resolved",
                    {
                        "companion_id": companion.id,
                        "target": target.instance_id,
                        "order": order,
                        "damage": damage,
                        "guard_added": 0,
                        "opening_created": True,
                        "independent_clock": True,
                    },
                )
            )
        if reserved:
            events.append(
                DomainEvent(
                    "combat.companion_finish_reserved",
                    {
                        "companion_id": companion.id,
                        "target": target.instance_id,
                        "target_health": target.health,
                        "reserved_for": state.character.key,
                        "independent_clock": True,
                    },
                )
            )
        if stats is not None and (hit or reserved):
            stats.partner_synchrony += 1
            stats.tactical_successes.append("Sol independent setup")
        if finishing:
            self.engine._defeat_creature_from_battlefield(
                state,
                target,
                definition,
                now,
                lines,
                events,
                finisher=companion.id,
            )
        return BattleResolution(tuple(lines), tuple(events), True)

    def _resolve_enemy_action(
        self,
        state: GameState,
        actor: CombatActorState,
        now: float,
    ) -> BattleResolution:
        creature = self._live_creature(state, actor.actor_id)
        if creature is None:
            state.battle.actors.pop(actor.actor_id, None)
            state.battle.effects.pop(actor.actor_id, None)
            return BattleResolution(changed=True)
        definition = self.catalog.creatures[creature.definition_id]
        intent = actor.current_intent or "quick_strike"
        lines = [
            f"[{definition.behavior_profile.title()} ready] {definition.name.capitalize()} resolves {intent.replace('_', ' ')}."
        ]
        events: list[DomainEvent] = []
        stats = state.battle.encounter
        if stats is not None:
            stats.hostile_actions += 1

        if intent == "repair_ally":
            injured = [
                ally
                for ally in self.engine._live_creatures(state)
                if ally.health < self.catalog.creatures[ally.definition_id].max_health
            ]
            if injured:
                target = min(
                    injured,
                    key=lambda ally: ally.health
                    / max(1, self.catalog.creatures[ally.definition_id].max_health),
                )
                target_definition = self.catalog.creatures[target.definition_id]
                before = target.health
                target.health = min(
                    target_definition.max_health,
                    target.health + max(1, definition.support_power),
                )
                restored = target.health - before
                lines.append(
                    f"[Support] The repair field restores {restored} health to {target_definition.name}."
                )
                events.append(
                    DomainEvent(
                        "combat.support_action",
                        {
                            "supporter": creature.instance_id,
                            "target": target.instance_id,
                            "health_restored": restored,
                            "independent_clock": True,
                        },
                    )
                )
                if stats is not None:
                    stats.enemy_healing_completed += 1
                return BattleResolution(tuple(lines), tuple(events), True)
            intent = "covering_fire"
            actor.current_intent = intent

        if intent == "retreat":
            state.creatures[state.character.room_id].remove(creature)
            state.battle.actors.pop(actor.actor_id, None)
            state.battle.effects.pop(actor.actor_id, None)
            lines.append(
                f"{definition.name.capitalize()} withdraws from the encounter without yielding loot or field insight."
            )
            events.append(
                DomainEvent(
                    "combat.enemy_withdrew",
                    {"target": creature.instance_id, "profile": definition.behavior_profile},
                )
            )
            return BattleResolution(tuple(lines), tuple(events), True)

        if intent in {"brace", "protect_ally", "reposition", "disrupt", "track", "direct_focus"}:
            return self._resolve_pressure_action(
                state, actor, creature, definition, intent, lines, events, now
            )

        return self._resolve_enemy_attack(
            state, actor, creature, definition, intent, lines, events, now
        )

    def _resolve_pressure_action(
        self,
        state: GameState,
        actor: CombatActorState,
        creature: "CreatureState",
        definition: "CreatureDefinition",
        intent: str,
        lines: list[str],
        events: list[DomainEvent],
        now: float,
    ) -> BattleResolution:
        battle = state.battle
        stats = battle.encounter
        if stats is not None:
            stats.pressure_actions += 1
        payload: dict[str, object] = {
            "actor": creature.instance_id,
            "intent": intent,
            "profile": definition.behavior_profile,
        }
        if intent == "brace":
            self._apply_effect(
                battle,
                actor_id=actor.actor_id,
                name="protected",
                magnitude=8,
                duration=7,
                source_actor_id=actor.actor_id,
            )
            lines.append("[Protected] It gains 8 defense against the next committed attack.")
        elif intent == "protect_ally":
            live = self.engine._live_creatures(state)
            target = next(
                (
                    ally
                    for ally in live
                    if self.catalog.creatures[ally.definition_id].behavior_profile
                    == "support"
                    and ally.instance_id != creature.instance_id
                ),
                None,
            )
            if target is None:
                target = min(
                    (ally for ally in live if ally.instance_id != creature.instance_id),
                    default=creature,
                    key=lambda ally: ally.health
                    / max(1, self.catalog.creatures[ally.definition_id].max_health),
                )
            target_id = creature_actor_id(target.instance_id)
            self._apply_effect(
                battle,
                actor_id=target_id,
                name="protected",
                magnitude=8,
                duration=8,
                source_actor_id=actor.actor_id,
            )
            target_name = self.catalog.creatures[target.definition_id].name
            lines.append(
                f"[Protected] {definition.name.capitalize()} covers {target_name}, adding 8 defense to its next exchange."
            )
            payload["target"] = target.instance_id
        elif intent == "reposition":
            self._apply_effect(
                battle,
                actor_id=player_actor_id(),
                name="off_balance",
                magnitude=6,
                duration=6,
                source_actor_id=actor.actor_id,
            )
            self._apply_effect(
                battle,
                actor_id=actor.actor_id,
                name="protected",
                magnitude=4,
                duration=5,
                source_actor_id=actor.actor_id,
            )
            lines.append(
                "[Off-balance] The lateral cut reduces your next defensive read by 6 unless the state expires."
            )
        elif intent == "disrupt":
            self._apply_effect(
                battle,
                actor_id=player_actor_id(),
                name="suppressed",
                magnitude=8,
                duration=7,
                source_actor_id=actor.actor_id,
            )
            self._apply_effect(
                battle,
                actor_id=player_actor_id(),
                name="pinned",
                magnitude=6,
                duration=6,
                source_actor_id=actor.actor_id,
            )
            state.character.roundtime_until = min(
                max(state.character.roundtime_until, now) + 1,
                now + 12,
            )
            lines.append(
                "[Suppressed] The control field costs 8 offense on your next attack and adds 1 second of server recovery."
            )
        elif intent == "track":
            self._apply_effect(
                battle,
                actor_id=player_actor_id(),
                name="read",
                magnitude=8,
                duration=8,
                source_actor_id=actor.actor_id,
            )
            lines.append(
                "[Read] The hunter studies your command rhythm; vary the next tactic to deny its counter bonus."
            )
        elif intent == "direct_focus":
            target_id = actor.target_id or player_actor_id()
            focused_allies: list[str] = []
            for ally in battle.actors.values():
                if ally.kind != "creature":
                    continue
                ally.target_id = target_id
                self._apply_effect(
                    battle,
                    actor_id=ally.actor_id,
                    name="focused",
                    magnitude=6,
                    duration=8,
                    source_actor_id=actor.actor_id,
                )
                focused_allies.append(ally.actor_id)
            target_name = self._actor_name(state, target_id, self.catalog)
            lines.append(
                f"[Formation focus] The commander assigns {target_name} as the shared target; ready allies gain 6 offense."
            )
            payload["target"] = target_id
            payload["focused_allies"] = focused_allies
        events.append(DomainEvent("combat.pressure_action", payload))
        return BattleResolution(tuple(lines), tuple(events), True)

    def _resolve_enemy_attack(
        self,
        state: GameState,
        actor: CombatActorState,
        creature: "CreatureState",
        definition: "CreatureDefinition",
        intent: str,
        lines: list[str],
        events: list[DomainEvent],
        now: float,
    ) -> BattleResolution:
        offense_bonus = {
            "rush": 5,
            "press_wound": 9,
            "reckless_strike": 14,
            "heavy_strike": 6,
            "quick_strike": 3,
            "covering_fire": -2,
            "aim_wound": 8,
            "exploit_pattern": 12,
            "command_strike": 7,
            "disruptive_pulse": 10,
            "command_barrage": 12,
        }.get(intent, 0)
        damage_bonus = {
            "press_wound": 1,
            "reckless_strike": 3,
            "heavy_strike": 2,
            "aim_wound": 1,
            "command_barrage": 2,
        }.get(intent, 0)
        focused = self._consume_effect(state.battle, actor.actor_id, "focused")
        if focused is not None:
            offense_bonus += abs(focused.magnitude)
        suppressed = self._consume_effect(state.battle, actor.actor_id, "suppressed")
        if suppressed is not None:
            offense_bonus -= abs(suppressed.magnitude)
        read = self._consume_effect(state.battle, player_actor_id(), "read")
        if read is not None and intent in {"aim_wound", "exploit_pattern", "quick_strike"}:
            offense_bonus += abs(read.magnitude)
            lines.append(
                f"[Pattern counter] The stored read grants {abs(read.magnitude)} offense against the repeated tactic."
            )
        if intent == "reckless_strike":
            self._apply_effect(
                state.battle,
                actor_id=actor.actor_id,
                name="exposed",
                magnitude=3,
                duration=8,
                source_actor_id=actor.actor_id,
            )
        modified = replace(
            definition,
            offense=max(0, definition.offense + offense_bonus),
            damage_min=max(1, definition.damage_min + damage_bonus),
            damage_max=max(1, definition.damage_max + damage_bonus),
        )
        target_id = actor.target_id or player_actor_id()
        if target_id.startswith("companion:"):
            return self._enemy_attack_sol(
                state, actor, creature, modified, intent, lines, events, now
            )

        armor = equipped_item(state.character, self.catalog.items, "body")
        weapon = equipped_item(state.character, self.catalog.items, "main_hand")
        baseline = (
            state.character.health,
            [Wound(w.location, w.severity, w.bleeding) for w in state.character.wounds],
            state.character.condition_pulse_at,
            state.character.stunned_until,
            state.character.prone,
        )
        outcome = resolve_creature_attack(
            modified,
            state.character,
            armor,
            self.rng,
            weapon,
            opponent_count=1,
        )
        if definition.id == "sol_confrontation" and outcome.hit:
            outcome = replace(
                outcome,
                damage=min(outcome.damage, 6),
                severity=min(outcome.severity, 3),
                critical=(
                    "Sol's controlled answer finds the opening without turning the lesson into a disabling impact."
                ),
            )
        if outcome.hit and intent == "aim_wound" and state.character.wounds:
            location = max(
                state.character.wounds,
                key=lambda wound: (wound.severity, wound.bleeding),
            ).location
            outcome = replace(outcome, location=location)
        lines.append(
            f"[Roll {outcome.roll:+d} + Offense {outcome.offense} - Defense {outcome.defense} = {outcome.endroll}]"
        )
        if state.character.defense_mode is not DefenseMode.BALANCED:
            lines.append(f"Defensive reaction: {outcome.reaction_effect}.")
        if not outcome.hit:
            lines.append("You keep the independently timed attack outside your guard.")
            events.append(
                DomainEvent(
                    "combat.enemy_attack_resolved",
                    {
                        "attacker": creature.instance_id,
                        "intent": intent,
                        "target": state.character.key,
                        "hit": False,
                        "endroll": outcome.endroll,
                        "independent_clock": True,
                    },
                )
            )
            return BattleResolution(tuple(lines), tuple(events), True)

        companion, progress = self._active_sol(state, now)
        incoming = outcome.damage
        companion_intercepted = 0
        if companion is not None and progress is not None and progress.health > 0:
            intercept_capacity = {
                "balanced": max(1, 2 + progress.level // 2),
                "guard": max(2, companion.power + progress.level),
                "assault": 0,
            }[progress.order]
            companion_intercepted = min(incoming, intercept_capacity)
            if companion_intercepted:
                progress.health = max(0, progress.health - companion_intercepted)
                progress.damage_intercepted = min(
                    100_000_000,
                    progress.damage_intercepted + companion_intercepted,
                )
                incoming -= companion_intercepted
                lines.append(
                    f"Sol intercepts {companion_intercepted} damage under {progress.order} ({progress.health}/{progress.max_health} integrity)."
                )
                events.append(
                    DomainEvent(
                        "combat.companion_intercepted",
                        {
                            "companion_id": companion.id,
                            "order": progress.order,
                            "damage": companion_intercepted,
                            "health": progress.health,
                            "independent_clock": True,
                        },
                    )
                )
                if progress.health <= 0:
                    progress.downed_until = now + 30
                    sol_actor = state.battle.actors.get(companion_actor_id("sol"))
                    if sol_actor is not None:
                        sol_actor.interrupted_until = max(
                            sol_actor.interrupted_until, state.battle.time + 6
                        )
                    lines.append(
                        "Sol is forced out of the exchange for 30 real seconds and at least 6 field seconds."
                    )
                    events.append(
                        DomainEvent(
                            "companion.downed",
                            {
                                "companion_id": companion.id,
                                "recover_at": progress.downed_until,
                            },
                        )
                    )
        protected = self._consume_effect(state.battle, player_actor_id(), "protected")
        protection = min(incoming, abs(protected.magnitude)) if protected else 0
        if protection:
            incoming -= protection
            lines.append(f"Your protected lane absorbs {protection} damage.")
        guard_absorbed = min(state.character.guard_points, incoming)
        if guard_absorbed:
            state.character.guard_points -= guard_absorbed
            incoming -= guard_absorbed
            lines.append(
                f"Prepared guard absorbs {guard_absorbed} damage ({state.character.guard_points} guard remains)."
            )
        effective_damage = max(0, incoming)
        state.character.health -= effective_damage
        if effective_damage and outcome.severity >= 2 and outcome.location:
            bleeding_before = active_bleeding(state.character)
            wound = Wound(
                outcome.location,
                outcome.severity,
                max(0, outcome.severity - 2),
            )
            state.character.wounds.append(wound)
            if bleeding_before == 0 and wound.bleeding:
                state.character.condition_pulse_at = now
        lines.append(
            f"{outcome.critical} ({effective_damage} damage after protection; critical {outcome.severity})"
        )
        if outcome.absorbed:
            lines.append(f"Your {outcome.reaction} absorbs {outcome.absorbed} damage.")
        events.append(
            DomainEvent(
                "combat.enemy_attack_resolved",
                {
                    "attacker": creature.instance_id,
                    "intent": intent,
                    "target": state.character.key,
                    "hit": True,
                    "damage": effective_damage,
                    "guard_absorbed": guard_absorbed,
                    "protected_absorbed": protection,
                    "companion_intercepted": companion_intercepted,
                    "location": outcome.location,
                    "severity": outcome.severity,
                    "endroll": outcome.endroll,
                    "independent_clock": True,
                },
            )
        )
        # Preserve the established character-hit event contract for analytics,
        # tutorials, and downstream systems while adding the independent-clock event.
        events.append(
            DomainEvent(
                "combat.character_hit",
                {
                    "damage": effective_damage,
                    "guard_absorbed": guard_absorbed,
                    "protected_absorbed": protection,
                    "companion_intercepted": companion_intercepted,
                    "location": outcome.location,
                    "severity": outcome.severity,
                    "opponent_count": 1,
                    "pressure_penalty": 0,
                    "attacker": creature.instance_id,
                    "intent": intent,
                    "independent_clock": True,
                },
            )
        )
        impact = apply_impact_condition(
            state.character,
            severity=outcome.severity,
            location=outcome.location,
            now=now,
        )
        if impact.stun_seconds:
            lines.append(f"The impact stuns you for {impact.stun_seconds} seconds.")
            events.append(
                DomainEvent(
                    "condition.stunned",
                    {
                        "seconds": impact.stun_seconds,
                        "location": outcome.location,
                        "simulated": definition.nonlethal,
                    },
                )
            )
        if impact.knocked_down:
            lines.append("The force knocks you prone.")
            events.append(
                DomainEvent(
                    "condition.knocked_down",
                    {
                        "location": outcome.location,
                        "simulated": definition.nonlethal,
                    },
                )
            )
        armor_state = self.engine._equipped_item_state(state, "body")
        if (
            not definition.nonlethal
            and outcome.severity >= 2
            and armor_state is not None
            and armor_state.durability is not None
            and armor_state.durability > 0
        ):
            before = armor_state.durability
            loss = max(1, outcome.severity - 1)
            if definition.attack_profile == "heavy":
                loss += 1
            armor_state.durability = max(0, armor_state.durability - loss)
            armor_name = self.catalog.items[armor_state.definition_id].name
            lines.append(
                f"Your {armor_name} loses {before - armor_state.durability} durability ({armor_state.durability} remaining)."
            )
            events.append(
                DomainEvent(
                    "item.durability_changed",
                    {
                        "instance_id": armor_state.instance_id,
                        "before": before,
                        "after": armor_state.durability,
                        "cause_profile": definition.attack_profile,
                    },
                )
            )
        if intent in {"press_wound", "command_barrage", "disruptive_pulse"} and outcome.hit:
            self._apply_effect(
                state.battle,
                actor_id=player_actor_id(),
                name="pinned" if intent == "press_wound" else "suppressed",
                magnitude=6 if intent == "press_wound" else 8,
                duration=7,
                source_actor_id=actor.actor_id,
            )
        if definition.nonlethal and outcome.hit:
            knockout = state.character.health <= 0
            restore = definition.id != "sol_confrontation" or knockout
            if restore:
                (
                    state.character.health,
                    state.character.wounds,
                    state.character.condition_pulse_at,
                    state.character.stunned_until,
                    state.character.prone,
                ) = baseline
                lines.append(
                    "The nonlethal interlock discards the simulated injury and restores your pre-drill condition; the timed lesson remains recorded."
                )
                events.append(
                    DomainEvent(
                        "combat.diagnostic_character_reset",
                        {"target": creature.instance_id, "knockout_threshold": knockout},
                    )
                )
        elif state.character.health <= 0:
            events.extend(
                self.engine._incapacitate(
                    state,
                    now,
                    lines,
                    cause=definition.id,
                )
            )
        return BattleResolution(tuple(lines), tuple(events), True)

    def _enemy_attack_sol(
        self,
        state: GameState,
        actor: CombatActorState,
        creature: "CreatureState",
        definition: "CreatureDefinition",
        intent: str,
        lines: list[str],
        events: list[DomainEvent],
        now: float,
    ) -> BattleResolution:
        companion, progress = self._active_sol(state, now)
        if companion is None or progress is None or progress.health <= 0:
            actor.target_id = player_actor_id()
            return self._resolve_enemy_attack(
                state,
                actor,
                creature,
                definition,
                intent,
                lines,
                events,
                now,
            )
        roll = open_d100(self.rng)
        defense = 52 + progress.level * 3 + (10 if progress.order == "guard" else 0)
        endroll = roll + definition.offense - defense
        hit = endroll >= 100
        damage = 0
        if hit:
            damage = max(
                1,
                self.rng.randint(definition.damage_min, definition.damage_max)
                - max(0, progress.level // 4),
            )
            progress.health = max(0, progress.health - damage)
        lines.append(
            f"[Roll {roll:+d} + Offense {definition.offense} - Sol defense {defense} = {endroll}]"
        )
        if hit:
            lines.append(
                f"The attack changes targets and deals {damage} integrity damage to Sol ({progress.health}/{progress.max_health})."
            )
        else:
            lines.append("Sol keeps the redirected attack outside his Akari-line guard.")
        events.append(
            DomainEvent(
                "combat.enemy_attack_resolved",
                {
                    "attacker": creature.instance_id,
                    "intent": intent,
                    "target": companion.id,
                    "hit": hit,
                    "damage": damage,
                    "endroll": endroll,
                    "independent_clock": True,
                },
            )
        )
        if progress.health <= 0:
            progress.downed_until = now + 30
            sol_actor = state.battle.actors.get(companion_actor_id("sol"))
            if sol_actor is not None:
                sol_actor.interrupted_until = max(
                    sol_actor.interrupted_until, state.battle.time + 6
                )
            lines.append(
                "Sol is downed, but his story partnership remains recoverable after 30 real seconds."
            )
            events.append(
                DomainEvent(
                    "companion.downed",
                    {"companion_id": companion.id, "recover_at": progress.downed_until},
                )
            )
        return BattleResolution(tuple(lines), tuple(events), True)

    def withdrawal_penalty(self, state: GameState) -> int:
        pinned = self._effect(state.battle, player_actor_id(), "pinned")
        return abs(pinned.magnitude) if pinned is not None else 0

    def consume_withdrawal_penalty(self, state: GameState) -> int:
        pinned = self._consume_effect(state.battle, player_actor_id(), "pinned")
        return abs(pinned.magnitude) if pinned is not None else 0

    def complete_encounter(self, state: GameState) -> BattleResolution:
        battle = state.battle
        stats = battle.encounter
        if stats is None:
            return BattleResolution()
        distinct_actions = len(set(stats.player_actions))
        tactical_score = (
            stats.interrupted_charges
            + stats.armor_openings
            + stats.enemy_healing_prevented
            + (1 if distinct_actions >= 3 else 0)
            + (1 if stats.partner_synchrony else 0)
        )
        bonus = min(4, tactical_score)
        lines = ["Victory review:"]
        lines.append(f"• Hostile actions resolved independently: {stats.hostile_actions}")
        lines.append(f"• Sol independent actions: {stats.sol_actions}")
        lines.append(f"• Distinct player tactics recorded: {distinct_actions}")
        lines.append(f"• Charged attacks interrupted: {stats.interrupted_charges}")
        lines.append(f"• Enemy healing prevented/completed: {stats.enemy_healing_prevented}/{stats.enemy_healing_completed}")
        if bonus:
            award_field_insight(
                state.character.experience, bonus, self.engine.clock.now()
            )
            lines.append(f"• Tactical field insight earned: {bonus}")
        else:
            lines.append("• Tactical field insight earned: 0 (victory rewards remain unchanged)")
        review = list(lines)
        battle.last_victory_review = review
        encounter_id = stats.encounter_id
        battle.room_id = None
        battle.actors.clear()
        battle.effects.clear()
        battle.player_action_history.clear()
        battle.encounter = None
        return BattleResolution(
            tuple(lines),
            (
                DomainEvent(
                    "combat.victory_review",
                    {
                        "encounter_id": encounter_id,
                        "hostile_actions": stats.hostile_actions,
                        "sol_actions": stats.sol_actions,
                        "distinct_player_actions": distinct_actions,
                        "interrupted_charges": stats.interrupted_charges,
                        "healing_prevented": stats.enemy_healing_prevented,
                        "field_insight": bonus,
                    },
                ),
            ),
            True,
        )
