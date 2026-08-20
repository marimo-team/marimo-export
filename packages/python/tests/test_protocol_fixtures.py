from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Literal, cast

import pytest
from marimo_export._json import JsonObject, canonical_bytes, json_value
from marimo_export.errors import IntegrityError, NotebookExportError
from marimo_export.index import ExportIndex
from marimo_export.reader import _validate_snapshot
from marimo_export.wire import state_fingerprint

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
HTTP_MODULE_URL_CASES = cast(
    list[JsonObject],
    json.loads((FIXTURES / "export" / "http-module-urls.json").read_text()),
)


def test_canonical_json_fixtures_match_python_producer() -> None:
    cases = cast(
        list[JsonObject],
        json.loads((FIXTURES / "canonical-json" / "cases.json").read_text()),
    )

    for case in cases:
        assert canonical_bytes(case["value"]).decode() == case["canonical"]


def test_input_name_fixtures_match_python_export_reader_policy() -> None:
    cases = json.loads((FIXTURES / "export" / "input-names.json").read_text())
    fixture = cast(
        JsonObject,
        json.loads((FIXTURES / "export" / "scalar-index.json").read_text()),
    )

    for case in cases:
        value = case["value"]
        wire = cast(JsonObject, copy.deepcopy(fixture))
        states = cast(JsonObject, wire["states"])
        aliases = cast(JsonObject, wire["aliases"])
        fingerprints: dict[str, str] = {}
        renamed_states: JsonObject = {}
        for fingerprint, state_value in states.items():
            state = cast(JsonObject, state_value)
            old_inputs = cast(JsonObject, state["inputs"])
            renamed_inputs = {value: old_inputs["choice"]}
            renamed_fingerprint = state_fingerprint(renamed_inputs)
            fingerprints[fingerprint] = renamed_fingerprint
            state["inputs"] = renamed_inputs
            renamed_states[renamed_fingerprint] = state
        wire["inputs"] = [value]
        wire["control_bindings"] = {"fixture-control": {"input": value, "path": []}}
        wire["states"] = renamed_states
        wire["aliases"] = {
            alias: fingerprints[cast(str, target)] for alias, target in aliases.items()
        }
        wire["default_state"] = fingerprints[cast(str, wire["default_state"])]

        if case["valid"]:
            assert ExportIndex.from_value(wire).inputs == (value,)
        else:
            with pytest.raises(NotebookExportError):
                ExportIndex.from_value(wire)


def test_scalar_export_fixture_matches_python_v1_reader() -> None:
    encoded = (FIXTURES / "export" / "scalar-index.json").read_bytes().removesuffix(b"\n")
    index = ExportIndex.from_bytes(encoded)

    assert set(index.aliases) == {"one", "two"}
    assert index.aliases["one"] in index.states
    assert index.aliases["two"] in index.states
    assert index.default_state == index.aliases["one"]
    assert index.spec_sha256 == "d" * 64
    assert index.control_bindings == {}


def test_projection_records_match_python_snapshot_contracts() -> None:
    records = cast(
        JsonObject,
        json.loads((FIXTURES / "export" / "projection-records.json").read_text()),
    )

    assert json_value(records["json"]) == records["json"]
    for name, schema in (
        ("output", "marimo.output.v1"),
        ("cell", "marimo.cell.v1"),
    ):
        _validate_snapshot(canonical_bytes(records[name]), schema)


@pytest.mark.parametrize(
    "case",
    HTTP_MODULE_URL_CASES,
    ids=[cast(str, case["name"]) for case in HTTP_MODULE_URL_CASES],
)
def test_http_module_url_fixtures_match_python_snapshot_contracts(case: JsonObject) -> None:
    records = cast(
        JsonObject,
        json.loads((FIXTURES / "export" / "projection-records.json").read_text()),
    )
    output = cast(JsonObject, copy.deepcopy(records["output"]))
    resources = cast(JsonObject, output["resources"])
    notifications = cast(list[JsonObject], resources["modelNotifications"])
    message = cast(JsonObject, notifications[1]["message"])
    esm_spec = cast(JsonObject, message["esm_spec"])
    esm_spec["url"] = case["url"]

    if case["valid"]:
        _validate_snapshot(canonical_bytes(output), "marimo.output.v1")
    else:
        with pytest.raises(IntegrityError, match="snapshot is invalid"):
            _validate_snapshot(canonical_bytes(output), "marimo.output.v1")


