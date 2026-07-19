from __future__ import annotations

from typing import Protocol

from beta_earth.domain.economy import EconomyCatalog
from beta_earth.domain.models import PlayerState, Stats, World
from beta_earth.domain.quests import QuestCatalog


class RevisionConflict(RuntimeError):
    """Raised when a stale client tries to save over newer player state."""


class PlayerDataError(RuntimeError):
    """Raised when a local player save cannot be decoded safely."""


class PlayerRepository(Protocol):
    def load(self, player_id: str) -> PlayerState | None: ...

    def save(self, state: PlayerState, expected_revision: int) -> PlayerState: ...


class WorldRepository(Protocol):
    def load(self) -> World: ...


class EconomyRepository(Protocol):
    def load(self, world: World) -> EconomyCatalog: ...


class QuestRepository(Protocol):
    def load(self, world: World, economy: EconomyCatalog) -> QuestCatalog: ...


class RandomSource(Protocol):
    def roll_stats(self) -> Stats: ...
