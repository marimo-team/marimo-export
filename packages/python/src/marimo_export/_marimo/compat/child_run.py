"""Construct, execute, and release one transient Marimo state child."""

from __future__ import annotations

import copy
import time
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from marimo_export._cell_ids import canonical_cell_id
from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._execution.plan import (
    ExecutionPlan,
    NormalizedState,
    ordinary_cell_code,
    output_cell_code,
    snapshot_token_code,
)
from marimo_export._json import JsonObject, JsonValue
from marimo_export._marimo.capabilities import PreparedExporter
from marimo_export.errors import ExecutionError, MarimoExportError, OutputError
from marimo_export.spec import CellSource, RenderedOutputSource

if TYPE_CHECKING:
    from marimo_export._marimo.compat.projections import ProjectionRecording


@dataclass(slots=True)
class StateChildOwnership:
    """Recording and cleanup state owned from child construction onward."""

    recordings: ExitStack = field(default_factory=ExitStack)
    cleanup_seconds: float = 0.0


@dataclass(slots=True)
class StopProvenance:
    """Latest exact Marimo stop relation observed across child runs."""

    descendants_by_stopping_cell: dict[Any, set[Any]] = field(default_factory=dict)
    _graph: weakref.ReferenceType[Any] | None = None

    def remember_graph(self, graph: Any) -> None:
        self._graph = None if graph is None else weakref.ref(graph)

    def record_cell(self, cell: Any, context: Any, result: Any) -> None:
        from marimo._runtime.control_flow import MarimoStopError

        self.remember_graph(context.graph)
        cell_id = cell.cell_id
        if isinstance(result.exception, MarimoStopError):
            self.descendants_by_stopping_cell.setdefault(cell_id, set())
            return
        self.descendants_by_stopping_cell.pop(cell_id, None)
        for descendants in self.descendants_by_stopping_cell.values():
            descendants.discard(cell_id)

    def record_finish(self, context: Any) -> None:
        from marimo._runtime.control_flow import MarimoStopError

        self.remember_graph(context.graph)
        cancellation_roots = set(context.cancelled_cells)
        for stopping_cell, error in context.exceptions.items():
            if not isinstance(error, MarimoStopError):
                continue
            descendants = (
                set(context.cancelled_cells[stopping_cell])
                if stopping_cell in cancellation_roots
                else set()
            )
            self.descendants_by_stopping_cell[stopping_cell] = descendants

    def stopping_cell(self, cell_id: Any) -> Any | None:
        if cell_id in self.descendants_by_stopping_cell:
            return cell_id
        recorded = next(
            (
                stopping_cell
                for stopping_cell, descendants in self.descendants_by_stopping_cell.items()
                if cell_id in descendants
            ),
            None,
        )
        graph = None if self._graph is None else self._graph()
        if recorded is not None or graph is None:
            return recorded
        from marimo._runtime.dataflow import transitive_closure

        ancestors = transitive_closure(
            graph,
            {cell_id},
            children=False,
        )
        return next(
            (
                stopping_cell
                for stopping_cell in self.descendants_by_stopping_cell
                if stopping_cell in ancestors
            ),
            None,
        )


@dataclass(slots=True)
class StateChild:
    """One configured transient child and its package-owned execution state."""

    runner: Any
    context: Any
    internal: Any
    cell_ids: Mapping[str, Any]
    source_cell_ids: Mapping[Any, str]
    output_cell_ids: Mapping[str, Any]
    snapshot_cell_ids: Mapping[str, Any]
    recording: ProjectionRecording
    stop_provenance: StopProvenance
    setup_seconds: float
    cleanup_seconds: float = 0.0


