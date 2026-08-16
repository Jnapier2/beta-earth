"""Bounded compatibility services extracted from the legacy application engine.

The services own coherent command families while delegating shared state and
cross-service calls through :class:`EngineService`. This keeps the established
GameEngine API stable while reducing the monolithic implementation surface.
"""

from __future__ import annotations

from collections import deque
import difflib
import math
import re

from beta_earth.application.contracts import (
    MAX_BULK_SELECTION,
    TUTORIAL_EVIDENCE_PREFIX as _TUTORIAL_EVIDENCE_PREFIX,
)
from beta_earth.application.parser import ParsedCommand
from beta_earth.application.results import HandlerResult as _HandlerResult
from beta_earth.application.selection import RelativeSelector, Scope, parse_selection
from beta_earth.application.text import natural_list as _natural_list
from beta_earth.domain.battle_ai import sol_recovery_seconds
from beta_earth.domain.battlefield import companion_actor_id
from beta_earth.domain.combat import effective_item_definition
from beta_earth.domain.encumbrance import (
    encumbrance as calculate_encumbrance,
    item_bulk,
)
from beta_earth.domain.events import DomainEvent
from beta_earth.domain.model import CompanionProgressState, CreatureState, ItemState
from beta_earth.domain.progression import (
    INSIGHT_PER_LEVEL,
    award_field_insight,
    buy_training_rank,
    choose_training_profile,
    effective_training_cost,
    refund_training_rank,
)
from beta_earth.domain.recovery import disabled_limbs

from beta_earth.application.services.base import EngineService

