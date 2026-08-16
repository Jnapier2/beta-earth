"""Derived carrying limits and recovery penalties."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from beta_earth.domain.content import ItemDefinition
from beta_earth.domain.model import CharacterState, ItemState


@dataclass(frozen=True, slots=True)
class Encumbrance:
    carried_bulk: int
    comfortable_limit: int
    hard_limit: int
    tier: str
    recovery_penalty: int


def item_bulk(
    items: Sequence[ItemState],
    definitions: Mapping[str, ItemDefinition],
) -> int:
    return sum(definitions[item.definition_id].bulk for item in items)


def encumbrance(
    character: CharacterState,
    definitions: Mapping[str, ItemDefinition],
) -> Encumbrance:
    carried = item_bulk(character.inventory, definitions)
    comfortable = max(6, character.strength)
    hard = comfortable * 2
    if carried <= comfortable:
        tier, penalty = "unburdened", 0
    elif carried <= (comfortable * 3) // 2:
        tier, penalty = "burdened", 1
    else:
        tier, penalty = "strained", 2
    return Encumbrance(carried, comfortable, hard, tier, penalty)
