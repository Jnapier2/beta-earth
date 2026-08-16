"""Command-boundary constants with one authoritative home."""

from __future__ import annotations

import re


CONTENT_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)
MAX_BULK_SELECTION = 20
TUTORIAL_EVIDENCE_PREFIX = "tutorial:evidence:"

HISTORY_EXCLUDED = frozenset({
    "again", "briefing", "build", "cancel", "course", "emote", "effects",
    "examine", "help", "guide", "journal", "quest", "look", "next", "plan",
    "playtest", "queue", "quit", "recover", "route", "roundtime", "save",
    "signal", "say", "state", "wait",
})

INCAPACITATED_COMMANDS = frozenset({
    "briefing", "build", "emote", "effects", "glance", "health", "help",
    "guide", "info", "journal", "quest", "look", "next", "plan", "playtest",
    "quit", "recover", "route", "roundtime", "save", "say", "signal", "state",
    "wait",
})

PENDING_BUILD_COMMANDS = frozenset({
    "build", "guide", "help", "info", "look", "next", "playtest", "quit",
    "save", "state",
})
