"""Shared proxy boundary for extracted application services."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from beta_earth.application.engine import GameEngine


class EngineService:
    """A bounded service that shares the authoritative engine/state context.

    Attribute reads fall through to the orchestrator. Extracted methods can
    therefore retain their established implementation while ownership moves
    out of the monolith one coherent family at a time.
    """

    __slots__ = ("_engine",)

    def __init__(self, engine: "GameEngine") -> None:
        object.__setattr__(self, "_engine", engine)

    @property
    def engine(self) -> "GameEngine":
        return object.__getattribute__(self, "_engine")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.engine, name)
