from __future__ import annotations

import json
from typing import cast

import pytest
from marimo_export import BlobAsset
from marimo_export.exporters.altair import png, vegalite
from marimo_export.exporters.blob import html, text
from marimo_export.exporters.blob import json as json_blob
from marimo_export.exporters.parquet import Compression, table


def test_blob_exporters_return_exact_native_blob_assets() -> None:
    document = json_blob(
        {"b": 1, "a": 2},
        filename="config.json",
        metadata={"kind": "configuration"},
    )
    plain = text("Grüße")
    markup = html("<strong>ready</strong>")

    assert type(document) is BlobAsset
    assert document.data == b'{"a":2,"b":1}'
    assert document.media_type == "application/json"
    assert document.filename == "config.json"
    assert document.metadata == {"kind": "configuration"}
    assert plain.data == "Grüße".encode()
    assert plain.media_type == "text/plain; charset=utf-8"
    assert markup.media_type == "text/html; charset=utf-8"


def test_blob_text_rejects_unpaired_surrogates() -> None:
    with pytest.raises(ValueError, match="Unicode scalar"):
        text("\ud800")


def test_vegalite_exporter_uses_declared_schema_major() -> None:
    specification = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.1.0.json",
        "mark": "point",
        "data": {"values": [{"x": 1, "y": 2}]},
    }

    asset = vegalite(specification)

    assert type(asset) is BlobAsset
    assert json.loads(asset.data) == specification
    assert asset.media_type == "application/vnd.vegalite.v6+json"
    assert asset.metadata == {"schema_major": 6}


def test_vegalite_exporter_requires_versioned_schema() -> None:
    with pytest.raises(ValueError, match=r"declare \$schema"):
        vegalite({"mark": "point"})


def test_png_exporter_produces_complete_png() -> None:
    pytest.importorskip("vl_convert")
    specification = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "mark": "point",
        "data": {"values": [{"x": 1, "y": 2}]},
    }

    asset = png(specification, scale=2)

    assert asset.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert asset.data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    assert asset.media_type == "image/png"
    assert asset.metadata == {"scale": 2.0}


def test_parquet_exporter_is_hyparquet_compatible_by_default() -> None:
    pyarrow = pytest.importorskip("pyarrow")
    source = pyarrow.table({"symbol": ["AAPL", "MSFT"], "value": [1.0, 2.0]})

    asset = table(source, filename="prices.parquet")

    assert type(asset) is BlobAsset
    assert asset.data.startswith(b"PAR1")
    assert asset.data.endswith(b"PAR1")
    assert asset.media_type == "application/vnd.apache.parquet"
    assert asset.filename == "prices.parquet"
    assert asset.metadata == {
        "compression": "snappy",
        "rows": 2,
        "columns": 2,
    }


@pytest.mark.parametrize("compression", ["snappy", "none", "gzip", "brotli", "lz4", "zstd"])
def test_parquet_exporter_accepts_declared_compressions(compression: str) -> None:
    pyarrow = pytest.importorskip("pyarrow")
    source = pyarrow.table({"value": [1, 2]})

    asset = table(source, compression=cast(Compression, compression))

    assert asset.metadata["compression"] == compression


def test_exporter_modules_have_no_registry_side_effect() -> None:
    from marimo_export.exporters import Exporter

    exporter: Exporter[object, BlobAsset] = json_blob
    assert exporter({"ready": True}).media_type == "application/json"
