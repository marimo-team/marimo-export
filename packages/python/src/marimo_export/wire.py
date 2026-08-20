"""Canonical portable JSON and prepared-state identity."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TypeAlias

from marimo_export._json import (
    JsonValue,
    canonical_bytes,
    decode_json,
    portable_json_object,
    portable_json_value,
    sha256_bytes,
)

FrozenJsonPrimitive: TypeAlias = str | int | float | bool | None
FrozenJsonValue: TypeAlias = (
    FrozenJsonPrimitive | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
)
FrozenJsonObject: TypeAlias = Mapping[str, FrozenJsonValue]


def portable_json(value: object, path: str = "value") -> JsonValue:
    """Return a detached JSON value accepted by browser state resolution."""

    return portable_json_value(value, path)


def canonical_json_bytes(value: object, path: str = "value") -> bytes:
    """Serialize portable JSON with the notebook export's canonical rules."""

    return canonical_bytes(portable_json(value, path))


def canonical_json_sha256(value: object, path: str = "value") -> str:
    """Return the lowercase SHA-256 for canonical portable JSON."""

    return sha256_bytes(canonical_json_bytes(value, path))


def parse_canonical_json(
    data: str | bytes | bytearray | memoryview,
    path: str = "value",
) -> JsonValue:
    """Parse exact canonical portable JSON into a detached mutable value."""

    if isinstance(data, str):
        try:
            encoded = data.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError(f"{path} must be UTF-8 JSON") from error
    else:
        try:
            source = memoryview(data)
        except (TypeError, ValueError) as error:
            raise TypeError(f"{path} must be JSON text or a contiguous byte buffer") from error
        with source:
            if not source.c_contiguous:
                raise TypeError(f"{path} byte buffer must be C-contiguous")
            with source.cast("B") as octets:
                encoded = bytes(octets)
    parsed = portable_json(decode_json(encoded, path), path)
    if canonical_bytes(parsed) != encoded:
        raise ValueError(f"{path} must be canonical portable JSON")
    return parsed


def state_fingerprint(inputs: Mapping[str, object]) -> str:
    """Return the durable identity for one complete portable input vector."""

    return sha256_bytes(canonical_bytes(portable_json_object(inputs, "state inputs")))


def _freeze_json(value: JsonValue) -> FrozenJsonValue:
    """Return a recursively immutable view of detached JSON."""

    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    return value


__all__ = [
    "FrozenJsonObject",
    "FrozenJsonValue",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "parse_canonical_json",
    "portable_json",
    "state_fingerprint",
]
