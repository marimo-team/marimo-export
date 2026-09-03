"""Serialize prepared manifests within the browser byte bound."""

from __future__ import annotations

from typing import Final

from marimo_export.errors import MarimoExportError
from marimo_export.wire import canonical_json_bytes

MAX_PREPARED_MANIFEST_BYTES: Final = 256 * 1024


class PreparedManifestLimitError(MarimoExportError):
    """A canonical prepared manifest exceeds the browser byte bound."""

    code = "prepared_manifest_limit_exceeded"


def prepared_manifest_bytes(value: object) -> bytes:
    """Return canonical portable JSON within the prepared manifest byte bound."""

    encoded = canonical_json_bytes(value, "prepared manifest")
    size = len(encoded)
    if size > MAX_PREPARED_MANIFEST_BYTES:
        raise PreparedManifestLimitError(
            f"The prepared manifest exceeds the {MAX_PREPARED_MANIFEST_BYTES}-byte limit.",
            details={
                "max_bytes": MAX_PREPARED_MANIFEST_BYTES,
                "size_bytes": size,
            },
        )
    return encoded


__all__ = [
    "MAX_PREPARED_MANIFEST_BYTES",
    "PreparedManifestLimitError",
    "prepared_manifest_bytes",
]
