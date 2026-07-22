from __future__ import annotations

import asyncio
import copy
import sys
import threading
import weakref
from collections.abc import Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from marimo._ast.app import AppKernelRunnerRegistry, InternalApp
from marimo._ast.cell import CellConfig
from marimo._plugins.ui._core.ui_element import UIElement
from marimo._runtime.app.kernel_runner import AppKernelRunner
from marimo._runtime.commands import UpdateUIElementCommand
from marimo._runtime.dataflow import prune_cells_for_overrides, topological_sort
from marimo._runtime.runner import cell_runner, hook_context
from marimo._runtime.runner.hooks import NotebookCellHooks
from marimo._schemas.serialization import CellDef
from marimo._types.ids import CellId_t

from marimo_export import Projection
from marimo_export._json import json_identity, json_object
from marimo_export._marimo.anywidget import (
    detach_anywidget_capture,
    install_anywidget_capture,
)
from marimo_export._marimo.cache import flush_caches, polars_cache_restore_scope, put_payload
from marimo_export._marimo.context import NotebookSnapshot, root_context, snapshot_app
from marimo_export._marimo.execution import (
    prepare_projection_cache_tokens,
    run_preparation_cells,
    run_projection_cells,
    run_to_quiescence,
)
from marimo_export.index import PayloadRef, ProjectionEntry, ScenarioIndex
from marimo_export.plan import ExportPlan, Scenario
from marimo_export.projection.synthetic_cells import (
    ProjectionBinding,
    SyntheticPreparationCell,
    SyntheticProjectionCell,
    projection_binding,
)


class _ScenarioGate:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.owner_lock = threading.Lock()
        self.owner: asyncio.Task[Any] | None = None

    @asynccontextmanager
    async def enter(self) -> Any:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("scenario execution requires an asyncio task")
        with self.owner_lock:
            if self.owner is task:
                raise RuntimeError("nested scenario execution in one task is unsupported")
        while not self.lock.acquire(blocking=False):
            await asyncio.sleep(0.001)
        with self.owner_lock:
            self.owner = task
        try:
            yield
        finally:
            with self.owner_lock:
                self.owner = None
            self.lock.release()


_PROCESS_SCENARIO_GATE = _ScenarioGate()


@dataclass(frozen=True)
class _SyntheticCellIds:
    projections: dict[SyntheticProjectionCell, CellId_t]
    preparations: dict[SyntheticPreparationCell, CellId_t]


class _ScenarioRunnerRegistry(AppKernelRunnerRegistry):
    def get_runner(self, app: Any) -> AppKernelRunner:
        previous_argv = sys.argv
        parent_argv = list(previous_argv)
        previous_path = sys.path[:]
        try:
            runner = super().get_runner(app)
            runner._kernel.argv = [
                runner._kernel.app_metadata.filename or "",
                *parent_argv[1:],
            ]
            runner_path = sys.path[:]
        finally:
            sys.argv = previous_argv
            sys.path[:] = previous_path
        if not getattr(runner, "_marimo_export_process_state_scoped", False):
            _scope_nested_runner_process_state(runner, runner_path)
        return runner


def _scope_nested_runner_process_state(
    runner: AppKernelRunner,
    runner_path: list[str],
) -> None:
    for name in ("run", "set_ui_element_value", "function_call"):
        method = getattr(runner, name)

        async def scoped(*args: Any, _method: Any = method, **kwargs: Any) -> Any:
            previous_argv = sys.argv
            previous_path = sys.path[:]
            sys.argv = runner._kernel.argv
            sys.path[:] = runner_path
            try:
                return await _method(*args, **kwargs)
            finally:
                sys.argv = previous_argv
                sys.path[:] = previous_path

        setattr(runner, name, scoped)
    setattr(runner, "_marimo_export_process_state_scoped", True)  # noqa: B010


async def run_scenario_in_child(
    plan: ExportPlan,
    scenario: Scenario,
    snapshot_value: object,
) -> ScenarioIndex:
    if not isinstance(snapshot_value, NotebookSnapshot):
        raise TypeError("scenario execution requires a notebook snapshot")

    async with _PROCESS_SCENARIO_GATE.enter():
        with polars_cache_restore_scope():
            return await _run_scenario_in_child(plan, scenario, snapshot_value)


