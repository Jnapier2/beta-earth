"""Transactional SQLite snapshot store with optimistic revisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from typing import Iterable

from beta_earth.application.service import StateConflict
from beta_earth.domain.events import DomainEvent
from beta_earth.domain.model import GameState


DATABASE_SCHEMA_VERSION = 3


class StoreConflict(StateConflict):
    """A newer state revision exists for the same character."""


def _normalized_state_snapshot(
    value: dict[str, object],
) -> dict[str, object]:
    """Return the current-schema meaning of a readable state snapshot."""
    return GameState.from_dict(value).to_dict()


class SQLiteStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > DATABASE_SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current} is newer than supported {DATABASE_SCHEMA_VERSION}"
                )
            if current not in {0, 1, 2, DATABASE_SCHEMA_VERSION}:
                raise RuntimeError(f"database schema {current} cannot be upgraded safely")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS characters (
                    player_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS domain_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_key TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    event_kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (player_key) REFERENCES characters(player_key)
                );
                CREATE INDEX IF NOT EXISTS ix_domain_events_player_revision
                    ON domain_events(player_key, revision);
                CREATE TABLE IF NOT EXISTS state_migration_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_key TEXT NOT NULL,
                    source_revision INTEGER NOT NULL CHECK (source_revision > 0),
                    from_content_version TEXT NOT NULL,
                    to_content_version TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (player_key) REFERENCES characters(player_key)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS
                    ux_state_migration_history_transition
                    ON state_migration_history(
                        player_key, source_revision, to_content_version
                    );
                CREATE TABLE IF NOT EXISTS state_schema_migration_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_key TEXT NOT NULL,
                    source_revision INTEGER NOT NULL CHECK (source_revision > 0),
                    from_schema INTEGER NOT NULL,
                    to_schema INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (player_key) REFERENCES characters(player_key)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS
                    ux_state_schema_migration_history_transition
                    ON state_schema_migration_history(
                        player_key, source_revision, to_schema
                    );
                """
            )
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")

    def load(self, player_key: str) -> GameState | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT display_name, revision, state_json
                FROM characters
                WHERE player_key = ?
                """,
                (player_key,),
            ).fetchone()
        if row is None:
            return None
        state = GameState.from_dict(json.loads(str(row[2])))
        if state.character.key != player_key:
            raise RuntimeError("saved state player key does not match its database row")
        if state.character.name != str(row[0]):
            raise RuntimeError("saved state display name does not match its database row")
        if state.revision != int(row[1]):
            raise RuntimeError("saved state revision does not match its database row")
        return state

    @staticmethod
    def _state_json(state: GameState) -> str:
        return json.dumps(
            state.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _event_rows(
        player_key: str,
        revision: int,
        events: Iterable[DomainEvent],
    ) -> list[tuple[str, int, str, str]]:
        return [
            (
                player_key,
                revision,
                event.kind,
                json.dumps(
                    event.payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            for event in events
        ]

    @staticmethod
    def _insert_events(
        connection: sqlite3.Connection,
        rows: list[tuple[str, int, str, str]],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO domain_events
                (player_key, revision, event_kind, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )

    def save(self, state: GameState, events: Iterable[DomainEvent]) -> int:
        previous_revision = state.revision
        new_revision = previous_revision + 1
        state.revision = new_revision
        try:
            state_json = self._state_json(state)
            event_rows = self._event_rows(
                state.character.key, new_revision, events
            )
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if previous_revision == 0:
                    try:
                        connection.execute(
                            """
                            INSERT INTO characters
                                (player_key, display_name, revision, state_json)
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                state.character.key,
                                state.character.name,
                                new_revision,
                                state_json,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise StoreConflict(
                            f"character {state.character.key!r} already exists"
                        ) from exc
                else:
                    cursor = connection.execute(
                        """
                        UPDATE characters
                        SET display_name = ?, revision = ?, state_json = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE player_key = ? AND revision = ?
                        """,
                        (
                            state.character.name,
                            new_revision,
                            state_json,
                            state.character.key,
                            previous_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise StoreConflict(
                            f"character {state.character.key!r} changed in another session"
                        )
                self._insert_events(connection, event_rows)
        except Exception:
            state.revision = previous_revision
            raise
        return new_revision

    def save_schema_migration(
        self,
        state: GameState,
        from_schema: int,
        events: Iterable[DomainEvent],
    ) -> int:
        """Atomically preserve the raw old-schema snapshot and persist the current schema."""

        if state.revision <= 0:
            raise ValueError("only a persisted state can receive a schema migration")
        if from_schema >= state.schema_version:
            raise ValueError("schema migration source must be older than the current schema")

        previous_revision = state.revision
        new_revision = previous_revision + 1
        state.revision = new_revision
        state.source_schema_version = state.schema_version
        try:
            migrated_json = self._state_json(state)
            event_rows = self._event_rows(state.character.key, new_revision, events)
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT state_json
                    FROM characters
                    WHERE player_key = ? AND revision = ?
                    """,
                    (state.character.key, previous_revision),
                ).fetchone()
                if row is None:
                    raise StoreConflict(
                        f"character {state.character.key!r} changed in another session"
                    )
                saved_json = str(row[0])
                saved_snapshot = json.loads(saved_json)
                if not isinstance(saved_snapshot, dict):
                    raise ValueError("saved state snapshot must be a JSON object")
                durable_schema = int(saved_snapshot.get("schema_version", 0))
                if durable_schema != from_schema:
                    raise StoreConflict(
                        "schema migration source does not match the durable schema"
                    )
                # Compare the durable legacy snapshot *after* applying the same
                # deterministic migration registry. Raw older-schema JSON need not equal current JSON because
                # sequential migrations can add or normalize foundation state.
                durable_migrated = GameState.from_dict(saved_snapshot).to_dict()
                requested_migrated = state.to_dict()
                durable_migrated["revision"] = previous_revision
                requested_migrated["revision"] = previous_revision
                if _normalized_state_snapshot(durable_migrated) != _normalized_state_snapshot(
                    requested_migrated
                ):
                    raise StoreConflict(
                        "schema migration source does not match the durable snapshot"
                    )
                connection.execute(
                    """
                    INSERT INTO state_schema_migration_history
                        (player_key, source_revision, from_schema, to_schema, state_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        state.character.key,
                        previous_revision,
                        from_schema,
                        state.schema_version,
                        saved_json,
                    ),
                )
                cursor = connection.execute(
                    """
                    UPDATE characters
                    SET display_name = ?, revision = ?, state_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE player_key = ? AND revision = ?
                    """,
                    (
                        state.character.name,
                        new_revision,
                        migrated_json,
                        state.character.key,
                        previous_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StoreConflict(
                        f"character {state.character.key!r} changed in another session"
                    )
                self._insert_events(connection, event_rows)
        except Exception:
            state.revision = previous_revision
            state.source_schema_version = from_schema
            raise
        return new_revision

    def save_migration(
        self,
        previous_state: GameState,
        migrated_state: GameState,
        events: Iterable[DomainEvent],
    ) -> int:
        """Atomically preserve the exact old snapshot before replacing it."""
        if previous_state.character.key != migrated_state.character.key:
            raise ValueError("migration states belong to different characters")
        if previous_state.revision <= 0:
            raise ValueError("only a persisted state can be migrated")
        if migrated_state.revision != previous_state.revision:
            raise StoreConflict("migration state revision does not match its source")
        if previous_state.content_version == migrated_state.content_version:
            raise ValueError("migration must change the content version")

        previous_revision = previous_state.revision
        new_revision = previous_revision + 1
        migrated_state.revision = new_revision
        try:
            migrated_json = self._state_json(migrated_state)
            event_rows = self._event_rows(
                migrated_state.character.key, new_revision, events
            )
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT state_json
                    FROM characters
                    WHERE player_key = ? AND revision = ?
                    """,
                    (previous_state.character.key, previous_revision),
                ).fetchone()
                if row is None:
                    raise StoreConflict(
                        f"character {previous_state.character.key!r} changed in another session"
                    )
                saved_json = str(row[0])
                saved_snapshot = json.loads(saved_json)
                if not isinstance(saved_snapshot, dict):
                    raise ValueError("saved state snapshot must be a JSON object")
                if _normalized_state_snapshot(
                    saved_snapshot
                ) != _normalized_state_snapshot(previous_state.to_dict()):
                    raise StoreConflict(
                        "migration source does not match the durable snapshot"
                    )
                connection.execute(
                    """
                    INSERT INTO state_migration_history
                        (player_key, source_revision, from_content_version,
                         to_content_version, state_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        previous_state.character.key,
                        previous_revision,
                        previous_state.content_version,
                        migrated_state.content_version,
                        saved_json,
                    ),
                )
                cursor = connection.execute(
                    """
                    UPDATE characters
                    SET display_name = ?, revision = ?, state_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE player_key = ? AND revision = ?
                    """,
                    (
                        migrated_state.character.name,
                        new_revision,
                        migrated_json,
                        migrated_state.character.key,
                        previous_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StoreConflict(
                        f"character {migrated_state.character.key!r} changed in another session"
                    )
                self._insert_events(connection, event_rows)
        except Exception:
            migrated_state.revision = previous_revision
            raise
        return new_revision

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _playtest_markdown(payload: dict[str, object]) -> bytes:
        timing = payload.get("timing") if isinstance(payload.get("timing"), dict) else {}
        campaign = payload.get("campaign") if isinstance(payload.get("campaign"), dict) else {}
        calibration = payload.get("calibration") if isinstance(payload.get("calibration"), dict) else {}
        sol = payload.get("sol") if isinstance(payload.get("sol"), dict) else {}
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        environment = payload.get("environment") if isinstance(payload.get("environment"), dict) else {}
        readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
        survey = payload.get("survey") if isinstance(payload.get("survey"), dict) else {}
        notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
        issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
        lines = [
            "# Beta Earth — Local Beginner Playtest Receipt",
            "",
            f"- Content version: `{payload.get('content_version', 'unknown')}`",
            f"- Generated UTC: `{payload.get('generated_at_utc', 'unspecified')}`",
            f"- Player label: `{(payload.get('player') or {}).get('local_label', 'unknown') if isinstance(payload.get('player'), dict) else 'unknown'}`",
            f"- Session ID: `{timing.get('session_id') or 'unspecified'}`",
            f"- Status: **{str(timing.get('status', 'unknown')).replace('_', ' ')}**",
            "",
            "## Session Profile",
            "",
            f"- Gameplay family: **{str(profile.get('family') or 'unassigned').replace('_', ' ')}**",
            f"- Class: **{profile.get('class_name') or profile.get('class_id') or 'unassigned'}**",
            f"- Mode: **{str(profile.get('mode') or 'standard').replace('_', ' ')}**",
            f"- Experience: **{str(profile.get('experience') or 'unspecified').replace('_', ' ')}**",
            f"- Recommended representative: **{profile.get('representative_class_name') or profile.get('representative_class_id') or 'unassigned'}**",
            f"- Class/family match: **{'Yes' if profile.get('class_matches_family') else 'No'}**",
            f"- Assistive tool: **{profile.get('assistive_tool') or 'Not recorded'}**",
            "",
            "## Environment",
            "",
            f"- OS family: **{environment.get('os_family', 'unknown')}**",
            f"- Native Windows launcher: **{'Yes' if environment.get('native_windows_launcher') else 'No'}**",
            f"- Launch surface: **{environment.get('launch_surface', 'unknown')}**",
            f"- Python: **{environment.get('python_version', 'unknown')}**",
            f"- Sanitized computer label: **{environment.get('computer_label', 'PC-LOCAL-UNASSIGNED')}**",
            "",
            "## Timing",
            "",
            f"- Active-window time: **{timing.get('active_text', '00:00:00')}**",
            f"- Idle-gap time: **{timing.get('idle_text', '00:00:00')}**",
            f"- Intentional paused time: **{timing.get('paused_text', '00:00:00')}**",
            f"- Wall time: **{timing.get('wall_text', '00:00:00')}**",
            f"- Commands recorded: **{timing.get('command_count', 0)}**",
            f"- Timing basis: {timing.get('timing_basis', 'Local timing only.')}",
            "",
            "## Campaign Result",
            "",
            f"- Complete: **{'Yes' if campaign.get('complete') else 'No'}**",
            f"- Level: **{campaign.get('level', 0)}/{campaign.get('target_level', 10)}**",
            f"- Competencies: **{campaign.get('competencies', 0)}/{campaign.get('competency_total', 10)}**",
            f"- Modeled progress: **{campaign.get('modeled_minutes', 0)}/{campaign.get('target_minutes', 120)} minutes**",
            f"- Active route: `{campaign.get('active_quest_id') or 'checkpoint'} / {campaign.get('active_stage_id') or campaign.get('checkpoint_id') or 'complete'}`",
            "",
            "## Sol Partnership",
            "",
            f"- Active companion at receipt: **{'Yes' if sol.get('active_companion') else 'No'}**",
            f"- Level: **{sol.get('level', 'n/a')}**",
            f"- Order: **{sol.get('order', 'n/a')}**",
            f"- Integrity: **{sol.get('health', 'n/a')}/{sol.get('max_health', 'n/a')}**",
            f"- Setups recorded: **{sol.get('setup_actions', calibration.get('companion_setups', 0))}**",
            f"- Player finishes reserved: **{sol.get('finish_reservations', calibration.get('companion_finish_reservations', 0))}**",
            f"- Player-converted finishes: **{sol.get('player_enabled_finishes', 0)}**",
            f"- Sol finishing strikes: **{sol.get('finishing_strikes', 0)}**",
            f"- Sol damage / intercepted: **{sol.get('damage_dealt', 0)} / {sol.get('damage_intercepted', 0)}**",
            "",
            "## Calibration",
            "",
            f"- Overall: **{calibration.get('status', 'unknown')}** — {calibration.get('summary', '')}",
            f"- Route/story: **{calibration.get('route_status', 'unknown')}** — {calibration.get('route_summary', '')}",
            f"- Combat: **{calibration.get('combat_status', 'unknown')}** — {calibration.get('combat_summary', '')}",
            f"- Successful/failed withdrawals: **{calibration.get('successful_withdrawals', 0)}/{calibration.get('failed_withdrawals', 0)}**",
            "",
            "## Readiness",
            "",
            f"- Receipt complete: **{'Yes' if readiness.get('receipt_complete') else 'No'}**",
            f"- Survey complete: **{'Yes' if readiness.get('survey_complete') else 'No'}**",
            f"- Blocking issues: **{readiness.get('blocking_issue_count', 0)}**",
            f"- Windows first-time standard cohort eligible: **{'Yes' if readiness.get('windows_first_time_standard_eligible') else 'No'}**",
            f"- Cohort decision: **{readiness.get('cohort_decision', 'not_eligible')}**",
            "",
            "## Structured Issues",
            "",
        ]
        if issues:
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                lines.append(
                    f"- **{str(issue.get('severity', 'unknown')).upper()}** · "
                    f"{str(issue.get('category', 'other')).replace('_', ' ')} · "
                    f"{issue.get('note', '')}"
                )
        else:
            lines.append("- No structured issues recorded.")
        lines.extend(("", "## Optional Survey", ""))
        if survey:
            lines.extend(
                f"- {str(key).replace('_', ' ').title()}: **{value}/5**"
                for key, value in sorted(survey.items())
            )
        else:
            lines.append("- No survey values recorded.")
        lines.extend(("", "## Optional Notes", ""))
        if notes:
            lines.extend(f"- {str(note)}" for note in notes)
        else:
            lines.append("- No notes recorded.")
        lines.extend(
            (
                "",
                "## Privacy Boundary",
                "",
                "This receipt is generated and stored locally. It contains no credentials, "
                "does not include raw command history or absolute user paths, and sends no analytics over the network.",
                "",
                "Copyright © 2026 Gateway Information Group LLC. All rights reserved.",
                "",
            )
        )
        return "\n".join(lines).encode("utf-8")

    def write_playtest_receipt(
        self,
        player_key: str,
        payload: dict[str, object],
    ) -> str:
        """Write paired local JSON/Markdown receipts with atomic hashes."""

        if not player_key.strip():
            raise ValueError("playtest receipt requires a player key")
        if payload.get("schema") not in {
            "beta-earth-local-playtest-receipt-v1",
            "beta-earth-local-playtest-receipt-v2",
            "beta-earth-local-playtest-receipt-v3",
        }:
            raise ValueError("playtest receipt payload is invalid")
        data = dict(payload)
        data.setdefault(
            "generated_at_utc",
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        )
        timing = data.get("timing")
        session_id = None
        if isinstance(timing, dict):
            raw_session_id = timing.get("session_id")
            if isinstance(raw_session_id, str) and raw_session_id.strip():
                session_id = raw_session_id.strip()
        if session_id is None:
            seed = (
                f"{player_key}|{data.get('generated_at_utc')}|"
                f"{data.get('content_version')}"
            ).encode("utf-8")
            session_id = hashlib.sha256(seed).hexdigest()[:16]
        safe_session = "".join(
            character
            for character in session_id
            if character.isalnum() or character in {"-", "_"}
        )[:48]
        if not safe_session:
            raise ValueError("playtest receipt session id is invalid")
        version = str(data.get("content_version", "unknown")).strip()
        safe_version = "".join(
            character
            for character in version
            if character.isalnum() or character in {".", "-", "_"}
        )[:32] or "unknown"
        export_dir = self.path.parent / "playtests"
        base = export_dir / f"BetaEarth_Playtest_{safe_session}_v{safe_version}"
        # Do not use Path.with_suffix here: semantic versions contain dots and
        # would lose their patch component (for example v0.36.0 -> v0.36.json).
        json_path = Path(f"{base}.json")
        markdown_path = Path(f"{base}.md")
        json_encoded = (
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        markdown_encoded = self._playtest_markdown(data)
        json_digest = hashlib.sha256(json_encoded).hexdigest()
        markdown_digest = hashlib.sha256(markdown_encoded).hexdigest()
        self._atomic_write(json_path, json_encoded)
        self._atomic_write(markdown_path, markdown_encoded)
        self._atomic_write(
            json_path.with_suffix(".json.sha256.txt"),
            f"{json_digest}  {json_path.name}\n".encode("utf-8"),
        )
        self._atomic_write(
            markdown_path.with_suffix(".md.sha256.txt"),
            f"{markdown_digest}  {markdown_path.name}\n".encode("utf-8"),
        )
        latest_json = export_dir / "LATEST_PLAYTEST_RECEIPT.json"
        latest_md = export_dir / "LATEST_PLAYTEST_RECEIPT.md"
        self._atomic_write(latest_json, json_encoded)
        self._atomic_write(latest_md, markdown_encoded)
        self._atomic_write(
            latest_json.with_suffix(".json.sha256.txt"),
            f"{json_digest}  {latest_json.name}\n".encode("utf-8"),
        )
        self._atomic_write(
            latest_md.with_suffix(".md.sha256.txt"),
            f"{markdown_digest}  {latest_md.name}\n".encode("utf-8"),
        )
        try:
            return json_path.relative_to(self.path.parent.parent).as_posix()
        except ValueError:
            return json_path.as_posix()

    def event_count(self, player_key: str) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM domain_events WHERE player_key = ?",
                    (player_key,),
                ).fetchone()[0]
            )

    def schema_migration_history_count(self, player_key: str) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM state_schema_migration_history WHERE player_key = ?",
                    (player_key,),
                ).fetchone()[0]
            )

    def migration_history_count(self, player_key: str) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM state_migration_history
                    WHERE player_key = ?
                    """,
                    (player_key,),
                ).fetchone()[0]
            )

    def load_latest_migration_snapshot(self, player_key: str) -> GameState | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT state_json
                FROM state_migration_history
                WHERE player_key = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (player_key,),
            ).fetchone()
        if row is None:
            return None
        state = GameState.from_dict(json.loads(str(row[0])))
        if state.character.key != player_key:
            raise RuntimeError(
                "migration snapshot player key does not match its history row"
            )
        return state


class InMemoryStateStore:
    """Test adapter that preserves the same revision contract."""

    def __init__(self) -> None:
        self._states: dict[str, dict[str, object]] = {}
        self._migration_history: list[tuple[str, dict[str, object]]] = []
        self._schema_migration_history: list[tuple[str, dict[str, object], int, int]] = []
        self.events: list[DomainEvent] = []
        self.playtest_receipts: list[tuple[str, dict[str, object]]] = []

    def load(self, player_key: str) -> GameState | None:
        raw = self._states.get(player_key)
        if raw is None:
            return None
        state = GameState.from_dict(json.loads(json.dumps(raw)))
        if state.character.key != player_key:
            raise RuntimeError("saved state player key does not match its store key")
        return state

    def save(self, state: GameState, events: Iterable[DomainEvent]) -> int:
        existing = self._states.get(state.character.key)
        existing_revision = int(existing["revision"]) if existing else 0
        if existing_revision != state.revision:
            raise StoreConflict("state changed in another session")
        previous_revision = state.revision
        state.revision = previous_revision + 1
        try:
            serialized = json.loads(json.dumps(state.to_dict()))
            event_list = list(events)
        except Exception:
            state.revision = previous_revision
            raise
        self._states[state.character.key] = serialized
        self.events.extend(event_list)
        return state.revision

    def save_schema_migration(
        self,
        state: GameState,
        from_schema: int,
        events: Iterable[DomainEvent],
    ) -> int:
        existing = self._states.get(state.character.key)
        if existing is None or int(existing["revision"]) != state.revision:
            raise StoreConflict("state changed in another session")
        durable_schema = int(existing.get("schema_version", 0))
        if durable_schema != from_schema:
            raise StoreConflict("schema migration source does not match durable schema")
        if from_schema >= state.schema_version:
            raise ValueError("schema migration source must be older than current schema")
        previous_revision = state.revision
        state.revision = previous_revision + 1
        state.source_schema_version = state.schema_version
        try:
            serialized = json.loads(json.dumps(state.to_dict()))
            snapshot = json.loads(json.dumps(existing))
            event_list = list(events)
        except Exception:
            state.revision = previous_revision
            state.source_schema_version = from_schema
            raise
        self._schema_migration_history.append(
            (state.character.key, snapshot, from_schema, state.schema_version)
        )
        self._states[state.character.key] = serialized
        self.events.extend(event_list)
        return state.revision

    def save_migration(
        self,
        previous_state: GameState,
        migrated_state: GameState,
        events: Iterable[DomainEvent],
    ) -> int:
        if previous_state.character.key != migrated_state.character.key:
            raise ValueError("migration states belong to different characters")
        existing = self._states.get(previous_state.character.key)
        if existing is None or int(existing["revision"]) != previous_state.revision:
            raise StoreConflict("state changed in another session")
        if _normalized_state_snapshot(existing) != _normalized_state_snapshot(
            previous_state.to_dict()
        ):
            raise StoreConflict("migration source does not match the durable snapshot")
        if previous_state.content_version == migrated_state.content_version:
            raise ValueError("migration must change the content version")
        if migrated_state.revision != previous_state.revision:
            raise StoreConflict("migration state revision does not match its source")

        previous_revision = migrated_state.revision
        migrated_state.revision = previous_revision + 1
        try:
            serialized = json.loads(json.dumps(migrated_state.to_dict()))
            snapshot = json.loads(json.dumps(existing))
            event_list = list(events)
        except Exception:
            migrated_state.revision = previous_revision
            raise
        self._migration_history.append((previous_state.character.key, snapshot))
        self._states[migrated_state.character.key] = serialized
        self.events.extend(event_list)
        return migrated_state.revision

    def write_playtest_receipt(
        self,
        player_key: str,
        payload: dict[str, object],
    ) -> str:
        if not isinstance(payload, dict) or payload.get("schema") not in {
            "beta-earth-local-playtest-receipt-v1",
            "beta-earth-local-playtest-receipt-v2",
            "beta-earth-local-playtest-receipt-v3",
        }:
            raise ValueError("playtest receipt payload is invalid")
        copied = json.loads(json.dumps(payload))
        self.playtest_receipts.append((player_key, copied))
        return f"memory://playtests/{player_key}/{len(self.playtest_receipts)}.json"

    def schema_migration_history_count(self, player_key: str) -> int:
        return sum(key == player_key for key, _, _, _ in self._schema_migration_history)

    def migration_history_count(self, player_key: str) -> int:
        return sum(key == player_key for key, _ in self._migration_history)

    def load_latest_migration_snapshot(self, player_key: str) -> GameState | None:
        for key, snapshot in reversed(self._migration_history):
            if key == player_key:
                state = GameState.from_dict(json.loads(json.dumps(snapshot)))
                if state.character.key != player_key:
                    raise RuntimeError(
                        "migration snapshot player key does not match its history key"
                    )
                return state
        return None
