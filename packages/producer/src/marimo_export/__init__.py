from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field

from marimo_export._json import json_object as _json_object


@_dataclass(frozen=True)
class Projection:
    """Portable bytes produced by a notebook exporter.

    ``format_id`` selects the matching frontend loader. ``media_type``
    describes the payload for generic readers. ``metadata`` is copied into a
    JSON-compatible object and recorded in the export index.
    """

    payload: bytes
    format_id: str = _field(kw_only=True)
    media_type: str = _field(default="application/octet-stream", kw_only=True)
    metadata: _Mapping[str, object] = _field(default_factory=dict, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TypeError("projection payload must be bytes")
        if not isinstance(self.format_id, str) or not self.format_id:
            raise TypeError("projection format_id must be a non-empty string")
        if not isinstance(self.media_type, str) or not self.media_type:
            raise TypeError("projection media_type must be a non-empty string")
        object.__setattr__(self, "metadata", _json_object(self.metadata, "projection.metadata"))


__version__ = "0.0.0"

__all__ = ["Projection", "__version__"]
