"""Save-safe sovereignty, pledge, civic, party, and territory contracts.

These contracts activate bounded gameplay interpretations without inventing
unresolved lore. Authored story and the established combat resolver remain
authoritative, while explicit pledges and civic receipts remain reversible only
through future content that deliberately defines such a path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CANONICAL_FACTION_IDS = (
    "armageddon",
    "syndicate",
    "final_bloodline",
    "guardian_angel",
    "redemption",
    "bounty_hunters",
    "security_uf",
)
PARTY_FORMATIONS = frozenset({"unformed", "balanced", "offensive", "defensive", "custom"})
QUEST_STATUSES = frozenset({"inactive", "active", "completed", "failed", "abandoned"})
TERRITORY_LEVELS = frozenset({0, 1, 2, 3, 4})
MAX_LEDGER_ENTRIES = 512
MAX_PARTY_MEMBERS = 8
MAX_TERRITORIES = 128
MAX_QUEST_MACHINES = 512


def _text(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text" + (" or null" if optional else ""))
    return value.strip()


def _bounded_text_set(value: Any, label: str, *, limit: int = MAX_LEDGER_ENTRIES) -> set[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{label} must be a bounded list")
    result = {_text(item, label) for item in value}
    if len(result) != len(value):
        raise ValueError(f"{label} must not contain duplicates")
    return {str(item) for item in result}


@dataclass(slots=True)
class FactionStandingState:
    """Public/covert standing and progression for one faction."""

    public_standing: int = 0
    covert_standing: int = 0
    rank: int = 0
    rank_title: str = "Unranked"
    completed_quest_ids: set[str] = field(default_factory=set)
    access_flags: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "public_standing": self.public_standing,
            "covert_standing": self.covert_standing,
            "rank": self.rank,
            "rank_title": self.rank_title,
            "completed_quest_ids": sorted(self.completed_quest_ids),
            "access_flags": sorted(self.access_flags),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FactionStandingState":
        if not isinstance(value, dict):
            raise ValueError("faction standing must be an object")
        public = int(value.get("public_standing", 0))
        covert = int(value.get("covert_standing", 0))
        rank = int(value.get("rank", 0))
        rank_title = str(_text(value.get("rank_title", "Unranked"), "faction rank title"))
        if not -1000 <= public <= 1000 or not -1000 <= covert <= 1000:
            raise ValueError("faction standing must be between -1000 and 1000")
        if not 0 <= rank <= 100:
            raise ValueError("faction rank must be between 0 and 100")
        return cls(
            public_standing=public,
            covert_standing=covert,
            rank=rank,
            rank_title=rank_title,
            completed_quest_ids=_bounded_text_set(
                value.get("completed_quest_ids", []), "faction completed quests"
            ),
            access_flags=_bounded_text_set(value.get("access_flags", []), "faction access flags"),
        )


@dataclass(slots=True)
class SovereigntyState:
    """Competing relationships rather than a single morality score."""

    allegiance_id: str | None = None
    pending_allegiance_id: str | None = None
    allegiance_confirmed_turn: int | None = None
    pledge_receipt_ids: set[str] = field(default_factory=set)
    previous_affiliations: set[str] = field(default_factory=set)
    factions: dict[str, FactionStandingState] = field(default_factory=dict)
    local_trust: dict[str, int] = field(default_factory=dict)
    known_crimes: set[str] = field(default_factory=set)
    favors: set[str] = field(default_factory=set)
    debts: set[str] = field(default_factory=set)
    pardons: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allegiance_id": self.allegiance_id,
            "pending_allegiance_id": self.pending_allegiance_id,
            "allegiance_confirmed_turn": self.allegiance_confirmed_turn,
            "pledge_receipt_ids": sorted(self.pledge_receipt_ids),
            "previous_affiliations": sorted(self.previous_affiliations),
            "factions": {key: value.to_dict() for key, value in sorted(self.factions.items())},
            "local_trust": dict(sorted(self.local_trust.items())),
            "known_crimes": sorted(self.known_crimes),
            "favors": sorted(self.favors),
            "debts": sorted(self.debts),
            "pardons": sorted(self.pardons),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SovereigntyState":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("sovereignty state must be an object")
        allegiance = _text(value.get("allegiance_id"), "allegiance ID", optional=True)
        pending_allegiance = _text(
            value.get("pending_allegiance_id"), "pending allegiance ID", optional=True
        )
        confirmed_turn_raw = value.get("allegiance_confirmed_turn")
        confirmed_turn = None if confirmed_turn_raw is None else int(confirmed_turn_raw)
        if confirmed_turn is not None and not 0 <= confirmed_turn <= 1_000_000_000:
            raise ValueError("allegiance confirmation turn must be bounded")
        raw_factions = value.get("factions", {})
        if not isinstance(raw_factions, dict) or len(raw_factions) > 64:
            raise ValueError("sovereignty factions must be a bounded object")
        raw_trust = value.get("local_trust", {})
        if not isinstance(raw_trust, dict) or len(raw_trust) > MAX_LEDGER_ENTRIES:
            raise ValueError("local trust must be a bounded object")
        trust: dict[str, int] = {}
        for key, score in raw_trust.items():
            normalized = _text(key, "local trust ID")
            numeric = int(score)
            if not -1000 <= numeric <= 1000:
                raise ValueError("local trust must be between -1000 and 1000")
            trust[str(normalized)] = numeric
        return cls(
            allegiance_id=str(allegiance) if allegiance else None,
            pending_allegiance_id=(
                str(pending_allegiance) if pending_allegiance else None
            ),
            allegiance_confirmed_turn=confirmed_turn,
            pledge_receipt_ids=_bounded_text_set(
                value.get("pledge_receipt_ids", []), "pledge receipts"
            ),
            previous_affiliations=_bounded_text_set(
                value.get("previous_affiliations", []), "previous affiliations"
            ),
            factions={
                str(_text(key, "faction ID")): FactionStandingState.from_dict(item)
                for key, item in raw_factions.items()
            },
            local_trust=trust,
            known_crimes=_bounded_text_set(value.get("known_crimes", []), "known crimes"),
            favors=_bounded_text_set(value.get("favors", []), "favors"),
            debts=_bounded_text_set(value.get("debts", []), "debts"),
            pardons=_bounded_text_set(value.get("pardons", []), "pardons"),
        )


@dataclass(slots=True)
class PartyState:
    """Generic party/mercenary/command contract; maximum group size is eight."""

    formation: str = "unformed"
    member_ids: list[str] = field(default_factory=list)
    mercenary_ids: list[str] = field(default_factory=list)
    commander_id: str | None = None
    shared_target_id: str | None = None
    protection_assignments: dict[str, str] = field(default_factory=dict)
    intelligence_reports: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.formation not in PARTY_FORMATIONS:
            raise ValueError("party formation is invalid")
        members = [str(_text(item, "party member ID")) for item in self.member_ids]
        mercenaries = [str(_text(item, "party mercenary ID")) for item in self.mercenary_ids]
        if len(members) != len(set(members)):
            raise ValueError("party members must not contain duplicates")
        if len(mercenaries) != len(set(mercenaries)):
            raise ValueError("party mercenaries must not contain duplicates")
        if len(mercenaries) > 2:
            raise ValueError("party cannot contain more than two mercenaries")
        if set(members) & set(mercenaries):
            raise ValueError("party members and mercenaries must be separate")
        if len(members) + len(mercenaries) > MAX_PARTY_MEMBERS:
            raise ValueError("party cannot exceed eight total members")
        if len(self.protection_assignments) > MAX_PARTY_MEMBERS:
            raise ValueError("party protection assignments must be bounded")
        self.member_ids = sorted(members)
        self.mercenary_ids = sorted(mercenaries)
        self.commander_id = _text(self.commander_id, "commander ID", optional=True)
        self.shared_target_id = _text(self.shared_target_id, "shared target ID", optional=True)
        self.protection_assignments = {
            str(_text(key, "protector ID")): str(_text(target, "protected ID"))
            for key, target in self.protection_assignments.items()
        }
        self.intelligence_reports = {
            str(_text(item, "party intelligence report"))
            for item in self.intelligence_reports
        }
        if len(self.intelligence_reports) > MAX_LEDGER_ENTRIES:
            raise ValueError("party intelligence reports must be bounded")

    def to_dict(self) -> dict[str, Any]:
        return {
            "formation": self.formation,
            "member_ids": list(self.member_ids),
            "mercenary_ids": list(self.mercenary_ids),
            "commander_id": self.commander_id,
            "shared_target_id": self.shared_target_id,
            "protection_assignments": dict(sorted(self.protection_assignments.items())),
            "intelligence_reports": sorted(self.intelligence_reports),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "PartyState":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("party state must be an object")
        formation = str(value.get("formation", "unformed"))
        if formation not in PARTY_FORMATIONS:
            raise ValueError("party formation is invalid")
        members = list(_bounded_text_set(value.get("member_ids", []), "party members", limit=MAX_PARTY_MEMBERS))
        mercenaries = list(_bounded_text_set(value.get("mercenary_ids", []), "party mercenaries", limit=2))
        if len(set(members) | set(mercenaries)) > MAX_PARTY_MEMBERS:
            raise ValueError("party cannot exceed eight total members")
        assignments = value.get("protection_assignments", {})
        if not isinstance(assignments, dict) or len(assignments) > MAX_PARTY_MEMBERS:
            raise ValueError("party protection assignments must be bounded")
        return cls(
            formation=formation,
            member_ids=sorted(members),
            mercenary_ids=sorted(mercenaries),
            commander_id=_text(value.get("commander_id"), "commander ID", optional=True),
            shared_target_id=_text(value.get("shared_target_id"), "shared target ID", optional=True),
            protection_assignments={
                str(_text(key, "protector ID")): str(_text(target, "protected ID"))
                for key, target in assignments.items()
            },
            intelligence_reports=_bounded_text_set(
                value.get("intelligence_reports", []), "party intelligence reports"
            ),
        )


@dataclass(slots=True)
class TerritoryState:
    """Reusable Barron Lands sprawl/U.F. camp simulation record."""

    territory_id: str
    owner_id: str | None = None
    level: int = 0
    population: int = 0
    supply: int = 0
    defense: int = 0
    prosperity: int = 0
    tension: int = 0
    visibility: int = 0
    immunity_until: float = 0.0
    active_threats: set[str] = field(default_factory=set)
    citizens: set[str] = field(default_factory=set)
    alliances: set[str] = field(default_factory=set)
    caravan_route_ids: set[str] = field(default_factory=set)
    world_modifiers: set[str] = field(default_factory=set)
    maintenance_ready_turns: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "territory_id": self.territory_id,
            "owner_id": self.owner_id,
            "level": self.level,
            "population": self.population,
            "supply": self.supply,
            "defense": self.defense,
            "prosperity": self.prosperity,
            "tension": self.tension,
            "visibility": self.visibility,
            "immunity_until": self.immunity_until,
            "active_threats": sorted(self.active_threats),
            "citizens": sorted(self.citizens),
            "alliances": sorted(self.alliances),
            "caravan_route_ids": sorted(self.caravan_route_ids),
            "world_modifiers": sorted(self.world_modifiers),
            "maintenance_ready_turns": dict(sorted(self.maintenance_ready_turns.items())),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TerritoryState":
        if not isinstance(value, dict):
            raise ValueError("territory state must be an object")
        level = int(value.get("level", 0))
        if level not in TERRITORY_LEVELS:
            raise ValueError("territory level must be 0-4")
        bounded: dict[str, int] = {}
        for key in ("population", "supply", "defense", "prosperity", "tension", "visibility"):
            numeric = int(value.get(key, 0))
            if not 0 <= numeric <= 1_000_000:
                raise ValueError(f"territory {key} must be bounded")
            bounded[key] = numeric
        immunity = float(value.get("immunity_until", 0.0))
        if immunity < 0:
            raise ValueError("territory immunity timestamp cannot be negative")
        raw_ready = value.get("maintenance_ready_turns", {})
        if not isinstance(raw_ready, dict) or len(raw_ready) > 64:
            raise ValueError("territory maintenance readiness must be bounded")
        ready: dict[str, int] = {}
        for action_id, turn in raw_ready.items():
            normalized = str(_text(action_id, "territory maintenance action ID"))
            numeric = int(turn)
            if not 0 <= numeric <= 1_000_000_000:
                raise ValueError("territory maintenance turn must be bounded")
            ready[normalized] = numeric
        return cls(
            territory_id=str(_text(value.get("territory_id"), "territory ID")),
            owner_id=_text(value.get("owner_id"), "territory owner ID", optional=True),
            level=level,
            immunity_until=immunity,
            active_threats=_bounded_text_set(value.get("active_threats", []), "territory threats"),
            citizens=_bounded_text_set(value.get("citizens", []), "territory citizens"),
            alliances=_bounded_text_set(value.get("alliances", []), "territory alliances"),
            caravan_route_ids=_bounded_text_set(value.get("caravan_route_ids", []), "caravan routes"),
            world_modifiers=_bounded_text_set(value.get("world_modifiers", []), "territory modifiers"),
            maintenance_ready_turns=ready,
            **bounded,
        )


@dataclass(slots=True)
class QuestMachineState:
    """Branching objective state separate from authored quest definitions."""

    quest_id: str
    status: str = "inactive"
    active_objective_ids: set[str] = field(default_factory=set)
    completed_objective_ids: set[str] = field(default_factory=set)
    failed_objective_ids: set[str] = field(default_factory=set)
    selected_resolution_id: str | None = None
    consequence_ids: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "quest_id": self.quest_id,
            "status": self.status,
            "active_objective_ids": sorted(self.active_objective_ids),
            "completed_objective_ids": sorted(self.completed_objective_ids),
            "failed_objective_ids": sorted(self.failed_objective_ids),
            "selected_resolution_id": self.selected_resolution_id,
            "consequence_ids": sorted(self.consequence_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "QuestMachineState":
        if not isinstance(value, dict):
            raise ValueError("quest machine state must be an object")
        status = str(value.get("status", "inactive"))
        if status not in QUEST_STATUSES:
            raise ValueError("quest machine status is invalid")
        active = _bounded_text_set(value.get("active_objective_ids", []), "active objectives")
        completed = _bounded_text_set(value.get("completed_objective_ids", []), "completed objectives")
        failed = _bounded_text_set(value.get("failed_objective_ids", []), "failed objectives")
        if (active & completed) or (active & failed) or (completed & failed):
            raise ValueError("quest objectives cannot occupy multiple terminal states")
        return cls(
            quest_id=str(_text(value.get("quest_id"), "quest machine ID")),
            status=status,
            active_objective_ids=active,
            completed_objective_ids=completed,
            failed_objective_ids=failed,
            selected_resolution_id=_text(
                value.get("selected_resolution_id"), "quest resolution ID", optional=True
            ),
            consequence_ids=_bounded_text_set(value.get("consequence_ids", []), "quest consequences"),
        )


@dataclass(slots=True)
class FoundationState:
    """Versioned container for the next sovereignty-scale systems."""

    schema_version: int = 3
    sovereignty: SovereigntyState = field(default_factory=SovereigntyState)
    party: PartyState = field(default_factory=PartyState)
    territories: dict[str, TerritoryState] = field(default_factory=dict)
    quests: dict[str, QuestMachineState] = field(default_factory=dict)
    applied_story_record_ids: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sovereignty": self.sovereignty.to_dict(),
            "party": self.party.to_dict(),
            "territories": {key: value.to_dict() for key, value in sorted(self.territories.items())},
            "quests": {key: value.to_dict() for key, value in sorted(self.quests.items())},
            "applied_story_record_ids": sorted(self.applied_story_record_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FoundationState":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("foundation state must be an object")
        schema = int(value.get("schema_version", 3))
        if schema != 3:
            raise ValueError("unsupported foundation state schema")
        raw_territories = value.get("territories", {})
        raw_quests = value.get("quests", {})
        if not isinstance(raw_territories, dict) or len(raw_territories) > MAX_TERRITORIES:
            raise ValueError("foundation territories must be a bounded object")
        if not isinstance(raw_quests, dict) or len(raw_quests) > MAX_QUEST_MACHINES:
            raise ValueError("foundation quests must be a bounded object")
        territories = {str(key): TerritoryState.from_dict(item) for key, item in raw_territories.items()}
        if any(key != item.territory_id for key, item in territories.items()):
            raise ValueError("territory map keys must match territory IDs")
        quests = {str(key): QuestMachineState.from_dict(item) for key, item in raw_quests.items()}
        if any(key != item.quest_id for key, item in quests.items()):
            raise ValueError("quest map keys must match quest IDs")
        return cls(
            schema_version=schema,
            sovereignty=SovereigntyState.from_dict(value.get("sovereignty")),
            party=PartyState.from_dict(value.get("party")),
            territories=territories,
            quests=quests,
            applied_story_record_ids=_bounded_text_set(
                value.get("applied_story_record_ids", []),
                "applied foundation story records",
            ),
        )
