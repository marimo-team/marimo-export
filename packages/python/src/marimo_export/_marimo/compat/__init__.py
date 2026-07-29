from __future__ import annotations

import copy
import gc
import hashlib
import importlib
import importlib.metadata
import inspect
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import msgspec
from marimo._save.stubs import BlobAsset

from marimo_export._execution.matrix import (
    Baseline,
    Definition,
    MatrixPlan,
    NormalizedState,
    projection_code,
)
from marimo_export._json import JsonObject, JsonValue, canonical_bytes, json_object, json_value
from marimo_export.errors import CodecError, CompatibilityError, ExecutionError, OutputError
from marimo_export.exporters._definitions import runtime_reference
from marimo_export.publication import (
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
    CacheSummary,
    FreshChildTimings,
    NumpyDescriptor,
    OutputCodec,
    OutputDescriptor,
    Provenance,
    ScalarDescriptor,
)

_CAPABILITIES = (
    "asset_transfer",
    "blob_asset",
    "cache_cells",
    "cell_cache_receipts",
    "child_sessions",
    "child_ui_updates",
    "definition_overrides",
    "setup_definition_overrides",
    "synthetic_projection_cells",
)
_MAX_PYTHON_TYPE_BYTES = 512
_MAX_EXPORTER_DEPENDENCIES = 256


@dataclass(frozen=True, slots=True)
class MarimoCapabilities:
    """Private marimo capabilities available in the selected kernel."""

    version: str
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NativeReceipt:
    """One verified native cell-cache return ready for publication."""

    output: str
    descriptor: OutputDescriptor
    payload: bytes | None
    disposition: Literal["hit", "miss"]

    @property
    def asset_identity(self) -> tuple[OutputCodec, str] | None:
        if isinstance(self.descriptor, ScalarDescriptor):
            return None
        return self.descriptor.codec, self.descriptor.asset.sha256


@dataclass(frozen=True, slots=True)
class StateExecution:
    """Receipts and run-local diagnostics from one fresh state child."""

    receipts: tuple[NativeReceipt, ...]
    upstream_cache: CacheSummary
    timings: FreshChildTimings


@dataclass(slots=True)
class _CacheActivity:
    hits: int = 0
    misses: int = 0


_CACHE_TRACKER_LOCK = threading.Lock()
_CACHE_TRACKERS: dict[int, tuple[Any, frozenset[Any], _CacheActivity]] = {}
_TRACKED_CACHE_FUNCTION: Callable[..., Any] | None = None
_NATIVE_CACHE_FUNCTION: Callable[..., Any] | None = None


