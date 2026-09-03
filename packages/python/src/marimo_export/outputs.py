"""Public output values returned by Python exporters and readers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from marimo_export._json import (
    canonical_bytes,
    decode_json_object,
    portable_json_object,
)
from marimo_export._media_type import MAX_BLOB_METADATA_JSON_BYTES, validate_media_type
from marimo_export._portable import validate_portable_basename
from marimo_export.wire import FrozenJsonObject, _freeze_json


@dataclass(frozen=True, slots=True, init=False)
class BlobAsset:
    """Portable bytes and metadata returned by a Python output exporter."""

    data: bytes
    media_type: str | None
    filename: str | None
    _metadata_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        data: bytes,
        media_type: str | None = None,
        filename: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if not isinstance(data, bytes):
            raise TypeError("BlobAsset data must be bytes")
        if media_type is not None:
            media_type = validate_media_type(media_type, "BlobAsset media_type")
        if filename is not None:
            filename = validate_portable_basename(filename, "BlobAsset filename")
        parsed_metadata = portable_json_object(
            {} if metadata is None else metadata,
            "BlobAsset metadata",
        )
        metadata_bytes = canonical_bytes(parsed_metadata)
        if len(metadata_bytes) > MAX_BLOB_METADATA_JSON_BYTES:
            raise ValueError(
                f"BlobAsset metadata exceeds {MAX_BLOB_METADATA_JSON_BYTES} canonical JSON bytes"
            )
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "_metadata_bytes", metadata_bytes)

    @property
    def metadata(self) -> FrozenJsonObject:
        """Return recursively immutable portable metadata."""

        value = decode_json_object(self._metadata_bytes, "BlobAsset metadata")
        return cast(FrozenJsonObject, _freeze_json(value))


__all__ = ["BlobAsset"]
