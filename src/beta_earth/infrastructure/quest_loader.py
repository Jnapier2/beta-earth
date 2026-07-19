from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from beta_earth.domain.economy import EconomyCatalog, normalize_economy_command
from beta_earth.domain.models import ItemStack, World
from beta_earth.infrastructure.json_document import JsonDocumentError, load_bounded_json
from beta_earth.infrastructure.catalog_validation import (
    require_string as _require_string,
    require_string_list as _string_list,
    unknown_keys as _unknown_keys,
)
from beta_earth.domain.quests import (
    ObjectiveKind,
    QuestCatalog,
    QuestDefinition,
    QuestObjective,
    normalize_quest_command,
    shortest_route,
)

_SUPPORTED_SCHEMA_VERSIONS = {"1.0"}
_ROOT_KEYS = {"schema_version", "quests"}
_QUEST_KEYS = {
    "title", "summary", "giver", "tier", "offer_room", "requires_flags",
    "accept_label", "accept_command", "accept_message", "objectives",
    "turn_in_room", "turn_in_label", "turn_in_command", "turn_in_message",
    "reward_summary", "reward_cred", "reward_items", "reward_flags", "canon_refs",
}
_OBJECTIVE_KEYS = {"id", "label", "description", "kind", "target", "room_id"}
_ITEM_STACK_KEYS = {"item_id", "quantity"}
_RESERVED_COMMANDS = {
    "look", "help", "begin", "rollstats", "balancedstats",
    "north", "east", "south", "west", "up", "down", "n", "e", "s", "w", "u", "d",
}
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_]*$")


class QuestValidationError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        super().__init__("Quest validation failed: " + "; ".join(issues))
        self.issues = tuple(issues)


class JsonQuestRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, world: World, economy: EconomyCatalog) -> QuestCatalog:
        try:
            raw = load_bounded_json(self._path, context="Quest catalog")
        except JsonDocumentError as exc:
            raise QuestValidationError([str(exc)]) from exc
        issues = validate_quest_document(raw, world, economy)
        if issues:
            raise QuestValidationError(issues)
        quests: dict[str, QuestDefinition] = {}
        for quest_id, item in raw["quests"].items():
            quests[quest_id] = QuestDefinition(
                id=quest_id,
                title=item["title"],
                summary=item["summary"],
                giver=item["giver"],
                tier=item["tier"],
                offer_room=item["offer_room"],
                requires_flags=tuple(item["requires_flags"]),
                accept_label=item["accept_label"],
                accept_command=item["accept_command"],
                accept_message=item["accept_message"],
                objectives=tuple(
                    QuestObjective(
                        id=objective["id"],
                        label=objective["label"],
                        description=objective["description"],
                        kind=ObjectiveKind(objective["kind"]),
                        target=objective["target"],
                        room_id=objective["room_id"],
                    )
                    for objective in item["objectives"]
                ),
                turn_in_room=item["turn_in_room"],
                turn_in_label=item["turn_in_label"],
                turn_in_command=item["turn_in_command"],
                turn_in_message=item["turn_in_message"],
                reward_summary=item["reward_summary"],
                reward_cred=item["reward_cred"],
                reward_items=tuple(ItemStack(stack["item_id"], stack["quantity"]) for stack in item["reward_items"]),
                reward_flags=tuple(item["reward_flags"]),
                canon_refs=tuple(item["canon_refs"]),
            )
        return QuestCatalog(schema_version=raw["schema_version"], quests=quests)


