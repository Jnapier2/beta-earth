from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


class JsonDocumentError(ValueError):
    """Raised when a bounded local JSON document cannot be read safely."""


def load_bounded_json(
    path: Path,
    *,
    context: str,
    maximum_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
) -> Any:
    if not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool) or maximum_bytes < 1:
        raise ValueError("maximum_bytes must be a positive integer")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise JsonDocumentError(f"{context} could not be read: {path.name}") from exc
    if len(payload) > maximum_bytes:
        raise JsonDocumentError(f"{context} exceeds {maximum_bytes} bytes: {path.name}")
    try:
        return json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise JsonDocumentError(f"{context} must be UTF-8: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise JsonDocumentError(
            f"{context} contains invalid JSON at line {exc.lineno}, column {exc.colno}: {path.name}"
        ) from exc
