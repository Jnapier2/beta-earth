from __future__ import annotations

import hashlib
import re

PLAYER_ID_MAX_LENGTH = 64
PLAYER_INPUT_MAX_LENGTH = 256
DISPLAY_NAME_MAX_LENGTH = 40
_DEFAULT_PLAYER_ID = "Traveler"
_ALLOWED_PLAYER_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
_CANONICAL_PLAYER_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")
_WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def canonical_player_id(value: object, *, fallback: str = _DEFAULT_PLAYER_ID) -> str:
    """Return the one cross-platform profile identifier used by browser, CLI, and storage.

    Already-canonical, non-reserved IDs remain byte-for-byte stable. Inputs that require
    cleanup receive a short deterministic hash suffix, so different raw names cannot silently
    collapse onto the same save filename. Persistence itself accepts only the returned form.
    """

    source = _clean_player_input(value)
    fallback_id = _canonical_fallback(fallback)
    if not source:
        return fallback_id
    if _CANONICAL_PLAYER_ID.fullmatch(source) and not _is_windows_reserved(source):
        return source

    base = "".join(character if character in _ALLOWED_PLAYER_CHARACTERS else "_" for character in source)
    base = re.sub(r"_+", "_", base).lstrip(".-").rstrip(".")
    if not base:
        base = "Player"
    if _is_windows_reserved(base):
        base = f"Player_{base}"

    digest = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()[:10]
    suffix = f"-{digest}"
    base = base[: PLAYER_ID_MAX_LENGTH - len(suffix)].rstrip(".-") or "Player"
    candidate = f"{base}{suffix}"
    if _CANONICAL_PLAYER_ID.fullmatch(candidate) and not _is_windows_reserved(candidate):
        return candidate
    return fallback_id


def require_canonical_player_id(value: object) -> str:
    if not isinstance(value, str) or value != canonical_player_id(value, fallback=""):
        raise ValueError(
            "player_id must be a canonical 1-64 character ASCII profile id using letters, numbers, _, ., or -"
        )
    return value


def display_name_from_input(value: object, *, fallback: str = _DEFAULT_PLAYER_ID) -> str:
    """Normalize presentation-only display text without using it as a storage identity."""

    source = value if isinstance(value, str) else ""
    cleaned = " ".join(
        "".join(
            character
            for character in source
            if character.isprintable() and character not in "\r\n\t"
        ).split()
    )
    if cleaned:
        return cleaned[:DISPLAY_NAME_MAX_LENGTH]
    fallback_cleaned = " ".join(
        "".join(
            character
            for character in str(fallback)
            if character.isprintable() and character not in "\r\n\t"
        ).split()
    )
    return fallback_cleaned[:DISPLAY_NAME_MAX_LENGTH] or _DEFAULT_PLAYER_ID


def _clean_player_input(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(
        character
        for character in value.strip()
        if character.isprintable() and character not in "\r\n\t"
    )
    return cleaned


def _canonical_fallback(value: object) -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        if _CANONICAL_PLAYER_ID.fullmatch(cleaned) and not _is_windows_reserved(cleaned):
            return cleaned
    return _DEFAULT_PLAYER_ID


def _is_windows_reserved(value: str) -> bool:
    stem = value.split(".", 1)[0].upper()
    return stem in _WINDOWS_RESERVED_STEMS
