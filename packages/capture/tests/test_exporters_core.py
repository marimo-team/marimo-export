from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from moexport.artifacts import Artifact, ArtifactData, JsonObject
from moexport.blobs import BlobContent, BlobRef
from moexport.exporters import core


class CapturingExporterContext:
    scenario_id = "default"
    value_name = "value"
    format_name = "json"

    def __init__(self) -> None:
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
        format_id: str,
        files: dict[str, BlobRef],
        entry: str | None = None,
        media_type: str | None = None,
        metadata: JsonObject | None = None,
    ) -> Artifact:
        return Artifact(
            format_id=format_id,
            media_type=media_type,
            data=ArtifactData(files=files, entry=entry),
            metadata=metadata,
        )


class HtmlLike:
    text = "<strong>hello</strong>"


def _payload(ctx: CapturingExporterContext, artifact: Artifact) -> bytes:
    return ctx.blobs[artifact.data.files[artifact.data.entry or ""].href]


def test_json_exporter_writes_json_blob_with_options() -> None:
    ctx = CapturingExporterContext()

    artifact = core.json(
        {"answer": 42},
        ctx,
        filename="answer.json",
        format_id="example.answer.json.v1",
        metadata={"kind": "answer"},
    )

    assert artifact.format_id == "example.answer.json.v1"
    assert artifact.media_type == "application/json"
    assert artifact.metadata == {"kind": "answer"}
    assert json.loads(_payload(ctx, artifact)) == {"answer": 42}


def test_text_exporter_writes_text_blob() -> None:
    ctx = CapturingExporterContext()

    artifact = core.text(123, ctx)

    assert artifact.format_id == "text.v1"
    assert _payload(ctx, artifact) == b"123"


def test_html_exporter_reads_text_attribute() -> None:
    ctx = CapturingExporterContext()

    artifact = core.html(HtmlLike(), ctx, format_id="example.html.v1")

    assert artifact.format_id == "example.html.v1"
    assert artifact.media_type == "text/html"
    assert _payload(ctx, artifact) == b"<strong>hello</strong>"


def test_core_exporters_validate_options() -> None:
    with pytest.raises(ValidationError, match="extra"):
        core.json({"ok": True}, CapturingExporterContext(), extra=True)
