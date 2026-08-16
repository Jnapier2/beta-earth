"""Stable application result contracts shared by orchestrators and services."""

from __future__ import annotations

from dataclasses import dataclass

from beta_earth.domain.events import DomainEvent


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Transport-neutral result returned by the public game engine."""

    lines: tuple[str, ...]
    events: tuple[DomainEvent, ...] = ()
    changed: bool = False
    quit: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True, slots=True)
class HandlerResult:
    """Internal result returned by one bounded application service."""

    lines: tuple[str, ...]
    events: tuple[DomainEvent, ...] = ()
    changed: bool = False
    quit: bool = False
