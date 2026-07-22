from __future__ import annotations

from typing import Any, cast

import pytest
from marimo_export.plan import PLAN_SCHEMA, decode_plan
from marimo_export.projection.synthetic_cells import projection_binding


def complete_plan() -> dict[str, Any]:
    return {
        "schema": PLAN_SCHEMA,
        "inputs": {
            "symbol": {"ui": "symbol_picker", "default": "MSFT"},
            "width": {"definition": "chart_width", "default": 800},
        },
        "scenarios": [
            {"id": "microsoft", "inputs": {}},
            {"id": "coreweave", "inputs": {"symbol": "CRWV"}},
        ],
        "outputs": {
            "summary": {
                "source": {"expression": "public_summary(frame)"},
                "formats": {"json": {"exporter": "json"}},
            },
            "chart": {
                "source": "chart_spec",
                "formats": {"vega": {"exporter": "vegalite"}},
            },
        },
    }


def test_plan_resolves_complete_public_input_vectors() -> None:
    plan = decode_plan(complete_plan())

    assert plan.scenarios[0].inputs == {"symbol": "MSFT", "width": 800}
    assert plan.scenarios[1].inputs == {"symbol": "CRWV", "width": 800}
    assert plan.outputs[0].source.wire() == {"expression": "public_summary(frame)"}
    assert plan.outputs[1].source.wire() == "chart_spec"
    assert plan.outputs[1].formats[0].exporter.wire() == {
        "ref": "marimo_export.projection.exporters.vegalite:vegalite",
        "version": "vegalite.v1",
    }
    assert plan.inputs[0].wire() == {"ui": "symbol_picker", "default": "MSFT"}
    assert plan.wire()["schema"] == PLAN_SCHEMA
    assert len(plan.sha256) == 64


def test_plan_requires_explicit_schema() -> None:
    value = complete_plan()
    del value["schema"]

    with pytest.raises(ValueError, match="schema is required"):
        decode_plan(value)


def test_plan_rejects_duplicate_resolved_vectors() -> None:
    value = complete_plan()
    value["scenarios"] = [
        {"id": "one", "inputs": {}},
        {"id": "two", "inputs": {"symbol": "MSFT", "width": 800}},
    ]

    with pytest.raises(ValueError, match="unique input vectors"):
        decode_plan(value)


@pytest.mark.parametrize(("first", "second"), [(1, 1.0), (0, -0.0)])
def test_scenario_identity_matches_javascript_number_semantics(
    first: int | float, second: int | float
) -> None:
    value = complete_plan()
    value["scenarios"] = [
        {"id": "one", "inputs": {"width": first}},
        {"id": "two", "inputs": {"width": second}},
    ]

    with pytest.raises(ValueError, match="unique input vectors"):
        decode_plan(value)


def test_booleans_remain_distinct_from_numbers() -> None:
    value = complete_plan()
    value["scenarios"] = [
        {"id": "boolean", "inputs": {"width": True}},
        {"id": "number", "inputs": {"width": 1}},
    ]

    assert len(decode_plan(value).scenarios) == 2


@pytest.mark.parametrize("number", [2**53, float(2**53)])
def test_plan_rejects_integral_numbers_outside_javascript_safe_range(
    number: int | float,
) -> None:
    value = complete_plan()
    value["scenarios"] = [{"id": "unsafe", "inputs": {"width": number}}]

    with pytest.raises(ValueError, match="JavaScript safe range"):
        decode_plan(value)


def test_plan_rejects_missing_and_unknown_inputs() -> None:
    value = complete_plan()
    inputs = value["inputs"]
    assert isinstance(inputs, dict)
    width = inputs["width"]
    assert isinstance(width, dict)
    del width["default"]

    with pytest.raises(ValueError, match="missing: width"):
        decode_plan(value)

    value = complete_plan()
    value["scenarios"] = [{"id": "bad", "inputs": {"unknown": 1}}]
    with pytest.raises(ValueError, match="does not accept: unknown"):
        decode_plan(value)


def test_input_binding_is_flat_and_selects_one_target_kind() -> None:
    value = complete_plan()
    inputs = value["inputs"]
    assert isinstance(inputs, dict)
    inputs["symbol"] = {"bind": {"ui": "symbol_picker"}, "default": "MSFT"}

    with pytest.raises(ValueError, match="does not accept: bind"):
        decode_plan(value)

    value = complete_plan()
    inputs = value["inputs"]
    assert isinstance(inputs, dict)
    inputs["symbol"] = {
        "ui": "symbol_picker",
        "definition": "symbol",
        "default": "MSFT",
    }

    with pytest.raises(ValueError, match="exactly one of definition or ui"):
        decode_plan(value)


def test_custom_exporters_use_notebook_definitions_or_versioned_refs() -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    outputs["summary"] = {
        "source": "frame",
        "formats": {
            "network": {"exporter": {"definition": "export_network"}},
            "other": {"exporter": {"ref": "project.exporters:network", "version": "2"}},
        },
    }

    plan = decode_plan(value)
    formats = plan.outputs[0].formats

    assert formats[0].exporter.wire() == {"definition": "export_network"}
    assert formats[1].exporter.wire() == {
        "ref": "project.exporters:network",
        "version": "2",
    }


def test_importable_exporter_requires_a_version() -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    outputs["summary"] = {
        "source": "frame",
        "formats": {"custom": {"exporter": {"ref": "project.exporters:network"}}},
    }

    with pytest.raises(ValueError, match="ref plus version"):
        decode_plan(value)


