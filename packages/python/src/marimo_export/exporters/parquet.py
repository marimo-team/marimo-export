from __future__ import annotations

from io import BytesIO
from typing import Any, Literal

from marimo_export._marimo.compat import BlobAsset
from marimo_export.exporters._optional import optional

Compression = Literal["snappy", "none", "gzip", "brotli", "lz4", "zstd"]
_COMPRESSIONS = frozenset({"snappy", "none", "gzip", "brotli", "lz4", "zstd"})


def table(
    value: object,
    *,
    compression: Compression = "snappy",
    filename: str | None = None,
) -> BlobAsset:
    """Encode a pandas, Polars, or PyArrow table as Parquet."""

    if not isinstance(compression, str) or compression not in _COMPRESSIONS:
        raise ValueError("compression must be one of: brotli, gzip, lz4, none, snappy, zstd")
    pyarrow = optional("pyarrow", "parquet")
    parquet = optional("pyarrow.parquet", "parquet")
    arrow_table = _arrow_table(value, pyarrow)
    output = BytesIO()
    parquet.write_table(
        arrow_table,
        output,
        compression=None if compression == "none" else compression,
    )
    payload = output.getvalue()
    _validate_parquet(payload)
    return BlobAsset(
        data=payload,
        media_type="application/vnd.apache.parquet",
        filename=filename,
        metadata={
            "compression": compression,
            "rows": int(arrow_table.num_rows),
            "columns": int(arrow_table.num_columns),
        },
    )


def _arrow_table(value: object, pyarrow: Any) -> Any:
    if isinstance(value, pyarrow.Table):
        return value
    to_arrow = getattr(value, "to_arrow", None)
    if callable(to_arrow):
        converted = to_arrow()
        if isinstance(converted, pyarrow.Table):
            return converted
        if isinstance(converted, pyarrow.Array):
            return pyarrow.table({"value": converted})
    to_frame = getattr(value, "to_frame", None)
    if callable(to_frame):
        value = to_frame()
    try:
        return pyarrow.Table.from_pandas(value, preserve_index=False)
    except Exception as error:
        raise TypeError(
            "value must be a pandas or Polars DataFrame or Series, or a PyArrow table"
        ) from error


def _validate_parquet(payload: bytes) -> None:
    if len(payload) < 12 or not payload.startswith(b"PAR1") or not payload.endswith(b"PAR1"):
        raise ValueError("Parquet writer returned invalid file framing")
    footer_size = int.from_bytes(payload[-8:-4], "little")
    if footer_size <= 0 or footer_size > len(payload) - 12:
        raise ValueError("Parquet writer returned an invalid footer")


__all__ = ["Compression", "table"]
