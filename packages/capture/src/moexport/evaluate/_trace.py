"""Trace metadata for graph and execution diagnostics.

Evaluation returns runtime Python values, but bundle producers also need a JSON
account of how the value was obtained. This module turns the plan/execution
facts into graph nodes, dependency edges, timing steps, cache status, and skip
reasons without leaking Python object handles.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from marimo._ast.cell import CellImpl
from marimo._runtime.context.types import RuntimeContext
from marimo._runtime.dataflow import DirectedGraph
from marimo._types.ids import CellId_t

from moexport.evaluate._analysis import body_refs
from moexport.evaluate._types import CellStatus, JsonDict, TraceMetadata
from moexport.evaluate._values import value_preview


def _short_id(cid: CellId_t) -> str:
    return str(cid)[:8]


def _preview(cell: CellImpl, lines: int = 3, width: int = 100) -> list[str]:
    return [line.rstrip()[:width] for line in cell.code.splitlines() if line.strip()][
        :lines
    ]


def _cell_status(
    cid: CellId_t,
    *,
    executed: set[CellId_t],
    cached: set[CellId_t],
    skipped: Mapping[CellId_t, str],
    pruned: set[CellId_t],
    required: set[CellId_t],
) -> CellStatus:
    if cid in executed:
        return "executed"
    if cid in cached:
        return "cached"
    if cid in pruned:
        return "pruned"
    if cid in skipped:
        return "skipped"
    if cid in required:
        return "needed"
    return "inactive"


def build_trace_metadata(
    *,
    graph: DirectedGraph,
    ctx: RuntimeContext,
    required_order: list[CellId_t],
    execution_order: list[CellId_t],
    executed: list[CellId_t],
    cached: list[CellId_t],
    skipped: dict[CellId_t, str],
    pruned: set[CellId_t],
    required: set[CellId_t],
    completed_overrides: dict[str, Any],
    per_cell_defs: dict[str, dict[str, Any]],
    outputs: dict[str, Any],
    timings: dict[str, float],
    elapsed_ms: float,
) -> TraceMetadata:
    # The trace is diagnostic/provenance data for the producer. Graph metadata
    # is structural/status data suitable for Mermaid-style side graphs.
    # Execution metadata is timing/cache data for explaining performance.
    display_order = [cid for cid in graph.cells if cid != ctx.cell_id]
    display_set = set(display_order)
    executed_set = set(executed)
    cached_set = set(cached)
    status_counts: dict[CellStatus, int] = {
        "executed": 0,
        "cached": 0,
        "pruned": 0,
        "skipped": 0,
        "needed": 0,
        "inactive": 0,
    }

    nodes = []
    for cid in display_order:
        cell = graph.cells[cid]
        status = _cell_status(
            cid,
            executed=executed_set,
            cached=cached_set,
            skipped=skipped,
            pruned=pruned,
            required=required,
        )
        status_counts[status] += 1

        nodes.append(
            {
                "cell_id": str(cid),
                "short_id": _short_id(cid),
                "status": status,
                "skip_reason": skipped.get(cid),
                "disabled": graph.is_disabled(cid),
                "defs": sorted(cell.defs),
                "refs": sorted(cell.refs),
                "body_refs": sorted(body_refs(cell)),
                "overridden_defs": sorted(cell.defs & set(completed_overrides)),
                "preview": _preview(cell),
            }
        )

    edges = [
        {"from": str(parent), "to": str(child)}
        for child in display_set
        for parent in graph.parents[child]
        if parent in display_set and child != ctx.cell_id
    ]

    graph_metadata: JsonDict = {
        "nodes": nodes,
        "edges": sorted(edges, key=lambda edge: (edge["from"], edge["to"])),
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "required": len(required),
            "status_counts": status_counts,
        },
    }

    steps = [
        {
            "cell_id": str(cid),
            "status": "cached" if cid in cached_set else "executed",
            "defs": sorted(per_cell_defs.get(str(cid), {})),
            "output_preview": value_preview(outputs[str(cid)])
            if str(cid) in outputs
            else None,
            "elapsed_ms": round(timings.get(str(cid), 0) * 1000, 2),
        }
        for cid in execution_order
        if cid in executed_set or cid in cached_set
    ]
    execution_metadata: JsonDict = {
        "elapsed_ms": elapsed_ms,
        "required_order": [str(cid) for cid in required_order],
        "scheduled_order": [str(cid) for cid in execution_order],
        "executed_cell_ids": [str(cid) for cid in executed],
        "cached_cell_ids": [str(cid) for cid in cached],
        "pruned_cell_ids": [str(cid) for cid in required_order if cid in pruned],
        "skipped_cell_ids": [str(cid) for cid in execution_order if cid in skipped],
        "skip_reasons": {
            str(cid): skipped[cid] for cid in execution_order if cid in skipped
        },
        "steps": steps,
        "stats": {
            "required": len(required),
            "scheduled": len(execution_order),
            "executed": len(executed),
            "cached": len(cached),
            "pruned": len(pruned),
            "skipped": len(skipped),
        },
    }
    return TraceMetadata(graph=graph_metadata, execution=execution_metadata)
