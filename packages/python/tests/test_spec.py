from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from marimo_export import ExportSpec, OutputSpec
from marimo_export.errors import SpecError
from marimo_export.exporters import altair, importable, parquet
from marimo_export.spec import SPEC_SCHEMA


def _value() -> dict[str, object]:
    return {
        "schema": SPEC_SCHEMA,
        "inputs": ["chart_width", "symbols_selector"],
        "states": {
            "baseline": {},
            "msft": {"symbols_selector": ["MSFT"]},
            "compact": {"chart_width": 480},
        },
        "outputs": {
            "prices": {
                "source": "df",
                "exporter": {
                    "name": "parquet.table",
                    "options": {
                        "compression": "snappy",
                        "filename": "prices.parquet",
                    },
                },
            },
            "chart": {
                "source": "symbols_chart",
                "exporter": "altair.vegalite",
            },
        },
    }


def test_programmatic_and_wire_construction_have_one_contract() -> None:
    programmatic = ExportSpec(
        inputs=("chart_width", "symbols_selector"),
        states={
            "baseline": {},
            "msft": {"symbols_selector": ["MSFT"]},
            "compact": {"chart_width": 480},
        },
        outputs={
            "prices": OutputSpec(
                source="df",
                exporter=parquet.table(filename="prices.parquet"),
            ),
            "chart": OutputSpec(
                source="symbols_chart",
                exporter=altair.vegalite(),
            ),
        },
    )
    decoded = ExportSpec.from_value(_value())

    assert programmatic == decoded
    assert programmatic.inputs == ("chart_width", "symbols_selector")
    assert tuple(programmatic.states) == ("baseline", "msft", "compact")
    assert tuple(programmatic.outputs) == ("prices", "chart")
    assert programmatic.outputs["prices"] == OutputSpec(
        source="df",
        exporter=parquet.table(filename="prices.parquet"),
    )
    assert programmatic.to_value() == _value()
    assert ExportSpec.from_value(programmatic) is programmatic


def test_spec_copies_and_freezes_authored_values() -> None:
    symbols = ["MSFT"]
    states = {"msft": {"symbols_selector": symbols}}
    spec = ExportSpec(
        inputs=("symbols_selector",),
        states=states,
        outputs={"chart": OutputSpec(source="chart")},
    )
    symbols.append("AAPL")
    states["msft"]["symbols_selector"] = ["NVDA"]

    assert isinstance(spec.states, MappingProxyType)
    assert isinstance(spec.states["msft"], MappingProxyType)
    assert spec.states["msft"]["symbols_selector"] == ("MSFT",)

    detached = spec.to_value()
    cast(dict[str, Any], detached["states"])["msft"]["symbols_selector"].append("GOOGL")
    assert spec.states["msft"]["symbols_selector"] == ("MSFT",)

    with pytest.raises(TypeError):
        cast(Any, spec.states)["new"] = {}
    with pytest.raises(TypeError):
        cast(Any, spec.states["msft"])["symbols_selector"] = ()


def test_json_schema_accepts_the_exact_wire_value() -> None:
    schema = ExportSpec.json_schema()

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_value())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value.update({"unexpected": True}), "spec_invalid"),
        (lambda value: value.update({"schema": "other"}), "spec_invalid"),
        (lambda value: value.update({"states": {}}), "spec_value_invalid"),
        (lambda value: value.update({"outputs": {}}), "spec_output_invalid"),
        (
            lambda value: cast(dict[str, Any], value["states"])["bad"].update({"interval": "1d"}),
            "spec_state_input_unknown",
        ),
        (
            lambda value: cast(list[str], value["inputs"]).append("chart_width"),
            "spec_invalid",
        ),
    ],
)
def test_spec_rejects_invalid_root_shapes(
    mutate: Any,
    code: str,
) -> None:
    value = _value()
    cast(dict[str, Any], value["states"])["bad"] = {}
    mutate(value)

    with pytest.raises(SpecError) as raised:
        ExportSpec.from_value(value)

    assert raised.value.code == code


@pytest.mark.parametrize(
    "source",
    ["", "not valid", "class", "a.b", "x" * 256],
)
def test_output_source_is_one_bounded_python_definition(source: str) -> None:
    with pytest.raises(SpecError) as raised:
        OutputSpec(source=source)

    assert raised.value.code == "spec_output_invalid"


def test_output_source_requires_a_string() -> None:
    with pytest.raises(TypeError):
        OutputSpec(source=cast(Any, 42))


def test_output_exporter_requires_a_descriptor() -> None:
    with pytest.raises(TypeError):
        OutputSpec(source="chart", exporter=cast(Any, "altair.vegalite"))


