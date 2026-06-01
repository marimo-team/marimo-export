from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from moexport.artifacts import Artifact, ArtifactData
from moexport.blobs import ContentAddressedBlobStore
from moexport.exporters import BundleExporterContext, ExporterContext


def _blob_files(root: Path) -> list[Path]:
    blob_root = root / "blobs"
    return [path for path in blob_root.rglob("*") if path.is_file()]


def test_blob_store_addresses_bytes_by_sha256(tmp_path) -> None:
    store = ContentAddressedBlobStore(tmp_path)

    ref = store.write("ignored-name.bin", b"hello", media_type="text/plain")

    digest = hashlib.sha256(b"hello").hexdigest()
    assert ref.model_dump(mode="json") == {
        "href": f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}",
        "media_type": "text/plain",
        "size": 5,
        "sha256": digest,
    }
    assert (tmp_path / ref.href).read_bytes() == b"hello"


def test_blob_store_rejects_parent_relative_href_prefix(tmp_path) -> None:
    store = ContentAddressedBlobStore(tmp_path, href_prefix="../../blobs")

    with pytest.raises(ValueError, match="invalid bundle href"):
        store.write("ignored-name.bin", b"hello", media_type="text/plain")


def test_blob_store_dedupes_identical_bytes(tmp_path) -> None:
    store = ContentAddressedBlobStore(tmp_path)

    first = store.write("first.arrow", b"same", media_type="application/a")
    second = store.write("second.parquet", bytearray(b"same"), media_type=None)

    assert first.href == second.href
    assert first.sha256 == second.sha256
    assert first.media_type == "application/a"
    assert second.media_type is None
    assert len(_blob_files(tmp_path)) == 1


def test_blob_store_keeps_distinct_bytes_separate(tmp_path) -> None:
    store = ContentAddressedBlobStore(tmp_path)

    first = store.write("value.bin", b"one")
    second = store.write("value.bin", b"two")

    assert first.href != second.href
    assert first.sha256 != second.sha256
    assert len(_blob_files(tmp_path)) == 2


def _export_bytes(value: str, ctx: ExporterContext, **options: Any) -> Artifact:
    if options:
        raise AssertionError(f"unexpected options: {options}")

    blob = ctx.write_blob(
        "value.bin",
        value.encode("utf-8"),
        media_type="text/plain",
    )
    return Artifact(
        format_id="text.v1",
        media_type="text/plain",
        data=ArtifactData(files={"value": blob}, entry="value"),
        metadata={
            "scenario": ctx.scenario_id,
            "value": ctx.value_name,
            "format_id": ctx.artifact_name,
        },
    )


def test_bundle_exporter_context_allows_distinct_artifacts_to_share_blob(
    tmp_path,
) -> None:
    store = ContentAddressedBlobStore(tmp_path)
    first_ctx = BundleExporterContext(
        scenario_id="default",
        value_name="title",
        artifact_name="text",
        blob_store=store,
    )
    second_ctx = BundleExporterContext(
        scenario_id="wide-chart",
        value_name="title",
        artifact_name="text",
        blob_store=store,
    )

    first = _export_bytes("same value", first_ctx)
    second = _export_bytes("same value", second_ctx)

    first_data = first.data
    second_data = second.data
    assert first_data.type == "bundle"
    assert second_data.type == "bundle"
    first_blob = first_data.files["value"]
    second_blob = second_data.files["value"]
    assert first.metadata != second.metadata
    assert first_blob.href == second_blob.href
    assert len(_blob_files(tmp_path)) == 1
