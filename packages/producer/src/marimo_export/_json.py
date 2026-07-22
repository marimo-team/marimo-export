from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import TypeAlias

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
_MAX_SAFE_INTEGER = 2**53 - 1


def json_value(value: object, path: str = "value") -> JsonValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError(f"{path} integer must be within the JavaScript safe range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        if value.is_integer() and abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError(f"{path} integer must be within the JavaScript safe range")
        return value
    if isinstance(value, Mapping):
        result: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            result[key] = json_value(item, f"{path}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"{path} must be JSON-compatible, got {type(value).__name__}")


def json_object(value: object, path: str = "value") -> JsonObject:
    parsed = json_value(value, path)
    if not isinstance(parsed, dict):
        raise TypeError(f"{path} must be an object")
    return parsed


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_bytes(canonical_bytes(value))


def json_identity(value: object) -> object:
    parsed = json_value(value)
    if parsed is None:
        return ("null",)
    if isinstance(parsed, bool):
        return ("boolean", parsed)
    if isinstance(parsed, str):
        return ("string", parsed)
    if isinstance(parsed, (int, float)):
        number = int(parsed) if float(parsed).is_integer() else parsed
        return ("number", number)
    if isinstance(parsed, list):
        return ("array", tuple(json_identity(item) for item in parsed))
    return (
        "object",
        tuple((key, json_identity(item)) for key, item in sorted(parsed.items())),
    )
