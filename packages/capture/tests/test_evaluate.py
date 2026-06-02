from __future__ import annotations

import asyncio
import html
import importlib
from contextlib import nullcontext
from typing import Any

import moexport as mox
import pytest
from moexport.evaluate import evaluate
from marimo._ast.cell import CellConfig
from marimo._ast.compiler import compile_cell
from marimo._messaging.notebook.document import (
    NotebookCell,
    NotebookDocument,
    notebook_document_context,
)
from marimo._runtime.dataflow import DirectedGraph
from marimo._types.ids import CellId_t

runtime_module = importlib.import_module("moexport.runtime")
target_module = importlib.import_module("moexport.evaluate._target")


class FakeContext:
    def __init__(
        self,
        *,
        graph: DirectedGraph,
        globals: dict[str, Any],
        cell_id: CellId_t = CellId_t("__current__"),
        filename: str | None = None,
    ) -> None:
        self.graph = graph
        self.globals = globals
        self.cell_id = cell_id
        self.filename = filename

    def with_cell_id(self, cid: CellId_t):
        del cid
        return nullcontext()


def cell(code: str, cell_id: str):
    return compile_cell(code, cell_id=CellId_t(cell_id))


def graph_from(cells: dict[str, str]) -> DirectedGraph:
    graph = DirectedGraph()
    for cid, code in cells.items():
        graph.register_cell(CellId_t(cid), cell(code, cid))
    return graph


def run(coro):
    return asyncio.run(coro)


def graph_statuses(result: dict[str, Any]) -> list[str]:
    return [node["status"] for node in result["metadata"]["graph"]["nodes"]]


def first_result(response: dict[str, Any]) -> dict[str, Any]:
    return response["results"][0]


def test_evaluate_expression_metadata_ignores_builtins(monkeypatch) -> None:
    graph = graph_from({})
    ctx = FakeContext(graph=graph, globals={"df": [1], "xs": [2, 3]})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("len(df) + max(xs)"))
    result = first_result(response)

    assert result["value"] == 4
    assert result["metadata"]["target"]["expression_refs"] == ["df", "xs"]
    assert result["metadata"]["execution"]["stats"]["executed"] == 0


def test_evaluate_expression_metadata_ignores_comprehension_locals(
    monkeypatch,
) -> None:
    graph = graph_from({})
    ctx = FakeContext(graph=graph, globals={"xs": [1, 2], "offset": 10})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("{x: y for x in xs for y in [x + offset]}"))
    result = first_result(response)

    assert result["value"] == {1: 11, 2: 12}
    assert result["metadata"]["target"]["expression_refs"] == ["offset", "xs"]
    assert result["metadata"]["execution"]["stats"]["executed"] == 0


def test_evaluate_recomputes_cell_body_without_display_only_refs(
    monkeypatch,
) -> None:
    graph = graph_from(
        {
            "input": "x = 1",
            "display": "y = x + 1\ndf",
            "dataframe": "df = 'live-only-display-value'",
        }
    )
    ctx = FakeContext(
        graph=graph,
        globals={"x": 1, "y": 2, "df": "live-only-display-value"},
    )
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("y", {"x": 2}))
    result = first_result(response)

    assert result["value"] == 3
    assert result["computed_defs"] == {"y": 3}
    assert graph_statuses(result) == ["pruned", "executed", "inactive"]


def test_evaluate_auto_fills_live_sibling_defs(monkeypatch) -> None:
    graph = graph_from({"config": "symbols = ['AAPL', 'MSFT']\ninterval = '1d'"})
    ctx = FakeContext(
        graph=graph,
        globals={"symbols": ["AAPL", "MSFT"], "interval": "1d"},
    )
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("interval", {"symbols": ["GOOGL"]}))
    result = first_result(response)

    assert result["value"] == "1d"
    assert result["auto_filled_overrides"]["interval"] == {
        "from_cell": "config",
        "because": "symbols",
        "source": "live_runtime",
        "value_preview": "'1d'",
    }


def test_evaluate_computes_missing_sibling_defaults(monkeypatch) -> None:
    graph = graph_from({"config": "a = 1\nb = 2"})
    ctx = FakeContext(graph=graph, globals={"a": 1})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("b", {"a": 10}))
    result = first_result(response)

    assert result["value"] == 2
    assert result["auto_filled_overrides"]["b"] == {
        "from_cell": "config",
        "because": "a",
        "source": "computed_default",
        "value_preview": "2",
    }


