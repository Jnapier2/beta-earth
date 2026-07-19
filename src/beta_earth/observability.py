from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import datetime

from .timekeeping import USER_TIMEZONE_FALLBACK, USER_TIMEZONE_SOURCE, user_now, utc_now


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: str
    started_utc: datetime
    started_ct: datetime
    monotonic_started: float
    user_timezone_source: str
    user_timezone_fallback: bool

    @classmethod
    def create(cls) -> "RunContext":
        now_utc = utc_now()
        return cls(
            run_id=secrets.token_hex(6),
            started_utc=now_utc,
            started_ct=user_now(),
            monotonic_started=time.monotonic(),
            user_timezone_source=USER_TIMEZONE_SOURCE,
            user_timezone_fallback=USER_TIMEZONE_FALLBACK,
        )

    def elapsed_ms(self) -> int:
        return max(0, round((time.monotonic() - self.monotonic_started) * 1000))

    def as_public_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "started_utc": self.started_utc.isoformat(timespec="seconds"),
            "started_ct": self.started_ct.isoformat(timespec="seconds"),
            "elapsed_ms": self.elapsed_ms(),
            "user_timezone_source": self.user_timezone_source,
            "user_timezone_fallback": self.user_timezone_fallback,
        }
