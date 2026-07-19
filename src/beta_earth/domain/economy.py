from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from .models import Action, ActionKind, ItemStack, PlayerState

_MAX_TEXT = 1_000
_MAX_CRED_COST = 1_000_000


@dataclass(frozen=True, slots=True)
class ItemDefinition:
    id: str
    name: str
    description: str
    category: str
    canon_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.id, "item id", 80)
        _text(self.name, "item name", 160)
        _text(self.description, "item description", _MAX_TEXT)
        _text(self.category, "item category", 80)
        refs = _strings(self.canon_refs, "item canon_refs", maximum=32, item_maximum=300, require=True)
        object.__setattr__(self, "canon_refs", refs)


@dataclass(frozen=True, slots=True)
class BarterOffer:
    id: str
    room_id: str
    label: str
    command: str
    description: str
    cost_cred: int
    grants: tuple[ItemStack, ...]
    requires_flags: tuple[str, ...] = ()
    once: bool = True
    priority: int = 18
    canon_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.id, "barter offer id", 80)
        _text(self.room_id, "barter room_id", 120)
        _text(self.label, "barter label", 160)
        _text(self.command, "barter command", 120)
        _text(self.description, "barter description", _MAX_TEXT)
        if not isinstance(self.cost_cred, int) or isinstance(self.cost_cred, bool) or not 0 <= self.cost_cred <= _MAX_CRED_COST:
            raise ValueError(f"Barter cost_cred must be an integer from 0 to {_MAX_CRED_COST}")
        grants = tuple(self.grants)
        if not grants or len(grants) > 16 or any(not isinstance(grant, ItemStack) for grant in grants):
            raise ValueError("Barter grants must contain 1-16 ItemStack values")
        grant_ids = [grant.item_id for grant in grants]
        if len(grant_ids) != len(set(grant_ids)):
            raise ValueError("Barter grants cannot contain duplicate item ids")
        if not isinstance(self.once, bool):
            raise ValueError("Barter once must be a boolean")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool) or not 1 <= self.priority <= 999:
            raise ValueError("Barter priority must be an integer from 1 to 999")
        object.__setattr__(self, "grants", grants)
        object.__setattr__(
            self,
            "requires_flags",
            _strings(self.requires_flags, "barter requires_flags", maximum=64, item_maximum=160),
        )
        object.__setattr__(
            self,
            "canon_refs",
            _strings(self.canon_refs, "barter canon_refs", maximum=32, item_maximum=300, require=True),
        )


@dataclass(frozen=True, slots=True)
class EconomyCatalog:
    schema_version: str
    items: Mapping[str, ItemDefinition]
    offers: Mapping[str, BarterOffer]

    def __post_init__(self) -> None:
        _text(self.schema_version, "economy schema_version", 32)
        items = dict(self.items)
        offers = dict(self.offers)
        if len(items) > 256 or len(offers) > 128:
            raise ValueError("Economy catalog exceeds configured bounds")
        for item_id, item in items.items():
            if not isinstance(item, ItemDefinition) or item.id != item_id:
                raise ValueError(f"Economy item key/id mismatch: {item_id}")
        for offer_id, offer in offers.items():
            if not isinstance(offer, BarterOffer) or offer.id != offer_id:
                raise ValueError(f"Economy offer key/id mismatch: {offer_id}")
            missing = sorted({grant.item_id for grant in offer.grants} - set(items))
            if missing:
                raise ValueError(f"Economy offer {offer_id} grants missing items: {', '.join(missing)}")
        object.__setattr__(self, "items", MappingProxyType(items))
        object.__setattr__(self, "offers", MappingProxyType(offers))

    @classmethod
    def empty(cls) -> "EconomyCatalog":
        return cls(schema_version="1.0", items={}, offers={})


@dataclass(frozen=True, slots=True)
class InventoryItemView:
    item_id: str
    name: str
    description: str
    category: str
    quantity: int


@dataclass(frozen=True, slots=True)
class BarterOfferView:
    id: str
    label: str
    command: str
    description: str
    cost_cred: int
    affordable: bool
    completed: bool
    grant_summary: str


