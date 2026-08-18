"""Inspect the attached marimo kernel through stable export records."""

from __future__ import annotations

import hashlib
import inspect
import unicodedata
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from marimo_export._execution.plan import Baseline, Definition
from marimo_export._json import JsonObject, JsonValue, canonical_bytes, json_object, json_value
from marimo_export._marimo.capabilities import MarimoCapabilities
from marimo_export.errors import CompatibilityError, ExecutionError

_CAPABILITIES = (
    "asset_transfer",
    "blob_asset",
    "cache_cells",
    "cell_cache_receipts",
    "child_sessions",
    "child_ui_updates",
    "definition_overrides",
    "setup_definition_overrides",
    "synthetic_output_cells",
)
_MAX_PYTHON_TYPE_BYTES = 512


def runtime_path() -> str | None:
    """Return the selected notebook path from the attached runtime."""

    from marimo._runtime.context import get_context

    value = get_context().filename
    return value if isinstance(value, str) and value else None


def require_capabilities() -> MarimoCapabilities:
    """Validate the focused private seams used by the rewrite."""

    missing: list[str] = []
    try:
        import cryptography
        import marimo
        from marimo._ast.app import InternalApp
        from marimo._ast.load import load_notebook_ir
        from marimo._code_mode import get_context as get_code_context
        from marimo._runtime.app.kernel_runner import AppKernelRunner
        from marimo._runtime.context import get_context as get_runtime_context
        from marimo._runtime.context.kernel_context import KernelRuntimeContext
        from marimo._runtime.dataflow import prune_cells_for_overrides
        from marimo._runtime.executor.lifecycles.cached import (
            CachedLifecycle,
        )
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
        from marimo._save.loaders.lazy import (
            CacheSignatureError,
            LazyLoader,
            _incomplete_cache_error,
        )
        from marimo._save.stubs import BlobAsset as NativeBlobAsset
        from marimo._save.stubs.lazy_stub import BLOB_DESERIALIZERS, BLOB_SERIALIZERS
        from marimo._schemas.serialization import CellDef, NotebookSerializationV1
    except ImportError as error:
        raise CompatibilityError(
            "the attached marimo runtime lacks required export capabilities",
            code="marimo_incompatible",
        ) from error

    if not getattr(cryptography, "__version__", None):
        missing.append("cell_cache_receipts")

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
        (CellDef, "synthetic_output_cells"),
        (NotebookSerializationV1, "child_sessions"),
    ):
        if not callable(value):
            missing.append(name)
    if not callable(get_code_context):
        missing.append("child_sessions")
    from marimo_export._marimo.compat.cache import SequentialLazyLoader

    if (
        inspect.signature(LazyLoader._read_blobs)
        != inspect.signature(SequentialLazyLoader._read_blobs)
        or tuple(inspect.signature(CachedLifecycle.setup).parameters) != ("self", "cell", "glbls")
        or not {"_attempts", "_exec_starts"}.issubset(
            getattr(getattr(CachedLifecycle.__init__, "__code__", None), "co_names", ())
        )
        or not isinstance(CacheSignatureError, type)
        or not callable(_incomplete_cache_error)
    ):
        missing.append("cell_cache_receipts")
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
            frontend_value: JsonValue | None = None
            portable_input = True
            if is_ui:
                try:
                    frontend_value = _ui_baseline_value(value, f"definition {name!r}")
                except ExecutionError:
                    portable_input = False
            else:
                try:
                    json_value(value, f"definition {name!r}")
                except (TypeError, ValueError):
                    portable_input = False
            definitions[name] = Definition(
                name=name,
                cell_id=str(owner),
                siblings=tuple(sorted(cell.defs)),
                kind="ui" if is_ui else "ordinary",
                python_type=_python_type(value),
                value=value,
                frontend_value=frontend_value,
                portable_input=portable_input,
                ui_patch=_is_anywidget(value) if is_ui else False,
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
                result[name] = _ui_baseline_value(value, f"definition {name!r}")
    return result


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


def _ui_baseline_value(value: Any, path: str) -> JsonValue:
    if _is_anywidget(value):
        try:
            return json_value(value.value, f"{path} widget state")
        except (TypeError, ValueError) as error:
            raise ExecutionError(f"{path} has a nonportable widget state") from error
    return _frontend_value(value, path)


def _is_anywidget(value: Any) -> bool:
    from marimo._plugins.ui._impl.from_anywidget import anywidget

    return isinstance(value, anywidget)


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


__all__ = [
    "declared_ui_values",
    "inspect_baseline",
    "require_capabilities",
    "runtime_path",
]
