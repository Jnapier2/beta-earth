from __future__ import annotations

import logging
from datetime import datetime
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from beta_earth.application.service import GameService
from beta_earth.infrastructure.economy_loader import JsonEconomyRepository
from beta_earth.infrastructure.json_store import JsonPlayerRepository, SystemRandomSource
from beta_earth.infrastructure.quest_loader import JsonQuestRepository
from beta_earth.infrastructure.runtime_paths import ensure_project_local_directory
from beta_earth.infrastructure.world_loader import JsonWorldRepository
from beta_earth.observability import RunContext
from beta_earth.timekeeping import USER_TIMEZONE, USER_TIMEZONE_SOURCE

@dataclass(frozen=True, slots=True)
class ProjectPaths:
    root: Path
    data: Path
    static: Path
    state: Path
    logs: Path
    diagnostics: Path
    exports: Path
    temp: Path

    @classmethod
    def discover(cls) -> "ProjectPaths":
        root = Path(__file__).resolve().parents[2]
        return cls(
            root=root,
            data=root / "data",
            static=root / "static",
            state=root / "state",
            logs=root / "logs",
            diagnostics=root / "diagnostics",
            exports=root / "exports",
            temp=root / "temp",
        )

    def ensure_runtime_dirs(self) -> None:
        for path in (self.state, self.logs, self.diagnostics, self.exports, self.temp):
            ensure_project_local_directory(self.root, path)


class ProjectTimeFormatter(logging.Formatter):
    @staticmethod
    def converter(timestamp: float):
        return datetime.fromtimestamp(timestamp, USER_TIMEZONE).timetuple()


def configure_logging(paths: ProjectPaths, run: RunContext, *, verbose: bool = False) -> logging.Logger:
    paths.ensure_runtime_dirs()
    logger = logging.getLogger("beta_earth")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = ProjectTimeFormatter(
        f"%(asctime)s [{USER_TIMEZONE_SOURCE}] %(levelname)s run={run.run_id} %(message)s"
    )
    file_handler = RotatingFileHandler(
        paths.logs / "beta_earth.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


def build_service(paths: ProjectPaths | None = None) -> GameService:
    resolved = paths or ProjectPaths.discover()
    resolved.ensure_runtime_dirs()
    return GameService(
        JsonWorldRepository(resolved.data / "world.json"),
        JsonEconomyRepository(resolved.data / "economy.json"),
        JsonQuestRepository(resolved.data / "quests.json"),
        JsonPlayerRepository(resolved.state / "players"),
        SystemRandomSource(),
    )
