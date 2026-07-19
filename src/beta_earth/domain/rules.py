from __future__ import annotations

import re
from dataclasses import replace

from .economy import EconomyCatalog, economy_actions
from .models import Action, ActionKind, PlayerState, Room, SetupStage, World
from .quests import QuestCatalog, promote_mission_action, quest_actions

_DIRECTION_ORDER = ("north", "east", "south", "west", "up", "down")
_DIRECTION_ALIASES = {"n": "north", "e": "east", "s": "south", "w": "west", "u": "up", "d": "down"}


def normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip()).casefold()


def command_alias(command: str) -> str:
    value = normalize_command(command)
    if value in _DIRECTION_ALIASES:
        return f"go {_DIRECTION_ALIASES[value]}"
    if value in _DIRECTION_ORDER:
        return f"go {value}"
    return value


def available_actions(
    state: PlayerState,
    world: World,
    quests: QuestCatalog | None = None,
    economy: EconomyCatalog | None = None,
) -> tuple[Action, ...]:
    catalog = quests or QuestCatalog.empty()
    economy_catalog = economy or EconomyCatalog.empty()
    if state.stage == SetupStage.IDENTITY:
        actions = (
            Action("identity_female", "Female", "gender female", ActionKind.PRIMARY, "Set your identity to female.", 10),
            Action("identity_male", "Male", "gender male", ActionKind.PRIMARY, "Set your identity to male.", 20),
            Action("identity_nonbinary", "Nonbinary", "gender nonbinary", ActionKind.PRIMARY, "Set your identity to nonbinary.", 30),
            Action("identity_unspecified", "Unspecified", "gender unspecified", ActionKind.PRIMARY, "Continue without specifying.", 40),
        )
        return _with_shortcuts(actions)

    if state.stage == SetupStage.ATTRIBUTES:
        actions = (
            Action("roll_stats", "Roll attributes", "rollstats", ActionKind.PRIMARY, "Generate a fresh balanced attribute spread.", 10),
            Action("balanced_stats", "Use balanced attributes", "balancedstats", ActionKind.PRIMARY, "Use a dependable 10-point baseline.", 20),
        )
        return _with_shortcuts(actions)

    if state.stage == SetupStage.READY:
        actions = (
            Action("begin", "Awaken in Sprawl 15", "begin", ActionKind.PRIMARY, "Enter Caroline's house and begin the playable route.", 10),
            Action("reroll", "Roll attributes again", "rollstats", ActionKind.UTILITY, "Generate a different attribute spread.", 90),
        )
        return _with_shortcuts(actions)

    room = world.rooms[state.room_id]
    actions: list[Action] = list(quest_actions(state, catalog))
    actions.extend(economy_actions(state, economy_catalog))
    flags = set(state.flags)
    for interaction in room.interactions:
        if not set(interaction.requires_flags).issubset(flags):
            continue
        if interaction.once and interaction.grants_flags and set(interaction.grants_flags).issubset(flags):
            continue
        actions.append(
            Action(
                interaction.id,
                interaction.label,
                interaction.command,
                ActionKind.INTERACTION,
                interaction.description,
                interaction.priority,
            )
        )

    exit_priority = 60
    for direction in _DIRECTION_ORDER:
        if direction in room.exits:
            destination = world.rooms[room.exits[direction]]
            actions.append(
                Action(
                    f"go_{direction}",
                    f"Go {direction}",
                    f"go {direction}",
                    ActionKind.MOVEMENT,
                    f"Travel to {destination.name}.",
                    exit_priority,
                )
            )
            exit_priority += 1

    actions.extend(
        (
            Action("look", "Look around", "look", ActionKind.UTILITY, "Refresh the room description and current situation.", 90),
            Action("help", "Show help", "help", ActionKind.UTILITY, "Explain the current controls and available-action rule.", 100),
        )
    )
    promoted = promote_mission_action(tuple(actions), state, world, catalog)
    ordered = tuple(sorted(promoted, key=lambda action: (action.priority, action.label.casefold())))
    return _with_shortcuts(ordered)


def _with_shortcuts(actions: tuple[Action, ...]) -> tuple[Action, ...]:
    return tuple(replace(action, shortcut=index if index <= 9 else None) for index, action in enumerate(actions, start=1))


def validate_action_surface(actions: tuple[Action, ...]) -> tuple[str, ...]:
    issues: list[str] = []
    seen_ids: set[str] = set()
    seen_commands: set[str] = set()
    seen_shortcuts: set[int] = set()
    for action in actions:
        if action.id in seen_ids:
            issues.append(f"duplicate action id: {action.id}")
        seen_ids.add(action.id)
        command = normalize_command(action.command)
        if command in seen_commands:
            issues.append(f"duplicate visible command: {action.command}")
        seen_commands.add(command)
        if action.shortcut is not None:
            if action.shortcut in seen_shortcuts:
                issues.append(f"duplicate action shortcut: {action.shortcut}")
            seen_shortcuts.add(action.shortcut)
    if not actions:
        issues.append("current action surface is empty")
    return tuple(issues)


def room_summary(room: Room) -> str:
    exits = ", ".join(room.exits) if room.exits else "none"
    return f"{room.description}\n\n{room.ambient}\n\nVisible exits: {exits}."
