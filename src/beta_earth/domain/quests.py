from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .models import Action, ActionKind, ItemStack, PlayerState, World

_DIRECTION_ORDER = ("north", "east", "south", "west", "up", "down")


class ObjectiveKind(StrEnum):
    VISIT_ROOM = "visit_room"
    FLAG = "flag"


class QuestStatus(StrEnum):
    ACTIVE = "active"
    READY_TO_TURN_IN = "ready_to_turn_in"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class QuestObjective:
    id: str
    label: str
    description: str
    kind: ObjectiveKind
    target: str
    room_id: str

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("objective id", self.id, 80),
            ("objective label", self.label, 160),
            ("objective description", self.description, 1_000),
            ("objective target", self.target, 160),
            ("objective room_id", self.room_id, 120),
        ):
            _text(value, name, maximum)
        if not isinstance(self.kind, ObjectiveKind):
            raise ValueError("Quest objective kind must be an ObjectiveKind")


@dataclass(frozen=True, slots=True)
class QuestDefinition:
    id: str
    title: str
    summary: str
    giver: str
    tier: int
    offer_room: str
    requires_flags: tuple[str, ...]
    accept_label: str
    accept_command: str
    accept_message: str
    objectives: tuple[QuestObjective, ...]
    turn_in_room: str
    turn_in_label: str
    turn_in_command: str
    turn_in_message: str
    reward_summary: str
    reward_cred: int
    reward_items: tuple[ItemStack, ...]
    reward_flags: tuple[str, ...]
    canon_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("quest id", self.id, 80),
            ("quest title", self.title, 160),
            ("quest summary", self.summary, 2_000),
            ("quest giver", self.giver, 120),
            ("quest offer_room", self.offer_room, 120),
            ("quest accept_label", self.accept_label, 160),
            ("quest accept_command", self.accept_command, 120),
            ("quest accept_message", self.accept_message, 16_384),
            ("quest turn_in_room", self.turn_in_room, 120),
            ("quest turn_in_label", self.turn_in_label, 160),
            ("quest turn_in_command", self.turn_in_command, 120),
            ("quest turn_in_message", self.turn_in_message, 16_384),
            ("quest reward_summary", self.reward_summary, 2_000),
        ):
            _text(value, name, maximum)
        if not isinstance(self.tier, int) or isinstance(self.tier, bool) or not 1 <= self.tier <= 100:
            raise ValueError("Quest tier must be an integer from 1 to 100")
        if not isinstance(self.reward_cred, int) or isinstance(self.reward_cred, bool) or not 0 <= self.reward_cred <= 1_000_000:
            raise ValueError("Quest reward_cred must be an integer from 0 to 1000000")
        objectives = tuple(self.objectives)
        if not objectives or len(objectives) > 64 or any(not isinstance(item, QuestObjective) for item in objectives):
            raise ValueError("A quest must contain 1-64 QuestObjective values")
        objective_ids = [item.id for item in objectives]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("Quest objectives contain duplicate ids")
        reward_items = tuple(self.reward_items)
        if len(reward_items) > 16 or any(not isinstance(item, ItemStack) for item in reward_items):
            raise ValueError("Quest reward_items must contain at most 16 ItemStack values")
        reward_ids = [item.item_id for item in reward_items]
        if len(reward_ids) != len(set(reward_ids)):
            raise ValueError("Quest reward_items contain duplicate item ids")
        object.__setattr__(self, "objectives", objectives)
        object.__setattr__(self, "reward_items", reward_items)
        object.__setattr__(self, "requires_flags", _strings(self.requires_flags, "quest requires_flags", 64, 160))
        object.__setattr__(self, "reward_flags", _strings(self.reward_flags, "quest reward_flags", 64, 160))
        object.__setattr__(self, "canon_refs", _strings(self.canon_refs, "quest canon_refs", 32, 300, require=True))


@dataclass(frozen=True, slots=True)
class QuestCatalog:
    schema_version: str
    quests: Mapping[str, QuestDefinition]

    def __post_init__(self) -> None:
        _text(self.schema_version, "quest schema_version", 32)
        quests = dict(self.quests)
        if len(quests) > 256:
            raise ValueError("Quest catalog exceeds 256 quests")
        for quest_id, quest in quests.items():
            if not isinstance(quest, QuestDefinition) or quest.id != quest_id:
                raise ValueError(f"Quest key/id mismatch: {quest_id}")
        object.__setattr__(self, "quests", MappingProxyType(quests))

    @classmethod
    def empty(cls) -> "QuestCatalog":
        return cls(schema_version="1.0", quests={})


@dataclass(frozen=True, slots=True)
class ObjectiveProgress:
    id: str
    label: str
    description: str
    complete: bool


@dataclass(frozen=True, slots=True)
class RouteStep:
    direction: str
    room_id: str
    room_name: str


@dataclass(frozen=True, slots=True)
class MissionTracer:
    instruction: str
    target_room_id: str | None
    target_room_name: str | None
    recommended_command: str | None
    route: tuple[RouteStep, ...] = ()


