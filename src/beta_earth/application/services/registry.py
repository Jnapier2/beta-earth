"""Explicit command and compatibility ownership for application services."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from beta_earth.application.services.foundations import (
    CompanionService,
    InventoryService,
    ProgressionService,
    StoryService,
    WorldService,
)
from beta_earth.application.services.sovereignty import SovereigntyService

if TYPE_CHECKING:
    from beta_earth.application.engine import GameEngine


class EngineServiceRegistry:
    """Owns service instances and resolves legacy engine method attributes."""

    ATTRIBUTE_OWNERS = {
        **{name: "world" for name in (
            "render_room", "render_room_revisit", "_look", "_examine", "_glance",
            "_exits", "_resolve_known_room", "_resolve_known_npc_route", "_known_route",
            "_objective_route_projection", "_resume_briefing_projection", "_briefing",
            "_next", "_route", "_go", "_resolve_exit", "_move_character",
        )},
        **{name: "inventory" for name in (
            "_spawn_item", "_new_item_state", "_inventory_item",
            "_effective_item_definition", "_effective_max_durability",
            "_validate_item_durability", "_equipped_item_state", "_item_candidates",
            "_matching_items", "_resolve_items", "_set_reference",
            "_remove_inventory_item", "_repair_stale_reference", "_get", "_drop",
            "_inventory", "_equipment", "_compare", "_equip", "_unequip", "_repair",
            "_modify", "_vendors_here", "_resolve_vendor_item", "_market",
            "_resolve_recipe", "_craft", "_salvage",
        )},
        **{name: "companion" for name in (
            "_ensure_companion_progress", "_active_companion_context",
            "_award_companion_experience", "_sync_story_companion_order",
            "_detach_sol_if_story_requires", "_resolve_mercenary",
            "_companion_sync_projection", "_companion", "_party_projection", "_party",
        )},
        **{name: "progression" for name in (
            "_selected_specialization", "_selected_specialization_upgrade",
            "_specialization_values", "_apply_specialization_passive",
            "_prime_specialization_follow_up", "_resolve_ability_branch",
            "_resolve_specialization_upgrade", "_specialization_follow_up",
            "_experience", "_resolve_training_option", "_training_summary", "_train",
            "_retrain", "_resolve_training_profile", "_profile_summary", "_path",
            "_training_option_projection", "_plan", "_resolve_course",
            "_course_summary", "_course", "_apply_course_progress",
        )},
        **{name: "sovereignty" for name in (
            "_ensure_foundation_seed", "_apply_record_impacts",
            "_sync_quest_machines", "_sync_party_state",
            "_sync_active_foundations", "_foundation_projection",
            "_foundation_party_lines", "_sovereignty", "_faction",
            "_civic", "_territory",
        )},
        **{name: "story" for name in (
            "_tutorial_evidence_flag", "_clear_tutorial_evidence",
            "_tutorial_event_matches", "_record_tutorial_evidence",
            "_tutorial_step_satisfied", "_apply_tutorial_progress",
            "_active_story_context", "_story_event_matches",
            "_story_transition_satisfied", "_apply_story_progress",
            "_world_cycle_phase", "_effective_npc_room", "_story_npcs_in_room",
            "_resolve_npc", "_story_action_variant", "_story_action_label",
            "_story_action_command", "_story_inventory_counts",
            "_story_action_availability", "_checkpoint_label", "_candidacy_status",
            "_route_interest_projection", "_resolve_story_action", "_talk",
            "_relationship_descriptor", "_quest", "_choose", "_interact",
            "_execute_story_verb", "_remove_story_item", "_apply_story_action",
            "_story_shortest_step", "_story_route_command",
            "_story_live_pressure_command", "_story_transition_destination",
            "_story_required_item_ids", "_story_item_acquisition_command",
            "_story_recipe_guidance_command", "_story_contact_route_command",
            "_story_primary_command", "_story_readiness_projection",
            "_sprawl_pulse_projection", "_active_stage_contacts", "_story_projection",
        )},
    }

    COMMAND_OWNERS = {
        "look": "world", "examine": "world", "glance": "world", "exits": "world",
        "route": "world", "briefing": "world", "next": "world", "go": "world",
        "get": "inventory", "drop": "inventory", "inventory": "inventory",
        "equipment": "inventory", "compare": "inventory", "equip": "inventory",
        "unequip": "inventory", "repair": "inventory", "modify": "inventory",
        "market": "inventory", "craft": "inventory", "salvage": "inventory",
        "companion": "companion", "party": "companion",
        "sovereignty": "sovereignty", "faction": "sovereignty",
        "territory": "sovereignty", "civic": "sovereignty",
        "experience": "progression", "train": "progression", "retrain": "progression",
        "path": "progression", "plan": "progression", "course": "progression",
        "talk": "story", "choose": "story", "interact": "story", "quest": "story",
    }

    COMMAND_METHODS = {
        "look": "_look", "examine": "_examine", "glance": "_glance", "exits": "_exits",
        "route": "_route", "briefing": "_briefing", "next": "_next", "playtest": "_playtest",
        "journal": "_journal", "go": "_go", "withdraw": "_withdraw", "get": "_get",
        "drop": "_drop", "inventory": "_inventory", "equipment": "_equipment",
        "compare": "_compare", "equip": "_equip", "unequip": "_unequip", "repair": "_repair",
        "modify": "_modify", "technique": "_technique", "ability": "_ability",
        "market": "_market", "craft": "_craft", "salvage": "_salvage",
        "companion": "_companion", "party": "_party",
        "sovereignty": "_sovereignty", "faction": "_faction",
        "territory": "_territory", "civic": "_civic", "report": "_report",
        "district": "_district", "service": "_service", "hospice": "_hospice",
        "appeal": "_appeal", "wayfinding": "_wayfinding", "stance": "_stance",
        "defense": "_defense", "stand": "_stand", "attack": "_attack", "target": "_target",
        "assess": "_assess", "health": "_health", "injury": "_injury",
        "stabilize": "_stabilize", "experience": "_experience", "train": "_train",
        "retrain": "_retrain", "path": "_path", "plan": "_plan", "course": "_course",
        "talk": "_talk", "choose": "_choose", "interact": "_interact", "quest": "_quest",
        "build": "_build", "guide": "_guide", "info": "_info", "effects": "_effects",
        "roundtime": "_roundtime", "search": "_search", "say": "_say", "emote": "_emote",
        "wait": "_wait", "rest": "_rest", "recover": "_recover", "signal": "_signal",
        "queue": "_queue", "cancel": "_cancel", "again": "_again", "state": "_state",
        "help": "_help", "save": "_save", "quit": "_quit",
    }

    def __init__(self, engine: "GameEngine") -> None:
        self.engine = engine
        self.world = WorldService(engine)
        self.inventory = InventoryService(engine)
        self.companion = CompanionService(engine)
        self.progression = ProgressionService(engine)
        self.story = StoryService(engine)
        self.sovereignty = SovereigntyService(engine)
        self._services = {
            "world": self.world,
            "inventory": self.inventory,
            "companion": self.companion,
            "progression": self.progression,
            "story": self.story,
            "sovereignty": self.sovereignty,
        }

    def resolve_attribute(self, name: str) -> Any | None:
        owner = self.ATTRIBUTE_OWNERS.get(name)
        if owner is None:
            return None
        service = self._services[owner]
        descriptor = getattr(type(service), name, None)
        if descriptor is None:
            return None
        return getattr(service, name)

    def handler_map(self) -> dict[str, Callable[..., Any]]:
        handlers: dict[str, Callable[..., Any]] = {}
        for command, method_name in self.COMMAND_METHODS.items():
            owner = self.COMMAND_OWNERS.get(command)
            target = self._services[owner] if owner else self.engine
            handlers[command] = getattr(target, method_name)
        return handlers

    def ownership_projection(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for command in self.COMMAND_METHODS:
            grouped.setdefault(self.COMMAND_OWNERS.get(command, "orchestrator"), []).append(command)
        return {name: tuple(sorted(commands)) for name, commands in sorted(grouped.items())}
