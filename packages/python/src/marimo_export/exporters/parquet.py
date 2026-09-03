from __future__ import annotations

from typing import Literal

from marimo_export.exporters._spec import ExporterSpec, builtin

Compression = Literal["snappy", "none", "gzip", "brotli", "lz4", "zstd"]


def table(
    *,
    compression: Compression = "snappy",
    filename: str | None = None,
) -> ExporterSpec:
    """Select a Parquet representation for a table."""

    return builtin(
        "parquet.table",
        {
            "compression": compression,
            "filename": filename,
        },
    )


__all__ = ["Compression", "table"]
