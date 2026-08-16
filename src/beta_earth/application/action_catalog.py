"""Derive the unified action registry from the authoritative command parser."""

from __future__ import annotations

from dataclasses import replace

from beta_earth.application.parser import CommandParser
from beta_earth.domain.actions import ActionSpec, ActionSpecRegistry, RecoveryClass


_SOURCE = "Gameplay Mechanics In-depth — Roundtime / Actions"


def build_action_registry(parser: CommandParser) -> ActionSpecRegistry:
    """Create one action contract for every canonical command."""

    specs: dict[str, ActionSpec] = {}
    for command in parser.specs:
        specs[command.name] = ActionSpec(
            id=command.name,
            recovery=command.recovery,
            base_recovery_seconds=(0.0 if command.recovery is RecoveryClass.SOFT else None),
            allowed_states=("ready", "recovering") if command.recovery is RecoveryClass.SOFT else ("ready",),
            ai_tags=(command.recovery.value, "player_command"),
            source_provenance=(_SOURCE,),
        )

    overrides = {
        "attack": dict(
            interrupt_rules=("stun", "disarm", "incapacitation"),
            emitted_event_kinds=("combat.player_attack",),
            ai_tags=("hard", "combat", "offense", "dynamic_recovery"),
        ),
        "ability": dict(
            preparation_seconds=0.0,
            interrupt_rules=("movement", "running", "heavy_armor", "stun"),
            ai_tags=("hard", "ability", "dynamic_recovery", "interruptible"),
        ),
        "equip": dict(
            interrupt_rules=("hostile_pressure",),
            emitted_event_kinds=("equipment.equipped",),
            ai_tags=("hard", "equipment", "dynamic_recovery"),
        ),
        "unequip": dict(
            interrupt_rules=("hostile_pressure",),
            emitted_event_kinds=("equipment.unequipped",),
            ai_tags=("hard", "equipment", "dynamic_recovery"),
        ),
        "stance": dict(
            emitted_event_kinds=("combat.stance_changed",),
            ai_tags=("hard", "combat", "posture", "dynamic_recovery"),
        ),
        "withdraw": dict(
            interrupt_rules=("pinned", "stun", "incapacitation"),
            emitted_event_kinds=("combat.withdrawal_resolved",),
            ai_tags=("hard", "combat", "movement", "dynamic_recovery"),
        ),
        "territory": dict(
            interrupt_rules=("hostile_pressure", "stun", "incapacitation"),
            emitted_event_kinds=("foundation.territory_maintained",),
            ai_tags=("hard", "sovereignty", "territory", "dynamic_recovery"),
        ),
        "faction": dict(
            interrupt_rules=("hostile_pressure", "stun", "incapacitation"),
            emitted_event_kinds=(
                "foundation.faction_pledge_staged",
                "foundation.faction_pledged",
                "foundation.faction_pledge_cancelled",
            ),
            ai_tags=("hard", "sovereignty", "faction", "dynamic_recovery"),
        ),
        "civic": dict(
            interrupt_rules=("hostile_pressure", "stun", "incapacitation"),
            emitted_event_kinds=(
                "foundation.civic_mission_accepted",
                "foundation.civic_plan_selected",
                "foundation.civic_plan_executed",
                "foundation.civic_mission_completed",
            ),
            ai_tags=("hard", "sovereignty", "quest", "territory", "dynamic_recovery"),
        ),
    }
    for action_id, values in overrides.items():
        if action_id in specs:
            specs[action_id] = replace(specs[action_id], **values)
    return ActionSpecRegistry(tuple(specs.values()))
