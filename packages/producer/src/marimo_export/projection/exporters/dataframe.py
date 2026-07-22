from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any

from marimo_export import Projection
from marimo_export._builtin_exporters import normalize_builtin_options
from marimo_export.projection.exporters._optional import optional


def arrow(value: Any, **options: Any) -> Projection:
    normalize_builtin_options("arrow", options, "arrow options")
    pyarrow = optional("pyarrow", "arrow")
    table = _arrow_table(value, pyarrow, "arrow")
    output = BytesIO()
    with pyarrow.ipc.new_stream(output, table.schema) as writer:
        writer.write_table(table)
    return Projection(
        output.getvalue(),
        format_id="dataframe.arrow.v1",
        media_type="application/vnd.apache.arrow.stream",
        metadata={"rows": table.num_rows, "columns": list(table.column_names)},
    )


def parquet(value: Any, **options: Any) -> Projection:
    normalized = normalize_builtin_options("parquet", options, "parquet options")
    compression = normalized["compression"]
    assert isinstance(compression, str)
    pyarrow = optional("pyarrow", "parquet")
    parquet_module = optional("pyarrow.parquet", "parquet")
    table = _arrow_table(value, pyarrow, "parquet")
    output = BytesIO()
    parquet_module.write_table(table, output, compression=compression)
    return Projection(
        output.getvalue(),
        format_id="dataframe.parquet.v1",
        media_type="application/vnd.apache.parquet",
        metadata={
            "rows": table.num_rows,
            "columns": list(table.column_names),
            "compression": compression,
        },
    )


def _arrow_table(value: Any, pyarrow: Any, exporter: str) -> Any:
    if isinstance(value, pyarrow.Table):
        return value
    if isinstance(value, list) and all(isinstance(row, Mapping) for row in value):
        return pyarrow.Table.from_pylist(value)
    if hasattr(value, "to_arrow"):
        return value.to_arrow()
    narwhals = optional("narwhals", exporter)
    return narwhals.from_native(value, eager_only=True).to_arrow()
