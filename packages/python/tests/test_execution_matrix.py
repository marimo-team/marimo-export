from __future__ import annotations

from marimo_export import ExportSpec, OutputSpec
from marimo_export._execution import (
    Baseline,
    Definition,
    normalize_matrix,
    projection_code,
)
from marimo_export.errors import SpecError


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


def test_projection_body_is_deterministic_and_definition_free() -> None:
    assert projection_code('chart "main"', "symbols_chart") == (
        '# marimo-export projection: "chart \\"main\\""\nsymbols_chart'
    )
