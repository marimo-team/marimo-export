from __future__ import annotations

from typing import Any

from moexport.artifacts import Artifact, ArtifactData
from moexport.blobs import BlobContent, BlobRef
from moexport.exporters import Exporter, ExporterContext


class FakeExporterContext:
    def __init__(
        self,
        *,
        scenario_id: str = "default",
        value_name: str = "value",
        format_name: str = "bytes",
    ) -> None:
        self.scenario_id = scenario_id
        self.value_name = value_name
        self.format_name = format_name

    def write_blob(
        self,
        name: str,
        data: BlobContent,
        *,
        media_type: str | None = None,
    ) -> BlobRef:
        return BlobRef(
            href=f"blobs/sha256/{name}",
            media_type=media_type or "application/octet-stream",
            size=len(bytes(data)),
            sha256="abc123",
        )

    def artifact(
        self,
        *,
        format_id: str,
        files: dict[str, BlobRef],
        entry: str | None = None,
        media_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        return Artifact(
            format_id=format_id,
            media_type=media_type,
            data=ArtifactData(files=files, entry=entry),
            metadata=metadata,
        )


def export_bytes(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
    del options
    blob = ctx.write_blob(
        "value.bin",
        bytes(str(value), "utf-8"),
        media_type="application/octet-stream",
    )
    return Artifact(
        format_id="bytes.v1",
        media_type="application/octet-stream",
        data=ArtifactData(files={"value": blob}, entry="value"),
        metadata={
            "scenario": ctx.scenario_id,
            "value": ctx.value_name,
            "format": ctx.format_name,
        },
    )


def test_exporter_callable_protocol_shape() -> None:
    exporter: Exporter = export_bytes
    artifact = exporter("hello", FakeExporterContext())

    assert artifact.format_id == "bytes.v1"
    assert artifact.data.type == "bundle"
    assert artifact.data.files["value"].size == 5
    assert set(Artifact.model_fields) == {"format_id", "media_type", "data", "metadata"}
    assert set(ArtifactData.model_fields) == {"type", "files", "entry"}


def test_artifact_data_is_blob_only() -> None:
    blob = BlobRef(
        href="blobs/sha256/ab/cd/hash",
        media_type="application/json",
        size=12,
        sha256="hash",
    )
    artifact = Artifact(
        format_id="json.v1",
        media_type="application/json",
        data=ArtifactData(files={"data": blob}, entry="data"),
        metadata=None,
    )

    assert artifact.data.type == "bundle"
    assert artifact.data.files["data"] == blob
    assert set(Artifact.model_fields) == {"format_id", "media_type", "data", "metadata"}


def test_exporter_context_creates_artifact_envelope() -> None:
    ctx = FakeExporterContext()
    blob = ctx.write_blob("data.json", b"{}", media_type="application/json")

    artifact = ctx.artifact(
        format_id="json.v1",
        media_type="application/json",
        files={"data": blob},
        entry="data",
        metadata={"kind": "test"},
    )

    assert artifact.format_id == "json.v1"
    assert artifact.data.files == {"data": blob}
    assert artifact.data.entry == "data"
    assert artifact.metadata == {"kind": "test"}
