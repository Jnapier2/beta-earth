from __future__ import annotations

from pathlib import Path
from typing import Any

from beta_earth.domain.models import Interaction, Room, World
from beta_earth.infrastructure.json_document import JsonDocumentError, load_bounded_json
from beta_earth.infrastructure.catalog_validation import (
    normalized_command as _normalized,
    require_string as _require_string,
    require_string_list as _string_list,
    unknown_keys as _unknown_keys,
)

_SUPPORTED_SCHEMA_VERSIONS = {"1.0"}
_ROOT_KEYS = {"schema_version", "title", "start_room", "rooms"}
_ROOM_KEYS = {"name", "zone", "description", "ambient", "danger", "exits", "canon_refs", "interactions"}
_INTERACTION_KEYS = {
    "id",
    "label",
    "command",
    "description",
    "message",
    "priority",
    "requires_flags",
    "grants_flags",
    "once",
}
_DIRECTIONS = {"north", "east", "south", "west", "up", "down"}
_MAX_ROOMS = 256
_MAX_EXITS_PER_ROOM = 6
_MAX_INTERACTIONS_PER_ROOM = 64
_MAX_CANON_REFS = 32
_MAX_FLAGS_PER_INTERACTION = 64

_RESERVED_COMMANDS = {
    "look",
    "help",
    "begin",
    "rollstats",
    "balancedstats",
    "north",
    "east",
    "south",
    "west",
    "up",
    "down",
    "n",
    "e",
    "s",
    "w",
    "u",
    "d",
}


class WorldValidationError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        super().__init__("World validation failed: " + "; ".join(issues))
        self.issues = tuple(issues)


class JsonWorldRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> World:
        try:
            raw = load_bounded_json(self._path, context="World catalog")
        except JsonDocumentError as exc:
            raise WorldValidationError([str(exc)]) from exc
        issues = validate_world_document(raw)
        if issues:
            raise WorldValidationError(issues)
        rooms: dict[str, Room] = {}
        for room_id, value in raw["rooms"].items():
            interactions = tuple(
                Interaction(
                    id=item["id"],
                    label=item["label"],
                    command=item["command"],
                    description=item["description"],
                    message=item["message"],
                    priority=item.get("priority", 50),
                    requires_flags=tuple(item.get("requires_flags", [])),
                    grants_flags=tuple(item.get("grants_flags", [])),
                    once=item.get("once", False),
                )
                for item in value.get("interactions", [])
            )
            rooms[room_id] = Room(
                id=room_id,
                name=value["name"],
                zone=value["zone"],
                description=value["description"],
                ambient=value.get("ambient", ""),
                danger=value.get("danger", "quiet"),
                exits=value.get("exits", {}),
                interactions=interactions,
                canon_refs=tuple(value.get("canon_refs", [])),
            )
        return World(
            schema_version=raw["schema_version"],
            title=raw["title"],
            start_room=raw["start_room"],
            rooms=rooms,
        )


