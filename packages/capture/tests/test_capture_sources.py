from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

from marimo._ast.compiler import compile_cell
from marimo._runtime.dataflow import DirectedGraph
from marimo._types.ids import CellId_t

from capture_test_helpers import export_module, request_module, run, target_module


def test_capture_materializes_cell_output_for_the_activescenario(
    tmp_path: Path,
    monkeypatch,
) -> None:
    graph = DirectedGraph()
    graph.register_cell(
        CellId_t("config"),
        compile_cell("symbols = ['AAPL']", cell_id=CellId_t("config")),
    )
    graph.register_cell(
        CellId_t("display"),
        compile_cell("symbols[0]", cell_id=CellId_t("display")),
    )

    class FakeContext:
        filename = None
        cell_id = CellId_t("__current__")

        def __init__(self) -> None:
            self.graph = graph
            self.globals = {"symbols": ["AAPL"]}

        def with_cell_id(self, cid: CellId_t):
            del cid
            return nullcontext()

    ctx = FakeContext()
    monkeypatch.setattr(request_module, "get_context", lambda: ctx)
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    result = run(
        export_module.capture(
            {
                "scenarios": [{"id": "override", "state": {"symbols": ["MSFT"]}}],
                "values": {
                    "title": {
                        "source": {"cell": {"index": 1}},
                        "formats": ["text"],
                    }
                },
            },
            to=tmp_path / "scenario",
        )
    )

    def artifact_text(result: Any) -> str:
        artifact = result.manifest["scenarios"][0]["values"]["title"]["text"]
        entry = artifact["data"]["entry"]
        blob = artifact["data"]["files"][entry]
        return (Path(result.bundle_path).parent.parent / blob["href"]).read_text()

    assert result.manifest["values"]["title"]["source"] == {
        "type": "cell_output",
        "cell": {"index": 1},
        "on_error": "raise",
    }
    assert artifact_text(result) == "MSFT"
