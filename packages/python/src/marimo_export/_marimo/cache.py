from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast

import marimo
import msgspec
from marimo._runtime.context import get_context
from marimo._save.cache import CACHE_PREFIX
from marimo._save.hash import HashKey
from marimo._save.loaders.lazy import LazyLoader, LazyStore
from marimo._save.stubs.lazy_stub import BlobAsset
from marimo._save.stubs.lazy_stub import Cache as CacheSchema

from marimo_export._json import (
    JsonObject,
    canonical_bytes,
    decode_json_object,
    json_object,
)
from marimo_export.errors import IntegrityError
from marimo_export.exporters._registry import _ResolvedExporter
from marimo_export.projection import Projection

_R = TypeVar("_R")
_CacheCall = Callable[..., Awaitable[_R]]
_PROJECTION_ABI = "marimo-export.projection.v1"


@dataclass(frozen=True, slots=True)
class CacheAssetRef:
    """Exact marimo cache object referenced by a publication."""

    key: str
    sha256: str
    size: int

    def wire(self) -> JsonObject:
        return {"key": self.key, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True, slots=True)
class CacheAssetReceipt:
    """Verified result of one cache-backed projection."""

    asset: CacheAssetRef
    envelope: bytes
    blob: BlobAsset
    disposition: Literal["hit", "miss", "skipped"]


async def project_and_cache(
    value: object,
    resolved_exporter: _ResolvedExporter,
    options: Mapping[str, object],
) -> CacheAssetReceipt:
    """Project a live notebook value and persist its ``BlobAsset`` envelope."""

    projector = _persistent_cache(_project_value)
    try:
        return await _invoke_cached(
            projector,
            _PROJECTION_ABI,
            value,
            resolved_exporter.function,
            resolved_exporter.reference,
            resolved_exporter.version,
            canonical_bytes(json_object(options, "projection options")),
        )
    except (AssertionError, TypeError) as error:
        if projector.last_hash is not None or not _is_unhashable_source(error):
            raise

    # An exporter may accept a value that marimo cannot use as cache input.
    # Persist the completed projection through a content-hashable bytes input
    # so publication storage still uses the native lazy-cache object layout.
    projection = await resolved_exporter.project(value, options)
    blob = _to_blob_asset(projection)
    encoded = msgspec.msgpack.encode(blob)
    persister = _persistent_cache(_persist_blob_asset)
    receipt = await _invoke_cached(persister, _PROJECTION_ABI, encoded)
    return CacheAssetReceipt(
        asset=receipt.asset,
        envelope=receipt.envelope,
        blob=receipt.blob,
        disposition="skipped",
    )


async def _project_value(
    projection_abi: str,
    value: object,
    exporter_function: Callable[..., object],
    exporter_reference: str,
    exporter_version: str | None,
    options_json: bytes,
) -> BlobAsset:
    _require_projection_abi(projection_abi)
    resolved_exporter = _ResolvedExporter(
        function=cast(Any, exporter_function),
        reference=exporter_reference,
        version=exporter_version,
    )
    projection = await resolved_exporter.project(
        value,
        decode_json_object(options_json, "projection options"),
    )
    return _to_blob_asset(projection)


async def _persist_blob_asset(
    projection_abi: str,
    envelope: bytes,
) -> BlobAsset:
    _require_projection_abi(projection_abi)
    return msgspec.msgpack.decode(envelope, type=BlobAsset)


def _require_projection_abi(value: str) -> None:
    if value != _PROJECTION_ABI:
        raise IntegrityError("marimo projection cache received an unsupported ABI")


def _to_blob_asset(projection: Projection) -> BlobAsset:
    return BlobAsset(
        data=projection.data,
        media_type=projection.media_type,
        filename=projection.filename,
        metadata={
            "format_id": projection.format_id,
            "metadata_json": canonical_bytes(projection.metadata),
        },
    )


def _persistent_cache(function: Callable[..., Awaitable[_R]]) -> Any:
    store = LazyStore(get_context().cache.store)
    # The positional callable is marimo's supported function form. The
    # ``fn=`` parameter is reserved and rejected by the current implementation.
    persistent_cache = cast(Any, marimo.persistent_cache)
    return persistent_cache(
        function,
        method="lazy",
        pin_modules=True,
        store=store,
    )


async def _invoke_cached(cache: Any, *args: object) -> CacheAssetReceipt:
    hits_before = int(cache.hits)
    blob = await cast(_CacheCall[BlobAsset], cache)(*args)
    if not isinstance(blob, BlobAsset):
        raise IntegrityError("marimo projection cache returned an invalid asset")

    loader = cache.loader
    if not isinstance(loader, LazyLoader):
        raise IntegrityError("marimo projection cache did not use the lazy loader")
    loader.flush()

    last_hash = cache.last_hash
    if not isinstance(last_hash, str) or not last_hash:
        raise IntegrityError("marimo projection cache did not expose its cache hash")

    key = f"{loader.name}/{last_hash}/return.bin"
    manifests = _matching_manifests(loader, last_hash, key)
    if not manifests:
        raise IntegrityError("marimo projection cache manifest is missing or invalid")
    if len(manifests) != 1:
        raise IntegrityError("marimo projection cache manifest is ambiguous")

    envelope = loader.store.get(key)
    if envelope is None:
        raise IntegrityError(f"marimo projection cache asset is missing: {key}")
    digest = hashlib.sha256(envelope).hexdigest()
    for manifest in manifests:
        expected_digest = manifest.meta.blob_hashes.get(key)
        if expected_digest is not None and expected_digest != digest:
            raise IntegrityError(f"marimo projection cache asset failed integrity: {key}")

    try:
        persisted = msgspec.msgpack.decode(envelope, type=BlobAsset)
    except msgspec.DecodeError as error:
        raise IntegrityError(
            f"marimo projection cache asset has an invalid BlobAsset envelope: {key}"
        ) from error
    if persisted != blob:
        raise IntegrityError(
            f"marimo projection cache restored value differs from its envelope: {key}"
        )

    return CacheAssetReceipt(
        asset=CacheAssetRef(key=key, sha256=digest, size=len(envelope)),
        envelope=envelope,
        blob=persisted,
        disposition="hit" if int(cache.hits) > hits_before else "miss",
    )


def _matching_manifests(
    loader: LazyLoader,
    last_hash: str,
    expected_reference: str,
) -> list[CacheSchema]:
    matches: list[CacheSchema] = []
    for cache_type in CACHE_PREFIX:
        manifest_key = str(loader.build_path(HashKey(hash=last_hash, cache_type=cache_type)))
        encoded = loader.store.get(manifest_key)
        if encoded is None:
            continue
        try:
            manifest = msgspec.json.decode(encoded, type=CacheSchema)
        except msgspec.DecodeError:
            continue
        returned = manifest.meta.return_value
        if (
            manifest.hash == last_hash
            and manifest.cache_type.value == cache_type
            and returned is not None
            and returned.reference == expected_reference
        ):
            matches.append(manifest)
    return matches


def _is_unhashable_source(error: AssertionError | TypeError) -> bool:
    message = str(error)
    return (
        "Content addressed hash could not be utilized" in message
        or "Content addressed hash could not be resolved" in message
    )


__all__ = ["CacheAssetReceipt", "CacheAssetRef", "project_and_cache"]
