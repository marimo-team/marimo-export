from __future__ import annotations

from typing import cast

import msgspec
import pytest
from marimo_export._blob_asset import BlobAssetEnvelope, decode_blob_asset
from marimo_export._marimo.blob import to_native_blob_asset
from marimo_export.outputs import BlobAsset


def _encoded(
    *,
    data: bytes = b'{"answer":42}',
    media_type: str | None = "application/json",
    filename: str | None = "summary.json",
    metadata: dict[str, object] | None = None,
) -> bytes:
    return msgspec.msgpack.encode(
        to_native_blob_asset(
            BlobAsset(
                data=data,
                media_type=media_type,
                filename=filename,
                metadata={"rows": 1} if metadata is None else metadata,
            )
        )
    )


def _raw_encoded(
    *,
    data: object = b'{"answer":42}',
    media_type: object = "application/json",
    filename: object = "summary.json",
    metadata: object = None,
) -> bytes:
    return msgspec.msgpack.encode(
        {
            "data": data,
            "media_type": media_type,
            "filename": filename,
            "metadata": {"rows": 1} if metadata is None else metadata,
        }
    )


def test_decode_blob_asset_matches_the_native_marimo_encoder() -> None:
    assert decode_blob_asset(_encoded()) == BlobAssetEnvelope(
        data=b'{"answer":42}',
        media_type="application/json",
        filename="summary.json",
        metadata={"rows": 1},
    )
    assert decode_blob_asset(_encoded(media_type=None, filename=None)).media_type is None


def test_decode_blob_asset_validates_portable_metadata() -> None:
    metadata: dict[str, object] = {
        "nested": {"rows": [1, 2, 3]},
        "truth": True,
        "missing": None,
        "ratio": 1.5,
    }
    assert decode_blob_asset(_encoded(metadata=metadata)).metadata == metadata

    for invalid in (
        {"binary": b"bytes"},
        {"integer": 2**53},
        {"float": float("inf")},
        {1: "not a string key"},
    ):
        with pytest.raises(ValueError, match="portable JSON"):
            decode_blob_asset(_raw_encoded(metadata=cast(dict[str, object], invalid)))


def test_decode_blob_asset_requires_the_native_envelope_shape() -> None:
    reordered = msgspec.msgpack.encode(
        {
            "media_type": "application/json",
            "data": b"{}",
            "filename": None,
            "metadata": {},
        }
    )
    with pytest.raises(ValueError, match="four-field envelope"):
        decode_blob_asset(reordered)

    encoded = _encoded()
    duplicate = (
        bytes([0x85])
        + encoded[1:]
        + msgspec.msgpack.encode("data")
        + msgspec.msgpack.encode(b"replacement")
    )
    with pytest.raises(ValueError):
        decode_blob_asset(duplicate)


def test_decode_blob_asset_rejects_invalid_public_fields() -> None:
    for media_type, filename in (
        ("", None),
        ("not a media type", None),
        ("application/json", "../escape.json"),
        ("application/json", "CON"),
    ):
        with pytest.raises(ValueError):
            decode_blob_asset(_raw_encoded(media_type=media_type, filename=filename))


def test_decode_blob_asset_enforces_the_envelope_limit() -> None:
    encoded = _encoded(data=b"x" * 100)

    with pytest.raises(ValueError, match="byte limit"):
        decode_blob_asset(encoded, maximum_bytes=len(encoded) - 1)

    assert decode_blob_asset(encoded, maximum_bytes=len(encoded)).data == b"x" * 100
