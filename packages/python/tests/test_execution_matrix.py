from __future__ import annotations

import ast

from marimo._ast.compiler import compile_cell
from marimo._types.ids import CellId_t
from marimo_export import ExportSpec, OutputSpec
from marimo_export._execution import (
    Baseline,
    Definition,
    OutputProjection,
    normalize_matrix,
    projection_code,
)
from marimo_export.errors import SpecError
from marimo_export.exporters import altair, importable


def _baseline() -> Baseline:
    return Baseline(
        definitions={
            "symbols": Definition(
                name="symbols",
                cell_id="cell-inputs",
                siblings=("chart_width", "interval", "symbols"),
                kind="ordinary",
                python_type="builtins.list",
                value=["AAPL", "MSFT"],
            ),
            "interval": Definition(
                name="interval",
                cell_id="cell-inputs",
                siblings=("chart_width", "interval", "symbols"),
                kind="ordinary",
                python_type="builtins.str",
                value="1d",
            ),
            "chart_width": Definition(
                name="chart_width",
                cell_id="cell-inputs",
                siblings=("chart_width", "interval", "symbols"),
                kind="ordinary",
                python_type="builtins.int",
                value=800,
            ),
            "selector": Definition(
                name="selector",
                cell_id="cell-ui",
                siblings=("selector",),
                kind="ui",
                python_type="marimo.ui.multiselect",
                value=object(),
                frontend_value=["AAPL"],
            ),
            "df": Definition(
                name="df",
                cell_id="cell-df",
                siblings=("df",),
                kind="ordinary",
                python_type="polars.DataFrame",
                value=object(),
            ),
        },
        document_sha256="a" * 64,
        filename="finance.py",
    )


def _spec() -> ExportSpec:
    return ExportSpec(
        inputs=("symbols", "selector"),
        states={
            "wide": {"symbols": ["AAPL", "MSFT", "GOOGL"]},
            "baseline": {},
            "focus": {"selector": ["MSFT"]},
        },
        outputs={"prices": OutputSpec(source="df")},
    )


def test_sparse_rows_normalize_in_canonical_state_order() -> None:
    plan = normalize_matrix(_spec(), _baseline())

    assert tuple(state.name for state in plan.states) == ("baseline", "focus", "wide")
    assert dict(plan.states[0].inputs) == {
        "symbols": ["AAPL", "MSFT"],
        "selector": ["AAPL"],
    }
    assert dict(plan.states[1].inputs) == {
        "symbols": ["AAPL", "MSFT"],
        "selector": ["MSFT"],
    }
    assert plan.states[0].fingerprint != plan.states[1].fingerprint


def test_ordinary_override_supplies_complete_sibling_packet() -> None:
    state = normalize_matrix(_spec(), _baseline()).states[-1]

    assert dict(state.ordinary_overrides) == {
        "chart_width": 800,
        "interval": "1d",
        "symbols": ["AAPL", "MSFT", "GOOGL"],
    }
    assert dict(state.ui_values) == {"selector": ["AAPL"]}


def test_duplicate_normalized_vectors_fail_before_execution() -> None:
    spec = ExportSpec(
        inputs=("symbols",),
        states={"one": {}, "two": {"symbols": ["AAPL", "MSFT"]}},
        outputs={"prices": OutputSpec(source="df")},
    )

    try:
        normalize_matrix(spec, _baseline())
    except SpecError as error:
        assert error.code == "spec_state_duplicate"
    else:
        raise AssertionError("duplicate normalized states were accepted")


def test_projection_body_reads_state_and_source_without_definitions() -> None:
    code = projection_code(
        OutputProjection(
            name='chart "main"',
            source="symbols_chart",
            exporter=None,
        ),
        "marimo_export_state_0123456789abcdef",
    )
    cell = compile_cell(code, cell_id=CellId_t("projection"))

    assert cell.defs == set()
    assert cell.refs == {
        "marimo_export_state_0123456789abcdef",
        "symbols_chart",
    }


def test_exporter_projection_is_a_deterministic_marimo_leaf() -> None:
    code = projection_code(
        OutputProjection(
            name="snapshot",
            source="performance",
            exporter=altair.png(scale=2),
        ),
        "marimo_export_state_0123456789abcdef",
    )
    tree = ast.parse(code)
    cell = compile_cell(code, cell_id=CellId_t("projection"))

    assert cell.defs == set()
    assert cell.refs == {
        "marimo_export_state_0123456789abcdef",
        "performance",
    }
    imported = tree.body[1]
    assert isinstance(imported, ast.ImportFrom)
    assert imported.module == "marimo_export.exporters._runtime.altair"
    call = tree.body[2]
    assert isinstance(call, ast.Expr)
    assert isinstance(call.value, ast.Call)
    assert ast.unparse(call.value) == "_marimo_export_exporter(performance, scale=2)"


def test_custom_exporter_projection_uses_an_explicit_importable_callable() -> None:
    code = projection_code(
        OutputProjection(
            name="summary",
            source="result",
            exporter=importable(
                "acme.exports:encode",
                columns=["a", "b"],
                config={"compact": True},
            ),
        ),
        "marimo_export_state_0123456789abcdef",
    )

    assert "from acme.exports import encode as _marimo_export_exporter" in code
    assert ("_marimo_export_exporter(result, columns=['a', 'b'], config={'compact': True})") in code
