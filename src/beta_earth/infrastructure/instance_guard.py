from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any


class SingleInstanceGuard:
    """Cross-platform, folder-local single-instance guard with Windows mutex support."""

    def __init__(
        self,
        state_dir: Path,
        *,
        name: str = "BetaEarthCleanRebuild",
        lock_id: str = "runtime",
    ) -> None:
        self._state_dir = state_dir.resolve()
        self._state_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(character for character in name if character.isalnum() or character in "_.-")[:80]
        safe_lock_id = "".join(character for character in lock_id if character.isalnum() or character in "_.-")[:40]
        self._name = safe_name or "BetaEarthCleanRebuild"
        self._lock_id = safe_lock_id or "runtime"
        scope = hashlib.sha256(str(self._state_dir).casefold().encode("utf-8")).hexdigest()[:16]
        self._mutex_name = f"Local\\{self._name}-{self._lock_id}-{scope}"
        self._lock_path = self._state_dir / f"{self._lock_id}.lock"
        self._metadata_path = self._state_dir / f"{self._lock_id}_instance.json"
        self._token = secrets.token_hex(8)
        self._fd: int | None = None
        self._mutex: int | None = None
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    @property
    def mutex_name(self) -> str:
        return self._mutex_name

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    @property
    def metadata_path(self) -> Path:
        return self._metadata_path

    def acquire(self) -> bool:
        if self._acquired:
            return True
        if os.name == "nt":
            self._acquired = self._acquire_windows_mutex()
        else:
            self._acquired = self._acquire_lockfile()
        if self._acquired:
            self._clear_stale_metadata()
        return self._acquired

    def publish(self, *, url: str, run_id: str, pid: int) -> None:
        if not self._acquired:
            raise RuntimeError("Cannot publish runtime metadata without owning the instance guard")
        if _validated_loopback_url(url) is None:
            raise ValueError("Runtime URL must be an http loopback URL")
        payload = {
            "url": url,
            "run_id": run_id,
            "pid": pid,
            "process_start": _process_start_signature(pid),
            "token": self._token,
        }
        _atomic_write_json(self._metadata_path, payload)
        if os.name != "nt":
            _atomic_write_json(self._lock_path, payload)

    def existing_url(self) -> str | None:
        try:
            value = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        return _validated_loopback_url(value.get("url"))

    def wait_for_existing_url(self, *, timeout: float = 3.0, poll_interval: float = 0.1) -> str | None:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ValueError("timeout must be a non-negative number")
        if not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool) or poll_interval <= 0:
            raise ValueError("poll_interval must be a positive number")
        deadline = time.monotonic() + float(timeout)
        while True:
            existing = self.existing_url()
            if existing is not None:
                return existing
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(float(poll_interval), remaining))

    def release(self) -> None:
        if not self._acquired:
            return
        if os.name == "nt":
            self._release_windows_mutex()
        else:
            self._release_lockfile()
        try:
            metadata = json.loads(self._metadata_path.read_text(encoding="utf-8"))
            if isinstance(metadata, dict) and metadata.get("token") == self._token:
                self._metadata_path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
        self._acquired = False

    def __enter__(self) -> "SingleInstanceGuard":
        if not self.acquire():
            raise RuntimeError("Another Beta Earth clean rebuild instance is already running")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()

    def _clear_stale_metadata(self) -> None:
        try:
            self._metadata_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _acquire_windows_mutex(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, self._mutex_name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        already_exists = ctypes.get_last_error() == 183
        if already_exists:
            kernel32.CloseHandle(handle)
            return False
        self._mutex = int(handle)
        return True

    def _release_windows_mutex(self) -> None:
        if self._mutex is None:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(self._mutex)
        self._mutex = None

    def _acquire_lockfile(self) -> bool:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        for _ in range(2):
            try:
                self._fd = os.open(self._lock_path, flags, 0o600)
                pid = os.getpid()
                payload = {
                    "pid": pid,
                    "process_start": _process_start_signature(pid),
                    "token": self._token,
                }
                os.write(self._fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
                os.fsync(self._fd)
                return True
            except FileExistsError:
                if self._lock_owner_is_alive():
                    return False
                self._lock_path.unlink(missing_ok=True)
        return False

    def _lock_owner_is_alive(self) -> bool:
        try:
            value = json.loads(self._lock_path.read_text(encoding="utf-8"))
            pid = value.get("pid") if isinstance(value, dict) else None
            stored_start = value.get("process_start") if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        current_start = _process_start_signature(pid)
        if isinstance(stored_start, str) and stored_start:
            # A live reused PID is not the original owner. If the platform cannot expose
            # a comparable signature, fail closed and leave the lock in place.
            return current_start is None or current_start == stored_start
        return True

    def _release_lockfile(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            value = json.loads(self._lock_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("token") == self._token:
                self._lock_path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass


def _process_start_signature(pid: int) -> str | None:
    """Return a stable process-start signature where the OS exposes one cheaply.

    Linux /proc field 22 is the process start time in clock ticks since boot. Windows uses
    a kernel mutex for liveness, while other POSIX systems safely fall back to PID-only.
    """

    if os.name == "nt" or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        text = stat_path.read_text(encoding="utf-8")
        closing = text.rfind(")")
        if closing < 0:
            return None
        fields = text[closing + 2 :].split()
        # fields starts at proc field 3 (state), so field 22 is index 19.
        start_ticks = fields[19]
    except (OSError, IndexError, UnicodeDecodeError):
        return None
    return f"linux-proc-start:{start_ticks}"


def _validated_loopback_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 300:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or port is None:
        return None
    if parsed.username or parsed.password:
        return None
    return value


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
