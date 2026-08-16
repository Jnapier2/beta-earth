"""Validate, summarize, and export bounded local playtest evidence.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from beta_earth.domain.playtest import PLAYTEST_FAMILY_CLASSES


RECEIPT_SCHEMA = "beta-earth-local-playtest-receipt-v3"
COHORT_SCHEMA = "beta-earth-playtest-cohort-readiness-v1"
MAX_EXPORT_ITEMS = 20
COPYRIGHT_NOTICE = "Copyright © 2026 Gateway Information Group LLC. All rights reserved."


class PlaytestEvidenceError(ValueError):
    """Raised when local playtest evidence cannot be trusted."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _write_with_sidecar(path: Path, data: bytes) -> tuple[Path, Path]:
    _atomic_write(path, data)
    digest = _sha256_bytes(data)
    sidecar = Path(f"{path}.sha256.txt")
    _atomic_write(sidecar, f"{digest}  {path.name}\n".encode("utf-8"))
    return path, sidecar


def _verify_sidecar(path: Path) -> bool:
    sidecar = Path(f"{path}.sha256.txt")
    if not sidecar.is_file():
        return False
    expected = f"{_sha256_file(path)}  {path.name}"
    return sidecar.read_text(encoding="utf-8").strip() == expected


def _load_receipt(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlaytestEvidenceError(f"invalid receipt {path.name}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != RECEIPT_SCHEMA:
        raise PlaytestEvidenceError(f"receipt {path.name} is not schema v3")
    timing = payload.get("timing")
    profile = payload.get("profile")
    environment = payload.get("environment")
    campaign = payload.get("campaign")
    readiness = payload.get("readiness")
    if not all(isinstance(value, dict) for value in (timing, profile, environment, campaign, readiness)):
        raise PlaytestEvidenceError(f"receipt {path.name} lacks required sections")
    session_id = timing.get("session_id")
    family = profile.get("family")
    if not isinstance(session_id, str) or not session_id.strip():
        raise PlaytestEvidenceError(f"receipt {path.name} has no session ID")
    if family not in PLAYTEST_FAMILY_CLASSES:
        raise PlaytestEvidenceError(f"receipt {path.name} has an unknown family")
    if not _verify_sidecar(path):
        raise PlaytestEvidenceError(f"receipt {path.name} has no valid SHA-256 sidecar")
    return payload


def _generated_sort_key(payload: dict[str, object], path: Path) -> tuple[str, float, str]:
    generated = payload.get("generated_at_utc")
    return (
        generated if isinstance(generated, str) else "",
        path.stat().st_mtime,
        path.name,
    )


def discover_receipts(playtest_dir: Path) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Load trusted v3 receipts; return evidence rows and bounded rejection notes."""

    trusted: list[dict[str, object]] = []
    rejected: list[dict[str, str]] = []
    if not playtest_dir.is_dir():
        return trusted, rejected
    candidates = sorted(
        path
        for path in playtest_dir.glob("BetaEarth_Playtest_*_v*.json")
        if "Cohort_Readiness" not in path.name and not path.name.startswith("LATEST_")
    )
    for path in candidates:
        try:
            payload = _load_receipt(path)
        except PlaytestEvidenceError as exc:
            rejected.append({"file": path.name, "reason": str(exc)[:240]})
            continue
        trusted.append(
            {
                "path": path,
                "payload": payload,
                "sha256": _sha256_file(path),
                "sidecar_valid": True,
            }
        )
    return trusted, rejected


def _candidate_windows_first_time_standard(payload: dict[str, object]) -> bool:
    profile = payload["profile"]
    environment = payload["environment"]
    timing = payload["timing"]
    campaign = payload["campaign"]
    readiness = payload["readiness"]
    assert isinstance(profile, dict)
    assert isinstance(environment, dict)
    assert isinstance(timing, dict)
    assert isinstance(campaign, dict)
    assert isinstance(readiness, dict)
    return bool(
        timing.get("status") == "completed"
        and campaign.get("complete") is True
        and readiness.get("receipt_complete") is True
        and readiness.get("profile_valid") is True
        and environment.get("os_family") == "Windows"
        and environment.get("native_windows_launcher") is True
        and profile.get("experience") == "first_time"
        and profile.get("mode") == "standard"
    )


def select_family_receipts(
    trusted: Iterable[dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Select the latest eligible Windows first-time standard receipt per family."""

    selected: dict[str, dict[str, object]] = {}
    for row in trusted:
        payload = row["payload"]
        path = row["path"]
        assert isinstance(payload, dict)
        assert isinstance(path, Path)
        if not _candidate_windows_first_time_standard(payload):
            continue
        profile = payload["profile"]
        assert isinstance(profile, dict)
        family = str(profile["family"])
        existing = selected.get(family)
        if existing is None:
            selected[family] = row
            continue
        existing_payload = existing["payload"]
        existing_path = existing["path"]
        assert isinstance(existing_payload, dict)
        assert isinstance(existing_path, Path)
        if _generated_sort_key(payload, path) > _generated_sort_key(existing_payload, existing_path):
            selected[family] = row
    return selected


def _issue_counters(payloads: Iterable[dict[str, object]]) -> tuple[Counter[str], Counter[str]]:
    severities: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    for payload in payloads:
        issues = payload.get("issues", [])
        if not isinstance(issues, list):
            continue
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            severities[str(issue.get("severity", "unknown"))] += 1
            categories[str(issue.get("category", "other"))] += 1
    return severities, categories


def build_cohort_report(playtest_dir: Path, *, content_version: str) -> dict[str, object]:
    trusted, rejected = discover_receipts(playtest_dir)
    selected = select_family_receipts(trusted)
    families: dict[str, dict[str, object]] = {}
    selected_payloads: list[dict[str, object]] = []
    active_minutes: list[float] = []
    for family in PLAYTEST_FAMILY_CLASSES:
        row = selected.get(family)
        if row is None:
            families[family] = {
                "status": "missing",
                "receipt": None,
                "reason": "No completed, profile-valid, native-Windows, first-time, standard-mode receipt was found.",
            }
            continue
        payload = row["payload"]
        path = row["path"]
        assert isinstance(payload, dict)
        assert isinstance(path, Path)
        selected_payloads.append(payload)
        timing = payload["timing"]
        profile = payload["profile"]
        readiness = payload["readiness"]
        survey = payload.get("survey", {})
        issues = payload.get("issues", [])
        assert isinstance(timing, dict)
        assert isinstance(profile, dict)
        assert isinstance(readiness, dict)
        active_seconds = float(timing.get("active_seconds", 0.0) or 0.0)
        active_minutes.append(active_seconds / 60.0)
        blocking = int(readiness.get("blocking_issue_count", 0) or 0)
        families[family] = {
            "status": "blocked" if blocking else "complete",
            "receipt": path.name,
            "sha256": row["sha256"],
            "session_id": timing.get("session_id"),
            "class_id": profile.get("class_id"),
            "class_name": profile.get("class_name"),
            "active_seconds": round(active_seconds, 3),
            "active_minutes": round(active_seconds / 60.0, 2),
            "idle_seconds": timing.get("idle_seconds", 0),
            "paused_seconds": timing.get("paused_seconds", 0),
            "survey": survey if isinstance(survey, dict) else {},
            "issue_count": len(issues) if isinstance(issues, list) else 0,
            "blocking_issue_count": blocking,
        }
    missing = [family for family, row in families.items() if row["status"] == "missing"]
    blocked = [family for family, row in families.items() if row["status"] == "blocked"]
    severities, categories = _issue_counters(selected_payloads)
    if missing:
        status = "insufficient-evidence"
        conclusion = "The four-family Windows first-time cohort is incomplete. No global pacing or release decision is supported."
    elif blocked:
        status = "blocked"
        conclusion = "All four families are represented, but blocking issues require repair and retest before a readiness decision."
    else:
        status = "cohort-complete-review-required"
        conclusion = "All four required Windows first-time standard sessions are present with no blocking issue. Human review is still required before tuning or release decisions."
    timing_summary: dict[str, object] = {
        "session_count": len(active_minutes),
        "target_minutes": 120,
        "median_active_minutes": round(statistics.median(active_minutes), 2) if active_minutes else None,
        "minimum_active_minutes": round(min(active_minutes), 2) if active_minutes else None,
        "maximum_active_minutes": round(max(active_minutes), 2) if active_minutes else None,
    }
    return {
        "schema": COHORT_SCHEMA,
        "project": "MUDD Game Development",
        "release": "Beta Earth: Sovereignty Next",
        "content_version": content_version,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "conclusion": conclusion,
        "required_families": list(PLAYTEST_FAMILY_CLASSES),
        "families": families,
        "missing_families": missing,
        "blocked_families": blocked,
        "timing": timing_summary,
        "issues": {
            "by_severity": dict(sorted(severities.items())),
            "by_category": dict(sorted(categories.items())),
        },
        "receipt_inventory": {
            "trusted_v3_receipts": len(trusted),
            "selected_receipts": len(selected),
            "rejected_receipts": rejected[:20],
        },
        "decision_boundary": {
            "automatic_release_approval": False,
            "automatic_balance_change": False,
            "human_review_required": True,
            "notes": "This report measures evidence coverage. It does not substitute for accessibility review, antivirus scanning, signing, licensing review, or public QA.",
        },
        "privacy": {
            "local_only": True,
            "network_reporting": False,
            "contains_raw_commands": False,
            "contains_credentials": False,
            "contains_absolute_paths": False,
        },
        "copyright": COPYRIGHT_NOTICE,
    }


def cohort_markdown(report: dict[str, object]) -> bytes:
    timing = report.get("timing") if isinstance(report.get("timing"), dict) else {}
    families = report.get("families") if isinstance(report.get("families"), dict) else {}
    issues = report.get("issues") if isinstance(report.get("issues"), dict) else {}
    lines = [
        "# Beta Earth — Four-Family Beginner Cohort Readiness",
        "",
        f"- Content version: `{report.get('content_version', 'unknown')}`",
        f"- Generated UTC: `{report.get('generated_at_utc', 'unspecified')}`",
        f"- Status: **{str(report.get('status', 'unknown')).replace('-', ' ')}**",
        "",
        str(report.get("conclusion", "")),
        "",
        "## Family Coverage",
        "",
        "| Gameplay family | Status | Class | Active minutes | Blocking issues |",
        "|---|---|---|---:|---:|",
    ]
    for family in PLAYTEST_FAMILY_CLASSES:
        row = families.get(family, {}) if isinstance(families, dict) else {}
        if not isinstance(row, dict):
            row = {}
        lines.append(
            f"| {family} | {row.get('status', 'missing')} | {row.get('class_name') or row.get('class_id') or '—'} | "
            f"{row.get('active_minutes') if row.get('active_minutes') is not None else '—'} | {row.get('blocking_issue_count', 0)} |"
        )
    lines.extend(
        (
            "",
            "## Timing",
            "",
            f"- Sessions selected: **{timing.get('session_count', 0)}/4**",
            f"- Median active time: **{timing.get('median_active_minutes', '—')} minutes**",
            f"- Range: **{timing.get('minimum_active_minutes', '—')}–{timing.get('maximum_active_minutes', '—')} minutes**",
            "",
            "## Issues",
            "",
            f"- By severity: `{json.dumps(issues.get('by_severity', {}), sort_keys=True)}`",
            f"- By category: `{json.dumps(issues.get('by_category', {}), sort_keys=True)}`",
            "",
            "## Decision Boundary",
            "",
            "This report never approves a public release or balance change automatically. Human review remains required.",
            "",
            "Copyright © 2026 Gateway Information Group LLC. All rights reserved.",
            "",
        )
    )
    return "\n".join(lines).encode("utf-8")


def write_cohort_report(playtest_dir: Path, *, content_version: str) -> tuple[dict[str, object], tuple[Path, ...]]:
    report = build_cohort_report(playtest_dir, content_version=content_version)
    base = playtest_dir / f"BetaEarth_Playtest_Cohort_Readiness_v{content_version}"
    json_data = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    json_path, json_sidecar = _write_with_sidecar(Path(f"{base}.json"), json_data)
    md_path, md_sidecar = _write_with_sidecar(Path(f"{base}.md"), cohort_markdown(report))
    return report, (json_path, json_sidecar, md_path, md_sidecar)


def _selected_receipt_artifacts(playtest_dir: Path, report: dict[str, object]) -> list[Path]:
    artifacts: list[Path] = []
    families = report.get("families")
    if not isinstance(families, dict):
        return artifacts
    for family in PLAYTEST_FAMILY_CLASSES:
        row = families.get(family)
        if not isinstance(row, dict):
            continue
        filename = row.get("receipt")
        if not isinstance(filename, str) or not filename:
            continue
        json_path = playtest_dir / filename
        md_path = json_path.with_suffix(".md")
        for path in (
            json_path,
            Path(f"{json_path}.sha256.txt"),
            md_path,
            Path(f"{md_path}.sha256.txt"),
        ):
            if path.is_file():
                artifacts.append(path)
    return artifacts


def export_playtest_evidence(
    playtest_dir: Path,
    destination: Path,
    *,
    content_version: str,
) -> tuple[int, list[str], Path]:
    """Write a deterministic ≤20-item ZIP using temp → CRC → atomic finalize."""

    report, report_artifacts = write_cohort_report(
        playtest_dir, content_version=content_version
    )
    artifacts = [*report_artifacts, *_selected_receipt_artifacts(playtest_dir, report)]
    # De-duplicate while preserving the deterministic family/report order.
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in artifacts:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    errors: list[str] = []
    if len(unique) > MAX_EXPORT_ITEMS:
        errors.append(f"artifact selection exceeded {MAX_EXPORT_ITEMS}; truncated deterministically")
        unique = unique[:MAX_EXPORT_ITEMS]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = json.dumps(
                {
                    "asset_id": "BE-NEXT-PLAYTEST-EVIDENCE-ZIP",
                    "project": "MUDD Game Development",
                    "version": content_version,
                    "status": report.get("status"),
                    "copyright_notice": COPYRIGHT_NOTICE,
                    "item_limit": MAX_EXPORT_ITEMS,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            for path in unique:
                archive.write(path, arcname=path.name)
        with zipfile.ZipFile(temporary, "r") as archive:
            bad = archive.testzip()
            if bad is not None:
                raise PlaytestEvidenceError(f"playtest evidence ZIP CRC failed at {bad}")
            names = archive.namelist()
            if len(names) != len(unique) or len(set(names)) != len(names):
                raise PlaytestEvidenceError("playtest evidence ZIP inventory is inconsistent")
            if len(names) > MAX_EXPORT_ITEMS:
                raise PlaytestEvidenceError("playtest evidence ZIP exceeds the item limit")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    sidecar = Path(f"{destination}.sha256.txt")
    _atomic_write(
        sidecar,
        f"{_sha256_file(destination)}  {destination.name}\n".encode("utf-8"),
    )
    return len(unique), errors, destination
