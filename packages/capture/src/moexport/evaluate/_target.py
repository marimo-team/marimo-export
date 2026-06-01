"""Single-target evaluation over the live marimo runtime.

This module composes the evaluator pipeline for one target and one scenario:
complete overrides, plan required cells, execute or cache them locally, patch
objects, evaluate the final target value, and package trace metadata for export
provenance.
"""

from __future__ import annotations

import ast
import inspect
import time
from typing import Any

from marimo._runtime.context import get_context
from marimo._runtime.dataflow import topological_sort

from moexport.evaluate._execution import (
    cell_cache_key,
    evaluate_cell_output,
    execute_cell_body,
)
from moexport.evaluate._mermaid import trace_mermaid
from moexport.evaluate._object_patches import (
    apply_object_patches,
    object_patch_roots,
)
from moexport.evaluate._overrides import complete_overrides
from moexport.evaluate._planning import plan_target
from moexport.evaluate._trace import build_trace_metadata
from moexport.evaluate._types import (
    ActiveEvaluation,
    CellCache,
    DefinitionOverrides,
    JsonDict,
    ObjectPatches,
    TargetRunResult,
)
from moexport.evaluate._values import value_preview
from moexport.runtime import (
    bind_runtime,
    expression_globals as moexport_expression_globals,
    selected_output_cell_ids,
)


async def evaluate_target_once(
    target: str,
    definition_overrides: DefinitionOverrides,
    *,
    object_patches: ObjectPatches,
    cell_cache: CellCache,
    output_cell_ids: set[Any] | None = None,
    output_error_policy: str = "raise",
) -> TargetRunResult:
    """Evaluate a notebook definition or Python expression in the live runtime.

    Existing values are reused from the live session unless an explicit override
    marks them dirty. The returned trace includes graph and execution metadata
    for the TS producer to explain the run without transporting Python object
    handles out of the kernel.
    """
    ctx = get_context()
    graph = ctx.graph
    override_completion = await complete_overrides(graph, ctx, definition_overrides)
    patch_roots = object_patch_roots(object_patches)
    selected_cell_ids = (
        selected_output_cell_ids(target, ctx)
        if output_cell_ids is None
        else output_cell_ids
    )
    plan = plan_target(
        graph,
        ctx,
        target,
        override_completion.values,
        override_completion.explicit_names,
        patch_roots,
        selected_cell_ids,
    )

    pruned = {
        cid
        for name in override_completion.values
        if name in graph.definitions
        for cid in graph.get_defining_cells(name)
    }
    required = plan.dirty_cells | pruned
    required_order = topological_sort(graph, required)
    # Cells whose definitions are supplied by completed overrides are pruned:
    # executing them would overwrite the requested replacement values.
    execution_order = [cid for cid in required_order if cid not in pruned]

    executed = []
    cached = []
    skipped = {}
    outputs: dict[str, Any] = {}
    computed_defs: dict[str, Any] = {}
    per_cell_defs: dict[str, dict[str, Any]] = {}
    timings: dict[str, float] = {}
    applied_object_patches: list[JsonDict] = []
    glbls: dict[str, Any] = {**plan.live_values, **override_completion.values}
    started = time.perf_counter()

    for cid in execution_order:
        if cid == ctx.cell_id:
            skipped[cid] = "current_cell"
            continue

        if graph.is_disabled(cid):
            skipped[cid] = "disabled"
            continue

        cell = graph.cells[cid]
        cache_key = cell_cache_key(cell, glbls, object_patches)
        patched_roots = cell.defs & patch_roots
        cacheable = not patched_roots
        t0 = time.perf_counter()

        if cacheable and cache_key in cell_cache:
            produced_defs = dict(cell_cache[cache_key])
            glbls.update(produced_defs)
            cached.append(cid)
        else:
            # This is a side computation, not marimo's reactive runner. It
            # executes the planned body into local glbls and leaves notebook
            # state/frontend notifications untouched. The per-call cache
            # assumes cell bodies are pure with respect to produced defs. Dirty
            # side effects are intentionally not replayed across batch variants.
            with ctx.with_cell_id(cid):
                await execute_cell_body(cell, glbls)

            produced_defs = {name: glbls[name] for name in cell.defs if name in glbls}
            if cacheable:
                cell_cache[cache_key] = produced_defs
            executed.append(cid)

        if patched_roots:
            applied_object_patches.extend(
                apply_object_patches(
                    glbls,
                    object_patches,
                    roots=set(patched_roots),
                )
            )

        with ctx.with_cell_id(cid):
            output = await evaluate_cell_output(
                cell,
                glbls,
                ctx,
                on_error=output_error_policy,
            )
        outputs[str(cid)] = output
        per_cell_defs[str(cid)] = produced_defs
        computed_defs.update(produced_defs)
        timings[str(cid)] = time.perf_counter() - t0

    if plan.kind == "definition":
        value = glbls[target] if target in glbls else ctx.globals[target]
    else:
        # Expressions run against live globals plus locally computed defs. This
        # intentionally exposes real live objects. Callers must avoid mutating
        # them in-place when they need a read-only export probe.
        expression_globals = {
            **ctx.globals,
            **glbls,
            **moexport_expression_globals(),
        }
        with bind_runtime(ActiveEvaluation(runtime=ctx, outputs=outputs)):
            value = eval(
                compile(ast.parse(target, mode="eval"), "<moexport>", "eval"),
                expression_globals,
            )
        if inspect.isawaitable(value):
            value = await value

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    trace = build_trace_metadata(
        graph=graph,
        ctx=ctx,
        required_order=required_order,
        execution_order=execution_order,
        executed=executed,
        cached=cached,
        skipped=skipped,
        pruned=pruned,
        required=required,
        completed_overrides=override_completion.values,
        per_cell_defs=per_cell_defs,
        outputs=outputs,
        timings=timings,
        elapsed_ms=elapsed_ms,
    )

    metadata: JsonDict = {
        "target": {
            "root_names": plan.root_names,
            "expression_refs": plan.expression_refs,
            "override_refs": sorted(plan.override_refs),
            "object_patch_refs": sorted(object_patches),
        },
        "state": {"applied_object_patches": applied_object_patches},
        "graph": trace.graph,
        "execution": trace.execution,
    }
    metadata["mermaid"] = trace_mermaid(metadata)

    return {
        "kind": plan.kind,
        "value": value,
        "value_preview": value_preview(value),
        "outputs": outputs,
        "defs": {**plan.live_values, **override_completion.values, **computed_defs},
        "computed_defs": computed_defs,
        "live_values": plan.live_values,
        "live_value_previews": {
            name: value_preview(value) for name, value in plan.live_values.items()
        },
        "auto_filled_overrides": override_completion.auto_filled,
        "metadata": metadata,
    }
