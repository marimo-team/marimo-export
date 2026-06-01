"""Override completion for marimo's same-cell definition semantics.

marimo treats all definitions produced by one cell as a group. If a scenario
overrides one name from that cell, this module fills the sibling definitions
from the live runtime or by locally computing the cell's default output, so
later planning can prune the overridden cell without losing required values.
"""

from __future__ import annotations

from typing import Any

from marimo._runtime.context.types import RuntimeContext
from marimo._runtime.dataflow import DirectedGraph
from marimo._types.ids import CellId_t

from moexport.evaluate._analysis import body_refs, single_defining_cell
from moexport.evaluate._execution import execute_cell_body
from moexport.evaluate._types import (
    DefaultCellCache,
    DefinitionOverrides,
    OverrideCompletion,
)
from moexport.evaluate._values import value_preview


async def complete_overrides(
    graph: DirectedGraph,
    ctx: RuntimeContext,
    overrides: DefinitionOverrides,
) -> OverrideCompletion:
    # marimo overrides a cell's definitions as a group. If the user overrides
    # one sibling def, fill the remaining siblings from the live session. When
    # notebook export starts from source before a kernel exists, compute the
    # cell's default defs locally as a fallback.
    completed = dict(overrides)
    auto_filled: dict[str, dict[str, str]] = {}
    default_cache: DefaultCellCache = {}

    for name in list(overrides):
        for cid in graph.get_defining_cells(name):
            cell = graph.cells[cid]
            default_defs: dict[str, Any] | None = None
            for sibling in cell.defs:
                if sibling in completed:
                    continue

                source = "live_runtime"
                if sibling in ctx.globals:
                    value = ctx.globals[sibling]
                else:
                    if default_defs is None:
                        try:
                            default_defs = await _default_cell_defs(
                                graph=graph,
                                ctx=ctx,
                                cid=cid,
                                cache=default_cache,
                                visiting=set(),
                            )
                        except Exception as exc:
                            raise ValueError(
                                f"Cannot auto-fill {sibling!r} because it is defined in "
                                f"the same cell as {name!r} and no live value or "
                                "default cell value is available."
                            ) from exc

                    if sibling not in default_defs:
                        raise ValueError(
                            f"Cannot auto-fill {sibling!r} because it is defined in "
                            f"the same cell as {name!r} and the default cell body "
                            "did not produce it."
                        )
                    value = default_defs[sibling]
                    source = "computed_default"

                completed[sibling] = value
                auto_filled[sibling] = {
                    "from_cell": str(cid),
                    "because": name,
                    "source": source,
                    "value_preview": value_preview(value),
                }

    return OverrideCompletion(
        values=completed,
        explicit_names=set(overrides),
        auto_filled=auto_filled,
    )


async def _default_cell_defs(
    *,
    graph: DirectedGraph,
    ctx: RuntimeContext,
    cid: CellId_t,
    cache: DefaultCellCache,
    visiting: set[CellId_t],
) -> dict[str, Any]:
    """Compute default defs for same-cell override completion.

    Example:

        symbols = ["AAPL", "MSFT"]
        interval = "1d"

    If a scenario overrides only ``symbols``, marimo's cell-override semantics
    still require ``interval``. A live kernel can provide it from
    ``ctx.globals``. Source-file export computes that default locally here.
    """

    if cid in cache:
        return cache[cid]
    if cid in visiting or graph.is_disabled(cid):
        return {}

    visiting.add(cid)
    try:
        cell = graph.cells[cid]
        glbls: dict[str, Any] = dict(ctx.globals)

        for ref in body_refs(cell):
            if ref in glbls:
                continue
            if ref not in graph.definitions:
                raise NameError(f"{ref!r} is not defined in the graph or live globals.")
            glbls.update(
                await _default_cell_defs(
                    graph=graph,
                    ctx=ctx,
                    cid=single_defining_cell(graph, ref),
                    cache=cache,
                    visiting=visiting,
                )
            )

        with ctx.with_cell_id(cid):
            await execute_cell_body(cell, glbls)

        produced_defs = {name: glbls[name] for name in cell.defs if name in glbls}
        cache[cid] = produced_defs
        return produced_defs
    finally:
        visiting.remove(cid)
