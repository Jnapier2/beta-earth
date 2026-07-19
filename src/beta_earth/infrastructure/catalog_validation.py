from __future__ import annotations

from typing import Any


def unknown_keys(value: dict[str, Any], allowed: set[str], context: str, issues: list[str]) -> None:
    """Append an issue for every unsupported key in a static catalog object."""

    for key in sorted(set(value) - allowed, key=str):
        issues.append(f"{context} has unknown key: {key}")


def require_string(
    value: Any,
    context: str,
    issues: list[str],
    *,
    maximum: int = 16_384,
) -> str | None:
    """Require a non-empty string and return the trimmed value when valid."""

    if not isinstance(value, str) or not value.strip():
        issues.append(f"{context} must be a non-empty string")
        return None
    if len(value) > maximum:
        issues.append(f"{context} exceeds {maximum} characters")
    return value.strip()


def require_string_list(
    value: Any,
    context: str,
    issues: list[str],
    *,
    nonempty: bool = False,
    maximum_entries: int = 64,
    maximum_length: int = 160,
) -> None:
    """Validate catalog string arrays without coercing ambiguous values."""

    if not isinstance(value, list):
        issues.append(f"{context} must be an array")
        return
    if nonempty and not value:
        issues.append(f"{context} must not be empty")
        return
    if len(value) > maximum_entries:
        issues.append(f"{context} exceeds {maximum_entries} entries")
    valid_strings = all(
        isinstance(item, str) and item.strip() and len(item) <= maximum_length for item in value
    )
    if not valid_strings:
        issues.append(f"{context} must contain non-empty strings no longer than {maximum_length} characters")
        return
    cleaned = tuple(item.strip() for item in value)
    if len(cleaned) != len(set(cleaned)):
        issues.append(f"{context} contains duplicates after trimming")


def normalized_command(value: str) -> str:
    """Normalize typed MUD commands for conflict checks."""

    return " ".join(value.split()).casefold()
