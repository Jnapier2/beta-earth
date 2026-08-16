"""v0.18.0 unrestricted multi-computer launch and recognition contracts.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from beta_earth import __version__
from beta_earth.application.engine import GameEngine
from beta_earth.application.service import GameApplication
from beta_earth.domain.clock import ManualClock
from beta_earth.infrastructure.sqlite_store import SQLiteStateStore
from beta_earth.infrastructure.startup_support import (
    REQUIRED_PROJECT_FILES,
    MachineContext,
    canonical_machine_id,
    detect_machine_profile,
    migrate_legacy_database,
    resolve_machine_context,
    run_preflight,
    write_startup_failure,
)
from beta_earth.presentation.hud_server import create_server
from tests.support import PROJECT_ROOT, PredictableRandom, load_test_catalog


class LaunchResilienceTests(unittest.TestCase):
    def _minimal_release_copy(self, destination: Path) -> Path:
        manifest = json.loads((PROJECT_ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
        for record in manifest["assets"]:
            if not record.get("package_managed", True):
                continue
            relative = record["path"]
            source = PROJECT_ROOT / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for relative in ("MANIFEST.json",):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT_ROOT / relative, target)
        return destination

    def test_release_and_content_versions_are_synchronized_at_0180(self) -> None:
        self.assertEqual("0.51.1", __version__)
        self.assertEqual(
            "0.51.1",
            (PROJECT_ROOT / "VERSION.txt").read_text(encoding="utf-8").strip(),
        )
        world = json.loads(
            (PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8")
        )
        self.assertEqual("0.51.1", world["content_version"])
        self.assertIn("0.17.0", world["additive_from"])

    def test_aliases_are_sanitized_and_recognition_only(self) -> None:
        self.assertEqual("PC-ALPHA-01", canonical_machine_id("ALPHA"))
        self.assertEqual("PC-ASCEND-02", canonical_machine_id("Ascend laptop"))
        self.assertEqual("PC-DEUSEX-03", canonical_machine_id("Raider"))
        self.assertEqual("PC-DEUSEX-03", canonical_machine_id("GE66"))
        self.assertIsNone(canonical_machine_id("auto"))
        with self.assertRaises(ValueError):
            canonical_machine_id("unknown-machine")

    def test_broad_family_detection_discards_raw_fingerprint(self) -> None:
        ascend = detect_machine_profile(
            family_facts={
                "hostname": ["private-host-name"],
                "model": ["ROG Strix G634JY"],
                "processor": ["13th Gen Intel Core i9-13980HX"],
            }
        )
        self.assertEqual("PC-ASCEND-02", ascend.canonical_id)
        self.assertIn("model-family:G634JY", ascend.evidence)
        self.assertNotIn("private-host-name", " ".join(ascend.evidence))

    def test_identity_persists_as_non_authoritative_recognition(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Beta Earth Recognition ") as temporary:
            identity_root = Path(temporary) / "identity"
            context = resolve_machine_context(
                PROJECT_ROOT,
                environment={"BETA_EARTH_LOCAL_CONFIG": str(identity_root)},
                persist=True,
                family_facts={
                    "hostname": ["private-label"],
                    "model": ["Crosshair VIII Hero"],
                    "processor": ["AMD Ryzen 9 5950X"],
                },
            )
            self.assertEqual("PC-ALPHA-01", context.canonical_id)
            self.assertEqual(PROJECT_ROOT / "runtime", context.runtime_dir(PROJECT_ROOT))
            self.assertEqual(
                PROJECT_ROOT / "runtime" / "computers" / "PC-ALPHA-01",
                context.telemetry_dir(PROJECT_ROOT),
            )
            document = json.loads(
                (identity_root / "machine_identity.json").read_text(encoding="utf-8")
            )
            self.assertTrue(document["recognition_only"])
            self.assertFalse(document["launch_restrictions"])
            self.assertEqual("runtime/beta_earth.sqlite3", document["shared_save_path"])
            serialized = json.dumps(document, sort_keys=True)
            self.assertNotIn("private-label", serialized)
            self.assertNotIn("Crosshair VIII Hero", serialized)
            self.assertNotIn("Ryzen 9 5950X", serialized)

    def test_bad_explicit_label_never_blocks_launch_resolution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Beta Earth Fallback ") as temporary:
            context = resolve_machine_context(
                PROJECT_ROOT,
                explicit="not-a-real-computer",
                environment={"BETA_EARTH_LOCAL_CONFIG": temporary},
                persist=True,
                family_facts={"hostname": [], "model": [], "processor": []},
            )
            self.assertRegex(context.canonical_id, r"^PC-LOCAL-[A-F0-9]{10}$")
            self.assertIn("ignored", context.identity_issue or "")
            self.assertFalse(context.restrictions_enabled)

    def test_read_only_preflight_uses_shared_runtime_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Beta Earth Read Only ") as temporary:
            root = self._minimal_release_copy(Path(temporary) / "clean release")
            context = MachineContext(
                "PC-ASCEND-02", "unit-test", Path(temporary) / "identity.json"
            )
            runtime = context.runtime_dir(root)
            telemetry = context.telemetry_dir(root)
            report = run_preflight(
                root,
                runtime_dir=runtime,
                machine_context=context,
                repair=False,
                persist_report=False,
            )
            self.assertNotEqual("blocked", report["status"])
            self.assertFalse(report["portable_contract"]["launch_restrictions"])
            self.assertFalse(report["portable_contract"]["instance_locking"])
            self.assertTrue(report["portable_contract"]["parallel_hud_launches"])
            self.assertTrue(report["runtime"]["shared_across_recognized_computers"])
            self.assertFalse(runtime.exists())
            self.assertFalse(telemetry.exists())

    def test_repair_is_idempotent_and_writes_only_assistance_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Beta Earth Repair ") as temporary:
            root = self._minimal_release_copy(Path(temporary) / "release with spaces")
            context = MachineContext(
                "PC-ALPHA-01", "unit-test", Path(temporary) / "identity.json"
            )
            first = run_preflight(root, machine_context=context, repair=True, persist_report=True)
            second = run_preflight(root, machine_context=context, repair=True, persist_report=True)
            self.assertNotEqual("blocked", first["status"])
            self.assertNotEqual("blocked", second["status"])
            self.assertTrue(first["repair_actions"])
            self.assertEqual([], second["repair_actions"])
            report = context.telemetry_dir(root) / "diagnostics" / "PREFLIGHT_LATEST.json"
            self.assertTrue(report.is_file())
            self.assertEqual("none", first["machine_profile"]["launch_authority"])

    def test_two_huds_can_bind_parallel_ports_on_same_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Beta Earth Parallel HUD ") as temporary:
            runtime = Path(temporary) / "shared runtime"
            first = create_server(
                PROJECT_ROOT,
                runtime_dir=runtime,
                computer_id="PC-ALPHA-01",
                telemetry_dir=Path(temporary) / "alpha evidence",
            )
            second = create_server(
                PROJECT_ROOT,
                runtime_dir=runtime,
                computer_id="PC-ASCEND-02",
                telemetry_dir=Path(temporary) / "ascend evidence",
            )
            try:
                self.assertNotEqual(first.server_address[1], second.server_address[1])
                self.assertEqual(first.controller.store.path, second.controller.store.path)
            finally:
                first.server_close()
                second.server_close()

    def test_lock_implementation_and_lock_file_are_absent(self) -> None:
        startup = (
            PROJECT_ROOT / "src/beta_earth/infrastructure/startup_support.py"
        ).read_text(encoding="utf-8")
        hud = (
            PROJECT_ROOT / "src/beta_earth/presentation/hud_server.py"
        ).read_text(encoding="utf-8")
        launcher = (PROJECT_ROOT / "BetaEarthSovereignty.py").read_text(
            encoding="utf-8"
        ) + (PROJECT_ROOT / "run_beta_earth.py").read_text(encoding="utf-8")
        combined = startup + hud + launcher
        self.assertNotIn("class InstanceLock", combined)
        self.assertNotIn("InstanceAlreadyRunning", combined)
        self.assertNotIn("beta_earth_hud.lock", combined)
        self.assertNotIn("session lease", combined.casefold())

    def test_legacy_machine_saves_consolidate_and_preserve_conflicts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Beta Earth Consolidation ") as temporary:
            root = Path(temporary)
            catalog = load_test_catalog()
            engine = GameEngine(catalog, ManualClock(), PredictableRandom())
            for machine, extra_character, commands in (
                ("PC-ALPHA-01", None, ("build class soldier", "build auto")),
                ("PC-ASCEND-02", "Ascend Only", ("build class sniper",)),
            ):
                store = SQLiteStateStore(
                    root / "runtime" / "machines" / machine / "beta_earth.sqlite3"
                )
                app = GameApplication(engine, store)
                shared = app.open_session("Shared Hero")
                for command in commands:
                    shared.execute(command)
                if extra_character:
                    app.open_session(extra_character)
            receipt = migrate_legacy_database(root, root / "runtime")
            self.assertEqual("consolidated-and-verified", receipt["status"])
            self.assertEqual(2, receipt["characters_merged"])
            self.assertEqual(1, receipt["conflicting_characters_preserved"])
            shared_store = SQLiteStateStore(root / "runtime" / "beta_earth.sqlite3")
            self.assertIsNotNone(shared_store.load("shared hero"))
            self.assertIsNotNone(shared_store.load("ascend only"))
            conflicts = list(
                (root / "runtime" / "backups" / "fleet_save_consolidation_v018").glob(
                    "character_*_variants.json"
                )
            )
            self.assertEqual(1, len(conflicts))
            self.assertTrue(
                (root / "runtime" / "machines" / "PC-ALPHA-01" / "beta_earth.sqlite3").is_file()
            )

    def test_startup_failure_is_sanitized_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Beta Earth Failure ") as temporary:
            telemetry = Path(temporary)
            path = write_startup_failure(
                PROJECT_ROOT,
                telemetry,
                RuntimeError(f"failure under {PROJECT_ROOT}"),
                phase="unit-test",
            )
            self.assertIsNotNone(path)
            assert path is not None
            text = path.read_text(encoding="utf-8")
            self.assertIn("<PROJECT_ROOT>", text)
            self.assertNotIn(str(PROJECT_ROOT), text)
            self.assertLess(len(text), 14000)

    def test_dry_run_verifies_runtime_identity_before_content_dispatch(self) -> None:
        launcher = (PROJECT_ROOT / "BetaEarthSovereignty.py").read_text(encoding="utf-8")
        dry_dispatch = launcher.index('if "--dry-run" in remaining:')
        gate = launcher.index("preflight = run_preflight", dry_dispatch)
        content_dispatch = launcher.index("return cli_main", gate)
        self.assertLess(dry_dispatch, gate)
        self.assertLess(gate, content_dispatch)
        self.assertIn("exact managed release identity", launcher)

    def test_compact_sbom_declares_no_bundled_runtime_dependencies(self) -> None:
        document = json.loads((PROJECT_ROOT / "SBOM.json").read_text(encoding="utf-8"))
        self.assertEqual("0.51.1", document["version"])
        self.assertEqual([], document["bundled_third_party_components"])
        self.assertEqual([], document["runtime_network_dependencies"])
        self.assertIn("No third-party", document["runtime_dependency_declaration"])

    def test_batch_launcher_is_root_relative_and_has_clear_recovery(self) -> None:
        data = (PROJECT_ROOT / "BetaEarthSovereignty.bat").read_bytes()
        self.assertIn(b"%~dp0", data)
        self.assertIn(b".venv\\Scripts\\python.exe", data)
        self.assertIn(b"--self-test", data)
        self.assertIn(b"STARTUP_FAILURE_LATEST.txt", data)
        self.assertNotIn(b"powershell", data.lower())
        self.assertNotIn(b"curl", data.lower())

    def test_public_rights_boundary_excludes_uncleared_music(self) -> None:
        text = (PROJECT_ROOT / "docs" / "RIGHTS_REGISTRY.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("field-signal.wav", text)
        self.assertIn("user-supplied intro track", text)
        self.assertIn("not independently verified", text)


if __name__ == "__main__":
    unittest.main()
