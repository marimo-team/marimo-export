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
from marimo_export.spec import (
    SPEC_SCHEMA,
    CellSource,
    ExportSource,
    JsonSource,
    NativeSource,
    RenderedOutputSource,
)


def _value() -> dict[str, Any]:
    return {
        "schema": SPEC_SCHEMA,
        "default_state": "baseline",
        "states": {
            "baseline": {},
            "msft": {"symbols_selector": ["MSFT"]},
            "compact": {"chart_width": 480},
        },
        "outputs": {
            "prices": {
                "source": {"kind": "export", "selector": "df"},
                "exporter": {
                    "dependencies": [],
                    "name": "parquet.table",
                    "options": {
                        "compression": "snappy",
                        "filename": "prices.parquet",
                    },
                },
            },
            "chart": {
                "source": {"kind": "export", "selector": "symbols_chart"},
                "exporter": "altair.vegalite",
            },
            "summary": {"source": {"kind": "json", "selector": "summary"}},
            "table": {"source": {"kind": "native", "selector": "table"}},
        },
    }


def test_programmatic_and_wire_construction_have_one_contract() -> None:
    programmatic = ExportSpec(
        default_state="baseline",
        states={
            "baseline": {},
            "msft": {"symbols_selector": ["MSFT"]},
            "compact": {"chart_width": 480},
        },
        outputs={
            "prices": OutputSpec.export(
                "df",
                parquet.table(filename="prices.parquet"),
            ),
            "chart": OutputSpec.export("symbols_chart", altair.vegalite()),
            "summary": OutputSpec.json("summary"),
            "table": OutputSpec.native("table"),
        },
    )

    assert ExportSpec.from_value(_value()) == programmatic
    assert programmatic.to_value() == _value()
    assert ExportSpec.from_value(programmatic) is programmatic


def test_spec_copies_and_freezes_authored_values() -> None:
    symbols = ["MSFT"]
    states = {"msft": {"symbols_selector": symbols}}
    spec = ExportSpec(
        default_state="msft",
        states=states,
        outputs={"chart": OutputSpec.json("chart")},
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
    assert set(cast(list[str], schema["required"])) == {
        "schema",
        "default_state",
        "states",
        "outputs",
    }
    assert set(cast(dict[str, Any], schema["properties"])) == {
        "schema",
        "default_state",
        "states",
        "outputs",
    }
    definitions = cast(dict[str, Any], schema["$defs"])
    exporter_schema = cast(dict[str, Any], definitions["exporter"])
    assert set(exporter_schema["required"]) == {"name", "options", "dependencies"}


def test_spec_canonicalizes_state_and_output_order() -> None:
    spec = ExportSpec(
        default_state="z-state",
        states={"z-state": {"choice": "z"}, "a-state": {"choice": "a"}},
        outputs={
            "z-output": OutputSpec.json("choice"),
            "a-output": OutputSpec.output("choice"),
        },
    )

    assert tuple(spec.states) == ("a-state", "z-state")
    assert tuple(spec.outputs) == ("a-output", "z-output")
    assert ExportSpec.from_value(spec.to_value()) == spec


def test_spec_rejects_invalid_root_contracts() -> None:
    unexpected = _value()
    unexpected["unexpected"] = True

    empty_states = _value()
    empty_states["states"] = {}

    missing_default = _value()
    del missing_default["default_state"]

    unknown_default = _value()
    unknown_default["default_state"] = "missing"

    previous_shape = _value()
    previous_shape["inputs"] = ["chart_width", "symbols_selector"]

    previous_schema = _value()
    previous_schema["schema"] = "marimo-export.spec.v1"

    for value, code in (
        (unexpected, "spec_invalid"),
        (empty_states, "spec_value_invalid"),
        (missing_default, "spec_invalid"),
        (unknown_default, "spec_invalid"),
        (previous_shape, "spec_invalid"),
        (previous_schema, "spec_invalid"),
    ):
        with pytest.raises(SpecError) as raised:
            ExportSpec.from_value(value)
        assert raised.value.code == code


def test_output_sources_and_exporters_are_validated_at_construction() -> None:
    assert not hasattr(OutputSpec, "value")

    for source in ("", "a()", "a['key']", "x" * 2_049):
        with pytest.raises(SpecError) as raised:
            OutputSpec.json(source)
        assert raised.value.code == "spec_output_invalid"

    json_source = OutputSpec.json('report.rows[0]["value"]').source
    native_source = OutputSpec.native("report.table").source
    export_source = OutputSpec.export("report.chart", altair.vegalite()).source
    output_source = OutputSpec.output("report.chart").source
    named_cell = OutputSpec.cell("summary").source
    identified_cell = OutputSpec.cell(id="runtime-cell").source
    assert isinstance(json_source, JsonSource) and json_source.selector.path
    assert isinstance(native_source, NativeSource)
    assert isinstance(export_source, ExportSource)
    assert isinstance(output_source, RenderedOutputSource)
    assert output_source.selector.root == "report"
    assert isinstance(named_cell, CellSource) and named_cell.value == "summary"
    assert isinstance(identified_cell, CellSource) and identified_cell.value == "runtime-cell"
    with pytest.raises(TypeError):
        OutputSpec.cell()
    with pytest.raises(TypeError):
        OutputSpec.cell("summary", id="runtime-cell")
    with pytest.raises(TypeError):
        OutputSpec(source=cast(Any, "chart"))
    with pytest.raises(SpecError):
        OutputSpec.json("chart").__class__(
            source=OutputSpec.json("chart").source,
            exporter=altair.vegalite(),
        )
    with pytest.raises(SpecError):
        OutputSpec(source=OutputSpec.export("chart", altair.vegalite()).source)

    value = _value()
    value["outputs"]["chart"]["exporter"] = {
        "dependencies": [],
        "name": "altair.vegalite",
        "options": {"unexpected": True},
    }
    with pytest.raises(SpecError) as raised:
        ExportSpec.from_value(value)
    assert raised.value.code == "spec_exporter_invalid"


def test_programmatic_constructor_rejects_ambiguous_shorthands() -> None:
    with pytest.raises(TypeError):
        cast(Any, ExportSpec)(
            states={"baseline": {}},
            outputs={"chart": OutputSpec.json("chart")},
        )

    with pytest.raises(TypeError):
        ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs=cast(Any, {"chart": {"source": "chart"}}),
        )