async def _run_scenario_in_child(
    plan: ExportPlan,
    scenario: Scenario,
    snapshot_value: NotebookSnapshot,
) -> ScenarioIndex:
    context = root_context()
    previous_argv = sys.argv
    previous_path = sys.path[:]
    sys.argv = context.argv
    notebook_directory = str(snapshot_value.path.parent)
    sys.path[:] = [notebook_directory, *(path for path in sys.path if path != notebook_directory)]
    try:
        flush_caches()
        app = snapshot_app(snapshot_value)
        internal = InternalApp(app)
        graph = internal.graph
        authored_ids = set(internal.cell_manager.valid_cell_ids())
        bindings = _projection_bindings(plan, set(graph.definitions))

        definitions, ui_values = _scenario_bindings(plan, scenario)
        _validate_input_targets(internal, definitions, ui_values)
        authored_order = [
            cell_id for cell_id in topological_sort(graph, authored_ids) if cell_id in authored_ids
        ]
        authored_order = prune_cells_for_overrides(graph, authored_order, definitions)

        registry = context.app_kernel_runner_registry
        runner: AppKernelRunner | None = None
        try:
            runner = registry.get_runner(app)
            return await _execute_scenario(
                runner,
                plan,
                scenario,
                app,
                bindings,
                authored_order,
                definitions,
                ui_values,
            )
        finally:
            if runner is not None:
                try:
                    _release_runner(registry, app, runner)
                finally:
                    # Widget comms retain their opening stream. Keep that stream
                    # attached through lifecycle disposal so model closes reach
                    # the parent, then revoke every copied capture stream.
                    detach_anywidget_capture(runner)
    finally:
        sys.argv = previous_argv
        sys.path[:] = previous_path


async def _execute_scenario(
    runner: AppKernelRunner,
    plan: ExportPlan,
    scenario: Scenario,
    app: Any,
    bindings: list[ProjectionBinding],
    authored_order: list[CellId_t],
    definitions: dict[str, Any],
    ui_values: dict[str, Any],
) -> ScenarioIndex:
    if any(binding.cell.preparation is not None for binding in bindings):
        install_anywidget_capture(runner)
    _configure_child_kernel(runner, root_context())
    runner.globals.update(definitions)
    failures: dict[CellId_t, object] = {}

    def capture_failure(
        cell: Any,
        context: hook_context.PostExecutionHookContext,
        result: cell_runner.RunResult,
    ) -> None:
        del context
        if result.exception is not None:
            failures[cell.cell_id] = result.exception
        else:
            failures.pop(cell.cell_id, None)

    def activate_child_argv(cell: Any, context: Any) -> None:
        del cell, context
        sys.argv = runner._kernel.argv

    runner._kernel._hooks.add_pre_execution(activate_child_argv)
    runner._kernel._hooks.add_post_execution(capture_failure)

    authored_ids = set(authored_order)
    if ui_values:
        await _run_with_ui_inputs(
            runner,
            authored_order,
            authored_ids,
            ui_values,
            failures,
        )
    elif authored_ids:
        await run_to_quiescence(runner, authored_ids, authored_ids)
        _raise_failures(failures.values())

    synthetic_ids = _append_projection_cells(app, bindings)
    projection_graph = InternalApp(app).graph
    if synthetic_ids.preparations:
        await run_preparation_cells(
            runner,
            projection_graph,
            set(synthetic_ids.preparations.values()),
        )
        _raise_failures(failures.values())

    projection_ids = set(synthetic_ids.projections.values())
    if projection_ids:
        await prepare_projection_cache_tokens(
            runner,
            projection_graph,
            synthetic_ids.projections,
            lambda: _raise_failures(failures.values()),
        )
        await run_projection_cells(
            runner,
            projection_graph,
            projection_ids,
        )
        _raise_failures(failures.values())

    entries: dict[SyntheticProjectionCell, ProjectionEntry] = {}
    for synthetic, cell_id in synthetic_ids.projections.items():
        result = runner.outputs.get(cell_id)
        if not isinstance(result, Projection):
            raise TypeError(f"projection cell {synthetic.result_name!r} returned no Projection")
        key, digest, size = put_payload(result.payload)
        entries[synthetic] = ProjectionEntry(
            format_id=result.format_id,
            media_type=result.media_type,
            metadata=json_object(result.metadata, "projection.metadata"),
            payload=PayloadRef(key=key, sha256=digest, size=size),
        )

    outputs: dict[str, dict[str, ProjectionEntry]] = {}
    for binding in bindings:
        outputs.setdefault(binding.output_name, {})[binding.format_name] = entries[binding.cell]
    return ScenarioIndex(id=scenario.id, inputs=scenario.inputs, outputs=outputs)


