from __future__ import annotations

from pathlib import Path

from beta_earth.application.service import GameService
from beta_earth.domain.models import Stats
from beta_earth.infrastructure.economy_loader import JsonEconomyRepository
from beta_earth.infrastructure.json_store import JsonPlayerRepository
from beta_earth.infrastructure.quest_loader import JsonQuestRepository
from beta_earth.infrastructure.world_loader import JsonWorldRepository

ROOT = Path(__file__).resolve().parents[1]


class FixedRandom:
    def roll_stats(self) -> Stats:
        return Stats(14, 12, 9, 11, 10)


def build_test_service(state_dir: Path) -> GameService:
    return GameService(
        JsonWorldRepository(ROOT / "data" / "world.json"),
        JsonEconomyRepository(ROOT / "data" / "economy.json"),
        JsonQuestRepository(ROOT / "data" / "quests.json"),
        JsonPlayerRepository(state_dir),
        FixedRandom(),
    )


def start_active(service: GameService, player: str = "player"):
    snapshot = service.get_snapshot(player)
    snapshot = service.execute(player, "gender female", expected_revision=snapshot.state.revision)
    snapshot = service.execute(player, "balancedstats", expected_revision=snapshot.state.revision)
    return service.execute(player, "begin", expected_revision=snapshot.state.revision)

def complete_route_mission(service: GameService, player: str = "player"):
    snapshot = start_active(service, player)
    for command in (
        "talk Caroline",
        "accept route mission",
        "go east",
        "listen threshold",
        "go east",
        "go south",
        "mark return route",
        "go north",
        "go west",
        "go west",
        "report route to Caroline",
    ):
        snapshot = service.execute(player, command, expected_revision=snapshot.state.revision)
    return snapshot

