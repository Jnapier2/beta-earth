"""Serializable runtime state with explicit schema boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from beta_earth.domain.actions import ActionIntent, QueuedAction
from beta_earth.domain.battlefield import BattleState
from beta_earth.domain.sovereignty import FoundationState
from beta_earth.domain.state_migrations import (
    CURRENT_STATE_SCHEMA,
    READABLE_STATE_SCHEMAS,
    migrate_state_payload,
)
from beta_earth.domain.playtest import (
    PLAYTEST_EXPERIENCE_LEVELS,
    PLAYTEST_FAMILY_CLASSES,
    PLAYTEST_ISSUE_CATEGORIES,
    PLAYTEST_ISSUE_SEVERITIES,
    PLAYTEST_MODES,
)


STATE_SCHEMA_VERSION = CURRENT_STATE_SCHEMA
PLAYTEST_STATUSES = frozenset({"not_started", "running", "paused", "completed"})
PLAYTEST_SURVEY_FIELDS = frozenset(
    {"clarity", "pacing", "sol_helpfulness", "player_agency", "capstone", "readiness"}
)

BUILD_STATUSES = frozenset(
    {
        "pending",
        "confirmed",
        "legacy_preserved",
        "legacy_unresolved",
    }
)
BUILD_ALLOCATION_MODES = frozenset({"recommended", "manual", "legacy"})
TUTORIAL_STATUSES = frozenset(
    {"offered", "active", "skipped", "completed"}
)
TRAINABLE_ATTRIBUTE_IDS = (
    "strength",
    "agility",
    "perception",
    "combat_skill",
)


class Stance(str, Enum):
    DEFENSIVE = "defensive"
    GUARDED = "guarded"
    NEUTRAL = "neutral"
    FORWARD = "forward"
    ADVANCED = "advanced"
    OFFENSIVE = "offensive"

    @property
    def offense_modifier(self) -> int:
        return {
            Stance.DEFENSIVE: -45,
            Stance.GUARDED: -30,
            Stance.NEUTRAL: -15,
            Stance.FORWARD: 0,
            Stance.ADVANCED: 12,
            Stance.OFFENSIVE: 22,
        }[self]

    @property
    def defense_modifier(self) -> int:
        return {
            Stance.DEFENSIVE: 45,
            Stance.GUARDED: 30,
            Stance.NEUTRAL: 15,
            Stance.FORWARD: 0,
            Stance.ADVANCED: -12,
            Stance.OFFENSIVE: -22,
        }[self]


class DefenseMode(str, Enum):
    BALANCED = "balanced"
    EVADE = "evade"
    BLOCK = "block"
    PARRY = "parry"


@dataclass(slots=True)
class Wound:
    location: str
    severity: int
    bleeding: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "severity": self.severity,
            "bleeding": self.bleeding,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Wound":
        return cls(
            location=str(value["location"]),
            severity=int(value["severity"]),
            bleeding=int(value.get("bleeding", 0)),
        )


@dataclass(slots=True)
class ExperienceState:
    absorbed: int = 0
    field_pool: int = 0
    last_pulse_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "absorbed": self.absorbed,
            "field_pool": self.field_pool,
            "last_pulse_at": self.last_pulse_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExperienceState":
        return cls(
            absorbed=int(value.get("absorbed", 0)),
            field_pool=int(value.get("field_pool", 0)),
            last_pulse_at=float(value.get("last_pulse_at", 0.0)),
        )


@dataclass(slots=True)
class TrainingState:
    physical_points: int = 0
    mental_points: int = 0
    ranks: dict[str, int] = field(default_factory=dict)
    early_refunds_remaining: int = 0
    last_awarded_milestone: int = 0
    profile_id: str = "generalist"
    profile_changes_remaining: int = 1
    profile_locked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "physical_points": self.physical_points,
            "mental_points": self.mental_points,
            "ranks": dict(self.ranks),
            "early_refunds_remaining": self.early_refunds_remaining,
            "last_awarded_milestone": self.last_awarded_milestone,
            "profile_id": self.profile_id,
            "profile_changes_remaining": self.profile_changes_remaining,
            "profile_locked": self.profile_locked,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TrainingState":
        raw_ranks = value.get("ranks", {})
        if not isinstance(raw_ranks, dict):
            raise ValueError("training ranks must be an object")
        integer_fields = {
            "physical_points": value.get("physical_points", 0),
            "mental_points": value.get("mental_points", 0),
            "early_refunds_remaining": value.get(
                "early_refunds_remaining",
                0,
            ),
            "last_awarded_milestone": value.get(
                "last_awarded_milestone",
                0,
            ),
            "profile_changes_remaining": value.get(
                "profile_changes_remaining",
                1,
            ),
        }
        if any(type(item) is not int for item in integer_fields.values()):
            raise ValueError("training counters must be integers")
        if any(
            not isinstance(key, str) or type(rank) is not int
            for key, rank in raw_ranks.items()
        ):
            raise ValueError("training ranks must map strings to integers")
        profile_id = value.get("profile_id", "generalist")
        profile_locked = value.get("profile_locked", False)
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError("training profile ID must be non-empty text")
        if not isinstance(profile_locked, bool):
            raise ValueError("training profile lock must be boolean")
        return cls(
            physical_points=integer_fields["physical_points"],
            mental_points=integer_fields["mental_points"],
            ranks={str(key): int(rank) for key, rank in raw_ranks.items()},
            early_refunds_remaining=integer_fields[
                "early_refunds_remaining"
            ],
            last_awarded_milestone=integer_fields[
                "last_awarded_milestone"
            ],
            profile_id=profile_id,
            profile_changes_remaining=integer_fields[
                "profile_changes_remaining"
            ],
            profile_locked=profile_locked,
        )


@dataclass(slots=True)
class CourseProgressState:
    active_course_id: str | None = None
    step_index: int = 0
    completed_courses: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_course_id": self.active_course_id,
            "step_index": self.step_index,
            "completed_courses": sorted(self.completed_courses),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CourseProgressState":
        active_course_id = value.get("active_course_id")
        if active_course_id is not None and (
            not isinstance(active_course_id, str) or not active_course_id.strip()
        ):
            raise ValueError("active course ID must be non-empty text or null")
        step_index = value.get("step_index", 0)
        if type(step_index) is not int:
            raise ValueError("course step index must be an integer")
        raw_completed = value.get("completed_courses", [])
        if not isinstance(raw_completed, list) or not all(
            isinstance(course_id, str) and course_id.strip()
            for course_id in raw_completed
        ):
            raise ValueError(
                "completed courses must be a list of non-empty strings"
            )
        completed = {course_id.strip() for course_id in raw_completed}
        if len(completed) != len(raw_completed):
            raise ValueError("completed courses must not contain duplicates")
        return cls(
            active_course_id=(
                active_course_id.strip()
                if isinstance(active_course_id, str)
                else None
            ),
            step_index=step_index,
            completed_courses=completed,
        )


@dataclass(slots=True)
class CharacterBuildState:
    """Persisted character-creation choices, separate from learned ranks."""

    status: str = "legacy_unresolved"
    class_id: str | None = None
    allocation_mode: str | None = None
    base_attributes: dict[str, int] = field(default_factory=dict)
    tutorial_status: str = "offered"
    tutorial_step_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "class_id": self.class_id,
            "allocation_mode": self.allocation_mode,
            "base_attributes": dict(self.base_attributes),
            "tutorial_status": self.tutorial_status,
            "tutorial_step_id": self.tutorial_step_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CharacterBuildState":
        status = value.get("status", "legacy_unresolved")
        if not isinstance(status, str) or status not in BUILD_STATUSES:
            raise ValueError("character build status is invalid")
        class_id = value.get("class_id")
        if class_id is not None and (
            not isinstance(class_id, str) or not class_id.strip()
        ):
            raise ValueError(
                "character build class ID must be non-empty text or null"
            )
        allocation_mode = value.get("allocation_mode")
        if allocation_mode is not None and (
            not isinstance(allocation_mode, str)
            or allocation_mode not in BUILD_ALLOCATION_MODES
        ):
            raise ValueError("character build allocation mode is invalid")
        raw_attributes = value.get("base_attributes", {})
        if not isinstance(raw_attributes, dict) or any(
            not isinstance(key, str) or type(attribute_value) is not int
            for key, attribute_value in raw_attributes.items()
        ):
            raise ValueError(
                "character build base attributes must map strings to integers"
            )
        tutorial_status = value.get("tutorial_status", "offered")
        if (
            not isinstance(tutorial_status, str)
            or tutorial_status not in TUTORIAL_STATUSES
        ):
            raise ValueError("character tutorial status is invalid")
        tutorial_step_id = value.get("tutorial_step_id")
        if tutorial_step_id is not None and (
            not isinstance(tutorial_step_id, str)
            or not tutorial_step_id.strip()
        ):
            raise ValueError(
                "character tutorial step ID must be non-empty text or null"
            )
        return cls(
            status=status,
            class_id=class_id.strip() if isinstance(class_id, str) else None,
            allocation_mode=allocation_mode,
            base_attributes={
                str(key): int(attribute_value)
                for key, attribute_value in raw_attributes.items()
            },
            tutorial_status=tutorial_status,
            tutorial_step_id=(
                tutorial_step_id.strip()
                if isinstance(tutorial_step_id, str)
                else None
            ),
        )


@dataclass(slots=True)
class StoryState:
    """Persistent authored-story progress and concrete sovereignty decisions."""

    active_quest_id: str | None = None
    active_stage_id: str | None = None
    completed_quests: set[str] = field(default_factory=set)
    records: set[str] = field(default_factory=set)
    relationships: dict[str, int] = field(default_factory=dict)
    seen_dialogues: set[str] = field(default_factory=set)
    completed_actions: set[str] = field(default_factory=set)
    claimed_rewards: set[str] = field(default_factory=set)
    checkpoint_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_quest_id": self.active_quest_id,
            "active_stage_id": self.active_stage_id,
            "completed_quests": sorted(self.completed_quests),
            "records": sorted(self.records),
            "relationships": dict(sorted(self.relationships.items())),
            "seen_dialogues": sorted(self.seen_dialogues),
            "completed_actions": sorted(self.completed_actions),
            "claimed_rewards": sorted(self.claimed_rewards),
            "checkpoint_id": self.checkpoint_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StoryState":
        def optional_identifier(key: str) -> str | None:
            raw = value.get(key)
            if raw is None:
                return None
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"story {key} must be non-empty text or null")
            return raw.strip()

        def text_set(key: str) -> set[str]:
            raw = value.get(key, [])
            if not isinstance(raw, list) or not all(
                isinstance(item, str) and item.strip() for item in raw
            ):
                raise ValueError(
                    f"story {key} must be a list of non-empty strings"
                )
            normalized = {item.strip() for item in raw}
            if len(normalized) != len(raw):
                raise ValueError(f"story {key} must not contain duplicates")
            return normalized

        raw_relationships = value.get("relationships", {})
        if not isinstance(raw_relationships, dict) or not all(
            isinstance(key, str)
            and key.strip()
            and type(score) is int
            and -100 <= score <= 100
            for key, score in raw_relationships.items()
        ):
            raise ValueError(
                "story relationships must map non-empty strings to integers "
                "between -100 and 100"
            )
        active_quest_id = optional_identifier("active_quest_id")
        active_stage_id = optional_identifier("active_stage_id")
        if (active_quest_id is None) != (active_stage_id is None):
            raise ValueError(
                "story active quest and stage must both be set or both be null"
            )
        return cls(
            active_quest_id=active_quest_id,
            active_stage_id=active_stage_id,
            completed_quests=text_set("completed_quests"),
            records=text_set("records"),
            relationships={
                str(key).strip(): int(score)
                for key, score in raw_relationships.items()
            },
            seen_dialogues=text_set("seen_dialogues"),
            completed_actions=text_set("completed_actions"),
            claimed_rewards=text_set("claimed_rewards"),
            checkpoint_id=optional_identifier("checkpoint_id"),
        )


@dataclass(slots=True)
class CompanionProgressState:
    """Persistent growth and field condition for one companion."""

    level: int = 1
    experience: int = 0
    health: int = 32
    max_health: int = 32
    order: str = "balanced"
    defeated_targets: int = 0
    setup_actions: int = 0
    finish_reservations: int = 0
    player_enabled_finishes: int = 0
    finishing_strikes: int = 0
    damage_dealt: int = 0
    damage_intercepted: int = 0
    downed_until: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "experience": self.experience,
            "health": self.health,
            "max_health": self.max_health,
            "order": self.order,
            "defeated_targets": self.defeated_targets,
            "setup_actions": self.setup_actions,
            "finish_reservations": self.finish_reservations,
            "player_enabled_finishes": self.player_enabled_finishes,
            "finishing_strikes": self.finishing_strikes,
            "damage_dealt": self.damage_dealt,
            "damage_intercepted": self.damage_intercepted,
            "downed_until": self.downed_until,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CompanionProgressState":
        if not isinstance(value, dict):
            raise ValueError("companion progress must be an object")
        return cls(
            level=max(1, int(value.get("level", 1))),
            experience=max(0, int(value.get("experience", 0))),
            health=max(0, int(value.get("health", 32))),
            max_health=max(1, int(value.get("max_health", 32))),
            order=str(value.get("order", "balanced")),
            defeated_targets=max(0, int(value.get("defeated_targets", 0))),
            setup_actions=max(0, int(value.get("setup_actions", 0))),
            finish_reservations=max(0, int(value.get("finish_reservations", 0))),
            player_enabled_finishes=max(0, int(value.get("player_enabled_finishes", 0))),
            finishing_strikes=max(0, int(value.get("finishing_strikes", 0))),
            damage_dealt=max(0, int(value.get("damage_dealt", 0))),
            damage_intercepted=max(0, int(value.get("damage_intercepted", 0))),
            downed_until=max(0.0, float(value.get("downed_until", 0.0))),
        )


@dataclass(slots=True)
class CharacterState:
    key: str
    name: str
    room_id: str
    level: int = 1
    health: int = 40
    max_health: int = 40
    strength: int = 12
    agility: int = 12
    perception: int = 10
    combat_skill: int = 5
    stance: Stance = Stance.GUARDED
    defense_mode: DefenseMode = DefenseMode.BALANCED
    roundtime_until: float = 0.0
    condition_pulse_at: float = 0.0
    stunned_until: float = 0.0
    prone: bool = False
    resting: bool = False
    rest_pulse_at: float = 0.0
    inventory: list["ItemState"] = field(default_factory=list)
    equipped: dict[str, str] = field(default_factory=dict)
    wounds: list[Wound] = field(default_factory=list)
    experience: ExperienceState = field(default_factory=ExperienceState)
    training: TrainingState = field(default_factory=TrainingState)
    course: CourseProgressState = field(default_factory=CourseProgressState)
    build: CharacterBuildState = field(default_factory=CharacterBuildState)
    technique_ready_at: float = 0.0
    specialization_ready_at: float = 0.0
    specialization_follow_up_ready_until: float = 0.0
    specialization_uses: int = 0
    specialization_upgrade_id: str | None = None
    guard_points: int = 0
    credits: int = 0
    companion_id: str | None = None
    companion_progress: dict[str, CompanionProgressState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "room_id": self.room_id,
            "level": self.level,
            "health": self.health,
            "max_health": self.max_health,
            "strength": self.strength,
            "agility": self.agility,
            "perception": self.perception,
            "combat_skill": self.combat_skill,
            "stance": self.stance.value,
            "defense_mode": self.defense_mode.value,
            "roundtime_until": self.roundtime_until,
            "condition_pulse_at": self.condition_pulse_at,
            "stunned_until": self.stunned_until,
            "prone": self.prone,
            "resting": self.resting,
            "rest_pulse_at": self.rest_pulse_at,
            "inventory": [item.to_dict() for item in self.inventory],
            "equipped": dict(self.equipped),
            "wounds": [wound.to_dict() for wound in self.wounds],
            "experience": self.experience.to_dict(),
            "training": self.training.to_dict(),
            "course": self.course.to_dict(),
            "build": self.build.to_dict(),
            "technique_ready_at": self.technique_ready_at,
            "specialization_ready_at": self.specialization_ready_at,
            "specialization_follow_up_ready_until": self.specialization_follow_up_ready_until,
            "specialization_uses": self.specialization_uses,
            "specialization_upgrade_id": self.specialization_upgrade_id,
            "guard_points": self.guard_points,
            "credits": self.credits,
            "companion_id": self.companion_id,
            "companion_progress": {
                companion_id: progress.to_dict()
                for companion_id, progress in self.companion_progress.items()
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CharacterState":
        raw_prone = value.get("prone", False)
        if not isinstance(raw_prone, bool):
            raise ValueError("character prone state must be boolean")
        raw_resting = value.get("resting", False)
        if not isinstance(raw_resting, bool):
            raise ValueError("character resting state must be boolean")
        raw_training = value.get("training", {})
        if not isinstance(raw_training, dict):
            raise ValueError("character training state must be an object")
        raw_course = value.get("course", {})
        if not isinstance(raw_course, dict):
            raise ValueError("character course state must be an object")
        raw_build = value.get("build")
        if raw_build is not None and not isinstance(raw_build, dict):
            raise ValueError("character build state must be an object")
        raw_companion_progress = value.get("companion_progress", {})
        if not isinstance(raw_companion_progress, dict):
            raise ValueError("character companion progress must be an object")
        return cls(
            key=str(value["key"]),
            name=str(value["name"]),
            room_id=str(value["room_id"]),
            level=int(value.get("level", 1)),
            health=int(value.get("health", 40)),
            max_health=int(value.get("max_health", 40)),
            strength=int(value.get("strength", 12)),
            agility=int(value.get("agility", 12)),
            perception=int(value.get("perception", 10)),
            combat_skill=int(value.get("combat_skill", 5)),
            stance=Stance(value.get("stance", Stance.GUARDED.value)),
            defense_mode=DefenseMode(
                value.get("defense_mode", DefenseMode.BALANCED.value)
            ),
            roundtime_until=float(value.get("roundtime_until", 0.0)),
            condition_pulse_at=float(value.get("condition_pulse_at", 0.0)),
            stunned_until=float(value.get("stunned_until", 0.0)),
            prone=raw_prone,
            resting=raw_resting,
            rest_pulse_at=float(value.get("rest_pulse_at", 0.0)),
            inventory=[ItemState.from_dict(item) for item in value.get("inventory", [])],
            equipped={str(k): str(v) for k, v in value.get("equipped", {}).items()},
            wounds=[Wound.from_dict(wound) for wound in value.get("wounds", [])],
            experience=ExperienceState.from_dict(value.get("experience", {})),
            training=TrainingState.from_dict(raw_training),
            course=CourseProgressState.from_dict(raw_course),
            build=(
                CharacterBuildState.from_dict(raw_build)
                if isinstance(raw_build, dict)
                else CharacterBuildState()
            ),
            technique_ready_at=float(value.get("technique_ready_at", 0.0)),
            specialization_ready_at=float(value.get("specialization_ready_at", 0.0)),
            specialization_follow_up_ready_until=float(
                value.get("specialization_follow_up_ready_until", 0.0)
            ),
            specialization_uses=max(0, int(value.get("specialization_uses", 0))),
            specialization_upgrade_id=(
                str(value["specialization_upgrade_id"])
                if value.get("specialization_upgrade_id")
                else None
            ),
            guard_points=max(0, int(value.get("guard_points", 0))),
            credits=max(0, int(value.get("credits", 0))),
            companion_id=(str(value["companion_id"]) if value.get("companion_id") else None),
            companion_progress={
                str(companion_id): CompanionProgressState.from_dict(progress)
                for companion_id, progress in raw_companion_progress.items()
            },
        )


@dataclass(slots=True)
class ItemState:
    instance_id: str
    definition_id: str
    durability: int | None = None
    upgrade_level: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "definition_id": self.definition_id,
            "durability": self.durability,
            "upgrade_level": self.upgrade_level,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ItemState":
        raw_durability = value.get("durability")
        if raw_durability is not None and type(raw_durability) is not int:
            raise ValueError("item durability must be an integer or null")
        raw_upgrade = value.get("upgrade_level", 0)
        if type(raw_upgrade) is not int or not 0 <= raw_upgrade <= 3:
            raise ValueError("item upgrade level must be an integer from 0 to 3")
        return cls(
            instance_id=str(value["instance_id"]),
            definition_id=str(value["definition_id"]),
            durability=raw_durability,
            upgrade_level=raw_upgrade,
        )


@dataclass(slots=True)
class CreatureState:
    instance_id: str
    definition_id: str
    health: int
    phase: int = 1
    exchange_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "definition_id": self.definition_id,
            "health": self.health,
            "phase": self.phase,
            "exchange_count": self.exchange_count,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CreatureState":
        raw_phase = value.get("phase", 1)
        raw_exchange_count = value.get("exchange_count", 0)
        if type(raw_phase) is not int or not 1 <= raw_phase <= 3:
            raise ValueError("creature phase must be an integer from 1 to 3")
        if type(raw_exchange_count) is not int or raw_exchange_count < 0:
            raise ValueError("creature exchange_count must be a non-negative integer")
        return cls(
            instance_id=str(value["instance_id"]),
            definition_id=str(value["definition_id"]),
            health=int(value["health"]),
            phase=raw_phase,
            exchange_count=raw_exchange_count,
        )


@dataclass(slots=True)
class IncapacitationState:
    origin_room_id: str
    downed_at: float
    recover_at: float
    cause: str
    help_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin_room_id": self.origin_room_id,
            "downed_at": self.downed_at,
            "recover_at": self.recover_at,
            "cause": self.cause,
            "help_requested": self.help_requested,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IncapacitationState":
        help_requested = value.get("help_requested", False)
        if not isinstance(help_requested, bool):
            raise ValueError("incapacitation help_requested must be boolean")
        return cls(
            origin_room_id=str(value["origin_room_id"]),
            downed_at=float(value["downed_at"]),
            recover_at=float(value["recover_at"]),
            cause=str(value["cause"]),
            help_requested=help_requested,
        )


@dataclass(slots=True)
class BeginnerTelemetryState:
    """Private, local evidence used to calibrate the level 1-10 foundation."""

    total_commands: int = 0
    changed_commands: int = 0
    parse_errors: int = 0
    blocked_commands: int = 0
    incapacitations: int = 0
    recoveries: int = 0
    hints_requested: int = 0
    commands_since_progress: int = 0
    longest_stall: int = 0
    friction_since_progress: int = 0
    combat_progress_events: int = 0
    combat_repetition_commands: int = 0
    current_combat_repetition: int = 0
    longest_combat_repetition: int = 0
    current_combat_sequence: int = 0
    longest_combat_sequence: int = 0
    successful_withdrawals: int = 0
    failed_withdrawals: int = 0
    companion_setups: int = 0
    companion_finish_reservations: int = 0
    assist_prompts: int = 0
    last_assist_friction_count: int = 0
    brief_revisit_descriptions: int = 0
    last_progress_label: str | None = None
    first_friction_command: str | None = None
    last_friction_command: str | None = None
    chapter_commands: dict[str, int] = field(default_factory=dict)
    room_entries: dict[str, int] = field(default_factory=dict)
    playtest_status: str = "not_started"
    playtest_session_id: str | None = None
    playtest_family: str | None = None
    playtest_class_id: str | None = None
    playtest_mode: str = "standard"
    playtest_experience: str = "unspecified"
    playtest_profile_source: str | None = None
    playtest_assistive_tool: str | None = None
    playtest_issues: list[dict[str, str]] = field(default_factory=list)
    playtest_idle_threshold_seconds: int = 180
    playtest_started_at: float = 0.0
    playtest_last_activity_at: float = 0.0
    playtest_pause_started_at: float = 0.0
    playtest_completed_at: float = 0.0
    playtest_active_seconds: float = 0.0
    playtest_idle_seconds: float = 0.0
    playtest_paused_seconds: float = 0.0
    playtest_command_count: int = 0
    playtest_chapter_active_seconds: dict[str, float] = field(default_factory=dict)
    playtest_chapter_idle_seconds: dict[str, float] = field(default_factory=dict)
    playtest_milestones: dict[str, float] = field(default_factory=dict)
    playtest_notes: list[str] = field(default_factory=list)
    playtest_survey: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_commands": self.total_commands,
            "changed_commands": self.changed_commands,
            "parse_errors": self.parse_errors,
            "blocked_commands": self.blocked_commands,
            "incapacitations": self.incapacitations,
            "recoveries": self.recoveries,
            "hints_requested": self.hints_requested,
            "commands_since_progress": self.commands_since_progress,
            "longest_stall": self.longest_stall,
            "friction_since_progress": self.friction_since_progress,
            "combat_progress_events": self.combat_progress_events,
            "combat_repetition_commands": self.combat_repetition_commands,
            "current_combat_repetition": self.current_combat_repetition,
            "longest_combat_repetition": self.longest_combat_repetition,
            "current_combat_sequence": self.current_combat_sequence,
            "longest_combat_sequence": self.longest_combat_sequence,
            "successful_withdrawals": self.successful_withdrawals,
            "failed_withdrawals": self.failed_withdrawals,
            "companion_setups": self.companion_setups,
            "companion_finish_reservations": self.companion_finish_reservations,
            "assist_prompts": self.assist_prompts,
            "last_assist_friction_count": self.last_assist_friction_count,
            "brief_revisit_descriptions": self.brief_revisit_descriptions,
            "last_progress_label": self.last_progress_label,
            "first_friction_command": self.first_friction_command,
            "last_friction_command": self.last_friction_command,
            "chapter_commands": dict(sorted(self.chapter_commands.items())),
            "room_entries": dict(sorted(self.room_entries.items())),
            "playtest_status": self.playtest_status,
            "playtest_session_id": self.playtest_session_id,
            "playtest_family": self.playtest_family,
            "playtest_class_id": self.playtest_class_id,
            "playtest_mode": self.playtest_mode,
            "playtest_experience": self.playtest_experience,
            "playtest_profile_source": self.playtest_profile_source,
            "playtest_assistive_tool": self.playtest_assistive_tool,
            "playtest_issues": [dict(issue) for issue in self.playtest_issues],
            "playtest_idle_threshold_seconds": self.playtest_idle_threshold_seconds,
            "playtest_started_at": self.playtest_started_at,
            "playtest_last_activity_at": self.playtest_last_activity_at,
            "playtest_pause_started_at": self.playtest_pause_started_at,
            "playtest_completed_at": self.playtest_completed_at,
            "playtest_active_seconds": self.playtest_active_seconds,
            "playtest_idle_seconds": self.playtest_idle_seconds,
            "playtest_paused_seconds": self.playtest_paused_seconds,
            "playtest_command_count": self.playtest_command_count,
            "playtest_chapter_active_seconds": dict(sorted(self.playtest_chapter_active_seconds.items())),
            "playtest_chapter_idle_seconds": dict(sorted(self.playtest_chapter_idle_seconds.items())),
            "playtest_milestones": dict(sorted(self.playtest_milestones.items())),
            "playtest_notes": list(self.playtest_notes),
            "playtest_survey": dict(sorted(self.playtest_survey.items())),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BeginnerTelemetryState":
        if not isinstance(value, dict):
            raise ValueError("beginner telemetry must be an object")

        def counter(name: str) -> int:
            raw = value.get(name, 0)
            if type(raw) is not int or raw < 0:
                raise ValueError(f"beginner telemetry {name} must be a non-negative integer")
            return raw

        def timestamp(name: str) -> float:
            raw = value.get(name, 0.0)
            if not isinstance(raw, (int, float)):
                raise ValueError(f"beginner telemetry {name} must be numeric")
            number = float(raw)
            if number < 0 or number != number or number == float("inf"):
                raise ValueError(f"beginner telemetry {name} must be finite and non-negative")
            return number

        def optional_text(name: str) -> str | None:
            raw = value.get(name)
            if raw is None:
                return None
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"beginner telemetry {name} must be non-empty text or null")
            return raw.strip()[:160]

        def counter_map(name: str) -> dict[str, int]:
            raw = value.get(name, {})
            if not isinstance(raw, dict) or not all(
                isinstance(key, str) and key.strip() and type(count) is int and count >= 0
                for key, count in raw.items()
            ):
                raise ValueError(f"beginner telemetry {name} must map text to non-negative integers")
            return {str(key).strip(): int(count) for key, count in raw.items()}

        def duration_map(name: str) -> dict[str, float]:
            raw = value.get(name, {})
            if not isinstance(raw, dict):
                raise ValueError(f"beginner telemetry {name} must be an object")
            result: dict[str, float] = {}
            for key, duration in raw.items():
                if not isinstance(key, str) or not key.strip() or not isinstance(duration, (int, float)):
                    raise ValueError(f"beginner telemetry {name} contains an invalid entry")
                number = float(duration)
                if number < 0 or number != number or number == float("inf"):
                    raise ValueError(f"beginner telemetry {name} durations must be finite and non-negative")
                result[key.strip()] = number
            return result

        status = value.get("playtest_status", "not_started")
        if not isinstance(status, str) or status not in PLAYTEST_STATUSES:
            raise ValueError("beginner telemetry playtest status is invalid")
        raw_notes = value.get("playtest_notes", [])
        if not isinstance(raw_notes, list) or not all(
            isinstance(note, str) and note.strip() for note in raw_notes
        ):
            raise ValueError("beginner telemetry playtest notes must be non-empty text")
        notes = [" ".join(note.strip().split())[:240] for note in raw_notes]
        if len(notes) > 20:
            raise ValueError("beginner telemetry playtest notes exceed the 20-note limit")
        playtest_session_id = optional_text("playtest_session_id")
        raw_family = value.get("playtest_family")
        if raw_family is not None and (
            not isinstance(raw_family, str) or raw_family not in PLAYTEST_FAMILY_CLASSES
        ):
            raise ValueError("beginner telemetry playtest family is invalid")
        playtest_family = raw_family
        playtest_class_id = optional_text("playtest_class_id")
        raw_mode = value.get("playtest_mode", "standard")
        if not isinstance(raw_mode, str) or raw_mode not in PLAYTEST_MODES:
            raise ValueError("beginner telemetry playtest mode is invalid")
        raw_experience = value.get("playtest_experience", "unspecified")
        if (
            not isinstance(raw_experience, str)
            or raw_experience not in PLAYTEST_EXPERIENCE_LEVELS
        ):
            raise ValueError("beginner telemetry playtest experience is invalid")
        raw_profile_source = value.get("playtest_profile_source")
        if raw_profile_source is not None and raw_profile_source not in {
            "pending_build",
            "inferred",
            "explicit",
        }:
            raise ValueError("beginner telemetry playtest profile source is invalid")
        playtest_profile_source = raw_profile_source
        playtest_assistive_tool = optional_text("playtest_assistive_tool")
        if playtest_assistive_tool is not None:
            playtest_assistive_tool = playtest_assistive_tool[:80]
        raw_issues = value.get("playtest_issues", [])
        if not isinstance(raw_issues, list) or len(raw_issues) > 20:
            raise ValueError("beginner telemetry playtest issues must be a list of at most 20 entries")
        playtest_issues: list[dict[str, str]] = []
        for raw_issue in raw_issues:
            if not isinstance(raw_issue, dict):
                raise ValueError("beginner telemetry playtest issue must be an object")
            severity = raw_issue.get("severity")
            category = raw_issue.get("category")
            note = raw_issue.get("note")
            if severity not in PLAYTEST_ISSUE_SEVERITIES:
                raise ValueError("beginner telemetry playtest issue severity is invalid")
            if category not in PLAYTEST_ISSUE_CATEGORIES:
                raise ValueError("beginner telemetry playtest issue category is invalid")
            if not isinstance(note, str) or not note.strip():
                raise ValueError("beginner telemetry playtest issue note is invalid")
            playtest_issues.append({
                "severity": str(severity),
                "category": str(category),
                "note": " ".join(note.strip().split())[:240],
            })
        raw_idle_threshold = value.get("playtest_idle_threshold_seconds", 180)
        if type(raw_idle_threshold) is not int or not 30 <= raw_idle_threshold <= 900:
            raise ValueError("beginner telemetry playtest idle threshold must be 30-900 seconds")
        playtest_idle_threshold_seconds = raw_idle_threshold
        raw_survey = value.get("playtest_survey", {})
        if not isinstance(raw_survey, dict) or not all(
            field_name in PLAYTEST_SURVEY_FIELDS
            and type(score) is int
            and 1 <= score <= 5
            for field_name, score in raw_survey.items()
        ):
            raise ValueError("beginner telemetry playtest survey is invalid")

        return cls(
            total_commands=counter("total_commands"),
            changed_commands=counter("changed_commands"),
            parse_errors=counter("parse_errors"),
            blocked_commands=counter("blocked_commands"),
            incapacitations=counter("incapacitations"),
            recoveries=counter("recoveries"),
            hints_requested=counter("hints_requested"),
            commands_since_progress=counter("commands_since_progress"),
            longest_stall=counter("longest_stall"),
            friction_since_progress=counter("friction_since_progress"),
            combat_progress_events=counter("combat_progress_events"),
            combat_repetition_commands=counter("combat_repetition_commands"),
            current_combat_repetition=counter("current_combat_repetition"),
            longest_combat_repetition=counter("longest_combat_repetition"),
            current_combat_sequence=counter("current_combat_sequence"),
            longest_combat_sequence=counter("longest_combat_sequence"),
            successful_withdrawals=counter("successful_withdrawals"),
            failed_withdrawals=counter("failed_withdrawals"),
            companion_setups=counter("companion_setups"),
            companion_finish_reservations=counter("companion_finish_reservations"),
            assist_prompts=counter("assist_prompts"),
            last_assist_friction_count=counter("last_assist_friction_count"),
            brief_revisit_descriptions=counter("brief_revisit_descriptions"),
            last_progress_label=optional_text("last_progress_label"),
            first_friction_command=optional_text("first_friction_command"),
            last_friction_command=optional_text("last_friction_command"),
            chapter_commands=counter_map("chapter_commands"),
            room_entries=counter_map("room_entries"),
            playtest_status=status,
            playtest_session_id=playtest_session_id,
            playtest_family=playtest_family,
            playtest_class_id=playtest_class_id,
            playtest_mode=raw_mode,
            playtest_experience=raw_experience,
            playtest_profile_source=playtest_profile_source,
            playtest_assistive_tool=playtest_assistive_tool,
            playtest_issues=playtest_issues,
            playtest_idle_threshold_seconds=playtest_idle_threshold_seconds,
            playtest_started_at=timestamp("playtest_started_at"),
            playtest_last_activity_at=timestamp("playtest_last_activity_at"),
            playtest_pause_started_at=timestamp("playtest_pause_started_at"),
            playtest_completed_at=timestamp("playtest_completed_at"),
            playtest_active_seconds=timestamp("playtest_active_seconds"),
            playtest_idle_seconds=timestamp("playtest_idle_seconds"),
            playtest_paused_seconds=timestamp("playtest_paused_seconds"),
            playtest_command_count=counter("playtest_command_count"),
            playtest_chapter_active_seconds=duration_map("playtest_chapter_active_seconds"),
            playtest_chapter_idle_seconds=duration_map("playtest_chapter_idle_seconds"),
            playtest_milestones=duration_map("playtest_milestones"),
            playtest_notes=notes,
            playtest_survey={str(key): int(score) for key, score in raw_survey.items()},
        )


@dataclass(slots=True)
class GameState:
    character: CharacterState
    content_version: str
    room_items: dict[str, list[ItemState]]
    creatures: dict[str, list[CreatureState]]
    next_item_serial: int = 0
    defeated_creatures: set[str] = field(default_factory=set)
    revealed: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    story: StoryState = field(default_factory=StoryState)
    visited_rooms: set[str] = field(default_factory=set)
    target_id: str | None = None
    last_reference_kind: str | None = None
    last_reference_id: str | None = None
    last_action: ActionIntent | None = None
    queued_action: QueuedAction | None = None
    incapacitation: IncapacitationState | None = None
    beginner_telemetry: BeginnerTelemetryState = field(default_factory=BeginnerTelemetryState)
    battle: BattleState = field(default_factory=BattleState)
    foundation: FoundationState = field(default_factory=FoundationState)
    turn: int = 0
    revision: int = 0
    schema_version: int = STATE_SCHEMA_VERSION
    source_schema_version: int = field(
        default=STATE_SCHEMA_VERSION, repr=False, compare=False
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "turn": self.turn,
            "content_version": self.content_version,
            "next_item_serial": self.next_item_serial,
            "defeated_creatures": sorted(self.defeated_creatures),
            "character": self.character.to_dict(),
            "room_items": {
                key: [item.to_dict() for item in value]
                for key, value in self.room_items.items()
            },
            "creatures": {
                key: [creature.to_dict() for creature in value]
                for key, value in self.creatures.items()
            },
            "revealed": sorted(self.revealed),
            "flags": sorted(self.flags),
            "story": self.story.to_dict(),
            "visited_rooms": sorted(self.visited_rooms),
            "target_id": self.target_id,
            "last_reference_kind": self.last_reference_kind,
            "last_reference_id": self.last_reference_id,
            "last_action": self.last_action.to_dict() if self.last_action else None,
            "queued_action": (
                self.queued_action.to_dict() if self.queued_action else None
            ),
            "incapacitation": (
                self.incapacitation.to_dict() if self.incapacitation else None
            ),
            "beginner_telemetry": self.beginner_telemetry.to_dict(),
            "battle": self.battle.to_dict(),
            "foundation": self.foundation.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GameState":
        source_schema = int(value.get("schema_version", 0))
        value, _migration_names = migrate_state_payload(value)
        schema = int(value.get("schema_version", 0))
        if schema != STATE_SCHEMA_VERSION:
            raise ValueError(
                f"state migration produced schema {schema}; expected "
                f"{STATE_SCHEMA_VERSION}"
            )
        raw_last_action = value.get("last_action")
        if raw_last_action is not None and not isinstance(raw_last_action, dict):
            raise ValueError("last_action must be an object or null")
        raw_queued_action = value.get("queued_action")
        if raw_queued_action is not None and not isinstance(raw_queued_action, dict):
            raise ValueError("queued_action must be an object or null")
        raw_incapacitation = value.get("incapacitation")
        if raw_incapacitation is not None and not isinstance(
            raw_incapacitation, dict
        ):
            raise ValueError("incapacitation must be an object or null")
        raw_story = value.get("story", {})
        raw_beginner_telemetry = value.get("beginner_telemetry", {})
        if not isinstance(raw_beginner_telemetry, dict):
            raise ValueError("beginner telemetry must be an object")
        if not isinstance(raw_story, dict):
            raise ValueError("story state must be an object")
        raw_visited_rooms = value.get("visited_rooms", [])
        if not isinstance(raw_visited_rooms, list) or not all(
            isinstance(room_id, str) and room_id.strip()
            for room_id in raw_visited_rooms
        ):
            raise ValueError(
                "visited rooms must be a list of non-empty strings"
            )
        visited_rooms = {room_id.strip() for room_id in raw_visited_rooms}
        if len(visited_rooms) != len(raw_visited_rooms):
            raise ValueError("visited rooms must not contain duplicates")
        return cls(
            character=CharacterState.from_dict(value["character"]),
            content_version=str(value["content_version"]),
            room_items={
                str(key): [ItemState.from_dict(item) for item in items]
                for key, items in value.get("room_items", {}).items()
            },
            creatures={
                str(key): [CreatureState.from_dict(item) for item in creatures]
                for key, creatures in value.get("creatures", {}).items()
            },
            revealed={str(item) for item in value.get("revealed", [])},
            flags={str(item) for item in value.get("flags", [])},
            story=StoryState.from_dict(raw_story),
            visited_rooms=visited_rooms,
            target_id=(str(value["target_id"]) if value.get("target_id") else None),
            last_reference_kind=(
                str(value["last_reference_kind"])
                if value.get("last_reference_kind")
                else None
            ),
            last_reference_id=(
                str(value["last_reference_id"])
                if value.get("last_reference_id")
                else None
            ),
            last_action=(
                ActionIntent.from_dict(raw_last_action)
                if raw_last_action is not None
                else None
            ),
            queued_action=(
                QueuedAction.from_dict(raw_queued_action)
                if raw_queued_action is not None
                else None
            ),
            incapacitation=(
                IncapacitationState.from_dict(raw_incapacitation)
                if raw_incapacitation is not None
                else None
            ),
            beginner_telemetry=BeginnerTelemetryState.from_dict(raw_beginner_telemetry),
            battle=BattleState.from_dict(value.get("battle")),
            foundation=FoundationState.from_dict(value.get("foundation")),
            next_item_serial=int(value.get("next_item_serial", 0)),
            defeated_creatures={
                str(item) for item in value.get("defeated_creatures", [])
            },
            turn=int(value.get("turn", 0)),
            revision=int(value.get("revision", 0)),
            schema_version=STATE_SCHEMA_VERSION,
            source_schema_version=source_schema,
        )
