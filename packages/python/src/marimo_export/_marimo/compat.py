from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from marimo_export.errors import SessionError


@dataclass(frozen=True, slots=True)
class MarimoCapabilities:
    """Private marimo features required by the live capture adapter."""

    version: str
    names: tuple[str, ...]


_CAPABILITY_NAMES = (
    "blob-asset",
    "code-mode",
    "lazy-cache-receipt",
    "virtual-file-transfer",
)


def require_capabilities() -> MarimoCapabilities:
    """Validate the private marimo seams used by the attached kernel."""

    missing: list[str] = []

    try:
        import marimo
        from marimo._code_mode import AsyncCodeModeContext, get_context
        from marimo._messaging.cell_output import CellOutput
        from marimo._messaging.notification_utils import CellNotificationUtils
        from marimo._output import formatting
        from marimo._runtime.context import (
            ContextNotInitializedError,
        )
        from marimo._runtime.context import (
            get_context as get_runtime_context,
        )
        from marimo._runtime.control_flow import MarimoStopError
        from marimo._runtime.dataflow.graph import DirectedGraph
        from marimo._runtime.runner.cell_runner import RunResult
        from marimo._runtime.runner.hooks import NotebookCellHooks
        from marimo._runtime.virtual_file import (
            VirtualFile,
            VirtualFileRegistry,
            random_filename,
        )
        from marimo._save.loaders import flush_active_caches
        from marimo._save.loaders.lazy import LazyLoader, LazyStore
        from marimo._save.save import persistent_cache
        from marimo._save.stores.store import Store
        from marimo._save.stubs import BlobAsset
        from marimo._save.stubs.lazy_stub import (
            BLOB_DESERIALIZERS,
            BLOB_SERIALIZERS,
        )
    except ImportError as error:
        raise SessionError(
            "the attached marimo environment lacks live export capabilities"
        ) from error

    if not callable(get_context) or not _methods(
        AsyncCodeModeContext,
        "__aenter__",
        "__aexit__",
        "run_cell",
        "set_ui_value",
    ):
        missing.append("code-mode")
    if not callable(get_runtime_context):
        missing.append("runtime-context")
    if not callable(flush_active_caches) or not _methods(
        LazyLoader,
        "build_path",
        "flush",
    ):
        missing.append("lazy-cache-flush")
    if not isinstance(LazyStore, type):
        missing.append("lazy-store")
    cache_parameters = inspect.signature(persistent_cache).parameters
    if "store" not in cache_parameters:
        missing.append("persistent-cache-store")
    function_parameter = cache_parameters.get("fn")
    if function_parameter is None or function_parameter.kind is inspect.Parameter.KEYWORD_ONLY:
        missing.append("persistent-cache-function")
    if not isinstance(BlobAsset, type):
        missing.append("blob-asset")
    if not _blob_asset_codec(BlobAsset, BLOB_SERIALIZERS, BLOB_DESERIALIZERS):
        missing.append("blob-asset-codec")
    if (
        not _methods(VirtualFileRegistry, "add", "has", "remove")
        or not _methods(Store, "get", "hit", "put")
        or not callable(random_filename)
        or not _virtual_file_shape(VirtualFile)
    ):
        missing.append("virtual-file-transfer")
    if (
        not _methods(
            NotebookCellHooks,
            "add_post_execution",
            "copy",
        )
        or not isinstance(MarimoStopError, type)
        or not {
            "accumulated_output",
            "exception",
            "output",
        }.issubset(getattr(RunResult, "__dataclass_fields__", {}))
    ):
        missing.append("execution-hook-capture")
    if (
        not isinstance(CellOutput, type)
        or not callable(getattr(formatting, "try_format", None))
        or not callable(getattr(CellNotificationUtils, "maybe_truncate_output", None))
    ):
        missing.append("rendered-output-capture")
    if not _methods(
        DirectedGraph,
        "get_defining_cells",
        "get_stale",
        "set_stale",
    ):
        missing.append("lazy-source-refresh")

    _probe_attached_context(
        get_context,
        get_runtime_context,
        ContextNotInitializedError,
        missing,
    )

    if missing:
        raise SessionError(
            "the attached marimo environment lacks required capture seams: "
            + ", ".join(sorted(set(missing)))
        )

    return MarimoCapabilities(
        version=str(marimo.__version__),
        names=_CAPABILITY_NAMES,
    )


def _methods(value: object, *names: str) -> bool:
    return all(callable(getattr(value, name, None)) for name in names)


def _blob_asset_codec(
    blob_asset: type,
    serializers: Mapping[str, object],
    deserializers: Mapping[str, object],
) -> bool:
    serializer = cast(Callable[..., bytes] | None, serializers.get("bin"))
    deserializer = cast(
        Callable[..., object] | None,
        deserializers.get(".bin"),
    )
    if not callable(serializer) or not callable(deserializer):
        return False
    try:
        value = blob_asset(
            data=b"capability",
            media_type="application/octet-stream",
            filename="capability.bin",
            metadata={"format_id": "capability.v1", "metadata_json": b"{}"},
        )
        return deserializer(serializer(value), None) == value
    except Exception:
        return False


def _virtual_file_shape(virtual_file: type) -> bool:
    try:
        value = virtual_file(filename="capability.bin", buffer=b"capability")
    except Exception:
        return False
    return (
        value.filename == "capability.bin"
        and value.buffer == b"capability"
        and value.url == "./@file/10-capability.bin"
    )


def _probe_attached_context(
    get_code_context: Callable[[], object],
    get_runtime_context: Callable[[], object],
    context_error: type[Exception],
    missing: list[str],
) -> None:
    try:
        context = get_code_context()
    except (RuntimeError, context_error):
        return

    kernel = getattr(context, "_kernel", None)
    hooks = getattr(kernel, "_hooks", None)
    graph = getattr(kernel, "graph", None)
    if (
        not isinstance(getattr(context, "globals", None), dict)
        or getattr(context, "cells", None) is None
        or not _methods(context, "run_cell", "set_ui_value")
    ):
        missing.append("attached-code-mode")
    if (
        hooks is None
        or not _methods(hooks, "add_post_execution", "copy")
        or getattr(kernel, "reactive_execution_mode", None) not in {"autorun", "lazy"}
    ):
        missing.append("attached-execution-hooks")
    if (
        graph is None
        or not isinstance(getattr(graph, "cells", None), Mapping)
        or not _methods(graph, "get_defining_cells", "get_stale", "set_stale")
    ):
        missing.append("attached-lazy-refresh")

    try:
        runtime = get_runtime_context()
    except Exception:
        missing.append("attached-runtime-context")
        return
    store = getattr(getattr(runtime, "cache", None), "store", None)
    registry = getattr(runtime, "virtual_file_registry", None)
    if store is None or not _methods(store, "get", "hit", "put"):
        missing.append("attached-cache-store")
    if (
        not isinstance(getattr(runtime, "virtual_files_supported", None), bool)
        or registry is None
        or not _methods(registry, "add", "has", "remove")
    ):
        missing.append("attached-virtual-files")


__all__ = ["MarimoCapabilities", "require_capabilities"]
