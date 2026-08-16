"""Time ports used to make recovery and progression deterministic in tests."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Return an epoch timestamp in seconds."""


class SystemClock:
    def now(self) -> float:
        return time.time()


@dataclass(slots=True)
class ManualClock:
    current: float = 1_700_000_000.0

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("time cannot move backward")
        self.current += seconds
