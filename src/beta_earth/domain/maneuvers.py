"""Original opposed movement maneuvers for contested rooms."""

from __future__ import annotations

from dataclasses import dataclass

from beta_earth.domain.combat import RandomSource, open_d100
from beta_earth.domain.content import CreatureDefinition, ItemDefinition
from beta_earth.domain.model import CharacterState, DefenseMode
from beta_earth.domain.recovery import disabled_limbs


@dataclass(frozen=True, slots=True)
class WithdrawalProfile:
    """Deterministic withdrawal factors shown before the random roll."""

    escape: int
    pressure: int
    normal_roll_needed: int
    opponent_count: int
    crowd_pressure: int
    wound_penalty: int
    disabled_leg_penalty: int
    encumbrance_penalty: int
    armor_penalty: int
    reaction_bonus: int
    companion_bonus: int
    retry_bonus: int


@dataclass(frozen=True, slots=True)
class WithdrawalOutcome:
    roll: int
    escape: int
    pressure: int
    endroll: int
    success: bool
    normal_roll_needed: int
    opponent_count: int
    crowd_pressure: int
    wound_penalty: int
    disabled_leg_penalty: int
    encumbrance_penalty: int
    armor_penalty: int
    reaction_bonus: int
    companion_bonus: int
    retry_bonus: int


def calculate_withdrawal_profile(
    character: CharacterState,
    opponents: tuple[CreatureDefinition, ...],
    armor: ItemDefinition | None,
    *,
    encumbrance_penalty: int = 0,
    companion_bonus: int = 0,
    retry_bonus: int = 0,
) -> WithdrawalProfile:
    """Return exact fixed modifiers without consuming randomness."""

    if not opponents:
        raise ValueError("withdrawal requires at least one active opponent")
    crowd_pressure = min(18, max(0, len(opponents) - 1) * 6)
    pressure = max(opponent.offense for opponent in opponents) + crowd_pressure
    wound_penalty = sum(wound.severity * 2 for wound in character.wounds)
    disabled_leg_penalty = 12 * sum(
        "leg" in location for location in disabled_limbs(character)
    )
    load_penalty = max(0, encumbrance_penalty) * 8
    armor_penalty = 6 if armor and armor.armor_profile == "heavy" else 0
    reaction_bonus = {
        DefenseMode.BALANCED: 0,
        DefenseMode.EVADE: 12,
        DefenseMode.BLOCK: 4,
        DefenseMode.PARRY: 6,
    }[character.defense_mode]
    bounded_companion = max(0, min(20, int(companion_bonus)))
    bounded_retry = max(0, min(18, int(retry_bonus)))
    escape = (
        25
        + character.agility
        + character.level * 2
        + character.combat_skill * 2
        + character.stance.defense_modifier
        + reaction_bonus
        + bounded_companion
        + bounded_retry
        - wound_penalty
        - disabled_leg_penalty
        - load_penalty
        - armor_penalty
    )
    return WithdrawalProfile(
        escape=escape,
        pressure=pressure,
        normal_roll_needed=100 - (escape - pressure),
        opponent_count=len(opponents),
        crowd_pressure=crowd_pressure,
        wound_penalty=wound_penalty,
        disabled_leg_penalty=disabled_leg_penalty,
        encumbrance_penalty=load_penalty,
        armor_penalty=armor_penalty,
        reaction_bonus=reaction_bonus,
        companion_bonus=bounded_companion,
        retry_bonus=bounded_retry,
    )


def resolve_withdrawal(
    character: CharacterState,
    opponents: tuple[CreatureDefinition, ...],
    armor: ItemDefinition | None,
    rng: RandomSource,
    *,
    encumbrance_penalty: int = 0,
    companion_bonus: int = 0,
    retry_bonus: int = 0,
) -> WithdrawalOutcome:
    """Resolve one original, bounded opposed-d100 withdrawal attempt."""

    profile = calculate_withdrawal_profile(
        character,
        opponents,
        armor,
        encumbrance_penalty=encumbrance_penalty,
        companion_bonus=companion_bonus,
        retry_bonus=retry_bonus,
    )
    roll = open_d100(rng)
    endroll = roll + profile.escape - profile.pressure
    return WithdrawalOutcome(
        roll=roll,
        escape=profile.escape,
        pressure=profile.pressure,
        endroll=endroll,
        success=endroll >= 100,
        normal_roll_needed=profile.normal_roll_needed,
        opponent_count=profile.opponent_count,
        crowd_pressure=profile.crowd_pressure,
        wound_penalty=profile.wound_penalty,
        disabled_leg_penalty=profile.disabled_leg_penalty,
        encumbrance_penalty=profile.encumbrance_penalty,
        armor_penalty=profile.armor_penalty,
        reaction_bonus=profile.reaction_bonus,
        companion_bonus=profile.companion_bonus,
        retry_bonus=profile.retry_bonus,
    )