def validate_quest_document(raw: Any, world: World, economy: EconomyCatalog | None = None) -> list[str]:
    catalog = economy
    issues: list[str] = []
    if not isinstance(raw, dict):
        return ["root document must be an object"]
    _unknown_keys(raw, _ROOT_KEYS, "quest root", issues)
    for key in _ROOT_KEYS:
        if key not in raw:
            issues.append(f"missing root key: {key}")
    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, str):
        issues.append("quest schema_version must be a string")
    elif schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        issues.append(f"unsupported quest schema_version: {schema_version!r}")

    quests = raw.get("quests")
    if not isinstance(quests, dict) or not quests:
        issues.append("quests must be a non-empty object")
        return issues
    if len(quests) > 256:
        issues.append("quest catalog exceeds 256 quests")

    world_commands = {
        normalize_quest_command(interaction.command)
        for room in world.rooms.values()
        for interaction in room.interactions
    }
    economy_commands = {normalize_economy_command(offer.command) for offer in catalog.offers.values()} if catalog else set()
    world_grants = {
        flag for room in world.rooms.values() for interaction in room.interactions for flag in interaction.grants_flags
    }
    world_requirements = [
        (flag, f"interaction {interaction.id}")
        for room in world.rooms.values()
        for interaction in room.interactions
        for flag in interaction.requires_flags
    ]
    all_reward_flags: set[str] = set()
    for value in quests.values():
        if isinstance(value, dict) and isinstance(value.get("reward_flags"), list):
            all_reward_flags.update(flag for flag in value["reward_flags"] if isinstance(flag, str) and flag.strip())
    grantable_flags = world_grants | all_reward_flags
    for required_flag, context in world_requirements:
        if required_flag not in grantable_flags:
            issues.append(f"{context} requires flag that no current content can grant: {required_flag}")
    issues.extend(_quest_dependency_issues(quests, world_grants))
    seen_commands: set[str] = set()

    for quest_id, quest in quests.items():
        context = f"quest {quest_id}"
        if not isinstance(quest_id, str) or not _SAFE_ID.fullmatch(quest_id):
            issues.append(f"quest id must use lowercase letters, numbers, and underscores: {quest_id!r}")
            continue
        if not isinstance(quest, dict):
            issues.append(f"{context} must be an object")
            continue
        _unknown_keys(quest, _QUEST_KEYS, context, issues)
        for key in _QUEST_KEYS:
            if key not in quest:
                issues.append(f"{context} missing key: {key}")

        string_fields = (
            "title", "summary", "giver", "offer_room", "accept_label", "accept_command", "accept_message",
            "turn_in_room", "turn_in_label", "turn_in_command", "turn_in_message", "reward_summary",
        )
        field_limits = {
            "title": 160, "summary": 2_000, "giver": 120, "offer_room": 120,
            "accept_label": 160, "accept_command": 120, "accept_message": 16_384,
            "turn_in_room": 120, "turn_in_label": 160, "turn_in_command": 120,
            "turn_in_message": 16_384, "reward_summary": 2_000,
        }
        values = {
            key: _require_string(quest.get(key), f"{context} {key}", issues, maximum=field_limits[key])
            for key in string_fields
        }
        tier = quest.get("tier")
        if not isinstance(tier, int) or isinstance(tier, bool) or not 1 <= tier <= 100:
            issues.append(f"{context} tier must be an integer from 1 to 100")
        reward_cred = quest.get("reward_cred")
        if not isinstance(reward_cred, int) or isinstance(reward_cred, bool) or not 0 <= reward_cred <= 1_000_000:
            issues.append(f"{context} reward_cred must be an integer from 0 to 1000000")

        for room_key in ("offer_room", "turn_in_room"):
            room_id = values[room_key]
            if room_id is not None and room_id not in world.rooms:
                issues.append(f"{context} {room_key} targets missing room {room_id}")
        for list_key in ("requires_flags", "reward_flags", "canon_refs"):
            _string_list(
                quest.get(list_key),
                f"{context} {list_key}",
                issues,
                maximum_entries=32 if list_key == "canon_refs" else 64,
                maximum_length=240 if list_key == "canon_refs" else 160,
            )
        canon_refs = quest.get("canon_refs")
        if isinstance(canon_refs, list) and not canon_refs:
            issues.append(f"{context} must include at least one canon reference")
        requires_flags = quest.get("requires_flags")
        if isinstance(requires_flags, list):
            for flag in requires_flags:
                if isinstance(flag, str) and flag.strip() and flag not in grantable_flags:
                    issues.append(f"{context} requires flag that no current content can grant: {flag}")

        normalized_commands: dict[str, str] = {}
        for command_key in ("accept_command", "turn_in_command"):
            raw_command = values[command_key]
            if raw_command is None:
                continue
            command = normalize_quest_command(raw_command)
            normalized_commands[command_key] = command
            if len(raw_command) > 120:
                issues.append(f"{context} {command_key} exceeds 120 characters")
            if command in _RESERVED_COMMANDS or command.startswith("go "):
                issues.append(f"{context} {command_key} uses reserved command: {command}")
            if command in world_commands:
                issues.append(f"{context} {command_key} conflicts with a world interaction: {command}")
            if command in economy_commands:
                issues.append(f"{context} {command_key} conflicts with an economy command: {command}")
            if command in seen_commands:
                issues.append(f"duplicate quest command: {command}")
            seen_commands.add(command)
        if normalized_commands.get("accept_command") == normalized_commands.get("turn_in_command"):
            issues.append(f"{context} accept and turn-in commands must differ")

        reward_items = quest.get("reward_items")
        if not isinstance(reward_items, list) or len(reward_items) > 16:
            issues.append(f"{context} reward_items must be an array with at most 16 entries")
        else:
            seen_reward_items: set[str] = set()
            for index, stack in enumerate(reward_items):
                stack_context = f"{context} reward item {index + 1}"
                if not isinstance(stack, dict):
                    issues.append(f"{stack_context} must be an object")
                    continue
                _unknown_keys(stack, _ITEM_STACK_KEYS, stack_context, issues)
                item_id = _require_string(stack.get("item_id"), f"{stack_context} item_id", issues)
                if item_id is not None:
                    if catalog is not None and item_id not in catalog.items:
                        issues.append(f"{stack_context} references missing item: {item_id}")
                    if item_id in seen_reward_items:
                        issues.append(f"{context} has duplicate reward item: {item_id}")
                    seen_reward_items.add(item_id)
                quantity = stack.get("quantity")
                if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 9999:
                    issues.append(f"{stack_context} quantity must be an integer from 1 to 9999")

        objectives = quest.get("objectives")
        if not isinstance(objectives, list) or not objectives:
            issues.append(f"{context} objectives must be a non-empty array")
            continue
        if len(objectives) > 64:
            issues.append(f"{context} objectives exceed 64 entries")
        seen_objective_ids: set[str] = set()
        sequence: list[str] = [values["offer_room"]] if values["offer_room"] else []
        for index, objective in enumerate(objectives):
            objective_context = f"{context} objective {index + 1}"
            if not isinstance(objective, dict):
                issues.append(f"{objective_context} must be an object")
                continue
            _unknown_keys(objective, _OBJECTIVE_KEYS, objective_context, issues)
            for key in _OBJECTIVE_KEYS:
                if key not in objective:
                    issues.append(f"{objective_context} missing key: {key}")
            objective_values = {
                key: _require_string(
                    objective.get(key),
                    f"{objective_context} {key}",
                    issues,
                    maximum=2_000 if key == "description" else 160,
                )
                for key in _OBJECTIVE_KEYS
            }
            objective_id = objective_values["id"]
            if objective_id is not None:
                if not _SAFE_ID.fullmatch(objective_id):
                    issues.append(f"{objective_context} id must use lowercase letters, numbers, and underscores")
                if objective_id in seen_objective_ids:
                    issues.append(f"{context} duplicate objective id: {objective_id}")
                seen_objective_ids.add(objective_id)
            kind_value = objective_values["kind"]
            try:
                kind = ObjectiveKind(kind_value) if kind_value is not None else None
            except ValueError:
                issues.append(f"{objective_context} has unsupported kind: {kind_value}")
                kind = None
            room_id = objective_values["room_id"]
            target = objective_values["target"]
            if room_id is None or room_id not in world.rooms:
                if room_id is not None:
                    issues.append(f"{objective_context} targets missing room: {room_id}")
                continue
            sequence.append(room_id)
            if kind == ObjectiveKind.VISIT_ROOM and target is not None and target != room_id:
                issues.append(f"{objective_context} visit_room target must equal room_id")
            if kind == ObjectiveKind.FLAG and target is not None:
                granting = [interaction for interaction in world.rooms[room_id].interactions if target in interaction.grants_flags]
                if not granting:
                    issues.append(f"{objective_context} flag {target!r} is not granted by an interaction in {room_id}")
                else:
                    reward_flags = set(quest.get("reward_flags", []))
                    if reward_flags and all(
                        reward_flags.intersection(interaction.requires_flags)
                        for interaction in granting
                    ):
                        issues.append(
                            f"{objective_context} can only be granted by an interaction that requires this quest's completion reward"
                        )
        if values["turn_in_room"]:
            sequence.append(values["turn_in_room"])
        for start, target in zip(sequence, sequence[1:]):
            if start in world.rooms and target in world.rooms and shortest_route(world, start, target) is None:
                issues.append(f"{context} has no route from {start} to {target}")
    return issues


