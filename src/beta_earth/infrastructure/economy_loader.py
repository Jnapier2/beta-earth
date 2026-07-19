from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from beta_earth.domain.economy import BarterOffer, EconomyCatalog, ItemDefinition, normalize_economy_command
from beta_earth.domain.models import ItemStack, World
from beta_earth.infrastructure.json_document import JsonDocumentError, load_bounded_json
from beta_earth.infrastructure.catalog_validation import (
    require_string as _require_string,
    require_string_list as _string_list,
    unknown_keys as _unknown_keys,
)

_SUPPORTED_SCHEMA_VERSIONS = {"1.0"}
_ROOT_KEYS = {"schema_version", "items", "barter_offers"}
_ITEM_KEYS = {"name", "description", "category", "canon_refs"}
_OFFER_KEYS = {
    "room_id",
    "label",
    "command",
    "description",
    "cost_cred",
    "grants",
    "requires_flags",
    "once",
    "priority",
    "canon_refs",
}
_GRANT_KEYS = {"item_id", "quantity"}
_SAFE_ID = re.compile(r"[a-z0-9_]+")
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


class EconomyValidationError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        super().__init__("Economy validation failed: " + "; ".join(issues))
        self.issues = tuple(issues)


class JsonEconomyRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, world: World) -> EconomyCatalog:
        try:
            raw = load_bounded_json(self._path, context="Economy catalog")
        except JsonDocumentError as exc:
            raise EconomyValidationError([str(exc)]) from exc
        issues = validate_economy_document(raw, world)
        if issues:
            raise EconomyValidationError(issues)
        items = {
            item_id: ItemDefinition(
                id=item_id,
                name=value["name"],
                description=value["description"],
                category=value["category"],
                canon_refs=tuple(value["canon_refs"]),
            )
            for item_id, value in raw["items"].items()
        }
        offers = {
            offer_id: BarterOffer(
                id=offer_id,
                room_id=value["room_id"],
                label=value["label"],
                command=value["command"],
                description=value["description"],
                cost_cred=value["cost_cred"],
                grants=tuple(ItemStack(grant["item_id"], grant["quantity"]) for grant in value["grants"]),
                requires_flags=tuple(value["requires_flags"]),
                once=value["once"],
                priority=value["priority"],
                canon_refs=tuple(value["canon_refs"]),
            )
            for offer_id, value in raw["barter_offers"].items()
        }
        return EconomyCatalog(schema_version=raw["schema_version"], items=items, offers=offers)


def validate_economy_document(raw: Any, world: World) -> list[str]:
    issues: list[str] = []
    if not isinstance(raw, dict):
        return ["economy root document must be an object"]
    _unknown_keys(raw, _ROOT_KEYS, "economy root", issues)
    for key in _ROOT_KEYS:
        if key not in raw:
            issues.append(f"economy missing root key: {key}")
    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, str):
        issues.append("economy schema_version must be a string")
    elif schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        issues.append(f"unsupported economy schema_version: {schema_version!r}")

    items = raw.get("items")
    if not isinstance(items, dict) or not items:
        issues.append("economy items must be a non-empty object")
        return issues
    if len(items) > 256:
        issues.append("economy item catalog exceeds 256 items")
    for item_id, item in items.items():
        context = f"item {item_id}"
        if not isinstance(item_id, str) or not _SAFE_ID.fullmatch(item_id):
            issues.append(f"{context} id must use lowercase letters, numbers, and underscores")
            continue
        if not isinstance(item, dict):
            issues.append(f"{context} must be an object")
            continue
        _unknown_keys(item, _ITEM_KEYS, context, issues)
        for key in _ITEM_KEYS:
            if key not in item:
                issues.append(f"{context} missing key: {key}")
        for key, maximum in (("name", 160), ("description", 2_000), ("category", 80)):
            _require_string(item.get(key), f"{context} {key}", issues, maximum=maximum)
        _string_list(item.get("canon_refs"), f"{context} canon_refs", issues, nonempty=True, maximum_entries=32, maximum_length=240)

    offers = raw.get("barter_offers")
    if not isinstance(offers, dict):
        issues.append("economy barter_offers must be an object")
        return issues
    if len(offers) > 128:
        issues.append("economy barter offer catalog exceeds 128 offers")
    seen_commands: set[str] = set()
    world_commands = {
        " ".join(interaction.command.split()).casefold()
        for room in world.rooms.values()
        for interaction in room.interactions
    }
    for offer_id, offer in offers.items():
        context = f"barter offer {offer_id}"
        if not isinstance(offer_id, str) or not _SAFE_ID.fullmatch(offer_id):
            issues.append(f"{context} id must use lowercase letters, numbers, and underscores")
            continue
        if not isinstance(offer, dict):
            issues.append(f"{context} must be an object")
            continue
        _unknown_keys(offer, _OFFER_KEYS, context, issues)
        for key in _OFFER_KEYS:
            if key not in offer:
                issues.append(f"{context} missing key: {key}")
        room_id = _require_string(offer.get("room_id"), f"{context} room_id", issues)
        if room_id is not None and room_id not in world.rooms:
            issues.append(f"{context} references missing room: {room_id}")
        for key, maximum in (("label", 160), ("command", 120), ("description", 2_000)):
            _require_string(offer.get(key), f"{context} {key}", issues, maximum=maximum)
        command = offer.get("command")
        if isinstance(command, str) and command.strip():
            normalized = normalize_economy_command(command)
            if len(command) > 120:
                issues.append(f"{context} command exceeds 120 characters")
            if normalized in _RESERVED_COMMANDS or normalized.startswith("go "):
                issues.append(f"{context} uses reserved command: {normalized}")
            if normalized in world_commands:
                issues.append(f"{context} command conflicts with a world interaction: {normalized}")
            if normalized in seen_commands:
                issues.append(f"duplicate barter command: {normalized}")
            seen_commands.add(normalized)
        cost = offer.get("cost_cred")
        if not isinstance(cost, int) or isinstance(cost, bool) or not 0 <= cost <= 1_000_000:
            issues.append(f"{context} cost_cred must be an integer from 0 to 1000000")
        priority = offer.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or not 1 <= priority <= 999:
            issues.append(f"{context} priority must be an integer from 1 to 999")
        if not isinstance(offer.get("once"), bool):
            issues.append(f"{context} once must be a boolean")
        _string_list(offer.get("requires_flags"), f"{context} requires_flags", issues, maximum_entries=64, maximum_length=160)
        _string_list(offer.get("canon_refs"), f"{context} canon_refs", issues, nonempty=True, maximum_entries=32, maximum_length=240)
        grants = offer.get("grants")
        if not isinstance(grants, list) or not grants:
            issues.append(f"{context} grants must be a non-empty array")
            continue
        if len(grants) > 16:
            issues.append(f"{context} grants exceed 16 entries")
        seen_grants: set[str] = set()
        for index, grant in enumerate(grants):
            grant_context = f"{context} grant {index + 1}"
            if not isinstance(grant, dict):
                issues.append(f"{grant_context} must be an object")
                continue
            _unknown_keys(grant, _GRANT_KEYS, grant_context, issues)
            item_id = _require_string(grant.get("item_id"), f"{grant_context} item_id", issues)
            if item_id is not None:
                if item_id not in items:
                    issues.append(f"{grant_context} references missing item: {item_id}")
                if item_id in seen_grants:
                    issues.append(f"{context} grants duplicate item: {item_id}")
                seen_grants.add(item_id)
            quantity = grant.get("quantity")
            if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 9999:
                issues.append(f"{grant_context} quantity must be an integer from 1 to 9999")
    return issues
