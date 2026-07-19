from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .identity import require_canonical_player_id

_MAX_TEXT = 16_384
_MAX_COLLECTION = 512
_MAX_INVENTORY_STACKS = 128
_MAX_ITEM_QUANTITY = 9_999
_MAX_CRED = 1_000_000_000
_ALLOWED_IDENTITIES = frozenset({"female", "male", "nonbinary", "unspecified"})


class SetupStage(StrEnum):
    IDENTITY = "identity"
    ATTRIBUTES = "attributes"
    READY = "ready"
    ACTIVE = "active"


class ActionKind(StrEnum):
    PRIMARY = "primary"
    MISSION = "mission"
    ECONOMY = "economy"
    INTERACTION = "interaction"
    MOVEMENT = "movement"
    UTILITY = "utility"


@dataclass(frozen=True, slots=True)
class Stats:
    strength: int = 10
    agility: int = 10
    intellect: int = 10
    spirit: int = 10
    resilience: int = 10

    def __post_init__(self) -> None:
        for name, value in self.as_dict().items():
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 30:
                raise ValueError(f"{name} must be an integer from 1 to 30")

    def as_dict(self) -> dict[str, int]:
        return {
            "strength": self.strength,
            "agility": self.agility,
            "intellect": self.intellect,
            "spirit": self.spirit,
            "resilience": self.resilience,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object] | None) -> "Stats":
        if value is None:
            data: Mapping[str, object] = {}
        elif isinstance(value, Mapping):
            data = value
        else:
            raise ValueError("stats must be an object")
        known = {"strength", "agility", "intellect", "spirit", "resilience"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"stats contains unknown fields: {', '.join(unknown)}")
        return cls(
            strength=_stat_value(data.get("strength", 10), "strength"),
            agility=_stat_value(data.get("agility", 10), "agility"),
            intellect=_stat_value(data.get("intellect", 10), "intellect"),
            spirit=_stat_value(data.get("spirit", 10), "spirit"),
            resilience=_stat_value(data.get("resilience", 10), "resilience"),
        )


@dataclass(frozen=True, slots=True, order=True)
class ItemStack:
    item_id: str
    quantity: int = 1

    def __post_init__(self) -> None:
        _required_text(self.item_id, "item_id", maximum=80)
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or not 1 <= self.quantity <= _MAX_ITEM_QUANTITY:
            raise ValueError(f"item quantity must be an integer from 1 to {_MAX_ITEM_QUANTITY}")


@dataclass(frozen=True, slots=True)
class Inventory:
    stacks: tuple[ItemStack, ...] = ()

    def __post_init__(self) -> None:
        stacks = tuple(self.stacks)
        if len(stacks) > _MAX_INVENTORY_STACKS:
            raise ValueError(f"inventory exceeds {_MAX_INVENTORY_STACKS} item stacks")
        if any(not isinstance(stack, ItemStack) for stack in stacks):
            raise ValueError("inventory stacks must be ItemStack values")
        ids = [stack.item_id for stack in stacks]
        if len(ids) != len(set(ids)):
            raise ValueError("inventory contains duplicate item ids")
        object.__setattr__(self, "stacks", tuple(sorted(stacks, key=lambda stack: stack.item_id)))

    @classmethod
    def from_mapping(cls, values: Mapping[str, int] | None) -> "Inventory":
        if values is None:
            return cls()
        if not isinstance(values, Mapping):
            raise ValueError("inventory mapping must be an object")
        stacks: list[ItemStack] = []
        for item_id, quantity in values.items():
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError("inventory mapping keys must be non-empty item ids")
            if not isinstance(quantity, int) or isinstance(quantity, bool):
                raise ValueError(f"inventory quantity for {item_id} must be an integer")
            if quantity < 0 or quantity > _MAX_ITEM_QUANTITY:
                raise ValueError(
                    f"inventory quantity for {item_id} must be from 0 to {_MAX_ITEM_QUANTITY}"
                )
            if quantity:
                stacks.append(ItemStack(item_id, quantity))
        return cls(tuple(stacks))

    def as_dict(self) -> dict[str, int]:
        return {stack.item_id: stack.quantity for stack in self.stacks}

    def quantity(self, item_id: str) -> int:
        return next((stack.quantity for stack in self.stacks if stack.item_id == item_id), 0)

    def add(self, grants: tuple[ItemStack, ...]) -> "Inventory":
        values = self.as_dict()
        for grant in grants:
            new_quantity = values.get(grant.item_id, 0) + grant.quantity
            if new_quantity > _MAX_ITEM_QUANTITY:
                raise ValueError(f"inventory quantity exceeds {_MAX_ITEM_QUANTITY} for {grant.item_id}")
            values[grant.item_id] = new_quantity
        return Inventory.from_mapping(values)

    def remove(self, costs: tuple[ItemStack, ...]) -> "Inventory":
        values = self.as_dict()
        for cost in costs:
            current = values.get(cost.item_id, 0)
            if current < cost.quantity:
                raise ValueError(f"not enough {cost.item_id}")
            remaining = current - cost.quantity
            if remaining:
                values[cost.item_id] = remaining
            else:
                values.pop(cost.item_id, None)
        return Inventory.from_mapping(values)


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    label: str
    command: str
    kind: ActionKind
    description: str
    priority: int = 100
    shortcut: int | None = None
    enabled: bool = True
    mission_id: str | None = None

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("id", self.id, 120),
            ("label", self.label, 160),
            ("command", self.command, 120),
            ("description", self.description, 1_000),
        ):
            _required_text(value, name, maximum=maximum)
        if not isinstance(self.kind, ActionKind):
            raise ValueError("Action kind must be an ActionKind")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool) or not 1 <= self.priority <= 999:
            raise ValueError("Action priority must be an integer from 1 to 999")
        if self.shortcut is not None and (
            not isinstance(self.shortcut, int) or isinstance(self.shortcut, bool) or not 1 <= self.shortcut <= 9
        ):
            raise ValueError("Action shortcut must be an integer from 1 through 9")
        if not isinstance(self.enabled, bool):
            raise ValueError("Action enabled must be a boolean")
        if self.mission_id is not None:
            _required_text(self.mission_id, "mission_id", maximum=120)


