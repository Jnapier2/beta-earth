from __future__ import annotations

from dataclasses import dataclass

from .models import PlayerState


@dataclass(frozen=True, slots=True)
class EquipmentSlotPreview:
    """Read-only equipment slot projection for the DID readiness layer.

    v0.4.8 intentionally does not add equip/unequip commands, combat modifiers, durability,
    or save-schema changes. This preview derives from inventory and flags so the HUD can show
    what the limiter is already using without introducing a second state source.
    """

    slot_id: str
    label: str
    equipped_item_id: str | None
    equipped_item_name: str | None
    status: str
    description: str


@dataclass(frozen=True, slots=True)
class DIDReadinessPreview:
    tier: str
    label: str
    summary: str
    combat_modifiers_enabled: bool
    reasons: tuple[str, ...]
    next_hint: str
    slots: tuple[EquipmentSlotPreview, ...]


ROUTE_TRACER_ITEM_ID = "route_tracer_calibration"
LANE_SAFE_FILTER_ITEM_ID = "lane_safe_filter"


def build_did_readiness_preview(state: PlayerState) -> DIDReadinessPreview:
    flags = set(state.flags)
    has_route_tracer = state.inventory.quantity(ROUTE_TRACER_ITEM_ID) > 0
    has_filter = state.inventory.quantity(LANE_SAFE_FILTER_ITEM_ID) > 0
    inspected_did = "inspected_did" in flags
    trusted = "trusted_by_caroline" in flags

    reasons: list[str] = []
    if inspected_did:
        reasons.append("DID limiter inspected")
    if trusted:
        reasons.append("Caroline's trust recorded")
    if has_route_tracer:
        reasons.append("Route-Tracer Calibration carried")
    if has_filter:
        reasons.append("Lane-Safe Filter carried")

    if has_route_tracer and has_filter and trusted:
        tier = "field_prepared"
        label = "Field-prepared"
        summary = "Your limiter has a trusted route calibration and one lane-safe field filter. Combat effects remain disabled."
        next_hint = "A future equipment tier can expose true slots before any combat math is enabled."
    elif has_route_tracer and trusted:
        tier = "calibrated"
        label = "Calibrated"
        summary = "Caroline's route calibration is active as a guidance preview only. Combat effects remain disabled."
        next_hint = "Barter for a Lane-Safe Filter to preview field-prepared readiness."
    elif inspected_did:
        tier = "baseline"
        label = "Baseline"
        summary = "The limiter is awake, but its SE/DID functions are still locked to observation."
        next_hint = "Complete Caroline's route mission to calibrate the limiter."
    else:
        tier = "locked"
        label = "Locked"
        summary = "The DID readiness layer is visible but not calibrated yet."
        next_hint = "Inspect your limiter in Caroline's house."

    slot = EquipmentSlotPreview(
        slot_id="did_core",
        label="DID core preview slot",
        equipped_item_id=ROUTE_TRACER_ITEM_ID if has_route_tracer else None,
        equipped_item_name="Route-Tracer Calibration" if has_route_tracer else None,
        status="linked" if has_route_tracer else "empty",
        description=(
            "Read-only limiter slot. It shows what the DID can reference, but does not grant combat modifiers."
        ),
    )
    filter_slot = EquipmentSlotPreview(
        slot_id="field_filter",
        label="Field safety preview slot",
        equipped_item_id=LANE_SAFE_FILTER_ITEM_ID if has_filter else None,
        equipped_item_name="Lane-Safe Filter" if has_filter else None,
        status="carried" if has_filter else "empty",
        description=(
            "Read-only safety slot. It confirms carried protection without creating equipment stats."
        ),
    )
    return DIDReadinessPreview(
        tier=tier,
        label=label,
        summary=summary,
        combat_modifiers_enabled=False,
        reasons=tuple(reasons),
        next_hint=next_hint,
        slots=(slot, filter_slot),
    )