def test_custom_importable_exporter_uses_the_wire_contract() -> None:
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec.export(
                "result",
                importable(
                    "acme.exports:encode",
                    options={"level": 3},
                    dependencies=("acme.models",),
                ),
            )
        },
    )

    assert spec.to_value()["outputs"] == {
        "summary": {
            "exporter": {
                "dependencies": ["acme.models"],
                "name": "acme.exports:encode",
                "options": {"level": 3},
            },
            "source": {"kind": "export", "selector": "result"},
        }
    }


def test_exporter_wire_requires_sorted_explicit_dependencies() -> None:
    missing = _value()
    missing["outputs"]["prices"]["exporter"].pop("dependencies")

    unsorted = _value()
    unsorted["outputs"]["prices"]["exporter"] = {
        "dependencies": ["acme.transforms", "acme.models"],
        "name": "acme.exports:encode",
        "options": {},
    }

    invalid = _value()
    invalid["outputs"]["prices"]["exporter"] = {
        "dependencies": ["acme:models"],
        "name": "acme.exports:encode",
        "options": {},
    }

    custom_shorthand = _value()
    custom_shorthand["outputs"]["prices"]["exporter"] = "acme.exports:encode"

    for value in (missing, unsorted, invalid, custom_shorthand):
        with pytest.raises(SpecError) as raised:
            ExportSpec.from_value(value)
        assert raised.value.code == "spec_exporter_invalid"


def test_export_names_and_state_values_use_the_portable_grammar() -> None:
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
        '{"schema":"marimo-export.spec.v2","default_state":"one",'
        '"states":{"one":{},"one":{}},'
        '"outputs":{"result":{"source":{"kind":"json","selector":"result"}}}}',
        encoding="utf-8",
    )

    with pytest.raises(SpecError, match="duplicate key"):
        ExportSpec.from_file(path)


def test_json_file_decodes_custom_exporter_dependencies(tmp_path: Path) -> None:
    value = _value()
    value["outputs"]["prices"]["exporter"] = {
        "dependencies": ["acme.models"],
        "name": "acme.exports:encode",
        "options": {"level": 3},
    }
    path = tmp_path / "stocks.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    exporter = ExportSpec.from_file(path).outputs["prices"].exporter

    assert exporter is not None
    assert exporter.options == {"level": 3}
    assert exporter.dependencies == ("acme.models",)


def test_yaml_file_decodes_the_same_contract(tmp_path: Path) -> None:
    path = tmp_path / "stocks.yaml"
    path.write_text(
        """
schema: marimo-export.spec.v2
default_state: full
states:
  full: {}
  compact:
    chart_width: 480
outputs:
  chart:
    source: {kind: export, selector: chart}
    exporter:
      name: altair.png
      options:
        scale: 2
      dependencies: []
""".lstrip(),
        encoding="utf-8",
    )

    spec = ExportSpec.from_file(path)

    assert spec.default_state == "full"
    assert spec.states["compact"]["chart_width"] == 480
    assert spec.outputs["chart"].exporter == altair.png(scale=2)


def test_yaml_rejects_aliases(tmp_path: Path) -> None:
    path = tmp_path / "stocks.yml"
    path.write_text(
        """
schema: marimo-export.spec.v2
default_state: base
states:
  base: &base {}
  copy: *base
outputs: {result: {source: {kind: json, selector: result}}}
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
