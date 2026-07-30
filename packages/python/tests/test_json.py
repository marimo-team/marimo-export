from __future__ import annotations

import math

import pytest
from marimo_export._json import (
    JsonValue,
    decode_json,
    decode_json_object,
    json_equal,
    json_value,
)


def test_json_equal_preserves_json_types_and_number_semantics() -> None:
    left: JsonValue = {"second": [1, -0.0], "first": {"enabled": True}}
    right: JsonValue = {"first": {"enabled": True}, "second": [1.0, 0]}

    assert json_equal(left, right)
    assert not json_equal({"enabled": True}, {"enabled": 1})
    assert not json_equal({"value": None}, {"value": "null"})


def test_json_value_detaches_values_and_enforces_the_value_limit() -> None:
    source: dict[str, list[object]] = {"items": [1.0, True]}
    decoded = json_value(source)
    source["items"].append(None)

    assert isinstance(decoded, dict)
    assert isinstance(decoded["items"], list)
    assert decoded == {"items": [1.0, True]}
    assert type(decoded["items"][0]) is float

    with pytest.raises(ValueError, match="maximum JSON value count"):
        json_value({"first": 0, "second": 1}, max_values=4)
    with pytest.raises(ValueError, match="maximum JSON value count"):
        decode_json('{"first":0,"second":1}', max_values=4)


def test_decode_json_accepts_supported_text_and_buffer_inputs() -> None:
    expected = {"value": 1}
    for encoded in (
        '\n{"value":1}\t',
        b'\n{"value":1}\t',
        bytearray(b'\n{"value":1}\t'),
        memoryview(b'\n{"value":1}\t'),
    ):
        assert decode_json_object(encoded) == expected

    source = b'{"value":1}'
    backing = bytearray(len(source) * 2)
    backing[::2] = source
    with pytest.raises(TypeError, match="C-contiguous"):
        decode_json(memoryview(backing)[::2])


def test_decode_json_preserves_number_identity() -> None:
    decoded = decode_json('{"integer":1,"float":1.0,"negative_zero":-0,"zero_exponent":0e9999}')

    assert isinstance(decoded, dict)
    assert type(decoded["integer"]) is int
    assert type(decoded["float"]) is float
    assert decoded["float"] == 1.0
    assert type(decoded["negative_zero"]) is float
    assert math.copysign(1.0, decoded["negative_zero"]) == -1.0
    assert decoded["zero_exponent"] == 0.0


def test_decode_json_rejects_numbers_that_lose_portable_identity() -> None:
    for encoded, message in (
        ("1.00000000000000001", "loses its fractional component"),
        ("9007199254740992.0", "JavaScript safe range"),
        ("1" + "0" * 309 + ".1", "non-finite number"),
    ):
        with pytest.raises(ValueError, match=message):
            decode_json(encoded, "document")


def test_decode_json_rejects_malformed_and_duplicate_objects() -> None:
    with pytest.raises(ValueError, match="document is invalid JSON"):
        decode_json('{"value":[1,}', "document")

    with pytest.raises(ValueError, match="contains duplicate key 'a'"):
        decode_json(r'{"a":1,"\u0061":2}', "document")


def test_json_values_reject_unicode_surrogates() -> None:
    with pytest.raises(ValueError, match="Unicode scalar values"):
        decode_json(r'"\ud800"')
    with pytest.raises(ValueError, match="Unicode scalar values"):
        json_value("\ud800")


def test_json_diagnostics_are_bounded_and_escape_controls() -> None:
    key = "\x1b" + "x" * 2_000

    with pytest.raises(TypeError) as raised:
        json_value({key: object()}, "document")

    message = str(raised.value)
    assert r"\x1b" in message
    assert "\x1b" not in message
    assert len(message) < 1024
