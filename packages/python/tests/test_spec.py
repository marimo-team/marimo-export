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


def _value() -> dict[str, Any]:
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

    assert ExportSpec.from_value(_value()) == programmatic
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


def test_json_schema_accepts_the_wire_contract() -> None:
    schema = ExportSpec.json_schema()

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_value())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False


def test_spec_rejects_invalid_root_contracts() -> None:
    unexpected = _value()
    unexpected["unexpected"] = True

    empty_states = _value()
    empty_states["states"] = {}

    unknown_input = _value()
    unknown_input["states"]["bad"] = {"interval": "1d"}

    duplicate_input = _value()
    duplicate_input["inputs"].append("chart_width")

    for value, code in (
        (unexpected, "spec_invalid"),
        (empty_states, "spec_value_invalid"),
        (unknown_input, "spec_state_input_unknown"),
        (duplicate_input, "spec_invalid"),
    ):
        with pytest.raises(SpecError) as raised:
            ExportSpec.from_value(value)
        assert raised.value.code == code


def test_output_sources_and_exporters_are_validated_at_construction() -> None:
    for source in ("", "a.b", "x" * 256):
        with pytest.raises(SpecError) as raised:
            OutputSpec(source=source)
        assert raised.value.code == "spec_output_invalid"

    value = _value()
    value["outputs"]["chart"]["exporter"] = {
        "name": "altair.vegalite",
        "options": {"unexpected": True},
    }
    with pytest.raises(SpecError) as raised:
        ExportSpec.from_value(value)
    assert raised.value.code == "spec_exporter_invalid"


def test_programmatic_constructor_rejects_ambiguous_shorthands() -> None:
    with pytest.raises(TypeError):
        ExportSpec(
            inputs=cast(Any, "chart_width"),
            states={"baseline": {}},
            outputs={"chart": OutputSpec(source="chart")},
        )

    with pytest.raises(TypeError):
        ExportSpec(
            inputs=(),
            states={"baseline": {}},
            outputs=cast(Any, {"chart": {"source": "chart"}}),
        )


def test_custom_importable_exporter_uses_the_wire_contract() -> None:
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


def test_public_names_and_state_values_use_the_portable_grammar() -> None:
    invalid_name = _value()
    state = invalid_name["states"].pop("baseline")
    invalid_name["states"]["state\n"] = state
    with pytest.raises(SpecError):
        ExportSpec.from_value(invalid_name)

    for authored in (2**53, math.nan, datetime(2026, 7, 28)):
        invalid_value = _value()
        invalid_value["states"]["bad"] = {"chart_width": authored}
        with pytest.raises(SpecError) as raised:
            ExportSpec.from_value(invalid_value)
        assert raised.value.code == "spec_value_invalid"


def test_json_file_rejects_duplicate_keys(tmp_path: Path) -> None:
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
    assert spec.outputs["chart"].exporter == altair.png(scale=2)


def test_yaml_rejects_aliases(tmp_path: Path) -> None:
    path = tmp_path / "stocks.yml"
    path.write_text(
        """
schema: marimo-export.spec.v1
inputs: []
states:
  base: &base {}
  copy: *base
outputs: {result: {source: result}}
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(SpecError):
        ExportSpec.from_file(path)


def test_spec_file_suffix_and_size_are_bounded(tmp_path: Path) -> None:
    suffixless = tmp_path / "stocks"
    suffixless.write_text(json.dumps(_value()), encoding="utf-8")
    with pytest.raises(SpecError, match=r"\.json"):
        ExportSpec.from_file(suffixless)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (16 * 1024 * 1024 + 1))
    with pytest.raises(SpecError, match="16777216"):
        ExportSpec.from_file(oversized)
