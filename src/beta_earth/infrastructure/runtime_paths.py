from __future__ import annotations

import os
import stat
from pathlib import Path


class RuntimePathError(RuntimeError):
    """Raised when a runtime directory could escape or redirect outside the project."""


def _is_reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and attributes & marker)


def _absolute(path: Path) -> Path:
    """Return a normalized absolute path without following links."""

    return Path(os.path.abspath(os.fspath(path)))


def _inspect_existing_directory(path: Path) -> None:
    if not os.path.lexists(path):
        return
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise RuntimePathError(f"Could not inspect runtime path {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or _is_reparse_point(info):
        raise RuntimePathError(f"Runtime path may not be a symlink or reparse point: {path}")
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimePathError(f"Runtime path exists but is not a directory: {path}")


def ensure_project_local_directory(project_root: Path, directory: Path) -> Path:
    """Create a project-local directory without following descendant redirects.

    The project root itself may be reached through a user-selected mount/junction, but
    every directory below it must be a real directory. This prevents state, logs,
    diagnostics, exports, or temporary files from being redirected into another
    program's folder through a symlink or Windows reparse point.
    """

    root = _absolute(project_root)
    target = _absolute(directory)
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise RuntimePathError(f"Runtime directory is outside the project root: {target}") from exc
    if not relative.parts:
        raise RuntimePathError("The project root itself is not a runtime directory")

    if not root.exists() or not root.is_dir():
        raise RuntimePathError(f"Project root is unavailable: {root}")

    current = root
    for part in relative.parts:
        current = current / part
        _inspect_existing_directory(current)

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimePathError(f"Could not create runtime directory {target}: {exc}") from exc

    current = root
    for part in relative.parts:
        current = current / part
        _inspect_existing_directory(current)

    resolved_root = root.resolve(strict=True)
    resolved_target = target.resolve(strict=True)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimePathError(f"Runtime directory resolves outside the project root: {target}") from exc
    return target
