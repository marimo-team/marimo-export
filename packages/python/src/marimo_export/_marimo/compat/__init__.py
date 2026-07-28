from __future__ import annotations

import copy
import gc
import hashlib
import unicodedata
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
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
from marimo_export.publication import (
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
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
    "code_mode_projection_cells",
    "definition_overrides",
    "setup_definition_overrides",
)
_MAX_PYTHON_TYPE_BYTES = 512


@dataclass(frozen=True, slots=True)
class MarimoCapabilities:
    """Private marimo capabilities available in the selected kernel."""

    version: str
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectionLease:
    """Operation-local state token and output leaves installed in the parent."""

    state_cell: str
    state_name: str
    state_code: str
    cells: Mapping[str, str]
    codes: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.state_cell, str) or not self.state_cell:
            raise TypeError("state_cell must be a non-empty string")
        if not isinstance(self.state_name, str) or not self.state_name.isidentifier():
            raise TypeError("state_name must be a Python identifier")
        if not isinstance(self.state_code, str) or not self.state_code:
            raise TypeError("state_code must be a non-empty string")
        object.__setattr__(self, "cells", MappingProxyType(dict(self.cells)))
        object.__setattr__(self, "codes", MappingProxyType(dict(self.codes)))


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


def require_capabilities() -> MarimoCapabilities:
    """Validate the focused private seams used by the rewrite."""

    missing: list[str] = []
    try:
        import marimo
        from marimo._ast.app import InternalApp
        from marimo._ast.load import load_notebook_ir
        from marimo._code_mode import AsyncCodeModeContext
        from marimo._code_mode import get_context as get_code_context
        from marimo._runtime.app.kernel_runner import AppKernelRunner
        from marimo._runtime.context import get_context as get_runtime_context
        from marimo._runtime.context.kernel_context import KernelRuntimeContext
        from marimo._runtime.dataflow import prune_cells_for_overrides
        from marimo._save.hash import cache_attempt_from_hash
        from marimo._save.loaders import flush_active_caches
        from marimo._save.loaders.lazy import LazyLoader
        from marimo._save.stubs import BlobAsset as NativeBlobAsset
        from marimo._save.stubs.lazy_stub import BLOB_DESERIALIZERS, BLOB_SERIALIZERS
        from marimo._schemas.serialization import NotebookSerializationV1
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
        (cache_attempt_from_hash, "cell_cache_receipts"),
        (flush_active_caches, "cell_cache_receipts"),
        (LazyLoader, "cell_cache_receipts"),
        (NotebookSerializationV1, "child_sessions"),
    ):
        if not callable(value):
            missing.append(name)
    if not all(
        callable(getattr(AsyncCodeModeContext, name, None))
        for name in ("create_cell", "delete_cell", "set_ui_value")
    ):
        missing.append("code_mode_projection_cells")
    if not callable(get_code_context):
        missing.append("code_mode_projection_cells")
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


async def install_projections(plan: MatrixPlan) -> ProjectionLease:
    """Append one state token and one unexecuted leaf per output."""

    state_name = _projection_state_name(plan)
    state_code = f"{state_name} = {plan.states[0].fingerprint!r}"
    cells: dict[str, str] = {}
    codes = {
        output: projection_code(output, source, state_name)
        for output, source in plan.output_sources.items()
    }
    async with _ephemeral_code_context() as context:
        if state_name in context._kernel.graph.definitions:
            raise ExecutionError(
                f"temporary projection state name {state_name!r} collides with the notebook",
                code="projection_invalid",
                details={"definition": state_name},
            )
        after = str(context.cells[-1].id) if len(context.cells) else None
        created: list[str] = []
        try:
            state_cell = str(
                context.create_cell(
                    state_code,
                    after=after,
                    hide_code=True,
                    disabled=False,
                )
            )
            created.append(state_cell)
            after = state_cell
            for output, code in codes.items():
                cell_id = context.create_cell(
                    code,
                    after=after,
                    hide_code=True,
                    disabled=False,
                )
                cells[output] = str(cell_id)
                created.append(str(cell_id))
                after = str(cell_id)
        except BaseException:
            cleanup_errors: list[BaseException] = []
            for cell_id in reversed(created):
                try:
                    context.delete_cell(cell_id)
                except BaseException as error:
                    cleanup_errors.append(error)
            if cleanup_errors:
                raise ExecutionError(
                    "temporary projection installation could not be rolled back",
                    code="projection_cleanup_failed",
                    details={"cell_ids": created},
                ) from cleanup_errors[0]
            raise
    return ProjectionLease(
        state_cell=state_cell,
        state_name=state_name,
        state_code=state_code,
        cells=cells,
        codes=codes,
    )