@pytest.mark.parametrize(
    "exporter",
    [
        "unknown",
        {"name": "altair.vegalite", "options": {"unexpected": True}},
        {"name": "acme.exports:encode", "options": {"bad-option": 1}},
    ],
)
def test_wire_exporters_are_validated_and_normalized(exporter: object) -> None:
    value = _value()
    cast(dict[str, Any], cast(dict[str, Any], value["outputs"])["chart"])["exporter"] = exporter

    with pytest.raises(SpecError) as raised:
        ExportSpec.from_value(value)

    assert raised.value.code == "spec_exporter_invalid"


def test_custom_importable_exporter_uses_the_same_wire_contract() -> None:
    spec = ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec(
                source="result",
                exporter=importable("acme.exports:encode", level=3),
            )
        },
    )

    assert spec.to_value()["outputs"] == {
        "summary": {
            "exporter": {
                "name": "acme.exports:encode",
                "options": {"level": 3},
            },
            "source": "result",
        }
    }


@pytest.mark.parametrize(
    "name",
    ["", " state", "state ", "state\n", "\x00state", "x" * 256],
)
def test_public_names_are_bounded_and_printable(name: str) -> None:
    value = _value()
    state = cast(dict[str, Any], value["states"]).pop("baseline")
    cast(dict[str, Any], value["states"])[name] = state

    with pytest.raises(SpecError):
        ExportSpec.from_value(value)


@pytest.mark.parametrize(
    "value",
    [
        2**53,
        -(2**53),
        math.inf,
        -math.inf,
        math.nan,
        datetime(2026, 7, 28),
        b"bytes",
    ],
)
def test_state_values_use_the_portable_input_grammar(value: object) -> None:
    spec = _value()
    cast(dict[str, Any], spec["states"])["bad"] = {"chart_width": value}

    with pytest.raises(SpecError) as raised:
        ExportSpec.from_value(spec)

    assert raised.value.code == "spec_value_invalid"


def test_json_file_uses_strict_duplicate_key_decoding(tmp_path: Path) -> None:
    path = tmp_path / "stocks.json"
    path.write_text(
        '{"schema":"marimo-export.spec.v1","inputs":[],"states":{"one":{},"one":{}},'
        '"outputs":{"result":{"source":"result"}}}',
        encoding="utf-8",
    )

    with pytest.raises(SpecError, match="duplicate key"):
        ExportSpec.from_file(path)


def test_yaml_file_decodes_the_same_contract(tmp_path: Path) -> None:
    path = tmp_path / "stocks.yaml"
    path.write_text(
        """
schema: marimo-export.spec.v1
inputs: [chart_width]
states:
  full: {}
  compact:
    chart_width: 480
outputs:
  chart:
    source: chart
    exporter:
      name: altair.png
      options:
        scale: 2
""".lstrip(),
        encoding="utf-8",
    )

    spec = ExportSpec.from_file(path)

    assert spec.inputs == ("chart_width",)
    assert spec.states["compact"]["chart_width"] == 480
    assert spec.outputs["chart"].source == "chart"
    assert spec.outputs["chart"].exporter == altair.png(scale=2)


@pytest.mark.parametrize(
    "body",
    [
        """
schema: marimo-export.spec.v1
inputs: []
states:
  base: &base {}
  copy: *base
outputs: {result: {source: result}}
""",
        """
schema: marimo-export.spec.v1
inputs: []
defaults: &defaults {result: {source: result}}
states: {base: {}}
outputs:
  <<: *defaults
""",
        """
schema: marimo-export.spec.v1
inputs: []
states: {base: {}, base: {}}
outputs: {result: {source: result}}
""",
    ],
)
def test_yaml_rejects_aliases_merges_and_duplicate_keys(tmp_path: Path, body: str) -> None:
    path = tmp_path / "stocks.yml"
    path.write_text(body.lstrip(), encoding="utf-8")

    with pytest.raises(SpecError):
        ExportSpec.from_file(path)


def test_file_format_is_selected_by_suffix(tmp_path: Path) -> None:
    path = tmp_path / "stocks"
    path.write_text(json.dumps(_value()), encoding="utf-8")

    with pytest.raises(SpecError, match=r"\.json"):
        ExportSpec.from_file(path)


def test_file_is_bounded_to_16_mib(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (16 * 1024 * 1024 + 1))

    with pytest.raises(SpecError, match="16777216"):
        ExportSpec.from_file(path)


def test_inputs_reject_a_bare_string() -> None:
    with pytest.raises(TypeError):
        ExportSpec(
            inputs=cast(Any, "chart_width"),
            states={"baseline": {}},
            outputs={"chart": OutputSpec(source="chart")},
        )


def test_programmatic_outputs_require_output_spec_values() -> None:
    with pytest.raises(TypeError):
        ExportSpec(
            inputs=(),
            states={"baseline": {}},
            outputs=cast(Any, {"chart": {"source": "chart"}}),
        )
