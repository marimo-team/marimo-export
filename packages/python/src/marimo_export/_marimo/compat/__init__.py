from __future__ import annotations

import copy
import dis
import gc
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import re
import struct
import sys
import threading
import time
import unicodedata
import weakref
from _thread import LockType
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any, Literal, cast

import msgspec
from marimo._save.stores.store import Store
from marimo._save.stubs import BlobAsset

from marimo_export._execution.matrix import (
    Baseline,
    Definition,
    MatrixPlan,
    NormalizedState,
    ordinary_cell_code,
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


def _exporter_value_identity(value: Any, label: str) -> JsonValue:
    active: set[int] = set()

    def encode(item: Any, item_label: str) -> JsonValue:
        if item is None:
            return {"type": "none"}
        if type(item) is bool:
            return {"type": "bool", "value": item}
        if type(item) is int:
            return {"type": "int", "value": str(item)}
        if type(item) is float:
            return {"type": "float64", "hex": struct.pack(">d", item).hex()}
        if type(item) is str:
            return {"type": "str", "value": item}
        if type(item) is bytes:
            return {"type": "bytes", "hex": item.hex()}
        if type(item) is object:
            return {"type": "sentinel"}
        if isinstance(item, re.Pattern):
            return {
                "type": "regex",
                "pattern": encode(item.pattern, f"{item_label} pattern"),
                "flags": item.flags,
            }
        if type(item) in {tuple, list, set, frozenset}:
            identifier = id(item)
            if identifier in active:
                raise TypeError(f"{item_label} contains a cycle")
            active.add(identifier)
            try:
                items = [
                    encode(member, f"{item_label}[{index}]") for index, member in enumerate(item)
                ]
            finally:
                active.remove(identifier)
            if type(item) in {set, frozenset}:
                items.sort(key=canonical_bytes)
            return {"type": type(item).__name__, "items": items}
        if type(item) is dict:
            identifier = id(item)
            if identifier in active:
                raise TypeError(f"{item_label} contains a cycle")
            active.add(identifier)
            try:
                entries = [
                    {
                        "key": encode(key, f"{item_label} key"),
                        "value": encode(member, f"{item_label}[{key!r}]"),
                    }
                    for key, member in item.items()
                ]
            finally:
                active.remove(identifier)
            entries.sort(key=canonical_bytes)
            return {"type": "dict", "entries": entries}
        raise TypeError(
            f"{item_label} has unsupported type {type(item).__module__}.{type(item).__qualname__}"
        )

    return encode(value, label)


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
    projections: dict[Any, Literal["hit", "miss"]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ExporterIdentity:
    cache: str
    runtime: str
    environment: str
    modules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ExporterSnapshot:
    module: weakref.ReferenceType[Any]
    runtime: str
    environment: str
    modules: tuple[str, ...]


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
_EXPORTER_SNAPSHOT_LOCK = threading.Lock()
_EXPORTER_SNAPSHOTS: dict[str, _ExporterSnapshot] = {}
_EXPORTER_SNAPSHOT_STATE_ATTRIBUTE = "_marimo_export_exporter_snapshot_state"
_EXPORTER_IMPORT_LOCK = threading.RLock()


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
                        disposition: Literal["hit", "miss"] = "hit" if attempt.hit else "miss"
                        if cell_id in excluded:
                            current.projections[cell_id] = disposition
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
        try:
            identity = _exporter_identity(
                name=exporter.name,
                module=module,
                value=value,
                distributions=reference.distributions,
                package_distributions=package_distributions,
            )
            _record_exporter_snapshot(
                output=output,
                name=exporter.name,
                module=module,
                identity=identity,
            )
        except OutputError:
            raise
        except Exception as error:
            raise OutputError(
                f"output {output!r} exporter {exporter.name!r} could not be fingerprinted",
                code="exporter_identity_failed",
                details={
                    "output": output,
                    "exporter": exporter.name,
                    "exception_type": type(error).__name__,
                },
            ) from error
        resolved[exporter.name] = identity.cache
        identities[output] = identity.cache
    return identities


@contextmanager
def prepared_exporters(plan: MatrixPlan) -> Iterator[Mapping[str, str]]:
    """Resolve custom exporters in a capture-scoped module overlay."""

    custom = {
        projection.exporter.name: runtime_reference(projection.exporter.name).module
        for projection in plan.projections.values()
        if projection.exporter is not None and ":" in projection.exporter.name
    }
    if not custom:
        yield preflight_exporters(plan)
        return

    with _EXPORTER_IMPORT_LOCK:
        original_modules = dict(sys.modules)
        candidates = set(custom.values())
        candidates.update(_recorded_exporter_modules(custom))
        _include_new_package_parents(candidates, original_modules)
        while True:
            with _isolated_modules(
                candidates,
                original_modules,
                roots=set(custom.values()),
            ):
                identities = preflight_exporters(plan)
                discovered = _recorded_exporter_modules(custom)
                if discovered <= candidates:
                    yield identities
                    return
                candidates.update(discovered)
                _include_new_package_parents(candidates, original_modules)
            if len(candidates) > _MAX_EXPORTER_DEPENDENCIES + len(custom):
                name = next(iter(custom))
                raise OutputError(
                    f"exporter {name!r} has too many local modules to isolate",
                    code="exporter_identity_failed",
                    details={"exporter": name},
                )


def _recorded_exporter_modules(exporters: Mapping[str, str]) -> set[str]:
    lock, snapshots = _exporter_snapshot_state()
    with lock:
        return {
            module_name
            for name, root in exporters.items()
            for module_name in (snapshots[name].modules if name in snapshots else (root,))
        }


def _include_new_package_parents(
    names: set[str],
    original_modules: Mapping[str, Any],
) -> None:
    for name in tuple(names):
        parent = name.rpartition(".")[0]
        while parent:
            if parent not in original_modules:
                names.add(parent)
            parent = parent.rpartition(".")[0]


@contextmanager
def _isolated_modules(
    names: set[str],
    original_modules: Mapping[str, Any],
    *,
    roots: set[str],
) -> Iterator[None]:
    missing = object()
    try:
        package_distributions = importlib.metadata.packages_distributions()
    except Exception:
        package_distributions = {}
    package_attributes = {
        name: dict(vars(module))
        for name, module in original_modules.items()
        if module is not None and hasattr(module, "__path__")
    }
    native_get_code = SourceFileLoader.get_code

    def get_code(loader: SourceFileLoader, fullname: str) -> Any:
        filename = loader.get_filename(fullname)
        return loader.source_to_code(loader.get_data(filename), filename)

    try:
        eviction_names = _reloadable_module_names(
            names,
            original_modules,
            roots=roots,
        )
        for name in sorted(
            eviction_names,
            key=lambda value: value.count("."),
            reverse=True,
        ):
            sys.modules.pop(name, None)
            parent_name, separator, attribute = name.rpartition(".")
            parent = sys.modules.get(parent_name) if separator else None
            if parent is not None:
                with suppress(AttributeError):
                    delattr(parent, attribute)
        importlib.invalidate_caches()
        cast(Any, SourceFileLoader).get_code = get_code
        yield
    finally:
        cast(Any, SourceFileLoader).get_code = native_get_code
        selected_distributions = {
            distribution
            for name in roots
            if _is_reloadable_module(sys.modules.get(name))
            for distribution in package_distributions.get(name.partition(".")[0], ())
        }
        rollback_names = _reloadable_module_names(
            names,
            sys.modules,
            roots=roots,
        )
        rollback_names.update(
            name
            for name in set(sys.modules) - set(original_modules)
            if _is_local_source_module(
                name,
                sys.modules[name],
                selected_distributions=selected_distributions,
                package_distributions=package_distributions,
            )
        )
        for name in sorted(
            rollback_names,
            key=lambda value: value.count("."),
            reverse=True,
        ):
            sys.modules.pop(name, None)
        for name in sorted(rollback_names, key=lambda value: value.count(".")):
            original = original_modules.get(name, missing)
            if original is not missing:
                sys.modules[name] = original
        for name in sorted(rollback_names):
            parent_name, separator, attribute = name.rpartition(".")
            if not separator:
                continue
            parent = original_modules.get(parent_name)
            if parent is None:
                continue
            original = package_attributes.get(parent_name, {}).get(attribute, missing)
            if original is missing:
                with suppress(AttributeError):
                    delattr(parent, attribute)
            else:
                setattr(parent, attribute, original)
        importlib.invalidate_caches()


def _reloadable_module_names(
    names: set[str],
    modules: Mapping[str, Any],
    *,
    roots: set[str],
) -> set[str]:
    reloadable = {name for name in names if _is_reloadable_module(modules.get(name))}
    for root in roots:
        if _is_reloadable_module(modules.get(root)):
            continue
        candidate = root
        while candidate:
            reloadable.discard(candidate)
            candidate = candidate.rpartition(".")[0]
    return reloadable


def _is_reloadable_module(module: Any) -> bool:
    return module is None or _is_python_source_module(module) or _is_namespace_package(module)


def _is_namespace_package(module: Any) -> bool:
    spec = getattr(module, "__spec__", None)
    return spec is not None and spec.origin is None and spec.submodule_search_locations is not None


def _is_python_source_module(module: Any) -> bool:
    origin = _module_origin(module)
    return origin is not None and origin.suffix == ".py"


def _is_local_source_module(
    name: str,
    module: Any,
    *,
    selected_distributions: set[str],
    package_distributions: Mapping[str, list[str]],
) -> bool:
    if not _is_python_source_module(module):
        return False
    top_level = name.partition(".")[0]
    if top_level == "marimo_export" or top_level in sys.stdlib_module_names:
        return False
    distributions = set(package_distributions.get(top_level, ()))
    if not distributions:
        return True
    return bool(distributions & selected_distributions)


def _record_exporter_snapshot(
    *,
    output: str,
    name: str,
    module: Any,
    identity: _ExporterIdentity,
) -> None:
    lock, snapshots = _exporter_snapshot_state()
    with lock:
        previous = snapshots.get(name)
        if (
            previous is not None
            and previous.module() is module
            and previous.environment != identity.environment
            and previous.runtime == identity.runtime
        ):
            raise OutputError(
                f"output {output!r} exporter {name!r} changed while its loaded module stayed stale",
                code="exporter_stale",
                details={"output": output, "exporter": name},
            )
        snapshots[name] = _ExporterSnapshot(
            module=weakref.ref(module),
            runtime=identity.runtime,
            environment=identity.environment,
            modules=identity.modules,
        )


def _exporter_snapshot_state() -> tuple[LockType, dict[str, _ExporterSnapshot]]:
    try:
        from marimo._runtime.context import get_context

        context = get_context()
    except Exception:
        return _EXPORTER_SNAPSHOT_LOCK, _EXPORTER_SNAPSHOTS
    owner = context.app_kernel_runner_registry
    state = getattr(owner, _EXPORTER_SNAPSHOT_STATE_ATTRIBUTE, None)
    if (
        isinstance(state, tuple)
        and len(state) == 2
        and isinstance(state[0], LockType)
        and isinstance(state[1], dict)
    ):
        return cast(tuple[LockType, dict[str, _ExporterSnapshot]], state)
    created: tuple[LockType, dict[str, _ExporterSnapshot]] = (
        threading.Lock(),
        {},
    )
    setattr(owner, _EXPORTER_SNAPSHOT_STATE_ATTRIBUTE, created)
    return created


def _direct_callable_code(value: Any) -> Any | None:
    if inspect.ismethod(value):
        value = value.__func__
    if inspect.isfunction(value):
        return value.__code__
    candidate = inspect.getattr_static(value, "__code__", None)
    return candidate if inspect.iscode(candidate) else None


def _callable_code(value: Any) -> Any | None:
    candidate = _direct_callable_code(value)
    if candidate is not None:
        return candidate
    call = inspect.getattr_static(type(value), "__call__", None)
    if isinstance(call, (classmethod, staticmethod)):
        call = call.__func__
    if inspect.isfunction(call):
        return call.__code__
    candidate = inspect.getattr_static(call, "__code__", None)
    return candidate if inspect.iscode(candidate) else None


def _exporter_identity(
    *,
    name: str,
    module: Any,
    value: Any,
    distributions: tuple[str, ...],
    package_distributions: Mapping[str, list[str]],
) -> _ExporterIdentity:
    dependencies, dependency_modules, isolation_modules = _exporter_dependencies(
        value,
        module,
        package_distributions=package_distributions,
    )
    common: JsonObject = {
        "name": name,
        "module": str(getattr(module, "__name__", "")),
        "symbol_type": f"{type(value).__module__}.{type(value).__qualname__}",
    }
    runtime_dependencies = {
        key: value for key, value in dependencies.items() if not key.startswith("module:")
    }
    environment_dependencies = {
        key: value for key, value in dependencies.items() if key.startswith("module:")
    }
    payload: JsonObject = {**common, "dependencies": dependencies}
    runtime_payload: JsonObject = {
        **common,
        "dependencies": runtime_dependencies,
    }
    environment_payload: JsonObject = {
        **common,
        "dependencies": environment_dependencies,
    }
    origin = getattr(getattr(module, "__spec__", None), "origin", None)
    if isinstance(origin, str) and origin not in {"built-in", "frozen"}:
        module_digest = _file_digest(Path(origin))
        if module_digest is not None:
            payload["module_sha256"] = module_digest
            environment_payload["module_sha256"] = module_digest
    code = _callable_code(value)
    if code is not None:
        from marimo._save.hash import hash_module

        with suppress(TypeError, ValueError):
            callable_digest = hash_module(code).hex()
            payload["callable_sha256"] = callable_digest
            runtime_payload["callable_sha256"] = callable_digest
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError):
        source = None
    if source is not None:
        source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        payload["source_sha256"] = source_digest
        environment_payload["source_sha256"] = source_digest

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
        environment_payload["distributions"] = versions
    return _ExporterIdentity(
        cache=hashlib.sha256(canonical_bytes(payload)).hexdigest(),
        runtime=hashlib.sha256(canonical_bytes(runtime_payload)).hexdigest(),
        environment=hashlib.sha256(canonical_bytes(environment_payload)).hexdigest(),
        modules=tuple(sorted(isolation_modules)),
    )


def _exporter_dependencies(
    value: Any,
    module: Any,
    *,
    package_distributions: Mapping[str, list[str]],
) -> tuple[JsonObject, frozenset[str], frozenset[str]]:
    records: JsonObject = {}
    module_names: set[str] = set()
    isolation_module_names: set[str] = set()
    recorded_modules: set[int] = set()
    expanded_modules: set[int] = set()
    visited_callables: set[int] = set()
    resolved_imports: dict[str, Any | None] = {}
    selected_owner = sys.modules.get(str(getattr(value, "__module__", "")))
    selected_distributions: set[str] = set()
    for selected_module in (module, selected_owner):
        selected_top_level = str(getattr(selected_module, "__name__", "")).partition(".")[0]
        selected_distributions.update(package_distributions.get(selected_top_level, ()))

    def is_local_module(dependency: Any) -> bool:
        origin = _module_origin(dependency)
        if origin is None:
            return False
        top_level = str(getattr(dependency, "__name__", "")).partition(".")[0]
        if not top_level or top_level == "marimo_export" or top_level in sys.stdlib_module_names:
            return False
        dependency_distributions = package_distributions.get(top_level)
        if not dependency_distributions:
            return True
        return bool(selected_distributions.intersection(dependency_distributions))

    def record(name: str, digest: str) -> None:
        if name in records:
            return
        if len(records) >= _MAX_EXPORTER_DEPENDENCIES:
            raise ValueError(
                f"exporter dependency graph exceeds {_MAX_EXPORTER_DEPENDENCIES} entries"
            )
        records[name] = digest

    def visit_module(
        dependency: Any,
        *,
        expand: bool,
    ) -> None:
        identifier = id(dependency)
        name = str(getattr(dependency, "__name__", ""))
        if identifier not in recorded_modules:
            recorded_modules.add(identifier)
            if name:
                module_names.add(name)
                if dependency is module or is_local_module(dependency):
                    isolation_module_names.add(name)
            origin = _module_origin(dependency)
            if origin is not None:
                digest = _file_digest(origin)
                if digest is not None:
                    record(f"module:{name}", digest)
        if not is_local_module(dependency):
            return
        if not expand or identifier in expanded_modules:
            return
        expanded_modules.add(identifier)
        namespace = getattr(dependency, "__dict__", {})
        for attribute in sorted(namespace):
            member = namespace[attribute]
            if inspect.ismodule(member):
                if is_local_module(member):
                    visit_module(
                        member,
                        expand=True,
                    )
                continue
            if inspect.isfunction(member):
                owner = sys.modules.get(str(getattr(member, "__module__", "")))
                if owner is dependency or (owner is not None and is_local_module(owner)):
                    visit_callable(member)
                continue
            if inspect.isclass(member):
                owner = sys.modules.get(str(getattr(member, "__module__", "")))
                if owner is dependency or (owner is not None and is_local_module(owner)):
                    visit_callable(member)

    def referenced_attributes(code: Any, name: str) -> frozenset[str]:
        attributes: set[str] = set()
        instructions = tuple(dis.get_instructions(code))
        saw_reference = False
        saw_dynamic_reference = False
        for index, instruction in enumerate(instructions[:-1]):
            if (
                instruction.opname
                not in {
                    "LOAD_GLOBAL",
                    "LOAD_NAME",
                    "LOAD_DEREF",
                    "LOAD_FAST",
                }
                or instruction.argval != name
            ):
                continue
            saw_reference = True
            following = instructions[index + 1]
            if following.opname in {"LOAD_ATTR", "LOAD_METHOD"} and isinstance(
                following.argval, str
            ):
                attributes.add(following.argval)
                continue
            if (
                following.opname == "LOAD_CONST"
                and isinstance(following.argval, str)
                and index > 0
                and instructions[index - 1].opname in {"LOAD_GLOBAL", "LOAD_NAME"}
                and instructions[index - 1].argval == "getattr"
            ):
                attributes.add(following.argval)
                continue
            saw_dynamic_reference = True
        if saw_reference and saw_dynamic_reference:
            attributes.add("*")
        return frozenset(attributes)

    def visit_code_globals(dependency: Any, code: Any) -> None:
        owner = sys.modules.get(str(getattr(dependency, "__module__", "")))
        namespace = getattr(owner, "__dict__", {}) if owner is not None else {}
        names = {
            instruction.argval
            for instruction in dis.get_instructions(code)
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME"}
            and isinstance(instruction.argval, str)
        }
        module_name = str(getattr(dependency, "__module__", ""))
        qualname = str(
            getattr(
                dependency,
                "__qualname__",
                getattr(dependency, "__name__", type(dependency).__qualname__),
            )
        )
        for name in sorted(names):
            if name not in namespace:
                continue
            referenced = namespace[name]
            visit_reference(
                referenced,
                f"code-value:{module_name}:{qualname}:{code.co_name}:{name}",
                module_members=(
                    referenced_attributes(code, name) if inspect.ismodule(referenced) else None
                ),
            )
        for constant in code.co_consts:
            if inspect.iscode(constant):
                visit_code_globals(dependency, constant)

    def visit_module_members(
        dependency: Any,
        members: frozenset[str],
        *,
        label: str,
    ) -> None:
        visit_module(dependency, expand=False)
        if "*" in members:
            visit_module(dependency, expand=True)
            return
        module_name = str(getattr(dependency, "__name__", ""))
        namespace = getattr(dependency, "__dict__", {})
        for member_name in sorted(members):
            member = namespace.get(member_name)
            if member is None and module_name:
                candidate = f"{module_name}.{member_name}"
                if candidate not in resolved_imports:
                    try:
                        resolved_imports[candidate] = importlib.import_module(candidate)
                    except Exception:
                        resolved_imports[candidate] = None
                member = resolved_imports[candidate]
            if member is None:
                continue
            visit_reference(
                member,
                f"{label}:{member_name}",
            )

    def visit_imports(dependency: Any, code: Any) -> None:
        module_name = str(getattr(dependency, "__module__", ""))
        owner = sys.modules.get(module_name)
        package = str(getattr(owner, "__package__", "")) if owner is not None else ""
        instructions = tuple(dis.get_instructions(code))
        for index, instruction in enumerate(instructions):
            if instruction.opname != "IMPORT_NAME" or not isinstance(instruction.argval, str):
                continue
            level = 0
            fromlist: tuple[str, ...] = ()
            if index >= 2:
                level_value = instructions[index - 2].argval
                fromlist_value = instructions[index - 1].argval
                if isinstance(level_value, int):
                    level = level_value
                if isinstance(fromlist_value, tuple) and all(
                    isinstance(item, str) for item in fromlist_value
                ):
                    fromlist = fromlist_value
            imported_name = instruction.argval
            if level:
                if not package:
                    continue
                try:
                    imported_name = importlib.util.resolve_name(
                        f"{'.' * level}{imported_name}",
                        package,
                    )
                except (ImportError, ValueError):
                    continue
            if not imported_name:
                continue
            if imported_name not in resolved_imports:
                try:
                    resolved_imports[imported_name] = importlib.import_module(imported_name)
                except Exception:
                    resolved_imports[imported_name] = None
            imported = resolved_imports[imported_name]
            if imported is None:
                continue
            members: frozenset[str]
            if fromlist:
                members = frozenset(member for member in fromlist if isinstance(member, str))
            else:
                aliases: set[str] = set()
                for following in instructions[index + 1 : index + 8]:
                    if following.opname in {"STORE_FAST", "STORE_NAME"} and isinstance(
                        following.argval, str
                    ):
                        aliases.add(following.argval)
                        break
                    if following.opname in {
                        "IMPORT_NAME",
                        "POP_TOP",
                        "RETURN_VALUE",
                        "RETURN_CONST",
                    }:
                        break
                direct_members = {
                    name
                    for name in code.co_names
                    if isinstance(name, str) and name in vars(imported)
                }
                for alias in aliases:
                    direct_members.update(referenced_attributes(code, alias))
                members = frozenset(direct_members)
            visit_module_members(
                imported,
                members,
                label=f"import:{module_name}:{imported_name}",
            )
        for constant in code.co_consts:
            if inspect.iscode(constant):
                visit_imports(dependency, constant)

    def visit_callable(dependency: Any) -> None:
        if inspect.ismethod(dependency):
            dependency = dependency.__func__
        identifier = id(dependency)
        if identifier in visited_callables:
            return
        visited_callables.add(identifier)
        module_name = str(getattr(dependency, "__module__", ""))
        owner = sys.modules.get(module_name)
        qualname = str(
            getattr(
                dependency,
                "__qualname__",
                getattr(dependency, "__name__", type(dependency).__qualname__),
            )
        )
        if owner is not None:
            visit_module(owner, expand=False)
        code = _direct_callable_code(dependency)
        if code is None and inspect.isclass(dependency):
            if owner is None or (owner is not module and not is_local_module(owner)):
                return
            for attribute in sorted(vars(dependency)):
                if attribute.startswith("__"):
                    continue
                member = inspect.getattr_static(dependency, attribute)
                if isinstance(member, (classmethod, staticmethod)):
                    member = member.__func__
                if callable(member):
                    visit_callable(member)
                    continue
                if inspect.isdatadescriptor(member) or inspect.ismethoddescriptor(member):
                    continue
                try:
                    portable = _exporter_value_identity(
                        member,
                        f"class-value:{module_name}:{qualname}:{attribute}",
                    )
                except TypeError:
                    continue
                record(
                    f"class-value:{module_name}:{qualname}:{attribute}",
                    hashlib.sha256(canonical_bytes(portable)).hexdigest(),
                )
            return
        if code is None:
            instance_state = getattr(dependency, "__dict__", None)
            if isinstance(instance_state, dict) and instance_state:
                try:
                    portable = _exporter_value_identity(
                        instance_state,
                        f"callable-state:{module_name}:{qualname}",
                    )
                except TypeError:
                    for attribute, member in sorted(instance_state.items()):
                        visit_reference(
                            member,
                            f"callable-value:{module_name}:{qualname}:{attribute}",
                        )
                else:
                    record(
                        f"callable-state:{module_name}:{qualname}",
                        hashlib.sha256(canonical_bytes(portable)).hexdigest(),
                    )
            call = inspect.getattr_static(type(dependency), "__call__", None)
            if inspect.isfunction(call):
                visit_callable(call)
            return
        from marimo._save.hash import hash_module

        record(f"callable:{module_name}:{qualname}", hash_module(code).hex())
        if owner is not None and owner is not module and not is_local_module(owner):
            return
        visit_imports(dependency, code)
        visit_code_globals(dependency, code)
        defaults = getattr(dependency, "__defaults__", None)
        if isinstance(defaults, tuple):
            for index, default in enumerate(defaults):
                visit_reference(
                    default,
                    f"default:{module_name}:{qualname}:{index}",
                )
        keyword_defaults = getattr(dependency, "__kwdefaults__", None)
        if isinstance(keyword_defaults, dict):
            for default_name, default in sorted(keyword_defaults.items()):
                visit_reference(
                    default,
                    f"keyword-default:{module_name}:{qualname}:{default_name}",
                )
        try:
            closure = inspect.getclosurevars(dependency)
        except TypeError:
            return
        references = {**closure.globals, **closure.nonlocals}
        for dependency_name, referenced in sorted(references.items()):
            visit_reference(
                referenced,
                f"value:{module_name}:{qualname}:{dependency_name}",
                module_members=(
                    referenced_attributes(code, dependency_name)
                    if inspect.ismodule(referenced)
                    else None
                ),
            )

    def visit_reference(
        dependency: Any,
        label: str,
        *,
        module_members: frozenset[str] | None = None,
    ) -> None:
        if inspect.ismodule(dependency):
            if module_members is None:
                visit_module(dependency, expand=True)
            else:
                visit_module_members(
                    dependency,
                    module_members,
                    label=label,
                )
            return
        if (
            inspect.isfunction(dependency)
            or inspect.ismethod(dependency)
            or inspect.isclass(dependency)
        ):
            visit_callable(dependency)
            return
        if inspect.isbuiltin(dependency):
            module_name = str(getattr(dependency, "__module__", ""))
            owner = sys.modules.get(module_name)
            if owner is not None:
                visit_module(owner, expand=False)
            portable: JsonValue = {
                "type": "builtin-function",
                "module": module_name,
                "name": str(
                    getattr(
                        dependency,
                        "__qualname__",
                        getattr(dependency, "__name__", ""),
                    )
                ),
            }
            record(label, hashlib.sha256(canonical_bytes(portable)).hexdigest())
            return
        if callable(dependency):
            visit_callable(dependency)
            return
        try:
            portable = _exporter_value_identity(dependency, label)
        except TypeError:
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
    return (
        records,
        frozenset(module_names),
        frozenset(isolation_module_names),
    )


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
    from marimo._runtime.runner.hooks_post_execution import (
        _set_run_result_status,
    )
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
    child._kernel._hooks.add_post_execution(_set_run_result_status)
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
        overrides = {plan.state_name: state.fingerprint}
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

                elements: list[tuple[str, UIElement[Any, Any]]] = []
                values: list[JsonValue] = []
                with child_context.install():
                    child_context.ui_element_registry.register_scope(
                        child.globals,
                        defs=set(state.ui_values),
                    )
                for name, value in state.ui_values.items():
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
            reactive_cells = {
                cell_id
                for cell_id, cell in child._kernel.graph.cells.items()
                if cell.stale and cell_id not in projection_id_set
            }
            await child.run(reactive_cells | set(projection_id_set))
            _raise_child_errors(child, reactive_cells, state.name)
            for output, cell_id in projection_ids.items():
                _raise_child_errors(child, {cell_id}, state.name, output=output)
            receipt_items: list[NativeReceipt] = []
            for output in plan.outputs:
                cell_id = projection_ids[output]
                disposition = upstream_cache.projections.get(cell_id)
                if disposition is None:
                    raise OutputError(
                        f"output {output!r} did not execute through marimo's cell cache",
                        code="cache_receipt_missing",
                        details={"state": state.name, "output": output},
                    )
                receipt_items.append(
                    _projection_receipt(
                        child,
                        cell_id,
                        output,
                        disposition,
                    )
                )
            receipts = tuple(receipt_items)
            projection_execution_seconds = time.monotonic() - projection_started
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


def _projection_receipt(
    child: Any,
    cell_id: Any,
    output: str,
    disposition: Literal["hit", "miss"],
) -> NativeReceipt:
    from marimo._runtime.context import get_context
    from marimo._save.hash import cache_attempt_from_hash
    from marimo._save.loaders import flush_active_caches
    from marimo._save.loaders.lazy import LazyLoader

    with child._runtime_context.install():
        context = get_context()
        flush_active_caches()
        cell = child._kernel.graph.cells[cell_id]
        native_loader = LazyLoader(name="cell_cache", store=context.cache.store)
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
    "declared_ui_values",
    "execute_state",
    "flush_native_caches",
    "inspect_baseline",
    "new_transfer_virtual_file",
    "preflight_exporters",
    "prepared_exporters",
    "require_capabilities",
    "transfer_runtime_context",
]
