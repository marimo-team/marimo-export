"""Validation primitives shared by durable export records."""

from __future__ import annotations

import keyword
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath, PureWindowsPath

from marimo_export._json import JsonObject, json_object, json_string

MAX_NAME_BYTES = 255
MAX_PROVENANCE_BYTES = 2_048
MAX_CONTROL_ID_BYTES = 1_024
SHA256 = re.compile(r"[0-9a-f]{64}")
_EDGE_WHITESPACE = frozenset(
    "\u0009\u000a\u000b\u000c\u000d"
    "\u001c\u001d\u001e\u001f\u0020"
    "\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def object_value(value: object, path: str) -> JsonObject:
    return json_object(value, path)


def exact_fields(value: Mapping[str, object], expected: set[str], path: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        facts = []
        if missing:
            facts.append(f"missing fields: {', '.join(missing)}")
        if extra:
            facts.append(f"unknown fields: {', '.join(extra)}")
        raise ValueError(f"{path} does not accept this shape ({'; '.join(facts)})")


def name_array(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be an array")
    return tuple(json_string(item, f"{path} item") for item in value)


def ordered_names(
    values: Sequence[object],
    path: str,
    *,
    identifier: bool,
    nonempty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{path} must be a tuple")
    parsed = tuple(
        identifier_name(value, f"{path} item") if identifier else export_name(value, f"{path} item")
        for value in values
    )
    if nonempty and not parsed:
        raise ValueError(f"{path} must contain at least one name")
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{path} must contain unique names")
    return parsed


def identifier_name(value: object, path: str) -> str:
    name = json_string(value, path)
    if (
        not name.isidentifier()
        or keyword.iskeyword(name)
        or len(name.encode("utf-8")) > MAX_NAME_BYTES
    ):
        raise ValueError(f"{path} must be a bounded non-keyword Python identifier")
    return name


def opaque_name(value: object, path: str) -> str:
    name = json_string(value, path)
    if not name or len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise ValueError(
            f"{path} must be a non-empty UTF-8 string of at most {MAX_NAME_BYTES} bytes"
        )
    return name


def export_name(value: object, path: str) -> str:
    return bounded_printable(value, path, MAX_NAME_BYTES)


def edge_whitespace(value: str) -> bool:
    return bool(value) and (value[0] in _EDGE_WHITESPACE or value[-1] in _EDGE_WHITESPACE)


def bounded_printable(value: object, path: str, maximum_bytes: int) -> str:
    text = json_string(value, path)
    if (
        not text
        or edge_whitespace(text)
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
        or len(text.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(
            f"{path} must be a non-empty printable string of at most {maximum_bytes} UTF-8 bytes"
        )
    return text


def opaque_store_reference(value: object, path: str) -> str:
    text = bounded_printable(value, path, MAX_PROVENANCE_BYTES)
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute():
        raise ValueError(f"{path} must be a store-relative opaque identifier")
    return text


def digest(value: object, path: str) -> str:
    text = json_string(value, path)
    if SHA256.fullmatch(text) is None:
        raise ValueError(f"{path} must be a lowercase SHA-256 digest")
    return text


__all__ = [
    "MAX_CONTROL_ID_BYTES",
    "MAX_NAME_BYTES",
    "MAX_PROVENANCE_BYTES",
    "bounded_printable",
    "digest",
    "edge_whitespace",
    "exact_fields",
    "export_name",
    "identifier_name",
    "name_array",
    "object_value",
    "opaque_name",
    "opaque_store_reference",
    "ordered_names",
]
