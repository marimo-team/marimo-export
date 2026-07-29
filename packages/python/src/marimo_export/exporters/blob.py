from __future__ import annotations

from collections.abc import Mapping

from marimo_export._json import JsonValue
from marimo_export.exporters._spec import ExporterSpec, builtin


def json(
    *,
    media_type: str = "application/json",
    filename: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ExporterSpec:
    """Select canonical JSON in a BlobAsset."""

    return builtin(
        "blob.json",
        {
            "media_type": media_type,
            "filename": filename,
            "metadata": {} if metadata is None else dict(metadata),
        },
    )


def text(
    *,
    media_type: str = "text/plain; charset=utf-8",
    filename: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ExporterSpec:
    """Select UTF-8 text in a BlobAsset."""

    return builtin(
        "blob.text",
        {
            "media_type": media_type,
            "filename": filename,
            "metadata": {} if metadata is None else dict(metadata),
        },
    )


def html(
    *,
    filename: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ExporterSpec:
    """Select UTF-8 HTML in a BlobAsset."""

    return builtin(
        "blob.html",
        {
            "filename": filename,
            "metadata": {} if metadata is None else dict(metadata),
        },
    )


__all__ = ["html", "json", "text"]
