
from __future__ import annotations

import argparse
import errno
import logging
import os
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from beta_earth.application.service import GameService  # noqa: E402
from beta_earth.bootstrap import ProjectPaths, build_service, configure_logging  # noqa: E402
from beta_earth.domain.identity import PLAYER_INPUT_MAX_LENGTH, canonical_player_id, display_name_from_input  # noqa: E402
from beta_earth.infrastructure.instance_guard import SingleInstanceGuard  # noqa: E402
from beta_earth.observability import RunContext  # noqa: E402
from beta_earth.presentation.http_server import BetaEarthHTTPServer, create_server  # noqa: E402

SUPPORTED_MIN = (3, 11)
SUPPORTED_MAX_EXCLUSIVE = (3, 14)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Beta Earth clean rebuild HUD locally.")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Optional explicit loopback port. Default 0 lets Windows choose a free port safely.",
    )
    parser.add_argument("--player", default="Traveler", help="Local player profile name.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the default browser automatically.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose local logging.")
    parser.add_argument("--dry-run", action="store_true", help="Validate startup imports, config, paths, data, and port binding without opening the browser or serving forever.")
    return parser.parse_args()


def create_available_server(
    preferred: int,
    service: GameService,
    static_dir: Path,
    logger: logging.Logger,
    run: RunContext,
    *,
    span: int = 1,
) -> tuple[BetaEarthHTTPServer, int]:
    if not isinstance(preferred, int) or isinstance(preferred, bool) or not 0 <= preferred <= 65535:
        raise ValueError("Preferred port must be 0 or an integer from 1 to 65535")
    if preferred and preferred < 1024:
        raise ValueError("Explicit ports below 1024 are not supported")
    if not isinstance(span, int) or isinstance(span, bool) or span < 1:
        raise ValueError("Port search span must be a positive integer")
    if preferred == 0:
        server = create_server("127.0.0.1", 0, service, static_dir, logger, run)
        return server, int(server.server_address[1])
    upper = min(65535, preferred + span - 1)
    for port in range(preferred, upper + 1):
        try:
            return create_server("127.0.0.1", port, service, static_dir, logger, run), port
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
                continue
            raise
    raise RuntimeError(f"No free loopback port found in {preferred}-{upper}")


def _open_browser(url: str) -> None:
    try:
        webbrowser.open_new_tab(url)
    except Exception:
        pass


def main() -> int:
    if not (SUPPORTED_MIN <= sys.version_info[:2] < SUPPORTED_MAX_EXCLUSIVE):
        print("Beta Earth requires Python 3.11, 3.12, or 3.13.", file=sys.stderr)
        return 2
    args = parse_args()
    paths = ProjectPaths.discover()
    paths.ensure_runtime_dirs()
    if args.dry_run:
        run = RunContext.create()
        logger = configure_logging(paths, run, verbose=args.verbose)
        service = build_service(paths)
        server, port = create_available_server(args.port, service, paths.static, logger, run)
        try:
            snapshot = service.get_snapshot(args.player.strip() or "Traveler")
            print("Beta Earth startup dry-run: OK")
            print(f"Run ID: {run.run_id}")
            print(f"Validated root: {paths.root}")
            print(f"Validated loopback port bind: {port}")
            print(f"Initial actions: {len(snapshot.actions)}")
            print("Browser opened: NO")
            print("Server loop started: NO")
        finally:
            server.server_close()
            logger.info("Dry-run complete after %s ms", run.elapsed_ms())
        return 0

    guard = SingleInstanceGuard(paths.state, lock_id="runtime")
    if not guard.acquire():
        existing = guard.wait_for_existing_url(timeout=3.0)
        if existing:
            print("This project folder is already running. Opening its existing local HUD.")
            if not args.no_browser:
                _open_browser(existing)
            print(f"Open: {existing}")
            return 0
        print("This project folder has an active instance guard, but no HUD address was published.", file=sys.stderr)
        print("Close that Beta Earth process and run the launcher again.", file=sys.stderr)
        return 3

    run = RunContext.create()
    logger = logging.getLogger("beta_earth")
    server: BetaEarthHTTPServer | None = None
    opener: threading.Timer | None = None
    try:
        logger = configure_logging(paths, run, verbose=args.verbose)
        service = build_service(paths)
        server, port = create_available_server(args.port, service, paths.static, logger, run)
        raw_player = args.player.strip() or "Traveler"
        if len(raw_player) > PLAYER_INPUT_MAX_LENGTH:
            raise ValueError(f"Player profile input must not exceed {PLAYER_INPUT_MAX_LENGTH} characters")
        player = canonical_player_id(raw_player)
        display_name = display_name_from_input(raw_player, fallback=player)
        query = urllib.parse.urlencode({"player": raw_player, "name": display_name})
        url = f"http://127.0.0.1:{port}/?{query}"
        guard.publish(url=url, run_id=run.run_id, pid=os.getpid())
        logger.info("Beta Earth clean rebuild ready at loopback port %s", port)
        print("\nBeta Earth clean rebuild is running.")
        print(f"Run ID: {run.run_id}")
        print(f"Open: {url}")
        print("Press Ctrl+C to stop.\n")
        if not args.no_browser:
            opener = threading.Timer(0.6, _open_browser, args=(url,))
            opener.daemon = True
            opener.start()
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception:
        logger.exception("Startup or runtime failure")
        return 1
    finally:
        if opener is not None:
            opener.cancel()
        if server is not None:
            server.server_close()
        guard.release()
        logger.info("Shutdown complete after %s ms", run.elapsed_ms())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
