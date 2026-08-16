"""Asset-ID: BE-NEXT-CANONICAL-PYTHON-BACKEND | Version: 0.51.1 | Status: current.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import argparse
import sys
import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from beta_earth import __version__  # noqa: E402
from beta_earth.application.diagnostics import export_support_bundle  # noqa: E402
from beta_earth.infrastructure.playtest_evidence import (  # noqa: E402
    export_playtest_evidence,
    write_cohort_report,
)
from beta_earth.infrastructure.startup_support import (  # noqa: E402
    MachineContext,
    configure_runtime_logging,
    format_preflight,
    resolve_machine_context,
    run_preflight,
    write_startup_failure,
)
from beta_earth.presentation.cli import main as cli_main  # noqa: E402
from beta_earth.presentation.hud_server import main as hud_main  # noqa: E402
from beta_earth.presentation.startup_smoke import run_startup_smoke  # noqa: E402


CLI_ONLY_FLAGS = frozenset({"--cli", "--dry-run", "--command", "--state-json"})


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def _management_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--export-diagnostics", action="store_true")
    parser.add_argument("--playtest-guide", action="store_true")
    parser.add_argument("--playtest-report", action="store_true")
    parser.add_argument("--export-playtests", action="store_true")
    parser.add_argument("--machine-status", action="store_true")
    parser.add_argument("--launcher-help", action="store_true")
    parser.add_argument("--machine", metavar="ALIAS")
    return parser


def _print_launcher_help() -> None:
    print(
        "Beta Earth launcher modes\n"
        "=========================\n"
        "BetaEarthSovereignty.bat                 Start another browser HUD\n"
        "BetaEarthSovereignty.bat --cli           Start the terminal client\n"
        "BetaEarthSovereignty.bat --dry-run       Read-only content smoke check\n"
        "BetaEarthSovereignty.bat --preflight     Read-only portability check\n"
        "BetaEarthSovereignty.bat --repair        Repair shared saves and support folders\n"
        "BetaEarthSovereignty.bat --self-test     End-to-end unrestricted HUD test\n"
        "BetaEarthSovereignty.bat --playtest-guide\n"
        "                                           Show the measured-session workflow\n"
        "BetaEarthSovereignty.bat --playtest-report\n"
        "                                           Build the local four-family cohort report\n"
        "BetaEarthSovereignty.bat --export-playtests\n"
        "                                           Export up to 20 verified playtest items\n"
        "BetaEarthSovereignty.bat --machine-status\n"
        "                                           Show the recognized computer label\n"
        "BetaEarthSovereignty.bat --export-diagnostics\n"
        "                                           Refresh this computer's Export20\n\n"
        "Normal launch uses a private local diagnostic label. It never assigns ownership,\n"
        "blocks launch, or changes the shared project-local save path.\n"
    )


def _extract_runtime_argument(arguments: list[str]) -> tuple[Path | None, list[str]]:
    cleaned: list[str] = []
    value: str | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--runtime-dir":
            if index + 1 >= len(arguments):
                raise ValueError("--runtime-dir requires a path")
            value = arguments[index + 1]
            index += 2
            continue
        if argument.startswith("--runtime-dir="):
            value = argument.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(argument)
        index += 1
    return (Path(value).expanduser() if value else None), cleaned


def _resolve_effective_runtime(arguments: list[str], context: MachineContext) -> tuple[Path, list[str], bool]:
    requested, cleaned = _extract_runtime_argument(arguments)
    if requested is None:
        return context.runtime_dir(PROJECT_ROOT), cleaned, False
    candidate = requested if requested.is_absolute() else PROJECT_ROOT / requested
    runtime = candidate.resolve()
    immutable_roots = [PROJECT_ROOT / name for name in ("src", "content", "hud", "tools", "tests", "planning", "docs")]
    if any(runtime == path.resolve() or path.resolve() in runtime.parents for path in immutable_roots):
        raise ValueError("The explicit runtime directory may not overlap package-managed source/content folders.")
    return runtime, cleaned, not runtime.is_relative_to(PROJECT_ROOT)


def _runtime_arguments(arguments: list[str], runtime_dir: Path) -> list[str]:
    return ["--runtime-dir", str(runtime_dir), *arguments]


def _cli_arguments(arguments: list[str], context: MachineContext, runtime_dir: Path) -> list[str]:
    return [
        "--computer-id",
        context.canonical_id,
        *_runtime_arguments(arguments, runtime_dir),
    ]


def _hud_arguments(arguments: list[str], context: MachineContext, runtime_dir: Path) -> list[str]:
    return [
        "--computer-id",
        context.canonical_id,
        "--telemetry-dir",
        str(context.telemetry_dir(PROJECT_ROOT)),
        *_runtime_arguments(arguments, runtime_dir),
    ]


def _machine_status(context: MachineContext) -> None:
    print("Beta Earth computer recognition")
    print("===============================")
    print(f"Sanitized label: {context.canonical_id}")
    print(f"Recognition source: {context.source}")
    print(f"Shared player save: runtime/beta_earth.sqlite3")
    print(f"Diagnostic lane: {context.overlay_relative_path}")
    print(f"Known computer profile: {'yes' if context.known_computer_profile else 'no'}")
    print("Launch restrictions: none")
    print("Parallel HUD launches: allowed on the same or another computer")
    print("Recognition role: labels, local defaults, logs, and support exports only")
    print("Privacy: no serial, UUID, MAC/IP, username, product key, or credential is stored")
    print("Shipping boundary: remove this development recognition layer before public release")


def _run_self_test(context: MachineContext) -> int:
    runtime_dir = context.runtime_dir(PROJECT_ROOT)
    preflight = run_preflight(
        PROJECT_ROOT,
        runtime_dir=runtime_dir,
        machine_context=context,
        repair=True,
        persist_report=True,
    )
    if preflight["status"] == "blocked":
        print(format_preflight(preflight), file=sys.stderr)
        return 2
    dry_run = cli_main(["--dry-run"], project_root=PROJECT_ROOT)
    if dry_run != 0:
        return dry_run
    smoke = run_startup_smoke(PROJECT_ROOT)
    temporary_root = runtime_dir / "temp" / "self-test-export"
    if temporary_root.exists():
        shutil.rmtree(temporary_root)
    temporary_root.mkdir(parents=True, exist_ok=True)
    try:
        output = temporary_root / (
            f"UPLOAD_THIS_BetaEarth_{context.canonical_id}_Diagnostics_v{__version__}.zip"
        )
        count, errors, _ = export_support_bundle(
            PROJECT_ROOT,
            output,
            runtime_database=runtime_dir / "beta_earth.sqlite3",
            telemetry_dir=context.telemetry_dir(PROJECT_ROOT),
        )
        if count != 20 or errors:
            print(
                f"Startup self-test failed: Export20 returned {count} files and {errors!r}.",
                file=sys.stderr,
            )
            return 2
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    print(
        "Beta Earth startup self-test: OK "
        f"({len(smoke['checks'])} game and interface checks, temporary test save, "
        f"support package 20/20, recognition label {context.canonical_id}, no launch lock)"
    )
    return 0


def _print_playtest_guide() -> None:
    print(
        "Beta Earth measured beginner playtest\n"
        "=======================================\n"
        "Required Windows cohort: one first-time standard session in each family.\n"
        "Representatives: Soldier (command), Medic (support), Infiltrator (control), Protector (damage).\n\n"
        "1. Run BetaEarthSovereignty.bat --self-test on the test computer.\n"
        "2. Start the game, then begin timing before character creation with\n"
        "   PLAYTEST START FAMILY <family> MODE STANDARD EXPERIENCE FIRST-TIME.\n"
        "3. Create and confirm a matching class; the family locks at BUILD CONFIRM.\n"
        "4. Enter PLAYTEST PLAN and PLAYTEST CHECKLIST.\n"
        "5. Record defects with PLAYTEST ISSUE <severity> <category> <short note>.\n"
        "6. Complete all six PLAYTEST SURVEY fields, then PLAYTEST COMPLETE.\n"
        "7. Build the cohort report with BetaEarthSovereignty.bat --playtest-report.\n"
        "8. Export evidence with BetaEarthSovereignty.bat --export-playtests.\n\n"
        "Accessibility modes are KEYBOARD, SCREEN-READER, and LOW-VISION. They are tracked\n"
        "separately and never count as a standard cohort session. No data is uploaded automatically.\n"
    )


def _run_management(args: argparse.Namespace) -> int | None:
    if args.launcher_help:
        _print_launcher_help()
        return 0
    if args.playtest_guide:
        _print_playtest_guide()
        return 0

    context = resolve_machine_context(
        PROJECT_ROOT,
        args.machine,
        persist=bool(
            args.repair
            or args.self_test
            or args.export_diagnostics
            or args.playtest_report
            or args.export_playtests
            or args.machine_status
        ),
    )

    if args.machine_status:
        _machine_status(context)
        return 0
    if args.preflight or args.repair:
        report = run_preflight(
            PROJECT_ROOT,
            runtime_dir=context.runtime_dir(PROJECT_ROOT),
            machine_context=context,
            repair=args.repair,
            persist_report=args.repair,
        )
        print(format_preflight(report))
        if args.repair and report["status"] != "blocked":
            print("Beta Earth repair: READY — launch restrictions remain disabled")
        return 2 if report["status"] == "blocked" else 0
    if args.self_test:
        return _run_self_test(context)
    if args.playtest_report:
        runtime_dir = context.runtime_dir(PROJECT_ROOT)
        report, artifacts = write_cohort_report(
            runtime_dir / "playtests",
            content_version=__version__,
        )
        print(f"Playtest cohort status: {report['status']}")
        print(report["conclusion"])
        print(f"JSON report: {artifacts[0]}")
        print(f"Markdown report: {artifacts[2]}")
        return 0
    if args.export_playtests:
        runtime_dir = context.runtime_dir(PROJECT_ROOT)
        destination = context.support_dir(PROJECT_ROOT) / (
            f"UPLOAD_THIS_BetaEarth_{context.canonical_id}_Playtest_Evidence_v{__version__}.zip"
        )
        count, errors, output = export_playtest_evidence(
            runtime_dir / "playtests",
            destination,
            content_version=__version__,
        )
        print(f"Playtest evidence export: {'PARTIAL' if errors else 'OK'} ({count}/20 files)")
        print(f"UPLOAD THIS: {output}")
        print(f"Sidecar: {output}.sha256.txt")
        for error in errors:
            print(f"Warning: {error}")
        return 2 if errors else 0
    if args.export_diagnostics:
        runtime_dir = context.runtime_dir(PROJECT_ROOT)
        preflight = run_preflight(
            PROJECT_ROOT,
            runtime_dir=runtime_dir,
            machine_context=context,
            repair=True,
            persist_report=True,
        )
        if preflight["status"] == "blocked":
            print(format_preflight(preflight), file=sys.stderr)
            return 2
        destination = context.support_dir(PROJECT_ROOT) / (
            f"UPLOAD_THIS_BetaEarth_{context.canonical_id}_Diagnostics_v{__version__}.zip"
        )
        count, errors, output = export_support_bundle(
            PROJECT_ROOT,
            destination,
            runtime_database=runtime_dir / "beta_earth.sqlite3",
            telemetry_dir=context.telemetry_dir(PROJECT_ROOT),
        )
        print(f"Diagnostic export: {'PARTIAL' if errors else 'OK'} ({count}/20 files)")
        print(f"UPLOAD THIS: {output}")
        print(f"Sidecar: {output.with_suffix(output.suffix + '.sha256.txt')}")
        return 2 if errors else 0
    return None


def main() -> int:
    """Launch a new HUD without computer ownership or cross-computer restrictions."""

    _configure_console()
    arguments = list(sys.argv[1:])
    management, remaining = _management_parser().parse_known_args(arguments)

    # Dry-run remains read-only but still proves the exact managed release identity.
    if "--dry-run" in remaining:
        context = resolve_machine_context(PROJECT_ROOT, management.machine, persist=False)
        try:
            runtime_dir, remaining, external_runtime = _resolve_effective_runtime(remaining, context)
        except ValueError as exc:
            print(f"Startup failed: {exc}", file=sys.stderr)
            return 2
        preflight = run_preflight(PROJECT_ROOT, runtime_dir=runtime_dir, machine_context=context, repair=False, persist_report=False)
        if preflight["status"] == "blocked":
            print(format_preflight(preflight), file=sys.stderr)
            return 2
        if "--cli" in remaining:
            remaining.remove("--cli")
        print(f"Execution namespace: BetaEarthSovereignty | runtime: {'<EXPLICIT_EXTERNAL_RUNTIME>' if external_runtime else runtime_dir.relative_to(PROJECT_ROOT).as_posix()}")
        return cli_main(_runtime_arguments(remaining, runtime_dir), project_root=PROJECT_ROOT)

    managed = _run_management(management)
    if managed is not None:
        return managed

    context = resolve_machine_context(
        PROJECT_ROOT,
        management.machine,
        persist=True,
    )
    use_cli = any(argument in CLI_ONLY_FLAGS for argument in remaining)
    if "--cli" in remaining:
        remaining.remove("--cli")

    try:
        runtime_dir, remaining, external_runtime = _resolve_effective_runtime(remaining, context)
    except ValueError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 2
    telemetry_dir = context.telemetry_dir(PROJECT_ROOT)
    logger = configure_runtime_logging(telemetry_dir)
    preflight = run_preflight(
        PROJECT_ROOT,
        runtime_dir=runtime_dir,
        machine_context=context,
        repair=True,
        persist_report=True,
    )
    if preflight["status"] == "blocked":
        print(format_preflight(preflight), file=sys.stderr)
        logger.error("startup blocked by project-integrity findings computer=%s", context.canonical_id)
        return 2
    if preflight["status"] == "ready-with-warnings":
        print(
            "Startup preflight completed with warnings; see "
            f"{context.overlay_relative_path}\\diagnostics\\PREFLIGHT_LATEST.txt."
        )
    invoked = os.environ.get("BETA_EARTH_INVOKED_ENTRYPOINT", "BetaEarthSovereignty.py")
    runtime_display = "<EXPLICIT_EXTERNAL_RUNTIME>" if external_runtime else runtime_dir.relative_to(PROJECT_ROOT).as_posix()
    print(f"Execution: BetaEarthSovereignty via {invoked}")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Effective runtime: {runtime_display}")
    try:
        logger.info(
            "launcher dispatch version=%s mode=%s computer=%s source=%s restrictions=none",
            __version__,
            "cli" if use_cli else "hud",
            context.canonical_id,
            context.source,
        )
        if use_cli:
            return cli_main(_cli_arguments(remaining, context, runtime_dir), project_root=PROJECT_ROOT)
        return hud_main(_hud_arguments(remaining, context, runtime_dir), project_root=PROJECT_ROOT)
    except Exception as exc:
        failure = write_startup_failure(
            PROJECT_ROOT,
            telemetry_dir,
            exc,
            phase="launcher-dispatch",
        )
        logger.exception("unhandled startup failure")
        print(f"Beta Earth did not start: {type(exc).__name__}: {exc}", file=sys.stderr)
        if failure is not None:
            print(f"Startup report: {failure}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
