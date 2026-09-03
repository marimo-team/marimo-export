from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from marimo_export import ExportSpec, OutputSpec, StateSpace
from marimo_export.errors import SpecError
from marimo_export.spec import STATE_SPACE_SCHEMA


def _value() -> dict[str, Any]:
    return {
        "schema": STATE_SPACE_SCHEMA,
        "default_state": "baseline",
        "states": {"baseline": {"metric": "CO2", "threshold": 0.5}},
        "matrix": {
            "threshold": [0.1, 0.2],
            "metric": ["CO2", "Light"],
        },
    }


def test_state_space_expands_a_deterministic_cartesian_matrix() -> None:
    state_space = StateSpace.from_value(_value())

    assert state_space.default_state == "baseline"
    assert state_space.states == {
        "baseline": {"metric": "CO2", "threshold": 0.5},
        "matrix-000000": {"metric": "CO2", "threshold": 0.1},
        "matrix-000001": {"metric": "CO2", "threshold": 0.2},
        "matrix-000002": {"metric": "Light", "threshold": 0.1},
        "matrix-000003": {"metric": "Light", "threshold": 0.2},
    }
    assert isinstance(state_space.states, MappingProxyType)
    assert StateSpace.from_value(state_space.to_value()) == state_space


def test_programmatic_state_space_uses_the_wire_contract() -> None:
    state_space = StateSpace(
        default_state="matrix-000000",
        matrix={"threshold": [0.25, 0.5, 0.75]},
    )

    assert tuple(state_space.states) == (
        "matrix-000000",
        "matrix-000001",
        "matrix-000002",
    )
    assert state_space.states["matrix-000001"] == {"threshold": 0.5}
    assert state_space.to_value() == {
        "schema": STATE_SPACE_SCHEMA,
        "default_state": "matrix-000000",
        "states": {
            "matrix-000000": {"threshold": 0.25},
            "matrix-000001": {"threshold": 0.5},
            "matrix-000002": {"threshold": 0.75},
        },
        "matrix": {},
    }


def test_export_spec_combines_a_state_space_with_outputs() -> None:
    state_space = StateSpace(
        default_state="matrix-000000",
        matrix={"threshold": [0.25, 0.5]},
    )

    spec = ExportSpec.from_state_space(
        state_space,
        outputs={"summary": OutputSpec.json("report.summary")},
    )

    assert spec.default_state == state_space.default_state
    assert spec.states == state_space.states
    assert tuple(spec.outputs) == ("summary",)


def test_state_space_json_schema_accepts_explicit_and_matrix_states() -> None:
    schema = StateSpace.json_schema()

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_value())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(cast(dict[str, Any], schema["properties"])) == {
        "schema",
        "default_state",
        "states",
        "matrix",
    }


def test_state_space_file_and_yaml_text_share_validation(tmp_path: Path) -> None:
    source = tmp_path / "states.yaml"
    source.write_text(
        f"""schema: {STATE_SPACE_SCHEMA}
default_state: matrix-000000
matrix:
  mode: [first, second]
""",
        encoding="utf-8",
    )

    from_file = StateSpace.from_file(source)
    from_yaml = StateSpace.from_yaml(source.read_text(encoding="utf-8"), source=str(source))

    assert from_file == from_yaml
    assert from_file.states["matrix-000001"] == {"mode": "second"}


@pytest.mark.parametrize(
    "value",
    [
        {
            "schema": STATE_SPACE_SCHEMA,
            "default_state": "baseline",
            "states": {},
            "matrix": {},
        },
        {
            "schema": STATE_SPACE_SCHEMA,
            "default_state": "missing",
            "states": {"baseline": {}},
            "matrix": {},
        },
        {
            "schema": STATE_SPACE_SCHEMA,
            "default_state": "matrix-000000",
            "states": {},
            "matrix": {"mode": ["first", "first"]},
        },
        {
            "schema": STATE_SPACE_SCHEMA,
            "default_state": "matrix-000000",
            "states": {"matrix-000000": {}},
            "matrix": {"mode": ["first"]},
        },
    ],
)
def test_state_space_rejects_invalid_relations(value: dict[str, object]) -> None:
    with pytest.raises(SpecError) as raised:
        StateSpace.from_value(value)

    assert raised.value.code == "spec_value_invalid"


def test_state_space_bounds_cartesian_expansion() -> None:
    with pytest.raises(SpecError, match="exceeds 10000 states") as raised:
        StateSpace(
            default_state="matrix-000000",
            matrix={"left": list(range(101)), "right": list(range(100))},
        )

    assert raised.value.code == "spec_value_invalid"


def test_state_space_yaml_rejects_aliases_and_duplicate_keys() -> None:
    for text in (
        f"""schema: {STATE_SPACE_SCHEMA}
default_state: baseline
states: &states
  baseline: {{}}
matrix: *states
""",
        f"""schema: {STATE_SPACE_SCHEMA}
default_state: baseline
states: {{baseline: {{}}}}
states: {{other: {{}}}}
""",
    ):
        with pytest.raises(SpecError) as raised:
            StateSpace.from_yaml(text)
        assert raised.value.code == "spec_invalid"
