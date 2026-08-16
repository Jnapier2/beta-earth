"""Transport-independent command execution over one character state."""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import random
import re
from collections import deque
from dataclasses import replace

from beta_earth.application.action_catalog import build_action_registry
from beta_earth.application.contracts import (
    CONTENT_VERSION_PATTERN,
    HISTORY_EXCLUDED as _HISTORY_EXCLUDED,
    INCAPACITATED_COMMANDS as _INCAPACITATED_COMMANDS,
    PENDING_BUILD_COMMANDS as _PENDING_BUILD_COMMANDS,
)
from beta_earth.application.results import (
    CommandResult,
    HandlerResult as _HandlerResult,
)
from beta_earth.application.services.registry import EngineServiceRegistry
from beta_earth.application.text import (
    natural_list as _natural_list,
    normalize_player_name,
)
from beta_earth.application.parser import (
    CommandParseError,
    CommandParser,
    ParsedCommand,
)
from beta_earth.application.combat_scheduler import CombatScheduler
from beta_earth.application.selection import (
    RelativeSelector,
    Scope,
    parse_selection,
)
from beta_earth.domain.actions import (
    ActionIntent,
    QueuedAction,
    RecoveryClass,
    default_tactical_effect_specs,
)
from beta_earth.domain.battle_ai import (
    ATTACK_INTENTS,
    CHARGED_INTENTS,
    EnemyDecisionContext,
    choose_enemy_intent,
    enemy_recovery_seconds,
    intent_telegraph,
    sol_recovery_seconds,
    timing_description,
)
from beta_earth.domain.battlefield import (
    BattleState,
    CombatActorState,
    EncounterStatsState,
    TacticalEffectState,
    companion_actor_id,
    creature_actor_id,
    player_actor_id,
)
from beta_earth.domain.clock import Clock
from beta_earth.domain.combat import (
    HIT_LOCATIONS,
    RandomSource,
    aimed_attack_penalty,
    attack_roundtime,
    effective_item_definition,
    equipped_item,
    player_defense,
    player_offense,
    resolve_creature_attack,
    resolve_player_attack,
)
from beta_earth.domain.content import (
    ContentCatalog,
    CourseDefinition,
    CreatureDefinition,
    NpcDefinition,
    StoryActionDefinition,
    StoryQuestDefinition,
    StoryStageDefinition,
    TrainingOptionDefinition,
    TrainingProfileDefinition,
)
from beta_earth.domain.creation import (
    ATTRIBUTE_IDS,
    allocation_cost,
    apply_base_attributes,
    legacy_base_attributes,
    minimum_allocation,
    stat_effect_projection,
    validate_allocation,
)
from beta_earth.domain.events import DomainEvent
from beta_earth.domain.encumbrance import (
    encumbrance as calculate_encumbrance,
    item_bulk,
)
from beta_earth.domain.model import (
    CharacterBuildState,
    CharacterState,
    CompanionProgressState,
    CreatureState,
    DefenseMode,
    ExperienceState,
    GameState,
    IncapacitationState,
    ItemState,
    PLAYTEST_SURVEY_FIELDS,
    Stance,
    StoryState,
    Wound,
)
from beta_earth.domain.maneuvers import calculate_withdrawal_profile, resolve_withdrawal
from beta_earth.domain.playtest import (
    PLAYTEST_CHECKLISTS,
    PLAYTEST_EXPERIENCE_LEVELS,
    PLAYTEST_FAMILY_CLASSES,
    PLAYTEST_ISSUE_CATEGORIES,
    PLAYTEST_ISSUE_SEVERITIES,
    PLAYTEST_MODES,
    PLAYTEST_REPRESENTATIVE_CLASSES,
    family_for_class,
    normalize_experience,
    normalize_family,
    normalize_issue_category,
    normalize_mode,
)
from beta_earth.domain.progression import (
    INSIGHT_PER_LEVEL,
    award_training_milestones,
    award_field_insight,
    buy_training_rank,
    choose_training_profile,
    effective_training_cost,
    expected_trainable_attributes,
    initialize_training,
    pulse_experience,
    refund_training_rank,
    roundtime_remaining,
)
from beta_earth.domain.recovery import (
    active_bleeding,
    apply_impact_condition,
    disabled_limbs,
    pulse_bleeding,
    pulse_rest,
)












class GameEngine:
    def __init__(
        self,
        catalog: ContentCatalog,
        clock: Clock,
        rng: RandomSource | None = None,
        parser: CommandParser | None = None,
    ) -> None:
        self.catalog = catalog
        self.clock = clock
        self.rng = rng or random.Random()
        self.parser = parser or CommandParser()
        self.action_specs = build_action_registry(self.parser)
        self.effect_specs = default_tactical_effect_specs()
        self.combat_scheduler = CombatScheduler(self)
        self._services = EngineServiceRegistry(self)
        self._handlers = self._services.handler_map()

    def __getattr__(self, name: str):
        """Preserve the historical GameEngine API through bounded services."""

        services = self.__dict__.get("_services")
        if services is not None:
            resolved = services.resolve_attribute(name)
            if resolved is not None:
                return resolved
        raise AttributeError(f"{type(self).__name__!s} has no attribute {name!r}")

    @property
    def service_ownership(self) -> dict[str, tuple[str, ...]]:
        """Expose read-only command ownership for diagnostics and tests."""

        return self._services.ownership_projection()

    def new_game(
        self,
        player_name: str,
        *,
        foundation_pending: bool = False,
    ) -> GameState:
        key, display_name = normalize_player_name(player_name)
        now = self.clock.now()
        room_items = {
            room.id: [
                self._new_item_state(spawn.id, spawn.item_id)
                for spawn in room.items
            ]
            for room in self.catalog.rooms.values()
        }
        creatures: dict[str, list[CreatureState]] = {}
        for room in self.catalog.rooms.values():
            instances: list[CreatureState] = []
            for spawn in room.creatures:
                definition = self.catalog.creatures[spawn.creature_id]
                instances.append(
                    CreatureState(
                        instance_id=spawn.id,
                        definition_id=spawn.creature_id,
                        health=definition.max_health,
                    )
                )
            creatures[room.id] = instances
        character = CharacterState(
            key=key,
            name=display_name,
            room_id=self.catalog.start_room,
            inventory=[
                self._new_item_state(
                    f"starter:{key}:service-blade", "service_blade"
                ),
                self._new_item_state(
                    f"starter:{key}:field-coat", "field_coat"
                ),
            ],
            equipped={
                "main_hand": f"starter:{key}:service-blade",
                "body": f"starter:{key}:field-coat",
            },
            condition_pulse_at=now,
            experience=ExperienceState(last_pulse_at=now),
            training=initialize_training(self.catalog.progression),
            companion_id=("sol" if foundation_pending else None),
            companion_progress=(
                {
                    "sol": CompanionProgressState(
                        level=1,
                        experience=0,
                        health=32,
                        max_health=32,
                        order="balanced",
                    )
                }
                if foundation_pending
                else {}
            ),
            build=(
                CharacterBuildState(
                    status="pending",
                    base_attributes=minimum_allocation(self.catalog.creation),
                    tutorial_status="offered",
                )
                if foundation_pending
                else CharacterBuildState(
                    status="legacy_preserved",
                    allocation_mode="legacy",
                    base_attributes={
                        "strength": 12,
                        "agility": 12,
                        "perception": 10,
                        "combat_skill": 5,
                    },
                    tutorial_status="offered",
                )
            ),
        )
        apply_base_attributes(
            character,
            character.build.base_attributes,
            self.catalog.progression.options,
        )
        state = GameState(
            character=character,
            content_version=self.catalog.version,
            room_items=room_items,
            creatures=creatures,
            story=StoryState(
                active_quest_id=self.catalog.story.starting_quest_id,
                active_stage_id=self.catalog.story.starting_stage_id,
                relationships={
                    npc_id: 0 for npc_id in self.catalog.story.npcs
                },
            ),
            visited_rooms={self.catalog.start_room},
        )
        self._sync_active_foundations(state)
        return state








    @staticmethod
    def _version_key(value: str) -> tuple[int, int, int]:
        if not CONTENT_VERSION_PATTERN.fullmatch(value):
            raise ValueError(
                f"content version {value!r} is not numeric semantic versioning"
            )
        major, minor, patch = (int(part) for part in value.split("."))
        return major, minor, patch

    def reconcile_state(self, state: GameState) -> tuple[DomainEvent, ...]:
        """Apply safe additive catalog changes using stable authored spawn IDs."""
        if state.content_version == self.catalog.version:
            events: list[DomainEvent] = []
            if (
                state.story.active_quest_id is None
                and state.story.checkpoint_id is None
                and not state.story.completed_quests
                and not state.story.records
            ):
                state.story = StoryState(
                    active_quest_id=self.catalog.story.starting_quest_id,
                    active_stage_id=self.catalog.story.starting_stage_id,
                    relationships={
                        npc_id: 0 for npc_id in self.catalog.story.npcs
                    },
                )
                events.append(
                    DomainEvent(
                        "story.initialized",
                        {
                            "quest_id": self.catalog.story.starting_quest_id,
                            "stage_id": self.catalog.story.starting_stage_id,
                        },
                    )
                )
            synchronized = self._sync_active_foundations(state, tuple(events))
            events.extend(synchronized.events)
            return tuple(events)
        saved_key = self._version_key(state.content_version)
        catalog_key = self._version_key(self.catalog.version)
        if saved_key > catalog_key:
            raise ValueError(
                f"save content {state.content_version!r} is newer than "
                f"catalog {self.catalog.version!r}"
            )
        if state.content_version not in self.catalog.additive_from:
            raise ValueError(
                f"save content {state.content_version!r} has no declared additive "
                f"migration to catalog {self.catalog.version!r}"
            )
        previous = state.content_version
        relationships_initialized = 0
        # Additive NPCs must be initialized before validating an older save.
        # v0.13.0 only knew Sol and Mara; v0.13.1 adds Ilya Renn.
        if saved_key >= (0, 13, 0):
            for npc_id in self.catalog.story.npcs:
                if npc_id not in state.story.relationships:
                    state.story.relationships[npc_id] = 0
                    relationships_initialized += 1
        companion_initialized = False
        if (
            saved_key < (0, 19, 0)
            and state.character.companion_id is None
            and "sol_left_intake" not in state.flags
            and "sol_escaped" not in state.flags
            and "price_of_second_life" not in state.story.completed_quests
            and state.character.build.status in {"pending", "confirmed"}
        ):
            state.character.companion_id = "sol"
            state.character.companion_progress.setdefault(
                "sol",
                CompanionProgressState(
                    level=max(1, min(state.character.level, 10)),
                    experience=max(0, (max(1, min(state.character.level, 10)) - 1) * INSIGHT_PER_LEVEL),
                    health=32 + 4 * (max(1, min(state.character.level, 10)) - 1),
                    max_health=32 + 4 * (max(1, min(state.character.level, 10)) - 1),
                    order="balanced",
                ),
            )
            companion_initialized = True
        build_initialized = False
        if state.character.build.status == "legacy_unresolved":
            state.character.build = CharacterBuildState(
                status="legacy_preserved",
                class_id=None,
                allocation_mode="legacy",
                base_attributes=legacy_base_attributes(
                    state.character,
                    self.catalog.progression.options,
                ),
                tutorial_status="offered",
            )
            build_initialized = True
        stale_reference_repaired = self._repair_stale_reference(state)
        self.validate_state(state, require_content_version=False)
        training_initialized = saved_key < (0, 5, 0)
        if training_initialized:
            state.character.training = initialize_training(
                self.catalog.progression
            )
        profile_initialized = saved_key < (0, 6, 0)
        if profile_initialized:
            training = state.character.training
            training.profile_id = self.catalog.progression.default_profile
            training.profile_locked = bool(training.ranks)
            training.profile_changes_remaining = 0 if training.ranks else 1
        navigation_initialized = saved_key < (0, 9, 0)
        if navigation_initialized:
            state.visited_rooms.add(state.character.room_id)
        existing_item_ids = {
            item.instance_id for item in state.character.inventory
        } | {
            item.instance_id
            for room_items in state.room_items.values()
            for item in room_items
        }
        added_items = 0
        for room in self.catalog.rooms.values():
            room_state = state.room_items.setdefault(room.id, [])
            for spawn in room.items:
                if spawn.id not in existing_item_ids:
                    room_state.append(
                        self._new_item_state(spawn.id, spawn.item_id)
                    )
                    existing_item_ids.add(spawn.id)
                    added_items += 1
        durability_initialized = 0
        existing_items = list(state.character.inventory) + [
            item
            for room_state in state.room_items.values()
            for item in room_state
        ]
        for item in existing_items:
            definition = self.catalog.items.get(item.definition_id)
            if (
                definition is not None
                and definition.max_durability > 0
                and item.durability is None
            ):
                item.durability = definition.max_durability
                durability_initialized += 1
        live_creature_ids = {
            creature.instance_id
            for creatures in state.creatures.values()
            for creature in creatures
        }
        added_creatures = 0
        for room in self.catalog.rooms.values():
            room_state = state.creatures.setdefault(room.id, [])
            for spawn in room.creatures:
                if (
                    spawn.id not in live_creature_ids
                    and spawn.id not in state.defeated_creatures
                ):
                    definition = self.catalog.creatures[spawn.creature_id]
                    room_state.append(
                        CreatureState(
                            spawn.id, spawn.creature_id, definition.max_health
                        )
                    )
                    live_creature_ids.add(spawn.id)
                    added_creatures += 1
        story_initialized = False
        story_continued = False
        if saved_key < (0, 13, 0):
            state.story = StoryState(
                active_quest_id=self.catalog.story.starting_quest_id,
                active_stage_id=self.catalog.story.starting_stage_id,
                relationships={
                    npc_id: 0 for npc_id in self.catalog.story.npcs
                },
            )
            story_initialized = True
        else:
            if (
                saved_key < (0, 13, 1)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "first_watch_complete"
                and "lines_in_the_rain" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "lines_in_the_rain"
                state.story.active_stage_id = "power_briefing"
                story_continued = True
            elif (
                saved_key < (0, 13, 2)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "lines_in_the_rain_complete"
                and "marked_before_waking" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "marked_before_waking"
                state.story.active_stage_id = "find_subject_marker"
                story_continued = True
            elif (
                saved_key < (0, 14, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "wrong_pattern_complete"
                and "foundation_trials" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "foundation_trials"
                state.story.active_stage_id = "claim_technique"
                story_continued = True
            elif (
                saved_key < (0, 15, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "regional_path_open"
                and "first_contact_crossroads" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "first_contact_crossroads"
                state.story.active_stage_id = "choose_road"
                story_continued = True
            elif (
                saved_key < (0, 16, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "regional_expedition_complete"
                and "headquarters_approach_crossroads" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "headquarters_approach_crossroads"
                state.story.active_stage_id = "choose_approach"
                story_continued = True
            elif (
                saved_key < (0, 24, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "headquarters_approach_complete"
                and "unowned_caravan" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "unowned_caravan"
                state.story.active_stage_id = "return_to_concourse"
                story_continued = True
            elif (
                saved_key < (0, 25, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "unowned_caravan_complete"
                and "medicine_must_arrive" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "medicine_must_arrive"
                state.story.active_stage_id = "reach_muster"
                story_continued = True
            elif (
                saved_key < (0, 26, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "medicine_road_complete"
                and "one_report_many_lives" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "one_report_many_lives"
                state.story.active_stage_id = "receive_detail"
                story_continued = True
            elif (
                saved_key < (0, 27, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "relief_detail_complete"
                and "report_that_arrived_twice" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "report_that_arrived_twice"
                state.story.active_stage_id = "receive_conflict"
                story_continued = True
            elif (
                saved_key < (0, 28, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "report_reliability_complete"
                and "fifteen_lenses_one_truth" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "fifteen_lenses_one_truth"
                state.story.active_stage_id = "reach_gallery"
                story_continued = True
            elif (
                saved_key < (0, 29, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "class_lens_complete"
                and "the_road_that_changes_meaning" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "the_road_that_changes_meaning"
                state.story.active_stage_id = "receive_notice"
                story_continued = True
            elif (
                saved_key < (0, 30, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "district22_public_access_complete"
                and "the_public_queue_remembers" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "the_public_queue_remembers"
                state.story.active_stage_id = "hear_dispute"
                story_continued = True
            elif (
                saved_key < (0, 31, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "shaklas_queue_memory_complete"
                and "the_threshold_has_a_cost" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "the_threshold_has_a_cost"
                state.story.active_stage_id = "receive_cost"
                story_continued = True
            elif (
                saved_key < (0, 32, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "shaklas_threshold_cost_complete"
                and "the_light_is_borrowed" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "the_light_is_borrowed"
                state.story.active_stage_id = "receive_notice"
                story_continued = True
            elif (
                saved_key < (0, 33, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "shaklas_borrowed_light_complete"
                and "the_name_on_the_gift" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "the_name_on_the_gift"
                state.story.active_stage_id = "receive_offer"
                story_continued = True
            elif (
                saved_key < (0, 34, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "shaklas_gift_terms_complete"
                and "the_receipt_travels_without_you" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "the_receipt_travels_without_you"
                state.story.active_stage_id = "receive_copy"
                story_continued = True
            elif (
                saved_key < (0, 40, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "shaklas_receipt_scope_complete"
                and "the_appeal_is_not_a_verdict" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "the_appeal_is_not_a_verdict"
                state.story.active_stage_id = "receive_appeal"
                story_continued = True
            elif (
                saved_key < (0, 41, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "shaklas_appeal_complete"
                and "the_map_is_not_the_road" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "the_map_is_not_the_road"
                state.story.active_stage_id = "receive_wayfinding_notice"
                story_continued = True
            elif (
                saved_key < (0, 44, 0)
                and state.story.active_quest_id is None
                and state.story.checkpoint_id == "shaklas_wayfinding_complete"
                and "the_echo_on_the_public_line" in self.catalog.story.quests
            ):
                state.story.active_quest_id = "the_echo_on_the_public_line"
                state.story.active_stage_id = "receive_echo_notice"
                state.flags.add("journey_11_20_active")
                story_continued = True

        phase_interludes_credited = False
        if saved_key < (0, 45, 0):
            active_quest = state.story.active_quest_id
            active_stage = state.story.active_stage_id
            if (
                active_quest == "the_echo_takes_hold"
                and active_stage == "open_repeater"
                and "the_discipline_you_carry"
                not in state.story.completed_quests
            ):
                state.story.active_quest_id = "the_discipline_you_carry"
                state.story.active_stage_id = "receive_discipline_notice"
                story_continued = True
            elif (
                active_quest == "anchor_the_body"
                and active_stage == "enter_touch_workshop"
                and "the_partner_in_the_present"
                not in state.story.completed_quests
            ):
                state.story.completed_quests.add("the_discipline_you_carry")
                state.flags.add("phase_discipline_complete")
                state.story.records.add(
                    "journey_phase_interludes_prior_service"
                )
                state.story.active_quest_id = "the_partner_in_the_present"
                state.story.active_stage_id = "receive_partner_notice"
                phase_interludes_credited = True
                story_continued = True
            elif (
                state.story.checkpoint_id == "second_horizon_complete"
                or "journey_11_20_complete" in state.flags
                or state.character.level >= 20
            ):
                state.story.completed_quests.update(
                    {
                        "the_discipline_you_carry",
                        "the_partner_in_the_present",
                    }
                )
                state.flags.update(
                    {
                        "phase_discipline_complete",
                        "partner_synchrony_complete",
                    }
                )
                state.story.records.add(
                    "journey_phase_interludes_prior_service"
                )
                phase_interludes_credited = True
            elif active_quest in {
                "anchor_the_sightline",
                "anchor_the_signal",
                "the_partner_in_the_present",
                "anchor_the_body",
                "the_self_returns",
                "the_second_horizon",
            }:
                state.story.completed_quests.add("the_discipline_you_carry")
                state.flags.add("phase_discipline_complete")
                state.story.records.add(
                    "journey_phase_interludes_prior_service"
                )
                phase_interludes_credited = True
                if active_quest in {
                    "the_self_returns",
                    "the_second_horizon",
                }:
                    state.story.completed_quests.add(
                        "the_partner_in_the_present"
                    )
                    state.flags.add("partner_synchrony_complete")

        field_epilogue_continued = False
        if (
            saved_key < (0, 46, 0)
            and state.story.active_quest_id is None
            and (
                state.story.checkpoint_id == "second_horizon_complete"
                or "journey_11_20_complete" in state.flags
            )
            and "the_people_behind_the_signal" in self.catalog.story.quests
            and "the_people_behind_the_signal" not in state.story.completed_quests
        ):
            state.story.active_quest_id = "the_people_behind_the_signal"
            state.story.active_stage_id = "receive_cohort_signal"
            field_epilogue_continued = True
            story_continued = True

        witness_epilogue_continued = False
        if (
            saved_key < (0, 46, 1)
            and state.story.active_quest_id is None
            and (
                state.story.checkpoint_id == "field_cohort_epilogue_complete"
                or "field_cohort_detail_complete" in state.flags
            )
            and "the_report_that_expires" in self.catalog.story.quests
            and "the_report_that_expires" not in state.story.completed_quests
        ):
            state.story.active_quest_id = "the_report_that_expires"
            state.story.active_stage_id = "enter_witness_station"
            witness_epilogue_continued = True
            story_continued = True

        consent_epilogue_continued = False
        if (
            saved_key < (0, 47, 2)
            and state.story.active_quest_id is None
            and (
                state.story.checkpoint_id == "counter_signal_trial_complete"
                or "counter_signal_trial_complete" in state.flags
            )
            and "the_consent_that_cannot_be_copied" in self.catalog.story.quests
            and "the_consent_that_cannot_be_copied" not in state.story.completed_quests
        ):
            state.story.active_quest_id = "the_consent_that_cannot_be_copied"
            state.story.active_stage_id = "enter_consent_archive"
            consent_epilogue_continued = True
            story_continued = True

        shelter_lesson_credited = False
        if (
            saved_key < (0, 23, 0)
            and "partner_in_the_field" in state.story.completed_quests
            and "price_of_shelter" not in state.story.completed_quests
            and state.story.active_quest_id != "price_of_shelter"
        ):
            # Older characters already beyond Sol's field drill must not be sent
            # backward or forced to invent a choice that did not exist. Credit the
            # additive quest without its reward and preserve a neutral record.
            state.story.completed_quests.add("price_of_shelter")
            state.story.records.add("shelter_lesson_prior_service")
            state.flags.add("shelter_lesson_legacy_credit")
            shelter_lesson_credited = True

        tutorial_repair = self._apply_tutorial_progress(state, ())
        story_repair = self._apply_story_progress(state, ())
        state.content_version = self.catalog.version
        migration_events: list[DomainEvent] = [
            DomainEvent(
                "content.additive_reconciliation",
                {
                    "from": previous,
                    "to": self.catalog.version,
                    "items_added": added_items,
                    "creatures_added": added_creatures,
                    "durability_initialized": durability_initialized,
                    "training_initialized": training_initialized,
                    "profile_initialized": profile_initialized,
                    "navigation_initialized": navigation_initialized,
                    "build_initialized": build_initialized,
                    "stale_reference_repaired": stale_reference_repaired,
                    "companion_initialized": companion_initialized,
                    "story_initialized": story_initialized,
                    "story_continued": story_continued,
                    "shelter_lesson_credited": shelter_lesson_credited,
                    "phase_interludes_credited": phase_interludes_credited,
                    "field_epilogue_continued": field_epilogue_continued,
                    "story_stages_repaired": sum(
                        event.kind == "story.stage_completed"
                        for event in story_repair.events
                    ),
                    "relationships_initialized": relationships_initialized,
                    "tutorial_steps_repaired": sum(
                        event.kind == "tutorial.step_completed"
                        for event in tutorial_repair.events
                    ),
                },
            )
        ]
        if story_continued:
            migration_events.append(
                DomainEvent(
                    "story.arc_continued",
                    {
                        "quest_id": state.story.active_quest_id,
                        "stage_id": state.story.active_stage_id,
                        "from_checkpoint": state.story.checkpoint_id,
                    },
                )
            )
        if shelter_lesson_credited:
            migration_events.append(
                DomainEvent(
                    "story.additive_quest_credited",
                    {
                        "quest_id": "price_of_shelter",
                        "record_id": "shelter_lesson_prior_service",
                        "reward_granted": False,
                    },
                )
            )
        migration_events.extend(tutorial_repair.events)
        migration_events.extend(story_repair.events)
        synchronized = self._sync_active_foundations(
            state, tuple(migration_events)
        )
        migration_events.extend(synchronized.events)
        return tuple(migration_events)

    def validate_state(
        self, state: GameState, *, require_content_version: bool = True
    ) -> None:
        """Reject a stale or internally inconsistent snapshot before play."""
        if require_content_version and state.content_version != self.catalog.version:
            raise ValueError(
                "save content version "
                f"{state.content_version!r} is incompatible with catalog {self.catalog.version!r}"
            )
        if state.turn < 0:
            raise ValueError("save turn cannot be negative")
        if state.revision < 0:
            raise ValueError("save revision cannot be negative")
        character = state.character
        if character.technique_ready_at < 0:
            raise ValueError("save technique cooldown cannot be negative")
        if character.specialization_ready_at < 0:
            raise ValueError("save specialization cooldown cannot be negative")
        if character.specialization_follow_up_ready_until < 0:
            raise ValueError("save specialization follow-up window cannot be negative")
        if (
            character.specialization_uses < 0
            or character.specialization_uses > 100_000_000
        ):
            raise ValueError("save specialization use count is outside the valid range")
        if character.credits < 0 or character.credits > 100_000_000:
            raise ValueError("save credits are outside the valid range")
        if (
            character.companion_id is not None
            and character.companion_id not in self.catalog.economy.mercenaries
        ):
            raise ValueError("save companion references an unknown mercenary")
        unknown_companion_progress = set(character.companion_progress) - set(
            self.catalog.economy.mercenaries
        )
        if unknown_companion_progress:
            raise ValueError(
                "save companion progress references unknown companions "
                f"{sorted(unknown_companion_progress)}"
            )
        for companion_id, progress in character.companion_progress.items():
            if progress.level < 1 or progress.level > 100:
                raise ValueError(f"save companion {companion_id!r} has invalid level")
            if progress.experience < 0 or progress.experience > 100_000_000:
                raise ValueError(f"save companion {companion_id!r} has invalid experience")
            if progress.max_health < 1 or progress.max_health > 100_000:
                raise ValueError(f"save companion {companion_id!r} has invalid maximum health")
            if progress.health < 0 or progress.health > progress.max_health:
                raise ValueError(f"save companion {companion_id!r} has invalid health")
            if progress.order not in {"balanced", "guard", "assault"}:
                raise ValueError(f"save companion {companion_id!r} has invalid order")
            companion_counters = (
                progress.defeated_targets, progress.setup_actions,
                progress.finish_reservations, progress.player_enabled_finishes,
                progress.finishing_strikes, progress.damage_dealt,
                progress.damage_intercepted,
            )
            if any(type(value) is not int or not 0 <= value <= 100_000_000 for value in companion_counters):
                raise ValueError(f"save companion {companion_id!r} has invalid field counters")
            if progress.downed_until < 0 or not math.isfinite(progress.downed_until):
                raise ValueError(f"save companion {companion_id!r} has invalid recovery time")
        specialization_flags = [
            flag for flag in state.flags if flag.startswith("specialization:")
        ]
        if len(specialization_flags) > 1:
            raise ValueError("save contains more than one class specialization")
        if specialization_flags:
            selected_class = self.catalog.creation.classes.get(
                character.build.class_id or ""
            )
            branch_id = specialization_flags[0].split(":", 1)[1]
            if selected_class is None or branch_id not in selected_class.ability_branches:
                raise ValueError("save specialization does not belong to the selected class")
            selected_branch = selected_class.ability_branches[branch_id]
            if (
                character.specialization_upgrade_id is not None
                and character.specialization_upgrade_id
                not in selected_branch.upgrade_options
            ):
                raise ValueError("save specialization upgrade does not belong to the selected branch")
        elif character.specialization_upgrade_id is not None:
            raise ValueError("save cannot contain a specialization upgrade without a specialization")
        if not 0 <= character.guard_points <= 1000:
            raise ValueError("save guard points are outside the valid range")
        if character.room_id not in self.catalog.rooms:
            raise ValueError(f"save references unknown room {character.room_id!r}")
        if not isinstance(state.visited_rooms, set) or any(
            not isinstance(room_id, str) or not room_id
            for room_id in state.visited_rooms
        ):
            raise ValueError("save visited-room state is invalid")
        unknown_visited = state.visited_rooms - set(self.catalog.rooms)
        if unknown_visited:
            raise ValueError(
                f"save references unknown visited rooms {sorted(unknown_visited)}"
            )
        if self._version_key(state.content_version) >= (0, 9, 0):
            if not state.visited_rooms:
                raise ValueError("save must contain at least one visited room")
            if character.room_id not in state.visited_rooms:
                raise ValueError(
                    "save current room is absent from spatial memory"
                )
        if state.next_item_serial < 0:
            raise ValueError("save next_item_serial cannot be negative")
        if character.level < 1:
            raise ValueError("save character level must be positive")
        if character.max_health <= 0:
            raise ValueError("save character max_health must be positive")
        if character.health <= 0 or character.health > character.max_health:
            raise ValueError("save character health is outside its valid range")
        if any(
            value < 0
            for value in (
                character.strength,
                character.agility,
                character.perception,
                character.combat_skill,
            )
        ):
            raise ValueError("save character attributes cannot be negative")
        if not math.isfinite(character.roundtime_until) or character.roundtime_until < 0:
            raise ValueError("save character roundtime is invalid")
        if (
            not math.isfinite(character.condition_pulse_at)
            or character.condition_pulse_at < 0
        ):
            raise ValueError("save character condition pulse timestamp is invalid")
        if not math.isfinite(character.stunned_until) or character.stunned_until < 0:
            raise ValueError("save character stun timestamp is invalid")
        if not isinstance(character.prone, bool):
            raise ValueError("save character prone state is invalid")
        if not isinstance(character.resting, bool):
            raise ValueError("save character resting state is invalid")
        if not math.isfinite(character.rest_pulse_at) or character.rest_pulse_at < 0:
            raise ValueError("save character rest pulse timestamp is invalid")
        incapacitation = state.incapacitation
        if incapacitation is not None:
            if incapacitation.origin_room_id not in self.catalog.rooms:
                raise ValueError("save incapacitation references an unknown room")
            if character.room_id != incapacitation.origin_room_id:
                raise ValueError(
                    "save incapacitated character is not in the origin room"
                )
            if (
                not math.isfinite(incapacitation.downed_at)
                or incapacitation.downed_at < 0
                or not math.isfinite(incapacitation.recover_at)
                or incapacitation.recover_at < incapacitation.downed_at
            ):
                raise ValueError("save incapacitation timing is invalid")
            if not incapacitation.cause.strip():
                raise ValueError("save incapacitation cause is empty")
            if not isinstance(incapacitation.help_requested, bool):
                raise ValueError("save incapacitation help request is invalid")
            if character.health != 1:
                raise ValueError("save incapacitated character health must be 1")
            if not character.prone:
                raise ValueError("save incapacitated character must be prone")
            if character.resting:
                raise ValueError("save incapacitated character cannot be resting")
            if state.target_id is not None:
                raise ValueError("save incapacitated character cannot have a target")
            if state.queued_action is not None:
                raise ValueError("save incapacitated character cannot queue an action")
        experience = character.experience
        if experience.absorbed < 0 or experience.field_pool < 0:
            raise ValueError("save experience values cannot be negative")
        if not math.isfinite(experience.last_pulse_at) or experience.last_pulse_at < 0:
            raise ValueError("save experience pulse timestamp is invalid")
        training = character.training
        training_counters = (
            training.physical_points,
            training.mental_points,
            training.early_refunds_remaining,
            training.last_awarded_milestone,
            training.profile_changes_remaining,
        )
        if any(type(value) is not int or value < 0 for value in training_counters):
            raise ValueError("save training counters must be non-negative integers")
        if any(value > 100_000_000 for value in training_counters):
            raise ValueError("save training counter exceeds the safe bound")
        if (
            training.early_refunds_remaining
            > self.catalog.progression.early_refunds
        ):
            raise ValueError("save training refunds exceed the authored allowance")
        if training.profile_changes_remaining > 1:
            raise ValueError("save training-path changes exceed the authored allowance")
        if training.profile_id not in self.catalog.progression.profiles:
            raise ValueError(
                f"save references unknown training profile {training.profile_id!r}"
            )
        if not isinstance(training.profile_locked, bool):
            raise ValueError("save training profile lock is invalid")
        maximum_milestone = experience.absorbed // 100
        if training.last_awarded_milestone > maximum_milestone:
            raise ValueError("save training milestone exceeds learned experience")
        if character.level != training.last_awarded_milestone + 1:
            raise ValueError("save level and training milestone disagree")
        unknown_training = set(training.ranks) - set(
            self.catalog.progression.options
        )
        if unknown_training:
            raise ValueError(
                f"save references unknown training options {sorted(unknown_training)}"
            )
        for option_id, rank in training.ranks.items():
            option = self.catalog.progression.options[option_id]
            if type(rank) is not int or rank < 1 or rank > option.max_rank:
                raise ValueError(
                    f"save training rank {option_id!r} is outside its valid range"
                )
        build = character.build
        if build.status == "legacy_unresolved":
            raise ValueError(
                "save has unresolved legacy character-foundation state"
            )
        if set(build.base_attributes) != set(ATTRIBUTE_IDS):
            raise ValueError(
                "save build must define every base attribute exactly once"
            )
        if any(
            type(value) is not int or value < 0
            for value in build.base_attributes.values()
        ):
            raise ValueError(
                "save build base attributes must be non-negative integers"
            )
        if build.status == "legacy_preserved":
            if build.class_id is not None:
                raise ValueError(
                    "save infers a class for a legacy-preserved character"
                )
            if build.allocation_mode != "legacy":
                raise ValueError(
                    "save legacy-preserved build has a non-legacy allocation"
                )
        else:
            if build.class_id is not None and (
                build.class_id not in self.catalog.creation.classes
            ):
                raise ValueError(
                    f"save references unknown class {build.class_id!r}"
                )
            validate_allocation(
                self.catalog.creation,
                build.base_attributes,
                require_full_budget=build.status == "confirmed",
            )
            if build.status == "confirmed":
                if build.class_id is None:
                    raise ValueError("save confirmed build has no class")
                if build.allocation_mode not in {"recommended", "manual"}:
                    raise ValueError(
                        "save confirmed build has no allocation mode"
                    )
                expected_profile = self.catalog.creation.classes[
                    build.class_id
                ].training_profile_id
                if training.profile_id != expected_profile:
                    raise ValueError(
                        "save confirmed class and training profile disagree"
                    )
                if training.profile_changes_remaining:
                    raise ValueError(
                        "save confirmed class retains a free path change"
                    )
            elif build.status == "pending":
                if build.allocation_mode not in {
                    None,
                    "recommended",
                    "manual",
                }:
                    raise ValueError(
                        "save pending build has an invalid allocation mode"
                    )
                if training.ranks:
                    raise ValueError(
                        "save pending build contains learned training ranks"
                    )
        tutorial = self.catalog.creation.tutorial
        tutorial_step_ids = {step.id for step in tutorial.steps}
        if build.tutorial_status == "active":
            if build.tutorial_step_id not in tutorial_step_ids:
                raise ValueError(
                    "save active tutorial references an unknown step"
                )
        elif build.tutorial_step_id is not None:
            raise ValueError(
                "save inactive tutorial retains a current step"
            )
        expected_attributes = expected_trainable_attributes(
            training,
            self.catalog.progression.options,
            base_attributes=build.base_attributes,
        )
        for attribute, expected in expected_attributes.items():
            if getattr(character, attribute) != expected:
                raise ValueError(
                    f"save trained attribute {attribute!r} disagrees with ranks"
                )
        if self._version_key(state.content_version) >= (0, 6, 0):
            if training.profile_locked and training.profile_changes_remaining:
                raise ValueError(
                    "save locked training profile retains a path change"
                )
            if training.ranks and not training.profile_locked:
                raise ValueError(
                    "save has training ranks without a locked profile"
                )
            if (
                training.profile_id != self.catalog.progression.default_profile
                and training.profile_changes_remaining
            ):
                raise ValueError(
                    "save changed training profile retains a path change"
                )
        course_progress = character.course
        if type(course_progress.step_index) is not int:
            raise ValueError("save course step index must be an integer")
        if not isinstance(course_progress.completed_courses, set) or any(
            not isinstance(course_id, str) or not course_id
            for course_id in course_progress.completed_courses
        ):
            raise ValueError("save completed courses are invalid")
        unknown_completed = (
            course_progress.completed_courses - set(self.catalog.courses)
        )
        if unknown_completed:
            raise ValueError(
                f"save references unknown completed courses "
                f"{sorted(unknown_completed)}"
            )
        active_course_id = course_progress.active_course_id
        if active_course_id is None:
            if course_progress.step_index != 0:
                raise ValueError(
                    "save has a course step without an active course"
                )
        else:
            if (
                not isinstance(active_course_id, str)
                or active_course_id not in self.catalog.courses
            ):
                raise ValueError(
                    f"save references unknown active course "
                    f"{active_course_id!r}"
                )
            if active_course_id in course_progress.completed_courses:
                raise ValueError(
                    "save marks the active course as already completed"
                )
            step_count = len(self.catalog.courses[active_course_id].steps)
            if (
                course_progress.step_index < 0
                or course_progress.step_index >= step_count
            ):
                raise ValueError(
                    "save active course step is outside its valid range"
                )
        story = state.story
        all_quest_ids = set(self.catalog.story.quests)
        all_stage_ids = {
            (quest.id, stage.id)
            for quest in self.catalog.story.quests.values()
            for stage in quest.stages
        }
        all_action_ids = {
            action.id
            for quest in self.catalog.story.quests.values()
            for stage in quest.stages
            for action in stage.actions
        }
        if (story.active_quest_id is None) != (story.active_stage_id is None):
            raise ValueError(
                "save story active quest and stage must both be set or null"
            )
        if story.active_quest_id is not None and (
            story.active_quest_id,
            story.active_stage_id,
        ) not in all_stage_ids:
            raise ValueError("save story references an unknown active stage")
        if story.active_quest_id in story.completed_quests:
            raise ValueError("save story marks the active quest complete")
        unknown_completed_quests = story.completed_quests - all_quest_ids
        if unknown_completed_quests:
            raise ValueError(
                "save story references unknown completed quests "
                f"{sorted(unknown_completed_quests)}"
            )
        unknown_records = story.records - set(self.catalog.story.records)
        if unknown_records:
            raise ValueError(
                f"save story references unknown records {sorted(unknown_records)}"
            )
        unknown_dialogues = story.seen_dialogues - set(
            self.catalog.story.dialogues
        )
        if unknown_dialogues:
            raise ValueError(
                "save story references unknown dialogues "
                f"{sorted(unknown_dialogues)}"
            )
        unknown_actions = story.completed_actions - all_action_ids
        if unknown_actions:
            raise ValueError(
                f"save story references unknown actions {sorted(unknown_actions)}"
            )
        unknown_rewards = story.claimed_rewards - set(
            self.catalog.story.rewards
        )
        if unknown_rewards:
            raise ValueError(
                f"save story references unknown rewards {sorted(unknown_rewards)}"
            )
        unknown_relationships = set(story.relationships) - set(
            self.catalog.story.npcs
        )
        if unknown_relationships:
            raise ValueError(
                "save story references unknown NPC relationships "
                f"{sorted(unknown_relationships)}"
            )
        if any(
            type(value) is not int or value < -100 or value > 100
            for value in story.relationships.values()
        ):
            raise ValueError("save story relationship scores are invalid")
        if story.checkpoint_id is not None and (
            not isinstance(story.checkpoint_id, str)
            or not story.checkpoint_id.strip()
        ):
            raise ValueError("save story checkpoint is invalid")
        if self._version_key(state.content_version) >= (0, 13, 0):
            if set(story.relationships) != set(self.catalog.story.npcs):
                raise ValueError(
                    "save story must initialize every authored NPC relationship"
                )
            if (
                story.active_quest_id is None
                and story.checkpoint_id is None
            ):
                raise ValueError(
                    "save story has neither an active stage nor a checkpoint"
                )

        telemetry = state.beginner_telemetry
        telemetry_counters = (
            telemetry.total_commands,
            telemetry.changed_commands,
            telemetry.parse_errors,
            telemetry.blocked_commands,
            telemetry.incapacitations,
            telemetry.recoveries,
            telemetry.hints_requested,
            telemetry.commands_since_progress,
            telemetry.longest_stall,
            telemetry.friction_since_progress,
            telemetry.combat_progress_events,
            telemetry.combat_repetition_commands,
            telemetry.current_combat_repetition,
            telemetry.longest_combat_repetition,
            telemetry.current_combat_sequence,
            telemetry.longest_combat_sequence,
            telemetry.successful_withdrawals,
            telemetry.failed_withdrawals,
            telemetry.companion_setups,
            telemetry.companion_finish_reservations,
            telemetry.playtest_command_count,
        )
        if any(
            type(value) is not int or not 0 <= value <= 100_000_000
            for value in telemetry_counters
        ):
            raise ValueError("save beginner telemetry counters are invalid")
        if telemetry.changed_commands > telemetry.total_commands:
            raise ValueError("save beginner telemetry changed count exceeds total commands")
        if telemetry.current_combat_repetition > telemetry.current_combat_sequence:
            raise ValueError("save beginner telemetry combat repetition exceeds sequence")
        if telemetry.current_combat_repetition > telemetry.longest_combat_repetition:
            raise ValueError("save beginner telemetry combat repetition exceeds its maximum")
        if telemetry.current_combat_sequence > telemetry.longest_combat_sequence:
            raise ValueError("save beginner telemetry combat sequence exceeds its maximum")
        for mapping_name, mapping in (
            ("chapter", telemetry.chapter_commands),
            ("room", telemetry.room_entries),
        ):
            if not isinstance(mapping, dict) or any(
                not isinstance(key, str)
                or not key.strip()
                or type(value) is not int
                or value < 0
                for key, value in mapping.items()
            ):
                raise ValueError(f"save beginner telemetry {mapping_name} map is invalid")
        if any(
            value is not None and (not isinstance(value, str) or not value.strip())
            for value in (
                telemetry.last_progress_label,
                telemetry.first_friction_command,
                telemetry.last_friction_command,
                telemetry.playtest_session_id,
                telemetry.playtest_family,
                telemetry.playtest_class_id,
                telemetry.playtest_profile_source,
                telemetry.playtest_assistive_tool,
            )
        ):
            raise ValueError("save beginner telemetry text is invalid")
        if telemetry.playtest_status not in {
            "not_started", "running", "paused", "completed"
        }:
            raise ValueError("save beginner telemetry playtest status is invalid")
        if telemetry.playtest_family is not None and telemetry.playtest_family not in PLAYTEST_FAMILY_CLASSES:
            raise ValueError("save beginner telemetry playtest family is invalid")
        if telemetry.playtest_mode not in PLAYTEST_MODES:
            raise ValueError("save beginner telemetry playtest mode is invalid")
        if telemetry.playtest_experience not in PLAYTEST_EXPERIENCE_LEVELS:
            raise ValueError("save beginner telemetry playtest experience is invalid")
        if telemetry.playtest_profile_source not in {
            None,
            "pending_build",
            "inferred",
            "explicit",
        }:
            raise ValueError("save beginner telemetry playtest profile source is invalid")
        if telemetry.playtest_assistive_tool is not None and len(telemetry.playtest_assistive_tool) > 80:
            raise ValueError("save beginner telemetry playtest assistive tool is invalid")
        if len(telemetry.playtest_issues) > 20:
            raise ValueError("save beginner telemetry playtest issues exceed the 20-entry limit")
        for issue in telemetry.playtest_issues:
            if (
                not isinstance(issue, dict)
                or issue.get("severity") not in PLAYTEST_ISSUE_SEVERITIES
                or issue.get("category") not in PLAYTEST_ISSUE_CATEGORIES
                or not isinstance(issue.get("note"), str)
                or not str(issue.get("note")).strip()
                or len(str(issue.get("note"))) > 240
            ):
                raise ValueError("save beginner telemetry playtest issue is invalid")
        if not 30 <= telemetry.playtest_idle_threshold_seconds <= 900:
            raise ValueError("save beginner telemetry playtest idle threshold is invalid")
        playtest_times = (
            telemetry.playtest_started_at,
            telemetry.playtest_last_activity_at,
            telemetry.playtest_pause_started_at,
            telemetry.playtest_completed_at,
            telemetry.playtest_active_seconds,
            telemetry.playtest_idle_seconds,
            telemetry.playtest_paused_seconds,
        )
        if any(
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in playtest_times
        ):
            raise ValueError("save beginner telemetry playtest timing is invalid")
        if telemetry.playtest_status != "not_started":
            if not telemetry.playtest_session_id:
                raise ValueError("save active playtest has no session id")
            if telemetry.playtest_started_at <= 0:
                raise ValueError("save active playtest has no start timestamp")
            build_is_confirmed = state.character.build.status == "confirmed"
            if build_is_confirmed and (
                telemetry.playtest_family is None
                or telemetry.playtest_class_id is None
            ):
                raise ValueError(
                    "save active playtest has no locked class-family profile"
                )
            if telemetry.playtest_profile_source not in {
                "pending_build",
                "inferred",
                "explicit",
            }:
                raise ValueError("save active playtest has no profile source")
            if (
                telemetry.playtest_class_id is not None
                and telemetry.playtest_family is not None
                and telemetry.playtest_class_id
                not in PLAYTEST_FAMILY_CLASSES[telemetry.playtest_family]
            ):
                raise ValueError("save active playtest class does not match its family")
        if telemetry.playtest_status == "paused" and telemetry.playtest_pause_started_at <= 0:
            raise ValueError("save paused playtest has no pause timestamp")
        if telemetry.playtest_status == "completed" and telemetry.playtest_completed_at <= 0:
            raise ValueError("save completed playtest has no completion timestamp")
        if (
            telemetry.playtest_completed_at
            and telemetry.playtest_completed_at < telemetry.playtest_started_at
        ):
            raise ValueError("save playtest completion predates its start")
        if (
            telemetry.playtest_last_activity_at
            and telemetry.playtest_last_activity_at < telemetry.playtest_started_at
        ):
            raise ValueError("save playtest activity predates its start")
        if len(telemetry.playtest_notes) > 20 or any(
            not isinstance(note, str) or not note.strip() or len(note) > 240
            for note in telemetry.playtest_notes
        ):
            raise ValueError("save beginner telemetry playtest notes are invalid")
        if any(
            field not in PLAYTEST_SURVEY_FIELDS
            or type(score) is not int
            or not 1 <= score <= 5
            for field, score in telemetry.playtest_survey.items()
        ):
            raise ValueError("save beginner telemetry playtest survey is invalid")

        if len(character.wounds) > 100:
            raise ValueError("save contains too many wounds")
        for wound in character.wounds:
            if not isinstance(wound.location, str) or not wound.location.strip():
                raise ValueError("save contains a wound without a location")
            if wound.severity < 1 or wound.severity > 5:
                raise ValueError("save contains an invalid wound severity")
            if wound.bleeding < 0 or wound.bleeding > wound.severity:
                raise ValueError("save contains an invalid wound bleeding value")
        if require_content_version:
            missing_item_rooms = set(self.catalog.rooms) - set(state.room_items)
            missing_creature_rooms = set(self.catalog.rooms) - set(state.creatures)
            if missing_item_rooms:
                raise ValueError(
                    f"save has no item state for rooms {sorted(missing_item_rooms)}"
                )
            if missing_creature_rooms:
                raise ValueError(
                    f"save has no creature state for rooms {sorted(missing_creature_rooms)}"
                )
        all_instance_ids: list[str] = []
        for item in character.inventory:
            if not item.instance_id:
                raise ValueError("save contains an empty item instance ID")
            all_instance_ids.append(item.instance_id)
            if item.definition_id not in self.catalog.items:
                raise ValueError(
                    f"save inventory references unknown item {item.definition_id!r}"
                )
            self._validate_item_durability(
                item,
                allow_missing=not require_content_version,
            )
        equipped_ids = list(character.equipped.values())
        if len(equipped_ids) != len(set(equipped_ids)):
            raise ValueError("save equips one item instance in multiple slots")
        for slot, instance_id in character.equipped.items():
            equipped_item_state = self._inventory_item(state, instance_id)
            if equipped_item_state is None:
                raise ValueError(
                    f"save equips item instance {instance_id!r} in {slot!r} without carrying it"
                )
            definition = self.catalog.items[equipped_item_state.definition_id]
            if definition.slot != slot:
                raise ValueError(
                    f"save equips item instance {instance_id!r} in incompatible slot {slot!r}"
                )
        for room_id, items in state.room_items.items():
            if room_id not in self.catalog.rooms:
                raise ValueError(f"save item state references unknown room {room_id!r}")
            for item in items:
                if not item.instance_id:
                    raise ValueError("save contains an empty item instance ID")
                all_instance_ids.append(item.instance_id)
            unknown = [
                item.definition_id
                for item in items
                if item.definition_id not in self.catalog.items
            ]
            if unknown:
                raise ValueError(f"save room {room_id!r} references unknown items {unknown}")
            for item in items:
                self._validate_item_durability(
                    item,
                    allow_missing=not require_content_version,
                )
        if len(all_instance_ids) != len(set(all_instance_ids)):
            raise ValueError("save contains duplicate item instance IDs")
        live_creature_ids: list[str] = []
        for room_id, creatures in state.creatures.items():
            if room_id not in self.catalog.rooms:
                raise ValueError(f"save creature state references unknown room {room_id!r}")
            for creature in creatures:
                if not creature.instance_id:
                    raise ValueError("save contains an empty creature instance ID")
                live_creature_ids.append(creature.instance_id)
                if creature.definition_id not in self.catalog.creatures:
                    raise ValueError(
                        f"save references unknown creature {creature.definition_id!r}"
                    )
                definition = self.catalog.creatures[creature.definition_id]
                maximum = definition.max_health
                if creature.health <= 0 or creature.health > maximum:
                    raise ValueError(
                        f"save creature {creature.instance_id!r} has invalid health"
                    )
                if type(creature.phase) is not int or not 1 <= creature.phase <= 3:
                    raise ValueError(
                        f"save creature {creature.instance_id!r} has invalid phase"
                    )
                if type(creature.exchange_count) is not int or creature.exchange_count < 0:
                    raise ValueError(
                        f"save creature {creature.instance_id!r} has invalid exchange count"
                    )
                if definition.id != "sol_confrontation" and (
                    creature.phase != 1 or creature.exchange_count != 0
                ):
                    raise ValueError(
                        f"save creature {creature.instance_id!r} contains unsupported boss state"
                    )
        if len(live_creature_ids) != len(set(live_creature_ids)):
            raise ValueError("save contains duplicate creature instance IDs")
        live_creature_set = set(live_creature_ids)
        overlap = live_creature_set & state.defeated_creatures
        if overlap:
            raise ValueError(
                f"save marks live creatures as defeated: {sorted(overlap)}"
            )
        if any(not instance_id for instance_id in state.defeated_creatures):
            raise ValueError("save contains an empty defeated creature ID")
        if state.target_id is not None:
            current_room_ids = {
                creature.instance_id
                for creature in state.creatures.get(character.room_id, [])
            }
            if state.target_id not in current_room_ids:
                raise ValueError(
                    f"save target {state.target_id!r} is not live in the current room"
                )
        if (state.last_reference_kind is None) != (state.last_reference_id is None):
            raise ValueError("save has an incomplete pronoun reference")
        if state.last_reference_kind not in {None, "item", "creature"}:
            raise ValueError("save has an invalid pronoun reference kind")
        if state.last_reference_kind == "item":
            if state.last_reference_id not in set(all_instance_ids):
                raise ValueError("save pronoun references an unknown item")
        elif state.last_reference_kind == "creature":
            if state.last_reference_id not in live_creature_set:
                raise ValueError("save pronoun references an unknown creature")
        if state.last_action is not None:
            spec = self.parser.spec_for(state.last_action.command)
            if spec is None or state.last_action.command in _HISTORY_EXCLUDED:
                raise ValueError("save contains an unsupported command-history action")
        if state.queued_action is not None:
            queued = state.queued_action
            spec = self.parser.spec_for(queued.intent.command)
            if spec is None or spec.recovery is not RecoveryClass.HARD:
                raise ValueError("save contains a non-hard queued action")
            queued_parsed = self._parsed_from_intent(queued.intent)
            if not self._effective_hard(queued_parsed):
                raise ValueError("save queues a read-only command query")
            if queued.execute_at < self._hard_ready_at(state):
                raise ValueError("save queues an action before hard recovery ends")

    def _beginner_level_ceiling(self, state: GameState) -> int:
        """Return the highest authored foundation level currently unlocked.

        Existing saves are never downgraded.  Fresh sessions settle banked field
        insight only at authored quest checkpoints so the difficulty curve has a
        stable story cadence instead of an accidental late jump.
        """

        definition = self.catalog.beginner_experience
        unlocked = 1
        for quest_id, level in definition.difficulty_curve.level_checkpoints.items():
            if quest_id in state.story.completed_quests:
                unlocked = max(unlocked, level)
        return max(state.character.level, min(definition.target_level, unlocked))


    def _journeyman_started(self, state: GameState) -> bool:
        definition = self.catalog.journeyman_experience
        phase_quests = set(definition.difficulty_curve.level_checkpoints)
        return bool(
            state.story.active_quest_id in phase_quests
            or phase_quests & state.story.completed_quests
            or "journey_11_20_active" in state.flags
            or "journey_11_20_complete" in state.flags
        )

    def _journeyman_level_cap_active(self, state: GameState) -> bool:
        """Keep post-foundation insight banked until each level 11-20 checkpoint."""

        return bool(
            "price_of_second_life" in state.story.completed_quests
            and "the_second_horizon" not in state.story.completed_quests
            and state.character.build.status in {"pending", "confirmed", "legacy_preserved"}
        )

    def _journeyman_level_ceiling(self, state: GameState) -> int:
        definition = self.catalog.journeyman_experience
        starting_level = min(band.minimum_level for band in definition.difficulty_curve.bands) - 1
        unlocked = starting_level
        for quest_id, level in definition.difficulty_curve.level_checkpoints.items():
            if quest_id in state.story.completed_quests:
                unlocked = max(unlocked, level)
        return max(state.character.level, min(definition.target_level, unlocked))

    def _authored_progression_ceiling(self, state: GameState) -> int | None:
        if self._beginner_level_cap_active(state):
            return self._beginner_level_ceiling(state)
        if self._journeyman_level_cap_active(state):
            return self._journeyman_level_ceiling(state)
        return None

    def _difficulty_experience_for_room(
        self, state: GameState
    ) -> BeginnerExperienceDefinition | None:
        room_id = state.character.room_id
        if (
            self._beginner_level_cap_active(state)
            and room_id in set(self.catalog.beginner_experience.starter_room_ids)
        ):
            return self.catalog.beginner_experience
        if (
            self._journeyman_level_cap_active(state)
            and room_id in set(self.catalog.journeyman_experience.starter_room_ids)
        ):
            return self.catalog.journeyman_experience
        return None

    @staticmethod
    def _difficulty_band_for_definition(
        state: GameState,
        definition: BeginnerExperienceDefinition,
    ):
        minimum = min(band.minimum_level for band in definition.difficulty_curve.bands)
        maximum = max(band.maximum_level for band in definition.difficulty_curve.bands)
        level = max(minimum, min(state.character.level, maximum))
        for band in definition.difficulty_curve.bands:
            if band.minimum_level <= level <= band.maximum_level:
                return band
        raise RuntimeError(f"no difficulty band covers level {level}")

    def _difficulty_curve_active_in_room(self, state: GameState) -> bool:
        return self._difficulty_experience_for_room(state) is not None

    def _beginner_difficulty_band(self, state: GameState):
        definition = (
            self._difficulty_experience_for_room(state)
            or (
                self.catalog.journeyman_experience
                if self._journeyman_started(state)
                and "the_second_horizon" not in state.story.completed_quests
                else self.catalog.beginner_experience
            )
        )
        return self._difficulty_band_for_definition(state, definition)

    def _foundation_injury_wound(self, state: GameState) -> Wound | None:
        location = self.catalog.beginner_experience.difficulty_curve.injury.location
        return next(
            (wound for wound in state.character.wounds if wound.location == location),
            None,
        )

    def _foundation_injury_should_be_active(self, state: GameState) -> bool:
        injury = self.catalog.beginner_experience.difficulty_curve.injury
        trigger_quest = next(
            (
                quest_id
                for quest_id, level in self.catalog.beginner_experience.difficulty_curve.level_checkpoints.items()
                if level == injury.trigger_level
            ),
            None,
        )
        return bool(
            trigger_quest is not None
            and trigger_quest in state.story.completed_quests
            and injury.trigger_level <= state.character.level < injury.clear_level
            and "price_of_second_life" not in state.story.completed_quests
        )

    def _sync_foundation_injury(
        self,
        state: GameState,
        now: float,
        *,
        onset: bool = False,
        checkpoint: bool = False,
    ) -> tuple[tuple[str, ...], tuple[DomainEvent, ...]]:
        """Reconcile the authored level 5-8 injury without changing save schema."""

        injury = self.catalog.beginner_experience.difficulty_curve.injury
        wound = self._foundation_injury_wound(state)
        should_be_active = self._foundation_injury_should_be_active(state)
        lines: list[str] = []
        events: list[DomainEvent] = []

        if should_be_active:
            authored_severity = injury.severity_by_level[state.character.level]
            newly_applied = wound is None
            if wound is None:
                wound = Wound(
                    location=injury.location,
                    severity=authored_severity,
                    bleeding=1 if onset else 0,
                )
                state.character.wounds.append(wound)
            else:
                # Recovery earned through STABILIZE is never undone by a later
                # reconciliation.  Authored level milestones may only improve it.
                wound.severity = min(wound.severity, authored_severity)
                if checkpoint:
                    wound.bleeding = 0

            state.flags.add("foundation_injury_active")
            state.flags.discard("foundation_injury_rehabilitated")
            if onset and "foundation_injury_onset_recorded" not in state.flags:
                state.flags.add("foundation_injury_onset_recorded")
                before_health = state.character.health
                onset_health = max(
                    1,
                    math.ceil(
                        state.character.max_health * injury.onset_health_percent / 100
                    ),
                )
                state.character.health = min(state.character.health, onset_health)
                # The injury begins now.  It must not retroactively bleed across
                # every prior tutorial pulse accumulated before the onset scene.
                state.character.condition_pulse_at = now
                if "foundation_injury_kit_granted" not in state.flags:
                    kit = self._spawn_item(state, injury.recovery_item_id)
                    state.character.inventory.append(kit)
                    state.flags.add("foundation_injury_kit_granted")
                    kit_name = self.catalog.items[injury.recovery_item_id].name
                else:
                    kit_name = None
                lines.extend(
                    (
                        f"[Difficulty shock] {injury.onset_text}",
                        f"[Injury] {injury.label}: severity {wound.severity}; bleeding {wound.bleeding}.",
                        injury.recovery_text,
                        "Use INJURY for exact effects; HEALTH, STABILIZE RIBS, REST, "
                        "DEFENSE, and COMPANION ORDER GUARD are all valid recovery tools.",
                    )
                )
                if kit_name is not None:
                    lines.append(
                        f"Sol presses a {kit_name} into your hand. It is recovery support, not an automatic cure."
                    )
                events.append(
                    DomainEvent(
                        "condition.foundation_injury_applied",
                        {
                            "injury_id": injury.id,
                            "level": state.character.level,
                            "severity": wound.severity,
                            "bleeding": wound.bleeding,
                            "health_before": before_health,
                            "health_after": state.character.health,
                            "recovery_item_granted": kit_name is not None,
                        },
                    )
                )
            elif newly_applied:
                lines.append(
                    f"[Continuity] {injury.label} is restored at severity {wound.severity}; "
                    "the prior build did not persist this authored survival state."
                )
                events.append(
                    DomainEvent(
                        "condition.foundation_injury_reconciled",
                        {
                            "injury_id": injury.id,
                            "level": state.character.level,
                            "severity": wound.severity,
                        },
                    )
                )
            if checkpoint and not onset:
                before_health = state.character.health
                recovery_floor = max(
                    1,
                    math.ceil(
                        state.character.max_health
                        * injury.checkpoint_health_percent
                        / 100
                    ),
                )
                state.character.health = max(state.character.health, recovery_floor)
                state.character.condition_pulse_at = now
                state.flags.add(f"foundation_injury_checkpoint:{state.character.level}")
                lines.append(
                    f"[Recovery milestone] The reconstruction-line injury settles to "
                    f"severity {wound.severity}. Bleeding is controlled and field support "
                    f"restores you to at least {injury.checkpoint_health_percent}% integrity."
                )
                events.append(
                    DomainEvent(
                        "condition.foundation_injury_improved",
                        {
                            "injury_id": injury.id,
                            "level": state.character.level,
                            "severity": wound.severity,
                            "health_before": before_health,
                            "health_after": state.character.health,
                        },
                    )
                )
            return tuple(lines), tuple(events)

        trigger_quest = next(
            (
                quest_id
                for quest_id, level in self.catalog.beginner_experience.difficulty_curve.level_checkpoints.items()
                if level == injury.trigger_level
            ),
            None,
        )
        injury_was_reached = bool(
            trigger_quest is not None
            and trigger_quest in state.story.completed_quests
        )
        if state.character.level >= injury.clear_level and injury_was_reached:
            if wound is not None:
                state.character.wounds.remove(wound)
            state.flags.discard("foundation_injury_active")
            if "foundation_injury_rehabilitated" not in state.flags:
                state.flags.add("foundation_injury_rehabilitated")
                before_health = state.character.health
                rehabilitation_floor = max(
                    1,
                    math.ceil(
                        state.character.max_health
                        * injury.rehabilitation_health_percent
                        / 100
                    ),
                )
                state.character.health = max(state.character.health, rehabilitation_floor)
                state.character.condition_pulse_at = now
                lines.extend(
                    (
                        f"[Rehabilitation] {injury.label} is no longer an active combat wound.",
                        "Levels 9-10 return to the authored average pressure baseline; the scar remains part of the story, not a permanent stat tax.",
                    )
                )
                events.append(
                    DomainEvent(
                        "condition.foundation_injury_rehabilitated",
                        {
                            "injury_id": injury.id,
                            "level": state.character.level,
                            "health_before": before_health,
                            "health_after": state.character.health,
                        },
                    )
                )
        elif not should_be_active:
            state.flags.discard("foundation_injury_active")
        return tuple(lines), tuple(events)


    def _journey_injury_wound(self, state: GameState) -> Wound | None:
        location = self.catalog.journeyman_experience.difficulty_curve.injury.location
        return next(
            (wound for wound in state.character.wounds if wound.location == location),
            None,
        )

    def _journey_injury_should_be_active(self, state: GameState) -> bool:
        injury = self.catalog.journeyman_experience.difficulty_curve.injury
        trigger_quest = next(
            (
                quest_id
                for quest_id, level in self.catalog.journeyman_experience.difficulty_curve.level_checkpoints.items()
                if level == injury.trigger_level
            ),
            None,
        )
        return bool(
            trigger_quest is not None
            and trigger_quest in state.story.completed_quests
            and injury.trigger_level <= state.character.level < injury.clear_level
            and "the_second_horizon" not in state.story.completed_quests
        )

    def _sync_journey_injury(
        self,
        state: GameState,
        now: float,
        *,
        onset: bool = False,
        checkpoint: bool = False,
    ) -> tuple[tuple[str, ...], tuple[DomainEvent, ...]]:
        """Reconcile the authored level 15-18 sensory echo without schema drift."""

        injury = self.catalog.journeyman_experience.difficulty_curve.injury
        wound = self._journey_injury_wound(state)
        should_be_active = self._journey_injury_should_be_active(state)
        lines: list[str] = []
        events: list[DomainEvent] = []

        if should_be_active:
            authored_severity = injury.severity_by_level[state.character.level]
            newly_applied = wound is None
            if wound is None:
                wound = Wound(
                    location=injury.location,
                    severity=authored_severity,
                    bleeding=0,
                )
                state.character.wounds.append(wound)
            else:
                wound.severity = min(wound.severity, authored_severity)
                wound.bleeding = 0

            state.flags.add("journey_injury_active")
            state.flags.add("sensorium_echo_active")
            state.flags.discard("journey_injury_rehabilitated")
            if onset and "journey_injury_onset_recorded" not in state.flags:
                state.flags.add("journey_injury_onset_recorded")
                before_health = state.character.health
                onset_health = max(
                    1,
                    math.ceil(
                        state.character.max_health * injury.onset_health_percent / 100
                    ),
                )
                state.character.health = min(state.character.health, onset_health)
                state.character.condition_pulse_at = now
                item_name: str | None = None
                if "journey_injury_item_granted" not in state.flags:
                    support_item = self._spawn_item(state, injury.recovery_item_id)
                    state.character.inventory.append(support_item)
                    state.flags.add("journey_injury_item_granted")
                    item_name = self.catalog.items[injury.recovery_item_id].name
                lines.extend(
                    (
                        f"[Difficulty shock] {injury.onset_text}",
                        f"[Condition] {injury.label}: severity {wound.severity}; no active bleeding.",
                        injury.recovery_text,
                        "Use INJURY for exact effects; HEALTH, STABILIZE SENSORIUM, REST, "
                        "DEFENSE, WITHDRAW STATUS, and COMPANION ORDER GUARD remain valid.",
                    )
                )
                if item_name is not None:
                    lines.append(
                        f"Sol fits a {item_name} around your wrist. It provides local reference points; it does not diagnose or explain the echo."
                    )
                events.append(
                    DomainEvent(
                        "condition.journey_injury_applied",
                        {
                            "injury_id": injury.id,
                            "level": state.character.level,
                            "severity": wound.severity,
                            "health_before": before_health,
                            "health_after": state.character.health,
                            "recovery_item_granted": item_name is not None,
                        },
                    )
                )
            elif newly_applied:
                lines.append(
                    f"[Continuity] {injury.label} is restored at severity {wound.severity}; "
                    "the prior save did not persist this authored phase state."
                )
                events.append(
                    DomainEvent(
                        "condition.journey_injury_reconciled",
                        {
                            "injury_id": injury.id,
                            "level": state.character.level,
                            "severity": wound.severity,
                        },
                    )
                )
            if checkpoint and not onset:
                before_health = state.character.health
                recovery_floor = max(
                    1,
                    math.ceil(
                        state.character.max_health
                        * injury.checkpoint_health_percent
                        / 100
                    ),
                )
                state.character.health = max(state.character.health, recovery_floor)
                state.character.condition_pulse_at = now
                state.flags.add(f"journey_injury_checkpoint:{state.character.level}")
                lines.append(
                    f"[Recovery milestone] The sensory echo settles to severity {wound.severity}. "
                    f"Field support restores you to at least {injury.checkpoint_health_percent}% integrity."
                )
                events.append(
                    DomainEvent(
                        "condition.journey_injury_improved",
                        {
                            "injury_id": injury.id,
                            "level": state.character.level,
                            "severity": wound.severity,
                            "health_before": before_health,
                            "health_after": state.character.health,
                        },
                    )
                )
            return tuple(lines), tuple(events)

        trigger_quest = next(
            (
                quest_id
                for quest_id, level in self.catalog.journeyman_experience.difficulty_curve.level_checkpoints.items()
                if level == injury.trigger_level
            ),
            None,
        )
        injury_was_reached = bool(
            trigger_quest is not None and trigger_quest in state.story.completed_quests
        )
        if state.character.level >= injury.clear_level and injury_was_reached:
            if wound is not None:
                state.character.wounds.remove(wound)
            state.flags.discard("journey_injury_active")
            state.flags.discard("sensorium_echo_active")
            if "journey_injury_rehabilitated" not in state.flags:
                state.flags.add("journey_injury_rehabilitated")
                state.flags.add("sensorium_echo_rehabilitated")
                before_health = state.character.health
                rehabilitation_floor = max(
                    1,
                    math.ceil(
                        state.character.max_health
                        * injury.rehabilitation_health_percent
                        / 100
                    ),
                )
                state.character.health = max(state.character.health, rehabilitation_floor)
                state.character.condition_pulse_at = now
                lines.extend(
                    (
                        f"[Rehabilitation] {injury.label} is no longer an active combat condition.",
                        "The five anchors remain available, while the cause of the echo stays explicitly unresolved.",
                    )
                )
                events.append(
                    DomainEvent(
                        "condition.journey_injury_rehabilitated",
                        {
                            "injury_id": injury.id,
                            "level": state.character.level,
                            "health_before": before_health,
                            "health_after": state.character.health,
                        },
                    )
                )
        elif not should_be_active:
            state.flags.discard("journey_injury_active")
        return tuple(lines), tuple(events)

    def _settle_journey_checkpoint(
        self,
        state: GameState,
        quest_id: str,
        now: float,
    ) -> tuple[tuple[str, ...], tuple[DomainEvent, ...]]:
        definition = self.catalog.journeyman_experience
        target_level = definition.difficulty_curve.level_checkpoints.get(quest_id)
        if target_level is None or state.character.level >= target_level:
            return self._sync_journey_injury(state, now)

        experience = state.character.experience
        target_absorbed = (target_level - 1) * INSIGHT_PER_LEVEL
        capacity = max(0, target_absorbed - experience.absorbed)
        absorbed = min(experience.field_pool, capacity)
        if absorbed:
            experience.absorbed += absorbed
            experience.field_pool -= absorbed
        prior_level = state.character.level
        training_award = award_training_milestones(
            state.character, self.catalog.progression
        )
        lines: list[str] = []
        events: list[DomainEvent] = []
        if state.character.level < target_level:
            lines.append(
                f"[Progression held] This phase checkpoint targets level {target_level}, but only "
                f"{absorbed} banked insight was available. Continue earning field insight; "
                "the game will not fabricate a level."
            )
            events.append(
                DomainEvent(
                    "progress.journey_checkpoint_shortfall",
                    {
                        "quest_id": quest_id,
                        "target_level": target_level,
                        "level_after": state.character.level,
                        "absorbed": absorbed,
                    },
                )
            )
            return tuple(lines), tuple(events)

        band = self._difficulty_band_for_definition(state, definition)
        lines.append(
            f"[Journey checkpoint] {absorbed} earned field insight settles. "
            f"Level {prior_level} → {state.character.level}: {band.label}."
        )
        lines.append(f"[Difficulty] {band.summary}")
        events.append(
            DomainEvent(
                "progress.journey_checkpoint_settled",
                {
                    "quest_id": quest_id,
                    "absorbed": absorbed,
                    "level_before": prior_level,
                    "level_after": state.character.level,
                    "difficulty_band": band.id,
                },
            )
        )
        if training_award is not None:
            lines.append(
                f"[Progression] +{training_award.physical_points} physical and "
                f"+{training_award.mental_points} mental training points."
            )
            events.append(
                DomainEvent(
                    "progression.training_points_awarded",
                    {
                        "milestones": training_award.milestones,
                        "physical_points": training_award.physical_points,
                        "mental_points": training_award.mental_points,
                        "level_before": training_award.level_before,
                        "level_after": training_award.level_after,
                    },
                )
            )
        active_companion, active_progress = self._active_companion_context(state, now)
        if active_companion is not None and active_progress is not None:
            companion_before = active_progress.level
            self._ensure_companion_progress(state, active_companion, sync_level=True)
            if active_progress.level > companion_before:
                lines.append(
                    f"[Partner] {active_companion.name} advances to level {active_progress.level} beside you."
                )
                events.append(
                    DomainEvent(
                        "companion.level_changed",
                        {
                            "companion_id": active_companion.id,
                            "level_before": companion_before,
                            "level_after": active_progress.level,
                            "reason": f"journey checkpoint {quest_id}",
                        },
                    )
                )

        injury = definition.difficulty_curve.injury
        injury_lines, injury_events = self._sync_journey_injury(
            state,
            now,
            onset=state.character.level == injury.trigger_level,
            checkpoint=(
                injury.trigger_level < state.character.level < injury.clear_level
                or state.character.level >= injury.clear_level
            ),
        )
        lines.extend(injury_lines)
        events.extend(injury_events)
        return tuple(lines), tuple(events)

    def _settle_beginner_checkpoint(
        self,
        state: GameState,
        quest_id: str,
        now: float,
    ) -> tuple[tuple[str, ...], tuple[DomainEvent, ...]]:
        target_level = self.catalog.beginner_experience.difficulty_curve.level_checkpoints.get(
            quest_id
        )
        if target_level is None:
            journey_target = self.catalog.journeyman_experience.difficulty_curve.level_checkpoints.get(
                quest_id
            )
            if journey_target is not None:
                return self._settle_journey_checkpoint(state, quest_id, now)
            foundation_lines, foundation_events = self._sync_foundation_injury(
                state, now
            )
            journey_lines, journey_events = self._sync_journey_injury(state, now)
            return (
                foundation_lines + journey_lines,
                foundation_events + journey_events,
            )
        if state.character.level >= target_level:
            return self._sync_foundation_injury(state, now)

        experience = state.character.experience
        target_absorbed = (target_level - 1) * INSIGHT_PER_LEVEL
        capacity = max(0, target_absorbed - experience.absorbed)
        absorbed = min(experience.field_pool, capacity)
        if absorbed:
            experience.absorbed += absorbed
            experience.field_pool -= absorbed
        prior_level = state.character.level
        training_award = award_training_milestones(
            state.character, self.catalog.progression
        )
        lines: list[str] = []
        events: list[DomainEvent] = []
        if state.character.level < target_level:
            lines.append(
                f"[Progression held] This checkpoint targets level {target_level}, but only "
                f"{absorbed} banked insight was available. Continue earning field insight; "
                "the game will not fabricate a level."
            )
            events.append(
                DomainEvent(
                    "progress.foundation_checkpoint_shortfall",
                    {
                        "quest_id": quest_id,
                        "target_level": target_level,
                        "level_after": state.character.level,
                        "absorbed": absorbed,
                    },
                )
            )
            return tuple(lines), tuple(events)

        band = self._beginner_difficulty_band(state)
        lines.append(
            f"[Foundation checkpoint] {absorbed} earned field insight settles. "
            f"Level {prior_level} → {state.character.level}: {band.label}."
        )
        lines.append(f"[Difficulty] {band.summary}")
        events.append(
            DomainEvent(
                "progress.foundation_checkpoint_settled",
                {
                    "quest_id": quest_id,
                    "absorbed": absorbed,
                    "level_before": prior_level,
                    "level_after": state.character.level,
                    "difficulty_band": band.id,
                },
            )
        )
        if training_award is not None:
            lines.append(
                f"[Progression] +{training_award.physical_points} physical and "
                f"+{training_award.mental_points} mental training points."
            )
            events.append(
                DomainEvent(
                    "progression.training_points_awarded",
                    {
                        "milestones": training_award.milestones,
                        "physical_points": training_award.physical_points,
                        "mental_points": training_award.mental_points,
                        "level_before": training_award.level_before,
                        "level_after": training_award.level_after,
                    },
                )
            )
        active_companion, active_progress = self._active_companion_context(
            state, now
        )
        if active_companion is not None and active_progress is not None:
            companion_before = active_progress.level
            self._ensure_companion_progress(
                state, active_companion, sync_level=True
            )
            if active_progress.level > companion_before:
                lines.append(
                    f"[Partner] {active_companion.name} advances to level {active_progress.level} beside you."
                )
                events.append(
                    DomainEvent(
                        "companion.level_changed",
                        {
                            "companion_id": active_companion.id,
                            "level_before": companion_before,
                            "level_after": active_progress.level,
                            "reason": f"foundation checkpoint {quest_id}",
                        },
                    )
                )

        injury = self.catalog.beginner_experience.difficulty_curve.injury
        injury_lines, injury_events = self._sync_foundation_injury(
            state,
            now,
            onset=state.character.level == injury.trigger_level,
            checkpoint=(
                injury.trigger_level < state.character.level < injury.clear_level
                or state.character.level >= injury.clear_level
            ),
        )
        lines.extend(injury_lines)
        events.extend(injury_events)
        return tuple(lines), tuple(events)

    def _effective_beginner_creature_definition(
        self,
        state: GameState,
        definition: CreatureDefinition,
    ) -> CreatureDefinition:
        experience = self._difficulty_experience_for_room(state)
        if (
            experience is None
            or definition.nonlethal
            or definition.combat_role == "diagnostic"
        ):
            return definition
        band = self._difficulty_band_for_definition(state, experience)
        return replace(
            definition,
            offense=max(1, definition.offense + band.enemy_offense_modifier),
            defense=max(1, definition.defense + band.enemy_defense_modifier),
            armor=max(0, definition.armor + band.enemy_armor_modifier),
            damage_min=max(1, definition.damage_min + band.enemy_damage_min_modifier),
            damage_max=max(
                1,
                definition.damage_max + band.enemy_damage_max_modifier,
            ),
        )

    def _beginner_difficulty_projection(self, state: GameState) -> dict[str, object]:
        journey_mode = bool(
            self._journeyman_started(state)
            or state.character.room_id
            in set(self.catalog.journeyman_experience.starter_room_ids)
        )
        experience = (
            self.catalog.journeyman_experience
            if journey_mode
            else self.catalog.beginner_experience
        )
        definition = experience.difficulty_curve
        band = self._difficulty_band_for_definition(state, experience)
        injury = definition.injury
        if journey_mode:
            wound = self._journey_injury_wound(state)
            active = self._journey_injury_should_be_active(state) and wound is not None
            braced = "journey_injury_braced" in state.flags
            rehabilitated = "journey_injury_rehabilitated" in state.flags
            level_ceiling = self._journeyman_level_ceiling(state)
            cadence = [
                {"levels": "11-14", "label": "Easy / guided re-entry"},
                {"levels": "15-18", "label": "Shock / punishing with recovery"},
                {"levels": "19-20", "label": "Average / stabilized"},
            ]
        else:
            wound = self._foundation_injury_wound(state)
            active = self._foundation_injury_should_be_active(state) and wound is not None
            braced = "foundation_injury_braced" in state.flags
            rehabilitated = "foundation_injury_rehabilitated" in state.flags
            level_ceiling = self._beginner_level_ceiling(state)
            cadence = [
                {"levels": "1-4", "label": "Easy / guided"},
                {"levels": "5-8", "label": "Shock / punishing with recovery"},
                {"levels": "9-10", "label": "Average / stabilized"},
            ]
        return {
            "phase_id": experience.id,
            "phase_title": experience.title,
            "band_id": band.id,
            "label": band.label,
            "summary": band.summary,
            "level_range": [band.minimum_level, band.maximum_level],
            "cadence": cadence,
            "modifiers": {
                "enemy_offense": band.enemy_offense_modifier,
                "enemy_defense": band.enemy_defense_modifier,
                "enemy_armor": band.enemy_armor_modifier,
                "enemy_damage_min": band.enemy_damage_min_modifier,
                "enemy_damage_max": band.enemy_damage_max_modifier,
                "player_roundtime": band.player_roundtime_modifier,
            },
            "level_ceiling": level_ceiling,
            "injury": {
                "id": injury.id,
                "label": injury.label,
                "summary": injury.summary,
                "active": active,
                "severity": wound.severity if active else 0,
                "bleeding": wound.bleeding if active else 0,
                "trigger_level": injury.trigger_level,
                "clear_level": injury.clear_level,
                "braced": braced,
                "rehabilitated": rehabilitated,
                "recovery": injury.recovery_text,
            },
        }

    def welcome(self, state: GameState) -> str:
        if state.character.build.status == "pending":
            return "\n".join(
                (
                    "BETA EARTH: SOVEREIGNTY NEXT",
                    f"Welcome, {state.character.name}.",
                    "",
                    "Before the field link opens, establish your character foundation.",
                    "Choose a class, use recommended or manual equal-budget stats, "
                    "and decide whether Guided Start should accompany you.",
                    "Use BUILD for the text workflow or complete the guided HUD setup.",
                )
            )
        return "\n".join(
            (
                "BETA EARTH: SOVEREIGNTY NEXT",
                f"Welcome, {state.character.name}. Type HELP when you need a hand.",
                "",
                self.render_room(state),
            )
        )

    def execute(self, state: GameState, raw: str) -> CommandResult:
        now = self.clock.now()
        foundation_lines, foundation_events = self._sync_foundation_injury(
            state, now
        )
        journey_lines, journey_events = self._sync_journey_injury(state, now)
        continuity_lines = foundation_lines + journey_lines
        continuity_events = foundation_events + journey_events
        pulse_events: tuple[DomainEvent, ...] = continuity_events
        prefix: tuple[str, ...] = continuity_lines
        mutations = 1 if continuity_events else 0
        absorbed = pulse_experience(state.character.experience, now)
        authored_ceiling = self._authored_progression_ceiling(state)
        if authored_ceiling is not None:
            maximum_absorbed = (authored_ceiling - 1) * INSIGHT_PER_LEVEL
            if state.character.experience.absorbed > maximum_absorbed:
                overflow = state.character.experience.absorbed - maximum_absorbed
                state.character.experience.absorbed = maximum_absorbed
                state.character.experience.field_pool += overflow
                absorbed = max(0, absorbed - overflow)
        if absorbed:
            prefix += (f"A quiet connection settles into place. You absorb {absorbed} insight.",)
            pulse_events += (
                DomainEvent("experience.absorbed", {"amount": absorbed}),
            )
            mutations += 1

        training_award = award_training_milestones(
            state.character,
            self.catalog.progression,
        )
        if training_award is not None:
            prefix += (
                f"Learned insight reaches {training_award.milestones} new "
                f"{'milestone' if training_award.milestones == 1 else 'milestones'}. "
                f"You gain {training_award.physical_points} physical and "
                f"{training_award.mental_points} mental training points; "
                f"level rises to {training_award.level_after}.",
            )
            pulse_events += (
                DomainEvent(
                    "progression.training_points_awarded",
                    {
                        "milestones": training_award.milestones,
                        "physical_points": training_award.physical_points,
                        "mental_points": training_award.mental_points,
                        "level_before": training_award.level_before,
                        "level_after": training_award.level_after,
                    },
                ),
            )
            mutations += 1

            foundation_injury_lines, foundation_injury_events = self._sync_foundation_injury(
                state, now
            )
            journey_injury_lines, journey_injury_events = self._sync_journey_injury(
                state, now
            )
            injury_lines = foundation_injury_lines + journey_injury_lines
            injury_events = foundation_injury_events + journey_injury_events
            if injury_lines:
                prefix += injury_lines
            if injury_events:
                pulse_events += injury_events
                mutations += 1

        active_companion, _active_progress = self._active_companion_context(
            state,
            now,
            recover_if_ready=True,
        )
        if active_companion is not None and training_award is not None:
            companion_before_level = _active_progress.level if _active_progress is not None else 1
            companion_progress = self._ensure_companion_progress(
                state,
                active_companion,
                sync_level=True,
            )
            if companion_progress.level > companion_before_level:
                prefix += (
                    f"[Partner level] {active_companion.name} keeps pace at level "
                    f"{companion_progress.level}; integrity expands to "
                    f"{companion_progress.max_health}.",
                )
                pulse_events += (
                    DomainEvent(
                        "companion.level_gained",
                        {
                            "companion_id": active_companion.id,
                            "level_before": companion_before_level,
                            "level_after": companion_progress.level,
                            "max_health": companion_progress.max_health,
                            "reason": "player progression sync",
                        },
                    ),
                )
                mutations += 1

        bleeding = (
            pulse_bleeding(state.character, now)
            if state.incapacitation is None
            else None
        )
        if bleeding is not None and bleeding.checkpoint_changed:
            mutations += 1
        if bleeding is not None and bleeding.damage:
            prefix += (
                f"Your untreated wounds bleed for {bleeding.damage} damage "
                f"across {bleeding.pulses} recovery "
                f"{'pulse' if bleeding.pulses == 1 else 'pulses'}.",
            )
            pulse_events += (
                DomainEvent(
                    "condition.bleeding_pulse",
                    {
                        "pulses": bleeding.pulses,
                        "rate": bleeding.rate,
                        "damage": bleeding.damage,
                    },
                ),
            )
            if state.character.health <= 0:
                recovery_lines: list[str] = []
                recovery_events = self._incapacitate(
                    state,
                    now,
                    recovery_lines,
                    cause="untreated bleeding",
                )
                state.turn += mutations
                return CommandResult(
                    lines=prefix + tuple(recovery_lines),
                    events=pulse_events + tuple(recovery_events),
                    changed=True,
                )

        rest = (
            pulse_rest(state.character, now)
            if state.incapacitation is None
            else None
        )
        if rest is not None and rest.checkpoint_changed:
            mutations += 1
        if rest is not None and rest.healed:
            prefix += (
                f"Rest restores {rest.healed} health across {rest.pulses} "
                f"{'pulse' if rest.pulses == 1 else 'pulses'}.",
            )
            pulse_events += (
                DomainEvent(
                    "recovery.rest_pulse",
                    {"pulses": rest.pulses, "health_restored": rest.healed},
                ),
            )

        parse_error: CommandParseError | None = None
        try:
            parsed = self.parser.parse(raw)
        except CommandParseError as exc:
            parsed = None
            parse_error = exc

        # CANCEL is the explicit poison-action escape and wins at the same
        # dispatch boundary that would otherwise run an eligible queued intent.
        scheduled = (
            None
            if parsed is not None and parsed.name == "cancel"
            else self._run_due_queue(state, now)
        )
        if scheduled is not None:
            prefix += scheduled.lines
            pulse_events += scheduled.events
            if scheduled.changed:
                mutations += 1
            scheduled_story = self._apply_story_progress(
                state,
                scheduled.events,
            )
            prefix += scheduled_story.lines
            pulse_events += scheduled_story.events
            if scheduled_story.changed:
                mutations += 1
            scheduled_course = self._apply_course_progress(
                state,
                scheduled.events,
            )
            prefix += scheduled_course.lines
            pulse_events += scheduled_course.events
            if scheduled_course.changed:
                mutations += 1
            scheduled_tutorial = self._apply_tutorial_progress(
                state,
                scheduled.events,
            )
            prefix += scheduled_tutorial.lines
            pulse_events += scheduled_tutorial.events
            if scheduled_tutorial.changed:
                mutations += 1
            scheduled_foundations = self._sync_active_foundations(
                state,
                scheduled.events
                + scheduled_story.events
                + scheduled_course.events
                + scheduled_tutorial.events,
            )
            prefix += scheduled_foundations.lines
            pulse_events += scheduled_foundations.events
            if scheduled_foundations.changed:
                mutations += 1
        if parse_error is not None:
            if mutations:
                state.turn += mutations
            return CommandResult(
                lines=prefix + (str(parse_error),),
                events=pulse_events,
                changed=bool(mutations),
            )
        assert parsed is not None

        if (
            state.character.build.status == "pending"
            and parsed.name not in _PENDING_BUILD_COMMANDS
        ):
            if mutations:
                state.turn += mutations
            return CommandResult(
                lines=prefix
                + (
                    "Finish your character foundation before entering the Sprawl.",
                    "Use BUILD for status, BUILD CLASS for choices, or the guided HUD setup.",
                ),
                events=pulse_events,
                changed=bool(mutations),
            )

        if (
            state.incapacitation is not None
            and parsed.name not in _INCAPACITATED_COMMANDS
        ):
            if mutations:
                state.turn += mutations
            remaining = max(
                0,
                math.ceil(state.incapacitation.recover_at - now),
            )
            return CommandResult(
                lines=prefix
                + (
                    "You are incapacitated and cannot perform that action.",
                    (
                        f"RECOVER becomes available in {remaining} sec.; "
                        "SIGNAL persists a request for future rescue services."
                        if remaining
                        else "RECOVER is available now; SIGNAL can still request assistance."
                    ),
                ),
                events=pulse_events,
                changed=bool(mutations),
            )

        hard = self._effective_hard(parsed)
        remaining = self._hard_recovery_remaining(state, now)
        if hard and remaining:
            if mutations:
                state.turn += mutations
            return CommandResult(
                lines=prefix
                + (
                    f"You are still recovering from your last action ({remaining} sec).",
                    "Use QUEUE <action> to schedule one hard action, or keep using soft commands.",
                ),
                events=pulse_events,
                changed=bool(mutations),
            )
        if hard and state.character.prone and parsed.name != "stand":
            if mutations:
                state.turn += mutations
            return CommandResult(
                lines=prefix
                + (
                    "You are prone. STAND before attempting another hard action.",
                ),
                events=pulse_events,
                changed=bool(mutations),
            )
        if (
            hard
            and state.character.resting
            and parsed.name not in {"rest", "stand"}
        ):
            state.character.resting = False
            prefix += ("You leave your resting posture to act.",)
            pulse_events += (DomainEvent("recovery.rest_ended"),)
            mutations += 1
        handled = self._run_parsed_with_battlefield(state, parsed, now)
        story_progress = self._apply_story_progress(state, handled.events)
        course_progress = self._apply_course_progress(state, handled.events)
        tutorial_progress = self._apply_tutorial_progress(
            state,
            handled.events,
        )
        foundation_progress = (
            self._sync_active_foundations(
                state,
                handled.events
                + story_progress.events
                + course_progress.events
                + tutorial_progress.events,
            )
            if (
                handled.changed
                or story_progress.changed
                or course_progress.changed
                or tutorial_progress.changed
            )
            else _HandlerResult(())
        )
        if handled.changed:
            mutations += 1
        if story_progress.changed:
            mutations += 1
        if course_progress.changed:
            mutations += 1
        if tutorial_progress.changed:
            mutations += 1
        if foundation_progress.changed:
            mutations += 1
        changed = bool(mutations)
        if mutations:
            state.turn += mutations
        return CommandResult(
            lines=(
                prefix
                + handled.lines
                + story_progress.lines
                + course_progress.lines
                + tutorial_progress.lines
                + foundation_progress.lines
            ),
            events=(
                pulse_events
                + handled.events
                + story_progress.events
                + course_progress.events
                + tutorial_progress.events
                + foundation_progress.events
            ),
            changed=changed,
            quit=handled.quit,
        )

    def _remember_action(self, state: GameState, parsed: ParsedCommand) -> None:
        if parsed.name not in _HISTORY_EXCLUDED:
            state.last_action = ActionIntent(parsed.name, parsed.args)

    @staticmethod
    def _effective_hard(parsed: ParsedCommand) -> bool:
        course_query = (
            parsed.name == "course"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"list", "status"}
            )
        )
        companion_query = (
            parsed.name == "companion"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "advise", "advice", "hint"}
                or (
                    parsed.args[0].casefold() == "sync"
                    and len(parsed.args) > 1
                    and parsed.args[1].casefold() in {"status", "list"}
                )
            )
        )
        party_query = (
            parsed.name == "party"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "roster"}
            )
        )
        territory_query = (
            parsed.name == "territory"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "summary"}
            )
        )
        faction_query = (
            parsed.name == "faction"
            and (
                not parsed.args
                or parsed.args[0].casefold() not in {
                    "pledge", "join", "allege", "y", "yes", "confirm",
                    "n", "no", "cancel",
                }
            )
        )
        civic_query = (
            parsed.name == "civic"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "summary"}
            )
        )
        report_query = (
            parsed.name == "report"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "summary"}
            )
        )
        district_query = (
            parsed.name == "district"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "summary"}
            )
        )
        service_query = (
            parsed.name == "service"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "summary"}
            )
        )
        hospice_query = (
            parsed.name == "hospice"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "summary"}
            )
        )
        appeal_query = (
            parsed.name == "appeal"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "summary"}
            )
        )
        wayfinding_query = (
            parsed.name == "wayfinding"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list", "summary"}
            )
        )
        playtest_query = (
            parsed.name == "playtest"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "receipt"}
            )
        )
        withdrawal_query = (
            parsed.name == "withdraw"
            and bool(parsed.args)
            and parsed.args[0].casefold() in {"status", "odds", "plan"}
        )
        return parsed.hard and not (
            parsed.name in {"stance", "defense", "train", "retrain", "path"}
            and not parsed.args
        ) and not (
            course_query
            or companion_query
            or party_query
            or faction_query
            or territory_query
            or civic_query
            or report_query
            or district_query
            or service_query
            or hospice_query
            or appeal_query
            or wayfinding_query
            or playtest_query
            or withdrawal_query
        )

    def _parsed_from_intent(self, intent: ActionIntent) -> ParsedCommand:
        spec = self.parser.spec_for(intent.command)
        if spec is None:
            raise ValueError(f"saved action command {intent.command!r} is unavailable")
        return ParsedCommand(
            name=intent.command,
            args=intent.args,
            raw="",
            recovery=spec.recovery,
        )

    def _run_parsed_with_battlefield(
        self,
        state: GameState,
        parsed: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        """Run one successful hard command through the shared action economy.

        Soft queries and rejected actions remain read-only. A valid ATTACK starts
        its encounter inside the handler so its first enemy intentions are visible
        before resolution; other hard actions synchronize immediately afterward.
        """

        origin_room_id = state.character.room_id
        handled = self._handlers[parsed.name](state, parsed, now)
        lines = list(handled.lines)
        events = list(handled.events)
        changed = handled.changed

        if handled.changed and self._effective_hard(parsed):
            if parsed.name != "attack":
                synchronized = self.combat_scheduler.synchronize(state, now)
                if state.character.room_id == origin_room_id:
                    lines = list(synchronized.lines) + lines
                    events = list(synchronized.events) + events
                else:
                    lines.extend(synchronized.lines)
                    events.extend(synchronized.events)
                changed = changed or synchronized.changed

            elapsed = max(
                1.0,
                float(state.character.roundtime_until - now),
            )
            advanced = self.combat_scheduler.advance(
                state,
                now,
                elapsed=elapsed,
                player_command=parsed.name,
                origin_room_id=origin_room_id,
            )
            lines.extend(advanced.lines)
            events.extend(advanced.events)
            changed = changed or advanced.changed

        if handled.changed:
            self._remember_action(state, parsed)
        return _HandlerResult(
            tuple(lines),
            tuple(events),
            changed,
            handled.quit,
        )

    def _run_intent(
        self,
        state: GameState,
        intent: ActionIntent,
        now: float,
        *,
        require_hard: bool = False,
    ) -> _HandlerResult:
        parsed = self._parsed_from_intent(intent)
        effective_hard = self._effective_hard(parsed)
        if require_hard and not effective_hard:
            raise ValueError("queued intent is no longer a hard action")
        if effective_hard and state.character.prone and parsed.name != "stand":
            return _HandlerResult(
                ("You are prone. STAND before attempting another hard action.",)
            )
        left_rest = (
            effective_hard
            and state.character.resting
            and parsed.name not in {"rest", "stand"}
        )
        if left_rest:
            state.character.resting = False
        handled = self._run_parsed_with_battlefield(state, parsed, now)
        if left_rest:
            handled = _HandlerResult(
                ("You leave your resting posture to act.",) + handled.lines,
                (DomainEvent("recovery.rest_ended"),) + handled.events,
                True,
                handled.quit,
            )
        return handled

    def _run_due_queue(
        self, state: GameState, now: float
    ) -> _HandlerResult | None:
        queued = state.queued_action
        if queued is None or now < queued.execute_at:
            return None
        state.queued_action = None
        try:
            handled = self._run_intent(
                state,
                queued.intent,
                now,
                require_hard=True,
            )
        except (KeyError, ValueError) as exc:
            return _HandlerResult(
                (
                    f"[Queue] The pending action was safely discarded: {exc}.",
                ),
                (
                    DomainEvent(
                        "action.queue_failed",
                        {
                            "command": queued.intent.command,
                            "reason": type(exc).__name__,
                        },
                    ),
                ),
                True,
            )
        return _HandlerResult(
            (f"[Queue] {queued.intent.command.upper()}",) + handled.lines,
            (
                DomainEvent(
                    "action.queue_executed",
                    {"command": queued.intent.command},
                ),
            )
            + handled.events,
            True,
            handled.quit,
        )

    def _room_description(self, state: GameState) -> str:
        room = self.catalog.rooms[state.character.room_id]
        overlays = [
            text
            for flag, text in room.story_overlays.items()
            if flag in state.flags
        ]
        return "\n\n".join((room.description, *overlays))

    def _exit_is_available(
        self, state: GameState, room_id: str, direction: str
    ) -> bool:
        room = self.catalog.rooms[room_id]
        required = room.exit_requirements.get(direction, ())
        return all(flag in state.flags for flag in required)

    def _exit_lock_reason(self, state: GameState, direction: str) -> str | None:
        room = self.catalog.rooms[state.character.room_id]
        required = room.exit_requirements.get(direction, ())
        missing = [flag for flag in required if flag not in state.flags]
        if not missing:
            return None
        return (
            f"The {direction} route is sealed by the current story state. "
            "Continue the active directive before crossing it."
        )

    def _available_exits(
        self, state: GameState, room_id: str | None = None
    ) -> list[tuple[str, str]]:
        active_room = room_id or state.character.room_id
        return [
            (direction, destination)
            for direction, destination in self.catalog.rooms[active_room].exits.items()
            if self._exit_is_available(state, active_room, direction)
        ]



    @staticmethod
    def _query(args: tuple[str, ...]) -> str:
        words = list(args)
        while words and words[0].casefold() in {"at", "the", "a", "an"}:
            words.pop(0)
        return " ".join(words).strip().casefold()




    def _live_creatures(self, state: GameState) -> list[CreatureState]:
        return [
            creature
            for creature in state.creatures.get(state.character.room_id, [])
            if creature.health > 0
        ]

    def _resolve_creature(
        self, state: GameState, query: str
    ) -> tuple[CreatureState | None, str | None]:
        candidates = self._live_creatures(state)
        selection = parse_selection(query)
        if selection.scope not in {Scope.DEFAULT, Scope.ROOM}:
            return None, "Creatures can only be selected in the current room."
        if selection.exclusion is not None:
            return None, "EXCEPT is only available for bounded item actions."
        if selection.all_matches:
            return None, "Choose one living target rather than ALL."
        if selection.pronoun:
            match = next(
                (
                    creature
                    for creature in candidates
                    if state.last_reference_kind == "creature"
                    and creature.instance_id == state.last_reference_id
                ),
                None,
            )
            if match:
                return match, None
            return None, "The creature pronoun has no living referent here."
        if not selection.terms and selection.relative is None:
            if state.target_id:
                match = next(
                    (item for item in candidates if item.instance_id == state.target_id),
                    None,
                )
                if match:
                    return match, None
            if len(candidates) == 1:
                return candidates[0], None
            if not candidates:
                return None, "There is nothing here to target."
            return None, "Choose a target first: TARGET <name>."
        scored: list[tuple[int, CreatureState]] = []
        for creature in candidates:
            definition = self.catalog.creatures[creature.definition_id]
            terms = {
                definition.name.casefold(),
                definition.id.casefold(),
                *(noun.casefold() for noun in definition.nouns),
            }
            if not selection.terms:
                score = 1
            elif selection.terms in terms:
                score = 3
            elif any(term.startswith(selection.terms) for term in terms):
                score = 2
            elif any(selection.terms in term for term in terms):
                score = 1
            else:
                continue
            scored.append((score, creature))
        if not scored:
            choices = [
                f"TARGET {self.catalog.creatures[creature.definition_id].nouns[0].upper()}"
                for creature in candidates
            ]
            suffix = (
                " Available targets: " + ", ".join(choices) + "."
                if choices
                else " There are no living targets here."
            )
            return None, (
                f"You cannot find a living target matching {selection.terms!r}." + suffix
            )
        best = max(score for score, _ in scored)
        matches = [creature for score, creature in scored if score == best]
        if selection.ordinal is not None:
            if selection.ordinal >= len(matches):
                return None, (
                    f"There are only {len(matches)} matching living targets here."
                )
            return matches[selection.ordinal], None
        if selection.relative is RelativeSelector.RANDOM:
            return self.rng.choice(tuple(matches)), None
        if selection.relative is not None:
            last_id = (
                state.last_reference_id
                if state.last_reference_kind == "creature"
                else state.target_id
            )
            if selection.relative is RelativeSelector.OTHER:
                other = next(
                    (
                        creature
                        for creature in matches
                        if creature.instance_id != last_id
                    ),
                    None,
                )
                if last_id is None or other is None:
                    return None, (
                        "OTHER needs a prior matching target and another choice."
                    )
                return other, None
            if last_id is None:
                return matches[0], None
            current = next(
                (
                    index
                    for index, creature in enumerate(matches)
                    if creature.instance_id == last_id
                ),
                None,
            )
            if current is None:
                return matches[0], None
            if len(matches) == 1:
                return None, "There is no next matching target."
            return matches[(current + 1) % len(matches)], None
        if len(matches) > 1:
            name = self.catalog.creatures[matches[0].definition_id].name
            return None, (
                f"There is more than one {name}; use an ordinal, OTHER, NEXT, or RANDOM."
            )
        return matches[0], None




    @staticmethod
    def _set_roundtime(state: GameState, now: float, seconds: int) -> None:
        state.character.roundtime_until = max(
            state.character.roundtime_until, now + seconds
        )

    @staticmethod
    def _hard_ready_at(state: GameState) -> float:
        character = state.character
        return max(character.roundtime_until, character.stunned_until)

    @classmethod
    def _hard_recovery_remaining(cls, state: GameState, now: float) -> int:
        return roundtime_remaining(cls._hard_ready_at(state), now)












    @staticmethod
    def _format_playtest_seconds(value: float) -> str:
        seconds = max(0, int(round(value)))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _playtest_profile_projection(self, state: GameState) -> dict[str, object]:
        telemetry = state.beginner_telemetry
        current_class_id = state.character.build.class_id
        inferred_family = family_for_class(current_class_id)
        family = telemetry.playtest_family or inferred_family
        profile_class_id = telemetry.playtest_class_id or current_class_id
        representative = (
            PLAYTEST_REPRESENTATIVE_CLASSES.get(family or "")
            if family is not None
            else None
        )
        class_definition = self.catalog.creation.classes.get(profile_class_id or "")
        return {
            "family": family,
            "inferred_family": inferred_family,
            "class_id": profile_class_id,
            "current_class_id": current_class_id,
            "class_name": class_definition.name if class_definition is not None else None,
            "representative_class_id": representative,
            "representative_class_name": (
                self.catalog.creation.classes[representative].name
                if representative in self.catalog.creation.classes
                else None
            ),
            "class_matches_family": bool(
                family
                and profile_class_id
                and profile_class_id in PLAYTEST_FAMILY_CLASSES[family]
            ),
            "representative_match": bool(
                representative
                and profile_class_id == representative
            ),
            "mode": telemetry.playtest_mode,
            "experience": telemetry.playtest_experience,
            "source": telemetry.playtest_profile_source,
            "assistive_tool": telemetry.playtest_assistive_tool,
        }

    def _playtest_projection(
        self,
        state: GameState,
        *,
        now: float | None = None,
    ) -> dict[str, object]:
        observed_at = self.clock.now() if now is None else now
        telemetry = state.beginner_telemetry
        active_seconds = telemetry.playtest_active_seconds
        idle_seconds = telemetry.playtest_idle_seconds
        paused_seconds = telemetry.playtest_paused_seconds
        if telemetry.playtest_status == "running":
            anchor = (
                telemetry.playtest_last_activity_at
                or telemetry.playtest_started_at
                or observed_at
            )
            delta = max(0.0, observed_at - anchor)
            active_seconds += min(delta, float(telemetry.playtest_idle_threshold_seconds))
            idle_seconds += max(0.0, delta - float(telemetry.playtest_idle_threshold_seconds))
        elif telemetry.playtest_status == "paused" and telemetry.playtest_pause_started_at:
            paused_seconds += max(0.0, observed_at - telemetry.playtest_pause_started_at)
        end_at = (
            telemetry.playtest_completed_at
            if telemetry.playtest_status == "completed" and telemetry.playtest_completed_at
            else observed_at
        )
        wall_seconds = (
            max(0.0, end_at - telemetry.playtest_started_at)
            if telemetry.playtest_started_at
            else 0.0
        )
        campaign = self._beginner_experience_projection(state)
        issue_counts = {
            severity: sum(
                issue.get("severity") == severity
                for issue in telemetry.playtest_issues
            )
            for severity in sorted(PLAYTEST_ISSUE_SEVERITIES)
        }
        survey_complete = PLAYTEST_SURVEY_FIELDS.issubset(telemetry.playtest_survey)
        return {
            "status": telemetry.playtest_status,
            "started_at": telemetry.playtest_started_at or None,
            "completed_at": telemetry.playtest_completed_at or None,
            "active_seconds": round(active_seconds, 3),
            "idle_seconds": round(idle_seconds, 3),
            "paused_seconds": round(paused_seconds, 3),
            "wall_seconds": round(wall_seconds, 3),
            "active_text": self._format_playtest_seconds(active_seconds),
            "idle_text": self._format_playtest_seconds(idle_seconds),
            "paused_text": self._format_playtest_seconds(paused_seconds),
            "wall_text": self._format_playtest_seconds(wall_seconds),
            "command_count": telemetry.playtest_command_count,
            "chapter_active_seconds": {
                key: round(value, 3)
                for key, value in sorted(telemetry.playtest_chapter_active_seconds.items())
            },
            "chapter_idle_seconds": {
                key: round(value, 3)
                for key, value in sorted(telemetry.playtest_chapter_idle_seconds.items())
            },
            "milestones": {
                key: round(value, 3)
                for key, value in sorted(telemetry.playtest_milestones.items())
            },
            "notes_count": len(telemetry.playtest_notes),
            "issues_count": len(telemetry.playtest_issues),
            "issue_counts": issue_counts,
            "blocking_issues": issue_counts.get("blocking", 0),
            "survey": dict(sorted(telemetry.playtest_survey.items())),
            "survey_fields": sorted(PLAYTEST_SURVEY_FIELDS),
            "survey_complete": survey_complete,
            "campaign_complete": bool(campaign["complete"]),
            "campaign_minutes_modeled": campaign["estimated_completed_minutes"],
            "target_minutes": campaign["target_minutes"],
            "session_id": telemetry.playtest_session_id,
            "idle_threshold_seconds": telemetry.playtest_idle_threshold_seconds,
            "profile": self._playtest_profile_projection(state),
            "local_only": True,
            "network_reporting": False,
            "reward_neutral": True,
            "receipt_command": "PLAYTEST RECEIPT",
            "cohort_report_command": "BetaEarthSovereignty.bat --playtest-report",
            "timing_basis": (
                "Local wall clock. Inter-command gaps up to "
                f"{telemetry.playtest_idle_threshold_seconds} seconds count as active; "
                "the remainder counts as idle. PLAYTEST PAUSE excludes intentional breaks."
            ),
        }

    @staticmethod
    def _new_playtest_session_id(state: GameState, now: float) -> str:
        seed = (
            f"{state.character.key}|{state.revision}|{state.turn}|"
            f"{now:.6f}|{state.beginner_telemetry.total_commands}"
        ).encode("utf-8")
        return hashlib.sha256(seed).hexdigest()[:16]

    def _parse_playtest_start_options(
        self,
        state: GameState,
        args: tuple[str, ...],
    ) -> tuple[dict[str, str | None] | None, str | None]:
        class_id = state.character.build.class_id
        inferred_family = family_for_class(class_id)
        if class_id is not None and inferred_family is None:
            return None, f"Class {class_id!r} is not mapped to a beginner gameplay family."
        family: str | None = None
        mode = "standard"
        experience = "unspecified"
        family_explicit = False
        index = 0
        tokens = [token.strip() for token in args if token.strip()]
        while index < len(tokens):
            token = tokens[index].casefold()
            if token in {"family", "mode", "experience"}:
                if index + 1 >= len(tokens):
                    return None, f"PLAYTEST START {token.upper()} requires a value."
                value = tokens[index + 1]
                index += 2
                if token == "family":
                    normalized = normalize_family(value)
                    if normalized is None:
                        return None, "Unknown playtest family. Use command, support, control, or damage."
                    family = normalized
                    family_explicit = True
                elif token == "mode":
                    normalized = normalize_mode(value)
                    if normalized is None:
                        return None, "Unknown playtest mode. Use standard, keyboard, screen-reader, or low-vision."
                    mode = normalized
                else:
                    normalized = normalize_experience(value)
                    if normalized is None:
                        return None, "Unknown experience value. Use first-time, returning, developer, or unspecified."
                    experience = normalized
                continue
            normalized_family = normalize_family(token)
            normalized_mode = normalize_mode(token)
            normalized_experience = normalize_experience(token)
            matches = [
                ("family", normalized_family),
                ("mode", normalized_mode),
                ("experience", normalized_experience),
            ]
            matches = [(kind, value) for kind, value in matches if value is not None]
            if len(matches) != 1:
                return None, (
                    f"Unrecognized PLAYTEST START option {tokens[index]!r}. "
                    "Use FAMILY, MODE, and EXPERIENCE labels."
                )
            kind, normalized = matches[0]
            assert normalized is not None
            if kind == "family":
                family = normalized
                family_explicit = True
            elif kind == "mode":
                mode = normalized
            else:
                experience = normalized
            index += 1
        family = family or inferred_family
        if (
            class_id is not None
            and family is not None
            and class_id not in PLAYTEST_FAMILY_CLASSES[family]
        ):
            return None, (
                f"The selected class {class_id} belongs to {inferred_family}, not {family}. "
                "The clock was not started."
            )
        return {
            "family": family,
            "class_id": class_id,
            "mode": mode,
            "experience": experience,
            "source": (
                "explicit"
                if family_explicit
                else "inferred"
                if class_id is not None
                else "pending_build"
            ),
        }, None

    def _reset_playtest_timer(
        self,
        state: GameState,
        now: float,
        *,
        profile: dict[str, str | None] | None = None,
        preserve_profile: bool = False,
    ) -> None:
        telemetry = state.beginner_telemetry
        if profile is not None:
            telemetry.playtest_family = profile.get("family")
            telemetry.playtest_class_id = profile.get("class_id")
            telemetry.playtest_mode = str(profile.get("mode") or "standard")
            telemetry.playtest_experience = str(
                profile.get("experience") or "unspecified"
            )
            telemetry.playtest_profile_source = str(
                profile.get("source") or "pending_build"
            )
        elif not preserve_profile:
            telemetry.playtest_family = None
            telemetry.playtest_class_id = None
            telemetry.playtest_mode = "standard"
            telemetry.playtest_experience = "unspecified"
            telemetry.playtest_profile_source = None
        telemetry.playtest_assistive_tool = None
        telemetry.playtest_issues.clear()
        telemetry.playtest_status = "running"
        telemetry.playtest_session_id = self._new_playtest_session_id(state, now)
        telemetry.playtest_idle_threshold_seconds = 180
        telemetry.playtest_started_at = now
        telemetry.playtest_last_activity_at = now
        telemetry.playtest_pause_started_at = 0.0
        telemetry.playtest_completed_at = 0.0
        telemetry.playtest_active_seconds = 0.0
        telemetry.playtest_idle_seconds = 0.0
        telemetry.playtest_paused_seconds = 0.0
        telemetry.playtest_command_count = 0
        telemetry.playtest_chapter_active_seconds.clear()
        telemetry.playtest_chapter_idle_seconds.clear()
        telemetry.playtest_milestones.clear()
        telemetry.playtest_notes.clear()
        telemetry.playtest_survey.clear()

    def _playtest_checklist_lines(self, state: GameState) -> list[str]:
        projection = self._playtest_profile_projection(state)
        mode = str(projection.get("mode") or "standard")
        family = projection.get("family") or "unassigned"
        representative = projection.get("representative_class_name") or "unassigned"
        lines = [
            "MEASURED BEGINNER PLAYTEST CHECKLIST",
            f"Family: {family} · recommended representative: {representative}",
            f"Mode: {mode.replace('_', ' ')}",
            "Before launch: run BetaEarthSovereignty.bat --self-test on the test computer.",
        ]
        lines.extend(
            f"  {index}. {item}"
            for index, item in enumerate(PLAYTEST_CHECKLISTS[mode], start=1)
        )
        lines.extend(
            (
                "Start template: PLAYTEST START FAMILY <family> MODE <mode> EXPERIENCE FIRST-TIME",
                "After completion: PLAYTEST RECEIPT",
                "Cohort report: BetaEarthSovereignty.bat --playtest-report",
                "Evidence ZIP: BetaEarthSovereignty.bat --export-playtests",
            )
        )
        return lines

    def _playtest(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        telemetry = state.beginner_telemetry
        query = command.args[0].casefold() if command.args else "status"
        if query in {"plan", "profile"}:
            profile = self._playtest_profile_projection(state)
            family = profile.get("family") or "unassigned"
            class_name = profile.get("class_name") or profile.get("class_id") or "unassigned"
            representative = profile.get("representative_class_name") or "unassigned"
            lines = [
                "MEASURED PLAYTEST PROFILE",
                f"Current class: {class_name}",
                f"Gameplay family: {family}",
                f"Recommended family representative: {representative}",
                f"Mode: {str(profile.get('mode') or 'standard').replace('_', ' ')}",
                f"Experience: {str(profile.get('experience') or 'unspecified').replace('_', ' ')}",
                f"Class matches family: {'yes' if profile.get('class_matches_family') else 'no'}",
                "The representative is a cohort recommendation, not a gameplay restriction.",
                "Start: PLAYTEST START FAMILY <family> MODE <mode> EXPERIENCE FIRST-TIME",
            ]
            return _HandlerResult(("\n".join(lines),))
        if query == "checklist":
            return _HandlerResult(("\n".join(self._playtest_checklist_lines(state)),))
        if query in {"issues", "issue-list"}:
            lines = [f"PLAYTEST ISSUES · {len(telemetry.playtest_issues)}/20"]
            if telemetry.playtest_issues:
                lines.extend(
                    f"  {index}. {issue['severity'].upper()} · {issue['category'].replace('_', ' ')} · {issue['note']}"
                    for index, issue in enumerate(telemetry.playtest_issues, start=1)
                )
            else:
                lines.append("No structured issues recorded.")
            lines.append("Record: PLAYTEST ISSUE <severity> <category> <short note>")
            return _HandlerResult(("\n".join(lines),))
        if query in {"status", "receipt"}:
            projection = self._playtest_projection(state, now=now)
            profile = projection["profile"]
            assert isinstance(profile, dict)
            issue_counts = projection["issue_counts"]
            assert isinstance(issue_counts, dict)
            lines = [
                "LOCAL BEGINNER PLAYTEST",
                f"Status: {str(projection['status']).replace('_', ' ')}",
                f"Profile: {profile.get('family') or 'unassigned'} · {str(profile.get('mode') or 'standard').replace('_', ' ')} · {str(profile.get('experience') or 'unspecified').replace('_', ' ')}",
                f"Class: {profile.get('class_name') or profile.get('class_id') or 'unassigned'}",
                f"Active-window time: {projection['active_text']}",
                f"Idle-gap time: {projection['idle_text']}",
                f"Paused time: {projection['paused_text']}",
                f"Wall time: {projection['wall_text']}",
                f"Commands recorded: {projection['command_count']}",
                f"Chapter timing buckets: {len(projection['chapter_active_seconds'])} · milestones: {len(projection['milestones'])}",
                f"Campaign complete: {'yes' if projection['campaign_complete'] else 'no'}",
                f"Notes: {projection['notes_count']} · structured issues: {projection['issues_count']} (blocking {issue_counts.get('blocking', 0)})",
                f"Survey fields: {len(projection['survey'])}/6 · complete: {'yes' if projection['survey_complete'] else 'no'}",
                str(projection["timing_basis"]),
            ]
            if query == "receipt":
                lines.append(
                    "Fresh JSON and Markdown receipts will be written under runtime/playtests/."
                )
                lines.append(
                    "The receipt stays project-local and sends no analytics over the network."
                )
                return _HandlerResult(
                    ("\n".join(lines),),
                    (DomainEvent("playtest.receipt_requested", {"session_id": telemetry.playtest_session_id}),),
                    False,
                )
            if telemetry.playtest_status == "not_started":
                lines.append("Plan/checklist: PLAYTEST PLAN · PLAYTEST CHECKLIST")
                lines.append("Start: PLAYTEST START [FAMILY ...] [MODE ...] [EXPERIENCE ...]")
            elif telemetry.playtest_status == "running":
                lines.append("Pause or finish: PLAYTEST PAUSE · PLAYTEST COMPLETE")
            elif telemetry.playtest_status == "paused":
                lines.append("Resume: PLAYTEST RESUME")
            else:
                lines.append("Export: PLAYTEST RECEIPT")
            return _HandlerResult(("\n".join(lines),))
        if query == "start":
            if telemetry.playtest_status != "not_started":
                return _HandlerResult(
                    (
                        "This character already has a playtest clock. Use PLAYTEST STATUS, "
                        "PLAYTEST RESUME, or PLAYTEST RESTART CONFIRM.",
                    )
                )
            profile, error = self._parse_playtest_start_options(state, command.args[1:])
            if error is not None or profile is None:
                return _HandlerResult((error or "The playtest profile is invalid.",))
            self._reset_playtest_timer(state, now, profile=profile)
            family = profile.get("family")
            class_id = profile.get("class_id")
            representative = (
                PLAYTEST_REPRESENTATIVE_CLASSES[str(family)]
                if family is not None
                else None
            )
            profile_line = (
                f"Profile locked: {family} · "
                f"{str(profile.get('mode') or 'standard').replace('_', ' ')} · "
                f"{str(profile.get('experience') or 'unspecified').replace('_', ' ')}."
                if family is not None
                else (
                    "Profile timing started before character confirmation; "
                    "class and gameplay family will lock at BUILD CONFIRM."
                )
            )
            class_line = (
                f"Class: {class_id} · cohort representative: {representative} (recommendation only)."
                if class_id is not None and representative is not None
                else (
                    f"Target family: {family} · cohort representative: {representative}; "
                    "confirm a matching class."
                    if family is not None and representative is not None
                    else "Character creation time is included in this measured session."
                )
            )
            return _HandlerResult(
                (
                    "Local playtest timing started. Intentional breaks can be excluded with PLAYTEST PAUSE.",
                    profile_line,
                    class_line,
                    "No rewards, difficulty, story decisions, or network analytics are changed.",
                ),
                (DomainEvent("playtest.started", {"started_at": now, **profile}),),
                True,
            )
        if query == "pause":
            if telemetry.playtest_status != "running":
                return _HandlerResult(("PLAYTEST PAUSE requires a running playtest clock.",))
            telemetry.playtest_status = "paused"
            telemetry.playtest_pause_started_at = now
            return _HandlerResult(
                ("Playtest timing paused. Resume with PLAYTEST RESUME.",),
                (DomainEvent("playtest.paused", {"paused_at": now}),),
                True,
            )
        if query == "resume":
            if telemetry.playtest_status != "paused":
                return _HandlerResult(("PLAYTEST RESUME requires a paused playtest clock.",))
            if telemetry.playtest_pause_started_at:
                telemetry.playtest_paused_seconds += max(
                    0.0, now - telemetry.playtest_pause_started_at
                )
            telemetry.playtest_status = "running"
            telemetry.playtest_pause_started_at = 0.0
            telemetry.playtest_last_activity_at = now
            return _HandlerResult(
                ("Playtest timing resumed.",),
                (DomainEvent("playtest.resumed", {"resumed_at": now}),),
                True,
            )
        if query in {"complete", "finish", "stop"}:
            if telemetry.playtest_status not in {"running", "paused"}:
                return _HandlerResult(("No active playtest clock is available to complete.",))
            if telemetry.playtest_status == "paused" and telemetry.playtest_pause_started_at:
                telemetry.playtest_paused_seconds += max(
                    0.0, now - telemetry.playtest_pause_started_at
                )
            telemetry.playtest_status = "completed"
            telemetry.playtest_pause_started_at = 0.0
            telemetry.playtest_completed_at = now
            missing_survey = sorted(PLAYTEST_SURVEY_FIELDS - telemetry.playtest_survey.keys())
            lines = [
                "Playtest timing completed.",
                "Local JSON and Markdown receipts will be written automatically. Use PLAYTEST RECEIPT to create another copy.",
            ]
            if missing_survey:
                lines.append(
                    "Readiness note: survey incomplete — " + ", ".join(name.replace("_", " ") for name in missing_survey)
                )
            if any(issue.get("severity") == "blocking" for issue in telemetry.playtest_issues):
                lines.append("Readiness note: one or more blocking issues were recorded.")
            return _HandlerResult(
                tuple(lines),
                (DomainEvent("playtest.completed", {"completed_at": now}),),
                True,
            )
        if query == "note":
            note = " ".join(command.args[1:]).strip()
            if not note:
                return _HandlerResult(("Add a short note after PLAYTEST NOTE.",))
            if telemetry.playtest_status == "not_started":
                return _HandlerResult(("Start the local playtest clock before adding notes.",))
            if len(telemetry.playtest_notes) >= 20:
                return _HandlerResult(("The local receipt already contains the maximum 20 notes.",))
            note = " ".join(note.split())[:240]
            telemetry.playtest_notes.append(note)
            return _HandlerResult(
                (f"Playtest note {len(telemetry.playtest_notes)}/20 recorded locally.",),
                (DomainEvent("playtest.note_recorded", {"note_number": len(telemetry.playtest_notes)}),),
                True,
            )
        if query == "issue":
            if telemetry.playtest_status == "not_started":
                return _HandlerResult(("Start the local playtest clock before recording an issue.",))
            if len(command.args) < 4:
                return _HandlerResult((
                    "Use PLAYTEST ISSUE <LOW|MEDIUM|HIGH|BLOCKING> <CATEGORY> <SHORT NOTE>. "
                    "Categories: command, navigation, combat, sol, pacing, accessibility, lore, save, bug, other.",
                ))
            severity = command.args[1].casefold()
            if severity == "blocker":
                severity = "blocking"
            category = normalize_issue_category(command.args[2])
            note = " ".join(command.args[3:]).strip()
            if severity not in PLAYTEST_ISSUE_SEVERITIES:
                return _HandlerResult(("Issue severity must be LOW, MEDIUM, HIGH, or BLOCKING.",))
            if category is None:
                return _HandlerResult(("Unknown issue category. Use PLAYTEST ISSUE for the category list.",))
            if not note:
                return _HandlerResult(("Add a short issue note without names, credentials, or absolute paths.",))
            if len(telemetry.playtest_issues) >= 20:
                return _HandlerResult(("The local receipt already contains the maximum 20 structured issues.",))
            issue = {
                "severity": severity,
                "category": category,
                "note": " ".join(note.split())[:240],
            }
            telemetry.playtest_issues.append(issue)
            return _HandlerResult(
                (f"Playtest issue {len(telemetry.playtest_issues)}/20 recorded: {severity} · {category.replace('_', ' ')}.",),
                (DomainEvent("playtest.issue_recorded", {"severity": severity, "category": category}),),
                True,
            )
        if query == "tool":
            if telemetry.playtest_status == "not_started":
                return _HandlerResult(("Start the local playtest clock before recording an assistive tool.",))
            tool = " ".join(command.args[1:]).strip()
            if not tool:
                current = telemetry.playtest_assistive_tool or "not recorded"
                return _HandlerResult((f"Assistive tool: {current}. Record with PLAYTEST TOOL <short name>.",))
            telemetry.playtest_assistive_tool = " ".join(tool.split())[:80]
            return _HandlerResult(
                (f"Assistive tool recorded locally: {telemetry.playtest_assistive_tool}.",),
                (DomainEvent("playtest.assistive_tool_recorded", {}),),
                True,
            )
        if query == "survey":
            if telemetry.playtest_status == "not_started":
                return _HandlerResult(("Start the local playtest clock before recording a survey.",))
            if len(command.args) < 3:
                return _HandlerResult(
                    (
                        "Use PLAYTEST SURVEY FIELD 1-5. Fields: clarity, pacing, "
                        "sol_helpfulness, player_agency, capstone, readiness.",
                    )
                )
            field_name = command.args[1].casefold().replace("-", "_")
            if field_name not in PLAYTEST_SURVEY_FIELDS:
                return _HandlerResult(("Unknown survey field. Use PLAYTEST SURVEY for the field list.",))
            try:
                score = int(command.args[2])
            except ValueError:
                score = 0
            if not 1 <= score <= 5:
                return _HandlerResult(("Survey scores must be whole numbers from 1 to 5.",))
            telemetry.playtest_survey[field_name] = score
            return _HandlerResult(
                (f"Playtest survey recorded: {field_name.replace('_', ' ')} = {score}/5.",),
                (DomainEvent("playtest.survey_recorded", {"field": field_name, "score": score}),),
                True,
            )
        if query == "restart":
            if len(command.args) < 2 or command.args[1].casefold() != "confirm":
                return _HandlerResult(
                    (
                        "Restarting clears only this character's local playtest timing, notes, issues, and survey. "
                        "Use PLAYTEST RESTART CONFIRM.",
                    )
                )
            if telemetry.playtest_family is None or telemetry.playtest_class_id is None:
                profile, error = self._parse_playtest_start_options(state, ())
                if error is not None or profile is None:
                    return _HandlerResult((error or "The playtest profile is invalid.",))
                self._reset_playtest_timer(state, now, profile=profile)
            else:
                self._reset_playtest_timer(state, now, preserve_profile=True)
            return _HandlerResult(
                ("Local playtest clock restarted after explicit confirmation.",),
                (DomainEvent("playtest.restarted", {"started_at": now}),),
                True,
            )
        return _HandlerResult(
            (
                "Use PLAYTEST STATUS, PLAN, CHECKLIST, START, PAUSE, RESUME, COMPLETE, "
                "NOTE, ISSUE, ISSUES, TOOL, SURVEY, RESTART, or RECEIPT.",
            )
        )


    def _journal_projection(self, state: GameState) -> dict[str, object]:
        locations = [
            {
                "id": room_id,
                "title": self.catalog.rooms[room_id].title,
            }
            for room_id in sorted(
                state.visited_rooms,
                key=lambda value: self.catalog.rooms[value].title,
            )
        ]
        clues = [
            {
                "id": room.search.id,
                "room_id": room.id,
                "room_title": room.title,
                "text": room.search.text,
            }
            for room in self.catalog.rooms.values()
            if room.search is not None and room.search.id in state.revealed
        ]
        courses = [
            {
                "id": course_id,
                "name": self.catalog.courses[course_id].name,
            }
            for course_id in sorted(state.character.course.completed_courses)
        ]
        authored_spawns = {
            spawn.id: (room, self.catalog.creatures[spawn.creature_id])
            for room in self.catalog.rooms.values()
            for spawn in room.creatures
        }
        victories = [
            {
                "instance_id": instance_id,
                "name": authored_spawns[instance_id][1].name,
                "room_id": authored_spawns[instance_id][0].id,
                "room_title": authored_spawns[instance_id][0].title,
            }
            for instance_id in sorted(state.defeated_creatures)
            if instance_id in authored_spawns
        ]
        sovereignty = [
            {
                "id": record_id,
                "label": self.catalog.story.records[record_id].label,
                "description": self.catalog.story.records[record_id].description,
            }
            for record_id in sorted(
                state.story.records,
                key=lambda value: self.catalog.story.records[value].label,
            )
        ]
        return {
            "locations": locations,
            "location_count": len(locations),
            "world_room_count": len(self.catalog.rooms),
            "clues": clues,
            "clue_count": len(clues),
            "courses": courses,
            "course_count": len(courses),
            "course_catalog_count": len(self.catalog.courses),
            "victories": victories,
            "victory_count": len(state.defeated_creatures),
            "unresolved_victory_count": (
                len(state.defeated_creatures) - len(victories)
            ),
            "sovereignty": sovereignty,
            "sovereignty_count": len(sovereignty),
            "progress": {
                "level": state.character.level,
                "absorbed_insight": state.character.experience.absorbed,
                "field_insight": state.character.experience.field_pool,
                "training_ranks": dict(state.character.training.ranks),
            },
        }

    def _journal(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        projection = self._journal_projection(state)
        section = self._query(command.args)
        aliases = {
            "": "all",
            "all": "all",
            "locations": "locations",
            "location": "locations",
            "map": "locations",
            "clues": "clues",
            "clue": "clues",
            "lore": "clues",
            "courses": "courses",
            "course": "courses",
            "victories": "victories",
            "victory": "victories",
            "combat": "victories",
            "sovereignty": "sovereignty",
            "decisions": "sovereignty",
            "decision": "sovereignty",
            "records": "sovereignty",
            "record": "sovereignty",
            "progress": "progress",
            "advancement": "progress",
        }
        selected = aliases.get(section)
        if selected is None:
            return _HandlerResult(
                (
                    "Journal sections: LOCATIONS, CLUES, COURSES, "
                    "VICTORIES, SOVEREIGNTY, or PROGRESS.",
                )
            )
        lines = ["Field journal"]
        if selected in {"all", "locations"}:
            lines.append(
                f"Locations visited: {projection['location_count']}/"
                f"{projection['world_room_count']}."
            )
            locations = projection["locations"]
            assert isinstance(locations, list)
            lines.extend(
                f"  {entry['title']}" for entry in locations
            )
        if selected in {"all", "clues"}:
            lines.append(f"Clues recorded: {projection['clue_count']}.")
            clues = projection["clues"]
            assert isinstance(clues, list)
            lines.extend(
                f"  {entry['room_title']}: {entry['text']}"
                for entry in clues
            )
            if not clues:
                lines.append("  No clues recorded.")
        if selected in {"all", "courses"}:
            lines.append(
                f"Courses completed: {projection['course_count']}/"
                f"{projection['course_catalog_count']}."
            )
            courses = projection["courses"]
            assert isinstance(courses, list)
            lines.extend(f"  {entry['name']}" for entry in courses)
            if not courses:
                lines.append("  No courses completed.")
        if selected in {"all", "victories"}:
            lines.append(f"Victories recorded: {projection['victory_count']}.")
            victories = projection["victories"]
            assert isinstance(victories, list)
            lines.extend(
                f"  {entry['name']} at {entry['room_title']}"
                for entry in victories
            )
            unresolved = int(projection["unresolved_victory_count"])
            if unresolved:
                lines.append(
                    f"  {unresolved} dynamic encounter "
                    f"{'record' if unresolved == 1 else 'records'} retained."
                )
            if not victories and not unresolved:
                lines.append("  No victories recorded.")
        if selected in {"all", "sovereignty"}:
            lines.append(
                f"Sovereignty records: {projection['sovereignty_count']}."
            )
            sovereignty = projection["sovereignty"]
            assert isinstance(sovereignty, list)
            lines.extend(
                f"  {entry['label']}: {entry['description']}"
                for entry in sovereignty
            )
            if not sovereignty:
                lines.append("  No defining decisions recorded.")
        if selected in {"all", "progress"}:
            progress = projection["progress"]
            assert isinstance(progress, dict)
            ranks = progress["training_ranks"]
            assert isinstance(ranks, dict)
            rank_text = (
                ", ".join(
                    f"{option_id} {rank}"
                    for option_id, rank in sorted(ranks.items())
                )
                if ranks
                else "none"
            )
            lines.extend(
                (
                    f"Progress: level {progress['level']}; learned insight "
                    f"{progress['absorbed_insight']}; field insight "
                    f"{progress['field_insight']}.",
                    f"  Training ranks: {rank_text}.",
                )
            )
        if selected == "all":
            lines.append(
                "Use JOURNAL <section> for a focused, read-only view."
            )
        return _HandlerResult(
            ("\n".join(lines),),
            (DomainEvent("journal.viewed", {"section": selected}),),
        )




    @staticmethod
    def _withdrawal_retry_prefix(room_id: str, direction: str) -> str:
        return f"withdrawal_retry:{room_id}:{direction}:"

    def _withdrawal_retry_bonus(
        self,
        state: GameState,
        room_id: str,
        direction: str,
    ) -> int:
        prefix = self._withdrawal_retry_prefix(room_id, direction)
        for flag in state.flags:
            if not flag.startswith(prefix):
                continue
            try:
                return max(0, min(18, int(flag.rsplit(":", 1)[1])))
            except ValueError:
                return 0
        return 0

    def _set_withdrawal_retry_bonus(
        self,
        state: GameState,
        room_id: str,
        direction: str,
        value: int,
    ) -> int:
        prefix = self._withdrawal_retry_prefix(room_id, direction)
        state.flags.difference_update(
            flag for flag in tuple(state.flags) if flag.startswith(prefix)
        )
        bounded = max(0, min(18, int(value)))
        if bounded:
            state.flags.add(f"{prefix}{bounded}")
        return bounded

    def _withdrawal_companion_bonus(
        self,
        state: GameState,
        now: float,
    ) -> tuple[int, str | None]:
        companion, progress = self._active_companion_context(state, now)
        if (
            companion is None
            or progress is None
            or companion.assist_kind != "partner"
            or progress.health <= 0
            or progress.downed_until > now
        ):
            return 0, None
        bonus = {"balanced": 6, "guard": 12, "assault": 0}[progress.order]
        return bonus, f"{companion.name} {progress.order} cover +{bonus}"

    @staticmethod
    def _withdrawal_roll_text(normal_roll_needed: int) -> str:
        if normal_roll_needed <= 1:
            return "Any normal d100 result succeeds before open-ended rolls."
        if normal_roll_needed <= 100:
            return f"A normal d100 needs {normal_roll_needed} or higher."
        return (
            f"A normal d100 cannot reach {normal_roll_needed}; "
            "only an open-ended high roll can succeed without improving the setup."
        )

    def _withdrawal_status(
        self,
        state: GameState,
        now: float,
        direction_query: str,
    ) -> _HandlerResult:
        room = self.catalog.rooms[state.character.room_id]
        if direction_query:
            direction, destination = self._resolve_exit(state, direction_query)
            candidates = [(direction, destination)] if destination is not None else []
        else:
            candidates = list(room.exits.items())
        if not candidates:
            return _HandlerResult(("No matching withdrawal route is visible. Try EXITS.",))
        opponents = self._live_creatures(state)
        lines = ["WITHDRAWAL STATUS · READ-ONLY"]
        if not opponents:
            lines.append(
                "No active opponent is contesting this room. WITHDRAW DIRECTION uses ordinary movement."
            )
            lines.extend(
                f"  {direction.upper():<10} → {self.catalog.rooms[destination].title}"
                for direction, destination in candidates
                if destination is not None
            )
            return _HandlerResult(("\n".join(lines),))
        load = calculate_encumbrance(state.character, self.catalog.items)
        armor = equipped_item(state.character, self.catalog.items, "body")
        companion_bonus, companion_text = self._withdrawal_companion_bonus(
            state, now
        )
        pinned_penalty = self.combat_scheduler.withdrawal_penalty(state)
        definitions = tuple(
            self.catalog.creatures[opponent.definition_id]
            for opponent in opponents
        )
        for direction, destination in candidates:
            if destination is None:
                continue
            locked = self._exit_lock_reason(state, direction)
            if locked:
                lines.append(f"{direction.upper()}: blocked — {locked}")
                continue
            retry_bonus = self._withdrawal_retry_bonus(
                state, state.character.room_id, direction
            )
            profile = calculate_withdrawal_profile(
                state.character,
                definitions,
                armor,
                encumbrance_penalty=load.recovery_penalty + pinned_penalty,
                companion_bonus=companion_bonus,
                retry_bonus=retry_bonus,
            )
            lines.extend(
                (
                    f"{direction.upper()} → {self.catalog.rooms[destination].title}",
                    f"  Escape {profile.escape} vs pressure {profile.pressure}; "
                    f"normal roll needed {profile.normal_roll_needed}.",
                    f"  {self._withdrawal_roll_text(profile.normal_roll_needed)}",
                    "  Modifiers: "
                    f"reaction +{profile.reaction_bonus}, "
                    f"Sol cover +{profile.companion_bonus}, "
                    f"route-read +{profile.retry_bonus}, "
                    f"wounds -{profile.wound_penalty}, "
                    f"disabled legs -{profile.disabled_leg_penalty}, "
                    f"load/pinned -{profile.encumbrance_penalty} "
                    f"(load {load.recovery_penalty}; pinned {pinned_penalty}), "
                    f"armor -{profile.armor_penalty}, "
                    f"crowd pressure +{profile.crowd_pressure}.",
                    f"  Attempt: WITHDRAW {direction}",
                )
            )
        lines.append(
            "Improve the attempt with DEFENSE EVADE (+12 reaction), lower load or wounds, "
            "or COMPANION ORDER GUARD when Sol is present. Failed same-route attempts gain +6 route-read, capped at +18."
        )
        if companion_text:
            lines.append(f"Current partner cover: {companion_text}.")
        return _HandlerResult(("\n".join(lines),))

    def _withdrawal_projection(
        self,
        state: GameState,
        now: float,
    ) -> dict[str, object]:
        """Return a read-only explanation of each available withdrawal route."""

        room = self.catalog.rooms[state.character.room_id]
        opponents = self._live_creatures(state)
        definitions = tuple(
            self.catalog.creatures[opponent.definition_id]
            for opponent in opponents
        )
        load = calculate_encumbrance(state.character, self.catalog.items)
        armor = equipped_item(state.character, self.catalog.items, "body")
        companion_bonus, companion_text = self._withdrawal_companion_bonus(
            state, now
        )
        pinned_penalty = self.combat_scheduler.withdrawal_penalty(state)
        routes: list[dict[str, object]] = []
        for direction, destination in room.exits.items():
            lock_reason = self._exit_lock_reason(state, direction)
            route: dict[str, object] = {
                "direction": direction,
                "destination_id": destination,
                "destination_title": self.catalog.rooms[destination].title,
                "locked": bool(lock_reason),
                "lock_reason": lock_reason,
                "command": f"withdraw {direction}",
            }
            if not opponents:
                route.update(
                    {
                        "contested": False,
                        "ordinary_movement": True,
                        "normal_roll_needed": 1,
                        "roll_text": "No opponent is contesting this exit.",
                    }
                )
            elif not lock_reason:
                retry_bonus = self._withdrawal_retry_bonus(
                    state, state.character.room_id, direction
                )
                profile = calculate_withdrawal_profile(
                    state.character,
                    definitions,
                    armor,
                    encumbrance_penalty=load.recovery_penalty + pinned_penalty,
                    companion_bonus=companion_bonus,
                    retry_bonus=retry_bonus,
                )
                route.update(
                    {
                        "contested": True,
                        "ordinary_movement": False,
                        "escape": profile.escape,
                        "pressure": profile.pressure,
                        "normal_roll_needed": profile.normal_roll_needed,
                        "roll_text": self._withdrawal_roll_text(
                            profile.normal_roll_needed
                        ),
                        "modifiers": {
                            "reaction_bonus": profile.reaction_bonus,
                            "companion_bonus": profile.companion_bonus,
                            "retry_bonus": profile.retry_bonus,
                            "wound_penalty": profile.wound_penalty,
                            "disabled_leg_penalty": profile.disabled_leg_penalty,
                            "encumbrance_penalty": profile.encumbrance_penalty,
                            "load_penalty": load.recovery_penalty,
                            "pinned_penalty": pinned_penalty,
                            "armor_penalty": profile.armor_penalty,
                            "crowd_pressure": profile.crowd_pressure,
                        },
                    }
                )
            routes.append(route)
        viable = [
            route
            for route in routes
            if not route["locked"] and route.get("normal_roll_needed") is not None
        ]
        best_route = (
            min(viable, key=lambda route: int(route["normal_roll_needed"]))
            if viable
            else None
        )
        return {
            "contested": bool(opponents),
            "opponent_count": len(opponents),
            "opponents": [definition.name for definition in definitions],
            "load_tier": load.tier,
            "load_penalty": load.recovery_penalty,
            "pinned_penalty": pinned_penalty,
            "armor": armor.name if armor is not None else "none",
            "companion_cover_bonus": companion_bonus,
            "companion_cover": companion_text,
            "retry_increment": 6,
            "retry_cap": 18,
            "best_route_direction": (
                str(best_route["direction"]) if best_route is not None else None
            ),
            "routes": routes,
            "status_command": "withdraw status",
            "guidance": (
                "Withdrawal is an opposed roll. DEFENSE EVADE, lower load or wounds, "
                "and COMPANION ORDER GUARD can improve the attempt. A failed attempt "
                "on the same route grants +6 route-read, capped at +18."
                if opponents
                else "No active opponent is contesting movement from this room."
            ),
            "read_only": True,
        }

    def _withdraw(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if command.args and command.args[0].casefold() in {"status", "odds", "plan"}:
            return self._withdrawal_status(
                state,
                now,
                self._query(command.args[1:]),
            )
        query = self._query(command.args)
        if not query:
            return _HandlerResult(("Withdraw where? Try EXITS or WITHDRAW STATUS.",))
        direction, destination = self._resolve_exit(state, query)
        if destination is None:
            return _HandlerResult((f"You cannot withdraw {query} from here.",))
        locked = self._exit_lock_reason(state, direction)
        if locked:
            return _HandlerResult((locked,))
        opponents = self._live_creatures(state)
        if not opponents:
            return self._move_character(
                state,
                now,
                direction=direction,
                destination=destination,
                base_duration=1,
                mode="uncontested-withdrawal",
            )
        origin = state.character.room_id
        load = calculate_encumbrance(state.character, self.catalog.items)
        armor = equipped_item(state.character, self.catalog.items, "body")
        companion_bonus, companion_text = self._withdrawal_companion_bonus(
            state, now
        )
        pinned_penalty = self.combat_scheduler.consume_withdrawal_penalty(state)
        retry_bonus = self._withdrawal_retry_bonus(state, origin, direction)
        outcome = resolve_withdrawal(
            state.character,
            tuple(
                self.catalog.creatures[opponent.definition_id]
                for opponent in opponents
            ),
            armor,
            self.rng,
            encumbrance_penalty=load.recovery_penalty + pinned_penalty,
            companion_bonus=companion_bonus,
            retry_bonus=retry_bonus,
        )
        lines = [
            f"You commit to a withdrawal {direction}.",
            f"[Roll {outcome.roll:+d} + Escape {outcome.escape} "
            f"- Pressure {outcome.pressure} = {outcome.endroll}]",
            self._withdrawal_roll_text(outcome.normal_roll_needed),
        ]
        if companion_text:
            lines.append(f"Partner cover: {companion_text}.")
        if retry_bonus:
            lines.append(f"Route-read from prior attempts: +{retry_bonus}.")
        if pinned_penalty:
            lines.append(
                f"Pinned pressure consumes this opening and applies -{pinned_penalty} escape."
            )
        event_payload = {
            "from": origin,
            "to": destination,
            "direction": direction,
            "success": outcome.success,
            "threshold": 100,
            "roll": outcome.roll,
            "escape": outcome.escape,
            "pressure": outcome.pressure,
            "endroll": outcome.endroll,
            "normal_roll_needed": outcome.normal_roll_needed,
            "opponent_count": outcome.opponent_count,
            "crowd_pressure": outcome.crowd_pressure,
            "wound_penalty": outcome.wound_penalty,
            "disabled_leg_penalty": outcome.disabled_leg_penalty,
            "encumbrance_penalty": outcome.encumbrance_penalty,
            "load_penalty": load.recovery_penalty,
            "pinned_penalty": pinned_penalty,
            "armor_penalty": outcome.armor_penalty,
            "reaction_bonus": outcome.reaction_bonus,
            "companion_bonus": outcome.companion_bonus,
            "retry_bonus": outcome.retry_bonus,
        }
        if not outcome.success:
            next_retry = self._set_withdrawal_retry_bonus(
                state, origin, direction, retry_bonus + 6
            )
            event_payload["next_retry_bonus"] = next_retry
            duration = 2 + load.recovery_penalty
            self._set_roundtime(state, now, duration)
            lines.extend(
                (
                    "The opposition closes the route before you can break contact.",
                    f"You read more of the contested route; the next {direction} attempt gains +{next_retry} route-read (cap +18).",
                    "Pressure factors: "
                    f"crowd +{outcome.crowd_pressure}; wounds -{outcome.wound_penalty}; "
                    f"disabled legs -{outcome.disabled_leg_penalty}; load -{outcome.encumbrance_penalty}; "
                    f"armor -{outcome.armor_penalty}; reaction +{outcome.reaction_bonus}; "
                    f"Sol cover +{outcome.companion_bonus}.",
                    "Use WITHDRAW STATUS "
                    f"{direction} for exact odds. DEFENSE EVADE adds +12 reaction; "
                    "COMPANION ORDER GUARD improves Sol's cover; STABILIZE or reduce load "
                    "when those penalties are present.",
                    f"Hard recovery: {duration} sec.",
                )
            )
            return _HandlerResult(
                tuple(lines),
                (DomainEvent("combat.withdrawal_resolved", event_payload),),
                True,
            )

        self._set_withdrawal_retry_bonus(state, origin, direction, 0)
        moved = self._move_character(
            state,
            now,
            direction=direction,
            destination=destination,
            base_duration=3,
            mode="contested-withdrawal",
        )
        return _HandlerResult(
            tuple(lines)
            + ("You break contact and clear the contested room.",)
            + moved.lines,
            (DomainEvent("combat.withdrawal_resolved", event_payload),)
            + moved.events,
            True,
        )










    def _technique(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        class_id = state.character.build.class_id
        definition = self.catalog.creation.classes.get(class_id or "")
        if definition is None:
            return _HandlerResult(
                ("A confirmed class foundation is required before using a signature technique.",)
            )
        unlocked = "signature_instinct_claimed" in state.flags
        remaining = max(0, math.ceil(state.character.technique_ready_at - now))
        if not command.args:
            status = (
                "not yet claimed"
                if not unlocked
                else "ready"
                if remaining == 0
                else f"ready in {remaining} sec"
            )
            return _HandlerResult(
                (
                    f"{definition.technique_name} · {status}.",
                    definition.technique_summary,
                    f"Passive: {definition.passive_name} — {definition.passive_summary}",
                    f"Exploration: {definition.exploration_name} — {definition.exploration_summary}",
                )
            )
        if not unlocked:
            return _HandlerResult(
                (
                    f"{definition.technique_name} remains a recovered possibility, not a dependable instinct.",
                    "Continue the opening story until the Relay Overlook lets you claim it deliberately.",
                )
            )
        if remaining:
            return _HandlerResult(
                (f"{definition.technique_name} is recovering for {remaining} more sec.",)
            )
        kind = definition.technique_kind
        character = state.character
        query = self._query(command.args)
        attack_kinds = {
            "power_attack", "precision_attack", "system_attack", "balanced_attack"
        }
        if kind in attack_kinds:
            if (
                query in {"self", "form", "practice", "rehearse"}
                and not self._live_creatures(state)
            ):
                character.guard_points = min(1000, character.guard_points + 4)
                character.technique_ready_at = now + 20
                state.flags.add("class_technique_used")
                self._set_roundtime(state, now, 2)
                return _HandlerResult(
                    (
                        f"[Technique · {definition.technique_name}] You rehearse the complete field motion without inventing a target or wasting the recovered instinct.",
                        "Controlled execution grants guard +4 and proves the technique is available under pressure.",
                        "Roundtime: 2 sec.",
                    ),
                    (DomainEvent("class.technique_used", {"class_id": class_id, "kind": kind, "mode": "controlled_rehearsal", "guard_gained": 4}),),
                    True,
                )
            if kind == "balanced_attack" and character.health < character.max_health * 0.6:
                before = character.health
                character.health = min(character.max_health, character.health + 8)
                character.guard_points = min(1000, character.guard_points + 3)
                character.technique_ready_at = now + 20
                state.flags.add("class_technique_used")
                self._set_roundtime(state, now, 2)
                return _HandlerResult(
                    (
                        f"[Technique · {definition.technique_name}] You restore balance instead of forcing an attack.",
                        f"Health rises from {before} to {character.health}; guard +3.",
                        "Roundtime: 2 sec.",
                    ),
                    (DomainEvent("class.technique_used", {"class_id": class_id, "kind": kind, "mode": "recovery"}),),
                    True,
                )
            if not query:
                return _HandlerResult((f"Use TECHNIQUE <target> for {definition.technique_name}.",))
            flag = f"technique:attack:{class_id}"
            state.flags.add(flag)
            result = self._attack(
                state,
                ParsedCommand("attack", command.args, command.raw, command.recovery),
                now,
            )
            state.flags.discard(flag)
            if not result.changed:
                return result
            character.technique_ready_at = now + 20
            state.flags.add("class_technique_used")
            event = DomainEvent(
                "class.technique_used",
                {"class_id": class_id, "kind": kind, "mode": "attack"},
            )
            return _HandlerResult(
                (f"[Technique · {definition.technique_name}] {definition.technique_summary}",)
                + result.lines,
                (event,) + result.events,
                True,
            )
        if kind == "escape":
            direction, destination = self._resolve_exit(state, query)
            if destination is None:
                return _HandlerResult(("Use TECHNIQUE <direction> to take a known exit.",))
            locked = self._exit_lock_reason(state, direction)
            if locked:
                return _HandlerResult((locked,))
            result = self._move_character(
                state, now, direction=direction, destination=destination,
                base_duration=1, mode="class-technique-escape",
            )
            character.technique_ready_at = now + 20
            state.flags.add("class_technique_used")
            return _HandlerResult(
                (f"[Technique · {definition.technique_name}] You disappear through the route before pressure can close.",)
                + result.lines,
                (DomainEvent("class.technique_used", {"class_id": class_id, "kind": kind}),)
                + result.events,
                True,
            )
        if kind == "repair":
            durable = [
                item for item in character.inventory if item.durability is not None
            ]
            candidates = [
                item for item in durable
                if item.durability < self._effective_max_durability(item)
            ]
            character.technique_ready_at = now + 20
            state.flags.add("class_technique_used")
            self._set_roundtime(state, now, 3)
            if not candidates:
                character.guard_points = min(1000, character.guard_points + 6)
                return _HandlerResult(
                    (
                        f"[Technique · {definition.technique_name}] You pre-brace straps, seals, and load points before failure can begin.",
                        "No carried equipment needed repair; preventive bracing grants guard +6.",
                        "Roundtime: 3 sec.",
                    ),
                    (DomainEvent("class.technique_used", {"class_id": class_id, "kind": kind, "mode": "preventive_bracing", "guard_gained": 6}),),
                    True,
                )
            target = min(
                candidates,
                key=lambda item: item.durability / max(1, self._effective_max_durability(item)),
            )
            before = int(target.durability or 0)
            target.durability = min(self._effective_max_durability(target), before + 8)
            name = self.catalog.items[target.definition_id].name
            return _HandlerResult(
                (
                    f"[Technique · {definition.technique_name}] You field-refit {name} without consuming material.",
                    f"Durability rises from {before} to {target.durability}.",
                    "Roundtime: 3 sec.",
                ),
                (DomainEvent("class.technique_used", {"class_id": class_id, "kind": kind, "target_item_id": target.definition_id}),),
                True,
            )
        if kind in {"field_heal", "regenerate"}:
            before = character.health
            amount = 12 if class_id == "medic" else 10 if kind == "field_heal" else 8
            character.health = min(character.max_health, character.health + amount)
            for wound in character.wounds:
                wound.bleeding = max(0, wound.bleeding - 1)
            if kind == "regenerate":
                character.guard_points = min(1000, character.guard_points + 3)
            character.technique_ready_at = now + 20
            state.flags.add("class_technique_used")
            self._set_roundtime(state, now, 2)
            return _HandlerResult(
                (
                    f"[Technique · {definition.technique_name}] Health rises from {before} to {character.health}.",
                    "Active bleeding is reduced by one step." + (" Guard +3." if kind == "regenerate" else ""),
                    "Roundtime: 2 sec.",
                ),
                (DomainEvent("class.technique_used", {"class_id": class_id, "kind": kind, "health_restored": character.health - before}),),
                True,
            )
        if kind == "guard":
            gained = 14 if class_id == "messenger" else 10
            character.guard_points = min(1000, character.guard_points + gained)
            character.stance = Stance.DEFENSIVE if class_id == "messenger" else Stance.GUARDED
            character.defense_mode = DefenseMode.BLOCK
            character.technique_ready_at = now + 20
            state.flags.add("class_technique_used")
            self._set_roundtime(state, now, 2)
            return _HandlerResult(
                (
                    f"[Technique · {definition.technique_name}] Guard +{gained}; {character.stance.value} stance and block reaction prepared.",
                    "Roundtime: 2 sec.",
                ),
                (DomainEvent("class.technique_used", {"class_id": class_id, "kind": kind, "guard_gained": gained}),),
                True,
            )
        return _HandlerResult(("That class technique is not implemented safely.",))









    def _ability(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        if class_definition is None:
            return _HandlerResult(
                ("Choose and confirm a class before specializing.",)
            )
        selected = self._selected_specialization(state)
        character = state.character
        cooldown_remaining = max(
            0, math.ceil(character.specialization_ready_at - now)
        )
        follow_up_remaining = max(
            0,
            math.ceil(character.specialization_follow_up_ready_until - now),
        )
        if not command.args or command.args[0].casefold() in {"status", "list"}:
            lines = [f"{class_definition.name} specialization:"]
            for branch in class_definition.ability_branches.values():
                marker = (
                    " [learned]"
                    if selected and selected.id == branch.id
                    else ""
                )
                lines.append(
                    f"  {branch.name} ({branch.id}) · {branch.kind} · "
                    f"power {branch.power} · cooldown {branch.cooldown} sec{marker}"
                )
                lines.append(f"    {branch.summary}")
                lines.append(
                    f"    Passive — {branch.passive.name}: "
                    f"{branch.passive.summary}"
                )
                lines.append(
                    f"    Follow-up — {branch.follow_up.name}: "
                    f"{branch.follow_up.summary}"
                )
                lines.append(f"    Counterplay — {branch.counterplay}")
            if selected is None:
                availability = (
                    "one point available"
                    if "ability_point_available" in state.flags
                    else "earn a specialization point from a first faction contact"
                )
                lines.append(f"Status: no branch learned; {availability}.")
                lines.append(
                    "Use ABILITY LEARN <branch> once a point is available."
                )
            else:
                upgrade = self._selected_specialization_upgrade(
                    state, selected
                )
                values = self._specialization_values(state, selected)
                lines.append(
                    f"Status: {selected.name}; "
                    + (
                        "ready."
                        if cooldown_remaining == 0
                        else f"ready in {cooldown_remaining} sec."
                    )
                )
                lines.append(
                    f"Mastery: {min(character.specialization_uses, selected.mastery_uses_required)}"
                    f"/{selected.mastery_uses_required} successful uses; "
                    + (
                        f"{upgrade.name} selected."
                        if upgrade is not None
                        else "upgrade choice available."
                        if character.specialization_uses
                        >= selected.mastery_uses_required
                        else "upgrade locked."
                    )
                )
                if follow_up_remaining:
                    lines.append(
                        f"Follow-up: {selected.follow_up.name} available for "
                        f"{follow_up_remaining} sec."
                    )
                lines.append(
                    f"Effective power {values['power']}; cooldown "
                    f"{values['cooldown']} sec; commitment "
                    f"{values['commitment_roundtime']} sec."
                )
                lines.append(
                    "Use ABILITY USE [target or direction], ABILITY FOLLOWUP "
                    "[target], or ABILITY UPGRADE <option>."
                )
            return _HandlerResult(("\n".join(lines),))

        action = command.args[0].casefold()
        if action == "learn":
            if selected is not None:
                return _HandlerResult(
                    (
                        f"You already learned {selected.name}; this foundation "
                        "does not permit a silent respec.",
                    )
                )
            if "ability_point_available" not in state.flags:
                return _HandlerResult(
                    ("No class specialization point is available yet.",)
                )
            query = self._query(command.args[1:])
            branch = self._resolve_ability_branch(state, query)
            if branch is None:
                return _HandlerResult(
                    ("Choose one of the two listed class branches.",)
                )
            state.flags.discard("ability_point_available")
            state.flags.add(f"specialization:{branch.id}")
            character.specialization_uses = 0
            character.specialization_upgrade_id = None
            character.specialization_follow_up_ready_until = 0.0
            return _HandlerResult(
                (
                    f"[Specialization learned] {branch.name}.",
                    branch.summary,
                    f"Passive: {branch.passive.name} — "
                    f"{branch.passive.summary}",
                    f"Follow-up: {branch.follow_up.name} — "
                    f"{branch.follow_up.summary}",
                    f"Counterplay: {branch.counterplay}",
                    "This choice stays with your character and shapes your class path.",
                ),
                (
                    DomainEvent(
                        "class.specialization_learned",
                        {
                            "class_id": class_definition.id,
                            "branch_id": branch.id,
                        },
                    ),
                ),
                True,
            )
        if selected is None:
            return _HandlerResult(
                ("Learn a class branch before using ABILITY.",)
            )
        if action in {"followup", "follow-up", "chain"}:
            return self._specialization_follow_up(
                state, selected, command, now
            )
        if action == "upgrade":
            if character.specialization_upgrade_id:
                chosen = self._selected_specialization_upgrade(
                    state, selected
                )
                return _HandlerResult(
                    (
                        f"{chosen.name if chosen else 'A mastery upgrade'} is "
                        "already locked in; no silent respec is granted.",
                    )
                )
            if character.specialization_uses < selected.mastery_uses_required:
                needed = (
                    selected.mastery_uses_required
                    - character.specialization_uses
                )
                return _HandlerResult(
                    (
                        f"Use {selected.name} successfully {needed} more "
                        f"time{'s' if needed != 1 else ''} before choosing a mastery upgrade.",
                    )
                )
            query = self._query(command.args[1:])
            upgrade = self._resolve_specialization_upgrade(selected, query)
            if upgrade is None:
                options = _natural_list(
                    [item.name for item in selected.upgrade_options.values()]
                )
                return _HandlerResult((f"Choose {options}.",))
            character.specialization_upgrade_id = upgrade.id
            return _HandlerResult(
                (
                    f"[Mastery chosen] {upgrade.name}.",
                    upgrade.summary,
                    "This mastery choice persists with the specialization.",
                ),
                (
                    DomainEvent(
                        "class.specialization_upgraded",
                        {
                            "class_id": class_definition.id,
                            "branch_id": selected.id,
                            "upgrade_id": upgrade.id,
                        },
                    ),
                ),
                True,
            )

        if action == "use":
            args = command.args[1:]
        else:
            args = command.args
        if cooldown_remaining:
            return _HandlerResult(
                (
                    f"{selected.name} is recovering for "
                    f"{cooldown_remaining} more sec.",
                )
            )
        values = self._specialization_values(state, selected)
        kind = selected.kind
        query = self._query(args)
        events = [
            DomainEvent(
                "class.specialization_used",
                {
                    "class_id": class_definition.id,
                    "branch_id": selected.id,
                    "kind": kind,
                },
            )
        ]
        lines = [f"[Ability · {selected.name}] {selected.summary}"]
        changed = False
        if kind in {"attack", "precision"}:
            if not query:
                return _HandlerResult(
                    (f"Use ABILITY USE <target> for {selected.name}.",)
                )
            state.flags.add(f"specialization_attack:{selected.id}")
            result = self._attack(
                state,
                ParsedCommand("attack", args, command.raw, command.recovery),
                now,
            )
            state.flags.discard(f"specialization_attack:{selected.id}")
            if not result.changed:
                return result
            lines.extend(result.lines)
            events.extend(result.events)
            changed = True
        elif kind in {"heal", "regenerate", "support"}:
            before_health = character.health
            character.health = min(
                character.max_health,
                character.health + values["power"],
            )
            if kind == "regenerate":
                before_guard = character.guard_points
                character.guard_points = min(
                    1000,
                    character.guard_points + max(2, values["power"] // 4),
                )
                lines.append(
                    f"Health {before_health} → {character.health}; guard "
                    f"{before_guard} → {character.guard_points}."
                )
            elif kind == "support":
                before_guard = character.guard_points
                character.guard_points = min(
                    1000,
                    character.guard_points + max(1, values["power"] // 2),
                )
                lines.append(
                    f"Health {before_health} → {character.health}; guard "
                    f"{before_guard} → {character.guard_points}."
                )
            else:
                lines.append(
                    f"Health {before_health} → {character.health}."
                )
            changed = True
        elif kind == "guard":
            before = character.guard_points
            character.guard_points = min(
                1000, character.guard_points + values["power"]
            )
            lines.append(f"Guard {before} → {character.guard_points}.")
            changed = True
        elif kind == "control":
            if not self._live_creatures(state):
                return _HandlerResult(
                    ("No active opponent can be controlled here.",)
                )
            state.flags.add("specialization_control_ready")
            if values["power"] > 1:
                before = character.guard_points
                character.guard_points = min(
                    1000,
                    character.guard_points + values["power"] - 1,
                )
                lines.append(
                    f"The next answering counterstrike is suppressed; guard "
                    f"{before} → {character.guard_points}."
                )
            else:
                lines.append(
                    "The next answering counterstrike is suppressed."
                )
            changed = True
        elif kind == "report":
            target, error = self._resolve_creature(state, query)
            if target is None:
                return _HandlerResult((error or "Report which opponent?",))
            state.flags.add(f"reported_target:{target.instance_id}")
            state.flags.add(
                f"specialization_report_power:{target.instance_id}:"
                f"{values['power']}"
            )
            lines.append(
                "The target's movement is reported for the next committed attack."
            )
            changed = True
        elif kind == "escape":
            direction, destination = self._resolve_exit(state, query)
            if destination is None:
                return _HandlerResult(
                    ("Use ABILITY USE <direction> to take a known exit.",)
                )
            locked = self._exit_lock_reason(state, direction)
            if locked:
                return _HandlerResult((locked,))
            result = self._move_character(
                state,
                now,
                direction=direction,
                destination=destination,
                base_duration=0,
                mode="specialization-escape",
            )
            if not result.changed:
                return result
            lines.extend(result.lines)
            events.extend(result.events)
            before = character.guard_points
            character.guard_points = min(
                1000, character.guard_points + values["power"]
            )
            lines.append(
                f"Arrival guard {before} → {character.guard_points}."
            )
            changed = True
        elif kind == "repair":
            candidates = [
                item
                for item in character.inventory
                if item.durability is not None
                and item.durability < self._effective_max_durability(item)
            ]
            if not candidates:
                return _HandlerResult(
                    ("No carried equipment currently needs repair.",)
                )
            target = min(candidates, key=lambda item: item.durability or 0)
            before = int(target.durability or 0)
            target.durability = min(
                self._effective_max_durability(target),
                before + values["power"],
            )
            lines.append(
                f"{self.catalog.items[target.definition_id].name} durability "
                f"{before} → {target.durability}."
            )
            changed = True
        elif kind == "craft":
            state.flags.add("specialization_craft_discount")
            lines.append(
                "Your next recipe costs one fewer input unit where possible."
            )
            changed = True
        else:
            return _HandlerResult(
                ("That specialization kind is not implemented safely.",)
            )
        if not changed:
            return _HandlerResult(
                ("The specialization produced no safe change.",)
            )
        previous_uses = character.specialization_uses
        character.specialization_uses = min(
            100_000_000, character.specialization_uses + 1
        )
        character.specialization_ready_at = now + values["cooldown"]
        self._set_roundtime(
            state, now, values["commitment_roundtime"]
        )
        lines.extend(self._apply_specialization_passive(state, selected))
        lines.append(
            self._prime_specialization_follow_up(
                state, selected, now, values
            )
        )
        if (
            previous_uses < selected.mastery_uses_required
            <= character.specialization_uses
            and character.specialization_upgrade_id is None
        ):
            options = _natural_list(
                [item.name for item in selected.upgrade_options.values()]
            )
            lines.append(
                f"[Mastery ready] Choose {options} with ABILITY UPGRADE."
            )
        lines.append(
            f"Roundtime: at least {values['commitment_roundtime']} sec."
        )
        return _HandlerResult(tuple(lines), tuple(events), True)







    def _beginner_level_cap_active(self, state: GameState) -> bool:
        """Keep the authored foundation at level 10 until its final quest closes."""

        return (
            state.character.build.status in {"pending", "confirmed"}
            and "price_of_second_life" not in state.story.completed_quests
        )











    def _report_projection(self, state: GameState) -> dict[str, object]:
        """Project bounded report evidence without claiming live report skills."""

        context = self._active_story_context(state)
        active_quest_id = context[0].id if context is not None else None
        reliability_active = (
            "report_reliability_active" in state.flags
            and "report_reliability_complete" not in state.flags
        )
        reliability_complete = "report_reliability_complete" in state.flags
        lens_records = {
            "shared_signal_reopened",
            "class_lens_applied",
            "fact_inference_unknown_separated",
            "class_lens_review_condition_met",
            "class_lens_limits_disclosed",
            "class_annotation_published",
            "class_lens_permission_expired",
        }
        lens_formed = (
            active_quest_id == "fifteen_lenses_one_truth"
            or "class_lens_active" in state.flags
            or "class_lens_complete" in state.flags
            or bool(lens_records.intersection(state.story.records))
        )
        lens_complete = "class_lens_complete" in state.flags
        mode = "class_lens" if lens_formed else "reliability"
        active = (
            (not lens_complete and active_quest_id == "fifteen_lenses_one_truth")
            or ("class_lens_active" in state.flags and not lens_complete)
            if lens_formed
            else reliability_active
        )
        complete = lens_complete if lens_formed else reliability_complete
        formed = (
            reliability_active
            or reliability_complete
            or "duplicate_report_received" in state.story.records
            or lens_formed
        )

        rule_map = (
            (
                "report_rule:corroborate",
                "report_rule_corroborated",
                "Corroboration",
                "Two independent marks must agree inside one visible window.",
            ),
            (
                "report_rule:timebox",
                "report_rule_timeboxed",
                "Shortest verified window",
                "Guidance expires at the edge of the freshest confirmed interval.",
            ),
            (
                "report_rule:quarantine",
                "report_rule_quarantined",
                "Quarantine",
                "Disputed intelligence cannot direct movement until interference is disclosed.",
            ),
        )
        doctrine = "Not selected"
        confidence = "Unclassified"
        for flag, record, label, description in rule_map:
            if flag in state.flags or record in state.story.records:
                doctrine = label
                confidence = description
                break
        outcome_map = (
            ("corroborated_guidance_published", "Corroborated movement window"),
            ("expiring_guidance_published", "Expiring movement window"),
            ("hold_guidance_published", "Hold notice with review condition"),
        )
        outcome = next(
            (
                label
                for record, label in outcome_map
                if record in state.story.records
            ),
            "Not published",
        )
        classified = (
            "report_raw_classified" in state.flags
            or "raw_observation_separated" in state.story.records
        )
        interference_marked = (
            "report_interference_marked" in state.flags
            or "signal_disruption_disclosed" in state.story.records
        )

        available_actions: list[dict[str, object]] = []
        if context is not None:
            _, stage = context
            for action in stage.actions:
                if action.verb != "report":
                    continue
                available, reason = self._story_action_availability(state, action)
                label, summary, _ = self._story_action_label(state, action)
                available_actions.append(
                    {
                        "id": action.id,
                        "label": label,
                        "summary": summary,
                        "command": self._story_action_command(action),
                        "available": available,
                        "unavailable_reason": reason,
                    }
                )
        primary_command = next(
            (
                str(action["command"])
                for action in available_actions
                if action["available"]
            ),
            "report",
        )
        companion, _progress = self._active_companion_context(
            state,
            self.clock.now(),
        )

        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        class_name = class_definition.name if class_definition is not None else "Unselected"
        lens_name = class_definition.exploration_name if class_definition is not None else "Unselected lens"
        lens_inference = (
            class_definition.exploration_summary
            if class_definition is not None
            else "No class discipline is available."
        )
        lens_review = "Not declared"
        lens_quest = self.catalog.story.quests.get("fifteen_lenses_one_truth")
        if lens_quest is not None:
            lens_actions = {
                action.id: action
                for stage in lens_quest.stages
                for action in stage.actions
            }
            read_action = lens_actions.get("read_class_lens")
            if read_action is not None:
                label, summary, _ = self._story_action_label(state, read_action)
                lens_name = label.removeprefix("Apply the ")
                lens_inference = summary
            test_action = lens_actions.get("test_class_lens")
            if test_action is not None:
                _label, summary, _ = self._story_action_label(state, test_action)
                lens_review = summary

        lens_read = (
            "class_lens_read" in state.flags
            or "class_lens_applied" in state.story.records
        )
        truth_separated = (
            "class_truth_separated" in state.flags
            or "fact_inference_unknown_separated" in state.story.records
        )
        lens_reviewed = (
            "class_lens_reviewed" in state.flags
            or "class_lens_review_condition_met" in state.story.records
        )
        limits_labeled = (
            "class_limits_labeled" in state.flags
            or "class_lens_limits_disclosed" in state.story.records
        )
        annotation_published = (
            "class_annotation_published" in state.flags
            or "class_annotation_published" in state.story.records
        )

        if lens_formed:
            state_label = (
                "Closed — annotation permission expired"
                if lens_complete
                else "Annotation published — closure required"
                if annotation_published
                else "Integrity and review limits labeled"
                if limits_labeled
                else "Class lens tested against evidence"
                if lens_reviewed
                else "Fact, inference, and unknown separated"
                if truth_separated
                else "Class lens applied"
                if lens_read
                else "Shared verified signal awaiting class lens"
            )
        else:
            state_label = (
                "Closed — authority expired"
                if reliability_complete
                else "Guidance published — closure required"
                if "report_guidance_published" in state.flags
                else "Interference disclosed"
                if interference_marked
                else "Reliability rule selected"
                if doctrine != "Not selected"
                else "Observation classified"
                if classified
                else "Disputed input received"
                if formed
                else "No bounded report active"
            )

        fact = (
            "One neutral resupply cart moved east during the verified window; the copied echo was excluded."
            if classified
            else "No verified movement fact has been separated."
        )
        unknown = (
            "Culprit, faction, motive, final destination, future movement, hostile intent, and any live target remain unproven."
        )
        signal_state = (
            "VERIFIED fact · DEGRADED echo · REVIEW REQUIRED after age, route change, source conflict, or repeated interference"
            if limits_labeled or lens_complete
            else "DEGRADED — known signal wash; attribution remains unknown"
            if interference_marked
            else "UNCLASSIFIED"
        )
        return {
            "mode": mode,
            "active": active,
            "formed": formed,
            "complete": complete,
            "reliability_complete": reliability_complete,
            "class_lens_complete": lens_complete,
            "status": state_label,
            "scope": (
                "One closed neutral-cart movement record between the Fever Shelter relay and the Neutral Dispatch Gate."
                if lens_formed
                else "One neutral resupply cart between the Fever Shelter relay and the Neutral Dispatch Gate."
            ),
            "source_count": (
                "1 direct observation + 1 excluded copied echo"
                if classified and lens_formed
                else "1 direct observation + 1 copied echo"
                if classified
                else "Unclassified"
            ),
            "doctrine": doctrine,
            "confidence": confidence,
            "interference": (
                "Disclosed — signal wash present; culprit, faction, and motive remain unknown."
                if interference_marked
                else "Suspected — not yet published as fact."
                if formed
                else "None recorded"
            ),
            "expiration": (
                "Closed at the class-lens ledger; teaching record remains read-only"
                if lens_complete
                else "Class annotation permission expires at ledger closure"
                if lens_formed
                else "Closed at the dispatch ledger"
                if reliability_complete
                else "Expires at the cart decision and ledger closure"
                if reliability_active
                else "Not active"
            ),
            "outcome": outcome,
            "class_name": class_name,
            "lens_name": lens_name,
            "fact": fact,
            "inference": lens_inference if lens_read or lens_complete else "Not yet applied.",
            "unknown": unknown,
            "review_condition": lens_review if lens_reviewed or lens_complete else "Not yet declared.",
            "signal_state": signal_state,
            "reliability_label": (
                "Class annotation, not new evidence"
                if lens_formed
                else doctrine
            ),
            "boundary": (
                "A class lens changes explanation and tactical emphasis only. It does not change the verified fact, reward value, faction or guild status, Commander authority, permanent surveillance rights, or the full class report-skill system."
                if lens_formed
                else "This is a story-bounded reliability exercise, not faction membership, guild status, Commander rank, permanent surveillance, or the full class report-skill system."
            ),
            "actions": available_actions,
            "primary_command": primary_command,
            "active_companion": (
                {
                    "id": companion.id,
                    "name": companion.name,
                    "separate_from_report": True,
                }
                if companion is not None
                else None
            ),
        }

    def _report(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        query = self._query(command.args)
        if query.casefold() in {"", "status", "list", "summary"}:
            projection = self._report_projection(state)
            if projection["mode"] == "class_lens":
                lines = [
                    f"Class evidence lens: {projection['status']}",
                    f"Class: {projection['class_name']} — {projection['lens_name']}",
                    f"Fact: {projection['fact']}",
                    f"Inference: {projection['inference']}",
                    f"Unknown: {projection['unknown']}",
                    f"Signal state: {projection['signal_state']}",
                    f"Review condition: {projection['review_condition']}",
                    f"Prior reliability rule: {projection['doctrine']} — {projection['confidence']}",
                    f"Expiration: {projection['expiration']}",
                    str(projection["boundary"]),
                ]
            else:
                lines = [
                    f"Field report: {projection['status']}",
                    f"Scope: {projection['scope']}",
                    f"Sources: {projection['source_count']}",
                    f"Rule: {projection['doctrine']} — {projection['confidence']}",
                    f"Interference: {projection['interference']}",
                    f"Outcome: {projection['outcome']}",
                    f"Expiration: {projection['expiration']}",
                    str(projection["boundary"]),
                ]
            companion_projection = projection["active_companion"]
            if companion_projection is not None:
                lines.append(
                    f"Active field partner: {companion_projection['name']} remains a separate companion slot."
                )
            available = [
                action for action in projection["actions"] if action["available"]
            ]
            if available:
                lines.append(
                    "Available: "
                    + _natural_list(
                        [str(action["command"]).upper() for action in available]
                    )
                    + "."
                )
            return _HandlerResult(("\n".join(lines),))
        return self._execute_story_verb(
            state,
            command,
            now,
            verb="report",
        )


    def _district_projection(self, state: GameState) -> dict[str, object]:
        """Project one bounded public passage without claiming permanent access."""

        context = self._active_story_context(state)
        active_quest_id = context[0].id if context is not None else None
        passage_records = {
            "district_notice_received",
            "district_class_caution_read",
            "district_preparation_access",
            "district_preparation_witness",
            "district_preparation_fallback",
            "district_window_verified",
            "district_public_boundary_crossed",
            "district_passage_authority_expired",
        }
        formed = (
            active_quest_id == "the_road_that_changes_meaning"
            or "district_passage_active" in state.flags
            or "district22_public_access_complete" in state.flags
            or bool(passage_records.intersection(state.story.records))
        )
        complete = "district22_public_access_complete" in state.flags
        active = formed and not complete and (
            active_quest_id == "the_road_that_changes_meaning"
            or "district_passage_active" in state.flags
        )
        notice_received = (
            "district_passage_active" in state.flags
            or "district_notice_received" in state.story.records
        )
        caution_read = (
            "district_caution_read" in state.flags
            or "district_class_caution_read" in state.story.records
        )
        window_verified = (
            "district_window_verified" in state.flags
            or "district_window_verified" in state.story.records
        )
        boundary_crossed = (
            "district_boundary_crossed" in state.flags
            or "district_public_boundary_crossed" in state.story.records
        )

        preparation_map = (
            ("district_prep:access", "district_preparation_access", "Transparent access", "Declare purpose, supplies, refusal boundary, and expiration."),
            ("district_prep:witness", "district_preparation_witness", "Public witness", "Record posted terms and material changes without private tracking."),
            ("district_prep:fallback", "district_preparation_fallback", "Reversible fallback", "Preserve a return route, safe pause, and stop condition."),
        )
        preparation = "Not selected"
        preparation_summary = "No passage preparation has been recorded."
        for flag, record, label, summary in preparation_map:
            if flag in state.flags or record in state.story.records:
                preparation = label
                preparation_summary = summary
                break

        available_actions: list[dict[str, object]] = []
        if context is not None:
            _, stage = context
            for action in stage.actions:
                if action.verb != "district":
                    continue
                available, reason = self._story_action_availability(state, action)
                label, summary, _ = self._story_action_label(state, action)
                available_actions.append(
                    {
                        "id": action.id,
                        "label": label,
                        "summary": summary,
                        "command": self._story_action_command(action),
                        "available": available,
                        "unavailable_reason": reason,
                    }
                )
        primary_command = next(
            (
                str(action["command"])
                for action in available_actions
                if action["available"]
            ),
            "district status",
        )

        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        class_name = class_definition.name if class_definition is not None else "Unselected"
        caution_name = "Unselected caution"
        caution = "No class discipline is available."
        review_condition = "Reverify if posted terms, route markers, queue conditions, or the class-specific risk materially changes."
        passage_quest = self.catalog.story.quests.get("the_road_that_changes_meaning")
        if passage_quest is not None:
            passage_actions = {
                action.id: action
                for stage in passage_quest.stages
                for action in stage.actions
            }
            caution_action = passage_actions.get("read_district_caution")
            if caution_action is not None:
                label, summary, _ = self._story_action_label(state, caution_action)
                caution_name = label.removeprefix("Read the ")
                caution = summary
            verify_action = passage_actions.get("verify_district_window")
            if verify_action is not None:
                _label, summary, _ = self._story_action_label(state, verify_action)
                review_condition = summary

        if complete:
            status = "Closed — one-shift passage expired"
        elif boundary_crossed:
            status = "Public boundary crossed — ledger closure required"
        elif window_verified:
            status = "Passage window verified"
        elif preparation != "Not selected":
            status = "Preparation selected — verification required"
        elif caution_read:
            status = "Class caution read — preparation required"
        elif notice_received:
            status = "District 22 notice received"
        else:
            status = "No public district passage active"

        companion, _progress = self._active_companion_context(
            state,
            self.clock.now(),
        )
        return {
            "active": active,
            "formed": formed,
            "complete": complete,
            "status": status,
            "destination": "District 22, Shaklas Public Queue",
            "fact": (
                "District 22’s Shaklas public relief passage is open for one verified shift; neutral travelers may reach the public queue without faction sponsorship."
                if notice_received
                else "No current district passage fact has been accepted."
            ),
            "class_name": class_name,
            "caution_name": caution_name,
            "caution": caution if caution_read or complete else "Not yet read.",
            "preparation": preparation,
            "preparation_summary": preparation_summary,
            "window": (
                "Verified immediately before crossing"
                if window_verified or complete
                else "Not yet reverified"
            ),
            "review_condition": review_condition if caution_read or complete else "Not yet declared.",
            "unknown": "Window renewal, reason for opening, arriving travelers, informal tolls, side-street control, and conditions beyond the public queue remain unknown.",
            "expiration": (
                "Expired at the Shaklas public ledger"
                if complete
                else "Expires at the public queue and grants no continuing access"
                if formed
                else "Not active"
            ),
            "boundary": "This is one neutral public passage, not faction sponsorship, citizenship, ownership, Commander authority, permanent access, surveillance permission, or knowledge of deeper Shaklas.",
            "actions": available_actions,
            "primary_command": primary_command,
            "active_companion": (
                {
                    "id": companion.id,
                    "name": companion.name,
                    "separate_from_passage": True,
                }
                if companion is not None
                else None
            ),
        }

    def _district(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        query = self._query(command.args)
        if query.casefold() in {"", "status", "list", "summary"}:
            projection = self._district_projection(state)
            lines = [
                f"District passage: {projection['status']}",
                f"Destination: {projection['destination']}",
                f"Fact: {projection['fact']}",
                f"Class caution: {projection['class_name']} — {projection['caution_name']}",
                f"Caution: {projection['caution']}",
                f"Preparation: {projection['preparation']} — {projection['preparation_summary']}",
                f"Window: {projection['window']}",
                f"Review condition: {projection['review_condition']}",
                f"Unknown: {projection['unknown']}",
                f"Expiration: {projection['expiration']}",
                str(projection["boundary"]),
            ]
            companion_projection = projection["active_companion"]
            if companion_projection is not None:
                lines.append(
                    f"Active field partner: {companion_projection['name']} remains a separate companion slot."
                )
            available = [
                action for action in projection["actions"] if action["available"]
            ]
            if available:
                lines.append(
                    "Available: "
                    + _natural_list(
                        [str(action["command"]).upper() for action in available]
                    )
                    + "."
                )
            return _HandlerResult(("\n".join(lines),))
        return self._execute_story_verb(
            state,
            command,
            now,
            verb="district",
        )

    def _service_projection(self, state: GameState) -> dict[str, object]:
        """Project one temporary Shaklas public-service review."""

        context = self._active_story_context(state)
        active_quest_id = context[0].id if context is not None else None
        review_records = {
            "public_queue_conflict_received",
            "queue_memory_sources_separated",
            "queue_history_recalled",
            "queue_method_reconciled",
            "queue_method_witnessed",
            "queue_method_held",
            "queue_service_window_verified",
            "queue_claims_preserved_for_clinical_review",
            "queue_review_authority_expired",
        }
        formed = (
            active_quest_id == "the_public_queue_remembers"
            or "shaklas_queue_review_active" in state.flags
            or "shaklas_queue_memory_complete" in state.flags
            or bool(review_records.intersection(state.story.records))
        )
        complete = "shaklas_queue_memory_complete" in state.flags
        active = formed and not complete and (
            active_quest_id == "the_public_queue_remembers"
            or "shaklas_queue_review_active" in state.flags
        )
        accepted = (
            "shaklas_queue_review_accepted" in state.flags
            or "public_queue_conflict_received" in state.story.records
        )
        memory_read = "shaklas_queue_memory_read" in state.flags or complete
        sources_traced = (
            "shaklas_queue_sources_traced" in state.flags
            or "queue_memory_sources_separated" in state.story.records
        )
        history_compared = (
            "shaklas_queue_history_compared" in state.flags
            or "queue_history_recalled" in state.story.records
        )
        window_verified = (
            "shaklas_queue_window_verified" in state.flags
            or "queue_service_window_verified" in state.story.records
        )
        resolution_applied = (
            "shaklas_queue_resolution_applied" in state.flags
            or "queue_claims_preserved_for_clinical_review" in state.story.records
        )

        method_map = (
            (
                "shaklas_queue_method:reconcile",
                "queue_method_reconciled",
                "Reconcile",
                "Restore the earlier position, preserve the cold-chain claim, and send both to trained clinical review.",
            ),
            (
                "shaklas_queue_method:witness",
                "queue_method_witnessed",
                "Witness",
                "Post both claims, their sources, and the overwrite gap under public witness before clinical review.",
            ),
            (
                "shaklas_queue_method:hold",
                "queue_method_held",
                "Hold",
                "Freeze the disputed ordering while preserving both claims for trained clinical review.",
            ),
        )
        method = "Not selected"
        method_summary = "No temporary review method has been selected."
        for flag, record, label, summary in method_map:
            if flag in state.flags or record in state.story.records:
                method = label
                method_summary = summary
                break

        def remembered_label(
            candidates: tuple[tuple[str, str], ...],
            fallback: str,
        ) -> str:
            for record_id, label in candidates:
                if record_id in state.story.records:
                    return label
            return fallback

        shelter_memory = remembered_label(
            (
                ("shelter_rule_public_tally", "Public tally"),
                ("shelter_rule_neutral_passage", "Neutral passage"),
                ("shelter_rule_receipts", "Accountable receipts"),
                ("shelter_lesson_prior_service", "Prior service credited"),
            ),
            "No recorded shelter precedent",
        )
        caravan_memory = remembered_label(
            (
                ("unowned_caravan_escorted", "Escort without ownership"),
                ("unowned_caravan_reported", "Verified movement report"),
                ("unowned_caravan_terms_rewritten", "Rewritten protection terms"),
            ),
            "No recorded caravan precedent",
        )
        report_memory = remembered_label(
            (
                ("report_rule_corroborated", "Corroboration"),
                ("report_rule_timeboxed", "Shortest verified window"),
                ("report_rule_quarantined", "Quarantine disputed intelligence"),
            ),
            "No recorded report precedent",
        )
        passage_memory = remembered_label(
            (
                ("district_preparation_access", "Transparent access"),
                ("district_preparation_witness", "Public witness"),
                ("district_preparation_fallback", "Reversible fallback"),
            ),
            "No recorded passage preparation",
        )
        suggested_method = {
            "Transparent access": "Reconcile",
            "Public witness": "Witness",
            "Reversible fallback": "Hold",
        }.get(passage_memory, "Any of the three equal methods")

        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        class_name = class_definition.name if class_definition is not None else "Unselected"
        district_projection = self._district_projection(state)
        class_caution = str(district_projection["caution"])

        available_actions: list[dict[str, object]] = []
        if context is not None:
            _, stage = context
            for action in stage.actions:
                if action.verb != "service":
                    continue
                available, reason = self._story_action_availability(state, action)
                label, summary, _ = self._story_action_label(state, action)
                available_actions.append(
                    {
                        "id": action.id,
                        "label": label,
                        "summary": summary,
                        "command": self._story_action_command(action),
                        "available": available,
                        "unavailable_reason": reason,
                    }
                )
        primary_command = next(
            (
                str(action["command"])
                for action in available_actions
                if action["available"]
            ),
            "service status",
        )

        if complete:
            status = "Closed — temporary review authority expired"
        elif resolution_applied:
            status = "Claims preserved — hospice closure required"
        elif window_verified:
            status = "Public service window verified"
        elif method != "Not selected":
            status = "Review method selected — window verification required"
        elif history_compared:
            status = "Earlier precedents compared — method required"
        elif sources_traced:
            status = "Claims traced — public comparison required"
        elif memory_read:
            status = "Queue memory read — sources must be traced"
        elif accepted:
            status = "Bounded public review accepted"
        else:
            status = "No Shaklas public-service review active"

        companion, _progress = self._active_companion_context(
            state,
            self.clock.now(),
        )
        return {
            "active": active,
            "formed": formed,
            "complete": complete,
            "status": status,
            "location": "District 22, Shaklas Public Queue and hospice threshold",
            "fact": (
                "Two legitimate service claims survived one overwritten marker: an earlier household position and a current neutral cold-chain delivery."
                if memory_read or complete
                else "No queue-memory fact has been accepted yet."
            ),
            "source_state": (
                "Paper sequence and two independent witness marks support the earlier position; a cold-cell receipt supports the current carrier."
                if sources_traced or complete
                else "Sources have not yet been traced separately."
            ),
            "class_name": class_name,
            "class_caution": class_caution,
            "history": {
                "shelter": shelter_memory,
                "caravan": caravan_memory,
                "report": report_memory,
                "passage": passage_memory,
            },
            "suggested_method": suggested_method,
            "method": method,
            "method_summary": method_summary,
            "window": (
                "Verified for this public review only"
                if window_verified or complete
                else "Not yet verified"
            ),
            "unknown": "Who overwrote the marker, motive, intent, faction involvement, clinic capacity beyond this shift, medical priority, side-street control, and deeper Shaklas remain unknown.",
            "expiration": (
                "Expired when Milo Fen closed the review ledger"
                if complete
                else "Expires at hospice review closure and grants no continuing office"
                if formed
                else "Not active"
            ),
            "boundary": "This bounded public-service review preserves access to trained clinical review. It does not assign medical priority, queue office, faction or guild status, Commander authority, residency, ownership, permanent service priority, surveillance, side-street permission, or knowledge of deeper Shaklas.",
            "actions": available_actions,
            "primary_command": primary_command,
            "active_companion": (
                {
                    "id": companion.id,
                    "name": companion.name,
                    "separate_from_service": True,
                }
                if companion is not None
                else None
            ),
        }

    def _service(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        query = self._query(command.args)
        if query.casefold() in {"", "status", "list", "summary"}:
            projection = self._service_projection(state)
            history = projection["history"]
            lines = [
                f"Public service review: {projection['status']}",
                f"Location: {projection['location']}",
                f"Fact: {projection['fact']}",
                f"Sources: {projection['source_state']}",
                f"Class caution: {projection['class_name']} — {projection['class_caution']}",
                "Remembered precedents: "
                f"shelter={history['shelter']}; caravan={history['caravan']}; "
                f"report={history['report']}; passage={history['passage']}.",
                f"Suggested emphasis: {projection['suggested_method']}",
                f"Selected method: {projection['method']} — {projection['method_summary']}",
                f"Window: {projection['window']}",
                f"Unknown: {projection['unknown']}",
                f"Expiration: {projection['expiration']}",
                str(projection["boundary"]),
            ]
            companion_projection = projection["active_companion"]
            if companion_projection is not None:
                lines.append(
                    f"Active field partner: {companion_projection['name']} remains a separate companion slot."
                )
            available = [
                action for action in projection["actions"] if action["available"]
            ]
            if available:
                lines.append(
                    "Available: "
                    + _natural_list(
                        [str(action["command"]).upper() for action in available]
                    )
                    + "."
                )
            return _HandlerResult(("\n".join(lines),))
        return self._execute_story_verb(
            state,
            command,
            now,
            verb="service",
        )

    def _hospice_projection(self, state: GameState) -> dict[str, object]:
        """Project Shaklas public-threshold and borrowed-light stewardship lessons."""

        context = self._active_story_context(state)
        active_quest_id = context[0].id if context is not None else None

        threshold_records = {
            "threshold_capacity_notice_received",
            "threshold_load_read",
            "public_cell_inspected",
            "threshold_costs_compared",
            "threshold_method_reserved",
            "threshold_method_rotated",
            "threshold_method_manual",
            "return_route_verified",
            "threshold_method_applied",
            "return_route_exercised",
            "threshold_authority_expired",
        }
        formed = (
            active_quest_id == "the_threshold_has_a_cost"
            or "shaklas_threshold_cost_active" in state.flags
            or "shaklas_threshold_cost_complete" in state.flags
            or bool(threshold_records.intersection(state.story.records))
        )
        complete = "shaklas_threshold_cost_complete" in state.flags
        active = formed and not complete and (
            active_quest_id == "the_threshold_has_a_cost"
            or "shaklas_threshold_cost_active" in state.flags
        )
        accepted = (
            "shaklas_threshold_cost_accepted" in state.flags
            or "threshold_capacity_notice_received" in state.story.records
        )
        load_read = (
            "shaklas_threshold_load_read" in state.flags
            or "threshold_load_read" in state.story.records
        )
        cell_inspected = (
            "shaklas_threshold_cell_inspected" in state.flags
            or "public_cell_inspected" in state.story.records
        )
        compared = (
            "shaklas_threshold_costs_compared" in state.flags
            or "threshold_costs_compared" in state.story.records
        )
        route_verified = (
            "shaklas_threshold_return_verified" in state.flags
            or "return_route_verified" in state.story.records
        )
        applied = (
            "shaklas_threshold_method_applied" in state.flags
            or "threshold_method_applied" in state.story.records
        )
        route_tested = (
            "shaklas_threshold_return_tested" in state.flags
            or "return_route_exercised" in state.story.records
        )

        threshold_method_map = (
            (
                "shaklas_threshold_method:reserve",
                "threshold_method_reserved",
                "Reserve",
                "Dedicate the public cell for one bounded review window, then release it.",
                "Whole public allowance for one window",
            ),
            (
                "shaklas_threshold_method:rotate",
                "threshold_method_rotated",
                "Rotate",
                "Sequence privacy, receipt, and return functions with announced handoffs.",
                "Timing, coordination, and handoff discipline",
            ),
            (
                "shaklas_threshold_method:manual",
                "threshold_method_manual",
                "Manual fallback",
                "Use paper privacy, a hand-stamped receipt, and readable route markers.",
                "Labor, attention, and visible upkeep",
            ),
        )
        method = "Not selected"
        method_summary = "No temporary capacity method has been selected."
        cost = "Not yet declared"
        for flag, record_id, label, summary, declared_cost in threshold_method_map:
            if flag in state.flags or record_id in state.story.records:
                method = label
                method_summary = summary
                cost = declared_cost
                break

        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        class_id = state.character.build.class_id or ""
        class_name = class_definition.name if class_definition is not None else "Unselected"
        threshold_cautions = {
            "chosen_one": "Do not confuse cell strain with a source-energy event or proof that stronger bodies should bear more risk.",
            "messenger": "A narrow threshold can concentrate crowd pressure; containment must preserve an exit.",
            "boss": "Scarce capacity creates leverage, but not ownership of access, patients, or future terms.",
            "fixer": "A single support cell is a dependency; name the substitute before promising continuity.",
            "zealot": "Do not turn a temporary capacity choice into an unquestioned duty after conditions change.",
            "devout": "Care continuity is valuable, but public service is not clinical authority or sacred entitlement.",
            "protector": "Preserve privacy without using protection as a reason to trap people at the threshold.",
            "guide": "The least harmful path keeps privacy, receipt, and retreat available while staff keep medical judgment.",
            "system": "Keep public controls isolated from clinical systems; a shared interface must not become a route inward.",
            "engineer": "Measure duty cycle, recovery, and manual fallback before treating an aging cell as reliable capacity.",
            "infiltrator": "A beacon should reveal the route, not identity, diagnosis, or destination history.",
            "sniper": "Keep every return turn readable without turning the walkway into a surveillance lane.",
            "born_assassin": "One cell is one failure point; verify a fast, nonviolent exit before pressure arrives.",
            "soldier": "Orderly release is useful, but temporary formation control is not Commander authority over civilians.",
            "medic": "Public threshold capacity cannot determine diagnosis, urgency, treatment, or who is seen first.",
        }
        class_caution = threshold_cautions.get(
            class_id,
            "Keep public capacity separate from clinical authority and preserve a reversible return route.",
        )

        stewardship_records = {
            "borrowed_light_notice_received",
            "public_cell_expiration_read",
            "public_cell_claims_traced",
            "stewardship_terms_compared",
            "borrowed_light_method_repaired",
            "borrowed_light_method_shared",
            "borrowed_light_method_rested",
            "stewardship_terms_verified",
            "borrowed_light_method_applied",
            "fallback_service_exercised",
            "borrowed_light_authority_expired",
        }
        stewardship_formed = (
            active_quest_id == "the_light_is_borrowed"
            or "shaklas_borrowed_light_active" in state.flags
            or "shaklas_borrowed_light_complete" in state.flags
            or bool(stewardship_records.intersection(state.story.records))
        )
        stewardship_complete = "shaklas_borrowed_light_complete" in state.flags
        stewardship_active = stewardship_formed and not stewardship_complete and (
            active_quest_id == "the_light_is_borrowed"
            or "shaklas_borrowed_light_active" in state.flags
        )
        stewardship_accepted = (
            "shaklas_borrowed_light_accepted" in state.flags
            or "borrowed_light_notice_received" in state.story.records
        )
        expiration_read = (
            "shaklas_borrowed_light_expiration_read" in state.flags
            or "public_cell_expiration_read" in state.story.records
        )
        claims_traced = (
            "shaklas_borrowed_light_claims_traced" in state.flags
            or "public_cell_claims_traced" in state.story.records
        )
        terms_compared = (
            "shaklas_borrowed_light_terms_compared" in state.flags
            or "stewardship_terms_compared" in state.story.records
        )
        terms_verified = (
            "shaklas_borrowed_light_terms_verified" in state.flags
            or "stewardship_terms_verified" in state.story.records
        )
        stewardship_applied = (
            "shaklas_borrowed_light_method_applied" in state.flags
            or "borrowed_light_method_applied" in state.story.records
        )
        fallback_tested = (
            "shaklas_borrowed_light_fallback_tested" in state.flags
            or "fallback_service_exercised" in state.story.records
        )
        stewardship_method_map = (
            (
                "shaklas_borrowed_light_method:repair",
                "borrowed_light_method_repaired",
                "Open repair",
                "Use declared public salvage, publish the work receipt, verify output, and grant no title.",
                "Public salvage and one maintenance interval",
                "Abort if measured output fails; the repair receipt creates no ownership.",
            ),
            (
                "shaklas_borrowed_light_method:share",
                "borrowed_light_method_shared",
                "Shared window",
                "Divide one shift among public functions with visible times, refusal rights, and automatic expiration.",
                "Less continuous access for each public function",
                "Any participant may refuse; the schedule expires after one shift.",
            ),
            (
                "shaklas_borrowed_light_method:rest",
                "borrowed_light_method_rested",
                "Rest and redirect",
                "Let the cell cool while manual public tools and the neutral route carry the shift.",
                "Electronic convenience pauses during recovery",
                "Release the cooling latch only after a new bounded review.",
            ),
        )
        stewardship_method = "Not selected"
        stewardship_summary = "No borrowed-light method has been selected."
        stewardship_cost = "Not yet declared"
        stewardship_refusal = "Not yet declared"
        for flag, record_id, label, summary, declared_cost, refusal in stewardship_method_map:
            if flag in state.flags or record_id in state.story.records:
                stewardship_method = label
                stewardship_summary = summary
                stewardship_cost = declared_cost
                stewardship_refusal = refusal
                break
        stewardship_cautions = {
            "chosen_one": "Do not treat raw strength or radiant endurance as proof the cell should run beyond its measured public limit.",
            "messenger": "A shared schedule must not become a choke point that traps people until their assigned window.",
            "boss": "Paying for parts or negotiating time does not create ownership of the cell or the people who depend on it.",
            "fixer": "Name who supplies parts, who verifies the work, and what happens when either support link fails.",
            "zealot": "A repair pledge must expire at its stated task; devotion is not perpetual authority.",
            "devout": "Keep records and human support available while power changes; care cannot vanish between service windows.",
            "protector": "Protect the cell from overload without turning protection into exclusion from the public route.",
            "guide": "Preserve privacy, accountable receipt, and retreat while minimizing forced dependence on one device.",
            "system": "No diagnostic line, shared schedule, or repair interface may bridge into clinical systems.",
            "engineer": "A fitted part needs measured output and a test cycle; a successful wrench turn is not proof of sustained capacity.",
            "infiltrator": "Open receipts should disclose parts and times, not identities, diagnoses, or movement histories.",
            "sniper": "The service window must be precise enough to act on and short enough to reverify before conditions drift.",
            "born_assassin": "Every method needs a clean abort condition before equipment strain becomes a trap.",
            "soldier": "Shared public terms need a visible handoff order, but temporary coordination is not Commander authority over civilians.",
            "medic": "Public power can preserve access but cannot decide diagnosis, urgency, treatment, or triage.",
        }
        stewardship_class_caution = stewardship_cautions.get(
            class_id,
            "Separate repair, sharing, rest, ownership, clinical authority, and the neutral fallback.",
        )

        influence_records = {
            "influence_offer_received",
            "gift_claims_traced",
            "gift_terms_compared",
            "gift_method_unconditional",
            "gift_method_contracted",
            "gift_method_refused",
            "gift_refusal_verified",
            "gift_source_acknowledged",
            "independent_fallback_exercised",
            "gift_terms_authority_expired",
        }
        influence_formed = (
            active_quest_id == "the_name_on_the_gift"
            or "shaklas_gift_terms_active" in state.flags
            or "shaklas_gift_terms_complete" in state.flags
            or bool(influence_records.intersection(state.story.records))
        )
        influence_complete = "shaklas_gift_terms_complete" in state.flags
        influence_active = influence_formed and not influence_complete and (
            active_quest_id == "the_name_on_the_gift"
            or "shaklas_gift_terms_active" in state.flags
        )
        influence_accepted = (
            "shaklas_gift_terms_accepted" in state.flags
            or "influence_offer_received" in state.story.records
        )
        influence_claims_traced = (
            "shaklas_gift_claims_traced" in state.flags
            or "gift_claims_traced" in state.story.records
        )
        influence_terms_compared = (
            "shaklas_gift_terms_compared" in state.flags
            or "gift_terms_compared" in state.story.records
        )
        influence_refusal_verified = (
            "shaklas_gift_refusal_verified" in state.flags
            or "gift_refusal_verified" in state.story.records
        )
        influence_applied = (
            "shaklas_gift_method_applied" in state.flags
            or bool(
                {
                    "gift_method_unconditional",
                    "gift_method_contracted",
                    "gift_method_refused",
                }.intersection(state.story.records)
            )
        )
        influence_source_acknowledged = (
            "shaklas_gift_source_acknowledged" in state.flags
            or "gift_source_acknowledged" in state.story.records
        )
        influence_fallback_tested = (
            "shaklas_gift_fallback_tested" in state.flags
            or "independent_fallback_exercised" in state.story.records
        )
        influence_method_map = (
            (
                "shaklas_gift_method:gift",
                "gift_method_unconditional",
                "Unconditional contribution",
                "Accept one coupler only after every plaque, priority, sponsorship, data, and future-access claim is waived.",
                "Talin contributes one part; the public owes no payment or continuing preference.",
                "Talin remains the acknowledged source, not an owner, sponsor, priority holder, or data gatekeeper.",
                "The queue may refuse this or any future contribution without blacklist, lost access, clinical consequence, or hidden debt.",
            ),
            (
                "shaklas_gift_method:contract",
                "gift_method_contracted",
                "Bounded public purchase",
                "Use one fixed public consideration for one coupler and close every future influence claim at receipt.",
                "One fixed consideration from the neutral public ledger for one part.",
                "Talin supplied one purchased part; payment ends at receipt and creates no sponsor or title.",
                "Either side may decline future transactions; this receipt grants no renewal, priority, or exclusive data.",
            ),
            (
                "shaklas_gift_method:refuse",
                "gift_method_refused",
                "Refuse and salvage",
                "Decline the conditional crate and adapt slower public salvage without debt, blacklist, or loss of access.",
                "Additional public labor, adaptation time, and a slower replacement path.",
                "Talin's conditional crate was refused; neutral public salvage supplied the replacement path.",
                "Refusal is final for this offer and causes no blacklist, debt, clinical consequence, or loss of the public route.",
            ),
        )
        influence_method = "Not selected"
        influence_summary = "No supplier-offer method has been selected."
        influence_consideration = "Not yet declared"
        influence_source_line = "No source line has been published."
        influence_refusal = "Not yet verified"
        for (
            flag,
            record_id,
            label,
            summary,
            consideration,
            source_line,
            refusal,
        ) in influence_method_map:
            if flag in state.flags or record_id in state.story.records:
                influence_method = label
                influence_summary = summary
                influence_consideration = consideration
                influence_source_line = source_line
                influence_refusal = refusal
                break
        influence_cautions = {
            "chosen_one": "A powerful or scarce part can help the public without proving that its source deserves title, obedience, or future access.",
            "messenger": "Do not let one supplier's first-notice privilege become a choke point for later public movement or protection.",
            "boss": "Price, leverage, and source acknowledgement are negotiable; ownership of the queue, cell, hospice, and people is not.",
            "fixer": "A useful supply chain becomes dependency when side terms outlive the part; preserve another repair and salvage path.",
            "zealot": "Gratitude for a contribution must not harden into unquestioned duty after the recorded shift ends.",
            "devout": "Acknowledging help does not create sacred entitlement to later service, data, priority, or clinical decisions.",
            "protector": "Protection against shortage cannot justify locking future access behind one supplier or sponsor.",
            "guide": "The least harmful method preserves a real refusal, an honest source line, and an independent fallback for the next shift.",
            "system": "No performance-data request, first-notice channel, or supplier interface may bridge into identities, movement histories, or clinical systems.",
            "engineer": "Inspect compatibility, provenance, test limits, and salvage alternatives; fitting a part does not convey title to the machine or route.",
            "infiltrator": "A public source line may name the supplier without becoming a surveillance feed, exclusive failure alert, or identity ledger.",
            "sniper": "Keep the offer window and consideration precise; a permanent plaque or open-ended preference is too broad to verify.",
            "born_assassin": "One supplier is one failure point; prove a fast refusal and independent salvage route before accepting the part.",
            "soldier": "Sponsorship does not create command authority, and a supplier's schedule preference does not become an order to civilians.",
            "medic": "A coupler, donation, purchase, or refusal cannot determine diagnosis, urgency, treatment, triage, or who receives care first.",
        }
        influence_class_caution = influence_cautions.get(
            class_id,
            "Separate source, consideration, title, sponsorship, priority, data access, refusal, fallback, and clinical authority.",
        )

        receipt_records = {
            "receipt_copy_discovered",
            "receipt_scope_traced",
            "receipt_methods_compared",
            "receipt_method_minimized",
            "receipt_method_timeboxed",
            "receipt_method_segmented",
            "receipt_private_boundary_verified",
            "receipt_scope_published",
            "receipt_expiration_tested",
            "receipt_scope_authority_expired",
        }
        receipt_formed = (
            active_quest_id == "the_receipt_travels_without_you"
            or "shaklas_receipt_scope_active" in state.flags
            or "shaklas_receipt_scope_complete" in state.flags
            or bool(receipt_records.intersection(state.story.records))
        )
        receipt_complete = "shaklas_receipt_scope_complete" in state.flags
        receipt_active = receipt_formed and not receipt_complete and (
            active_quest_id == "the_receipt_travels_without_you"
            or "shaklas_receipt_scope_active" in state.flags
        )
        receipt_accepted = (
            "shaklas_receipt_scope_accepted" in state.flags
            or "receipt_copy_discovered" in state.story.records
        )
        receipt_traced = (
            "shaklas_receipt_scope_traced" in state.flags
            or "receipt_scope_traced" in state.story.records
        )
        receipt_compared = (
            "shaklas_receipt_methods_compared" in state.flags
            or "receipt_methods_compared" in state.story.records
        )
        receipt_boundary_verified = (
            "shaklas_receipt_private_boundary_verified" in state.flags
            or "receipt_private_boundary_verified" in state.story.records
        )
        receipt_applied = "shaklas_receipt_method_applied" in state.flags
        receipt_scope_published = (
            "shaklas_receipt_scope_published" in state.flags
            or "receipt_scope_published" in state.story.records
        )
        receipt_expiration_tested = (
            "shaklas_receipt_expiration_tested" in state.flags
            or "receipt_expiration_tested" in state.story.records
        )
        receipt_method_map = (
            (
                "shaklas_receipt_method:minimize",
                "receipt_method_minimized",
                "Minimal public digest",
                "Keep only method, immediate source or neutral fallback, consideration or refusal, one-shift scope, and expiration.",
                "A bounded digest remains public; no detailed copy or private field remains active.",
            ),
            (
                "shaklas_receipt_method:timebox",
                "receipt_method_timeboxed",
                "Time-limited review",
                "Keep the complete public transaction record for one announced review window, then reduce it to the minimal digest.",
                "Detailed public review ends at the declared window; the minimal digest remains.",
            ),
            (
                "shaklas_receipt_method:segment",
                "receipt_method_segmented",
                "Segmented public ledger",
                "Keep public transaction facts reviewable while operational and clinical records remain isolated and unlinked.",
                "Public transaction facts remain; private, movement, operational, and clinical ledgers stay separate.",
            ),
        )
        receipt_method = "Not selected"
        receipt_summary = "No receipt-scope method has been selected."
        receipt_lifecycle = "Not yet declared"
        for flag, record_id, label, summary, lifecycle in receipt_method_map:
            if flag in state.flags or record_id in state.story.records:
                receipt_method = label
                receipt_summary = summary
                receipt_lifecycle = lifecycle
                break
        receipt_cautions = {
            "chosen_one": "A visible or important record can remain accountable without becoming permanent proof of authority over everyone near it.",
            "messenger": "A copied receipt must not become a command channel that directs movement after its declared window ends.",
            "boss": "Transaction evidence can support negotiation, but it grants no ownership, sponsorship, title, or continuing market privilege.",
            "fixer": "Preserve source, consideration or refusal, scope, and rollback while keeping recipient identity and private service detail out of the public copy.",
            "zealot": "Public witness does not justify endless publication; the declared task and review window must still end.",
            "devout": "Keep the truth reviewable without turning vulnerable people into permanent entries in someone else's record system.",
            "protector": "Privacy should shield people, not erase the accountable transaction facts needed to challenge misuse.",
            "guide": "Retain the smallest fact set that supports review, refusal, and correction while avoiding unnecessary exposure.",
            "system": "No public receipt field may bridge into identity, movement history, private operations, or isolated clinical systems.",
            "engineer": "A record needs a declared version, scope, and expiration; a cropped copy is incomplete even when every surviving field is accurate.",
            "infiltrator": "Uncontrolled copies can become movement intelligence; exclude identity and route history before publishing anything reusable.",
            "sniper": "The review window and covered transaction must be precise enough that a copied line cannot drift into permanent authority.",
            "born_assassin": "Every unnecessary field and unexpired copy is another attack surface; prove the reduction or separation actually occurs.",
            "soldier": "A public receipt is not an order, access roster, sponsorship pass, policing list, or Commander instrument.",
            "medic": "No receipt may reveal diagnosis, urgency, treatment, triage, clinical priority, or who received care first.",
        }
        receipt_class_caution = receipt_cautions.get(
            class_id,
            "Separate public accountability, private identity, movement history, clinical information, copy scope, expiration, and authority.",
        )

        available_actions: list[dict[str, object]] = []
        if context is not None:
            _, stage = context
            for story_action in stage.actions:
                if story_action.verb != "hospice":
                    continue
                available, reason = self._story_action_availability(state, story_action)
                label, summary, _ = self._story_action_label(state, story_action)
                available_actions.append(
                    {
                        "id": story_action.id,
                        "label": label,
                        "summary": summary,
                        "command": self._story_action_command(story_action),
                        "available": available,
                        "unavailable_reason": reason,
                    }
                )
        primary_command = next(
            (
                str(story_action["command"])
                for story_action in available_actions
                if story_action["available"]
            ),
            "hospice status",
        )

        if complete:
            status = "Closed — temporary threshold authority expired"
        elif route_tested:
            status = "Return route exercised — public ledger closure required"
        elif applied:
            status = "Capacity method applied — return route test required"
        elif route_verified:
            status = "Return route verified — selected method may be applied"
        elif method != "Not selected":
            status = "Capacity method selected — reversible route verification required"
        elif compared:
            status = "Costs compared — temporary method required"
        elif cell_inspected:
            status = "Public cell inspected — compare the three costs"
        elif load_read:
            status = "Public load separated — inspect the aging cell"
        elif accepted:
            status = "Bounded threshold-capacity review accepted"
        else:
            status = "No Shaklas threshold-capacity review active"

        if stewardship_complete:
            stewardship_status = "Closed — borrowed-light authority expired"
        elif fallback_tested:
            stewardship_status = "Neutral fallback exercised — public closure required"
        elif stewardship_applied:
            stewardship_status = "Stewardship applied — neutral fallback test required"
        elif terms_verified:
            stewardship_status = "Terms verified — selected method may be applied"
        elif stewardship_method != "Not selected":
            stewardship_status = "Method selected — terms and refusal condition must be verified"
        elif terms_compared:
            stewardship_status = "Stewardship costs compared — select repair, share, or rest"
        elif claims_traced:
            stewardship_status = "Parts, time, and title separated — compare stewardship terms"
        elif expiration_read:
            stewardship_status = "Expiration read — trace public claims with Davin"
        elif stewardship_accepted:
            stewardship_status = "Bounded borrowed-light review accepted"
        else:
            stewardship_status = "No borrowed-light stewardship active"

        if influence_complete:
            influence_status = "Closed — supplier-offer authority expired"
        elif influence_fallback_tested:
            influence_status = "Independent fallback exercised — public closure required"
        elif influence_source_acknowledged:
            influence_status = "Source acknowledged without title — independent fallback test required"
        elif influence_applied:
            influence_status = "Selected part method applied — public source line required"
        elif influence_refusal_verified:
            influence_status = "Refusal and fallback verified — selected method may be applied"
        elif influence_method != "Not selected":
            influence_status = "Method selected — refusal and independence must be verified"
        elif influence_terms_compared:
            influence_status = "Terms compared — select contribution, purchase, or refusal"
        elif influence_claims_traced:
            influence_status = "Part and influence claims separated — compare public terms"
        elif influence_accepted:
            influence_status = "Bounded supplier-offer review accepted"
        else:
            influence_status = "No supplier-offer review active"

        if receipt_complete:
            receipt_status = "Closed — receipt-review authority expired"
        elif receipt_expiration_tested:
            receipt_status = "Expiration exercised — public queue closure required"
        elif receipt_scope_published:
            receipt_status = "Bounded scope published — expiration test required"
        elif receipt_applied:
            receipt_status = "Publication method applied — scope line required"
        elif receipt_boundary_verified:
            receipt_status = "Private boundary verified — selected method may be applied"
        elif receipt_method != "Not selected":
            receipt_status = "Method selected — private and clinical boundary must be verified"
        elif receipt_compared:
            receipt_status = "Publication methods compared — select minimal, timeboxed, or segmented"
        elif receipt_traced:
            receipt_status = "Surviving fact and cropped context separated — compare publication scope"
        elif receipt_accepted:
            receipt_status = "Bounded copied-receipt review accepted"
        else:
            receipt_status = "No copied-receipt review active"

        companion, _progress = self._active_companion_context(state, self.clock.now())
        companion_projection = (
            {
                "id": companion.id,
                "name": companion.name,
                "separate_from_hospice": True,
            }
            if companion is not None
            else None
        )
        return {
            # Threshold contract retained for v0.31 compatibility.
            "active": active,
            "formed": formed,
            "complete": complete,
            "status": status,
            "location": "Shaklas public hospice threshold and return route",
            "fact": (
                "One aging public cell supports privacy screen, receipt seal, and return beacons outside the isolated clinical systems."
                if load_read or complete
                else "No threshold-capacity fact has been accepted yet."
            ),
            "public_functions": (
                ["Privacy screen", "Accountable receipt seal", "Return beacons"]
                if load_read or complete
                else []
            ),
            "isolation": "Clinical systems, diagnosis, treatment, and medical priority remain with trained hospice staff.",
            "class_name": class_name,
            "class_caution": class_caution,
            "method": method,
            "method_summary": method_summary,
            "cost": cost,
            "return_route": (
                "Exercised from threshold to public queue"
                if route_tested or complete
                else "Verified but not yet exercised"
                if route_verified
                else "Not yet verified"
            ),
            "unknown": "The cell's donor, original installer, exact remaining lifespan, future clinic capacity, medical priority, faction involvement, and conditions deeper in Shaklas remain unknown.",
            "expiration": (
                "Expired when Kessa Noll closed the public return ledger"
                if complete
                else "Expires at public-ledger closure and grants no continuing office"
                if formed
                else "Not active"
            ),
            "boundary": "This lesson manages only temporary nonclinical public capacity. It grants no diagnosis, treatment, medical priority, hospice office, ownership, donor claim, faction or guild status, Commander authority, residency, surveillance, permanent service priority, side-street permission, or knowledge of deeper Shaklas.",
            "actions": available_actions,
            "primary_command": primary_command,
            "active_companion": companion_projection,
            # v0.32 borrowed-light continuation.
            "phase": (
                "receipt_scope"
                if receipt_formed
                else "gift_terms"
                if influence_formed
                else "borrowed_light"
                if stewardship_formed
                else "threshold"
            ),
            "stewardship_formed": stewardship_formed,
            "stewardship_active": stewardship_active,
            "stewardship_complete": stewardship_complete,
            "stewardship_status": stewardship_status,
            "stewardship_location": "Shaklas borrowed-light notice, open parts bench, neutral service turn, and public queue",
            "stewardship_fact": (
                "The previous public allowance expired. One bounded output trace supports repair, sharing, or rest, while the owner line remains blank and clinical systems remain isolated."
                if expiration_read or stewardship_complete
                else "No borrowed-light expiration fact has been accepted yet."
            ),
            "stewardship_class_caution": stewardship_class_caution,
            "stewardship_method": stewardship_method,
            "stewardship_summary": stewardship_summary,
            "stewardship_cost": stewardship_cost,
            "stewardship_refusal": stewardship_refusal,
            "stewardship_terms": (
                "Verified: cost, refusal or abort condition, one-shift expiration, clinical separation, and neutral fallback"
                if terms_verified or stewardship_complete
                else "Not yet verified"
            ),
            "stewardship_fallback": (
                "Exercised from neutral service turn through the outer steps to the public queue"
                if fallback_tested or stewardship_complete
                else "Available but not yet exercised"
                if stewardship_applied
                else "Not yet tested"
            ),
            "stewardship_unknown": "The donor, original installer, owner, exact remaining cell life, cause of wear, future clinical capacity, faction involvement, and conditions deeper in Shaklas remain unknown.",
            "stewardship_expiration": (
                "Expired when Kessa Noll closed the borrowed-light ledger"
                if stewardship_complete
                else "Expires after one declared shift and grants no continuing switch, title, or office"
                if stewardship_formed
                else "Not active"
            ),
            "stewardship_boundary": "Borrowed-light stewardship covers only open repair, revocable sharing, deliberate rest, public receipts, and a neutral fallback. It grants no donor identity, ownership, diagnosis, treatment, triage, medical priority, hospice office, faction or guild status, Commander authority, residency, surveillance, side-street access, permanent service priority, or deeper Shaklas knowledge.",
            "stewardship_actions": (
                available_actions if active_quest_id == "the_light_is_borrowed" else []
            ),
            "stewardship_primary_command": (
                primary_command if active_quest_id == "the_light_is_borrowed" else "hospice status"
            ),
            # v0.33 source acknowledgement and supplier-influence continuation.
            "influence_formed": influence_formed,
            "influence_active": influence_active,
            "influence_complete": influence_complete,
            "influence_status": influence_status,
            "influence_location": "Shaklas public offer rail, terms table, neutral salvage locker, service turn, outer steps, and public queue",
            "influence_fact": (
                "One compatible coupler is present beside four proposed continuing claims: a permanent nameplate, first notice, schedule preference, and access to public failure data. The part and those claims are separate."
                if influence_claims_traced or influence_complete
                else "No supplier-offer fact has been accepted yet."
            ),
            "influence_class_caution": influence_class_caution,
            "influence_method": influence_method,
            "influence_summary": influence_summary,
            "influence_consideration": influence_consideration,
            "influence_refusal": influence_refusal,
            "influence_source_line": (
                influence_source_line
                if influence_source_acknowledged or influence_complete
                else "Not yet published"
            ),
            "influence_terms": (
                "Verified: source, consideration or refusal, expiry, no title, no sponsorship, no priority, no exclusive data, clinical separation, and independent fallback"
                if influence_refusal_verified or influence_complete
                else "Not yet verified"
            ),
            "influence_fallback": (
                "Exercised through neutral salvage, the public service turn, outer steps, and public queue"
                if influence_fallback_tested or influence_complete
                else "Available but not yet exercised"
                if influence_applied
                else "Not yet tested"
            ),
            "influence_unknown": "Talin's upstream source, faction affiliation, future availability, original owner or donor, exact remaining cell life, clinical capacity and priority, conditions deeper in Shaklas, and any motive beyond the displayed side terms remain unknown.",
            "influence_expiration": (
                "Expired when Kessa Noll closed the supplier-offer ledger"
                if influence_complete
                else "Expires at public-ledger closure and grants no continuing review, title, sponsorship, priority, or data right"
                if influence_formed
                else "Not active"
            ),
            "influence_boundary": "The supplier-offer review covers only one public part, its source, visible consideration or refusal, time-limited terms, source acknowledgement, and independent salvage. It grants no owner, donor identity, sponsor, plaque title, first-notice privilege, schedule preference, exclusive data, diagnosis, treatment, triage, medical priority, hospice office, faction or guild status, Commander authority, residency, surveillance, permanent service priority, side-street permission, or deeper Shaklas knowledge.",
            "influence_actions": (
                available_actions if active_quest_id == "the_name_on_the_gift" else []
            ),
            "influence_primary_command": (
                primary_command if active_quest_id == "the_name_on_the_gift" else "hospice status"
            ),
            # v0.34 public-receipt scope, privacy, and expiration continuation.
            "receipt_formed": receipt_formed,
            "receipt_active": receipt_active,
            "receipt_complete": receipt_complete,
            "receipt_status": receipt_status,
            "receipt_location": "Shaklas public receipt copy rail, scope desk, public ledger gate, neutral service turn, outer steps, and public queue",
            "receipt_fact": (
                "One copied public receipt retains its immediate source and transaction method, while its declared one-shift scope and expiration line were cropped. The copy contains no identity, diagnosis, or clinical record."
                if receipt_traced or receipt_complete
                else "No copied-receipt fact has been accepted yet."
            ),
            "receipt_class_caution": receipt_class_caution,
            "receipt_method": receipt_method,
            "receipt_summary": receipt_summary,
            "receipt_transaction_line": (
                influence_source_line
                if influence_complete or receipt_formed
                else "No prior public transaction line is available."
            ),
            "receipt_lifecycle": receipt_lifecycle,
            "receipt_private_boundary": (
                "Verified: no recipient identity, diagnosis, clinical priority, movement history, policing list, private operational note, or cross-ledger link"
                if receipt_boundary_verified or receipt_complete
                else "Not yet verified"
            ),
            "receipt_scope_line": (
                f"{receipt_method}: one public-cell transaction; immediate source or neutral fallback, consideration or refusal, exclusions, and expiration only."
                if receipt_scope_published or receipt_complete
                else "Not yet published"
            ),
            "receipt_expiration": (
                "Exercised: detail expired, reduced to the bounded digest, or remained segmented exactly as declared"
                if receipt_expiration_tested or receipt_complete
                else "Declared but not yet exercised"
                if receipt_scope_published
                else "Not yet declared"
            ),
            "receipt_unknown": "Who copied or cropped the receipt, why it was copied, how many copies exist, where any other copy traveled, whether a faction used it, and conditions deeper in Shaklas remain unknown.",
            "receipt_boundary": "The receipt-scope review preserves one accountable public transaction record. It grants no recipient identity, diagnosis, treatment, triage, clinical priority, movement tracking, policing authority, ownership, donor identity, sponsorship, title, first-notice privilege, schedule preference, exclusive data, faction or guild status, Commander authority, residency, surveillance, permanent access, side-street permission, deeper Shaklas knowledge, culprit attribution, or continuing copy authority.",
            "receipt_actions": (
                available_actions if active_quest_id == "the_receipt_travels_without_you" else []
            ),
            "receipt_primary_command": (
                primary_command if active_quest_id == "the_receipt_travels_without_you" else "hospice status"
            ),
        }

    def _hospice(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        query = self._query(command.args)
        if query.casefold() in {"", "status", "list", "summary"}:
            projection = self._hospice_projection(state)
            if projection["receipt_formed"]:
                lines = [
                    f"Public receipt: {projection['receipt_status']}",
                    f"Location: {projection['receipt_location']}",
                    f"Fact: {projection['receipt_fact']}",
                    f"Prior transaction line: {projection['receipt_transaction_line']}",
                    f"Class caution: {projection['class_name']} — {projection['receipt_class_caution']}",
                    f"Selected method: {projection['receipt_method']} — {projection['receipt_summary']}",
                    f"Detail lifecycle: {projection['receipt_lifecycle']}",
                    f"Private and clinical boundary: {projection['receipt_private_boundary']}",
                    f"Published scope: {projection['receipt_scope_line']}",
                    f"Expiration: {projection['receipt_expiration']}",
                    f"Unknown: {projection['receipt_unknown']}",
                    str(projection["receipt_boundary"]),
                    f"Supplier-offer history: {projection['influence_status']}; prior method {projection['influence_method']}.",
                ]
                actions = projection["receipt_actions"]
            elif projection["influence_formed"]:
                lines = [
                    f"Supplier offer: {projection['influence_status']}",
                    f"Location: {projection['influence_location']}",
                    f"Fact: {projection['influence_fact']}",
                    "Public owner line: Blank by design; supply, acknowledgement, payment, repair, or scheduling grants no title.",
                    "Sponsorship line: None accepted; a source name is not a sponsor, pass, faction mark, or command office.",
                    f"Public functions: {_natural_list([str(value) for value in projection['public_functions']]) if projection['public_functions'] else 'privacy screen, accountable receipt seal, and return beacons'}.",
                    f"Isolation: {projection['isolation']}",
                    f"Class caution: {projection['class_name']} — {projection['influence_class_caution']}",
                    f"Selected method: {projection['influence_method']} — {projection['influence_summary']}",
                    f"Consideration: {projection['influence_consideration']}",
                    f"Refusal: {projection['influence_refusal']}",
                    f"Public source line: {projection['influence_source_line']}",
                    f"Terms: {projection['influence_terms']}",
                    f"Independent fallback: {projection['influence_fallback']}",
                    f"Unknown: {projection['influence_unknown']}",
                    f"Expiration: {projection['influence_expiration']}",
                    str(projection["influence_boundary"]),
                    f"Borrowed-light history: {projection['stewardship_status']}; prior method {projection['stewardship_method']}.",
                ]
                actions = projection["influence_actions"]
            elif projection["stewardship_formed"]:
                lines = [
                    f"Borrowed light: {projection['stewardship_status']}",
                    f"Location: {projection['stewardship_location']}",
                    f"Fact: {projection['stewardship_fact']}",
                    "Public owner line: Blank by design; repair, payment, parts supply, or scheduling grants no title.",
                    f"Public functions: {_natural_list([str(value) for value in projection['public_functions']]) if projection['public_functions'] else 'privacy screen, accountable receipt seal, and return beacons'}.",
                    f"Isolation: {projection['isolation']}",
                    f"Class caution: {projection['class_name']} — {projection['stewardship_class_caution']}",
                    f"Selected method: {projection['stewardship_method']} — {projection['stewardship_summary']}",
                    f"Declared cost: {projection['stewardship_cost']}",
                    f"Refusal/abort: {projection['stewardship_refusal']}",
                    f"Terms: {projection['stewardship_terms']}",
                    f"Neutral fallback: {projection['stewardship_fallback']}",
                    f"Unknown: {projection['stewardship_unknown']}",
                    f"Expiration: {projection['stewardship_expiration']}",
                    str(projection["stewardship_boundary"]),
                    f"Threshold history: {projection['status']}; prior method {projection['method']}.",
                ]
                actions = projection["stewardship_actions"]
            else:
                functions = projection["public_functions"]
                lines = [
                    f"Hospice threshold: {projection['status']}",
                    f"Location: {projection['location']}",
                    f"Fact: {projection['fact']}",
                    "Public functions: "
                    + (_natural_list([str(value) for value in functions]) if functions else "not yet separated")
                    + ".",
                    f"Isolation: {projection['isolation']}",
                    f"Class caution: {projection['class_name']} — {projection['class_caution']}",
                    f"Selected method: {projection['method']} — {projection['method_summary']}",
                    f"Declared cost: {projection['cost']}",
                    f"Return route: {projection['return_route']}",
                    f"Unknown: {projection['unknown']}",
                    f"Expiration: {projection['expiration']}",
                    str(projection["boundary"]),
                ]
                actions = projection["actions"]
            companion_projection = projection["active_companion"]
            if companion_projection is not None:
                lines.append(
                    f"Active field partner: {companion_projection['name']} remains a separate companion slot and receives no clinical, repair, ownership, sharing, supplier, sponsorship, data, or threshold authority."
                )
            available = [story_action for story_action in actions if story_action["available"]]
            if available:
                lines.append(
                    "Available: "
                    + _natural_list(
                        [str(story_action["command"]).upper() for story_action in available]
                    )
                    + "."
                )
            return _HandlerResult(("\n".join(lines),))
        return self._execute_story_verb(state, command, now, verb="hospice")

    def _appeal_projection(self, state: GameState) -> dict[str, object]:
        """Project the source-backed public-index correction lesson."""

        context = self._active_story_context(state)
        active_quest_id = context[0].id if context is not None else None
        appeal_records = {
            "appeal_mislabel_received",
            "appeal_index_inspected",
            "appeal_methods_compared",
            "appeal_method_annotated",
            "appeal_method_superseded",
            "appeal_method_held",
            "appeal_source_boundary_verified",
            "appeal_correction_published",
            "appeal_reliance_tested",
            "appeal_authority_expired",
        }
        formed = (
            active_quest_id == "the_appeal_is_not_a_verdict"
            or "shaklas_appeal_active" in state.flags
            or "shaklas_appeal_complete" in state.flags
            or bool(appeal_records.intersection(state.story.records))
        )
        complete = "shaklas_appeal_complete" in state.flags
        active = formed and not complete and (
            active_quest_id == "the_appeal_is_not_a_verdict"
            or "shaklas_appeal_active" in state.flags
        )
        accepted = (
            "shaklas_appeal_accepted" in state.flags
            or "appeal_mislabel_received" in state.story.records
        )
        inspected = (
            "shaklas_appeal_index_inspected" in state.flags
            or "appeal_index_inspected" in state.story.records
        )
        compared = (
            "shaklas_appeal_methods_compared" in state.flags
            or "appeal_methods_compared" in state.story.records
        )
        source_verified = (
            "shaklas_appeal_source_verified" in state.flags
            or "appeal_source_boundary_verified" in state.story.records
        )
        applied = "shaklas_appeal_method_applied" in state.flags or bool(
            {"appeal_method_annotated", "appeal_method_superseded", "appeal_method_held"}
            .intersection(state.story.records)
        )
        published = (
            "shaklas_appeal_correction_published" in state.flags
            or "appeal_correction_published" in state.story.records
        )
        reliance_tested = (
            "shaklas_appeal_reliance_tested" in state.flags
            or "appeal_reliance_tested" in state.story.records
        )

        source_line = "The prior supplier outcome is not yet available; restore the bounded source ledger before correcting the index."
        supplier_method = "Unknown"
        if "gift_method_unconditional" in state.story.records:
            supplier_method = "Unconditional contribution"
            source_line = "An unconditional contribution was accepted with influence waived; no sponsorship, title, permanent access, or continuing authority was created."
        elif "gift_method_contracted" in state.story.records:
            supplier_method = "Bounded public purchase"
            source_line = "A bounded public purchase recorded consideration and closure; no sponsorship, title, permanent access, or continuing authority was created."
        elif "gift_method_refused" in state.story.records:
            supplier_method = "Offer refused; neutral salvage used"
            source_line = "The conditional supplier offer was refused and neutral salvage was used; no sponsorship, title, permanent access, or continuing authority was created."

        remedy = "Not selected"
        remedy_summary = "No public correction remedy has been selected."
        operative_status = "The unsupported label remains visible and uncorrected."
        remedy_map = (
            (
                "shaklas_appeal_method:append",
                "appeal_method_annotated",
                "Dated correction",
                "Preserve the original line and attach a dated, source-backed correction.",
                "The original remains auditable; the dated correction governs future reading.",
            ),
            (
                "shaklas_appeal_method:supersede",
                "appeal_method_superseded",
                "Linked corrected edition",
                "Make a corrected edition operative while retaining a stable link to the superseded wording.",
                "The corrected edition is operative; the old line remains visible as superseded history.",
            ),
            (
                "shaklas_appeal_method:hold",
                "appeal_method_held",
                "Temporary reliance hold",
                "Keep the original visible but non-operative while the bounded source ledger governs decisions.",
                "The old line remains auditable but cannot guide decisions during the bounded review window.",
            ),
        )
        for flag, record_id, label, summary, operative in remedy_map:
            if flag in state.flags or record_id in state.story.records:
                remedy = label
                remedy_summary = summary
                operative_status = operative
                break

        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        class_name = class_definition.name if class_definition is not None else "Unselected"
        class_caution = "Keep proven source facts, unsupported inference, and unresolved authorship or consequence in separate fields."
        quest = self.catalog.story.quests.get("the_appeal_is_not_a_verdict")
        if quest is not None:
            verify_stage = next((stage for stage in quest.stages if stage.id == "verify_source"), None)
            if verify_stage is not None:
                verify_action = next((action for action in verify_stage.actions if action.id == "verify_appeal_source"), None)
                if verify_action is not None:
                    _label, class_caution, _result = self._story_action_label(state, verify_action)

        available_actions: list[dict[str, object]] = []
        if context is not None:
            _, stage = context
            for story_action in stage.actions:
                if story_action.verb != "appeal":
                    continue
                available, reason = self._story_action_availability(state, story_action)
                label, summary, _ = self._story_action_label(state, story_action)
                available_actions.append(
                    {
                        "id": story_action.id,
                        "label": label,
                        "summary": summary,
                        "command": self._story_action_command(story_action),
                        "available": available,
                        "unavailable_reason": reason,
                    }
                )
        primary_command = next(
            (
                str(story_action["command"])
                for story_action in available_actions
                if story_action["available"]
            ),
            "appeal status",
        )

        if complete:
            status = "Closed — temporary appeal and correction authority expired"
        elif reliance_tested:
            status = "Operative behavior proven — public-queue closure required"
        elif published:
            status = "Source-backed correction published — reliance test required"
        elif applied:
            status = "Selected remedy applied — bounded publication required"
        elif source_verified:
            status = "Source boundary verified — selected remedy may be applied"
        elif remedy != "Not selected":
            status = "Remedy selected — fact, inference, and unknown must be verified"
        elif compared:
            status = "Remedies compared — select annotation, supersession, or hold"
        elif inspected:
            status = "Index and source separated — compare correction remedies"
        elif accepted:
            status = "Bounded public-index appeal accepted"
        else:
            status = "No public-index appeal active"

        companion, _progress = self._active_companion_context(state, self.clock.now())
        return {
            "active": active,
            "formed": formed,
            "complete": complete,
            "status": status,
            "location": "Shaklas public index, correction table, and reliance gate",
            "unsupported_label": "sponsored restoration",
            "fact": "The derived public index contains the phrase ‘sponsored restoration,’ but the bounded source ledger records no sponsor, title, permanent access, or continuing authority.",
            "supplier_method": supplier_method,
            "source_line": source_line,
            "class_name": class_name,
            "class_caution": class_caution,
            "remedy": remedy,
            "remedy_summary": remedy_summary,
            "operative_status": operative_status,
            "unknown": "Who wrote or cropped the phrase, why it was written, whether Talin requested it, who relied on it, whether harm occurred, and whether any faction was involved remain unknown.",
            "expiration": (
                "Expired at the Shaklas public queue; the remedy remains public without a continuing reviewer office"
                if complete
                else "Ends at public-queue closure and grants no permanent correction, investigation, censorship, access, or ownership authority"
                if formed
                else "Not active"
            ),
            "boundary": "This appeal corrects one derived public-index label. It does not rewrite the source transaction, erase the original record, identify a culprit, decide guilt or harm, create sponsorship or title, expose identity or clinical data, control movement or access, grant faction or guild status, create Commander authority, establish policing or censorship power, or reveal deeper Shaklas conditions.",
            "actions": available_actions,
            "primary_command": primary_command,
            "active_companion": (
                {
                    "id": companion.id,
                    "name": companion.name,
                    "separate_from_appeal": True,
                }
                if companion is not None
                else None
            ),
        }

    def _appeal(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        query = self._query(command.args)
        if query.casefold() in {"", "status", "list", "summary"}:
            projection = self._appeal_projection(state)
            lines = [
                f"Public-index appeal: {projection['status']}",
                f"Location: {projection['location']}",
                f"Unsupported label: {projection['unsupported_label']}",
                f"Fact: {projection['fact']}",
                f"Governing supplier record: {projection['supplier_method']} — {projection['source_line']}",
                f"Class caution: {projection['class_name']} — {projection['class_caution']}",
                f"Selected remedy: {projection['remedy']} — {projection['remedy_summary']}",
                f"Operative status: {projection['operative_status']}",
                f"Unknown: {projection['unknown']}",
                f"Expiration: {projection['expiration']}",
                str(projection["boundary"]),
            ]
            companion_projection = projection["active_companion"]
            if companion_projection is not None:
                lines.append(
                    f"Active field partner: {companion_projection['name']} remains a separate companion slot and receives no correction, investigation, censorship, ownership, faction, access, clinical, or command authority."
                )
            available = [action for action in projection["actions"] if action["available"]]
            if available:
                lines.append(
                    "Available: "
                    + _natural_list(
                        [str(action["command"]).upper() for action in available]
                    )
                    + "."
                )
            return _HandlerResult(("\n".join(lines),))
        return self._execute_story_verb(state, command, now, verb="appeal")

    def _wayfinding_projection(self, state: GameState) -> dict[str, object]:
        """Project the bounded live-route verification lesson."""

        context = self._active_story_context(state)
        active_quest_id = context[0].id if context is not None else None
        wayfinding_records = {
            "wayfinding_notice_received",
            "wayfinding_waymark_inspected",
            "wayfinding_methods_compared",
            "wayfinding_method_timeboxed",
            "wayfinding_method_escorted",
            "wayfinding_method_held",
            "wayfinding_live_conditions_verified",
            "wayfinding_method_applied",
            "wayfinding_boundary_tested",
            "wayfinding_authority_expired",
        }
        formed = (
            active_quest_id == "the_map_is_not_the_road"
            or "shaklas_wayfinding_active" in state.flags
            or "shaklas_wayfinding_complete" in state.flags
            or bool(wayfinding_records.intersection(state.story.records))
        )
        complete = "shaklas_wayfinding_complete" in state.flags
        active = formed and not complete and (
            active_quest_id == "the_map_is_not_the_road"
            or "shaklas_wayfinding_active" in state.flags
        )
        accepted = (
            "shaklas_wayfinding_accepted" in state.flags
            or "wayfinding_notice_received" in state.story.records
        )
        inspected = (
            "shaklas_wayfinding_waymark_inspected" in state.flags
            or "wayfinding_waymark_inspected" in state.story.records
        )
        compared = (
            "shaklas_wayfinding_methods_compared" in state.flags
            or "wayfinding_methods_compared" in state.story.records
        )
        conditions_verified = (
            "shaklas_wayfinding_conditions_verified" in state.flags
            or "wayfinding_live_conditions_verified" in state.story.records
        )
        applied = (
            "shaklas_wayfinding_method_applied" in state.flags
            or "wayfinding_method_applied" in state.story.records
        )
        boundary_tested = (
            "shaklas_wayfinding_boundary_tested" in state.flags
            or "wayfinding_boundary_tested" in state.story.records
        )

        method = "Not selected"
        method_summary = "No live-route method has been selected."
        operative_status = "The copied waymark remains visible but cannot safely guide movement."
        method_map = (
            (
                "shaklas_wayfinding_method:timebox",
                "wayfinding_method_timeboxed",
                "Timeboxed waymark",
                "Publish one inspected segment, observation time, return option, and mandatory expiration.",
                "The waymark is operative only for the declared inspection window and expires when that window closes.",
            ),
            (
                "shaklas_wayfinding_method:escort",
                "wayfinding_method_escorted",
                "Single-cohort escort",
                "Guide one voluntary cohort over the inspected segment without publishing a reusable traveler schedule.",
                "One willing cohort may cross with a return option; the escort creates no repeat route, roster, or future claim.",
            ),
            (
                "shaklas_wayfinding_method:hold",
                "wayfinding_method_held",
                "Bounded reliance hold",
                "Keep the sign visible but non-operative until two local checks agree.",
                "The sign remains auditable but cannot direct movement until fresh local checks agree.",
            ),
        )
        for flag, record_id, label, summary, operative in method_map:
            if flag in state.flags or record_id in state.story.records:
                method = label
                method_summary = summary
                operative_status = operative
                break

        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        class_name = class_definition.name if class_definition is not None else "Unselected"
        class_caution = "Keep a stale map, a live observation, an unresolved cause, and the right to refuse or return in separate fields."
        quest = self.catalog.story.quests.get("the_map_is_not_the_road")
        if quest is not None:
            verify_stage = next(
                (stage for stage in quest.stages if stage.id == "verify_live_conditions"),
                None,
            )
            if verify_stage is not None:
                verify_action = next(
                    (
                        action
                        for action in verify_stage.actions
                        if action.id == "verify_wayfinding_conditions"
                    ),
                    None,
                )
                if verify_action is not None:
                    _label, class_caution, _result = self._story_action_label(
                        state, verify_action
                    )

        available_actions: list[dict[str, object]] = []
        if context is not None:
            _, stage = context
            for story_action in stage.actions:
                if story_action.verb != "wayfinding":
                    continue
                available, reason = self._story_action_availability(
                    state, story_action
                )
                label, summary, _ = self._story_action_label(state, story_action)
                available_actions.append(
                    {
                        "id": story_action.id,
                        "label": label,
                        "summary": summary,
                        "command": self._story_action_command(story_action),
                        "available": available,
                        "unavailable_reason": reason,
                    }
                )
        primary_command = next(
            (
                str(story_action["command"])
                for story_action in available_actions
                if story_action["available"]
            ),
            "wayfinding status",
        )

        if complete:
            status = "Closed — temporary route-review authority expired"
        elif boundary_tested:
            status = "Route boundary proven — return to the public queue for closure"
        elif applied:
            status = "Bounded method applied — expiration and return must be tested"
        elif conditions_verified:
            status = "Live condition verified — selected method may be applied"
        elif method != "Not selected":
            status = "Method selected — inspect the physical route before use"
        elif compared:
            status = "Methods compared — select timebox, escort, or hold"
        elif inspected:
            status = "Stale waymark separated from live conditions — compare methods"
        elif accepted:
            status = "Bounded live-route review accepted"
        else:
            status = "No live-route review active"

        companion, _progress = self._active_companion_context(
            state, self.clock.now()
        )
        return {
            "active": active,
            "formed": formed,
            "complete": complete,
            "status": status,
            "location": "Shaklas live waymark, public route survey span, and route fork",
            "stale_claim": "OPEN — PUBLIC HOSPICE RETURN",
            "fact": "The copied waymark points to a real public return route, but its observation time and expiration were removed; the physical route must be checked again before it guides movement.",
            "condition": "During the bounded inspection, the main lane is passable in single file, the side stair is closed, and the east return gate is open.",
            "class_name": class_name,
            "class_caution": class_caution,
            "method": method,
            "method_summary": method_summary,
            "operative_status": operative_status,
            "unknown": "Who copied or moved the sign, why it changed, whether a faction was involved, who may have relied on it, future route conditions, traveler identities, and deeper Shaklas conditions remain unknown.",
            "expiration": (
                "Expired at the Shaklas public queue; the public result remains without a continuing route office"
                if complete
                else "Ends at public-queue closure and grants no reusable traveler list, surveillance, route ownership, patrol, faction, access, or command authority"
                if formed
                else "Not active"
            ),
            "boundary": "This lesson verifies one public route segment for one bounded window. It does not map private or deeper streets, identify a culprit, track people, publish schedules, create a traveler roster, grant route ownership, toll rights, patrol power, faction or guild status, Commander authority, permanent access, clinical priority, or future safety.",
            "actions": available_actions,
            "primary_command": primary_command,
            "active_companion": (
                {
                    "id": companion.id,
                    "name": companion.name,
                    "separate_from_wayfinding": True,
                }
                if companion is not None
                else None
            ),
        }

    def _wayfinding(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        query = self._query(command.args)
        if query.casefold() in {"", "status", "list", "summary"}:
            projection = self._wayfinding_projection(state)
            lines = [
                f"Live-route review: {projection['status']}",
                f"Location: {projection['location']}",
                f"Copied waymark: {projection['stale_claim']}",
                f"Fact: {projection['fact']}",
                f"Live condition: {projection['condition']}",
                f"Class caution: {projection['class_name']} — {projection['class_caution']}",
                f"Selected method: {projection['method']} — {projection['method_summary']}",
                f"Operative status: {projection['operative_status']}",
                f"Unknown: {projection['unknown']}",
                f"Expiration: {projection['expiration']}",
                str(projection["boundary"]),
            ]
            companion_projection = projection["active_companion"]
            if companion_projection is not None:
                lines.append(
                    f"Active field partner: {companion_projection['name']} remains a separate companion slot and receives no route ownership, traveler-tracking, patrol, faction, access, clinical, or command authority."
                )
            available = [
                action for action in projection["actions"] if action["available"]
            ]
            if available:
                lines.append(
                    "Available: "
                    + _natural_list(
                        [str(action["command"]).upper() for action in available]
                    )
                    + "."
                )
            return _HandlerResult(("\n".join(lines),))
        return self._execute_story_verb(
            state, command, now, verb="wayfinding"
        )

    def _stance(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        if not command.args:
            return _HandlerResult(
                (
                    f"Your stance is {state.character.stance.value}. "
                    "Choices: defensive, guarded, neutral, forward, advanced, offensive.",
                )
            )
        query = self._query(command.args)
        matches = [stance for stance in Stance if stance.value.startswith(query)]
        if len(matches) != 1:
            return _HandlerResult(
                (
                    "Choose: defensive, guarded, neutral, forward, "
                    "advanced, or offensive.",
                )
            )
        chosen = matches[0]
        if chosen == state.character.stance:
            return _HandlerResult((f"You are already in a {chosen.value} stance.",))
        previous = state.character.stance
        state.character.stance = chosen
        self._set_roundtime(state, now, 1)
        return _HandlerResult(
            (f"You shift from {previous.value} to {chosen.value} stance.", "Roundtime: 1 sec."),
            (DomainEvent("combat.stance_changed", {"from": previous.value, "to": chosen.value}),),
            True,
        )

    def _defense(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if not command.args:
            return _HandlerResult(
                (
                    f"Your defensive reaction is {state.character.defense_mode.value}. "
                    "Choices: balanced, evade, block, parry.",
                )
            )
        query = self._query(command.args)
        matches = [
            mode for mode in DefenseMode if mode.value.startswith(query)
        ]
        if len(matches) != 1:
            return _HandlerResult(
                ("Choose: balanced, evade, block, or parry.",)
            )
        chosen = matches[0]
        weapon = equipped_item(
            state.character,
            self.catalog.items,
            "main_hand",
        )
        armor = equipped_item(
            state.character,
            self.catalog.items,
            "body",
        )
        disabled = disabled_limbs(state.character)
        if chosen is DefenseMode.EVADE and any(
            "leg" in location for location in disabled
        ):
            return _HandlerResult(
                ("EVADE is unavailable while a leg is disabled.",)
            )
        if chosen is DefenseMode.BLOCK and (
            armor is None or armor.armor_profile == "none"
        ):
            return _HandlerResult(
                ("BLOCK requires equipped protective armor.",)
            )
        if chosen is DefenseMode.PARRY and (
            weapon is None or weapon.weapon_profile == "unarmed"
        ):
            return _HandlerResult(
                ("PARRY requires an equipped weapon.",)
            )
        if chosen is DefenseMode.PARRY and any(
            "arm" in location for location in disabled
        ):
            return _HandlerResult(
                ("PARRY is unavailable while an arm is disabled.",)
            )
        if chosen is state.character.defense_mode:
            return _HandlerResult(
                (f"Your defensive reaction is already {chosen.value}.",)
            )
        previous = state.character.defense_mode
        state.character.defense_mode = chosen
        self._set_roundtime(state, now, 1)
        return _HandlerResult(
            (
                f"You prepare to {chosen.value} instead of {previous.value}.",
                "Roundtime: 1 sec.",
            ),
            (
                DomainEvent(
                    "combat.defense_mode_changed",
                    {"from": previous.value, "to": chosen.value},
                ),
            ),
            True,
        )

    def _stand(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if command.args:
            return _HandlerResult(("Use STAND without a target.",))
        if not state.character.prone and not state.character.resting:
            return _HandlerResult(("You are already standing.",))
        was_prone = state.character.prone
        was_resting = state.character.resting
        state.character.prone = False
        state.character.resting = False
        duration = 3 if was_prone else 1
        self._set_roundtime(state, now, duration)
        message = (
            "You plant your feet, regain your balance, and stand."
            if was_prone
            else "You rise from your resting posture."
        )
        return _HandlerResult(
            (
                message,
                f"Roundtime: {duration} sec.",
            ),
            (
                DomainEvent(
                    "condition.stood",
                    {"from_prone": was_prone, "from_rest": was_resting},
                ),
            ),
            True,
        )

    def _defeat_creature_from_battlefield(
        self,
        state: GameState,
        target: CreatureState,
        target_definition: CreatureDefinition,
        now: float,
        lines: list[str],
        events: list[DomainEvent],
        *,
        finisher: str,
        player_used_companion_finish_window: bool = False,
    ) -> None:
        """Resolve one target defeat through the canonical reward/reset path."""

        target_actor_id = creature_actor_id(target.instance_id)
        for flag in tuple(state.flags):
            if (
                flag.endswith(f":{target.instance_id}")
                and (
                    flag.startswith("companion_opening:")
                    or flag.startswith("companion_finish_window:")
                    or flag.startswith("companion_sync_used:")
                    or flag.startswith("companion_sync_suppress_follow:")
                )
            ):
                state.flags.discard(flag)

        room_id = state.character.room_id
        companion, companion_progress = self._active_companion_context(
            state, now
        )
        if target_definition.nonlethal:
            completed_phase = target.phase
            completed_exchanges = target.exchange_count
            target.health = target_definition.max_health
            target.phase = 1
            target.exchange_count = 0
            actor = state.battle.actors.get(target_actor_id)
            if actor is not None:
                actor.next_action_at = (
                    state.battle.time + target_definition.action_interval
                )
                actor.recovery_duration = float(
                    target_definition.action_interval
                )
                actor.current_intent = None
                actor.target_id = None
                actor.interrupted_until = state.battle.time
                actor.telegraph_shown = False
                actor.actions_taken = 0
            state.battle.effects.pop(target_actor_id, None)
            if target_definition.id == "sol_confrontation":
                bleeding_before = active_bleeding(state.character)
                health_before = state.character.health
                prone_before = state.character.prone
                stunned_before = max(
                    0, math.ceil(state.character.stunned_until - now)
                )
                for wound in state.character.wounds:
                    wound.bleeding = 0
                state.character.health = max(
                    state.character.health,
                    max(1, state.character.max_health // 3),
                )
                state.character.stunned_until = now
                state.character.prone = False
                state.character.condition_pulse_at = now
                lines.append(
                    "Sol catches you before the control lattice can turn the last "
                    "exchange into continuing harm. Bleeding stops, the stun clears, "
                    "and the emergency rail restores enough integrity for the capstone "
                    "dialogue; wound severity remains recorded."
                )
                events.append(
                    DomainEvent(
                        "condition.capstone_stabilized",
                        {
                            "target": target.instance_id,
                            "bleeding_before": bleeding_before,
                            "bleeding_after": 0,
                            "health_before": health_before,
                            "health_after": state.character.health,
                            "prone_before": prone_before,
                            "stunned_seconds_before": stunned_before,
                            "phase_completed": completed_phase,
                            "exchanges": completed_exchanges,
                        },
                    )
                )
                for flag in tuple(state.flags):
                    if (
                        flag.startswith("sol_capstone_phase:")
                        or flag.startswith("sol_capstone_opening:")
                        or flag.startswith("companion_opening:")
                        or flag.startswith("companion_finish_window:")
                        or flag.startswith("companion_sync_used:")
                        or flag.startswith("companion_sync_suppress_follow:")
                    ):
                        state.flags.discard(flag)
            lines.append(
                f"{target_definition.name.capitalize()} locks, records the result, "
                "and resets to full calibration."
            )
            events.append(
                DomainEvent(
                    "combat.diagnostic_target_reset",
                    {
                        "target": target.instance_id,
                        "phase_completed": completed_phase,
                        "exchanges": completed_exchanges,
                        "independent_clock": True,
                    },
                )
            )
            return

        room_creatures = state.creatures.setdefault(room_id, [])
        if target in room_creatures:
            room_creatures.remove(target)
        state.battle.actors.pop(target_actor_id, None)
        state.battle.effects.pop(target_actor_id, None)
        state.defeated_creatures.add(target.instance_id)
        if state.target_id == target.instance_id:
            state.target_id = None
        if (
            state.last_reference_kind == "creature"
            and state.last_reference_id == target.instance_id
        ):
            self._set_reference(state, None, None)

        award_field_insight(
            state.character.experience,
            target_definition.xp_reward,
            now,
        )
        loot_states = [
            self._spawn_item(state, definition_id)
            for definition_id in target_definition.loot
        ]
        state.room_items.setdefault(room_id, []).extend(loot_states)
        credit_reward = target_definition.credit_reward
        if credit_reward:
            state.character.credits = min(
                100_000_000, state.character.credits + credit_reward
            )
        lines.append(
            f"{target_definition.name.capitalize()} collapses. "
            f"You gain {target_definition.xp_reward} field insight"
            + (f" and {credit_reward} credits." if credit_reward else ".")
        )
        companion_lines, companion_events = self._award_companion_experience(
            state,
            target_definition.xp_reward,
            now,
            reason="shared combat",
        )
        lines.extend(companion_lines)
        events.extend(companion_events)
        if companion_progress is not None and companion is not None:
            companion_progress.defeated_targets = min(
                100_000_000, companion_progress.defeated_targets + 1
            )
        if target_definition.loot:
            loot_names = [
                self.catalog.items[item_id].name
                for item_id in target_definition.loot
            ]
            lines.append(f"It leaves behind {_natural_list(loot_names)}.")
        events.append(
            DomainEvent(
                "combat.target_defeated",
                {
                    "target": target.instance_id,
                    "finisher": finisher,
                    "player_used_companion_finish_window": (
                        player_used_companion_finish_window
                    ),
                    "xp": target_definition.xp_reward,
                    "credits": target_definition.credit_reward,
                    "loot": [
                        {
                            "instance_id": item.instance_id,
                            "item_id": item.definition_id,
                        }
                        for item in loot_states
                    ],
                    "independent_clock": finisher != state.character.key,
                },
            )
        )
        if not self._live_creatures(state):
            events.append(
                DomainEvent(
                    "combat.room_cleared",
                    {"room_id": room_id},
                )
            )
            lines.append(
                "[Area secured] No active hostile remains in this room."
            )

    @staticmethod
    def _attack_request(
        args: tuple[str, ...],
    ) -> tuple[str, str | None, str | None]:
        words = [word.casefold() for word in args]
        if "at" not in words:
            return " ".join(words), None, None
        marker = words.index("at")
        target_query = " ".join(words[:marker]).strip()
        aimed_location = " ".join(words[marker + 1 :]).strip()
        if not aimed_location:
            return target_query, None, "Name a body location after AT."
        valid = set(HIT_LOCATIONS)
        if aimed_location not in valid:
            return (
                target_query,
                None,
                "Aim at chest, abdomen, head, left arm, right arm, left leg, or right leg.",
            )
        return target_query, aimed_location, None

    def _attack(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        query, aimed_location, aim_error = self._attack_request(command.args)
        if aim_error:
            return _HandlerResult((aim_error,))
        target, error = self._resolve_creature(state, query)
        if not target:
            return _HandlerResult((error or "Attack what?",))
        attack_sync = self.combat_scheduler.synchronize(state, now)
        target_definition = self.catalog.creatures[target.definition_id]
        combat_target_definition = self._effective_beginner_creature_definition(
            state, target_definition
        )
        capstone_offense_bonus = 0
        capstone_damage_bonus = 0
        capstone_aim_reduction = 0
        capstone_phase_intro: str | None = None
        forced_capstone_phases: list[int] = []
        if target_definition.id == "sol_confrontation":
            target.exchange_count += 1
            if target.exchange_count >= 18 and target.phase < 3:
                # The final bounded exchange must still expose every authored
                # phase. Low-roll support classes are allowed to reach the soft
                # maximum, but the capstone never skips the meaning of the
                # close-pressure and final-control beats.
                forced_capstone_phases = list(range(target.phase + 1, 4))
                target.phase = 3
                state.flags.add("sol_capstone_opening:three")
            phase_defense_penalty, phase_offense_penalty, phase_armor_penalty = {
                1: (0, 0, 0),
                2: (8, 6, 1),
                3: (16, 12, 2),
            }[target.phase]
            combat_target_definition = replace(
                target_definition,
                defense=max(1, target_definition.defense - phase_defense_penalty),
                offense=max(1, target_definition.offense - phase_offense_penalty),
                armor=max(0, target_definition.armor - phase_armor_penalty),
            )
            capstone_offense_bonus = 6 + min(18, max(0, target.exchange_count - 1) * 2)
            capstone_damage_bonus = 2 + min(7, max(0, target.exchange_count - 1) // 3)
            capstone_aim_reduction = min(12, target.exchange_count)
            if target.exchange_count >= 18:
                # The capstone tests mastery; it must not become a low-impact
                # loop. Repetition exposes the control lattice and creates a
                # decisive player-owned read instead of silently lowering Sol.
                combat_target_definition = replace(
                    combat_target_definition,
                    defense=max(1, combat_target_definition.defense - 14),
                    offense=max(1, combat_target_definition.offense - 10),
                    armor=0,
                )
                capstone_offense_bonus += 16
                capstone_damage_bonus += 8
                capstone_aim_reduction += 10
            if target.exchange_count == 1:
                capstone_phase_intro = (
                    "[Capstone phase one] Sol tests your foundation through Akari-line "
                    "timing. Your class rehearsal grants an immediate player-owned read."
                )
        baseline_health = state.character.health
        baseline_condition_pulse = state.character.condition_pulse_at
        baseline_stunned_until = state.character.stunned_until
        baseline_prone = state.character.prone
        baseline_wounds = [
            Wound(wound.location, wound.severity, wound.bleeding)
            for wound in state.character.wounds
        ]
        state.target_id = target.instance_id
        self._set_reference(state, "creature", target.instance_id)
        weapon = equipped_item(state.character, self.catalog.items, "main_hand")
        armor = equipped_item(state.character, self.catalog.items, "body")
        armor_state = self._equipped_item_state(state, "body")
        # Every hostile now receives an independent scheduled opportunity.
        # The attack resolver therefore handles this one target without also
        # charging an abstract crowd-pressure penalty.
        opponent_count = 1
        technique_flag = next(
            (flag for flag in state.flags if flag.startswith("technique:attack:")),
            None,
        )
        technique_class = technique_flag.rsplit(":", 1)[-1] if technique_flag else ""
        offense_bonus, damage_bonus, aim_reduction = {
            "boss": (12, 3, 0),
            "zealot": (10, 5, 0),
            "born_assassin": (16, 4, 0),
            "sniper": (18, 2, 12),
            "system": (14, 2, 2),
            "guide": (8, 2, 0),
        }.get(technique_class, (0, 0, 0))
        offense_bonus += capstone_offense_bonus
        damage_bonus += capstone_damage_bonus
        aim_reduction += capstone_aim_reduction
        specialization = self._selected_specialization(state)
        specialization_flag = (
            specialization is not None
            and f"specialization_attack:{specialization.id}" in state.flags
        )
        follow_up_flag = (
            specialization is not None
            and f"specialization_followup_attack:{specialization.id}" in state.flags
        )
        if specialization_flag or follow_up_flag:
            specialization_values = self._specialization_values(
                state, specialization
            )
            specialization_power = (
                specialization_values["follow_up_power"]
                if follow_up_flag
                else specialization_values["power"]
            )
            precision_action = (
                specialization.kind == "precision"
                if specialization_flag
                else specialization.follow_up.kind == "precision"
            )
            if precision_action:
                offense_bonus += specialization_power
                aim_reduction += specialization_power
                if aimed_location is None:
                    aimed_location = "head"
            else:
                offense_bonus += specialization_power
                damage_bonus += max(1, specialization_power // 3)
        report_flag = f"reported_target:{target.instance_id}"
        if report_flag in state.flags:
            report_power = 0
            report_prefix = (
                f"specialization_report_power:{target.instance_id}:"
            )
            report_power_flag = next(
                (
                    flag
                    for flag in state.flags
                    if flag.startswith(report_prefix)
                ),
                None,
            )
            if report_power_flag is not None:
                try:
                    report_power = max(
                        0, int(report_power_flag.rsplit(":", 1)[1])
                    )
                except ValueError:
                    report_power = 0
                state.flags.discard(report_power_flag)
            offense_bonus += 12 + report_power
            aim_reduction += 8 + max(0, report_power // 2)
            state.flags.discard(report_flag)
        field_formation_text: str | None = None
        if target_definition.id == "echo_route_pursuit_frame":
            if "field_cohort_formation_offensive" in state.flags:
                offense_bonus += 10
                damage_bonus += 2
                field_formation_text = (
                    "[Offensive detail] The pressure lane grants +10 offense and +2 damage, "
                    "with less guard prepared at release."
                )
            elif "field_cohort_formation_defensive" in state.flags:
                offense_bonus += 2
                aim_reduction += 4
                field_formation_text = (
                    "[Defensive detail] The shelter lane grants +2 offense and steadier aim; "
                    "its main benefit is the larger guard reserve prepared at release."
                )
            elif "field_cohort_formation_balanced" in state.flags:
                offense_bonus += 5
                damage_bonus += 1
                aim_reduction += 2
                field_formation_text = (
                    "[Balanced detail] Mixed coverage grants +5 offense, +1 damage, and steadier aim."
                )

        companion_opening_text: str | None = None
        player_used_companion_finish_window = False
        player_used_companion_opening = False
        companion_opening_flag = f"companion_opening:{target.instance_id}"
        companion_finish_flag = f"companion_finish_window:{target.instance_id}"
        if companion_finish_flag in state.flags:
            state.flags.discard(companion_finish_flag)
            state.flags.discard(companion_opening_flag)
            offense_bonus += 18
            damage_bonus += 4
            aim_reduction += 8
            player_used_companion_finish_window = True
            companion_opening_text = (
                "Sol has stopped short of the finishing strike. His reserved opening "
                "grants +18 offense and +4 damage; the result now belongs to you."
            )
        elif companion_opening_flag in state.flags:
            state.flags.discard(companion_opening_flag)
            offense_bonus += 16
            damage_bonus += 3
            aim_reduction += 8
            player_used_companion_opening = True
            companion_opening_text = (
                "Sol's preceding pressure exposes the next accountable opening: "
                "+16 offense and +3 damage, with a reliable contact floor for your strike."
            )
        capstone_opening = next(
            (
                flag
                for flag in state.flags
                if flag.startswith("sol_capstone_opening:")
            ),
            None,
        )
        if target_definition.id == "sol_confrontation" and capstone_opening is not None:
            state.flags.discard(capstone_opening)
            phase = capstone_opening.rsplit(":", 1)[-1]
            phase_offense, phase_damage = {
                "two": (18, 4),
                "three": (24, 6),
            }.get(phase, (12, 3))
            offense_bonus += phase_offense
            damage_bonus += phase_damage
            aim_reduction += phase_offense // 2
            class_definition = self.catalog.creation.classes.get(
                state.character.build.class_id or ""
            )
            technique_name = (
                class_definition.technique_name
                if class_definition is not None
                else "rehearsed instinct"
            )
            companion_opening_text = (
                f"Your {technique_name} rehearsal reads Sol's changed rhythm: "
                f"+{phase_offense} offense and +{phase_damage} damage."
            )
        companion, companion_progress = self._active_companion_context(
            state, now
        )
        if companion is not None and companion.assist_kind == "scout":
            offense_bonus += companion.power
        if companion is not None and companion.assist_kind == "guard":
            state.character.guard_points = min(
                1000, state.character.guard_points + companion.power
            )
        if companion is not None and companion.assist_kind == "medic" and state.character.health < state.character.max_health:
            before_companion_heal = state.character.health
            state.character.health = min(
                state.character.max_health, state.character.health + companion.power
            )
        else:
            before_companion_heal = state.character.health
        if technique_class == "sniper" and aimed_location is None:
            aimed_location = "head"

        battle_modifiers = self.combat_scheduler.player_attack_modifiers(
            state,
            target.instance_id,
            ignore_player_opening=(
                player_used_companion_finish_window
                or player_used_companion_opening
            ),
        )
        offense_bonus += battle_modifiers.offense
        damage_bonus += battle_modifiers.damage
        combat_target_definition = replace(
            combat_target_definition,
            defense=max(0, combat_target_definition.defense + battle_modifiers.defense_delta),
            armor=max(0, combat_target_definition.armor + battle_modifiers.armor_delta),
        )
        outcome = resolve_player_attack(
            state.character,
            weapon,
            combat_target_definition,
            self.rng,
            aimed_location=aimed_location,
            opponent_count=opponent_count,
            offense_bonus=offense_bonus,
            damage_bonus=damage_bonus,
            aim_penalty_reduction=aim_reduction,
        )
        if player_used_companion_finish_window and target.health > 0:
            # Sol already did the dangerous setup and deliberately yielded the
            # final line. Requiring another lucky roll would turn a visible
            # player-owned finish into an arbitrary repetition loop. The player
            # must still choose ATTACK, but that action now converts the opening
            # reliably and records the player as finisher.
            outcome = replace(
                outcome,
                hit=True,
                damage=max(target.health, outcome.damage),
                location=outcome.location or aimed_location or "chest",
                severity=max(2, outcome.severity),
                critical=(
                    "Sol's reserved line is exact; your chosen strike closes the exchange."
                ),
            )
        elif player_used_companion_opening and target.health > 0:
            # A setup opening must be useful even when the underlying attack roll
            # is poor. Sol is contributing positioning and timing rather than
            # replacing the player; the next chosen strike receives a bounded
            # damage floor while the ordinary action and roundtime still apply.
            opening_floor = min(
                target.health,
                max(3, 2 + state.character.level // 2),
            )
            outcome = replace(
                outcome,
                hit=True,
                damage=max(opening_floor, outcome.damage),
                location=outcome.location or aimed_location or "chest",
                severity=max(1, outcome.severity),
                critical=(
                    "Sol's setup makes contact reliable; your strike carries the opening forward."
                ),
            )
        if target_definition.id == "sol_confrontation":
            # Preserve three readable beats. A lucky critical cannot skip the
            # entire capstone, while the eighteenth exchange becomes a decisive
            # player-owned soft stop instead of an exhausting low-impact loop.
            phase_damage_cap = {1: 7, 2: 8, 3: 9}[target.phase]
            if target.exchange_count >= 18 and target.health > 0:
                outcome = replace(
                    outcome,
                    hit=True,
                    damage=target.health,
                    location=outcome.location or aimed_location or "chest",
                    severity=max(3, outcome.severity),
                    critical=(
                        "Your rehearsed sequence resolves every accumulated read; "
                        "the control lattice can no longer hide Sol's final opening."
                    ),
                )
            elif outcome.hit:
                outcome = replace(
                    outcome,
                    damage=min(outcome.damage, phase_damage_cap),
                )
        load = calculate_encumbrance(state.character, self.catalog.items)
        difficulty_band = self._beginner_difficulty_band(state)
        rt = attack_roundtime(
            state.character,
            weapon,
            encumbrance_penalty=(
                load.recovery_penalty
                + (
                    difficulty_band.player_roundtime_modifier
                    if self._difficulty_curve_active_in_room(state)
                    and not target_definition.nonlethal
                    else 0
                )
            ),
        )
        self._set_roundtime(state, now, rt)
        weapon_name = weapon.name if weapon else "bare hands"
        aim_text = f", aiming at the {aimed_location}" if aimed_location else ""
        lines = list(attack_sync.lines) + [
            f"You attack {target_definition.name} with {weapon_name}{aim_text}."
        ]
        difficulty_seen_flag = f"difficulty_band_seen:{difficulty_band.id}"
        if (
            self._difficulty_curve_active_in_room(state)
            and not target_definition.nonlethal
            and difficulty_seen_flag not in state.flags
        ):
            state.flags.add(difficulty_seen_flag)
            lines.append(
                f"[Difficulty: {difficulty_band.label}] {difficulty_band.summary}"
            )
        if capstone_phase_intro:
            lines.append(capstone_phase_intro)
        if (
            target_definition.id == "sol_confrontation"
            and target.exchange_count == 18
        ):
            lines.append(
                "[Capstone pattern broken] The lattice has repeated itself too long. "
                "Your rehearsal turns the exposed loop into a decisive player-owned window."
            )
            if forced_capstone_phases:
                lines.append(
                    "The accumulated read exposes the remaining control beats in order: "
                    + " -> ".join(f"phase {phase}" for phase in forced_capstone_phases)
                    + "."
                )
        if target_definition.id == "sol_confrontation":
            lines.append(
                f"Capstone read: phase {target.phase}/3 · exchange {target.exchange_count} · "
                f"effective defense {combat_target_definition.defense}."
            )
        if companion is not None and companion.assist_kind == "guard":
            lines.append(f"{companion.name} braces the formation for +{companion.power} guard.")
        if companion is not None and companion.assist_kind == "scout":
            lines.append(f"{companion.name} reports movement for +{companion.power} offense.")
        if companion is not None and companion.assist_kind == "medic" and state.character.health > before_companion_heal:
            lines.append(f"{companion.name} restores {state.character.health - before_companion_heal} health before the exchange.")
        if field_formation_text:
            lines.append(field_formation_text)
        if companion_opening_text:
            lines.append(companion_opening_text)
        lines.extend(battle_modifiers.lines)
        lines.append(
            f"[Roll {outcome.roll:+d} + Offense {outcome.offense} - "
            f"Defense {outcome.defense} = {outcome.endroll}]"
        )
        events: list[DomainEvent] = (
            list(attack_sync.events) + list(battle_modifiers.events) + [
            DomainEvent(
                "combat.attack_resolved",
                {
                    "attacker": state.character.key,
                    "target": target.instance_id,
                    "endroll": outcome.endroll,
                    "hit": outcome.hit,
                    "damage": outcome.damage if outcome.hit else 0,
                    "critical_severity": outcome.severity if outcome.hit else 0,
                    "aimed_location": aimed_location,
                    "hit_location": outcome.location,
                    "weapon_profile": (
                        weapon.weapon_profile if weapon else "unarmed"
                    ),
                    "target_armor_profile": combat_target_definition.armor_profile,
                    "opponent_count": opponent_count,
                    "pressure_penalty": outcome.pressure_penalty,
                },
            )
            ]
        )
        if outcome.hit:
            target.health -= outcome.damage
            lines.append(
                f"{outcome.critical} ({outcome.damage} damage; critical {outcome.severity})"
            )
            if outcome.armor_interaction:
                lines.append(outcome.armor_interaction)
        else:
            lines.append("The attack fails to break the target's guard.")
        if outcome.pressure_penalty:
            lines.append(
                f"Opponent pressure reduces your offense by "
                f"{outcome.pressure_penalty}."
            )
        player_landed_final = bool(outcome.hit and target.health <= 0)
        companion_landed_final = False
        companion_counter_suppressed = False
        if (
            player_landed_final
            and player_used_companion_finish_window
            and companion is not None
            and companion_progress is not None
            and companion.id == "sol"
        ):
            companion_progress.player_enabled_finishes = min(
                100_000_000, companion_progress.player_enabled_finishes + 1
            )
            lines.append(
                "You convert Sol's reserved opening into the finishing strike. "
                "The victory remains yours."
            )
            events.append(
                DomainEvent(
                    "combat.player_finished_companion_setup",
                    {
                        "companion_id": companion.id,
                        "target": target.instance_id,
                        "finisher": state.character.key,
                    },
                )
            )

        companion_sync_suppress_flag = (
            f"companion_sync_suppress_follow:{target.instance_id}"
        )
        if companion_sync_suppress_flag in state.flags:
            state.flags.discard(companion_sync_suppress_flag)
            sol_actor = state.battle.actors.get(companion_actor_id("sol"))
            if sol_actor is not None:
                pending_player_recovery = max(
                    1, self._hard_recovery_remaining(state, now)
                )
                sol_actor.next_action_at = max(
                    sol_actor.next_action_at,
                    state.battle.time
                    + pending_player_recovery
                    + sol_recovery_seconds(
                        companion_progress.order
                        if companion_progress is not None
                        else "balanced"
                    ),
                )
                sol_actor.telegraph_shown = False
            lines.append(
                "Sol holds the synchronized line on his own recovery clock; "
                "the next player decision remains yours."
            )
            events.append(
                DomainEvent(
                    "combat.companion_follow_suppressed",
                    {
                        "companion_id": companion.id if companion is not None else None,
                        "target": target.instance_id,
                        "source": "player_triggered_synchrony",
                        "independent_clock": True,
                    },
                )
            )

        if target_definition.id == "sol_confrontation" and forced_capstone_phases:
            class_definition = self.catalog.creation.classes.get(
                state.character.build.class_id or ""
            )
            technique_name = (
                class_definition.technique_name
                if class_definition is not None
                else "rehearsed instinct"
            )
            for phase in forced_capstone_phases:
                events.append(
                    DomainEvent(
                        "combat.boss_phase_changed",
                        {
                            "target": target.instance_id,
                            "phase": phase,
                            "health": max(0, target.health),
                            "exchange_count": target.exchange_count,
                            "technique": technique_name,
                            "guard_added": 0,
                            "bounded_pattern_break": True,
                        },
                    )
                )
        if target_definition.id == "sol_confrontation" and target.health > 0:
            class_definition = self.catalog.creation.classes.get(
                state.character.build.class_id or ""
            )
            technique_name = (
                class_definition.technique_name
                if class_definition is not None
                else "rehearsed instinct"
            )
            next_phase = target.phase
            phase_three_health = max(1, target_definition.max_health // 3)
            phase_two_health = max(
                phase_three_health + 1,
                (target_definition.max_health * 2) // 3,
            )
            if target.health <= phase_three_health:
                next_phase = 3
            elif target.health <= phase_two_health:
                next_phase = 2
            if next_phase > target.phase:
                target.phase = next_phase
                opening_name = "three" if next_phase == 3 else "two"
                state.flags.add(f"sol_capstone_opening:{opening_name}")
                guard_added = 8 if next_phase == 3 else 6
                state.character.guard_points = min(
                    1000, state.character.guard_points + guard_added
                )
                if next_phase == 2:
                    lines.extend(
                        (
                            "[Capstone phase two] Sol breaks the first control rhythm and "
                            "shifts into Akari's close-pressure cadence.",
                            f"Your {technique_name} rehearsal identifies the transition. "
                            "His guard is now less complete; the next strike gains a "
                            "class-specific opening, and you gain 6 guard.",
                        )
                    )
                else:
                    lines.extend(
                        (
                            "[Capstone phase three] The control lattice overdrives Sol's "
                            "damaged gauntlet housings, but his stance hesitates before "
                            "the final command.",
                            f"Your {technique_name} rehearsal turns that hesitation into "
                            "the last player-owned opening. His defense and counterpressure "
                            "are now visibly failing; you gain 8 guard.",
                        )
                    )
                events.append(
                    DomainEvent(
                        "combat.boss_phase_changed",
                        {
                            "target": target.instance_id,
                            "phase": target.phase,
                            "health": target.health,
                            "exchange_count": target.exchange_count,
                            "technique": technique_name,
                            "guard_added": guard_added,
                        },
                    )
                )
        if target.health <= 0:
            self._defeat_creature_from_battlefield(
                state,
                target,
                target_definition,
                now,
                lines,
                events,
                finisher=state.character.key,
                player_used_companion_finish_window=(
                    player_used_companion_finish_window
                ),
            )
        else:
            tactical_result = self.combat_scheduler.after_player_attack(
                state,
                target_instance_id=target.instance_id,
                hit=outcome.hit,
                severity=outcome.severity,
                damage=outcome.damage if outcome.hit else 0,
                weapon_profile=(
                    weapon.weapon_profile if weapon is not None else "unarmed"
                ),
            )
            lines.extend(tactical_result.lines)
            events.extend(tactical_result.events)

            if "specialization_control_ready" in state.flags:
                state.flags.discard("specialization_control_ready")
                target_actor = state.battle.actors.get(
                    creature_actor_id(target.instance_id)
                )
                if target_actor is not None:
                    target_actor.current_intent = "recover"
                    target_actor.target_id = None
                    target_actor.interrupted_until = max(
                        target_actor.interrupted_until,
                        state.battle.time + 2,
                    )
                    target_actor.next_action_at = max(
                        target_actor.next_action_at,
                        target_actor.interrupted_until,
                    )
                    target_actor.telegraph_shown = False
                lines.append(
                    "[Control] Your specialization interrupts the hostile intent "
                    "and delays its independent recovery by 2 field seconds."
                )
                events.append(
                    DomainEvent(
                        "combat.intent_interrupted",
                        {
                            "target": target.instance_id,
                            "source": "specialization",
                            "delay": 2,
                            "independent_clock": True,
                        },
                    )
                )

        if state.incapacitation is not None:
            remaining = max(
                0,
                math.ceil(state.incapacitation.recover_at - now),
            )
            lines.append(f"Incapacitated recovery: {remaining} sec.")
        else:
            actual_recovery = self._hard_recovery_remaining(state, now)
            lines.append(f"Hard recovery: {actual_recovery} sec.")
        return _HandlerResult(tuple(lines), tuple(events), True)

    def _incapacitate(
        self,
        state: GameState,
        now: float,
        lines: list[str],
        *,
        cause: str,
    ) -> list[DomainEvent]:
        origin_room_id = state.character.room_id
        state.character.health = 1
        state.character.stunned_until = now
        state.character.roundtime_until = now
        state.character.prone = True
        state.character.resting = False
        state.incapacitation = IncapacitationState(
            origin_room_id=origin_room_id,
            downed_at=now,
            recover_at=now + 10,
            cause=cause,
        )
        state.target_id = None
        state.last_reference_kind = None
        state.last_reference_id = None
        interrupted = state.queued_action
        state.queued_action = None
        lines.extend(
            (
                "Your senses shear away before the final impact.",
                "You are incapacitated but remain present at the scene.",
                "SIGNAL records a help request. RECOVER becomes available in 10 sec.",
            )
        )
        events = [
            DomainEvent(
                "character.incapacitated",
                {
                    "room_id": origin_room_id,
                    "recover_at": now + 10,
                    "cause": cause,
                },
            )
        ]
        if interrupted is not None:
            lines.append("Your pending action is canceled by incapacitation.")
            events.append(
                DomainEvent(
                    "action.queue_interrupted",
                    {
                        "command": interrupted.intent.command,
                        "reason": "character_incapacitation",
                    },
                )
            )
        return events

    def _beginner_recovery_room(self, state: GameState) -> str:
        """Return a safe authored hub without turning defeat into a route reset."""

        quest_id = state.story.active_quest_id or ""
        if quest_id in {"water_is_a_border", "lines_in_the_rain"}:
            return "rain_market"
        if quest_id in {"sprawl_tradecraft", "marked_before_waking"}:
            return "salvage_row"
        if quest_id in {"foundation_trials", "class_field_assignment"}:
            return "relay_overlook"
        if quest_id in {"last_patrol", "price_of_second_life"}:
            return "sprawl_watchpost"
        return self.catalog.start_room

    def _complete_recovery(
        self, state: GameState, now: float, lines: list[str]
    ) -> list[DomainEvent]:
        incapacitation = state.incapacitation
        if incapacitation is None:
            raise ValueError("character is not incapacitated")
        experience = state.character.experience
        beginner_active = not bool(
            self._beginner_experience_projection(state)["complete"]
        )
        lost = min(
            experience.field_pool,
            max(5, experience.field_pool // 10)
            if beginner_active
            else max(10, experience.field_pool // 4),
        )
        experience.field_pool -= lost
        state.character.health = max(1, state.character.max_health // 2)
        recovery_room = (
            self._beginner_recovery_room(state)
            if beginner_active
            else self.catalog.start_room
        )
        state.character.room_id = recovery_room
        state.visited_rooms.add(recovery_room)
        state.character.wounds = [Wound("systemic shock", 1, 0)]
        state.character.condition_pulse_at = now
        state.character.stunned_until = now
        state.character.prone = False
        state.character.resting = False
        state.incapacitation = None
        if beginner_active and state.character.companion_id == "sol":
            progress = state.character.companion_progress.get("sol")
            if progress is not None:
                progress.health = max(1, progress.max_health // 2)
                progress.downed_until = 0.0
        self._set_roundtime(state, now, 5)
        lines.extend(
            (
                (
                    f"Sol and a recovery beacon draw you back to {self.catalog.rooms[recovery_room].title}."
                    if beginner_active and state.character.companion_id == "sol"
                    else f"A recovery beacon draws you back to {self.catalog.rooms[recovery_room].title}."
                ),
                f"Unstable insight lost: {lost}.",
                "Roundtime: 5 sec.",
            )
        )
        return [
            DomainEvent(
                "character.recovered",
                {
                    "room_id": recovery_room,
                    "origin_room_id": incapacitation.origin_room_id,
                    "field_insight_lost": lost,
                    "cause": incapacitation.cause,
                    "help_requested": incapacitation.help_requested,
                    "recovery_profile": "beginner_momentum" if beginner_active else "standard",
                },
            )
        ]

    def _target(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        if query in {"clear", "none"}:
            if not state.target_id:
                return _HandlerResult(("You have no target selected.",))
            state.target_id = None
            return _HandlerResult(
                ("You clear your target.",),
                (DomainEvent("combat.target_cleared"),),
                True,
            )
        if not query and state.target_id:
            creature = next(
                (
                    item
                    for item in self._live_creatures(state)
                    if item.instance_id == state.target_id
                ),
                None,
            )
            if creature:
                return _HandlerResult(
                    (f"Your target is {self.catalog.creatures[creature.definition_id].name}.",)
                )
        target, error = self._resolve_creature(state, query)
        if not target:
            return _HandlerResult((error or "Target what?",))
        state.target_id = target.instance_id
        self._set_reference(state, "creature", target.instance_id)
        name = self.catalog.creatures[target.definition_id].name
        return _HandlerResult(
            (f"You focus on {name}.",),
            (DomainEvent("combat.target_selected", {"target": target.instance_id}),),
            True,
        )

    def _assess(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        query = self._query(command.args)
        target, error = self._resolve_creature(state, query)
        if target is None:
            return _HandlerResult((error or "Assess which foe?",))
        base_definition = self.catalog.creatures[target.definition_id]
        definition = self._effective_beginner_creature_definition(
            state, base_definition
        )
        weapon = equipped_item(
            state.character,
            self.catalog.items,
            "main_hand",
        )
        armor = equipped_item(
            state.character,
            self.catalog.items,
            "body",
        )
        relative_danger = (
            definition.offense
            + definition.defense
            + definition.level * 6
            - player_offense(state.character, weapon)
            - player_defense(state.character, armor)
        )
        if relative_danger <= -30:
            danger = "You hold a clear mechanical advantage."
        elif relative_danger <= 10:
            danger = "The matchup is close enough to punish mistakes."
        elif relative_danger <= 40:
            danger = "The foe has a dangerous mechanical advantage."
        else:
            danger = "Direct engagement is overwhelmingly unfavorable."

        ratio = target.health / definition.max_health
        condition = (
            "stable"
            if ratio >= 0.9
            else "damaged"
            if ratio >= 0.5
            else "near collapse"
        )
        opponent_count = len(self._live_creatures(state))
        lines = [
            f"Assessment: {base_definition.name}",
            f"Target condition: {condition}.",
            danger,
        ]
        if self._difficulty_curve_active_in_room(state) and not base_definition.nonlethal:
            band = self._beginner_difficulty_band(state)
            lines.append(
                f"Authored phase pressure: {band.label} "
                f"(offense {band.enemy_offense_modifier:+d}, defense "
                f"{band.enemy_defense_modifier:+d}, damage "
                f"{band.enemy_damage_min_modifier:+d}/"
                f"{band.enemy_damage_max_modifier:+d})."
            )
        lines.append(
            f"Behavior: {base_definition.behavior_profile.title()} · "
            f"base recovery {base_definition.action_interval} field sec."
        )
        actor = state.battle.actors.get(creature_actor_id(target.instance_id))
        if actor is not None and state.battle.room_id == state.character.room_id:
            remaining = max(0.0, actor.next_action_at - state.battle.time)
            lines.append(
                "Intent: "
                f"{(actor.current_intent or 'recovering').replace('_', ' ')} · "
                f"{timing_description(remaining, state.character.perception)}."
            )
            effects = [
                name.replace("_", " ")
                for name, effect in sorted(
                    state.battle.effects.get(actor.actor_id, {}).items()
                )
                if effect.expires_at > state.battle.time
            ]
            if effects:
                lines.append(f"Tactical states: {', '.join(effects)}.")
        else:
            lines.append(
                "Intent: not committed until a successful hard action starts the "
                "command-resolved field clock."
            )
        if opponent_count > 1:
            lines.append(
                f"Formation: {opponent_count} active opponents each hold an independent "
                "readiness lane; no invisible crowd penalty is added to this target's roll."
            )
        if state.character.prone:
            lines.append("Priority: regain your feet with STAND.")
        elif active_bleeding(state.character):
            lines.append("Priority: use STABILIZE before bleeding compounds.")
        elif state.character.health * 2 < state.character.max_health:
            lines.append(
                "Priority: create distance through an available exit before the next exchange."
            )
        elif relative_danger > 10:
            lines.append(
                "Priority: use a defensive stance or reaction and preserve an exit."
            )
        else:
            lines.append(
                "Priority: manage roundtime and watch for wound escalation."
            )
        return _HandlerResult(("\n".join(lines),))

    def _health(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        character = state.character
        lines = [f"Health: {max(0, character.health)}/{character.max_health}"]
        if state.incapacitation is not None:
            remaining = max(
                0,
                math.ceil(state.incapacitation.recover_at - now),
            )
            lines.append(
                f"Status: incapacitated; recovery "
                f"{'available now' if remaining == 0 else f'in {remaining} sec.'}"
            )
        elif character.resting:
            lines.append("Status: resting.")
        if not character.wounds:
            lines.append("Wounds: none")
        else:
            lines.append("Wounds:")
            for wound in character.wounds:
                bleed = f", bleeding {wound.bleeding}" if wound.bleeding else ""
                lines.append(
                    f"  {wound.location}: severity {wound.severity}{bleed}"
                )
            total_bleeding = active_bleeding(character)
            if total_bleeding:
                lines.append(
                    f"Active bleeding: {total_bleeding} damage per 10-second pulse."
                )
            disabled = disabled_limbs(character)
            if disabled:
                lines.append(f"Disabled: {_natural_list(list(disabled))}.")
        if self._foundation_injury_should_be_active(state):
            lines.append("Use INJURY for the authored level 5-8 recovery plan and exact pressure modifiers.")
        elif self._journey_injury_should_be_active(state):
            lines.append("Use INJURY for the authored level 15-18 sensory-echo recovery plan and exact pressure modifiers.")
        return _HandlerResult(("\n".join(lines),))

    def _injury(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        projection = self._beginner_difficulty_projection(state)
        modifiers = projection["modifiers"]
        injury = projection["injury"]
        journey_mode = projection.get("phase_id") == self.catalog.journeyman_experience.id
        if journey_mode:
            heading = "JOURNEY DIFFICULTY CURVE"
            cadence_lines = (
                "Levels 11-14: easy / guided re-entry.",
                "Levels 15-18: shock pressure / punishing with ample recovery.",
                "Levels 19-20: average / stabilized horizon.",
            )
            active_tools = (
                "Recovery tools: HEALTH, STABILIZE SENSORIUM, REST, DEFENSE, "
                "WITHDRAW STATUS, and COMPANION ORDER GUARD."
            )
            stabilized_text = (
                "The event remains part of the campaign record; levels 19-20 use average pressure."
            )
        else:
            heading = "FOUNDATION DIFFICULTY CURVE"
            cadence_lines = (
                "Levels 1-4: easy / guided onboarding.",
                "Levels 5-8: shock pressure / punishing with ample recovery.",
                "Levels 9-10: average / stabilized readiness.",
            )
            active_tools = (
                "Recovery tools: HEALTH, STABILIZE RIBS, REST, DEFENSE, "
                "WITHDRAW STATUS, and COMPANION ORDER GUARD."
            )
            stabilized_text = (
                "The scar remains part of the campaign record; levels 9-10 use average pressure."
            )
        lines = [heading, *cadence_lines, "", f"Current: level {state.character.level} — {projection['label']}", str(projection["summary"]), (
            "Enemy modifiers: offense "
            f"{int(modifiers['enemy_offense']):+d}; defense "
            f"{int(modifiers['enemy_defense']):+d}; armor "
            f"{int(modifiers['enemy_armor']):+d}; damage "
            f"{int(modifiers['enemy_damage_min']):+d}/"
            f"{int(modifiers['enemy_damage_max']):+d}; attack roundtime "
            f"{int(modifiers['player_roundtime']):+d}."
        )]
        if bool(injury["active"]):
            lines.extend((
                "",
                f"Active condition: {injury['label']}",
                f"Severity {injury['severity']}; bleeding {injury['bleeding']}.",
                str(injury["summary"]),
                str(injury["recovery"]),
                active_tools,
                f"Rehabilitation target: level {injury['clear_level']}.",
            ))
        elif bool(injury["rehabilitated"]):
            lines.extend((
                "",
                f"Rehabilitated: {injury['label']} is no longer an active combat penalty.",
                stabilized_text,
            ))
        else:
            lines.extend((
                "",
                f"The authored condition begins at level {injury['trigger_level']} and clears at level {injury['clear_level']}.",
                "No hidden condition penalty is active now.",
            ))
        return _HandlerResult(("\n".join(lines),))

    def _stabilize(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        patch_kit = next(
            (
                item
                for item in state.character.inventory
                if item.definition_id == "patch_kit"
            ),
            None,
        )
        journey_injury = self.catalog.journeyman_experience.difficulty_curve.injury
        journey_wound = self._journey_injury_wound(state)
        sensory_anchor = next(
            (
                item
                for item in state.character.inventory
                if item.definition_id == journey_injury.recovery_item_id
            ),
            None,
        )
        sensory_brace_available = bool(
            sensory_anchor is not None
            and journey_wound is not None
            and journey_wound.severity > 1
            and "journey_injury_braced" not in state.flags
        )
        candidates = [
            wound
            for wound in state.character.wounds
            if wound.bleeding > 0
            or (patch_kit is not None and wound.severity > 1)
            or (sensory_brace_available and wound is journey_wound)
        ]
        if not candidates:
            return _HandlerResult(
                ("You have no active bleeding or treatable severe wound.",)
            )

        query = self._query(command.args)
        if query:
            treatable_locations = sorted(
                {wound.location for wound in candidates}
            )
            candidates = [
                wound
                for wound in candidates
                if query == wound.location.casefold()
                or query in wound.location.casefold()
            ]
            if not candidates:
                locations = _natural_list(treatable_locations)
                return _HandlerResult(
                    (f"No treatable wound matches {query!r}. Treatable sites: {locations}.",)
                )
        wound = max(candidates, key=lambda item: (item.bleeding, item.severity))
        before = wound.bleeding
        severity_before = wound.severity
        use_sensory_anchor = bool(
            sensory_brace_available and wound is journey_wound
        )
        if use_sensory_anchor:
            wound.bleeding = 0
            wound.severity = max(1, wound.severity - 1)
            state.flags.add("journey_injury_braced")
            anchor_name = self.catalog.items[journey_injury.recovery_item_id].name
            lines = (
                f"You align the {anchor_name} with the five field references and slow your breathing.",
                f"Sensorium severity falls from {severity_before} to {wound.severity}.",
                "The band remains available as an orientation aid; it is not a diagnosis or an automatic cure.",
                "Roundtime: 4 sec.",
            )
        elif patch_kit is not None:
            self._remove_inventory_item(state, patch_kit)
            wound.bleeding = 0
            wound.severity = max(1, wound.severity - 1)
            lines = (
                f"You break the seal on a patch kit and bind the {wound.location}.",
                f"Bleeding falls from {before} to 0.",
                f"Severity falls from {severity_before} to {wound.severity}.",
                "Roundtime: 4 sec.",
            )
        else:
            wound.bleeding = max(0, before - 1)
            lines = (
                f"You apply direct pressure to the {wound.location}.",
                f"Bleeding falls from {before} to {wound.bleeding}.",
                "Roundtime: 4 sec.",
            )
        if (
            patch_kit is not None
            and not use_sensory_anchor
            and wound.location
            == self.catalog.beginner_experience.difficulty_curve.injury.location
        ):
            state.flags.add("foundation_injury_braced")
            lines += (
                "The brace controls the immediate tear, but the authored injury remains active until the level 9 rehabilitation milestone.",
            )
        self._set_roundtime(state, now, 4)
        if active_bleeding(state.character) == 0:
            state.character.condition_pulse_at = now
        return _HandlerResult(
            lines,
            (
                DomainEvent(
                    "condition.wound_stabilized",
                    {
                        "location": wound.location,
                        "bleeding_before": before,
                        "bleeding_after": wound.bleeding,
                        "severity_before": severity_before,
                        "severity_after": wound.severity,
                        "patch_kit_consumed": (
                            patch_kit is not None and not use_sensory_anchor
                        ),
                        "sensory_anchor_used": use_sensory_anchor,
                    },
                ),
            ),
            True,
        )



























































    def _resolve_creation_class(self, query: str):
        normalized = query.strip().casefold().replace("-", "_").replace(" ", "_")
        exact = self.catalog.creation.classes.get(normalized)
        if exact is not None:
            return exact
        matches = [
            definition
            for definition in self.catalog.creation.classes.values()
            if query.strip().casefold() == definition.name.casefold()
            or definition.name.casefold().startswith(query.strip().casefold())
        ]
        return matches[0] if len(matches) == 1 else None

    def _build_summary(self, state: GameState) -> str:
        build = state.character.build
        lines = ["Character foundation:"]
        if build.status == "legacy_preserved":
            lines.extend(
                (
                    "  Status: legacy character preserved without inferred class or faction.",
                    "  Optional guidance remains available with GUIDE START.",
                )
            )
            return "\n".join(lines)
        selected_class = (
            self.catalog.creation.classes.get(build.class_id)
            if build.class_id is not None
            else None
        )
        lines.append(f"  Status: {build.status.replace('_', ' ')}")
        lines.append(
            "  Class: "
            + (
                f"{selected_class.name} "
                f"({self.catalog.creation.factions[selected_class.faction_id].name} route)"
                if selected_class is not None
                else "not selected"
            )
        )
        spent = allocation_cost(
            self.catalog.creation,
            build.base_attributes,
        )
        lines.append(
            f"  Allocation: {spent}/{self.catalog.creation.budget} weighted points "
            f"({build.allocation_mode or 'not selected'})"
        )
        lines.append(
            "  Base stats: "
            + ", ".join(
                f"{attribute_id.replace('_', ' ')} "
                f"{build.base_attributes[attribute_id]}"
                for attribute_id in ATTRIBUTE_IDS
            )
        )
        lines.append(
            f"  Guidance: {build.tutorial_status.replace('_', ' ')}"
        )
        if build.status == "pending":
            lines.append(
                "Use BUILD CLASS, BUILD AUTO or BUILD RESET/SET, "
                "BUILD TUTORIAL GUIDED|SKIP, then BUILD CONFIRM."
            )
        return "\n".join(lines)

    def _build(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        build = state.character.build
        if not command.args or command.args[0].casefold() == "status":
            return _HandlerResult((self._build_summary(state),))
        action = command.args[0].casefold()
        if action in {"classes", "class"} and len(command.args) == 1:
            lines = [
                "Available classes (selection defines a provisional faction route):"
            ]
            for definition in self.catalog.creation.classes.values():
                faction = self.catalog.creation.factions[definition.faction_id]
                beginner = (
                    " [recommended first character]"
                    if definition.id == "soldier"
                    else ""
                )
                lines.append(
                    f"  {definition.id:<16} {definition.name} · "
                    f"{faction.name} · {definition.role} · "
                    f"{definition.difficulty}{beginner}"
                )
            lines.append("Use BUILD CLASS <id> to preview one route.")
            return _HandlerResult(("\n".join(lines),))
        if build.status != "pending":
            return _HandlerResult(
                (
                    "This character foundation is already preserved. "
                    "Future faction-HQ content will provide an explicit correction point.",
                )
            )
        if action == "class":
            query = " ".join(command.args[1:]).strip()
            definition = self._resolve_creation_class(query) if query else None
            if definition is None:
                return _HandlerResult(
                    (
                        "Name one unambiguous class ID. Use BUILD CLASSES to list them.",
                    )
                )
            changed = build.class_id != definition.id
            build.class_id = definition.id
            if build.allocation_mode == "recommended":
                package = self.catalog.creation.packages[
                    definition.recommended_package_id
                ]
                build.base_attributes = dict(package.attributes)
                apply_base_attributes(
                    state.character,
                    build.base_attributes,
                    self.catalog.progression.options,
                )
            faction = self.catalog.creation.factions[definition.faction_id]
            return _HandlerResult(
                (
                    f"Class preview: {definition.name} · {definition.role}.",
                    definition.summary,
                    f"Tradeoff: {definition.tradeoff}",
                    f"Story route: {faction.route_label}. "
                    "Faction membership is not assigned here.",
                    "Use BUILD AUTO for its equal-budget recommendation, "
                    "or BUILD RESET then BUILD SET for manual allocation.",
                ),
                (
                    DomainEvent(
                        "build.class_selected",
                        {
                            "class_id": definition.id,
                            "faction_route_id": definition.faction_id,
                        },
                    ),
                )
                if changed
                else (),
                changed,
            )
        if action == "auto":
            if build.class_id is None:
                return _HandlerResult(
                    ("Choose a class with BUILD CLASS <id> first.",)
                )
            definition = self.catalog.creation.classes[build.class_id]
            package = self.catalog.creation.packages[
                definition.recommended_package_id
            ]
            changed = (
                build.allocation_mode != "recommended"
                or build.base_attributes != dict(package.attributes)
            )
            build.base_attributes = dict(package.attributes)
            build.allocation_mode = "recommended"
            apply_base_attributes(
                state.character,
                build.base_attributes,
                self.catalog.progression.options,
            )
            return _HandlerResult(
                (
                    f"Applied {package.name}: {package.summary}",
                    self._build_summary(state),
                ),
                (
                    DomainEvent(
                        "build.allocation_previewed",
                        {
                            "mode": "recommended",
                            "package_id": package.id,
                        },
                    ),
                )
                if changed
                else (),
                changed,
            )
        if action == "reset":
            reset = minimum_allocation(self.catalog.creation)
            changed = (
                build.base_attributes != reset
                or build.allocation_mode != "manual"
            )
            build.base_attributes = reset
            build.allocation_mode = "manual"
            apply_base_attributes(
                state.character,
                build.base_attributes,
                self.catalog.progression.options,
            )
            return _HandlerResult(
                (
                    "Manual allocation reset to every authored minimum.",
                    f"{self.catalog.creation.budget} weighted points remain.",
                ),
                (DomainEvent("build.allocation_reset"),) if changed else (),
                changed,
            )
        if action == "set":
            if len(command.args) != 3:
                return _HandlerResult(
                    ("Use BUILD SET <attribute> <value>.",)
                )
            attribute_id = command.args[1].casefold()
            if attribute_id == "combat":
                attribute_id = "combat_skill"
            if attribute_id not in ATTRIBUTE_IDS:
                return _HandlerResult(
                    (
                        "Attribute must be strength, agility, perception, "
                        "or combat_skill.",
                    )
                )
            try:
                value = int(command.args[2])
            except ValueError:
                return _HandlerResult(("Attribute value must be an integer.",))
            preview = dict(build.base_attributes)
            preview[attribute_id] = value
            try:
                spent = validate_allocation(
                    self.catalog.creation,
                    preview,
                    require_full_budget=False,
                )
            except ValueError as exc:
                return _HandlerResult((str(exc),))
            changed = (
                build.base_attributes != preview
                or build.allocation_mode != "manual"
            )
            build.base_attributes = preview
            build.allocation_mode = "manual"
            apply_base_attributes(
                state.character,
                build.base_attributes,
                self.catalog.progression.options,
            )
            definition = self.catalog.creation.attributes[attribute_id]
            return _HandlerResult(
                (
                    f"{definition.name} preview set to {value}. "
                    f"{self.catalog.creation.budget - spent} weighted points remain.",
                    "Next point: "
                    + "; ".join(
                        str(item)
                        for item in stat_effect_projection(
                            state.character,
                            attribute_id,
                        )["next"]
                    ),
                ),
                (
                    DomainEvent(
                        "build.allocation_previewed",
                        {
                            "mode": "manual",
                            "attribute": attribute_id,
                            "value": value,
                            "spent": spent,
                        },
                    ),
                )
                if changed
                else (),
                changed,
            )
        if action == "tutorial":
            if len(command.args) != 2:
                return _HandlerResult(
                    ("Use BUILD TUTORIAL GUIDED or BUILD TUTORIAL SKIP.",)
                )
            choice = command.args[1].casefold()
            if choice in {"guided", "start", "yes"}:
                next_status = "active"
                next_step = self.catalog.creation.tutorial.steps[0].id
            elif choice in {"skip", "free", "no"}:
                next_status = "skipped"
                next_step = None
            else:
                return _HandlerResult(
                    ("Tutorial choice must be GUIDED or SKIP.",)
                )
            changed = (
                build.tutorial_status != next_status
                or build.tutorial_step_id != next_step
            )
            self._clear_tutorial_evidence(state)
            build.tutorial_status = next_status
            build.tutorial_step_id = next_step
            return _HandlerResult(
                (
                    "Guided Start selected. It is optional, reward-free, and resumable."
                    if next_status == "active"
                    else "Explore Freely selected. GUIDE START remains available later.",
                ),
                (
                    DomainEvent(
                        "tutorial.preference_changed",
                        {"status": next_status},
                    ),
                )
                if changed
                else (),
                changed,
            )
        if action == "confirm":
            if build.class_id is None:
                return _HandlerResult(
                    ("Choose a class before confirming.",)
                )
            if build.allocation_mode not in {"recommended", "manual"}:
                return _HandlerResult(
                    ("Choose BUILD AUTO or make a manual allocation first.",)
                )
            if build.tutorial_status == "offered":
                return _HandlerResult(
                    (
                        "Choose BUILD TUTORIAL GUIDED or BUILD TUTORIAL SKIP first.",
                    )
                )
            try:
                validate_allocation(
                    self.catalog.creation,
                    build.base_attributes,
                    require_full_budget=True,
                )
            except ValueError as exc:
                return _HandlerResult((str(exc),))
            definition = self.catalog.creation.classes[build.class_id]
            telemetry = state.beginner_telemetry
            playtest_profile_locked = False
            if telemetry.playtest_status != "not_started":
                selected_family = family_for_class(definition.id)
                if selected_family is None:
                    return _HandlerResult(
                        (
                            f"Class {definition.id!r} is not mapped to a measured gameplay family. "
                            "The build was not confirmed.",
                        )
                    )
                if (
                    telemetry.playtest_family is not None
                    and telemetry.playtest_family != selected_family
                ):
                    return _HandlerResult(
                        (
                            "This playtest targets "
                            f"{telemetry.playtest_family}, but {definition.name} belongs to "
                            f"{selected_family}. Choose a matching class or use "
                            "PLAYTEST RESTART CONFIRM before changing the measured profile.",
                        )
                    )
                telemetry.playtest_family = selected_family
                telemetry.playtest_class_id = definition.id
                if telemetry.playtest_profile_source == "pending_build":
                    telemetry.playtest_profile_source = "inferred"
                playtest_profile_locked = True
            training = state.character.training
            training.profile_id = definition.training_profile_id
            training.profile_changes_remaining = 0
            training.profile_locked = False
            build.status = "confirmed"
            apply_base_attributes(
                state.character,
                build.base_attributes,
                self.catalog.progression.options,
            )
            faction = self.catalog.creation.factions[definition.faction_id]
            events = [
                DomainEvent(
                    "build.confirmed",
                    {
                        "class_id": definition.id,
                        "faction_route_id": definition.faction_id,
                        "allocation_mode": build.allocation_mode,
                        "tutorial_status": build.tutorial_status,
                    },
                )
            ]
            if playtest_profile_locked:
                events.append(
                    DomainEvent(
                        "playtest.profile_locked",
                        {
                            "family": telemetry.playtest_family,
                            "class_id": telemetry.playtest_class_id,
                            "source": telemetry.playtest_profile_source,
                        },
                    )
                )
            return _HandlerResult(
                (
                    f"Character foundation confirmed: {definition.name}.",
                    f"Your provisional story route points toward {faction.route_label}; "
                    "you have not joined that faction.",
                    (
                        "Guided Start is active."
                        if build.tutorial_status == "active"
                        else "Guided Start was skipped and remains available with GUIDE START."
                    ),
                    "The Sprawl is now open.",
                ),
                tuple(events),
                True,
            )
        return _HandlerResult(
            (
                "Unknown BUILD action. Use BUILD for a concise status and next steps.",
            )
        )

    def _guide(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        build = state.character.build
        if not command.args or command.args[0].casefold() in {"status", "list"}:
            if build.tutorial_status != "active":
                return _HandlerResult(
                    (
                        f"Guided Start is {build.tutorial_status}. "
                        "Use GUIDE START or GUIDE SKIP.",
                    )
                )
            tutorial = self.catalog.creation.tutorial
            step = next(
                item
                for item in tutorial.steps
                if item.id == build.tutorial_step_id
            )
            step_number = tutorial.steps.index(step) + 1
            return _HandlerResult(
                (
                    f"Guided Start {step_number}/{len(tutorial.steps)}: {step.description}",
                    f"Why: {step.why}",
                    f"Try: {step.suggested_command}",
                    "Use GUIDE SYNC if you already completed this step out of order.",
                )
            )
        action = command.args[0].casefold()
        if action in {"start", "restart", "resume"}:
            step_id = self.catalog.creation.tutorial.steps[0].id
            changed = (
                build.tutorial_status != "active"
                or build.tutorial_step_id != step_id
            )
            self._clear_tutorial_evidence(state)
            build.tutorial_status = "active"
            build.tutorial_step_id = step_id
            return _HandlerResult(
                (
                    "Guided Start is active. It grants no mechanical reward "
                    "and can be skipped at any time.",
                    self.catalog.creation.tutorial.steps[0].description,
                ),
                (
                    DomainEvent(
                        "tutorial.preference_changed",
                        {"status": "active"},
                    ),
                )
                if changed
                else (),
                changed,
            )
        if action in {"sync", "catchup", "catch-up"}:
            progress = self._apply_tutorial_progress(state, ())
            if progress.changed:
                return progress
            if build.tutorial_status != "active":
                return _HandlerResult(
                    ("Guided Start is not active. Use GUIDE START to begin it.",)
                )
            step = next(
                item
                for item in self.catalog.creation.tutorial.steps
                if item.id == build.tutorial_step_id
            )
            return _HandlerResult(
                (
                    "Guided Start is synchronized, but the current step still needs an action.",
                    f"Next: {step.description}",
                    f"Try: {step.suggested_command}",
                )
            )
        if action in {"skip", "pause", "stop"}:
            changed = (
                build.tutorial_status != "skipped"
                or build.tutorial_step_id is not None
            )
            build.tutorial_status = "skipped"
            build.tutorial_step_id = None
            return _HandlerResult(
                (
                    "Guided Start is paused. GUIDE START can restart it later.",
                ),
                (
                    DomainEvent(
                        "tutorial.preference_changed",
                        {"status": "skipped"},
                    ),
                )
                if changed
                else (),
                changed,
            )
        return _HandlerResult(("Use GUIDE, GUIDE START, GUIDE SYNC, or GUIDE SKIP.",))

    def _info(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        character = state.character
        remaining = self._hard_recovery_remaining(state, now)
        load = calculate_encumbrance(character, self.catalog.items)
        selected_class = (
            self.catalog.creation.classes.get(character.build.class_id)
            if character.build.class_id is not None
            else None
        )
        selected_faction = (
            self.catalog.creation.factions[selected_class.faction_id]
            if selected_class is not None
            else None
        )
        active_course = (
            self.catalog.courses[character.course.active_course_id]
            if character.course.active_course_id is not None
            else None
        )
        return _HandlerResult(
            (
                "\n".join(
                    (
                        f"Name: {character.name}",
                        f"Level: {character.level}",
                        (
                            f"Class: {selected_class.name}"
                            if selected_class is not None
                            else "Class: unselected"
                        ),
                        (
                            f"Faction route: {selected_faction.name} "
                            "(membership unassigned)"
                            if selected_faction is not None
                            else "Faction route: unassigned"
                        ),
                        f"Foundation: {character.build.status.replace('_', ' ')}",
                        f"Location: {self.catalog.rooms[character.room_id].title}",
                        f"Spatial memory: {len(state.visited_rooms)}/"
                        f"{len(self.catalog.rooms)} locations",
                        f"Stance: {character.stance.value}",
                        f"Defense: {character.defense_mode.value}",
                        (
                            "Position: prone"
                            if character.prone
                            else "Position: resting"
                            if character.resting
                            else "Position: standing"
                        ),
                        (
                            "Status: incapacitated"
                            if state.incapacitation is not None
                            else "Status: active"
                        ),
                        f"Health: {max(0, character.health)}/{character.max_health}",
                        f"Training: {character.training.physical_points} physical / "
                        f"{character.training.mental_points} mental points",
                        "Path: "
                        + self.catalog.progression.profiles[
                            character.training.profile_id
                        ].name,
                        (
                            "Course: none active"
                            if active_course is None
                            else "Course: "
                            f"{active_course.name} "
                            f"(step {character.course.step_index + 1}/"
                            f"{len(active_course.steps)})"
                        ),
                        f"Load: {load.carried_bulk}/{load.hard_limit} "
                        f"bulk ({load.tier})",
                        f"Hard recovery: {remaining} sec.",
                        f"Turn: {state.turn}",
                    )
                ),
            )
        )

    def _effects(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        character = state.character
        lines = ["Active effects:"]
        effects: list[str] = []
        incapacitation = state.incapacitation
        if incapacitation is not None:
            remaining = max(0, math.ceil(incapacitation.recover_at - now))
            effects.append(
                f"incapacitated (RECOVER "
                f"{'available' if remaining == 0 else f'in {remaining} sec.'})"
            )
            if incapacitation.help_requested:
                effects.append("recovery signal recorded")
        stun = roundtime_remaining(character.stunned_until, now)
        if stun:
            effects.append(f"stunned ({stun} sec.)")
        if character.prone:
            effects.append("prone")
        if character.resting:
            effects.append("resting")
        bleeding = active_bleeding(character)
        if bleeding:
            effects.append(f"bleeding ({bleeding} damage per pulse)")
        disabled = disabled_limbs(character)
        if disabled:
            effects.append(f"disabled: {_natural_list(list(disabled))}")
        load = calculate_encumbrance(character, self.catalog.items)
        if load.tier != "unburdened":
            effects.append(
                f"{load.tier} ({load.carried_bulk}/{load.hard_limit} bulk; "
                f"+{load.recovery_penalty} sec. recovery)"
            )
        armor_state = self._equipped_item_state(state, "body")
        if armor_state is not None and armor_state.durability is not None:
            armor_definition = self.catalog.items[armor_state.definition_id]
            if armor_state.durability < armor_definition.max_durability:
                effects.append(
                    f"{armor_definition.name} durability "
                    f"{armor_state.durability}/{armor_definition.max_durability}"
                )
        if state.battle.room_id == state.character.room_id:
            for actor_id, actor_effects in sorted(state.battle.effects.items()):
                actor_name = self.combat_scheduler._actor_name(
                    state, actor_id, self.catalog
                )
                for name, effect in sorted(actor_effects.items()):
                    if effect.expires_at <= state.battle.time:
                        continue
                    remaining_field = max(
                        0.0, effect.expires_at - state.battle.time
                    )
                    effects.append(
                        f"{actor_name}: {name.replace('_', ' ')} "
                        f"(magnitude {effect.magnitude}; {remaining_field:.1f} field sec.; "
                        f"{effect.uses_remaining} use{'s' if effect.uses_remaining != 1 else ''})"
                    )
        if effects:
            lines.extend(f"  {effect}" for effect in effects)
        else:
            lines.append("  none")
        return _HandlerResult(("\n".join(lines),))

    def _roundtime(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        if state.incapacitation is not None:
            remaining = max(
                0,
                math.ceil(state.incapacitation.recover_at - now),
            )
            return _HandlerResult(
                (
                    f"Incapacitated recovery: {remaining} sec."
                    if remaining
                    else "Incapacitated recovery is available now.",
                )
            )
        remaining = self._hard_recovery_remaining(state, now)
        roundtime = roundtime_remaining(state.character.roundtime_until, now)
        stun = roundtime_remaining(state.character.stunned_until, now)
        player_line = (
            "You are ready to act."
            if remaining == 0
            else (
                f"Hard recovery remaining: {remaining} sec. "
                f"(roundtime {roundtime}; stun {stun})"
            )
        )
        return _HandlerResult(
            (player_line,)
            + self.combat_scheduler.status_lines(state, now)
        )

    def _search(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        room = self.catalog.rooms[state.character.room_id]
        self._set_roundtime(state, now, 3)
        if room.search is None:
            return _HandlerResult(
                ("You search carefully but find nothing concealed.", "Roundtime: 3 sec."),
                (DomainEvent("room.searched", {"room_id": room.id, "found": False}),),
                True,
            )
        if room.search.id in state.revealed:
            return _HandlerResult(
                ("You search again but uncover nothing new.", "Roundtime: 3 sec."),
                (DomainEvent("room.searched", {"room_id": room.id, "found": False}),),
                True,
            )
        state.revealed.add(room.search.id)
        if room.search.flag:
            state.flags.add(room.search.flag)
        if room.search.item_id:
            revealed_item = self._spawn_item(state, room.search.item_id)
            state.room_items.setdefault(room.id, []).append(revealed_item)
        else:
            revealed_item = None
        return _HandlerResult(
            (room.search.text, "Roundtime: 3 sec."),
            (
                DomainEvent(
                    "room.secret_found",
                    {
                        "room_id": room.id,
                        "reveal_id": room.search.id,
                        "item_id": room.search.item_id,
                        "instance_id": (
                            revealed_item.instance_id if revealed_item else None
                        ),
                        "flag": room.search.flag,
                    },
                ),
            ),
            True,
        )

    def _say(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        message = " ".join(command.args).strip()
        if not message:
            return _HandlerResult(("Say what?",))
        return _HandlerResult(
            (f'You say, "{message}"',),
            (DomainEvent("social.spoken", {"speaker": state.character.key}),),
            True,
        )

    def _emote(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        action = " ".join(command.args).strip()
        if not action:
            return _HandlerResult(("Emote what?",))
        return _HandlerResult(
            (f"{state.character.name} {action}",),
            (DomainEvent("social.emoted", {"speaker": state.character.key}),),
            True,
        )

    def _wait(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        if state.incapacitation is not None:
            remaining = max(
                0,
                math.ceil(state.incapacitation.recover_at - now),
            )
            return _HandlerResult(
                (
                    f"You remain incapacitated. RECOVER is available in {remaining} sec."
                    if remaining
                    else "You remain incapacitated. RECOVER is available now.",
                )
            )
        if state.character.resting:
            return _HandlerResult(
                (
                    "You continue resting. Health recovers in bounded "
                    "15-second pulses.",
                )
            )
        remaining = self._hard_recovery_remaining(state, now)
        if remaining:
            return _HandlerResult((f"You steady yourself. {remaining} sec remain.",))
        return _HandlerResult(("You are ready.",))

    def _rest(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if command.args:
            return _HandlerResult(("Use REST without a target.",))
        if state.character.resting:
            return _HandlerResult(("You are already resting.",))
        if self._live_creatures(state):
            return _HandlerResult(
                ("You cannot settle into recovery with an active opponent nearby.",)
            )
        if active_bleeding(state.character):
            return _HandlerResult(
                ("Stabilize active bleeding before attempting to rest.",)
            )
        if state.character.health >= state.character.max_health:
            return _HandlerResult(("You are already at full health.",))
        state.character.resting = True
        state.character.rest_pulse_at = now
        self._set_roundtime(state, now, 2)
        return _HandlerResult(
            (
                "You settle into a guarded resting posture.",
                "Health will recover in bounded 15-second pulses.",
                "Roundtime: 2 sec.",
            ),
            (DomainEvent("recovery.rest_started"),),
            True,
        )

    def _recover(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if command.args:
            return _HandlerResult(("Use RECOVER without a target.",))
        incapacitation = state.incapacitation
        if incapacitation is None:
            return _HandlerResult(("You are not incapacitated.",))
        remaining = max(0, math.ceil(incapacitation.recover_at - now))
        if remaining:
            return _HandlerResult(
                (
                    f"Recovery is not available for {remaining} sec. "
                    "You may SIGNAL for assistance.",
                )
            )
        lines: list[str] = []
        events = self._complete_recovery(state, now, lines)
        return _HandlerResult(tuple(lines), tuple(events), True)

    def _signal(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if command.args:
            return _HandlerResult(("Use SIGNAL without a target.",))
        incapacitation = state.incapacitation
        if incapacitation is None:
            return _HandlerResult(
                ("You are not incapacitated; no recovery signal is needed.",)
            )
        if incapacitation.help_requested:
            return _HandlerResult(
                ("Your recovery signal is already recorded.",)
            )
        incapacitation.help_requested = True
        return _HandlerResult(
            (
                "You trigger a recovery signal. The request is recorded "
                "for a future rescue service.",
            ),
            (
                DomainEvent(
                    "recovery.help_requested",
                    {
                        "room_id": incapacitation.origin_room_id,
                        "downed_at": incapacitation.downed_at,
                    },
                ),
            ),
            True,
        )

    def _queue(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        if not command.args:
            queued = state.queued_action
            if queued is None:
                return _HandlerResult(("No hard action is queued.",))
            remaining = max(0, math.ceil(queued.execute_at - now))
            rendered = " ".join(
                (queued.intent.command.upper(), *queued.intent.args)
            )
            return _HandlerResult(
                (f"Queued: {rendered} ({remaining} sec until eligible).",)
            )
        if state.queued_action is not None:
            return _HandlerResult(
                ("One action is already queued. Use CANCEL before replacing it.",)
            )
        try:
            nested = self.parser.parse(" ".join(command.args))
        except CommandParseError as exc:
            return _HandlerResult((f"Cannot queue that action: {exc}",))
        effective_hard = self._effective_hard(nested)
        if not effective_hard:
            return _HandlerResult(
                ("Only a hard action can be queued; soft commands are available now.",)
            )
        if state.character.prone and nested.name != "stand":
            return _HandlerResult(
                ("While prone, only STAND can be queued as a hard action.",)
            )
        intent = ActionIntent(nested.name, nested.args)
        remaining = self._hard_recovery_remaining(state, now)
        if remaining == 0:
            handled = self._run_intent(
                state,
                intent,
                now,
                require_hard=True,
            )
            return _HandlerResult(
                ("You are ready, so the requested action executes now.",)
                + handled.lines,
                handled.events,
                handled.changed,
                handled.quit,
            )
        execute_at = self._hard_ready_at(state)
        state.queued_action = QueuedAction(
            intent=intent,
            queued_at=now,
            execute_at=execute_at,
        )
        return _HandlerResult(
            (
                f"Queued {nested.name.upper()} for the end of hard recovery.",
                "Use QUEUE to inspect it or CANCEL to remove it.",
            ),
            (
                DomainEvent(
                    "action.queued",
                    {
                        "command": nested.name,
                        "execute_at": execute_at,
                    },
                ),
            ),
            True,
        )

    def _cancel(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        queued = state.queued_action
        if queued is None:
            return _HandlerResult(("No action is queued.",))
        state.queued_action = None
        return _HandlerResult(
            (f"Canceled queued {queued.intent.command.upper()}.",),
            (
                DomainEvent(
                    "action.queue_canceled",
                    {"command": queued.intent.command},
                ),
            ),
            True,
        )

    def _again(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        intent = state.last_action
        if intent is None:
            return _HandlerResult(
                ("There is no meaningful action in history to repeat yet.",)
            )
        parsed = self._parsed_from_intent(intent)
        effective_hard = self._effective_hard(parsed)
        remaining = self._hard_recovery_remaining(state, now)
        if effective_hard and remaining:
            return _HandlerResult(
                (
                    f"{intent.command.upper()} is still blocked by hard recovery "
                    f"({remaining} sec). Use QUEUE <action> to schedule it explicitly.",
                )
            )
        handled = self._run_intent(state, intent, now)
        return _HandlerResult(
            (f"[Again] {intent.command.upper()}",) + handled.lines,
            handled.events,
            handled.changed,
            handled.quit,
        )

    def _build_projection(self, state: GameState) -> dict[str, object]:
        character = state.character
        build = character.build
        selected_class = (
            self.catalog.creation.classes.get(build.class_id)
            if build.class_id is not None
            else None
        )
        selected_faction = (
            self.catalog.creation.factions[selected_class.faction_id]
            if selected_class is not None
            else None
        )
        if build.status == "legacy_preserved":
            spent: int | None = None
            remaining: int | None = None
        else:
            spent = allocation_cost(
                self.catalog.creation,
                build.base_attributes,
            )
            remaining = self.catalog.creation.budget - spent
        active_tutorial_step = next(
            (
                step
                for step in self.catalog.creation.tutorial.steps
                if step.id == build.tutorial_step_id
            ),
            None,
        )
        return {
            "status": build.status,
            "class_id": build.class_id,
            "class": (
                {
                    "id": selected_class.id,
                    "name": selected_class.name,
                    "role": selected_class.role,
                    "difficulty": selected_class.difficulty,
                    "summary": selected_class.summary,
                    "tradeoff": selected_class.tradeoff,
                    "training_profile_id": selected_class.training_profile_id,
                    "recommended_package_id": (
                        selected_class.recommended_package_id
                    ),
                    "technique_name": selected_class.technique_name,
                    "technique_summary": selected_class.technique_summary,
                    "technique_kind": selected_class.technique_kind,
                    "passive_name": selected_class.passive_name,
                    "passive_summary": selected_class.passive_summary,
                    "exploration_name": selected_class.exploration_name,
                    "exploration_summary": selected_class.exploration_summary,
                }
                if selected_class is not None
                else None
            ),
            "faction_route": (
                {
                    "id": selected_faction.id,
                    "name": selected_faction.name,
                    "route_label": selected_faction.route_label,
                    "hq_label": selected_faction.hq_label,
                    "candidacy_status": self._candidacy_status(
                        state, selected_faction.id
                    ),
                    "membership_status": "unaffiliated",
                    "rank_status": "none",
                    "guild_eligibility": "locked_until_required_faction_quests",
                    "freeform_guild_path": "locked_until_all_faction_quests_and_special_access_quest",
                }
                if selected_faction is not None
                else None
            ),
            "allocation_mode": build.allocation_mode,
            "budget": self.catalog.creation.budget,
            "spent": spent,
            "remaining": remaining,
            "base_attributes": dict(build.base_attributes),
            "can_confirm": (
                build.status == "pending"
                and build.class_id is not None
                and build.allocation_mode in {"recommended", "manual"}
                and remaining == 0
                and build.tutorial_status != "offered"
            ),
            "attributes": [
                {
                    "id": definition.id,
                    "name": definition.name,
                    "abbreviation": definition.abbreviation,
                    "minimum": definition.minimum,
                    "maximum": definition.maximum,
                    "weight": definition.weight,
                    "summary": definition.summary,
                    "effects": list(definition.effects),
                    "base_value": build.base_attributes.get(definition.id),
                    "effective_value": int(
                        getattr(character, definition.id)
                    ),
                    "effect_projection": stat_effect_projection(
                        character,
                        definition.id,
                    ),
                }
                for definition in self.catalog.creation.attributes.values()
            ],
            "classes": [
                {
                    "id": definition.id,
                    "name": definition.name,
                    "faction_id": definition.faction_id,
                    "faction_name": self.catalog.creation.factions[
                        definition.faction_id
                    ].name,
                    "role": definition.role,
                    "difficulty": definition.difficulty,
                    "summary": definition.summary,
                    "tradeoff": definition.tradeoff,
                    "training_profile_id": definition.training_profile_id,
                    "recommended_package_id": (
                        definition.recommended_package_id
                    ),
                    "recommended_first_character": (
                        definition.id == "soldier"
                    ),
                }
                for definition in self.catalog.creation.classes.values()
            ],
            "packages": [
                {
                    "id": package.id,
                    "name": package.name,
                    "summary": package.summary,
                    "attributes": dict(package.attributes),
                }
                for package in self.catalog.creation.packages.values()
            ],
            "tutorial": {
                "id": self.catalog.creation.tutorial.id,
                "title": self.catalog.creation.tutorial.title,
                "description": self.catalog.creation.tutorial.description,
                "status": build.tutorial_status,
                "step": (
                    {
                        "id": active_tutorial_step.id,
                        "description": active_tutorial_step.description,
                        "why": active_tutorial_step.why,
                        "suggested_command": (
                            active_tutorial_step.suggested_command
                        ),
                        "step_number": (
                            self.catalog.creation.tutorial.steps.index(
                                active_tutorial_step
                            )
                            + 1
                        ),
                        "step_total": len(self.catalog.creation.tutorial.steps),
                    }
                    if active_tutorial_step is not None
                    else None
                ),
            },
        }

    def _directive_projection(
        self,
        state: GameState,
        build_projection: dict[str, object],
    ) -> dict[str, object] | None:
        build = state.character.build
        if build.status == "pending":
            if build.class_id is None:
                return {
                    "kind": "setup",
                    "tag": "FOUNDATION",
                    "title": "Choose how you enter the world",
                    "summary": (
                        "Your class sets a real training profile and a "
                        "provisional story route without joining a faction."
                    ),
                    "why": (
                        "This makes later stat advice and quest direction "
                        "specific to your play style."
                    ),
                    "suggested_command": "build classes",
                }
            if (
                build.allocation_mode not in {"recommended", "manual"}
                or build_projection["remaining"] != 0
            ):
                return {
                    "kind": "setup",
                    "tag": "FOUNDATION",
                    "title": "Shape your starting stats",
                    "summary": (
                        "Use the equal-budget class recommendation or "
                        "manually spend every weighted point."
                    ),
                    "why": (
                        "Each point changes combat, movement, accuracy, or "
                        "carrying capacity before you confirm."
                    ),
                    "suggested_command": (
                        "build auto"
                        if build.allocation_mode is None
                        else "build"
                    ),
                }
            if build.tutorial_status == "offered":
                return {
                    "kind": "setup",
                    "tag": "OPTIONAL",
                    "title": "Choose your guidance level",
                    "summary": (
                        "Guided Start teaches the interface with no reward; "
                        "Explore Freely skips it without penalty."
                    ),
                    "why": (
                        "You can change your guidance choice later with GUIDE."
                    ),
                    "suggested_command": "build tutorial guided",
                }
            return {
                "kind": "setup",
                "tag": "READY",
                "title": "Review and enter the Sprawl",
                "summary": (
                    "Class, equal-budget stats, and guidance preference "
                    "are ready for one atomic confirmation."
                ),
                "why": (
                    "Faction membership remains unassigned until lived "
                    "story contact and an explicit future choice."
                ),
                "suggested_command": "build confirm",
            }
        story = self._story_projection(state)
        if story["active"]:
            progress_index = int(story["progress_index"])
            progress_total = int(story["progress_total"])
            return {
                "kind": "story",
                "tag": str(story["quest_title"]).upper(),
                "title": str(story["directive"]),
                "summary": str(story["objective"]),
                "why": str(story["why"]),
                "suggested_command": story["primary_command"],
                "progress_index": progress_index,
                "progress_total": progress_total,
                "actions": story["actions"],
                "room_hint": story["room_hint"],
                "guide_active": build.tutorial_status == "active",
                "guide": (
                    build_projection["tutorial"]["step"]
                    if build.tutorial_status == "active"
                    else None
                ),
            }
        if build.tutorial_status == "active":
            tutorial = build_projection["tutorial"]
            assert isinstance(tutorial, dict)
            step = tutorial["step"]
            assert isinstance(step, dict)
            return {
                "kind": "tutorial",
                "tag": "GUIDED START",
                "title": str(step["description"]),
                "summary": str(step["why"]),
                "why": (
                    "This guidance is optional, reward-free, and resumable."
                ),
                "suggested_command": str(step["suggested_command"]),
            }
        return None

    def _client_item_projection(
        self, state: GameState, item: ItemState
    ) -> dict[str, object]:
        base = self.catalog.items[item.definition_id]
        effective = self._effective_item_definition(item)
        equipped_slot = next(
            (
                slot
                for slot, equipped_id in state.character.equipped.items()
                if equipped_id == item.instance_id
            ),
            None,
        )
        return {
            "instance_id": item.instance_id,
            "definition_id": item.definition_id,
            "name": base.name,
            "description": base.description,
            "bulk": base.bulk,
            "slot": base.slot,
            "equipped": equipped_slot is not None,
            "equipped_slot": equipped_slot,
            "upgrade_level": item.upgrade_level,
            "attack_bonus": effective.attack_bonus,
            "defense_bonus": effective.defense_bonus,
            "damage": [effective.damage_min, effective.damage_max],
            "roundtime": effective.roundtime,
            "armor": effective.armor,
            "durability": item.durability,
            "max_durability": self._effective_max_durability(item),
            "repair_family": base.repair_family,
            "repair_value": base.repair_value,
            "can_compare": bool(base.slot),
            "can_modify": bool(
                base.slot and base.max_durability > 0 and item.upgrade_level < 3
            ),
        }

    def _specialization_projection(self, state: GameState, now: float) -> dict[str, object] | None:
        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        if class_definition is None:
            return None
        selected = self._selected_specialization(state)
        upgrade = self._selected_specialization_upgrade(state, selected)
        values = (
            self._specialization_values(state, selected)
            if selected is not None
            else None
        )
        follow_up_ready_in_seconds = max(
            0,
            math.ceil(
                state.character.specialization_follow_up_ready_until - now
            ),
        )
        return {
            "point_available": "ability_point_available" in state.flags,
            "selected_id": selected.id if selected is not None else None,
            "selected_name": selected.name if selected is not None else None,
            "selected_summary": selected.summary if selected is not None else None,
            "selected_kind": selected.kind if selected is not None else None,
            "selected_passive": (
                {
                    "name": selected.passive.name,
                    "summary": selected.passive.summary,
                    "kind": selected.passive.kind,
                    "power": selected.passive.power,
                }
                if selected is not None
                else None
            ),
            "selected_follow_up": (
                {
                    "name": selected.follow_up.name,
                    "summary": selected.follow_up.summary,
                    "kind": selected.follow_up.kind,
                    "power": values["follow_up_power"],
                    "ready_in_seconds": follow_up_ready_in_seconds,
                    "roundtime": values["follow_up_roundtime"],
                }
                if selected is not None and values is not None
                else None
            ),
            "counterplay": selected.counterplay if selected is not None else None,
            "ready_in_seconds": max(
                0, math.ceil(state.character.specialization_ready_at - now)
            ),
            "effective_power": values["power"] if values is not None else None,
            "effective_cooldown": values["cooldown"] if values is not None else None,
            "commitment_roundtime": (
                values["commitment_roundtime"] if values is not None else None
            ),
            "mastery_uses": state.character.specialization_uses,
            "mastery_required": (
                selected.mastery_uses_required if selected is not None else None
            ),
            "mastery_ready": bool(
                selected is not None
                and state.character.specialization_uses
                >= selected.mastery_uses_required
                and upgrade is None
            ),
            "selected_upgrade_id": upgrade.id if upgrade is not None else None,
            "selected_upgrade_name": upgrade.name if upgrade is not None else None,
            "upgrade_options": (
                [
                    {
                        "id": option.id,
                        "name": option.name,
                        "summary": option.summary,
                        "command": f"ability upgrade {option.id}",
                    }
                    for option in selected.upgrade_options.values()
                ]
                if selected is not None
                else []
            ),
            "branches": [
                {
                    "id": branch.id,
                    "name": branch.name,
                    "summary": branch.summary,
                    "kind": branch.kind,
                    "power": branch.power,
                    "cooldown": branch.cooldown,
                    "passive_name": branch.passive.name,
                    "passive_summary": branch.passive.summary,
                    "follow_up_name": branch.follow_up.name,
                    "follow_up_summary": branch.follow_up.summary,
                    "counterplay": branch.counterplay,
                    "learned": selected is not None and selected.id == branch.id,
                    "learn_command": f"ability learn {branch.id}",
                }
                for branch in class_definition.ability_branches.values()
            ],
        }

    def _economy_projection(self, state: GameState) -> dict[str, object]:
        vendors = self._vendors_here(state)
        vendor = vendors[0] if vendors else None
        room = self.catalog.rooms[state.character.room_id]
        counts = self._story_inventory_counts(state)
        recipes = []
        for recipe in self.catalog.economy.recipes.values():
            missing = {
                item_id: max(0, count - counts.get(item_id, 0))
                for item_id, count in recipe.inputs.items()
                if counts.get(item_id, 0) < count
            }
            recipes.append(
                {
                    "id": recipe.id,
                    "name": recipe.name,
                    "facility": recipe.facility,
                    "facility_present": recipe.facility in room.facilities,
                    "credit_cost": recipe.credit_cost,
                    "inputs": [
                        {
                            "item_id": item_id,
                            "name": self.catalog.items[item_id].name,
                            "count": count,
                            "carried": counts.get(item_id, 0),
                        }
                        for item_id, count in recipe.inputs.items()
                    ],
                    "outputs": [
                        {
                            "item_id": item_id,
                            "name": self.catalog.items[item_id].name,
                            "count": count,
                        }
                        for item_id, count in recipe.outputs.items()
                    ],
                    "missing": missing,
                    "affordable": state.character.credits >= recipe.credit_cost,
                    "available": (
                        recipe.facility in room.facilities
                        and not missing
                        and state.character.credits >= recipe.credit_cost
                    ),
                    "command": f"craft {recipe.id}",
                }
            )
        active, companion_progress = self._active_companion_context(
            state, self.clock.now()
        )
        sync_projection = self._companion_sync_projection(
            state, self.clock.now()
        )
        order_summaries = {
            "balanced": (
                "Measured setup, occasional interruption, and player-owned finishes."
            ),
            "guard": (
                "No direct strike; adds guard, creates an opening, and prioritizes interception."
            ),
            "assault": (
                "Strongest damage and no interception; explicit Assault authority may let Sol finish."
            ),
        }
        return {
            "credits": state.character.credits,
            "vendor": (
                {
                    "id": vendor.id,
                    "name": vendor.name,
                    "sell_rate_percent": vendor.sell_rate_percent,
                    "items": [
                        {
                            "item_id": item_id,
                            "name": self.catalog.items[item_id].name,
                            "price": price,
                            "affordable": state.character.credits >= price,
                            "command": f"market buy {item_id}",
                        }
                        for item_id, price in vendor.inventory.items()
                    ],
                }
                if vendor is not None
                else None
            ),
            "recipes": recipes,
            "companion": (
                {
                    "id": active.id,
                    "name": active.name,
                    "role": active.role,
                    "summary": active.summary,
                    "assist_kind": active.assist_kind,
                    "power": active.power,
                    "story_bound": active.story_bound,
                    "dismissible": active.dismissible,
                    "level": companion_progress.level if companion_progress is not None else 1,
                    "experience": companion_progress.experience if companion_progress is not None else 0,
                    "health": companion_progress.health if companion_progress is not None else 0,
                    "max_health": companion_progress.max_health if companion_progress is not None else 1,
                    "order": companion_progress.order if companion_progress is not None else "balanced",
                    "order_summary": (
                        order_summaries.get(companion_progress.order, "")
                        if companion_progress is not None
                        else order_summaries["balanced"]
                    ),
                    "downed_seconds": (
                        max(0, math.ceil(companion_progress.downed_until - self.clock.now()))
                        if companion_progress is not None
                        else 0
                    ),
                    "defeated_targets": (
                        companion_progress.defeated_targets
                        if companion_progress is not None
                        else 0
                    ),
                    "setup_actions": companion_progress.setup_actions,
                    "finish_reservations": companion_progress.finish_reservations,
                    "player_enabled_finishes": (
                        companion_progress.player_enabled_finishes
                    ),
                    "finishing_strikes": companion_progress.finishing_strikes,
                    "damage_dealt": companion_progress.damage_dealt,
                    "damage_intercepted": companion_progress.damage_intercepted,
                    "agency_rule": (
                        "Balanced and Guard preserve player-owned finishes. Only an explicit "
                        "Assault order authorizes Sol to take an occasional finishing strike."
                    ),
                    "order_commands": [
                        {"id": order, "command": f"companion order {order}"}
                        for order in ("balanced", "guard", "assault")
                    ] if active.assist_kind == "partner" else [],
                    "sync_unlocked": bool(sync_projection["unlocked"]),
                    "sync_available": bool(sync_projection["available"]),
                    "sync_reason": sync_projection["reason"],
                    "sync_target_id": sync_projection["target_id"],
                    "sync_target_name": sync_projection["target_name"],
                    "sync_command": sync_projection["command"],
                    "sync_summary": sync_projection["summary"],
                    "dismiss_command": "companion dismiss" if active.dismissible else None,
                }
                if active is not None and companion_progress is not None
                else None
            ),
            "mercenaries": [
                {
                    "id": merc.id,
                    "name": merc.name,
                    "role": merc.role,
                    "summary": merc.summary,
                    "cost": merc.cost,
                    "hire_room_id": merc.hire_room_id,
                    "hire_here": state.character.room_id == merc.hire_room_id,
                    "affordable": state.character.credits >= merc.cost,
                    "active": active is not None and active.id == merc.id,
                    "command": f"companion hire {merc.id}",
                }
                for merc in self.catalog.economy.mercenaries.values()
                if not merc.hidden_from_hire
            ],
        }

    def _beginner_calibration_projection(self, state: GameState) -> dict[str, object]:
        telemetry = state.beginner_telemetry

        # Route/story friction and combat repetition are intentionally separate.
        # A long fight with successful damage is progress, not a navigation stall.
        route_score = (
            telemetry.friction_since_progress * 2
            + max(0, telemetry.commands_since_progress - 8)
        )
        if state.incapacitation is not None:
            route_status = "RED"
            route_summary = (
                "The character is incapacitated. Recovery remains available and no "
                "campaign route has been lost."
            )
        elif telemetry.commands_since_progress >= 15 or route_score >= 12:
            route_status = "RED"
            route_summary = (
                "A route or command stall is visible. Ask Sol for one optional next "
                "action or review the active directive."
            )
        elif telemetry.commands_since_progress >= 8 or route_score >= 5:
            route_status = "YELLOW"
            route_summary = (
                "Some route friction is accumulating. The route remains open and "
                "advice is optional."
            )
        else:
            route_status = "GREEN"
            route_summary = (
                "The current route is advancing without a sustained story or command stall."
            )

        if telemetry.current_combat_repetition >= 8:
            combat_status = "RED"
            combat_summary = (
                "Combat is repeating without damage, a phase change, recovery, setup, "
                "or successful withdrawal. Change stance, defense, target, technique, "
                "or retreat plan."
            )
        elif telemetry.current_combat_repetition >= 4:
            combat_status = "YELLOW"
            combat_summary = (
                "Several combat commands have repeated without a measurable result. "
                "A tactical change may help."
            )
        elif telemetry.current_combat_sequence > 0:
            combat_status = "GREEN"
            combat_summary = (
                "Combat is active and successful damage, setup, protection, healing, "
                "phase changes, and withdrawals count as progress."
            )
        else:
            combat_status = "GREEN"
            combat_summary = "No unresolved combat repetition signal is active."

        severity = {"GREEN": 0, "YELLOW": 1, "RED": 2}
        status = max(
            (route_status, combat_status),
            key=lambda item: severity[item],
        )
        if severity[combat_status] > severity[route_status]:
            summary = combat_summary
        elif severity[route_status] > severity[combat_status]:
            summary = route_summary
        elif status == "GREEN" and telemetry.current_combat_sequence > 0:
            summary = combat_summary
        else:
            summary = route_summary

        return {
            "status": status,
            "summary": summary,
            "route_status": route_status,
            "route_summary": route_summary,
            "combat_status": combat_status,
            "combat_summary": combat_summary,
            "total_commands": telemetry.total_commands,
            "changed_commands": telemetry.changed_commands,
            "parse_errors": telemetry.parse_errors,
            "blocked_commands": telemetry.blocked_commands,
            "incapacitations": telemetry.incapacitations,
            "recoveries": telemetry.recoveries,
            "hints_requested": telemetry.hints_requested,
            "commands_since_progress": telemetry.commands_since_progress,
            "longest_stall": telemetry.longest_stall,
            "friction_since_progress": telemetry.friction_since_progress,
            "combat_progress_events": telemetry.combat_progress_events,
            "combat_repetition_commands": telemetry.combat_repetition_commands,
            "current_combat_repetition": telemetry.current_combat_repetition,
            "longest_combat_repetition": telemetry.longest_combat_repetition,
            "current_combat_sequence": telemetry.current_combat_sequence,
            "longest_combat_sequence": telemetry.longest_combat_sequence,
            "successful_withdrawals": telemetry.successful_withdrawals,
            "failed_withdrawals": telemetry.failed_withdrawals,
            "companion_setups": telemetry.companion_setups,
            "companion_finish_reservations": telemetry.companion_finish_reservations,
            "assist_prompts": telemetry.assist_prompts,
            "brief_revisit_descriptions": telemetry.brief_revisit_descriptions,
            "last_progress_label": telemetry.last_progress_label,
            "first_friction_command": telemetry.first_friction_command,
            "last_friction_command": telemetry.last_friction_command,
            "chapter_commands": dict(sorted(telemetry.chapter_commands.items())),
            "room_entries": dict(sorted(telemetry.room_entries.items())),
            "advice_command": "companion advise",
            "local_only": True,
            "reward_neutral": True,
        }

    def _beginner_experience_projection(self, state: GameState) -> dict[str, object]:
        definition = self.catalog.beginner_experience
        completed_quests = state.story.completed_quests
        chapters: list[dict[str, object]] = []
        completed_minutes = 0
        active_chapter_id: str | None = None
        for chapter in definition.chapters:
            completed_count = sum(
                quest_id in completed_quests for quest_id in chapter.quest_ids
            )
            complete = completed_count == len(chapter.quest_ids)
            active = state.story.active_quest_id in chapter.quest_ids
            if complete:
                earned_minutes = chapter.minutes
            elif active or completed_count:
                earned_minutes = round(
                    chapter.minutes * completed_count / max(1, len(chapter.quest_ids))
                )
            else:
                earned_minutes = 0
            completed_minutes += earned_minutes
            if active:
                active_chapter_id = chapter.id
            chapters.append(
                {
                    "id": chapter.id,
                    "title": chapter.title,
                    "summary": chapter.summary,
                    "minutes": chapter.minutes,
                    "earned_minutes": earned_minutes,
                    "quest_count": len(chapter.quest_ids),
                    "completed_quests": completed_count,
                    "complete": complete,
                    "active": active,
                }
            )
        competencies: list[dict[str, object]] = []
        for competency in definition.competencies:
            quests_complete = all(
                quest_id in completed_quests
                for quest_id in competency.required_quests
            )
            flags_complete = all(
                flag in state.flags for flag in competency.required_flags
            )
            complete = quests_complete and flags_complete
            competencies.append(
                {
                    "id": competency.id,
                    "label": competency.label,
                    "description": competency.description,
                    "complete": complete,
                }
            )
        completed_competencies = sum(
            bool(item["complete"]) for item in competencies
        )
        estimated_minutes = min(definition.target_minutes, completed_minutes)
        campaign_percent = round(
            100 * estimated_minutes / max(1, definition.target_minutes)
        )
        competency_percent = round(
            100 * completed_competencies / max(1, len(competencies))
        )
        class_id = state.character.build.class_id
        assignment = definition.class_assignments.get(class_id or "")
        competencies_ready = completed_competencies == len(competencies)
        ready_for_capstone = (
            state.character.level >= definition.target_level
            and competencies_ready
            and (
                "last_patrol" in completed_quests
                or "price_of_second_life" in completed_quests
            )
        )
        complete = (
            "price_of_second_life" in completed_quests
            and state.character.level >= definition.target_level
        )
        return {
            "id": definition.id,
            "title": definition.title,
            "summary": definition.summary,
            "target_minutes": definition.target_minutes,
            "estimated_completed_minutes": estimated_minutes,
            "target_level": definition.target_level,
            "current_level": state.character.level,
            "level_ready": state.character.level >= definition.target_level,
            "ready_for_capstone": ready_for_capstone,
            "starter_room_count": len(definition.starter_room_ids),
            "starter_rooms_discovered": sum(
                room_id in state.visited_rooms
                for room_id in definition.starter_room_ids
            ),
            "chapters": chapters,
            "active_chapter_id": active_chapter_id,
            "competencies": competencies,
            "completed_competencies": completed_competencies,
            "competency_total": len(competencies),
            # ``percent`` remains the backward-compatible HUD field, but now
            # represents the whole modeled campaign instead of only competencies.
            "percent": campaign_percent,
            "campaign_percent": campaign_percent,
            "competency_percent": competency_percent,
            "capstone_instinct_rehearsed": (
                "capstone_instinct_rehearsed" in state.flags
            ),
            "difficulty": self._beginner_difficulty_projection(state),
            "calibration": self._beginner_calibration_projection(state),
            "resume_briefing": self._resume_briefing_projection(state),
            "class_assignment": (
                {
                    "class_id": assignment.class_id,
                    "title": assignment.title,
                    "objective": assignment.objective,
                    "practice_command": assignment.practice_command,
                    "complete": "class_field_assignment" in completed_quests,
                }
                if assignment is not None
                else None
            ),
            "complete": complete,
        }

    def _journeyman_experience_projection(self, state: GameState) -> dict[str, object]:
        """Project the authored level 11-20 campaign without changing save shape.

        The HUD consumes the same stable presentation contract used by the
        foundation card.  The phase remains explicitly separate so a completed
        level 1-10 campaign is never relabeled as incomplete when the next arc
        begins.
        """

        definition = self.catalog.journeyman_experience
        completed_quests = state.story.completed_quests
        phase_quest_ids = {
            quest_id
            for chapter in definition.chapters
            for quest_id in chapter.quest_ids
        }
        started = self._journeyman_started(state)
        chapters: list[dict[str, object]] = []
        completed_minutes = 0
        active_chapter_id: str | None = None
        for chapter in definition.chapters:
            completed_count = sum(
                quest_id in completed_quests for quest_id in chapter.quest_ids
            )
            complete = completed_count == len(chapter.quest_ids)
            active = state.story.active_quest_id in chapter.quest_ids
            if complete:
                earned_minutes = chapter.minutes
            elif active or completed_count:
                earned_minutes = round(
                    chapter.minutes
                    * completed_count
                    / max(1, len(chapter.quest_ids))
                )
            else:
                earned_minutes = 0
            completed_minutes += earned_minutes
            if active:
                active_chapter_id = chapter.id
            chapters.append(
                {
                    "id": chapter.id,
                    "title": chapter.title,
                    "summary": chapter.summary,
                    "minutes": chapter.minutes,
                    "earned_minutes": earned_minutes,
                    "quest_count": len(chapter.quest_ids),
                    "completed_quests": completed_count,
                    "complete": complete,
                    "active": active,
                }
            )

        competencies: list[dict[str, object]] = []
        for competency in definition.competencies:
            quests_complete = all(
                quest_id in completed_quests
                for quest_id in competency.required_quests
            )
            flags_complete = all(
                flag in state.flags for flag in competency.required_flags
            )
            complete = quests_complete and flags_complete
            competencies.append(
                {
                    "id": competency.id,
                    "label": competency.label,
                    "description": competency.description,
                    "complete": complete,
                }
            )
        completed_competencies = sum(
            bool(item["complete"]) for item in competencies
        )
        estimated_minutes = min(definition.target_minutes, completed_minutes)
        campaign_percent = round(
            100 * estimated_minutes / max(1, definition.target_minutes)
        )
        competency_percent = round(
            100 * completed_competencies / max(1, len(competencies))
        )
        class_id = state.character.build.class_id
        assignment = definition.class_assignments.get(class_id or "")
        readiness_ids = {
            competency.id
            for competency in definition.competencies
            if competency.id != "horizon_readiness"
        }
        ready_competencies = all(
            bool(item["complete"])
            for item in competencies
            if item["id"] in readiness_ids
        )
        ready_for_capstone = bool(
            state.character.level >= definition.target_level - 1
            and ready_competencies
            and (
                state.story.active_quest_id == "the_second_horizon"
                or "the_second_horizon" in completed_quests
            )
        )
        complete = bool(
            "the_second_horizon" in completed_quests
            and state.character.level >= definition.target_level
        )
        return {
            "id": definition.id,
            "title": definition.title,
            "summary": definition.summary,
            "started": started,
            "active": started and not complete,
            "phase_quest_count": len(phase_quest_ids),
            "target_minutes": definition.target_minutes,
            "estimated_completed_minutes": estimated_minutes,
            "target_level": definition.target_level,
            "current_level": state.character.level,
            "level_ready": state.character.level >= definition.target_level,
            "ready_for_capstone": ready_for_capstone,
            "starter_room_count": len(definition.starter_room_ids),
            "starter_rooms_discovered": sum(
                room_id in state.visited_rooms
                for room_id in definition.starter_room_ids
            ),
            "chapters": chapters,
            "active_chapter_id": active_chapter_id,
            "competencies": competencies,
            "completed_competencies": completed_competencies,
            "competency_total": len(competencies),
            "percent": campaign_percent,
            "campaign_percent": campaign_percent,
            "competency_percent": competency_percent,
            "capstone_instinct_rehearsed": (
                "five_anchors_complete" in state.flags
            ),
            "difficulty": self._beginner_difficulty_projection(state),
            "calibration": self._beginner_calibration_projection(state),
            "resume_briefing": self._resume_briefing_projection(state),
            "class_assignment": (
                {
                    "class_id": assignment.class_id,
                    "title": assignment.title,
                    "objective": assignment.objective,
                    "practice_command": assignment.practice_command,
                    "complete": "five_anchors_complete" in state.flags,
                }
                if assignment is not None
                else None
            ),
            "complete": complete,
        }

    def _creature_battlefield_projection(
        self,
        state: GameState,
        creature: CreatureState,
    ) -> dict[str, object]:
        definition = self.catalog.creatures[creature.definition_id]
        actor = state.battle.actors.get(creature_actor_id(creature.instance_id))
        active = bool(
            actor is not None
            and state.battle.room_id == state.character.room_id
            and state.battle.encounter is not None
        )
        effects = []
        if active and actor is not None:
            effects = [
                {
                    "name": name,
                    "magnitude": effect.magnitude,
                    "remaining_field_seconds": max(
                        0.0, effect.expires_at - state.battle.time
                    ),
                    "uses_remaining": effect.uses_remaining,
                }
                for name, effect in sorted(
                    state.battle.effects.get(actor.actor_id, {}).items()
                )
                if effect.expires_at > state.battle.time
            ]
        return {
            "active": active,
            "behavior_profile": definition.behavior_profile,
            "base_action_interval": definition.action_interval,
            "intent": actor.current_intent if active and actor is not None else None,
            "target_id": actor.target_id if active and actor is not None else None,
            "ready_in_field_seconds": (
                max(0.0, actor.next_action_at - state.battle.time)
                if active and actor is not None
                else None
            ),
            "timing_text": (
                timing_description(
                    max(0.0, actor.next_action_at - state.battle.time),
                    state.character.perception,
                )
                if active and actor is not None
                else "not committed"
            ),
            "effects": effects,
        }

    def client_state(self, state: GameState, *, now: float | None = None) -> dict[str, object]:
        """Return a stable, transport-neutral state projection for first-party clients."""

        observed_at = self.clock.now() if now is None else now
        room = self.catalog.rooms[state.character.room_id]
        remaining = self._hard_recovery_remaining(state, observed_at)
        roundtime = roundtime_remaining(
            state.character.roundtime_until, observed_at
        )
        stun = roundtime_remaining(state.character.stunned_until, observed_at)
        queued = state.queued_action
        live_creatures = self._live_creatures(state)
        load = calculate_encumbrance(state.character, self.catalog.items)
        incapacitation = state.incapacitation
        course_progress = state.character.course
        active_course = (
            self.catalog.courses[course_progress.active_course_id]
            if course_progress.active_course_id is not None
            else None
        )
        build_projection = self._build_projection(state)
        story_projection = self._story_projection(state)
        return {
            "schema": "beta-earth-client-state-v1",
            "content_version": state.content_version,
            "revision": state.revision,
            "turn": state.turn,
            "directive": self._directive_projection(
                state,
                build_projection,
            ),
            "story": story_projection,
            "party": self._party_projection(state),
            "foundation": self._foundation_projection(state),
            "report": self._report_projection(state),
            "district": self._district_projection(state),
            "service": self._service_projection(state),
            "hospice": self._hospice_projection(state),
            "appeal": self._appeal_projection(state),
            "wayfinding": self._wayfinding_projection(state),
            "beginner_experience": self._beginner_experience_projection(state),
            "journeyman_experience": self._journeyman_experience_projection(state),
            "difficulty_curve": self._beginner_difficulty_projection(state),
            "playtest": self._playtest_projection(state, now=observed_at),
            "withdrawal": self._withdrawal_projection(state, observed_at),
            "battlefield": self.combat_scheduler.projection(
                state, observed_at
            ),
            "guidance": build_projection["tutorial"],
            "character": {
                "name": state.character.name,
                "level": state.character.level,
                "health": state.character.health,
                "max_health": state.character.max_health,
                "stance": state.character.stance.value,
                "defense_mode": state.character.defense_mode.value,
                "guard_points": state.character.guard_points,
                "credits": state.character.credits,
                "specialization": self._specialization_projection(
                    state, observed_at
                ),
                "companion_id": state.character.companion_id,
                "technique": (
                    {
                        "name": build_projection["class"]["technique_name"],
                        "summary": build_projection["class"]["technique_summary"],
                        "kind": build_projection["class"]["technique_kind"],
                        "passive_name": build_projection["class"]["passive_name"],
                        "passive_summary": build_projection["class"]["passive_summary"],
                        "exploration_name": build_projection["class"]["exploration_name"],
                        "exploration_summary": build_projection["class"]["exploration_summary"],
                        "unlocked": "signature_instinct_claimed" in state.flags,
                        "ready_in_seconds": max(
                            0,
                            math.ceil(
                                state.character.technique_ready_at - observed_at
                            ),
                        ),
                    }
                    if build_projection["class"] is not None
                    else None
                ),
                "attributes": {
                    "strength": state.character.strength,
                    "agility": state.character.agility,
                    "perception": state.character.perception,
                    "combat_skill": state.character.combat_skill,
                },
                "build": build_projection,
                "field_insight": state.character.experience.field_pool,
                "absorbed_insight": state.character.experience.absorbed,
                "level_progress": {
                    "learned_in_level": (
                        state.character.experience.absorbed
                        % INSIGHT_PER_LEVEL
                    ),
                    "required_per_level": INSIGHT_PER_LEVEL,
                    "remaining": (
                        INSIGHT_PER_LEVEL
                        - (
                            state.character.experience.absorbed
                            % INSIGHT_PER_LEVEL
                        )
                    ),
                    "awaiting_absorption": (
                        state.character.experience.field_pool
                    ),
                },
                "bleeding_rate": active_bleeding(state.character),
                "wounds": [
                    wound.to_dict() for wound in state.character.wounds
                ],
                "disabled_limbs": list(disabled_limbs(state.character)),
                "encumbrance": {
                    "bulk": load.carried_bulk,
                    "comfortable_limit": load.comfortable_limit,
                    "hard_limit": load.hard_limit,
                    "tier": load.tier,
                    "recovery_penalty": load.recovery_penalty,
                },
                "inventory": [
                    self._client_item_projection(state, item)
                    for item in state.character.inventory
                ],
                "prone": state.character.prone,
                "resting": state.character.resting,
                "stunned_seconds": stun,
                    "training": {
                    "physical_points": state.character.training.physical_points,
                    "mental_points": state.character.training.mental_points,
                    "ranks": dict(state.character.training.ranks),
                    "early_refunds_remaining": (
                        state.character.training.early_refunds_remaining
                    ),
                    "early_refund_level_limit": (
                        self.catalog.progression.early_refund_level_limit
                    ),
                    "last_awarded_milestone": (
                        state.character.training.last_awarded_milestone
                    ),
                    "profile_id": state.character.training.profile_id,
                    "profile_name": self.catalog.progression.profiles[
                        state.character.training.profile_id
                    ].name,
                    "profile_locked": state.character.training.profile_locked,
                    "profile_changes_remaining": (
                        state.character.training.profile_changes_remaining
                    ),
                    "options": [
                        self._training_option_projection(state, option)
                        for option in self.catalog.progression.options.values()
                        ],
                    },
                    "course": {
                        "active_course_id": course_progress.active_course_id,
                        "step_index": course_progress.step_index,
                        "next_step": (
                            {
                                "id": active_course.steps[
                                    course_progress.step_index
                                ].id,
                                "description": active_course.steps[
                                    course_progress.step_index
                                ].description,
                                "number": course_progress.step_index + 1,
                                "total": len(active_course.steps),
                            }
                            if active_course is not None
                            else None
                        ),
                        "completed_courses": sorted(
                            course_progress.completed_courses
                        ),
                        "catalog": [
                            {
                                "id": course.id,
                                "name": course.name,
                                "description": course.description,
                                "completed": (
                                    course.id
                                    in course_progress.completed_courses
                                ),
                                "reward_points": dict(course.reward_points),
                            }
                            for course in self.catalog.courses.values()
                        ],
                    },
                },
            "room": {
                "id": room.id,
                "title": room.title,
                "world_body": room.world_body,
                "layer": room.layer,
                "description": self._room_description(state),
                "exits": [
                    direction
                    for direction in room.exits
                    if self._exit_is_available(state, room.id, direction)
                ],
                "exit_details": [
                    {
                        "direction": direction,
                        "destination_id": destination,
                        "visited": destination in state.visited_rooms,
                        "locked": not self._exit_is_available(state, room.id, direction),
                        "lock_reason": self._exit_lock_reason(state, direction),
                        "required_flags": list(room.exit_requirements.get(direction, ())),
                        "destination_title": (
                            self.catalog.rooms[destination].title
                            if destination in state.visited_rooms
                            else None
                        ),
                    }
                    for direction, destination in room.exits.items()
                ],
                "facilities": list(room.facilities),
                "hazard": (
                    {
                        "name": room.hazard_name,
                        "text": room.hazard_text,
                        "damage": room.hazard_damage,
                        "roundtime": room.hazard_roundtime,
                        "mitigation_items": [
                            {"id": item_id, "name": self.catalog.items[item_id].name}
                            for item_id in room.hazard_mitigation_items
                        ],
                        "mitigation_classes": [
                            {"id": class_id, "name": self.catalog.creation.classes[class_id].name}
                            for class_id in room.hazard_mitigation_classes
                        ],
                    }
                    if room.hazard_name is not None
                    else None
                ),
                "world_cycle": {
                    "phase": self._world_cycle_phase(state),
                    "turns_until_next": 1,
                },
                "inspectables": sorted(room.details),
                "items": [
                    {
                        "instance_id": item.instance_id,
                        "definition_id": item.definition_id,
                        "name": self.catalog.items[item.definition_id].name,
                    }
                    for item in state.room_items.get(room.id, [])
                ],
                "npcs": [
                    {
                        "id": npc.id,
                        "name": npc.name,
                        "description": npc.description,
                        "relationship_label": npc.relationship_label,
                        "relationship_score": state.story.relationships.get(
                            npc.id,
                            0,
                        ),
                        "relationship_standing": self._relationship_descriptor(
                            state.story.relationships.get(npc.id, 0)
                        ),
                        "command": f"talk {npc.id}",
                        "scheduled_phase": self._world_cycle_phase(state),
                        "scheduled_room_id": self._effective_npc_room(state, npc),
                    }
                    for npc in self._story_npcs_in_room(state)
                ],
                "creatures": [
                    {
                        "instance_id": creature.instance_id,
                        "definition_id": creature.definition_id,
                        "name": self.catalog.creatures[creature.definition_id].name,
                        "health": creature.health,
                        "max_health": self.catalog.creatures[
                            creature.definition_id
                        ].max_health,
                        "combat_role": self.catalog.creatures[
                            creature.definition_id
                        ].combat_role,
                        "support_power": self.catalog.creatures[
                            creature.definition_id
                        ].support_power,
                        "behavior_profile": self.catalog.creatures[
                            creature.definition_id
                        ].behavior_profile,
                        "action_interval": self.catalog.creatures[
                            creature.definition_id
                        ].action_interval,
                        "battlefield": self._creature_battlefield_projection(
                            state, creature
                        ),
                        "phase": creature.phase,
                        "phase_count": (
                            3 if creature.definition_id == "sol_confrontation" else 1
                        ),
                        "exchange_count": creature.exchange_count,
                        "phase_label": (
                            {
                                1: "Foundation read",
                                2: "Akari-line pressure",
                                3: "Control-lattice break",
                            }.get(creature.phase, f"Phase {creature.phase}")
                            if creature.definition_id == "sol_confrontation"
                            else None
                        ),
                    }
                    for creature in live_creatures
                ],
            },
            "economy": self._economy_projection(state),
            "navigation": {
                "visited_count": len(state.visited_rooms),
                "total_rooms": len(self.catalog.rooms),
                "visited_rooms": [
                    {
                        "id": room_id,
                        "title": self.catalog.rooms[room_id].title,
                    }
                    for room_id in sorted(
                        state.visited_rooms,
                        key=lambda value: self.catalog.rooms[value].title,
                    )
                ],
                "connections": [
                    {
                        "from_id": room_id,
                        "to_id": destination,
                        "direction": direction,
                    }
                    for room_id in sorted(state.visited_rooms)
                    for direction, destination in self._available_exits(state, room_id)
                    if destination in state.visited_rooms
                ],
            },
            "journal": self._journal_projection(state),
            "recovery": {
                "class": (
                    "incapacitated"
                    if incapacitation is not None
                    else "hard"
                    if remaining
                    else "ready"
                ),
                "remaining_seconds": remaining,
                "roundtime_seconds": roundtime,
                "stun_seconds": stun,
            },
            "incapacitation": (
                {
                    "origin_room_id": incapacitation.origin_room_id,
                    "recover_in_seconds": max(
                        0,
                        math.ceil(incapacitation.recover_at - observed_at),
                    ),
                    "cause": incapacitation.cause,
                    "help_requested": incapacitation.help_requested,
                }
                if incapacitation is not None
                else None
            ),
            "target_id": state.target_id,
            "queued_action": (
                {
                    "command": queued.intent.command,
                    "args": list(queued.intent.args),
                    "eligible_in_seconds": max(
                        0,
                        math.ceil(queued.execute_at - observed_at),
                    ),
                }
                if queued
                else None
            ),
            "context_commands": self._contextual_commands(state, remaining),
            "context_actions": self._contextual_actions(state, remaining),
        }

    def _state(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        return _HandlerResult(
            (
                json.dumps(
                    self.client_state(state, now=now),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )

    def _contextual_actions(
        self, state: GameState, remaining: int | None = None
    ) -> list[dict[str, str]]:
        """Return bounded exact commands with plain-language reasons."""

        actions: list[dict[str, str]] = []
        seen: set[str] = set()

        def add(command: str, reason: str) -> None:
            normalized = " ".join(command.strip().split()).upper()
            if not normalized or normalized.casefold() in seen:
                return
            seen.add(normalized.casefold())
            actions.append({"command": normalized, "reason": reason})

        if state.character.build.status == "pending":
            add("BUILD", "review the character foundation still awaiting confirmation")
            add("NEXT", "show the next exact setup step without changing anything")
            add("LOOK", "review the current setup space")
            add("HELP HERE", "list exact setup commands")
            return actions

        recovery = (
            self._hard_recovery_remaining(state, self.clock.now())
            if remaining is None
            else remaining
        )
        if state.incapacitation is not None:
            add("HEALTH", "review the current injuries")
            add("SIGNAL", "record a local help request")
            add("RECOVER", "recover when the displayed timer permits it")
            add("NEXT", "review the preserved objective")
            add("LOOK", "review the current recovery location")
            return actions

        route = self._objective_route_projection(state)
        next_command = str(route.get("next_command") or "").strip()
        if next_command:
            add(next_command, str(route.get("summary") or "advance the active objective"))
        add("NEXT", "show one exact objective step without executing it")

        room = self.catalog.rooms[state.character.room_id]
        for npc in self._story_npcs_in_room(state)[:3]:
            add(f"TALK {npc.nouns[0]}", f"speak with {npc.name}")

        context = self._active_story_context(state)
        if context is not None:
            _quest, stage = context
            for action in stage.actions:
                available, _reason = self._story_action_availability(state, action)
                if available:
                    _label, summary, _result = self._story_action_label(state, action)
                    add(self._story_action_command(action), summary)

        live = self._live_creatures(state)
        if live:
            noun = self.catalog.creatures[live[0].definition_id].nouns[0]
            add(f"ASSESS {noun}", "compare the active opponent with your current readiness")
            add("WITHDRAW STATUS", "inspect exact escape pressure and Sol cover")
            if not recovery:
                add(f"ATTACK {noun}", "strike the active opponent")
        elif state.room_items.get(room.id) and not recovery:
            for item in state.room_items[room.id][:2]:
                definition = self.catalog.items[item.definition_id]
                add(f"GET {definition.nouns[0]}", f"pick up {definition.name}")

        if active_bleeding(state.character) and not recovery:
            add("STABILIZE", "slow active bleeding")
        if state.character.prone:
            add("STAND", "regain your feet")
        if room.search is not None and not recovery:
            add("SEARCH", "investigate this location")
        if "repair_bench" in room.facilities and not recovery:
            damaged = next(
                (
                    item
                    for item in state.character.inventory
                    if item.durability is not None
                    and item.durability < self.catalog.items[item.definition_id].max_durability
                ),
                None,
            )
            if damaged is not None:
                definition = self.catalog.items[damaged.definition_id]
                add(f"REPAIR {definition.nouns[0]}", f"repair {definition.name} at this bench")
        if "training_station" in room.facilities and not recovery:
            add("PLAN", "preview training costs and affordability")
            add("TRAIN", "review trainable disciplines at this station")
        if self._vendors_here(state) and not recovery:
            add("MARKET", "review the local vendor inventory")
        available_recipe = next(
            (recipe for recipe in self.catalog.economy.recipes.values() if recipe.facility in room.facilities),
            None,
        )
        if available_recipe is not None and not recovery:
            add(f"CRAFT {available_recipe.id}", "review or create an available local recipe")

        for direction, destination in self._available_exits(state)[:4]:
            add(
                f"GO {direction}",
                f"move toward {self.catalog.rooms[destination].title}",
            )
        add("LOOK", "show the complete authored location description")
        add("BRIEFING", "review the full objective, route, checkpoint, and Sol status")
        return actions[:12]

    def _contextual_commands(
        self, state: GameState, remaining: int | None = None
    ) -> list[str]:
        if state.character.build.status == "pending":
            return ["build", "look", "help", "save"]
        if state.incapacitation is not None:
            suggestions = [
                "look",
                "health",
                "route",
                "journal",
                "plan",
                "signal",
                "recover",
                "help",
            ]
            return list(dict.fromkeys(suggestions))
        room = self.catalog.rooms[state.character.room_id]
        recovery = (
            self._hard_recovery_remaining(state, self.clock.now())
            if remaining is None
            else remaining
        )
        suggestions = [
            "next", "look", "exits", "briefing", "route", "journal", "quest",
            "sovereignty", "faction", "territory status", "plan", "ability"
        ]
        if self._story_npcs_in_room(state):
            suggestions.append("talk")
        story_context = self._active_story_context(state)
        if story_context is not None:
            _, story_stage = story_context
            if any(action.verb == "choose" for action in story_stage.actions):
                suggestions.append("choose")
            if (
                any(action.verb == "interact" for action in story_stage.actions)
                and not recovery
            ):
                suggestions.append("interact")
            if any(action.verb == "party" for action in story_stage.actions):
                suggestions.append("party")
            if any(action.verb == "report" for action in story_stage.actions):
                suggestions.append("report")
            if any(action.verb == "district" for action in story_stage.actions):
                suggestions.append("district")
            if any(action.verb == "service" for action in story_stage.actions):
                suggestions.append("service")
            if any(action.verb == "hospice" for action in story_stage.actions):
                suggestions.append("hospice")
            if any(action.verb == "appeal" for action in story_stage.actions):
                suggestions.append("appeal")
            if any(action.verb == "wayfinding" for action in story_stage.actions):
                suggestions.append("wayfinding")
        if (
            state.character.course.active_course_id is not None
            or any(
                course.start_room == room.id
                and course.id
                not in state.character.course.completed_courses
                for course in self.catalog.courses.values()
            )
        ):
            suggestions.append("course")
        if state.character.prone:
            suggestions.append("stand")
        if state.room_items.get(room.id) and not recovery:
            suggestions.append("get")
        if self._live_creatures(state):
            suggestions.append("target")
            suggestions.append("assess")
            suggestions.append("withdraw status")
            if not recovery:
                suggestions.append("withdraw")
            if not recovery:
                suggestions.append("attack")
        if active_bleeding(state.character) and not recovery:
            suggestions.append("stabilize")
        if (
            state.character.health < state.character.max_health
            and not active_bleeding(state.character)
            and not self._live_creatures(state)
            and not recovery
        ):
            suggestions.append("rest")
        if room.search is not None and not recovery:
            suggestions.append("search")
        if (
            "repair_bench" in room.facilities
            and any(
                item.durability is not None
                and item.durability
                < self.catalog.items[item.definition_id].max_durability
                for item in state.character.inventory
            )
            and not recovery
        ):
            suggestions.append("repair")
        if "training_station" in room.facilities and not recovery:
            if (
                not state.character.training.profile_locked
                and state.character.training.profile_changes_remaining
            ):
                suggestions.append("path")
            suggestions.append("train")
            if (
                state.character.training.ranks
                and state.character.training.early_refunds_remaining
                and state.character.level
                <= self.catalog.progression.early_refund_level_limit
            ):
                suggestions.append("retrain")
        if self._vendors_here(state) and not recovery:
            suggestions.append("market")
        if any(
            recipe.facility in room.facilities
            for recipe in self.catalog.economy.recipes.values()
        ) and not recovery:
            suggestions.append("craft")
        if (
            ("salvage_bench" in room.facilities or "repair_bench" in room.facilities)
            and not recovery
        ):
            suggestions.append("salvage")
        if state.character.room_id == "route_concourse" or state.character.companion_id:
            suggestions.append("companion")
        if recovery:
            suggestions.append("queue")
            if state.queued_action is not None:
                suggestions.append("cancel")
        return list(dict.fromkeys(suggestions))

    def _help(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        if query:
            if query in {"here", "now", "context"}:
                actions = self._contextual_actions(state)
                lines = ["USEFUL HERE · EXACT COMMANDS"]
                lines.extend(
                    f"  {item['command']:<30} {item['reason']}"
                    for item in actions
                )
                lines.append(
                    "Nothing above was executed. NEXT gives one objective step; HELP <command> explains a command family."
                )
                return _HandlerResult(("\n".join(lines),))
            exact = self.parser.spec_for(query)
            if not exact:
                exact = next(
                    (
                        spec
                        for spec in self.parser.specs
                        if query in spec.aliases or spec.name.startswith(query)
                    ),
                    None,
                )
            if exact:
                return _HandlerResult((exact.summary,))
            names = [spec.name for spec in self.parser.specs]
            suggestion = difflib.get_close_matches(query, names, n=1, cutoff=0.55)
            if suggestion:
                return _HandlerResult(
                    (f"No help for {query!r}. Did you mean HELP {suggestion[0].upper()}?",)
                )
            return _HandlerResult((f"No help is available for {query!r}.",))
        groups = (
            "World: NEXT, LOOK, EXAMINE, GLANCE, EXITS, ROUTE, JOURNAL, GO, WITHDRAW, SEARCH",
            "Items: GET, DROP, INVENTORY, EQUIPMENT, EQUIP, UNEQUIP, REPAIR, MODIFY, MARKET, CRAFT, SALVAGE",
            "Combat: TARGET, ASSESS, ATTACK, TECHNIQUE, ABILITY, COMPANION, STANCE, DEFENSE, STAND, HEALTH, STABILIZE, ROUNDTIME",
            "Sovereignty: SOVEREIGNTY, FACTION, TERRITORY (live Sprawl 15 consequences; candidacy is not membership)",
            "Coordination: PARTY, REPORT, DISTRICT, SERVICE (bounded authority and field exercises)",
            "Character: INFO, EFFECTS, EXPERIENCE, PLAN, PATH, TRAIN, RETRAIN, COURSE, REST",
            "Social: SAY, EMOTE",
            "Flow: QUEUE, CANCEL, AGAIN, WAIT, ROUNDTIME, STATE, SIGNAL, RECOVER",
            "Session: NEXT [FULL], HELP <command>, HELP HERE, SAVE, QUIT",
            "Directions can be entered directly: N, E, S, W, UP, DOWN.",
        )
        return _HandlerResult(("\n".join(groups),))

    def _save(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        return _HandlerResult(("Progress is saved automatically after every meaningful action.",))

    def _quit(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        return _HandlerResult(("Your progress is safe. Until next time.",), quit=True)
