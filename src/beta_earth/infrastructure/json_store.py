from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from beta_earth.application.ports import PlayerDataError, RevisionConflict
from beta_earth.domain.identity import require_canonical_player_id
from beta_earth.domain.models import Inventory, ItemStack, PlayerState, QuestLog, SetupStage, Stats

_SAVE_SCHEMA_VERSION = "4.0"
_SUPPORTED_SAVE_SCHEMAS = {"1.0", "2.0", "3.0", "4.0"}
_MAX_SAVE_BYTES = 256 * 1024
_MAX_BACKUPS_PER_PLAYER = 3
_MAX_JOURNAL_BYTES = 256 * 1024
_MAX_ARRAY = 512
_CURRENT_KEYS = {
    "schema_version", "player_id", "display_name", "stage", "room_id", "identity", "stats", "flags",
    "visited_rooms", "quest_log", "cred", "inventory", "completed_transactions", "last_message", "revision",
}
_SCHEMA_KEYS = {
    "1.0": _CURRENT_KEYS - {"visited_rooms", "quest_log", "cred", "inventory", "completed_transactions"},
    "2.0": _CURRENT_KEYS - {"cred", "inventory", "completed_transactions"},
    "3.0": _CURRENT_KEYS | {"wallet_cred", "transactions"},
    "4.0": _CURRENT_KEYS,
}


