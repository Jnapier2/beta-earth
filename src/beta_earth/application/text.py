"""Application-level text normalization with one canonical implementation."""

from __future__ import annotations

import re


NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '\\-]{1,31}$")


def normalize_player_name(value: str) -> tuple[str, str]:
    """Return stable key/display forms for a validated local character name."""

    display = " ".join(value.strip().split())
    if not NAME_PATTERN.fullmatch(display):
        raise ValueError(
            "Names must be 2-32 characters and use letters, numbers, "
            "spaces, apostrophes, or hyphens."
        )
    return display.casefold(), display


def natural_list(values: list[str]) -> str:
    """Render a compact English list without presentation-layer duplication."""

    if not values:
        return "nothing"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"
