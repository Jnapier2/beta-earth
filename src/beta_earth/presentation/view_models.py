from __future__ import annotations

from beta_earth.application.service import GameSnapshot
from beta_earth.domain.quests import QuestJournalEntry


def snapshot_to_dict(snapshot: GameSnapshot, *, version: str) -> dict[str, object]:
    state = snapshot.state
    room = snapshot.room
    actions = [
        {
            "id": action.id,
            "label": action.label,
            "command": action.command,
            "kind": action.kind.value,
            "description": action.description,
            "shortcut": action.shortcut,
            "enabled": action.enabled,
            "mission_id": action.mission_id,
            "mission_relevant": action.mission_id is not None,
        }
        for action in snapshot.actions
    ]
    economy = snapshot.economy
    return {
        "api_version": "1.3",
        "game_version": version,
        "world": snapshot.world_title,
        "player": {
            "id": state.player_id,
            "name": state.display_name,
            "stage": state.stage.value,
            "identity": state.identity,
            "stats": state.stats.as_dict(),
            "revision": state.revision,
            "visited_room_count": len(state.visited_rooms),
        },
        "room": {
            "id": room.id,
            "name": room.name,
            "zone": room.zone,
            "description": room.description,
            "ambient": room.ambient,
            "danger": room.danger,
            "exits": list(room.exits.keys()),
            "canon_refs": list(room.canon_refs),
        },
        "message": state.last_message,
        "recommended_action": actions[0] if actions else None,
        "current_options": actions,
        "selectable_commands": [action["command"] for action in actions],
        "quest_journal": {
            "active": [_journal_entry(entry) for entry in snapshot.quest_journal.active],
            "completed": [_journal_entry(entry) for entry in snapshot.quest_journal.completed],
            "active_count": len(snapshot.quest_journal.active),
            "completed_count": len(snapshot.quest_journal.completed),
        },
        "economy": {
            "cred": economy.cred,
            "inventory": [
                {
                    "item_id": item.item_id,
                    "name": item.name,
                    "description": item.description,
                    "category": item.category,
                    "quantity": item.quantity,
                }
                for item in economy.inventory
            ],
            "room_offers": [
                {
                    "id": offer.id,
                    "label": offer.label,
                    "command": offer.command,
                    "description": offer.description,
                    "cost_cred": offer.cost_cred,
                    "affordable": offer.affordable,
                    "completed": offer.completed,
                    "grant_summary": offer.grant_summary,
                }
                for offer in economy.room_offers
            ],
        },
        "did_readiness": _did_readiness(snapshot.did_readiness),
    }


def _did_readiness(readiness) -> dict[str, object]:
    return {
        "tier": readiness.tier,
        "label": readiness.label,
        "summary": readiness.summary,
        "combat_modifiers_enabled": readiness.combat_modifiers_enabled,
        "reasons": list(readiness.reasons),
        "next_hint": readiness.next_hint,
        "slots": [
            {
                "slot_id": slot.slot_id,
                "label": slot.label,
                "equipped_item_id": slot.equipped_item_id,
                "equipped_item_name": slot.equipped_item_name,
                "status": slot.status,
                "description": slot.description,
            }
            for slot in readiness.slots
        ],
    }


def _journal_entry(entry: QuestJournalEntry) -> dict[str, object]:
    tracer = entry.tracer
    first_incomplete = next((objective.id for objective in entry.objectives if not objective.complete), None)
    return {
        "id": entry.id,
        "title": entry.title,
        "summary": entry.summary,
        "giver": entry.giver,
        "tier": entry.tier,
        "status": entry.status.value,
        "reward_summary": entry.reward_summary,
        "canon_refs": list(entry.canon_refs),
        "objectives": [
            {
                "id": objective.id,
                "label": objective.label,
                "description": objective.description,
                "complete": objective.complete,
                "current": objective.id == first_incomplete,
            }
            for objective in entry.objectives
        ],
        "tracer": None
        if tracer is None
        else {
            "instruction": tracer.instruction,
            "target_room_id": tracer.target_room_id,
            "target_room_name": tracer.target_room_name,
            "recommended_command": tracer.recommended_command,
            "route": [
                {"direction": step.direction, "room_id": step.room_id, "room_name": step.room_name}
                for step in tracer.route
            ],
        },
    }