class WorldService(EngineService):
    """Owns the world application boundary."""

    def render_room(self, state: GameState, compact: bool = False) -> str:
        room = self.catalog.rooms[state.character.room_id]
        lines = [f"[{room.title}]", self._room_description(state)]
        if not compact:
            item_names = [
                self.catalog.items[item.definition_id].name
                for item in state.room_items.get(room.id, [])
            ]
            creature_names = [
                self.catalog.creatures[creature.definition_id].name
                for creature in state.creatures.get(room.id, [])
                if creature.health > 0
            ]
            if item_names:
                lines.append(f"You also see {_natural_list(item_names)}.")
            if creature_names:
                lines.append(f"Nearby: {_natural_list(creature_names)}.")
        npc_names = [npc.name for npc in self._story_npcs_in_room(state)]
        if npc_names:
            lines.append(f"Present: {_natural_list(npc_names)}.")
        exits = [
            direction if self._exit_is_available(state, room.id, direction)
            else f"{direction} (sealed)"
            for direction in room.exits
        ]
        lines.append(f"Obvious exits: {_natural_list(exits) if exits else 'none'}.")
        if state.incapacitation is not None:
            lines.append(
                "You are incapacitated here; SIGNAL or wait until RECOVER is available."
            )
        return "\n".join(lines)

    def render_room_revisit(self, state: GameState) -> str:
        """Render a concise revisit while preserving all live tactical information."""

        room = self.catalog.rooms[state.character.room_id]
        lines = [
            f"[{room.title}]",
            "Familiar ground. Use LOOK for the full location description.",
        ]
        overlays = [
            text
            for flag, text in room.story_overlays.items()
            if flag in state.flags
        ]
        lines.extend(overlays)
        item_names = [
            self.catalog.items[item.definition_id].name
            for item in state.room_items.get(room.id, [])
        ]
        creature_names = [
            self.catalog.creatures[creature.definition_id].name
            for creature in state.creatures.get(room.id, [])
            if creature.health > 0
        ]
        if item_names:
            lines.append(f"You also see {_natural_list(item_names)}.")
        if creature_names:
            lines.append(f"Nearby: {_natural_list(creature_names)}.")
        npc_names = [npc.name for npc in self._story_npcs_in_room(state)]
        if npc_names:
            lines.append(f"Present: {_natural_list(npc_names)}.")
        exits = [
            direction if self._exit_is_available(state, room.id, direction)
            else f"{direction} (sealed)"
            for direction in room.exits
        ]
        lines.append(f"Obvious exits: {_natural_list(exits) if exits else 'none'}.")
        if state.incapacitation is not None:
            lines.append(
                "You are incapacitated here; SIGNAL or wait until RECOVER is available."
            )
        return "\n".join(lines)

    def _look(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        if command.args:
            return self._examine(state, command, now)
        return _HandlerResult(
            (self.render_room(state),),
            (
                DomainEvent(
                    "world.looked",
                    {"room_id": state.character.room_id},
                ),
            ),
        )

    def _examine(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        if not query:
            return _HandlerResult(("Examine what?",))
        room = self.catalog.rooms[state.character.room_id]
        selection = parse_selection(query)
        npc, _ = self._resolve_npc(state, selection.terms)
        if (
            npc is not None
            and selection.scope in {Scope.DEFAULT, Scope.ROOM}
            and not selection.pronoun
        ):
            score = state.story.relationships.get(npc.id, 0)
            return _HandlerResult(
                (
                    npc.description,
                    f"{npc.relationship_label}: {score:+d} "
                    f"({self._relationship_descriptor(score)}).",
                    f"Use TALK {npc.name.upper()} to speak with them.",
                )
            )
        detail_matches = [
            text
            for noun, text in room.details.items()
            if noun == selection.terms or noun.startswith(selection.terms)
        ]
        if (
            len(detail_matches) == 1
            and selection.scope in {Scope.DEFAULT, Scope.ROOM}
            and not selection.pronoun
        ):
            return _HandlerResult((detail_matches[0],))
        items, _ = self._resolve_items(
            state,
            query,
            default_scope=None,
            allowed_scopes={Scope.ROOM, Scope.INVENTORY, Scope.EQUIPPED},
        )
        if items:
            item_state = items[0]
            definition = self.catalog.items[item_state.definition_id]
            lines = [definition.description]
            if definition.slot:
                lines.append(
                    f"It can be equipped in {definition.slot.replace('_', ' ')}."
                )
            if item_state.durability is not None:
                lines.append(
                    f"Condition: {item_state.durability}/{definition.max_durability} durability."
                )
            changed = self._set_reference(
                state,
                "item",
                item_state.instance_id,
            )
            events = (
                (
                    DomainEvent(
                        "context.reference_changed",
                        {
                            "kind": "item",
                            "instance_id": item_state.instance_id,
                        },
                    ),
                )
                if changed
                else ()
            )
            return _HandlerResult(tuple(lines), events, changed)
        creature, _ = self._resolve_creature(state, query)
        if creature:
            definition = self.catalog.creatures[creature.definition_id]
            condition = max(0, creature.health) / definition.max_health
            condition_text = (
                "unhurt"
                if condition >= 0.95
                else "wounded"
                if condition >= 0.45
                else "badly wounded"
            )
            changed = self._set_reference(
                state,
                "creature",
                creature.instance_id,
            )
            events = (
                (
                    DomainEvent(
                        "context.reference_changed",
                        {
                            "kind": "creature",
                            "instance_id": creature.instance_id,
                        },
                    ),
                )
                if changed
                else ()
            )
            return _HandlerResult(
                (definition.description, f"It appears {condition_text}."),
                events,
                changed,
            )
        return _HandlerResult((f"You find nothing notable matching {query!r}.",))

    def _glance(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        return _HandlerResult((self.render_room(state, compact=True),))

    def _exits(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        room = self.catalog.rooms[state.character.room_id]
        if not room.exits:
            return _HandlerResult(("You see no obvious way out.",))
        lines = ["Obvious paths:"]
        for direction, destination in room.exits.items():
            locked = not self._exit_is_available(state, room.id, direction)
            suffix = " [sealed by story progression]" if locked else ""
            lines.append(
                f"  {direction:<10} - {self.catalog.rooms[destination].title}{suffix}"
            )
        return _HandlerResult(("\n".join(lines),))

    def _resolve_known_room(
        self,
        state: GameState,
        query: str,
    ) -> tuple[str | None, str | None]:
        if not query:
            return None, "Name a known location."
        scored: list[tuple[int, str]] = []
        for room_id in state.visited_rooms:
            room = self.catalog.rooms[room_id]
            title = room.title.casefold()
            terms = {
                room.id.casefold(),
                room.id.replace("_", " ").casefold(),
                title,
                *(part.strip() for part in title.split(",")),
                *(
                    word
                    for word in re.findall(r"[a-z0-9]+", title)
                    if len(word) >= 3
                ),
            }
            if query in terms:
                score = 3
            elif any(term.startswith(query) for term in terms):
                score = 2
            elif any(query in term for term in terms):
                score = 1
            else:
                continue
            scored.append((score, room_id))
        if not scored:
            return None, (
                f"No visited location matches {query!r}. "
                "Explore it before relying on route memory."
            )
        best = max(score for score, _ in scored)
        matches = [room_id for score, room_id in scored if score == best]
        if len(matches) > 1:
            titles = sorted(self.catalog.rooms[room_id].title for room_id in matches)
            return None, "Choose one known location: " + _natural_list(titles) + "."
        return matches[0], None

    def _resolve_known_npc_route(
        self,
        state: GameState,
        query: str,
    ) -> tuple[NpcDefinition | None, str | None, str | None]:
        """Resolve a currently available person without revealing unvisited rooms."""

        if not query:
            return None, None, "Name a person or known location."
        scored: list[tuple[int, NpcDefinition, str]] = []
        for npc in self.catalog.story.npcs.values():
            if not all(flag in state.flags for flag in npc.requires_flags):
                continue
            if any(flag in state.flags for flag in npc.forbidden_flags):
                continue
            room_id = self._effective_npc_room(state, npc)
            if room_id not in state.visited_rooms:
                continue
            terms = {
                npc.id.casefold(),
                npc.id.replace("_", " " ).casefold(),
                npc.name.casefold(),
                *(noun.casefold() for noun in npc.nouns),
            }
            if query in terms:
                score = 3
            elif any(term.startswith(query) for term in terms):
                score = 2
            elif any(query in term for term in terms):
                score = 1
            else:
                continue
            scored.append((score, npc, room_id))
        if not scored:
            return None, None, (
                f"No visited location or currently known person matches {query!r}."
            )
        best = max(score for score, _, _ in scored)
        matches = [(npc, room_id) for score, npc, room_id in scored if score == best]
        if len(matches) > 1:
            return None, None, (
                "Choose one known person: "
                + _natural_list(sorted(npc.name for npc, _ in matches))
                + "."
            )
        npc, room_id = matches[0]
        return npc, room_id, None

    def _known_route(
        self,
        state: GameState,
        destination: str,
    ) -> list[tuple[str, str]] | None:
        origin = state.character.room_id
        if origin == destination:
            return []
        frontier = deque([origin])
        parent: dict[str, tuple[str, str]] = {}
        seen = {origin}
        while frontier:
            room_id = frontier.popleft()
            for direction, next_room in self._available_exits(state, room_id):
                if next_room not in state.visited_rooms or next_room in seen:
                    continue
                parent[next_room] = (room_id, direction)
                if next_room == destination:
                    route: list[tuple[str, str]] = []
                    cursor = destination
                    while cursor != origin:
                        prior, step_direction = parent[cursor]
                        route.append((step_direction, cursor))
                        cursor = prior
                    route.reverse()
                    return route
                seen.add(next_room)
                frontier.append(next_room)
        return None

    def _objective_route_projection(
        self, state: GameState
    ) -> dict[str, object]:
        """Project spoiler-bounded travel guidance for the active objective."""

        context = self._active_story_context(state)
        if context is None:
            return {
                "active": False,
                "status": "no-objective",
                "summary": "No active story objective requires a route.",
                "next_command": "quest",
                "steps": [],
                "known_path": False,
            }
        quest, stage = context
        suggested = self._story_primary_command(state, stage)
        current_room = self.catalog.rooms[state.character.room_id]
        target_id = stage.target_room_id
        base = {
            "active": True,
            "quest_id": quest.id,
            "quest_title": quest.title,
            "stage_id": stage.id,
            "objective": stage.objective,
            "current_room_id": current_room.id,
            "current_room_title": current_room.title,
            "target_room_id": target_id,
            "target_room_title": (
                self.catalog.rooms[target_id].title if target_id is not None else None
            ),
            "room_hint": stage.room_hint,
            "suggested_command": suggested,
        }
        if target_id is None:
            return {
                **base,
                "status": "action-here",
                "summary": "The current objective can be advanced from this area.",
                "next_command": suggested,
                "steps": [],
                "known_path": True,
            }
        if state.character.room_id == target_id:
            return {
                **base,
                "status": "arrived",
                "summary": f"You are already at {self.catalog.rooms[target_id].title}.",
                "next_command": suggested,
                "steps": [],
                "known_path": True,
            }

        movement = "withdraw" if self._live_creatures(state) else "go"
        known_route = (
            self._known_route(state, target_id)
            if target_id in state.visited_rooms
            else None
        )
        if known_route:
            steps = [
                {
                    "direction": direction,
                    "room_id": room_id,
                    "room_title": self.catalog.rooms[room_id].title,
                    "command": (
                        f"{movement} {direction}"
                        if index == 0
                        else f"go {direction}"
                    ),
                }
                for index, (direction, room_id) in enumerate(known_route)
            ]
            directions = " → ".join(
                str(step["direction"]).upper() for step in steps
            )
            return {
                **base,
                "status": "known-route",
                "summary": (
                    f"Known route to {self.catalog.rooms[target_id].title}: "
                    f"{directions}."
                ),
                "next_command": steps[0]["command"],
                "steps": steps,
                "known_path": True,
            }

        first_step = self._story_shortest_step(
            state, state.character.room_id, target_id
        )
        if first_step is None:
            return {
                **base,
                "status": "blocked",
                "summary": (
                    "No open route to the objective is available from the current "
                    "story state. Review the directive or nearby interactions."
                ),
                "next_command": suggested,
                "steps": [],
                "known_path": False,
            }
        direction, next_room_id = first_step
        next_command = f"{movement} {direction}"
        return {
            **base,
            "status": "first-step",
            "summary": (
                f"Next bearing: {direction.upper()}. The remaining path stays "
                "undisclosed until you explore it."
            ),
            "next_command": next_command,
            "steps": [
                {
                    "direction": direction,
                    "room_id": next_room_id if next_room_id in state.visited_rooms else None,
                    "room_title": (
                        self.catalog.rooms[next_room_id].title
                        if next_room_id in state.visited_rooms
                        else "Unmapped approach"
                    ),
                    "command": next_command,
                }
            ],
            "known_path": False,
        }

    def _resume_briefing_projection(
        self, state: GameState, *, now: float | None = None
    ) -> dict[str, object]:
        """Return a compact, reward-neutral field resume for text and HUD clients."""

        observed_at = self.clock.now() if now is None else now
        route = self._objective_route_projection(state)
        context = self._active_story_context(state)
        quest_id = state.story.active_quest_id
        active_chapter = next(
            (
                chapter
                for chapter in self.catalog.beginner_experience.chapters
                if quest_id in chapter.quest_ids
            ),
            None,
        )
        companion_id = state.character.companion_id
        companion = self.catalog.economy.mercenaries.get(companion_id or "")
        companion_progress = state.character.companion_progress.get(companion_id or "")
        if companion is None or companion_progress is None:
            sol_status = "No active field partner."
            sol_order = None
            sol_health = None
        else:
            recovery = max(0, math.ceil(companion_progress.downed_until - observed_at))
            if recovery:
                sol_status = f"{companion.name} is recovering for {recovery} sec."
            else:
                sol_status = (
                    f"{companion.name} is active at "
                    f"{companion_progress.health}/{companion_progress.max_health} integrity."
                )
            sol_order = companion_progress.order
            sol_health = {
                "current": companion_progress.health,
                "maximum": companion_progress.max_health,
                "level": companion_progress.level,
                "recover_in_seconds": recovery,
            }
        if context is None:
            objective = "No active story directive. Review the journal or choose an open road."
            why = "The current checkpoint is preserved; no decision is being made for you."
        else:
            _quest, stage = context
            objective = stage.objective
            why = stage.why
        checkpoint = self._checkpoint_label(state.story.checkpoint_id)
        return {
            "title": "Field Resume",
            "position": self.catalog.rooms[state.character.room_id].title,
            "level": state.character.level,
            "chapter_id": active_chapter.id if active_chapter is not None else None,
            "chapter_title": (
                active_chapter.title if active_chapter is not None else "Beyond the foundation"
            ),
            "objective": objective,
            "why": why,
            "checkpoint_id": state.story.checkpoint_id,
            "checkpoint_label": checkpoint,
            "route": route,
            "next_command": str(route.get("next_command") or "quest"),
            "sol": {
                "status": sol_status,
                "order": sol_order,
                "health": sol_health,
            },
            "briefing_command": "briefing",
            "route_command": "route objective",
            "reward_neutral": True,
            "local_only": True,
        }

    def _briefing(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        briefing = self._resume_briefing_projection(state, now=now)
        route = briefing["route"]
        assert isinstance(route, dict)
        sol = briefing["sol"]
        assert isinstance(sol, dict)
        lines = [
            "FIELD RESUME",
            f"Position: {briefing['position']} · level {briefing['level']}",
            f"Chapter: {briefing['chapter_title']}",
            f"Checkpoint: {briefing['checkpoint_label']}",
            f"Objective: {briefing['objective']}",
            f"Why now: {briefing['why']}",
            f"Route: {route.get('summary')}",
            f"Sol: {sol.get('status')}",
            f"Next: {briefing['next_command']}",
            "This briefing is guidance only and changes no rewards or decisions.",
        ]
        return _HandlerResult(("\n".join(lines),))

    def _next(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        """Show exactly one reward-neutral step without executing it."""

        if command.args and self._query(command.args) in {"full", "briefing", "recap"}:
            return self._briefing(state, command, now)
        briefing = self._resume_briefing_projection(state, now=now)
        route = briefing["route"]
        assert isinstance(route, dict)
        next_command = str(briefing.get("next_command") or "quest").strip()
        lines = [
            "NEXT STEP",
            f"Objective: {briefing['objective']}",
            f"Why: {briefing['why']}",
            f"Enter: {next_command.upper()}",
            f"Route note: {route.get('summary')}",
            "Nothing was executed. Use NEXT FULL for the complete field resume or HELP HERE for nearby exact commands.",
        ]
        return _HandlerResult(("\n".join(lines),))

    def _route(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if command.args and self._query(command.args) in {
            "objective", "quest", "mission", "next"
        }:
            route = self._objective_route_projection(state)
            if not route["active"]:
                return _HandlerResult((str(route["summary"]),))
            lines = [str(route["summary"])]
            steps = route.get("steps", [])
            if isinstance(steps, list) and len(steps) > 1:
                lines.extend(
                    f"  {index}. {str(step['direction']).upper():<10} "
                    f"{step['room_title']}"
                    for index, step in enumerate(steps, start=1)
                    if isinstance(step, dict)
                )
            next_command = str(route.get("next_command") or "").strip()
            if next_command:
                lines.append(f"Next: {next_command}")
            lines.append(
                "This is guidance only; enter each movement or action command yourself."
            )
            return _HandlerResult(("\n".join(lines),))
        if not command.args:
            known = sorted(
                (
                    self.catalog.rooms[room_id].title,
                    room_id,
                )
                for room_id in state.visited_rooms
            )
            lines = [
                f"Spatial memory: {len(known)}/{len(self.catalog.rooms)} "
                "locations visited."
            ]
            lines.extend(
                f"  {room_id:<18} {title}" for title, room_id in known
            )
            lines.append(
                "Use ROUTE <known place> for a shortest known path; "
                "ROUTE never moves you."
            )
            return _HandlerResult(("\n".join(lines),))
        query = self._query(command.args)
        destination, room_error = self._resolve_known_room(state, query)
        npc: NpcDefinition | None = None
        if destination is None and room_error and room_error.startswith(
            "Choose one known location"
        ):
            return _HandlerResult((room_error,))
        if destination is None:
            npc, destination, npc_error = self._resolve_known_npc_route(
                state, query
            )
            if destination is None:
                return _HandlerResult((npc_error or room_error or "Route where?",))
        room_title = self.catalog.rooms[destination].title
        title = (
            f"{npc.name} at {room_title}"
            if npc is not None
            else room_title
        )
        route = self._known_route(state, destination)
        if route is None:
            return _HandlerResult(
                (
                    f"No connected route to {title} exists within your "
                    "visited spatial memory.",
                )
            )
        if not route:
            if npc is not None:
                return _HandlerResult(
                    (f"{npc.name} is currently here at {room_title}. TALK {npc.nouns[0]} when ready.",)
                )
            return _HandlerResult((f"You are already at {title}.",))
        lines = [
            f"Shortest known route to {title}: {len(route)} "
            f"{'step' if len(route) == 1 else 'steps'}.",
            " -> ".join(direction.upper() for direction, _ in route),
        ]
        lines.extend(
            f"  {index}. {direction.upper():<10} "
            f"{self.catalog.rooms[room_id].title}"
            for index, (direction, room_id) in enumerate(route, start=1)
        )
        lines.append("This is guidance only; enter each movement command yourself.")
        return _HandlerResult(("\n".join(lines),))

    def _go(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        if not query:
            return _HandlerResult(("Go where? Try EXITS.",))
        direction, destination = self._resolve_exit(state, query)
        if destination is None:
            room = self.catalog.rooms[state.character.room_id]
            directions = list(room.exits)
            suggestion = difflib.get_close_matches(
                query, directions, n=1, cutoff=0.55
            )
            if suggestion:
                return _HandlerResult(
                    (
                        f"You cannot go {query} from here. "
                        f"Did you mean GO {suggestion[0].upper()}?",
                    )
                )
            choices = ", ".join(f"GO {item.upper()}" for item in directions)
            return _HandlerResult(
                (
                    f"You cannot go {query} from here. "
                    + (f"Available movement: {choices}." if choices else "There are no exits."),
                )
            )
        locked = self._exit_lock_reason(state, direction)
        if locked:
            return _HandlerResult((locked,))
        if self._live_creatures(state):
            return _HandlerResult(
                (
                    "An active opponent contests your movement. "
                    f"Use WITHDRAW {direction} to make a visible opposed attempt.",
                )
            )
        return self._move_character(
            state,
            now,
            direction=direction,
            destination=destination,
            base_duration=1,
            mode="ordinary",
        )

    def _resolve_exit(
        self,
        state: GameState,
        query: str,
    ) -> tuple[str, str | None]:
        room = self.catalog.rooms[state.character.room_id]
        exact = room.exits.get(query)
        if exact:
            return query, exact
        matches = [key for key in room.exits if key.startswith(query)]
        if len(matches) != 1:
            return query, None
        direction = matches[0]
        return direction, room.exits[direction]

    def _move_character(
        self,
        state: GameState,
        now: float,
        *,
        direction: str,
        destination: str,
        base_duration: int,
        mode: str,
    ) -> _HandlerResult:
        origin = state.character.room_id
        discovered = destination not in state.visited_rooms
        state.character.room_id = destination
        state.visited_rooms.add(destination)
        state.target_id = None
        disabled_legs = sum(
            "leg" in location for location in disabled_limbs(state.character)
        )
        load = calculate_encumbrance(state.character, self.catalog.items)
        duration = base_duration + disabled_legs * 2 + load.recovery_penalty
        self._set_roundtime(state, now, duration)
        room_view = (
            self.render_room(state)
            if discovered
            else self.render_room_revisit(state)
        )
        lines = [
            f"You move {direction}.\n\n{room_view}",
            f"Roundtime: {duration} sec.",
        ]
        events = [
            DomainEvent(
                "character.moved",
                {
                    "from": origin,
                    "to": destination,
                    "disabled_leg_penalty": disabled_legs * 2,
                    "encumbrance_penalty": load.recovery_penalty,
                    "mode": mode,
                },
            )
        ]
        if not discovered:
            events.append(
                DomainEvent(
                    "world.room_revisited_briefly",
                    {"room_id": destination, "full_description_command": "look"},
                )
            )
        if discovered:
            lines.append(
                f"[Discovery] {self.catalog.rooms[destination].title} "
                "is added to your spatial memory."
            )
            events.append(
                DomainEvent(
                    "world.room_discovered",
                    {
                        "room_id": destination,
                        "visited_count": len(state.visited_rooms),
                    },
                )
            )
        hazard = self.catalog.rooms[destination]
        if hazard.hazard_name is not None:
            carried_ids = {item.definition_id for item in state.character.inventory}
            class_id = state.character.build.class_id or ""
            mitigating_items = carried_ids.intersection(hazard.hazard_mitigation_items)
            class_mitigated = class_id in hazard.hazard_mitigation_classes
            mitigated = bool(mitigating_items) or class_mitigated
            if mitigated:
                method = (
                    self.catalog.items[sorted(mitigating_items)[0]].name
                    if mitigating_items
                    else self.catalog.creation.classes[class_id].name
                )
                lines.append(
                    f"[Hazard · {hazard.hazard_name}] {method} keeps the exposure controlled."
                )
                events.append(
                    DomainEvent(
                        "world.hazard_mitigated",
                        {"room_id": destination, "hazard": hazard.hazard_name, "method": method},
                    )
                )
            else:
                damage = max(0, hazard.hazard_damage)
                extra = max(0, hazard.hazard_roundtime)
                state.character.health -= damage
                if extra:
                    self._set_roundtime(state, now, duration + extra)
                lines.append(f"[Hazard · {hazard.hazard_name}] {hazard.hazard_text}")
                lines.append(
                    f"Exposure costs {damage} health"
                    + (f" and adds {extra} sec. recovery." if extra else ".")
                )
                events.append(
                    DomainEvent(
                        "world.hazard_exposure",
                        {"room_id": destination, "hazard": hazard.hazard_name, "damage": damage, "roundtime": extra},
                    )
                )
                if state.character.health <= 0:
                    events.extend(
                        self._incapacitate(
                            state, now, lines, cause=f"hazard:{destination}"
                        )
                    )
        return _HandlerResult(
            tuple(lines),
            tuple(events),
            True,
        )

class InventoryService(EngineService):
    """Owns the inventory application boundary."""

    def _spawn_item(self, state: GameState, definition_id: str) -> ItemState:
        state.next_item_serial += 1
        return self._new_item_state(
            f"dynamic:item:{state.next_item_serial}",
            definition_id,
        )

    def _new_item_state(
        self, instance_id: str, definition_id: str
    ) -> ItemState:
        definition = self.catalog.items[definition_id]
        return ItemState(
            instance_id=instance_id,
            definition_id=definition_id,
            durability=(
                definition.max_durability
                if definition.max_durability > 0
                else None
            ),
        )

    @staticmethod
    def _inventory_item(state: GameState, instance_id: str) -> ItemState | None:
        return next(
            (
                item
                for item in state.character.inventory
                if item.instance_id == instance_id
            ),
            None,
        )

    def _effective_item_definition(self, item: ItemState):
        return effective_item_definition(
            self.catalog.items[item.definition_id], item.upgrade_level
        )

    def _effective_max_durability(self, item: ItemState) -> int:
        return self._effective_item_definition(item).max_durability

    def _validate_item_durability(
        self, item: ItemState, *, allow_missing: bool = False
    ) -> None:
        definition = self.catalog.items[item.definition_id]
        if definition.max_durability <= 0:
            if item.durability is not None:
                raise ValueError(
                    f"save gives indestructible item {item.instance_id!r} durability"
                )
            return
        if item.durability is None:
            if allow_missing:
                return
            raise ValueError(
                f"save durable item {item.instance_id!r} has no durability"
            )
        maximum = definition.max_durability + 5 * item.upgrade_level
        if item.durability < 0 or item.durability > maximum:
            raise ValueError(
                f"save item {item.instance_id!r} durability is outside its valid range"
            )

    @classmethod
    def _equipped_item_state(
        cls, state: GameState, slot: str
    ) -> ItemState | None:
        instance_id = state.character.equipped.get(slot)
        return (
            cls._inventory_item(state, instance_id)
            if instance_id is not None
            else None
        )

    def _item_candidates(
        self,
        state: GameState,
        scope: Scope,
        default_scope: Scope | None,
    ) -> list[ItemState]:
        room_items = list(
            state.room_items.get(state.character.room_id, [])
        )
        inventory = list(state.character.inventory)
        if scope is Scope.ROOM or (
            scope is Scope.DEFAULT and default_scope is Scope.ROOM
        ):
            return room_items
        if scope is Scope.INVENTORY or (
            scope is Scope.DEFAULT and default_scope is Scope.INVENTORY
        ):
            return inventory
        if scope is Scope.EQUIPPED or (
            scope is Scope.DEFAULT and default_scope is Scope.EQUIPPED
        ):
            equipped = set(state.character.equipped.values())
            return [item for item in inventory if item.instance_id in equipped]
        return room_items + inventory

    def _matching_items(
        self, query: str, candidates: list[ItemState]
    ) -> list[ItemState]:
        if not query:
            return list(candidates)
        scored: list[tuple[int, ItemState]] = []
        for candidate in candidates:
            definition = self.catalog.items[candidate.definition_id]
            terms = {
                definition.name.casefold(),
                definition.id.casefold(),
                candidate.instance_id.casefold(),
                *(noun.casefold() for noun in definition.nouns),
            }
            query_words = set(query.replace("_", " ").split())
            if query in terms:
                score = 3
            elif any(term.startswith(query) for term in terms):
                score = 2
            elif any(query in term for term in terms):
                score = 1
            elif any(
                query_words and query_words.issubset(set(term.replace("_", " ").split()))
                for term in terms
            ):
                score = 1
            else:
                continue
            scored.append((score, candidate))
        if not scored:
            return []
        best = max(score for score, _ in scored)
        return [candidate for score, candidate in scored if score == best]

    def _resolve_items(
        self,
        state: GameState,
        raw_query: str,
        *,
        default_scope: Scope | None,
        allow_all: bool = False,
        allowed_scopes: set[Scope] | None = None,
    ) -> tuple[list[ItemState], str | None]:
        selection = parse_selection(raw_query)
        if (
            allowed_scopes is not None
            and selection.scope is not Scope.DEFAULT
            and selection.scope not in allowed_scopes
        ):
            return [], f"That action cannot use the {selection.scope.value} scope."
        if selection.exclusion is not None and not selection.all_matches:
            return [], "EXCEPT is only valid after ALL."
        if selection.exclusion == "":
            return [], "Name what should be excluded after EXCEPT."
        if selection.all_matches and (
            selection.ordinal is not None
            or selection.relative is not None
            or selection.pronoun
        ):
            return [], (
                "ALL cannot be combined with an ordinal, pronoun, "
                "or relative selector."
            )
        candidates = self._item_candidates(
            state,
            selection.scope,
            default_scope,
        )
        if selection.pronoun:
            match = next(
                (
                    item
                    for item in candidates
                    if state.last_reference_kind == "item"
                    and item.instance_id == state.last_reference_id
                ),
                None,
            )
            if match is None:
                return [], "The item pronoun has no visible referent."
            return [match], None
        if (
            not selection.terms
            and not selection.all_matches
            and selection.relative is None
        ):
            return [], "What item do you mean?"
        matches = self._matching_items(selection.terms, candidates)
        if selection.exclusion is not None:
            excluded = {
                item.instance_id
                for item in self._matching_items(
                    selection.exclusion,
                    candidates,
                )
            }
            matches = [
                item for item in matches if item.instance_id not in excluded
            ]
        if not matches:
            label = selection.terms or "those items"
            visible = _natural_list(
                [self.catalog.items[item.definition_id].name for item in candidates]
            )
            suffix = (
                f" Visible item choices: {visible}."
                if candidates
                else " No items are available in that scope."
            )
            return [], f"You do not see {label!r} in that scope.{suffix}"
        if selection.all_matches:
            if not allow_all:
                return [], "That command requires one item, not ALL."
            if len(matches) > MAX_BULK_SELECTION:
                return [], (
                    f"That selection contains {len(matches)} items; "
                    f"the safe limit is {MAX_BULK_SELECTION}."
                )
            return matches, None
        if selection.ordinal is not None:
            if selection.ordinal >= len(matches):
                return [], f"There are only {len(matches)} matching items in that scope."
            return [matches[selection.ordinal]], None
        if selection.relative is RelativeSelector.RANDOM:
            return [self.rng.choice(tuple(matches))], None
        if selection.relative is not None:
            last_id = (
                state.last_reference_id
                if state.last_reference_kind == "item"
                else None
            )
            if selection.relative is RelativeSelector.OTHER:
                other = next(
                    (item for item in matches if item.instance_id != last_id),
                    None,
                )
                if last_id is None or other is None:
                    return [], "OTHER needs a prior matching item and another choice."
                return [other], None
            if last_id is None:
                return [matches[0]], None
            current = next(
                (
                    index
                    for index, item in enumerate(matches)
                    if item.instance_id == last_id
                ),
                None,
            )
            if current is None:
                return [matches[0]], None
            if len(matches) == 1:
                return [], "There is no next matching item."
            return [matches[(current + 1) % len(matches)]], None
        if len(matches) > 1:
            names = [self.catalog.items[item.definition_id].name for item in matches]
            return [], (
                f"Which do you mean: {_natural_list(names)}? "
                "Use an ordinal, OTHER, NEXT, or RANDOM."
            )
        return [matches[0]], None

    @staticmethod
    def _set_reference(
        state: GameState,
        kind: str | None,
        instance_id: str | None,
    ) -> bool:
        changed = (
            state.last_reference_kind != kind
            or state.last_reference_id != instance_id
        )
        state.last_reference_kind = kind
        state.last_reference_id = instance_id
        return changed

    def _remove_inventory_item(
        self,
        state: GameState,
        item: ItemState,
    ) -> ItemState:
        """Remove one carried item and atomically clear all direct references.

        Consuming, selling, salvaging, surrendering, or destroying an item must
        never leave a pronoun or equipment slot pointing at an instance that no
        longer exists.  Centralizing the mutation keeps persistence validation
        identical across every destructive item path.
        """

        state.character.inventory.remove(item)
        if (
            state.last_reference_kind == "item"
            and state.last_reference_id == item.instance_id
        ):
            self._set_reference(state, None, None)
        for slot, instance_id in tuple(state.character.equipped.items()):
            if instance_id == item.instance_id:
                del state.character.equipped[slot]
        return item

    def _repair_stale_reference(self, state: GameState) -> bool:
        """Clear an obsolete direct reference while reconciling an older save."""

        if state.last_reference_kind is None:
            return False
        if state.last_reference_kind == "item":
            live_ids = {
                item.instance_id for item in state.character.inventory
            } | {
                item.instance_id
                for room_items in state.room_items.values()
                for item in room_items
            }
        elif state.last_reference_kind == "creature":
            live_ids = {
                creature.instance_id
                for room_creatures in state.creatures.values()
                for creature in room_creatures
            }
        else:
            return False
        if state.last_reference_id in live_ids:
            return False
        self._set_reference(state, None, None)
        return True

    def _get(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        room_items = state.room_items.setdefault(state.character.room_id, [])
        items, error = self._resolve_items(
            state,
            query,
            default_scope=Scope.ROOM,
            allow_all=True,
            allowed_scopes={Scope.ROOM},
        )
        if not items:
            return _HandlerResult((error or "Take what?",))
        load = calculate_encumbrance(state.character, self.catalog.items)
        selected_bulk = item_bulk(items, self.catalog.items)
        projected_bulk = load.carried_bulk + selected_bulk
        if projected_bulk > load.hard_limit:
            return _HandlerResult(
                (
                    f"That selection would raise your carried bulk from "
                    f"{load.carried_bulk} to {projected_bulk}; your hard limit is "
                    f"{load.hard_limit}. Nothing was moved.",
                )
            )
        for item_state in items:
            room_items.remove(item_state)
            state.character.inventory.append(item_state)
        self._set_reference(state, "item", items[-1].instance_id)
        duration = min(5, max(1, len(items)))
        self._set_roundtime(state, now, duration)
        names = [
            self.catalog.items[item_state.definition_id].name
            for item_state in items
        ]
        message = (
            f"You pick up {names[0]}."
            if len(names) == 1
            else f"You pick up {_natural_list(names)}."
        )
        return _HandlerResult(
            (message, f"Roundtime: {duration} sec."),
            tuple(
                DomainEvent(
                    "item.taken",
                    {
                        "instance_id": item_state.instance_id,
                        "item_id": item_state.definition_id,
                    },
                )
                for item_state in items
            ),
            True,
        )

    def _drop(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        items, error = self._resolve_items(
            state,
            query,
            default_scope=Scope.INVENTORY,
            allow_all=True,
            allowed_scopes={Scope.INVENTORY, Scope.EQUIPPED},
        )
        if not items:
            return _HandlerResult((error or "Drop what?",))
        equipped = [
            self.catalog.items[item.definition_id].name
            for item in items
            if item.instance_id in state.character.equipped.values()
        ]
        if equipped:
            return _HandlerResult(
                (
                    f"Unequip {_natural_list(equipped)} before dropping "
                    "the selection; nothing was moved.",
                )
            )
        room_items = state.room_items.setdefault(state.character.room_id, [])
        for item_state in items:
            state.character.inventory.remove(item_state)
            room_items.append(item_state)
        self._set_reference(state, "item", items[-1].instance_id)
        duration = min(5, max(1, len(items)))
        self._set_roundtime(state, now, duration)
        names = [
            self.catalog.items[item_state.definition_id].name
            for item_state in items
        ]
        message = (
            f"You set down {names[0]}."
            if len(names) == 1
            else f"You set down {_natural_list(names)}."
        )
        return _HandlerResult(
            (message, f"Roundtime: {duration} sec."),
            tuple(
                DomainEvent(
                    "item.dropped",
                    {
                        "instance_id": item_state.instance_id,
                        "item_id": item_state.definition_id,
                    },
                )
                for item_state in items
            ),
            True,
        )

    def _inventory(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        if not state.character.inventory:
            return _HandlerResult(
                ("You are carrying nothing.",),
                (DomainEvent("inventory.viewed", {"item_count": 0}),),
            )
        load = calculate_encumbrance(state.character, self.catalog.items)
        lines = [
            f"You are carrying (bulk {load.carried_bulk}/{load.hard_limit}; "
            f"{load.tier}):"
        ]
        for item_state in state.character.inventory:
            definition = self.catalog.items[item_state.definition_id]
            equipped = (
                " (equipped)"
                if item_state.instance_id in state.character.equipped.values()
                else ""
            )
            effective = self._effective_item_definition(item_state)
            condition = (
                f", durability {item_state.durability}/{effective.max_durability}"
                if item_state.durability is not None
                else ""
            )
            modification = (
                f", modification +{item_state.upgrade_level}"
                if item_state.upgrade_level
                else ""
            )
            lines.append(
                f"  {definition.name}{equipped} "
                f"[bulk {definition.bulk}{condition}{modification}]"
            )
        return _HandlerResult(
            ("\n".join(lines),),
            (
                DomainEvent(
                    "inventory.viewed",
                    {"item_count": len(state.character.inventory)},
                ),
            ),
        )

    def _equipment(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        if not state.character.equipped:
            return _HandlerResult(("You have no equipment readied.",))
        lines = ["Readied equipment:"]
        for slot, instance_id in sorted(state.character.equipped.items()):
            item_state = self._inventory_item(state, instance_id)
            if item_state is None:
                continue
            mod = f" +{item_state.upgrade_level}" if item_state.upgrade_level else ""
            lines.append(
                f"  {slot.replace('_', ' '):<12} - "
                f"{self.catalog.items[item_state.definition_id].name}{mod}"
            )
        return _HandlerResult(("\n".join(lines),))

    def _compare(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        query = self._query(command.args)
        items, error = self._resolve_items(
            state,
            query,
            default_scope=Scope.INVENTORY,
            allowed_scopes={Scope.INVENTORY, Scope.EQUIPPED},
        )
        if not items:
            return _HandlerResult((error or "Compare what?",))
        candidate = items[0]
        base = self.catalog.items[candidate.definition_id]
        if not base.slot:
            return _HandlerResult((f"The {base.name} is not equippable.",))
        current_id = state.character.equipped.get(base.slot)
        current = self._inventory_item(state, current_id) if current_id else None
        candidate_def = self._effective_item_definition(candidate)
        lines = [f"Equipment comparison · {base.slot.replace('_', ' ').title()}:"]
        lines.append(
            f"  Candidate: {base.name} +{candidate.upgrade_level} · "
            f"attack {candidate_def.attack_bonus}, defense {candidate_def.defense_bonus}, "
            f"damage {candidate_def.damage_min}-{candidate_def.damage_max}, armor {candidate_def.armor}, "
            f"roundtime {candidate_def.roundtime}s"
        )
        if current is None:
            lines.append("  Equipped: empty slot.")
        else:
            current_base = self.catalog.items[current.definition_id]
            current_def = self._effective_item_definition(current)
            lines.append(
                f"  Equipped: {current_base.name} +{current.upgrade_level} · "
                f"attack {current_def.attack_bonus}, defense {current_def.defense_bonus}, "
                f"damage {current_def.damage_min}-{current_def.damage_max}, armor {current_def.armor}, "
                f"roundtime {current_def.roundtime}s"
            )
            deltas = {
                "attack": candidate_def.attack_bonus - current_def.attack_bonus,
                "defense": candidate_def.defense_bonus - current_def.defense_bonus,
                "damage ceiling": candidate_def.damage_max - current_def.damage_max,
                "armor": candidate_def.armor - current_def.armor,
                "roundtime": candidate_def.roundtime - current_def.roundtime,
            }
            lines.append(
                "  Change: "
                + ", ".join(
                    f"{label} {value:+d}" for label, value in deltas.items()
                )
                + ". Lower roundtime is faster."
            )
        return _HandlerResult(
            ("\n".join(lines),),
            (DomainEvent("equipment.compared", {"item_id": base.id, "slot": base.slot}),),
        )

    def _equip(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        items, error = self._resolve_items(
            state,
            query,
            default_scope=Scope.INVENTORY,
            allowed_scopes={Scope.INVENTORY, Scope.EQUIPPED},
        )
        if not items:
            return _HandlerResult((error or "Equip what?",))
        item_state = items[0]
        definition = self.catalog.items[item_state.definition_id]
        if not definition.slot:
            return _HandlerResult((f"The {definition.name} is not wearable or wieldable.",))
        if state.character.equipped.get(definition.slot) == item_state.instance_id:
            return _HandlerResult((f"You already have {definition.name} equipped.",))
        displaced = state.character.equipped.get(definition.slot)
        state.character.equipped[definition.slot] = item_state.instance_id
        self._set_reference(state, "item", item_state.instance_id)
        self._set_roundtime(state, now, 1)
        lines = []
        if displaced:
            displaced_item = self._inventory_item(state, displaced)
            if displaced_item:
                lines.append(
                    f"You stow {self.catalog.items[displaced_item.definition_id].name}."
                )
        lines.extend((f"You equip {definition.name}.", "Roundtime: 1 sec."))
        return _HandlerResult(
            tuple(lines),
            (
                DomainEvent(
                    "item.equipped",
                    {
                        "instance_id": item_state.instance_id,
                        "item_id": item_state.definition_id,
                        "slot": definition.slot,
                    },
                ),
            ),
            True,
        )

    def _unequip(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        items, error = self._resolve_items(
            state,
            query,
            default_scope=Scope.EQUIPPED,
            allowed_scopes={Scope.INVENTORY, Scope.EQUIPPED},
        )
        if not items:
            return _HandlerResult((error or "Unequip what?",))
        item_state = items[0]
        if item_state.instance_id not in state.character.equipped.values():
            return _HandlerResult(
                (
                    f"You are carrying {self.catalog.items[item_state.definition_id].name}, "
                    "but it is not equipped.",
                )
            )
        slot = next(
            key
            for key, equipped_id in state.character.equipped.items()
            if equipped_id == item_state.instance_id
        )
        del state.character.equipped[slot]
        self._set_reference(state, "item", item_state.instance_id)
        self._set_roundtime(state, now, 1)
        return _HandlerResult(
            (
                f"You stow {self.catalog.items[item_state.definition_id].name}.",
                "Roundtime: 1 sec.",
            ),
            (
                DomainEvent(
                    "item.unequipped",
                    {
                        "instance_id": item_state.instance_id,
                        "item_id": item_state.definition_id,
                        "slot": slot,
                    },
                ),
            ),
            True,
        )

    def _repair(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        room = self.catalog.rooms[state.character.room_id]
        if "repair_bench" not in room.facilities:
            return _HandlerResult(
                ("You need a fitted repair bench before working on equipment.",)
            )
        if self._live_creatures(state):
            return _HandlerResult(
                ("You cannot begin careful repairs with an active opponent nearby.",)
            )
        query = self._query(command.args)
        items, error = self._resolve_items(
            state,
            query,
            default_scope=Scope.INVENTORY,
            allowed_scopes={Scope.INVENTORY, Scope.EQUIPPED},
        )
        if not items:
            return _HandlerResult((error or "Repair what?",))
        target = items[0]
        definition = self.catalog.items[target.definition_id]
        if definition.max_durability <= 0 or target.durability is None:
            return _HandlerResult(
                (f"The {definition.name} has no repairable durability.",)
            )
        if target.instance_id in state.character.equipped.values():
            return _HandlerResult(
                (f"Unequip {definition.name} before placing it in the repair cradle.",)
            )
        maximum = self._effective_max_durability(target)
        if target.durability >= maximum:
            return _HandlerResult(
                (f"The {definition.name} is already at full durability.",)
            )
        material = next(
            (
                item
                for item in state.character.inventory
                if item.instance_id != target.instance_id
                and self.catalog.items[item.definition_id].repair_family
                == definition.repair_family
                and self.catalog.items[item.definition_id].repair_value > 0
            ),
            None,
        )
        if material is None:
            family = (definition.repair_family or "matching").replace("_", " ")
            return _HandlerResult(
                (
                    f"You lack a consumable {family} repair material. "
                    "Nothing was changed.",
                )
            )
        material_definition = self.catalog.items[material.definition_id]
        before = target.durability
        repair_bonus = 2 if state.character.build.class_id == "engineer" else 0
        restored = min(
            material_definition.repair_value + repair_bonus,
            maximum - before,
        )
        target.durability = before + restored
        self._remove_inventory_item(state, material)
        self._set_reference(state, "item", target.instance_id)
        self._set_roundtime(state, now, 6)
        return _HandlerResult(
            (
                f"You seat {definition.name} in the repair cradle and consume "
                f"{material_definition.name}.",
                f"Durability rises from {before} to {target.durability} "
                f"of {maximum}.",
                "Roundtime: 6 sec.",
            ),
            (
                DomainEvent(
                    "equipment.repaired",
                    {
                        "room_id": room.id,
                        "facility": "repair_bench",
                        "target_instance_id": target.instance_id,
                        "target_item_id": target.definition_id,
                        "repair_family": definition.repair_family,
                        "durability_before": before,
                        "durability_after": target.durability,
                        "durability_restored": restored,
                        "material_instance_id": material.instance_id,
                        "material_item_id": material.definition_id,
                        "material_consumed": True,
                    },
                ),
            ),
            True,
        )

    def _modify(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        room = self.catalog.rooms[state.character.room_id]
        if "repair_bench" not in room.facilities:
            return _HandlerResult(("You need a fitted repair bench to modify equipment.",))
        if self._live_creatures(state):
            return _HandlerResult(("You cannot modify equipment under hostile pressure.",))
        query = self._query(command.args)
        items, error = self._resolve_items(
            state, query, default_scope=Scope.INVENTORY,
            allowed_scopes={Scope.INVENTORY, Scope.EQUIPPED},
        )
        if not items:
            return _HandlerResult((error or "Modify what?",))
        target = items[0]
        definition = self.catalog.items[target.definition_id]
        if not definition.slot or definition.max_durability <= 0:
            return _HandlerResult((f"The {definition.name} has no stable modification frame.",))
        if target.instance_id in state.character.equipped.values():
            return _HandlerResult((f"Unequip {definition.name} before modifying it.",))
        if target.upgrade_level >= 3:
            return _HandlerResult((f"The {definition.name} is already at modification +3.",))
        kit = next(
            (item for item in state.character.inventory if item.definition_id == "field_mod_kit"),
            None,
        )
        if kit is None:
            return _HandlerResult(("A field mod kit is required; nothing was changed.",))
        before = target.upgrade_level
        target.upgrade_level += 1
        if target.durability is not None:
            target.durability = min(
                self._effective_max_durability(target),
                target.durability + 5,
            )
        self._remove_inventory_item(state, kit)
        self._set_reference(state, "item", target.instance_id)
        self._set_roundtime(state, now, 8)
        return _HandlerResult(
            (
                f"You fit {definition.name} with a field modification (+{before} → +{target.upgrade_level}).",
                "The modification improves its active combat profile and raises its durability ceiling.",
                "Roundtime: 8 sec.",
            ),
            (
                DomainEvent(
                    "equipment.modified",
                    {
                        "target_instance_id": target.instance_id,
                        "target_item_id": target.definition_id,
                        "upgrade_before": before,
                        "upgrade_after": target.upgrade_level,
                        "material_item_id": "field_mod_kit",
                    },
                ),
            ),
            True,
        )

    def _vendors_here(self, state: GameState):
        return [vendor for vendor in self.catalog.economy.vendors.values() if vendor.room_id == state.character.room_id]

    def _resolve_vendor_item(self, vendor, query: str):
        normalized = query.casefold().strip()
        query_words = set(normalized.replace("_", " ").split())
        matches = []
        for item_id in vendor.inventory:
            definition = self.catalog.items[item_id]
            terms = {
                item_id.casefold().replace("_", " "),
                definition.name.casefold(),
                *(noun.casefold() for noun in definition.nouns),
            }
            if (
                normalized in terms
                or any(term.startswith(normalized) for term in terms)
                or any(query_words and query_words.issubset(set(term.split())) for term in terms)
            ):
                matches.append(item_id)
        return matches[0] if len(matches) == 1 else None

    def _market(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        vendors = self._vendors_here(state)
        if not vendors:
            return _HandlerResult(("No exchange operates in this room.",))
        vendor = vendors[0]
        if self._live_creatures(state):
            return _HandlerResult(("The exchange closes while hostile pressure is active.",))
        if not command.args or command.args[0].casefold() in {"list", "status"}:
            lines = [f"{vendor.name} · credits {state.character.credits}:"]
            for item_id, price in vendor.inventory.items():
                item = self.catalog.items[item_id]
                lines.append(f"  {item.name:<28} {price:>3} credits")
            lines.append(f"The exchange buys tradeable items at {vendor.sell_rate_percent}% of base value.")
            lines.append("Use MARKET BUY <item> or MARKET SELL <carried item>.")
            return _HandlerResult(("\n".join(lines),))
        action = command.args[0].casefold()
        query = self._query(command.args[1:])
        if action == "buy":
            item_id = self._resolve_vendor_item(vendor, query)
            if item_id is None:
                return _HandlerResult(("That item is not listed by this exchange.",))
            price = vendor.inventory[item_id]
            if state.character.credits < price:
                return _HandlerResult((f"You need {price} credits; you have {state.character.credits}.",))
            before = state.character.credits
            state.character.credits -= price
            item = self._spawn_item(state, item_id)
            state.character.inventory.append(item)
            self._set_roundtime(state, now, 1)
            return _HandlerResult(
                (f"You buy {self.catalog.items[item_id].name} for {price} credits.", f"Credits: {before} → {state.character.credits}.", "Roundtime: 1 sec."),
                (DomainEvent("economy.item_bought", {"vendor_id": vendor.id, "item_id": item_id, "price": price, "instance_id": item.instance_id}),),
                True,
            )
        if action == "sell":
            items, error = self._resolve_items(state, query, default_scope=Scope.INVENTORY, allowed_scopes={Scope.INVENTORY})
            if not items:
                return _HandlerResult((error or "Sell what?",))
            item = items[0]
            definition = self.catalog.items[item.definition_id]
            if item.instance_id in state.character.equipped.values():
                return _HandlerResult(("Unequip that item before selling it.",))
            if not definition.tradeable or definition.base_value <= 0:
                return _HandlerResult((f"{definition.name.capitalize()} has no safe exchange value.",))
            value = max(1, definition.base_value * vendor.sell_rate_percent // 100)
            self._remove_inventory_item(state, item)
            before = state.character.credits
            state.character.credits = min(100_000_000, state.character.credits + value)
            self._set_roundtime(state, now, 1)
            return _HandlerResult(
                (f"You sell {definition.name} for {value} credits.", f"Credits: {before} → {state.character.credits}.", "Roundtime: 1 sec."),
                (DomainEvent("economy.item_sold", {"vendor_id": vendor.id, "item_id": item.definition_id, "value": value}),),
                True,
            )
        return _HandlerResult(("Use MARKET, MARKET BUY <item>, or MARKET SELL <item>.",))

    def _resolve_recipe(self, query: str):
        normalized = query.casefold().replace("-", " ").replace("_", " ").strip()
        exact = self.catalog.economy.recipes.get(query.casefold().replace(" ", "_"))
        if exact is not None:
            return exact
        matches = [
            recipe for recipe in self.catalog.economy.recipes.values()
            if recipe.name.casefold().startswith(normalized)
            or any(noun.casefold().startswith(normalized) for noun in recipe.nouns)
        ]
        return matches[0] if len(matches) == 1 else None

    def _craft(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        room = self.catalog.rooms[state.character.room_id]
        if self._live_creatures(state):
            return _HandlerResult(("You cannot craft under hostile pressure.",))
        if not command.args or command.args[0].casefold() in {"list", "status"}:
            lines = ["Available recipes:"]
            for recipe in self.catalog.economy.recipes.values():
                availability = "available here" if recipe.facility in room.facilities else f"requires {recipe.facility.replace('_', ' ')}"
                ingredients = ", ".join(f"{count} {self.catalog.items[item_id].name}" for item_id, count in recipe.inputs.items())
                outputs = ", ".join(f"{count} {self.catalog.items[item_id].name}" for item_id, count in recipe.outputs.items())
                lines.append(f"  {recipe.name}: {ingredients} + {recipe.credit_cost} credits → {outputs} ({availability})")
            return _HandlerResult(("\n".join(lines),))
        recipe = self._resolve_recipe(self._query(command.args))
        if recipe is None:
            return _HandlerResult(("That recipe is not in your current field catalog.",))
        if recipe.facility not in room.facilities:
            return _HandlerResult((f"{recipe.name.capitalize()} requires a {recipe.facility.replace('_', ' ')}.",))
        counts = self._story_inventory_counts(state)
        discount = "specialization_craft_discount" in state.flags
        required = dict(recipe.inputs)
        if discount and required:
            first = next(iter(required))
            required[first] = max(0, required[first] - 1)
        missing = [f"{count - counts.get(item_id, 0)} {self.catalog.items[item_id].name}" for item_id, count in required.items() if counts.get(item_id, 0) < count]
        if missing:
            return _HandlerResult(("Missing: " + _natural_list(missing) + ".",))
        if state.character.credits < recipe.credit_cost:
            return _HandlerResult((f"You need {recipe.credit_cost} credits; you have {state.character.credits}.",))
        consumed: list[str] = []
        for item_id, count in required.items():
            for _ in range(count):
                item = next(item for item in state.character.inventory if item.definition_id == item_id)
                self._remove_inventory_item(state, item)
                consumed.append(item_id)
        state.character.credits -= recipe.credit_cost
        produced: list[ItemState] = []
        for item_id, count in recipe.outputs.items():
            for _ in range(count):
                item = self._spawn_item(state, item_id)
                state.character.inventory.append(item)
                produced.append(item)
        if discount:
            state.flags.discard("specialization_craft_discount")
        state.flags.add(f"recipe_crafted:{recipe.id}")
        self._set_roundtime(state, now, 5)
        return _HandlerResult(
            (f"You craft {recipe.name}.", f"Credits remaining: {state.character.credits}.", "Roundtime: 5 sec."),
            (DomainEvent("economy.recipe_crafted", {"recipe_id": recipe.id, "inputs": consumed, "outputs": [item.definition_id for item in produced], "credit_cost": recipe.credit_cost, "specialization_discount": discount}),),
            True,
        )

    def _salvage(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        room = self.catalog.rooms[state.character.room_id]
        if "salvage_bench" not in room.facilities and "repair_bench" not in room.facilities:
            return _HandlerResult(("A salvage or repair bench is required.",))
        if self._live_creatures(state):
            return _HandlerResult(("You cannot dismantle equipment under hostile pressure.",))
        items, error = self._resolve_items(state, self._query(command.args), default_scope=Scope.INVENTORY, allowed_scopes={Scope.INVENTORY})
        if not items:
            return _HandlerResult((error or "Salvage what?",))
        item = items[0]
        definition = self.catalog.items[item.definition_id]
        if item.instance_id in state.character.equipped.values():
            return _HandlerResult(("Unequip that item before salvaging it.",))
        if not definition.salvage_yields:
            return _HandlerResult((f"{definition.name.capitalize()} has no authored salvage yield.",))
        self._remove_inventory_item(state, item)
        outputs: list[str] = []
        for item_id, count in definition.salvage_yields.items():
            for _ in range(count):
                salvaged = self._spawn_item(state, item_id)
                state.character.inventory.append(salvaged)
                outputs.append(item_id)
        self._set_roundtime(state, now, 4)
        names = [self.catalog.items[item_id].name for item_id in outputs]
        return _HandlerResult(
            (f"You salvage {definition.name} into {_natural_list(names)}.", "Roundtime: 4 sec."),
            (DomainEvent("economy.item_salvaged", {"item_id": definition.id, "outputs": outputs}),),
            True,
        )

class CompanionService(EngineService):
    """Owns the companion application boundary."""

    def _ensure_companion_progress(
        self,
        state: GameState,
        companion,
        *,
        sync_level: bool = False,
    ) -> CompanionProgressState:
        """Create or intentionally advance companion progression.

        Read-only projections and failed commands must not silently level or heal a
        companion.  Callers opt into level synchronization only after an actual
        progression mutation such as a player level award or shared field insight.
        """

        base_health = companion.base_health or max(24, 20 + companion.power * 2)
        # Story-bound companions mirror the complete authored solo campaign,
        # not only the first foundation.  Sol therefore advances beside the
        # player through both the level 1-10 foundation and the level 11-20
        # journeyman phase, while still remaining unable to race ahead on
        # banked field insight.
        authored_story_cap = max(
            self.catalog.beginner_experience.target_level,
            self.catalog.journeyman_experience.target_level,
        )
        level_cap = authored_story_cap if companion.story_bound else 100
        progress = state.character.companion_progress.get(companion.id)
        if progress is None:
            level = max(1, min(state.character.level, level_cap))
            maximum = base_health + 4 * (level - 1)
            progress = CompanionProgressState(
                level=level,
                experience=(level - 1) * INSIGHT_PER_LEVEL,
                health=maximum,
                max_health=maximum,
                order="balanced",
            )
            state.character.companion_progress[companion.id] = progress
            return progress

        if not sync_level:
            return progress

        if companion.story_bound:
            # Sol is a partner, not a carry.  He mirrors the current player level
            # through the authored foundation and never races ahead on banked
            # quest insight.
            target_level = max(1, min(state.character.level, level_cap))
            level_floor = (target_level - 1) * INSIGHT_PER_LEVEL
            if target_level >= level_cap:
                progress.experience = level_floor
            else:
                progress.experience = max(
                    level_floor,
                    min(
                        progress.experience,
                        target_level * INSIGHT_PER_LEVEL - 1,
                    ),
                )
        else:
            target_level = max(
                progress.level,
                min(state.character.level, level_cap),
                min(level_cap, progress.experience // INSIGHT_PER_LEVEL + 1),
            )
            progress.experience = max(
                progress.experience,
                (target_level - 1) * INSIGHT_PER_LEVEL,
            )
        expected_maximum = base_health + 4 * (target_level - 1)
        old_maximum = progress.max_health
        old_health = progress.health
        progress.level = target_level
        progress.max_health = expected_maximum
        if expected_maximum >= old_maximum:
            progress.health = min(
                progress.max_health,
                old_health + (expected_maximum - old_maximum),
            )
        else:
            progress.health = min(progress.max_health, old_health)
        return progress

    def _active_companion_context(
        self,
        state: GameState,
        now: float,
        *,
        recover_if_ready: bool = False,
    ):
        companion = self.catalog.economy.mercenaries.get(
            state.character.companion_id or ""
        )
        if companion is None:
            return None, None
        if companion.id == "sol" and (
            "sol_left_intake" in state.flags or "sol_escaped" in state.flags
        ):
            return None, None
        progress = self._ensure_companion_progress(state, companion)
        if (
            recover_if_ready
            and progress.downed_until
            and now >= progress.downed_until
        ):
            progress.downed_until = 0.0
            progress.health = max(
                progress.health,
                max(1, progress.max_health // 2),
            )
        return companion, progress

    def _award_companion_experience(
        self,
        state: GameState,
        amount: int,
        now: float,
        *,
        reason: str,
    ) -> tuple[list[str], list[DomainEvent]]:
        companion, progress = self._active_companion_context(state, now)
        if companion is None or progress is None or amount <= 0:
            return [], []
        before_level = progress.level
        progress.experience = min(100_000_000, progress.experience + amount)
        self._ensure_companion_progress(
            state,
            companion,
            sync_level=True,
        )
        lines = [
            f"[Partner] {companion.name} gains {amount} shared field insight ({reason})."
        ]
        events = [
            DomainEvent(
                "companion.experience_gained",
                {
                    "companion_id": companion.id,
                    "amount": amount,
                    "experience": progress.experience,
                    "reason": reason,
                },
            )
        ]
        if progress.level > before_level:
            lines.append(
                f"[Partner level] {companion.name} rises from level {before_level} to {progress.level}; "
                f"integrity expands to {progress.max_health}."
            )
            events.append(
                DomainEvent(
                    "companion.level_gained",
                    {
                        "companion_id": companion.id,
                        "level_before": before_level,
                        "level_after": progress.level,
                        "max_health": progress.max_health,
                    },
                )
            )
        return lines, events

    @staticmethod
    def _sync_story_companion_order(state: GameState) -> None:
        progress = state.character.companion_progress.get("sol")
        if progress is None:
            return
        for order in ("guard", "assault", "balanced"):
            if f"sol_order_{order}" in state.flags:
                progress.order = order
                return

    def _detach_sol_if_story_requires(
        self,
        state: GameState,
    ) -> tuple[list[str], list[DomainEvent]]:
        if state.character.companion_id != "sol":
            return [], []
        if "sol_left_intake" not in state.flags and "sol_escaped" not in state.flags:
            return [], []
        state.character.companion_id = None
        return (
            [
                "[Formation change] Sol leaves the active companion slot as the Collector confrontation begins."
            ],
            [DomainEvent("companion.story_detached", {"companion_id": "sol"})],
        )

    def _resolve_mercenary(self, query: str):
        normalized = query.casefold().replace("-", "_").replace(" ", "_")
        exact = self.catalog.economy.mercenaries.get(normalized)
        if exact is not None:
            return exact
        matches = [merc for merc in self.catalog.economy.mercenaries.values() if merc.name.casefold().startswith(query.casefold()) or merc.role.casefold().startswith(query.casefold())]
        return matches[0] if len(matches) == 1 else None

    def _companion_sync_projection(
        self, state: GameState, now: float
    ) -> dict[str, object]:
        companion, progress = self._active_companion_context(state, now)
        unlocked = "partner_synchrony_complete" in state.flags
        target, target_error = self._resolve_creature(state, "")
        target_name = None
        target_id = None
        used = False
        if target is not None:
            target_id = target.instance_id
            target_name = self.catalog.creatures[target.definition_id].name
            used = f"companion_sync_used:{target.instance_id}" in state.flags
        recovering = bool(
            progress is not None
            and (progress.downed_until > now or progress.health <= 0)
        )
        available = bool(
            unlocked
            and companion is not None
            and companion.assist_kind == "partner"
            and progress is not None
            and not recovering
            and target is not None
            and not used
        )
        reason = None
        if not unlocked:
            reason = "Complete The Partner in the Present before using field synchrony."
        elif companion is None or progress is None:
            reason = "No active field partner is available."
        elif companion.assist_kind != "partner":
            reason = f"{companion.name} follows a fixed support contract rather than partner synchrony."
        elif recovering:
            reason = f"{companion.name} is recovering and cannot synchronize yet."
        elif target is None:
            reason = target_error or "Choose one living target first."
        elif used:
            reason = "This target has already been synchronized once."
        return {
            "unlocked": unlocked,
            "available": available,
            "reason": reason,
            "target_id": target_id,
            "target_name": target_name,
            "used": used,
            "command": (
                f"companion sync {target_name}" if target_name else "companion sync"
            ),
            "summary": (
                "Trigger one player-owned shared beat. Balanced creates measured pressure, "
                "Guard adds protection, and Assault commits harder while still yielding the next chosen action."
            ),
        }

    def _companion(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        # Story-authored companion actions share the public COMPANION verb but
        # are not necessarily attacks against a living creature. Resolve an
        # exact active-stage action before the generic field-synchrony command
        # so narrative coordination (for example a shielded counter-signal
        # exercise) cannot be misrouted into combat target resolution.
        story_action, _story_error = self._resolve_story_action(
            state,
            "companion",
            self._query(command.args),
        )
        if story_action is not None:
            if self._live_creatures(state) and not story_action.allow_under_pressure:
                return _HandlerResult(
                    (
                        "Hostile pressure makes that story action unsafe. Defeat the threat or withdraw first.",
                    )
                )
            available, reason = self._story_action_availability(state, story_action)
            if not available:
                return _HandlerResult((reason or "That companion action is not available.",))
            return self._apply_story_action(state, story_action, now)

        active, progress = self._active_companion_context(state, now)
        query = command.args[0].casefold() if command.args else "status"
        if query == "sync":
            if len(command.args) > 1 and command.args[1].casefold() in {"status", "list"}:
                projection = self._companion_sync_projection(state, now)
                target_line = (
                    f"Current target: {projection['target_name']}."
                    if projection["target_name"]
                    else "Current target: none selected."
                )
                availability = (
                    f"Ready: {str(projection['command']).upper()}."
                    if projection["available"]
                    else f"Unavailable: {projection['reason']}"
                )
                return _HandlerResult(
                    (
                        "Partner synchrony:\n"
                        f"  Unlocked: {'yes' if projection['unlocked'] else 'no'}\n"
                        f"  {target_line}\n"
                        f"  {availability}\n"
                        f"  {projection['summary']}\n"
                        "  One use per living target; it never selects the next player action.",
                    )
                )
            if "partner_synchrony_complete" not in state.flags:
                return _HandlerResult(
                    ("Complete The Partner in the Present before using COMPANION SYNC.",)
                )
            if active is None or progress is None:
                return _HandlerResult(("No active field partner can synchronize with you.",))
            if active.assist_kind != "partner":
                return _HandlerResult(
                    (f"{active.name} follows a fixed support role rather than partner synchrony.",)
                )
            if progress.downed_until > now or progress.health <= 0:
                remaining = max(0, math.ceil(progress.downed_until - now))
                return _HandlerResult(
                    (f"{active.name} is recovering for {remaining} seconds and cannot synchronize yet.",)
                )
            target_query = self._query(command.args[1:])
            target, error = self._resolve_creature(state, target_query)
            if target is None:
                return _HandlerResult((error or "Synchronize against which living target?",))
            used_flag = f"companion_sync_used:{target.instance_id}"
            if used_flag in state.flags:
                return _HandlerResult(
                    ("This target has already received one synchronized maneuver. Choose your next action directly.",)
                )
            # A valid synchronized maneuver is itself Sol's committed action.
            # Start the encounter only after validation so rejected commands stay
            # read-only, then place Sol on recovery before field time advances.
            synchronized = self.combat_scheduler.synchronize(state, now)
            definition = self.catalog.creatures[target.definition_id]
            order = progress.order
            raw_damage = 0
            damage = 0
            guard_added = 0
            finish_reserved = False
            if order == "guard":
                guard_added = max(10, 8 + progress.level // 2)
                state.character.guard_points = min(
                    1000, state.character.guard_points + guard_added
                )
                state.flags.add(f"companion_opening:{target.instance_id}")
                result_line = (
                    f"{active.name} folds into your guard, adds {guard_added} guard, and fixes "
                    "the target on a player-owned opening without making a direct strike."
                )
            else:
                base = 4 + progress.level // 4
                if order == "assault":
                    base += 5 + self.rng.randint(0, 2)
                raw_damage = max(1, base - definition.armor // 3)
                damage = min(raw_damage, max(0, target.health - 1))
                target.health -= damage
                if order == "assault" and target.health <= max(10, progress.level // 2 + 5):
                    finish_reserved = True
                    state.flags.add(f"companion_finish_window:{target.instance_id}")
                    state.flags.discard(f"companion_opening:{target.instance_id}")
                else:
                    state.flags.add(f"companion_opening:{target.instance_id}")
                result_line = (
                    f"{active.name} and you commit one {order} beat for {damage} damage, then "
                    + (
                        "stop at a player-owned finishing line."
                        if finish_reserved
                        else "leave a reliable player-owned opening."
                    )
                )
            state.flags.add(used_flag)
            state.flags.add(
                f"companion_sync_suppress_follow:{target.instance_id}"
            )
            state.flags.add("partner_sync_exercised")
            sol_actor = state.battle.actors.get(companion_actor_id("sol"))
            if sol_actor is not None:
                recovery = sol_recovery_seconds(order)
                sol_actor.next_action_at = max(
                    sol_actor.next_action_at,
                    state.battle.time + 4 + recovery,
                )
                sol_actor.recovery_duration = float(recovery)
                sol_actor.current_intent = self.combat_scheduler._select_sol_intent(order)
                sol_actor.target_id = None
                sol_actor.telegraph_shown = False
            progress.damage_dealt = min(
                100_000_000, progress.damage_dealt + damage
            )
            progress.setup_actions = min(
                100_000_000, progress.setup_actions + 1
            )
            if finish_reserved:
                progress.finish_reservations = min(
                    100_000_000, progress.finish_reservations + 1
                )
            self._set_roundtime(state, now, 4)
            return _HandlerResult(
                tuple(synchronized.lines)
                + (
                    f"[Partner synchrony · {order}] {result_line}",
                    "Sol holds the synchronized line rather than taking an automatic second action. Roundtime: 4 sec.",
                ),
                tuple(synchronized.events)
                + (
                    DomainEvent(
                        "companion.sync_resolved",
                        {
                            "companion_id": active.id,
                            "target": target.instance_id,
                            "order": order,
                            "damage": damage,
                            "raw_damage": raw_damage,
                            "guard_added": guard_added,
                            "finishing_window_reserved": finish_reserved,
                            "player_action_required": True,
                        },
                    ),
                ),
                True,
            )
        if query in {"advise", "advice", "hint"}:
            if active is None or progress is None:
                return _HandlerResult(("No active field partner can advise you.",))
            directive = self._directive_projection(
                state, self._build_projection(state)
            )
            room = self.catalog.rooms[state.character.room_id]
            if directive is None:
                return _HandlerResult(
                    (
                        f"{active.name} studies {room.title}. 'Nothing is forcing our hand. "
                        "Check ROUTE, JOURNAL PROGRESS, or choose the work that matters to you.'",
                    )
                )
            suggested = str(directive.get("suggested_command") or "").strip()
            summary = str(directive.get("summary") or directive.get("title") or "Review the field.")
            why = str(directive.get("why") or "It moves the current field objective without choosing for you.")
            friction = self._beginner_calibration_projection(state)
            lines = [
                f"{active.name} checks the route from {room.title}.",
                f"Objective: {summary}",
                f"Why now: {why}",
            ]
            if suggested:
                lines.append(f"Try: {suggested}")
            if friction["status"] in {"YELLOW", "RED"}:
                lines.append(
                    "We can slow the problem down: LOOK, read the directive, then take one action. "
                    "No reward depends on using this advice."
                )
            return _HandlerResult(("\n".join(lines),))
        if query in {"status", "list"}:
            lines = ["Active companion formation:"]
            if active is not None and progress is not None:
                recovery = max(0, math.ceil(progress.downed_until - now))
                lines.extend(
                    (
                        f"  {active.name} · {active.role} · level {progress.level}",
                        f"  Integrity {progress.health}/{progress.max_health} · "
                        f"shared insight {progress.experience} · order {progress.order}",
                        (
                            f"  Recovering for {recovery} sec before rejoining combat."
                            if recovery
                            else f"  {active.summary}"
                        ),
                        f"  Setups {progress.setup_actions} · reserved finishes {progress.finish_reservations} · "
                        f"player conversions {progress.player_enabled_finishes} · Sol finishes {progress.finishing_strikes}.",
                        f"  Damage {progress.damage_dealt} · intercepted {progress.damage_intercepted}.",
                    )
                )
                if active.assist_kind == "partner":
                    lines.append(
                        "  Orders: COMPANION ORDER BALANCED, GUARD, or ASSAULT. Ask COMPANION ADVISE for an optional field hint."
                    )
                    lines.extend(
                        (
                            "  Balanced: measured setup, interruption, and player-owned finishes.",
                            "  Guard: adds guard and openings without a direct strike.",
                            "  Assault: strongest damage and no interception; the explicit order may authorize Sol to finish.",
                            "  Agency ledger: "
                            f"{progress.setup_actions} setups · "
                            f"{progress.finish_reservations} finishes reserved · "
                            f"{progress.player_enabled_finishes} player conversions · "
                            f"{progress.finishing_strikes} Sol finishing strikes.",
                            "  Synchrony: COMPANION SYNC STATUS inspects the one-use player-triggered maneuver.",
                        )
                    )
            else:
                lines.append("  No active companion.")
            available = [
                merc
                for merc in self.catalog.economy.mercenaries.values()
                if not merc.hidden_from_hire
            ]
            if available:
                lines.append("Contract support available later:")
                for merc in available:
                    marker = " [active]" if active and active.id == merc.id else ""
                    lines.append(
                        f"  {merc.name} · {merc.role} · {merc.cost} credits{marker}"
                    )
            lines.append(f"Credits: {state.character.credits}.")
            return _HandlerResult(("\n".join(lines),))
        if query == "order":
            if active is None or progress is None:
                return _HandlerResult(("No active companion can receive an order.",))
            if active.assist_kind != "partner":
                return _HandlerResult(
                    (f"{active.name} follows the fixed {active.assist_kind} contract role.",)
                )
            if len(command.args) < 2:
                return _HandlerResult(
                    (
                        f"{active.name}'s current order is {progress.order}. "
                        "Choose balanced, guard, or assault.",
                    )
                )
            order = command.args[1].casefold()
            matches = [
                option for option in ("balanced", "guard", "assault")
                if option.startswith(order)
            ]
            if len(matches) != 1:
                return _HandlerResult(("Choose balanced, guard, or assault.",))
            chosen = matches[0]
            if progress.order == chosen:
                return _HandlerResult((f"{active.name} is already using the {chosen} order.",))
            before = progress.order
            progress.order = chosen
            state.flags.discard(f"sol_order_{before}")
            if active.id == "sol":
                state.flags.add(f"sol_order_{chosen}")
            descriptions = {
                "balanced": (
                    "uses measured setup strikes, interrupts dangerous answers, and "
                    "reserves safe finishing opportunities for you"
                ),
                "guard": (
                    "checks his offense, adds guard, interrupts dangerous answers, "
                    "and exposes your next opening"
                ),
                "assault": (
                    "commits to the strongest damage and leaves interception behind; "
                    "because you explicitly selected Assault, he may take a finishing strike"
                ),
            }
            return _HandlerResult(
                (
                    f"You shift {active.name} from {before} to {chosen}. "
                    f"He {descriptions[chosen]}.",
                ),
                (
                    DomainEvent(
                        "companion.order_changed",
                        {
                            "companion_id": active.id,
                            "from": before,
                            "to": chosen,
                        },
                    ),
                ),
                True,
            )
        if query == "dismiss":
            if active is None:
                return _HandlerResult(("No companion is currently attached.",))
            if not active.dismissible:
                return _HandlerResult(
                    (
                        f"{active.name} is part of the active story formation and cannot be silently dismissed. "
                        "Change his order instead; the story will release the slot explicitly.",
                    )
                )
            state.character.companion_id = None
            return _HandlerResult(
                (f"{active.name} leaves the active formation without refund or penalty.",),
                (DomainEvent("companion.dismissed", {"mercenary_id": active.id}),),
                True,
            )
        if query != "hire":
            return _HandlerResult(
                (
                    "Use COMPANION STATUS, COMPANION ADVISE, COMPANION ORDER <balanced|guard|assault>, "
                    "COMPANION SYNC [target], COMPANION HIRE <name>, or COMPANION DISMISS.",
                )
            )
        merc = self._resolve_mercenary(self._query(command.args[1:]))
        if merc is None or merc.hidden_from_hire:
            return _HandlerResult(("Choose one listed contract companion.",))
        if state.character.room_id != merc.hire_room_id:
            return _HandlerResult(
                (f"{merc.name} can be contracted only at {self.catalog.rooms[merc.hire_room_id].title}.",)
            )
        if active is not None:
            return _HandlerResult((f"Dismiss {active.name} before hiring another companion.",))
        if state.character.credits < merc.cost:
            return _HandlerResult(
                (f"You need {merc.cost} credits; you have {state.character.credits}.",)
            )
        state.character.credits -= merc.cost
        state.character.companion_id = merc.id
        self._ensure_companion_progress(state, merc, sync_level=True)
        return _HandlerResult(
            (
                f"{merc.name} joins as your bounded field companion.",
                f"Credits remaining: {state.character.credits}.",
            ),
            (DomainEvent("companion.hired", {"mercenary_id": merc.id, "cost": merc.cost}),),
            True,
        )

    def _party_projection(self, state: GameState) -> dict[str, object]:
        """Project bounded field details without claiming full party support."""

        context = self._active_story_context(state)
        active_quest_id = context[0].id if context is not None else None
        field_mode = bool(
            active_quest_id == "the_people_behind_the_signal"
            or "field_cohort_detail_active" in state.flags
            or "field_cohort_detail_complete" in state.flags
            or "journey_field_cohort_formed" in state.story.records
        )
        companion, _progress = self._active_companion_context(
            state, self.clock.now()
        )
        available_actions: list[dict[str, object]] = []
        if context is not None:
            _, stage = context
            for action in stage.actions:
                if action.verb != "party":
                    continue
                available, reason = self._story_action_availability(state, action)
                label, summary, _ = self._story_action_label(state, action)
                available_actions.append(
                    {
                        "id": action.id,
                        "label": label,
                        "summary": summary,
                        "command": self._story_action_command(action),
                        "available": available,
                        "unavailable_reason": reason,
                    }
                )
        primary_command = next(
            (
                str(action["command"])
                for action in available_actions
                if action["available"]
            ),
            "party",
        )

        if field_mode:
            active = (
                "field_cohort_detail_active" in state.flags
                and "field_cohort_detail_complete" not in state.flags
            )
            complete = "field_cohort_detail_complete" in state.flags
            formed = active or complete or "journey_field_cohort_formed" in state.story.records
            formation_map = (
                ("field_cohort_formation_balanced", "Balanced"),
                ("field_cohort_formation_offensive", "Offensive"),
                ("field_cohort_formation_defensive", "Defensive"),
            )
            order = next(
                (label for flag, label in formation_map if flag in state.flags),
                "Not selected",
            )
            evidence_status = (
                "expired"
                if complete
                else "current and anonymous"
                if "field_cohort_tokens_verified" in state.flags
                else "awaiting verification"
                if formed
                else "not formed"
            )
            status = (
                "Closed — authority expired"
                if complete
                else "Active — temporary authority"
                if active
                else "Ready to form"
                if active_quest_id == "the_people_behind_the_signal"
                else "No temporary detail active"
            )
            roles = [
                {
                    "id": "player",
                    "name": state.character.name,
                    "role": "Field Lead",
                    "function": "Chooses one visible formation and one player-owned synchrony beat.",
                    "active": active,
                },
                {
                    "id": "sera_vann",
                    "name": "Sera Vann",
                    "role": "Public Witness",
                    "function": "Keeps scope, refusal, and expiration visible without collecting identities.",
                    "active": active,
                },
                {
                    "id": "neutral_cohort",
                    "name": "Anonymous neutral cohort",
                    "role": "Self-directed travelers",
                    "function": "Retains refusal, return, and identity; no permanent roster is created.",
                    "active": active,
                },
            ]
            return {
                "detail_kind": "field_cohort",
                "label": "Protected cohort detail",
                "active": active,
                "formed": formed,
                "complete": complete,
                "status": status,
                "evidence_label": "Route evidence",
                "report_status": evidence_status,
                "order_label": "Formation",
                "order": order,
                "authority_scope": (
                    "Protect one anonymous neutral cohort from the Second Horizon apron to the public return shelter."
                ),
                "authority_expiration": (
                    "Field Lead, Public Witness, formation, route, and synchrony authority expire at shelter."
                ),
                "boundary": (
                    "This is not a persistent traveler roster, faction, guild, Commander rank, route ownership, or the full six-player party system."
                ),
                "roles": roles,
                "actions": available_actions,
                "primary_command": primary_command,
                "active_companion": (
                    {
                        "id": companion.id,
                        "name": companion.name,
                        "separate_from_detail": True,
                    }
                    if companion is not None
                    else None
                ),
            }

        active = (
            "relief_detail_active" in state.flags
            and "relief_detail_complete" not in state.flags
        )
        complete = "relief_detail_complete" in state.flags
        formed = active or complete or "relief_detail_formed" in state.story.records
        report_status = (
            "expired"
            if complete
            else "received"
            if "relief_report_received" in state.flags
            else "awaiting"
            if formed
            else "not formed"
        )
        order_map = (
            ("relief_order_screen", "Patient screen"),
            ("relief_order_stagger", "Staggered crossing"),
            ("relief_order_feint", "Attention draw"),
        )
        order = next(
            (label for flag, label in order_map if flag in state.flags),
            "Not issued",
        )
        status = (
            "Closed — authority expired"
            if complete
            else "Active — temporary authority"
            if active
            else "Ready to form"
            if active_quest_id == "one_report_many_lives"
            else "No temporary detail active"
        )
        roles = [
            {
                "id": "player",
                "name": state.character.name,
                "role": "Leader",
                "function": "Issues one visible order tied to the bounded report.",
                "active": active,
            },
            {
                "id": "taro_scout",
                "name": "Taro Scout",
                "role": "Report",
                "function": "Observes movement facts and expires the report at shelter.",
                "active": active,
            },
            {
                "id": "neme_patch",
                "name": "Neme Patch",
                "role": "Support",
                "function": "Maintains patient accountability and shelter intake.",
                "active": active,
            },
        ]
        return {
            "detail_kind": "relief",
            "label": "Relief detail",
            "active": active,
            "formed": formed,
            "complete": complete,
            "status": status,
            "evidence_label": "Report",
            "report_status": report_status,
            "order_label": "Order",
            "order": order,
            "authority_scope": (
                "Move the fever patients from the neutral clinic road to the shelter annex."
            ),
            "authority_expiration": (
                "Leader, Report, and Support expire when the final patient reaches shelter."
            ),
            "boundary": (
                "This is not faction membership, guild status, commander rank, or the full six-player party system."
            ),
            "roles": roles,
            "actions": available_actions,
            "primary_command": primary_command,
            "active_companion": (
                {
                    "id": companion.id,
                    "name": companion.name,
                    "separate_from_detail": True,
                }
                if companion is not None
                else None
            ),
        }

    def _party(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        query = self._query(command.args)
        if query.casefold() in {"", "status", "list", "roster"}:
            projection = self._party_projection(state)
            lines = [
                f"{projection['label']}: {projection['status']}",
                f"Scope: {projection['authority_scope']}",
                "Roster:",
            ]
            for role in projection["roles"]:
                posture = "active" if role["active"] else "inactive"
                lines.append(
                    f"  {role['role']}: {role['name']} ({posture}) — {role['function']}"
                )
            lines.extend(
                (
                    f"{projection['evidence_label']}: {projection['report_status']}.",
                    f"{projection['order_label']}: {projection['order']}.",
                    f"Expiration: {projection['authority_expiration']}",
                    str(projection["boundary"]),
                )
            )
            companion_projection = projection["active_companion"]
            if companion_projection is not None:
                lines.append(
                    f"Active field partner: {companion_projection['name']} remains a separate companion slot."
                )
            available = [
                action for action in projection["actions"] if action["available"]
            ]
            if available:
                lines.append(
                    "Available: "
                    + _natural_list(
                        [str(action["command"]).upper() for action in available]
                    )
                    + "."
                )
            lines.extend(self._foundation_party_lines(state))
            return _HandlerResult(("\n".join(lines),))
        return self._execute_story_verb(
            state,
            command,
            now,
            verb="party",
        )

class ProgressionService(EngineService):
    """Owns the progression application boundary."""

    def _selected_specialization(self, state: GameState):
        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        if class_definition is None:
            return None
        selected = next(
            (
                flag.split(":", 1)[1]
                for flag in state.flags
                if flag.startswith("specialization:")
            ),
            None,
        )
        return (
            class_definition.ability_branches.get(selected)
            if selected is not None
            else None
        )

    def _selected_specialization_upgrade(self, state: GameState, branch):
        upgrade_id = state.character.specialization_upgrade_id
        if branch is None or not upgrade_id:
            return None
        return branch.upgrade_options.get(upgrade_id)

    def _specialization_values(self, state: GameState, branch) -> dict[str, int]:
        upgrade = self._selected_specialization_upgrade(state, branch)
        passive_power = branch.passive.power
        power = branch.power
        cooldown = branch.cooldown
        follow_up_power = branch.follow_up.power
        if branch.passive.kind == "power":
            power += passive_power
            follow_up_power += passive_power
        elif branch.passive.kind == "tempo":
            cooldown -= passive_power
        if upgrade is not None:
            power += upgrade.power_bonus
            follow_up_power += upgrade.follow_up_power_bonus
            cooldown += upgrade.cooldown_delta
            commitment_roundtime = (
                branch.commitment_roundtime
                + upgrade.commitment_roundtime_delta
            )
            follow_up_window = (
                branch.follow_up.window_seconds
                + upgrade.follow_up_window_bonus
            )
        else:
            commitment_roundtime = branch.commitment_roundtime
            follow_up_window = branch.follow_up.window_seconds
        return {
            "power": max(1, power),
            "cooldown": max(5, cooldown),
            "follow_up_power": max(1, follow_up_power),
            "commitment_roundtime": max(1, commitment_roundtime),
            "follow_up_window": max(3, follow_up_window),
            "follow_up_roundtime": max(1, branch.follow_up.roundtime),
        }

    def _apply_specialization_passive(
        self, state: GameState, branch
    ) -> tuple[str, ...]:
        character = state.character
        if branch.passive.kind == "guard":
            before = character.guard_points
            character.guard_points = min(
                1000, character.guard_points + branch.passive.power
            )
            return (
                f"[Passive · {branch.passive.name}] Guard {before} → "
                f"{character.guard_points}.",
            )
        if branch.passive.kind == "recovery":
            before = character.health
            character.health = min(
                character.max_health,
                character.health + branch.passive.power,
            )
            return (
                f"[Passive · {branch.passive.name}] Health {before} → "
                f"{character.health}.",
            )
        return ()

    def _prime_specialization_follow_up(
        self, state: GameState, branch, now: float, values: dict[str, int]
    ) -> str:
        state.character.specialization_follow_up_ready_until = (
            now + values["follow_up_window"]
        )
        return (
            f"Follow-up ready: {branch.follow_up.name} for "
            f"{values['follow_up_window']} sec."
        )

    def _resolve_ability_branch(self, state: GameState, query: str):
        class_definition = self.catalog.creation.classes.get(
            state.character.build.class_id or ""
        )
        if class_definition is None:
            return None
        normalized = query.casefold().replace("-", "_").replace(" ", "_")
        exact = class_definition.ability_branches.get(normalized)
        if exact is not None:
            return exact
        matches = [
            branch
            for branch in class_definition.ability_branches.values()
            if branch.name.casefold().startswith(query.casefold())
            or any(
                noun.casefold().startswith(query.casefold())
                for noun in branch.nouns
            )
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _resolve_specialization_upgrade(branch, query: str):
        normalized = query.casefold().replace("-", "_").replace(" ", "_")
        exact = branch.upgrade_options.get(normalized)
        if exact is not None:
            return exact
        matches = [
            upgrade
            for upgrade in branch.upgrade_options.values()
            if upgrade.name.casefold().startswith(query.casefold())
            or upgrade.id.casefold().startswith(normalized)
        ]
        return matches[0] if len(matches) == 1 else None

    def _specialization_follow_up(
        self,
        state: GameState,
        branch,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        character = state.character
        remaining = max(
            0,
            math.ceil(character.specialization_follow_up_ready_until - now),
        )
        if remaining <= 0:
            character.specialization_follow_up_ready_until = 0.0
            return _HandlerResult(
                ("No specialization follow-up is currently available.",)
            )
        values = self._specialization_values(state, branch)
        follow_up = branch.follow_up
        query = self._query(command.args[1:])
        lines = [
            f"[Follow-up · {follow_up.name}] {follow_up.summary}"
        ]
        events = [
            DomainEvent(
                "class.specialization_follow_up_used",
                {
                    "class_id": state.character.build.class_id,
                    "branch_id": branch.id,
                    "kind": follow_up.kind,
                },
            )
        ]
        changed = False
        if follow_up.kind in {"attack", "precision"}:
            if not query:
                return _HandlerResult(
                    (f"Use ABILITY FOLLOWUP <target> for {follow_up.name}.",)
                )
            state.flags.add(f"specialization_followup_attack:{branch.id}")
            result = self._attack(
                state,
                ParsedCommand(
                    "attack",
                    command.args[1:],
                    command.raw,
                    command.recovery,
                ),
                now,
            )
            state.flags.discard(f"specialization_followup_attack:{branch.id}")
            if not result.changed:
                return result
            lines.extend(result.lines)
            events.extend(result.events)
            changed = True
        elif follow_up.kind == "heal":
            before = character.health
            character.health = min(
                character.max_health,
                character.health + values["follow_up_power"],
            )
            lines.append(f"Health {before} → {character.health}.")
            changed = True
        elif follow_up.kind == "guard":
            before = character.guard_points
            character.guard_points = min(
                1000,
                character.guard_points + values["follow_up_power"],
            )
            lines.append(f"Guard {before} → {character.guard_points}.")
            changed = True
        elif follow_up.kind == "support":
            before_health = character.health
            before_guard = character.guard_points
            character.health = min(
                character.max_health,
                character.health + values["follow_up_power"],
            )
            character.guard_points = min(
                1000,
                character.guard_points
                + max(1, values["follow_up_power"] // 2),
            )
            lines.append(
                f"Health {before_health} → {character.health}; guard "
                f"{before_guard} → {character.guard_points}."
            )
            changed = True
        elif follow_up.kind == "control":
            if not self._live_creatures(state):
                return _HandlerResult(
                    ("No active opponent can be controlled here.",)
                )
            state.flags.add("specialization_control_ready")
            lines.append("The next answering counterstrike is suppressed.")
            changed = True
        elif follow_up.kind == "report":
            target, error = self._resolve_creature(state, query)
            if target is None:
                return _HandlerResult((error or "Report which opponent?",))
            state.flags.add(f"reported_target:{target.instance_id}")
            state.flags.add(
                f"specialization_report_power:{target.instance_id}:"
                f"{values['follow_up_power']}"
            )
            lines.append(
                "The target's route is reported for the next committed attack."
            )
            changed = True
        elif follow_up.kind == "repair":
            candidates = [
                item
                for item in character.inventory
                if item.durability is not None
                and item.durability < self._effective_max_durability(item)
            ]
            if not candidates:
                return _HandlerResult(
                    ("No carried equipment currently needs repair.",)
                )
            target = min(candidates, key=lambda item: item.durability or 0)
            before = int(target.durability or 0)
            target.durability = min(
                self._effective_max_durability(target),
                before + values["follow_up_power"],
            )
            lines.append(
                f"{self.catalog.items[target.definition_id].name} durability "
                f"{before} → {target.durability}."
            )
            changed = True
        else:
            return _HandlerResult(
                ("That specialization follow-up is not implemented safely.",)
            )
        if not changed:
            return _HandlerResult(("The follow-up produced no safe change.",))
        character.specialization_follow_up_ready_until = 0.0
        self._set_roundtime(
            state, now, values["follow_up_roundtime"]
        )
        lines.extend(self._apply_specialization_passive(state, branch))
        lines.append(
            f"Roundtime: at least {values['follow_up_roundtime']} sec."
        )
        return _HandlerResult(tuple(lines), tuple(events), True)

    def _experience(self, state: GameState, command: ParsedCommand, now: float) -> _HandlerResult:
        experience = state.character.experience
        return _HandlerResult(
            (
                f"Learned experience: {experience.absorbed}\n"
                f"Field insight awaiting absorption: {experience.field_pool}\n"
                "Insight settles in pulses while you explore, recover, or socialize.",
            )
        )

    def _resolve_training_option(
        self,
        query: str,
    ) -> tuple[TrainingOptionDefinition | None, str | None]:
        if not query:
            return None, "Name a training discipline."
        scored: list[tuple[int, TrainingOptionDefinition]] = []
        for option in self.catalog.progression.options.values():
            terms = {
                option.id.casefold(),
                option.name.casefold(),
                *(noun.casefold() for noun in option.nouns),
            }
            if query in terms:
                score = 3
            elif any(term.startswith(query) for term in terms):
                score = 2
            elif any(query in term for term in terms):
                score = 1
            else:
                continue
            scored.append((score, option))
        if not scored:
            return None, f"No training discipline matches {query!r}."
        best = max(score for score, _ in scored)
        matches = [option for score, option in scored if score == best]
        if len(matches) > 1:
            return None, (
                "Choose one discipline: "
                + _natural_list([option.id for option in matches])
                + "."
            )
        return matches[0], None

    def _training_summary(self, state: GameState) -> str:
        character = state.character
        training = character.training
        progression = self.catalog.progression
        profile = progression.profiles[training.profile_id]
        lines = [
            f"Training points: physical {training.physical_points}; "
            f"mental {training.mental_points}.",
            f"Training path: {profile.name} ({profile.id}); "
            f"{'locked' if training.profile_locked else 'open before the first rank'}.",
            f"Early refunds: {training.early_refunds_remaining} remaining "
            f"through level {progression.early_refund_level_limit}.",
            "Disciplines:",
        ]
        for option in progression.options.values():
            rank = training.ranks.get(option.id, 0)
            lines.append(
                f"  {option.id:<10} rank {rank}/{option.max_rank}; "
                f"cost {effective_training_cost(option, profile)} "
                f"{option.pool}; "
                f"+{option.gain_per_rank} {option.attribute.replace('_', ' ')}"
            )
        lines.append(
            "Use TRAIN <discipline> at a training station. "
            "Use RETRAIN <discipline> for an eligible early refund. "
            "Use PATH to compare pre-training profiles."
        )
        return "\n".join(lines)

    def _train(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if not command.args:
            return _HandlerResult((self._training_summary(state),))
        room = self.catalog.rooms[state.character.room_id]
        if "training_station" not in room.facilities:
            return _HandlerResult(
                ("You need a neutral training station to commit a rank.",)
            )
        if self._live_creatures(state):
            return _HandlerResult(
                ("You cannot begin focused training with an active opponent nearby.",)
            )
        option, error = self._resolve_training_option(
            self._query(command.args)
        )
        if option is None:
            return _HandlerResult((error or "Train what?",))
        try:
            change = buy_training_rank(
                state.character,
                option,
                self.catalog.progression.profiles[
                    state.character.training.profile_id
                ],
            )
        except ValueError as exc:
            return _HandlerResult((f"Training refused: {exc}.",))
        self._set_roundtime(state, now, 2)
        return _HandlerResult(
            (
                f"You complete one cycle of {option.name}.",
                f"{option.id.capitalize()} rises from rank "
                f"{change.rank_before} to {change.rank_after}; "
                f"{change.pool} points fall from {change.points_before} "
                f"to {change.points_after}.",
                f"{change.attribute.replace('_', ' ').capitalize()} rises "
                f"from {change.attribute_before} to {change.attribute_after}.",
                "Roundtime: 2 sec.",
            ),
            (
                DomainEvent(
                    "progression.rank_trained",
                    {
                        "option_id": option.id,
                        "rank_before": change.rank_before,
                        "rank_after": change.rank_after,
                        "pool": change.pool,
                        "points_before": change.points_before,
                        "points_after": change.points_after,
                        "attribute": change.attribute,
                        "attribute_before": change.attribute_before,
                        "attribute_after": change.attribute_after,
                        "profile_id": state.character.training.profile_id,
                        "cost": change.points_before - change.points_after,
                    },
                ),
            ),
            True,
        )

    def _retrain(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if not command.args:
            return _HandlerResult(
                (
                    f"Early refunds remaining: "
                    f"{state.character.training.early_refunds_remaining}; "
                    f"available through level "
                    f"{self.catalog.progression.early_refund_level_limit}.",
                )
            )
        room = self.catalog.rooms[state.character.room_id]
        if "training_station" not in room.facilities:
            return _HandlerResult(
                ("You need a neutral training station to refund a rank.",)
            )
        if self._live_creatures(state):
            return _HandlerResult(
                ("You cannot restructure training with an active opponent nearby.",)
            )
        option, error = self._resolve_training_option(
            self._query(command.args)
        )
        if option is None:
            return _HandlerResult((error or "Retrain what?",))
        try:
            change = refund_training_rank(
                state.character,
                option,
                self.catalog.progression,
                self.catalog.progression.profiles[
                    state.character.training.profile_id
                ],
            )
        except ValueError as exc:
            return _HandlerResult((f"Retraining refused: {exc}.",))
        self._set_roundtime(state, now, 2)
        return _HandlerResult(
            (
                f"You unwind one rank of {option.name}.",
                f"{option.id.capitalize()} falls from rank "
                f"{change.rank_before} to {change.rank_after}; "
                f"{change.pool} points rise from {change.points_before} "
                f"to {change.points_after}.",
                f"{state.character.training.early_refunds_remaining} "
                "early refunds remain.",
                "Roundtime: 2 sec.",
            ),
            (
                DomainEvent(
                    "progression.rank_refunded",
                    {
                        "option_id": option.id,
                        "rank_before": change.rank_before,
                        "rank_after": change.rank_after,
                        "pool": change.pool,
                        "points_before": change.points_before,
                        "points_after": change.points_after,
                        "attribute": change.attribute,
                        "attribute_before": change.attribute_before,
                        "attribute_after": change.attribute_after,
                        "profile_id": state.character.training.profile_id,
                        "cost_refunded": (
                            change.points_after - change.points_before
                        ),
                        "refunds_remaining": (
                            state.character.training.early_refunds_remaining
                        ),
                    },
                ),
            ),
            True,
        )

    def _resolve_training_profile(
        self,
        query: str,
    ) -> tuple[TrainingProfileDefinition | None, str | None]:
        if not query:
            return None, "Name a training path."
        scored: list[tuple[int, TrainingProfileDefinition]] = []
        for profile in self.catalog.progression.profiles.values():
            terms = {
                profile.id.casefold(),
                profile.name.casefold(),
                *(noun.casefold() for noun in profile.nouns),
            }
            if query in terms:
                score = 3
            elif any(term.startswith(query) for term in terms):
                score = 2
            elif any(query in term for term in terms):
                score = 1
            else:
                continue
            scored.append((score, profile))
        if not scored:
            return None, f"No training path matches {query!r}."
        best = max(score for score, _ in scored)
        matches = [profile for score, profile in scored if score == best]
        if len(matches) > 1:
            return None, (
                "Choose one training path: "
                + _natural_list([profile.id for profile in matches])
                + "."
            )
        return matches[0], None

    def _profile_summary(self, state: GameState) -> str:
        progression = self.catalog.progression
        training = state.character.training
        lines = [
            f"Active training path: {progression.profiles[training.profile_id].name} "
            f"({training.profile_id}).",
            f"Path changes remaining: {training.profile_changes_remaining}; "
            f"{'locked' if training.profile_locked else 'open before the first rank'}.",
            "Available paths and discipline costs:",
        ]
        for profile in progression.profiles.values():
            costs = ", ".join(
                f"{option.id} {effective_training_cost(option, profile)}"
                for option in progression.options.values()
            )
            lines.append(f"  {profile.id:<10} {costs} - {profile.description}")
        lines.append(
            "Use PATH <profile> at a training station before buying a rank. "
            "Choosing a non-default path uses the one preview change."
        )
        return "\n".join(lines)

    def _path(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if not command.args:
            return _HandlerResult((self._profile_summary(state),))
        room = self.catalog.rooms[state.character.room_id]
        if "training_station" not in room.facilities:
            return _HandlerResult(
                ("You need a neutral training station to choose a path.",)
            )
        if self._live_creatures(state):
            return _HandlerResult(
                ("You cannot realign training with an active opponent nearby.",)
            )
        profile, error = self._resolve_training_profile(
            self._query(command.args)
        )
        if profile is None:
            return _HandlerResult((error or "Choose which path?",))
        try:
            change = choose_training_profile(state.character, profile)
        except ValueError as exc:
            return _HandlerResult((f"Path change refused: {exc}.",))
        self._set_roundtime(state, now, 2)
        return _HandlerResult(
            (
                f"You align your training plan with {profile.name}.",
                f"Path changes from {change.profile_before} to "
                f"{change.profile_after}; {change.changes_remaining} "
                "preview changes remain.",
                "The path locks when you buy your first rank.",
                "Roundtime: 2 sec.",
            ),
            (
                DomainEvent(
                    "progression.profile_changed",
                    {
                        "profile_before": change.profile_before,
                        "profile_after": change.profile_after,
                        "changes_remaining": change.changes_remaining,
                    },
                ),
            ),
            True,
        )

    def _training_option_projection(
        self,
        state: GameState,
        option: TrainingOptionDefinition,
    ) -> dict[str, object]:
        character = state.character
        training = character.training
        profile = self.catalog.progression.profiles[training.profile_id]
        cost = effective_training_cost(option, profile)
        rank = training.ranks.get(option.id, 0)
        points = int(getattr(training, f"{option.pool}_points"))
        capped = rank >= option.max_rank
        affordable = not capped and points >= cost
        if capped:
            reason = "rank cap reached"
        elif not affordable:
            reason = f"need {cost - points} more {option.pool} points"
        else:
            reason = "ready"
        return {
            "id": option.id,
            "name": option.name,
            "description": option.description,
            "pool": option.pool,
            "cost": cost,
            "rank": rank,
            "max_rank": option.max_rank,
            "affordable": affordable,
            "reason": reason,
            "points_before": points,
            "points_after": points - cost if affordable else None,
            "attribute": option.attribute,
            "attribute_before": int(getattr(character, option.attribute)),
            "attribute_after": (
                int(getattr(character, option.attribute))
                + option.gain_per_rank
                if not capped
                else int(getattr(character, option.attribute))
            ),
        }

    def _plan(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if command.args:
            option, error = self._resolve_training_option(
                self._query(command.args)
            )
            if option is None:
                return _HandlerResult((error or "Plan which discipline?",))
            projection = self._training_option_projection(state, option)
            points_after = projection["points_after"]
            outcome = (
                f"{projection['points_before']} -> {points_after} "
                f"{projection['pool']} points; "
                f"{projection['attribute'].replace('_', ' ')} "
                f"{projection['attribute_before']} -> "
                f"{projection['attribute_after']}"
                if projection["affordable"]
                else str(projection["reason"])
            )
            return _HandlerResult(
                (
                    f"{projection['name']} ({projection['id']}): rank "
                    f"{projection['rank']}/{projection['max_rank']}; cost "
                    f"{projection['cost']} {projection['pool']}.",
                    str(projection["description"]),
                    f"Projected next step: {outcome}.",
                )
            )
        character = state.character
        training = character.training
        profile = self.catalog.progression.profiles[training.profile_id]
        next_milestone = (training.last_awarded_milestone + 1) * 100
        insight_remaining = max(
            0,
            next_milestone - character.experience.absorbed,
        )
        lines = [
            "Training plan:",
            f"  path {profile.id} ({profile.name}); "
            f"{'locked' if training.profile_locked else 'open before the first rank'}",
            f"  points {training.physical_points} physical / "
            f"{training.mental_points} mental",
            f"  next level milestone {next_milestone} learned insight; "
            f"{insight_remaining} remaining; "
            f"{character.experience.field_pool} awaiting absorption",
            f"  early refunds {training.early_refunds_remaining}; "
            + (
                "eligible"
                if character.level
                <= self.catalog.progression.early_refund_level_limit
                else "closed by level"
            ),
            "Options:",
        ]
        for option in self.catalog.progression.options.values():
            projection = self._training_option_projection(state, option)
            lines.append(
                f"  {option.id:<10} rank {projection['rank']}/"
                f"{projection['max_rank']}; cost {projection['cost']} "
                f"{projection['pool']}; {projection['reason']}"
            )
        lines.append(
            "Use PLAN <discipline> for an exact non-mutating projection."
        )
        return _HandlerResult(("\n".join(lines),))

    def _resolve_course(
        self,
        query: str,
    ) -> tuple[CourseDefinition | None, str | None]:
        if not query:
            return None, "Name a course."
        scored: list[tuple[int, CourseDefinition]] = []
        for course in self.catalog.courses.values():
            terms = {
                course.id.casefold(),
                course.name.casefold(),
                *(noun.casefold() for noun in course.nouns),
            }
            if query in terms:
                score = 3
            elif any(term.startswith(query) for term in terms):
                score = 2
            elif any(query in term for term in terms):
                score = 1
            else:
                continue
            scored.append((score, course))
        if not scored:
            return None, f"No course matches {query!r}."
        best = max(score for score, _ in scored)
        matches = [course for score, course in scored if score == best]
        if len(matches) > 1:
            return None, (
                "Choose one course: "
                + _natural_list([course.id for course in matches])
                + "."
            )
        return matches[0], None

    def _course_summary(self, state: GameState) -> str:
        progress = state.character.course
        lines = [
            "Optional courses demonstrate existing systems and grant no "
            "faction, military, or professional status."
        ]
        if progress.active_course_id is not None:
            active = self.catalog.courses[progress.active_course_id]
            step = active.steps[progress.step_index]
            lines.extend(
                (
                    f"Active course: {active.name} ({active.id}); step "
                    f"{progress.step_index + 1}/{len(active.steps)}.",
                    f"Next: {step.description}",
                    "Use COURSE ABANDON to discard current progress.",
                )
            )
        else:
            lines.append("Active course: none.")
        lines.append("Course catalog:")
        for course in self.catalog.courses.values():
            status = (
                "completed"
                if course.id in progress.completed_courses
                else "available"
            )
            reward = (
                f"{course.reward_points['physical']} physical and "
                f"{course.reward_points['mental']} mental training points"
            )
            lines.append(
                f"  {course.id:<14} {status}; reward {reward} - "
                f"{course.description}"
            )
        if progress.active_course_id is None:
            lines.append("Use COURSE START <name> at its listed terminal.")
        return "\n".join(lines)

    def _course(
        self, state: GameState, command: ParsedCommand, now: float
    ) -> _HandlerResult:
        if not command.args or command.args[0].casefold() in {"list", "status"}:
            return _HandlerResult((self._course_summary(state),))
        action = command.args[0].casefold()
        progress = state.character.course
        if action == "abandon":
            if len(command.args) != 1:
                return _HandlerResult(("Use COURSE ABANDON without a course name.",))
            if progress.active_course_id is None:
                return _HandlerResult(("You have no active course to abandon.",))
            course = self.catalog.courses[progress.active_course_id]
            progress.active_course_id = None
            progress.step_index = 0
            self._set_roundtime(state, now, 1)
            return _HandlerResult(
                (
                    f"You abandon {course.name}; its incomplete steps are discarded.",
                    "Roundtime: 1 sec.",
                ),
                (
                    DomainEvent(
                        "course.abandoned",
                        {"course_id": course.id},
                    ),
                ),
                True,
            )
        if action != "start":
            return _HandlerResult(
                ("Use COURSE, COURSE START <name>, or COURSE ABANDON.",)
            )
        course, error = self._resolve_course(self._query(command.args[1:]))
        if course is None:
            return _HandlerResult((error or "Start which course?",))
        if progress.active_course_id is not None:
            active = self.catalog.courses[progress.active_course_id]
            return _HandlerResult(
                (
                    f"You are already enrolled in {active.name}. "
                    "Finish it or use COURSE ABANDON first.",
                )
            )
        if course.id in progress.completed_courses:
            return _HandlerResult(
                (
                    f"You have already completed {course.name}; "
                    "its one-time reward cannot be repeated.",
                )
            )
        room = self.catalog.rooms[state.character.room_id]
        if state.character.room_id != course.start_room:
            start = self.catalog.rooms[course.start_room]
            return _HandlerResult(
                (f"Start {course.name} at {start.title}.",)
            )
        if course.facility not in room.facilities:
            return _HandlerResult(
                (f"The required {course.facility.replace('_', ' ')} is unavailable.",)
            )
        if self._live_creatures(state):
            return _HandlerResult(
                ("You cannot enroll while an active opponent is nearby.",)
            )
        progress.active_course_id = course.id
        progress.step_index = 0
        self._set_roundtime(state, now, 2)
        return _HandlerResult(
            (
                f"You open {course.name}.",
                "This optional, decommissioned syllabus grants no military, "
                "faction, or professional status.",
                f"First step: {course.steps[0].description}",
                "Roundtime: 2 sec.",
            ),
            (
                DomainEvent(
                    "course.started",
                    {"course_id": course.id},
                ),
            ),
            True,
        )

    def _apply_course_progress(
        self,
        state: GameState,
        events: tuple[DomainEvent, ...],
    ) -> _HandlerResult:
        progress = state.character.course
        if progress.active_course_id is None:
            return _HandlerResult(())
        course = self.catalog.courses[progress.active_course_id]
        step = course.steps[progress.step_index]
        matched = next(
            (
                event
                for event in events
                if event.kind == step.event_kind
                and all(
                    key in event.payload
                    and type(event.payload[key]) is type(expected)
                    and event.payload[key] == expected
                    for key, expected in step.event_filters.items()
                )
            ),
            None,
        )
        if matched is None:
            return _HandlerResult(())
        completed_step_index = progress.step_index
        progress.step_index += 1
        if progress.step_index < len(course.steps):
            next_step = course.steps[progress.step_index]
            return _HandlerResult(
                (
                    f"[Course] Step {completed_step_index + 1}/"
                    f"{len(course.steps)} verified: {step.description}",
                    f"[Course] Next: {next_step.description}",
                ),
                (
                    DomainEvent(
                        "course.step_completed",
                        {
                            "course_id": course.id,
                            "step_id": step.id,
                            "step_number": completed_step_index + 1,
                        },
                    ),
                ),
                True,
            )
        awarded: dict[str, int] = {}
        training = state.character.training
        for pool in ("physical", "mental"):
            attribute = f"{pool}_points"
            current = int(getattr(training, attribute))
            grant = min(
                int(course.reward_points[pool]),
                100_000_000 - current,
            )
            setattr(training, attribute, current + grant)
            awarded[pool] = grant
        progress.completed_courses.add(course.id)
        progress.active_course_id = None
        progress.step_index = 0
        return _HandlerResult(
            (
                f"[Course] Final step verified: {step.description}",
                f"[Course] {course.name} complete. You receive "
                f"{awarded['physical']} physical and "
                f"{awarded['mental']} mental training points.",
                "The archived syllabus records completion without assigning "
                "military, faction, or professional status.",
            ),
            (
                DomainEvent(
                    "course.completed",
                    {
                        "course_id": course.id,
                        "physical_points": awarded["physical"],
                        "mental_points": awarded["mental"],
                    },
                ),
            ),
            True,
        )

class StoryService(EngineService):
    """Owns the story application boundary."""

    @staticmethod
    def _tutorial_evidence_flag(step_id: str) -> str:
        return f"{_TUTORIAL_EVIDENCE_PREFIX}{step_id}"

    def _clear_tutorial_evidence(self, state: GameState) -> None:
        state.flags.difference_update(
            flag
            for flag in tuple(state.flags)
            if flag.startswith(_TUTORIAL_EVIDENCE_PREFIX)
        )

    @staticmethod
    def _tutorial_event_matches(step, event: DomainEvent) -> bool:
        return event.kind == step.event_kind and all(
            key in event.payload
            and type(event.payload[key]) is type(expected)
            and event.payload[key] == expected
            for key, expected in step.event_filters.items()
        )

    def _record_tutorial_evidence(
        self,
        state: GameState,
        events: tuple[DomainEvent, ...],
    ) -> None:
        """Remember valid Guided Start actions even when performed out of order."""

        if state.character.build.tutorial_status != "active":
            return
        for step in self.catalog.creation.tutorial.steps:
            if any(self._tutorial_event_matches(step, event) for event in events):
                state.flags.add(self._tutorial_evidence_flag(step.id))

    def _tutorial_step_satisfied(self, state: GameState, step) -> bool:
        if self._tutorial_evidence_flag(step.id) in state.flags:
            return True
        # v0.13.0 could strand a player who entered the market before this
        # step became current. Spatial memory is durable evidence that the
        # requested movement already happened, so migration and GUIDE SYNC can
        # repair that save without forcing a south/north backtrack.
        if step.id == "enter_sprawl":
            return "rain_market" in state.visited_rooms
        return False

    def _apply_tutorial_progress(
        self,
        state: GameState,
        events: tuple[DomainEvent, ...],
    ) -> _HandlerResult:
        build = state.character.build
        if build.tutorial_status != "active":
            return _HandlerResult(())
        tutorial = self.catalog.creation.tutorial
        step_by_id = {step.id: step for step in tutorial.steps}
        self._record_tutorial_evidence(state, events)

        lines: list[str] = []
        completed_events: list[DomainEvent] = []
        changed = False
        while build.tutorial_status == "active":
            step = step_by_id.get(build.tutorial_step_id or "")
            if step is None:
                raise ValueError("active tutorial references an unknown step")
            if not self._tutorial_step_satisfied(state, step):
                break
            index = tutorial.steps.index(step)
            state.flags.discard(self._tutorial_evidence_flag(step.id))
            lines.append(f"[Guide] {step.description} verified.")
            completed_events.append(
                DomainEvent(
                    "tutorial.step_completed",
                    {
                        "tutorial_id": tutorial.id,
                        "step_id": step.id,
                        "step_number": index + 1,
                    },
                )
            )
            changed = True
            if index + 1 < len(tutorial.steps):
                next_step = tutorial.steps[index + 1]
                build.tutorial_step_id = next_step.id
                lines.append(f"[Guide] Next: {next_step.description}")
                continue
            build.tutorial_status = "completed"
            build.tutorial_step_id = None
            self._clear_tutorial_evidence(state)
            lines.append(
                "[Guide] Guided Start complete. No reward or build advantage was granted."
            )
            completed_events.append(
                DomainEvent(
                    "tutorial.completed",
                    {"tutorial_id": tutorial.id},
                )
            )
        return _HandlerResult(
            tuple(lines),
            tuple(completed_events),
            changed,
        )

    def _active_story_context(
        self,
        state: GameState,
    ) -> tuple[StoryQuestDefinition, StoryStageDefinition] | None:
        quest_id = state.story.active_quest_id
        stage_id = state.story.active_stage_id
        if quest_id is None or stage_id is None:
            return None
        quest = self.catalog.story.quests[quest_id]
        stage = next(
            stage for stage in quest.stages if stage.id == stage_id
        )
        return quest, stage

    @staticmethod
    def _story_event_matches(
        event: DomainEvent,
        event_kind: str,
        event_filters: dict[str, object] | object,
    ) -> bool:
        if event.kind != event_kind:
            return False
        assert hasattr(event_filters, "items")
        return all(
            key == "fresh_event"
            or (
                key in event.payload
                and type(event.payload[key]) is type(expected)
                and event.payload[key] == expected
            )
            for key, expected in event_filters.items()
        )

    def _story_transition_satisfied(
        self,
        state: GameState,
        transition,
        events: tuple[DomainEvent, ...],
    ) -> bool:
        if any(
            self._story_event_matches(
                event,
                transition.event_kind,
                transition.event_filters,
            )
            for event in events
        ):
            return True

        filters = transition.event_filters
        if filters.get("fresh_event") is True:
            return False
        if transition.event_kind == "room.secret_found":
            reveal_id = filters.get("reveal_id")
            return isinstance(reveal_id, str) and reveal_id in state.revealed
        if transition.event_kind == "combat.target_defeated":
            target_id = filters.get("target")
            return (
                isinstance(target_id, str)
                and target_id in state.defeated_creatures
            )
        if transition.event_kind == "story.dialogue_seen":
            dialogue_id = filters.get("dialogue_id")
            return (
                isinstance(dialogue_id, str)
                and dialogue_id in state.story.seen_dialogues
            )
        if transition.event_kind == "character.moved":
            room_id = filters.get("to")
            return (
                isinstance(room_id, str)
                and state.character.room_id == room_id
            )
        if transition.event_kind == "world.room_discovered":
            room_id = filters.get("room_id")
            return isinstance(room_id, str) and room_id in state.visited_rooms
        if transition.event_kind == "progress.level_at_least":
            level = filters.get("level")
            return isinstance(level, int) and state.character.level >= level
        if transition.event_kind == "equipment.modified":
            return any(item.upgrade_level > 0 for item in state.character.inventory)
        if transition.event_kind == "class.technique_used":
            context = self._active_story_context(state)
            if (
                context is not None
                and context[0].id == "class_field_assignment"
                and context[1].id == "use_class_instinct"
            ):
                # This assignment deliberately requires a fresh technique use
                # after Sol issues the class-specific field order; the earlier
                # foundation rehearsal is not reusable evidence.
                return False
            return "class_technique_used" in state.flags
        if transition.event_kind == "economy.recipe_crafted":
            recipe_id = filters.get("recipe_id")
            return (
                isinstance(recipe_id, str)
                and f"recipe_crafted:{recipe_id}" in state.flags
            )
        if transition.event_kind == "combat.room_cleared":
            room_id = filters.get("room_id")
            return (
                isinstance(room_id, str)
                and room_id in state.visited_rooms
                and not state.creatures.get(room_id, [])
            )
        return False

    def _apply_story_progress(
        self,
        state: GameState,
        events: tuple[DomainEvent, ...],
    ) -> _HandlerResult:
        lines: list[str] = []
        progress_events: list[DomainEvent] = []
        changed = False
        authored_stage_count = sum(
            len(quest.stages) for quest in self.catalog.story.quests.values()
        )
        for _ in range(authored_stage_count + 1):
            context = self._active_story_context(state)
            if context is None:
                break
            quest, stage = context
            matched_transition = next(
                (
                    transition
                    for transition in stage.event_transitions
                    if self._story_transition_satisfied(
                        state, transition, events
                    )
                ),
                None,
            )
            if matched_transition is None:
                break
            state.flags.update(matched_transition.sets_flags)
            self._sync_story_companion_order(state)
            detached_lines, detached_events = self._detach_sol_if_story_requires(state)
            lines.extend(detached_lines)
            progress_events.extend(detached_events)
            for record_id in matched_transition.records:
                if record_id not in state.story.records:
                    state.story.records.add(record_id)
                    progress_events.append(
                        DomainEvent(
                            "story.sovereignty_recorded",
                            {"record_id": record_id, "method": "transition"},
                        )
                    )
            if matched_transition.despawn_creatures:
                for room_creatures in state.creatures.values():
                    for creature in tuple(room_creatures):
                        if creature.instance_id in matched_transition.despawn_creatures:
                            room_creatures.remove(creature)
                            state.defeated_creatures.add(creature.instance_id)
                            if state.target_id == creature.instance_id:
                                state.target_id = None
                            if (
                                state.last_reference_kind == "creature"
                                and state.last_reference_id == creature.instance_id
                            ):
                                self._set_reference(state, None, None)
            state.story.active_quest_id = matched_transition.next_quest_id
            state.story.active_stage_id = matched_transition.next_stage_id
            next_context = self._active_story_context(state)
            assert next_context is not None
            next_quest, next_stage = next_context
            lines.extend(
                (
                    f"[Story] {matched_transition.result_text}",
                    f"[Directive] {next_stage.directive}",
                    f"Objective: {next_stage.objective}",
                )
            )
            progress_events.extend(
                (
                    DomainEvent(
                        "story.stage_completed",
                        {
                            "quest_id": quest.id,
                            "stage_id": stage.id,
                            "method": (
                                "event" if events else "durable_evidence"
                            ),
                        },
                    ),
                    DomainEvent(
                        "story.stage_started",
                        {
                            "quest_id": next_quest.id,
                            "stage_id": next_stage.id,
                        },
                    ),
                )
            )
            changed = True
            events = ()
        else:
            raise ValueError("story progression exceeded authored stage bound")
        return _HandlerResult(tuple(lines), tuple(progress_events), changed)

    @staticmethod
    def _world_cycle_phase(state: GameState) -> str:
        return ("rest", "market", "field", "watch")[state.turn % 4]

    def _effective_npc_room(self, state: GameState, npc: NpcDefinition) -> str:
        context = self._active_story_context(state)
        if context is not None:
            _quest, stage = context
            # Story-critical people stay at their authored meeting place.
            if npc.id in stage.dialogues:
                return npc.room_id
            required_dialogues = {
                action.requires_dialogue_id
                for action in stage.actions
                if action.requires_dialogue_id is not None
            }
            if any(
                dialogue_id in self.catalog.story.dialogues
                and self.catalog.story.dialogues[dialogue_id].npc_id == npc.id
                for dialogue_id in required_dialogues
            ):
                return npc.room_id
        return npc.schedule_rooms.get(self._world_cycle_phase(state), npc.room_id)

    def _story_npcs_in_room(self, state: GameState) -> list[NpcDefinition]:
        return [
            npc
            for npc in self.catalog.story.npcs.values()
            if self._effective_npc_room(state, npc) == state.character.room_id
            and all(flag in state.flags for flag in npc.requires_flags)
            and not any(flag in state.flags for flag in npc.forbidden_flags)
        ]

    def _resolve_npc(
        self,
        state: GameState,
        query: str,
    ) -> tuple[NpcDefinition | None, str | None]:
        candidates = self._story_npcs_in_room(state)
        if not query:
            if len(candidates) == 1:
                return candidates[0], None
            if not candidates:
                return None, "No one here is available to speak with."
            return None, (
                "Talk to whom? "
                + ", ".join(f"TALK {npc.nouns[0].upper()}" for npc in candidates)
                + "."
            )
        scored: list[tuple[int, NpcDefinition]] = []
        for npc in candidates:
            terms = {
                npc.id.casefold(),
                npc.name.casefold(),
                *(noun.casefold() for noun in npc.nouns),
            }
            if query in terms:
                score = 3
            elif any(term.startswith(query) for term in terms):
                score = 2
            elif any(query in term for term in terms):
                score = 1
            else:
                continue
            scored.append((score, npc))
        if not scored:
            choices = ", ".join(
                f"TALK {npc.nouns[0].upper()}" for npc in candidates
            )
            return None, (
                f"No one here matches {query!r}."
                + (f" Available conversations: {choices}." if choices else "")
            )
        best = max(score for score, _ in scored)
        matches = [npc for score, npc in scored if score == best]
        if len(matches) > 1:
            return None, (
                "That could mean "
                + _natural_list(
                    [f"TALK {npc.nouns[0].upper()}" for npc in matches]
                )
                + "."
            )
        return matches[0], None

    def _story_action_variant(
        self,
        state: GameState,
        action: StoryActionDefinition,
    ):
        class_id = state.character.build.class_id
        return action.class_variants.get(class_id or "")

    def _story_action_label(
        self,
        state: GameState,
        action: StoryActionDefinition,
    ) -> tuple[str, str, str]:
        variant = self._story_action_variant(state, action)
        if variant is None:
            return action.label, action.summary, action.result_text
        return variant.label, variant.summary, variant.result_text

    def _story_action_command(
        self,
        action: StoryActionDefinition,
    ) -> str:
        return f"{action.verb} {action.nouns[0]}"

    def _story_inventory_counts(self, state: GameState) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in state.character.inventory:
            counts[item.definition_id] = counts.get(item.definition_id, 0) + 1
        return counts

    def _story_action_availability(
        self,
        state: GameState,
        action: StoryActionDefinition,
    ) -> tuple[bool, str | None]:
        if action.id in state.story.completed_actions:
            return False, "That decision has already been recorded."
        selected_specialization = self._selected_specialization(state)
        if action.id == "open_phase_specialization_choice":
            if selected_specialization is not None:
                return False, (
                    f"{selected_specialization.name} is already learned; affirm "
                    "the current branch instead of opening a silent respec."
                )
        elif action.id == "affirm_current_phase_specialization":
            if selected_specialization is None:
                return False, "No current class specialization exists to affirm."
        elif action.id == "affirm_current_phase_mastery":
            if selected_specialization is None:
                return False, "Learn a class specialization before affirming mastery."
            if self._selected_specialization_upgrade(
                state, selected_specialization
            ) is None:
                return False, (
                    "Complete three successful specialization uses, then choose "
                    "IMPACT or TEMPO with ABILITY UPGRADE."
                )
        if (
            action.requires_dialogue_id is not None
            and action.requires_dialogue_id not in state.story.seen_dialogues
        ):
            dialogue = self.catalog.story.dialogues[
                action.requires_dialogue_id
            ]
            npc = self.catalog.story.npcs[dialogue.npc_id]
            return False, f"Talk to {npc.name} first."
        if (
            action.requires_room_id is not None
            and state.character.room_id != action.requires_room_id
        ):
            room = self.catalog.rooms[action.requires_room_id]
            return False, f"This must be done at {room.title}."
        counts = self._story_inventory_counts(state)
        missing_items: list[str] = []
        required_counts: dict[str, int] = {}
        for item_id in action.requires_items:
            required_counts[item_id] = required_counts.get(item_id, 0) + 1
        for item_id, required in required_counts.items():
            if counts.get(item_id, 0) < required:
                missing_items.append(self.catalog.items[item_id].name)
        if missing_items:
            return False, "Required: " + _natural_list(missing_items) + "."
        missing_flags = [
            flag for flag in action.requires_flags if flag not in state.flags
        ]
        if missing_flags:
            return False, "More evidence is needed before that choice is available."
        missing_records = [
            record_id
            for record_id in action.requires_records
            if record_id not in state.story.records
        ]
        if missing_records:
            return False, "A prior sovereignty decision is still unresolved."
        return True, None

    @staticmethod
    def _checkpoint_label(checkpoint_id: str | None) -> str:
        labels = {
            "first_watch_complete": "First Watch",
            "lines_in_the_rain_complete": "Lines in the Rain",
            "wrong_pattern_complete": "The Wrong Pattern",
            "foundation_ready_complete": "Foundation Ready",
            "second_life_complete": "The Price of a Second Life",
            "regional_path_open": "The Road to Sovereignty",
            "first_contact_complete": "First Contact",
            "regional_expedition_complete": "Regional Expedition",
            "headquarters_approach_complete": "Faction Candidacy Threshold",
            "unowned_caravan_complete": "The First Unowned Caravan",
            "medicine_road_complete": "The Medicine Must Arrive",
            "relief_detail_complete": "One Report, Many Lives",
            "report_reliability_complete": "The Report That Arrived Twice",
            "class_lens_complete": "Fifteen Lenses, One Truth",
            "district22_public_access_complete": "The Road That Changes Meaning",
            "shaklas_queue_memory_complete": "The Public Queue Remembers",
            "shaklas_threshold_cost_complete": "The Threshold Has a Cost",
            "shaklas_borrowed_light_complete": "The Light Is Borrowed",
            "shaklas_gift_terms_complete": "The Name on the Gift",
            "shaklas_receipt_scope_complete": "The Receipt Travels Without You",
            "shaklas_appeal_complete": "The Appeal Is Not a Verdict",
            "journey_phase_discipline_complete": "The Discipline You Carry",
            "journey_partner_synchrony_complete": "The Partner in the Present",
            "second_horizon_complete": "The Second Horizon",
        }
        if checkpoint_id is None:
            return "The opening arc"
        return labels.get(
            checkpoint_id,
            checkpoint_id.removesuffix("_complete").replace("_", " ").title(),
        )

    @staticmethod
    def _candidacy_status(state: GameState, faction_id: str) -> str:
        if f"faction_candidate:{faction_id}" in state.flags:
            return "accepted"
        if f"candidacy_deferred:{faction_id}" in state.flags:
            return "deferred"
        if f"candidacy_declined:{faction_id}" in state.flags:
            return "declined"
        if f"candidacy_offered:{faction_id}" in state.flags:
            return "offered"
        return "not_offered"

    def _route_interest_projection(
        self,
        state: GameState,
    ) -> dict[str, object] | None:
        for faction_id, faction in self.catalog.creation.factions.items():
            interested = f"route_interest:{faction_id}" in state.flags
            selected = f"route_selected:{faction_id}" in state.flags
            if interested or selected:
                handoff = f"hq_handoff:{faction.id}" in state.flags
                threshold_open = f"headquarters_approach:{faction.id}" in state.flags
                candidacy = self._candidacy_status(state, faction.id)
                status = (
                    f"candidacy_{candidacy}"
                    if candidacy != "not_offered"
                    else "threshold_open"
                    if threshold_open
                    else "handoff_ready"
                    if handoff
                    else "contact_selected"
                    if selected
                    else "noticed"
                )
                return {
                    "faction_id": faction.id,
                    "faction_name": faction.name,
                    "route_label": faction.route_label,
                    "status": status,
                    "candidacy_status": candidacy,
                    "membership_status": "unaffiliated",
                    "rank_status": "none",
                    "guild_eligibility": "locked_until_required_faction_quests",
                    "freeform_guild_path": "locked_until_all_faction_quests_and_special_access_quest",
                    "handoff_ready": handoff,
                    "threshold_open": threshold_open,
                }
        return None

    def _resolve_story_action(
        self,
        state: GameState,
        verb: str,
        query: str,
    ) -> tuple[StoryActionDefinition | None, str | None]:
        context = self._active_story_context(state)
        if context is None:
            checkpoint = self._checkpoint_label(state.story.checkpoint_id)
            return None, (
                f"{checkpoint} has reached its checkpoint; no opening decision is active."
            )
        _, stage = context
        candidates = [action for action in stage.actions if action.verb == verb]
        if not candidates:
            return None, (
                f"There is no {verb.upper()} action in the current objective. "
                "Use NEXT for one exact step or HELP HERE for current commands."
            )
        if not query:
            choices = [
                self._story_action_command(action).upper()
                for action in candidates
            ]
            return None, (
                f"{verb.title()} which option? "
                + _natural_list(choices)
                + "."
            )
        scored: list[tuple[int, StoryActionDefinition]] = []
        normalized = query.casefold().replace("_", " ")
        for action in candidates:
            label, _, _ = self._story_action_label(state, action)
            terms = {
                action.id.casefold(),
                action.id.casefold().replace("_", " "),
                action.label.casefold(),
                label.casefold(),
                *(noun.casefold() for noun in action.nouns),
            }
            if normalized in terms:
                score = 3
            elif any(term.startswith(normalized) for term in terms):
                score = 2
            elif any(normalized in term for term in terms):
                score = 1
            else:
                continue
            scored.append((score, action))
        if not scored:
            choices = ", ".join(
                self._story_action_command(action).upper() for action in candidates
            )
            return None, (
                f"No current {verb.upper()} option matches {query!r}. "
                f"Available: {choices}."
            )
        best = max(score for score, _ in scored)
        matches = [action for score, action in scored if score == best]
        if len(matches) > 1:
            return None, (
                "That could mean "
                + _natural_list(
                    [self._story_action_label(state, action)[0] for action in matches]
                )
                + "."
            )
        return matches[0], None

    def _talk(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        npc, error = self._resolve_npc(state, self._query(command.args))
        if npc is None:
            return _HandlerResult((error or "Talk to whom?",))
        context = self._active_story_context(state)
        if context is None:
            score = state.story.relationships.get(npc.id, 0)
            ambient = npc.ambient_text or (
                f"{npc.name} acknowledges you while {self._checkpoint_label(state.story.checkpoint_id)} holds."
            )
            return _HandlerResult(
                (
                    ambient,
                    f"{npc.relationship_label}: {score:+d} ({self._relationship_descriptor(score)}).",
                )
            )
        _, stage = context
        dialogue_id = stage.dialogues.get(npc.id)
        if dialogue_id is None:
            score = state.story.relationships.get(npc.id, 0)
            ambient = npc.ambient_text or f"{npc.name} watches the current situation without interrupting it."
            return _HandlerResult(
                (
                    ambient,
                    f"{npc.relationship_label}: {score:+d} ({self._relationship_descriptor(score)}).",
                    f"Current directive: {stage.directive}",
                )
            )
        dialogue = self.catalog.story.dialogues[dialogue_id]
        first_read = dialogue.id not in state.story.seen_dialogues
        if first_read:
            state.story.seen_dialogues.add(dialogue.id)
        lines = [f"[{npc.name} — {dialogue.title}]", dialogue.text]
        available_choices = [
            action
            for action in stage.actions
            if action.id in dialogue.choice_ids
        ]
        if available_choices:
            lines.append("Your response:")
            for action in available_choices:
                label, summary, _ = self._story_action_label(state, action)
                lines.append(
                    f"  {self._story_action_command(action).upper():<24} "
                    f"{label} — {summary}"
                )
        else:
            lines.append(f"Objective: {stage.objective}")
        events = (
            (
                DomainEvent(
                    "story.dialogue_seen",
                    {"dialogue_id": dialogue.id, "npc_id": npc.id},
                ),
            )
            if first_read
            else ()
        )
        return _HandlerResult(tuple(lines), events, first_read)

    @staticmethod
    def _relationship_descriptor(score: int) -> str:
        if score <= -3:
            return "strained"
        if score == -2:
            return "wary"
        if score == -1:
            return "guarded"
        if score == 0:
            return "uncertain"
        if score == 1:
            return "attentive"
        if score == 2:
            return "trusting"
        return "aligned"

    def _quest(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        query = self._query(command.args)
        if query in {"record", "records", "sovereignty", "decisions"}:
            if not state.story.records:
                return _HandlerResult(
                    ("No sovereignty decisions have been recorded yet.",)
                )
            lines = ["Sovereignty record:"]
            for record_id in sorted(
                state.story.records,
                key=lambda value: self.catalog.story.records[value].label,
            ):
                record = self.catalog.story.records[record_id]
                lines.append(f"  {record.label}: {record.description}")
            return _HandlerResult(("\n".join(lines),))
        if query in {"relationship", "relationships", "people", "npcs"}:
            lines = ["People remember:"]
            for npc_id, score in sorted(state.story.relationships.items()):
                npc = self.catalog.story.npcs[npc_id]
                lines.append(
                    f"  {npc.relationship_label} with {npc.name}: {score:+d} "
                    f"({self._relationship_descriptor(score)})"
                )
            return _HandlerResult(("\n".join(lines),))
        context = self._active_story_context(state)
        if context is None:
            checkpoint = self._checkpoint_label(state.story.checkpoint_id)
            completed = [
                self.catalog.story.quests[quest_id].title
                for quest_id in sorted(state.story.completed_quests)
            ]
            return _HandlerResult(
                (
                    f"{checkpoint} checkpoint reached.",
                    f"Checkpoint: {checkpoint}.",
                    "Completed: " + _natural_list(completed) + ".",
                    "Use QUEST RECORDS or QUEST RELATIONSHIPS to review what the Sprawl remembers.",
                )
            )
        quest, stage = context
        actions = [
            self._story_action_label(state, action)[0]
            for action in stage.actions
        ]
        lines = [
            f"[{quest.title}] {stage.progress_index}/{stage.progress_total}",
            quest.summary,
            f"Directive: {stage.directive}",
            f"Objective: {stage.objective}",
            f"Location: {stage.room_hint}",
        ]
        contacts = self._active_stage_contacts(state, stage)
        if contacts:
            contact_lines = []
            for contact in contacts:
                if contact["known"]:
                    contact_lines.append(
                        f"{contact['name']} at {contact['room_title']} "
                        f"({str(contact['route_command']).upper()})"
                    )
                else:
                    contact_lines.append(f"{contact['name']} at an unmapped location")
            lines.append("Available contacts: " + _natural_list(contact_lines) + ".")
        if actions:
            lines.append("Current choices: " + _natural_list(actions) + ".")
        lines.append(
            "Use QUEST RECORDS or QUEST RELATIONSHIPS to review lasting consequences."
        )
        return _HandlerResult(tuple(lines))

    def _choose(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        return self._execute_story_verb(state, command, now, verb="choose")

    def _interact(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
    ) -> _HandlerResult:
        handled = self._execute_story_verb(
            state,
            command,
            now,
            verb="interact",
        )
        if not handled.changed:
            return handled
        self._set_roundtime(state, now, 3)
        return _HandlerResult(
            handled.lines + ("Roundtime: 3 sec.",),
            handled.events,
            True,
        )

    def _execute_story_verb(
        self,
        state: GameState,
        command: ParsedCommand,
        now: float,
        *,
        verb: str,
    ) -> _HandlerResult:
        action, error = self._resolve_story_action(
            state,
            verb,
            self._query(command.args),
        )
        if action is None:
            return _HandlerResult((error or f"{verb.title()} what?",))
        if self._live_creatures(state) and not action.allow_under_pressure:
            return _HandlerResult(
                (
                    "Hostile pressure makes that story action unsafe. Defeat the threat or withdraw first.",
                )
            )
        available, reason = self._story_action_availability(state, action)
        if not available:
            return _HandlerResult((reason or "That choice is not available.",))
        return self._apply_story_action(state, action, now)

    def _remove_story_item(self, state: GameState, item_id: str) -> ItemState:
        item = next(
            item
            for item in state.character.inventory
            if item.definition_id == item_id
        )
        return self._remove_inventory_item(state, item)

    def _apply_story_action(
        self,
        state: GameState,
        action: StoryActionDefinition,
        now: float,
    ) -> _HandlerResult:
        context = self._active_story_context(state)
        if context is None:
            return _HandlerResult(("No opening story action is active.",))
        quest, stage = context
        label, _, result_text = self._story_action_label(state, action)
        consumed_names: list[str] = []
        for item_id in action.consumes_items:
            self._remove_story_item(state, item_id)
            consumed_names.append(self.catalog.items[item_id].name)
        state.story.completed_actions.add(action.id)
        state.flags.update(action.sets_flags)
        state.flags.difference_update(action.clears_flags)
        self._sync_story_companion_order(state)

        special_lines: list[str] = []
        if action.id == "prepare_phase_discipline_frame":
            plate = next(
                (
                    item
                    for item in state.character.inventory
                    if item.definition_id == "phase_practice_plate"
                ),
                None,
            )
            if plate is None:
                plate = self._new_item_state(
                    "journey:phase-practice-plate",
                    "phase_practice_plate",
                )
                state.character.inventory.append(plate)
            plate.durability = min(int(plate.durability or 1), 8)
            room_creatures = state.creatures.setdefault(
                "phase_discipline_lab", []
            )
            frame_id = "journey:phase-discipline-frame"
            if not any(
                creature.instance_id == frame_id
                for creature in room_creatures
            ):
                definition = self.catalog.creatures["phase_discipline_frame"]
                room_creatures.append(
                    CreatureState(
                        frame_id,
                        "phase_discipline_frame",
                        definition.max_health,
                    )
                )
            state.defeated_creatures.discard(frame_id)
            special_lines.append(
                "[Practice support] A scuffed repair plate and non-sentient "
                "discipline frame are ready for every branch kind."
            )
        elif action.id == "release_echo_formation_splitter":
            room_creatures = state.creatures.setdefault(
                "present_tense_overlook", []
            )
            splitter_id = "journey:echo-formation-splitter"
            if not any(
                creature.instance_id == splitter_id
                for creature in room_creatures
            ):
                definition = self.catalog.creatures[
                    "echo_formation_splitter"
                ]
                room_creatures.append(
                    CreatureState(
                        splitter_id,
                        "echo_formation_splitter",
                        definition.max_health,
                    )
                )
            state.defeated_creatures.discard(splitter_id)
            special_lines.append(
                "[Formation test] The echo splitter activates with no XP, "
                "credits, ownership, or persistent traveler record."
            )
        elif action.id == "release_echo_route_pursuit_frame":
            room_creatures = state.creatures.setdefault(
                "echo_cohort_crossing", []
            )
            frame_id = "journey:echo-route-pursuit-frame"
            if not any(
                creature.instance_id == frame_id
                for creature in room_creatures
            ):
                definition = self.catalog.creatures[
                    "echo_route_pursuit_frame"
                ]
                room_creatures.append(
                    CreatureState(
                        frame_id,
                        "echo_route_pursuit_frame",
                        definition.max_health,
                    )
                )
            state.defeated_creatures.discard(frame_id)
            if "field_cohort_formation_defensive" in state.flags:
                guard_added = 24
                formation_note = "Defensive prepares 24 guard before the first exchange."
            elif "field_cohort_formation_offensive" in state.flags:
                guard_added = 4
                formation_note = "Offensive prepares 4 guard and shifts its advantage into attack pressure."
            else:
                guard_added = 12
                formation_note = "Balanced prepares 12 guard and mixed attack support."
            state.character.guard_points = min(
                1000, state.character.guard_points + guard_added
            )
            special_lines.append(
                "[Field test] The non-sentient pursuit frame activates with no XP, "
                "credits, traveler identity, ownership, or continuing route record. "
                + formation_note
            )

        events: list[DomainEvent] = [
            DomainEvent(
                "story.action_completed",
                {
                    "quest_id": quest.id,
                    "stage_id": stage.id,
                    "action_id": action.id,
                    "approach": action.approach,
                },
            )
        ]
        lines = [f"[{label}] {result_text}"]
        lines.extend(special_lines)
        detached_lines, detached_events = self._detach_sol_if_story_requires(state)
        lines.extend(detached_lines)
        events.extend(detached_events)
        if consumed_names:
            lines.append("Used: " + _natural_list(consumed_names) + ".")

        new_records: list[str] = []
        for record_id in action.records:
            if record_id not in state.story.records:
                state.story.records.add(record_id)
                new_records.append(record_id)
                events.append(
                    DomainEvent(
                        "story.sovereignty_recorded",
                        {"record_id": record_id, "action_id": action.id},
                    )
                )
        if new_records:
            labels = [
                self.catalog.story.records[record_id].label
                for record_id in new_records
            ]
            lines.append("[Sovereignty] " + _natural_list(labels) + ".")

        for npc_id, change in action.relationship_changes.items():
            before = state.story.relationships.get(npc_id, 0)
            after = max(-100, min(100, before + change))
            state.story.relationships[npc_id] = after
            npc = self.catalog.story.npcs[npc_id]
            lines.append(
                f"[{npc.relationship_label}] {npc.name}: {before:+d} → {after:+d} "
                f"({self._relationship_descriptor(after)})."
            )
            events.append(
                DomainEvent(
                    "story.relationship_changed",
                    {
                        "npc_id": npc_id,
                        "before": before,
                        "after": after,
                        "change": change,
                    },
                )
            )

        if action.reward_id is not None:
            reward = self.catalog.story.rewards[action.reward_id]
            if reward.id not in state.story.claimed_rewards:
                state.story.claimed_rewards.add(reward.id)
                award_field_insight(
                    state.character.experience,
                    reward.field_insight,
                    now,
                )
                state.character.training.physical_points = min(
                    100_000_000,
                    state.character.training.physical_points
                    + reward.physical_points,
                )
                state.character.training.mental_points = min(
                    100_000_000,
                    state.character.training.mental_points
                    + reward.mental_points,
                )
                awarded_items: list[str] = []
                for item_id in reward.items:
                    item = self._spawn_item(state, item_id)
                    state.character.inventory.append(item)
                    awarded_items.append(self.catalog.items[item_id].name)
                reward_parts: list[str] = []
                if reward.credits:
                    before_credits = state.character.credits
                    state.character.credits = min(
                        100_000_000, state.character.credits + reward.credits
                    )
                    reward_parts.append(f"{reward.credits} credits")
                    events.append(
                        DomainEvent(
                            "economy.credits_changed",
                            {
                                "before": before_credits,
                                "after": state.character.credits,
                                "change": reward.credits,
                                "reason": f"story_reward:{reward.id}",
                            },
                        )
                    )
                if reward.grants_ability_point:
                    state.flags.add("ability_point_available")
                    reward_parts.append("one class specialization point")
                if reward.field_insight:
                    reward_parts.append(f"{reward.field_insight} field insight")
                if reward.physical_points:
                    reward_parts.append(
                        f"{reward.physical_points} physical training point"
                        + ("s" if reward.physical_points != 1 else "")
                    )
                if reward.mental_points:
                    reward_parts.append(
                        f"{reward.mental_points} mental training point"
                        + ("s" if reward.mental_points != 1 else "")
                    )
                reward_parts.extend(awarded_items)
                lines.append(
                    f"[Reward] {reward.title}: "
                    + (_natural_list(reward_parts) if reward_parts else "recorded")
                    + "."
                )
                events.append(
                    DomainEvent(
                        "story.reward_awarded",
                        {
                            "reward_id": reward.id,
                            "field_insight": reward.field_insight,
                            "physical_points": reward.physical_points,
                            "mental_points": reward.mental_points,
                            "credits": reward.credits,
                            "grants_ability_point": reward.grants_ability_point,
                            "items": list(reward.items),
                        },
                    )
                )
                companion_lines, companion_events = self._award_companion_experience(
                    state,
                    reward.field_insight,
                    now,
                    reason=f"story reward {reward.title}",
                )
                lines.extend(companion_lines)
                events.extend(companion_events)

        if action.route_interest:
            selected_class = self.catalog.creation.classes.get(
                state.character.build.class_id or ""
            )
            if selected_class is not None:
                faction = self.catalog.creation.factions[
                    selected_class.faction_id
                ]
                interest_flag = f"route_interest:{faction.id}"
                first_interest = interest_flag not in state.flags
                state.flags.add(interest_flag)
                lines.append(
                    f"[Provisional interest] {faction.name} has noticed how you handled the Sprawl. "
                    "This is observation, not membership or reputation."
                )
                if first_interest:
                    events.append(
                        DomainEvent(
                            "story.route_interest_recorded",
                            {
                                "faction_id": faction.id,
                                "route_label": faction.route_label,
                                "action_id": action.id,
                            },
                        )
                    )

        if action.route_handoff:
            selected_class = self.catalog.creation.classes.get(
                state.character.build.class_id or ""
            )
            if selected_class is not None:
                faction = self.catalog.creation.factions[selected_class.faction_id]
                handoff_flag = f"hq_handoff:{faction.id}"
                interest_flag = f"route_interest:{faction.id}"
                first_handoff = handoff_flag not in state.flags
                state.flags.add(interest_flag)
                state.flags.add(handoff_flag)
                state.flags.add("route_handoff_ready")
                lines.append(
                    f"[Road to sovereignty] Clave marks a route toward {faction.route_label}. "
                    "This opens contact, not faction membership."
                )
                if first_handoff:
                    events.append(
                        DomainEvent(
                            "story.route_handoff_recorded",
                            {
                                "faction_id": faction.id,
                                "route_label": faction.route_label,
                                "action_id": action.id,
                            },
                        )
                    )

        if action.complete_quest:
            state.story.completed_quests.add(quest.id)
            events.append(
                DomainEvent(
                    "story.quest_completed",
                    {"quest_id": quest.id, "action_id": action.id},
                )
            )
            lines.append(f"[Quest complete] {quest.title}.")
            checkpoint_lines, checkpoint_events = self._settle_beginner_checkpoint(
                state, quest.id, now
            )
            lines.extend(checkpoint_lines)
            events.extend(checkpoint_events)

        if action.next_quest_id is not None:
            state.story.active_quest_id = action.next_quest_id
            state.story.active_stage_id = action.next_stage_id
            next_context = self._active_story_context(state)
            assert next_context is not None
            next_quest, next_stage = next_context
            lines.extend(
                (
                    f"[New directive] {next_stage.directive}",
                    f"Objective: {next_stage.objective}",
                )
            )
            events.append(
                DomainEvent(
                    "story.stage_started",
                    {
                        "quest_id": next_quest.id,
                        "stage_id": next_stage.id,
                    },
                )
            )
        elif action.complete_quest:
            state.story.active_quest_id = None
            state.story.active_stage_id = None

        if action.checkpoint_id is not None:
            state.story.checkpoint_id = action.checkpoint_id
            checkpoint_label = self._checkpoint_label(action.checkpoint_id)
            lines.extend(
                (
                    f"[Checkpoint] {checkpoint_label} complete.",
                    "You are safe to end the session. The Sprawl and its people retain every recorded consequence.",
                )
            )
            events.append(
                DomainEvent(
                    "story.checkpoint_reached",
                    {"checkpoint_id": action.checkpoint_id},
                )
            )
        return _HandlerResult(tuple(lines), tuple(events), True)

    def _story_shortest_step(
        self,
        state: GameState,
        origin: str,
        destination: str,
    ) -> tuple[str, str] | None:
        if origin == destination:
            return None
        queue: deque[tuple[str, tuple[tuple[str, str], ...]]] = deque(
            [(origin, ())]
        )
        visited = {origin}
        while queue:
            room_id, path = queue.popleft()
            for direction, next_room_id in self._available_exits(state, room_id):
                if next_room_id in visited:
                    continue
                next_path = path + ((direction, next_room_id),)
                if next_room_id == destination:
                    return next_path[0]
                visited.add(next_room_id)
                queue.append((next_room_id, next_path))
        return None

    def _story_route_command(
        self,
        state: GameState,
        destination: str | None,
    ) -> str | None:
        if destination is None or state.character.room_id == destination:
            return None
        step = self._story_shortest_step(
            state,
            state.character.room_id,
            destination,
        )
        if step is None:
            return None
        movement = "withdraw" if self._live_creatures(state) else "go"
        return f"{movement} {step[0]}"

    def _story_live_pressure_command(
        self,
        state: GameState,
        stage: StoryStageDefinition,
    ) -> str | None:
        live = self._live_creatures(state)
        if not live:
            return None
        # Preserve deliberately authored noncombat pressure options.  These are
        # explicit player choices, not a generic bypass of combat safety.
        for action in stage.actions:
            if not action.allow_under_pressure:
                continue
            available, _ = self._story_action_availability(state, action)
            if available:
                return self._story_action_command(action)

        # When the authored objective is to leave a contested room, recommend
        # the executable WITHDRAW step rather than forcing an unnecessary kill.
        # This preserves player agency and the existing escape/tactical loop.
        for destination in (
            stage.target_room_id,
            self._story_transition_destination(stage),
        ):
            routed = self._story_route_command(state, destination)
            if routed is not None:
                return routed
        target = next(
            (
                creature
                for creature in live
                if creature.instance_id == state.target_id
            ),
            live[0],
        )
        definition = self.catalog.creatures[target.definition_id]
        noun = definition.nouns[0] if definition.nouns else definition.name
        return f"attack {noun}"

    def _story_transition_destination(
        self,
        stage: StoryStageDefinition,
    ) -> str | None:
        destinations = [
            transition.event_filters.get("to")
            for transition in stage.event_transitions
            if transition.event_kind == "character.moved"
        ]
        valid = [value for value in destinations if isinstance(value, str)]
        return valid[0] if len(set(valid)) == 1 and valid else None

    def _story_required_item_ids(
        self,
        state: GameState,
        stage: StoryStageDefinition,
    ) -> tuple[str, ...]:
        counts = self._story_inventory_counts(state)
        required: list[str] = []
        for transition in stage.event_transitions:
            if transition.event_kind != "item.taken":
                continue
            item_id = transition.event_filters.get("item_id")
            if isinstance(item_id, str) and counts.get(item_id, 0) < 1:
                required.append(item_id)
        for action in stage.actions:
            for item_id in action.requires_items:
                if counts.get(item_id, 0) < 1 and item_id not in required:
                    required.append(item_id)
        return tuple(required)

    def _story_item_acquisition_command(
        self,
        state: GameState,
        item_ids: tuple[str, ...],
    ) -> str | None:
        if not item_ids:
            return None
        current_room_items = state.room_items.get(state.character.room_id, [])
        for item_id in item_ids:
            if any(item.definition_id == item_id for item in current_room_items):
                return f"get {self.catalog.items[item_id].name}"

        for vendor in self._vendors_here(state):
            for item_id in item_ids:
                if item_id in vendor.inventory:
                    return f"market buy {self.catalog.items[item_id].name}"

        source_rooms: list[tuple[str, str]] = []
        for item_id in item_ids:
            for room_id, room_items in state.room_items.items():
                if any(item.definition_id == item_id for item in room_items):
                    source_rooms.append((item_id, room_id))
            for vendor in self.catalog.economy.vendors.values():
                if item_id in vendor.inventory:
                    source_rooms.append((item_id, vendor.room_id))
        for _item_id, room_id in source_rooms:
            command = self._story_route_command(state, room_id)
            if command is not None:
                return command
        return None

    def _story_recipe_guidance_command(
        self,
        state: GameState,
        stage: StoryStageDefinition,
    ) -> str | None:
        recipe_id = next(
            (
                transition.event_filters.get("recipe_id")
                for transition in stage.event_transitions
                if transition.event_kind == "economy.recipe_crafted"
                and isinstance(transition.event_filters.get("recipe_id"), str)
            ),
            None,
        )
        recipe = (
            self.catalog.economy.recipes.get(recipe_id)
            if isinstance(recipe_id, str)
            else None
        )
        if recipe is None and stage.suggested_command.casefold().startswith("craft "):
            recipe = self._resolve_recipe(stage.suggested_command[6:].strip())
        if recipe is None:
            return None

        counts = self._story_inventory_counts(state)
        required = dict(recipe.inputs)
        if "specialization_craft_discount" in state.flags and required:
            first = next(iter(required))
            required[first] = max(0, required[first] - 1)
        missing = tuple(
            item_id
            for item_id, count in required.items()
            if counts.get(item_id, 0) < count
        )
        acquisition = self._story_item_acquisition_command(state, missing)
        if acquisition is not None:
            return acquisition
        if missing:
            return "inventory"

        room = self.catalog.rooms[state.character.room_id]
        if recipe.facility not in room.facilities:
            preferred = stage.target_room_id
            if (
                preferred is not None
                and recipe.facility in self.catalog.rooms[preferred].facilities
            ):
                routed = self._story_route_command(state, preferred)
                if routed is not None:
                    return routed
            for candidate in self.catalog.rooms.values():
                if recipe.facility in candidate.facilities:
                    routed = self._story_route_command(state, candidate.id)
                    if routed is not None:
                        return routed
        if state.character.credits < recipe.credit_cost:
            return "market"
        return f"craft {recipe.name}"

    def _story_contact_route_command(
        self,
        state: GameState,
        stage: StoryStageDefinition,
    ) -> str | None:
        dialogue_ids = set(stage.dialogues.values())
        dialogue_ids.update(
            action.requires_dialogue_id
            for action in stage.actions
            if action.requires_dialogue_id is not None
        )
        for dialogue_id in sorted(dialogue_ids):
            if dialogue_id in state.story.seen_dialogues:
                continue
            dialogue = self.catalog.story.dialogues.get(dialogue_id)
            if dialogue is None:
                continue
            npc = self.catalog.story.npcs[dialogue.npc_id]
            room_id = self._effective_npc_room(state, npc)
            if room_id == state.character.room_id:
                noun = npc.nouns[0] if npc.nouns else npc.name
                return f"talk {noun}"
            routed = self._story_route_command(state, room_id)
            if routed is not None:
                return routed
        return None

    def _story_primary_command(
        self,
        state: GameState,
        stage: StoryStageDefinition,
    ) -> str:
        context = self._active_story_context(state)
        active_quest_id = context[0].id if context is not None else None
        if active_quest_id == "the_discipline_you_carry":
            selected = self._selected_specialization(state)
            if stage.id == "learn_specialization":
                class_definition = self.catalog.creation.classes.get(
                    state.character.build.class_id or ""
                )
                if class_definition is None:
                    return "build status"
                first_branch = next(iter(class_definition.ability_branches.values()))
                return f"ability learn {first_branch.id}"
            if stage.id.startswith("practice_primary_"):
                if state.character.room_id != "phase_discipline_lab":
                    routed = self._story_route_command(
                        state, "phase_discipline_lab"
                    )
                    if routed is not None:
                        return routed
                if selected is None:
                    return "ability status"
                if selected.kind in {"attack", "precision", "report"}:
                    return "ability use discipline frame"
                if selected.kind == "escape":
                    return (
                        "ability use south"
                        if state.character.room_id == "phase_discipline_lab"
                        else "ability use north"
                    )
                return "ability use"
            if stage.id.startswith("practice_followup_"):
                if selected is None:
                    return "ability status"
                if selected.follow_up.kind in {
                    "attack",
                    "precision",
                    "report",
                }:
                    return "ability followup discipline frame"
                return "ability followup"
            if stage.id == "select_mastery":
                if selected is None:
                    return "ability status"
                if self._selected_specialization_upgrade(state, selected) is not None:
                    return "choose current mastery"
                return "ability upgrade impact"

        if stage.id in {"use_technique", "use_class_instinct"}:
            class_id = state.character.build.class_id or ""
            class_definition = self.catalog.creation.classes.get(class_id)
            if class_definition is not None:
                if class_definition.technique_kind == "escape":
                    available = self._available_exits(state, state.character.room_id)
                    if available:
                        return f"technique {available[0][0]}"
                return "technique self"

        pressure = self._story_live_pressure_command(state, stage)
        if pressure is not None:
            return pressure

        recipe = self._story_recipe_guidance_command(state, stage)
        if recipe is not None:
            return recipe

        # An action that is executable in the current state takes precedence
        # over a broad stage location hint.  This prevents NEXT/HUD Focus from
        # walking away from an available choice such as CHOOSE INSTINCT.
        for action in stage.actions:
            available, _ = self._story_action_availability(state, action)
            if available:
                return self._story_action_command(action)

        # Required visible or purchasable evidence comes before generic travel.
        acquisition = self._story_item_acquisition_command(
            state,
            self._story_required_item_ids(state, stage),
        )
        if acquisition is not None:
            return acquisition

        # Route to the exact room required by a blocked action before using the
        # stage's broader target-room hint.
        for action in stage.actions:
            if action.requires_room_id is None:
                continue
            routed = self._story_route_command(state, action.requires_room_id)
            if routed is not None:
                return routed

        # Route to the authored stage location first, then follow the actual
        # character.moved destination when arrival and advancement are distinct.
        routed = self._story_route_command(state, stage.target_room_id)
        if routed is not None:
            return routed
        routed = self._story_route_command(
            state,
            self._story_transition_destination(stage),
        )
        if routed is not None:
            return routed

        contact = self._story_contact_route_command(state, stage)
        if contact is not None:
            return contact

        if stage.id == "modify_gear":
            coat = next(
                (
                    item
                    for item in state.character.inventory
                    if item.definition_id == "field_coat"
                ),
                None,
            )
            if (
                coat is not None
                and coat.instance_id in state.character.equipped.values()
            ):
                return "unequip coat"
            if "repair_bench" not in self.catalog.rooms[state.character.room_id].facilities:
                for room in self.catalog.rooms.values():
                    if "repair_bench" in room.facilities:
                        routed = self._story_route_command(state, room.id)
                        if routed is not None:
                            return routed
            return "modify field coat"

        # A secret-finding transition needs SEARCH, not a read-only LOOK loop.
        if any(
            transition.event_kind == "room.secret_found"
            for transition in stage.event_transitions
        ):
            return "search"

        suggested = stage.suggested_command.strip().casefold()
        words = suggested.split()
        if words and words[0] in {"go", "withdraw"} and len(words) >= 2:
            available = {direction for direction, _ in self._available_exits(state, state.character.room_id)}
            if words[1] not in available:
                transition_room = self._story_transition_destination(stage)
                routed = self._story_route_command(state, transition_room)
                if routed is not None:
                    return routed
                return "exits"
        if suggested == "look" and stage.event_transitions:
            return "search"
        return stage.suggested_command

    def _story_readiness_projection(
        self,
        state: GameState,
    ) -> dict[str, object]:
        checks = (
            (
                "wrong_date",
                "Wrong date",
                "The Collector recorded your reconstruction before Sol's claimed discovery.",
                "collector_wrong_date_found" in state.flags,
            ),
            (
                "pre_intake_marker",
                "Pre-intake marker",
                "A route tag selected your response profile before you awakened.",
                "subject_marker_decoded" in state.flags,
            ),
            (
                "staged_trial",
                "Staged trial",
                "The signal-yard threat was built to measure how you choose under pressure.",
                "trial_was_staged" in state.flags,
            ),
            (
                "witness_plan",
                "Witness plan",
                "A trusted account can survive if you disappear.",
                "witness_plan_ready" in state.flags,
            ),
            (
                "field_cache",
                "Field cache",
                "Recovery and repair supplies are stored outside the Collector's control.",
                "survival_cache_ready" in state.flags,
            ),
            (
                "second_exit",
                "Second exit",
                "A route back to the market bypasses the measured trial lane.",
                "escape_route_confirmed" in state.flags,
            ),
        )
        items = [
            {
                "id": identifier,
                "label": label,
                "detail": detail,
                "complete": complete,
            }
            for identifier, label, detail, complete in checks
        ]
        completed = sum(bool(item["complete"]) for item in items)
        total = len(items)
        return {
            "title": "Unknown confrontation",
            "completed": completed,
            "total": total,
            "percent": round(completed / total * 100) if total else 0,
            "ready": completed == total,
            "summary": (
                "Evidence, witnesses, supplies, and an escape route are ready."
                if completed == total
                else f"{completed} of {total} preparations secured."
            ),
            "items": items,
        }

    def _sprawl_pulse_projection(self, state: GameState) -> dict[str, object]:
        def status(options: tuple[tuple[str, str], ...], fallback: str) -> str:
            return next((label for flag, label in options if flag in state.flags), fallback)

        items = [
            {
                "id": "power",
                "label": "Power",
                "status": status(
                    (
                        ("power_clinic_priority", "Clinic and water pump prioritized"),
                        ("power_collector_priority", "Collector systems prioritized"),
                        ("power_public_bus_restored", "Public rotation restored"),
                        ("power_siphon_exposed", "Hidden siphon exposed"),
                    ),
                    "Unresolved feeder pressure",
                ),
            },
            {
                "id": "clinic",
                "label": "Clinic",
                "status": status(
                    (
                        ("market_clinic_restored", "Medicine secured for the clinic"),
                        ("market_clinic_shared", "Medicine divided across needs"),
                        ("market_independent_clinic", "Independent supply route established"),
                        ("market_sol_stocked", "Sol retained the clinic case"),
                    ),
                    "Supply remains uncertain",
                ),
            },
            {
                "id": "protection",
                "label": "Protection ledger",
                "status": status(
                    (
                        ("protection_ledger_published", "Overlapping claims published"),
                        ("protection_ledger_hidden", "Ledger concealed"),
                        ("ledger_source_shielded", "Source protected"),
                        ("ledger_source_pressured", "Source pressured"),
                    ),
                    "Protection claims remain opaque",
                ),
            },
            {
                "id": "surveillance",
                "label": "Watcher",
                "status": status(
                    (
                        ("trial_was_staged", "Staged trial confirmed"),
                        ("watcher_evidence_with_mara", "Evidence held with Mara"),
                        ("watcher_evidence_preserved", "Evidence privately preserved"),
                        ("watcher_evidence_hidden", "Evidence concealed"),
                        ("watcher_evidence_with_sol", "Evidence given to Sol"),
                    ),
                    "Unknown observer remains active",
                ),
            },
            {
                "id": "sol",
                "label": "Sol",
                "status": (
                    "Escaped after the Collector confrontation"
                    if "sol_escaped" in state.flags
                    else "Absent from the public concourse"
                    if "sol_left_intake" in state.flags
                    else "Still operating beside the Collector"
                ),
            },
            {
                "id": "road",
                "label": "Outbound road",
                "status": (
                    "Shaklas public-index correction published and temporary appeal authority expired"
                    if state.story.checkpoint_id == "shaklas_appeal_complete"
                    else "Shaklas public receipt scope bounded and expiration proven"
                    if state.story.checkpoint_id == "shaklas_receipt_scope_complete"
                    else "Shaklas supplier terms bounded and independent salvage proven"
                    if state.story.checkpoint_id == "shaklas_gift_terms_complete"
                    else "Shaklas borrowed-light stewardship and neutral fallback proven"
                    if state.story.checkpoint_id == "shaklas_borrowed_light_complete"
                    else "Shaklas threshold cost and return route proven"
                    if state.story.checkpoint_id == "shaklas_threshold_cost_complete"
                    else "Shaklas public-service memory preserved"
                    if state.story.checkpoint_id == "shaklas_queue_memory_complete"
                    else "Neutral District 22 passage proven"
                    if state.story.checkpoint_id == "district22_public_access_complete"
                    else "Class-aware evidence discipline proven"
                    if state.story.checkpoint_id == "class_lens_complete"
                    else "Neutral reporting discipline proven"
                    if state.story.checkpoint_id == "report_reliability_complete"
                    else "Neutral relief coordination proven"
                    if state.story.checkpoint_id == "relief_detail_complete"
                    else "Neutral medicine route proven"
                    if state.story.checkpoint_id == "medicine_road_complete"
                    else "Neutral caravan precedent recorded"
                    if state.story.checkpoint_id == "unowned_caravan_complete"
                    else "Regional concourse open"
                    if state.story.checkpoint_id in {"regional_path_open", "first_contact_complete", "regional_expedition_complete", "headquarters_approach_complete"}
                    else "Provisional handoff ready"
                    if "route_handoff_ready" in state.flags
                    else "Second exit mapped"
                    if "escape_route_confirmed" in state.flags
                    else "No secure outbound route"
                ),
            },
        ]
        unresolved = {
            "Unresolved feeder pressure",
            "Supply remains uncertain",
            "Protection claims remain opaque",
            "Unknown observer remains active",
            "Still operating beside the Collector",
            "No secure outbound route",
        }
        changed = sum(item["status"] not in unresolved for item in items)
        return {
            "title": "Sprawl 15 pulse",
            "changed": changed,
            "total": len(items),
            "summary": f"{changed} of {len(items)} local conditions now reflect your decisions.",
            "items": items,
        }

    def _active_stage_contacts(
        self,
        state: GameState,
        stage: StoryStageDefinition,
    ) -> list[dict[str, object]]:
        npc_ids = set(stage.dialogues)
        for action in stage.actions:
            dialogue_id = action.requires_dialogue_id
            if dialogue_id is not None and dialogue_id in self.catalog.story.dialogues:
                npc_ids.add(self.catalog.story.dialogues[dialogue_id].npc_id)
        contacts: list[dict[str, object]] = []
        for npc_id in sorted(npc_ids, key=lambda value: self.catalog.story.npcs[value].name):
            npc = self.catalog.story.npcs[npc_id]
            if not all(flag in state.flags for flag in npc.requires_flags):
                continue
            if any(flag in state.flags for flag in npc.forbidden_flags):
                continue
            room_id = self._effective_npc_room(state, npc)
            known = room_id in state.visited_rooms
            contacts.append(
                {
                    "npc_id": npc.id,
                    "name": npc.name,
                    "room_id": room_id if known else None,
                    "room_title": self.catalog.rooms[room_id].title if known else "Unmapped",
                    "known": known,
                    "route_command": f"route {npc.nouns[0]}" if known else None,
                    "talk_command": f"talk {npc.nouns[0]}",
                }
            )
        return contacts

    def _story_projection(self, state: GameState) -> dict[str, object]:
        relationships = [
            {
                "npc_id": npc_id,
                "name": self.catalog.story.npcs[npc_id].name,
                "label": self.catalog.story.npcs[
                    npc_id
                ].relationship_label,
                "score": score,
                "standing": self._relationship_descriptor(score),
            }
            for npc_id, score in sorted(state.story.relationships.items())
        ]
        records = [
            {
                "id": record_id,
                "label": self.catalog.story.records[record_id].label,
                "description": self.catalog.story.records[
                    record_id
                ].description,
            }
            for record_id in sorted(
                state.story.records,
                key=lambda value: self.catalog.story.records[value].label,
            )
        ]
        route_interest = self._route_interest_projection(state)
        context = self._active_story_context(state)
        if context is None:
            return {
                "active": False,
                "arc_title": self._checkpoint_label(state.story.checkpoint_id),
                "checkpoint_id": state.story.checkpoint_id,
                "checkpoint_label": self._checkpoint_label(
                    state.story.checkpoint_id
                ),
                "completed_quests": sorted(state.story.completed_quests),
                "records": records,
                "relationships": relationships,
                "route_interest": route_interest,
                "readiness": self._story_readiness_projection(state),
                "sprawl_pulse": self._sprawl_pulse_projection(state),
                "actions": [],
                "contacts": [],
                "primary_command": None,
            }
        quest, stage = context
        actions: list[dict[str, object]] = []
        for action in stage.actions:
            label, summary, _ = self._story_action_label(state, action)
            available, reason = self._story_action_availability(state, action)
            actions.append(
                {
                    "id": action.id,
                    "label": label,
                    "summary": summary,
                    "approach": action.approach,
                    "command": self._story_action_command(action),
                    "available": available,
                    "unavailable_reason": reason,
                }
            )
        return {
            "active": True,
            "quest_id": quest.id,
            "quest_title": quest.title,
            "arc_title": quest.arc_title,
            "quest_summary": quest.summary,
            "stage_id": stage.id,
            "stage_title": stage.title,
            "directive": stage.directive,
            "objective": stage.objective,
            "why": stage.why,
            "room_hint": stage.room_hint,
            "target_room_id": stage.target_room_id,
            "progress_index": stage.progress_index,
            "progress_total": stage.progress_total,
            "primary_command": self._story_primary_command(state, stage),
            "actions": actions,
            "contacts": self._active_stage_contacts(state, stage),
            "checkpoint_id": state.story.checkpoint_id,
            "checkpoint_label": self._checkpoint_label(
                state.story.checkpoint_id
            ),
            "completed_quests": sorted(state.story.completed_quests),
            "records": records,
            "relationships": relationships,
            "route_interest": route_interest,
            "readiness": self._story_readiness_projection(state),
            "sprawl_pulse": self._sprawl_pulse_projection(state),
        }