def test_malformed_projection_records_match_python_snapshot_contracts() -> None:
    records = cast(
        JsonObject,
        json.loads((FIXTURES / "export" / "projection-records.json").read_text()),
    )
    cases = cast(
        list[JsonObject],
        json.loads((FIXTURES / "export" / "malformed-projection-records.json").read_text()),
    )

    for case in cases:
        record = cast(str, case["record"])
        snapshot = cast(JsonObject, copy.deepcopy(records[record]))
        _apply_mutation(snapshot, case)
        schema = cast(str, snapshot["schema"])
        with pytest.raises(IntegrityError, match="snapshot is invalid"):
            _validate_snapshot(
                canonical_bytes(snapshot),
                cast(Literal["marimo.output.v1", "marimo.cell.v1"], schema),
            )


def test_projection_records_reject_invalid_ui_ownership() -> None:
    records = cast(
        JsonObject,
        json.loads((FIXTURES / "export" / "projection-records.json").read_text()),
    )
    output = cast(JsonObject, copy.deepcopy(records["output"]))
    resources = cast(JsonObject, output["resources"])
    functions = cast(JsonObject, resources["functions"])
    object_id = next(iter(functions))
    resources["functions"] = {object_id: []}
    resources["uiValues"] = {}

    with pytest.raises(IntegrityError, match="snapshot is invalid"):
        _validate_snapshot(canonical_bytes(output), "marimo.output.v1")


def test_projection_records_reject_live_python_functions() -> None:
    records = cast(
        JsonObject,
        json.loads((FIXTURES / "export" / "projection-records.json").read_text()),
    )
    output = cast(JsonObject, copy.deepcopy(records["output"]))
    resources = cast(JsonObject, output["resources"])
    functions = cast(JsonObject, resources["functions"])
    object_id = next(iter(functions))
    functions[object_id] = ["validate"]

    with pytest.raises(IntegrityError, match="snapshot is invalid"):
        _validate_snapshot(canonical_bytes(output), "marimo.output.v1")


def test_projection_records_reject_cross_projection_model_ids() -> None:
    records = cast(
        JsonObject,
        json.loads((FIXTURES / "export" / "projection-records.json").read_text()),
    )
    output = cast(JsonObject, copy.deepcopy(records["output"]))
    resources = cast(JsonObject, output["resources"])
    resources["modelNotifications"] = [
        {
            "op": "model-lifecycle",
            "model_id": f"projection-{'d' * 64}-model-0",
            "message": {"method": "close"},
        }
    ]

    with pytest.raises(IntegrityError, match="snapshot is invalid"):
        _validate_snapshot(canonical_bytes(output), "marimo.output.v1")


def _apply_mutation(snapshot: JsonObject, case: JsonObject) -> None:
    path = case["path"]
    if not isinstance(path, list) or not path:
        raise AssertionError(f"{case['name']}: mutation path must be nonempty")
    target: object = snapshot
    for token in path[:-1]:
        if (isinstance(token, str) and isinstance(target, dict)) or (
            isinstance(token, int) and not isinstance(token, bool) and isinstance(target, list)
        ):
            target = target[token]
        else:
            raise AssertionError(f"{case['name']}: mutation path is invalid")
    final = path[-1]
    operation = case["operation"]
    if isinstance(final, str) and isinstance(target, dict):
        if operation == "set":
            target[final] = copy.deepcopy(case["value"])
        elif operation == "delete":
            del target[final]
        else:
            raise AssertionError(f"{case['name']}: mutation operation is invalid")
        return
    if isinstance(final, int) and not isinstance(final, bool) and isinstance(target, list):
        if operation == "set":
            target[final] = copy.deepcopy(case["value"])
        elif operation == "delete":
            del target[final]
        else:
            raise AssertionError(f"{case['name']}: mutation operation is invalid")
        return
    raise AssertionError(f"{case['name']}: mutation target is invalid")
