"""Value previews and cache fingerprints.

Evaluation traces need readable previews, while batch execution needs stable
cache keys for primitive containers and identity keys for expensive runtime
objects. This module centralizes those rules so planning, execution, and
metadata report values consistently.
"""

from __future__ import annotations

from typing import Any


def value_preview(value: Any, width: int = 120) -> str:
    text = repr(value)
    return text if len(text) <= width else text[: width - 3] + "..."


def _sort_fingerprints(values: list[Any]) -> tuple[Any, ...]:
    return tuple(sorted(values, key=repr))


def value_fingerprint(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str | bytes):
        return ("primitive", value)

    if isinstance(value, tuple):
        return ("tuple", tuple(value_fingerprint(item) for item in value))

    if isinstance(value, list):
        return ("list", tuple(value_fingerprint(item) for item in value))

    if isinstance(value, set):
        return ("set", _sort_fingerprints([value_fingerprint(item) for item in value]))

    if isinstance(value, dict):
        items = [
            (value_fingerprint(key), value_fingerprint(item))
            for key, item in value.items()
        ]
        return ("dict", _sort_fingerprints(items))

    # Avoid expensive dataframe/model hashing. Object identity is the cheap,
    # correct cache key inside one running kernel call.
    return ("object", type(value).__module__, type(value).__qualname__, id(value))