@dataclass(frozen=True, slots=True)
class QuestJournalEntry:
    id: str
    title: str
    summary: str
    giver: str
    tier: int
    status: QuestStatus
    reward_summary: str
    objectives: tuple[ObjectiveProgress, ...]
    tracer: MissionTracer | None
    canon_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuestJournal:
    active: tuple[QuestJournalEntry, ...]
    completed: tuple[QuestJournalEntry, ...]


def normalize_quest_command(command: str) -> str:
    return " ".join(command.split()).casefold()


def objective_is_complete(state: PlayerState, objective: QuestObjective) -> bool:
    if objective.kind == ObjectiveKind.VISIT_ROOM:
        return objective.target in state.visited_rooms
    if objective.kind == ObjectiveKind.FLAG:
        return objective.target in state.flags
    raise ValueError(f"Unsupported objective kind: {objective.kind}")


def quest_is_ready(state: PlayerState, quest: QuestDefinition) -> bool:
    return all(objective_is_complete(state, objective) for objective in quest.objectives)


def quest_is_available(state: PlayerState, quest: QuestDefinition) -> bool:
    return (
        state.room_id == quest.offer_room
        and quest.id not in state.quest_log.active
        and quest.id not in state.quest_log.completed
        and set(quest.requires_flags).issubset(state.flags)
    )


def quest_actions(state: PlayerState, catalog: QuestCatalog) -> tuple[Action, ...]:
    actions: list[Action] = []
    for quest in sorted(catalog.quests.values(), key=lambda item: (item.tier, item.title.casefold())):
        if quest_is_available(state, quest):
            actions.append(
                Action(
                    id=f"quest_accept:{quest.id}",
                    label=quest.accept_label,
                    command=quest.accept_command,
                    kind=ActionKind.MISSION,
                    description=quest.summary,
                    priority=15,
                    mission_id=quest.id,
                )
            )
        elif quest.id in state.quest_log.active and quest_is_ready(state, quest) and state.room_id == quest.turn_in_room:
            actions.append(
                Action(
                    id=f"quest_turn_in:{quest.id}",
                    label=quest.turn_in_label,
                    command=quest.turn_in_command,
                    kind=ActionKind.MISSION,
                    description=f"Complete {quest.title} with {quest.giver}. {quest.reward_summary}",
                    priority=5,
                    mission_id=quest.id,
                )
            )
    return tuple(actions)


def apply_quest_command(state: PlayerState, command: str, catalog: QuestCatalog) -> PlayerState | None:
    normalized = normalize_quest_command(command)
    for quest in catalog.quests.values():
        if normalized == normalize_quest_command(quest.accept_command) and quest_is_available(state, quest):
            return replace(state, quest_log=state.quest_log.accept(quest.id), last_message=quest.accept_message)
        if (
            normalized == normalize_quest_command(quest.turn_in_command)
            and quest.id in state.quest_log.active
            and quest_is_ready(state, quest)
            and state.room_id == quest.turn_in_room
        ):
            flags = tuple(sorted(set(state.flags).union(quest.reward_flags)))
            inventory = state.inventory.add(quest.reward_items)
            return replace(
                state,
                quest_log=state.quest_log.complete(quest.id),
                flags=flags,
                cred=state.cred + quest.reward_cred,
                inventory=inventory,
                last_message=quest.turn_in_message,
            )
    return None


def validate_quest_log_references(state: PlayerState, catalog: QuestCatalog) -> tuple[str, ...]:
    known = set(catalog.quests)
    referenced = set(state.quest_log.active).union(state.quest_log.completed)
    return tuple(sorted(referenced - known))


def build_quest_journal(state: PlayerState, world: World, catalog: QuestCatalog) -> QuestJournal:
    active_entries = [
        _journal_entry(state, catalog.quests[quest_id], world, completed=False)
        for quest_id in state.quest_log.active
        if quest_id in catalog.quests
    ]
    completed_entries = [
        _journal_entry(state, catalog.quests[quest_id], world, completed=True)
        for quest_id in state.quest_log.completed
        if quest_id in catalog.quests
    ]
    return QuestJournal(
        active=tuple(sorted(active_entries, key=lambda item: (item.tier, item.title.casefold()))),
        completed=tuple(sorted(completed_entries, key=lambda item: (item.tier, item.title.casefold()))),
    )


def promote_mission_action(
    actions: tuple[Action, ...], state: PlayerState, world: World, catalog: QuestCatalog
) -> tuple[Action, ...]:
    recommendation = recommended_quest_command(state, world, catalog)
    if recommendation is None:
        return actions
    quest_id, command = recommendation
    normalized = normalize_quest_command(command)
    return tuple(
        replace(action, priority=min(action.priority, 3), mission_id=quest_id)
        if normalize_quest_command(action.command) == normalized
        else action
        for action in actions
    )