@dataclass(frozen=True, slots=True)
class EconomyView:
    cred: int
    inventory: tuple[InventoryItemView, ...]
    room_offers: tuple[BarterOfferView, ...]


def economy_actions(state: PlayerState, catalog: EconomyCatalog) -> tuple[Action, ...]:
    actions: list[Action] = []
    flags = set(state.flags)
    completed = set(state.completed_transactions)
    for offer in sorted(catalog.offers.values(), key=lambda item: (item.priority, item.label.casefold())):
        if offer.room_id != state.room_id:
            continue
        if not set(offer.requires_flags).issubset(flags):
            continue
        if offer.once and offer.id in completed:
            continue
        if state.cred < offer.cost_cred:
            continue
        actions.append(
            Action(
                id=f"barter:{offer.id}",
                label=offer.label,
                command=offer.command,
                kind=ActionKind.ECONOMY,
                description=offer.description,
                priority=offer.priority,
            )
        )
    return tuple(actions)


def apply_economy_command(state: PlayerState, command: str, catalog: EconomyCatalog) -> PlayerState | None:
    normalized = normalize_economy_command(command)
    flags = set(state.flags)
    completed = set(state.completed_transactions)
    for offer in catalog.offers.values():
        if normalize_economy_command(offer.command) != normalized:
            continue
        if offer.room_id != state.room_id or not set(offer.requires_flags).issubset(flags):
            return None
        if offer.once and offer.id in completed:
            return None
        if state.cred < offer.cost_cred:
            return None
        inventory = state.inventory.add(offer.grants)
        transactions = tuple(sorted(completed | {offer.id})) if offer.once else state.completed_transactions
        granted = _grant_summary(offer.grants, catalog)
        return replace(
            state,
            cred=state.cred - offer.cost_cred,
            inventory=inventory,
            completed_transactions=transactions,
            last_message=(
                f"Barter completed: {offer.cost_cred} cred exchanged for {granted}. "
                "The keeper records the trade without pretending the lane is stable."
            ),
        )
    return None


def build_economy_view(state: PlayerState, catalog: EconomyCatalog) -> EconomyView:
    inventory = tuple(
        InventoryItemView(
            item_id=stack.item_id,
            name=catalog.items[stack.item_id].name,
            description=catalog.items[stack.item_id].description,
            category=catalog.items[stack.item_id].category,
            quantity=stack.quantity,
        )
        for stack in state.inventory.stacks
        if stack.item_id in catalog.items
    )
    flags = set(state.flags)
    completed = set(state.completed_transactions)
    room_offers = tuple(
        BarterOfferView(
            id=offer.id,
            label=offer.label,
            command=offer.command,
            description=offer.description,
            cost_cred=offer.cost_cred,
            affordable=state.cred >= offer.cost_cred,
            completed=offer.once and offer.id in completed,
            grant_summary=_grant_summary(offer.grants, catalog),
        )
        for offer in sorted(catalog.offers.values(), key=lambda item: (item.priority, item.label.casefold()))
        if offer.room_id == state.room_id and set(offer.requires_flags).issubset(flags)
    )
    return EconomyView(cred=state.cred, inventory=inventory, room_offers=room_offers)


def validate_inventory_references(state: PlayerState, catalog: EconomyCatalog) -> tuple[str, ...]:
    issues = [f"missing item: {stack.item_id}" for stack in state.inventory.stacks if stack.item_id not in catalog.items]
    issues.extend(
        f"missing barter transaction: {transaction_id}"
        for transaction_id in sorted(set(state.completed_transactions) - set(catalog.offers))
    )
    return tuple(issues)


def normalize_economy_command(command: str) -> str:
    return " ".join(command.split()).casefold()


def _grant_summary(grants: tuple[ItemStack, ...], catalog: EconomyCatalog) -> str:
    values: list[str] = []
    for grant in grants:
        item = catalog.items[grant.item_id]
        values.append(f"{grant.quantity} × {item.name}" if grant.quantity != 1 else item.name)
    return ", ".join(values)


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
    *,
    maximum: int,
    item_maximum: int,
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
