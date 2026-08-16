"""Character-foundation rules and transparent derived-effect projections.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from collections.abc import Mapping

from beta_earth.domain.combat import aimed_attack_penalty
from beta_earth.domain.content import (
    CharacterCreationDefinition,
    TrainingOptionDefinition,
)
from beta_earth.domain.model import CharacterState


ATTRIBUTE_IDS = (
    "strength",
    "agility",
    "perception",
    "combat_skill",
)


def allocation_cost(
    creation: CharacterCreationDefinition,
    attributes: Mapping[str, int],
) -> int:
    """Return weighted spend above the authored minimums."""

    if set(attributes) != set(ATTRIBUTE_IDS):
        raise ValueError(
            "allocation must define every creation attribute exactly once"
        )
    spent = 0
    for attribute_id in ATTRIBUTE_IDS:
        definition = creation.attributes[attribute_id]
        value = attributes[attribute_id]
        if type(value) is not int:
            raise ValueError("allocation values must be integers")
        if not definition.minimum <= value <= definition.maximum:
            raise ValueError(
                f"{definition.name} must be between "
                f"{definition.minimum} and {definition.maximum}"
            )
        spent += (value - definition.minimum) * definition.weight
    return spent


def validate_allocation(
    creation: CharacterCreationDefinition,
    attributes: Mapping[str, int],
    *,
    require_full_budget: bool,
) -> int:
    spent = allocation_cost(creation, attributes)
    if spent > creation.budget:
        raise ValueError(
            f"allocation spends {spent} of {creation.budget} points"
        )
    if require_full_budget and spent != creation.budget:
        raise ValueError(
            f"allocation must spend exactly {creation.budget} points; "
            f"{creation.budget - spent} remain"
        )
    return spent


def minimum_allocation(
    creation: CharacterCreationDefinition,
) -> dict[str, int]:
    return {
        attribute_id: creation.attributes[attribute_id].minimum
        for attribute_id in ATTRIBUTE_IDS
    }


def trained_attributes(
    base_attributes: Mapping[str, int],
    ranks: Mapping[str, int],
    options: Mapping[str, TrainingOptionDefinition],
) -> dict[str, int]:
    """Combine persisted creation values with learned rank gains."""

    result = {attribute_id: int(base_attributes[attribute_id]) for attribute_id in ATTRIBUTE_IDS}
    for option_id, rank in ranks.items():
        option = options.get(option_id)
        if option is not None:
            result[option.attribute] += rank * option.gain_per_rank
    return result


def legacy_base_attributes(
    character: CharacterState,
    options: Mapping[str, TrainingOptionDefinition],
) -> dict[str, int]:
    """Mechanically remove learned gains from a pre-schema-8 snapshot."""

    result = {
        attribute_id: int(getattr(character, attribute_id))
        for attribute_id in ATTRIBUTE_IDS
    }
    for option_id, rank in character.training.ranks.items():
        option = options.get(option_id)
        if option is None:
            continue
        result[option.attribute] -= rank * option.gain_per_rank
    if any(value < 0 for value in result.values()):
        raise ValueError("legacy trained attributes cannot yield a negative base")
    return result


def apply_base_attributes(
    character: CharacterState,
    base_attributes: Mapping[str, int],
    options: Mapping[str, TrainingOptionDefinition],
) -> None:
    effective = trained_attributes(
        base_attributes,
        character.training.ranks,
        options,
    )
    for attribute_id, value in effective.items():
        setattr(character, attribute_id, value)


def stat_effect_projection(
    character: CharacterState,
    attribute_id: str,
) -> dict[str, object]:
    """Project current and next-point consequences without mutating state."""

    if attribute_id not in ATTRIBUTE_IDS:
        raise ValueError(f"unknown creation attribute {attribute_id!r}")
    value = int(getattr(character, attribute_id))
    if attribute_id == "strength":
        return {
            "current": [
                f"+{value} to attack offense",
                f"{max(6, value)} comfortable / {max(6, value) * 2} hard bulk",
            ],
            "next": [
                "+1 attack offense",
                "+1 comfortable and +2 hard bulk",
            ],
        }
    if attribute_id == "agility":
        return {
            "current": [
                f"+{value} to defense",
                f"+{value} to withdrawal contests",
                f"+{max(2, value // 2)} evade reaction before armor penalties",
                f"{max(0, (value - 10) // 5)} sec. attack-recovery reduction",
            ],
            "next": [
                "+1 defense and +1 withdrawal",
                (
                    "+1 evade reaction"
                    if (value + 1) // 2 > value // 2
                    else "evade reaction improves on the following point"
                ),
                (
                    "-1 sec. attack recovery"
                    if max(0, (value + 1 - 10) // 5)
                    > max(0, (value - 10) // 5)
                    else "attack recovery improves at the next five-point threshold"
                ),
            ],
        }
    if attribute_id == "perception":
        penalty = aimed_attack_penalty(character)
        preview = CharacterState.from_dict(character.to_dict())
        preview.perception += 1
        return {
            "current": [
                f"{penalty}-point defense penalty when aiming at a body location",
            ],
            "next": [
                (
                    "-1 aimed-attack penalty"
                    if aimed_attack_penalty(preview) < penalty
                    else "aimed-attack penalty is already at its floor"
                ),
            ],
        }
    return {
        "current": [
            f"+{value * 3} to offense",
            f"+{value * 2} to defense",
            f"+{value * 2} to withdrawal contests",
        ],
        "next": [
            "+3 offense, +2 defense, and +2 withdrawal",
        ],
    }
