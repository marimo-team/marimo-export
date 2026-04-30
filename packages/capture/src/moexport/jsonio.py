"""JSON serialization helpers for bundle manifests and stable identities."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def write_json(path: Path, value: object) -> None:
    """Write human-readable JSON without reordering top-level story keys."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pretty_json(value) + "\n", encoding="utf-8")


def pretty_json(value: object) -> str:
    return json.dumps(jsonable(value), indent=2, allow_nan=False)


def canonical_json_bytes(value: object) -> bytes:
    """Serialize JSON for hashing, not for humans."""

    return json.dumps(
        jsonable(value),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def manifest_value(value: Any) -> Any:
    """Return a JSON-safe representation for manifest/debug fields.

    Scenario state values are usually JSON literals. Code-backed state values
    can evaluate to arbitrary Python objects; keep exports possible by recording
    their type and repr instead of pretending the object itself is portable.
    """

    if value is None or isinstance(value, str | int | bool):
        return value

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)

    if isinstance(value, list | tuple):
        return [manifest_value(item) for item in value]

    if isinstance(value, dict):
        return {
            str(key): manifest_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }

    return {
        "type": "python-object",
        "python_type": f"{type(value).__module__}.{type(value).__qualname__}",
        "repr": _preview_repr(value),
    }


def jsonable(value: Any) -> Any:
    """Convert Pydantic models inside dict/list structures to plain JSON values."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]

    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}

    return value


def _preview_repr(value: Any, width: int = 160) -> str:
    text = repr(value)
    return text if len(text) <= width else text[: width - 3] + "..."
