from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest
from marimo_export.errors import SpecError
from marimo_export.exporters import ExporterSpec, altair, anywidget, blob, importable, parquet
from marimo_export.exporters._runtime import altair as altair_runtime
from marimo_export.exporters._runtime import blob as blob_runtime
from marimo_export.exporters._runtime import parquet as parquet_runtime
from marimo_export.outputs import BlobAsset


def test_builtin_and_importable_factories_construct_normalized_descriptors() -> None:
    assert altair.vegalite() == ExporterSpec("altair.vegalite")
    assert altair.png(scale=2).to_value() == {
        "dependencies": [],
        "name": "altair.png",
        "options": {"scale": 2},
    }
    assert anywidget.bundle().to_value() == "anywidget.bundle"
    assert parquet.table(filename="prices.parquet").to_value() == {
        "dependencies": [],
        "name": "parquet.table",
        "options": {
            "compression": "snappy",
            "filename": "prices.parquet",
        },
    }
    assert blob.json(metadata={"kind": "configuration"}).to_value() == {
        "dependencies": [],
        "name": "blob.json",
        "options": {
            "filename": None,
            "media_type": "application/json",
            "metadata": {"kind": "configuration"},
        },
    }
    custom = importable(
        "acme.exports:encode",
        options={"level": 3},
        dependencies=("acme.models", "acme.transforms"),
    )
    assert custom.dependencies == ("acme.models", "acme.transforms")
    assert custom.to_value() == {
        "dependencies": ["acme.models", "acme.transforms"],
        "name": "acme.exports:encode",
        "options": {"level": 3},
    }
    assert importable("acme.exports:encode").to_value() == {
        "dependencies": [],
        "name": "acme.exports:encode",
        "options": {},
    }


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ExporterSpec("unknown"),
        lambda: importable("acme.exports:encode.value"),
        lambda: importable("altair.vegalite"),
        lambda: importable("acme.exports:encode", options={"not-valid": 1}),
        lambda: importable(
            "acme.exports:encode",
            dependencies=("acme.transforms", "acme.models"),
        ),
        lambda: importable(
            "acme.exports:encode",
            dependencies=("acme.models", "acme.models"),
        ),
        lambda: importable("acme.exports:encode", dependencies=("acme:models",)),
        lambda: importable(
            "acme.exports:encode",
            dependencies=cast(Any, ["acme.models"]),
        ),
        lambda: ExporterSpec("altair.vegalite", dependencies=("acme.models",)),
        lambda: altair.png(scale=0),
        lambda: parquet.table(compression=cast(Any, "zip")),
    ],
)
def test_invalid_exporter_contracts_fail_during_spec_construction(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(SpecError) as raised:
        factory()

    assert raised.value.code == "spec_exporter_invalid"


def test_importable_accepts_options_through_one_mapping() -> None:
    with pytest.raises(TypeError, match="unexpected keyword"):
        cast(Any, importable)("acme.exports:encode", level=3)


def test_blob_and_vegalite_runtime_exporters_return_public_blob_assets() -> None:
    document = blob_runtime.json(
        {"b": 1, "a": 2},
        filename="config.json",
        metadata={"kind": "configuration"},
    )
    specification = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.1.0.json",
        "mark": "point",
        "data": {"values": [{"x": 1, "y": 2}]},
    }
    chart = altair_runtime.vegalite(specification)

    assert type(document) is BlobAsset
    assert document.data == b'{"a":2,"b":1}'
    assert document.filename == "config.json"
    assert json.loads(chart.data) == specification
    assert chart.media_type == "application/vnd.vegalite.v6+json"
    assert chart.metadata == {"schema_major": 6}


def test_png_and_parquet_runtime_exporters_produce_complete_assets() -> None:
    pyarrow = pytest.importorskip("pyarrow")
    pytest.importorskip("vl_convert")
    specification = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "mark": "point",
        "data": {"values": [{"x": 1, "y": 2}]},
    }
    source = pyarrow.table({"symbol": ["AAPL", "MSFT"], "value": [1.0, 2.0]})

    image = altair_runtime.png(specification, scale=2)
    table = parquet_runtime.table(source, filename="prices.parquet")

    assert image.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    assert image.metadata == {"scale": 2.0}
    assert table.data.startswith(b"PAR1")
    assert table.data.endswith(b"PAR1")
    assert table.filename == "prices.parquet"
    assert table.metadata == {
        "compression": "snappy",
        "rows": 2,
        "columns": 2,
    }
