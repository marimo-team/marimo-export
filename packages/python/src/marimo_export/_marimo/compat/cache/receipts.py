"""Read verified return values from Marimo's native lazy cache."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import msgspec

from marimo_export._json import json_object
from marimo_export._marimo.capabilities import (
    NativeArrowReturn,
    NativeBlobReturn,
    NativeCacheReturn,
    NativeNumpyReturn,
    NativeScalarReturn,
)
from marimo_export._marimo.compat.cache.attempts import NativeCacheAttempt
from marimo_export._marimo.compat.cache.barrier import flush_native_caches
from marimo_export.errors import CodecError, OutputError


class ReadSnapshotStore:
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


def read_cached_return(
    attempt: NativeCacheAttempt,
    *,
    output: str,
    value: object,
    python_type: str,
) -> NativeCacheReturn:
    """Verify and decode one persisted return without mutating its loader."""

    from marimo._save.loaders.lazy import (
        CacheSignatureError,
        _verify_signed_blob,
        from_item,
    )
    from marimo._save.stubs import BlobAsset as NativeBlobAsset
    from marimo._save.stubs.lazy_stub import Cache as CacheSchema

    flush_native_caches()
    store = ReadSnapshotStore(attempt.loader.store)
    encoded = store.get(attempt.manifest_key)
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
            details={"output": output},
        ) from error
    if manifest.hash != attempt.expected_hash:
        raise OutputError(
            f"output {output!r} native cache hash changed",
            code="cache_receipt_invalid",
            details={"output": output},
        )

    mode = attempt.loader._effective_mode()
    try:
        signer = attempt.loader._resolve_effective_signer(manifest, mode)
    except CacheSignatureError as error:
        if mode == "strict":
            raise
        raise OutputError(
            f"output {output!r} has an unverifiable native cache receipt",
            code="cache_receipt_missing",
            details={"output": output},
        ) from error

    returned = manifest.meta.return_value
    if returned is None or returned.reference is None:
        manifest_value = None if returned is None else from_item(returned, "return")
        manifest_type = _python_type(manifest_value)
        if (
            not _is_scalar(manifest_value)
            or not _same_scalar(manifest_value, value)
            or manifest_type != python_type
        ):
            raise OutputError(
                f"output {output!r} live scalar differs from its verified cache receipt",
                code="cache_receipt_invalid",
                details={"output": output},
            )
        return NativeScalarReturn(
            python_type=manifest_type,
            value=manifest_value,
        )

    reference = returned.reference
    payload = store.get(reference)
    if payload is None:
        raise OutputError(
            f"output {output!r} native return asset is missing",
            code="cache_receipt_missing",
            details={"output": output},
        )
    try:
        _verify_signed_blob(reference, payload, manifest.meta.blob_hashes, signer)
    except CacheSignatureError as error:
        if mode == "strict":
            raise
        raise OutputError(
            f"output {output!r} has an unverifiable native return asset",
            code="cache_receipt_missing",
            details={"output": output},
        ) from error

    suffix = Path(reference).suffix
    if suffix == ".npy":
        return NativeNumpyReturn(
            python_type=python_type,
            payload=payload,
        )
    if suffix == ".arrow":
        return NativeArrowReturn(
            python_type=python_type,
            payload=payload,
        )
    if suffix != ".bin":
        raise CodecError(
            f"output {output!r} uses unsupported native cache codec {suffix or '<inline>'!r}",
            code="codec_invalid",
            details={"output": output},
        )
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
    except (TypeError, ValueError) as error:
        raise CodecError(
            f"output {output!r} BlobAsset metadata is not portable",
            code="codec_invalid",
            details={"output": output},
        ) from error
    return NativeBlobReturn(
        python_type=python_type,
        envelope=payload,
        data=blob.data,
        media_type=blob.media_type,
        filename=blob.filename,
        metadata=metadata,
    )


def _python_type(value: object) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (bool, str, int, float))


def _same_scalar(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, float) and isinstance(right, float):
        if math.isnan(left) or math.isnan(right):
            return math.isnan(left) and math.isnan(right)
        if left == 0 and right == 0:
            return math.copysign(1, left) == math.copysign(1, right)
    return left == right


__all__ = ["ReadSnapshotStore", "read_cached_return"]
