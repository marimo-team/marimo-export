from __future__ import annotations

import importlib
import json as json_module
import sys
from types import ModuleType

import marimo as mo
import marimo_export.exporters as exporters
import pytest
from marimo_export.exporters._registry import _resolve_import
from marimo_export.exporters.bytes import bytes
from marimo_export.exporters.dataframe import arrow, parquet
from marimo_export.exporters.html import html
from marimo_export.exporters.json import json
from marimo_export.exporters.text import text
from marimo_export.exporters.vegalite import png, vegalite
from marimo_export.projection import Projection
from marimo_export.spec import ExportSpec


def test_exporters_package_has_no_barrel_surface() -> None:
    assert exporters.__all__ == []


def test_basic_exporters_produce_fixed_portable_formats() -> None:
    assert json({"b": 1, "a": 2}) == Projection(
        b'{"a": 2, "b": 1}',
        format_id="json.v1",
        media_type="application/json",
    )
    assert text(42) == Projection(
        b"42",
        format_id="text.v1",
        media_type="text/plain; charset=utf-8",
    )
    assert bytes(memoryview(b"value")) == Projection(
        b"value",
        format_id="bytes.v1",
        media_type="application/octet-stream",
    )
    assert html(mo.md("**hello**")).format_id == "html.v1"


def test_exporter_options_are_strict_and_canonical() -> None:
    with pytest.raises(TypeError, match="does not accept: media_type"):
        text("value", media_type="text/custom")
    with pytest.raises(TypeError, match="sort_keys"):
        json({}, sort_keys="false")
    with pytest.raises(ValueError, match="must be one of"):
        parquet([], compression="imaginary")
    with pytest.raises(TypeError, match="finite positive number"):
        png({}, scale=0)


@pytest.mark.parametrize(
    ("module_name", "function_name"),
    [
        ("marimo_export.exporters.anywidget", "anywidget"),
        ("marimo_export.exporters.bytes", "bytes"),
        ("marimo_export.exporters.dataframe", "arrow"),
        ("marimo_export.exporters.dataframe", "parquet"),
        ("marimo_export.exporters.html", "html"),
        ("marimo_export.exporters.json", "json"),
        ("marimo_export.exporters.text", "text"),
        ("marimo_export.exporters.vegalite", "png"),
        ("marimo_export.exporters.vegalite", "vegalite"),
    ],
)
def test_builtin_exporter_paths_resolve_to_callables(
    module_name: str,
    function_name: str,
) -> None:
    assert callable(getattr(importlib.import_module(module_name), function_name))


def test_unicode_import_reference_has_spec_and_runtime_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("módulo")

    def exportar_Δ(value: object) -> Projection:
        return Projection(
            str(value).encode(),
            format_id="unicode.v1",
            media_type="text/plain",
        )

    module.__dict__["exportar_Δ"] = exportar_Δ
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec = ExportSpec(
        {
            "schema": "marimo-export.spec.v1",
            "outputs": {
                "value": {
                    "source": "value",
                    "formats": {
                        "custom": {
                            "exporter": {
                                "import": "módulo:exportar_Δ",
                                "version": "1",
                            }
                        }
                    },
                }
            },
        }
    )

    reference = spec.outputs[0].formats[0].exporter.reference
    resolved = _resolve_import(reference, version="1")

    assert resolved.reference == "módulo:exportar_Δ"
    assert resolved.function is exportar_Δ


def test_optional_dataframe_exporters_emit_portable_formats() -> None:
    pyarrow = pytest.importorskip("pyarrow")
    table = pyarrow.table({"value": [1, 2]})

    arrow_projection = arrow(table)
    parquet_projection = parquet([{"value": 1}, {"value": 2}])

    assert arrow_projection.format_id == "dataframe.arrow.v1"
    assert arrow_projection.data
    assert parquet_projection.format_id == "dataframe.parquet.v1"
    assert parquet_projection.data.startswith(b"PAR1")
    assert parquet_projection.metadata == {
        "rows": 2,
        "columns": ["value"],
        "compression": "NONE",
    }


def test_vegalite_exporter_uses_schema_major_media_type() -> None:
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.1.0.json",
        "mark": "point",
        "data": {"values": [{"x": 1, "y": 2}]},
    }

    projection = vegalite(spec)

    assert json_module.loads(projection.data) == spec
    assert projection.format_id == "vegalite.v1"
    assert projection.media_type == "application/vnd.vegalite.v6+json"


def test_png_exporter_produces_png_bytes() -> None:
    pytest.importorskip("vl_convert")
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": "point",
        "data": {"values": [{"x": 1, "y": 2}]},
    }

    projection = png(spec, scale=2)

    assert projection.format_id == "vegalite.png.v1"
    assert projection.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert projection.metadata == {"scale": 2.0}
