"""Translate package-owned blob assets at the native Marimo boundary."""

from __future__ import annotations

from marimo_export._json import canonical_bytes, json_value, portable_json_object
from marimo_export.descriptors import JSON_CODEC, JSON_MEDIA_TYPE
from marimo_export.outputs import BlobAsset


def to_native_blob_asset(value: object) -> object:
    """Return the native value required by Marimo's lazy ``.bin`` codec.

    An exporter returns a ``BlobAsset`` or a JSON value. A JSON value uses the
    canonical JSON representation of a JSON source.
    """

    from marimo._save.stubs import BlobAsset as NativeBlobAsset

    if not isinstance(value, BlobAsset):
        try:
            portable = json_value(value, "exporter result")
        except (TypeError, ValueError) as error:
            raise TypeError(
                "output exporter must return marimo_export.outputs.BlobAsset or a JSON value"
            ) from error
        return NativeBlobAsset(
            data=canonical_bytes(portable),
            media_type=JSON_MEDIA_TYPE,
            metadata={"schema": JSON_CODEC},
        )
    return NativeBlobAsset(
        data=value.data,
        media_type=value.media_type,
        filename=value.filename,
        metadata=portable_json_object(value.metadata, "BlobAsset metadata"),
    )


__all__ = ["to_native_blob_asset"]
