"""Session use case coordinating the engine and a state repository."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Protocol

from beta_earth.application.engine import (
    CommandResult,
    GameEngine,
    normalize_player_name,
)
from beta_earth.application.parser import CommandParseError
from beta_earth.domain.events import CommandReceipt, DomainEvent, EventStream
from beta_earth.domain.model import (
    GameState,
    PLAYTEST_SURVEY_FIELDS,
    STATE_SCHEMA_VERSION,
)
from beta_earth.domain.state_migrations import migration_path


class StateConflict(RuntimeError):
    """A durable state changed between load and save."""


class StateStore(Protocol):
    def load(self, player_key: str) -> GameState | None: ...

    def save(self, state: GameState, events: tuple[DomainEvent, ...]) -> int: ...

    def save_migration(
        self,
        previous_state: GameState,
        migrated_state: GameState,
        events: tuple[DomainEvent, ...],
    ) -> int: ...

    def save_schema_migration(
        self,
        state: GameState,
        from_schema: int,
        events: tuple[DomainEvent, ...],
    ) -> int: ...

    def write_playtest_receipt(
        self,
        player_key: str,
        payload: dict[str, object],
    ) -> str: ...


@dataclass(slots=True)
class GameSession:
    engine: GameEngine
    store: StateStore
    state: GameState
    created: bool = False
    receipt_context: dict[str, object] = field(default_factory=dict)
    event_stream: EventStream = field(default_factory=EventStream)

    _READ_ONLY_COMMANDS = frozenset({
        "assess", "briefing", "build", "course", "effects", "equipment", "examine",
        "experience", "exits", "glance", "health", "help", "info",
        "inventory", "journal", "look", "next", "path", "plan", "quest",
        "recover", "roundtime", "route", "save", "signal", "state", "target", "train",
        "retrain", "wait",
    })

    @staticmethod
    def _creature_health_total(state: GameState) -> int:
        return sum(
            max(0, creature.health)
            for room_creatures in state.creatures.values()
            for creature in room_creatures
        )

    def _advance_playtest_clock(
        self,
        before: GameState,
        after: GameState,
        observed_at: float,
        *,
        campaign_complete: bool,
    ) -> bool:
        before_timer = before.beginner_telemetry
        timer = after.beginner_telemetry
        changed = False
        if before_timer.playtest_status == "running":
            anchor = (
                before_timer.playtest_last_activity_at
                or before_timer.playtest_started_at
                or observed_at
            )
            delta = max(0.0, observed_at - anchor)
            threshold = float(
                max(30, min(900, before_timer.playtest_idle_threshold_seconds))
            )
            active = min(delta, threshold)
            idle = max(0.0, delta - threshold)
            timer.playtest_active_seconds += active
            timer.playtest_idle_seconds += idle
            before_campaign = self.engine._beginner_experience_projection(before)
            after_campaign = self.engine._beginner_experience_projection(after)
            chapter_id = str(
                before_campaign.get("active_chapter_id") or "between_chapters"
            )
            timer.playtest_chapter_active_seconds[chapter_id] = (
                timer.playtest_chapter_active_seconds.get(chapter_id, 0.0) + active
            )
            timer.playtest_chapter_idle_seconds[chapter_id] = (
                timer.playtest_chapter_idle_seconds.get(chapter_id, 0.0) + idle
            )
            before_chapter = before_campaign.get("active_chapter_id")
            after_chapter = after_campaign.get("active_chapter_id")
            if before_chapter and before_chapter != after_chapter:
                timer.playtest_milestones.setdefault(
                    f"chapter:{before_chapter}:complete",
                    timer.playtest_active_seconds,
                )
            if before.character.level < 10 <= after.character.level:
                timer.playtest_milestones.setdefault(
                    "level:10", timer.playtest_active_seconds
                )
            timer.playtest_last_activity_at = observed_at
            timer.playtest_command_count += 1
            changed = bool(delta or active or idle or timer.playtest_command_count)
            if campaign_complete and timer.playtest_status == "running":
                timer.playtest_status = "completed"
                timer.playtest_completed_at = observed_at
                timer.playtest_pause_started_at = 0.0
                timer.playtest_milestones.setdefault(
                    "campaign:complete", timer.playtest_active_seconds
                )
                changed = True
        return changed

    def _record_beginner_telemetry(
        self,
        before: GameState,
        after: GameState,
        raw: str,
        result: CommandResult,
    ) -> bool:
        telemetry = after.beginner_telemetry
        before_projection = self.engine._beginner_experience_projection(before)
        after_projection = self.engine._beginner_experience_projection(after)
        beginner_active = not bool(before_projection["complete"])

        parsed = None
        parse_failed = False
        try:
            parsed = self.engine.parser.parse(raw)
        except CommandParseError:
            parse_failed = True

        playtest_command = parsed is not None and parsed.name == "playtest"
        observed_at = self.engine.clock.now()
        timing_changed = self._advance_playtest_clock(
            before,
            after,
            observed_at,
            campaign_complete=bool(after_projection["complete"]),
        )
        if not beginner_active and not playtest_command:
            return timing_changed

        if beginner_active:
            telemetry.total_commands += 1
            if result.changed:
                telemetry.changed_commands += 1
            if parse_failed:
                telemetry.parse_errors += 1

        normalized = " ".join(raw.strip().split())[:160]
        hint_requested = normalized.casefold().startswith((
            "next", "hint", "step", "briefing", "recap", "resume", "route objective",
            "companion advise", "mercenary advise", "hireling advise",
            "guide", "help here",
        ))
        if beginner_active and hint_requested:
            telemetry.hints_requested += 1

        companion_read_only = bool(
            parsed is not None
            and parsed.name == "companion"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {
                    "status", "list", "advise", "advice", "hint"
                }
            )
        )
        guide_read_only = bool(
            parsed is not None
            and parsed.name == "guide"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {"status", "list"}
            )
        )
        playtest_read_only = bool(
            parsed is not None
            and parsed.name == "playtest"
            and (
                not parsed.args
                or parsed.args[0].casefold() in {
                    "status", "receipt", "plan", "profile", "checklist", "issues", "issue-list"
                }
            )
        )
        withdrawal_read_only = bool(
            parsed is not None
            and parsed.name == "withdraw"
            and bool(parsed.args)
            and parsed.args[0].casefold() in {"status", "odds", "plan"}
        )
        semantically_read_only = bool(
            parsed is not None
            and (
                parsed.name in self._READ_ONLY_COMMANDS
                or companion_read_only
                or guide_read_only
                or playtest_read_only
                or withdrawal_read_only
            )
        )
        blocked = bool(
            beginner_active
            and not parse_failed
            and parsed is not None
            and not semantically_read_only
            and not result.changed
        )
        if blocked:
            telemetry.blocked_commands += 1
        combat_candidate = bool(
            parsed is not None
            and parsed.name in {"attack", "technique", "ability", "withdraw", "stabilize"}
        )
        if beginner_active and (parse_failed or blocked) and not combat_candidate:
            telemetry.friction_since_progress = min(
                100_000_000,
                telemetry.friction_since_progress + 1,
            )
            telemetry.last_friction_command = normalized or "<empty command>"
            if telemetry.first_friction_command is None:
                telemetry.first_friction_command = telemetry.last_friction_command

        if beginner_active and before.incapacitation is None and after.incapacitation is not None:
            telemetry.incapacitations += 1
        if beginner_active and before.incapacitation is not None and after.incapacitation is None:
            telemetry.recoveries += 1

        if beginner_active:
            chapter_id = str(before_projection.get("active_chapter_id") or "between_chapters")
            telemetry.chapter_commands[chapter_id] = telemetry.chapter_commands.get(chapter_id, 0) + 1
            if before.character.room_id != after.character.room_id:
                room_id = after.character.room_id
                telemetry.room_entries[room_id] = telemetry.room_entries.get(room_id, 0) + 1

        event_kinds = {event.kind for event in result.events}
        if beginner_active:
            telemetry.brief_revisit_descriptions += sum(
                event.kind == "world.room_revisited_briefly"
                for event in result.events
            )
        withdrawal_events = [
            event for event in result.events
            if event.kind == "combat.withdrawal_resolved"
        ]
        if beginner_active:
            for event in withdrawal_events:
                if bool(event.payload.get("success")):
                    telemetry.successful_withdrawals += 1
                else:
                    telemetry.failed_withdrawals += 1
            telemetry.companion_setups += sum(
                event.kind == "combat.companion_setup_resolved"
                for event in result.events
            )
            telemetry.companion_finish_reservations += sum(
                event.kind == "combat.companion_finish_reserved"
                for event in result.events
            )

        creature_damage = max(
            0,
            self._creature_health_total(before) - self._creature_health_total(after),
        )
        meaningful_heal = after.character.health > before.character.health
        meaningful_guard = after.character.guard_points > before.character.guard_points
        event_damage = sum(
            max(0, int(event.payload.get("damage", 0)))
            for event in result.events
            if event.kind in {
                "combat.attack_resolved",
                "combat.companion_attack_resolved",
            }
            and type(event.payload.get("damage", 0)) is int
        )
        combat_progress = bool(
            creature_damage
            or event_damage
            or meaningful_heal
            or meaningful_guard
            or event_kinds.intersection(
                {
                    "combat.target_defeated",
                    "combat.room_cleared",
                    "combat.boss_phase_changed",
                    "combat.diagnostic_target_reset",
                    "combat.diagnostic_character_reset",
                    "combat.companion_intercepted",
                    "combat.companion_setup_resolved",
                    "combat.companion_interrupted",
                    "combat.counter_suppressed",
                    "condition.capstone_stabilized",
                    "character.recovered",
                }
            )
            or any(bool(event.payload.get("success")) for event in withdrawal_events)
        )
        combat_command = bool(
            parsed is not None
            and parsed.name in {"attack", "technique", "ability", "withdraw", "stabilize"}
            and (
                any(kind.startswith("combat.") for kind in event_kinds)
                or parsed.name in {"attack", "withdraw"}
            )
        )

        progress_reasons: list[str] = []
        if beginner_active:
            before_competencies = int(before_projection["completed_competencies"])
            after_competencies = int(after_projection["completed_competencies"])
            if after_competencies > before_competencies:
                progress_reasons.append(
                    f"competency {after_competencies}/{after_projection['competency_total']}"
                )
            if before.story.active_quest_id != after.story.active_quest_id:
                progress_reasons.append(str(after.story.active_quest_id or "checkpoint"))
            elif before.story.active_stage_id != after.story.active_stage_id:
                progress_reasons.append(str(after.story.active_stage_id or "stage complete"))
            if after.character.level > before.character.level:
                progress_reasons.append(f"level {after.character.level}")
            if len(after.visited_rooms) > len(before.visited_rooms):
                progress_reasons.append(f"discovered {after.character.room_id}")
            if creature_damage or event_damage:
                progress_reasons.append(
                    f"combat damage {max(creature_damage, event_damage)}"
                )
            if "combat.boss_phase_changed" in event_kinds:
                progress_reasons.append("boss phase advanced")
            if "combat.target_defeated" in event_kinds:
                progress_reasons.append("target defeated")
            if "combat.companion_setup_resolved" in event_kinds:
                progress_reasons.append("Sol created an opening")
            if "combat.companion_intercepted" in event_kinds:
                progress_reasons.append("Sol intercepted pressure")
            if meaningful_heal:
                progress_reasons.append("health recovered")
            if any(bool(event.payload.get("success")) for event in withdrawal_events):
                progress_reasons.append("withdrawal succeeded")

            if combat_command:
                telemetry.current_combat_sequence += 1
                telemetry.longest_combat_sequence = max(
                    telemetry.longest_combat_sequence,
                    telemetry.current_combat_sequence,
                )
                if combat_progress:
                    telemetry.combat_progress_events += 1
                    telemetry.current_combat_repetition = 0
                else:
                    telemetry.combat_repetition_commands += 1
                    telemetry.current_combat_repetition += 1
                    # Combat repetition is reported on its own track. Do not
                    # contaminate route/story friction with a fight that simply
                    # needs a tactical change.
                    telemetry.longest_combat_repetition = max(
                        telemetry.longest_combat_repetition,
                        telemetry.current_combat_repetition,
                    )
            elif not semantically_read_only:
                telemetry.current_combat_sequence = 0
                telemetry.current_combat_repetition = 0

            if progress_reasons:
                telemetry.commands_since_progress = 0
                telemetry.friction_since_progress = 0
                telemetry.last_progress_label = ", ".join(progress_reasons)[:160]
            elif parse_failed:
                telemetry.commands_since_progress += 1
            elif parsed is not None and not semantically_read_only and not combat_command:
                telemetry.commands_since_progress += 1
            telemetry.longest_stall = max(
                telemetry.longest_stall,
                telemetry.commands_since_progress,
            )


        return bool(
            result.changed
            or parse_failed
            or blocked
            or hint_requested
            or progress_reasons
            or timing_changed
            or (before.incapacitation is None) != (after.incapacitation is None)
        )

    def _playtest_receipt_payload(self, state: GameState) -> dict[str, object]:
        timing = self.engine._playtest_projection(state)
        campaign = self.engine._beginner_experience_projection(state)
        calibration = self.engine._beginner_calibration_projection(state)
        profile = self.engine._playtest_profile_projection(state)
        sol = state.character.companion_progress.get("sol")
        environment = {
            "os_family": str(self.receipt_context.get("os_family") or "unknown")[:32],
            "python_version": str(self.receipt_context.get("python_version") or "unknown")[:32],
            "launch_surface": str(self.receipt_context.get("launch_surface") or "unknown")[:32],
            "computer_label": str(self.receipt_context.get("computer_label") or "PC-LOCAL-UNASSIGNED")[:64],
            "native_windows_launcher": bool(self.receipt_context.get("native_windows_launcher", False)),
        }
        issues = [dict(issue) for issue in state.beginner_telemetry.playtest_issues]
        blocking_issues = sum(issue.get("severity") == "blocking" for issue in issues)
        survey_complete = PLAYTEST_SURVEY_FIELDS.issubset(
            state.beginner_telemetry.playtest_survey
        )
        profile_valid = bool(profile.get("class_matches_family"))
        receipt_complete = bool(
            state.beginner_telemetry.playtest_status == "completed"
            and campaign["complete"]
            and survey_complete
            and profile_valid
        )
        windows_first_time_standard = bool(
            receipt_complete
            and environment["os_family"].casefold() == "windows"
            and environment["native_windows_launcher"]
            and profile.get("experience") == "first_time"
            and profile.get("mode") == "standard"
        )
        return {
            "schema": "beta-earth-local-playtest-receipt-v3",
            "project": "MUDD Game Development",
            "release": "Beta Earth: Sovereignty Next",
            "content_version": state.content_version,
            "player": {
                "key_sha256": hashlib.sha256(
                    state.character.key.encode("utf-8")
                ).hexdigest(),
                "local_label": (
                    "local-"
                    + hashlib.sha256(
                        state.character.key.encode("utf-8")
                    ).hexdigest()[:8]
                ),
                "revision": state.revision,
            },
            "environment": environment,
            "profile": profile,
            "timing": timing,
            "campaign": {
                "complete": bool(campaign["complete"]),
                "modeled_minutes": int(campaign["estimated_completed_minutes"]),
                "target_minutes": int(campaign["target_minutes"]),
                "level": state.character.level,
                "target_level": int(campaign["target_level"]),
                "competencies": int(campaign["completed_competencies"]),
                "competency_total": int(campaign["competency_total"]),
                "active_quest_id": state.story.active_quest_id,
                "active_stage_id": state.story.active_stage_id,
                "checkpoint_id": state.story.checkpoint_id,
            },
            "sol": (
                {
                    "level": sol.level,
                    "experience": sol.experience,
                    "health": sol.health,
                    "max_health": sol.max_health,
                    "order": sol.order,
                    "defeated_targets": sol.defeated_targets,
                    "setup_actions": sol.setup_actions,
                    "finish_reservations": sol.finish_reservations,
                    "player_enabled_finishes": sol.player_enabled_finishes,
                    "finishing_strikes": sol.finishing_strikes,
                    "damage_dealt": sol.damage_dealt,
                    "damage_intercepted": sol.damage_intercepted,
                    "active_companion": state.character.companion_id == "sol",
                }
                if sol is not None
                else None
            ),
            "calibration": calibration,
            "first_session_clarity": {
                "assist_prompts": state.beginner_telemetry.assist_prompts,
                "brief_revisit_descriptions": state.beginner_telemetry.brief_revisit_descriptions,
                "chapter_active_seconds": dict(sorted(state.beginner_telemetry.playtest_chapter_active_seconds.items())),
                "chapter_idle_seconds": dict(sorted(state.beginner_telemetry.playtest_chapter_idle_seconds.items())),
                "milestones": dict(sorted(state.beginner_telemetry.playtest_milestones.items())),
            },
            "issues": issues,
            "notes": list(state.beginner_telemetry.playtest_notes),
            "survey": dict(sorted(state.beginner_telemetry.playtest_survey.items())),
            "readiness": {
                "receipt_complete": receipt_complete,
                "campaign_complete": bool(campaign["complete"]),
                "survey_complete": survey_complete,
                "profile_valid": profile_valid,
                "blocking_issue_count": blocking_issues,
                "windows_first_time_standard_eligible": windows_first_time_standard,
                "cohort_decision": "blocked" if blocking_issues else ("eligible" if windows_first_time_standard else "not_eligible"),
            },
            "privacy": {
                "local_only": True,
                "network_reporting": False,
                "contains_credentials": False,
                "contains_raw_commands": False,
                "contains_absolute_paths": False,
            },
            "copyright": (
                "Copyright © 2026 Gateway Information Group LLC. "
                "All rights reserved."
            ),
        }

    @staticmethod
    def _receipt_requested(
        before: GameState,
        after: GameState,
        result: CommandResult,
    ) -> bool:
        kinds = {event.kind for event in result.events}
        return bool(
            kinds.intersection({"playtest.completed", "playtest.receipt_requested"})
            or (
                before.beginner_telemetry.playtest_status == "running"
                and after.beginner_telemetry.playtest_status == "completed"
            )
        )

    def _attach_playtest_receipt(
        self,
        result: CommandResult,
        state: GameState,
    ) -> CommandResult:
        writer = getattr(self.store, "write_playtest_receipt", None)
        if writer is None:
            return CommandResult(
                lines=result.lines + (
                    "Local playtest receipt could not be written by this storage adapter.",
                ),
                events=result.events,
                changed=result.changed,
                quit=result.quit,
            )
        try:
            path = str(writer(state.character.key, self._playtest_receipt_payload(state)))
            line = f"Local playtest receipt: {path}"
        except (OSError, ValueError, TypeError) as exc:
            line = (
                "Local playtest receipt write failed: "
                f"{exc}. Use PLAYTEST RECEIPT to retry without changing progress."
            )
        return CommandResult(
            lines=result.lines + (line,),
            events=result.events,
            changed=result.changed,
            quit=result.quit,
        )

    def _attach_adaptive_assist(
        self,
        result: CommandResult,
        state: GameState,
    ) -> tuple[CommandResult, bool]:
        """Offer bounded recovery guidance after repeated noncombat friction."""

        campaign = self.engine._beginner_experience_projection(state)
        telemetry = state.beginner_telemetry
        friction = telemetry.friction_since_progress
        if bool(campaign["complete"]) or friction < 2:
            return result, False
        if friction == telemetry.last_assist_friction_count:
            return result, False
        if friction != 2 and (friction < 5 or (friction - 2) % 3 != 0):
            return result, False
        telemetry.assist_prompts += 1
        telemetry.last_assist_friction_count = friction
        line = (
            "[Assist] NEXT shows one exact objective step. "
            "HELP HERE lists exact commands available in this location."
        )
        return (
            CommandResult(
                lines=result.lines + (line,),
                events=result.events + (
                    DomainEvent(
                        "guidance.adaptive_assist_shown",
                        {"friction_count": friction, "prompt_number": telemetry.assist_prompts},
                    ),
                ),
                changed=result.changed,
                quit=result.quit,
            ),
            True,
        )

    @property
    def last_command_receipt(self) -> CommandReceipt | None:
        """Return the newest deterministic command receipt, when available."""

        return self.event_stream.last_receipt

    def _observe_result(
        self,
        raw: str,
        before: GameState,
        after: GameState,
        result: CommandResult,
    ) -> CommandResult:
        self.event_stream.observe(
            result.events,
            command=raw,
            revision_before=before.revision,
            revision_after=after.revision,
            changed=result.changed,
        )
        return result

    def execute(self, raw: str) -> CommandResult:
        # Execute against an isolated working copy. If another unrestricted HUD
        # saves first, reload the newest durable state and apply this command once.
        # This keeps parallel launches useful without silently overwriting progress.
        before = GameState.from_dict(self.state.to_dict())
        working = GameState.from_dict(self.state.to_dict())
        result = self.engine.execute(working, raw)
        telemetry_changed = self._record_beginner_telemetry(before, working, raw, result)
        result, assist_changed = self._attach_adaptive_assist(result, working)
        telemetry_changed = telemetry_changed or assist_changed
        receipt_requested = self._receipt_requested(before, working, result)
        if not result.changed and not telemetry_changed:
            response = (
                self._attach_playtest_receipt(result, working)
                if receipt_requested
                else result
            )
            return self._observe_result(raw, before, working, response)
        self.engine.validate_state(working)
        try:
            self.store.save(working, result.events)
        except StateConflict:
            latest = self.store.load(self.state.character.key)
            if latest is None:
                raise
            retry_before = GameState.from_dict(latest.to_dict())
            working = GameState.from_dict(latest.to_dict())
            result = self.engine.execute(working, raw)
            retry_telemetry_changed = self._record_beginner_telemetry(
                retry_before, working, raw, result
            )
            result, retry_assist_changed = self._attach_adaptive_assist(
                result, working
            )
            retry_telemetry_changed = (
                retry_telemetry_changed or retry_assist_changed
            )
            if result.changed or retry_telemetry_changed:
                self.engine.validate_state(working)
                self.store.save(working, result.events)
                self.state = working
                response = CommandResult(
                    lines=(
                        "Another open session advanced this character. Your command was safely applied to the newest save.",
                        "",
                        *result.lines,
                    ),
                    events=result.events,
                    changed=True,
                    quit=result.quit,
                )
                response = (
                    self._attach_playtest_receipt(response, working)
                    if self._receipt_requested(retry_before, working, result)
                    else response
                )
                return self._observe_result(raw, retry_before, working, response)
            self.state = working
            response = CommandResult(
                lines=(
                    "Another open session advanced this character. The newest save is now loaded.",
                    "",
                    *result.lines,
                ),
                events=result.events,
                changed=False,
                quit=result.quit,
            )
            return self._observe_result(raw, retry_before, working, response)
        self.state = working
        response = (
            self._attach_playtest_receipt(result, working)
            if receipt_requested
            else result
        )
        return self._observe_result(raw, before, working, response)


class GameApplication:
    def __init__(
        self,
        engine: GameEngine,
        store: StateStore,
        *,
        receipt_context: dict[str, object] | None = None,
    ) -> None:
        self.engine = engine
        self.store = store
        self.receipt_context = dict(receipt_context or {})

    def open_session(self, player_name: str) -> GameSession:
        key, display = normalize_player_name(player_name)
        state = self.store.load(key)
        if state is not None:
            source_schema = state.source_schema_version
            if source_schema < STATE_SCHEMA_VERSION:
                steps = migration_path(source_schema)
                schema_events = (
                    DomainEvent(
                        "migration.schema_applied",
                        {
                            "from_schema": source_schema,
                            "to_schema": STATE_SCHEMA_VERSION,
                            "steps": [step.name for step in steps],
                        },
                    ),
                )
                self.store.save_schema_migration(
                    state, source_schema, schema_events
                )
            migrated = GameState.from_dict(state.to_dict())
            migration_events = self.engine.reconcile_state(migrated)
            self.engine.validate_state(migrated)
            if migration_events:
                if state.content_version != migrated.content_version:
                    self.store.save_migration(state, migrated, migration_events)
                else:
                    # Same-version reconciliation can initialize newly active,
                    # save-safe contracts after a structural schema migration.
                    # Persist it as an ordinary revision rather than pretending
                    # the content version changed.
                    self.store.save(migrated, migration_events)
                state = migrated
            return GameSession(self.engine, self.store, state, created=False, receipt_context=dict(self.receipt_context))
        state = self.engine.new_game(display, foundation_pending=True)
        self.engine.validate_state(state)
        self.store.save(
            state,
            (DomainEvent("character.created", {"name": state.character.name}),),
        )
        return GameSession(self.engine, self.store, state, created=True, receipt_context=dict(self.receipt_context))