@contextmanager
def _track_upstream_cache(
    child_graph: Any,
    projection_ids: frozenset[Any],
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
                with _CACHE_TRACKER_LOCK:
                    tracker = _CACHE_TRACKERS.get(id(graph))
                    if tracker is not None and tracker[0] is graph:
                        _, excluded, current = tracker
                        if cell_id not in excluded:
                            if attempt.hit:
                                current.hits += 1
                            else:
                                current.misses += 1
                return attempt

            _NATIVE_CACHE_FUNCTION = native
            _TRACKED_CACHE_FUNCTION = tracked
            cast(Any, cached_lifecycle).cache_attempt_from_hash = tracked

        _CACHE_TRACKERS[tracker_key] = (
            child_graph,
            projection_ids,
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


def blob_asset_type() -> type:
    """Return the exact native BlobAsset class."""

    from marimo._save.stubs import BlobAsset as NativeBlobAsset

    return NativeBlobAsset


def transfer_runtime_context() -> object:
    """Return the attached runtime context for temporary asset transfer."""

    from marimo._runtime.context import get_context

    return get_context()


def runtime_path() -> str | None:
    """Return the selected notebook path from the attached runtime."""

    from marimo._runtime.context import get_context

    value = get_context().filename
    return value if isinstance(value, str) and value else None


def capture_anywidget_bundle(value: object) -> bytes:
    """Capture a live AnyWidget graph through the compatibility boundary."""

    from marimo_export._marimo.compat.anywidget import capture_anywidget_payload

    return capture_anywidget_payload(value)


def new_transfer_virtual_file(data: bytes) -> object:
    """Create one marimo virtual file for exact transfer bytes."""

    from marimo._runtime.virtual_file import VirtualFile, random_filename

    return VirtualFile(filename=random_filename("bin"), buffer=data)


def flush_native_caches() -> None:
    """Make pending native cache writes visible to fresh state children."""

    from marimo._save.loaders import flush_active_caches

    flush_active_caches()


def require_capabilities() -> MarimoCapabilities:
    """Validate the focused private seams used by the rewrite."""

    missing: list[str] = []
    try:
        import marimo
        from marimo._ast.app import InternalApp
        from marimo._ast.load import load_notebook_ir
        from marimo._code_mode import get_context as get_code_context
        from marimo._runtime.app.kernel_runner import AppKernelRunner
        from marimo._runtime.context import get_context as get_runtime_context
        from marimo._runtime.context.kernel_context import KernelRuntimeContext
        from marimo._runtime.dataflow import prune_cells_for_overrides
        from marimo._runtime.executor.lifecycles.cached import (
            cache_attempt_from_hash as lifecycle_cache_attempt,
        )
        from marimo._save.hash import (
            cache_attempt_from_hash as direct_cache_attempt,
        )
        from marimo._save.hash import (
            hash_module,
        )
        from marimo._save.loaders import flush_active_caches
        from marimo._save.loaders.lazy import LazyLoader
        from marimo._save.stubs import BlobAsset as NativeBlobAsset
        from marimo._save.stubs.lazy_stub import BLOB_DESERIALIZERS, BLOB_SERIALIZERS
        from marimo._schemas.serialization import CellDef, NotebookSerializationV1
    except ImportError as error:
        raise CompatibilityError(
            "the attached marimo runtime lacks required export capabilities",
            code="marimo_incompatible",
        ) from error

    for value, name in (
        (InternalApp, "child_sessions"),
        (AppKernelRunner, "child_sessions"),
        (load_notebook_ir, "child_sessions"),
        (prune_cells_for_overrides, "definition_overrides"),
        (lifecycle_cache_attempt, "cell_cache_receipts"),
        (direct_cache_attempt, "cell_cache_receipts"),
        (hash_module, "cell_cache_receipts"),
        (flush_active_caches, "cell_cache_receipts"),
        (LazyLoader, "cell_cache_receipts"),
        (CellDef, "synthetic_projection_cells"),
        (NotebookSerializationV1, "child_sessions"),
    ):
        if not callable(value):
            missing.append(name)
    if not callable(get_code_context):
        missing.append("child_sessions")
    if not _blob_asset_codec(NativeBlobAsset, BLOB_SERIALIZERS, BLOB_DESERIALIZERS):
        missing.append("blob_asset")

    try:
        runtime = get_runtime_context()
    except Exception:
        runtime = None
    if runtime is not None:
        if not isinstance(runtime, KernelRuntimeContext):
            missing.append("child_sessions")
        store = getattr(getattr(runtime, "cache", None), "store", None)
        if store is None or not all(
            callable(getattr(store, name, None)) for name in ("get", "hit", "put")
        ):
            missing.append("cell_cache_receipts")

    if missing:
        names = sorted(set(missing))
        raise CompatibilityError(
            "the attached marimo runtime lacks required capabilities: " + ", ".join(names),
            code="marimo_incompatible",
            details={"missing": names},
        )
    return MarimoCapabilities(str(marimo.__version__), _CAPABILITIES)


def _blob_asset_codec(
    blob_asset: type,
    serializers: Mapping[str, object],
    deserializers: Mapping[str, object],
) -> bool:
    serializer = serializers.get("bin")
    deserializer = deserializers.get(".bin")
    if not callable(serializer) or not callable(deserializer):
        return False
    encode = cast(Callable[[object], bytes], serializer)
    decode = cast(Callable[[bytes, object], object], deserializer)
    try:
        value = blob_asset(
            data=b"capability",
            media_type="application/octet-stream",
            filename="capability.bin",
            metadata={"probe": True},
        )
        return decode(encode(value), None) == value
    except Exception:
        return False


async def inspect_baseline() -> Baseline:
    """Read graph ownership and current values from the selected parent."""

    from marimo._code_mode import get_context
    from marimo._plugins.ui._core.ui_element import UIElement

    async with get_context() as context:
        graph = context._kernel.graph
        definitions: dict[str, Definition] = {}
        for name in sorted(graph.definitions):
            owners = graph.get_defining_cells(name)
            if len(owners) != 1:
                continue
            owner = next(iter(owners))
            cell = graph.cells[owner]
            if name not in context.globals:
                continue
            value = context.globals[name]
            is_ui = isinstance(value, UIElement)
            definitions[name] = Definition(
                name=name,
                cell_id=str(owner),
                siblings=tuple(sorted(cell.defs)),
                kind="ui" if is_ui else "ordinary",
                python_type=_python_type(value),
                value=value,
                frontend_value=(_frontend_value(value, f"definition {name!r}") if is_ui else None),
                sensitive=_is_sensitive(value) if is_ui else False,
                domain=_control_domain(value) if is_ui else {},
            )
        return Baseline(
            definitions=definitions,
            document_sha256=_document_sha256(context.cells),
            filename=_portable_filename(context._kernel.app_metadata.filename),
        )


async def current_document_sha256() -> str:
    from marimo._code_mode import get_context

    async with get_context() as context:
        return _document_sha256(context.cells)


async def declared_ui_values(names: tuple[str, ...]) -> JsonObject:
    from marimo._code_mode import get_context
    from marimo._plugins.ui._core.ui_element import UIElement

    result: JsonObject = {}
    async with get_context() as context:
        for name in names:
            value = context.globals.get(name)
            if isinstance(value, UIElement):
                result[name] = _frontend_value(value, f"definition {name!r}")
    return result


def preflight_exporters(plan: MatrixPlan) -> Mapping[str, str]:
    """Resolve selected exporters and return their cache identities."""

    selected = {
        output: projection.exporter
        for output, projection in plan.projections.items()
        if projection.exporter is not None
    }
    if not selected:
        return {}
    identities: dict[str, str] = {}
    resolved: dict[str, str] = {}
    try:
        package_distributions = importlib.metadata.packages_distributions()
    except Exception:
        package_distributions = {}
    for output, exporter in selected.items():
        identity = resolved.get(exporter.name)
        if identity is not None:
            identities[output] = identity
            continue
        reference = runtime_reference(exporter.name)
        try:
            module = importlib.import_module(reference.module)
            value = getattr(module, reference.symbol)
        except Exception as error:
            raise OutputError(
                f"output {output!r} exporter {exporter.name!r} is unavailable",
                code="exporter_unavailable",
                details={
                    "output": output,
                    "exporter": exporter.name,
                    "exception_type": type(error).__name__,
                },
            ) from error
        if not callable(value):
            raise OutputError(
                f"output {output!r} exporter {exporter.name!r} is not callable",
                code="exporter_invalid",
                details={
                    "output": output,
                    "exporter": exporter.name,
                },
            )
        identity = _exporter_identity(
            name=exporter.name,
            module=module,
            value=value,
            distributions=reference.distributions,
            package_distributions=package_distributions,
        )
        resolved[exporter.name] = identity
        identities[output] = identity
    return identities


def _exporter_identity(
    *,
    name: str,
    module: Any,
    value: Any,
    distributions: tuple[str, ...],
    package_distributions: Mapping[str, list[str]],
) -> str:
    dependencies, dependency_modules = _exporter_dependencies(value, module)
    payload: JsonObject = {
        "dependencies": dependencies,
        "name": name,
        "module": str(getattr(module, "__name__", "")),
        "symbol_type": f"{type(value).__module__}.{type(value).__qualname__}",
    }
    origin = getattr(getattr(module, "__spec__", None), "origin", None)
    if isinstance(origin, str) and origin not in {"built-in", "frozen"}:
        module_digest = _file_digest(Path(origin))
        if module_digest is not None:
            payload["module_sha256"] = module_digest
    code = getattr(value, "__code__", None)
    if code is None:
        code = getattr(inspect.getattr_static(type(value), "__call__", None), "__code__", None)
    if code is not None:
        from marimo._save.hash import hash_module

        with suppress(TypeError, ValueError):
            payload["callable_sha256"] = hash_module(code).hex()
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError):
        source = None
    if source is not None:
        payload["source_sha256"] = hashlib.sha256(source.encode("utf-8")).hexdigest()

    package_names = set(distributions)
    top_level = str(getattr(module, "__name__", "")).partition(".")[0]
    if top_level:
        package_names.update(package_distributions.get(top_level, ()))
    for dependency_module in dependency_modules:
        dependency_top_level = dependency_module.partition(".")[0]
        if dependency_top_level:
            package_names.update(package_distributions.get(dependency_top_level, ()))
    versions: JsonObject = {}
    for distribution in sorted(package_names):
        with suppress(importlib.metadata.PackageNotFoundError):
            versions[distribution] = importlib.metadata.version(distribution)
    if versions:
        payload["distributions"] = versions
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _exporter_dependencies(value: Any, module: Any) -> tuple[JsonObject, frozenset[str]]:
    records: JsonObject = {}
    module_names: set[str] = set()
    visited: set[int] = set()
    local_root = _module_tree_root(module)

    def is_local_module(dependency: Any) -> bool:
        origin = _module_origin(dependency)
        if origin is None or local_root is None:
            return False
        try:
            origin.relative_to(local_root)
        except ValueError:
            return False
        return True

    def record(name: str, digest: str) -> None:
        if name in records:
            return
        if len(records) >= _MAX_EXPORTER_DEPENDENCIES:
            raise ValueError(
                f"exporter dependency graph exceeds {_MAX_EXPORTER_DEPENDENCIES} entries"
            )
        records[name] = digest

    def visit_module(dependency: Any, *, expand: bool) -> None:
        identifier = id(dependency)
        if identifier in visited:
            return
        visited.add(identifier)
        name = str(getattr(dependency, "__name__", ""))
        if name:
            module_names.add(name)
        origin = _module_origin(dependency)
        if origin is not None:
            digest = _file_digest(origin)
            if digest is not None:
                record(f"module:{name}", digest)
        if not expand or not is_local_module(dependency):
            return
        namespace = getattr(dependency, "__dict__", {})
        for attribute in sorted(namespace):
            member = namespace[attribute]
            if getattr(member, "__module__", None) != name:
                continue
            if inspect.isfunction(member) or inspect.isclass(member):
                visit_callable(member)

    def visit_callable(dependency: Any) -> None:
        if inspect.ismethod(dependency):
            dependency = dependency.__func__
        identifier = id(dependency)
        if identifier in visited:
            return
        visited.add(identifier)
        module_name = str(getattr(dependency, "__module__", ""))
        qualname = str(
            getattr(
                dependency,
                "__qualname__",
                getattr(dependency, "__name__", type(dependency).__qualname__),
            )
        )
        owner = sys.modules.get(module_name)
        if owner is not None:
            visit_module(owner, expand=False)
        code = getattr(dependency, "__code__", None)
        if code is None and inspect.isclass(dependency):
            if owner is None or not is_local_module(owner):
                return
            for attribute in sorted(vars(dependency)):
                member = inspect.getattr_static(dependency, attribute)
                if isinstance(member, (classmethod, staticmethod)):
                    member = member.__func__
                if inspect.isfunction(member):
                    visit_callable(member)
            return
        if code is None:
            call = inspect.getattr_static(type(dependency), "__call__", None)
            if inspect.isfunction(call):
                visit_callable(call)
            return
        from marimo._save.hash import hash_module

        record(f"callable:{module_name}:{qualname}", hash_module(code).hex())
        try:
            closure = inspect.getclosurevars(dependency)
        except TypeError:
            return
        for dependency_name, referenced in sorted({**closure.globals, **closure.nonlocals}.items()):
            visit_reference(
                referenced,
                f"value:{module_name}:{qualname}:{dependency_name}",
            )

    def visit_reference(dependency: Any, label: str) -> None:
        if inspect.ismodule(dependency):
            visit_module(dependency, expand=True)
            return
        if (
            inspect.isfunction(dependency)
            or inspect.ismethod(dependency)
            or inspect.isclass(dependency)
        ):
            visit_callable(dependency)
            return
        try:
            portable = json_value(dependency, label)
        except (TypeError, ValueError):
            owner = sys.modules.get(str(getattr(type(dependency), "__module__", "")))
            if owner is not None:
                visit_module(owner, expand=False)
            call = inspect.getattr_static(type(dependency), "__call__", None)
            if inspect.isfunction(call):
                visit_callable(call)
            return
        record(label, hashlib.sha256(canonical_bytes(portable)).hexdigest())

    visit_module(module, expand=False)
    visit_callable(value)
    return records, frozenset(module_names)


def _module_origin(module: Any) -> Path | None:
    origin = getattr(getattr(module, "__spec__", None), "origin", None)
    if not isinstance(origin, str) or origin in {"built-in", "frozen"}:
        origin = getattr(module, "__file__", None)
    if not isinstance(origin, str):
        return None
    try:
        path = Path(origin).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_file() else None


def _module_tree_root(module: Any) -> Path | None:
    origin = _module_origin(module)
    if origin is None:
        return None
    parts = str(getattr(module, "__name__", "")).split(".")
    levels = max(0, len(parts) - (1 if origin.name == "__init__.py" else 2))
    root = origin.parent
    for _ in range(levels):
        root = root.parent
    return root


def _file_digest(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


async def execute_state(
    state: NormalizedState,
    plan: MatrixPlan,
    exporter_identities: Mapping[str, str],
) -> StateExecution:
    """Execute one fresh child through marimo's graph and cell cache."""

    from marimo._ast.app import InternalApp
    from marimo._ast.load import load_notebook_ir
    from marimo._code_mode import get_context as get_code_context
    from marimo._runtime.app.kernel_runner import AppKernelRunner
    from marimo._runtime.context import get_context as get_runtime_context
    from marimo._runtime.dataflow import prune_cells_for_overrides
    from marimo._schemas.serialization import (
        AppInstantiation,
        CellDef,
        NotebookSerializationV1,
    )

    construction_started = time.monotonic()
    code_context = get_code_context()
    runtime = get_runtime_context()
    cells = tuple(code_context.cells)
    projection_codes = {
        output: projection_code(
            projection,
            plan.state_name,
            exporter_identity=exporter_identities.get(output),
        )
        for output, projection in plan.projections.items()
    }
    notebook = NotebookSerializationV1(
        app=AppInstantiation(options=runtime.app_config.asdict()),
        cells=[
            CellDef(
                code=cell.code,
                name=cell.name,
                options=cell.config.asdict(),
            )
            for cell in cells
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
                code=projection_codes[output],
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
    child_context = child._runtime_context
    receipts: tuple[NativeReceipt, ...] | None = None
    upstream_cache = _CacheActivity()
    construction_seconds = 0.0
    upstream_execution_seconds = 0.0
    ui_application_seconds = 0.0
    projection_execution_seconds = 0.0
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
        overrides = _isolated_overrides(state)
        overrides[plan.state_name] = state.fingerprint
        child._kernel.globals.update(overrides)

        projection_ids = _projection_ids(internal, projection_codes)
        projection_id_set = frozenset(projection_ids.values())
        execution_order = prune_cells_for_overrides(
            internal.graph,
            internal.execution_order,
            overrides,
        )
        cells_to_run = {
            cell_id
            for cell_id in execution_order
            if cell_id not in projection_ids.values() and not internal.graph.is_disabled(cell_id)
        }
        construction_seconds = time.monotonic() - construction_started

        with _track_upstream_cache(
            child._kernel.graph,
            projection_id_set,
        ) as upstream_cache:
            upstream_started = time.monotonic()
            await child.run(cells_to_run)
            upstream_execution_seconds = time.monotonic() - upstream_started
            _raise_child_errors(child, cells_to_run, state.name)

            if state.ui_values:
                ui_started = time.monotonic()
                from marimo._plugins.ui._core.ui_element import UIElement
                from marimo._runtime.commands import UpdateUIElementCommand

                elements: list[UIElement[Any, Any]] = []
                values: list[JsonValue] = []
                for name, value in state.ui_values.items():
                    element = child.globals.get(name)
                    if not isinstance(element, UIElement):
                        raise ExecutionError(
                            f"state {state.name!r} input {name!r} did not create a UI element",
                            code="input_value_invalid",
                            details={"state": state.name, "input": name},
                        )
                    elements.append(element)
                    values.append(value)
                updated = await child.set_ui_element_value(
                    UpdateUIElementCommand(
                        object_ids=[element._id for element in elements],
                        values=values,
                    ),
                    notify_frontend=False,
                )
                if not updated:
                    raise ExecutionError(
                        f"state {state.name!r} UI values were not applied",
                        code="input_value_invalid",
                        details={"state": state.name, "inputs": list(state.ui_values)},
                    )
                for name, expected in state.ui_values.items():
                    actual = _frontend_value(
                        child.globals[name],
                        f"state {state.name!r} input {name!r}",
                    )
                    if actual != expected:
                        raise ExecutionError(
                            f"state {state.name!r} input {name!r} rejected its value",
                            code="input_value_invalid",
                            details={"state": state.name, "input": name},
                        )
                ui_application_seconds = time.monotonic() - ui_started

            projection_started = time.monotonic()
            receipt_items: list[NativeReceipt] = []
            for output in plan.outputs:
                cell_id = projection_ids[output]
                receipt_items.append(
                    await _execute_projection(
                        child,
                        cell_id,
                        output,
                        state.name,
                    )
                )
            receipts = tuple(receipt_items)
            projection_execution_seconds = time.monotonic() - projection_started
    finally:
        cleanup_started = time.monotonic()
        with child_context.install(), suppress(Exception):
            child._kernel.cache_callbacks.teardown()
        del child
        gc.collect()
        cleanup_seconds = time.monotonic() - cleanup_started
    if receipts is None:
        raise RuntimeError("fresh child produced no projection receipts")
    return StateExecution(
        receipts=receipts,
        upstream_cache=CacheSummary(
            hits=upstream_cache.hits,
            misses=upstream_cache.misses,
        ),
        timings=FreshChildTimings(
            states=1,
            construction_seconds=construction_seconds,
            upstream_execution_seconds=upstream_execution_seconds,
            ui_application_seconds=ui_application_seconds,
            projection_execution_seconds=projection_execution_seconds,
            cleanup_seconds=cleanup_seconds,
        ),
    )


def _isolated_overrides(state: NormalizedState) -> dict[str, object]:
    values = dict(state.ordinary_overrides)
    shared = {
        id(value): value
        for value in values.values()
        if inspect.ismodule(value)
        or inspect.isfunction(value)
        or inspect.isbuiltin(value)
        or inspect.isclass(value)
    }
    try:
        isolated = copy.deepcopy(values, shared)
    except Exception as error:
        raise ExecutionError(
            f"state {state.name!r} ordinary input siblings could not be isolated",
            code="input_isolation_failed",
            details={
                "state": state.name,
                "definitions": sorted(values),
                "exception_type": type(error).__name__,
            },
        ) from error
    return cast(dict[str, object], isolated)


def _projection_ids(
    internal: Any,
    projection_codes: Mapping[str, str],
) -> dict[str, Any]:
    by_code: dict[str, list[Any]] = {}
    for cell_id, data in zip(
        internal.cell_manager.cell_ids(),
        internal.cell_manager.cell_data(),
        strict=True,
    ):
        by_code.setdefault(data.code, []).append(cell_id)
    result: dict[str, Any] = {}
    for output, code in projection_codes.items():
        matches = by_code.get(code, [])
        if len(matches) != 1:
            raise ExecutionError(
                f"projection for output {output!r} is unavailable in the child",
                code="projection_invalid",
                details={"output": output},
            )
        result[output] = matches[0]
    return result


async def _execute_projection(
    child: Any,
    cell_id: Any,
    output: str,
    state_name: str,
) -> NativeReceipt:
    from marimo._runtime.context import get_context
    from marimo._save.hash import cache_attempt_from_hash
    from marimo._save.loaders import flush_active_caches
    from marimo._save.loaders.lazy import LazyLoader

    with child._runtime_context.install():
        context = get_context()
        cell = child._kernel.graph.cells[cell_id]
        loader = LazyLoader(name="cell_cache", store=context.cache.store)
        attempt = cache_attempt_from_hash(
            cell.mod,
            child._kernel.graph,
            cell_id,
            child.globals,
            loader=loader,
            pin_modules=bool(child._kernel.user_config.get("runtime", {}).get("pin_modules", True)),
        )
        cache_key = str(loader.build_path(attempt.key))
        disposition: Literal["hit", "miss"] = "hit" if attempt.hit else "miss"

    await child.run({cell_id})
    _raise_child_errors(child, {cell_id}, state_name, output=output)
    with child._runtime_context.install():
        flush_active_caches()
        payload = child.outputs.get(cell_id)
        return _native_receipt(
            loader=loader,
            cache_key=cache_key,
            expected_hash=attempt.hash,
            output=output,
            value=payload,
            disposition=disposition,
        )


def _native_receipt(
    *,
    loader: Any,
    cache_key: str,
    expected_hash: str,
    output: str,
    value: object,
    disposition: Literal["hit", "miss"],
) -> NativeReceipt:
    from marimo._save.stubs import BlobAsset as NativeBlobAsset
    from marimo._save.stubs.lazy_stub import Cache as CacheSchema

    encoded = loader.store.get(cache_key)
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
    payload = loader.store.get(reference)
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
        error_type = type(error).__name__ if error is not None else str(cell.run_result_status)
        details["exception_type"] = error_type
        failure_type = OutputError if output is not None else ExecutionError
        raise failure_type(
            f"{label} failed in cell {cell_id!s} with {error_type}",
            code="output_execution_failed" if output is not None else "state_execution_failed",
            details=details,
        ) from error


def _document_sha256(cells: Any) -> str:
    from marimo._ast.names import is_internal_cell_name

    value = [
        {
            "code": cell.code.rstrip(),
            "name": (None if not cell.name or is_internal_cell_name(cell.name) else cell.name),
            "config": json_object(cell.config.asdict(), f"cell {cell.id!s} config"),
        }
        for cell in cells
    ]
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _portable_filename(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ExecutionError("the selected notebook has an invalid filename")
    return Path(value).name


def _python_type(value: object) -> str:
    concrete = type(value)
    module = _escape_type_part(type.__getattribute__(concrete, "__module__"), "unknown")
    qualname = _escape_type_part(type.__getattribute__(concrete, "__qualname__"), "unknown")
    descriptor = f"{module}.{qualname}"
    encoded = descriptor.encode("utf-8")
    if len(encoded) <= _MAX_PYTHON_TYPE_BYTES:
        return descriptor
    suffix = f"#sha256:{hashlib.sha256(encoded).hexdigest()}"
    prefix = encoded[: _MAX_PYTHON_TYPE_BYTES - len(suffix)]
    while True:
        try:
            return f"{prefix.decode('utf-8')}{suffix}"
        except UnicodeDecodeError:
            prefix = prefix[:-1]


def _escape_type_part(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "\\":
            escaped.append("\\\\")
        elif character.isspace() or unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            escaped.append(f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _frontend_value(value: Any, path: str) -> JsonValue:
    try:
        frontend = value._value_frontend
    except AttributeError as error:
        raise ExecutionError(f"{path} has no frontend value") from error
    try:
        return json_value(frontend, f"{path} frontend value")
    except (TypeError, ValueError) as error:
        raise ExecutionError(f"{path} has a nonportable frontend value") from error


def _is_sensitive(value: Any) -> bool:
    return any(_component_kind(item) == "password" for item in _control_tree(value))


def _component_kind(value: Any) -> object:
    args = getattr(value, "_component_args", None)
    return args.get("kind") if isinstance(args, Mapping) else None


def _control_tree(root: Any) -> list[Any]:
    from marimo._plugins.ui._core.ui_element import UIElement

    pending = [root]
    result: list[Any] = []
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        result.append(value)
        element = getattr(value, "element", None)
        if isinstance(element, UIElement):
            pending.append(element)
        elements = getattr(value, "elements", None)
        if isinstance(elements, Mapping):
            pending.extend(item for item in elements.values() if isinstance(item, UIElement))
        elif isinstance(elements, (list, tuple)):
            pending.extend(item for item in elements if isinstance(item, UIElement))
    return result


def _control_domain(value: Any) -> JsonObject:
    if _is_sensitive(value):
        return {}
    args = getattr(value, "_component_args", None)
    if not isinstance(args, Mapping):
        return {}
    result: JsonObject = {}
    for name in ("debounce", "full_width", "max", "min", "options", "step"):
        if name not in args:
            continue
        try:
            result[name] = json_value(args[name], f"control domain {name!r}")
        except (TypeError, ValueError):
            continue
    return result


def _is_native_scalar(value: object) -> bool:
    return value is None or isinstance(value, (bool, str, int, float))


__all__ = [
    "BlobAsset",
    "MarimoCapabilities",
    "NativeReceipt",
    "StateExecution",
    "blob_asset_type",
    "current_document_sha256",
    "declared_ui_values",
    "execute_state",
    "flush_native_caches",
    "inspect_baseline",
    "new_transfer_virtual_file",
    "preflight_exporters",
    "require_capabilities",
    "transfer_runtime_context",
]
