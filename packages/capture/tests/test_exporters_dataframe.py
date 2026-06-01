from __future__ import annotations

from io import BytesIO

import pytest
from pydantic import ValidationError

from moexport.artifacts import Artifact, ArtifactData, JsonObject
from moexport.blobs import BlobContent, BlobRef

pyarrow = pytest.importorskip("pyarrow")
pytest.importorskip("narwhals")
pyarrow_parquet = pytest.importorskip("pyarrow.parquet")
dataframe_exporters = pytest.importorskip("moexport.exporters.dataframe")


class CapturingExporterContext:
    def __init__(
        self,
        *,
        scenario_id: str = "default",
        value_name: str = "prices",
        format_name: str = "arrow",
    ) -> None:
        self.scenario_id = scenario_id
        self.value_name = value_name
        self.format_name = format_name
        self.blobs: dict[str, bytes] = {}

    def write_blob(
        self,
        name: str,
        data: BlobContent,
        *,
        media_type: str | None = None,
    ) -> BlobRef:
        href = f"blobs/test/{name}"
        blob = bytes(data)
        self.blobs[href] = blob
        return BlobRef(
            href=href,
            media_type=media_type,
            size=len(blob),
            sha256="test-sha",
        )

    def artifact(
        self,
        *,
        format: str,
        files: dict[str, BlobRef],
        entry: str | None = None,
        media_type: str | None = None,
        metadata: JsonObject | None = None,
    ) -> Artifact:
        return Artifact(
            format=format,
            media_type=media_type,
            data=ArtifactData(files=files, entry=entry),
            metadata=metadata,
        )


def test_parquet_exports_dataframe_family_via_narwhals() -> None:
    table = pyarrow.table({"symbol": ["AAPL", "MSFT"], "close": [10.0, 20.0]})
    ctx = CapturingExporterContext(format_name="parquet")

    artifact = dataframe_exporters.parquet(table, ctx)

    assert artifact.format == "dataframe.parquet.v1"
    assert artifact.media_type == "application/vnd.apache.parquet"
    assert artifact.metadata == {
        "rows": 2,
        "columns": ["symbol", "close"],
        "compression": "NONE",
    }
    assert artifact.data.type == "bundle"

    blob = artifact.data.files["data"]
    roundtrip = pyarrow_parquet.read_table(BytesIO(ctx.blobs[blob.href]))
    assert roundtrip.column_names == ["symbol", "close"]
    assert roundtrip.num_rows == 2


def test_arrow_exports_dataframe_family_as_ipc_stream() -> None:
    table = pyarrow.table({"symbol": ["AAPL", "MSFT"], "close": [10.0, 20.0]})
    ctx = CapturingExporterContext(format_name="arrow")

    artifact = dataframe_exporters.arrow(table, ctx)

    assert artifact.format == "dataframe.arrow.v1"
    assert artifact.media_type == "application/vnd.apache.arrow.stream"
    assert artifact.metadata == {
        "rows": 2,
        "columns": ["symbol", "close"],
    }
    assert artifact.data.type == "bundle"

    blob = artifact.data.files["data"]
    with pyarrow.ipc.open_stream(BytesIO(ctx.blobs[blob.href])) as reader:
        roundtrip = reader.read_all()
    assert roundtrip.column_names == ["symbol", "close"]
    assert roundtrip.num_rows == 2


def test_dataframe_exporters_validate_options_with_pydantic() -> None:
    table = pyarrow.table({"a": [1]})

    with pytest.raises(ValidationError, match="compression"):
        dataframe_exporters.parquet(
            table, CapturingExporterContext(), compression="zstd"
        )
