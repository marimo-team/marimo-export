from __future__ import annotations

import asyncio
import inspect
import json as json_module
from typing import Any, cast

import marimo as mo
import pytest
from marimo_export import Projection
from marimo_export.plan import decode_plan
from marimo_export.projection.exporters import (
    arrow,
    html,
    json,
    parquet,
    png,
    text,
    vegalite,
)
from marimo_export.projection.synthetic_cells import (
    PROJECTION_CELL_ABI,
    project,
    project_prepared_anywidget,
    projection_binding,
)


def _output(plan_value: dict[str, object], name: str):
    return next(item for item in decode_plan(plan_value).outputs if item.name == name)


def test_builtin_exporters_have_fixed_format_contracts() -> None:
    assert json({"b": 1, "a": 2}).payload == b'{"a": 2, "b": 1}'
    assert text(42) == Projection(
        b"42",
        format_id="text.v1",
        media_type="text/plain; charset=utf-8",
    )
    assert html(mo.md("**hello**")).media_type == "text/html; charset=utf-8"
    assert html(mo.Html('<img src="https://marimo.io/logo.png">')).payload == (
        b'<img src="https://marimo.io/logo.png">'
    )

    with pytest.raises(TypeError, match="does not accept: media_type"):
        text("value", media_type="text/custom")


def test_html_export_rejects_truncated_virtual_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "marimo_export._marimo.html.read_virtual_file",
        lambda filename, size: b"short",
    )

    with pytest.raises(ValueError, match="declared 10 bytes but returned 5"):
        html(mo.Html('<img src="./@file/10-image.png">'))


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"sort_keys": "false"}, "sort_keys"),
        ({"indent": True}, "indent"),
        ({"indent": -1}, "indent"),
        ({"indent": 2**53}, "indent"),
    ],
)
def test_json_exporter_rejects_coerced_options(options: dict[str, object], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        json({"value": 1}, **options)


def test_projection_requires_bytes_and_a_nonempty_media_type() -> None:
    with pytest.raises(TypeError, match="payload must be bytes"):
        Projection(cast(Any, "text"), format_id="custom.v1")
    with pytest.raises(TypeError, match="media_type"):
        Projection(b"data", format_id="custom.v1", media_type="")


def test_projection_names_codec_fields_at_the_call_site() -> None:
    parameters = inspect.signature(Projection).parameters
    assert parameters["payload"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("format_id", "media_type", "metadata")
    )
    assert Projection(b"data", format_id="custom.v1").media_type == "application/octet-stream"
    with pytest.raises(TypeError, match="positional"):
        cast(Any, Projection)(b"data", "custom.v1")


def test_synthetic_projection_identity_excludes_public_aliases() -> None:
    plan = decode_plan(
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "first": {
                    "source": "frame",
                    "formats": {"primary": {"exporter": "json"}},
                },
                "second": {
                    "source": "frame",
                    "formats": {"secondary": {"exporter": "json"}},
                },
            },
        }
    )
    first, second = plan.outputs
    first_binding = projection_binding(
        output_name=first.name,
        format_name=first.formats[0].name,
        source=first.source,
        format_plan=first.formats[0],
    )
    second_binding = projection_binding(
        output_name=second.name,
        format_name=second.formats[0].name,
        source=second.source,
        format_plan=second.formats[0],
    )

    assert first_binding.cell == second_binding.cell


def test_synthetic_cell_returns_the_complete_projection() -> None:
    result = asyncio.run(
        project(
            {"answer": 42},
            exporter_spec=(
                '{"ref":"marimo_export.projection.exporters.json:json","version":"json.v1"}'
            ),
            options_json="{}",
            cache_token=b"",
            projection_cell_abi=PROJECTION_CELL_ABI,
        )
    )

    assert result == Projection(
        b'{"answer": 42}',
        format_id="json.v1",
        media_type="application/json",
    )


def test_anywidget_projection_separates_live_preparation_from_cached_projection() -> None:
    plan = decode_plan(
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "widget": {
                    "source": "live_widget",
                    "formats": {"anywidget": {}},
                }
            },
        }
    )
    output = plan.outputs[0]
    binding = projection_binding(
        output_name=output.name,
        format_name=output.formats[0].name,
        source=output.source,
        format_plan=output.formats[0],
    )

    preparation = binding.cell.preparation
    assert preparation is not None
    assert "live_widget" in preparation.code
    assert "live_widget" not in binding.cell.code
    assert binding.cell.cache_token_name == preparation.result_name


