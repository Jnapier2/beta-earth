"""Deterministic canon-aware compiler for Beta Earth content.

The editable JSON files remain the authoring surface. This compiler validates
those files through the runtime loader, checks canon-sensitive provenance
against the supplied Master Canon v5 evidence, verifies world reachability,
and emits a compact deterministic runtime content pack.

It deliberately does not promote game-design proposals or original gameplay
content into story canon. Every provenance record carries both a canon status
and a source-authority boundary.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

from beta_earth.infrastructure.content_loader import ContentError, load_catalog


CANON_STATUSES = frozenset(
    {"explicit_canon", "strong_inference", "unspecified", "artistic_interpretation"}
)
SOURCE_AUTHORITIES = frozenset(
    {"story_canon", "beta_earth_online_design", "combined_with_gameplay_interpretation"}
)
TIMELINE_VALUES = frozenset(
    {
        "current",
        "destroyed_first_paradise",
        "historical",
        "game_continuity_unspecified",
        "not_applicable",
    }
)
REQUIRED_IDENTITY_RULE_FRAGMENTS = (
    "Riff is canonical",
    "Quell Tarsus",
    "Gene/Eugene Midas",
    "Exari may be called XR",
    "Leah Gaspar = Red",
    "SPQR Rick and Sky Rider Rick Martinez are separate",
    "Travis and Decius are separate",
    "Raph and Tryle are separate",
    "Theia/Beta Earth is one planetary body",
    "destroyed first-Paradise timeline",
    "Roni is alive/unresponsive",
)


class ContentCompileError(ValueError):
    """Raised when authored content cannot enter the player runtime pack."""


@dataclass(frozen=True, slots=True)
class CompileIssue:
    severity: str
    code: str
    detail: str
    entity_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ContentCompileReport:
    status: str
    content_version: str
    canon_version: str
    source_file_count: int
    compiled_file_count: int
    room_count: int
    reachable_room_count: int
    character_count: int
    section_count: int
    provenance_record_count: int
    output_sha256: dict[str, str] = field(default_factory=dict)
    issues: list[CompileIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "content_version": self.content_version,
            "canon_version": self.canon_version,
            "source_file_count": self.source_file_count,
            "compiled_file_count": self.compiled_file_count,
            "room_count": self.room_count,
            "reachable_room_count": self.reachable_room_count,
            "character_count": self.character_count,
            "section_count": self.section_count,
            "provenance_record_count": self.provenance_record_count,
            "output_sha256": dict(sorted(self.output_sha256.items())),
            "issues": [item.to_dict() for item in self.issues],
        }


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContentCompileError(f"missing compiler input: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContentCompileError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContentCompileError(f"{path} must contain a JSON object")
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentCompileError(f"{label} must be non-empty text")
    return value.strip()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_master_canon(value: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    if value.get("schema_version") != "5.0.0" or value.get("version") != "5.0.0":
        raise ContentCompileError("Master Canon must be exact schema/version 5.0.0")
    coverage = value.get("coverage")
    if not isinstance(coverage, dict):
        raise ContentCompileError("Master Canon coverage must be an object")
    expected = {
        "structured_sections": 145,
        "canonical_named_characters": 96,
        "visible_body_words": 119250,
    }
    for key, count in expected.items():
        if coverage.get(key) != count:
            raise ContentCompileError(
                f"Master Canon coverage conflict for {key}: expected {count}, found {coverage.get(key)!r}"
            )
    rules = value.get("mandatory_identity_rules")
    if not isinstance(rules, list) or not all(isinstance(item, str) for item in rules):
        raise ContentCompileError("Master Canon mandatory identity rules are missing")
    joined = "\n".join(rules)
    missing = [fragment for fragment in REQUIRED_IDENTITY_RULE_FRAGMENTS if fragment not in joined]
    if missing:
        raise ContentCompileError(f"Master Canon is missing identity rules: {missing}")

    characters = value.get("characters")
    if not isinstance(characters, list) or len(characters) != 96:
        raise ContentCompileError("Master Canon must contain 96 canonical characters")
    character_map: dict[str, dict[str, Any]] = {}
    canonical_names: set[str] = set()
    for index, record in enumerate(characters):
        if not isinstance(record, dict):
            raise ContentCompileError(f"Master Canon character {index} must be an object")
        character_id = _require_text(record.get("character_id"), f"character {index}.character_id")
        name = _require_text(record.get("name"), f"character {character_id}.name")
        if character_id in character_map:
            raise ContentCompileError(f"duplicate canonical character ID: {character_id}")
        folded = name.casefold()
        if folded in canonical_names:
            raise ContentCompileError(f"duplicate canonical character name: {name}")
        canonical_names.add(folded)
        character_map[character_id] = record

    sections = value.get("sections")
    if not isinstance(sections, list) or len(sections) != 145:
        raise ContentCompileError("Master Canon must contain 145 section records")
    section_ids = {
        _require_text(record.get("section_id"), "canon section ID")
        for record in sections
        if isinstance(record, dict)
    }
    if len(section_ids) != 145:
        raise ContentCompileError("Master Canon section IDs are not unique and complete")
    return character_map, section_ids


def _entity_records(content_root: Path) -> dict[str, dict[str, Any]]:
    """Return only stable authoring entities that can carry provenance."""

    specs = (
        ("world.json", "rooms", "room", "title"),
        ("items.json", "items", "item", "name"),
        ("creatures.json", "creatures", "creature", "name"),
        ("npcs.json", "npcs", "npc", "name"),
        ("quests.json", "quests", "quest", "title"),
        ("classes.json", "factions", "faction", "name"),
        ("classes.json", "classes", "class", "name"),
        ("courses.json", "courses", "course", "name"),
        ("economy.json", "vendors", "vendor", "name"),
        ("economy.json", "mercenaries", "mercenary", "name"),
        ("economy.json", "recipes", "recipe", "name"),
    )
    entities: dict[str, dict[str, Any]] = {}
    cache: dict[str, dict[str, Any]] = {}
    for filename, collection, entity_type, display_field in specs:
        document = cache.setdefault(filename, _load_object(content_root / filename))
        records = document.get(collection, [])
        if not isinstance(records, list):
            raise ContentCompileError(f"{filename}.{collection} must be a list")
        for record in records:
            if not isinstance(record, dict):
                raise ContentCompileError(f"{filename}.{collection} includes a non-object")
            entity_id = _require_text(record.get("id"), f"{filename}.{collection}.id")
            display_name = _require_text(
                record.get(display_field), f"{filename}.{collection}.{entity_id}.{display_field}"
            )
            key = f"{entity_type}:{entity_id}"
            if key in entities:
                raise ContentCompileError(f"duplicate compiler entity key: {key}")
            entities[key] = {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "display_name": display_name,
                "source_file": filename,
            }
    return entities


def _validate_provenance(
    value: dict[str, Any],
    *,
    entities: dict[str, dict[str, Any]],
    character_map: dict[str, dict[str, Any]],
    section_ids: set[str],
) -> list[dict[str, Any]]:
    if value.get("schema_version") != 1:
        raise ContentCompileError("canon_provenance.json schema_version must be 1")
    records = value.get("records")
    if not isinstance(records, list):
        raise ContentCompileError("canon_provenance.json.records must be a list")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ContentCompileError(f"provenance record {index} must be an object")
        entity_type = _require_text(record.get("entity_type"), f"provenance {index}.entity_type")
        entity_id = _require_text(record.get("entity_id"), f"provenance {index}.entity_id")
        key = f"{entity_type}:{entity_id}"
        if key in seen:
            raise ContentCompileError(f"duplicate provenance record: {key}")
        seen.add(key)
        entity = entities.get(key)
        if entity is None:
            raise ContentCompileError(f"provenance references unknown content entity: {key}")
        display_name = _require_text(record.get("display_name"), f"{key}.display_name")
        if display_name != entity["display_name"]:
            raise ContentCompileError(
                f"{key} display-name drift: provenance={display_name!r}, content={entity['display_name']!r}"
            )
        canon_status = _require_text(record.get("canon_status"), f"{key}.canon_status")
        if canon_status not in CANON_STATUSES:
            raise ContentCompileError(f"{key} has unsupported canon_status {canon_status!r}")
        source_authority = _require_text(record.get("source_authority"), f"{key}.source_authority")
        if source_authority not in SOURCE_AUTHORITIES:
            raise ContentCompileError(f"{key} has unsupported source_authority {source_authority!r}")
        timeline = _require_text(record.get("timeline"), f"{key}.timeline")
        if timeline not in TIMELINE_VALUES:
            raise ContentCompileError(f"{key} has unsupported timeline {timeline!r}")
        interpretation = _require_text(
            record.get("gameplay_interpretation"), f"{key}.gameplay_interpretation"
        )
        canonical_id = record.get("canonical_id")
        if canonical_id is not None:
            canonical_id = _require_text(canonical_id, f"{key}.canonical_id")
            if canonical_id not in character_map:
                raise ContentCompileError(f"{key} references unknown canonical character {canonical_id!r}")
        if canon_status in {"explicit_canon", "strong_inference"} and canonical_id is None:
            # Factions/places can be canonical without being character records.
            if entity_type in {"npc", "creature", "mercenary"}:
                raise ContentCompileError(f"{key} requires canonical_id for {canon_status}")
        raw_refs = record.get("source_refs", [])
        if not isinstance(raw_refs, list) or not raw_refs or not all(
            isinstance(item, str) and item.strip() for item in raw_refs
        ):
            raise ContentCompileError(f"{key}.source_refs must be a non-empty string list")
        refs = [item.strip() for item in raw_refs]
        invalid = [
            item
            for item in refs
            if item not in section_ids and not item.startswith("GAME_DESIGN:")
        ]
        if invalid:
            raise ContentCompileError(f"{key} has unknown source refs: {invalid}")
        normalized.append(
            {
                "entity_key": key,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "display_name": display_name,
                "canon_status": canon_status,
                "source_authority": source_authority,
                "canonical_id": canonical_id,
                "source_refs": refs,
                "timeline": timeline,
                "gameplay_interpretation": interpretation,
            }
        )

    by_key = {record["entity_key"]: record for record in normalized}
    canonical_name_to_id = {
        _require_text(record.get("name"), f"character {character_id}.name").casefold(): character_id
        for character_id, record in character_map.items()
    }
    for key, entity in entities.items():
        if entity["entity_type"] not in {"npc", "creature", "mercenary"}:
            continue
        canonical_id = canonical_name_to_id.get(entity["display_name"].casefold())
        if canonical_id is None:
            continue
        record = by_key.get(key)
        if record is None:
            raise ContentCompileError(
                f"canon-sensitive entity {key} ({entity['display_name']}) lacks provenance"
            )
        if record.get("canonical_id") != canonical_id:
            raise ContentCompileError(
                f"{key} canonical identity mismatch: expected {canonical_id!r}"
            )

    # Explicit guardrails for known manuscript variants and separate identities.
    for key, entity in entities.items():
        folded = entity["display_name"].casefold()
        if folded == "rift":
            raise ContentCompileError(f"{key} uses Rift as a canonical entity name; use Riff")
        if folded == "quell tarsus":
            raise ContentCompileError(f"{key} uses mistaken print Quell Tarsus")
    distinct = {"gate", "cage", "riff"}
    assigned = {
        record.get("canonical_id")
        for record in normalized
        if record.get("canonical_id") in distinct
    }
    if len(assigned) != len(set(assigned)):
        raise ContentCompileError("Gate, Cage, and Riff provenance identities must remain separate")
    return sorted(normalized, key=lambda record: record["entity_key"])


def _reachable_rooms(world_doc: dict[str, Any]) -> tuple[int, int, list[str]]:
    rooms = world_doc.get("rooms")
    if not isinstance(rooms, list):
        raise ContentCompileError("world rooms must be a list")
    room_map = {
        _require_text(room.get("id"), "room.id"): room
        for room in rooms
        if isinstance(room, dict)
    }
    start = _require_text(world_doc.get("start_room"), "world.start_room")
    if start not in room_map:
        raise ContentCompileError("world start room does not exist")
    visited = {start}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        exits = room_map[current].get("exits", {})
        if not isinstance(exits, dict):
            raise ContentCompileError(f"room {current}.exits must be an object")
        for destination in exits.values():
            destination_id = _require_text(destination, f"room {current} exit")
            if destination_id not in room_map:
                raise ContentCompileError(
                    f"room {current} points to missing destination {destination_id}"
                )
            if destination_id not in visited:
                visited.add(destination_id)
                queue.append(destination_id)
    unreachable = sorted(set(room_map) - visited)
    return len(room_map), len(visited), unreachable


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _runtime_json_bytes(value: Any) -> bytes:
    """Emit deterministic runtime JSON without destroying authored mapping order.

    Some Beta Earth content mappings are deliberately ordered progression
    contracts (for example onboarding checkpoint -> level). Alphabetically
    sorting every object is byte-stable but changes those authored semantics.
    Python's JSON loader preserves source order, so compact no-sort output is
    deterministic for the same reviewed source while retaining that contract.
    """

    return (json.dumps(value, ensure_ascii=False, sort_keys=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def compile_content(
    content_root: Path,
    *,
    canon_data_path: Path,
    provenance_path: Path | None = None,
    output_root: Path,
) -> ContentCompileReport:
    """Validate and deterministically compile editable content JSON files."""

    content_root = content_root.resolve()
    output_root = output_root.resolve()
    provenance_path = provenance_path or (content_root / "canon_provenance.json")

    try:
        catalog = load_catalog(content_root)
    except ContentError as exc:
        raise ContentCompileError(str(exc)) from exc

    canon = _load_object(canon_data_path)
    character_map, section_ids = _validate_master_canon(canon)
    entities = _entity_records(content_root)
    provenance = _validate_provenance(
        _load_object(provenance_path),
        entities=entities,
        character_map=character_map,
        section_ids=section_ids,
    )
    world_doc = _load_object(content_root / "world.json")
    room_count, reachable_count, unreachable = _reachable_rooms(world_doc)
    if unreachable:
        raise ContentCompileError(f"unreachable rooms: {unreachable}")

    source_files = sorted(content_root.glob("*.json"), key=lambda path: path.name.casefold())
    output_root.mkdir(parents=True, exist_ok=True)
    for existing in output_root.glob("*.json"):
        existing.unlink()

    hashes: dict[str, str] = {}
    for source in source_files:
        if source.name == provenance_path.name:
            # The normalized provenance becomes the runtime-facing artifact.
            continue
        value = _load_object(source)
        destination = output_root / source.name
        destination.write_bytes(_runtime_json_bytes(value))
        hashes[source.name] = _file_sha256(destination)

    normalized_provenance = {
        "schema_version": 1,
        "canon_version": canon["version"],
        "content_version": catalog.version,
        "records": provenance,
    }
    provenance_destination = output_root / "canon_provenance.json"
    provenance_destination.write_bytes(_canonical_json_bytes(normalized_provenance))
    hashes[provenance_destination.name] = _file_sha256(provenance_destination)

    report = ContentCompileReport(
        status="PASS",
        content_version=catalog.version,
        canon_version=str(canon["version"]),
        source_file_count=len(source_files),
        compiled_file_count=len(hashes),
        room_count=room_count,
        reachable_room_count=reachable_count,
        character_count=len(character_map),
        section_count=len(section_ids),
        provenance_record_count=len(provenance),
        output_sha256=hashes,
        issues=[],
    )
    receipt_path = output_root / "COMPILED_CONTENT_RECEIPT.json"
    receipt_path.write_bytes(_canonical_json_bytes(report.to_dict()))
    report.output_sha256[receipt_path.name] = _file_sha256(receipt_path)
    # Rewrite once so the receipt includes all content hashes except its unstable self-hash.
    receipt_value = report.to_dict()
    receipt_value["output_sha256"].pop(receipt_path.name, None)
    receipt_value["receipt_self_hash_note"] = "Omitted because a receipt cannot contain its own stable SHA-256."
    receipt_path.write_bytes(_canonical_json_bytes(receipt_value))
    report.output_sha256.pop(receipt_path.name, None)
    return report


def verify_compiled_content(output_root: Path, report: ContentCompileReport | dict[str, Any]) -> None:
    """Fail closed when compiled files do not match a prior compile report."""

    value = report.to_dict() if isinstance(report, ContentCompileReport) else report
    hashes = value.get("output_sha256")
    if not isinstance(hashes, dict):
        raise ContentCompileError("compiled content report lacks output hashes")
    for relative, expected in hashes.items():
        path = output_root / relative
        if not path.is_file():
            raise ContentCompileError(f"compiled content file is missing: {relative}")
        actual = _file_sha256(path)
        if actual != expected:
            raise ContentCompileError(
                f"compiled content hash mismatch for {relative}: {actual} != {expected}"
            )

    # Hash agreement alone cannot prove that a compiled pack still satisfies
    # runtime semantic contracts. Reload the exact compiled directory so
    # ordered progression maps, references, and startup validations fail here
    # rather than after the player ZIP is promoted.
    try:
        load_catalog(output_root)
    except ContentError as exc:
        raise ContentCompileError(f"compiled runtime content is invalid: {exc}") from exc
