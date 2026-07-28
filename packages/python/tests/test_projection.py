from __future__ import annotations

from typing import Any, cast

import pytest
from marimo_export._json import JsonValue, canonical_bytes
from marimo_export.projection import Projection


def test_projection_is_immutable_and_detaches_metadata() -> None:
    source: dict[str, JsonValue] = {"columns": ["amount"]}
    projection = Projection(
        b"payload",
        format_id="table.arrow.v1",
        media_type="application/vnd.apache.arrow.file",
        filename="table.arrow",
        metadata=source,
    )

    source_columns = source["columns"]
    assert isinstance(source_columns, list)
    source_columns.append("region")
    returned = projection.metadata
    returned_columns = returned["columns"]
    assert isinstance(returned_columns, list)
    returned_columns.append("date")

    assert projection.data == b"payload"
    assert projection.format_id == "table.arrow.v1"
    assert projection.metadata == {"columns": ["amount"]}
    field_name = "format_id"
    with pytest.raises(AttributeError):
        setattr(projection, field_name, "other.v1")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"data": bytearray(b"x")}, "data must be bytes"),
        ({"format_id": "json/value"}, "projection.format_id"),
        ({"media_type": "json"}, "type/subtype"),
        ({"media_type": "text/plain; name=naïve"}, "printable ASCII"),
        ({"media_type": "text/plain; charset=utf-8\x7f"}, "printable ASCII"),
        ({"filename": "../result.json"}, "portable filename"),
        ({"filename": "result\n.json"}, "portable filename"),
        ({"filename": "result\x7f.json"}, "portable filename"),
        ({"filename": "CON.json"}, "portable filename"),
        ({"filename": "result.json:secret"}, "portable filename"),
        ({"filename": "result?.json"}, "portable filename"),
        ({"filename": "result.json."}, "portable filename"),
        ({"filename": "é" * 128}, "portable filename"),
        ({"metadata": []}, "must be an object"),
        ({"metadata": {"value": float("nan")}}, "NaN or infinity"),
        ({"metadata": {"value": "\ud800"}}, "Unicode scalar values"),
    ],
)
def test_projection_rejects_invalid_fields(kwargs: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "data": b"x",
        "format_id": "json.v1",
        "media_type": "application/json",
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError), match=message):
        Projection(**cast(Any, values))


def test_projection_accepts_a_255_byte_filename() -> None:
    filename = f"{'é' * 127}a"

    projection = Projection(
        b"x",
        format_id="bytes.v1",
        media_type="application/octet-stream",
        filename=filename,
    )

    assert projection.filename == filename


def test_projection_bounds_format_id_at_255_ascii_bytes() -> None:
    format_id = "f" * 255
    projection = Projection(
        b"x",
        format_id=format_id,
        media_type="application/octet-stream",
    )

    assert projection.format_id == format_id

    oversized = f"{format_id}f"
    with pytest.raises(ValueError) as error:
        Projection(
            b"x",
            format_id=oversized,
            media_type="application/octet-stream",
        )
    assert oversized not in str(error.value)


def test_projection_bounds_media_type_at_1024_ascii_bytes() -> None:
    prefix = "text/plain;"
    media_type = f"{prefix}{'a' * (1024 - len(prefix))}"
    projection = Projection(
        b"x",
        format_id="text.v1",
        media_type=media_type,
    )

    assert projection.media_type == media_type

    oversized = f"{media_type}a"
    with pytest.raises(ValueError) as error:
        Projection(
            b"x",
            format_id="text.v1",
            media_type=oversized,
        )
    assert oversized not in str(error.value)


def test_projection_bounds_canonical_metadata_json_at_256_kib() -> None:
    limit = 256 * 1024
    empty_envelope_size = len(canonical_bytes({"value": ""}))
    value = "a" * (limit - empty_envelope_size)
    projection = Projection(
        b"x",
        format_id="text.v1",
        media_type="text/plain",
        metadata={"value": value},
    )

    assert len(canonical_bytes(projection.metadata)) == limit

    oversized = f"{value}a"
    with pytest.raises(ValueError) as error:
        Projection(
            b"x",
            format_id="text.v1",
            media_type="text/plain",
            metadata={"value": oversized},
        )
    assert oversized not in str(error.value)
