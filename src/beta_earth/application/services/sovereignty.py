"""Active sovereignty, faction, party, quest, and territory projections.

The authored StoryState remains authoritative for narrative progression. This
service mirrors those decisions into the versioned foundation contracts and
applies bounded, content-defined consequences exactly once.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from beta_earth.application.parser import ParsedCommand
from beta_earth.application.results import HandlerResult as _HandlerResult
from beta_earth.application.services.base import EngineService
from beta_earth.application.text import natural_list as _natural_list
from beta_earth.domain.events import DomainEvent
from beta_earth.domain.progression import award_field_insight
from beta_earth.domain.sovereignty import (
    CANONICAL_FACTION_IDS,
    FactionStandingState,
    PartyState,
    QuestMachineState,
    TerritoryState,
)


class SovereigntyService(EngineService):
    """Activate v0.51.0 pledges and civic duty through the Sprawl 15 loop."""

    TERRITORY_ID = "sprawl_15"

    @staticmethod
    def _clamp(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(maximum, int(value)))

    def _standing_label(self, score: int) -> str:
        for band in self.catalog.foundation_activation.standing_bands:
            if band.minimum <= score <= band.maximum:
                return band.label
        return "Uncommitted"

    def _ensure_foundation_seed(self, state) -> tuple[bool, list[DomainEvent]]:
        changed = False
        events: list[DomainEvent] = []
        sovereignty = state.foundation.sovereignty
        for faction_id in CANONICAL_FACTION_IDS:
            if faction_id not in sovereignty.factions:
                sovereignty.factions[faction_id] = FactionStandingState()
                changed = True
        seed = self.catalog.foundation_activation.territory_seed
        if seed.id not in state.foundation.territories:
            state.foundation.territories[seed.id] = TerritoryState(
                territory_id=seed.id,
                owner_id=seed.owner_id,
                level=seed.level,
                population=seed.population,
                supply=seed.supply,
                defense=seed.defense,
                prosperity=seed.prosperity,
                tension=seed.tension,
                visibility=seed.visibility,
                world_modifiers={
                    "gameplay_interpretation",
                    "community_state_no_player_ownership",
                },
            )
            changed = True
            events.append(
                DomainEvent(
                    "foundation.territory_seeded",
                    {
                        "territory_id": seed.id,
                        "canon_status": seed.canon_status,
                        "source_authority": seed.source_authority,
                    },
                )
            )
        civic = self.catalog.foundation_activation.civic_mission
        if civic.id not in state.foundation.quests:
            state.foundation.quests[civic.id] = QuestMachineState(
                quest_id=civic.id,
                status="inactive",
                active_objective_ids={"accept"},
            )
            changed = True
            events.append(
                DomainEvent(
                    "foundation.civic_mission_seeded",
                    {
                        "mission_id": civic.id,
                        "canon_status": civic.canon_status,
                        "source_authority": civic.source_authority,
                    },
                )
            )
        return changed, events

    def _apply_record_impacts(self, state) -> tuple[bool, list[DomainEvent]]:
        changed = False
        events: list[DomainEvent] = []
        activation = self.catalog.foundation_activation
        territory = state.foundation.territories[activation.territory_seed.id]
        sovereignty = state.foundation.sovereignty
        known_impacts = set(activation.faction_impacts) | set(activation.territory_impacts)
        pending = sorted(
            (state.story.records & known_impacts)
            - state.foundation.applied_story_record_ids
        )
        for record_id in pending:
            faction_impact = activation.faction_impacts.get(record_id)
            territory_impact = activation.territory_impacts.get(record_id)
            if faction_impact is not None:
                standing = sovereignty.factions.setdefault(
                    faction_impact.faction_id, FactionStandingState()
                )
                standing.public_standing = self._clamp(
                    standing.public_standing + faction_impact.public_delta,
                    -1000,
                    1000,
                )
                standing.covert_standing = self._clamp(
                    standing.covert_standing + faction_impact.covert_delta,
                    -1000,
                    1000,
                )
                standing.access_flags.update(faction_impact.access_flags)
                events.append(
                    DomainEvent(
                        "foundation.faction_standing_changed",
                        {
                            "record_id": record_id,
                            "faction_id": faction_impact.faction_id,
                            "public_delta": faction_impact.public_delta,
                            "covert_delta": faction_impact.covert_delta,
                            "membership_granted": False,
                        },
                    )
                )
                changed = True
            if territory_impact is not None:
                trust = sovereignty.local_trust.get(self.TERRITORY_ID, 0)
                sovereignty.local_trust[self.TERRITORY_ID] = self._clamp(
                    trust + territory_impact.local_trust_delta, -1000, 1000
                )
                for attribute in (
                    "supply",
                    "defense",
                    "prosperity",
                    "tension",
                    "visibility",
                ):
                    delta = getattr(territory_impact, f"{attribute}_delta")
                    setattr(
                        territory,
                        attribute,
                        self._clamp(getattr(territory, attribute) + delta, 0, 100),
                    )
                territory.caravan_route_ids.update(territory_impact.caravan_route_ids)
                territory.world_modifiers.update(territory_impact.world_modifiers)
                events.append(
                    DomainEvent(
                        "foundation.territory_changed",
                        {
                            "territory_id": territory.territory_id,
                            "record_id": record_id,
                            "local_trust_delta": territory_impact.local_trust_delta,
                            "supply_delta": territory_impact.supply_delta,
                            "defense_delta": territory_impact.defense_delta,
                            "prosperity_delta": territory_impact.prosperity_delta,
                            "tension_delta": territory_impact.tension_delta,
                            "visibility_delta": territory_impact.visibility_delta,
                        },
                    )
                )
                changed = True
            state.foundation.applied_story_record_ids.add(record_id)
        return changed, events

    def _actions_for_quest(self, quest):
        for stage in quest.stages:
            for action in stage.actions:
                yield stage, action

    def _sync_quest_machines(self, state) -> bool:
        changed = False
        story = state.story
        relevant = set(story.completed_quests)
        if story.active_quest_id is not None:
            relevant.add(story.active_quest_id)
        for quest_id in sorted(relevant):
            quest = self.catalog.story.quests.get(quest_id)
            if quest is None:
                continue
            completed_action_ids: set[str] = set()
            consequences: set[str] = set()
            selected_resolution: str | None = None
            for _stage, action in self._actions_for_quest(quest):
                if action.id in story.completed_actions:
                    completed_action_ids.add(f"action:{action.id}")
                    consequences.update(set(action.records) & story.records)
                    if action.records or action.complete_quest or action.next_quest_id:
                        selected_resolution = action.id
            active_objectives = (
                {f"stage:{story.active_stage_id}"}
                if story.active_quest_id == quest_id and story.active_stage_id
                else set()
            )
            status = (
                "completed"
                if quest_id in story.completed_quests
                else "active"
                if story.active_quest_id == quest_id
                else "inactive"
            )
            machine = QuestMachineState(
                quest_id=quest_id,
                status=status,
                active_objective_ids=active_objectives,
                completed_objective_ids=completed_action_ids,
                selected_resolution_id=selected_resolution,
                consequence_ids=consequences,
            )
            if state.foundation.quests.get(quest_id) != machine:
                state.foundation.quests[quest_id] = machine
                changed = True

            if quest_id in story.completed_quests:
                faction_ids = {
                    impact.faction_id
                    for _stage, action in self._actions_for_quest(quest)
                    for record_id in action.records
                    if (impact := self.catalog.foundation_activation.faction_impacts.get(record_id))
                    is not None
                }
                for faction_id in faction_ids:
                    standing = state.foundation.sovereignty.factions[faction_id]
                    if quest_id not in standing.completed_quest_ids:
                        standing.completed_quest_ids.add(quest_id)
                        changed = True
        return changed

    def _sync_party_state(self, state) -> bool:
        companion_id = state.character.companion_id
        flags = state.flags
        if (
            "field_cohort_detail_active" in flags
            and "field_cohort_detail_complete" not in flags
        ):
            formation = (
                "defensive"
                if "field_cohort_formation_defensive" in flags
                else "offensive"
                if "field_cohort_formation_offensive" in flags
                else "balanced"
            )
            reports = (
                {"route_tokens_verified"}
                if "field_cohort_tokens_verified" in flags
                else set()
            )
            party = PartyState(
                formation=formation,
                member_ids=["player", "sera_vann", "neutral_cohort"],
                commander_id="player",
                protection_assignments={"player": "neutral_cohort"},
                intelligence_reports=reports,
            )
        elif "relief_detail_active" in flags and "relief_detail_complete" not in flags:
            reports = (
                {"relief_report_received"}
                if "relief_report_received" in flags
                else set()
            )
            party = PartyState(
                formation="custom",
                member_ids=["player", "taro_scout", "neme_patch"],
                commander_id="player",
                protection_assignments={"player": "clinic_patients"},
                intelligence_reports=reports,
            )
        elif companion_id == "sol":
            progress = state.character.companion_progress.get("sol")
            order = progress.order if progress is not None else "balanced"
            formation = {
                "guard": "defensive",
                "assault": "offensive",
                "balanced": "balanced",
            }.get(order, "balanced")
            party = PartyState(
                formation=formation,
                member_ids=["player", "sol"],
                commander_id="player",
                protection_assignments=(
                    {"sol": "player"} if order == "guard" else {}
                ),
            )
        elif companion_id:
            party = PartyState(
                formation="custom",
                member_ids=["player"],
                mercenary_ids=[companion_id],
                commander_id="player",
            )
        else:
            party = PartyState(member_ids=["player"])
        if state.foundation.party != party:
            state.foundation.party = party
            return True
        return False

    def _sync_active_foundations(
        self,
        state,
        source_events: Iterable[DomainEvent] = (),
    ) -> _HandlerResult:
        """Idempotently synchronize all active contracts after a command/load."""

        changed, events = self._ensure_foundation_seed(state)
        record_changed, record_events = self._apply_record_impacts(state)
        changed = changed or record_changed
        events.extend(record_events)
        quest_changed = self._sync_quest_machines(state)
        party_changed = self._sync_party_state(state)
        changed = changed or quest_changed or party_changed
        if changed:
            events.append(
                DomainEvent(
                    "foundation.synchronized",
                    {
                        "source_event_count": sum(1 for _ in source_events),
                        "active_quest_id": state.story.active_quest_id,
                        "territory_id": self.TERRITORY_ID,
                    },
                )
            )
        return _HandlerResult((), tuple(events), changed)

    def _foundation_projection(self, state) -> dict[str, object]:
        self._ensure_foundation_seed(state)
        sovereignty = state.foundation.sovereignty
        territory = state.foundation.territories[self.TERRITORY_ID]
        factions = []
        for faction_id, definition in self.catalog.creation.factions.items():
            standing = sovereignty.factions[faction_id]
            route = self.catalog.foundation_activation.pledge_routes[faction_id]
            factions.append(
                {
                    "id": faction_id,
                    "name": definition.name,
                    "public_standing": standing.public_standing,
                    "covert_standing": standing.covert_standing,
                    "standing_label": self._standing_label(standing.public_standing),
                    "rank": standing.rank,
                    "rank_title": standing.rank_title,
                    "pledge_entry_rank_title": route.recruit_title,
                    "pledge_statement": route.pledge_statement,
                    "access_flags": sorted(standing.access_flags),
                    "completed_quest_ids": sorted(standing.completed_quest_ids),
                    "is_allegiance": sovereignty.allegiance_id == faction_id,
                    "is_pending_allegiance": sovereignty.pending_allegiance_id == faction_id,
                    "pledge_minimum_standing": route.minimum_public_standing,
                    "pledge_required_flag": route.required_access_flag,
                    "pledge_eligible": (
                        sovereignty.allegiance_id is None
                        and standing.public_standing >= route.minimum_public_standing
                        and route.required_access_flag in standing.access_flags
                    ),
                }
            )
        active_machine = (
            state.foundation.quests.get(state.story.active_quest_id)
            if state.story.active_quest_id
            else None
        )
        return {
            "schema": "beta-earth-foundation-state-v3",
            "activation": self.catalog.foundation_activation.id,
            "allegiance_id": sovereignty.allegiance_id,
            "pending_allegiance_id": sovereignty.pending_allegiance_id,
            "allegiance_confirmed_turn": sovereignty.allegiance_confirmed_turn,
            "pledge_receipt_ids": sorted(sovereignty.pledge_receipt_ids),
            "membership_granted": sovereignty.allegiance_id is not None,
            "local_trust": sovereignty.local_trust.get(self.TERRITORY_ID, 0),
            "factions": factions,
            "party": state.foundation.party.to_dict(),
            "territory": territory.to_dict(),
            "territory_title": self.catalog.foundation_activation.territory_seed.title,
            "territory_interpretation": self.catalog.foundation_activation.territory_seed.interpretation_note,
            "active_quest_machine": active_machine.to_dict() if active_machine else None,
            "civic_mission": state.foundation.quests[self.catalog.foundation_activation.civic_mission.id].to_dict(),
            "quest_machine_count": len(state.foundation.quests),
            "applied_story_record_count": len(state.foundation.applied_story_record_ids),
        }

    def _foundation_party_lines(self, state) -> tuple[str, ...]:
        party = state.foundation.party
        roster = list(party.member_ids) + [f"{item} (mercenary)" for item in party.mercenary_ids]
        lines = [
            "Foundation party state:",
            f"  Formation: {party.formation.title()}.",
            f"  Roster: {_natural_list(roster) if roster else 'none'}.",
            f"  Commander: {party.commander_id or 'none'}.",
            "  Capacity: 6 party members plus 2 mercenaries; current authority remains bounded by the active story detail.",
        ]
        if party.intelligence_reports:
            lines.append("  Current reports: " + _natural_list(sorted(party.intelligence_reports)) + ".")
        return tuple(lines)

    def _sovereignty(self, state, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        projection = self._foundation_projection(state)
        if query in {"records", "decisions", "ledger"}:
            labels = [
                self.catalog.story.records[record_id].label
                for record_id in sorted(state.story.records)
                if record_id in self.catalog.story.records
            ]
            lines = [
                f"Sovereignty ledger: {len(labels)} authored decisions; {projection['applied_story_record_count']} currently carry activated system consequences.",
            ]
            lines.extend(f"  • {label}" for label in labels[-12:])
            if len(labels) > 12:
                lines.append("  (Showing twelve alphabetically final entries; QUEST retains the authored narrative context.)")
            return _HandlerResult(("\n".join(lines),))
        trust = int(projection["local_trust"])
        allegiance = projection["allegiance_id"] or "none"
        pending = projection["pending_allegiance_id"] or "none"
        return _HandlerResult(
            (
                "SOVEREIGNTY · CHOSEN CONSEQUENCES\n"
                f"Allegiance: {allegiance}. Pending pledge: {pending}. No faction membership is granted by route observation or candidacy alone.\n"
                f"Sprawl 15 local trust: {trust:+d} ({self._standing_label(trust)}).\n"
                f"Activated authored records: {projection['applied_story_record_count']}.\n"
                "Use FACTION for all seven relationships, TERRITORY for community pressure, PARTY for the live field structure, or SOVEREIGNTY RECORDS for the decision ledger.",
            )
        )

    @staticmethod
    def _faction_matches(query: str, factions: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            item
            for item in factions
            if query == str(item["id"]).casefold()
            or query in str(item["name"]).casefold()
            or str(item["id"]).casefold().startswith(query)
        ]

    def _faction(self, state, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        projection = self._foundation_projection(state)
        factions = projection["factions"]
        assert isinstance(factions, list)
        sovereignty = state.foundation.sovereignty
        activation = self.catalog.foundation_activation

        if query in {"yes", "y", "confirm", "pledge yes", "pledge y"}:
            if self._live_creatures(state):
                return _HandlerResult(("Hostile pressure prevents a deliberate faction pledge. Resolve or leave the encounter first.",))
            faction_id = sovereignty.pending_allegiance_id
            if faction_id is None:
                return _HandlerResult(("No faction pledge is awaiting confirmation.",))
            if sovereignty.allegiance_id is not None:
                sovereignty.pending_allegiance_id = None
                return _HandlerResult(("An allegiance is already active; the pending pledge was cleared without changing it.",), changed=True)
            route = activation.pledge_routes[faction_id]
            standing = sovereignty.factions[faction_id]
            if standing.public_standing < route.minimum_public_standing or route.required_access_flag not in standing.access_flags:
                sovereignty.pending_allegiance_id = None
                return _HandlerResult(("The candidacy evidence changed before confirmation. The pledge was cleared without enlistment.",), changed=True)
            sovereignty.allegiance_id = faction_id
            sovereignty.pending_allegiance_id = None
            sovereignty.allegiance_confirmed_turn = state.turn
            receipt_id = f"pledge:{faction_id}"
            sovereignty.pledge_receipt_ids.add(receipt_id)
            standing.rank = max(1, standing.rank)
            standing.rank_title = route.recruit_title
            standing.access_flags.update({route.membership_flag, "pledge_confirmed"})
            self._set_roundtime(state, now, 2)
            definition = self.catalog.creation.factions[faction_id]
            return _HandlerResult(
                (
                    f"PLEDGE CONFIRMED · {definition.name}",
                    f"Entry rank: {route.recruit_title}. {route.pledge_statement}",
                    "This grants faction membership only. Guild enrollment, citizenship, territory ownership, raid command, and Commander transport remain locked behind later explicit systems.",
                ),
                (
                    DomainEvent(
                        "foundation.faction_pledged",
                        {
                            "faction_id": faction_id,
                            "rank": standing.rank,
                            "rank_title": standing.rank_title,
                            "receipt_id": receipt_id,
                            "guild_membership_granted": False,
                            "citizenship_granted": False,
                            "territory_ownership_granted": False,
                            "commander_authority_granted": False,
                        },
                    ),
                ),
                True,
            )

        if query in {"no", "n", "cancel", "pledge no", "pledge n", "pledge cancel"}:
            faction_id = sovereignty.pending_allegiance_id
            if faction_id is None:
                return _HandlerResult(("No faction pledge is awaiting cancellation.",))
            sovereignty.pending_allegiance_id = None
            return _HandlerResult(
                ("Faction pledge cancelled. No allegiance, rank, or access changed.",),
                (DomainEvent("foundation.faction_pledge_cancelled", {"faction_id": faction_id}),),
                True,
            )

        if query.startswith("pledge") or query.startswith("join") or query.startswith("allege"):
            if self._live_creatures(state):
                return _HandlerResult(("Hostile pressure prevents staging a deliberate faction pledge. Resolve or leave the encounter first.",))
            words = query.split(maxsplit=1)
            target_query = words[1] if len(words) > 1 else ""
            if target_query in {"", "status"}:
                pending = sovereignty.pending_allegiance_id or "none"
                return _HandlerResult((f"Pending pledge: {pending}. Use FACTION PLEDGE <NAME>, then answer with FACTION Y or FACTION N.",))
            matches = self._faction_matches(target_query, factions)
            if len(matches) != 1:
                return _HandlerResult(("Name one faction shown by FACTION STATUS.",))
            item = matches[0]
            faction_id = str(item["id"])
            if sovereignty.allegiance_id == faction_id:
                return _HandlerResult((f"You are already pledged to {item['name']} at rank {item['rank_title']}.",))
            if sovereignty.allegiance_id is not None:
                return _HandlerResult(("An allegiance is already active. Switching or abandoning factions is not part of this contained milestone.",))
            route = activation.pledge_routes[faction_id]
            standing = sovereignty.factions[faction_id]
            missing: list[str] = []
            if standing.public_standing < route.minimum_public_standing:
                missing.append(f"public standing {route.minimum_public_standing:+d}")
            if route.required_access_flag not in standing.access_flags:
                missing.append("an accepted candidacy contact")
            if missing:
                return _HandlerResult((f"Pledge unavailable for {item['name']}: requires {_natural_list(missing)}. Route observation alone is not membership.",))
            sovereignty.pending_allegiance_id = faction_id
            return _HandlerResult(
                (
                    f"PLEDGE STAGED · {item['name']}",
                    f"Entry rank: {route.recruit_title}. {route.pledge_statement}",
                    "One allegiance will become active. This does not grant guild access, citizenship, territory ownership, raid command, or a permanent right to direct others.",
                    "Action? [Y/N]  Use FACTION Y or FACTION N.",
                ),
                (DomainEvent("foundation.faction_pledge_staged", {"faction_id": faction_id, "entry_rank": route.recruit_title}),),
                True,
            )

        if query in {"", "status", "list", "routes"}:
            lines = [
                "FACTION RELATIONSHIPS · ALLEGIANCE REQUIRES AN EXPLICIT PLEDGE",
                "Route contact and candidacy can change trust or access. FACTION PLEDGE <NAME> stages one deliberate membership choice.",
            ]
            for item in factions:
                marker = " · pledged" if item["is_allegiance"] else " · pending" if item["is_pending_allegiance"] else ""
                eligibility = "eligible" if item["pledge_eligible"] else "locked"
                lines.append(
                    f"  {item['name']}: {item['public_standing']:+d} {item['standing_label']} · "
                    f"rank {item['rank_title']} · pledge {eligibility} · {len(item['completed_quest_ids'])} linked faction quest(s){marker}"
                )
            return _HandlerResult(("\n".join(lines),))

        matches = self._faction_matches(query, factions)
        if len(matches) != 1:
            return _HandlerResult(("Name one faction shown by FACTION STATUS.",))
        item = matches[0]
        access = _natural_list(item["access_flags"]) if item["access_flags"] else "none"
        pledge_state = "eligible" if item["pledge_eligible"] else "locked"
        return _HandlerResult(
            (
                f"{item['name']}\n"
                f"Public standing: {item['public_standing']:+d} ({item['standing_label']}).\n"
                f"Covert standing: {item['covert_standing']:+d}. Rank: {item['rank_title']} ({item['rank']}).\n"
                f"Access/contact flags: {access}.\n"
                f"Linked completed faction quests: {len(item['completed_quest_ids'])}.\n"
                f"Pledge: {pledge_state}; requires standing {item['pledge_minimum_standing']:+d} and {item['pledge_required_flag']}.\n"
                f"Allegiance: {'yes' if item['is_allegiance'] else 'no'} — candidacy never silently enlists you. Guild access remains locked until its own quest requirements are implemented.",
            )
        )

    def _civic(self, state, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        mission_definition = self.catalog.foundation_activation.civic_mission
        mission = state.foundation.quests[mission_definition.id]
        territory = state.foundation.territories[self.TERRITORY_ID]

        if query in {"", "status", "summary"}:
            objective = next(iter(sorted(mission.active_objective_ids)), "complete")
            plan = mission.selected_resolution_id or "none"
            return _HandlerResult(
                (
                    f"{mission_definition.title} · {mission.status.title()}\n"
                    f"{mission_definition.summary}\n"
                    f"Current step: {objective}. Selected plan: {plan}.\n"
                    f"Territory: supply {territory.supply}, defense {territory.defense}, prosperity {territory.prosperity}, tension {territory.tension}, visibility {territory.visibility}.\n"
                    "Sequence: CIVIC ACCEPT → CIVIC INSPECT → CIVIC PLAN <SUPPLY|WATCH|RELIEF> → CIVIC EXECUTE → CIVIC CLOSE. The chain grants no ownership, citizenship, guild status, raid state, or Commander transport.",
                )
            )

        if query == "accept":
            if mission.status == "completed":
                return _HandlerResult(("This first civic-duty receipt is already closed.",))
            if "accept" not in mission.active_objective_ids:
                return _HandlerResult(("The civic chain has already moved beyond acceptance.",))
            mission.status = "active"
            mission.active_objective_ids = {"inspect"}
            mission.completed_objective_ids.add("accept")
            self._set_roundtime(state, now, 1)
            return _HandlerResult(
                ("Civic duty accepted. Inspect the public shortage before choosing which pressure to answer.",),
                (DomainEvent("foundation.civic_mission_accepted", {"mission_id": mission.quest_id}),),
                True,
            )

        if query in {"inspect", "read", "assess"}:
            if "inspect" not in mission.active_objective_ids:
                return _HandlerResult(("CIVIC INSPECT is not the current step. Use CIVIC STATUS.",))
            mission.active_objective_ids = {"select_plan"}
            mission.completed_objective_ids.add("inspect")
            self._set_roundtime(state, now, 2)
            lines = [
                "Sprawl 15 cannot answer every pressure at once:",
                "  SUPPLY keeps the shared table and clinic line moving.",
                "  WATCH makes protection visible but may raise tension by making the district easier to read.",
                "  RELIEF lowers immediate pressure before protection debts become another form of tribute.",
                "Use CIVIC PLAN <SUPPLY|WATCH|RELIEF>.",
            ]
            return _HandlerResult(("\n".join(lines),), (DomainEvent("foundation.civic_conditions_inspected", {"mission_id": mission.quest_id}),), True)

        if query.startswith("plan ") or query.startswith("choose "):
            if "select_plan" not in mission.active_objective_ids:
                return _HandlerResult(("A civic plan cannot be selected at the current step. Use CIVIC STATUS.",))
            plan_query = query.split(maxsplit=1)[1]
            matches = [
                plan for plan in mission_definition.plans.values()
                if plan_query == plan.id or plan.id.startswith(plan_query) or plan_query in plan.name.casefold()
            ]
            if len(matches) != 1:
                return _HandlerResult(("Use CIVIC PLAN SUPPLY, WATCH, or RELIEF.",))
            plan = matches[0]
            mission.selected_resolution_id = plan.id
            mission.completed_objective_ids.add("select_plan")
            mission.active_objective_ids = {"execute"}
            self._set_roundtime(state, now, 1)
            return _HandlerResult(
                (f"Plan selected · {plan.name}: {plan.summary}", "Use CIVIC EXECUTE when Sprawl 15 is free of immediate hostile pressure."),
                (DomainEvent("foundation.civic_plan_selected", {"mission_id": mission.quest_id, "plan_id": plan.id}),),
                True,
            )

        if query in {"execute", "act", "perform"}:
            if "execute" not in mission.active_objective_ids or mission.selected_resolution_id is None:
                return _HandlerResult(("No executable civic plan is ready. Use CIVIC STATUS.",))
            if self._live_creatures(state):
                return _HandlerResult(("Hostile pressure prevents accountable civic work. Resolve or leave the encounter first.",))
            room = self.catalog.rooms[state.character.room_id]
            if "sprawl 15" not in room.title.casefold():
                return _HandlerResult(("Return to a Sprawl 15 location before executing this local civic duty.",))
            plan = mission_definition.plans[mission.selected_resolution_id]
            before = {key: getattr(territory, key) for key in ("supply", "defense", "prosperity", "tension", "visibility")}
            for key in before:
                setattr(territory, key, self._clamp(before[key] + getattr(plan, f"{key}_delta"), 0, 100))
            trust_before = state.foundation.sovereignty.local_trust.get(self.TERRITORY_ID, 0)
            trust_after = self._clamp(trust_before + plan.local_trust_delta, -1000, 1000)
            state.foundation.sovereignty.local_trust[self.TERRITORY_ID] = trust_after
            allegiance_id = state.foundation.sovereignty.allegiance_id
            allegiance_before = None
            allegiance_after = None
            if allegiance_id is not None:
                standing = state.foundation.sovereignty.factions[allegiance_id]
                allegiance_before = standing.public_standing
                allegiance_after = self._clamp(allegiance_before + plan.allegiance_standing_delta, -1000, 1000)
                standing.public_standing = allegiance_after
                standing.access_flags.add("civic_service_proven")
            territory.world_modifiers.add(f"civic_plan:{plan.id}")
            mission.completed_objective_ids.add("execute")
            mission.active_objective_ids = {"close"}
            mission.consequence_ids.update({f"plan:{plan.id}", "civic_action_executed"})
            self._set_roundtime(state, now, plan.roundtime)
            after = {key: getattr(territory, key) for key in before}
            changes = [f"{key.title()} {before[key]}→{after[key]}" for key in before if before[key] != after[key]]
            changes.append(f"Local trust {trust_before:+d}→{trust_after:+d}")
            if allegiance_before is not None and allegiance_after is not None:
                changes.append(f"Allegiance standing {allegiance_before:+d}→{allegiance_after:+d}")
            return _HandlerResult(
                (f"[{plan.name}] {plan.summary}", "; ".join(changes) + ".", f"Roundtime: {plan.roundtime} sec. Use CIVIC CLOSE to publish the bounded receipt."),
                (DomainEvent("foundation.civic_plan_executed", {"mission_id": mission.quest_id, "plan_id": plan.id, "before": before, "after": after, "local_trust_before": trust_before, "local_trust_after": trust_after, "allegiance_id": allegiance_id}),),
                True,
            )

        if query in {"close", "publish", "complete"}:
            if "close" not in mission.active_objective_ids or mission.selected_resolution_id is None:
                return _HandlerResult(("No civic receipt is ready to close. Use CIVIC STATUS.",))
            plan = mission_definition.plans[mission.selected_resolution_id]
            mission.completed_objective_ids.add("close")
            mission.active_objective_ids.clear()
            mission.status = "completed"
            mission.consequence_ids.update({mission_definition.completion_modifier, "reward_claimed"})
            territory.world_modifiers.add(mission_definition.completion_modifier)
            award_field_insight(state.character.experience, plan.field_insight, now)
            self._set_roundtime(state, now, 2)
            return _HandlerResult(
                (
                    f"CIVIC RECEIPT CLOSED · {plan.name}",
                    f"Field insight: +{plan.field_insight}. The record names the work, its limits, and the community affected.",
                    "Sprawl 15 remains unowned by the player. No guild eligibility, citizenship, settlement-loss state, caravan ownership, raid immunity, or Commander authority was created.",
                ),
                (DomainEvent("foundation.civic_mission_completed", {"mission_id": mission.quest_id, "plan_id": plan.id, "field_insight": plan.field_insight, "ownership_granted": False, "guild_access_granted": False, "citizenship_granted": False}),),
                True,
            )

        return _HandlerResult(("Use CIVIC STATUS, ACCEPT, INSPECT, PLAN <SUPPLY|WATCH|RELIEF>, EXECUTE, or CLOSE.",))

    def _territory(self, state, command: ParsedCommand, now: float) -> _HandlerResult:
        query = self._query(command.args)
        territory = state.foundation.territories[self.TERRITORY_ID]
        if query in {"", "status", "list", "summary"}:
            ready = []
            for action in self.catalog.foundation_activation.maintenance_actions.values():
                remaining = max(0, territory.maintenance_ready_turns.get(action.id, 0) - state.turn)
                ready.append(f"{action.id}: {'ready' if remaining == 0 else f'{remaining} turn(s)'}")
            owner = territory.owner_id or "community / unowned by the player"
            return _HandlerResult(
                (
                    f"{self.catalog.foundation_activation.territory_seed.title}\n"
                    f"Owner: {owner}. Level: {territory.level} gameplay band. Population: unmeasured.\n"
                    f"Supply {territory.supply}/100 · Defense {territory.defense}/100 · Prosperity {territory.prosperity}/100\n"
                    f"Tension {territory.tension}/100 · Visibility {territory.visibility}/100 · Local trust {state.foundation.sovereignty.local_trust.get(self.TERRITORY_ID, 0):+d}\n"
                    f"Caravan routes: {_natural_list(sorted(territory.caravan_route_ids)) if territory.caravan_route_ids else 'none recorded'}.\n"
                    f"Maintenance: {'; '.join(ready)}.\n"
                    "Use TERRITORY SUPPORT <SUPPLY|DEFENSE|RELIEF> for repeatable maintenance or CIVIC STATUS for the one-time accountable mission chain. Neither grants ownership, guild settlement, or a full Barron Lands claim.",
                )
            )
        words = query.split()
        if words and words[0] in {"support", "maintain", "help"}:
            words = words[1:]
        action_query = " ".join(words)
        matches = [
            action
            for action in self.catalog.foundation_activation.maintenance_actions.values()
            if action_query == action.id or action.id.startswith(action_query)
            or action_query in action.name.casefold()
        ]
        if len(matches) != 1:
            return _HandlerResult(("Use TERRITORY SUPPORT SUPPLY, DEFENSE, or RELIEF.",))
        if self._live_creatures(state):
            return _HandlerResult(("Hostile pressure prevents accountable territory maintenance. Resolve or leave the encounter first.",))
        action = matches[0]
        ready_turn = territory.maintenance_ready_turns.get(action.id, 0)
        if state.turn < ready_turn:
            return _HandlerResult(
                (f"{action.name} is already covered. It becomes available in {ready_turn - state.turn} turn(s).",)
            )
        before = {
            key: getattr(territory, key)
            for key in ("supply", "defense", "prosperity", "tension", "visibility")
        }
        for key in before:
            delta = getattr(action, f"{key}_delta")
            setattr(territory, key, self._clamp(before[key] + delta, 0, 100))
        territory.maintenance_ready_turns[action.id] = state.turn + action.cooldown_turns
        territory.world_modifiers.add(f"maintenance:{action.id}")
        self._set_roundtime(state, now, action.roundtime)
        after = {key: getattr(territory, key) for key in before}
        changes = [
            f"{key.title()} {before[key]}→{after[key]}"
            for key in before
            if before[key] != after[key]
        ]
        return _HandlerResult(
            (
                f"[{action.name}] {action.summary}",
                "; ".join(changes) + ".",
                f"Roundtime: {action.roundtime} sec. This maintenance line reopens after {action.cooldown_turns} turns.",
            ),
            (
                DomainEvent(
                    "foundation.territory_maintained",
                    {
                        "territory_id": territory.territory_id,
                        "action_id": action.id,
                        "before": before,
                        "after": after,
                        "ready_turn": territory.maintenance_ready_turns[action.id],
                    },
                ),
            ),
            True,
        )
