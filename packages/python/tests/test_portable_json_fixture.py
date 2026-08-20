from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from marimo_export._json import JsonObject, decode_json
from marimo_export.wire import canonical_json_bytes, portable_json

_FIXTURE = Path(__file__).parents[3] / "tests" / "fixtures" / "portable-json.json"


def test_portable_json_fixture_matches_javascript_contract() -> None:
    fixture = cast(JsonObject, json.loads(_FIXTURE.read_text(encoding="utf-8")))

    for case_value in cast(list[object], fixture["valid"]):
        case = cast(JsonObject, case_value)
        decoded = decode_json(cast(str, case["source"]), cast(str, case["name"]))
        expected = decode_json(cast(str, case["expected_source"]), cast(str, case["name"]))
        assert portable_json(decoded) == expected
        assert canonical_json_bytes(decoded) == canonical_json_bytes(expected)

    for case_value in cast(list[object], fixture["invalid"]):
        case = cast(JsonObject, case_value)
        with pytest.raises((TypeError, ValueError)):
            portable_json(decode_json(cast(str, case["source"]), cast(str, case["name"])))


def test_python_portable_json_accepts_aliases_and_rejects_cycles() -> None:
    shared = {"value": 1}
    converted = cast(JsonObject, portable_json({"first": shared, "second": shared}))

    assert converted == {"first": {"value": 1}, "second": {"value": 1}}
    assert converted["first"] is not converted["second"]

    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError, match="maximum JSON nesting depth"):
        portable_json(cycle)
