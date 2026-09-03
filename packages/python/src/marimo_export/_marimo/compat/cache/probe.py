"""Validate the pinned private Marimo cache contract."""

from __future__ import annotations

import hashlib
import inspect
import json
from importlib.metadata import version
from importlib.resources import files
from typing import Any

import msgspec

from marimo_export.errors import CompatibilityError

_RELEASE = json.loads(
    files("marimo_export._marimo.compat").joinpath("release.json").read_text(encoding="utf-8")
)
MARIMO_VERSION: str = _RELEASE["version"]
MARIMO_RELEASE_COMMIT: str = _RELEASE["commit"]
_SOURCE_SHA256: dict[str, str] = _RELEASE["cache_source_sha256"]
_POLARS_LOADERS: dict[str, str] = _RELEASE["polars_lazy_stub_loaders"]
_RECEIPT_FIELDS: dict[str, list[str]] = _RELEASE["receipt_schema_fields"]


def require_cache_capabilities() -> None:
    """Reject Marimo release or private cache source drift before patching."""

    observed_version = version("marimo")
    if observed_version != MARIMO_VERSION:
        raise CompatibilityError(
            f"marimo-export requires Marimo {MARIMO_VERSION}, observed {observed_version}",
            code="marimo_incompatible",
            details={"expected": MARIMO_VERSION, "observed": observed_version},
        )

    try:
        import cryptography
    except ImportError as error:
        raise CompatibilityError(
            "marimo-export cached execution requires cryptography",
            code="marimo_incompatible",
        ) from error
    if not getattr(cryptography, "__version__", None):
        raise CompatibilityError(
            "marimo-export cached execution requires cryptography",
            code="marimo_incompatible",
        )

    try:
        import marimo._runtime.context as context_module
        import marimo._save.loaders as loaders_module
        import marimo._save.loaders.lazy as lazy_module
        import marimo._save.stubs as stubs_module
        import marimo._save.stubs.lazy_stub as schema_module
    except ImportError as error:
        _raise_drift(["cache_modules"], cause=error)

    from marimo_export._marimo.compat.cache.host import native_host_cache_contract
    from marimo_export._marimo.compat.cache.patch import native_cache_contract

    loader_entry, lifecycle, cache_attempt = native_cache_contract()
    restored_ui_defs, tensor_buffer, polars_loaders = native_host_cache_contract()
    lazy_loader = getattr(lazy_module, "LazyLoader", None)
    dual_loader = getattr(loaders_module, "DualLoader", None)
    flush_active_caches = getattr(lazy_module, "flush_active_caches", None)
    cache_signature_error = getattr(lazy_module, "CacheSignatureError", None)
    cache_schema = getattr(schema_module, "Cache", None)
    meta_schema = getattr(schema_module, "Meta", None)
    item_schema = getattr(schema_module, "Item", None)
    blob_schema = getattr(stubs_module, "BlobAsset", None)

    symbols = {
        "CachedLifecycle.__init__": getattr(lifecycle, "__init__", None),
        "CachedLifecycle._restored_ui_defs": restored_ui_defs,
        "CachedLifecycle.setup": getattr(lifecycle, "setup", None),
        "BlobAsset.schema_source": blob_schema,
        "Cache.schema_source": cache_schema,
        "CacheSignatureError": cache_signature_error,
        "Item.schema_source": item_schema,
        "LazyLoader._effective_mode": getattr(lazy_loader, "_effective_mode", None),
        "LazyLoader._read_blobs": getattr(lazy_loader, "_read_blobs", None),
        "LazyLoader._resolve_effective_signer": getattr(
            lazy_loader, "_resolve_effective_signer", None
        ),
        "Meta.schema_source": meta_schema,
        "_verify_signed_blob": getattr(lazy_module, "_verify_signed_blob", None),
        "cache_attempt_from_hash": cache_attempt,
        "encode._contiguous_tensor_bytes": tensor_buffer,
        "from_item": getattr(lazy_module, "from_item", None),
        "flush_active_caches": flush_active_caches,
    }
    drift = [
        name
        for name, symbol in symbols.items()
        if _SOURCE_SHA256.get(name) != _source_digest(symbol)
    ]
    if (
        not isinstance(dual_loader, type)
        or not isinstance(loader_entry, dual_loader)
        or getattr(loader_entry, "native", None) is not lazy_loader
    ):
        drift.append("PERSISTENT_LOADERS.lazy")
    if polars_loaders != _POLARS_LOADERS:
        drift.append("LAZY_STUB_LOOKUP.polars")
    if not isinstance(cache_signature_error, type):
        drift.append("CacheSignatureError")
    for name, schema in (
        ("BlobAsset", blob_schema),
        ("Cache", cache_schema),
        ("Item", item_schema),
        ("Meta", meta_schema),
    ):
        if _schema_fields(schema) != tuple(_RECEIPT_FIELDS[name]):
            drift.append(f"{name}.schema")
    try:
        get_context = getattr(context_module, "get_context", None)
        runtime = get_context() if callable(get_context) else None
    except Exception:
        runtime = None
    if not callable(getattr(context_module, "get_context", None)):
        drift.append("get_context")
    if runtime is not None:
        store = getattr(getattr(runtime, "cache", None), "store", None)
        if store is None or not all(
            callable(getattr(store, name, None)) for name in ("get", "hit", "put")
        ):
            drift.append("RuntimeContext.cache.store")
    if drift:
        _raise_drift(drift)


def _source_digest(symbol: Any) -> str | None:
    try:
        source = inspect.getsource(symbol)
    except (OSError, TypeError):
        return None
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _schema_fields(schema: Any) -> tuple[str, ...]:
    try:
        return tuple(field.name for field in msgspec.structs.fields(schema))
    except (TypeError, ValueError):
        return ()


def _raise_drift(symbols: list[str], *, cause: BaseException | None = None) -> None:
    error = CompatibilityError(
        "the installed Marimo cache implementation differs from the supported release",
        code="marimo_incompatible",
        details={"capability": "cached_state_execution", "symbols": sorted(set(symbols))},
    )
    if cause is not None:
        raise error from cause
    raise error


__all__ = ["MARIMO_RELEASE_COMMIT", "MARIMO_VERSION", "require_cache_capabilities"]
