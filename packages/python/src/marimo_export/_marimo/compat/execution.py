"""Execute normalized states and materialize native cache receipts."""

from __future__ import annotations

import copy
import gc
import hashlib
import sys
import threading
import time
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import msgspec
from marimo._save.stores.store import Store

from marimo_export._execution.plan import (
    ExportPlan,
    NormalizedState,
    ordinary_cell_code,
    output_cell_code,
)
from marimo_export._json import JsonObject, JsonValue, json_equal, json_object
from marimo_export._marimo.capabilities import NativeReceipt, StateExecution
from marimo_export._marimo.compat.inspection import _python_type, _ui_baseline_value
from marimo_export.errors import CodecError, ExecutionError, MarimoExportError, OutputError
from marimo_export.export import (
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
    NumpyDescriptor,
    OutputDescriptor,
    Provenance,
    ScalarDescriptor,
)
from marimo_export.result import CacheSummary, StateRunTimings


@dataclass(slots=True)
class _CacheActivity:
    hits: int = 0
    misses: int = 0
    output_cells: dict[Any, Literal["hit", "miss"]] = field(default_factory=dict)


class _ReadSnapshotStore(Store):
    """Hold each native cache byte string stable for one receipt."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._values: dict[str, bytes | None] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            if key not in self._values:
                self._values[key] = self._store.get(key)
            return self._values[key]

    def put(self, key: str, value: bytes) -> bool:
        del key, value
        return False

    def hit(self, key: str) -> bool:
        return self.get(key) is not None

    def clear(self, key: str) -> bool:
        del key
        return False

    def get_batch(self, keys: Iterable[str]) -> Iterator[tuple[str, bytes | None]]:
        for key in keys:
            yield key, self.get(key)

    def export_keys(self) -> list[str]:
        with self._lock:
            return sorted(key for key, value in self._values.items() if value is not None)


_CACHE_TRACKER_LOCK = threading.Lock()
_CACHE_TRACKERS: dict[int, tuple[Any, frozenset[Any], _CacheActivity]] = {}
_TRACKED_CACHE_FUNCTION: Callable[..., Any] | None = None
_NATIVE_CACHE_FUNCTION: Callable[..., Any] | None = None


@contextmanager
def _track_notebook_cache(
    child_graph: Any,
    output_cell_ids: frozenset[Any],
) -> Iterator[_CacheActivity]:
    import marimo._runtime.executor.lifecycles.cached as cached_lifecycle

    global _NATIVE_CACHE_FUNCTION
    global _TRACKED_CACHE_FUNCTION

    activity = _CacheActivity()
    tracker_key = id(child_graph)

    with _CACHE_TRACKER_LOCK:
        if tracker_key in _CACHE_TRACKERS:
            raise RuntimeError("cache activity is already tracked for this graph")
        if not _CACHE_TRACKERS:
            native = cached_lifecycle.cache_attempt_from_hash

            def tracked(
                module: Any,
                graph: Any,
                cell_id: Any,
                scope: dict[str, Any],
                *args: Any,
                **kwargs: Any,
            ) -> Any:
                attempt = native(
                    module,
                    graph,
                    cell_id,
                    scope,
                    *args,
                    **kwargs,
                )
                from marimo_export._marimo.compat.cache import (
                    _rerun_unavailable_attempt,
                )

                attempt = _rerun_unavailable_attempt(attempt)
                with _CACHE_TRACKER_LOCK:
                    tracker = _CACHE_TRACKERS.get(id(graph))
                    if tracker is not None and tracker[0] is graph:
                        _, excluded, current = tracker
                        disposition: Literal["hit", "miss"] = "hit" if attempt.hit else "miss"
                        if cell_id in excluded:
                            current.output_cells[cell_id] = disposition
                        elif attempt.hit:
                            current.hits += 1
                        else:
                            current.misses += 1
                return attempt

            _NATIVE_CACHE_FUNCTION = native
            _TRACKED_CACHE_FUNCTION = tracked
            cast(Any, cached_lifecycle).cache_attempt_from_hash = tracked

        _CACHE_TRACKERS[tracker_key] = (
            child_graph,
            output_cell_ids,
            activity,
        )

    try:
        yield activity
    finally:
        with _CACHE_TRACKER_LOCK:
            tracker = _CACHE_TRACKERS.get(tracker_key)
            if tracker is not None and tracker[0] is child_graph:
                del _CACHE_TRACKERS[tracker_key]
            if not _CACHE_TRACKERS:
                if (
                    _TRACKED_CACHE_FUNCTION is not None
                    and cached_lifecycle.cache_attempt_from_hash is _TRACKED_CACHE_FUNCTION
                    and _NATIVE_CACHE_FUNCTION is not None
                ):
                    cast(Any, cached_lifecycle).cache_attempt_from_hash = _NATIVE_CACHE_FUNCTION
                _TRACKED_CACHE_FUNCTION = None
                _NATIVE_CACHE_FUNCTION = None


def flush_native_caches() -> None:
    """Make pending native cache writes visible to subsequent state runs."""

    from marimo._save.loaders import flush_active_caches

    flush_active_caches()


def _cleanup_state_child(
    *,
    teardown: Callable[[], None],
    release: Callable[[], None],
    primary: BaseException | None,
    state_name: str,
) -> None:
    cleanup_failures: list[BaseException] = []
    for operation in (teardown, release):
        try:
            operation()
        except BaseException as error:
            cleanup_failures.append(error)
    if not cleanup_failures:
        return
    if primary is not None:
        for cleanup_error in cleanup_failures:
            primary.add_note(f"state child cleanup also failed: {type(cleanup_error).__name__}")
        return
    cancellation = next(
        (failure for failure in cleanup_failures if not isinstance(failure, Exception)),
        None,
    )
    if cancellation is not None:
        for cleanup_error in cleanup_failures:
            if cleanup_error is not cancellation:
                cancellation.add_note(
                    f"state child cleanup also failed: {type(cleanup_error).__name__}"
                )
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


def _release_state_child(
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


async def _run_state_child(child: Any, cells: set[Any]) -> None:
    from marimo_export._marimo.compat.cache import sequential_cache_loader

    async with sequential_cache_loader():
        await child.run(cells)


async def execute_state(
    state: NormalizedState,
    plan: ExportPlan,
    exporter_identities: Mapping[str, str],
) -> StateExecution:
    """Execute one state run through marimo's graph and cell cache."""

    from marimo._ast.app import InternalApp
    from marimo._ast.load import load_notebook_ir
    from marimo._code_mode import get_context as get_code_context
    from marimo._runtime.app.kernel_runner import AppKernelRunner
    from marimo._runtime.context import get_context as get_runtime_context
    from marimo._runtime.dataflow import prune_cells_for_overrides
    from marimo._runtime.runner.hooks_post_execution import (
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
    output_cell_codes = {
        output: output_cell_code(
            planned_output,
            plan.state_name,
            exporter_identity=exporter_identities.get(output),
        )
        for output, planned_output in plan.planned_outputs.items()
    }
    authored_cells = [
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
    notebook = NotebookSerializationV1(
        app=AppInstantiation(options=runtime.app_config.asdict()),
        cells=authored_cells
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
    app = load_notebook_ir(notebook, filepath=runtime.filename)
    internal = InternalApp(app)
    child = AppKernelRunner(internal)
    from marimo_export._marimo.compat.cache import add_cache_write_barrier

    add_cache_write_barrier(child._kernel._hooks)
    child._kernel._hooks.add_post_execution(_set_run_result_status)
    child_context = child._runtime_context
    receipts: tuple[NativeReceipt, ...] | None = None
    notebook_cache = _CacheActivity()
    setup_seconds = 0.0
    dependency_execution_seconds = 0.0
    ui_update_seconds = 0.0
    output_materialization_seconds = 0.0
    cleanup_seconds = 0.0
    try:
        config = cast(dict[str, Any], copy.deepcopy(runtime.marimo_config))
        runtime_config = cast(dict[str, Any], config["runtime"])
        runtime_config["on_cell_change"] = "autorun"
        runtime_config["auto_instantiate"] = True
        runtime_config["auto_reload"] = "off"
        runtime_config["cache_cells"] = True
        cast(Any, child._kernel).user_config = config
        child._kernel.reactive_execution_mode = "autorun"
        overrides = {plan.state_name: state.fingerprint}
        child._kernel.globals.update(overrides)

        output_cell_ids = _output_cell_ids(internal, output_cell_codes)
        output_cell_id_set = frozenset(output_cell_ids.values())
        execution_order = prune_cells_for_overrides(
            internal.graph,
            internal.execution_order,
            overrides,
        )
        cells_to_run = {
            cell_id
            for cell_id in execution_order
            if cell_id not in output_cell_ids.values() and not internal.graph.is_disabled(cell_id)
        }
        setup_seconds = time.monotonic() - setup_started

        with _track_notebook_cache(
            child._kernel.graph,
            output_cell_id_set,
        ) as notebook_cache:
            dependency_started = time.monotonic()
            await _run_state_child(child, cells_to_run)
            dependency_execution_seconds = time.monotonic() - dependency_started
            _raise_child_errors(child, cells_to_run, state.name)

            if state.ui_updates:
                ui_started = time.monotonic()
                from marimo._plugins.ui._core.ui_element import UIElement
                from marimo._runtime.commands import UpdateUIElementCommand

                elements: list[tuple[str, UIElement[Any, Any]]] = []
                values: list[JsonValue] = []
                with child_context.install():
                    child_context.ui_element_registry.register_scope(
                        child.globals,
                        defs=set(state.ui_updates),
                    )
                for name, value in state.ui_updates.items():
                    element = child.globals.get(name)
                    if not isinstance(element, UIElement):
                        raise ExecutionError(
                            f"state {state.name!r} input {name!r} did not create a UI element",
                            code="input_value_invalid",
                            details={"state": state.name, "input": name},
                        )
                    elements.append((name, element))
                    values.append(value)
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
                        f"state {state.name!r} input {input_name!r} callback failed",
                        code="input_value_invalid",
                        details={
                            "state": state.name,
                            "input": input_name,
                            "exception_type": type(callback_error).__name__,
                        },
                    ) from callback_error
                if not updated:
                    raise ExecutionError(
                        f"state {state.name!r} UI values were not applied",
                        code="input_value_invalid",
                        details={"state": state.name, "inputs": list(state.ui_updates)},
                    )
                for name in state.ui_updates:
                    expected = state.inputs[name]
                    actual = _ui_baseline_value(
                        child.globals[name],
                        f"state {state.name!r} input {name!r}",
                    )
                    if not json_equal(actual, expected):
                        raise ExecutionError(
                            f"state {state.name!r} input {name!r} rejected its value",
                            code="input_value_invalid",
                            details={"state": state.name, "input": name},
                        )
                ui_update_seconds = time.monotonic() - ui_started

            output_started = time.monotonic()
            reactive_cells = {
                cell_id
                for cell_id, cell in child._kernel.graph.cells.items()
                if cell.stale and cell_id not in output_cell_id_set
            }
            await _run_state_child(child, reactive_cells | set(output_cell_id_set))
            _raise_child_errors(child, reactive_cells, state.name)
            for output, cell_id in output_cell_ids.items():
                _raise_child_errors(child, {cell_id}, state.name, output=output)
            receipt_items: list[NativeReceipt] = []
            for output in plan.outputs:
                cell_id = output_cell_ids[output]
                disposition = notebook_cache.output_cells.get(cell_id)
                if disposition is None:
                    raise OutputError(
                        f"output {output!r} did not execute through marimo's cell cache",
                        code="cache_receipt_missing",
                        details={"state": state.name, "output": output},
                    )
                receipt_items.append(
                    _output_receipt(
                        child,
                        cell_id,
                        output,
                        disposition,
                    )
                )
            receipts = tuple(receipt_items)
            output_materialization_seconds = time.monotonic() - output_started
    finally:
        cleanup_started = time.monotonic()
        primary = sys.exception()

        def teardown() -> None:
            with child_context.install():
                child._kernel.cache_callbacks.teardown()

        def release() -> None:
            nonlocal child
            _release_state_child(
                child=child,
                parent_context=runtime,
                child_context=child_context,
            )
            del child
            gc.collect()

        try:
            _cleanup_state_child(
                teardown=teardown,
                release=release,
                primary=primary,
                state_name=state.name,
            )
        finally:
            cleanup_seconds = time.monotonic() - cleanup_started
    if receipts is None:
        raise RuntimeError("state run produced no output receipts")
    return StateExecution(
        receipts=receipts,
        notebook_cache=CacheSummary(
            hits=notebook_cache.hits,
            misses=notebook_cache.misses,
        ),
        timings=StateRunTimings(
            states=1,
            setup_seconds=setup_seconds,
            dependency_execution_seconds=dependency_execution_seconds,
            ui_update_seconds=ui_update_seconds,
            output_materialization_seconds=output_materialization_seconds,
            cleanup_seconds=cleanup_seconds,
        ),
    )


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


def _output_cell_ids(
    internal: Any,
    output_cell_codes: Mapping[str, str],
) -> dict[str, Any]:
    by_code: dict[str, list[Any]] = {}
    for cell_id, data in zip(
        internal.cell_manager.cell_ids(),
        internal.cell_manager.cell_data(),
        strict=True,
    ):
        by_code.setdefault(data.code, []).append(cell_id)
    result: dict[str, Any] = {}
    for output, code in output_cell_codes.items():
        matches = by_code.get(code, [])
        if len(matches) != 1:
            raise ExecutionError(
                f"output cell for {output!r} is unavailable in the state run",
                code="output_cell_unavailable",
                details={"output": output},
            )
        result[output] = matches[0]
    return result


def _output_receipt(
    child: Any,
    cell_id: Any,
    output: str,
    disposition: Literal["hit", "miss"],
) -> NativeReceipt:
    from marimo._runtime.context import get_context
    from marimo._save.hash import cache_attempt_from_hash
    from marimo._save.loaders import flush_active_caches

    with child._runtime_context.install():
        context = get_context()
        flush_active_caches()
        cell = child._kernel.graph.cells[cell_id]
        root_context = context
        while root_context.parent is not None:
            root_context = root_context.parent
        native_loader = root_context.cache.active_lazy_loaders.get("cell_cache")
        from marimo_export._marimo.compat.cache import SequentialLazyLoader

        if not isinstance(native_loader, SequentialLazyLoader):
            raise OutputError(
                f"output {output!r} has no active native cache loader",
                code="cache_receipt_missing",
                details={"output": output},
            )
        effective_mode = native_loader._effective_mode()
        store = _ReadSnapshotStore(native_loader.store)
        native_store = native_loader.store
        configured_mode = native_loader.mode
        try:
            native_loader.store = store
            native_loader.mode = effective_mode
            attempt = cache_attempt_from_hash(
                cell.mod,
                child._kernel.graph,
                cell_id,
                child.globals,
                loader=native_loader,
                pin_modules=bool(
                    child._kernel.user_config.get("runtime", {}).get("pin_modules", True)
                ),
            )
        finally:
            native_loader.store = native_store
            native_loader.mode = configured_mode
        cache_key = str(native_loader.build_path(attempt.key))
        if not attempt.hit:
            raise OutputError(
                f"output {output!r} did not persist its native cache receipt",
                code="cache_receipt_missing",
                details={"output": output},
            )
        payload = child.outputs.get(cell_id)
        return _native_receipt(
            store=store,
            cache_key=cache_key,
            expected_hash=attempt.hash,
            output=output,
            value=payload,
            disposition=disposition,
        )


def _native_receipt(
    *,
    store: _ReadSnapshotStore,
    cache_key: str,
    expected_hash: str,
    output: str,
    value: object,
    disposition: Literal["hit", "miss"],
) -> NativeReceipt:
    from marimo._save.stubs import BlobAsset as NativeBlobAsset
    from marimo._save.stubs.lazy_stub import Cache as CacheSchema

    encoded = store.get(cache_key)
    if not encoded:
        raise OutputError(
            f"output {output!r} has no native cache receipt",
            code="cache_receipt_missing",
            details={"output": output},
        )
    try:
        manifest = msgspec.json.decode(encoded, type=CacheSchema)
    except msgspec.DecodeError as error:
        raise OutputError(
            f"output {output!r} has an invalid native cache manifest",
            code="cache_receipt_invalid",
            details={"output": output, "cache_key": cache_key},
        ) from error
    if manifest.hash != expected_hash:
        raise OutputError(
            f"output {output!r} native cache hash changed",
            code="cache_receipt_invalid",
            details={"output": output, "cache_key": cache_key},
        )
    returned = manifest.meta.return_value
    provenance = Provenance(
        cache_key=cache_key,
        return_reference=returned.reference if returned is not None else None,
        python_type=_python_type(value),
    )
    if returned is None or returned.reference is None:
        if not _is_native_scalar(value):
            raise CodecError(
                f"output {output!r} uses a nonportable inline cache value",
                code="codec_invalid",
                details={"output": output, "python_type": _python_type(value)},
            )
        return NativeReceipt(
            output=output,
            descriptor=ScalarDescriptor(value=cast(Any, value), provenance=provenance),
            payload=None,
            disposition=disposition,
        )

    reference = returned.reference
    payload = store.get(reference)
    if payload is None:
        raise OutputError(
            f"output {output!r} native return asset is missing",
            code="cache_receipt_missing",
            details={"output": output, "cache_key": cache_key},
        )
    expected_digest = manifest.meta.blob_hashes.get(reference)
    digest = hashlib.sha256(payload).hexdigest()
    if expected_digest is not None and expected_digest != digest:
        raise OutputError(
            f"output {output!r} native return asset failed integrity",
            code="cache_receipt_invalid",
            details={"output": output, "cache_key": cache_key},
        )
    asset = AssetRef(sha256=digest, size=len(payload))
    suffix = Path(reference).suffix
    if suffix == ".npy":
        descriptor: OutputDescriptor = NumpyDescriptor(asset=asset, provenance=provenance)
    elif suffix == ".arrow":
        descriptor = ArrowDescriptor(asset=asset, provenance=provenance)
    elif suffix == ".bin":
        try:
            blob = msgspec.msgpack.decode(payload, type=NativeBlobAsset)
        except msgspec.DecodeError as error:
            raise CodecError(
                f"output {output!r} has an invalid BlobAsset envelope",
                code="codec_invalid",
                details={"output": output},
            ) from error
        if blob.media_type is None:
            raise CodecError(
                f"output {output!r} BlobAsset has no media type",
                code="codec_invalid",
                details={"output": output},
            )
        try:
            metadata = json_object(blob.metadata, f"output {output!r} BlobAsset metadata")
            descriptor = BlobAssetDescriptor(
                asset=asset,
                provenance=provenance,
                media_type=blob.media_type,
                filename=blob.filename,
                metadata=metadata,
            )
        except (TypeError, ValueError) as error:
            raise CodecError(
                f"output {output!r} BlobAsset metadata is not portable",
                code="codec_invalid",
                details={"output": output},
            ) from error
    else:
        raise CodecError(
            f"output {output!r} uses unsupported native cache codec {suffix or '<inline>'!r}",
            code="codec_invalid",
            details={"output": output, "return_reference": reference},
        )
    return NativeReceipt(
        output=output,
        descriptor=descriptor,
        payload=payload,
        disposition=disposition,
    )


def _raise_child_errors(
    child: Any,
    cell_ids: set[Any],
    state_name: str,
    *,
    output: str | None = None,
) -> None:
    from marimo._runtime.dataflow import topological_sort

    for cell_id in topological_sort(child._kernel.graph, cell_ids):
        cell = child._kernel.graph.cells[cell_id]
        if cell.run_result_status not in {"exception", "cancelled", "marimo-error"}:
            continue
        error = cell.exception
        label = f"state {state_name!r}"
        if output is not None:
            label += f" output {output!r}"
        details: JsonObject = {
            "state": state_name,
            "cell_id": str(cell_id),
        }
        if output is not None:
            details["output"] = output
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


def _is_native_scalar(value: object) -> bool:
    return value is None or isinstance(value, (bool, str, int, float))


__all__ = ["execute_state", "flush_native_caches"]
