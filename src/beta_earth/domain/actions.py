"""Typed action history and server-authoritative recovery contracts."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


MAX_ACTION_ARGUMENTS = 32
MAX_ACTION_ARGUMENT_LENGTH = 160
_COMMAND_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")


class RecoveryClass(str, Enum):
    """Whether an intent is permitted during hard recovery."""

    SOFT = "soft"
    HARD = "hard"


@dataclass(frozen=True, slots=True)
class ActionIntent:
    """A replayable parsed intent; raw player text is deliberately excluded."""

    command: str
    args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _COMMAND_TOKEN.fullmatch(self.command):
            raise ValueError("action command must be a non-empty lowercase ASCII token")
        if len(self.args) > MAX_ACTION_ARGUMENTS:
            raise ValueError("action contains too many arguments")
        if any(
            not isinstance(argument, str)
            or len(argument) > MAX_ACTION_ARGUMENT_LENGTH
            or any(ord(character) < 32 for character in argument)
            for argument in self.args
        ):
            raise ValueError("action contains an invalid argument")

    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command, "args": list(self.args)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ActionIntent":
        command = value.get("command")
        arguments = value.get("args", [])
        if not isinstance(command, str):
            raise ValueError("action command must be a string")
        if not isinstance(arguments, list) or not all(
            isinstance(argument, str) for argument in arguments
        ):
            raise ValueError("action arguments must be a list of strings")
        return cls(command=command, args=tuple(arguments))


@dataclass(frozen=True, slots=True)
class QueuedAction:
    """One hard intent scheduled for the end of the current recovery window."""

    intent: ActionIntent
    queued_at: float
    execute_at: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.queued_at) or self.queued_at < 0:
            raise ValueError("queued action timestamp is invalid")
        if not math.isfinite(self.execute_at) or self.execute_at < self.queued_at:
            raise ValueError("queued action execution timestamp is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "queued_at": self.queued_at,
            "execute_at": self.execute_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "QueuedAction":
        intent = value.get("intent")
        if not isinstance(intent, dict):
            raise ValueError("queued action intent must be an object")
        return cls(
            intent=ActionIntent.from_dict(intent),
            queued_at=float(value["queued_at"]),
            execute_at=float(value["execute_at"]),
        )


class EffectDurationType(str, Enum):
    """How an effect's lifetime is measured."""

    ACTIONS = "actions"
    FIELD_SECONDS = "field_seconds"
    UNTIL_CONSUMED = "until_consumed"
    PERMANENT = "permanent"


