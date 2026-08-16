"""Deterministic, data-driven intent selection for command-resolved combat."""

from __future__ import annotations

from dataclasses import dataclass


CHARGED_INTENTS = frozenset({"disruptive_pulse", "command_barrage"})
ATTACK_INTENTS = frozenset(
    {
        "rush",
        "press_wound",
        "reckless_strike",
        "heavy_strike",
        "quick_strike",
        "covering_fire",
        "aim_wound",
        "exploit_pattern",
        "command_strike",
        "disruptive_pulse",
        "command_barrage",
    }
)
PRESSURE_INTENTS = frozenset(
    {"brace", "protect_ally", "reposition", "disrupt", "track", "direct_focus"}
)
SUPPORT_INTENTS = frozenset({"repair_ally", "retreat"})


@dataclass(frozen=True, slots=True)
class EnemyDecisionContext:
    profile: str
    actions_taken: int
    own_health_ratio: float
    ally_count: int
    injured_ally: bool
    unprotected_support: bool
    player_wounded: bool
    player_prone: bool
    player_repeating: bool
    player_recent_heal: bool
    sol_available: bool
    sol_order: str


def choose_enemy_intent(context: EnemyDecisionContext) -> str:
    """Select one readable intent without hidden randomness.

    Determinism keeps save/load, replays, and tests auditable. Creature data
    supplies the profile and action interval; the current battlefield supplies
    the tactical context.
    """

    profile = context.profile
    if profile == "aggressor":
        if context.own_health_ratio <= 0.30:
            return "reckless_strike"
        if context.player_prone or context.player_wounded:
            return "press_wound"
        return "rush"
    if profile == "defender":
        if context.unprotected_support:
            return "protect_ally"
        if context.own_health_ratio <= 0.50 or context.actions_taken % 3 == 1:
            return "brace"
        return "heavy_strike"
    if profile == "skirmisher":
        return "reposition" if context.actions_taken % 2 == 0 else "quick_strike"
    if profile == "controller":
        if context.player_recent_heal or context.actions_taken % 3 == 0:
            return "disruptive_pulse"
        return "disrupt"
    if profile == "support":
        if context.injured_ally:
            return "repair_ally"
        if context.own_health_ratio <= 0.25 and context.ally_count <= 1:
            return "retreat"
        return "covering_fire"
    if profile == "hunter":
        if context.player_repeating:
            return "exploit_pattern"
        if context.player_wounded:
            return "aim_wound"
        return "track" if context.actions_taken % 2 == 0 else "quick_strike"
    if profile == "commander":
        if context.ally_count > 1 and context.actions_taken % 3 == 0:
            return "direct_focus"
        if context.unprotected_support:
            return "protect_ally"
        if context.actions_taken % 4 == 2:
            return "command_barrage"
        return "command_strike"
    return "quick_strike"


def enemy_recovery_seconds(base_interval: int, intent: str) -> int:
    delta = {
        "quick_strike": -1,
        "rush": -1,
        "reposition": -1,
        "brace": -1,
        "track": -1,
        "repair_ally": 0,
        "protect_ally": 0,
        "direct_focus": 0,
        "heavy_strike": 1,
        "press_wound": 0,
        "reckless_strike": 0,
        "disrupt": 0,
        "covering_fire": 0,
        "aim_wound": 1,
        "exploit_pattern": 0,
        "command_strike": 0,
        "disruptive_pulse": 2,
        "command_barrage": 2,
        "retreat": 0,
    }.get(intent, 0)
    return max(2, min(9, int(base_interval) + delta))


def sol_recovery_seconds(order: str) -> int:
    return {"balanced": 4, "guard": 3, "assault": 3}.get(order, 4)


def intent_telegraph(
    *,
    actor_name: str,
    profile: str,
    intent: str,
    target_name: str,
) -> str:
    actor = actor_name.capitalize()
    lines = {
        "rush": f"{actor} lowers its center and prepares to rush {target_name}.",
        "press_wound": f"{actor} fixes on {target_name}'s injuries and prepares to press the weakness.",
        "reckless_strike": f"{actor} abandons its guard for a desperate finishing drive.",
        "heavy_strike": f"{actor} braces behind its protection and loads a heavy counterstroke.",
        "brace": f"{actor} seals its stance and prepares to absorb the next committed hit.",
        "protect_ally": f"{actor} angles across the lane to protect a more valuable ally.",
        "reposition": f"{actor} begins a lateral cut meant to pull {target_name} off balance.",
        "quick_strike": f"{actor} tests the near lane for a fast strike on {target_name}.",
        "disrupt": f"{actor} tunes a control field to suppress {target_name}'s next action.",
        "disruptive_pulse": f"{actor} begins charging a disruptive pulse; a hard hit can break the focus.",
        "repair_ally": f"{actor} raises a repair field toward its most damaged ally.",
        "covering_fire": f"{actor} backs behind the formation and lines up covering fire.",
        "retreat": f"{actor} searches for an exit rather than another exchange.",
        "track": f"{actor} studies {target_name}'s command rhythm instead of committing.",
        "aim_wound": f"{actor} tracks a wounded limb and prepares a measured shot.",
        "exploit_pattern": f"{actor} has read the repeated tactic and prepares its counter.",
        "direct_focus": f"{actor} signals the formation to concentrate on {target_name}.",
        "command_strike": f"{actor} advances behind coordinated pressure for a command strike.",
        "command_barrage": f"{actor} gathers the formation into a charged synchronized barrage.",
    }
    return lines.get(
        intent,
        f"{actor} prepares a {profile} action against {target_name}.",
    )


def timing_description(seconds: float, perception: int) -> str:
    remaining = max(0, int(round(seconds)))
    if perception >= 16:
        return f"{remaining}s"
    if perception >= 11:
        if remaining <= 1:
            return "about 1s"
        return f"about {remaining}s"
    if remaining <= 2:
        return "imminent"
    if remaining <= 4:
        return "soon"
    return "recovering"
