"""Translate package-owned blob assets at the native Marimo boundary."""

from __future__ import annotations

from marimo_export._json import portable_json_object
from marimo_export.outputs import BlobAsset


def to_native_blob_asset(value: BlobAsset) -> object:
    """Return the native value required by Marimo's lazy ``.bin`` codec."""

    if not isinstance(value, BlobAsset):
        raise TypeError("output exporter must return marimo_export.outputs.BlobAsset")
    from marimo._save.stubs import BlobAsset as NativeBlobAsset

    return NativeBlobAsset(
        data=value.data,
        media_type=value.media_type,
        filename=value.filename,
        metadata=portable_json_object(value.metadata, "BlobAsset metadata"),
    )


__all__ = ["to_native_blob_asset"]
