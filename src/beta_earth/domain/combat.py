"""Original opposed-d100 combat math and body-location consequences."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Mapping
from typing import Protocol, TypeVar

from beta_earth.domain.content import CreatureDefinition, ItemDefinition
from beta_earth.domain.model import CharacterState, DefenseMode
from beta_earth.domain.recovery import disabled_limbs


T = TypeVar("T")


class RandomSource(Protocol):
    def randint(self, start: int, end: int) -> int: ...

    def choice(self, values: tuple[T, ...]) -> T: ...


HIT_LOCATIONS = (
    "chest",
    "chest",
    "abdomen",
    "left arm",
    "right arm",
    "left leg",
    "right leg",
    "head",
)


@dataclass(frozen=True, slots=True)
class CombatOutcome:
    roll: int
    offense: int
    defense: int
    endroll: int
    hit: bool
    damage: int = 0
    location: str | None = None
    severity: int = 0
    critical: str = ""
    reaction: str = DefenseMode.BALANCED.value
    reaction_effect: str = ""
    armor_interaction: str = ""
    absorbed: int = 0
    pressure_penalty: int = 0


def open_d100(rng: RandomSource) -> int:
    """Roll a d100 with bounded high and low surges."""
    first = rng.randint(1, 100)
    total = first
    if first >= 96:
        for _ in range(4):
            extra = rng.randint(1, 100)
            total += extra
            if extra < 96:
                break
    elif first <= 5:
        for _ in range(4):
            extra = rng.randint(1, 100)
            total -= extra
            if extra > 5:
                break
    return total


def effective_item_definition(
    definition: ItemDefinition,
    upgrade_level: int,
) -> ItemDefinition:
    """Return the immutable equipment definition with bounded field modifications."""

    level = max(0, min(3, int(upgrade_level)))
    if level == 0:
        return definition
    return replace(
        definition,
        attack_bonus=definition.attack_bonus + (level if definition.slot == "main_hand" else 0),
        defense_bonus=definition.defense_bonus + (level if definition.slot == "body" else 0),
        damage_max=definition.damage_max + (level if definition.slot == "main_hand" else 0),
        armor=definition.armor + (level // 2 if definition.slot == "body" else 0),
        max_durability=definition.max_durability + (5 * level if definition.max_durability else 0),
    )


def _class_id(character: CharacterState) -> str:
    return character.build.class_id or ""


def _class_offense_bonus(character: CharacterState) -> int:
    return {
        "chosen_one": 4, "boss": 4, "zealot": 5, "protector": 2,
        "guide": 2, "system": 3, "infiltrator": 3, "sniper": 3,
        "born_assassin": 5, "soldier": 2,
    }.get(_class_id(character), 0)


def _class_defense_bonus(character: CharacterState) -> int:
    return {
        "messenger": 6, "fixer": 2, "devout": 2, "protector": 4,
        "guide": 2, "engineer": 3, "infiltrator": 2, "soldier": 4,
        "medic": 2,
    }.get(_class_id(character), 0)


def equipped_item(
    character: CharacterState,
    item_definitions: Mapping[str, ItemDefinition],
    slot: str,
) -> ItemDefinition | None:
    instance_id = character.equipped.get(slot)
    if not instance_id:
        return None
    item = next(
        (candidate for candidate in character.inventory if candidate.instance_id == instance_id),
        None,
    )
    if item is None:
        return None
    if item.durability is not None and item.durability <= 0:
        return None
    definition = item_definitions.get(item.definition_id)
    if definition is None:
        return None
    return effective_item_definition(definition, item.upgrade_level)


def player_offense(character: CharacterState, weapon: ItemDefinition | None) -> int:
    wound_penalty = sum(wound.severity * 3 for wound in character.wounds)
    return (
        48
        + character.level * 4
        + character.strength
        + character.combat_skill * 3
        + (weapon.attack_bonus if weapon else 2)
        + character.stance.offense_modifier
        + _class_offense_bonus(character)
        - wound_penalty
    )


def player_defense(
    character: CharacterState,
    armor: ItemDefinition | None,
) -> int:
    wound_penalty = sum(wound.severity * 2 for wound in character.wounds)
    return (
        38
        + character.level * 3
        + character.agility
        + character.combat_skill * 2
        + (armor.defense_bonus if armor else 0)
        + character.stance.defense_modifier
        + _class_defense_bonus(character)
        - wound_penalty
    )


def defensive_reaction(
    character: CharacterState,
    armor: ItemDefinition | None,
    weapon: ItemDefinition | None,
) -> tuple[int, int, str]:
    """Return defense bonus, on-hit absorption, and an availability note."""

    mode = character.defense_mode
    if mode is DefenseMode.EVADE:
        if any("leg" in location for location in disabled_limbs(character)):
            return 0, 0, "evasion unavailable with a disabled leg"
        armor_penalty = 6 if armor and armor.armor_profile == "heavy" else 0
        return max(2, character.agility // 2 - armor_penalty), 0, "evasion"
    if mode is DefenseMode.BLOCK:
        if armor is None or armor.armor_profile == "none":
            return 0, 0, "block unavailable without armor"
        return 6 + armor.defense_bonus // 2, 1 + armor.armor, "block"
    if mode is DefenseMode.PARRY:
        if any("arm" in location for location in disabled_limbs(character)):
            return 0, 0, "parry unavailable with a disabled arm"
        if weapon is None or weapon.weapon_profile == "unarmed":
            return 0, 0, "parry unavailable without a weapon"
        arm_penalty = sum(
            wound.severity
            for wound in character.wounds
            if "arm" in wound.location
        )
        return max(1, 5 + weapon.attack_bonus // 3 - arm_penalty), 0, "parry"
    return 0, 0, "balanced guard"


def weapon_armor_interaction(
    weapon: ItemDefinition | None,
    target: CreatureDefinition,
) -> tuple[int, str]:
    """Return effective target armor and an original profile interaction note."""

    profile = weapon.weapon_profile if weapon else "unarmed"
    if profile == "heavy" and target.armor_profile == "heavy":
        return max(0, target.armor - 2), "Heavy leverage opens the dense armor."
    if profile in {"light", "unarmed"} and target.armor_profile == "heavy":
        return target.armor + 2, "The light attack loses force against dense armor."
    if profile == "light" and target.armor_profile == "light":
        return max(0, target.armor - 1), "The quick edge finds a seam in the light shell."
    return target.armor, ""


def attack_roundtime(
    character: CharacterState,
    weapon: ItemDefinition | None,
    *,
    encumbrance_penalty: int = 0,
) -> int:
    base = weapon.roundtime if weapon else 3
    agility_reduction = max(0, (character.agility - 10) // 5)
    disabled_arm_penalty = 2 * sum(
        "arm" in location for location in disabled_limbs(character)
    )
    return max(
        2,
        min(
            9,
            base
            - agility_reduction
            + disabled_arm_penalty
            + max(0, encumbrance_penalty),
        ),
    )


def aimed_attack_penalty(character: CharacterState) -> int:
    """Return the visible accuracy cost of naming a hit location.

    The legacy baseline of 10 perception retains the established 16-point
    penalty. Every legal creation point changes the result by exactly one,
    while the floor keeps very high future values from erasing aimed risk.
    """

    class_reduction = 4 if _class_id(character) == "sniper" else 2 if _class_id(character) == "infiltrator" else 0
    return max(2, 26 - character.perception - class_reduction)


def _severity(endroll: int, damage: int) -> int:
    return max(1, min(5, 1 + max(0, endroll - 110) // 25 + damage // 12))


def _critical_text(location: str, severity: int) -> str:
    texts = {
        1: f"The impact glances across the {location}.",
        2: f"The strike bites into the {location} and leaves a painful wound.",
        3: f"A hard impact tears through the {location}; movement falters.",
        4: f"The {location} takes a crushing blow and blood follows.",
        5: f"A devastating strike destroys the target's guard at the {location}.",
    }
    return texts[severity]


def resolve_player_attack(
    character: CharacterState,
    weapon: ItemDefinition | None,
    target: CreatureDefinition,
    rng: RandomSource,
    *,
    aimed_location: str | None = None,
    opponent_count: int = 1,
    offense_bonus: int = 0,
    damage_bonus: int = 0,
    aim_penalty_reduction: int = 0,
) -> CombatOutcome:
    roll = open_d100(rng)
    pressure_penalty = min(12, max(0, opponent_count - 1) * 4)
    offense = player_offense(character, weapon) - pressure_penalty + offense_bonus
    defense = target.defense + (
        max(0, aimed_attack_penalty(character) - aim_penalty_reduction)
        if aimed_location
        else 0
    )
    endroll = roll + offense - defense
    if endroll < 100:
        return CombatOutcome(
            roll,
            offense,
            defense,
            endroll,
            False,
            pressure_penalty=pressure_penalty,
        )
    low = weapon.damage_min if weapon else 2
    high = weapon.damage_max if weapon else 4
    raw_damage = (
        rng.randint(low, high)
        + max(0, endroll - 100) // 18
        + max(0, damage_bonus)
    )
    effective_armor, interaction = weapon_armor_interaction(weapon, target)
    damage = max(1, raw_damage - effective_armor)
    location = aimed_location or rng.choice(HIT_LOCATIONS)
    severity = _severity(endroll, damage)
    return CombatOutcome(
        roll=roll,
        offense=offense,
        defense=defense,
        endroll=endroll,
        hit=True,
        damage=damage,
        location=location,
        severity=severity,
        critical=_critical_text(location, severity),
        armor_interaction=interaction,
        pressure_penalty=pressure_penalty,
    )


def resolve_creature_attack(
    attacker: CreatureDefinition,
    character: CharacterState,
    armor: ItemDefinition | None,
    rng: RandomSource,
    weapon: ItemDefinition | None = None,
    *,
    opponent_count: int = 1,
) -> CombatOutcome:
    roll = open_d100(rng)
    offense = attacker.offense
    reaction_bonus, absorption, reaction_note = defensive_reaction(
        character,
        armor,
        weapon,
    )
    pressure_penalty = min(18, max(0, opponent_count - 1) * 6)
    defense = player_defense(character, armor) + reaction_bonus - pressure_penalty
    endroll = roll + offense - defense
    if endroll < 100:
        return CombatOutcome(
            roll,
            offense,
            defense,
            endroll,
            False,
            reaction=character.defense_mode.value,
            reaction_effect=f"{reaction_note} succeeds",
            pressure_penalty=pressure_penalty,
        )
    armor_reduction = armor.armor if armor else 0
    raw_damage = rng.randint(attacker.damage_min, attacker.damage_max)
    raw_damage += max(0, endroll - 100) // 20
    before_reaction = max(1, raw_damage - armor_reduction)
    damage = max(1, before_reaction - absorption)
    absorbed = before_reaction - damage
    location = rng.choice(HIT_LOCATIONS)
    severity = _severity(endroll, damage)
    return CombatOutcome(
        roll=roll,
        offense=offense,
        defense=defense,
        endroll=endroll,
        hit=True,
        damage=damage,
        location=location,
        severity=severity,
        critical=_critical_text(location, severity),
        reaction=character.defense_mode.value,
        reaction_effect=f"{reaction_note} is pressured",
        absorbed=absorbed,
        pressure_penalty=pressure_penalty,
    )
