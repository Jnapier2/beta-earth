"""Command-resolved battlefield time and independently scheduled actor state."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any


TACTICAL_EFFECT_NAMES = frozenset(
    {
        "opening",
        "exposed",
        "off_balance",
        "pinned",
        "focused",
        "suppressed",
        "protected",
        "read",
    }
)
ACTOR_KINDS = frozenset({"player", "companion", "creature"})
MAX_BATTLE_ACTORS = 64
MAX_EFFECTS_PER_ACTOR = 16
MAX_ACTION_HISTORY = 8


def player_actor_id() -> str:
    return "player"


def companion_actor_id(companion_id: str) -> str:
    return f"companion:{companion_id}"


def creature_actor_id(instance_id: str) -> str:
    return f"creature:{instance_id}"


@dataclass(slots=True)
class TacticalEffectState:
    name: str
    magnitude: int
    expires_at: float
    source_actor_id: str
    uses_remaining: int = 1

    def __post_init__(self) -> None:
        if self.name not in TACTICAL_EFFECT_NAMES:
            raise ValueError(f"unknown tactical effect {self.name!r}")
        if not -100 <= self.magnitude <= 100:
            raise ValueError("tactical effect magnitude must be between -100 and 100")
        if not math.isfinite(self.expires_at) or self.expires_at < 0:
            raise ValueError("tactical effect expiry is invalid")
        if not self.source_actor_id:
            raise ValueError("tactical effect source actor is required")
        if not 1 <= self.uses_remaining <= 20:
            raise ValueError("tactical effect uses must be between 1 and 20")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "magnitude": self.magnitude,
            "expires_at": self.expires_at,
            "source_actor_id": self.source_actor_id,
            "uses_remaining": self.uses_remaining,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TacticalEffectState":
        if not isinstance(value, dict):
            raise ValueError("tactical effect must be an object")
        return cls(
            name=str(value["name"]),
            magnitude=int(value.get("magnitude", 0)),
            expires_at=float(value.get("expires_at", 0.0)),
            source_actor_id=str(value.get("source_actor_id", "unknown")),
            uses_remaining=int(value.get("uses_remaining", 1)),
        )


@dataclass(slots=True)
class CombatActorState:
    actor_id: str
    kind: str
    next_action_at: float = 0.0
    current_intent: str | None = None
    target_id: str | None = None
    recovery_duration: float = 0.0
    interrupted_until: float = 0.0
    telegraph_shown: bool = False
    actions_taken: int = 0

    def __post_init__(self) -> None:
        if not self.actor_id:
            raise ValueError("combat actor ID is required")
        if self.kind not in ACTOR_KINDS:
            raise ValueError(f"invalid combat actor kind {self.kind!r}")
        for field_name, value in (
            ("next_action_at", self.next_action_at),
            ("recovery_duration", self.recovery_duration),
            ("interrupted_until", self.interrupted_until),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"combat actor {field_name} is invalid")
        if self.actions_taken < 0:
            raise ValueError("combat actor action count cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "kind": self.kind,
            "next_action_at": self.next_action_at,
            "current_intent": self.current_intent,
            "target_id": self.target_id,
            "recovery_duration": self.recovery_duration,
            "interrupted_until": self.interrupted_until,
            "telegraph_shown": self.telegraph_shown,
            "actions_taken": self.actions_taken,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CombatActorState":
        if not isinstance(value, dict):
            raise ValueError("combat actor must be an object")
        current_intent = value.get("current_intent")
        target_id = value.get("target_id")
        return cls(
            actor_id=str(value["actor_id"]),
            kind=str(value["kind"]),
            next_action_at=float(value.get("next_action_at", 0.0)),
            current_intent=(str(current_intent) if current_intent else None),
            target_id=(str(target_id) if target_id else None),
            recovery_duration=float(value.get("recovery_duration", 0.0)),
            interrupted_until=float(value.get("interrupted_until", 0.0)),
            telegraph_shown=bool(value.get("telegraph_shown", False)),
            actions_taken=max(0, int(value.get("actions_taken", 0))),
        )


@dataclass(slots=True)
class EncounterStatsState:
    encounter_id: str
    room_id: str
    started_at: float
    player_actions: list[str] = field(default_factory=list)
    interrupted_charges: int = 0
    armor_openings: int = 0
    partner_synchrony: int = 0
    enemy_healing_prevented: int = 0
    enemy_healing_completed: int = 0
    hostile_actions: int = 0
    sol_actions: int = 0
    pressure_actions: int = 0
    tactical_successes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "encounter_id": self.encounter_id,
            "room_id": self.room_id,
            "started_at": self.started_at,
            "player_actions": list(self.player_actions[-MAX_ACTION_HISTORY:]),
            "interrupted_charges": self.interrupted_charges,
            "armor_openings": self.armor_openings,
            "partner_synchrony": self.partner_synchrony,
            "enemy_healing_prevented": self.enemy_healing_prevented,
            "enemy_healing_completed": self.enemy_healing_completed,
            "hostile_actions": self.hostile_actions,
            "sol_actions": self.sol_actions,
            "pressure_actions": self.pressure_actions,
            "tactical_successes": list(self.tactical_successes[-20:]),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EncounterStatsState":
        if not isinstance(value, dict):
            raise ValueError("encounter stats must be an object")
        player_actions = value.get("player_actions", [])
        tactical_successes = value.get("tactical_successes", [])
        if not isinstance(player_actions, list) or not all(
            isinstance(item, str) for item in player_actions
        ):
            raise ValueError("encounter player actions must be a list of strings")
        if not isinstance(tactical_successes, list) or not all(
            isinstance(item, str) for item in tactical_successes
        ):
            raise ValueError("encounter tactical successes must be a list of strings")
        return cls(
            encounter_id=str(value["encounter_id"]),
            room_id=str(value["room_id"]),
            started_at=float(value.get("started_at", 0.0)),
            player_actions=[str(item) for item in player_actions[-MAX_ACTION_HISTORY:]],
            interrupted_charges=max(0, int(value.get("interrupted_charges", 0))),
            armor_openings=max(0, int(value.get("armor_openings", 0))),
            partner_synchrony=max(0, int(value.get("partner_synchrony", 0))),
            enemy_healing_prevented=max(0, int(value.get("enemy_healing_prevented", 0))),
            enemy_healing_completed=max(0, int(value.get("enemy_healing_completed", 0))),
            hostile_actions=max(0, int(value.get("hostile_actions", 0))),
            sol_actions=max(0, int(value.get("sol_actions", 0))),
            pressure_actions=max(0, int(value.get("pressure_actions", 0))),
            tactical_successes=[str(item) for item in tactical_successes[-20:]],
        )


@dataclass(slots=True)
class BattleState:
    """Persisted command-resolved combat clock and bounded encounter state."""

    time: float = 0.0
    room_id: str | None = None
    encounter_serial: int = 0
    actors: dict[str, CombatActorState] = field(default_factory=dict)
    effects: dict[str, dict[str, TacticalEffectState]] = field(default_factory=dict)
    player_action_history: list[str] = field(default_factory=list)
    encounter: EncounterStatsState | None = None
    last_victory_review: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "room_id": self.room_id,
            "encounter_serial": self.encounter_serial,
            "actors": {
                actor_id: actor.to_dict()
                for actor_id, actor in sorted(self.actors.items())
            },
            "effects": {
                actor_id: {
                    name: effect.to_dict()
                    for name, effect in sorted(actor_effects.items())
                }
                for actor_id, actor_effects in sorted(self.effects.items())
            },
            "player_action_history": list(
                self.player_action_history[-MAX_ACTION_HISTORY:]
            ),
            "encounter": self.encounter.to_dict() if self.encounter else None,
            "last_victory_review": list(self.last_victory_review[-12:]),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "BattleState":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("battle state must be an object")
        raw_actors = value.get("actors", {})
        raw_effects = value.get("effects", {})
        raw_history = value.get("player_action_history", [])
        raw_review = value.get("last_victory_review", [])
        raw_encounter = value.get("encounter")
        if not isinstance(raw_actors, dict) or len(raw_actors) > MAX_BATTLE_ACTORS:
            raise ValueError("battle actors must be a bounded object")
        if not isinstance(raw_effects, dict) or len(raw_effects) > MAX_BATTLE_ACTORS:
            raise ValueError("battle effects must be a bounded object")
        if not isinstance(raw_history, list) or not all(
            isinstance(item, str) for item in raw_history
        ):
            raise ValueError("battle action history must be a list of strings")
        if not isinstance(raw_review, list) or not all(
            isinstance(item, str) for item in raw_review
        ):
            raise ValueError("battle victory review must be a list of strings")
        actors = {
            str(actor_id): CombatActorState.from_dict(actor)
            for actor_id, actor in raw_actors.items()
        }
        effects: dict[str, dict[str, TacticalEffectState]] = {}
        for actor_id, actor_effects in raw_effects.items():
            if not isinstance(actor_effects, dict) or len(actor_effects) > MAX_EFFECTS_PER_ACTOR:
                raise ValueError("battle actor effects must be a bounded object")
            effects[str(actor_id)] = {
                str(name): TacticalEffectState.from_dict(effect)
                for name, effect in actor_effects.items()
            }
        room_id = value.get("room_id")
        return cls(
            time=max(0.0, float(value.get("time", 0.0))),
            room_id=(str(room_id) if room_id else None),
            encounter_serial=max(0, int(value.get("encounter_serial", 0))),
            actors=actors,
            effects=effects,
            player_action_history=[str(item) for item in raw_history[-MAX_ACTION_HISTORY:]],
            encounter=(
                EncounterStatsState.from_dict(raw_encounter)
                if isinstance(raw_encounter, dict)
                else None
            ),
            last_victory_review=[str(item) for item in raw_review[-12:]],
        )
