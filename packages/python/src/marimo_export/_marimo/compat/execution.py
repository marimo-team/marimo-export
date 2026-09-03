"""Execute normalized states and materialize native cache receipts."""

from __future__ import annotations

import gc
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from marimo_export._execution.plan import ExecutionPlan, NormalizedState
from marimo_export._json import (
    JsonValue,
    json_equal,
)
from marimo_export._marimo.capabilities import NativeReceipt, PreparedExporter, StateExecution
from marimo_export._marimo.compat.cache.attempts import (
    CacheAttemptLog,
    force_cache_misses,
    track_notebook_cache,
)
from marimo_export._marimo.compat.child_run import (
    StateChild,
    open_state_child,
    raise_child_errors,
    raise_stopped_output,
    run_state_child,
)
from marimo_export._marimo.compat.inspection import _ui_baseline_value
from marimo_export._marimo.compat.projections import materialize_projection_tokens
from marimo_export._marimo.compat.receipts import collect_output_receipts
from marimo_export.errors import ExecutionError, OutputError
from marimo_export.index import ControlBinding
from marimo_export.result import StateRunTimings
from marimo_export.spec import CellSource


@dataclass(frozen=True, slots=True)
class _StateRunPlan:
    available_cells: frozenset[Any]
    complete_cell_owners: frozenset[Any]
    execution_order: tuple[Any, ...]
    output_cells: frozenset[Any]
    output_dependencies: Mapping[str, frozenset[Any]]
    output_owners: Mapping[str, Any]
    transient_cache_cells: frozenset[Any]
    ui_input_cells: frozenset[Any]
    ui_update_batches: tuple[tuple[Any, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class _InputPhase:
    dependency_seconds: float
    ui_update_seconds: float
    finalization_seconds: float


@dataclass(frozen=True, slots=True)
class _OutputPhase:
    receipts: tuple[NativeReceipt, ...]
    control_bindings: Mapping[str, ControlBinding]
    seconds: float


async def execute_state(
    state: NormalizedState,
    plan: ExecutionPlan,
    exporters: Mapping[str, PreparedExporter],
    implementation_sha256: str,
    producer_identity: str,
) -> StateExecution:
    """Execute one state run through marimo's graph and cell cache."""

    state_child: StateChild | None = None
    input_phase: _InputPhase | None = None
    output_phase: _OutputPhase | None = None
    notebook_cache = CacheAttemptLog()
    setup_seconds = 0.0
    cleanup_seconds = 0.0
    try:
        with open_state_child(
            state=state,
            plan=plan,
            exporters=exporters,
            implementation_sha256=implementation_sha256,
            producer_identity=producer_identity,
        ) as state_child:
            setup_seconds = state_child.setup_seconds
            run_plan = _plan_state_run(
                child=state_child,
                state=state,
                plan=plan,
            )
            with track_notebook_cache(
                state_child.runner._kernel.graph,
                run_plan.transient_cache_cells,
            ) as notebook_cache:
                input_phase = await _execute_inputs(state_child, state, run_plan)
                output_phase = await _execute_outputs(
                    state_child,
                    state,
                    plan,
                    run_plan,
                    notebook_cache,
                )
    finally:
        if state_child is not None:
            cleanup_seconds = state_child.cleanup_seconds
        state_child = None
        gc.collect()
    if input_phase is None or output_phase is None:
        raise RuntimeError("state run produced no output receipts")
    return StateExecution(
        receipts=output_phase.receipts,
        control_bindings=output_phase.control_bindings,
        cache=notebook_cache.activity(),
        timings=StateRunTimings(
            states=1,
            setup_seconds=setup_seconds,
            dependency_execution_seconds=input_phase.dependency_seconds,
            ui_update_seconds=input_phase.ui_update_seconds,
            output_materialization_seconds=(
                input_phase.finalization_seconds + output_phase.seconds
            ),
            cleanup_seconds=cleanup_seconds,
        ),
    )


def _plan_state_run(
    *,
    child: StateChild,
    state: NormalizedState,
    plan: ExecutionPlan,
) -> _StateRunPlan:
    from marimo._runtime.dataflow import prune_cells_for_overrides, transitive_closure
    from marimo._types.ids import CellId_t

    graph = child.runner._kernel.graph
    overrides = {plan.state_name: state.fingerprint}
    execution_order = tuple(
        prune_cells_for_overrides(
            child.internal.graph,
            child.internal.execution_order,
            overrides,
        )
    )
    output_cells = frozenset(child.output_cell_ids.values())
    available_cells = frozenset(
        cell_id
        for cell_id in execution_order
        if cell_id not in output_cells and not child.internal.graph.is_disabled(cell_id)
    )
    complete_cell_owners: set[CellId_t] = set()
    output_owners: dict[str, CellId_t] = {}
    for output, planned_output in plan.planned_outputs.items():
        owner_cell_id = planned_output.owner_cell_id
        runtime_id = child.cell_ids.get(owner_cell_id) if owner_cell_id is not None else None
        if runtime_id is None:
            raise OutputError(
                f"output {output!r} has no authored owner cell",
                code="output_execution_failed",
            )
        runtime_cell_id = CellId_t(runtime_id)
        output_owners[output] = runtime_cell_id
        if isinstance(planned_output.source, CellSource):
            complete_cell_owners.add(runtime_cell_id)
    ui_input_cells = frozenset(
        cell_id for name in state.ui_updates for cell_id in graph.get_defining_cells(name)
    )
    return _StateRunPlan(
        available_cells=available_cells,
        complete_cell_owners=frozenset(complete_cell_owners),
        execution_order=execution_order,
        output_cells=output_cells,
        output_dependencies={
            output: frozenset(
                transitive_closure(
                    graph,
                    {owner_cell_id},
                    children=False,
                )
            )
            for output, owner_cell_id in output_owners.items()
        },
        output_owners=output_owners,
        transient_cache_cells=frozenset(
            {
                *output_cells,
                *child.snapshot_cell_ids.values(),
            }
        ),
        ui_input_cells=ui_input_cells,
        ui_update_batches=_ui_update_batches(state, graph, execution_order),
    )


def _live_complete_cell_owners(
    run_plan: _StateRunPlan,
    cells: set[Any],
) -> frozenset[Any]:
    return frozenset(cells & run_plan.complete_cell_owners)


async def _execute_inputs(
    child: StateChild,
    state: NormalizedState,
    run_plan: _StateRunPlan,
) -> _InputPhase:
    from marimo._runtime.dataflow import transitive_closure

    runner = child.runner
    graph = runner._kernel.graph
    initialized_cells: set[Any] = set()
    dependency_seconds = 0.0
    ui_update_seconds = 0.0
    if run_plan.ui_update_batches:
        for owner_cell_id, names in run_plan.ui_update_batches:
            owner_closure = transitive_closure(
                graph,
                {owner_cell_id},
                children=False,
            )
            initialization_cells = {
                cell_id
                for cell_id in owner_closure & run_plan.available_cells
                if cell_id not in initialized_cells or graph.cells[cell_id].stale
            }
            if initialization_cells:
                dependency_started = time.monotonic()
                with force_cache_misses(
                    graph,
                    _live_complete_cell_owners(run_plan, initialization_cells),
                ):
                    await run_state_child(runner, initialization_cells)
                dependency_seconds += time.monotonic() - dependency_started
                raise_child_errors(
                    runner,
                    initialization_cells,
                    state.primary_alias,
                    stop_provenance=child.stop_provenance,
                    source_cell_ids=child.source_cell_ids,
                )
                initialized_cells.update(initialization_cells)
            ui_started = time.monotonic()
            await _apply_ui_update_batch(
                runner,
                child.context,
                state,
                names,
            )
            ui_update_seconds += time.monotonic() - ui_started
    else:
        dependency_started = time.monotonic()
        available_cells = set(run_plan.available_cells)
        with force_cache_misses(
            graph,
            _live_complete_cell_owners(run_plan, available_cells),
        ):
            await run_state_child(runner, available_cells)
        dependency_seconds = time.monotonic() - dependency_started
        raise_child_errors(
            runner,
            available_cells,
            state.primary_alias,
            stop_provenance=child.stop_provenance,
            source_cell_ids=child.source_cell_ids,
        )
        initialized_cells.update(available_cells)

    finalization_started = time.monotonic()
    reactive_cells = (
        {
            cell_id
            for cell_id in run_plan.available_cells - initialized_cells
            if not graph.is_disabled(cell_id)
        }
        if run_plan.ui_input_cells
        else set()
    )
    if run_plan.ui_input_cells:
        reactive_cells.update(
            cell_id
            for cell_id in initialized_cells
            if graph.cells[cell_id].stale and not graph.is_disabled(cell_id)
        )
    reactive_cells.difference_update(run_plan.ui_input_cells)
    final_forced = _live_complete_cell_owners(run_plan, reactive_cells)
    if reactive_cells:
        with force_cache_misses(graph, final_forced):
            await run_state_child(runner, reactive_cells)
        raise_child_errors(
            runner,
            reactive_cells,
            state.primary_alias,
            stop_provenance=child.stop_provenance,
            source_cell_ids=child.source_cell_ids,
        )
    _validate_ui_input_trees(state, runner.globals)
    return _InputPhase(
        dependency_seconds=dependency_seconds,
        ui_update_seconds=ui_update_seconds,
        finalization_seconds=time.monotonic() - finalization_started,
    )


async def _execute_outputs(
    child: StateChild,
    state: NormalizedState,
    plan: ExecutionPlan,
    run_plan: _StateRunPlan,
    cache: CacheAttemptLog,
) -> _OutputPhase:
    started = time.monotonic()
    runner = child.runner
    graph = runner._kernel.graph
    for output in plan.outputs:
        raise_stopped_output(
            state_name=state.primary_alias,
            output=output,
            owner_cell=run_plan.output_owners[output],
            dependency_cells=run_plan.output_dependencies[output],
            source_cell_ids=child.source_cell_ids,
            stop_provenance=child.stop_provenance,
        )
    materialize_projection_tokens(child, plan)
    control_bindings = _control_binding_mapping(
        state,
        runner.globals,
        child.recording.ui_scopes,
    )
    forced_output_cells = frozenset(
        child.output_cell_ids[output]
        for output, planned_output in plan.planned_outputs.items()
        if planned_output.exporter is not None
        and (
            ":" in planned_output.exporter.name
            or planned_output.exporter.name == "anywidget.bundle"
        )
    )
    if forced_output_cells:
        with force_cache_misses(graph, forced_output_cells):
            await run_state_child(runner, set(run_plan.output_cells))
    else:
        await run_state_child(runner, set(run_plan.output_cells))
    for output, cell_id in child.output_cell_ids.items():
        raise_child_errors(
            runner,
            {cell_id},
            state.primary_alias,
            output=output,
            stop_provenance=child.stop_provenance,
            source_cell_ids=child.source_cell_ids,
        )
    receipts = collect_output_receipts(
        child=child,
        state_name=state.primary_alias,
        outputs=plan.outputs,
        planned_outputs=plan.planned_outputs,
        output_cell_ids=child.output_cell_ids,
        cache=cache,
    )
    return _OutputPhase(
        receipts=receipts,
        control_bindings=control_bindings,
        seconds=time.monotonic() - started,
    )


def _ui_update_batches(
    state: NormalizedState,
    graph: Any,
    execution_order: tuple[Any, ...] | list[Any],
) -> tuple[tuple[Any, tuple[str, ...]], ...]:
    names_by_cell: dict[Any, list[str]] = {}
    for name in state.ui_updates:
        defining_cells = tuple(graph.get_defining_cells(name))
        if len(defining_cells) != 1:
            raise ExecutionError(
                f"state {state.primary_alias!r} input {name!r} has no unique defining cell",
                code="input_value_invalid",
                details={"state": state.primary_alias, "input": name},
            )
        names_by_cell.setdefault(defining_cells[0], []).append(name)
    ordered = tuple(
        (cell_id, tuple(names_by_cell[cell_id]))
        for cell_id in execution_order
        if cell_id in names_by_cell
    )
    if len(ordered) != len(names_by_cell):
        missing = next(cell_id for cell_id in names_by_cell if cell_id not in execution_order)
        raise ExecutionError(
            f"state {state.primary_alias!r} UI input cell {missing!s} is unavailable",
            code="input_value_invalid",
            details={"state": state.primary_alias, "cell_id": str(missing)},
        )
    return ordered


async def _apply_ui_update_batch(
    child: Any,
    child_context: Any,
    state: NormalizedState,
    names: tuple[str, ...],
) -> None:
    from marimo._plugins.ui._core.ui_element import UIElement
    from marimo._runtime.commands import UpdateUIElementCommand

    from marimo_export._marimo.compat.inspection import _is_sensitive

    elements: list[tuple[str, UIElement[Any, Any]]] = []
    values: list[JsonValue] = []
    with child_context.install():
        child_context.ui_element_registry.register_scope(
            child.globals,
            defs=set(names),
        )
    for name in names:
        element = child.globals.get(name)
        if not isinstance(element, UIElement):
            raise ExecutionError(
                f"state {state.primary_alias!r} input {name!r} did not create a UI element",
                code="input_value_invalid",
                details={"state": state.primary_alias, "input": name},
            )
        if _is_sensitive(element):
            raise ExecutionError(
                f"state {state.primary_alias!r} input {name!r} contains sensitive controls",
                code="input_value_invalid",
                details={"state": state.primary_alias, "input": name},
            )
        actual = _ui_baseline_value(
            element,
            f"state {state.primary_alias!r} input {name!r}",
        )
        if not json_equal(actual, state.inputs[name]):
            elements.append((name, element))
            values.append(state.ui_updates[name])
    if elements:
        callback_errors: list[tuple[str, Exception]] = []
        execution_mode = child._kernel.reactive_execution_mode
        try:
            child._kernel.reactive_execution_mode = "lazy"
            with _capture_ui_callback_errors(elements, callback_errors):
                updated = await child.set_ui_element_value(
                    UpdateUIElementCommand(
                        object_ids=[element._id for _, element in elements],
                        values=values,
                    ),
                    notify_frontend=False,
                )
        finally:
            child._kernel.reactive_execution_mode = execution_mode
        if callback_errors:
            input_name, callback_error = callback_errors[0]
            raise ExecutionError(
                f"state {state.primary_alias!r} input {input_name!r} callback failed",
                code="input_value_invalid",
                details={
                    "state": state.primary_alias,
                    "input": input_name,
                    "exception_type": type(callback_error).__name__,
                },
            ) from callback_error
        if not updated:
            raise ExecutionError(
                f"state {state.primary_alias!r} UI values were not applied",
                code="input_value_invalid",
                details={
                    "state": state.primary_alias,
                    "inputs": [name for name, _ in elements],
                },
            )
    for name in names:
        expected = state.inputs[name]
        actual = _ui_baseline_value(
            child.globals[name],
            f"state {state.primary_alias!r} input {name!r}",
        )
        if not json_equal(actual, expected):
            raise ExecutionError(
                f"state {state.primary_alias!r} input {name!r} rejected its value",
                code="input_value_invalid",
                details={"state": state.primary_alias, "input": name},
            )


def _validate_ui_input_trees(
    state: NormalizedState,
    glbls: Mapping[str, object],
) -> None:
    from marimo._plugins.ui._core.ui_element import UIElement

    from marimo_export._marimo.compat.inspection import _is_sensitive

    for name in state.ui_updates:
        element = glbls.get(name)
        if isinstance(element, UIElement) and _is_sensitive(element):
            raise ExecutionError(
                f"state {state.primary_alias!r} input {name!r} contains sensitive controls",
                code="input_value_invalid",
                details={"state": state.primary_alias, "input": name},
            )


def _control_binding_mapping(
    state: NormalizedState,
    glbls: Mapping[str, object],
    ui_scopes: Mapping[str, set[str]],
) -> Mapping[str, ControlBinding]:
    from marimo._plugins.ui._core.ui_element import UIElement

    from marimo_export._marimo.compat.inspection import _control_tree_entries

    result: dict[str, ControlBinding] = {}
    for input_name in sorted(state.ui_updates):
        root = glbls.get(input_name)
        if not isinstance(root, UIElement):
            raise ExecutionError(
                f"state {state.primary_alias!r} input {input_name!r} has no UI control tree",
                code="control_input_invalid",
                details={"state": state.primary_alias, "input": input_name},
            )
        for element, path in _control_tree_entries(root):
            binding = ControlBinding(input=input_name, path=path)
            for object_id in sorted(ui_scopes.get(str(element._id), ())):
                previous = result.setdefault(object_id, binding)
                if previous != binding:
                    raise ExecutionError(
                        f"UI object {object_id!r} belongs to multiple export inputs",
                        code="control_input_conflict",
                        details={
                            "state": state.primary_alias,
                            "object_id": object_id,
                            "inputs": sorted({previous.input, input_name}),
                        },
                    )
    return MappingProxyType(result)


@contextmanager
def _capture_ui_callback_errors(
    elements: list[tuple[str, Any]],
    errors: list[tuple[str, Exception]],
) -> Iterator[None]:
    originals: list[tuple[Any, Any]] = []
    try:
        for name, element in elements:
            callback = getattr(element, "_on_change", None)
            if callback is None:
                continue

            def wrapped(value: object, *, _name: str = name, _callback: Any = callback) -> Any:
                try:
                    return _callback(value)
                except Exception as error:
                    errors.append((_name, error))
                    raise

            originals.append((element, callback))
            element._on_change = wrapped
        yield
    finally:
        for element, callback in originals:
            element._on_change = callback


__all__ = ["execute_state"]
