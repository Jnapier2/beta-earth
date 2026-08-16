"""Live, bounded, privacy-preserving support export for Beta Earth.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sqlite3
import time
import uuid
import zipfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_ITEMS = 20
MAX_TOTAL_UNCOMPRESSED_BYTES = 8 * 1024 * 1024
PROJECT_SLUG = "beta-earth-sovereignty-next"
NOTICE = "Copyright © 2026 Gateway Information Group LLC. All rights reserved."
LICENSE = "Proprietary first-party content; no public license inferred."
DEFAULT_EXPORT_DIRECTORY = "exports/support"

PROJECT_FILES = (
    "VERSION.txt",
    "PACKAGE_METADATA.json",
    "diagnostics/JOURNEY_11_20_CALIBRATION.json",
    "KNOWN_GOOD_STATE.md",
    "diagnostics/RELEASE_CHECKS.json",
    "MANIFEST.json",
    "docs/RUNBOOK.md",
)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return default


def _version(project_root: Path) -> str:
    try:
        return (project_root / "VERSION.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def default_destination(project_root: Path) -> Path:
    version = _version(project_root)
    return (
        project_root
        / DEFAULT_EXPORT_DIRECTORY
        / f"UPLOAD_THIS_BetaEarth_Diagnostics_v{version}.zip"
    )


def _absolute_path_findings(project_root: Path) -> list[str]:
    patterns = (
        re.compile(r"(?i)[A-Z]:\\Users\\"),
        re.compile(r"/Users/[^/]+"),
        re.compile(r"/home/[^/]+"),
    )
    intentional_detection_literals = {
        "src/beta_earth/application/diagnostics.py",
        "tests/test_release_contract.py",
        "tests/test_supplemental_source_ledger.py",
        "tools/export_diagnostics.py",
        "tools/run_release_checks.py",
    }
    findings: list[str] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(project_root).as_posix()
        if relative in intentional_detection_literals:
            continue
        # Exact, read-only developer source evidence may legitimately preserve the
        # source workstation paths recorded by the canon package. It is never
        # imported or executed by the player runtime and is scanned separately for
        # secrets and archive safety during release finalization.
        if relative.startswith("development/"):
            continue
        if any(part in {"runtime", "SUPPORT_EXPORTS", "__pycache__"} for part in path.parts):
            continue
        if path.suffix.casefold() not in {".py", ".md", ".json", ".toml", ".bat", ".txt", ".css", ".js"}:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if any(pattern.search(text) for pattern in patterns):
            findings.append(relative)
    return sorted(findings)


def _security_and_drive_summary(project_root: Path) -> dict[str, Any]:
    norton: dict[str, Any] = {
        "artifact": None,
        "sha256": None,
        "test_result": "not tested on an exact final artifact",
        "unresolved_risk": "Scan the exact final ZIP with current protection before distribution.",
    }
    manifest = _read_json(project_root / "MANIFEST.json", {})
    if isinstance(manifest, dict) and isinstance(manifest.get("norton_status"), dict):
        norton.update(manifest["norton_status"])
    verification_path = project_root.parent / f"{project_root.name}_RELEASE_VERIFICATION.json"
    verification = _read_json(verification_path, {})
    if isinstance(verification, dict) and verification:
        artifact = verification.get("artifact", {})
        if isinstance(verification.get("norton_status"), dict):
            norton.update(verification["norton_status"])
        if isinstance(artifact, dict):
            norton["artifact"] = artifact.get("filename") or norton.get("artifact")
            norton["sha256"] = artifact.get("sha256") or norton.get("sha256")
        norton["exact_verification_record_status"] = verification.get("status")
    return {
        "sensitivity": "project-internal",
        "redaction": (
            "Character names/keys, commands, event payloads, credentials, tokens, cookies, "
            "absolute user paths, private endpoints, and connector identifiers are excluded."
        ),
        "network_actions": "none",
        "secret_handling": "No secret values are collected.",
        "norton_status": norton,
        "drive_status": "local-only; no upload or synchronization claimed",
    }


def _state_summary(document: dict[str, Any], slot: int) -> dict[str, Any]:
    character = document.get("character", {}) if isinstance(document.get("character"), dict) else {}
    build = character.get("build", {}) if isinstance(character.get("build"), dict) else {}
    training = character.get("training", {}) if isinstance(character.get("training"), dict) else {}
    story = document.get("story", {}) if isinstance(document.get("story"), dict) else {}
    telemetry = (
        document.get("beginner_telemetry", {})
        if isinstance(document.get("beginner_telemetry"), dict)
        else {}
    )
    inventory = character.get("inventory", []) if isinstance(character.get("inventory"), list) else []
    visited = document.get("visited_rooms", []) if isinstance(document.get("visited_rooms"), list) else []
    flags = document.get("flags", []) if isinstance(document.get("flags"), list) else []
    flag_set = {str(flag) for flag in flags}
    modified_items = [
        {
            "definition_id": item.get("definition_id"),
            "upgrade_level": item.get("upgrade_level", 0),
            "durability": item.get("durability"),
        }
        for item in inventory
        if isinstance(item, dict) and int(item.get("upgrade_level", 0) or 0) > 0
    ]
    readiness_flags = (
        "collector_wrong_date_found",
        "subject_marker_decoded",
        "trial_was_staged",
        "witness_plan_ready",
        "survival_cache_ready",
        "escape_route_confirmed",
    )
    route_handoffs = sorted(
        flag.split(":", 1)[1] for flag in flag_set if flag.startswith("hq_handoff:")
    )
    specializations = sorted(
        flag.split(":", 1)[1] for flag in flag_set if flag.startswith("specialization:")
    )
    established_contacts = sorted(
        flag.split(":", 1)[1] for flag in flag_set if flag.startswith("contact_established:")
    )
    selected_favors = sorted(
        flag.split(":", 1)[1] for flag in flag_set if flag.startswith("favor_selected:")
    )
    selected_expeditions = sorted(
        flag.split(":", 1)[1] for flag in flag_set if flag.startswith("expedition_selected:")
    )
    accepted_candidacies = sorted(
        flag.split(":", 1)[1] for flag in flag_set if flag.startswith("faction_candidate:")
    )
    deferred_candidacies = sorted(
        flag.split(":", 1)[1] for flag in flag_set if flag.startswith("candidacy_deferred:")
    )
    declined_candidacies = sorted(
        flag.split(":", 1)[1] for flag in flag_set if flag.startswith("candidacy_declined:")
    )
    return {
        "slot": slot,
        "schema_version": document.get("schema_version"),
        "content_version": document.get("content_version"),
        "revision": document.get("revision"),
        "turn": document.get("turn"),
        "room_id": character.get("room_id"),
        "level": character.get("level"),
        "health": character.get("health"),
        "max_health": character.get("max_health"),
        "inventory_count": len(inventory),
        "modified_equipment_count": len(modified_items),
        "modified_equipment": modified_items,
        "visited_room_count": len(visited),
        "flag_count": len(flags),
        "guard_points": character.get("guard_points", 0),
        "economy": {
            "credits": character.get("credits", 0),
            "companion_id": character.get("companion_id"),
        },
        "specialization": {
            "selected_branch_ids": specializations,
            "ability_point_available": "ability_point_available" in flag_set,
            "ready_at": character.get("specialization_ready_at", 0),
        },
        "signature_technique": {
            "claimed": "signature_instinct_claimed" in flag_set,
            "used": "class_technique_used" in flag_set,
            "ready_at": character.get("technique_ready_at", 0),
        },
        "build": {
            "status": build.get("status"),
            "class_id": build.get("class_id"),
            "allocation_mode": build.get("allocation_mode"),
            "tutorial_status": build.get("tutorial_status"),
            "tutorial_step_id": build.get("tutorial_step_id"),
        },
        "training": {
            "profile_id": training.get("profile_id"),
            "physical_points": training.get("physical_points"),
            "mental_points": training.get("mental_points"),
        },
        "story": {
            "active_quest_id": story.get("active_quest_id"),
            "active_stage_id": story.get("active_stage_id"),
            "checkpoint_id": story.get("checkpoint_id"),
            "completed_quest_count": len(story.get("completed_quests", [])) if isinstance(story.get("completed_quests"), list) else 0,
            "record_count": len(story.get("records", [])) if isinstance(story.get("records"), list) else 0,
            "claimed_reward_count": len(story.get("claimed_rewards", [])) if isinstance(story.get("claimed_rewards"), list) else 0,
            "relationship_count": len(story.get("relationships", {})) if isinstance(story.get("relationships"), dict) else 0,
            "confrontation_readiness": {
                "completed": sum(flag in flag_set for flag in readiness_flags),
                "total": len(readiness_flags),
            },
            "capstone_resolved": "sol_confrontation_resolved" in flag_set,
            "route_handoff_ready": "route_handoff_ready" in flag_set,
            "route_handoff_factions": route_handoffs,
            "established_contacts": established_contacts,
            "selected_favors": selected_favors,
            "selected_expeditions": selected_expeditions,
            "regional_expedition_complete": (
                "avalonte_expedition_complete" in flag_set
                or "dark_water_expedition_complete" in flag_set
            ),
            "candidacy": {
                "accepted": accepted_candidacies,
                "deferred": deferred_candidacies,
                "declined": declined_candidacies,
                "membership_status": "unaffiliated",
                "rank_status": "none",
                "guild_eligibility": "locked_until_required_faction_quests",
            },
        },
        "incapacitated": document.get("incapacitation") is not None,
        "queued_action_present": document.get("queued_action") is not None,
        "beginner_calibration": {
            "present": bool(telemetry),
            "total_commands": int(telemetry.get("total_commands", 0) or 0),
            "changed_commands": int(telemetry.get("changed_commands", 0) or 0),
            "parse_errors": int(telemetry.get("parse_errors", 0) or 0),
            "blocked_commands": int(telemetry.get("blocked_commands", 0) or 0),
            "incapacitations": int(telemetry.get("incapacitations", 0) or 0),
            "recoveries": int(telemetry.get("recoveries", 0) or 0),
            "hints_requested": int(telemetry.get("hints_requested", 0) or 0),
            "commands_since_progress": int(
                telemetry.get("commands_since_progress", 0) or 0
            ),
            "longest_stall": int(telemetry.get("longest_stall", 0) or 0),
            "last_progress_label": telemetry.get("last_progress_label"),
            "chapter_commands": (
                dict(sorted(telemetry.get("chapter_commands", {}).items()))
                if isinstance(telemetry.get("chapter_commands"), dict)
                else {}
            ),
            "room_entries": (
                dict(sorted(telemetry.get("room_entries", {}).items()))
                if isinstance(telemetry.get("room_entries"), dict)
                else {}
            ),
            "raw_command_text_exported": False,
        },
    }


def _tutorial_story_diagnostic(state: dict[str, Any], quests: dict[str, Any], tutorial: dict[str, Any]) -> dict[str, Any]:
    build = state.get("build", {})
    story = state.get("story", {})
    findings: list[dict[str, str]] = []
    tutorial_status = build.get("tutorial_status")
    tutorial_step = build.get("tutorial_step_id")
    if tutorial_status == "active" and tutorial_step == "enter_sprawl" and state.get("room_id") == "rain_market":
        findings.append({
            "severity": "repairable",
            "code": "guided-start-rain-market-already-reached",
            "detail": "The player is already in the Rain Market while the movement step is active. GUIDE SYNC should catch up immediately.",
        })
    if tutorial_status == "active" and tutorial_step and tutorial_step not in tutorial:
        findings.append({
            "severity": "error",
            "code": "unknown-guided-start-step",
            "detail": f"Saved tutorial step {tutorial_step!r} is not authored by this build.",
        })
    quest_id = story.get("active_quest_id")
    stage_id = story.get("active_stage_id")
    quest = quests.get(quest_id, {}) if quest_id else {}
    stages = quest.get("stages", {}) if isinstance(quest, dict) else {}
    stage = stages.get(stage_id, {}) if isinstance(stages, dict) else {}
    if quest_id and not quest:
        findings.append({"severity": "error", "code": "unknown-active-quest", "detail": f"Active quest {quest_id!r} is not present in content."})
    elif quest_id and stage_id and not stage:
        findings.append({"severity": "error", "code": "unknown-active-stage", "detail": f"Active stage {stage_id!r} is not present in quest {quest_id!r}."})
    calibration = (
        state.get("beginner_calibration", {})
        if isinstance(state.get("beginner_calibration"), dict)
        else {}
    )
    commands_since_progress = int(calibration.get("commands_since_progress", 0) or 0)
    if commands_since_progress >= 15:
        findings.append({
            "severity": "review",
            "code": "beginner-sustained-stall",
            "detail": (
                f"The local foundation record shows {commands_since_progress} "
                "commands without material progress. Use BRIEFING or ROUTE OBJECTIVE; "
                "no reward depends on guidance."
            ),
        })
    target_room_id = stage.get("target_room_id") if isinstance(stage, dict) else None
    current_room_id = state.get("room_id")
    if target_room_id and current_room_id == target_room_id:
        route_status = "at-objective"
        next_support_command = stage.get("suggested_command") or "briefing"
    elif target_room_id:
        route_status = "away-from-objective"
        next_support_command = "route objective"
    else:
        route_status = "action-in-current-area" if quest_id else "no-active-objective"
        next_support_command = "briefing" if quest_id else "quest"
    return {
        "slot": state.get("slot"),
        "tutorial": {
            "status": tutorial_status,
            "step_id": tutorial_step,
            "authored_step_count": len(tutorial),
            "recovery_command": "guide sync" if tutorial_status == "active" else None,
        },
        "story": {
            "active_quest_id": quest_id,
            "active_stage_id": stage_id,
            "checkpoint_id": story.get("checkpoint_id"),
            "objective": stage.get("objective") if isinstance(stage, dict) else None,
            "target_room_id": target_room_id,
            "current_room_id": current_room_id,
            "route_status": route_status,
            "suggested_command": stage.get("suggested_command") if isinstance(stage, dict) else None,
            "next_support_command": next_support_command,
        },
        "beginner_calibration": calibration,
        "findings": findings,
        "status": "needs-attention" if findings else "healthy",
    }


def _database_collect(path: Path, quest_index: dict[str, Any], tutorial_index: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str]:
    if not path.exists():
        return ({"present": False, "note": "No runtime database exists yet."}, [], [], {"total": 0, "counts": {}, "recent": []}, "No runtime event signals were available.\n")
    uri = path.resolve().as_uri() + "?mode=ro"
    deadline = time.monotonic() + 3.0
    with closing(sqlite3.connect(uri, uri=True, timeout=3.0)) as connection:
        connection.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0, 10_000)
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
        rows = connection.execute("SELECT state_json FROM characters ORDER BY updated_at, rowid").fetchall()
        event_rows = connection.execute(
            "SELECT event_kind, COUNT(*), MAX(recorded_at), MAX(revision) "
            "FROM domain_events GROUP BY event_kind ORDER BY event_kind"
        ).fetchall()
        recent = connection.execute(
            "SELECT event_kind, recorded_at, revision FROM domain_events "
            "ORDER BY id DESC LIMIT 40"
        ).fetchall()
        event_window_row = connection.execute(
            "SELECT MIN(recorded_at), MAX(recorded_at), COUNT(*) FROM domain_events"
        ).fetchone()
    states: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    parse_errors = 0
    for index, row in enumerate(rows, start=1):
        try:
            document = json.loads(str(row[0]))
            summary = _state_summary(document, index)
            states.append(summary)
            diagnostics.append(_tutorial_story_diagnostic(summary, quest_index, tutorial_index))
        except (json.JSONDecodeError, TypeError, ValueError):
            parse_errors += 1
    counts = {str(kind): int(count) for kind, count, _, _ in event_rows}
    first_event = event_window_row[0] if event_window_row else None
    last_event = event_window_row[1] if event_window_row else None
    event_window_count = int(event_window_row[2]) if event_window_row else 0
    try:
        event_span_seconds = max(
            0,
            int(
                (
                    datetime.fromisoformat(str(last_event))
                    - datetime.fromisoformat(str(first_event))
                ).total_seconds()
            ),
        ) if first_event is not None and last_event is not None else 0
    except (TypeError, ValueError):
        event_span_seconds = None
    observed_event_window = {
        "first_recorded_at": str(first_event) if first_event is not None else None,
        "last_recorded_at": str(last_event) if last_event is not None else None,
        "event_count": event_window_count,
        "span_seconds": event_span_seconds,
        "interpretation": (
            "Envelope between recorded state-changing events; not active play time "
            "and not a human-duration claim."
        ),
    }
    recent_safe = [
        {"event_kind": str(kind), "recorded_at": str(recorded_at), "revision": int(revision)}
        for kind, recorded_at, revision in recent
    ]
    signal_words = ("error", "fail", "conflict", "incapac", "recover", "migration", "repair", "tutorial")
    signal_lines = [
        f"{item['recorded_at']} | revision {item['revision']} | {item['event_kind']}"
        for item in recent_safe
        if any(word in item["event_kind"].casefold() for word in signal_words)
    ]
    runtime = {
        "present": True,
        "size_bytes": path.stat().st_size,
        "integrity_check": quick,
        "schema_version": schema,
        "character_slot_count": len(rows),
        "state_parse_error_count": parse_errors,
        "event_count": sum(counts.values()),
        "observed_event_window": observed_event_window,
        "collector_deadline_seconds": 3.0,
        "privacy": "State summaries are anonymized; raw state JSON is not exported.",
    }
    events = {
        "total": sum(counts.values()),
        "counts": counts,
        "recent": recent_safe,
        "observed_event_window": observed_event_window,
    }
    signals = "\n".join(signal_lines) + ("\n" if signal_lines else "No recent error/recovery/tutorial signals were detected.\n")
    return runtime, states, diagnostics, events, signals


def _startup_collect(project_root: Path, runtime_root: Path) -> tuple[dict[str, Any], str]:
    diagnostics_root = runtime_root / "diagnostics"
    preflight = _read_json(diagnostics_root / "PREFLIGHT_LATEST.json", {})
    if not isinstance(preflight, dict):
        preflight = {}
    failure_path = diagnostics_root / "STARTUP_FAILURE_LATEST.txt"
    try:
        failure = failure_path.read_text(encoding="utf-8", errors="replace")[-12000:]
    except OSError:
        failure = ""
    log_path = runtime_root / "logs" / "beta_earth.log"
    try:
        log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-120:]
    except OSError:
        log_lines = []
    root_text = str(project_root.resolve())
    home_text = str(Path.home())
    bounded_lines = [
        line.replace(root_text, "<PROJECT_ROOT>").replace(home_text, "<USER_HOME>")[-1000:]
        for line in log_lines
    ]
    machine = preflight.get("machine_profile", {}) if isinstance(preflight.get("machine_profile"), dict) else {}
    startup = {
        "preflight_present": bool(preflight),
        "preflight_status": preflight.get("status"),
        "preflight_mode": preflight.get("mode"),
        "machine_id": machine.get("canonical_id"),
        "machine_profile_source": machine.get("source"),
        "computer_awareness_only": True,
        "startup_failure_present": bool(failure),
        "bounded_log_present": bool(bounded_lines),
        "bounded_log_line_count": len(bounded_lines),
        "launch_locking_enabled": False,
        "parallel_hud_launches_allowed": True,
        "repair_command": "BetaEarthSovereignty.bat --repair",
        "self_test_command": "BetaEarthSovereignty.bat --self-test",
        "privacy": "Paths are replaced; logs contain startup lifecycle only, not commands or player names.",
    }
    signal_parts = []
    if failure:
        signal_parts.append("=== STARTUP FAILURE ===\n" + failure)
    if bounded_lines:
        signal_parts.append("=== BOUNDED STARTUP LOG TAIL ===\n" + "\n".join(bounded_lines))
    return startup, "\n\n".join(signal_parts)


def _content_collect(project_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    world = _read_json(project_root / "content" / "world.json", {})
    items = _read_json(project_root / "content" / "items.json", {})
    creatures = _read_json(project_root / "content" / "creatures.json", {})
    npcs = _read_json(project_root / "content" / "npcs.json", {})
    quests_doc = _read_json(project_root / "content" / "quests.json", {})
    rewards = _read_json(project_root / "content" / "rewards.json", {})
    classes = _read_json(project_root / "content" / "classes.json", {})
    creation = _read_json(project_root / "content" / "character_creation.json", {})
    economy = _read_json(project_root / "content" / "economy.json", {})
    rooms = world.get("rooms", []) if isinstance(world, dict) else []
    quests = quests_doc.get("quests", []) if isinstance(quests_doc, dict) else []
    quest_index: dict[str, Any] = {}
    for quest in quests:
        if not isinstance(quest, dict) or not quest.get("id"):
            continue
        stage_index = {stage.get("id"): stage for stage in quest.get("stages", []) if isinstance(stage, dict) and stage.get("id")}
        quest_index[str(quest["id"])] = {**quest, "stages": stage_index}
    tutorial_steps = creation.get("tutorial", {}).get("steps", []) if isinstance(creation, dict) else []
    tutorial_index = {step.get("id"): step for step in tutorial_steps if isinstance(step, dict) and step.get("id")}
    creature_rows = creatures.get("creatures", []) if isinstance(creatures, dict) else []
    class_rows = classes.get("classes", []) if isinstance(classes, dict) else []
    npc_rows = npcs.get("npcs", []) if isinstance(npcs, dict) else []
    vendor_rows = economy.get("vendors", []) if isinstance(economy, dict) else []
    recipe_rows = economy.get("recipes", []) if isinstance(economy, dict) else []
    mercenary_rows = economy.get("mercenaries", []) if isinstance(economy, dict) else []
    role_counts: dict[str, int] = {}
    for creature in creature_rows:
        if isinstance(creature, dict):
            role = str(creature.get("combat_role", "standard"))
            role_counts[role] = role_counts.get(role, 0) + 1
    locked_exit_count = sum(
        len(room.get("exit_requirements", {}))
        for room in rooms if isinstance(room, dict)
    )
    inventory = {
        "content_version": world.get("content_version"),
        "additive_from": world.get("additive_from", []),
        "counts": {
            "rooms": len(rooms),
            "items": len(items.get("items", [])) if isinstance(items, dict) else 0,
            "creatures": len(creatures.get("creatures", [])) if isinstance(creatures, dict) else 0,
            "npcs": len(npcs.get("npcs", [])) if isinstance(npcs, dict) else 0,
            "quests": len(quests),
            "quest_stages": sum(len(q.get("stages", [])) for q in quests if isinstance(q, dict)),
            "sovereignty_records": len(quests_doc.get("records", [])) if isinstance(quests_doc, dict) else 0,
            "rewards": len(rewards.get("rewards", [])) if isinstance(rewards, dict) else 0,
            "factions": len(classes.get("factions", [])) if isinstance(classes, dict) else 0,
            "classes": len(class_rows),
            "classes_with_signature_technique": sum(
                bool(row.get("technique_name")) for row in class_rows if isinstance(row, dict)
            ),
            "classes_with_passive_identity": sum(
                bool(row.get("passive_name")) for row in class_rows if isinstance(row, dict)
            ),
            "classes_with_exploration_identity": sum(
                bool(row.get("exploration_name")) for row in class_rows if isinstance(row, dict)
            ),
            "class_ability_branches": sum(
                len(row.get("ability_branches", [])) for row in class_rows if isinstance(row, dict)
            ),
            "vendors": len(vendor_rows),
            "recipes": len(recipe_rows),
            "mercenaries": len(mercenary_rows),
            "hazard_rooms": sum(bool(room.get("hazard_name")) for room in rooms if isinstance(room, dict)),
            "scheduled_npcs": sum(bool(row.get("schedule_rooms")) for row in npc_rows if isinstance(row, dict)),
            "enemy_combat_roles": role_counts,
            "story_locked_exit_count": locked_exit_count,
            "guided_start_steps": len(tutorial_steps),
        },
        "quest_arcs": sorted({str(q.get("arc_title")) for q in quests if isinstance(q, dict) and q.get("arc_title")}),
        "quest_ids": [q.get("id") for q in quests if isinstance(q, dict)],
        "economy": {
            "vendor_ids": [row.get("id") for row in vendor_rows if isinstance(row, dict)],
            "recipe_ids": [row.get("id") for row in recipe_rows if isinstance(row, dict)],
            "mercenary_ids": [row.get("id") for row in mercenary_rows if isinstance(row, dict)],
        },
    }
    topology = {
        "start_room": world.get("start_room"),
        "rooms": [
            {
                "id": room.get("id"),
                "name": room.get("name"),
                "region": room.get("region"),
                "exits": room.get("exits", {}),
                "facilities": room.get("facilities", []),
                "spawn_item_count": len(room.get("items", [])),
                "spawn_creature_count": len(room.get("creatures", [])),
                "hazard": {
                    "name": room.get("hazard_name"),
                    "damage": room.get("hazard_damage", 0),
                    "roundtime": room.get("hazard_roundtime", 0),
                    "mitigation_item_count": len(room.get("hazard_mitigation_items", [])),
                    "mitigation_class_count": len(room.get("hazard_mitigation_classes", [])),
                } if room.get("hazard_name") else None,
            }
            for room in rooms if isinstance(room, dict)
        ],
    }
    return inventory, topology, quest_index, tutorial_index


def _hud_contract(project_root: Path) -> dict[str, Any]:
    index = (project_root / "hud" / "index.html").read_text(encoding="utf-8", errors="replace")
    app = (project_root / "hud" / "app.js").read_text(encoding="utf-8", errors="replace")
    server = (project_root / "src" / "beta_earth" / "presentation" / "hud_server.py").read_text(encoding="utf-8", errors="replace")
    required_ids = (
        "directive-strip", "guide-companion", "story-card", "story-relationship-list",
        "technique-card", "technique-button", "ability-card", "ability-use-button",
        "hazard-banner", "economy-card", "credit-balance", "companion-summary",
        "sprawl-pulse", "sprawl-pulse-grid", "support-export-button",
        "support-export-status", "command-input",
    )
    return {
        "required_element_ids": {element_id: f'id="{element_id}"' in index for element_id in required_ids},
        "same_origin_token_header": '"X-Beta-Earth-Token": token' in app,
        "unsafe_inner_html_present": ".innerHTML" in app,
        "support_export_endpoint_client": 'api("/api/diagnostics/export"' in app,
        "support_export_endpoint_server": '"/api/diagnostics/export"' in server,
        "loopback_only": 'LOOPBACK_HOST = "127.0.0.1"' in server,
    }


def _release_integrity(project_root: Path) -> dict[str, Any]:
    manifest_path = project_root / "MANIFEST.json"
    checks_path = project_root / "diagnostics" / "RELEASE_CHECKS.json"
    manifest = _read_json(manifest_path, {})
    checks = _read_json(checks_path, {})
    verification_path = project_root.parent / f"{project_root.name}_RELEASE_VERIFICATION.json"
    verification = _read_json(verification_path, {})
    return {
        "manifest_present": manifest_path.is_file(),
        "manifest_sha256": _sha256(manifest_path) if manifest_path.is_file() else None,
        "manifest_version": manifest.get("version") if isinstance(manifest, dict) else None,
        "manifest_asset_count": len(manifest.get("assets", [])) if isinstance(manifest, dict) else 0,
        "release_checks_present": checks_path.is_file(),
        "release_checks_status": checks.get("status") if isinstance(checks, dict) else None,
        "release_checks_counts": checks.get("counts") if isinstance(checks, dict) else None,
        "exact_release_verification_present": verification_path.is_file(),
        "exact_release_status": verification.get("status") if isinstance(verification, dict) else None,
        "exact_release_artifact": verification.get("artifact") if isinstance(verification, dict) else None,
        "absolute_user_path_findings": _absolute_path_findings(project_root),
    }


def _source_inventory(project_root: Path) -> dict[str, Any]:
    roots = ("src", "content", "hud", "tools", "tests")
    records: list[dict[str, Any]] = []
    for root_name in roots:
        root = project_root / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(project_root).as_posix()
            data = path.read_bytes()
            records.append({"path": relative, "size_bytes": len(data), "sha256": _sha256_bytes(data)})
    return {
        "file_count": len(records),
        "total_bytes": sum(record["size_bytes"] for record in records),
        "roots": list(roots),
        "files": records,
    }


def _zip_comment(version: str) -> bytes:
    return json.dumps({
        "asset_id": "BE-NEXT-EXPORT20",
        "project_slug": PROJECT_SLUG,
        "version": version,
        "status": "diagnostic",
        "rights_holder": "Gateway Information Group LLC",
        "copyright_notice": NOTICE,
        "rights_scope": "first-party diagnostic artifact",
        "license": LICENSE,
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def export_support_bundle(
    project_root: Path,
    destination: Path | None = None,
    *,
    runtime_database: Path | None = None,
    telemetry_dir: Path | None = None,
) -> tuple[int, list[str], Path]:
    root = project_root.resolve()
    destination = (destination or default_destination(root)).resolve()
    runtime_path = (runtime_database or (root / "runtime" / "beta_earth.sqlite3")).resolve()
    if destination.suffix.casefold() != ".zip":
        raise ValueError("diagnostic output must use a .zip suffix")
    destination.parent.mkdir(parents=True, exist_ok=True)
    version = _version(root)
    run_id = f"BE-DIAG-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    errors: list[str] = []

    try:
        content_inventory, topology, quest_index, tutorial_index = _content_collect(root)
    except Exception as exc:  # fail closed into a report rather than losing the bundle
        errors.append(f"content:{type(exc).__name__}")
        content_inventory, topology, quest_index, tutorial_index = ({"collector_error": str(exc)}, {}, {}, {})
    try:
        runtime, states, progression, events, signals = _database_collect(
            runtime_path, quest_index, tutorial_index
        )
    except Exception as exc:
        errors.append(f"runtime:{type(exc).__name__}")
        runtime, states, progression, events, signals = (
            {"present": True, "collector_error": f"{type(exc).__name__}: {exc}"},
            [], [], {"collector_error": type(exc).__name__},
            f"Runtime collector failed: {type(exc).__name__}.\n",
        )
    startup_root = (telemetry_dir or runtime_path.parent).resolve()
    startup, startup_signals = _startup_collect(root, startup_root)
    runtime["startup"] = startup
    if startup_signals:
        signals = signals.rstrip() + "\n\n" + startup_signals.rstrip() + "\n"
    sbom = _read_json(root / "SBOM.json", {})
    if not isinstance(sbom, dict):
        sbom = {}
    generated_at = datetime.now(timezone.utc).isoformat()
    environment = {
        "generated_at_utc": generated_at,
        "app_version": version,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "dependency_mode": "Python standard library only",
        "compact_sbom": {
            "status": sbom.get("status"),
            "runtime_dependency_declaration": sbom.get("runtime_dependency_declaration"),
            "bundled_third_party_component_count": len(sbom.get("bundled_third_party_components", [])) if isinstance(sbom.get("bundled_third_party_components"), list) else None,
        },
        "machine_profile": {
            "canonical_id": startup.get("machine_id"),
            "source": startup.get("machine_profile_source"),
            "computer_awareness_only": True,
        },
        "project_root": "<PROJECT_ROOT>",
        "output_filename": destination.name,
        "runtime_database": (
            runtime_path.relative_to(root).as_posix()
            if runtime_path.is_relative_to(root)
            else "<CUSTOM_RUNTIME>/beta_earth.sqlite3"
        ),
    }
    security = _security_and_drive_summary(root)
    release = _release_integrity(root)
    hud = _hud_contract(root)
    source = _source_inventory(root)
    readme = (
        "BETA EARTH SUPPORT EXPORT — UPLOAD THIS ZIP\n"
        "================================================\n"
        f"Version: {version}\n"
        f"Generated: {generated_at}\n\n"
        "Upload the ZIP containing this file back into the Beta Earth development chat.\n"
        "The bundle is regenerated from the latest local game state and project files.\n"
        "It contains no character names, player keys, raw commands, or event payloads.\n"
        "Do not extract or edit the ZIP before uploading; keep its .sha256.txt beside it.\n\n"
        f"Rights: {NOTICE}\n"
    ).encode("utf-8")

    generated_entries: list[tuple[str, bytes]] = [
        ("READ_ME_FIRST.txt", readme),
        ("generated/environment.json", _json_bytes(environment)),
        ("generated/runtime_health.json", _json_bytes(runtime)),
        ("generated/player_state_summary.json", _json_bytes({"slots": states, "privacy": security["redaction"]})),
        ("generated/story_tutorial_diagnostics.json", _json_bytes({"slots": progression, "finding_count": sum(len(item.get("findings", [])) for item in progression)})),
        ("generated/event_diagnostics.json", _json_bytes(events)),
        ("generated/content_inventory.json", _json_bytes(content_inventory)),
        ("generated/world_topology.json", _json_bytes(topology)),
        ("generated/hud_contract.json", _json_bytes(hud)),
        ("generated/release_integrity.json", _json_bytes(release)),
        ("generated/source_inventory.json", _json_bytes(source)),
        ("generated/recent_error_signals.txt", signals.encode("utf-8")),
    ]
    project_entries: list[tuple[str, bytes]] = []
    missing: list[str] = []
    for relative in PROJECT_FILES:
        path = root / relative
        if path.is_file():
            project_entries.append((f"project/{relative}", path.read_bytes()))
        else:
            missing.append(relative)
            placeholder = {
                "status": "not-generated",
                "path": relative,
                "note": "This optional release receipt was not present when Export20 was refreshed.",
            }
            project_entries.append((f"project/{relative}", _json_bytes(placeholder)))
    non_summary = [*generated_entries, *project_entries]
    # SUMMARY + 12 generated/readme + 7 project files = exactly 20 in a complete release.
    if len(non_summary) > MAX_ITEMS - 1:
        raise RuntimeError("diagnostic design exceeds the 20-file contract")
    security_summary = _security_and_drive_summary(root)
    summary = {
        "asset_id": "BE-NEXT-EXPORT20",
        "title": "Beta Earth Live Support Export20",
        "purpose": "Current, bounded evidence for diagnosis, recovery, balancing, HUD review, and narrative progression review",
        "project_slug": PROJECT_SLUG,
        "version": version,
        "status": "complete" if not errors else "partial",
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "time_trace": {
            "started_at_utc": generated_at,
            "terminal_status": "complete" if not errors else "partial",
            "clock_sources": ["UTC wall clock", "monotonic duration clock"],
        },
        "mode": "live read-only aggregation; no state migration, repair, network action, or source mutation",
        "self_updating_contract": {
            "stable_output": f"{DEFAULT_EXPORT_DIRECTORY}/{destination.name}",
            "refresh_triggers": ["HUD session open", "major checkpoint or idle refresh", "HUD Refresh Support Export button", "BetaEarthSovereignty_ExportDiagnostics.bat"],
            "atomic_replacement": True,
        },
        "diagnostic_areas": [
            "environment", "startup preflight/log/failure", "runtime integrity", "anonymized player progression", "level 1-10 and level 11-20 campaign calibration", "story/tutorial stalls",
            "class specialization", "credits and bounded companion", "first-contact and expedition state",
            "event patterns", "content/economy inventory", "hazards and world topology", "NPC schedules",
            "HUD/API contract", "release integrity", "source hashes", "recent error/recovery signals", "recovery documentation",
        ],
        "privacy": security_summary,
        "security_and_transfer": security_summary,
        "launch_and_portability": {
            "launcher": "BetaEarthSovereignty.bat",
            "launcher_present": (root / "BetaEarthSovereignty.bat").is_file(),
            "project_root": "<PROJECT_ROOT>",
            "default_runtime": "runtime/beta_earth.sqlite3",
            "development_computer_awareness_layer": "remove before public distribution",
            "cross_machine_launch_blocking": False,
            "support_export": f"{DEFAULT_EXPORT_DIRECTORY}/{destination.name}",
            "stale_absolute_path_files": release.get("absolute_user_path_findings", []),
            "preflight_status": startup.get("preflight_status"),
            "machine_id": startup.get("machine_id"),
            "computer_awareness_only": True,
            "repair": "BetaEarthSovereignty.bat --repair",
            "self_test": "BetaEarthSovereignty.bat --self-test",
            "single_instance_guard": False,
            "parallel_hud_launches": True,
            "computer_recognition_role": "labels, local defaults, diagnostics, and support exports only",
        },
        "rights_holder": "Gateway Information Group LLC",
        "copyright_year": 2026,
        "copyright_notice": NOTICE,
        "rights_notice": NOTICE,
        "rights_scope": "first-party diagnostic artifact",
        "license": LICENSE,
        "collector_errors": errors,
        "missing_retained_files": missing,
        "entry_count": len(non_summary) + 1,
        "item_limit": MAX_ITEMS,
        "uncompressed_size_limit_bytes": MAX_TOTAL_UNCOMPRESSED_BYTES,
        "checksum_sidecar": destination.name + ".sha256.txt",
        "recovery": {
            "guided_start": "Run GUIDE SYNC. The current build remembers valid tutorial actions performed out of order.",
            "startup": "Run BetaEarthSovereignty.bat --self-test. If blocked, run BetaEarthSovereignty.bat --repair, then refresh Export20.",
            "rollback": "Re-extract the exact verified release ZIP; restore only a closed, schema-compatible runtime backup.",
        },
    }
    entries = [("SUMMARY.json", _json_bytes(summary)), *non_summary]
    total_uncompressed = sum(len(data) for _, data in entries)
    if len(entries) > MAX_ITEMS:
        raise RuntimeError("Export20 item limit exceeded")
    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise RuntimeError("Export20 uncompressed size limit exceeded")

    staging = destination.with_name(f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    sidecar = destination.with_suffix(destination.suffix + ".sha256.txt")
    staging_sidecar = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.comment = _zip_comment(version)
            for name, data in entries:
                archive.writestr(name, data)
        with zipfile.ZipFile(staging, "r") as archive:
            if archive.testzip() is not None:
                raise RuntimeError("Export20 CRC validation failed")
            if len(archive.infolist()) != len(entries):
                raise RuntimeError("Export20 entry count changed during staging")
        digest = _sha256(staging)
        staging_sidecar.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
        os.replace(staging, destination)
        os.replace(staging_sidecar, sidecar)
    finally:
        for path in (staging, staging_sidecar):
            if path.exists():
                path.unlink()
    return len(entries), errors, destination