@dataclass(frozen=True, slots=True)
class Interaction:
    id: str
    label: str
    command: str
    description: str
    message: str
    priority: int = 50
    requires_flags: tuple[str, ...] = ()
    grants_flags: tuple[str, ...] = ()
    once: bool = False

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("id", self.id, 120),
            ("label", self.label, 160),
            ("command", self.command, 120),
            ("description", self.description, 1_000),
            ("message", self.message, _MAX_TEXT),
        ):
            _required_text(value, name, maximum=maximum)
        if not isinstance(self.priority, int) or isinstance(self.priority, bool) or not 1 <= self.priority <= 999:
            raise ValueError("Interaction priority must be an integer from 1 to 999")
        if not isinstance(self.once, bool):
            raise ValueError("Interaction once must be a boolean")
        requires = _normalized_text_tuple(self.requires_flags, "requires_flags")
        grants = _normalized_text_tuple(self.grants_flags, "grants_flags")
        if self.once and not grants:
            raise ValueError("A once-only interaction must grant at least one flag")
        object.__setattr__(self, "requires_flags", requires)
        object.__setattr__(self, "grants_flags", grants)


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    name: str
    zone: str
    description: str
    ambient: str
    danger: str
    exits: Mapping[str, str] = field(default_factory=dict)
    interactions: tuple[Interaction, ...] = ()
    canon_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("id", self.id, 120),
            ("name", self.name, 160),
            ("zone", self.zone, 160),
            ("description", self.description, _MAX_TEXT),
            ("ambient", self.ambient, _MAX_TEXT),
            ("danger", self.danger, 80),
        ):
            _required_text(value, name, maximum=maximum, allow_empty=name == "ambient")
        exits = dict(self.exits)
        if any(not isinstance(key, str) or not isinstance(value, str) or not key.strip() or not value.strip() for key, value in exits.items()):
            raise ValueError("Room exits must map non-empty strings to non-empty strings")
        interactions = tuple(self.interactions)
        if any(not isinstance(item, Interaction) for item in interactions):
            raise ValueError("Room interactions must be Interaction values")
        canon_refs = _normalized_text_tuple(self.canon_refs, "canon_refs")
        object.__setattr__(self, "exits", MappingProxyType(exits))
        object.__setattr__(self, "interactions", interactions)
        object.__setattr__(self, "canon_refs", canon_refs)