def test_explicit_null_exporter_version_is_rejected() -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    outputs["summary"] = {
        "source": "frame",
        "formats": {"custom": {"exporter": {"definition": "export_network", "version": None}}},
    }

    with pytest.raises(TypeError, match="version must be a non-empty string"):
        decode_plan(value)


def test_plan_rejects_unknown_top_level_field() -> None:
    value = complete_plan()
    value["unexpected"] = {}

    with pytest.raises(ValueError, match="does not accept: unexpected"):
        decode_plan(value)


def test_cell_sources_are_rejected() -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    outputs["summary"] = {
        "source": {"cell": {"name": "summary"}},
        "formats": {"html": {"exporter": "html"}},
    }

    with pytest.raises(ValueError, match="definition string or an expression object"):
        decode_plan(value)


def test_definition_sources_use_the_string_form() -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    outputs["summary"] = {
        "source": {"definition": "summary"},
        "formats": {"json": {}},
    }

    with pytest.raises(ValueError, match="definition string or an expression object"):
        decode_plan(value)


def test_format_declarations_are_objects() -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    outputs["summary"] = {"source": "summary", "formats": {"json": None}}

    with pytest.raises(TypeError, match=r"plan\.outputs\.summary\.formats\.json must be an object"):
        decode_plan(value)


@pytest.mark.parametrize(
    ("exporter", "first", "second", "expected"),
    [
        ("json", {}, {"indent": None, "sort_keys": True}, {"indent": None, "sort_keys": True}),
        ("parquet", {}, {"compression": None}, {"compression": "NONE"}),
        (
            "parquet",
            {"compression": "snappy"},
            {"compression": "SNAPPY"},
            {"compression": "SNAPPY"},
        ),
        ("png", {}, {"scale": 1.0}, {"scale": 1}),
        ("png", {"scale": 2}, {"scale": 2.0}, {"scale": 2}),
    ],
)
def test_builtin_options_have_one_canonical_wire_shape(
    exporter: str,
    first: dict[str, object],
    second: dict[str, object],
    expected: dict[str, object],
) -> None:
    def decoded_plan(options: dict[str, object]):
        value = complete_plan()
        outputs = value["outputs"]
        assert isinstance(outputs, dict)
        outputs["summary"] = {
            "source": "frame",
            "formats": {"result": {"exporter": exporter, "options": options}},
        }
        return decode_plan(value)

    first_plan = decoded_plan(first)
    second_plan = decoded_plan(second)
    first_output = first_plan.outputs[0]
    second_output = second_plan.outputs[0]
    first_format = first_output.formats[0]
    second_format = second_output.formats[0]
    first_wire = cast(dict[str, object], first_format.wire())
    second_wire = cast(dict[str, object], second_format.wire())

    assert first_wire == second_wire
    assert first_wire["options"] == expected
    assert (
        projection_binding(
            output_name=first_output.name,
            format_name=first_format.name,
            source=first_output.source,
            format_plan=first_format,
        ).cell
        == projection_binding(
            output_name=second_output.name,
            format_name=second_format.name,
            source=second_output.source,
            format_plan=second_format,
        ).cell
    )


@pytest.mark.parametrize(
    ("exporter", "options", "message"),
    [
        ("text", {"encoding": "utf-8"}, "does not accept: encoding"),
        ("json", {"sort_keys": "yes"}, "sort_keys must be a boolean"),
        ("parquet", {"compression": "rar"}, "compression must be one of"),
        ("png", {"scale": 0}, "scale must be a finite positive number"),
    ],
)
def test_builtin_option_errors_are_plan_decode_errors(
    exporter: str,
    options: dict[str, object],
    message: str,
) -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    outputs["summary"] = {
        "source": "frame",
        "formats": {"result": {"exporter": exporter, "options": options}},
    }

    with pytest.raises((TypeError, ValueError), match=message):
        decode_plan(value)


def test_custom_exporter_options_preserve_json_values() -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    options = {"compression": None, "scale": 1.0, "nested": {"enabled": True}}
    outputs["summary"] = {
        "source": "frame",
        "formats": {
            "custom": {
                "exporter": {"ref": "project.exporters:render.value", "version": "1"},
                "options": options,
            }
        },
    }

    decoded = decode_plan(value).outputs[0].formats[0]

    assert decoded.options == options
    assert isinstance(decoded.options["scale"], float)


@pytest.mark.parametrize("expression", ["frame[", "value = 1", ""])
def test_expression_sources_must_parse_in_eval_mode(expression: str) -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    outputs["summary"] = {
        "source": {"expression": expression},
        "formats": {"json": {"exporter": "json"}},
    }

    with pytest.raises((TypeError, ValueError), match="expression"):
        decode_plan(value)


@pytest.mark.parametrize(
    "ref",
    [
        "project.exporters",
        "project.exporters:render:extra",
        "project..exporters:render",
        "project.exporters:render-value",
        "project.class:render",
    ],
)
def test_importable_exporter_refs_use_dotted_python_identifiers(ref: str) -> None:
    value = complete_plan()
    outputs = value["outputs"]
    assert isinstance(outputs, dict)
    outputs["summary"] = {
        "source": "frame",
        "formats": {"custom": {"exporter": {"ref": ref, "version": "1"}}},
    }

    with pytest.raises(ValueError, match=r"module:object|dotted Python identifiers"):
        decode_plan(value)
