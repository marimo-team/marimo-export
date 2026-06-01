from __future__ import annotations

import json

from moexport.artifacts import Artifact, ArtifactData, JsonObject
from moexport.blobs import BlobContent, BlobRef
from moexport.exporters import display
from moexport.snapshots import OutputSnapshot


class CapturingExporterContext:
    scenario_id = "default"
    value_name = "output"
    artifact_name = "display"

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


def test_display_json_preserves_output_snapshot_errors() -> None:
    ctx = CapturingExporterContext()
    output = OutputSnapshot(
        channel="error",
        mimetype="application/vnd.marimo.export.error+json",
        data={"type": "ValueError", "message": "bad display"},
    )

    artifact = display.display_json(output, ctx)
    blob = artifact.data.files[artifact.data.entry or ""]
    payload = json.loads(ctx.blobs[blob.href])

    assert payload["outputs"] == [
        {
            "channel": "error",
            "mimetype": "application/vnd.marimo.export.error+json",
            "data": {"type": "ValueError", "message": "bad display"},
        }
    ]
