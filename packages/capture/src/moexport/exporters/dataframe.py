"""Dataframe exporters.

This module is backend-family oriented: pandas, Polars, Arrow, and other
Narwhals-supported frames enter through the same Narwhals conversion path.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

from moexport.artifacts import Artifact, ArtifactData, JsonObject
from moexport.exporters._core import ExporterContext, ExporterOptions
from moexport.exporters._optional import import_optional

if TYPE_CHECKING:
    import narwhals as nw
    import pyarrow as pa

ARROW_FORMAT = "dataframe.arrow.v1"
ARROW_MEDIA_TYPE = "application/vnd.apache.arrow.stream"
PARQUET_FORMAT = "dataframe.parquet.v1"
PARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"


class DataframeOptions(ExporterOptions):
    """Options shared by dataframe exporters."""


_DATAFRAME_OPTIONS = TypeAdapter(DataframeOptions)


def parquet(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
    """Export a dataframe-like value as Parquet bytes."""

    _parse_options(options)
    pyarrow_parquet = import_optional(
        "pyarrow.parquet",
        package="pyarrow",
        extra="dataframe",
        purpose="Parquet dataframe export",
    )
    frame = _to_narwhals_dataframe(value)
    table: pa.Table = frame.to_arrow()

    output = BytesIO()
    pyarrow_parquet.write_table(table, output, compression="NONE")
    blob = ctx.write_blob(
        "data.parquet",
        output.getvalue(),
        media_type=PARQUET_MEDIA_TYPE,
    )

    return Artifact(
        format_id=PARQUET_FORMAT,
        media_type=PARQUET_MEDIA_TYPE,
        data=ArtifactData(files={"data": blob}, entry="data"),
        metadata={**_metadata(frame), "compression": "NONE"},
    )


def arrow(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
    """Export a dataframe-like value as an Arrow IPC stream."""

    _parse_options(options)
    pyarrow_ipc = import_optional(
        "pyarrow.ipc",
        package="pyarrow",
        extra="dataframe",
        purpose="Arrow dataframe export",
    )
    frame = _to_narwhals_dataframe(value)
    table: pa.Table = frame.to_arrow()

    output = BytesIO()
    with pyarrow_ipc.new_stream(output, table.schema) as writer:
        writer.write_table(table)

    blob = ctx.write_blob(
        "data.arrow",
        output.getvalue(),
        media_type=ARROW_MEDIA_TYPE,
    )

    return Artifact(
        format_id=ARROW_FORMAT,
        media_type=ARROW_MEDIA_TYPE,
        data=ArtifactData(files={"data": blob}, entry="data"),
        metadata=_metadata(frame),
    )


def _to_narwhals_dataframe(value: Any) -> nw.DataFrame[Any]:
    narwhals = import_optional(
        "narwhals",
        package="narwhals",
        extra="dataframe",
        purpose="Dataframe export",
    )
    return cast("nw.DataFrame[Any]", narwhals.from_native(value, eager_only=True))


def _metadata(frame: nw.DataFrame[Any]) -> JsonObject:
    rows, columns = frame.shape
    return {
        "rows": rows,
        "columns": list(frame.columns),
    }


def _parse_options(options: dict[str, Any]) -> DataframeOptions:
    return _DATAFRAME_OPTIONS.validate_python(options)
