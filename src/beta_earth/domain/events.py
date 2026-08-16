"""Serializable domain events and bounded projection streams.

``DomainEvent`` remains the persistence contract used by existing saves and
SQLite rows. ``EventEnvelope`` adds deterministic command/revision context for
presentation, diagnostics, replay fixtures, and future transports without
changing gameplay event payloads.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class EventChannel(str, Enum):
    """Independent player-facing and diagnostic event channels."""

    NARRATIVE = "narrative"
    TACTICAL = "tactical"
    AUDIT = "audit"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Small persistence-safe statement that something happened."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("domain event kind must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise ValueError("domain event payload must be an object")

    def to_dict(self) -> dict[str, Any]:
        # Keep the historical shape stable for existing persistence/tests.
        return {"kind": self.kind, "payload": self.payload}


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """A deterministic stream record around one persisted domain event."""

    sequence: int
    command: str
    revision_before: int
    revision_after: int
    channel: EventChannel
    event: DomainEvent

    @property
    def event_id(self) -> str:
        return (
            f"r{self.revision_after:08d}:"
            f"{self.sequence:04d}:{self.event.kind}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "command": self.command,
            "revision_before": self.revision_before,
            "revision_after": self.revision_after,
            "channel": self.channel.value,
            "event": self.event.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    """Compact deterministic audit receipt for one command resolution."""

    command: str
    revision_before: int
    revision_after: int
    changed: bool
    event_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "revision_before": self.revision_before,
            "revision_after": self.revision_after,
            "changed": self.changed,
            "event_ids": list(self.event_ids),
        }


class EventStream:
    """Bounded in-memory stream for projections and deterministic diagnostics."""

    __slots__ = ("_recent", "_next_sequence", "_last_receipt")

    def __init__(self, capacity: int = 512) -> None:
        if capacity < 32 or capacity > 8192:
            raise ValueError("event stream capacity must be between 32 and 8192")
        self._recent: deque[EventEnvelope] = deque(maxlen=capacity)
        self._next_sequence = 1
        self._last_receipt: CommandReceipt | None = None

    @staticmethod
    def classify(kind: str) -> EventChannel:
        prefix = kind.split(".", 1)[0]
        if prefix in {"combat", "condition", "recovery", "companion", "class"}:
            return EventChannel.TACTICAL
        if prefix in {"story", "world", "room", "social", "journal", "guidance"}:
            return EventChannel.NARRATIVE
        if prefix in {"migration", "state", "system", "character", "playtest"}:
            return EventChannel.SYSTEM
        return EventChannel.AUDIT

    def observe(
        self,
        events: Iterable[DomainEvent],
        *,
        command: str,
        revision_before: int,
        revision_after: int,
        changed: bool,
    ) -> CommandReceipt:
        normalized_command = " ".join(command.strip().split())[:160] or "<empty>"
        envelopes: list[EventEnvelope] = []
        for event in events:
            envelope = EventEnvelope(
                sequence=self._next_sequence,
                command=normalized_command,
                revision_before=revision_before,
                revision_after=revision_after,
                channel=self.classify(event.kind),
                event=event,
            )
            self._next_sequence += 1
            self._recent.append(envelope)
            envelopes.append(envelope)
        receipt = CommandReceipt(
            command=normalized_command,
            revision_before=revision_before,
            revision_after=revision_after,
            changed=bool(changed),
            event_ids=tuple(item.event_id for item in envelopes),
        )
        self._last_receipt = receipt
        return receipt

    @property
    def last_receipt(self) -> CommandReceipt | None:
        return self._last_receipt

    def snapshot(
        self,
        channel: EventChannel | None = None,
    ) -> tuple[EventEnvelope, ...]:
        if channel is None:
            return tuple(self._recent)
        return tuple(item for item in self._recent if item.channel is channel)