def test_evaluate_errors_when_sibling_default_is_unavailable(monkeypatch) -> None:
    graph = graph_from({"config": "a = 1\nb = missing"})
    ctx = FakeContext(graph=graph, globals={"a": 1})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    with pytest.raises(ValueError, match="Cannot auto-fill 'b'"):
        run(evaluate("b", {"a": 10}))


def test_evaluate_definition_reuses_clean_live_value(monkeypatch) -> None:
    graph = graph_from(
        {
            "config": "symbols = ['AAPL', 'MSFT']\ninterval = '1d'\nchart_width = 800",
            "df": "df = f'{symbols[0]}:{interval}'",
            "chart": "chart = f'{df}:{chart_width}'",
        }
    )
    ctx = FakeContext(
        graph=graph,
        globals={
            "symbols": ["AAPL", "MSFT"],
            "interval": "1d",
            "chart_width": 800,
            "df": "AAPL:1d",
            "chart": "AAPL:1d:800",
        },
    )
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("chart"))
    result = first_result(response)

    assert response["target"] == "chart"
    assert response["metadata"]["batch"]["result_count"] == 1
    assert result["value"] == "AAPL:1d:800"
    assert result["metadata"]["execution"]["stats"]["executed"] == 0
    assert result["live_values"] == {"chart": "AAPL:1d:800"}
    assert result["metadata"]["graph"]["stats"]["status_counts"] == {
        "executed": 0,
        "cached": 0,
        "pruned": 0,
        "skipped": 0,
        "needed": 0,
        "inactive": 3,
    }


def test_evaluate_definition_recomputes_dirty_dependencies(monkeypatch) -> None:
    graph = graph_from(
        {
            "config": "symbols = ['AAPL', 'MSFT']\ninterval = '1d'\nchart_width = 800",
            "df": "df = f'{symbols[0]}:{interval}'",
            "chart": "chart = f'{df}:{chart_width}'\nchart",
            "unrelated": "z = 1",
        }
    )
    ctx = FakeContext(
        graph=graph,
        globals={
            "symbols": ["AAPL", "MSFT"],
            "interval": "1d",
            "chart_width": 800,
            "df": "AAPL:1d",
            "chart": "AAPL:1d:800",
        },
    )
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("chart", {"symbols": ["GOOGL"]}))
    result = first_result(response)

    assert result["value"] == "GOOGL:1d:800"
    assert result["computed_defs"] == {
        "df": "GOOGL:1d",
        "chart": "GOOGL:1d:800",
    }
    assert result["auto_filled_overrides"].keys() == {
        "interval",
        "chart_width",
    }
    assert result["metadata"]["execution"]["stats"]["executed"] == 2
    assert result["metadata"]["graph"]["stats"]["required"] == 3
    assert graph_statuses(result) == [
        "pruned",
        "executed",
        "executed",
        "inactive",
    ]
    assert result["metadata"]["execution"]["steps"] == [
        {
            "cell_id": "df",
            "status": "executed",
            "defs": ["df"],
            "output_preview": "None",
            "elapsed_ms": pytest.approx(0, abs=10_000),
        },
        {
            "cell_id": "chart",
            "status": "executed",
            "defs": ["chart"],
            "output_preview": "'GOOGL:1d:800'",
            "elapsed_ms": pytest.approx(0, abs=10_000),
        },
    ]


def test_evaluate_auto_filled_sibling_defs_do_not_dirty_dependencies(
    monkeypatch,
) -> None:
    graph = graph_from(
        {
            "config": "symbols = ['AAPL', 'MSFT']\ninterval = '1d'\nchart_width = 800",
            "df": "df = f'{symbols[0]}:{interval}'",
            "chart": "chart = f'{df}:{chart_width}'",
        }
    )
    ctx = FakeContext(
        graph=graph,
        globals={
            "symbols": ["AAPL", "MSFT"],
            "interval": "1d",
            "chart_width": 800,
            "df": "AAPL:1d",
            "chart": "AAPL:1d:800",
        },
    )
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("chart", {"chart_width": 1000}))
    result = first_result(response)

    assert result["value"] == "AAPL:1d:1000"
    assert result["computed_defs"] == {"chart": "AAPL:1d:1000"}
    assert result["auto_filled_overrides"].keys() == {"symbols", "interval"}
    assert result["metadata"]["target"]["override_refs"] == ["chart_width"]
    assert result["metadata"]["execution"]["stats"]["executed"] == 1
    assert graph_statuses(result) == ["pruned", "inactive", "executed"]


