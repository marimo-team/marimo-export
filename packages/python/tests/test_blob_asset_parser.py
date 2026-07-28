from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast

import msgspec
import pytest
from marimo._save.stubs import BlobAsset
from marimo_export._blob_asset import BlobAssetWire, decode_blob_asset_wire
from marimo_export._format import (
    MAX_FORMAT_ID_ASCII_BYTES,
    MAX_FORMAT_METADATA_JSON_BYTES,
    MAX_MEDIA_TYPE_ASCII_BYTES,
)
from marimo_export._portable import MAX_PORTABLE_BASENAME_UTF8_BYTES


def _blob_asset(
    *,
    data: bytes = b'{"answer":42}',
    media_type: str = "application/json",
    filename: str | None = "summary.json",
    format_id: str = "json.v1",
    metadata_json: bytes = b'{"rows":1}',
) -> bytes:
    return msgspec.msgpack.encode(
        BlobAsset(
            data=data,
            media_type=media_type,
            filename=filename,
            metadata={
                "format_id": format_id,
                "metadata_json": metadata_json,
            },
        )
    )


def _wire(
    *,
    data: bytes | None = None,
    media_type: bytes | None = None,
    filename: bytes | None = None,
    metadata: bytes | None = None,
) -> bytes:
    return b"".join(
        (
            b"\x84\xa4data",
            msgspec.msgpack.encode(b"payload") if data is None else data,
            b"\xaamedia_type",
            msgspec.msgpack.encode("text/plain") if media_type is None else media_type,
            b"\xa8filename",
            msgspec.msgpack.encode(None) if filename is None else filename,
            b"\xa8metadata",
            (
                b"\x82\xa9format_id"
                + msgspec.msgpack.encode("text.v1")
                + b"\xadmetadata_json"
                + msgspec.msgpack.encode(b"{}")
            )
            if metadata is None
            else metadata,
        )
    )


def test_decode_blob_asset_wire_matches_marimo_encoder() -> None:
    encoded = _blob_asset()

    decoded = decode_blob_asset_wire(encoded)

    assert decoded == BlobAssetWire(
        data=memoryview(b'{"answer":42}'),
        media_type="application/json",
        filename="summary.json",
        format_id="json.v1",
        metadata_json=memoryview(b'{"rows":1}'),
    )
    with pytest.raises(FrozenInstanceError):
        cast(Any, decoded).media_type = "text/plain"


def test_decode_blob_asset_wire_returns_read_only_views_over_the_envelope() -> None:
    encoded = _blob_asset()

    decoded = decode_blob_asset_wire(encoded)

    assert decoded.data.readonly
    assert decoded.metadata_json.readonly
    assert decoded.data.obj is encoded
    assert decoded.metadata_json.obj is encoded
    assert decoded.data.tobytes() == b'{"answer":42}'
    assert decoded.metadata_json.tobytes() == b'{"rows":1}'


@pytest.mark.parametrize(
    "filename",
    [
        None,
        "",
        "summary.json",
        "öffnen.txt",
        "a" * MAX_PORTABLE_BASENAME_UTF8_BYTES,
        f"{'é' * 127}a",
    ],
)
def test_decode_blob_asset_wire_accepts_nullable_canonical_strings(
    filename: str | None,
) -> None:
    encoded = _blob_asset(filename=filename)

    assert decode_blob_asset_wire(encoded).filename == filename


@pytest.mark.parametrize("length", [0, 1, 255, 256, 65_535, 65_536])
def test_decode_blob_asset_wire_accepts_canonical_binary_lengths(length: int) -> None:
    data = b"x" * length

    assert decode_blob_asset_wire(_blob_asset(data=data)).data == data


@pytest.mark.parametrize(
    "length",
    [0, 1, 31, 32, 255, 256, MAX_MEDIA_TYPE_ASCII_BYTES],
)
def test_decode_blob_asset_wire_accepts_canonical_string_lengths(length: int) -> None:
    media_type = "x" * length

    assert decode_blob_asset_wire(_blob_asset(media_type=media_type)).media_type == media_type


@pytest.mark.parametrize(
    ("field", "wire", "maximum_bytes"),
    [
        (
            "media_type",
            {"media_type": b"\xdb\x04\x00\x00\x00"},
            MAX_MEDIA_TYPE_ASCII_BYTES,
        ),
        (
            "filename",
            {"filename": b"\xdb\x04\x00\x00\x00"},
            MAX_PORTABLE_BASENAME_UTF8_BYTES,
        ),
        (
            "format_id",
            {"metadata": (b"\x82\xa9format_id\xdb\x04\x00\x00\x00\xadmetadata_json\xc4\x02{}")},
            MAX_FORMAT_ID_ASCII_BYTES,
        ),
    ],
)
def test_decode_blob_asset_wire_rejects_oversized_strings_before_payload_decode(
    field: str,
    wire: dict[str, bytes],
    maximum_bytes: int,
) -> None:
    encoded = _wire(**wire)

    with pytest.raises(
        ValueError,
        match=rf"BlobAsset {field} exceeds the {maximum_bytes} byte limit",
    ):
        decode_blob_asset_wire(encoded)


def test_decode_blob_asset_wire_rejects_multibyte_filename_over_255_bytes() -> None:
    with pytest.raises(ValueError, match="BlobAsset filename exceeds the 255 byte limit"):
        decode_blob_asset_wire(_blob_asset(filename="é" * 128))


