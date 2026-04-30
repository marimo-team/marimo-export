"""Target planning over the live notebook dataflow graph.

The planner decides which values can be reused from the live runtime and which
cells must run locally for a target, definition override set, object patch set,
and selected output cells. It is the boundary between static dependency
analysis and the execution loop: the result is a declarative plan, not executed
state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from marimo._runtime.context.types import RuntimeContext
from marimo._runtime.dataflow import DirectedGraph
from marimo._types.ids import CellId_t

from moexport.evaluate._analysis import (
    body_refs,
    display_refs,
    expression_refs,
    single_defining_cell,
)
from moexport.evaluate._types import TargetKind, TargetPlan
from moexport.runtime import expression_globals as moexport_expression_globals


def _name_is_dirty(
    name: str,
    graph: DirectedGraph,
    ctx: RuntimeContext,
    dirty_names: set[str],
    memo: dict[str, bool],
    stack: set[str],
) -> bool:
    # Dirty means "must be recomputed locally": explicit override, missing live
    # value, or a defining cell whose body depends on another dirty name.
    if name in dirty_names:
        return True
    if name in memo:
        return memo[name]
    if name not in graph.definitions:
        memo[name] = False
        return False
    if name not in ctx.globals:
        memo[name] = True
        return True
    if name in stack:
        # Cycles are invalid in marimo proper; this guard only prevents this
        # sidecar analysis from recursing forever on unexpected graph states.
        return False

    stack.add(name)
    cell = graph.cells[single_defining_cell(graph, name)]
    dirty = any(
        _name_is_dirty(ref, graph, ctx, dirty_names, memo, stack)
        for ref in body_refs(cell)
    )
    stack.remove(name)

    memo[name] = dirty
    return dirty


def plan_target(
    graph: DirectedGraph,
    ctx: RuntimeContext,
    target: str,
    completed_overrides: Mapping[str, Any],
    override_names: set[str],
    object_patch_roots: set[str],
    output_cell_ids: set[CellId_t],
) -> TargetPlan:
    # A bare graph definition ("df") and an expression ("df.head()") share the
    # same planner after root names are identified.
    if target in graph.definitions:
        kind: TargetKind = "definition"
        root_names = [target]
        target_expression_refs: list[str] = []
    else:
        kind = "expression"
        target_expression_refs = expression_refs(target)
        root_names = target_expression_refs

    needed: set[CellId_t] = set()
    live_values: dict[str, Any] = {}
    override_refs: set[str] = set()
    dirty_memo: dict[str, bool] = {}
    visiting_cells: set[CellId_t] = set()
    special_globals = moexport_expression_globals()
    dirty_names = override_names | object_patch_roots

    def require_name(name: str) -> None:
        if name in object_patch_roots:
            if name not in graph.definitions:
                raise NameError(
                    f"scenario object patch root {name!r} must be a notebook definition"
                )
            require_cell(single_defining_cell(graph, name))
            return

        if name in override_names:
            override_refs.add(name)
            return

        if name in special_globals:
            live_values[name] = special_globals[name]
            return

        if name in completed_overrides:
            live_values[name] = completed_overrides[name]
            return

        if name in graph.definitions:
            dirty = _name_is_dirty(name, graph, ctx, dirty_names, dirty_memo, set())
            if dirty:
                require_cell(single_defining_cell(graph, name))
                return

        if name in ctx.globals:
            # Clean live globals are cache hits. This is the behavior that lets
            # evaluate("df.head()") avoid rerunning an expensive df cell.
            live_values[name] = ctx.globals[name]
            return

        if name in graph.definitions:
            require_cell(single_defining_cell(graph, name))
            return

        raise NameError(f"{name!r} is not defined in the graph or live globals.")

    def require_cell(cid: CellId_t) -> None:
        if cid in needed or cid in visiting_cells:
            return

        visiting_cells.add(cid)
        cell = graph.cells[cid]

        # Plan from body refs only. A final display expression may mention a
        # large live object, but it should not be required to compute defs.
        refs = set(body_refs(cell))
        if cid in output_cell_ids:
            refs |= display_refs(cell)
        for ref in refs:
            require_name(ref)

        needed.add(cid)
        visiting_cells.remove(cid)

    for name in root_names:
        require_name(name)

    for cid in output_cell_ids:
        require_cell(cid)

    return TargetPlan(
        kind=kind,
        root_names=root_names,
        expression_refs=target_expression_refs,
        dirty_cells=needed,
        live_values=live_values,
        override_refs=override_refs,
        object_patch_roots=object_patch_roots,
    )