def recommended_quest_command(state: PlayerState, world: World, catalog: QuestCatalog) -> tuple[str, str] | None:
    journal = build_quest_journal(state, world, catalog)
    for entry in journal.active:
        if entry.tracer and entry.tracer.recommended_command:
            return entry.id, entry.tracer.recommended_command
    return None


def shortest_route(world: World, start_room: str, target_room: str) -> tuple[RouteStep, ...] | None:
    if start_room == target_room:
        return ()
    if start_room not in world.rooms or target_room not in world.rooms:
        return None
    queue: deque[tuple[str, tuple[RouteStep, ...]]] = deque([(start_room, ())])
    visited = {start_room}
    while queue:
        room_id, route = queue.popleft()
        room = world.rooms[room_id]
        ordered_directions = [direction for direction in _DIRECTION_ORDER if direction in room.exits]
        ordered_directions.extend(sorted(set(room.exits) - set(ordered_directions)))
        for direction in ordered_directions:
            destination_id = room.exits[direction]
            if destination_id in visited:
                continue
            destination = world.rooms[destination_id]
            next_route = (*route, RouteStep(direction, destination.id, destination.name))
            if destination_id == target_room:
                return next_route
            visited.add(destination_id)
            queue.append((destination_id, next_route))
    return None


def _journal_entry(state: PlayerState, quest: QuestDefinition, world: World, *, completed: bool) -> QuestJournalEntry:
    status = QuestStatus.COMPLETED if completed else (
        QuestStatus.READY_TO_TURN_IN if quest_is_ready(state, quest) else QuestStatus.ACTIVE
    )
    return QuestJournalEntry(
        id=quest.id,
        title=quest.title,
        summary=quest.summary,
        giver=quest.giver,
        tier=quest.tier,
        status=status,
        reward_summary=quest.reward_summary,
        objectives=_objective_progress(state, quest),
        tracer=None if completed else _build_tracer(state, quest, world),
        canon_refs=quest.canon_refs,
    )


def _objective_progress(state: PlayerState, quest: QuestDefinition) -> tuple[ObjectiveProgress, ...]:
    return tuple(
        ObjectiveProgress(
            id=objective.id,
            label=objective.label,
            description=objective.description,
            complete=objective_is_complete(state, objective),
        )
        for objective in quest.objectives
    )


def _build_tracer(state: PlayerState, quest: QuestDefinition, world: World) -> MissionTracer:
    for objective in quest.objectives:
        if objective_is_complete(state, objective):
            continue
        if state.room_id != objective.room_id:
            route = shortest_route(world, state.room_id, objective.room_id)
            room = world.rooms[objective.room_id]
            if route is None:
                return MissionTracer(
                    instruction=f"No validated route currently reaches {room.name}.",
                    target_room_id=room.id,
                    target_room_name=room.name,
                    recommended_command=None,
                    route=(),
                )
            return MissionTracer(
                instruction=f"Travel to {room.name}: {objective.description}",
                target_room_id=room.id,
                target_room_name=room.name,
                recommended_command=f"go {route[0].direction}" if route else None,
                route=route,
            )
        return MissionTracer(
            instruction=objective.description,
            target_room_id=objective.room_id,
            target_room_name=world.rooms[objective.room_id].name,
            recommended_command=_objective_command(world, objective),
            route=(),
        )

    if state.room_id != quest.turn_in_room:
        route = shortest_route(world, state.room_id, quest.turn_in_room)
        room = world.rooms[quest.turn_in_room]
        if route is None:
            return MissionTracer(
                instruction=f"No validated route currently reaches {quest.giver} in {room.name}.",
                target_room_id=room.id,
                target_room_name=room.name,
                recommended_command=None,
                route=(),
            )
        return MissionTracer(
            instruction=f"Return to {quest.giver} in {room.name}.",
            target_room_id=room.id,
            target_room_name=room.name,
            recommended_command=f"go {route[0].direction}" if route else None,
            route=route,
        )
    return MissionTracer(
        instruction=f"Report back to {quest.giver}.",
        target_room_id=quest.turn_in_room,
        target_room_name=world.rooms[quest.turn_in_room].name,
        recommended_command=quest.turn_in_command,
        route=(),
    )


def _objective_command(world: World, objective: QuestObjective) -> str | None:
    if objective.kind != ObjectiveKind.FLAG:
        return None
    room = world.rooms[objective.room_id]
    for interaction in room.interactions:
        if objective.target in interaction.grants_flags:
            return interaction.command
    return None


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise ValueError(f"{name} contains unsupported control characters")
    return value.strip()


def _strings(
    values: tuple[str, ...] | list[str],
    name: str,
    maximum: int,
    item_maximum: int,
    *,
    require: bool = False,
) -> tuple[str, ...]:
    items = tuple(values)
    if require and not items:
        raise ValueError(f"{name} must not be empty")
    if len(items) > maximum:
        raise ValueError(f"{name} exceeds {maximum} entries")
    cleaned = tuple(_text(item, name, item_maximum) for item in items)
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{name} contains duplicates")
    return tuple(sorted(cleaned))