def test_decode_blob_asset_wire_rejects_oversized_metadata_before_slicing() -> None:
    metadata = b"\x82\xa9format_id\xa7text.v1\xadmetadata_json\xc6" + (
        MAX_FORMAT_METADATA_JSON_BYTES + 1
    ).to_bytes(4, "big")

    with pytest.raises(
        ValueError,
        match=(
            rf"BlobAsset metadata_json exceeds the "
            rf"{MAX_FORMAT_METADATA_JSON_BYTES} byte limit"
        ),
    ):
        decode_blob_asset_wire(_wire(metadata=metadata))


@pytest.mark.parametrize(
    "encoded",
    [
        _wire(data=b"\xc5\x00\x01x"),
        _wire(data=b"\xc6\x00\x00\x00\x01x"),
        _wire(media_type=b"\xd9\x01x"),
        _wire(media_type=b"\xda\x00\x01x"),
        _wire(media_type=b"\xdb\x00\x00\x00\x01x"),
    ],
    ids=["bin16", "bin32", "str8", "str16", "str32"],
)
def test_decode_blob_asset_wire_rejects_nonminimal_length_prefixes(encoded: bytes) -> None:
    with pytest.raises(ValueError, match="minimal MessagePack length prefixes"):
        decode_blob_asset_wire(encoded)


@pytest.mark.parametrize(
    "encoded",
    [
        b"\xde\x00\x04" + _blob_asset()[1:],
        _blob_asset()[0:1] + b"\xd9\x04data" + _blob_asset()[6:],
        msgspec.msgpack.encode(
            {
                "metadata": {"format_id": "text.v1", "metadata_json": b"{}"},
                "filename": None,
                "media_type": "text/plain",
                "data": b"payload",
            }
        ),
        msgspec.msgpack.encode(
            {
                "data": b"payload",
                "media_type": "text/plain",
                "filename": None,
                "metadata": {"metadata_json": b"{}", "format_id": "text.v1"},
            }
        ),
        msgspec.msgpack.encode(
            {
                "data": b"payload",
                "media_type": "text/plain",
                "filename": None,
                "unexpected": {},
            }
        ),
        b"".join(
            (
                b"\x84\xa4data",
                msgspec.msgpack.encode(b"payload"),
                b"\xaamedia_type",
                msgspec.msgpack.encode("text/plain"),
                b"\xa8filename\xc0\xa4data",
                msgspec.msgpack.encode(b"other"),
            )
        ),
        _wire(
            metadata=(
                b"\x82\xa9format_id"
                + msgspec.msgpack.encode("text.v1")
                + b"\xa9format_id"
                + msgspec.msgpack.encode("other.v1")
            )
        ),
        _wire(
            metadata=(
                b"\x82\xa9format_id"
                + msgspec.msgpack.encode("text.v1")
                + b"\xa7unknown"
                + msgspec.msgpack.encode(b"{}")
            )
        ),
        _wire() + b"\xc0",
    ],
    ids=[
        "nonminimal-map",
        "nonminimal-key",
        "reordered-envelope",
        "reordered-metadata",
        "unknown-envelope-key",
        "duplicate-envelope-key",
        "duplicate-metadata-key",
        "unknown-metadata-key",
        "trailing-data",
    ],
)
def test_decode_blob_asset_wire_requires_the_exact_map_shape(encoded: bytes) -> None:
    with pytest.raises(ValueError):
        decode_blob_asset_wire(encoded)


def test_decode_blob_asset_wire_rejects_every_truncated_prefix() -> None:
    encoded = _blob_asset()

    for length in range(len(encoded)):
        with pytest.raises(ValueError):
            decode_blob_asset_wire(encoded[:length])


def test_decode_blob_asset_wire_rejects_declared_payload_beyond_the_input() -> None:
    encoded = _wire(data=b"\xc6\xff\xff\xff\xff")

    with pytest.raises(ValueError, match="truncated"):
        decode_blob_asset_wire(encoded)


def test_decode_blob_asset_wire_rejects_invalid_utf8() -> None:
    encoded = _wire(media_type=b"\xa1\xff")

    with pytest.raises(UnicodeDecodeError):
        decode_blob_asset_wire(encoded)


def test_decode_blob_asset_wire_applies_the_envelope_byte_limit() -> None:
    encoded = _blob_asset()

    assert decode_blob_asset_wire(encoded, maximum_bytes=len(encoded)).data == b'{"answer":42}'
    with pytest.raises(ValueError, match="byte limit"):
        decode_blob_asset_wire(encoded, maximum_bytes=len(encoded) - 1)


@pytest.mark.parametrize("maximum_bytes", [True, 0, -1, 1.0, "128"])
def test_decode_blob_asset_wire_requires_a_positive_integer_limit(
    maximum_bytes: object,
) -> None:
    with pytest.raises(TypeError, match="positive integer"):
        decode_blob_asset_wire(_blob_asset(), maximum_bytes=cast(Any, maximum_bytes))


def test_decode_blob_asset_wire_requires_bytes() -> None:
    with pytest.raises(TypeError, match="must be bytes"):
        decode_blob_asset_wire(cast(Any, bytearray(_blob_asset())))
