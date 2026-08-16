"""Strict JSON content loading with cross-reference validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from beta_earth.domain.content import (
    AbilityBranchDefinition,
    AbilityFollowUpDefinition,
    AbilityPassiveDefinition,
    AbilityUpgradeDefinition,
    BeginnerChapterDefinition,
    BeginnerClassAssignmentDefinition,
    BeginnerCompetencyDefinition,
    BeginnerDifficultyBandDefinition,
    BeginnerDifficultyCurveDefinition,
    BeginnerExperienceDefinition,
    BeginnerInjuryDefinition,
    CharacterClassDefinition,
    CharacterCreationDefinition,
    ContentCatalog,
    CourseDefinition,
    CourseStepDefinition,
    CreationAttributeDefinition,
    CreationPackageDefinition,
    CreatureSpawnDefinition,
    CreatureDefinition,
    FactionRouteDefinition,
    FactionPledgeDefinition,
    CivicPlanDefinition,
    CivicMissionDefinition,
    FoundationActivationDefinition,
    FoundationFactionImpactDefinition,
    FoundationTerritoryImpactDefinition,
    ItemSpawnDefinition,
    ItemDefinition,
    DialogueDefinition,
    EconomyDefinition,
    MercenaryDefinition,
    NpcDefinition,
    ProgressionDefinition,
    SovereigntyRecordDefinition,
    StoryActionDefinition,
    StoryClassVariantDefinition,
    StoryDefinition,
    StoryEventTransitionDefinition,
    StoryQuestDefinition,
    StoryRewardDefinition,
    StoryStageDefinition,
    RoomDefinition,
    SearchReveal,
    StandingBandDefinition,
    TerritoryMaintenanceDefinition,
    TerritorySeedDefinition,
    TrainingOptionDefinition,
    TrainingProfileDefinition,
    TutorialDefinition,
    TutorialStepDefinition,
    RecipeDefinition,
    VendorDefinition,
)


class ContentError(ValueError):
    """Raised when authored data cannot safely enter the domain."""


_MISSING = object()
_MAX_CONTENT_INTEGER = 2_147_483_647
_SEMANTIC_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)


def _read_json(path: Path, supported_schemas: set[int]) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ContentError(f"missing content file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContentError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContentError(f"{path} must contain a JSON object")
    if value.get("schema_version") not in supported_schemas:
        raise ContentError(f"{path} has an unsupported schema_version")
    return value


def _required_text(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"{context}.{key} must be non-empty text")
    return value.strip()


def _string_tuple(value: Any, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ContentError(f"{context} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


WORLD_BODY_VALUES = {"earth", "beta-earth", "between-worlds", "mechanical", "abstract-threshold", "unspecified"}


def _world_body(value: Any, context: str) -> str:
    if value is None:
        return "unspecified"
    if not isinstance(value, str) or value.strip().casefold() not in WORLD_BODY_VALUES:
        raise ContentError(f"{context} must be one of {sorted(WORLD_BODY_VALUES)}")
    return value.strip().casefold()


def _optional_text(value: Any, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"{context} must be non-empty text when provided")
    return value.strip()


def _require_unique_nouns(nouns: tuple[str, ...], context: str) -> None:
    """Reject aliases that differ only by case or surrounding whitespace."""

    folded = [noun.casefold() for noun in nouns]
    if len(folded) != len(set(folded)):
        raise ContentError(f"{context} contains duplicate nouns")


def _integer(
    record: dict[str, Any],
    key: str,
    context: str,
    *,
    default: object = _MISSING,
    minimum: int = -_MAX_CONTENT_INTEGER,
    maximum: int = _MAX_CONTENT_INTEGER,
) -> int:
    if key in record:
        value = record[key]
    elif default is not _MISSING:
        value = default
    else:
        raise ContentError(f"{context}.{key} is required")
    if type(value) is not int:
        raise ContentError(f"{context}.{key} must be an integer")
    if value < minimum or value > maximum:
        raise ContentError(
            f"{context}.{key} must be between {minimum} and {maximum}"
        )
    return value


def _boolean(
    record: dict[str, Any],
    key: str,
    context: str,
    *,
    default: bool = False,
) -> bool:
    value = record.get(key, default)
    if type(value) is not bool:
        raise ContentError(f"{context}.{key} must be a boolean")
    return value


def _choice(
    record: dict[str, Any],
    key: str,
    context: str,
    choices: set[str],
    *,
    default: str,
) -> str:
    value = record.get(key, default)
    if not isinstance(value, str) or value not in choices:
        rendered = ", ".join(sorted(choices))
        raise ContentError(f"{context}.{key} must be one of: {rendered}")
    return value


def _damage_range(value: Any, context: str) -> tuple[int, int]:
    values = value
    if (
        not isinstance(values, list)
        or len(values) != 2
        or any(type(item) is not int for item in values)
        or values[0] < 0
        or values[1] < values[0]
        or values[1] > _MAX_CONTENT_INTEGER
    ):
        raise ContentError(
            f"{context} must be two ordered non-negative integers [min, max]"
        )
    return values[0], values[1]


def _semantic_version(value: Any, context: str) -> tuple[str, tuple[int, int, int]]:
    if not isinstance(value, str) or not _SEMANTIC_VERSION.fullmatch(value.strip()):
        raise ContentError(f"{context} must be numeric semantic versioning (major.minor.patch)")
    normalized = value.strip()
    major, minor, patch = (int(part) for part in normalized.split("."))
    return normalized, (major, minor, patch)


def _unique_records(document: dict[str, Any], key: str, path: Path) -> list[dict[str, Any]]:
    records = document.get(key)
    if not isinstance(records, list):
        raise ContentError(f"{path}.{key} must be a list")
    seen: set[str] = set()
    checked: list[dict[str, Any]] = []
    for index, value in enumerate(records):
        if not isinstance(value, dict):
            raise ContentError(f"{path}.{key}[{index}] must be an object")
        record_id = _required_text(value, "id", f"{path}.{key}[{index}]")
        if record_id in seen:
            raise ContentError(f"duplicate {key} id: {record_id}")
        seen.add(record_id)
        checked.append(value)
    return checked


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_DOTTED_IDENTIFIER = re.compile(
    r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\Z"
)


def _identifier(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value.strip()):
        raise ContentError(f"{context} must be a lowercase identifier")
    return value.strip()


def _optional_identifier(value: Any, context: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, context)


def _load_story(
    content_root: Path,
    *,
    rooms: dict[str, RoomDefinition],
    items: dict[str, ItemDefinition],
    classes: dict[str, CharacterClassDefinition],
) -> StoryDefinition:
    npcs_path = content_root / "npcs.json"
    dialogue_path = content_root / "dialogue.json"
    quests_path = content_root / "quests.json"
    rewards_path = content_root / "rewards.json"
    npcs_doc = _read_json(npcs_path, {1})
    dialogue_doc = _read_json(dialogue_path, {1})
    quests_doc = _read_json(quests_path, {1})
    rewards_doc = _read_json(rewards_path, {1})

    npcs: dict[str, NpcDefinition] = {}
    noun_index: dict[str, str] = {}
    for record in _unique_records(npcs_doc, "npcs", npcs_path):
        npc_id = _identifier(record.get("id"), "NPC id")
        nouns = _string_tuple(record.get("nouns"), f"NPC {npc_id}.nouns")
        if not nouns:
            raise ContentError(f"NPC {npc_id} requires at least one noun")
        _require_unique_nouns(nouns, f"NPC {npc_id}.nouns")
        room_id = _identifier(record.get("room_id"), f"NPC {npc_id}.room_id")
        if room_id not in rooms:
            raise ContentError(f"NPC {npc_id} references unknown room {room_id!r}")
        for noun in nouns:
            key = noun.casefold()
            if key in noun_index:
                raise ContentError(
                    f"NPC noun {noun!r} is shared by {noun_index[key]} and {npc_id}"
                )
            noun_index[key] = npc_id
        npcs[npc_id] = NpcDefinition(
            id=npc_id,
            name=_required_text(record, "name", f"NPC {npc_id}"),
            description=_required_text(record, "description", f"NPC {npc_id}"),
            nouns=nouns,
            room_id=room_id,
            relationship_label=_required_text(
                record,
                "relationship_label",
                f"NPC {npc_id}",
            ),
            ambient_text=str(record.get("ambient_text", "")).strip(),
            requires_flags=_string_tuple(
                record.get("requires_flags"), f"NPC {npc_id}.requires_flags"
            ),
            forbidden_flags=_string_tuple(
                record.get("forbidden_flags"), f"NPC {npc_id}.forbidden_flags"
            ),
            schedule_rooms=(
                {str(phase): _identifier(room, f"NPC {npc_id}.schedule_rooms.{phase}")
                 for phase, room in record.get("schedule_rooms", {}).items()}
                if isinstance(record.get("schedule_rooms", {}), dict)
                and all(phase in {"rest", "market", "field", "watch"}
                        and isinstance(room, str) and room.strip()
                        for phase, room in record.get("schedule_rooms", {}).items())
                else (_ for _ in ()).throw(
                    ContentError(f"NPC {npc_id}.schedule_rooms must map rest/market/field/watch to room ids")
                )
            ),
            source_features=_string_tuple(
                record.get("source_features"),
                f"NPC {npc_id}.source_features",
            ),
        )
        for phase, scheduled_room in npcs[npc_id].schedule_rooms.items():
            if scheduled_room not in rooms:
                raise ContentError(
                    f"NPC {npc_id} schedule phase {phase!r} references unknown room {scheduled_room!r}"
                )

    dialogues: dict[str, DialogueDefinition] = {}
    for record in _unique_records(dialogue_doc, "dialogue", dialogue_path):
        dialogue_id = _identifier(record.get("id"), "dialogue id")
        npc_id = _identifier(
            record.get("npc_id"),
            f"dialogue {dialogue_id}.npc_id",
        )
        if npc_id not in npcs:
            raise ContentError(
                f"dialogue {dialogue_id} references unknown NPC {npc_id!r}"
            )
        dialogues[dialogue_id] = DialogueDefinition(
            id=dialogue_id,
            npc_id=npc_id,
            title=_required_text(record, "title", f"dialogue {dialogue_id}"),
            text=_required_text(record, "text", f"dialogue {dialogue_id}"),
            choice_ids=_string_tuple(
                record.get("choice_ids"),
                f"dialogue {dialogue_id}.choice_ids",
            ),
            source_features=_string_tuple(
                record.get("source_features"),
                f"dialogue {dialogue_id}.source_features",
            ),
        )

    rewards: dict[str, StoryRewardDefinition] = {}
    for record in _unique_records(rewards_doc, "rewards", rewards_path):
        reward_id = _identifier(record.get("id"), "story reward id")
        reward_items = _string_tuple(
            record.get("items"),
            f"story reward {reward_id}.items",
        )
        for item_id in reward_items:
            if item_id not in items:
                raise ContentError(
                    f"story reward {reward_id} references unknown item {item_id!r}"
                )
        rewards[reward_id] = StoryRewardDefinition(
            id=reward_id,
            title=_required_text(record, "title", f"story reward {reward_id}"),
            field_insight=_integer(
                record,
                "field_insight",
                f"story reward {reward_id}",
                minimum=0,
                maximum=1_000_000,
            ),
            physical_points=_integer(
                record,
                "physical_points",
                f"story reward {reward_id}",
                minimum=0,
                maximum=100_000,
            ),
            mental_points=_integer(
                record,
                "mental_points",
                f"story reward {reward_id}",
                minimum=0,
                maximum=100_000,
            ),
            credits=_integer(
                record,
                "credits",
                f"story reward {reward_id}",
                default=0,
                minimum=0,
                maximum=1_000_000,
            ),
            grants_ability_point=_boolean(
                record,
                "grants_ability_point",
                f"story reward {reward_id}",
            ),
            items=reward_items,
            source_features=_string_tuple(
                record.get("source_features"),
                f"story reward {reward_id}.source_features",
            ),
        )

    records: dict[str, SovereigntyRecordDefinition] = {}
    for record in _unique_records(quests_doc, "records", quests_path):
        record_id = _identifier(record.get("id"), "sovereignty record id")
        records[record_id] = SovereigntyRecordDefinition(
            id=record_id,
            label=_required_text(record, "label", f"record {record_id}"),
            description=_required_text(
                record,
                "description",
                f"record {record_id}",
            ),
        )

    quests: dict[str, StoryQuestDefinition] = {}
    action_ids: set[str] = set()
    raw_quests = _unique_records(quests_doc, "quests", quests_path)
    for raw_quest in raw_quests:
        quest_id = _identifier(raw_quest.get("id"), "quest id")
        raw_stages = raw_quest.get("stages")
        if not isinstance(raw_stages, list) or not raw_stages:
            raise ContentError(f"quest {quest_id}.stages must be a non-empty list")
        stages: list[StoryStageDefinition] = []
        stage_ids: set[str] = set()
        for stage_index, raw_stage in enumerate(raw_stages):
            context = f"quest {quest_id}.stages[{stage_index}]"
            if not isinstance(raw_stage, dict):
                raise ContentError(f"{context} must be an object")
            stage_id = _identifier(raw_stage.get("id"), f"{context}.id")
            if stage_id in stage_ids:
                raise ContentError(f"quest {quest_id} has duplicate stage {stage_id}")
            stage_ids.add(stage_id)
            raw_dialogues = raw_stage.get("dialogues", {})
            if not isinstance(raw_dialogues, dict) or not all(
                isinstance(key, str)
                and isinstance(value, str)
                and key.strip()
                and value.strip()
                for key, value in raw_dialogues.items()
            ):
                raise ContentError(
                    f"{context}.dialogues must map NPC IDs to dialogue IDs"
                )
            transitions: list[StoryEventTransitionDefinition] = []
            raw_transitions = raw_stage.get("event_transitions", [])
            if not isinstance(raw_transitions, list):
                raise ContentError(f"{context}.event_transitions must be a list")
            for transition_index, raw_transition in enumerate(raw_transitions):
                transition_context = (
                    f"{context}.event_transitions[{transition_index}]"
                )
                if not isinstance(raw_transition, dict):
                    raise ContentError(f"{transition_context} must be an object")
                event_kind = _required_text(
                    raw_transition,
                    "event_kind",
                    transition_context,
                )
                if not _DOTTED_IDENTIFIER.fullmatch(event_kind):
                    raise ContentError(
                        f"{transition_context}.event_kind must be dotted"
                    )
                raw_filters = raw_transition.get("event_filters", {})
                if not isinstance(raw_filters, dict) or not all(
                    isinstance(key, str)
                    and _IDENTIFIER.fullmatch(key)
                    and type(value) in {str, int, bool, type(None)}
                    and not (isinstance(value, str) and not value)
                    for key, value in raw_filters.items()
                ):
                    raise ContentError(
                        f"{transition_context}.event_filters must map identifiers "
                        "to scalar values"
                    )
                transitions.append(
                    StoryEventTransitionDefinition(
                        event_kind=event_kind,
                        event_filters=dict(raw_filters),
                        next_quest_id=_identifier(
                            raw_transition.get("next_quest_id"),
                            f"{transition_context}.next_quest_id",
                        ),
                        next_stage_id=_identifier(
                            raw_transition.get("next_stage_id"),
                            f"{transition_context}.next_stage_id",
                        ),
                        result_text=_required_text(
                            raw_transition,
                            "result_text",
                            transition_context,
                        ),
                        sets_flags=_string_tuple(
                            raw_transition.get("sets_flags"),
                            f"{transition_context}.sets_flags",
                        ),
                        records=_string_tuple(
                            raw_transition.get("records"),
                            f"{transition_context}.records",
                        ),
                        despawn_creatures=_string_tuple(
                            raw_transition.get("despawn_creatures"),
                            f"{transition_context}.despawn_creatures",
                        ),
                        reward_id=_optional_identifier(
                            raw_transition.get("reward_id"),
                            f"{transition_context}.reward_id",
                        ),
                    )
                )
            actions: list[StoryActionDefinition] = []
            raw_actions = raw_stage.get("actions", [])
            if not isinstance(raw_actions, list):
                raise ContentError(f"{context}.actions must be a list")
            for action_index, raw_action in enumerate(raw_actions):
                action_context = f"{context}.actions[{action_index}]"
                if not isinstance(raw_action, dict):
                    raise ContentError(f"{action_context} must be an object")
                action_id = _identifier(raw_action.get("id"), f"{action_context}.id")
                if action_id in action_ids:
                    raise ContentError(f"duplicate story action id: {action_id}")
                action_ids.add(action_id)
                nouns = _string_tuple(
                    raw_action.get("nouns"),
                    f"{action_context}.nouns",
                )
                if not nouns:
                    raise ContentError(f"{action_context}.nouns cannot be empty")
                _require_unique_nouns(nouns, f"{action_context}.nouns")
                raw_relationships = raw_action.get("relationship_changes", {})
                if not isinstance(raw_relationships, dict) or not all(
                    isinstance(key, str)
                    and key.strip()
                    and type(value) is int
                    and -100 <= value <= 100
                    for key, value in raw_relationships.items()
                ):
                    raise ContentError(
                        f"{action_context}.relationship_changes must map NPC IDs "
                        "to integers between -100 and 100"
                    )
                raw_variants = raw_action.get("class_variants", {})
                if not isinstance(raw_variants, dict):
                    raise ContentError(f"{action_context}.class_variants must be an object")
                class_variants: dict[str, StoryClassVariantDefinition] = {}
                for class_id, raw_variant in raw_variants.items():
                    if class_id not in classes:
                        raise ContentError(
                            f"{action_context} references unknown class {class_id!r}"
                        )
                    if not isinstance(raw_variant, dict):
                        raise ContentError(
                            f"{action_context}.class_variants.{class_id} must be an object"
                        )
                    class_variants[class_id] = StoryClassVariantDefinition(
                        label=_required_text(
                            raw_variant,
                            "label",
                            f"{action_context}.class_variants.{class_id}",
                        ),
                        summary=_required_text(
                            raw_variant,
                            "summary",
                            f"{action_context}.class_variants.{class_id}",
                        ),
                        result_text=_required_text(
                            raw_variant,
                            "result_text",
                            f"{action_context}.class_variants.{class_id}",
                        ),
                    )
                raw_route_interest = raw_action.get("route_interest", False)
                if not isinstance(raw_route_interest, bool):
                    raise ContentError(
                        f"{action_context}.route_interest must be boolean"
                    )
                raw_route_handoff = raw_action.get("route_handoff", False)
                if not isinstance(raw_route_handoff, bool):
                    raise ContentError(
                        f"{action_context}.route_handoff must be boolean"
                    )
                action = StoryActionDefinition(
                    id=action_id,
                    verb=_identifier(raw_action.get("verb"), f"{action_context}.verb"),
                    nouns=nouns,
                    label=_required_text(raw_action, "label", action_context),
                    summary=_required_text(raw_action, "summary", action_context),
                    approach=_identifier(
                        raw_action.get("approach"),
                        f"{action_context}.approach",
                    ),
                    result_text=_required_text(
                        raw_action,
                        "result_text",
                        action_context,
                    ),
                    requires_dialogue_id=_optional_identifier(
                        raw_action.get("requires_dialogue_id"),
                        f"{action_context}.requires_dialogue_id",
                    ),
                    requires_room_id=_optional_identifier(
                        raw_action.get("requires_room_id"),
                        f"{action_context}.requires_room_id",
                    ),
                    requires_items=_string_tuple(
                        raw_action.get("requires_items"),
                        f"{action_context}.requires_items",
                    ),
                    consumes_items=_string_tuple(
                        raw_action.get("consumes_items"),
                        f"{action_context}.consumes_items",
                    ),
                    requires_flags=_string_tuple(
                        raw_action.get("requires_flags"),
                        f"{action_context}.requires_flags",
                    ),
                    requires_records=_string_tuple(
                        raw_action.get("requires_records"),
                        f"{action_context}.requires_records",
                    ),
                    sets_flags=_string_tuple(
                        raw_action.get("sets_flags"),
                        f"{action_context}.sets_flags",
                    ),
                    clears_flags=_string_tuple(
                        raw_action.get("clears_flags"),
                        f"{action_context}.clears_flags",
                    ),
                    records=_string_tuple(
                        raw_action.get("records"),
                        f"{action_context}.records",
                    ),
                    relationship_changes={
                        str(key): int(value)
                        for key, value in raw_relationships.items()
                    },
                    reward_id=_optional_identifier(
                        raw_action.get("reward_id"),
                        f"{action_context}.reward_id",
                    ),
                    next_quest_id=_optional_identifier(
                        raw_action.get("next_quest_id"),
                        f"{action_context}.next_quest_id",
                    ),
                    next_stage_id=_optional_identifier(
                        raw_action.get("next_stage_id"),
                        f"{action_context}.next_stage_id",
                    ),
                    complete_quest=_boolean(
                        raw_action,
                        "complete_quest",
                        action_context,
                    ),
                    checkpoint_id=_optional_identifier(
                        raw_action.get("checkpoint_id"),
                        f"{action_context}.checkpoint_id",
                    ),
                    route_interest=raw_route_interest,
                    route_handoff=raw_route_handoff,
                    allow_under_pressure=_boolean(
                        raw_action,
                        "allow_under_pressure",
                        action_context,
                    ),
                    class_variants=class_variants,
                )
                if (action.next_quest_id is None) != (action.next_stage_id is None):
                    raise ContentError(
                        f"{action_context} must provide both next quest and stage"
                    )
                actions.append(action)
            progress_index = _integer(
                raw_stage,
                "progress_index",
                context,
                minimum=1,
                maximum=100,
            )
            progress_total = _integer(
                raw_stage,
                "progress_total",
                context,
                minimum=1,
                maximum=100,
            )
            if progress_index > progress_total:
                raise ContentError(f"{context}.progress_index exceeds total")
            stages.append(
                StoryStageDefinition(
                    id=stage_id,
                    title=_required_text(raw_stage, "title", context),
                    objective=_required_text(raw_stage, "objective", context),
                    directive=_required_text(raw_stage, "directive", context),
                    why=_required_text(raw_stage, "why", context),
                    room_hint=_required_text(raw_stage, "room_hint", context),
                    target_room_id=_optional_identifier(
                        raw_stage.get("target_room_id"),
                        f"{context}.target_room_id",
                    ),
                    suggested_command=_required_text(
                        raw_stage,
                        "suggested_command",
                        context,
                    ).casefold(),
                    progress_index=progress_index,
                    progress_total=progress_total,
                    dialogues={
                        str(key): str(value)
                        for key, value in raw_dialogues.items()
                    },
                    event_transitions=tuple(transitions),
                    actions=tuple(actions),
                )
            )
        quests[quest_id] = StoryQuestDefinition(
            id=quest_id,
            title=_required_text(raw_quest, "title", f"quest {quest_id}"),
            arc_title=_required_text(
                raw_quest,
                "arc_title",
                f"quest {quest_id}",
            ),
            summary=_required_text(raw_quest, "summary", f"quest {quest_id}"),
            source_features=_string_tuple(
                raw_quest.get("source_features"),
                f"quest {quest_id}.source_features",
            ),
            stages=tuple(stages),
        )

    stage_index = {
        (quest.id, stage.id): stage
        for quest in quests.values()
        for stage in quest.stages
    }
    starting_quest_id = _identifier(
        quests_doc.get("starting_quest_id"),
        f"{quests_path}.starting_quest_id",
    )
    starting_stage_id = _identifier(
        quests_doc.get("starting_stage_id"),
        f"{quests_path}.starting_stage_id",
    )
    if (starting_quest_id, starting_stage_id) not in stage_index:
        raise ContentError("starting story quest/stage does not exist")

    for dialogue in dialogues.values():
        for choice_id in dialogue.choice_ids:
            if choice_id not in action_ids:
                raise ContentError(
                    f"dialogue {dialogue.id} references unknown action {choice_id!r}"
                )
    for quest in quests.values():
        for stage in quest.stages:
            if (
                stage.target_room_id is not None
                and stage.target_room_id not in rooms
            ):
                raise ContentError(
                    f"quest {quest.id}/{stage.id} references unknown target room "
                    f"{stage.target_room_id!r}"
                )
            for npc_id, dialogue_id in stage.dialogues.items():
                if npc_id not in npcs:
                    raise ContentError(
                        f"quest {quest.id}/{stage.id} references unknown NPC {npc_id!r}"
                    )
                dialogue = dialogues.get(dialogue_id)
                if dialogue is None or dialogue.npc_id != npc_id:
                    raise ContentError(
                        f"quest {quest.id}/{stage.id} dialogue {dialogue_id!r} "
                        f"does not belong to NPC {npc_id!r}"
                    )
            for transition in stage.event_transitions:
                if (transition.next_quest_id, transition.next_stage_id) not in stage_index:
                    raise ContentError(
                        f"quest {quest.id}/{stage.id} transition points to an "
                        "unknown quest stage"
                    )
                for record_id in transition.records:
                    if record_id not in records:
                        raise ContentError(
                            f"quest {quest.id}/{stage.id} transition references unknown record {record_id!r}"
                        )
                if transition.reward_id and transition.reward_id not in rewards:
                    raise ContentError(
                        f"quest {quest.id}/{stage.id} transition references unknown reward {transition.reward_id!r}"
                    )
            for action in stage.actions:
                if action.requires_dialogue_id and action.requires_dialogue_id not in dialogues:
                    raise ContentError(
                        f"action {action.id} requires unknown dialogue "
                        f"{action.requires_dialogue_id!r}"
                    )
                if (
                    action.requires_room_id is not None
                    and action.requires_room_id not in rooms
                ):
                    raise ContentError(
                        f"action {action.id} references unknown room "
                        f"{action.requires_room_id!r}"
                    )
                for item_id in (*action.requires_items, *action.consumes_items):
                    if item_id not in items:
                        raise ContentError(
                            f"action {action.id} references unknown item {item_id!r}"
                        )
                for record_id in (*action.requires_records, *action.records):
                    if record_id not in records:
                        raise ContentError(
                            f"action {action.id} references unknown record {record_id!r}"
                        )
                for npc_id in action.relationship_changes:
                    if npc_id not in npcs:
                        raise ContentError(
                            f"action {action.id} references unknown NPC {npc_id!r}"
                        )
                if action.reward_id and action.reward_id not in rewards:
                    raise ContentError(
                        f"action {action.id} references unknown reward "
                        f"{action.reward_id!r}"
                    )
                if action.next_quest_id and (
                    action.next_quest_id,
                    action.next_stage_id,
                ) not in stage_index:
                    raise ContentError(
                        f"action {action.id} points to an unknown quest stage"
                    )

    return StoryDefinition(
        starting_quest_id=starting_quest_id,
        starting_stage_id=starting_stage_id,
        npcs=npcs,
        dialogues=dialogues,
        records=records,
        rewards=rewards,
        quests=quests,
    )



def _parse_additional_experience(
    document: dict[str, Any],
    *,
    context_label: str,
    rooms: dict[str, RoomDefinition],
    items: dict[str, ItemDefinition],
    story: StoryDefinition,
    character_classes: dict[str, CharacterClassDefinition],
) -> BeginnerExperienceDefinition:
    """Parse a later authored progression phase using the foundation contract.

    Later phases intentionally reuse the stable chapter, competency, class lens,
    difficulty-band, and recoverable-condition structures.  Their facts remain
    separate content, and a ``starting_level`` prevents the loader from silently
    treating levels 11-20 as another level-one tutorial.
    """

    phase_id = _identifier(document.get("id"), f"{context_label} id")
    target_minutes = _integer(
        document,
        "target_minutes",
        f"{context_label} {phase_id}",
        minimum=30,
        maximum=600,
    )
    starting_level = _integer(
        document,
        "starting_level",
        f"{context_label} {phase_id}",
        minimum=1,
        maximum=99,
    )
    target_level = _integer(
        document,
        "target_level",
        f"{context_label} {phase_id}",
        minimum=starting_level + 1,
        maximum=100,
    )
    starter_room_ids = _string_tuple(
        document.get("starter_room_ids"),
        f"{context_label} {phase_id}.starter_room_ids",
    )
    if not starter_room_ids or len(starter_room_ids) != len(set(starter_room_ids)):
        raise ContentError(f"{context_label} starter rooms must be unique and non-empty")
    unknown_starter_rooms = set(starter_room_ids) - set(rooms)
    if unknown_starter_rooms:
        raise ContentError(
            f"{context_label} references unknown starter rooms {sorted(unknown_starter_rooms)}"
        )

    chapters: list[BeginnerChapterDefinition] = []
    chapter_ids: set[str] = set()
    chapter_minutes = 0
    raw_chapters = document.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise ContentError(f"{context_label} requires at least one chapter")
    for raw in raw_chapters:
        if not isinstance(raw, dict):
            raise ContentError(f"{context_label} chapters must be objects")
        chapter_id = _identifier(raw.get("id"), f"{context_label} chapter id")
        if chapter_id in chapter_ids:
            raise ContentError(f"duplicate {context_label} chapter {chapter_id!r}")
        chapter_ids.add(chapter_id)
        quest_ids = _string_tuple(
            raw.get("quest_ids"), f"{context_label} chapter {chapter_id}.quest_ids"
        )
        if not quest_ids:
            raise ContentError(f"{context_label} chapter {chapter_id} requires quests")
        unknown_quests = set(quest_ids) - set(story.quests)
        if unknown_quests:
            raise ContentError(
                f"{context_label} chapter {chapter_id} references unknown quests {sorted(unknown_quests)}"
            )
        minutes = _integer(
            raw,
            "minutes",
            f"{context_label} chapter {chapter_id}",
            minimum=1,
            maximum=240,
        )
        chapter_minutes += minutes
        chapters.append(
            BeginnerChapterDefinition(
                id=chapter_id,
                title=_required_text(raw, "title", f"{context_label} chapter {chapter_id}"),
                summary=_required_text(raw, "summary", f"{context_label} chapter {chapter_id}"),
                minutes=minutes,
                quest_ids=quest_ids,
            )
        )
    if chapter_minutes != target_minutes:
        raise ContentError(
            f"{context_label} chapter minutes total {chapter_minutes}, expected {target_minutes}"
        )

    competencies: list[BeginnerCompetencyDefinition] = []
    competency_ids: set[str] = set()
    raw_competencies = document.get("competencies")
    if not isinstance(raw_competencies, list) or not raw_competencies:
        raise ContentError(f"{context_label} requires competencies")
    for raw in raw_competencies:
        if not isinstance(raw, dict):
            raise ContentError(f"{context_label} competencies must be objects")
        competency_id = _identifier(raw.get("id"), f"{context_label} competency id")
        if competency_id in competency_ids:
            raise ContentError(f"duplicate {context_label} competency {competency_id!r}")
        competency_ids.add(competency_id)
        required_quests = _string_tuple(
            raw.get("required_quests"),
            f"{context_label} competency {competency_id}.required_quests",
        )
        required_flags = _string_tuple(
            raw.get("required_flags"),
            f"{context_label} competency {competency_id}.required_flags",
        )
        if not required_quests and not required_flags:
            raise ContentError(
                f"{context_label} competency {competency_id} requires durable evidence"
            )
        unknown_quests = set(required_quests) - set(story.quests)
        if unknown_quests:
            raise ContentError(
                f"{context_label} competency {competency_id} references unknown quests {sorted(unknown_quests)}"
            )
        competencies.append(
            BeginnerCompetencyDefinition(
                id=competency_id,
                label=_required_text(raw, "label", f"{context_label} competency {competency_id}"),
                description=_required_text(raw, "description", f"{context_label} competency {competency_id}"),
                required_quests=required_quests,
                required_flags=required_flags,
            )
        )

    class_assignments: dict[str, BeginnerClassAssignmentDefinition] = {}
    raw_assignments = document.get("class_assignments")
    if not isinstance(raw_assignments, dict):
        raise ContentError(f"{context_label} class_assignments must be an object")
    if set(raw_assignments) != set(character_classes):
        missing = sorted(set(character_classes) - set(raw_assignments))
        extra = sorted(set(raw_assignments) - set(character_classes))
        raise ContentError(
            f"{context_label} class assignments must cover every class; missing={missing}, extra={extra}"
        )
    for class_id, raw in raw_assignments.items():
        if not isinstance(raw, dict):
            raise ContentError(f"{context_label} assignment {class_id} must be an object")
        class_assignments[class_id] = BeginnerClassAssignmentDefinition(
            class_id=class_id,
            title=_required_text(raw, "title", f"{context_label} assignment {class_id}"),
            objective=_required_text(raw, "objective", f"{context_label} assignment {class_id}"),
            practice_command=_required_text(raw, "practice_command", f"{context_label} assignment {class_id}"),
        )

    raw_curve = document.get("difficulty_curve")
    if not isinstance(raw_curve, dict):
        raise ContentError(f"{context_label} difficulty_curve must be an object")
    raw_checkpoints = raw_curve.get("level_checkpoints")
    if not isinstance(raw_checkpoints, dict) or not raw_checkpoints:
        raise ContentError(
            f"{context_label} difficulty_curve.level_checkpoints must be a non-empty object"
        )
    level_checkpoints: dict[str, int] = {}
    last_level = starting_level
    for quest_id, raw_level in raw_checkpoints.items():
        if quest_id not in story.quests:
            raise ContentError(
                f"{context_label} difficulty checkpoint references unknown quest {quest_id!r}"
            )
        if type(raw_level) is not int or not starting_level < raw_level <= target_level:
            raise ContentError(
                f"{context_label} difficulty checkpoint {quest_id!r} has invalid level"
            )
        if raw_level <= last_level:
            raise ContentError(
                f"{context_label} difficulty checkpoint levels must increase in authored order"
            )
        last_level = raw_level
        level_checkpoints[quest_id] = raw_level
    if last_level != target_level:
        raise ContentError(
            f"{context_label} difficulty checkpoints must finish at the target level"
        )

    raw_bands = raw_curve.get("bands")
    if not isinstance(raw_bands, list) or not raw_bands:
        raise ContentError(f"{context_label} difficulty_curve.bands must be a non-empty list")
    bands: list[BeginnerDifficultyBandDefinition] = []
    covered_levels: set[int] = set()
    band_ids: set[str] = set()
    for raw in raw_bands:
        if not isinstance(raw, dict):
            raise ContentError(f"{context_label} difficulty bands must be objects")
        band_id = _identifier(raw.get("id"), f"{context_label} difficulty band id")
        if band_id in band_ids:
            raise ContentError(f"duplicate {context_label} difficulty band {band_id!r}")
        band_ids.add(band_id)
        minimum_level = _integer(
            raw,
            "minimum_level",
            f"{context_label} difficulty band {band_id}",
            minimum=starting_level + 1,
            maximum=target_level,
        )
        maximum_level = _integer(
            raw,
            "maximum_level",
            f"{context_label} difficulty band {band_id}",
            minimum=minimum_level,
            maximum=target_level,
        )
        overlap = covered_levels & set(range(minimum_level, maximum_level + 1))
        if overlap:
            raise ContentError(
                f"{context_label} difficulty band {band_id!r} overlaps levels {sorted(overlap)}"
            )
        covered_levels.update(range(minimum_level, maximum_level + 1))
        bands.append(
            BeginnerDifficultyBandDefinition(
                id=band_id,
                label=_required_text(raw, "label", f"{context_label} difficulty band {band_id}"),
                summary=_required_text(raw, "summary", f"{context_label} difficulty band {band_id}"),
                minimum_level=minimum_level,
                maximum_level=maximum_level,
                enemy_offense_modifier=_integer(raw, "enemy_offense_modifier", f"{context_label} difficulty band {band_id}", default=0, minimum=-100, maximum=100),
                enemy_defense_modifier=_integer(raw, "enemy_defense_modifier", f"{context_label} difficulty band {band_id}", default=0, minimum=-100, maximum=100),
                enemy_armor_modifier=_integer(raw, "enemy_armor_modifier", f"{context_label} difficulty band {band_id}", default=0, minimum=-20, maximum=20),
                enemy_damage_min_modifier=_integer(raw, "enemy_damage_min_modifier", f"{context_label} difficulty band {band_id}", default=0, minimum=-20, maximum=20),
                enemy_damage_max_modifier=_integer(raw, "enemy_damage_max_modifier", f"{context_label} difficulty band {band_id}", default=0, minimum=-20, maximum=20),
                player_roundtime_modifier=_integer(raw, "player_roundtime_modifier", f"{context_label} difficulty band {band_id}", default=0, minimum=0, maximum=4),
            )
        )
    expected_levels = set(range(starting_level + 1, target_level + 1))
    if covered_levels != expected_levels:
        raise ContentError(
            f"{context_label} difficulty bands must cover every level exactly once; "
            f"missing={sorted(expected_levels - covered_levels)}"
        )

    raw_injury = raw_curve.get("injury")
    if not isinstance(raw_injury, dict):
        raise ContentError(f"{context_label} difficulty_curve.injury must be an object")
    injury_id = _identifier(raw_injury.get("id"), f"{context_label} injury id")
    trigger_level = _integer(
        raw_injury,
        "trigger_level",
        f"{context_label} injury {injury_id}",
        minimum=starting_level + 1,
        maximum=target_level,
    )
    clear_level = _integer(
        raw_injury,
        "clear_level",
        f"{context_label} injury {injury_id}",
        minimum=trigger_level + 1,
        maximum=target_level,
    )
    raw_severity = raw_injury.get("severity_by_level")
    if not isinstance(raw_severity, dict):
        raise ContentError(
            f"{context_label} injury {injury_id}.severity_by_level must be an object"
        )
    severity_by_level: dict[int, int] = {}
    for level in range(trigger_level, clear_level):
        raw_value = raw_severity.get(str(level))
        if type(raw_value) is not int or not 1 <= raw_value <= 5:
            raise ContentError(
                f"{context_label} injury {injury_id} requires severity 1-5 for level {level}"
            )
        severity_by_level[level] = raw_value
    recovery_item_id = _required_text(
        raw_injury,
        "recovery_item_id",
        f"{context_label} injury {injury_id}",
    )
    if recovery_item_id not in items:
        raise ContentError(
            f"{context_label} injury {injury_id} references unknown recovery item {recovery_item_id!r}"
        )
    injury = BeginnerInjuryDefinition(
        id=injury_id,
        label=_required_text(raw_injury, "label", f"{context_label} injury {injury_id}"),
        location=_required_text(raw_injury, "location", f"{context_label} injury {injury_id}"),
        summary=_required_text(raw_injury, "summary", f"{context_label} injury {injury_id}"),
        onset_text=_required_text(raw_injury, "onset_text", f"{context_label} injury {injury_id}"),
        recovery_text=_required_text(raw_injury, "recovery_text", f"{context_label} injury {injury_id}"),
        trigger_level=trigger_level,
        clear_level=clear_level,
        severity_by_level=severity_by_level,
        recovery_item_id=recovery_item_id,
        onset_health_percent=_integer(raw_injury, "onset_health_percent", f"{context_label} injury {injury_id}", minimum=20, maximum=100),
        checkpoint_health_percent=_integer(raw_injury, "checkpoint_health_percent", f"{context_label} injury {injury_id}", minimum=20, maximum=100),
        rehabilitation_health_percent=_integer(raw_injury, "rehabilitation_health_percent", f"{context_label} injury {injury_id}", minimum=20, maximum=100),
    )
    return BeginnerExperienceDefinition(
        id=phase_id,
        title=_required_text(document, "title", f"{context_label} {phase_id}"),
        summary=_required_text(document, "summary", f"{context_label} {phase_id}"),
        target_minutes=target_minutes,
        target_level=target_level,
        starter_room_ids=starter_room_ids,
        chapters=tuple(chapters),
        competencies=tuple(competencies),
        class_assignments=class_assignments,
        difficulty_curve=BeginnerDifficultyCurveDefinition(
            level_checkpoints=level_checkpoints,
            bands=tuple(sorted(bands, key=lambda band: band.minimum_level)),
            injury=injury,
        ),
    )

def load_catalog(content_root: Path) -> ContentCatalog:
    world_path = content_root / "world.json"
    items_path = content_root / "items.json"
    creatures_path = content_root / "creatures.json"
    training_path = content_root / "training.json"
    courses_path = content_root / "courses.json"
    classes_path = content_root / "classes.json"
    creation_path = content_root / "character_creation.json"
    economy_path = content_root / "economy.json"
    onboarding_path = content_root / "onboarding.json"
    journeyman_path = content_root / "journey_11_20.json"
    foundation_path = content_root / "foundation_activation.json"
    world_doc = _read_json(world_path, {3})
    items_doc = _read_json(items_path, {2})
    creatures_doc = _read_json(creatures_path, {1})
    training_doc = _read_json(training_path, {2})
    courses_doc = _read_json(courses_path, {1})
    classes_doc = _read_json(classes_path, {1})
    creation_doc = _read_json(creation_path, {1})
    economy_doc = _read_json(economy_path, {1})
    onboarding_doc = _read_json(onboarding_path, {1})
    journeyman_doc = _read_json(journeyman_path, {1})
    foundation_doc = _read_json(foundation_path, {1})

    raw_starter_points = training_doc.get("starter_points")
    raw_milestone_points = training_doc.get("milestone_points")
    expected_pools = {"physical", "mental"}
    for value, context in (
        (raw_starter_points, "starter_points"),
        (raw_milestone_points, "milestone_points"),
    ):
        if not isinstance(value, dict) or set(value) != expected_pools:
            raise ContentError(
                f"{training_path}.{context} must define physical and mental"
            )
        if any(type(points) is not int or points < 0 for points in value.values()):
            raise ContentError(
                f"{training_path}.{context} values must be non-negative integers"
            )
    assert isinstance(raw_starter_points, dict)
    assert isinstance(raw_milestone_points, dict)
    training_options: dict[str, TrainingOptionDefinition] = {}
    trainable_attributes = {
        "strength",
        "agility",
        "perception",
        "combat_skill",
    }
    for record in _unique_records(training_doc, "options", training_path):
        option_id = _required_text(record, "id", "training option")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", option_id):
            raise ContentError(
                f"training option {option_id!r} must be a lowercase identifier"
            )
        nouns = _string_tuple(
            record.get("nouns"), f"training option {option_id}.nouns"
        )
        if not nouns:
            raise ContentError(
                f"training option {option_id} requires at least one noun"
            )
        if len(nouns) != len(set(noun.casefold() for noun in nouns)):
            raise ContentError(
                f"training option {option_id}.nouns contains duplicates"
            )
        attribute = _required_text(
            record,
            "attribute",
            f"training option {option_id}",
        )
        if attribute not in trainable_attributes:
            raise ContentError(
                f"training option {option_id}.attribute is not trainable"
            )
        training_options[option_id] = TrainingOptionDefinition(
            id=option_id,
            name=_required_text(record, "name", f"training option {option_id}"),
            description=_required_text(
                record,
                "description",
                f"training option {option_id}",
            ),
            nouns=nouns,
            pool=_choice(
                record,
                "pool",
                f"training option {option_id}",
                expected_pools,
                default="physical",
            ),
            cost=_integer(
                record,
                "cost",
                f"training option {option_id}",
                minimum=1,
                maximum=100,
            ),
            max_rank=_integer(
                record,
                "max_rank",
                f"training option {option_id}",
                minimum=1,
                maximum=100,
            ),
            attribute=attribute,
            gain_per_rank=_integer(
                record,
                "gain_per_rank",
                f"training option {option_id}",
                minimum=1,
                maximum=100,
            ),
            source_features=_string_tuple(
                record.get("source_features"),
                f"training option {option_id}.source_features",
            ),
        )
    if not training_options:
        raise ContentError(f"{training_path}.options must not be empty")
    training_profiles: dict[str, TrainingProfileDefinition] = {}
    for record in _unique_records(training_doc, "profiles", training_path):
        profile_id = _required_text(record, "id", "training profile")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", profile_id):
            raise ContentError(
                f"training profile {profile_id!r} must be a lowercase identifier"
            )
        nouns = _string_tuple(
            record.get("nouns"), f"training profile {profile_id}.nouns"
        )
        if not nouns:
            raise ContentError(
                f"training profile {profile_id} requires at least one noun"
            )
        if len(nouns) != len(set(noun.casefold() for noun in nouns)):
            raise ContentError(
                f"training profile {profile_id}.nouns contains duplicates"
            )
        raw_modifiers = record.get("cost_modifiers")
        if not isinstance(raw_modifiers, dict) or not all(
            isinstance(option_id, str) and type(modifier) is int
            for option_id, modifier in raw_modifiers.items()
        ):
            raise ContentError(
                f"training profile {profile_id}.cost_modifiers must map "
                "option IDs to integers"
            )
        if set(raw_modifiers) != set(training_options):
            raise ContentError(
                f"training profile {profile_id}.cost_modifiers must define "
                "every training option exactly once"
            )
        modifiers = {
            option_id: int(raw_modifiers[option_id])
            for option_id in training_options
        }
        for option_id, modifier in modifiers.items():
            effective = training_options[option_id].cost + modifier
            if modifier < -99 or modifier > 99 or effective < 1 or effective > 100:
                raise ContentError(
                    f"training profile {profile_id} produces invalid cost "
                    f"{effective} for {option_id}"
                )
        training_profiles[profile_id] = TrainingProfileDefinition(
            id=profile_id,
            name=_required_text(
                record,
                "name",
                f"training profile {profile_id}",
            ),
            description=_required_text(
                record,
                "description",
                f"training profile {profile_id}",
            ),
            nouns=nouns,
            cost_modifiers=modifiers,
            source_features=_string_tuple(
                record.get("source_features"),
                f"training profile {profile_id}.source_features",
            ),
        )
    if not training_profiles:
        raise ContentError(f"{training_path}.profiles must not be empty")
    default_profile = _required_text(
        training_doc,
        "default_profile",
        str(training_path),
    )
    if default_profile not in training_profiles:
        raise ContentError(
            f"{training_path}.default_profile references unknown profile "
            f"{default_profile!r}"
        )
    progression = ProgressionDefinition(
        starter_points={
            pool: int(raw_starter_points[pool]) for pool in sorted(expected_pools)
        },
        milestone_points={
            pool: int(raw_milestone_points[pool])
            for pool in sorted(expected_pools)
        },
        early_refunds=_integer(
            training_doc,
            "early_refunds",
            str(training_path),
            minimum=0,
            maximum=100,
        ),
        early_refund_level_limit=_integer(
            training_doc,
            "early_refund_level_limit",
            str(training_path),
            minimum=1,
            maximum=100,
        ),
        default_profile=default_profile,
        options=training_options,
        profiles=training_profiles,
    )

    creation_budget = _integer(
        creation_doc,
        "budget",
        str(creation_path),
        minimum=1,
        maximum=1_000,
    )
    creation_attributes: dict[str, CreationAttributeDefinition] = {}
    expected_attributes = {
        "strength",
        "agility",
        "perception",
        "combat_skill",
    }
    for record in _unique_records(creation_doc, "attributes", creation_path):
        attribute_id = _required_text(record, "id", "creation attribute")
        if attribute_id not in expected_attributes:
            raise ContentError(
                f"creation attribute {attribute_id!r} is not trainable"
            )
        minimum = _integer(
            record,
            "minimum",
            f"creation attribute {attribute_id}",
            minimum=0,
            maximum=100,
        )
        maximum = _integer(
            record,
            "maximum",
            f"creation attribute {attribute_id}",
            minimum=minimum,
            maximum=100,
        )
        effects = _string_tuple(
            record.get("effects"),
            f"creation attribute {attribute_id}.effects",
        )
        if not effects:
            raise ContentError(
                f"creation attribute {attribute_id} requires at least one effect"
            )
        creation_attributes[attribute_id] = CreationAttributeDefinition(
            id=attribute_id,
            name=_required_text(
                record,
                "name",
                f"creation attribute {attribute_id}",
            ),
            abbreviation=_required_text(
                record,
                "abbreviation",
                f"creation attribute {attribute_id}",
            ),
            minimum=minimum,
            maximum=maximum,
            weight=_integer(
                record,
                "weight",
                f"creation attribute {attribute_id}",
                minimum=1,
                maximum=100,
            ),
            summary=_required_text(
                record,
                "summary",
                f"creation attribute {attribute_id}",
            ),
            effects=effects,
        )
    if set(creation_attributes) != expected_attributes:
        missing = ", ".join(sorted(expected_attributes - set(creation_attributes)))
        raise ContentError(
            f"{creation_path}.attributes must define every trainable attribute; "
            f"missing: {missing or 'none'}"
        )

    creation_packages: dict[str, CreationPackageDefinition] = {}
    for record in _unique_records(creation_doc, "packages", creation_path):
        package_id = _required_text(record, "id", "creation package")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", package_id):
            raise ContentError(
                f"creation package {package_id!r} must be a lowercase identifier"
            )
        raw_attributes = record.get("attributes")
        if (
            not isinstance(raw_attributes, dict)
            or set(raw_attributes) != expected_attributes
            or any(type(value) is not int for value in raw_attributes.values())
        ):
            raise ContentError(
                f"creation package {package_id}.attributes must define every "
                "trainable attribute with integer values"
            )
        package_attributes = {
            attribute_id: int(raw_attributes[attribute_id])
            for attribute_id in sorted(expected_attributes)
        }
        spent = 0
        for attribute_id, value in package_attributes.items():
            definition = creation_attributes[attribute_id]
            if value < definition.minimum or value > definition.maximum:
                raise ContentError(
                    f"creation package {package_id}.{attribute_id} must be "
                    f"between {definition.minimum} and {definition.maximum}"
                )
            spent += (value - definition.minimum) * definition.weight
        if spent != creation_budget:
            raise ContentError(
                f"creation package {package_id} spends {spent}; "
                f"expected exactly {creation_budget}"
            )
        creation_packages[package_id] = CreationPackageDefinition(
            id=package_id,
            name=_required_text(
                record,
                "name",
                f"creation package {package_id}",
            ),
            summary=_required_text(
                record,
                "summary",
                f"creation package {package_id}",
            ),
            attributes=package_attributes,
        )
    if not creation_packages:
        raise ContentError(f"{creation_path}.packages must not be empty")

    raw_tutorial = creation_doc.get("tutorial")
    if not isinstance(raw_tutorial, dict):
        raise ContentError(f"{creation_path}.tutorial must be an object")
    tutorial_id = _required_text(raw_tutorial, "id", "creation tutorial")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", tutorial_id):
        raise ContentError(
            f"creation tutorial {tutorial_id!r} must be a lowercase identifier"
        )
    raw_tutorial_steps = raw_tutorial.get("steps")
    if (
        not isinstance(raw_tutorial_steps, list)
        or not 1 <= len(raw_tutorial_steps) <= 20
    ):
        raise ContentError(
            f"{creation_path}.tutorial.steps must contain between 1 and 20 steps"
        )
    tutorial_steps: list[TutorialStepDefinition] = []
    tutorial_step_ids: set[str] = set()
    for index, raw_step in enumerate(raw_tutorial_steps):
        context = f"creation tutorial.steps[{index}]"
        if not isinstance(raw_step, dict):
            raise ContentError(f"{context} must be an object")
        step_id = _required_text(raw_step, "id", context)
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", step_id)
            or step_id in tutorial_step_ids
        ):
            raise ContentError(
                f"{context}.id must be a unique lowercase identifier"
            )
        tutorial_step_ids.add(step_id)
        event_kind = _required_text(raw_step, "event_kind", context)
        if not re.fullmatch(
            r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+",
            event_kind,
        ):
            raise ContentError(f"{context}.event_kind must be a dotted identifier")
        suggested_command = _required_text(
            raw_step,
            "suggested_command",
            context,
        ).casefold()
        raw_filters = raw_step.get("event_filters", {})
        if not isinstance(raw_filters, dict) or not all(
            isinstance(key, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is not None
            and type(value) in {str, int, bool}
            and not (type(value) is str and not value)
            for key, value in raw_filters.items()
        ):
            raise ContentError(
                f"{context}.event_filters must map lowercase identifiers "
                "to non-empty scalar values"
            )
        tutorial_steps.append(
            TutorialStepDefinition(
                id=step_id,
                description=_required_text(raw_step, "description", context),
                why=_required_text(raw_step, "why", context),
                suggested_command=suggested_command,
                event_kind=event_kind,
                event_filters=dict(raw_filters),
            )
        )
    tutorial = TutorialDefinition(
        id=tutorial_id,
        title=_required_text(raw_tutorial, "title", "creation tutorial"),
        description=_required_text(
            raw_tutorial,
            "description",
            "creation tutorial",
        ),
        steps=tuple(tutorial_steps),
    )

    factions: dict[str, FactionRouteDefinition] = {}
    for record in _unique_records(classes_doc, "factions", classes_path):
        faction_id = _required_text(record, "id", "faction route")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", faction_id):
            raise ContentError(
                f"faction route {faction_id!r} must be a lowercase identifier"
            )
        factions[faction_id] = FactionRouteDefinition(
            id=faction_id,
            name=_required_text(record, "name", f"faction route {faction_id}"),
            route_label=_required_text(
                record,
                "route_label",
                f"faction route {faction_id}",
            ),
            hq_label=_required_text(
                record,
                "hq_label",
                f"faction route {faction_id}",
            ),
            source_features=_string_tuple(
                record.get("source_features"),
                f"faction route {faction_id}.source_features",
            ),
        )
    if len(factions) != 7:
        raise ContentError(f"{classes_path}.factions must define exactly 7 routes")

    character_classes: dict[str, CharacterClassDefinition] = {}
    for record in _unique_records(classes_doc, "classes", classes_path):
        class_id = _required_text(record, "id", "character class")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", class_id):
            raise ContentError(
                f"character class {class_id!r} must be a lowercase identifier"
            )
        faction_id = _required_text(
            record,
            "faction_id",
            f"character class {class_id}",
        )
        if faction_id not in factions:
            raise ContentError(
                f"character class {class_id} references unknown faction "
                f"{faction_id!r}"
            )
        profile_id = _required_text(
            record,
            "training_profile_id",
            f"character class {class_id}",
        )
        if profile_id not in training_profiles:
            raise ContentError(
                f"character class {class_id} references unknown training "
                f"profile {profile_id!r}"
            )
        package_id = _required_text(
            record,
            "recommended_package_id",
            f"character class {class_id}",
        )
        if package_id not in creation_packages:
            raise ContentError(
                f"character class {class_id} references unknown creation "
                f"package {package_id!r}"
            )
        raw_branches = record.get("ability_branches")
        if not isinstance(raw_branches, list) or len(raw_branches) != 2:
            raise ContentError(
                f"character class {class_id}.ability_branches must define exactly two options"
            )
        ability_branches: dict[str, AbilityBranchDefinition] = {}
        for branch_index, branch in enumerate(raw_branches):
            if not isinstance(branch, dict):
                raise ContentError(
                    f"character class {class_id}.ability_branches[{branch_index}] must be an object"
                )
            branch_id = _identifier(
                branch.get("id"),
                f"character class {class_id}.ability_branches[{branch_index}].id",
            )
            if branch_id in ability_branches:
                raise ContentError(
                    f"character class {class_id} repeats ability branch {branch_id!r}"
                )
            branch_nouns = _string_tuple(
                branch.get("nouns"),
                f"character class {class_id}.ability branch {branch_id}.nouns",
            )
            if not branch_nouns:
                raise ContentError(
                    f"character class {class_id} ability branch {branch_id} requires nouns"
                )
            branch_context = f"ability branch {class_id}.{branch_id}"
            raw_passive = branch.get("passive")
            if not isinstance(raw_passive, dict):
                raise ContentError(f"{branch_context}.passive must be an object")
            passive = AbilityPassiveDefinition(
                name=_required_text(raw_passive, "name", f"{branch_context}.passive"),
                summary=_required_text(raw_passive, "summary", f"{branch_context}.passive"),
                kind=_choice(
                    raw_passive,
                    "kind",
                    f"{branch_context}.passive",
                    {"power", "tempo", "guard", "recovery"},
                    default="power",
                ),
                power=_integer(
                    raw_passive,
                    "power",
                    f"{branch_context}.passive",
                    minimum=1,
                    maximum=10,
                ),
            )
            raw_follow_up = branch.get("follow_up")
            if not isinstance(raw_follow_up, dict):
                raise ContentError(f"{branch_context}.follow_up must be an object")
            follow_up = AbilityFollowUpDefinition(
                name=_required_text(raw_follow_up, "name", f"{branch_context}.follow_up"),
                summary=_required_text(raw_follow_up, "summary", f"{branch_context}.follow_up"),
                kind=_choice(
                    raw_follow_up,
                    "kind",
                    f"{branch_context}.follow_up",
                    {"attack", "precision", "guard", "heal", "control", "report", "repair", "support"},
                    default="support",
                ),
                power=_integer(raw_follow_up, "power", f"{branch_context}.follow_up", minimum=1, maximum=50),
                window_seconds=_integer(raw_follow_up, "window_seconds", f"{branch_context}.follow_up", minimum=3, maximum=60),
                roundtime=_integer(raw_follow_up, "roundtime", f"{branch_context}.follow_up", minimum=1, maximum=10),
            )
            raw_upgrades = branch.get("upgrade_options")
            if not isinstance(raw_upgrades, list) or len(raw_upgrades) != 2:
                raise ContentError(f"{branch_context}.upgrade_options must define exactly two options")
            upgrades: dict[str, AbilityUpgradeDefinition] = {}
            for upgrade_index, raw_upgrade in enumerate(raw_upgrades):
                if not isinstance(raw_upgrade, dict):
                    raise ContentError(f"{branch_context}.upgrade_options[{upgrade_index}] must be an object")
                upgrade_id = _identifier(raw_upgrade.get("id"), f"{branch_context}.upgrade_options[{upgrade_index}].id")
                if upgrade_id in upgrades:
                    raise ContentError(f"{branch_context} repeats upgrade option {upgrade_id!r}")
                upgrades[upgrade_id] = AbilityUpgradeDefinition(
                    id=upgrade_id,
                    name=_required_text(raw_upgrade, "name", f"{branch_context}.upgrade {upgrade_id}"),
                    summary=_required_text(raw_upgrade, "summary", f"{branch_context}.upgrade {upgrade_id}"),
                    power_bonus=_integer(raw_upgrade, "power_bonus", f"{branch_context}.upgrade {upgrade_id}", minimum=0, maximum=20),
                    follow_up_power_bonus=_integer(raw_upgrade, "follow_up_power_bonus", f"{branch_context}.upgrade {upgrade_id}", minimum=0, maximum=20),
                    cooldown_delta=_integer(raw_upgrade, "cooldown_delta", f"{branch_context}.upgrade {upgrade_id}", minimum=-30, maximum=30),
                    commitment_roundtime_delta=_integer(raw_upgrade, "commitment_roundtime_delta", f"{branch_context}.upgrade {upgrade_id}", minimum=-5, maximum=5),
                    follow_up_window_bonus=_integer(raw_upgrade, "follow_up_window_bonus", f"{branch_context}.upgrade {upgrade_id}", minimum=0, maximum=30),
                )
            ability_branches[branch_id] = AbilityBranchDefinition(
                id=branch_id,
                name=_required_text(branch, "name", branch_context),
                summary=_required_text(branch, "summary", branch_context),
                kind=_choice(
                    branch,
                    "kind",
                    branch_context,
                    {"attack", "precision", "guard", "heal", "regenerate", "control", "report", "repair", "craft", "escape", "support"},
                    default="support",
                ),
                power=_integer(branch, "power", branch_context, minimum=1, maximum=50),
                cooldown=_integer(branch, "cooldown", branch_context, minimum=5, maximum=120),
                passive=passive,
                follow_up=follow_up,
                mastery_uses_required=_integer(branch, "mastery_uses_required", branch_context, minimum=1, maximum=100),
                upgrade_options=upgrades,
                commitment_roundtime=_integer(branch, "commitment_roundtime", branch_context, minimum=1, maximum=10),
                counterplay=_required_text(branch, "counterplay", branch_context),
                nouns=branch_nouns,
                source_features=_string_tuple(
                    branch.get("source_features"),
                    f"{branch_context}.source_features",
                ),
            )
        character_classes[class_id] = CharacterClassDefinition(
            id=class_id,
            name=_required_text(record, "name", f"character class {class_id}"),
            faction_id=faction_id,
            role=_required_text(record, "role", f"character class {class_id}"),
            difficulty=_choice(
                record,
                "difficulty",
                f"character class {class_id}",
                {"beginner", "moderate", "advanced"},
                default="moderate",
            ),
            summary=_required_text(
                record,
                "summary",
                f"character class {class_id}",
            ),
            tradeoff=_required_text(
                record,
                "tradeoff",
                f"character class {class_id}",
            ),
            training_profile_id=profile_id,
            recommended_package_id=package_id,
            technique_name=_required_text(
                record, "technique_name", f"character class {class_id}"
            ),
            technique_summary=_required_text(
                record, "technique_summary", f"character class {class_id}"
            ),
            technique_kind=_choice(
                record,
                "technique_kind",
                f"character class {class_id}",
                {
                    "regenerate", "guard", "power_attack", "field_heal",
                    "balanced_attack", "system_attack", "repair",
                    "escape", "precision_attack"
                },
                default="balanced_attack",
            ),
            passive_name=_required_text(
                record, "passive_name", f"character class {class_id}"
            ),
            passive_summary=_required_text(
                record, "passive_summary", f"character class {class_id}"
            ),
            exploration_name=_required_text(
                record, "exploration_name", f"character class {class_id}"
            ),
            exploration_summary=_required_text(
                record, "exploration_summary", f"character class {class_id}"
            ),
            ability_branches=ability_branches,
            source_features=_string_tuple(
                record.get("source_features"),
                f"character class {class_id}.source_features",
            ),
        )
    if len(character_classes) != 15:
        raise ContentError(f"{classes_path}.classes must define exactly 15 classes")
    if set(factions) != {
        definition.faction_id for definition in character_classes.values()
    }:
        raise ContentError("every faction route must be represented by a class")
    creation = CharacterCreationDefinition(
        budget=creation_budget,
        attributes=creation_attributes,
        packages=creation_packages,
        tutorial=tutorial,
        factions=factions,
        classes=character_classes,
    )

    courses: dict[str, CourseDefinition] = {}
    for record in _unique_records(courses_doc, "courses", courses_path):
        course_id = _required_text(record, "id", "course")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", course_id):
            raise ContentError(
                f"course {course_id!r} must be a lowercase identifier"
            )
        nouns = _string_tuple(record.get("nouns"), f"course {course_id}.nouns")
        if not nouns:
            raise ContentError(f"course {course_id} requires at least one noun")
        if len(nouns) != len(set(noun.casefold() for noun in nouns)):
            raise ContentError(f"course {course_id}.nouns contains duplicates")
        raw_rewards = record.get("reward_points")
        if not isinstance(raw_rewards, dict) or set(raw_rewards) != expected_pools:
            raise ContentError(
                f"course {course_id}.reward_points must define physical and mental"
            )
        if any(
            type(points) is not int or points < 0 or points > 100
            for points in raw_rewards.values()
        ):
            raise ContentError(
                f"course {course_id}.reward_points values must be integers "
                "between 0 and 100"
            )
        raw_steps = record.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 20:
            raise ContentError(
                f"course {course_id}.steps must contain between 1 and 20 steps"
            )
        steps: list[CourseStepDefinition] = []
        step_ids: set[str] = set()
        for index, raw_step in enumerate(raw_steps):
            context = f"course {course_id}.steps[{index}]"
            if not isinstance(raw_step, dict):
                raise ContentError(f"{context} must be an object")
            step_id = _required_text(raw_step, "id", context)
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", step_id):
                raise ContentError(
                    f"{context}.id must be a lowercase identifier"
                )
            if step_id in step_ids:
                raise ContentError(f"course {course_id} has duplicate step {step_id}")
            step_ids.add(step_id)
            event_kind = _required_text(raw_step, "event_kind", context)
            if not re.fullmatch(
                r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+",
                event_kind,
            ):
                raise ContentError(f"{context}.event_kind must be a dotted identifier")
            raw_filters = raw_step.get("event_filters", {})
            if not isinstance(raw_filters, dict) or not all(
                isinstance(key, str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is not None
                and type(value) in {str, int, bool}
                and not (type(value) is str and not value)
                and not (
                    type(value) is int
                    and (
                        value < -_MAX_CONTENT_INTEGER
                        or value > _MAX_CONTENT_INTEGER
                    )
                )
                for key, value in raw_filters.items()
            ):
                raise ContentError(
                    f"{context}.event_filters must map lowercase identifier "
                    "keys to bounded, non-empty scalar values"
                )
            steps.append(
                CourseStepDefinition(
                    id=step_id,
                    description=_required_text(raw_step, "description", context),
                    event_kind=event_kind,
                    event_filters=dict(raw_filters),
                )
            )
        courses[course_id] = CourseDefinition(
            id=course_id,
            name=_required_text(record, "name", f"course {course_id}"),
            description=_required_text(
                record,
                "description",
                f"course {course_id}",
            ),
            nouns=nouns,
            start_room=_required_text(record, "start_room", f"course {course_id}"),
            facility=_required_text(record, "facility", f"course {course_id}"),
            reward_points={
                pool: int(raw_rewards[pool]) for pool in sorted(expected_pools)
            },
            steps=tuple(steps),
            source_features=_string_tuple(
                record.get("source_features"),
                f"course {course_id}.source_features",
            ),
        )
    if not courses:
        raise ContentError(f"{courses_path}.courses must not be empty")

    items: dict[str, ItemDefinition] = {}
    for record in _unique_records(items_doc, "items", items_path):
        item_id = _required_text(record, "id", "item")
        nouns = _string_tuple(record.get("nouns"), f"item {item_id}.nouns")
        if not nouns:
            raise ContentError(f"item {item_id} requires at least one noun")
        _require_unique_nouns(nouns, f"item {item_id}.nouns")
        damage_min, damage_max = _damage_range(
            record.get("damage", [1, 2]), f"item {item_id}.damage"
        )
        slot = _optional_text(record.get("slot"), f"item {item_id}.slot")
        items[item_id] = ItemDefinition(
            id=item_id,
            name=_required_text(record, "name", f"item {item_id}"),
            description=_required_text(record, "description", f"item {item_id}"),
            nouns=nouns,
            slot=slot,
            attack_bonus=_integer(
                record, "attack_bonus", f"item {item_id}", default=0
            ),
            defense_bonus=_integer(
                record, "defense_bonus", f"item {item_id}", default=0
            ),
            damage_min=damage_min,
            damage_max=damage_max,
            roundtime=_integer(
                record, "roundtime", f"item {item_id}", default=3, minimum=1
            ),
            armor=_integer(
                record, "armor", f"item {item_id}", default=0, minimum=0
            ),
            weapon_profile=_choice(
                record,
                "weapon_profile",
                f"item {item_id}",
                {"unarmed", "light", "balanced", "heavy", "ranged"},
                default="unarmed" if slot != "main_hand" else "balanced",
            ),
            armor_profile=_choice(
                record,
                "armor_profile",
                f"item {item_id}",
                {"none", "light", "heavy"},
                default="none" if slot != "body" else "light",
            ),
            bulk=_integer(
                record, "bulk", f"item {item_id}", default=1, minimum=0, maximum=20
            ),
            max_durability=_integer(
                record,
                "durability",
                f"item {item_id}",
                default=0,
                minimum=0,
                maximum=10_000,
            ),
            repair_family=_optional_text(
                record.get("repair_family"),
                f"item {item_id}.repair_family",
            ),
            repair_value=_integer(
                record,
                "repair_value",
                f"item {item_id}",
                default=0,
                minimum=0,
                maximum=10_000,
            ),
            base_value=_integer(
                record, "base_value", f"item {item_id}", default=0, minimum=0, maximum=1_000_000
            ),
            tradeable=_boolean(record, "tradeable", f"item {item_id}"),
            salvage_yields=(
                {str(material_id): int(count) for material_id, count in record.get("salvage_yields", {}).items()}
                if isinstance(record.get("salvage_yields", {}), dict)
                and all(isinstance(material_id, str) and material_id.strip() and type(count) is int and count > 0
                        for material_id, count in record.get("salvage_yields", {}).items())
                else (_ for _ in ()).throw(
                    ContentError(f"item {item_id}.salvage_yields must map item ids to positive integers")
                )
            ),
            source_features=_string_tuple(
                record.get("source_features"), f"item {item_id}.source_features"
            ),
        )
        definition = items[item_id]
        if definition.max_durability > 0 and definition.repair_family is None:
            raise ContentError(
                f"durable item {item_id} requires a repair_family"
            )
        if definition.repair_value > 0 and definition.repair_family is None:
            raise ContentError(
                f"repair material {item_id} requires a repair_family"
            )
        if definition.max_durability > 0 and definition.repair_value > 0:
            raise ContentError(
                f"item {item_id} cannot be durable equipment and repair material"
            )
        if (
            definition.repair_family is not None
            and definition.max_durability == 0
            and definition.repair_value == 0
        ):
            raise ContentError(
                f"item {item_id} has an unused repair_family"
            )

    creatures: dict[str, CreatureDefinition] = {}
    for record in _unique_records(creatures_doc, "creatures", creatures_path):
        creature_id = _required_text(record, "id", "creature")
        nouns = _string_tuple(record.get("nouns"), f"creature {creature_id}.nouns")
        if not nouns:
            raise ContentError(f"creature {creature_id} requires at least one noun")
        _require_unique_nouns(nouns, f"creature {creature_id}.nouns")
        damage_min, damage_max = _damage_range(
            record.get("damage"), f"creature {creature_id}.damage"
        )
        creatures[creature_id] = CreatureDefinition(
            id=creature_id,
            name=_required_text(record, "name", f"creature {creature_id}"),
            description=_required_text(record, "description", f"creature {creature_id}"),
            nouns=nouns,
            level=_integer(record, "level", f"creature {creature_id}", minimum=1),
            max_health=_integer(
                record, "max_health", f"creature {creature_id}", minimum=1
            ),
            offense=_integer(
                record, "offense", f"creature {creature_id}", minimum=0
            ),
            defense=_integer(
                record, "defense", f"creature {creature_id}", minimum=0
            ),
            armor=_integer(
                record, "armor", f"creature {creature_id}", default=0, minimum=0
            ),
            armor_profile=_choice(
                record,
                "armor_profile",
                f"creature {creature_id}",
                {"none", "light", "heavy"},
                default="none",
            ),
            attack_profile=_choice(
                record,
                "attack_profile",
                f"creature {creature_id}",
                {"light", "balanced", "heavy", "ranged"},
                default="balanced",
            ),
            damage_min=damage_min,
            damage_max=damage_max,
            xp_reward=_integer(
                record, "xp_reward", f"creature {creature_id}", minimum=0
            ),
            loot=_string_tuple(record.get("loot"), f"creature {creature_id}.loot"),
            nonlethal=_boolean(
                record,
                "nonlethal",
                f"creature {creature_id}",
            ),
            combat_role=_choice(
                record,
                "combat_role",
                f"creature {creature_id}",
                {"skirmisher", "bruiser", "ranged", "support", "controller", "boss"},
                default="skirmisher",
            ),
            support_power=_integer(
                record,
                "support_power",
                f"creature {creature_id}",
                default=0,
                minimum=0,
                maximum=100,
            ),
            behavior_profile=_choice(
                record,
                "behavior_profile",
                f"creature {creature_id}",
                {
                    "aggressor",
                    "defender",
                    "skirmisher",
                    "controller",
                    "support",
                    "hunter",
                    "commander",
                },
                default="skirmisher",
            ),
            action_interval=_integer(
                record,
                "action_interval",
                f"creature {creature_id}",
                default=4,
                minimum=2,
                maximum=9,
            ),
            credit_reward=_integer(
                record, "credit_reward", f"creature {creature_id}", default=0, minimum=0, maximum=1_000_000
            ),
            source_features=_string_tuple(
                record.get("source_features"),
                f"creature {creature_id}.source_features",
            ),
        )

    rooms: dict[str, RoomDefinition] = {}
    item_spawn_ids: set[str] = set()
    creature_spawn_ids: set[str] = set()
    reveal_ids: set[str] = set()
    for record in _unique_records(world_doc, "rooms", world_path):
        room_id = _required_text(record, "id", "room")
        exits = record.get("exits", {})
        details = record.get("details", {})
        if not isinstance(exits, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in exits.items()
        ):
            raise ContentError(f"room {room_id}.exits must map direction to room id")
        if not isinstance(details, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in details.items()
        ):
            raise ContentError(f"room {room_id}.details must map noun to text")
        facilities = _string_tuple(
            record.get("facilities"), f"room {room_id}.facilities"
        )
        if len(facilities) != len(set(facilities)):
            raise ContentError(
                f"room {room_id}.facilities contains duplicate values"
            )
        if any(
            not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", facility)
            for facility in facilities
        ):
            raise ContentError(
                f"room {room_id}.facilities must contain lowercase facility IDs"
            )
        reveal = None
        if record.get("search") is not None:
            raw_reveal = record["search"]
            if not isinstance(raw_reveal, dict):
                raise ContentError(f"room {room_id}.search must be an object")
            reveal_id = _required_text(raw_reveal, "id", f"room {room_id}.search")
            if reveal_id in reveal_ids:
                raise ContentError(f"duplicate search reveal id: {reveal_id}")
            reveal_ids.add(reveal_id)
            reveal = SearchReveal(
                id=reveal_id,
                text=_required_text(raw_reveal, "text", f"room {room_id}.search"),
                item_id=_optional_text(
                    raw_reveal.get("item_id"), f"room {room_id}.search.item_id"
                ),
                flag=_optional_text(
                    raw_reveal.get("flag"), f"room {room_id}.search.flag"
                ),
            )
        raw_items = record.get("items", [])
        if not isinstance(raw_items, list):
            raise ContentError(f"room {room_id}.items must be a list")
        item_spawns: list[ItemSpawnDefinition] = []
        for index, raw_spawn in enumerate(raw_items):
            if not isinstance(raw_spawn, dict):
                raise ContentError(f"room {room_id}.items[{index}] must be an object")
            spawn_id = _required_text(
                raw_spawn, "id", f"room {room_id}.items[{index}]"
            )
            item_id = _required_text(
                raw_spawn, "item_id", f"room {room_id}.items[{index}]"
            )
            if spawn_id in item_spawn_ids:
                raise ContentError(f"duplicate authored item spawn id: {spawn_id}")
            item_spawn_ids.add(spawn_id)
            item_spawns.append(ItemSpawnDefinition(spawn_id, item_id))
        raw_creatures = record.get("creatures", [])
        if not isinstance(raw_creatures, list):
            raise ContentError(f"room {room_id}.creatures must be a list")
        creature_spawns: list[CreatureSpawnDefinition] = []
        for index, raw_spawn in enumerate(raw_creatures):
            if not isinstance(raw_spawn, dict):
                raise ContentError(
                    f"room {room_id}.creatures[{index}] must be an object"
                )
            spawn_id = _required_text(
                raw_spawn, "id", f"room {room_id}.creatures[{index}]"
            )
            creature_id = _required_text(
                raw_spawn,
                "creature_id",
                f"room {room_id}.creatures[{index}]",
            )
            if spawn_id in creature_spawn_ids:
                raise ContentError(f"duplicate authored creature spawn id: {spawn_id}")
            creature_spawn_ids.add(spawn_id)
            creature_spawns.append(CreatureSpawnDefinition(spawn_id, creature_id))
        raw_exit_requirements = record.get("exit_requirements", {})
        if not isinstance(raw_exit_requirements, dict) or not all(
            isinstance(direction, str)
            and direction.lower() in {str(key).lower() for key in exits}
            and isinstance(flags, list)
            and all(isinstance(flag, str) and flag.strip() for flag in flags)
            for direction, flags in raw_exit_requirements.items()
        ):
            raise ContentError(
                f"room {room_id}.exit_requirements must map authored exits to flag lists"
            )
        rooms[room_id] = RoomDefinition(
            id=room_id,
            title=_required_text(record, "title", f"room {room_id}"),
            description=_required_text(record, "description", f"room {room_id}"),
            exits={str(key).lower(): str(value) for key, value in exits.items()},
            items=tuple(item_spawns),
            creatures=tuple(creature_spawns),
            details={str(key).lower(): str(value) for key, value in details.items()},
            search=reveal,
            facilities=facilities,
            layer=str(record.get("layer", "foundation")),
            world_body=_world_body(record.get("world_body"), f"room {room_id}.world_body"),
            source_features=_string_tuple(
                record.get("source_features"), f"room {room_id}.source_features"
            ),
            exit_requirements={
                str(direction).lower(): tuple(str(flag).strip() for flag in flags)
                for direction, flags in raw_exit_requirements.items()
            },
            hazard_name=_optional_text(record.get("hazard_name"), f"room {room_id}.hazard_name"),
            hazard_text=str(record.get("hazard_text", "")).strip(),
            hazard_damage=_integer(record, "hazard_damage", f"room {room_id}", default=0, minimum=0, maximum=1000),
            hazard_roundtime=_integer(record, "hazard_roundtime", f"room {room_id}", default=0, minimum=0, maximum=120),
            hazard_mitigation_items=_string_tuple(record.get("hazard_mitigation_items"), f"room {room_id}.hazard_mitigation_items"),
            hazard_mitigation_classes=_string_tuple(record.get("hazard_mitigation_classes"), f"room {room_id}.hazard_mitigation_classes"),
            story_overlays=(
                {
                    str(key): str(value)
                    for key, value in record.get("story_overlays", {}).items()
                }
                if isinstance(record.get("story_overlays", {}), dict)
                and all(
                    isinstance(key, str)
                    and key.strip()
                    and isinstance(value, str)
                    and value.strip()
                    for key, value in record.get("story_overlays", {}).items()
                )
                else (_ for _ in ()).throw(
                    ContentError(
                        f"room {room_id}.story_overlays must map flags to text"
                    )
                )
            ),
        )

    start_room = _required_text(world_doc, "start_room", str(world_path))
    if start_room not in rooms:
        raise ContentError(f"start_room {start_room!r} does not exist")
    for room in rooms.values():
        for direction, destination in room.exits.items():
            if destination not in rooms:
                raise ContentError(
                    f"room {room.id} exit {direction!r} references {destination!r}"
                )
        for spawn in room.items:
            if spawn.item_id not in items:
                raise ContentError(
                    f"room {room.id} references unknown item {spawn.item_id}"
                )
        for spawn in room.creatures:
            if spawn.creature_id not in creatures:
                raise ContentError(
                    f"room {room.id} references unknown creature {spawn.creature_id}"
                )
        if room.search and room.search.item_id and room.search.item_id not in items:
            raise ContentError(
                f"room {room.id} search references unknown item {room.search.item_id}"
            )
    for item in items.values():
        for material_id in item.salvage_yields:
            if material_id not in items:
                raise ContentError(f"item {item.id} salvage references unknown item {material_id}")
        if item.tradeable and item.base_value <= 0:
            raise ContentError(f"tradeable item {item.id} requires a positive base_value")
    for room in rooms.values():
        for item_id in room.hazard_mitigation_items:
            if item_id not in items:
                raise ContentError(f"room {room.id} hazard references unknown mitigation item {item_id}")
        for class_id in room.hazard_mitigation_classes:
            if class_id not in character_classes:
                raise ContentError(f"room {room.id} hazard references unknown class {class_id}")
        if room.hazard_name is None and (room.hazard_damage or room.hazard_roundtime or room.hazard_text):
            raise ContentError(f"room {room.id} hazard effects require hazard_name")
    for creature in creatures.values():
        for item_id in creature.loot:
            if item_id not in items:
                raise ContentError(
                    f"creature {creature.id} references unknown loot {item_id}"
                )
    for course in courses.values():
        if course.start_room not in rooms:
            raise ContentError(
                f"course {course.id} references unknown start room "
                f"{course.start_room!r}"
            )
        if course.facility not in rooms[course.start_room].facilities:
            raise ContentError(
                f"course {course.id} requires facility {course.facility!r}, "
                f"which is absent from room {course.start_room!r}"
            )
    vendors: dict[str, VendorDefinition] = {}
    for record in _unique_records(economy_doc, "vendors", economy_path):
        vendor_id = _identifier(record.get("id"), "vendor id")
        room_id = _identifier(record.get("room_id"), f"vendor {vendor_id}.room_id")
        if room_id not in rooms:
            raise ContentError(f"vendor {vendor_id} references unknown room {room_id!r}")
        raw_inventory = record.get("inventory")
        if not isinstance(raw_inventory, dict) or not raw_inventory:
            raise ContentError(f"vendor {vendor_id}.inventory must be a non-empty object")
        inventory: dict[str, int] = {}
        for item_id, price in raw_inventory.items():
            if item_id not in items or type(price) is not int or price <= 0:
                raise ContentError(f"vendor {vendor_id} has invalid stock {item_id!r}")
            if not items[item_id].tradeable:
                raise ContentError(f"vendor {vendor_id} stock {item_id!r} is not tradeable")
            inventory[item_id] = price
        vendors[vendor_id] = VendorDefinition(
            id=vendor_id,
            name=_required_text(record, "name", f"vendor {vendor_id}"),
            room_id=room_id,
            inventory=inventory,
            sell_rate_percent=_integer(record, "sell_rate_percent", f"vendor {vendor_id}", default=40, minimum=1, maximum=100),
        )

    recipes: dict[str, RecipeDefinition] = {}
    for record in _unique_records(economy_doc, "recipes", economy_path):
        recipe_id = _identifier(record.get("id"), "recipe id")
        raw_inputs = record.get("inputs")
        raw_outputs = record.get("outputs")
        if not isinstance(raw_inputs, dict) or not raw_inputs or not isinstance(raw_outputs, dict) or not raw_outputs:
            raise ContentError(f"recipe {recipe_id} requires non-empty inputs and outputs")
        inputs: dict[str, int] = {}
        outputs: dict[str, int] = {}
        for bucket, target in ((raw_inputs, inputs), (raw_outputs, outputs)):
            for item_id, count in bucket.items():
                if item_id not in items or type(count) is not int or count <= 0:
                    raise ContentError(f"recipe {recipe_id} references invalid item/count {item_id!r}")
                target[item_id] = count
        nouns = _string_tuple(record.get("nouns"), f"recipe {recipe_id}.nouns")
        if not nouns:
            raise ContentError(f"recipe {recipe_id} requires nouns")
        _require_unique_nouns(nouns, f"recipe {recipe_id}.nouns")
        recipes[recipe_id] = RecipeDefinition(
            id=recipe_id,
            name=_required_text(record, "name", f"recipe {recipe_id}"),
            nouns=nouns,
            facility=_identifier(record.get("facility"), f"recipe {recipe_id}.facility"),
            inputs=inputs,
            outputs=outputs,
            credit_cost=_integer(record, "credit_cost", f"recipe {recipe_id}", default=0, minimum=0, maximum=1_000_000),
            source_features=_string_tuple(record.get("source_features"), f"recipe {recipe_id}.source_features"),
        )
    authored_facilities = {facility for room in rooms.values() for facility in room.facilities}
    for recipe in recipes.values():
        if recipe.facility not in authored_facilities:
            raise ContentError(f"recipe {recipe.id} references unavailable facility {recipe.facility!r}")

    mercenaries: dict[str, MercenaryDefinition] = {}
    for record in _unique_records(economy_doc, "mercenaries", economy_path):
        mercenary_id = _identifier(record.get("id"), "mercenary id")
        hire_room_id = _identifier(record.get("hire_room_id"), f"mercenary {mercenary_id}.hire_room_id")
        if hire_room_id not in rooms:
            raise ContentError(f"mercenary {mercenary_id} references unknown hire room")
        mercenaries[mercenary_id] = MercenaryDefinition(
            id=mercenary_id,
            name=_required_text(record, "name", f"mercenary {mercenary_id}"),
            role=_required_text(record, "role", f"mercenary {mercenary_id}"),
            summary=_required_text(record, "summary", f"mercenary {mercenary_id}"),
            hire_room_id=hire_room_id,
            cost=_integer(record, "cost", f"mercenary {mercenary_id}", minimum=0, maximum=1_000_000),
            assist_kind=_choice(record, "assist_kind", f"mercenary {mercenary_id}", {"guard", "medic", "scout", "partner"}, default="guard"),
            power=_integer(record, "power", f"mercenary {mercenary_id}", minimum=1, maximum=50),
            story_bound=_boolean(record, "story_bound", f"mercenary {mercenary_id}"),
            dismissible=_boolean(record, "dismissible", f"mercenary {mercenary_id}", default=True),
            hidden_from_hire=_boolean(record, "hidden_from_hire", f"mercenary {mercenary_id}"),
            base_health=_integer(record, "base_health", f"mercenary {mercenary_id}", default=0, minimum=0, maximum=500),
            attack_power=_integer(record, "attack_power", f"mercenary {mercenary_id}", default=0, minimum=0, maximum=100),
            source_features=_string_tuple(record.get("source_features"), f"mercenary {mercenary_id}.source_features"),
        )
    economy = EconomyDefinition(vendors=vendors, recipes=recipes, mercenaries=mercenaries)

    story = _load_story(
        content_root,
        rooms=rooms,
        items=items,
        classes=character_classes,
    )

    onboarding_id = _identifier(onboarding_doc.get("id"), "onboarding id")
    target_minutes = _integer(
        onboarding_doc,
        "target_minutes",
        f"onboarding {onboarding_id}",
        minimum=30,
        maximum=600,
    )
    target_level = _integer(
        onboarding_doc,
        "target_level",
        f"onboarding {onboarding_id}",
        minimum=2,
        maximum=100,
    )
    starter_room_ids = _string_tuple(
        onboarding_doc.get("starter_room_ids"),
        f"onboarding {onboarding_id}.starter_room_ids",
    )
    if not starter_room_ids or len(starter_room_ids) != len(set(starter_room_ids)):
        raise ContentError("onboarding starter rooms must be unique and non-empty")
    unknown_starter_rooms = set(starter_room_ids) - set(rooms)
    if unknown_starter_rooms:
        raise ContentError(
            f"onboarding references unknown starter rooms {sorted(unknown_starter_rooms)}"
        )

    chapters: list[BeginnerChapterDefinition] = []
    chapter_ids: set[str] = set()
    chapter_minutes = 0
    raw_chapters = onboarding_doc.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise ContentError("onboarding requires at least one chapter")
    for raw in raw_chapters:
        if not isinstance(raw, dict):
            raise ContentError("onboarding chapters must be objects")
        chapter_id = _identifier(raw.get("id"), "onboarding chapter id")
        if chapter_id in chapter_ids:
            raise ContentError(f"duplicate onboarding chapter {chapter_id!r}")
        chapter_ids.add(chapter_id)
        quest_ids = _string_tuple(
            raw.get("quest_ids"), f"onboarding chapter {chapter_id}.quest_ids"
        )
        if not quest_ids:
            raise ContentError(f"onboarding chapter {chapter_id} requires quests")
        unknown_quests = set(quest_ids) - set(story.quests)
        if unknown_quests:
            raise ContentError(
                f"onboarding chapter {chapter_id} references unknown quests {sorted(unknown_quests)}"
            )
        minutes = _integer(
            raw,
            "minutes",
            f"onboarding chapter {chapter_id}",
            minimum=1,
            maximum=240,
        )
        chapter_minutes += minutes
        chapters.append(
            BeginnerChapterDefinition(
                id=chapter_id,
                title=_required_text(raw, "title", f"onboarding chapter {chapter_id}"),
                summary=_required_text(raw, "summary", f"onboarding chapter {chapter_id}"),
                minutes=minutes,
                quest_ids=quest_ids,
            )
        )
    if chapter_minutes != target_minutes:
        raise ContentError(
            f"onboarding chapter minutes total {chapter_minutes}, expected {target_minutes}"
        )

    competencies: list[BeginnerCompetencyDefinition] = []
    competency_ids: set[str] = set()
    raw_competencies = onboarding_doc.get("competencies")
    if not isinstance(raw_competencies, list) or not raw_competencies:
        raise ContentError("onboarding requires competencies")
    for raw in raw_competencies:
        if not isinstance(raw, dict):
            raise ContentError("onboarding competencies must be objects")
        competency_id = _identifier(raw.get("id"), "onboarding competency id")
        if competency_id in competency_ids:
            raise ContentError(f"duplicate onboarding competency {competency_id!r}")
        competency_ids.add(competency_id)
        required_quests = _string_tuple(
            raw.get("required_quests"),
            f"onboarding competency {competency_id}.required_quests",
        )
        required_flags = _string_tuple(
            raw.get("required_flags"),
            f"onboarding competency {competency_id}.required_flags",
        )
        if not required_quests and not required_flags:
            raise ContentError(
                f"onboarding competency {competency_id} requires durable evidence"
            )
        unknown_quests = set(required_quests) - set(story.quests)
        if unknown_quests:
            raise ContentError(
                f"onboarding competency {competency_id} references unknown quests {sorted(unknown_quests)}"
            )
        competencies.append(
            BeginnerCompetencyDefinition(
                id=competency_id,
                label=_required_text(raw, "label", f"onboarding competency {competency_id}"),
                description=_required_text(raw, "description", f"onboarding competency {competency_id}"),
                required_quests=required_quests,
                required_flags=required_flags,
            )
        )

    class_assignments: dict[str, BeginnerClassAssignmentDefinition] = {}
    raw_assignments = onboarding_doc.get("class_assignments")
    if not isinstance(raw_assignments, dict):
        raise ContentError("onboarding class_assignments must be an object")
    if set(raw_assignments) != set(character_classes):
        missing = sorted(set(character_classes) - set(raw_assignments))
        extra = sorted(set(raw_assignments) - set(character_classes))
        raise ContentError(
            f"onboarding class assignments must cover every class; missing={missing}, extra={extra}"
        )
    for class_id, raw in raw_assignments.items():
        if not isinstance(raw, dict):
            raise ContentError(f"onboarding assignment {class_id} must be an object")
        class_assignments[class_id] = BeginnerClassAssignmentDefinition(
            class_id=class_id,
            title=_required_text(raw, "title", f"onboarding assignment {class_id}"),
            objective=_required_text(raw, "objective", f"onboarding assignment {class_id}"),
            practice_command=_required_text(raw, "practice_command", f"onboarding assignment {class_id}"),
        )

    raw_curve = onboarding_doc.get("difficulty_curve")
    if not isinstance(raw_curve, dict):
        raise ContentError("onboarding difficulty_curve must be an object")
    raw_checkpoints = raw_curve.get("level_checkpoints")
    if not isinstance(raw_checkpoints, dict) or not raw_checkpoints:
        raise ContentError(
            "onboarding difficulty_curve.level_checkpoints must be a non-empty object"
        )
    level_checkpoints: dict[str, int] = {}
    last_level = 1
    for quest_id, raw_level in raw_checkpoints.items():
        if quest_id not in story.quests:
            raise ContentError(
                f"onboarding difficulty checkpoint references unknown quest {quest_id!r}"
            )
        if type(raw_level) is not int or not 2 <= raw_level <= target_level:
            raise ContentError(
                f"onboarding difficulty checkpoint {quest_id!r} has invalid level"
            )
        if raw_level <= last_level:
            raise ContentError(
                "onboarding difficulty checkpoint levels must increase in authored order"
            )
        last_level = raw_level
        level_checkpoints[quest_id] = raw_level
    if last_level != target_level:
        raise ContentError(
            "onboarding difficulty checkpoints must finish at the target level"
        )

    raw_bands = raw_curve.get("bands")
    if not isinstance(raw_bands, list) or not raw_bands:
        raise ContentError("onboarding difficulty_curve.bands must be a non-empty list")
    bands: list[BeginnerDifficultyBandDefinition] = []
    covered_levels: set[int] = set()
    band_ids: set[str] = set()
    for raw in raw_bands:
        if not isinstance(raw, dict):
            raise ContentError("onboarding difficulty bands must be objects")
        band_id = _identifier(raw.get("id"), "onboarding difficulty band id")
        if band_id in band_ids:
            raise ContentError(f"duplicate onboarding difficulty band {band_id!r}")
        band_ids.add(band_id)
        minimum_level = _integer(
            raw,
            "minimum_level",
            f"onboarding difficulty band {band_id}",
            minimum=1,
            maximum=target_level,
        )
        maximum_level = _integer(
            raw,
            "maximum_level",
            f"onboarding difficulty band {band_id}",
            minimum=minimum_level,
            maximum=target_level,
        )
        overlap = covered_levels & set(range(minimum_level, maximum_level + 1))
        if overlap:
            raise ContentError(
                f"onboarding difficulty band {band_id!r} overlaps levels {sorted(overlap)}"
            )
        covered_levels.update(range(minimum_level, maximum_level + 1))
        bands.append(
            BeginnerDifficultyBandDefinition(
                id=band_id,
                label=_required_text(raw, "label", f"onboarding difficulty band {band_id}"),
                summary=_required_text(raw, "summary", f"onboarding difficulty band {band_id}"),
                minimum_level=minimum_level,
                maximum_level=maximum_level,
                enemy_offense_modifier=_integer(
                    raw,
                    "enemy_offense_modifier",
                    f"onboarding difficulty band {band_id}",
                    default=0,
                    minimum=-100,
                    maximum=100,
                ),
                enemy_defense_modifier=_integer(
                    raw,
                    "enemy_defense_modifier",
                    f"onboarding difficulty band {band_id}",
                    default=0,
                    minimum=-100,
                    maximum=100,
                ),
                enemy_armor_modifier=_integer(
                    raw,
                    "enemy_armor_modifier",
                    f"onboarding difficulty band {band_id}",
                    default=0,
                    minimum=-20,
                    maximum=20,
                ),
                enemy_damage_min_modifier=_integer(
                    raw,
                    "enemy_damage_min_modifier",
                    f"onboarding difficulty band {band_id}",
                    default=0,
                    minimum=-20,
                    maximum=20,
                ),
                enemy_damage_max_modifier=_integer(
                    raw,
                    "enemy_damage_max_modifier",
                    f"onboarding difficulty band {band_id}",
                    default=0,
                    minimum=-20,
                    maximum=20,
                ),
                player_roundtime_modifier=_integer(
                    raw,
                    "player_roundtime_modifier",
                    f"onboarding difficulty band {band_id}",
                    default=0,
                    minimum=0,
                    maximum=4,
                ),
            )
        )
    expected_levels = set(range(1, target_level + 1))
    if covered_levels != expected_levels:
        raise ContentError(
            "onboarding difficulty bands must cover every level exactly once; "
            f"missing={sorted(expected_levels - covered_levels)}"
        )

    raw_injury = raw_curve.get("injury")
    if not isinstance(raw_injury, dict):
        raise ContentError("onboarding difficulty_curve.injury must be an object")
    injury_id = _identifier(raw_injury.get("id"), "onboarding injury id")
    trigger_level = _integer(
        raw_injury,
        "trigger_level",
        f"onboarding injury {injury_id}",
        minimum=2,
        maximum=target_level,
    )
    clear_level = _integer(
        raw_injury,
        "clear_level",
        f"onboarding injury {injury_id}",
        minimum=trigger_level + 1,
        maximum=target_level,
    )
    raw_severity = raw_injury.get("severity_by_level")
    if not isinstance(raw_severity, dict):
        raise ContentError(
            f"onboarding injury {injury_id}.severity_by_level must be an object"
        )
    severity_by_level: dict[int, int] = {}
    for level in range(trigger_level, clear_level):
        raw_value = raw_severity.get(str(level))
        if type(raw_value) is not int or not 1 <= raw_value <= 5:
            raise ContentError(
                f"onboarding injury {injury_id} requires severity 1-5 for level {level}"
            )
        severity_by_level[level] = raw_value
    recovery_item_id = _required_text(
        raw_injury,
        "recovery_item_id",
        f"onboarding injury {injury_id}",
    )
    if recovery_item_id not in items:
        raise ContentError(
            f"onboarding injury {injury_id} references unknown recovery item {recovery_item_id!r}"
        )
    injury = BeginnerInjuryDefinition(
        id=injury_id,
        label=_required_text(raw_injury, "label", f"onboarding injury {injury_id}"),
        location=_required_text(raw_injury, "location", f"onboarding injury {injury_id}"),
        summary=_required_text(raw_injury, "summary", f"onboarding injury {injury_id}"),
        onset_text=_required_text(raw_injury, "onset_text", f"onboarding injury {injury_id}"),
        recovery_text=_required_text(raw_injury, "recovery_text", f"onboarding injury {injury_id}"),
        trigger_level=trigger_level,
        clear_level=clear_level,
        severity_by_level=severity_by_level,
        recovery_item_id=recovery_item_id,
        onset_health_percent=_integer(
            raw_injury,
            "onset_health_percent",
            f"onboarding injury {injury_id}",
            minimum=20,
            maximum=100,
        ),
        checkpoint_health_percent=_integer(
            raw_injury,
            "checkpoint_health_percent",
            f"onboarding injury {injury_id}",
            minimum=20,
            maximum=100,
        ),
        rehabilitation_health_percent=_integer(
            raw_injury,
            "rehabilitation_health_percent",
            f"onboarding injury {injury_id}",
            minimum=20,
            maximum=100,
        ),
    )
    difficulty_curve = BeginnerDifficultyCurveDefinition(
        level_checkpoints=level_checkpoints,
        bands=tuple(sorted(bands, key=lambda band: band.minimum_level)),
        injury=injury,
    )
    beginner_experience = BeginnerExperienceDefinition(
        id=onboarding_id,
        title=_required_text(onboarding_doc, "title", f"onboarding {onboarding_id}"),
        summary=_required_text(onboarding_doc, "summary", f"onboarding {onboarding_id}"),
        target_minutes=target_minutes,
        target_level=target_level,
        starter_room_ids=starter_room_ids,
        chapters=tuple(chapters),
        competencies=tuple(competencies),
        class_assignments=class_assignments,
        difficulty_curve=difficulty_curve,
    )

    journeyman_experience = _parse_additional_experience(
        journeyman_doc,
        context_label="journeyman phase",
        rooms=rooms,
        items=items,
        story=story,
        character_classes=character_classes,
    )

    foundation_id = _required_text(foundation_doc, "id", "foundation activation")
    raw_seed = foundation_doc.get("territory_seed")
    if not isinstance(raw_seed, dict):
        raise ContentError(f"{foundation_path}.territory_seed must be an object")
    seed_id = _required_text(raw_seed, "id", "foundation territory seed")
    territory_seed = TerritorySeedDefinition(
        id=seed_id,
        title=_required_text(raw_seed, "title", f"foundation territory {seed_id}"),
        owner_id=_optional_text(raw_seed.get("owner_id"), f"foundation territory {seed_id}.owner_id"),
        level=_integer(raw_seed, "level", f"foundation territory {seed_id}", minimum=0, maximum=4),
        population=_integer(raw_seed, "population", f"foundation territory {seed_id}", minimum=0, maximum=1_000_000),
        supply=_integer(raw_seed, "supply", f"foundation territory {seed_id}", minimum=0, maximum=100),
        defense=_integer(raw_seed, "defense", f"foundation territory {seed_id}", minimum=0, maximum=100),
        prosperity=_integer(raw_seed, "prosperity", f"foundation territory {seed_id}", minimum=0, maximum=100),
        tension=_integer(raw_seed, "tension", f"foundation territory {seed_id}", minimum=0, maximum=100),
        visibility=_integer(raw_seed, "visibility", f"foundation territory {seed_id}", minimum=0, maximum=100),
        canon_status=_required_text(raw_seed, "canon_status", f"foundation territory {seed_id}"),
        source_authority=_required_text(raw_seed, "source_authority", f"foundation territory {seed_id}"),
        interpretation_note=_required_text(raw_seed, "interpretation_note", f"foundation territory {seed_id}"),
    )
    if territory_seed.canon_status != "artistic_interpretation":
        raise ContentError("foundation territory seed must remain artistic_interpretation")
    if territory_seed.source_authority != "combined_with_gameplay_interpretation":
        raise ContentError("foundation territory seed must declare combined gameplay authority")

    raw_bands = foundation_doc.get("standing_bands")
    if not isinstance(raw_bands, list) or not raw_bands:
        raise ContentError(f"{foundation_path}.standing_bands must be a non-empty list")
    standing_bands: list[StandingBandDefinition] = []
    previous_max: int | None = None
    for index, record in enumerate(raw_bands):
        if not isinstance(record, dict):
            raise ContentError(f"foundation standing band {index} must be an object")
        minimum = _integer(record, "minimum", f"foundation standing band {index}", minimum=-1000, maximum=1000)
        maximum = _integer(record, "maximum", f"foundation standing band {index}", minimum=-1000, maximum=1000)
        if minimum > maximum or (previous_max is not None and minimum != previous_max + 1):
            raise ContentError("foundation standing bands must be ordered and contiguous")
        standing_bands.append(StandingBandDefinition(minimum, maximum, _required_text(record, "label", f"foundation standing band {index}")))
        previous_max = maximum
    if standing_bands[0].minimum != -1000 or standing_bands[-1].maximum != 1000:
        raise ContentError("foundation standing bands must cover -1000 through 1000")

    def parse_foundation_impacts(key: str, *, faction: bool):
        raw = foundation_doc.get(key, [])
        if not isinstance(raw, list):
            raise ContentError(f"{foundation_path}.{key} must be a list")
        parsed = {}
        for index, record in enumerate(raw):
            if not isinstance(record, dict):
                raise ContentError(f"foundation {key} record {index} must be an object")
            record_id = _required_text(record, "record_id", f"foundation {key} record {index}")
            if record_id in parsed:
                raise ContentError(f"foundation {key} duplicates record {record_id}")
            if record_id not in story.records:
                raise ContentError(f"foundation {key} references unknown story record {record_id}")
            if faction:
                faction_id = _required_text(record, "faction_id", f"foundation faction impact {record_id}")
                if faction_id not in creation.factions:
                    raise ContentError(f"foundation faction impact {record_id} references unknown faction {faction_id}")
                parsed[record_id] = FoundationFactionImpactDefinition(
                    record_id=record_id,
                    faction_id=faction_id,
                    public_delta=_integer(record, "public_delta", f"foundation faction impact {record_id}", default=0, minimum=-1000, maximum=1000),
                    covert_delta=_integer(record, "covert_delta", f"foundation faction impact {record_id}", default=0, minimum=-1000, maximum=1000),
                    access_flags=_string_tuple(record.get("access_flags"), f"foundation faction impact {record_id}.access_flags"),
                )
            else:
                parsed[record_id] = FoundationTerritoryImpactDefinition(
                    record_id=record_id,
                    local_trust_delta=_integer(record, "local_trust_delta", f"foundation territory impact {record_id}", default=0, minimum=-1000, maximum=1000),
                    supply_delta=_integer(record, "supply_delta", f"foundation territory impact {record_id}", default=0, minimum=-100, maximum=100),
                    defense_delta=_integer(record, "defense_delta", f"foundation territory impact {record_id}", default=0, minimum=-100, maximum=100),
                    prosperity_delta=_integer(record, "prosperity_delta", f"foundation territory impact {record_id}", default=0, minimum=-100, maximum=100),
                    tension_delta=_integer(record, "tension_delta", f"foundation territory impact {record_id}", default=0, minimum=-100, maximum=100),
                    visibility_delta=_integer(record, "visibility_delta", f"foundation territory impact {record_id}", default=0, minimum=-100, maximum=100),
                    caravan_route_ids=_string_tuple(record.get("caravan_route_ids"), f"foundation territory impact {record_id}.caravan_route_ids"),
                    world_modifiers=_string_tuple(record.get("world_modifiers"), f"foundation territory impact {record_id}.world_modifiers"),
                )
        return parsed

    faction_impacts = parse_foundation_impacts("faction_record_impacts", faction=True)
    territory_impacts = parse_foundation_impacts("territory_record_impacts", faction=False)
    raw_actions = foundation_doc.get("maintenance_actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ContentError(f"{foundation_path}.maintenance_actions must be a non-empty list")
    maintenance_actions: dict[str, TerritoryMaintenanceDefinition] = {}
    for index, record in enumerate(raw_actions):
        if not isinstance(record, dict):
            raise ContentError(f"foundation maintenance action {index} must be an object")
        action_id = _required_text(record, "id", f"foundation maintenance action {index}")
        if action_id in maintenance_actions:
            raise ContentError(f"duplicate foundation maintenance action {action_id}")
        maintenance_actions[action_id] = TerritoryMaintenanceDefinition(
            id=action_id,
            name=_required_text(record, "name", f"foundation maintenance action {action_id}"),
            summary=_required_text(record, "summary", f"foundation maintenance action {action_id}"),
            roundtime=_integer(record, "roundtime", f"foundation maintenance action {action_id}", minimum=1, maximum=30),
            cooldown_turns=_integer(record, "cooldown_turns", f"foundation maintenance action {action_id}", minimum=1, maximum=1000),
            supply_delta=_integer(record, "supply_delta", f"foundation maintenance action {action_id}", default=0, minimum=-100, maximum=100),
            defense_delta=_integer(record, "defense_delta", f"foundation maintenance action {action_id}", default=0, minimum=-100, maximum=100),
            prosperity_delta=_integer(record, "prosperity_delta", f"foundation maintenance action {action_id}", default=0, minimum=-100, maximum=100),
            tension_delta=_integer(record, "tension_delta", f"foundation maintenance action {action_id}", default=0, minimum=-100, maximum=100),
            visibility_delta=_integer(record, "visibility_delta", f"foundation maintenance action {action_id}", default=0, minimum=-100, maximum=100),
        )
    raw_pledges = foundation_doc.get("pledge_routes")
    if not isinstance(raw_pledges, list) or not raw_pledges:
        raise ContentError(f"{foundation_path}.pledge_routes must be a non-empty list")
    pledge_routes: dict[str, FactionPledgeDefinition] = {}
    for index, record in enumerate(raw_pledges):
        if not isinstance(record, dict):
            raise ContentError(f"foundation pledge route {index} must be an object")
        faction_id = _required_text(record, "faction_id", f"foundation pledge route {index}")
        if faction_id in pledge_routes:
            raise ContentError(f"duplicate foundation pledge route {faction_id}")
        if faction_id not in creation.factions:
            raise ContentError(f"foundation pledge route references unknown faction {faction_id}")
        pledge_routes[faction_id] = FactionPledgeDefinition(
            faction_id=faction_id,
            recruit_title=_required_text(record, "recruit_title", f"foundation pledge route {faction_id}"),
            pledge_statement=_required_text(record, "pledge_statement", f"foundation pledge route {faction_id}"),
            minimum_public_standing=_integer(record, "minimum_public_standing", f"foundation pledge route {faction_id}", minimum=-1000, maximum=1000),
            required_access_flag=_required_text(record, "required_access_flag", f"foundation pledge route {faction_id}"),
            membership_flag=_required_text(record, "membership_flag", f"foundation pledge route {faction_id}"),
        )
    if set(pledge_routes) != set(creation.factions):
        raise ContentError("foundation pledge routes must cover all seven factions exactly once")

    raw_civic = foundation_doc.get("civic_mission")
    if not isinstance(raw_civic, dict):
        raise ContentError(f"{foundation_path}.civic_mission must be an object")
    civic_id = _required_text(raw_civic, "id", "foundation civic mission")
    raw_plans = raw_civic.get("plans")
    if not isinstance(raw_plans, list) or len(raw_plans) < 2:
        raise ContentError("foundation civic mission must define at least two plans")
    civic_plans: dict[str, CivicPlanDefinition] = {}
    for index, record in enumerate(raw_plans):
        if not isinstance(record, dict):
            raise ContentError(f"foundation civic plan {index} must be an object")
        plan_id = _required_text(record, "id", f"foundation civic plan {index}")
        if plan_id in civic_plans:
            raise ContentError(f"duplicate foundation civic plan {plan_id}")
        civic_plans[plan_id] = CivicPlanDefinition(
            id=plan_id,
            name=_required_text(record, "name", f"foundation civic plan {plan_id}"),
            summary=_required_text(record, "summary", f"foundation civic plan {plan_id}"),
            roundtime=_integer(record, "roundtime", f"foundation civic plan {plan_id}", minimum=1, maximum=30),
            field_insight=_integer(record, "field_insight", f"foundation civic plan {plan_id}", minimum=0, maximum=1000),
            local_trust_delta=_integer(record, "local_trust_delta", f"foundation civic plan {plan_id}", default=0, minimum=-100, maximum=100),
            allegiance_standing_delta=_integer(record, "allegiance_standing_delta", f"foundation civic plan {plan_id}", default=0, minimum=-100, maximum=100),
            supply_delta=_integer(record, "supply_delta", f"foundation civic plan {plan_id}", default=0, minimum=-100, maximum=100),
            defense_delta=_integer(record, "defense_delta", f"foundation civic plan {plan_id}", default=0, minimum=-100, maximum=100),
            prosperity_delta=_integer(record, "prosperity_delta", f"foundation civic plan {plan_id}", default=0, minimum=-100, maximum=100),
            tension_delta=_integer(record, "tension_delta", f"foundation civic plan {plan_id}", default=0, minimum=-100, maximum=100),
            visibility_delta=_integer(record, "visibility_delta", f"foundation civic plan {plan_id}", default=0, minimum=-100, maximum=100),
        )
    civic_mission = CivicMissionDefinition(
        id=civic_id,
        title=_required_text(raw_civic, "title", "foundation civic mission"),
        summary=_required_text(raw_civic, "summary", "foundation civic mission"),
        canon_status=_required_text(raw_civic, "canon_status", "foundation civic mission"),
        source_authority=_required_text(raw_civic, "source_authority", "foundation civic mission"),
        interpretation_note=_required_text(raw_civic, "interpretation_note", "foundation civic mission"),
        completion_modifier=_required_text(raw_civic, "completion_modifier", "foundation civic mission"),
        plans=civic_plans,
    )
    if civic_mission.canon_status != "artistic_interpretation":
        raise ContentError("foundation civic mission must remain artistic_interpretation")
    if civic_mission.source_authority != "combined_with_gameplay_interpretation":
        raise ContentError("foundation civic mission must declare combined gameplay authority")

    foundation_activation = FoundationActivationDefinition(
        id=foundation_id,
        title=_required_text(foundation_doc, "title", "foundation activation"),
        territory_seed=territory_seed,
        standing_bands=tuple(standing_bands),
        faction_impacts=faction_impacts,
        territory_impacts=territory_impacts,
        maintenance_actions=maintenance_actions,
        pledge_routes=pledge_routes,
        civic_mission=civic_mission,
        party_member_limit=_integer(foundation_doc, "party_member_limit", "foundation activation", minimum=1, maximum=6),
        mercenary_limit=_integer(foundation_doc, "mercenary_limit", "foundation activation", minimum=0, maximum=2),
    )
    if foundation_activation.party_member_limit != 6 or foundation_activation.mercenary_limit != 2:
        raise ContentError("foundation party contract must preserve six members plus two mercenaries")

    version, version_key = _semantic_version(
        world_doc.get("content_version"), f"{world_path}.content_version"
    )
    additive_from = _string_tuple(
        world_doc.get("additive_from"), f"{world_path}.additive_from"
    )
    if len(additive_from) != len(set(additive_from)):
        raise ContentError(f"{world_path}.additive_from contains duplicate versions")
    for source_version in additive_from:
        _, source_key = _semantic_version(
            source_version, f"{world_path}.additive_from"
        )
        if source_key >= version_key:
            raise ContentError(
                f"{world_path}.additive_from version {source_version!r} "
                f"must be older than {version!r}"
            )
    return ContentCatalog(
        version=version,
        start_room=start_room,
        rooms=rooms,
        items=items,
        creatures=creatures,
        progression=progression,
        courses=courses,
        creation=creation,
        story=story,
        economy=economy,
        beginner_experience=beginner_experience,
        journeyman_experience=journeyman_experience,
        foundation_activation=foundation_activation,
        additive_from=additive_from,
    )
