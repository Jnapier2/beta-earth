from __future__ import annotations

from dataclasses import dataclass, replace

from beta_earth.domain.economy import (
    EconomyCatalog,
    EconomyView,
    apply_economy_command,
    build_economy_view,
    validate_inventory_references,
)
from beta_earth.domain.identity import display_name_from_input
from beta_earth.domain.equipment import DIDReadinessPreview, build_did_readiness_preview
from beta_earth.domain.models import Action, PlayerState, Room, SetupStage, Stats, World
from beta_earth.domain.quests import (
    QuestCatalog,
    QuestJournal,
    apply_quest_command,
    build_quest_journal,
    validate_quest_log_references,
)
from beta_earth.domain.rules import available_actions, command_alias, normalize_command, room_summary, validate_action_surface

from .ports import EconomyRepository, PlayerRepository, QuestRepository, RandomSource, RevisionConflict, WorldRepository


class GameError(RuntimeError):
    pass


class InvalidCommand(GameError):
    pass


@dataclass(frozen=True, slots=True)
class GameSnapshot:
    state: PlayerState
    room: Room
    actions: tuple[Action, ...]
    quest_journal: QuestJournal
    economy: EconomyView
    did_readiness: DIDReadinessPreview
    world_title: str


class GameService:
    def __init__(
        self,
        world_repository: WorldRepository,
        economy_repository: EconomyRepository,
        quest_repository: QuestRepository,
        players: PlayerRepository,
        random_source: RandomSource,
    ) -> None:
        self._world: World = world_repository.load()
        self._economy: EconomyCatalog = economy_repository.load(self._world)
        self._quests: QuestCatalog = quest_repository.load(self._world, self._economy)
        self._players = players
        self._random = random_source

    @property
    def world(self) -> World:
        return self._world

    @property
    def economy(self) -> EconomyCatalog:
        return self._economy

    @property
    def quests(self) -> QuestCatalog:
        return self._quests

    def get_snapshot(self, player_id: str, display_name: str | None = None) -> GameSnapshot:
        state = self._players.load(player_id)
        if state is None:
            new_state = self._new_player_state(player_id, display_name)
            try:
                state = self._players.save(new_state, expected_revision=-1)
            except RevisionConflict:
                state = self._players.load(player_id)
                if state is None:
                    raise
        return self._snapshot(state)

    def execute(self, player_id: str, command: str, expected_revision: int) -> GameSnapshot:
        current = self.get_snapshot(player_id).state
        if current.revision != expected_revision:
            raise RevisionConflict(f"stale revision {expected_revision}; current revision is {current.revision}")

        normalized = command_alias(command)
        allowed = {
            normalize_command(action.command): action
            for action in available_actions(current, self._world, self._quests, self._economy)
            if action.enabled
        }
        if normalized not in allowed:
            raise InvalidCommand("That command is not available in the current state.")

        next_state = self._apply(current, normalized)
        persisted = self._players.save(next_state, expected_revision=current.revision)
        return self._snapshot(persisted)

    def reset(
        self,
        player_id: str,
        expected_revision: int,
        display_name: str | None = None,
    ) -> GameSnapshot:
        current = self.get_snapshot(player_id).state
        if current.revision != expected_revision:
            raise RevisionConflict(f"stale revision {expected_revision}; current revision is {current.revision}")
        # Preserve monotonic revision history. Deleting and recreating at revision zero would allow
        # an ancient stale browser tab to pass an ABA-style revision check after a reset.
        fresh = self._new_player_state(player_id, display_name or current.display_name)
        persisted = self._players.save(fresh, expected_revision=current.revision)
        return self._snapshot(persisted)

    def _new_player_state(self, player_id: str, display_name: str | None) -> PlayerState:
        return PlayerState(
            player_id=player_id,
            display_name=display_name_from_input(display_name, fallback=player_id),
            stage=SetupStage.IDENTITY,
            room_id=self._world.start_room,
            last_message="Your limiter flickers awake. Choose how this life will address you.",
        )

    def _snapshot(self, state: PlayerState) -> GameSnapshot:
        if state.room_id not in self._world.rooms:
            raise GameError(f"Player state references missing room: {state.room_id}")
        unknown_quests = validate_quest_log_references(state, self._quests)
        if unknown_quests:
            raise GameError(f"Player state references missing quests: {', '.join(unknown_quests)}")
        inventory_issues = validate_inventory_references(state, self._economy)
        if inventory_issues:
            raise GameError("Player state contains unresolved economy references: " + "; ".join(inventory_issues))
        if state.stage == SetupStage.IDENTITY and state.identity is not None:
            raise GameError("Identity-selection state cannot already contain a selected identity")
        if state.stage != SetupStage.IDENTITY and state.identity is None:
            raise GameError("Player state is missing the identity required after identity selection")
        if state.stage == SetupStage.ACTIVE and state.room_id not in state.visited_rooms:
            raise GameError(
                f"Active player state is inconsistent: current room {state.room_id!r} is not in visited_rooms"
            )
        actions = available_actions(state, self._world, self._quests, self._economy)
        issues = validate_action_surface(actions)
        if issues:
            raise GameError("Invalid action surface: " + "; ".join(issues))
        return GameSnapshot(
            state=state,
            room=self._world.rooms[state.room_id],
            actions=actions,
            quest_journal=build_quest_journal(state, self._world, self._quests),
            economy=build_economy_view(state, self._economy),
            did_readiness=build_did_readiness_preview(state),
            world_title=self._world.title,
        )

    def _apply(self, state: PlayerState, command: str) -> PlayerState:
        if state.stage == SetupStage.IDENTITY:
            identity = command.removeprefix("gender ").strip()
            return replace(
                state,
                identity=identity,
                stage=SetupStage.ATTRIBUTES,
                last_message=f"Identity recorded: {identity}. Your limiter is ready to establish a baseline.",
            )

        if command == "rollstats":
            return replace(
                state,
                stats=self._random.roll_stats(),
                stage=SetupStage.READY,
                last_message="The DID resolves a viable attribute pattern. Review it, then awaken.",
            )

        if command == "balancedstats":
            return replace(
                state,
                stats=Stats(),
                stage=SetupStage.READY,
                last_message="A balanced attribute pattern is locked in. You are ready to awaken.",
            )

        if command == "begin":
            room = self._world.rooms[self._world.start_room]
            return replace(
                state,
                stage=SetupStage.ACTIVE,
                room_id=self._world.start_room,
                visited_rooms=tuple(sorted(set(state.visited_rooms).union({self._world.start_room}))),
                last_message=f"You awaken in {room.name}. {room_summary(room)}",
            )

        if state.stage != SetupStage.ACTIVE:
            raise InvalidCommand("That command is not valid during character setup.")

        quest_result = apply_quest_command(state, command, self._quests)
        if quest_result is not None:
            return quest_result
        economy_result = apply_economy_command(state, command, self._economy)
        if economy_result is not None:
            return economy_result

        room = self._world.rooms[state.room_id]
        if command == "look":
            return replace(state, last_message=room_summary(room))
        if command == "help":
            return replace(
                state,
                last_message=(
                    "Every command currently available is shown as a button and selectable command text. "
                    "The mission journal identifies the next objective without hiding valid choices. "
                    "Inventory and barter use the same revision-guarded current-action contract. "
                    "Number keys activate visible actions when focus is not inside the command field."
                ),
            )
        if command.startswith("go "):
            direction = command.removeprefix("go ").strip()
            destination_id = room.exits.get(direction)
            if not destination_id:
                raise InvalidCommand("That exit is not available.")
            destination = self._world.rooms[destination_id]
            visited = tuple(sorted(set(state.visited_rooms).union({destination_id})))
            return replace(
                state,
                room_id=destination_id,
                visited_rooms=visited,
                last_message=f"You move {direction}. {room_summary(destination)}",
            )

        for interaction in room.interactions:
            if normalize_command(interaction.command) != command:
                continue
            flags = tuple(sorted(set(state.flags).union(interaction.grants_flags)))
            return replace(state, flags=flags, last_message=interaction.message)

        raise InvalidCommand("No handler exists for the selected current action.")