def _quest_dependency_issues(quests: dict[object, object], world_grants: set[str]) -> list[str]:
    issues: list[str] = []
    producers: dict[str, set[str]] = {}
    for raw_id, raw_quest in quests.items():
        if not isinstance(raw_id, str) or not isinstance(raw_quest, dict) or not isinstance(raw_quest.get("reward_flags"), list):
            continue
        for flag in raw_quest["reward_flags"]:
            if isinstance(flag, str) and flag.strip():
                producers.setdefault(flag, set()).add(raw_id)
    graph: dict[str, set[str]] = {quest_id: set() for quest_id in quests if isinstance(quest_id, str)}
    for raw_id, raw_quest in quests.items():
        if not isinstance(raw_id, str) or not isinstance(raw_quest, dict) or not isinstance(raw_quest.get("requires_flags"), list):
            continue
        for flag in raw_quest["requires_flags"]:
            if not isinstance(flag, str) or flag in world_grants:
                continue
            candidates = producers.get(flag, set())
            if candidates == {raw_id}:
                issues.append(f"quest {raw_id} requires its own completion reward flag: {flag}")
            elif len(candidates) == 1:
                graph[raw_id].update(candidates)
    for cycle in _dependency_cycles(graph):
        issues.append("quest reward dependency cycle: " + " -> ".join((*cycle, cycle[0])))
    return issues


def _dependency_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    cycles: set[tuple[str, ...]] = set()
    path: list[str] = []
    complete: set[str] = set()

    def visit(node: str) -> None:
        if node in path:
            body = path[path.index(node):]
            rotations = [tuple(body[index:] + body[:index]) for index in range(len(body))]
            cycles.add(min(rotations))
            return
        if node in complete:
            return
        path.append(node)
        for target in sorted(graph.get(node, ())):
            visit(target)
        path.pop()
        complete.add(node)

    for node in sorted(graph):
        visit(node)
    return sorted(cycles)
