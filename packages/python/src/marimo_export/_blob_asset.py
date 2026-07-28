from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import msgspec

from marimo_export._format import MAX_FORMAT_METADATA_JSON_BYTES, validate_media_type
from marimo_export._json import JsonObject, canonical_bytes, portable_json_object
from marimo_export._portable import validate_portable_basename

_FIELDS = ("data", "media_type", "filename", "metadata")


@dataclass(frozen=True, slots=True)
class BlobAssetEnvelope:
    """The verified native marimo BlobAsset MessagePack envelope."""

    data: bytes
    media_type: str | None
    filename: str | None
    metadata: JsonObject


def decode_blob_asset(
    data: bytes,
    *,
    maximum_bytes: int | None = None,
) -> BlobAssetEnvelope:
    """Strictly decode the exact MessagePack shape emitted by marimo."""

    if not isinstance(data, bytes):
        raise TypeError("BlobAsset MessagePack must be bytes")
    if maximum_bytes is not None and (
        not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool) or maximum_bytes <= 0
    ):
        raise TypeError("maximum_bytes must be a positive integer")
    if maximum_bytes is not None and len(data) > maximum_bytes:
        raise ValueError(f"BlobAsset MessagePack exceeds the {maximum_bytes} byte limit")

    try:
        decoded = msgspec.msgpack.decode(data)
    except msgspec.DecodeError as error:
        raise ValueError("BlobAsset is invalid MessagePack") from error
    if not isinstance(decoded, dict) or tuple(decoded) != _FIELDS:
        raise ValueError("BlobAsset does not use the native four-field envelope")
    if msgspec.msgpack.encode(decoded) != data:
        raise ValueError("BlobAsset MessagePack is not canonical")

    payload = decoded["data"]
    media_type = decoded["media_type"]
    filename = decoded["filename"]
    metadata = decoded["metadata"]
    if not isinstance(payload, bytes):
        raise ValueError("BlobAsset data must be bytes")
    if media_type is not None:
        try:
            media_type = validate_media_type(media_type, "BlobAsset media_type")
        except (TypeError, ValueError) as error:
            raise ValueError("BlobAsset media_type is invalid") from error
    if filename is not None:
        try:
            filename = validate_portable_basename(filename, "BlobAsset filename")
        except (TypeError, ValueError) as error:
            raise ValueError("BlobAsset filename is invalid") from error
    try:
        metadata_object = portable_json_object(metadata, "BlobAsset metadata")
    except (TypeError, ValueError) as error:
        raise ValueError("BlobAsset metadata must be portable JSON") from error
    if len(canonical_bytes(metadata_object)) > MAX_FORMAT_METADATA_JSON_BYTES:
        raise ValueError(
            f"BlobAsset metadata exceeds {MAX_FORMAT_METADATA_JSON_BYTES} canonical JSON bytes"
        )

    return BlobAssetEnvelope(
        data=payload,
        media_type=cast(str | None, media_type),
        filename=cast(str | None, filename),
        metadata=metadata_object,
    )


__all__ = ["BlobAssetEnvelope", "decode_blob_asset"]
