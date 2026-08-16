"""Local human-playtest profiles and bounded evidence contracts.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


PLAYTEST_FAMILY_CLASSES: Final[dict[str, frozenset[str]]] = {
    "beginner-command": frozenset({"soldier"}),
    "support-sustain": frozenset({"medic", "fixer", "devout", "guide", "engineer"}),
    "control-information": frozenset({"messenger", "infiltrator", "system"}),
    "damage-pressure": frozenset(
        {"chosen_one", "boss", "zealot", "protector", "sniper", "born_assassin"}
    ),
}

PLAYTEST_REPRESENTATIVE_CLASSES: Final[dict[str, str]] = {
    "beginner-command": "soldier",
    "support-sustain": "medic",
    "control-information": "infiltrator",
    "damage-pressure": "protector",
}

PLAYTEST_FAMILY_ALIASES: Final[dict[str, str]] = {
    "command": "beginner-command",
    "beginner": "beginner-command",
    "beginner-command": "beginner-command",
    "support": "support-sustain",
    "sustain": "support-sustain",
    "support-sustain": "support-sustain",
    "control": "control-information",
    "information": "control-information",
    "control-information": "control-information",
    "damage": "damage-pressure",
    "pressure": "damage-pressure",
    "damage-pressure": "damage-pressure",
}

PLAYTEST_MODES: Final[frozenset[str]] = frozenset(
    {"standard", "keyboard_only", "screen_reader", "low_vision"}
)
PLAYTEST_MODE_ALIASES: Final[dict[str, str]] = {
    "standard": "standard",
    "normal": "standard",
    "keyboard": "keyboard_only",
    "keyboard-only": "keyboard_only",
    "keyboard_only": "keyboard_only",
    "screenreader": "screen_reader",
    "screen-reader": "screen_reader",
    "screen_reader": "screen_reader",
    "reader": "screen_reader",
    "lowvision": "low_vision",
    "low-vision": "low_vision",
    "low_vision": "low_vision",
}

PLAYTEST_EXPERIENCE_LEVELS: Final[frozenset[str]] = frozenset(
    {"first_time", "returning", "developer", "unspecified"}
)
PLAYTEST_EXPERIENCE_ALIASES: Final[dict[str, str]] = {
    "first": "first_time",
    "first-time": "first_time",
    "first_time": "first_time",
    "new": "first_time",
    "returning": "returning",
    "experienced": "returning",
    "developer": "developer",
    "dev": "developer",
    "unspecified": "unspecified",
    "unknown": "unspecified",
}

PLAYTEST_ISSUE_SEVERITIES: Final[frozenset[str]] = frozenset(
    {"low", "medium", "high", "blocking"}
)
PLAYTEST_ISSUE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "command_clarity",
        "navigation",
        "combat",
        "sol",
        "pacing",
        "accessibility",
        "lore",
        "save_launch",
        "bug",
        "other",
    }
)
PLAYTEST_ISSUE_CATEGORY_ALIASES: Final[dict[str, str]] = {
    "command": "command_clarity",
    "commands": "command_clarity",
    "command_clarity": "command_clarity",
    "navigation": "navigation",
    "route": "navigation",
    "combat": "combat",
    "sol": "sol",
    "companion": "sol",
    "pacing": "pacing",
    "timing": "pacing",
    "accessibility": "accessibility",
    "a11y": "accessibility",
    "lore": "lore",
    "canon": "lore",
    "save": "save_launch",
    "launch": "save_launch",
    "save_launch": "save_launch",
    "bug": "bug",
    "other": "other",
}

PLAYTEST_CHECKLISTS: Final[dict[str, tuple[str, ...]]] = {
    "standard": (
        "Use a fresh or intentionally reset character and avoid developer-only route knowledge.",
        "Use NEXT and HELP HERE only when you would naturally ask for help.",
        "Record confusing moments with PLAYTEST ISSUE instead of forcing progress silently.",
        "Complete all six survey fields before exporting the receipt.",
    ),
    "keyboard_only": (
        "Keep the pointer unused for the complete session.",
        "Verify command focus, skip links, Tab/Shift+Tab order, Enter activation, and visible focus.",
        "Record any pointer-only control or focus trap as a blocking accessibility issue.",
        "Complete all six survey fields before exporting the receipt.",
    ),
    "screen_reader": (
        "Use the intended screen reader for the complete session and note its name without a version or user path.",
        "Verify command labels, transcript announcements, live-region order, headings, and action-button names.",
        "Record duplicate, missing, or out-of-order announcements as accessibility issues.",
        "Complete all six survey fields before exporting the receipt.",
    ),
    "low_vision": (
        "Exercise text scaling, high contrast, density, browser zoom, and reduced motion as needed.",
        "Verify that focus, warnings, exits, combat state, and NEXT remain readable without clipping.",
        "Record unreadable or overlapping content as accessibility issues.",
        "Complete all six survey fields before exporting the receipt.",
    ),
}


@dataclass(frozen=True, slots=True)
class PlaytestProfile:
    family: str
    class_id: str
    representative_class_id: str
    mode: str
    experience: str
    source: str

    @property
    def class_matches_family(self) -> bool:
        return self.class_id in PLAYTEST_FAMILY_CLASSES[self.family]

    @property
    def representative_match(self) -> bool:
        return self.class_id == self.representative_class_id


def family_for_class(class_id: str | None) -> str | None:
    if not class_id:
        return None
    normalized = class_id.strip().casefold()
    for family, class_ids in PLAYTEST_FAMILY_CLASSES.items():
        if normalized in class_ids:
            return family
    return None


def normalize_family(value: str) -> str | None:
    return PLAYTEST_FAMILY_ALIASES.get(value.strip().casefold().replace(" ", "-"))


def normalize_mode(value: str) -> str | None:
    return PLAYTEST_MODE_ALIASES.get(value.strip().casefold().replace(" ", "_"))


def normalize_experience(value: str) -> str | None:
    return PLAYTEST_EXPERIENCE_ALIASES.get(value.strip().casefold().replace(" ", "_"))


def normalize_issue_category(value: str) -> str | None:
    return PLAYTEST_ISSUE_CATEGORY_ALIASES.get(
        value.strip().casefold().replace("-", "_").replace(" ", "_")
    )
