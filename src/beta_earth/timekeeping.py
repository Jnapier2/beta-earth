from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CHICAGO_ZONE_NAME = "America/Chicago"


def _resolve_chicago_timezone() -> tuple[tzinfo, str, bool]:
    try:
        return ZoneInfo(CHICAGO_ZONE_NAME), CHICAGO_ZONE_NAME, False
    except ZoneInfoNotFoundError:
        # Some clean Windows Python installs do not include an IANA timezone database.
        # Keep the program dependency-free and running, but label the fallback honestly.
        local = datetime.now().astimezone().tzinfo or timezone.utc
        label = getattr(local, "key", None) or str(local) or "system-local"
        return local, f"system-local fallback ({label})", True


USER_TIMEZONE, USER_TIMEZONE_SOURCE, USER_TIMEZONE_FALLBACK = _resolve_chicago_timezone()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def user_now() -> datetime:
    return utc_now().astimezone(USER_TIMEZONE)