def test_evaluate_expression_uses_live_values(monkeypatch) -> None:
    graph = graph_from({"df": "df = 'AAPL:1d'"})
    ctx = FakeContext(graph=graph, globals={"df": "AAPL:1d"})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("df + ':close'"))
    result = first_result(response)

    assert result["kind"] == "expression"
    assert result["metadata"]["target"]["expression_refs"] == ["df"]
    assert result["value"] == "AAPL:1d:close"
    assert result["metadata"]["execution"]["stats"]["executed"] == 0


def test_evaluate_result_metadata_includes_mermaid_trace(monkeypatch) -> None:
    graph = graph_from(
        {
            "config": "symbol = 'AAPL'",
            "chart": "chart = f'<b>{symbol}</b>'",
        }
    )
    ctx = FakeContext(graph=graph, globals={"symbol": "AAPL", "chart": "<b>AAPL</b>"})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("chart", {"symbol": "MSFT"}))
    mermaid = first_result(response)["metadata"]["mermaid"]

    assert mermaid
    assert "flowchart TD" in mermaid
    assert "chart = f'<b>{symbol}</b>'" in html.unescape(mermaid)
    assert "chart = f'<b>{symbol}</b>'" not in mermaid


def test_runtime_expression_can_read_cell_source(monkeypatch) -> None:
    graph = graph_from(
        {
            "config": "symbols = ['AAPL']",
            "display": "symbols[0]",
        }
    )
    ctx = FakeContext(graph=graph, globals={"symbols": ["AAPL"]})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate("mox.runtime().cell(index=1).source"))
    result = first_result(response)

    assert result["value"] == "symbols[0]"
    assert result["metadata"]["execution"]["stats"]["executed"] == 0


def test_runtime_cell_exposes_authored_source(monkeypatch) -> None:
    graph = graph_from({"display": "symbols[0]"})
    ctx = FakeContext(graph=graph, globals={"symbols": ["AAPL"]})
    monkeypatch.setattr(runtime_module, "get_context", lambda: ctx)

    cell = mox.runtime().cell(index=0)

    assert cell.source == "symbols[0]"


def test_runtime_cell_output_materializes_live_markdown(monkeypatch) -> None:
    class FakeMo:
        def md(self, text: str) -> str:
            return f"md:{text}"

    graph = graph_from({"display": 'mo.md("hello")'})
    ctx = FakeContext(graph=graph, globals={"mo": FakeMo()})
    monkeypatch.setattr(runtime_module, "get_context", lambda: ctx)

    cell = mox.runtime().cell(index=0)

    assert cell.output == "md:hello"


def test_runtime_expression_can_select_cell_by_source_name(
    monkeypatch,
    tmp_path,
) -> None:
    notebook = tmp_path / "finance.py"
    notebook.write_text(
        """import marimo

__generated_with = "0.23.4"
app = marimo.App()


@app.cell
def _(mo):
    symbols = ["AAPL"]
    return (symbols,)


@app.cell
def change_desc(mo):
    mo.md("hello")
    return


if __name__ == "__main__":
    app.run()
""",
        encoding="utf-8",
    )
    graph = graph_from(
        {
            "config": 'symbols = ["AAPL"]',
            "display": 'mo.md("hello")',
        }
    )
    ctx = FakeContext(graph=graph, globals={}, filename=str(notebook))
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate('mox.runtime().cell("change_desc").source'))
    result = first_result(response)

    assert result["value"] == 'mo.md("hello")'
    assert result["metadata"]["execution"]["stats"]["executed"] == 0