class JsonPlayerRepository:
    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._backup_dir = state_dir / "_migration_backups"
        self._journal_path = state_dir / "_migration_journal.jsonl"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def load(self, player_id: str) -> PlayerState | None:
        path = self._path(player_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                raw_bytes = path.read_bytes()
                if len(raw_bytes) > _MAX_SAVE_BYTES:
                    raise PlayerDataError(f"Player save exceeds {_MAX_SAVE_BYTES} bytes: {path.name}")
                raw = json.loads(raw_bytes.decode("utf-8"))
                migrated, from_schema = self._migrate(raw, player_id)
                state = self._decode_current(migrated, player_id)
                if from_schema != _SAVE_SCHEMA_VERSION:
                    self._backup_before_migration(path, raw_bytes, from_schema)
                    # Write the canonical encoded model, not the transitional migration mapping.
                    # This prevents whitespace/order aliases from surviving a successful migration.
                    self._atomic_write_json(path, self._encode(state))
                    self._append_migration_journal(player_id, from_schema, _SAVE_SCHEMA_VERSION)
                return state
            except PlayerDataError:
                raise
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
                raise PlayerDataError(f"Player save could not be loaded safely: {path.name}") from exc

    def save(self, state: PlayerState, expected_revision: int) -> PlayerState:
        _validate_identity_stage(state.stage, state.identity)
        path = self._path(state.player_id)
        with self._lock:
            current = self.load(state.player_id)
            current_revision = current.revision if current is not None else -1
            if current_revision != expected_revision:
                raise RevisionConflict(f"expected revision {expected_revision}; found {current_revision}")
            persisted = replace(state, revision=current_revision + 1)
            self._atomic_write_json(path, self._encode(persisted))
            return persisted

    def _path(self, player_id: str) -> Path:
        try:
            safe = require_canonical_player_id(player_id)
        except ValueError as exc:
            raise PlayerDataError(str(exc)) from exc
        return self._state_dir / f"{safe}.json"

    @staticmethod
    def _encode(state: PlayerState) -> dict[str, object]:
        return {
            "schema_version": _SAVE_SCHEMA_VERSION,
            "player_id": state.player_id,
            "display_name": state.display_name,
            "stage": state.stage.value,
            "room_id": state.room_id,
            "identity": state.identity,
            "stats": state.stats.as_dict(),
            "flags": list(state.flags),
            "visited_rooms": list(state.visited_rooms),
            "quest_log": {"active": list(state.quest_log.active), "completed": list(state.quest_log.completed)},
            "cred": state.cred,
            "inventory": [
                {"item_id": stack.item_id, "quantity": stack.quantity}
                for stack in state.inventory.stacks
            ],
            "completed_transactions": list(state.completed_transactions),
            "last_message": state.last_message,
            "revision": state.revision,
        }

    @classmethod
    def _migrate(cls, raw: Any, fallback_player_id: str) -> tuple[dict[str, Any], str]:
        if not isinstance(raw, dict):
            raise PlayerDataError("Player save root must be a JSON object")
        schema = _required_string(raw.get("schema_version", "1.0"), "schema_version")
        if schema not in _SUPPORTED_SAVE_SCHEMAS:
            raise PlayerDataError(f"Unsupported player save schema: {schema}")
        unknown = sorted(set(raw) - _SCHEMA_KEYS[schema])
        if unknown:
            raise PlayerDataError(f"Player save contains unknown fields: {', '.join(unknown)}")
        original_schema = schema
        value = dict(raw)
        while schema != _SAVE_SCHEMA_VERSION:
            if schema == "1.0":
                value = cls._migrate_1_to_2(value)
                schema = "2.0"
            elif schema == "2.0":
                value = cls._migrate_2_to_3(value)
                schema = "3.0"
            elif schema == "3.0":
                value = cls._migrate_3_to_4(value)
                schema = "4.0"
            else:  # defensive; supported set and loop make this unreachable
                raise PlayerDataError(f"No migration path from player save schema: {schema}")
        value["schema_version"] = _SAVE_SCHEMA_VERSION
        # Validate identity before writing any migration result.
        stored_player_id = _required_string(value.get("player_id", fallback_player_id), "player_id")
        if stored_player_id != fallback_player_id:
            raise PlayerDataError("Player save identity does not match its requested profile")
        cls._decode_current(value, fallback_player_id)
        return value, original_schema

    @staticmethod
    def _migrate_1_to_2(value: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(value)
        stage = _required_string(migrated.get("stage"), "stage")
        room_id = _required_string(migrated.get("room_id"), "room_id")
        migrated["visited_rooms"] = [room_id] if stage == SetupStage.ACTIVE.value else []
        migrated["quest_log"] = {"active": [], "completed": []}
        migrated["schema_version"] = "2.0"
        return migrated

    @staticmethod
    def _migrate_2_to_3(value: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(value)
        completed = []
        quest_log = migrated.get("quest_log")
        if isinstance(quest_log, dict) and isinstance(quest_log.get("completed"), list):
            completed = quest_log["completed"]
        # Tier 2 was introduced after schema 2. Existing players who already completed
        # Caroline's route receive the new reward exactly once during this one-way migration.
        if "caroline_route_reading" in completed:
            migrated["cred"] = 5
            migrated["inventory"] = [{"item_id": "route_tracer_calibration", "quantity": 1}]
        else:
            migrated["cred"] = 0
            migrated["inventory"] = []
        migrated["completed_transactions"] = []
        migrated["schema_version"] = "3.0"
        return migrated

    @staticmethod
    def _migrate_3_to_4(value: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(value)
        if "cred" in migrated and "wallet_cred" in migrated and migrated["cred"] != migrated["wallet_cred"]:
            raise PlayerDataError("schema 3 save has conflicting cred and wallet_cred values")
        if "cred" not in migrated and "wallet_cred" in migrated:
            migrated["cred"] = migrated["wallet_cred"]
        migrated.pop("wallet_cred", None)
        if (
            "completed_transactions" in migrated
            and "transactions" in migrated
            and migrated["completed_transactions"] != migrated["transactions"]
        ):
            raise PlayerDataError("schema 3 save has conflicting completed_transactions and transactions values")
        if "completed_transactions" not in migrated and "transactions" in migrated:
            migrated["completed_transactions"] = migrated["transactions"]
        migrated.pop("transactions", None)
        inventory = migrated.get("inventory", [])
        if isinstance(inventory, dict):
            inventory = [
                {"item_id": item_id, "quantity": quantity}
                for item_id, quantity in sorted(inventory.items())
            ]
        migrated["inventory"] = inventory
        migrated.setdefault("cred", 0)
        migrated.setdefault("completed_transactions", [])
        migrated["schema_version"] = "4.0"
        return migrated

    @staticmethod
    def _decode_current(raw: dict[str, Any], fallback_player_id: str) -> PlayerState:
        unknown = sorted(set(raw) - _CURRENT_KEYS)
        missing = sorted(_CURRENT_KEYS - set(raw))
        if unknown:
            raise PlayerDataError(f"Player save contains unknown fields: {', '.join(unknown)}")
        if missing:
            raise PlayerDataError(f"Player save is missing fields: {', '.join(missing)}")
        if _required_string(raw["schema_version"], "schema_version") != _SAVE_SCHEMA_VERSION:
            raise PlayerDataError("Player save was not migrated to the current schema")
        stored_player_id = _required_string(raw["player_id"], "player_id")
        if stored_player_id != fallback_player_id:
            raise PlayerDataError("Player save identity does not match its requested profile")
        stage = SetupStage(_required_string(raw["stage"], "stage"))
        room_id = _required_string(raw["room_id"], "room_id")
        visited_rooms = _string_tuple(raw["visited_rooms"], "visited_rooms")
        if stage == SetupStage.ACTIVE and room_id not in visited_rooms:
            raise PlayerDataError("Active player save current room is not present in visited_rooms")
        quest_raw = raw["quest_log"]
        if not isinstance(quest_raw, dict):
            raise PlayerDataError("quest_log must be an object")
        unknown_quest_keys = sorted(set(quest_raw) - {"active", "completed"})
        missing_quest_keys = sorted({"active", "completed"} - set(quest_raw))
        if unknown_quest_keys or missing_quest_keys:
            raise PlayerDataError("quest_log must contain only active and completed arrays")
        identity = raw["identity"]
        if identity is not None and not isinstance(identity, str):
            raise PlayerDataError("identity must be a string or null")
        _validate_identity_stage(stage, identity)
        inventory_raw = raw["inventory"]
        if not isinstance(inventory_raw, list) or len(inventory_raw) > 128:
            raise PlayerDataError("inventory must be an array with at most 128 stacks")
        stacks: list[ItemStack] = []
        for index, stack in enumerate(inventory_raw):
            if not isinstance(stack, dict) or set(stack) != {"item_id", "quantity"}:
                raise PlayerDataError(f"inventory stack {index + 1} must contain item_id and quantity")
            stacks.append(
                ItemStack(
                    _required_string(stack["item_id"], f"inventory[{index}].item_id"),
                    _positive_int(stack["quantity"], f"inventory[{index}].quantity", maximum=9999),
                )
            )
        return PlayerState(
            player_id=fallback_player_id,
            display_name=_required_bounded_string(raw["display_name"], "display_name", maximum=40),
            stage=stage,
            room_id=room_id,
            identity=identity,
            stats=Stats.from_dict(raw["stats"]),
            flags=_string_tuple(raw["flags"], "flags"),
            visited_rooms=visited_rooms,
            quest_log=QuestLog(
                active=_string_tuple(quest_raw["active"], "quest_log.active"),
                completed=_string_tuple(quest_raw["completed"], "quest_log.completed"),
            ),
            cred=_nonnegative_int(raw["cred"], "cred", maximum=1_000_000_000),
            inventory=Inventory(tuple(stacks)),
            completed_transactions=_string_tuple(raw["completed_transactions"], "completed_transactions"),
            last_message=_optional_string(raw["last_message"], "last_message", maximum=16_384),
            revision=_nonnegative_int(raw["revision"], "revision"),
        )

    def _backup_before_migration(self, path: Path, raw_bytes: bytes, from_schema: str) -> None:
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self._backup_dir / f"{path.stem}.schema-{from_schema}.{timestamp}.{secrets.token_hex(3)}.json"
        self._atomic_write_bytes(backup, raw_bytes)
        backups = sorted(self._backup_dir.glob(f"{path.stem}.schema-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in backups[_MAX_BACKUPS_PER_PLAYER:]:
            stale.unlink(missing_ok=True)

    def _append_migration_journal(self, player_id: str, from_schema: str, to_schema: str) -> None:
        event = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "profile": self._path(player_id).stem,
            "from_schema": from_schema,
            "to_schema": to_schema,
            "status": "migrated",
        }
        existing = b""
        if self._journal_path.exists():
            existing = self._journal_path.read_bytes()[-(_MAX_JOURNAL_BYTES // 2):]
            if existing and not existing.startswith(b"{"):
                newline = existing.find(b"\n")
                existing = existing[newline + 1:] if newline >= 0 else b""
        payload = existing + (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self._atomic_write_bytes(self._journal_path, payload[-_MAX_JOURNAL_BYTES:])

    def _atomic_write_json(self, path: Path, payload: dict[str, object]) -> None:
        encoded = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        if len(encoded) > _MAX_SAVE_BYTES:
            raise PlayerDataError(f"Player save would exceed {_MAX_SAVE_BYTES} bytes")
        self._atomic_write_bytes(path, encoded)

    @staticmethod
    def _atomic_write_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            with temp.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
            os.replace(temp, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
            except (AttributeError, OSError):
                return
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
        except Exception:
            temp.unlink(missing_ok=True)
            raise


class SystemRandomSource:
    def __init__(self) -> None:
        self._random = secrets.SystemRandom()

    def roll_stats(self) -> Stats:
        return Stats(*(self._random.randint(8, 14) for _ in range(5)))


def _validate_identity_stage(stage: SetupStage, identity: str | None) -> None:
    if stage == SetupStage.IDENTITY and identity is not None:
        raise PlayerDataError("identity must be null during identity selection")
    if stage != SetupStage.IDENTITY and identity is None:
        raise PlayerDataError("identity is required after identity selection")


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerDataError(f"{field_name} must be a non-empty string")
    if len(value) > 16_384:
        raise PlayerDataError(f"{field_name} is too long")
    return value.strip()


def _required_bounded_string(value: Any, field_name: str, *, maximum: int) -> str:
    cleaned = _required_string(value, field_name)
    if len(value) > maximum:
        raise PlayerDataError(f"{field_name} exceeds {maximum} characters")
    return cleaned


def _optional_string(value: Any, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise PlayerDataError(f"{field_name} must be a string")
    if len(value) > maximum:
        raise PlayerDataError(f"{field_name} exceeds {maximum} characters")
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > _MAX_ARRAY:
        raise PlayerDataError(f"{field_name} must be an array with at most {_MAX_ARRAY} entries")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 160 for item in value):
        raise PlayerDataError(f"{field_name} must contain non-empty strings no longer than 160 characters")
    cleaned = tuple(item.strip() for item in value)
    if len(cleaned) != len(set(cleaned)):
        raise PlayerDataError(f"{field_name} contains duplicates")
    return cleaned


def _nonnegative_int(value: Any, field_name: str, *, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PlayerDataError(f"{field_name} must be a non-negative integer")
    if maximum is not None and value > maximum:
        raise PlayerDataError(f"{field_name} exceeds {maximum}")
    return value


def _positive_int(value: Any, field_name: str, *, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise PlayerDataError(f"{field_name} must be an integer from 1 to {maximum}")
    return value