def validate_world_document(raw: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(raw, dict):
        return ["root document must be an object"]
    _unknown_keys(raw, _ROOT_KEYS, "world root", issues)
    for key in _ROOT_KEYS:
        if key not in raw:
            issues.append(f"missing root key: {key}")

    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, str):
        issues.append("world schema_version must be a string")
    elif schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        issues.append(f"unsupported world schema_version: {schema_version!r}")
    _require_string(raw.get("title"), "world title", issues, maximum=200)
    start_room = _require_string(raw.get("start_room"), "start_room", issues, maximum=120)

    rooms = raw.get("rooms")
    if not isinstance(rooms, dict) or not rooms:
        issues.append("rooms must be a non-empty object")
        return issues
    if len(rooms) > _MAX_ROOMS:
        issues.append(f"world room catalog exceeds {_MAX_ROOMS} rooms")
    if start_room is not None and start_room not in rooms:
        issues.append(f"start_room does not exist: {start_room}")

    seen_interaction_ids: set[str] = set()
    for room_id, room in rooms.items():
        if not isinstance(room_id, str) or not room_id.strip():
            issues.append("room id must be a non-empty string")
            continue
        if len(room_id) > 120:
            issues.append(f"room id exceeds 120 characters: {room_id[:40]!r}")
            continue
        if not isinstance(room, dict):
            issues.append(f"room {room_id} must be an object")
            continue
        _unknown_keys(room, _ROOM_KEYS, f"room {room_id}", issues)
        for key, maximum in (("name", 160), ("zone", 160), ("description", 16_384)):
            _require_string(room.get(key), f"room {room_id} {key}", issues, maximum=maximum)
        for key, maximum in (("ambient", 16_384), ("danger", 80)):
            if key in room:
                value = room[key]
                if not isinstance(value, str):
                    issues.append(f"room {room_id} {key} must be a string")
                elif len(value) > maximum:
                    issues.append(f"room {room_id} {key} exceeds {maximum} characters")
        _string_list(
            room.get("canon_refs", []),
            f"room {room_id} canon_refs",
            issues,
            maximum_entries=_MAX_CANON_REFS,
            maximum_length=240,
        )
        if not room.get("canon_refs"):
            issues.append(f"room {room_id} must include at least one canon reference")

        exits = room.get("exits", {})
        if not isinstance(exits, dict):
            issues.append(f"room {room_id} exits must be an object")
        else:
            if len(exits) > _MAX_EXITS_PER_ROOM:
                issues.append(f"room {room_id} exits exceed {_MAX_EXITS_PER_ROOM} entries")
            for direction, target in exits.items():
                if not isinstance(direction, str) or direction not in _DIRECTIONS:
                    issues.append(f"room {room_id} has unsupported exit direction: {direction!r}")
                if not isinstance(target, str) or not target.strip():
                    issues.append(f"room {room_id} exit {direction!r} target must be a non-empty string")
                elif target not in rooms:
                    issues.append(f"room {room_id} exit {direction} targets missing room {target}")

        interactions = room.get("interactions", [])
        if not isinstance(interactions, list):
            issues.append(f"room {room_id} interactions must be an array")
            continue
        if len(interactions) > _MAX_INTERACTIONS_PER_ROOM:
            issues.append(f"room {room_id} interactions exceed {_MAX_INTERACTIONS_PER_ROOM} entries")
        commands: set[str] = set()
        for index, item in enumerate(interactions):
            context = f"room {room_id} interaction {index + 1}"
            if not isinstance(item, dict):
                issues.append(f"{context} must be an object")
                continue
            _unknown_keys(item, _INTERACTION_KEYS, context, issues)
            interaction_id = _require_string(item.get("id"), f"{context} id", issues, maximum=120)
            command_text = _require_string(item.get("command"), f"{context} command", issues, maximum=120)
            command = _normalized(command_text) if command_text is not None else ""
            if interaction_id is not None:
                if interaction_id in seen_interaction_ids:
                    issues.append(f"duplicate interaction id: {interaction_id!r}")
                seen_interaction_ids.add(interaction_id)
            if command:
                if command in commands:
                    issues.append(f"duplicate interaction command in {room_id}: {command!r}")
                commands.add(command)
                if command in _RESERVED_COMMANDS or command.startswith("go "):
                    issues.append(f"interaction {interaction_id or '<invalid>'} uses reserved command: {command}")
                if len(command_text or "") > 120:
                    issues.append(f"interaction {interaction_id or '<invalid>'} command exceeds 120 characters")
            for key, maximum in (("label", 160), ("description", 1_000), ("message", 16_384)):
                _require_string(
                    item.get(key), f"interaction {interaction_id or index + 1} {key}", issues, maximum=maximum
                )
            priority = item.get("priority", 50)
            if not isinstance(priority, int) or isinstance(priority, bool) or not 1 <= priority <= 999:
                issues.append(f"interaction {interaction_id or index + 1} priority must be an integer from 1 to 999")
            for list_key in ("requires_flags", "grants_flags"):
                _string_list(
                    item.get(list_key, []),
                    f"interaction {interaction_id or index + 1} {list_key}",
                    issues,
                    maximum_entries=_MAX_FLAGS_PER_INTERACTION,
                    maximum_length=160,
                )
            once = item.get("once", False)
            if not isinstance(once, bool):
                issues.append(f"interaction {interaction_id or index + 1} once must be a boolean")
            elif once and not item.get("grants_flags"):
                issues.append(f"interaction {interaction_id or index + 1} is once-only but grants no flag")
    return issues
