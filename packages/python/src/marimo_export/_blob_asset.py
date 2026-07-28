from __future__ import annotations

from dataclasses import dataclass

from marimo_export._format import (
    MAX_FORMAT_ID_ASCII_BYTES,
    MAX_FORMAT_METADATA_JSON_BYTES,
    MAX_MEDIA_TYPE_ASCII_BYTES,
)
from marimo_export._portable import MAX_PORTABLE_BASENAME_UTF8_BYTES


@dataclass(frozen=True, slots=True)
class BlobAssetWire:
    data: memoryview
    media_type: str
    filename: str | None
    format_id: str
    metadata_json: memoryview


class _BlobAssetFieldLimitError(ValueError):
    __slots__ = ("field",)

    def __init__(self, field: str, maximum_bytes: int) -> None:
        super().__init__(f"BlobAsset {field} exceeds the {maximum_bytes} byte limit")
        self.field = field


def decode_blob_asset_wire(
    data: bytes,
    *,
    maximum_bytes: int | None = None,
) -> BlobAssetWire:
    """Decode the fixed canonical MessagePack shape emitted for `BlobAsset`.

    `maximum_bytes` bounds the accepted envelope. Bounded field lengths are
    checked before strings are decoded or binary fields are sliced. Binary
    fields remain read-only views over the envelope. The accepted shape contains
    two fixed map levels and has no recursive values.
    """

    if not isinstance(data, bytes):
        raise TypeError("BlobAsset MessagePack must be bytes")
    if maximum_bytes is not None and (
        isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or maximum_bytes <= 0
    ):
        raise TypeError("maximum_bytes must be a positive integer")
    if maximum_bytes is not None and len(data) > maximum_bytes:
        raise ValueError(f"BlobAsset MessagePack exceeds the {maximum_bytes} byte limit")

    reader = _CanonicalReader(data)
    reader.expect_byte(0x84)
    reader.expect_key(b"data")
    payload = reader.binary()
    reader.expect_key(b"media_type")
    media_type = reader.string(
        maximum_bytes=MAX_MEDIA_TYPE_ASCII_BYTES,
        field="media_type",
    )
    reader.expect_key(b"filename")
    filename = reader.nullable_string(
        maximum_bytes=MAX_PORTABLE_BASENAME_UTF8_BYTES,
        field="filename",
    )
    reader.expect_key(b"metadata")
    reader.expect_byte(0x82)
    reader.expect_key(b"format_id")
    format_id = reader.string(
        maximum_bytes=MAX_FORMAT_ID_ASCII_BYTES,
        field="format_id",
    )
    reader.expect_key(b"metadata_json")
    metadata_json = reader.binary(
        maximum_bytes=MAX_FORMAT_METADATA_JSON_BYTES,
        field="metadata_json",
    )
    reader.expect_end()

    return BlobAssetWire(
        data=payload,
        media_type=media_type,
        filename=filename,
        format_id=format_id,
        metadata_json=metadata_json,
    )


class _CanonicalReader:
    __slots__ = ("_data", "_offset")

    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)
        self._offset = 0

    def binary(
        self,
        *,
        maximum_bytes: int | None = None,
        field: str = "binary value",
    ) -> memoryview:
        head = self._u8()
        if head == 0xC4:
            length = self._u8()
        elif head == 0xC5:
            length = self._u16()
            if length <= 0xFF:
                raise _noncanonical_length()
        elif head == 0xC6:
            length = self._u32()
            if length <= 0xFFFF:
                raise _noncanonical_length()
        else:
            raise ValueError("BlobAsset binary value has an invalid MessagePack token")
        if maximum_bytes is not None and length > maximum_bytes:
            raise _BlobAssetFieldLimitError(field, maximum_bytes)
        return self._payload(length)

    def string(self, *, maximum_bytes: int, field: str) -> str:
        head = self._u8()
        if 0xA0 <= head <= 0xBF:
            length = head & 0x1F
        elif head == 0xD9:
            length = self._u8()
            if length <= 0x1F:
                raise _noncanonical_length()
        elif head == 0xDA:
            length = self._u16()
            if length <= 0xFF:
                raise _noncanonical_length()
        elif head == 0xDB:
            length = self._u32()
            if length <= 0xFFFF:
                raise _noncanonical_length()
        else:
            raise ValueError("BlobAsset string has an invalid MessagePack token")
        if length > maximum_bytes:
            raise _BlobAssetFieldLimitError(field, maximum_bytes)
        return str(self._payload(length), "utf-8", "strict")

    def nullable_string(self, *, maximum_bytes: int, field: str) -> str | None:
        if self._peek() != 0xC0:
            return self.string(maximum_bytes=maximum_bytes, field=field)
        self._offset += 1
        return None

    def expect_key(self, expected: bytes) -> None:
        if len(expected) > 0x1F:
            raise AssertionError("BlobAsset key exceeds fixstr length")
        self.expect_byte(0xA0 | len(expected))
        if self._payload(len(expected)) != expected:
            raise ValueError("BlobAsset does not use its canonical MessagePack shape")

    def expect_byte(self, expected: int) -> None:
        if self._u8() != expected:
            raise ValueError("BlobAsset does not use its canonical MessagePack shape")

    def expect_end(self) -> None:
        if self._offset != len(self._data):
            raise ValueError("BlobAsset MessagePack contains trailing data")

    def _peek(self) -> int:
        self._ensure(1)
        return self._data[self._offset]

    def _payload(self, length: int) -> memoryview:
        self._ensure(length)
        start = self._offset
        self._offset += length
        return self._data[start : self._offset]

    def _u8(self) -> int:
        self._ensure(1)
        value = self._data[self._offset]
        self._offset += 1
        return value

    def _u16(self) -> int:
        self._ensure(2)
        value = int.from_bytes(self._data[self._offset : self._offset + 2], "big")
        self._offset += 2
        return value

    def _u32(self) -> int:
        self._ensure(4)
        value = int.from_bytes(self._data[self._offset : self._offset + 4], "big")
        self._offset += 4
        return value

    def _ensure(self, length: int) -> None:
        if length > len(self._data) - self._offset:
            raise ValueError("BlobAsset MessagePack is truncated")


def _noncanonical_length() -> ValueError:
    return ValueError("BlobAsset must use minimal MessagePack length prefixes")


__all__ = ["BlobAssetWire", "decode_blob_asset_wire"]
