from __future__ import annotations

import json
import math
import tracemalloc
from collections.abc import Callable
from typing import SupportsIndex

import pytest
from marimo_export import _json
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


def test_decode_json_rejects_large_value_sets_before_json_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = "[" + "0," * (_json._MAX_JSON_VALUES - 1) + "0]"

    def fail_loads(*args: object, **kwargs: object) -> object:
        raise AssertionError("json.loads must not run after the lexical bound is exceeded")

    monkeypatch.setattr(_json.json, "loads", fail_loads)

    with pytest.raises(ValueError, match="maximum JSON value count"):
        decode_json(encoded, "document")


def test_json_value_applies_the_same_bound_to_in_memory_trees() -> None:
    with pytest.raises(ValueError, match="maximum JSON value count"):
        json_value([None] * _json._MAX_JSON_VALUES, "document")


def test_json_value_preserves_safe_integral_floats() -> None:
    decoded = json_value({"value": 1.0})

    assert isinstance(decoded, dict)
    assert type(decoded["value"]) is float


def test_json_count_includes_object_keys_in_text_and_memory() -> None:
    with pytest.raises(ValueError, match="maximum JSON value count"):
        decode_json('{"first":0,"second":1}', "document", max_values=4)
    with pytest.raises(ValueError, match="maximum JSON value count"):
        json_value({"first": 0, "second": 1}, "document", max_values=4)


def test_decode_json_preserves_the_existing_depth_contract() -> None:
    accepted = "[" * (_json._MAX_JSON_DEPTH + 1) + "]" * (_json._MAX_JSON_DEPTH + 1)
    rejected = "[" * (_json._MAX_JSON_DEPTH + 2) + "]" * (_json._MAX_JSON_DEPTH + 2)

    assert isinstance(decode_json(accepted), list)
    with pytest.raises(ValueError, match="maximum JSON nesting depth"):
        decode_json(rejected, "document")


def test_decode_json_ignores_structural_content_inside_escaped_strings() -> None:
    content = '[{"quoted":"\\""}],true,false,null,123' * 25_001
    encoded = json.dumps({"content": content})

    assert decode_json(encoded) == {"content": content}


@pytest.mark.parametrize(
    "encoded",
    [
        b'\n{"value":1}\t',
        bytearray(b'\n{"value":1}\t'),
        memoryview(b'\n{"value":1}\t'),
        memoryview(bytearray(b'\n{"value":1}\t')),
    ],
)
def test_decode_json_accepts_contiguous_byte_buffers(
    encoded: bytes | bytearray | memoryview,
) -> None:
    assert decode_json_object(encoded) == {"value": 1}


def test_decode_json_rejects_noncontiguous_memoryviews() -> None:
    encoded = b'{"value":1}'
    backing = bytearray(len(encoded) * 2)
    backing[::2] = encoded

    with pytest.raises(TypeError, match="C-contiguous"):
        decode_json(memoryview(backing)[::2], "document")


def test_decode_json_releases_writable_buffers_after_utf8_errors() -> None:
    encoded = bytearray(b"\t\xff\n")

    with pytest.raises(ValueError, match="document must be UTF-8 JSON"):
        decode_json(encoded, "document")
    encoded.extend(b"still writable")


def test_decode_json_trims_large_buffer_before_utf8_decode() -> None:
    padding = 16 * 1024 * 1024
    payload = b'{"value":1}'

    tracemalloc.start()
    try:
        encoded = bytearray(b" ") * (2 * padding + len(payload))
        encoded[padding : padding + len(payload)] = payload
        assert decode_json_object(encoded) == {"value": 1}
        too_long = b"0." + b"1" * (_json._MAX_JSON_NUMBER_CHARS - 1)
        encoded[padding : padding + len(too_long)] = too_long
        with pytest.raises(ValueError, match="JSON number longer than"):
            decode_json(encoded)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(encoded) >= 32 * 1024 * 1024
    assert peak < len(encoded) + 2 * 1024 * 1024


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        ("1.0", 1.0),
        ("1e0", 1.0),
        ("1.5e1", 15.0),
    ],
)
def test_decode_json_preserves_exact_integral_float_lexemes(
    encoded: str,
    expected: float,
) -> None:
    decoded = decode_json(encoded)

    assert decoded == expected
    assert type(decoded) is float


@pytest.mark.parametrize("encoded", ["-0", "-0.0", "-0e0"])
def test_decode_json_preserves_negative_zero(encoded: str) -> None:
    decoded = decode_json(encoded)

    assert decoded == 0.0
    assert type(decoded) is float
    assert math.copysign(1.0, decoded) == -1.0


def test_decode_json_preserves_nested_negative_zero_and_integer_types() -> None:
    decoded = decode_json('{"integer_zero":-0,"float_zero":-0.0,"nested":[-0],"ordinary":1}')

    assert isinstance(decoded, dict)
    nested = decoded["nested"]
    assert isinstance(nested, list)
    for value in (decoded["integer_zero"], decoded["float_zero"], nested[0]):
        assert type(value) is float
        assert math.copysign(1.0, value) == -1.0
    assert type(decoded["ordinary"]) is int