def _release_runner(registry: Any, app: Any, runner: AppKernelRunner) -> None:
    failures: list[Exception] = []
    try:
        flush_caches()
    except Exception as error:
        failures.append(error)

    nested_registry = runner._runtime_context.app_kernel_runner_registry
    if nested_registry is not registry:
        try:
            _release_registered_runners(nested_registry)
        except Exception as error:
            failures.append(error)

    for release in (
        lambda: _teardown_child_resources(runner),
        lambda: registry.remove_runner(app),
        lambda: _finalize_runner_context(runner),
    ):
        try:
            release()
        except Exception as error:
            failures.append(error)

    _raise_teardown_failure("embedded marimo runner teardown failed", failures)


def _release_registered_runners(registry: AppKernelRunnerRegistry) -> None:
    registered = getattr(registry, "_runners", {})
    runners = [
        (app, runner) for app_runners in registered.values() for app, runner in app_runners.items()
    ]
    runners.sort(key=lambda item: _runtime_context_depth(item[1]), reverse=True)
    failures: list[Exception] = []
    for app, runner in runners:
        try:
            _release_runner(registry, app, runner)
        except Exception as error:
            failures.append(error)
    registry.shutdown()
    _raise_teardown_failure("nested marimo runner teardown failed", failures)


def _runtime_context_depth(runner: AppKernelRunner) -> int:
    depth = 0
    context = runner._runtime_context
    while context.parent is not None:
        depth += 1
        context = context.parent
    return depth


def _teardown_child_resources(runner: AppKernelRunner) -> None:
    runtime_context = runner._runtime_context
    failures: list[Exception] = []
    try:
        with runtime_context.install():
            lifecycle = runtime_context.cell_lifecycle_registry
            for cell_id in list(lifecycle.registry):
                try:
                    lifecycle.dispose(cell_id, deletion=True)
                except Exception as error:
                    failures.append(error)
            lifecycle.registry.clear()
            runner._kernel._hooks = NotebookCellHooks()
            runner.outputs.clear()
            runner.globals.clear()
    finally:
        try:
            runner._kernel.autoreload_manager.teardown()
        except Exception as error:
            failures.append(error)
    _raise_teardown_failure("embedded marimo resources failed to release", failures)


def _raise_teardown_failure(message: str, failures: list[Exception]) -> None:
    if len(failures) == 1:
        raise failures[0]
    if len(failures) > 1:
        raise RuntimeError(f"{message} ({len(failures)} errors)") from failures[0]


def _finalize_runner_context(runner: AppKernelRunner) -> None:
    runtime_context = runner._runtime_context
    parent = runtime_context.parent
    if parent is None:
        raise RuntimeError("embedded marimo runner has no parent context")

    for reference in weakref.getweakrefs(runner):
        callback = reference.__callback__
        if not isinstance(callback, weakref.finalize):
            continue
        state = callback.peek()
        if state is None:
            continue
        owner, function, args, kwargs = state
        if (
            owner is runner
            and function == parent.remove_child
            and args == (runtime_context,)
            and kwargs == {}
        ):
            callback()
            return
    raise RuntimeError("marimo runner context finalizer was not found")


