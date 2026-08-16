"""Portable terminal adapter. Runtime data stays beneath the project by default."""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from beta_earth.application.engine import GameEngine
from beta_earth.application.service import GameApplication
from beta_earth.domain.clock import SystemClock
from beta_earth.domain.model import GameState
from beta_earth.infrastructure.content_loader import ContentError, load_catalog
from beta_earth.infrastructure.sqlite_store import SQLiteStateStore, StoreConflict


SOURCE_CHECKOUT_ROOT = Path(__file__).resolve().parents[3]


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Beta Earth: Sovereignty Next",
        description="Run the local progression-planning preview.",
    )
    parser.add_argument("--player", help="character name")
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help="explicit runtime directory (defaults to <project>/runtime)",
    )
    parser.add_argument("--seed", type=int, help="deterministic combat seed")
    parser.add_argument("--computer-id", default="PC-LOCAL-UNASSIGNED", help=argparse.SUPPRESS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate content and execute an in-memory smoke path without saving",
    )
    parser.add_argument(
        "--command",
        action="append",
        dest="command_list",
        metavar="TEXT",
        help="run one command non-interactively; repeat for a sequence",
    )
    parser.add_argument(
        "--state-json",
        action="store_true",
        help="emit a stable @state JSON projection after opening and each command",
    )
    return parser


def _emit_client_state(engine: GameEngine, state: GameState) -> None:
    print(
        "@state "
        + json.dumps(
            engine.client_state(state),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _dry_run(project_root: Path, seed: int) -> int:
    from beta_earth.domain.clock import ManualClock

    catalog = load_catalog(project_root / "content")
    clock = ManualClock()
    engine = GameEngine(catalog, clock, random.Random(seed))
    state = engine.new_game("Verification", foundation_pending=True)
    for command in (
        "build class soldier",
        "build auto",
        "build tutorial skip",
        "build confirm",
        "look",
        "inventory",
        "stance",
        "help attack",
    ):
        result = engine.execute(state, command)
        if not result.lines:
            raise RuntimeError(f"dry-run command produced no output: {command}")
    course_label = "course" if len(catalog.courses) == 1 else "courses"
    print(
        "Beta Earth: Sovereignty Next dry-run: OK "
        f"({len(catalog.rooms)} rooms, {len(catalog.items)} items, "
        f"{len(catalog.creatures)} creatures, "
        f"{len(catalog.courses)} {course_label})"
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path | None = None,
) -> int:
    _configure_console()
    if not ((3, 11) <= sys.version_info[:2] < (3, 14)):
        print(
            "Startup failed: Python 3.11, 3.12, or 3.13 is required; "
            f"found {sys.version_info.major}.{sys.version_info.minor}.",
            file=sys.stderr,
        )
        return 2
    root = (project_root or SOURCE_CHECKOUT_ROOT).resolve()
    args = build_parser().parse_args(argv)
    try:
        if args.dry_run:
            return _dry_run(root, args.seed if args.seed is not None else 41)
        catalog = load_catalog(root / "content")
        runtime_dir = (args.runtime_dir or (root / "runtime")).resolve()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        store = SQLiteStateStore(runtime_dir / "beta_earth.sqlite3")
        engine = GameEngine(
            catalog,
            SystemClock(),
            random.Random(args.seed) if args.seed is not None else random.Random(),
        )
        app = GameApplication(
            engine,
            store,
            receipt_context={
                "os_family": platform.system() or "unknown",
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "launch_surface": "cli",
                "computer_label": args.computer_id,
                "native_windows_launcher": bool(
                    platform.system() == "Windows"
                    and os.environ.get("BETA_EARTH_LAUNCHED_BY_BAT") == "1"
                ),
            },
        )
        player_name = args.player
        if not player_name:
            player_name = input("Character name: ").strip()
        session = app.open_session(player_name)
        if session.created:
            opening_text = engine.welcome(session.state)
        else:
            opening_text = (
                f"Welcome back, {session.state.character.name}.\n\n"
                f"{engine.render_room(session.state)}"
            )
    except (ContentError, ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 2

    print(opening_text)
    if args.state_json:
        _emit_client_state(engine, session.state)

    scripted = list(args.command_list) if args.command_list else None
    if scripted is not None:
        for raw in scripted:
            command = raw.strip()
            if not command:
                continue
            print(f"\n> {command}")
            try:
                result = session.execute(command)
            except (StoreConflict, OSError, sqlite3.Error, ValueError) as exc:
                print(f"Save conflict: {exc}", file=sys.stderr)
                return 3
            print(result.text)
            if args.state_json:
                _emit_client_state(engine, session.state)
            if result.quit:
                break
        return 0

    while True:
        try:
            raw = input("\n> ")
            result = session.execute(raw)
        except (EOFError, KeyboardInterrupt):
            print("\nYour progress is safe. Until next time.")
            return 0
        except (StoreConflict, OSError, sqlite3.Error, ValueError) as exc:
            print(f"Save failed: {exc}. Restart this session before continuing.")
            return 3
        print(result.text)
        if args.state_json:
            _emit_client_state(engine, session.state)
        if result.quit:
            return 0
