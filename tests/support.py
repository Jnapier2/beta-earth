from __future__ import annotations

import sys
from pathlib import Path
from typing import TypeVar


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


T = TypeVar("T")


class PredictableRandom:
    """High ordinary rolls without triggering the open-roll surge."""

    def randint(self, start: int, end: int) -> int:
        if start == 1 and end == 100:
            return 90
        return end

    def choice(self, values: tuple[T, ...]) -> T:
        return values[0]


class ScriptedRandom:
    def __init__(self, values: list[int]) -> None:
        self.values = list(values)

    def randint(self, start: int, end: int) -> int:
        if not self.values:
            return min(max(50, start), end)
        value = self.values.pop(0)
        if not start <= value <= end:
            raise AssertionError(f"scripted value {value} outside [{start}, {end}]")
        return value

    def choice(self, values: tuple[T, ...]) -> T:
        return values[0]


def load_test_catalog():
    from beta_earth.infrastructure.content_loader import load_catalog

    return load_catalog(PROJECT_ROOT / "content")


def load_additive_test_catalog(*, declared: bool = True):
    from dataclasses import replace

    from beta_earth.domain.content import (
        CreatureSpawnDefinition,
        ItemSpawnDefinition,
    )

    catalog = load_test_catalog()
    relay = catalog.rooms["relay_overlook"]
    upgraded_relay = replace(
        relay,
        items=relay.items
        + (ItemSpawnDefinition("spawn:item:relay-upgrade-token", "transit_token"),),
        creatures=relay.creatures
        + (
            CreatureSpawnDefinition(
                "spawn:creature:relay-upgrade-mite", "rust_mite"
            ),
        ),
    )
    rooms = dict(catalog.rooms)
    rooms[relay.id] = upgraded_relay
    return replace(
        catalog,
        # Synthetic next-version fixture. Production content remains 0.51.1.
        version="0.51.2",
        additive_from=(catalog.version,) if declared else (),
        rooms=rooms,
    )


def complete_foundation(session) -> None:
    """Finish the production new-session gate for unrelated integration tests."""

    for command in (
        "build class soldier",
        "build auto",
        "build tutorial skip",
        "build confirm",
    ):
        session.execute(command)
    if session.state.character.build.status != "confirmed":
        raise AssertionError("test character foundation did not confirm")