def _projection_bindings(
    plan: ExportPlan,
    reserved_names: set[str],
) -> list[ProjectionBinding]:
    resolved_cells: dict[SyntheticProjectionCell, SyntheticProjectionCell] = {}
    bindings: list[ProjectionBinding] = []
    for output in plan.outputs:
        for format_plan in output.formats:
            base = projection_binding(
                output_name=output.name,
                format_name=format_plan.name,
                source=output.source,
                format_plan=format_plan,
            )
            cell = resolved_cells.get(base.cell)
            if cell is None:
                token_name = base.cell.cache_token_name
                suffix = 1
                while token_name in reserved_names:
                    token_name = f"{base.cell.cache_token_name}_{suffix}"
                    suffix += 1
                if token_name == base.cell.cache_token_name:
                    cell = base.cell
                else:
                    cell = projection_binding(
                        output_name=output.name,
                        format_name=format_plan.name,
                        source=output.source,
                        format_plan=format_plan,
                        _cache_token_name=token_name,
                    ).cell
                resolved_cells[base.cell] = cell
                reserved_names.add(token_name)
            bindings.append(
                ProjectionBinding(
                    output_name=output.name,
                    format_name=format_plan.name,
                    cell=cell,
                )
            )
    return bindings


def _append_projection_cells(
    app: Any,
    bindings: list[ProjectionBinding],
) -> _SyntheticCellIds:
    internal = InternalApp(app)
    projections: dict[SyntheticProjectionCell, CellId_t] = {}
    preparations: dict[SyntheticPreparationCell, CellId_t] = {}
    for binding in bindings:
        item = binding.cell
        if item in projections:
            continue
        preparation = item.preparation
        if preparation is not None and preparation not in preparations:
            cell_id, cell = _register_synthetic_cell(
                internal,
                preparation.code,
                preparation.result_name,
                "preparation",
            )
            if preparation.result_name not in cell._cell.defs:
                raise RuntimeError("synthetic preparation cell lost its payload definition")
            preparations[preparation] = cell_id

        cell_id, cell = _register_synthetic_cell(
            internal,
            item.code,
            item.result_name,
            "projection",
        )
        if item.cache_token_name not in cell._cell.refs:
            raise RuntimeError("synthetic projection cell lost its cache token reference")
        projections[item] = cell_id
    return _SyntheticCellIds(projections=projections, preparations=preparations)


def _register_synthetic_cell(
    internal: InternalApp,
    code: str,
    name: str,
    kind: str,
) -> tuple[CellId_t, Any]:
    previous = set(internal.cell_manager.valid_cell_ids())
    internal.cell_manager.register_ir_cell(
        CellDef(
            code=code,
            name=f"_{name}",
            options=CellConfig(hide_code=True).asdict_without_defaults(),
        ),
        internal,
    )
    added = set(internal.cell_manager.valid_cell_ids()) - previous
    if len(added) != 1:
        raise RuntimeError(f"failed to register synthetic {kind} cell")
    cell_id = next(iter(added))
    cell = internal.cell_manager.cell_data_at(cell_id).cell
    if cell is None:
        raise RuntimeError(f"failed to compile synthetic {kind} cell")
    internal.graph.register_cell(cell_id, cell._cell)
    return cell_id, cell


def _scenario_bindings(
    plan: ExportPlan,
    scenario: Scenario,
) -> tuple[dict[str, Any], dict[str, Any]]:
    definitions: dict[str, Any] = {}
    ui_values: dict[str, Any] = {}
    inputs = {item.name: item for item in plan.inputs}
    for name, value in scenario.inputs.items():
        binding = inputs[name].binding
        target = definitions if binding.kind == "definition" else ui_values
        target[binding.target] = copy.deepcopy(value)
    return definitions, ui_values


def _validate_input_targets(
    app: InternalApp,
    definitions: dict[str, Any],
    ui_values: dict[str, Any],
) -> None:
    graph = app.graph
    setup_cell_id = app.cell_manager.setup_cell_id
    for name in definitions:
        defining_cells = graph.get_defining_cells(name)
        if not defining_cells:
            raise NameError(f"definition input target is not defined by the notebook: {name!r}")
        if setup_cell_id in defining_cells:
            raise ValueError(f"definition input target is owned by the notebook setup: {name!r}")
    for name in ui_values:
        if name not in graph.definitions:
            raise NameError(f"UI input target is not defined by the notebook: {name!r}")


