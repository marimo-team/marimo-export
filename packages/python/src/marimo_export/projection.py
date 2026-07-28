from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from marimo_export._format import (
    MAX_FORMAT_METADATA_JSON_BYTES,
    validate_format_id,
    validate_media_type,
)
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    json_object,
)
from marimo_export._portable import validate_portable_basename


@dataclass(frozen=True, slots=True, init=False)
class Projection:
    """Portable bytes produced by an exporter inside the notebook kernel."""

    data: bytes
    format_id: str
    media_type: str
    filename: str | None
    _metadata_bytes: bytes = field(repr=False)

    def __init__(
        self,
        data: bytes,
        *,
        format_id: str,
        media_type: str,
        filename: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> None:
        if not isinstance(data, bytes):
            raise TypeError("projection.data must be bytes")
        format_id = validate_format_id(format_id, "projection.format_id")
        media_type = validate_media_type(media_type, "projection.media_type")
        _filename(filename)
        parsed_metadata = json_object(
            {} if metadata is None else metadata,
            "projection.metadata",
        )
        metadata_bytes = canonical_bytes(parsed_metadata)
        if len(metadata_bytes) > MAX_FORMAT_METADATA_JSON_BYTES:
            raise ValueError(
                "projection.metadata canonical JSON must contain at most "
                f"{MAX_FORMAT_METADATA_JSON_BYTES} bytes"
            )

        object.__setattr__(self, "data", data)
        object.__setattr__(self, "format_id", format_id)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "_metadata_bytes", metadata_bytes)

    @property
    def metadata(self) -> JsonObject:
        """Return a detached copy of the projection metadata."""

        return decode_json_object(self._metadata_bytes, "projection.metadata")


def _filename(value: object) -> str | None:
    if value is None:
        return None
    return validate_portable_basename(value, "projection.filename")
