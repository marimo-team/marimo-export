from __future__ import annotations

from collections.abc import Mapping

from marimo_export._json import JsonValue, canonical_bytes, json_string, json_value
from marimo_export._marimo.compat import BlobAsset


def json(
    value: object,
    *,
    media_type: str = "application/json",
    filename: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> BlobAsset:
    """Encode one public JSON value into a native BlobAsset."""

    payload = canonical_bytes(json_value(value, "JSON BlobAsset value"))
    return BlobAsset(
        data=payload,
        media_type=media_type,
        filename=filename,
        metadata={} if metadata is None else dict(metadata),
    )


def text(
    value: str,
    *,
    media_type: str = "text/plain; charset=utf-8",
    filename: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> BlobAsset:
    """Encode exact UTF-8 text into a native BlobAsset."""

    content = json_string(value, "text BlobAsset value")
    return BlobAsset(
        data=content.encode("utf-8"),
        media_type=media_type,
        filename=filename,
        metadata={} if metadata is None else dict(metadata),
    )


def html(
    value: str,
    *,
    filename: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> BlobAsset:
    """Encode exact UTF-8 HTML into a native BlobAsset."""

    return text(
        value,
        media_type="text/html; charset=utf-8",
        filename=filename,
        metadata=metadata,
    )


__all__ = ["html", "json", "text"]