@contextmanager
def open_state_child(
    *,
    state: NormalizedState,
    plan: ExecutionPlan,
    exporters: Mapping[str, PreparedExporter],
    implementation_sha256: str,
    producer_identity: str,
) -> Iterator[StateChild]:
    """Compile and own one configured child for the complete state run."""

    from marimo._ast.app import InternalApp
    from marimo._ast.load import load_notebook_ir
    from marimo._code_mode import get_context as get_code_context
    from marimo._runtime.app.kernel_runner import AppKernelRunner
    from marimo._runtime.context import get_context as get_runtime_context
    from marimo._runtime.runner.hooks import Priority
    from marimo._runtime.runner.hooks_post_execution import (
        _broadcast_outputs,
        _flush_console,
        _set_run_result_status,
    )
    from marimo._schemas.serialization import (
        AppInstantiation,
        CellDef,
        NotebookSerializationV1,
    )

    setup_started = time.monotonic()
    code_context = get_code_context()
    runtime = get_runtime_context()
    cells = tuple(code_context.cells)
    snapshot_cell_codes = {
        output: snapshot_token_code(planned_output)
        for output, planned_output in plan.planned_outputs.items()
        if isinstance(planned_output.source, (CellSource, RenderedOutputSource))
    }
    output_cell_codes = {
        output: output_cell_code(
            planned_output,
            plan.state_name,
            implementation_identity=implementation_sha256,
            document_sha256=plan.document_sha256,
            producer_identity=producer_identity,
            exporter_identity=(exporters[output].identity if output in exporters else None),
            exporter_token=(exporters[output].token if output in exporters else None),
        )
        for output, planned_output in plan.planned_outputs.items()
    }
    notebook = NotebookSerializationV1(
        app=AppInstantiation(options=runtime.app_config.asdict()),
        cells=[
            CellDef(
                code=ordinary_cell_code(
                    cell.code,
                    plan.ordinary_cells.get(str(cell.id), ()),
                    state.ordinary_values,
                ),
                name=cell.name,
                options=cell.config.asdict(),
            )
            for cell in cells
        ]
        + [
            CellDef(
                code=snapshot_cell_codes[output],
                name="_",
                options={"hide_code": True},
            )
            for output in plan.outputs
            if output in snapshot_cell_codes
        ]
        + [
            CellDef(
                code=plan.state_code,
                name="_",
                options={"hide_code": True},
            )
        ]
        + [
            CellDef(
                code=output_cell_codes[output],
                name="_",
                options={"hide_code": True},
            )
            for output in plan.outputs
        ],
        filename=runtime.filename,
    )
    internal = InternalApp(load_notebook_ir(notebook, filepath=runtime.filename))
    runner = AppKernelRunner(internal)
    ownership: StateChildOwnership | None = None
    state_child: StateChild | None = None
    try:
        with own_state_child(
            child=runner,
            parent_context=runtime,
            state_name=state.primary_alias,
        ) as ownership:
            authored_cell_ids = tuple(internal.cell_manager.cell_ids())[: len(cells)]
            cell_ids = {
                canonical_cell_id(source.id): runtime_id
                for source, runtime_id in zip(cells, authored_cell_ids, strict=True)
            }
            from marimo_export._marimo.compat.projections import record_child_notifications

            recording = ownership.recordings.enter_context(
                record_child_notifications(runner, cell_ids)
            )
            from marimo_export._marimo.compat.cache.barrier import add_cache_write_barrier

            add_cache_write_barrier(runner._kernel._hooks)
            stop_provenance = StopProvenance()
            runner._kernel._hooks.add_post_execution(
                stop_provenance.record_cell,
                priority=Priority.EARLY,
            )
            runner._kernel._hooks.add_on_finish(stop_provenance.record_finish)
            runner._kernel._hooks.add_post_execution(_set_run_result_status)
            runner._kernel._hooks.add_post_execution(_broadcast_outputs)
            runner._kernel._hooks.add_post_execution(_flush_console)
            config = cast(dict[str, Any], copy.deepcopy(runtime.marimo_config))
            runtime_config = cast(dict[str, Any], config["runtime"])
            runtime_config["on_cell_change"] = "autorun"
            runtime_config["auto_instantiate"] = True
            runtime_config["auto_reload"] = "off"
            runtime_config["cache_cells"] = True
            cast(Any, runner._kernel).user_config = config
            runner._kernel.reactive_execution_mode = "autorun"
            runner._kernel.globals[plan.state_name] = state.fingerprint
            state_child = StateChild(
                runner=runner,
                context=runner._runtime_context,
                internal=internal,
                cell_ids=cell_ids,
                source_cell_ids={
                    runtime_id: source_id for source_id, runtime_id in cell_ids.items()
                },
                output_cell_ids=_cell_ids_by_code(internal, output_cell_codes),
                snapshot_cell_ids=_cell_ids_by_code(internal, snapshot_cell_codes),
                recording=recording,
                stop_provenance=stop_provenance,
                setup_seconds=time.monotonic() - setup_started,
            )
            yield state_child
    finally:
        if state_child is not None and ownership is not None:
            state_child.cleanup_seconds = ownership.cleanup_seconds
        state_child = None