def _ordered_ui_inputs(
    graph: Any,
    authored_order: list[CellId_t],
    ui_values: dict[str, Any],
) -> list[str]:
    rank = {cell_id: index for index, cell_id in enumerate(authored_order)}

    def target_rank(name: str) -> int:
        cells = graph.get_defining_cells(name)
        return min((rank.get(cell_id, len(rank)) for cell_id in cells), default=len(rank))

    return sorted(ui_values, key=target_rank)


def _ui_creators(
    graph: Any,
    authored_ids: set[CellId_t],
    ordered_names: list[str],
) -> dict[str, CellId_t]:
    creators: dict[str, CellId_t] = {}
    for name in ordered_names:
        candidates = graph.get_defining_cells(name) & authored_ids
        if len(candidates) != 1:
            raise RuntimeError(f"UI input target must have one executable creator: {name!r}")
        creators[name] = next(iter(candidates))
    return creators


async def _run_with_ui_inputs(
    runner: AppKernelRunner,
    authored_order: list[CellId_t],
    authored_ids: set[CellId_t],
    ui_values: dict[str, Any],
    failures: dict[CellId_t, object],
) -> None:
    graph = runner._kernel.graph
    ordered_names = _ordered_ui_inputs(graph, authored_order, ui_values)
    creators = _ui_creators(graph, authored_ids, ordered_names)
    remaining_creators = set(creators.values())
    initialized: set[CellId_t] = set()
    applied: dict[str, UIElement[Any, Any]] = {}
    attempts: dict[str, int] = {}
    application_limit = max(32, len(authored_ids) * 4)

    async def reconcile_applied_inputs() -> None:
        await _stabilize_ui_inputs(
            runner,
            ordered_names,
            ui_values,
            applied,
            (),
            attempts,
            application_limit,
            failures,
        )

    while remaining_creators:
        creator_frontier = {
            cell_id
            for cell_id in remaining_creators
            if not (graph.ancestors(cell_id) & remaining_creators)
        }
        if not creator_frontier:
            raise RuntimeError("marimo UI input creators did not reach a frontier")

        creator_closure = creator_frontier | {
            ancestor
            for cell_id in creator_frontier
            for ancestor in graph.ancestors(cell_id)
            if ancestor in authored_ids
        }
        allowed = initialized | creator_closure
        pending = (creator_closure - initialized) | (graph.get_stale() & allowed)
        initialized = allowed
        await run_to_quiescence(
            runner,
            pending,
            initialized,
            reconcile_applied_inputs,
        )
        _raise_failures(failures.values())

        frontier_names = [name for name in ordered_names if creators[name] in creator_frontier]
        await _stabilize_ui_inputs(
            runner,
            ordered_names,
            ui_values,
            applied,
            frontier_names,
            attempts,
            application_limit,
            failures,
        )
        await run_to_quiescence(
            runner,
            graph.get_stale() & initialized,
            initialized,
            reconcile_applied_inputs,
        )
        _raise_failures(failures.values())
        remaining_creators.difference_update(creator_frontier)

    pending = (authored_ids - initialized) | (graph.get_stale() & authored_ids)
    await run_to_quiescence(
        runner,
        pending,
        authored_ids,
        reconcile_applied_inputs,
    )
    _raise_failures(failures.values())


async def _stabilize_ui_inputs(
    runner: AppKernelRunner,
    ordered_names: list[str],
    ui_values: dict[str, Any],
    applied: dict[str, UIElement[Any, Any]],
    names_to_apply: Iterable[str],
    attempts: dict[str, int],
    application_limit: int,
    failures: dict[CellId_t, object],
) -> None:
    pending = set(names_to_apply)
    pending.update(_rebound_ui_inputs(runner, applied, failures))

    while pending:
        ordered_pending = [name for name in ordered_names if name in pending]
        for name in ordered_pending:
            attempts[name] = attempts.get(name, 0) + 1
            if attempts[name] > application_limit:
                raise _ui_convergence_error(
                    runner,
                    ordered_pending,
                    application_limit,
                )

        constrained_names = set(applied) | set(ordered_pending)
        all_values = {name: ui_values[name] for name in ordered_names if name in constrained_names}
        applied.update(
            await _apply_ui_values(
                runner,
                {name: ui_values[name] for name in ordered_pending},
                all_values=all_values,
            )
        )
        pending = set(_rebound_ui_inputs(runner, applied, failures))


