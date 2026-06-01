from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any, cast

from marimo._ast.compiler import compile_cell
from marimo._runtime.context.types import RuntimeContext
from marimo._runtime.dataflow import DirectedGraph
from marimo._types.ids import CellId_t

from moexport.artifacts import Artifact, ArtifactData, JsonObject
from moexport.blobs import BlobContent, BlobRef
from moexport.exporters import notebook
from moexport.runtime import NotebookRuntime


class FakeContext:
    filename = None
    cell_id = CellId_t("__current__")

    def __init__(self, graph: DirectedGraph, globals: dict[str, Any]) -> None:
        self.graph = graph
        self.globals = globals

    def with_cell_id(self, cid: CellId_t):
        del cid
        return nullcontext()


class CapturingExporterContext:
    scenario_id = "default"
    value_name = "notebook"
    artifact_name = "linear"

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def write_blob(
        self,
        name: str,
        data: BlobContent,
        *,
        media_type: str | None = None,
    ) -> BlobRef:
        href = f"blobs/test/{name}"
        blob = bytes(data)
        self.blobs[href] = blob
        return BlobRef(
            href=href,
            media_type=media_type,
            size=len(blob),
            sha256="test-sha",
        )

    def artifact(
        self,
        *,
        format_id: str,
        files: dict[str, BlobRef],
        entry: str | None = None,
        media_type: str | None = None,
        metadata: JsonObject | None = None,
    ) -> Artifact:
        return Artifact(
            format_id=format_id,
            media_type=media_type,
            data=ArtifactData(files=files, entry=entry),
            metadata=metadata,
        )


def test_notebook_linear_records_cell_source() -> None:
    graph = DirectedGraph()
    graph.register_cell(
        CellId_t("display"),
        compile_cell('"hello"', cell_id=CellId_t("display")),
    )
    runtime = NotebookRuntime(
        runtime=cast(RuntimeContext, FakeContext(graph, globals={})),
    )
    ctx = CapturingExporterContext()

    artifact = notebook.linear(runtime.snapshot(), ctx)
    blob = artifact.data.files[artifact.data.entry or ""]
    snapshot = json.loads(ctx.blobs[blob.href])
    cell = snapshot["cells"][0]

    assert cell["source"] == '"hello"'


def test_notebook_snapshot_records_output_materialization_errors() -> None:
    graph = DirectedGraph()
    graph.register_cell(
        CellId_t("display"),
        compile_cell("1 / 0", cell_id=CellId_t("display")),
    )
    runtime = NotebookRuntime(
        runtime=cast(RuntimeContext, FakeContext(graph, globals={})),
    )

    snapshot = runtime.snapshot(include_empty_outputs=True, on_error="record")

    output = snapshot.cells[0].outputs[0]
    assert output.channel == "error"
    assert output.data == {
        "type": "ZeroDivisionError",
        "message": "division by zero",
    }
