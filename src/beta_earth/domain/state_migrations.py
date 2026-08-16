"""Explicit deterministic save-payload migration registry."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable


CURRENT_STATE_SCHEMA = 20
READABLE_STATE_SCHEMAS = frozenset(range(2, CURRENT_STATE_SCHEMA + 1))


@dataclass(frozen=True, slots=True)
class StateMigrationStep:
    from_schema: int
    to_schema: int
    name: str
    transform: Callable[[dict[str, Any]], dict[str, Any]]

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        if int(payload.get("schema_version", 0)) != self.from_schema:
            raise ValueError(
                f"migration {self.name} expected schema {self.from_schema}"
            )
        result = self.transform(deepcopy(payload))
        if int(result.get("schema_version", 0)) != self.to_schema:
            raise ValueError(
                f"migration {self.name} did not produce schema {self.to_schema}"
            )
        return result


def _legacy_compatibility_step(to_schema: int) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def transform(payload: dict[str, Any]) -> dict[str, Any]:
        # Historical field defaults remain interpreted by the current model.
        # This explicit step records the transition instead of hiding it in one
        # broad deserializer, without inventing transformations not preserved by
        # the original releases.
        payload["schema_version"] = to_schema
        return payload
    return transform


def _v17_to_v18(payload: dict[str, Any]) -> dict[str, Any]:
    payload.setdefault(
        "foundation",
        {
            "schema_version": 1,
            "sovereignty": {
                "allegiance_id": None,
                "previous_affiliations": [],
                "factions": {},
                "local_trust": {},
                "known_crimes": [],
                "favors": [],
                "debts": [],
                "pardons": [],
            },
            "party": {
                "formation": "unformed",
                "member_ids": [],
                "mercenary_ids": [],
                "commander_id": None,
                "shared_target_id": None,
                "protection_assignments": {},
                "intelligence_reports": [],
            },
            "territories": {},
            "quests": {},
        },
    )
    payload["schema_version"] = 18
    return payload


def _v18_to_v19(payload: dict[str, Any]) -> dict[str, Any]:
    foundation = payload.setdefault("foundation", {})
    if not isinstance(foundation, dict):
        raise ValueError("schema 18 foundation must be an object")
    foundation["schema_version"] = 2
    foundation.setdefault("applied_story_record_ids", [])
    sovereignty = foundation.setdefault("sovereignty", {})
    if not isinstance(sovereignty, dict):
        raise ValueError("schema 18 sovereignty must be an object")
    if sovereignty.get("allegiance_id") == "security":
        sovereignty["allegiance_id"] = "security_uf"
    previous = sovereignty.setdefault("previous_affiliations", [])
    if isinstance(previous, list):
        sovereignty["previous_affiliations"] = sorted(
            {"security_uf" if item == "security" else item for item in previous}
        )
    factions = sovereignty.setdefault("factions", {})
    if not isinstance(factions, dict):
        raise ValueError("schema 18 factions must be an object")
    legacy_security = factions.pop("security", None)
    if legacy_security is not None:
        if "security_uf" not in factions:
            factions["security_uf"] = legacy_security
        elif isinstance(legacy_security, dict) and isinstance(factions["security_uf"], dict):
            target = factions["security_uf"]
            for key in ("public_standing", "covert_standing", "rank"):
                target[key] = max(int(target.get(key, 0)), int(legacy_security.get(key, 0)))
            for key in ("completed_quest_ids", "access_flags"):
                target[key] = sorted(set(target.get(key, [])) | set(legacy_security.get(key, [])))
    territories = foundation.setdefault("territories", {})
    if not isinstance(territories, dict):
        raise ValueError("schema 18 territories must be an object")
    for territory in territories.values():
        if isinstance(territory, dict):
            territory.setdefault("maintenance_ready_turns", {})
    payload["schema_version"] = 19
    return payload


def _v19_to_v20(payload: dict[str, Any]) -> dict[str, Any]:
    foundation = payload.setdefault("foundation", {})
    if not isinstance(foundation, dict):
        raise ValueError("schema 19 foundation must be an object")
    foundation["schema_version"] = 3
    sovereignty = foundation.setdefault("sovereignty", {})
    if not isinstance(sovereignty, dict):
        raise ValueError("schema 19 sovereignty must be an object")
    sovereignty.setdefault("pending_allegiance_id", None)
    sovereignty.setdefault("allegiance_confirmed_turn", None)
    sovereignty.setdefault("pledge_receipt_ids", [])
    factions = sovereignty.setdefault("factions", {})
    if not isinstance(factions, dict):
        raise ValueError("schema 19 factions must be an object")
    for standing in factions.values():
        if isinstance(standing, dict):
            rank = int(standing.get("rank", 0))
            standing.setdefault(
                "rank_title", "Unranked" if rank <= 0 else f"Legacy Rank {rank}"
            )
    payload["schema_version"] = 20
    return payload


_STEPS = tuple(
    StateMigrationStep(
        source,
        source + 1,
        (
            "v17_to_v18_foundation_state"
            if source == 17
            else "v18_to_v19_activate_foundations"
            if source == 18
            else "v19_to_v20_activate_pledge_and_civic_duty"
            if source == 19
            else f"v{source}_to_v{source + 1}_legacy_compatibility"
        ),
        (
            _v17_to_v18
            if source == 17
            else _v18_to_v19
            if source == 18
            else _v19_to_v20
            if source == 19
            else _legacy_compatibility_step(source + 1)
        ),
    )
    for source in range(2, CURRENT_STATE_SCHEMA)
)
MIGRATION_STEPS = {step.from_schema: step for step in _STEPS}


def migration_path(source_schema: int) -> tuple[StateMigrationStep, ...]:
    if source_schema not in READABLE_STATE_SCHEMAS:
        raise ValueError(
            f"unsupported state schema {source_schema}; expected one of "
            f"{sorted(READABLE_STATE_SCHEMAS)}"
        )
    path: list[StateMigrationStep] = []
    current = source_schema
    while current < CURRENT_STATE_SCHEMA:
        step = MIGRATION_STEPS.get(current)
        if step is None:
            raise ValueError(f"missing migration step from schema {current}")
        path.append(step)
        current = step.to_schema
    return tuple(path)


def migrate_state_payload(value: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ValueError("saved state must be an object")
    source = int(value.get("schema_version", 0))
    payload = deepcopy(value)
    names: list[str] = []
    for step in migration_path(source):
        payload = step.apply(payload)
        names.append(step.name)
    return payload, tuple(names)