@dataclass(frozen=True, slots=True)
class World:
    schema_version: str
    title: str
    start_room: str
    rooms: Mapping[str, Room]

    def __post_init__(self) -> None:
        _required_text(self.schema_version, "schema_version", maximum=32)
        _required_text(self.title, "title", maximum=200)
        _required_text(self.start_room, "start_room", maximum=120)
        rooms = dict(self.rooms)
        if not rooms:
            raise ValueError("World must contain at least one room")
        if self.start_room not in rooms:
            raise ValueError(f"World start room does not exist: {self.start_room}")
        for room_id, room in rooms.items():
            if not isinstance(room, Room) or room.id != room_id:
                raise ValueError(f"World room key/id mismatch: {room_id}")
        object.__setattr__(self, "rooms", MappingProxyType(rooms))


@dataclass(frozen=True, slots=True)
class QuestLog:
    active: tuple[str, ...] = ()
    completed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        active = _normalized_text_tuple(self.active, "quest_log.active")
        completed = _normalized_text_tuple(self.completed, "quest_log.completed")
        overlap = set(active).intersection(completed)
        if overlap:
            raise ValueError(f"Quest ids cannot be active and completed: {sorted(overlap)}")
        object.__setattr__(self, "active", active)
        object.__setattr__(self, "completed", completed)

    def accept(self, quest_id: str) -> "QuestLog":
        if quest_id in self.completed:
            raise ValueError(f"Quest is already completed: {quest_id}")
        if quest_id in self.active:
            return self
        return replace(self, active=(*self.active, quest_id))

    def complete(self, quest_id: str) -> "QuestLog":
        if quest_id not in self.active:
            raise ValueError(f"Quest is not active: {quest_id}")
        return replace(
            self,
            active=tuple(item for item in self.active if item != quest_id),
            completed=(*self.completed, quest_id),
        )


@dataclass(frozen=True, slots=True)
class PlayerState:
    player_id: str
    display_name: str
    stage: SetupStage
    room_id: str
    identity: str | None = None
    stats: Stats = field(default_factory=Stats)
    flags: tuple[str, ...] = ()
    visited_rooms: tuple[str, ...] = ()
    quest_log: QuestLog = field(default_factory=QuestLog)
    cred: int = 0
    inventory: Inventory = field(default_factory=Inventory)
    completed_transactions: tuple[str, ...] = ()
    last_message: str = ""
    revision: int = 0

    def __post_init__(self) -> None:
        require_canonical_player_id(self.player_id)
        _required_text(self.display_name, "display_name", maximum=40)
        _required_text(self.room_id, "room_id", maximum=120)
        if not isinstance(self.stage, SetupStage):
            raise ValueError("stage must be a SetupStage")
        if self.identity is not None:
            identity = _required_text(self.identity, "identity", maximum=40)
            if identity not in _ALLOWED_IDENTITIES:
                raise ValueError(f"identity must be one of: {', '.join(sorted(_ALLOWED_IDENTITIES))}")
            object.__setattr__(self, "identity", identity)
        if not isinstance(self.stats, Stats):
            raise ValueError("stats must be a Stats value")
        if not isinstance(self.quest_log, QuestLog):
            raise ValueError("quest_log must be a QuestLog")
        if not isinstance(self.inventory, Inventory):
            raise ValueError("inventory must be an Inventory")
        if not isinstance(self.cred, int) or isinstance(self.cred, bool) or not 0 <= self.cred <= _MAX_CRED:
            raise ValueError(f"cred must be an integer from 0 to {_MAX_CRED}")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if not isinstance(self.last_message, str) or len(self.last_message) > _MAX_TEXT:
            raise ValueError(f"last_message must be a string no longer than {_MAX_TEXT} characters")
        object.__setattr__(self, "flags", _normalized_text_tuple(self.flags, "flags"))
        object.__setattr__(self, "visited_rooms", _normalized_text_tuple(self.visited_rooms, "visited_rooms"))
        object.__setattr__(
            self,
            "completed_transactions",
            _normalized_text_tuple(self.completed_transactions, "completed_transactions"),
        )


def _stat_value(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _required_text(value: object, field_name: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise ValueError(f"{field_name} is required")
    if len(value) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise ValueError(f"{field_name} contains unsupported control characters")
    return cleaned


def _normalized_text_tuple(values: tuple[str, ...] | list[str], field_name: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError(f"{field_name} must be an array of strings")
    items = tuple(values)
    if len(items) > _MAX_COLLECTION:
        raise ValueError(f"{field_name} exceeds {_MAX_COLLECTION} entries")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 160 for item in items):
        raise ValueError(f"{field_name} must contain non-empty strings no longer than 160 characters")
    return tuple(sorted(set(item.strip() for item in items)))
