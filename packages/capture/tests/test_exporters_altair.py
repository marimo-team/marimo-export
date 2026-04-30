from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from moexport.artifacts import Artifact, ArtifactData, JsonObject
from moexport.blobs import BlobContent, BlobRef, ContentAddressedBlobStore
from moexport.exporters import BundleExporterContext

altair = pytest.importorskip("altair")
altair_exporters = pytest.importorskip("moexport.exporters.altair")


class CapturingExporterContext:
    def __init__(
        self,
        *,
        scenario_id: str = "default",
        value_name: str = "chart",
        format_name: str = "png",
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
        href = f"memory://{name}"
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


def chart() -> Any:
    return (
        altair.Chart({"values": [{"x": 1, "y": 2}]})
        .mark_point()
        .encode(x="x:Q", y="y:Q")
    )


def _blob_files(root: Path) -> list[Path]:
    blob_root = root / "blobs"
    return [path for path in blob_root.rglob("*") if path.is_file()]


def test_vegalite_exports_blob_backed_json_spec() -> None:
    ctx = CapturingExporterContext()

    artifact = altair_exporters.vegalite(chart(), ctx)

    assert artifact.format == "vegalite.v1"
    assert artifact.media_type == "application/vnd.vegalite+json"
    assert artifact.data.type == "bundle"
    blob = artifact.data.files["spec"]
    value = json.loads(ctx.blobs[blob.href])
    assert artifact.data.entry == "spec"
    assert blob.media_type == "application/vnd.vegalite+json"
    assert artifact.metadata == {"schema": value["$schema"]}
    assert value["mark"]["type"] == "point"


def test_vegalite_dedupes_same_spec_across_value_names(tmp_path: Path) -> None:
    store = ContentAddressedBlobStore(tmp_path)
    first_ctx = BundleExporterContext(
        scenario_id="default",
        value_name="left_chart",
        format_name="vegalite",
        blob_store=store,
    )
    second_ctx = BundleExporterContext(
        scenario_id="default",
        value_name="right_chart",
        format_name="vegalite",
        blob_store=store,
    )

    first = altair_exporters.vegalite(chart(), first_ctx)
    second = altair_exporters.vegalite(chart(), second_ctx)

    first_blob = first.data.files["spec"]
    second_blob = second.data.files["spec"]
    assert first_blob.href == second_blob.href
    assert first_blob.sha256 == second_blob.sha256
    assert len(_blob_files(tmp_path)) == 1


def test_png_exports_chart_image_with_vl_convert(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def vegalite_to_png(**kwargs: Any) -> bytes:
        calls.append(kwargs)
        assert json.loads(kwargs["vl_spec"])["mark"]["type"] == "point"
        return b"png-bytes"

    monkeypatch.setitem(
        sys.modules,
        "vl_convert",
        SimpleNamespace(vegalite_to_png=vegalite_to_png),
    )
    ctx = CapturingExporterContext()

    artifact = altair_exporters.png(chart(), ctx, scale=2.0, vl_version="6.4.1")

    assert calls[0]["scale"] == 2.0
    assert calls[0]["vl_version"] == "6.4.1"
    assert artifact.format == "image.png.v1"
    assert artifact.media_type == "image/png"
    assert artifact.data.type == "bundle"
    blob = artifact.data.files["image"]
    assert ctx.blobs[blob.href] == b"png-bytes"


def test_altair_exporters_validate_options_with_pydantic() -> None:
    with pytest.raises(ValidationError, match="extra"):
        altair_exporters.vegalite(chart(), CapturingExporterContext(), unknown=True)

    with pytest.raises(ValidationError, match="greater than 0"):
        altair_exporters.png(chart(), CapturingExporterContext(), scale=0)