def _rebound_ui_inputs(
    runner: AppKernelRunner,
    applied: dict[str, UIElement[Any, Any]],
    failures: dict[CellId_t, object],
) -> list[str]:
    graph = runner._kernel.graph
    rebound: list[str] = []
    for name, previous in applied.items():
        current = runner.globals.get(name)
        if isinstance(current, UIElement):
            if current is not previous:
                rebound.append(name)
            continue
        if graph.get_defining_cells(name) & failures.keys():
            continue
        raise TypeError(f"UI input target did not produce a UIElement: {name!r}")
    return rebound


def _ui_convergence_error(
    runner: AppKernelRunner,
    names: list[str],
    application_limit: int,
) -> RuntimeError:
    graph = runner._kernel.graph
    targets = ", ".join(
        f"{name!r} "
        f"({', '.join(sorted(str(cell_id) for cell_id in graph.get_defining_cells(name)))})"
        for name in names
    )
    return RuntimeError(
        f"marimo UI inputs did not converge within {application_limit} applications: {targets}"
    )


def _ui_element(runner: AppKernelRunner, name: str) -> UIElement[Any, Any]:
    element = runner.globals.get(name)
    if not isinstance(element, UIElement):
        raise TypeError(f"UI input target did not produce a UIElement: {name!r}")
    return element


async def _apply_ui_values(
    runner: AppKernelRunner,
    ui_values: dict[str, Any],
    *,
    all_values: dict[str, Any] | None = None,
) -> dict[str, UIElement[Any, Any]]:
    constrained_values = ui_values if all_values is None else all_values
    elements = {name: _ui_element(runner, name) for name in constrained_values}
    aliases: dict[Any, list[str]] = {}
    for name, element in elements.items():
        aliases.setdefault(element._id, []).append(name)
    for names in aliases.values():
        identities = {json_identity(constrained_values[name]) for name in names}
        if len(identities) > 1:
            targets = ", ".join(repr(name) for name in names)
            raise ValueError(
                f"UI input targets alias one element with conflicting values: {targets}"
            )

    updates: dict[Any, list[str]] = {}
    for name in ui_values:
        updates.setdefault(elements[name]._id, []).append(name)

    applied: dict[str, UIElement[Any, Any]] = {}
    execution_mode = runner._kernel.reactive_execution_mode
    try:
        runner._kernel.reactive_execution_mode = "lazy"
        for names in updates.values():
            name = names[0]
            element = elements[name]
            value = ui_values[name]
            command = UpdateUIElementCommand.from_ids_and_values([(element._id, value)])
            updated = await runner.set_ui_element_value(command, notify_frontend=False)
            if not updated:
                targets = ", ".join(repr(target) for target in names)
                raise RuntimeError(f"marimo did not apply UI input targets: {targets}")
            for target in names:
                applied[target] = elements[target]
    finally:
        runner._kernel.reactive_execution_mode = execution_mode
    return applied


def _child_config(config: Any, *, cache_cells: bool) -> Any:
    copied = copy.deepcopy(config)
    copied.setdefault("runtime", {})["cache_cells"] = cache_cells
    copied["runtime"]["auto_reload"] = "off"
    return copied


def _configure_child_kernel(runner: AppKernelRunner, context: Any) -> None:
    root_argv = context.argv
    child_config = _child_config(context.marimo_config, cache_cells=not root_argv[1:])
    kernel = runner._kernel
    runner._runtime_context.app_kernel_runner_registry = _ScenarioRunnerRegistry()
    kernel._update_runtime_from_user_config(child_config)
    kernel.execution_type = context._kernel.execution_type
    kernel.argv = [kernel.app_metadata.filename or "", *root_argv[1:]]
    sys.argv = kernel.argv


def _raise_failures(failures: Iterable[object]) -> None:
    iterator = iter(failures)
    try:
        failure = next(iterator)
    except StopIteration:
        return
    if isinstance(failure, BaseException):
        raise failure
    raise RuntimeError(str(failure))