def _cell_ids_by_code(
    internal: Any,
    cell_codes: Mapping[str, str],
) -> dict[str, Any]:
    by_code: dict[str, list[Any]] = {}
    for cell_id, data in zip(
        internal.cell_manager.cell_ids(),
        internal.cell_manager.cell_data(),
        strict=True,
    ):
        by_code.setdefault(data.code, []).append(cell_id)
    result: dict[str, Any] = {}
    for output, code in cell_codes.items():
        matches = by_code.get(code, [])
        if len(matches) != 1:
            raise ExecutionError(
                f"output cell for {output!r} is unavailable in the state run",
                code="output_cell_unavailable",
                details={"output": output},
            )
        result[output] = matches[0]
    return result


@contextmanager
def own_state_child(
    *,
    child: Any,
    parent_context: Any,
    state_name: str,
) -> Iterator[StateChildOwnership]:
    """Release one registered child after setup, execution, or cancellation."""

    from marimo_export._marimo.compat.cache.barrier import flush_native_caches

    child_context = child._runtime_context
    ownership = StateChildOwnership()
    primary: BaseException | None = None
    try:
        yield ownership
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup_started = time.monotonic()

        def flush() -> None:
            with child_context.install():
                flush_native_caches()

        try:
            cleanup_state_child(
                close_recording=ownership.recordings.close,
                teardown=flush,
                release=lambda: release_state_child(
                    child=child,
                    parent_context=parent_context,
                    child_context=child_context,
                ),
                primary=primary,
                state_name=state_name,
            )
        finally:
            ownership.cleanup_seconds = time.monotonic() - cleanup_started


def cleanup_state_child(
    *,
    close_recording: Callable[[], None],
    teardown: Callable[[], None],
    release: Callable[[], None],
    primary: BaseException | None,
    state_name: str,
) -> None:
    cleanup_failures: list[BaseException] = []
    for operation in (close_recording, teardown, release):
        try:
            operation()
        except BaseException as error:
            cleanup_failures.append(error)
    if not cleanup_failures:
        return
    if primary is not None:
        for cleanup_error in cleanup_failures:
            record_cleanup_failure(primary, "state child cleanup", cleanup_error)
        return
    cancellation = next(
        (failure for failure in cleanup_failures if not isinstance(failure, Exception)),
        None,
    )
    if cancellation is not None:
        for cleanup_error in cleanup_failures:
            if cleanup_error is not cancellation:
                record_cleanup_failure(cancellation, "state child cleanup", cleanup_error)
        raise cancellation
    cleanup_error = cleanup_failures[0]
    raise ExecutionError(
        f"state {state_name!r} child cache cleanup failed",
        code="state_cleanup_failed",
        details={
            "state": state_name,
            "exception_type": type(cleanup_error).__name__,
        },
    ) from cleanup_error


def release_state_child(
    *,
    child: Any,
    parent_context: Any,
    child_context: Any,
) -> None:
    """Run AppKernelRunner's registered child-context finalizer now."""

    for reference in weakref.getweakrefs(child):
        finalizer = reference.__callback__
        if not isinstance(finalizer, weakref.finalize):
            continue
        pending = finalizer.peek()
        if pending is None:
            continue
        target, callback, args, kwargs = pending
        if (
            target is not child
            or getattr(callback, "__self__", None) is not parent_context
            or getattr(callback, "__name__", None) != "remove_child"
            or len(args) != 1
            or args[0] is not child_context
            or kwargs
        ):
            continue
        detached = finalizer.detach()
        if detached is None:
            break
        _, callback, args, kwargs = detached
        callback(*args, **kwargs)
        if child_context in parent_context.children:
            raise RuntimeError("marimo retained the released state child")
        return
    raise RuntimeError("marimo state child finalizer is unavailable")