class PersistencePolicy(str, Enum):
    """Whether an effect survives encounter, room, or save boundaries."""

    TRANSIENT = "transient"
    ENCOUNTER = "encounter"
    CHARACTER = "character"
    WORLD = "world"


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Data contract shared by commands, AI planning, and future transports."""

    id: str
    recovery: RecoveryClass
    base_recovery_seconds: float | None = None
    preparation_seconds: float = 0.0
    resource_costs: tuple[tuple[str, int], ...] = ()
    equipment_requirements: tuple[str, ...] = ()
    allowed_states: tuple[str, ...] = ("ready",)
    interrupt_rules: tuple[str, ...] = ()
    emitted_effect_ids: tuple[str, ...] = ()
    emitted_event_kinds: tuple[str, ...] = ()
    ai_tags: tuple[str, ...] = ()
    source_provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _COMMAND_TOKEN.fullmatch(self.id):
            raise ValueError("action spec ID must be a lowercase ASCII token")
        if self.base_recovery_seconds is not None and (
            not math.isfinite(self.base_recovery_seconds)
            or self.base_recovery_seconds < 0
            or self.base_recovery_seconds > 600
        ):
            raise ValueError("action base recovery must be null or 0-600 seconds")
        if (
            not math.isfinite(self.preparation_seconds)
            or self.preparation_seconds < 0
            or self.preparation_seconds > 600
        ):
            raise ValueError("action preparation time must be 0-600 seconds")
        resource_names = [name for name, _ in self.resource_costs]
        if len(resource_names) != len(set(resource_names)):
            raise ValueError("action resource costs must have unique names")
        if any(not name or amount < 0 for name, amount in self.resource_costs):
            raise ValueError("action resource costs must be named and non-negative")
        for values, label in (
            (self.equipment_requirements, "equipment requirement"),
            (self.allowed_states, "allowed state"),
            (self.interrupt_rules, "interrupt rule"),
            (self.emitted_effect_ids, "effect ID"),
            (self.emitted_event_kinds, "event kind"),
            (self.ai_tags, "AI tag"),
            (self.source_provenance, "source provenance"),
        ):
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"action {label} entries must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "recovery": self.recovery.value,
            "base_recovery_seconds": self.base_recovery_seconds,
            "preparation_seconds": self.preparation_seconds,
            "resource_costs": {name: value for name, value in self.resource_costs},
            "equipment_requirements": list(self.equipment_requirements),
            "allowed_states": list(self.allowed_states),
            "interrupt_rules": list(self.interrupt_rules),
            "emitted_effect_ids": list(self.emitted_effect_ids),
            "emitted_event_kinds": list(self.emitted_event_kinds),
            "ai_tags": list(self.ai_tags),
            "source_provenance": list(self.source_provenance),
        }


@dataclass(frozen=True, slots=True)
class EffectSpec:
    """Reusable status/effect definition independent of its live state."""

    id: str
    duration_type: EffectDurationType
    default_duration: float = 0.0
    max_stacks: int = 1
    stat_modifiers: tuple[tuple[str, int], ...] = ()
    action_restrictions: tuple[str, ...] = ()
    expiration_triggers: tuple[str, ...] = ()
    dispel_tags: tuple[str, ...] = ()
    persistence: PersistencePolicy = PersistencePolicy.TRANSIENT
    source_provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _COMMAND_TOKEN.fullmatch(self.id):
            raise ValueError("effect spec ID must be a lowercase ASCII token")
        if (
            not math.isfinite(self.default_duration)
            or self.default_duration < 0
            or self.default_duration > 86_400
        ):
            raise ValueError("effect default duration must be 0-86400")
        if not 1 <= self.max_stacks <= 100:
            raise ValueError("effect maximum stacks must be 1-100")
        modifier_names = [name for name, _ in self.stat_modifiers]
        if len(modifier_names) != len(set(modifier_names)):
            raise ValueError("effect modifiers must have unique names")
        if any(not name or not -1000 <= value <= 1000 for name, value in self.stat_modifiers):
            raise ValueError("effect modifiers must be named and bounded")
        if self.duration_type is EffectDurationType.PERMANENT and self.default_duration:
            raise ValueError("permanent effects cannot declare a finite duration")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "duration_type": self.duration_type.value,
            "default_duration": self.default_duration,
            "max_stacks": self.max_stacks,
            "stat_modifiers": {name: value for name, value in self.stat_modifiers},
            "action_restrictions": list(self.action_restrictions),
            "expiration_triggers": list(self.expiration_triggers),
            "dispel_tags": list(self.dispel_tags),
            "persistence": self.persistence.value,
            "source_provenance": list(self.source_provenance),
        }


class ActionSpecRegistry:
    """Immutable-by-contract lookup for action definitions."""

    __slots__ = ("_specs",)

    def __init__(self, specs: tuple[ActionSpec, ...]) -> None:
        values = {spec.id: spec for spec in specs}
        if len(values) != len(specs):
            raise ValueError("action specs must have unique IDs")
        self._specs = values

    def get(self, action_id: str) -> ActionSpec | None:
        return self._specs.get(action_id)

    def require(self, action_id: str) -> ActionSpec:
        spec = self.get(action_id)
        if spec is None:
            raise KeyError(f"unknown action spec {action_id!r}")
        return spec

    def values(self) -> tuple[ActionSpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def to_dict(self) -> dict[str, Any]:
        return {spec.id: spec.to_dict() for spec in self.values()}


class EffectSpecRegistry:
    """Immutable-by-contract lookup for effect definitions."""

    __slots__ = ("_specs",)

    def __init__(self, specs: tuple[EffectSpec, ...]) -> None:
        values = {spec.id: spec for spec in specs}
        if len(values) != len(specs):
            raise ValueError("effect specs must have unique IDs")
        self._specs = values

    def get(self, effect_id: str) -> EffectSpec | None:
        return self._specs.get(effect_id)

    def require(self, effect_id: str) -> EffectSpec:
        spec = self.get(effect_id)
        if spec is None:
            raise KeyError(f"unknown effect spec {effect_id!r}")
        return spec

    def values(self) -> tuple[EffectSpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def to_dict(self) -> dict[str, Any]:
        return {spec.id: spec.to_dict() for spec in self.values()}


def default_tactical_effect_specs() -> EffectSpecRegistry:
    """Return the canonical v0.48 tactical-state definitions as data."""

    provenance = (
        "Gameplay Mechanics In-depth — roundtime/actions",
        "MUDD Game Development v0.51.0 combat audit implementation",
    )
    definitions = (
        EffectSpec("opening", EffectDurationType.UNTIL_CONSUMED, max_stacks=1,
                   stat_modifiers=(("accuracy", 1),), expiration_triggers=("next_attack",),
                   persistence=PersistencePolicy.ENCOUNTER, source_provenance=provenance),
        EffectSpec("exposed", EffectDurationType.UNTIL_CONSUMED, max_stacks=1,
                   stat_modifiers=(("armor", -1),), expiration_triggers=("next_hit",),
                   persistence=PersistencePolicy.ENCOUNTER, source_provenance=provenance),
        EffectSpec("off_balance", EffectDurationType.ACTIONS, default_duration=1, max_stacks=1,
                   stat_modifiers=(("defense", -1),), expiration_triggers=("actor_action",),
                   persistence=PersistencePolicy.ENCOUNTER, source_provenance=provenance),
        EffectSpec("pinned", EffectDurationType.UNTIL_CONSUMED, max_stacks=1,
                   action_restrictions=("movement", "withdrawal"),
                   expiration_triggers=("withdrawal_attempt",),
                   persistence=PersistencePolicy.ENCOUNTER, source_provenance=provenance),
        EffectSpec("focused", EffectDurationType.UNTIL_CONSUMED, max_stacks=1,
                   stat_modifiers=(("interrupt_resistance", 1),),
                   expiration_triggers=("charged_action",),
                   persistence=PersistencePolicy.ENCOUNTER, source_provenance=provenance),
        EffectSpec("suppressed", EffectDurationType.ACTIONS, default_duration=1, max_stacks=1,
                   stat_modifiers=(("offense", -1),), expiration_triggers=("actor_action",),
                   persistence=PersistencePolicy.ENCOUNTER, source_provenance=provenance),
        EffectSpec("protected", EffectDurationType.UNTIL_CONSUMED, max_stacks=1,
                   expiration_triggers=("interception",),
                   persistence=PersistencePolicy.ENCOUNTER, source_provenance=provenance),
        EffectSpec("read", EffectDurationType.ACTIONS, default_duration=1, max_stacks=1,
                   stat_modifiers=(("counter", 1),), expiration_triggers=("counter_attempt",),
                   persistence=PersistencePolicy.ENCOUNTER, source_provenance=provenance),
    )
    return EffectSpecRegistry(definitions)