def test_runtime_expression_selects_cell_name_from_live_document(
    monkeypatch,
) -> None:
    graph = graph_from(
        {
            "target": 'mo.md("hello")',
            "other": '"other"',
        }
    )
    ctx = FakeContext(graph=graph, globals={})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)
    document = NotebookDocument(
        [
            NotebookCell(CellId_t("stale"), "stale = True", "_", CellConfig()),
            NotebookCell(
                CellId_t("target"),
                'mo.md("hello")',
                "change_desc",
                CellConfig(),
            ),
            NotebookCell(CellId_t("other"), '"other"', "_", CellConfig()),
        ]
    )

    with notebook_document_context(document):
        response = run(evaluate('mox.runtime().cell("change_desc").source'))
    result = first_result(response)

    assert result["value"] == 'mo.md("hello")'


def test_runtime_file_name_fallback_matches_source_not_position(
    monkeypatch,
    tmp_path,
) -> None:
    notebook = tmp_path / "finance.py"
    notebook.write_text(
        """import marimo

__generated_with = "0.23.4"
app = marimo.App()


@app.cell
def _():
    stale = True
    return (stale,)


@app.cell
def change_desc(mo):
    mo.md("hello")
    return


@app.cell
def _():
    "other"


if __name__ == "__main__":
    app.run()
""",
        encoding="utf-8",
    )
    graph = graph_from(
        {
            "target": 'mo.md("hello")',
            "other": '"other"',
        }
    )
    ctx = FakeContext(graph=graph, globals={}, filename=str(notebook))
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(evaluate('mox.runtime().cell("change_desc").source'))
    result = first_result(response)

    assert result["value"] == 'mo.md("hello")'


def test_runtime_expression_materializes_cell_output_with_overrides(
    monkeypatch,
) -> None:
    graph = graph_from(
        {
            "config": "symbols = ['AAPL']",
            "display": "symbols[0]",
        }
    )
    ctx = FakeContext(graph=graph, globals={"symbols": ["AAPL"]})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(
        evaluate(
            "mox.runtime().cell(index=1).output",
            {"symbols": ["MSFT"]},
        )
    )
    result = first_result(response)

    assert result["value"] == "MSFT"
    assert result["auto_filled_overrides"] == {}
    assert result["metadata"]["execution"]["stats"]["executed"] == 1
    assert graph_statuses(result) == ["pruned", "executed"]


def test_runtime_stored_output_bypasses_scenario_output(monkeypatch) -> None:
    graph = graph_from(
        {
            "config": "symbols = ['AAPL']",
            "display": "symbols[0]",
        }
    )
    ctx = FakeContext(graph=graph, globals={"symbols": ["AAPL"]})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(
        evaluate(
            "{"
            "'stored': mox.runtime().cell(index=1).stored_output(), "
            "'scenario': mox.runtime().cell(index=1).output"
            "}",
            {"symbols": ["MSFT"]},
            output_cell_ids={CellId_t("display")},
        )
    )
    result = first_result(response)

    assert result["value"] == {"stored": "AAPL", "scenario": "MSFT"}


def test_runtime_stored_output_records_materialization_errors(monkeypatch) -> None:
    graph = graph_from(
        {
            "config": "symbols = []",
            "display": "symbols[0]",
        }
    )
    ctx = FakeContext(graph=graph, globals={"symbols": []})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(
        evaluate(
            "mox.runtime().cell(index=1).stored_output(on_error='record')",
        )
    )
    output = first_result(response)["value"]

    assert output.channel == "error"
    assert output.data == {
        "type": "IndexError",
        "message": "list index out of range",
    }


def test_runtime_snapshot_records_scheduled_output_errors(monkeypatch) -> None:
    graph = graph_from(
        {
            "config": "symbols = ['AAPL']",
            "display": "symbols[0]",
        }
    )
    ctx = FakeContext(graph=graph, globals={"symbols": ["AAPL"]})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(
        evaluate(
            "mox.runtime().snapshot(include_empty_outputs=True, on_error='record')",
            {"symbols": []},
            output_cell_ids={CellId_t("display")},
            output_error_policy="record",
        )
    )
    snapshot = first_result(response)["value"]
    output = snapshot.cells[1].outputs[0]

    assert output.channel == "error"
    assert output.data == {
        "type": "IndexError",
        "message": "list index out of range",
    }