async def run_state_child(child: Any, cells: set[Any]) -> None:
    from marimo_export._marimo.compat.cache.patch import sequential_cache_loader

    async with sequential_cache_loader():
        await child.run(cells)


def raise_child_errors(
    child: Any,
    cell_ids: set[Any],
    state_name: str,
    *,
    output: str | None = None,
    output_details: Mapping[str, JsonValue] | None = None,
    stop_provenance: StopProvenance | None = None,
    source_cell_ids: Mapping[Any, str] | None = None,
) -> None:
    from marimo._runtime.dataflow import topological_sort

    for cell_id in topological_sort(child._kernel.graph, cell_ids):
        cell = child._kernel.graph.cells[cell_id]
        if cell.run_result_status not in {"exception", "cancelled", "marimo-error"}:
            continue
        stopping_cell = None if stop_provenance is None else stop_provenance.stopping_cell(cell_id)
        if stopping_cell is not None:
            if output is not None:
                _raise_stopped_output(
                    state_name=state_name,
                    output=output,
                    owner_cell=cell_id,
                    stopping_cell=stopping_cell,
                    source_cell_ids=source_cell_ids or {},
                )
            continue
        error = cell.exception
        label = f"state {state_name!r}"
        if output is not None:
            label += f" output {output!r}"
        details: JsonObject = {
            "state": state_name,
            "cell_id": (source_cell_ids or {}).get(cell_id, str(cell_id)),
        }
        if output is not None:
            details["output"] = output
        if output_details is not None:
            details.update(output_details)
        if isinstance(error, MarimoExportError):
            error._merge_details(details)
            raise error
        error_type = type(error).__name__ if error is not None else str(cell.run_result_status)
        details["exception_type"] = error_type
        failure_type = OutputError if output is not None else ExecutionError
        raise failure_type(
            f"{label} failed in cell {cell_id!s} with {error_type}",
            code="output_execution_failed" if output is not None else "state_execution_failed",
            details=details,
        ) from error


def raise_stopped_output(
    *,
    state_name: str,
    output: str,
    owner_cell: Any,
    dependency_cells: frozenset[Any],
    source_cell_ids: Mapping[Any, str],
    stop_provenance: StopProvenance,
) -> None:
    """Reject one named output whose selected dependency closure stopped."""

    stopping_cell = stop_provenance.stopping_cell(owner_cell)
    if stopping_cell is None:
        stopping_cell = next(
            (
                candidate
                for cell_id in dependency_cells
                if (candidate := stop_provenance.stopping_cell(cell_id)) is not None
            ),
            None,
        )
    if stopping_cell is None:
        return
    _raise_stopped_output(
        state_name=state_name,
        output=output,
        owner_cell=owner_cell,
        stopping_cell=stopping_cell,
        source_cell_ids=source_cell_ids,
    )


def _raise_stopped_output(
    *,
    state_name: str,
    output: str,
    owner_cell: Any,
    stopping_cell: Any,
    source_cell_ids: Mapping[Any, str],
) -> None:
    owner_id = source_cell_ids.get(owner_cell, str(owner_cell))
    stopping_id = source_cell_ids.get(stopping_cell, str(stopping_cell))
    raise OutputError(
        f"state {state_name!r} output {output!r} is unavailable because "
        f"cell {stopping_id!r} stopped execution",
        code="output_execution_failed",
        details={
            "state": state_name,
            "output": output,
            "cell_id": owner_id,
            "raising_cell_id": stopping_id,
            "status": "stopped",
        },
    )


__all__ = [
    "StateChild",
    "StopProvenance",
    "cleanup_state_child",
    "open_state_child",
    "own_state_child",
    "raise_child_errors",
    "raise_stopped_output",
    "release_state_child",
    "run_state_child",
]
