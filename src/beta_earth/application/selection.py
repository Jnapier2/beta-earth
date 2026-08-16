"""Deterministic parsing of object and creature selectors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


_NUMERIC_ORDINAL = re.compile(r"#?([1-9][0-9]*)(?:st|nd|rd|th)?\Z")
_WORD_ORDINALS = {
    "first": 0,
    "second": 1,
    "third": 2,
    "fourth": 3,
    "fifth": 4,
    "sixth": 5,
    "seventh": 6,
    "eighth": 7,
    "ninth": 8,
    "tenth": 9,
}


class Scope(str, Enum):
    DEFAULT = "default"
    ROOM = "room"
    INVENTORY = "inventory"
    EQUIPPED = "equipped"


class RelativeSelector(str, Enum):
    OTHER = "other"
    NEXT = "next"
    RANDOM = "random"


@dataclass(frozen=True, slots=True)
class SelectionQuery:
    terms: str
    scope: Scope = Scope.DEFAULT
    ordinal: int | None = None
    relative: RelativeSelector | None = None
    all_matches: bool = False
    exclusion: str | None = None
    pronoun: bool = False


def parse_selection(value: str) -> SelectionQuery:
    """Parse bounded selection grammar without resolving world state."""

    words = value.casefold().split()
    while words and words[0] in {"at", "the", "a", "an"}:
        words.pop(0)

    scope = Scope.DEFAULT
    if words and words[0] in {"my", "carried", "inventory"}:
        scope = Scope.INVENTORY
        words.pop(0)
    elif words and words[0] in {"room", "nearby"}:
        scope = Scope.ROOM
        words.pop(0)
    elif words and words[0] in {"held", "equipped", "worn"}:
        scope = Scope.EQUIPPED
        words.pop(0)

    if len(words) >= 2 and words[-2:] in (["in", "inventory"], ["on", "me"]):
        scope = Scope.INVENTORY
        del words[-2:]
    elif words and words[-1] in {"here", "nearby"}:
        scope = Scope.ROOM
        words.pop()

    all_matches = bool(words and words[0] == "all")
    if all_matches:
        words.pop(0)
    exclusion: str | None = None
    if "except" in words:
        index = words.index("except")
        exclusion = " ".join(words[index + 1 :]).strip()
        words = words[:index]

    relative: RelativeSelector | None = None
    if words and words[0] in {selector.value for selector in RelativeSelector}:
        relative = RelativeSelector(words.pop(0))

    ordinal: int | None = None
    for index in (0, len(words) - 1):
        if not words or index < 0 or index >= len(words):
            continue
        token = words[index]
        parsed = _WORD_ORDINALS.get(token)
        if parsed is None:
            match = _NUMERIC_ORDINAL.fullmatch(token)
            parsed = int(match.group(1)) - 1 if match else None
        if parsed is not None:
            ordinal = parsed
            words.pop(index)
            break

    terms = " ".join(words).strip()
    return SelectionQuery(
        terms=terms,
        scope=scope,
        ordinal=ordinal,
        relative=relative,
        all_matches=all_matches,
        exclusion=exclusion,
        pronoun=terms in {"it", "them", "that", "this"},
    )
