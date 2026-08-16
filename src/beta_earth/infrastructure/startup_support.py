"""Portable startup, non-restrictive computer awareness, shared saves, preflight, and logging.

Asset-ID: BE-NEXT-STARTUP-SUPPORT | Version: 0.51.1 | Status: current.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import traceback
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping

NOTICE = "Copyright © 2026 Gateway Information Group LLC. All rights reserved."
SUPPORTED_MINIMUM = (3, 11)
SUPPORTED_MAXIMUM_EXCLUSIVE = (3, 14)
MINIMUM_FREE_BYTES = 64 * 1024 * 1024
LOG_MAX_BYTES = 512 * 1024
LOG_BACKUP_COUNT = 3
PREFLIGHT_SCHEMA = "beta-earth-preflight-v3"
MACHINE_IDENTITY_SCHEMA = "beta-earth-development-machine-identity-v2"
LOCAL_MACHINE_PATTERN = re.compile(r"PC-LOCAL-[A-F0-9]{10}")
TEMPORARY_MACHINE_ID = "PC-LOCAL-UNASSIGNED"

EXECUTION_NAMESPACE = "BetaEarthSovereignty"
CANONICAL_ENTRYPOINT = "BetaEarthSovereignty.bat"
APPROVED_ENTRYPOINT_ALIASES = ("START_BETA_EARTH.bat",)
CANONICAL_BACKEND = "BetaEarthSovereignty.py"
APPROVED_BACKEND_ALIASES = ("run_beta_earth.py",)
CANONICAL_DIAGNOSTIC_HELPER = "BetaEarthSovereignty_ExportDiagnostics.bat"
APPROVED_DIAGNOSTIC_ALIASES = ("EXPORT_DIAGNOSTICS.bat",)
VERSION_FILE = "VERSION.txt"
MANIFEST_FILE = "MANIFEST.json"
PACKAGE_METADATA_FILE = "PACKAGE_METADATA.json"
BUILD_ID = "BESOV-0.51.1-20260816-HUD-TRUTH-ALIGNMENT"
PARAMETER_VERSION = "2.17.9"
OUTPUT_ROOTS = {
    "runtime": "runtime",
    "config": "runtime/config",
    "state": "runtime",
    "logs": "runtime/computers/<COMPUTER_ID>/logs",
    "temp": "runtime/temp",
    "cache": "runtime/cache",
    "exports": "exports",
    "support_exports": "exports/support/<COMPUTER_ID>",
    "diagnostics": "runtime/computers/<COMPUTER_ID>/diagnostics",
    "reports": "runtime/reports",
    "downloads": "runtime/downloads",
    "backups": "runtime/backups",
    "releases": "releases",
}

COMPUTER_PROFILE_ALIASES: dict[str, frozenset[str]] = {
    "PC-ALPHA-01": frozenset({
        "pc-alpha-01", "alpha", "alpha computer", "main desktop", "primary desktop"
    }),
    "PC-ASCEND-02": frozenset({
        "pc-ascend-02", "ascend", "ascend laptop", "asus rog strix", "g634jy"
    }),
    "PC-DEUSEX-03": frozenset({
        "pc-deusex-03", "pc-raider-03", "deusex", "deus ex", "raider",
        "msi raider", "ge66", "raider ge66 12uhs"
    }),
}

# Only broad, non-secret family labels are used for automatic development routing.
# Raw host/model/processor strings are discarded and are never written to reports.
COMPUTER_PROFILE_SIGNATURES: dict[str, tuple[tuple[str, str, int, str], ...]] = {
    "PC-ALPHA-01": (
        ("hostname", "pc-alpha-01", 12, "hostname-alias:PC-ALPHA-01"),
        ("hostname", "alpha", 10, "hostname-alias:ALPHA"),
        ("model", "crosshair viii", 7, "board-family:CROSSHAIR-VIII"),
        ("processor", "5950x", 6, "processor-family:5950X"),
    ),
    "PC-ASCEND-02": (
        ("hostname", "pc-ascend-02", 12, "hostname-alias:PC-ASCEND-02"),
        ("hostname", "ascend", 10, "hostname-alias:ASCEND"),
        ("model", "g634jy", 9, "model-family:G634JY"),
        ("model", "rog strix", 7, "model-family:ROG-STRIX"),
        ("processor", "13980hx", 6, "processor-family:13980HX"),
    ),
    "PC-DEUSEX-03": (
        ("hostname", "pc-deusex-03", 12, "hostname-alias:PC-DEUSEX-03"),
        ("hostname", "deusex", 10, "hostname-alias:DEUSEX"),
        ("hostname", "raider", 8, "hostname-alias:RAIDER"),
        ("model", "ge66", 9, "model-family:GE66"),
        ("model", "12uhs", 9, "model-family:12UHS"),
        ("model", "raider", 7, "model-family:RAIDER"),
        ("processor", "12700h", 6, "processor-family:12700H"),
    ),
}

REQUIRED_PROJECT_FILES = (
    VERSION_FILE,
    PACKAGE_METADATA_FILE,
    MANIFEST_FILE,
    CANONICAL_ENTRYPOINT,
    CANONICAL_BACKEND,
    *APPROVED_ENTRYPOINT_ALIASES,
    *APPROVED_BACKEND_ALIASES,
    CANONICAL_DIAGNOSTIC_HELPER,
    *APPROVED_DIAGNOSTIC_ALIASES,
    "content/world.json",
    "content/items.json",
    "content/creatures.json",
    "content/classes.json",
    "content/quests.json",
    "content/economy.json",
    "hud/index.html",
    "hud/app.js",
    "hud/styles.css",
    "SBOM.json",
)



@dataclass(frozen=True, slots=True)
class MachineContext:
    """Sanitized recognition context used only for assistance and diagnostics."""

    canonical_id: str
    source: str
    identity_path: Path
    detection_evidence: tuple[str, ...] = ()
    temporary: bool = False
    known_computer_profile: bool = False
    identity_issue: str | None = None

    def runtime_dir(self, project_root: Path) -> Path:
        """Return the unrestricted shared player-save directory."""

        return project_root.resolve() / "runtime"

    def telemetry_dir(self, project_root: Path) -> Path:
        """Return a per-computer evidence lane that never controls launch."""

        return project_root.resolve() / "runtime" / "computers" / self.canonical_id

    def support_dir(self, project_root: Path) -> Path:
        return project_root.resolve() / "exports" / "support" / self.canonical_id

    @property
    def overlay_relative_path(self) -> str:
        return f"runtime/computers/{self.canonical_id}"

    @property
    def restrictions_enabled(self) -> bool:
        return False

@dataclass(frozen=True, slots=True)
class MachineDetection:
    canonical_id: str | None
    evidence: tuple[str, ...]
    ambiguous: bool = False


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().replace("_", " ").split())


def canonical_machine_id(value: str | None) -> str | None:
    """Resolve a sanitized computer-profile alias without machine-unique identifiers."""

    if value is None or not value.strip() or value.strip().casefold() in {"auto", "none", "unselected"}:
        return None
    normalized = _normalize(value)
    for canonical, aliases in COMPUTER_PROFILE_ALIASES.items():
        if normalized == canonical.casefold() or normalized in aliases:
            return canonical
    if LOCAL_MACHINE_PATTERN.fullmatch(value.strip().upper()):
        return value.strip().upper()
    raise ValueError(
        "Unknown machine profile. Use ALPHA, ASCEND, DeusEx/Raider/GE66, "
        "or a canonical PC-* development ID."
    )


def _identity_path(project_root: Path, environment: Mapping[str, str]) -> Path:
    """Return a project-local identity path unless an explicit override is supplied."""

    root = project_root.resolve()
    override = environment.get("BETA_EARTH_LOCAL_CONFIG", "").strip()
    if not override:
        return root / "runtime" / "config" / "machine_identity.json"
    candidate = Path(override).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Relative BETA_EARTH_LOCAL_CONFIG may not escape the project root.") from exc
    else:
        candidate = candidate.resolve()
    return candidate if candidate.suffix.casefold() == ".json" else candidate / "machine_identity.json"


def _read_identity(path: Path) -> tuple[str | None, str | None]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        return None, f"Existing project-local development identity could not be read: {type(exc).__name__}."
    if not isinstance(document, dict):
        return None, "Existing project-local development identity is not a JSON object."
    try:
        machine_id = canonical_machine_id(str(document.get("machine_id", "")))
    except ValueError:
        return None, "Existing project-local development identity contains an invalid sanitized ID."
    if machine_id is None:
        return None, "Existing project-local development identity does not name a machine."
    return machine_id, None


def _windows_family_facts() -> dict[str, list[str]]:
    facts: dict[str, list[str]] = {"hostname": [], "model": [], "processor": []}
    if os.name != "nt":
        return facts
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\BIOS",
        ) as key:
            for name in ("SystemProductName", "SystemFamily", "BaseBoardProduct", "SystemManufacturer"):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if isinstance(value, str) and value.strip():
                    facts["model"].append(value)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            try:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            except OSError:
                value = ""
            if isinstance(value, str) and value.strip():
                facts["processor"].append(value)
    except (ImportError, OSError):
        pass
    return facts


def _collect_family_facts(environment: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    windows = _windows_family_facts()
    host_values = [
        environment.get("COMPUTERNAME", ""),
        environment.get("HOSTNAME", ""),
        platform.node(),
    ]
    processor_values = [
        environment.get("PROCESSOR_IDENTIFIER", ""),
        platform.processor(),
    ]
    return {
        "hostname": tuple(_normalize(value) for value in [*host_values, *windows["hostname"]] if value and value.strip()),
        "model": tuple(_normalize(value) for value in windows["model"] if value and value.strip()),
        "processor": tuple(_normalize(value) for value in [*processor_values, *windows["processor"]] if value and value.strip()),
    }


def detect_machine_profile(
    *,
    environment: Mapping[str, str] | None = None,
    family_facts: Mapping[str, tuple[str, ...] | list[str]] | None = None,
) -> MachineDetection:
    """Match broad family hints, then discard all raw machine facts."""

    env = environment or os.environ
    raw = family_facts or _collect_family_facts(env)
    facts = {
        category: tuple(_normalize(str(value)) for value in values if str(value).strip())
        for category, values in raw.items()
    }
    scores: dict[str, int] = {machine_id: 0 for machine_id in COMPUTER_PROFILE_SIGNATURES}
    evidence: dict[str, set[str]] = {machine_id: set() for machine_id in COMPUTER_PROFILE_SIGNATURES}
    for machine_id, signatures in COMPUTER_PROFILE_SIGNATURES.items():
        for category, token, weight, label in signatures:
            if any(token in value for value in facts.get(category, ())):
                scores[machine_id] += weight
                evidence[machine_id].add(label)
    best_score = max(scores.values(), default=0)
    if best_score <= 0:
        return MachineDetection(None, ())
    winners = [machine_id for machine_id, score in scores.items() if score == best_score]
    if len(winners) != 1:
        combined = sorted({label for machine_id in winners for label in evidence[machine_id]})
        return MachineDetection(None, tuple(combined), ambiguous=True)
    winner = winners[0]
    return MachineDetection(winner, tuple(sorted(evidence[winner])))


def _legacy_project_profile(project_root: Path) -> tuple[str | None, str | None]:
    profile_path = project_root.resolve() / "runtime" / "machine_profile.json"
    try:
        document = json.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None, "Legacy project-local machine profile is unreadable and was ignored."
    if not isinstance(document, dict):
        return None, "Legacy project-local machine profile is invalid and was ignored."
    try:
        return canonical_machine_id(str(document.get("machine_id", ""))), None
    except ValueError:
        return None, "Legacy project-local machine profile contains an unknown ID and was ignored."


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def atomic_write_text(path: Path, value: str) -> None:
    _atomic_write(path, value.encode("utf-8"))


def _persist_identity(path: Path, machine_id: str, source: str) -> bool:
    existing: dict[str, Any] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            existing = raw
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        pass
    now = datetime.now(timezone.utc).isoformat()
    desired = {
        "schema": MACHINE_IDENTITY_SCHEMA,
        "machine_id": machine_id,
        "source": source,
        "development_only": True,
        "recognition_only": True,
        "launch_restrictions": False,
        "shared_save_path": "runtime/beta_earth.sqlite3",
        "shipping_action": "Remove development computer-recognition metadata before public distribution.",
        "privacy": "No serial, UUID, MAC/IP, username, product key, or credential is stored.",
        "created_at_utc": existing.get("created_at_utc", now),
        "updated_at_utc": now,
        "copyright_notice": NOTICE,
    }
    comparable_existing = dict(existing)
    comparable_existing["updated_at_utc"] = desired["updated_at_utc"]
    if comparable_existing == desired:
        return False
    atomic_write_json(path, desired)
    return True


def resolve_machine_context(
    project_root: Path,
    explicit: str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    persist: bool = False,
    family_facts: Mapping[str, tuple[str, ...] | list[str]] | None = None,
) -> MachineContext:
    """Resolve a sanitized recognition label that never gates startup."""

    root = project_root.resolve()
    env = environment or os.environ
    identity_path = _identity_path(root, env)
    issue: str | None = None

    if explicit:
        try:
            machine_id = canonical_machine_id(explicit)
        except ValueError as exc:
            machine_id = None
            issue = f"Requested computer label was not recognized and was ignored: {exc}"
        if machine_id is not None:
            if persist:
                _persist_identity(identity_path, machine_id, "explicit-user-selection")
            return MachineContext(
                machine_id,
                "explicit-argument",
                identity_path,
                known_computer_profile=machine_id in COMPUTER_PROFILE_ALIASES,
            )

    configured = env.get("BETA_EARTH_MACHINE_ID", "").strip()
    if configured:
        try:
            machine_id = canonical_machine_id(configured)
        except ValueError as exc:
            machine_id = None
            issue = issue or f"BETA_EARTH_MACHINE_ID was ignored: {exc}"
        if machine_id is not None:
            return MachineContext(
                machine_id,
                "environment-override",
                identity_path,
                known_computer_profile=machine_id in COMPUTER_PROFILE_ALIASES,
                identity_issue=issue,
            )

    stored_id, stored_issue = _read_identity(identity_path)
    if stored_id is not None:
        return MachineContext(
            stored_id,
            "project-local-development-identity",
            identity_path,
            known_computer_profile=stored_id in COMPUTER_PROFILE_ALIASES,
            identity_issue=issue or stored_issue,
        )
    issue = issue or stored_issue

    detection = detect_machine_profile(environment=env, family_facts=family_facts)
    if detection.canonical_id is not None:
        if persist:
            _persist_identity(identity_path, detection.canonical_id, "automatic-family-detection")
        return MachineContext(
            detection.canonical_id,
            "automatic-family-detection",
            identity_path,
            detection_evidence=detection.evidence,
            known_computer_profile=True,
            identity_issue=issue,
        )

    legacy_id, legacy_issue = _legacy_project_profile(root)
    issue = issue or legacy_issue
    if legacy_id is not None:
        if persist:
            _persist_identity(identity_path, legacy_id, "legacy-project-profile-migration")
        return MachineContext(
            legacy_id,
            "legacy-project-profile",
            identity_path,
            known_computer_profile=legacy_id in COMPUTER_PROFILE_ALIASES,
            identity_issue=issue,
        )

    if not persist:
        return MachineContext(
            TEMPORARY_MACHINE_ID,
            "temporary-unassigned",
            identity_path,
            detection_evidence=detection.evidence,
            temporary=True,
            identity_issue=(
                issue
                or ("Automatic family detection was ambiguous; normal launch will create a random recognition label." if detection.ambiguous else None)
            ),
        )

    generated = f"PC-LOCAL-{secrets.token_hex(5).upper()}"
    _persist_identity(identity_path, generated, "generated-random-development-id")
    return MachineContext(
        generated,
        "generated-random-development-id",
        identity_path,
        detection_evidence=detection.evidence,
        temporary=False,
        known_computer_profile=False,
        identity_issue=(
            issue
            or ("Automatic family detection was ambiguous; a random recognition label was used instead of guessing." if detection.ambiguous else None)
        ),
    )


def resolve_machine_id(
    project_root: Path,
    explicit: str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[str | None, str]:
    """Compatibility wrapper returning the sanitized ID and resolution source."""

    context = resolve_machine_context(
        project_root,
        explicit,
        environment=environment,
        persist=False,
    )
    return context.canonical_id, context.source


def configure_runtime_logging(runtime_dir: Path) -> logging.Logger:
    """Create one bounded per-computer diagnostic log without affecting launch."""

    logger = logging.getLogger("beta_earth")
    logger.setLevel(logging.INFO)
    log_dir = runtime_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = (log_dir / "beta_earth.log").resolve()
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            try:
                if Path(handler.baseFilename).resolve() == target:
                    return logger
            except OSError:
                continue
    handler = RotatingFileHandler(
        target,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _database_health(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False, "status": "not-created", "integrity": None}
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=3.0)) as connection:
            connection.execute("PRAGMA busy_timeout = 3000")
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
        return {
            "present": True,
            "status": "healthy" if result.casefold() == "ok" else "needs-attention",
            "integrity": result,
            "database_schema": schema,
            "size_bytes": path.stat().st_size,
        }
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {
            "present": True,
            "status": "blocked",
            "integrity": None,
            "error": f"{type(exc).__name__}: {exc}"[:400],
        }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_managed_path(relative: str) -> bool:
    candidate = Path(relative)
    return bool(relative and not candidate.is_absolute() and ".." not in candidate.parts)


def runtime_identity_health(project_root: Path) -> dict[str, Any]:
    """Verify release identity and every package-managed file before runtime mutation."""

    started = datetime.now(timezone.utc)
    root = project_root.resolve()
    errors: list[dict[str, str]] = []
    try:
        version = (root / VERSION_FILE).read_text(encoding="utf-8").strip()
        metadata = json.loads((root / PACKAGE_METADATA_FILE).read_text(encoding="utf-8"))
        manifest = json.loads((root / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            "status": "blocked",
            "gate_result": "BLOCKED",
            "error": f"{type(exc).__name__}: {exc}"[:400],
            "gate_started_at_utc": started.isoformat(),
            "gate_duration_ms": 0,
            "pre_runtime_assertion": True,
            "managed_file_count": 0,
            "verified_file_count": 0,
            "mismatches": [],
        }
    if not isinstance(metadata, dict) or not isinstance(manifest, dict):
        return {
            "status": "blocked",
            "gate_result": "BLOCKED",
            "error": "PACKAGE_METADATA.json and MANIFEST.json must contain JSON objects.",
            "gate_started_at_utc": started.isoformat(),
            "gate_duration_ms": 0,
            "pre_runtime_assertion": True,
            "managed_file_count": 0,
            "verified_file_count": 0,
            "mismatches": [],
        }

    expected = {
        "version": version,
        "package_id": "beta-earth-sovereignty-next",
        "build_id": BUILD_ID,
        "execution_namespace": EXECUTION_NAMESPACE,
        "canonical_entrypoint": CANONICAL_ENTRYPOINT,
        "backend_target": CANONICAL_BACKEND,
    }
    for field, value in expected.items():
        metadata_value = metadata.get(field)
        manifest_value = manifest.get(field if field != "package_id" else "package")
        if str(metadata_value) != str(value):
            errors.append({"path": PACKAGE_METADATA_FILE, "code": f"metadata-{field}", "detail": f"expected {value!r}; found {metadata_value!r}"})
        if str(manifest_value) != str(value):
            errors.append({"path": MANIFEST_FILE, "code": f"manifest-{field}", "detail": f"expected {value!r}; found {manifest_value!r}"})

    aliases = tuple(str(item) for item in metadata.get("approved_aliases", []))
    if aliases != APPROVED_ENTRYPOINT_ALIASES:
        errors.append({"path": PACKAGE_METADATA_FILE, "code": "entrypoint-aliases", "detail": f"expected {APPROVED_ENTRYPOINT_ALIASES!r}; found {aliases!r}"})
    backend_aliases = tuple(str(item) for item in metadata.get("approved_backend_aliases", []))
    if backend_aliases != APPROVED_BACKEND_ALIASES:
        errors.append({"path": PACKAGE_METADATA_FILE, "code": "backend-aliases", "detail": f"expected {APPROVED_BACKEND_ALIASES!r}; found {backend_aliases!r}"})
    if metadata.get("output_roots") != OUTPUT_ROOTS or manifest.get("output_roots") != OUTPUT_ROOTS:
        errors.append({"path": PACKAGE_METADATA_FILE, "code": "output-roots", "detail": "package metadata and manifest must match the canonical project-local output map"})

    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        assets = []
        errors.append({"path": MANIFEST_FILE, "code": "asset-list", "detail": "assets must be a list"})
    managed_count = 0
    verified_count = 0
    seen: set[str] = set()
    for record in assets:
        if not isinstance(record, dict) or not record.get("package_managed", True):
            continue
        relative = str(record.get("path", ""))
        key = relative.casefold()
        if key in seen:
            errors.append({"path": relative or MANIFEST_FILE, "code": "duplicate-path", "detail": "case-insensitive duplicate managed path"})
            continue
        seen.add(key)
        if not _safe_managed_path(relative):
            errors.append({"path": relative or MANIFEST_FILE, "code": "unsafe-path", "detail": "managed path must be project-relative and may not contain '..'"})
            continue
        managed_count += 1
        path = root / relative
        if not path.is_file():
            errors.append({"path": relative, "code": "missing", "detail": "managed file is absent"})
            continue
        expected_size = record.get("size_bytes")
        if isinstance(expected_size, int) and path.stat().st_size != expected_size:
            errors.append({"path": relative, "code": "size", "detail": f"expected {expected_size}; found {path.stat().st_size}"})
            continue
        expected_sha = str(record.get("sha256", "")).casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            errors.append({"path": relative, "code": "sha256-missing", "detail": "managed file requires a valid SHA-256"})
            continue
        actual_sha = _file_sha256(path)
        if actual_sha != expected_sha:
            errors.append({"path": relative, "code": "sha256", "detail": f"expected {expected_sha}; found {actual_sha}"})
            continue
        verified_count += 1

    ended = datetime.now(timezone.utc)
    status = "healthy" if not errors else "blocked"
    return {
        "status": status,
        "gate_result": "PASS" if not errors else "BLOCKED",
        "version": version,
        "manifest_version": manifest.get("version"),
        "build_id": manifest.get("build_id"),
        "execution_namespace": manifest.get("execution_namespace"),
        "canonical_entrypoint": manifest.get("canonical_entrypoint"),
        "invoked_alias": os.environ.get("BETA_EARTH_INVOKED_ENTRYPOINT", CANONICAL_ENTRYPOINT),
        "backend_target": manifest.get("backend_target"),
        "output_roots": manifest.get("output_roots"),
        "asset_count": manifest.get("asset_count"),
        "managed_file_count": managed_count,
        "verified_file_count": verified_count,
        "mismatch_count": len(errors),
        "mismatches": errors[:200],
        "gate_started_at_utc": started.isoformat(),
        "gate_duration_ms": round((ended - started).total_seconds() * 1000, 3),
        "pre_runtime_assertion": True,
        "release": manifest.get("release"),
        "parameter_lineage": [
            {"name": row.get("name"), "version": row.get("version"), "status": row.get("status")}
            for row in manifest.get("source_lineage", [])
            if isinstance(row, dict) and "chatgpt_new_thread_parameters" in str(row.get("name", ""))
        ],
    }


def _manifest_health(project_root: Path) -> dict[str, Any]:
    """Compatibility name retained for existing callers."""

    return runtime_identity_health(project_root)


def _writable_probe(directory: Path, *, create: bool) -> tuple[bool, str | None]:
    """Check a lane without creating it unless repair was explicitly requested."""

    try:
        if not create:
            if directory.exists():
                if not directory.is_dir():
                    return False, "path exists but is not a directory"
                if os.access(directory, os.W_OK | os.X_OK):
                    return True, None
                return False, "directory is not writable"

            ancestor = directory.parent
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            if not ancestor.is_dir():
                return False, "no existing parent directory is available"
            if os.access(ancestor, os.W_OK | os.X_OK):
                return True, None
            return False, "nearest existing parent directory is not writable"

        directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir():
            return False, "path exists but is not a directory"
        handle = tempfile.NamedTemporaryFile(
            prefix=".beta-earth-write-test.", dir=directory, delete=False
        )
        probe = Path(handle.name)
        with handle:
            handle.write(b"ok")
            handle.flush()
            os.fsync(handle.fileno())
        probe.unlink(missing_ok=True)
        return True, None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"[:400]


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(4)}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(source, timeout=8.0)) as source_connection:
            source_connection.execute("PRAGMA busy_timeout = 8000")
            with closing(sqlite3.connect(temporary, timeout=8.0)) as target_connection:
                source_connection.backup(target_connection)
                target_connection.commit()
                result = str(target_connection.execute("PRAGMA quick_check").fetchone()[0])
                if result.casefold() != "ok":
                    raise sqlite3.DatabaseError(f"backup quick_check returned {result!r}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_legacy_database(project_root: Path, runtime_dir: Path) -> dict[str, Any]:
    """Consolidate old machine-lane saves into one unrestricted shared database.

    Every source database remains untouched. Unique characters are combined. When
    the same character differs between databases, the highest revision wins and
    every alternate snapshot is retained in a recovery JSON file.
    """

    root = project_root.resolve()
    runtime = runtime_dir.resolve()
    target = runtime / "beta_earth.sqlite3"
    legacy_root = root / "runtime" / "machines"
    lane_sources = sorted(legacy_root.glob("*/beta_earth.sqlite3")) if legacy_root.exists() else []
    receipt_path = runtime / "diagnostics" / "FLEET_SAVE_CONSOLIDATION.json"
    if not lane_sources:
        return {"status": "not-needed", "reason": "no-legacy-machine-lane-saves"}
    if target.exists() and receipt_path.exists():
        return {"status": "not-needed", "reason": "consolidation-already-recorded"}

    runtime.mkdir(parents=True, exist_ok=True)
    backup_root = runtime / "backups" / "fleet_save_consolidation_v018"
    backup_root.mkdir(parents=True, exist_ok=True)
    sources = ([target] if target.exists() else []) + lane_sources
    source_rows: dict[str, list[dict[str, Any]]] = {}
    valid_sources: list[Path] = []
    skipped: list[dict[str, str]] = []

    for source in sources:
        try:
            uri = source.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=8.0)) as connection:
                connection.execute("PRAGMA busy_timeout = 8000")
                check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                if check.casefold() != "ok":
                    raise sqlite3.DatabaseError(f"quick_check returned {check!r}")
                if not _table_exists(connection, "characters"):
                    skipped.append({"source": _safe_relative(source, root, source.name), "reason": "no characters table"})
                    continue
                rows = connection.execute(
                    "SELECT player_key, display_name, revision, state_json, updated_at FROM characters"
                ).fetchall()
                valid_sources.append(source)
                for player_key, display_name, revision, state_json, updated_at in rows:
                    source_rows.setdefault(str(player_key), []).append(
                        {
                            "source": source,
                            "display_name": str(display_name),
                            "revision": int(revision),
                            "state_json": str(state_json),
                            "updated_at": str(updated_at),
                            "mtime_ns": source.stat().st_mtime_ns,
                        }
                    )
        except (OSError, sqlite3.Error, ValueError) as exc:
            skipped.append({"source": _safe_relative(source, root, source.name), "reason": f"{type(exc).__name__}: {exc}"[:300]})

    if not valid_sources:
        return {
            "status": "warning",
            "reason": "legacy lane saves were found but none contained a readable game database",
            "sources_preserved": True,
            "skipped": skipped,
        }

    if target.exists():
        _backup_sqlite(target, backup_root / "shared_before_v018.sqlite3")

    from beta_earth.infrastructure.sqlite_store import SQLiteStateStore

    temporary = runtime / f".beta_earth.shared_merge.{secrets.token_hex(5)}.sqlite3"
    temporary.unlink(missing_ok=True)
    SQLiteStateStore(temporary)
    selected: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    try:
        with closing(sqlite3.connect(temporary, timeout=8.0)) as output, output:
            output.execute("PRAGMA busy_timeout = 8000")
            for player_key, variants in sorted(source_rows.items()):
                winner = max(
                    variants,
                    key=lambda row: (row["revision"], row["updated_at"], row["mtime_ns"], str(row["source"])),
                )
                selected[player_key] = winner
                output.execute(
                    "INSERT INTO characters(player_key, display_name, revision, state_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (player_key, winner["display_name"], winner["revision"], winner["state_json"], winner["updated_at"]),
                )
                source = winner["source"]
                uri = source.resolve().as_uri() + "?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=8.0)) as source_connection:
                    if _table_exists(source_connection, "domain_events"):
                        event_rows = source_connection.execute(
                            "SELECT revision, event_kind, payload_json, recorded_at FROM domain_events WHERE player_key = ? ORDER BY id",
                            (player_key,),
                        ).fetchall()
                        output.executemany(
                            "INSERT INTO domain_events(player_key, revision, event_kind, payload_json, recorded_at) VALUES (?, ?, ?, ?, ?)",
                            [(player_key, int(r), str(k), str(payload), str(recorded)) for r, k, payload, recorded in event_rows],
                        )
                    if _table_exists(source_connection, "state_migration_history"):
                        migration_rows = source_connection.execute(
                            "SELECT source_revision, from_content_version, to_content_version, state_json, recorded_at FROM state_migration_history WHERE player_key = ? ORDER BY id",
                            (player_key,),
                        ).fetchall()
                        for row in migration_rows:
                            output.execute(
                                "INSERT OR IGNORE INTO state_migration_history(player_key, source_revision, from_content_version, to_content_version, state_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                                (player_key, int(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4])),
                            )
                distinct = {variant["state_json"] for variant in variants}
                if len(distinct) > 1:
                    conflict_record = {
                        "player_key_hash": hashlib.sha256(player_key.encode("utf-8")).hexdigest()[:16],
                        "selected_source": _safe_relative(winner["source"], root, winner["source"].name),
                        "selected_revision": winner["revision"],
                        "variants": [
                            {
                                "source": _safe_relative(row["source"], root, row["source"].name),
                                "display_name": row["display_name"],
                                "revision": row["revision"],
                                "updated_at": row["updated_at"],
                                "state_json": json.loads(row["state_json"]),
                            }
                            for row in sorted(variants, key=lambda item: str(item["source"]))
                        ],
                    }
                    conflicts.append(conflict_record)
                    atomic_write_json(
                        backup_root / f"character_{conflict_record['player_key_hash']}_variants.json",
                        conflict_record,
                    )
            result = str(output.execute("PRAGMA quick_check").fetchone()[0])
            if result.casefold() != "ok":
                raise sqlite3.DatabaseError(f"consolidated quick_check returned {result!r}")
        os.replace(temporary, target)
        receipt = {
            "schema": "beta-earth-unrestricted-save-consolidation-v1",
            "status": "consolidated-and-verified",
            "target": "runtime/beta_earth.sqlite3",
            "characters_merged": len(selected),
            "conflicting_characters_preserved": len(conflicts),
            "source_databases_preserved": True,
            "source_databases": [_safe_relative(path, root, path.name) for path in valid_sources],
            "skipped": skipped,
            "selection_rule": "highest revision, then newest durable timestamp; alternate snapshots retained",
            "launch_restrictions": False,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "copyright_notice": NOTICE,
        }
        atomic_write_json(receipt_path, receipt)
        return receipt
    except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
        temporary.unlink(missing_ok=True)
        return {
            "status": "warning",
            "reason": f"{type(exc).__name__}: {exc}"[:400],
            "source_databases_preserved": True,
            "target_preserved": target.exists(),
        }
    finally:
        temporary.unlink(missing_ok=True)


def _safe_relative(path: Path, root: Path, fallback: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return fallback


def _preflight_text(report: Mapping[str, Any]) -> str:
    machine = report.get("machine_profile", {})
    lines = [
        "BETA EARTH STARTUP PREFLIGHT",
        "============================",
        f"Status: {str(report.get('status', 'unknown')).upper()}",
        f"Version: {report.get('version')}",
        f"Computer label: {machine.get('canonical_id') or 'unselected'}",
        f"Recognition source: {machine.get('source')}",
        f"Shared save path: {report.get('runtime', {}).get('relative_path')}/beta_earth.sqlite3",
        f"Computer diagnostics: {report.get('telemetry', {}).get('relative_path')}",
        f"Launch restrictions: {report.get('portable_contract', {}).get('launch_restrictions')}",
        f"Python: {report.get('environment', {}).get('python')}",
        f"Runtime writable: {report.get('runtime', {}).get('writable')}",
        f"Support export writable: {report.get('support_exports', {}).get('writable')}",
        f"Database: {report.get('database', {}).get('status')}",
        f"Runtime identity gate: {report.get('runtime_identity', report.get('manifest', {})).get('gate_result')}",
    ]
    findings = report.get("findings", [])
    if findings:
        lines.append("")
        lines.append("Findings:")
        for item in findings:
            lines.append(
                f"- [{str(item.get('severity', 'info')).upper()}] "
                f"{item.get('code')}: {item.get('detail')}"
            )
    actions = report.get("repair_actions", [])
    if actions:
        lines.append("")
        lines.append("Repair actions:")
        lines.extend(f"- {item}" for item in actions)
    lines.extend(
        [
            "",
            "Launch policy:",
            "Computer recognition is informational only. No computer identity, active session, lock, lease, or previous HUD blocks startup.",
            "",
            "Next recovery step:",
            str(report.get("next_recovery_step", "Run BetaEarthSovereignty.bat --self-test.")),
            "",
            "Shipping boundary: development computer-recognition metadata must be removed before public distribution.",
            f"Rights: {NOTICE}",
            "",
        ]
    )
    return "\n".join(lines)


def run_preflight(
    project_root: Path,
    *,
    runtime_dir: Path | None = None,
    machine: str | None = None,
    machine_context: MachineContext | None = None,
    environment: Mapping[str, str] | None = None,
    repair: bool = False,
    persist_report: bool = False,
) -> dict[str, Any]:
    """Verify release identity first, then validate or repair only project-local runtime lanes."""

    root = project_root.resolve()
    env = environment or os.environ
    findings: list[dict[str, str]] = []
    repair_actions: list[str] = []
    context = machine_context or resolve_machine_context(root, machine, environment=env, persist=repair)
    runtime = (runtime_dir or context.runtime_dir(root)).resolve()
    telemetry = context.telemetry_dir(root).resolve()
    support = context.support_dir(root).resolve()
    runtime_identity = runtime_identity_health(root)

    if runtime_identity.get("status") != "healthy":
        detail = str(runtime_identity.get("error") or f"{runtime_identity.get('mismatch_count', 0)} managed-file or identity mismatch(es)")
        findings.append({"severity": "error", "code": "runtime-release-identity", "detail": detail})
    if context.identity_issue:
        findings.append({"severity": "warning", "code": "computer-recognition-note", "detail": context.identity_issue})
    if context.temporary:
        findings.append({"severity": "warning", "code": "computer-recognition-pending", "detail": "Read-only mode did not persist a recognition label; normal launch may create one inside runtime/config without private hardware identifiers."})

    python_supported = SUPPORTED_MINIMUM <= sys.version_info[:2] < SUPPORTED_MAXIMUM_EXCLUSIVE
    if not python_supported:
        findings.append({"severity": "error", "code": "python-version", "detail": f"Python 3.11, 3.12, or 3.13 is required; found {sys.version_info.major}.{sys.version_info.minor}."})
    missing = [relative for relative in REQUIRED_PROJECT_FILES if not (root / relative).is_file()]
    if missing:
        findings.append({"severity": "error", "code": "missing-release-files", "detail": ", ".join(missing)})

    gate_passed = runtime_identity.get("status") == "healthy" and not missing and python_supported
    if repair and gate_passed:
        for directory, label in (
            (runtime, "shared runtime"),
            (runtime / "config", "runtime config"),
            (runtime / "temp", "runtime temp"),
            (runtime / "cache", "runtime cache"),
            (runtime / "reports", "runtime reports"),
            (runtime / "downloads", "runtime downloads"),
            (runtime / "backups", "shared runtime backups"),
            (telemetry / "logs", "computer diagnostic logs"),
            (telemetry / "diagnostics", "computer diagnostics"),
            (support, "computer support exports"),
            (root / "releases", "project release output"),
        ):
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                repair_actions.append(f"Created missing {label} directory.")
        migration = migrate_legacy_database(root, runtime)
        if migration.get("status") == "consolidated-and-verified":
            repair_actions.append("Consolidated legacy machine-lane saves into the shared database; every source and conflicting snapshot was preserved.")
        elif migration.get("status") == "warning":
            findings.append({"severity": "warning", "code": "legacy-save-consolidation", "detail": str(migration.get("reason"))})
    elif repair:
        migration = {"status": "not-run", "reason": "runtime identity gate blocked repair"}
    else:
        migration = {"status": "not-run", "reason": "read-only-preflight"}

    runtime_writable, runtime_error = _writable_probe(runtime, create=repair and gate_passed)
    telemetry_writable, telemetry_error = _writable_probe(telemetry, create=repair and gate_passed)
    support_writable, support_error = _writable_probe(support, create=repair and gate_passed)
    if not runtime_writable:
        findings.append({"severity": "error", "code": "runtime-not-writable", "detail": runtime_error or "shared runtime directory is not writable"})
    if not telemetry_writable:
        findings.append({"severity": "warning", "code": "diagnostics-not-writable", "detail": telemetry_error or "computer diagnostics directory is not writable"})
    if not support_writable:
        findings.append({"severity": "warning", "code": "support-export-not-writable", "detail": support_error or "computer support export directory is not writable"})
    try:
        free_bytes = shutil.disk_usage(runtime if runtime.exists() else root).free
    except OSError:
        free_bytes = None
    if free_bytes is not None and free_bytes < MINIMUM_FREE_BYTES:
        findings.append({"severity": "warning", "code": "low-free-space", "detail": f"Only {free_bytes} bytes are free near the runtime directory."})
    database = _database_health(runtime / "beta_earth.sqlite3") if gate_passed else {"present": False, "status": "not-probed-identity-blocked"}
    if database.get("status") in {"blocked", "needs-attention"}:
        findings.append({"severity": "error", "code": "database-integrity", "detail": str(database.get("error") or database.get("integrity"))})

    severity_set = {item["severity"] for item in findings}
    status = "blocked" if "error" in severity_set else "ready-with-warnings" if "warning" in severity_set else "ready"
    try:
        version = (root / VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        version = "unknown"
    report: dict[str, Any] = {
        "schema": PREFLIGHT_SCHEMA,
        "asset_id": "BE-NEXT-PREFLIGHT-REPORT",
        "project": "Beta Earth: Sovereignty Next",
        "canonical_project": "MUDD Game Development",
        "canonical_thread_title": "MUDD Game Development — Runtime Identity & Launch Reliability",
        "version": version,
        "build_id": BUILD_ID,
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "repair" if repair else "read-only-check",
        "execution": {
            "namespace": EXECUTION_NAMESPACE,
            "canonical_entrypoint": CANONICAL_ENTRYPOINT,
            "invoked_entrypoint": os.environ.get("BETA_EARTH_INVOKED_ENTRYPOINT", CANONICAL_ENTRYPOINT),
            "approved_aliases": list(APPROVED_ENTRYPOINT_ALIASES),
            "backend_target": CANONICAL_BACKEND,
            "approved_backend_aliases": list(APPROVED_BACKEND_ALIASES),
        },
        "output_roots": OUTPUT_ROOTS,
        "portable_contract": {
            "root_source": "launcher/script location", "root_relative": True, "space_safe": True,
            "drive_required": False, "shared_package": True, "shared_player_save": True,
            "machine_specific_code_forks": False, "launch_restrictions": False, "instance_locking": False,
            "session_leases": False, "machine_ownership_gate": False, "parallel_hud_launches": True,
            "cross_machine_launch_blocking": False, "same_machine_launch_blocking": False,
            "project_local_outputs_by_default": True, "system_temp_fallback": False, "cwd_output_fallback": False,
        },
        "machine_profile": {
            "canonical_id": context.canonical_id, "source": context.source,
            "known_computer_profile": context.known_computer_profile, "temporary": context.temporary,
            "detection_evidence": list(context.detection_evidence),
            "identity_location": _safe_relative(context.identity_path, root, "<EXPLICIT_EXTERNAL_CONFIG>/machine_identity.json"),
            "diagnostics_relative_path": _safe_relative(telemetry, root, "<EXPLICIT_EXTERNAL_DIAGNOSTICS>"),
            "recognition_role": "labels, local defaults, diagnostics, and support exports only",
            "launch_authority": "none", "computer_awareness_only": True,
            "computers_actually_tested": [],
            "computers_not_yet_tested": ["PC-ALPHA-01", "PC-ASCEND-02", "PC-DEUSEX-03"],
            "privacy": "No serial, UUID, MAC/IP, username, product key, or credential is collected or exported.",
            "development_only": True, "shipping_removal_required": True,
        },
        "environment": {
            "system": platform.system(), "release": platform.release(), "architecture": platform.machine(),
            "python": platform.python_version(), "python_implementation": platform.python_implementation(),
            "python_supported": python_supported, "dependency_mode": "Python standard library only; no bundled executable",
        },
        "project_root": "<PROJECT_ROOT>",
        "runtime": {
            "relative_path": _safe_relative(runtime, root, "<EXPLICIT_EXTERNAL_RUNTIME>"),
            "explicit_external": not runtime.is_relative_to(root),
            "writable": runtime_writable, "error": runtime_error, "free_bytes": free_bytes,
            "shared_across_recognized_computers": True, "machine_isolated": False,
        },
        "telemetry": {"relative_path": _safe_relative(telemetry, root, "<EXPLICIT_EXTERNAL_DIAGNOSTICS>"), "writable": telemetry_writable, "error": telemetry_error, "controls_launch": False},
        "support_exports": {"relative_path": _safe_relative(support, root, "<EXPLICIT_EXTERNAL_SUPPORT_EXPORTS>"), "writable": support_writable, "error": support_error, "controls_launch": False},
        "legacy_save_migration": migration,
        "database": database,
        "runtime_identity": runtime_identity,
        "manifest": runtime_identity,
        "repair_actions": repair_actions,
        "findings": findings,
        "security": {"network_probe": "none", "elevation": "not requested", "security_settings_changed": False, "antivirus_exclusion": "none", "runtime_download": "none", "raw_machine_identifier_collection": "none"},
        "shipping_boundary": {"development_computer_awareness_layer": "remove before public distribution", "computer_profile_aliases_and_hardware_family_hints": "remove before public distribution", "player_save_schema_dependency": False},
        "next_recovery_step": ("Re-extract the exact release or restore package-managed files, then run BetaEarthSovereignty.bat --self-test." if status == "blocked" else "Launch normally. Computer recognition never controls play."),
        "copyright_notice": NOTICE,
    }
    if persist_report and telemetry_writable and gate_passed:
        diagnostic_dir = telemetry / "diagnostics"
        diagnostic_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(diagnostic_dir / "PREFLIGHT_LATEST.json", report)
        atomic_write_text(diagnostic_dir / "PREFLIGHT_LATEST.txt", _preflight_text(report))
    return report


def format_preflight(report: Mapping[str, Any]) -> str:
    return _preflight_text(report)


def write_startup_failure(
    project_root: Path,
    runtime_dir: Path,
    exc: BaseException,
    *,
    phase: str,
) -> Path | None:
    """Persist one bounded, sanitized startup failure report for Export20."""

    try:
        root = project_root.resolve()
        diagnostic_dir = runtime_dir.resolve() / "diagnostics"
        diagnostic_dir.mkdir(parents=True, exist_ok=True)

        def sanitize(value: str) -> str:
            redacted = value
            replacements = (
                (str(root), "<PROJECT_ROOT>"),
                (root.as_posix(), "<PROJECT_ROOT>"),
                (str(Path.home()), "<USER_HOME>"),
                (Path.home().as_posix(), "<USER_HOME>"),
            )
            for source, target in replacements:
                if source:
                    redacted = redacted.replace(source, target)
            return redacted

        rendered = sanitize(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )[-12000:]
        summary = sanitize(f"{type(exc).__name__}: {str(exc)}")[:500]
        report = (
            "BETA EARTH STARTUP FAILURE\n"
            "==========================\n"
            f"Phase: {phase}\n"
            f"Time UTC: {datetime.now(timezone.utc).isoformat()}\n"
            f"Error: {summary}\n\n"
            f"Traceback (sanitized, bounded):\n{rendered}\n"
            "Recovery: run BetaEarthSovereignty.bat --self-test, then BetaEarthSovereignty_ExportDiagnostics.bat.\n\n"
            f"Rights: {NOTICE}\n"
        )
        path = diagnostic_dir / "STARTUP_FAILURE_LATEST.txt"
        atomic_write_text(path, report)
        return path
    except OSError:
        return None