def test_runtime_scenario_output_raises_recorded_materialization_errors(
    monkeypatch,
) -> None:
    graph = graph_from(
        {
            "config": "symbols = ['AAPL']",
            "display": "symbols[0]",
        }
    )
    ctx = FakeContext(graph=graph, globals={"symbols": ["AAPL"]})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    with pytest.raises(RuntimeError, match="list index out of range"):
        run(
            evaluate(
                "mox.runtime().cell(index=1).scenario_output(on_error='raise')",
                {"symbols": []},
                output_cell_ids={CellId_t("display")},
                output_error_policy="record",
            )
        )


def test_evaluate_batch_reuses_cells_with_identical_body_dependencies(
    monkeypatch,
) -> None:
    graph = graph_from(
        {
            "config": "symbols = ['AAPL']\nchart_width = 240",
            "df": "df = symbols[0]",
            "chart": "chart = f'{df}:{chart_width}'",
        }
    )
    ctx = FakeContext(
        graph=graph,
        globals={
            "symbols": ["AAPL"],
            "chart_width": 240,
            "df": "AAPL",
            "chart": "AAPL:240",
        },
    )
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(
        evaluate(
            "chart",
            states=[
                {"symbols": ["TSLA"], "chart_width": 240},
                {"symbols": ["TSLA"], "chart_width": 480},
            ],
        )
    )
    results = response["results"]

    assert response["target"] == "chart"
    assert response["metadata"]["batch"]["result_count"] == 2
    assert [result["value"] for result in results] == ["TSLA:240", "TSLA:480"]
    assert results[0]["metadata"]["execution"]["stats"]["executed"] == 2
    assert results[0]["metadata"]["execution"]["stats"]["cached"] == 0
    assert results[1]["metadata"]["execution"]["stats"]["executed"] == 1
    assert results[1]["metadata"]["execution"]["stats"]["cached"] == 1
    assert graph_statuses(results[1]) == [
        "pruned",
        "cached",
        "executed",
    ]
    assert results[1]["metadata"]["graph"]["stats"]["status_counts"] == {
        "executed": 1,
        "cached": 1,
        "pruned": 1,
        "skipped": 0,
        "needed": 0,
        "inactive": 0,
    }


def test_evaluate_dotted_state_updates_materialized_objects(monkeypatch) -> None:
    graph = graph_from(
        {
            "selector": """
class Selector:
    def __init__(self):
        self.value = ["AAPL"]

selector = Selector()
""",
            "chart": "chart = ','.join(selector.value)",
        }
    )
    ctx = FakeContext(graph=graph, globals={})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(
        evaluate(
            "chart",
            states=[
                {"selector.value": ["CRWV", "MSFT"]},
                {"selector.value": ["AAPL", "GOOGL", "AMZN"]},
            ],
        )
    )
    results = response["results"]

    assert [result["value"] for result in results] == [
        "CRWV,MSFT",
        "AAPL,GOOGL,AMZN",
    ]
    assert results[0]["metadata"]["state"]["applied_state_updates"] == [
        {
            "target": "selector.value",
            "root": "selector",
            "value_preview": "['CRWV', 'MSFT']",
        }
    ]
    assert results[1]["metadata"]["execution"]["stats"]["cached"] == 0


def test_evaluate_dotted_state_updates_do_not_leak_through_cache(monkeypatch) -> None:
    graph = graph_from(
        {
            "selector": """
class Selector:
    def __init__(self):
        self.value = ["AAPL"]

selector = Selector()
""",
            "chart": "chart = ','.join(selector.value)",
        }
    )
    ctx = FakeContext(graph=graph, globals={})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    response = run(
        evaluate(
            "chart",
            states=[
                {"selector.value": ["CRWV", "MSFT"]},
                {},
            ],
        )
    )
    results = response["results"]

    assert [result["value"] for result in results] == [
        "CRWV,MSFT",
        "AAPL",
    ]


def test_evaluate_shape_is_stable_for_single_and_batch(monkeypatch) -> None:
    graph = graph_from({"x": "x = width * 2"})
    ctx = FakeContext(graph=graph, globals={"width": 1, "x": 2})
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    single = run(evaluate("x", {"width": 2}))
    batch = run(evaluate("x", states=[{"width": 2}]))

    assert single.keys() == batch.keys() == {"target", "results", "metadata"}
    assert (
        single["metadata"].keys()
        == batch["metadata"].keys()
        == {
            "batch",
            "execution",
        }
    )
    assert len(single["results"]) == len(batch["results"]) == 1
    assert single["results"][0].keys() == batch["results"][0].keys()
