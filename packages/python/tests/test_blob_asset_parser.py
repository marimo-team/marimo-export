from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast

import msgspec
import pytest
from marimo._save.stubs import BlobAsset
from marimo_export._blob_asset import BlobAssetEnvelope, decode_blob_asset


def _encoded(
    *,
    data: bytes = b'{"answer":42}',
    media_type: str | None = "application/json",
    filename: str | None = "summary.json",
    metadata: dict[str, object] | None = None,
) -> bytes:
    return msgspec.msgpack.encode(
        BlobAsset(
            data=data,
            media_type=media_type,
            filename=filename,
            metadata={"rows": 1} if metadata is None else metadata,
        )
    )


def test_decode_blob_asset_matches_the_native_marimo_encoder() -> None:
    decoded = decode_blob_asset(_encoded())

    assert decoded == BlobAssetEnvelope(
        data=b'{"answer":42}',
        media_type="application/json",
        filename="summary.json",
        metadata={"rows": 1},
    )
    with pytest.raises(FrozenInstanceError):
        cast(Any, decoded).media_type = "text/plain"


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"nested": {"rows": [1, 2, 3]}},
        {"truth": True, "missing": None, "ratio": 1.5},
    ],
)
def test_decode_blob_asset_accepts_portable_metadata(
    metadata: dict[str, object],
) -> None:
    assert decode_blob_asset(_encoded(metadata=metadata)).metadata == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"binary": b"bytes"},
        {"integer": 2**53},
        {"float": float("inf")},
        {1: "not a string key"},
    ],
)
def test_decode_blob_asset_rejects_nonportable_metadata(
    metadata: dict[Any, object],
) -> None:
    with pytest.raises(ValueError, match="portable JSON"):
        decode_blob_asset(_encoded(metadata=cast(dict[str, object], metadata)))


def test_decode_blob_asset_requires_the_native_field_order() -> None:
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


def test_decode_blob_asset_rejects_duplicate_fields() -> None:
    encoded = _encoded()
    duplicate = (
        bytes([0x85])
        + encoded[1:]
        + msgspec.msgpack.encode("data")
        + msgspec.msgpack.encode(b"replacement")
    )

    with pytest.raises(ValueError):
        decode_blob_asset(duplicate)


@pytest.mark.parametrize(
    ("media_type", "filename"),
    [
        (None, None),
        ("", None),
        ("not a media type", None),
        ("application/json", "../escape.json"),
        ("application/json", "CON"),
    ],
)
def test_decode_blob_asset_rejects_invalid_public_fields(
    media_type: str | None,
    filename: str | None,
) -> None:
    if media_type is None and filename is None:
        assert decode_blob_asset(_encoded(media_type=None, filename=None)).media_type is None
        return
    with pytest.raises(ValueError):
        decode_blob_asset(_encoded(media_type=media_type, filename=filename))


def test_decode_blob_asset_enforces_the_envelope_limit() -> None:
    encoded = _encoded(data=b"x" * 100)

    with pytest.raises(ValueError, match="byte limit"):
        decode_blob_asset(encoded, maximum_bytes=len(encoded) - 1)

    assert decode_blob_asset(encoded, maximum_bytes=len(encoded)).data == b"x" * 100
