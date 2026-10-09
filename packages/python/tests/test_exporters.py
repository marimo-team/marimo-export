from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest
from marimo_export.errors import SpecError
from marimo_export.exporters import (
    ExporterSpec,
    altair,
    anywidget,
    blob,
    importable,
    media,
    parquet,
)
from marimo_export.exporters._runtime import altair as altair_runtime
from marimo_export.exporters._runtime import blob as blob_runtime
from marimo_export.exporters._runtime import media as media_runtime
from marimo_export.exporters._runtime import parquet as parquet_runtime
from marimo_export.outputs import BlobAsset
from marimo_export.values import Size


def test_builtin_and_importable_factories_construct_normalized_descriptors() -> None:
    assert altair.vegalite() == ExporterSpec("altair.vegalite")
    assert media(["image/SVG+xml", "image/png"], scale=2).to_value() == {
        "dependencies": [],
        "name": "media",
        "options": {"accept": ["image/svg+xml", "image/png"], "scale": 2.0},
    }
    assert media(["application/pdf"], size=Size(250.38)).to_value() == {
        "dependencies": [],
        "name": "media",
        "options": {
            "accept": ["application/pdf"],
            "scale": 1.0,
            "size": {"width": 250.38, "height": None},
        },
    }
    assert altair.png(scale=2).to_value() == {
        "dependencies": [],
        "name": "altair.png",
        "options": {"scale": 2.0},
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
        lambda: media(["image/png"], scale=0),
        lambda: media("image/png"),
        lambda: media(cast(Any, 5)),
        lambda: media(cast(Any, [5])),
        lambda: media([]),
        lambda: media(["image/*"]),
        lambda: media(["image/png", "image/PNG"]),
        lambda: ExporterSpec(
            "media", options={"accept": ["application/pdf"], "size": {"width": 0}}
        ),
        lambda: ExporterSpec(
            "media", options={"accept": ["application/pdf"], "size": {"height": 120}}
        ),
        lambda: ExporterSpec(
            "media",
            options={"accept": ["application/pdf"], "size": {"width": 200, "depth": 3}},
        ),
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


def test_vegalite_and_media_exporters_read_every_row_of_a_chart() -> None:
    altair_module = pytest.importorskip("altair")
    pandas = pytest.importorskip("pandas")
    pytest.importorskip("vl_convert")
    # More rows than Altair embeds by default.
    rows = pandas.DataFrame({"x": range(6_000), "y": [index % 7 for index in range(6_000)]})
    chart = altair_module.Chart(rows).mark_point().encode(x="x:Q", y="y:Q")

    specification = json.loads(altair_runtime.vegalite(chart).data)
    image = media_runtime.media(chart, accept=["image/svg+xml"])

    assert len(specification["datasets"][next(iter(specification["datasets"]))]) == 6_000
    assert image.data.startswith(b"<svg")


def test_media_exporter_draws_a_figure_at_its_size_option() -> None:
    figure_module = pytest.importorskip("matplotlib.figure")
    figure = figure_module.Figure(figsize=(6, 2), layout="constrained")
    figure.subplots().plot([1, 2, 3])

    pdf = media_runtime.media(
        figure, accept=["application/pdf"], size={"width": 200.0, "height": 90.0}
    )

    assert b"/MediaBox [ 0 0 200 90 ]" in pdf.data


def test_vegalite_exporter_reads_altair_charts_and_specifications() -> None:
    class Described:
        def to_dict(self) -> dict[str, object]:
            return {"$schema": "https://vega.github.io/schema/vega-lite/v6.json"}

    with pytest.raises(TypeError, match="Altair chart or Vega-Lite mapping"):
        altair_runtime.vegalite(Described())


def test_media_and_parquet_runtime_exporters_produce_complete_assets() -> None:
    pyarrow = pytest.importorskip("pyarrow")
    altair_module = pytest.importorskip("altair")
    pytest.importorskip("vl_convert")
    chart = (
        altair_module.Chart(altair_module.Data(values=[{"x": 1, "y": 2}]))
        .mark_point()
        .encode(x="x:Q", y="y:Q")
    )
    source = pyarrow.table({"symbol": ["AAPL", "MSFT"], "value": [1.0, 2.0]})

    image = media_runtime.media(chart, accept=["image/png"], scale=2)
    chart_png = altair_runtime.png(chart, scale=2)
    table = parquet_runtime.table(source, filename="prices.parquet")

    assert image.media_type == "image/png"
    assert image.data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = (
        int.from_bytes(image.data[16:20], "big"),
        int.from_bytes(image.data[20:24], "big"),
    )
    assert (width / 2, height / 2) == (image.metadata["width"], image.metadata["height"])
    assert chart_png.data == image.data
    assert chart_png.metadata == {"scale": 2.0, **image.metadata}
    assert table.data.startswith(b"PAR1")
    assert table.data.endswith(b"PAR1")
    assert table.filename == "prices.parquet"
    assert table.metadata == {
        "compression": "snappy",
        "rows": 2,
        "columns": 2,
    }