def test_decode_json_enforces_the_number_character_boundary() -> None:
    accepted = "0." + "1" * (_json._MAX_JSON_NUMBER_CHARS - 2)
    rejected = accepted + "1"

    assert isinstance(decode_json(accepted), float)
    with pytest.raises(ValueError, match="JSON number longer than 1024 characters"):
        decode_json(rejected, "document")


@pytest.mark.parametrize(
    "encoded",
    [
        "0e9999999999999999999",
        "0e-9999999999999999999",
        "-0e9999999999999999999",
        "-0e-9999999999999999999",
    ],
)
def test_decode_json_accepts_zero_with_large_exponents(encoded: str) -> None:
    decoded = decode_json(encoded)

    assert decoded == 0.0
    assert type(decoded) is float


def test_decode_json_translates_decimal_exponent_failures() -> None:
    with pytest.raises(ValueError):
        decode_json("1e999999999999999999", "document")


@pytest.mark.parametrize(
    "encoded",
    [
        "1.00000000000000001",
        "9007199254740990.5",
        "9007199254740991.1",
        "1e-324",
    ],
)
def test_decode_json_rejects_fractional_lexemes_that_become_integral(
    encoded: str,
) -> None:
    with pytest.raises(ValueError, match="loses its fractional component"):
        decode_json(encoded, "document")


def test_decode_json_rejects_unsafe_and_nonfinite_float_conversions() -> None:
    with pytest.raises(ValueError, match="JavaScript safe range"):
        decode_json("9007199254740992.0", "document")

    nonintegral_overflow = "1" + "0" * 309 + ".1"
    with pytest.raises(ValueError, match="non-finite number"):
        decode_json(nonintegral_overflow, "document")


@pytest.mark.parametrize(
    "encoded",
    [
        '{"value":"unterminated',
        '{"value":[1,}',
    ],
)
def test_decode_json_keeps_invalid_json_errors(encoded: str) -> None:
    with pytest.raises(ValueError, match="document is invalid JSON"):
        decode_json(encoded, "document")


def test_decode_json_stops_preflight_at_a_known_invalid_prefix() -> None:
    class CountingText(str):
        reads = 0

        def __getitem__(
            self,
            key: SupportsIndex | slice[SupportsIndex | None],
        ) -> str:
            type(self).reads += 1
            return super().__getitem__(key)

    encoded = CountingText("?" + "x" * (4 * 1024 * 1024 - 1))

    with pytest.raises(ValueError, match="document is invalid JSON"):
        decode_json(encoded, "document")

    assert CountingText.reads < 100


def test_decode_json_does_not_retain_malformed_input_through_parser_errors() -> None:
    encoded = b"?" + b"x" * (4 * 1024 * 1024 - 1)

    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="document is invalid JSON") as raised:
            decode_json(encoded, "document")
        current, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert len(str(raised.value)) < 1024
    assert current < 1024 * 1024


def test_decode_json_reports_malformed_strings_before_later_duplicates() -> None:
    encoded = '{"bad":"\x01","x":1,"x":2}'

    with pytest.raises(ValueError, match="document is invalid JSON"):
        decode_json(encoded, "document")


@pytest.mark.parametrize(
    "decode",
    [
        lambda: decode_json(r'"\ud800"'),
        lambda: json_value("\ud800"),
    ],
)
def test_json_values_reject_unicode_surrogates(decode: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match="Unicode scalar values"):
        decode()


def test_json_diagnostics_bound_long_nested_key_memory() -> None:
    value: object = object()
    for _ in range(_json._MAX_JSON_DEPTH):
        value = {"x" * 1024: value}

    tracemalloc.start()
    try:
        with pytest.raises(TypeError) as raised:
            json_value(value, "document")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 8 * 1024 * 1024
    assert len(str(raised.value)) < 1024


def test_json_diagnostics_escape_control_characters() -> None:
    with pytest.raises(TypeError) as raised:
        json_value({"\x1b[31m": object()}, "document")

    assert "\x1b" not in str(raised.value)
    assert r"\x1b" in str(raised.value)


def test_json_diagnostics_bound_duplicate_keys_without_retaining_parser_values() -> None:
    key = "x" * (4 * 1024 * 1024)
    encoded = f'{{"{key}":0,"{key}":1}}'

    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="contains duplicate key") as raised:
            decode_json(encoded, "document")
        current, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(str(raised.value)) < 1024
    assert current < 1024 * 1024


def test_decode_json_detects_duplicate_keys_across_escape_spellings() -> None:
    with pytest.raises(ValueError, match="contains duplicate key 'a'"):
        decode_json(r'{"a":1,"\u0061":2}', "document")


def test_decode_json_rejects_duplicate_before_parsing_later_large_value() -> None:
    tail = "y" * (16 * 1024 * 1024)
    encoded = f'{{"x":1,"x":2,"later":"{tail}"}}'

    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="contains duplicate key 'x'"):
            decode_json(encoded, "document")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 1024 * 1024