def _projection_state_name(plan: MatrixPlan) -> str:
    payload = json_object(
        {
            "inputs": plan.inputs,
            "outputs": plan.outputs,
            "output_sources": plan.output_sources,
        },
        "projection plan",
    )
    suffix = hashlib.sha256(canonical_bytes(payload)).hexdigest()[:16]
    return f"marimo_export_state_{suffix}"


async def delete_projections(lease: ProjectionLease) -> None:
    """Delete every still-live lease cell in one code-mode transaction."""

    cell_ids = [*lease.cells.values(), lease.state_cell]
    errors: list[BaseException] = []
    async with _ephemeral_code_context() as context:
        existing = {str(cell.id) for cell in context.cells}
        for cell_id in cell_ids:
            if cell_id not in existing:
                errors.append(RuntimeError(f"projection cell {cell_id!r} is missing"))
                continue
            try:
                context.delete_cell(cell_id)
            except BaseException as error:
                errors.append(error)
    if errors:
        raise ExecutionError(
            "one or more projection cells could not be deleted",
            code="projection_cleanup_failed",
            details={"cell_ids": cell_ids},
        ) from errors[0]


@asynccontextmanager
async def _ephemeral_code_context() -> AsyncIterator[Any]:
    """Broadcast projection edits as kernel transactions to skip autosave."""

    from marimo._code_mode import get_context
    from marimo._messaging.notification import (
        NotebookDocumentTransactionNotification,
    )

    context = get_context()
    original = context.broadcast_raw_notification

    def broadcast(notification: object) -> None:
        if isinstance(notification, NotebookDocumentTransactionNotification):
            notification = NotebookDocumentTransactionNotification(
                transaction=msgspec.structs.replace(
                    notification.transaction,
                    source="kernel",
                )
            )
        original(notification)

    context.broadcast_raw_notification = broadcast
    try:
        async with context as entered:
            yield entered
    finally:
        context.broadcast_raw_notification = original


async def execute_state(
    state: NormalizedState,
    plan: MatrixPlan,
    lease: ProjectionLease,
) -> tuple[NativeReceipt, ...]:
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

    code_context = get_code_context()
    runtime = get_runtime_context()
    cells = tuple(code_context.cells)
    notebook = NotebookSerializationV1(
        app=AppInstantiation(options=runtime.app_config.asdict()),
        cells=[
            CellDef(
                code=cell.code,
                name=cell.name,
                options=cell.config.asdict(),
            )
            for cell in cells
        ],
        filename=runtime.filename,
    )
    app = load_notebook_ir(notebook, filepath=runtime.filename)
    internal = InternalApp(app)
    child = AppKernelRunner(internal)
    child_context = child._runtime_context
    try:
        config = cast(dict[str, Any], copy.deepcopy(runtime.marimo_config))
        runtime_config = cast(dict[str, Any], config["runtime"])
        runtime_config["on_cell_change"] = "autorun"
        runtime_config["auto_instantiate"] = True
        runtime_config["auto_reload"] = "off"
        runtime_config["cache_cells"] = True
        cast(Any, child._kernel).user_config = config
        child._kernel.reactive_execution_mode = "autorun"
        overrides = dict(state.ordinary_overrides)
        overrides[lease.state_name] = state.fingerprint
        child._kernel.globals.update(overrides)

        projection_ids = _projection_ids(internal, lease)
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
        await child.run(cells_to_run)
        _raise_child_errors(child, cells_to_run, state.name)

        if state.ui_values:
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

        receipts: list[NativeReceipt] = []
        for output in plan.outputs:
            cell_id = projection_ids[output]
            receipts.append(
                await _execute_projection(
                    child,
                    cell_id,
                    output,
                    state.name,
                )
            )
        return tuple(receipts)
    finally:
        with child_context.install(), suppress(Exception):
            child._kernel.cache_callbacks.teardown()
        del child
        gc.collect()


def _projection_ids(internal: Any, lease: ProjectionLease) -> dict[str, Any]:
    by_code: dict[str, list[Any]] = {}
    for cell_id, data in zip(
        internal.cell_manager.cell_ids(),
        internal.cell_manager.cell_data(),
        strict=True,
    ):
        by_code.setdefault(data.code, []).append(cell_id)
    result: dict[str, Any] = {}
    for output, code in lease.codes.items():
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
    "ProjectionLease",
    "blob_asset_type",
    "current_document_sha256",
    "declared_ui_values",
    "delete_projections",
    "execute_state",
    "inspect_baseline",
    "install_projections",
    "new_transfer_virtual_file",
    "require_capabilities",
    "transfer_runtime_context",
]