def test_prepared_anywidget_payload_becomes_the_complete_projection() -> None:
    payload = json_module.dumps(
        {
            "schema": "marimo-export.anywidget.v1",
            "rootModelId": "model-0",
            "files": {},
            "modelNotifications": [
                {
                    "op": "model-lifecycle",
                    "model_id": "model-0",
                    "message": {
                        "method": "open",
                        "state": {},
                        "buffer_paths": [],
                        "buffers": [],
                        "esm_spec": {
                            "url": "data:text/javascript,export%20default%20%7B%7D",
                            "hash": "root-module",
                        },
                    },
                }
            ],
        },
        separators=(",", ":"),
    ).encode()

    result = project_prepared_anywidget(
        payload,
        exporter_spec=(
            '{"ref":"marimo_export.projection.exporters.anywidget:anywidget",'
            '"version":"anywidget.v1"}'
        ),
        options_json="{}",
        projection_cell_abi=PROJECTION_CELL_ABI,
    )

    assert result == Projection(
        payload,
        format_id="anywidget.v1",
        media_type="application/vnd.marimo-export.anywidget+json",
        metadata={"models": 1, "root_model_id": "model-0"},
    )


def test_custom_exporter_must_return_projection() -> None:
    def invalid(value: object) -> bytes:
        del value
        return b"raw"

    with pytest.raises(TypeError, match="must return Projection"):
        asyncio.run(
            project(
                object(),
                exporter_spec='{"definition":"invalid"}',
                options_json="{}",
                cache_token=b"",
                projection_cell_abi=PROJECTION_CELL_ABI,
                exporter=invalid,
            )
        )


def test_optional_dataframe_exporters_emit_portable_formats() -> None:
    pyarrow = pytest.importorskip("pyarrow")
    table = pyarrow.table({"value": [1, 2]})
    rows = [{"value": 1}, {"value": 2}]

    arrow_projection = arrow(table)
    parquet_projection = parquet(rows)

    assert arrow_projection.format_id == "dataframe.arrow.v1"
    assert arrow_projection.payload
    assert parquet_projection.format_id == "dataframe.parquet.v1"
    assert parquet_projection.payload.startswith(b"PAR1")
    assert parquet_projection.metadata == {
        "rows": 2,
        "columns": ["value"],
        "compression": "NONE",
    }


def test_vegalite_exporter_matches_the_schema_major_in_its_media_type() -> None:
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.1.0.json",
        "mark": "point",
        "data": {"values": [{"x": 1, "y": 2}]},
    }

    result = vegalite(spec)

    assert json_module.loads(result.payload) == spec
    assert result.format_id == "vegalite.v1"
    assert result.media_type == "application/vnd.vegalite.v6+json"


@pytest.mark.parametrize(
    "spec",
    [
        {"mark": "point"},
        {
            "$schema": "https://example.com/schema/vega-lite/v6.json",
            "mark": "point",
        },
    ],
)
def test_vegalite_exporter_uses_unversioned_media_type_without_an_official_schema(
    spec: dict[str, object],
) -> None:
    assert vegalite(spec).media_type == "application/vnd.vegalite+json"


def test_vegalite_and_png_exporters_share_the_same_spec_input() -> None:
    pytest.importorskip("vl_convert")
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": "point",
        "data": {"values": [{"x": 1, "y": 2}]},
        "encoding": {
            "x": {"field": "x", "type": "quantitative"},
            "y": {"field": "y", "type": "quantitative"},
        },
    }

    assert vegalite(spec).media_type == "application/vnd.vegalite.v5+json"
    assert png(spec).payload.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("scale", ["2", True, 0, -1, float("inf")])
def test_png_exporter_requires_a_finite_positive_numeric_scale(scale: object) -> None:
    with pytest.raises(TypeError, match="finite positive"):
        png({}, scale=scale)


def test_parquet_exporter_validates_compression_before_loading_dependencies() -> None:
    with pytest.raises(TypeError, match="supported string or null"):
        parquet([], compression=42)
    with pytest.raises(ValueError, match="must be one of"):
        parquet([], compression="imaginary")
