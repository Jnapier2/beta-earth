from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.support import (
    PredictableRandom,
    ScriptedRandom,
    complete_foundation,
    load_additive_test_catalog,
    load_test_catalog,
)

from beta_earth.application.engine import GameEngine
from beta_earth.application.service import GameApplication
from beta_earth.domain.clock import ManualClock
from beta_earth.domain.events import DomainEvent
from beta_earth.domain.model import GameState, ItemState
from beta_earth.infrastructure.sqlite_store import (
    DATABASE_SCHEMA_VERSION,
    InMemoryStateStore,
    SQLiteStateStore,
    StoreConflict,
)


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state.sqlite3"
        self.store = SQLiteStateStore(self.path)
        self.clock = ManualClock()
        self.engine = GameEngine(load_test_catalog(), self.clock, PredictableRandom())

    def _raw_schema_seven_snapshot(self, name: str) -> dict[str, object]:
        current = self.engine.new_game(name).to_dict()
        character = current["character"]
        assert isinstance(character, dict)
        return {
            "schema_version": 7,
            "revision": 1,
            "turn": current["turn"],
            "content_version": current["content_version"],
            "character": {
                "key": character["key"],
                "name": character["name"],
                "room_id": character["room_id"],
            },
            "room_items": current["room_items"],
            "creatures": current["creatures"],
        }

    def test_sqlite_round_trip_preserves_state(self) -> None:
        state = self.engine.new_game("Persistent One")
        self.store.save(state, (DomainEvent("character.created"),))
        state.character.inventory.append(ItemState("test:token", "transit_token"))
        self.store.save(state, (DomainEvent("item.taken", {"item_id": "transit_token"}),))
        restored = self.store.load("persistent one")
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertIn(
            "transit_token",
            [item.definition_id for item in restored.character.inventory],
        )
        self.assertEqual(2, restored.revision)
        self.assertEqual(2, self.store.event_count("persistent one"))

    def test_corrupt_row_cannot_redirect_a_save_to_another_character(self) -> None:
        alice = self.engine.new_game("Alice")
        bob = self.engine.new_game("Bob")
        self.store.save(alice, ())
        self.store.save(bob, ())
        corrupt = alice.to_dict()
        corrupt["character"]["key"] = "bob"
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "UPDATE characters SET state_json = ? WHERE player_key = ?",
                (json.dumps(corrupt), "alice"),
            )

        with self.assertRaisesRegex(RuntimeError, "player key"):
            self.store.load("alice")

        untouched = self.store.load("bob")
        self.assertIsNotNone(untouched)
        assert untouched is not None
        self.assertEqual("bob", untouched.character.key)
        self.assertEqual(1, untouched.revision)

    def test_database_schema_one_is_upgraded_with_migration_history(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy-schema-one.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as connection, connection:
            connection.execute("PRAGMA user_version = 1")
        SQLiteStateStore(legacy_path)
        with closing(sqlite3.connect(legacy_path)) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            history_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'state_migration_history'
                """
            ).fetchone()
        self.assertEqual(DATABASE_SCHEMA_VERSION, version)
        self.assertIsNotNone(history_table)

    def test_optimistic_conflict_is_detected(self) -> None:
        state = self.engine.new_game("Concurrent One")
        self.store.save(state, ())
        first = self.store.load("concurrent one")
        stale = self.store.load("concurrent one")
        assert first is not None and stale is not None
        first.flags.add("newer")
        self.store.save(first, ())
        stale.flags.add("stale")
        with self.assertRaises(StoreConflict):
            self.store.save(stale, ())
        self.assertEqual(1, stale.revision)

    def test_application_autosaves_meaningful_action(self) -> None:
        app = GameApplication(self.engine, self.store)
        session = app.open_session("Auto Save")
        complete_foundation(session)
        created_revision = session.state.revision
        result = session.execute("get token")
        self.assertTrue(result.changed)
        restored = self.store.load("auto save")
        assert restored is not None
        self.assertEqual(created_revision + 1, restored.revision)
        self.assertIn(
            "transit_token",
            [item.definition_id for item in restored.character.inventory],
        )

    def test_queued_action_survives_restart_and_executes_once(self) -> None:
        app = GameApplication(self.engine, self.store)
        session = app.open_session("Durable Queue")
        complete_foundation(session)
        session.execute("east")
        session.execute("queue attack mite")
        durable = self.store.load("durable queue")
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertIsNotNone(durable.queued_action)

        self.clock.advance(1)
        resumed = app.open_session("Durable Queue")
        result = resumed.execute("look")
        self.assertIn("[Queue] ATTACK", result.text)
        after = self.store.load("durable queue")
        self.assertIsNotNone(after)
        assert after is not None
        self.assertIsNone(after.queued_action)
        health = after.creatures["drill_gallery"][0].health

        resumed.execute("look")
        final = self.store.load("durable queue")
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(health, final.creatures["drill_gallery"][0].health)

    def test_incapacitation_survives_restart_and_recovers_once(self) -> None:
        impact_engine = GameEngine(
            load_test_catalog(),
            self.clock,
            ScriptedRandom([50, 100, 100, 100, 100, 100, 4]),
        )
        app = GameApplication(impact_engine, self.store)
        session = app.open_session("Durable Incapacitation")
        complete_foundation(session)
        session.state.character.room_id = "drill_gallery"
        session.state.visited_rooms.add("drill_gallery")
        session.state.character.health = 1
        session.state.character.experience.field_pool = 20
        session.state.character.companion_id = None
        downed = session.execute("attack mite")
        self.assertIn("incapacitated", downed.text)

        resumed = app.open_session("Durable Incapacitation")
        self.assertIsNotNone(resumed.state.incapacitation)
        self.assertEqual("drill_gallery", resumed.state.character.room_id)
        signaled = resumed.execute("signal")
        self.assertTrue(signaled.changed)

        signaled_restart = app.open_session("Durable Incapacitation")
        self.assertTrue(signaled_restart.state.incapacitation.help_requested)
        self.clock.advance(10)
        recovered = signaled_restart.execute("recover")
        self.assertIn("recovery beacon", recovered.text)
        self.assertIsNone(signaled_restart.state.incapacitation)
        durable = self.store.load("durable incapacitation")
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertIsNone(durable.incapacitation)
        revision = durable.revision

        duplicate = signaled_restart.execute("recover")
        self.assertFalse(duplicate.changed)
        self.assertIn("not incapacitated", duplicate.text)
        self.assertEqual(revision, signaled_restart.state.revision)

    def test_read_only_command_does_not_churn_revision(self) -> None:
        app = GameApplication(self.engine, self.store)
        session = app.open_session("Quiet Reader")
        complete_foundation(session)
        revision = session.state.revision
        pulse_checkpoint = session.state.character.experience.last_pulse_at
        self.clock.advance(15)
        result = session.execute("look")
        self.assertFalse(result.changed)
        self.assertEqual(revision, session.state.revision)
        self.assertEqual(
            pulse_checkpoint,
            session.state.character.experience.last_pulse_at,
        )

    def test_rest_state_and_due_healing_survive_restart(self) -> None:
        app = GameApplication(self.engine, self.store)
        session = app.open_session("Durable Rest")
        complete_foundation(session)
        session.state.character.health = 30
        started = session.execute("rest")
        self.assertTrue(started.changed)
        self.clock.advance(15)
        resumed = app.open_session("Durable Rest")
        self.assertTrue(resumed.state.character.resting)
        healed = resumed.execute("look")
        self.assertIn("Rest restores 2 health", healed.text)
        self.assertEqual(32, resumed.state.character.health)
        durable = self.store.load("durable rest")
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertEqual(32, durable.character.health)
        self.assertTrue(durable.character.resting)

    def test_social_event_is_persisted(self) -> None:
        app = GameApplication(self.engine, self.store)
        session = app.open_session("Social One")
        complete_foundation(session)
        revision = session.state.revision
        result = session.execute("say I'm ready")
        self.assertTrue(result.changed)
        self.assertEqual(revision + 1, session.state.revision)
        self.assertGreaterEqual(self.store.event_count("social one"), 2)

    def test_failed_save_leaves_live_session_unchanged(self) -> None:
        class FailingStore:
            def load(self, player_key):
                return None

            def save(self, state, events):
                raise OSError("simulated disk failure")

        state = self.engine.new_game("Rollback One")
        from beta_earth.application.service import GameSession

        session = GameSession(self.engine, FailingStore(), state)
        before = session.state.to_dict()
        with self.assertRaisesRegex(OSError, "disk failure"):
            session.execute("get token")
        self.assertEqual(before, session.state.to_dict())

    def test_declared_content_migration_preserves_pre_migration_snapshot(self) -> None:
        original_app = GameApplication(self.engine, self.store)
        original = original_app.open_session("Migration One")
        original_revision = original.state.revision

        upgraded_engine = GameEngine(
            load_additive_test_catalog(), self.clock, PredictableRandom()
        )
        migrated = GameApplication(upgraded_engine, self.store).open_session(
            "Migration One"
        )

        self.assertEqual("0.51.2", migrated.state.content_version)
        self.assertEqual(original_revision + 1, migrated.state.revision)
        self.assertEqual(1, self.store.migration_history_count("migration one"))
        snapshot = self.store.load_latest_migration_snapshot("migration one")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual("0.51.1", snapshot.content_version)
        self.assertEqual(original_revision, snapshot.revision)
        durable = self.store.load("migration one")
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertEqual("0.51.2", durable.content_version)
        self.assertEqual(
            ["spawn:item:relay-upgrade-token"],
            [item.instance_id for item in durable.room_items["relay_overlook"]],
        )

    def test_sqlite_migrates_normalized_schema_seven_and_preserves_raw_json(
        self,
    ) -> None:
        raw = self._raw_schema_seven_snapshot("Schema Seven SQLite")
        raw_json = json.dumps(raw, ensure_ascii=False, indent=2)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO characters
                    (player_key, display_name, revision, state_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "schema seven sqlite",
                    "Schema Seven SQLite",
                    1,
                    raw_json,
                ),
            )

        previous = self.store.load("schema seven sqlite")
        self.assertIsNotNone(previous)
        assert previous is not None
        self.assertNotEqual(raw, previous.to_dict())
        migrated = GameState.from_dict(previous.to_dict())
        migrated.content_version = "0.36.1"

        revision = self.store.save_migration(previous, migrated, ())

        self.assertEqual(2, revision)
        with closing(sqlite3.connect(self.path)) as connection:
            history_json = str(
                connection.execute(
                    """
                    SELECT state_json
                    FROM state_migration_history
                    WHERE player_key = ?
                    """,
                    ("schema seven sqlite",),
                ).fetchone()[0]
            )
        self.assertEqual(raw_json, history_json)

    def test_memory_store_migrates_normalized_schema_seven_and_preserves_raw(
        self,
    ) -> None:
        store = InMemoryStateStore()
        raw = self._raw_schema_seven_snapshot("Schema Seven Memory")
        store._states["schema seven memory"] = json.loads(json.dumps(raw))

        previous = store.load("schema seven memory")
        self.assertIsNotNone(previous)
        assert previous is not None
        self.assertNotEqual(raw, previous.to_dict())
        migrated = GameState.from_dict(previous.to_dict())
        migrated.content_version = "0.36.1"

        revision = store.save_migration(previous, migrated, ())

        self.assertEqual(2, revision)
        self.assertEqual(
            ("schema seven memory", raw),
            store._migration_history[-1],
        )

    def test_undeclared_content_migration_leaves_durable_state_unchanged(self) -> None:
        original = GameApplication(self.engine, self.store).open_session(
            "Migration Guard"
        )
        before = original.state.to_dict()
        incompatible_engine = GameEngine(
            load_additive_test_catalog(declared=False),
            self.clock,
            PredictableRandom(),
        )
        with self.assertRaisesRegex(ValueError, "no declared additive migration"):
            GameApplication(incompatible_engine, self.store).open_session(
                "Migration Guard"
            )
        durable = self.store.load("migration guard")
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertEqual(before, durable.to_dict())
        self.assertEqual(0, self.store.migration_history_count("migration guard"))

    def test_migration_transaction_rolls_back_history_and_state_on_failure(self) -> None:
        state = self.engine.new_game("Migration Rollback")
        self.store.save(state, (DomainEvent("character.created"),))
        previous = self.store.load("migration rollback")
        self.assertIsNotNone(previous)
        assert previous is not None
        migrated = GameState.from_dict(previous.to_dict())
        upgraded_engine = GameEngine(
            load_additive_test_catalog(), self.clock, PredictableRandom()
        )
        events = upgraded_engine.reconcile_state(migrated)
        upgraded_engine.validate_state(migrated)

        class FailingMigrationStore(SQLiteStateStore):
            @staticmethod
            def _insert_events(connection, rows):
                raise OSError("simulated event journal failure")

        failing_store = FailingMigrationStore(self.path)
        with self.assertRaisesRegex(OSError, "event journal failure"):
            failing_store.save_migration(previous, migrated, events)

        durable = self.store.load("migration rollback")
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertEqual(previous.to_dict(), durable.to_dict())
        self.assertEqual(previous.revision, migrated.revision)
        self.assertEqual(0, self.store.migration_history_count("migration rollback"))


    def test_parallel_sessions_reload_and_retry_once_without_overwrite(self) -> None:
        app = GameApplication(self.engine, self.store)
        first = app.open_session("Parallel Sovereign")
        second = app.open_session("Parallel Sovereign")

        first_result = first.execute("build class soldier")
        self.assertTrue(first_result.changed)
        second_result = second.execute("build class sniper")

        self.assertTrue(second_result.changed)
        self.assertIn("safely applied to the newest save", second_result.text)
        durable = self.store.load("parallel sovereign")
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertEqual("sniper", durable.character.build.class_id)
        self.assertEqual(second.state.revision, durable.revision)


if __name__ == "__main__":
    unittest.main()
