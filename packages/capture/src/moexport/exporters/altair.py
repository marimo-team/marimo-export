"""Altair chart exporters."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from pydantic import Field, TypeAdapter

from moexport.artifacts import Artifact, ArtifactData, JsonObject, JsonValue
from moexport.exporters._core import ExporterContext, ExporterOptions
from moexport.exporters._optional import import_optional

if TYPE_CHECKING:
    import altair as alt

PNG_FORMAT = "image.png.v1"
PNG_MEDIA_TYPE = "image/png"
VEGALITE_FORMAT = "vegalite.v1"
VEGALITE_MEDIA_TYPE = "application/vnd.vegalite+json"


class VegaLiteOptions(ExporterOptions):
    """Options for blob-backed Vega-Lite spec export."""


class PngOptions(ExporterOptions):
    """Options for PNG export through vl-convert."""

    scale: float = Field(
        default=1.0,
        gt=0,
        description="Scale factor passed to vl-convert when rendering PNG bytes.",
    )
    vl_version: str | None = Field(
        default=None,
        description="Optional Vega-Lite version passed through to vl-convert.",
    )


_VEGALITE_OPTIONS = TypeAdapter(VegaLiteOptions)
_PNG_OPTIONS = TypeAdapter(PngOptions)


def vegalite(
    value: "alt.TopLevelMixin",
    ctx: ExporterContext,
    **options: Any,
) -> Artifact:
    """Export an Altair chart as content-addressed Vega-Lite JSON."""

    _parse_vegalite_options(options)
    spec = _vegalite_spec(value)
    blob = ctx.write_blob(
        "chart.vl.json",
        _vegalite_json_bytes(spec),
        media_type=VEGALITE_MEDIA_TYPE,
    )

    return Artifact(
        format_id=VEGALITE_FORMAT,
        media_type=VEGALITE_MEDIA_TYPE,
        data=ArtifactData(files={"spec": blob}, entry="spec"),
        metadata=_metadata(spec),
    )


def png(
    value: "alt.TopLevelMixin",
    ctx: ExporterContext,
    **options: Any,
) -> Artifact:
    """Export an Altair chart as PNG bytes via vl-convert."""

    parsed = _parse_png_options(options)
    vl_convert = import_optional(
        "vl_convert",
        package="vl-convert-python",
        extra="altair",
        purpose="Altair PNG export",
    )
    spec = _vegalite_spec(value)
    kwargs: dict[str, Any] = {
        "vl_spec": _vegalite_json(spec),
        "scale": parsed.scale,
    }
    if parsed.vl_version is not None:
        kwargs["vl_version"] = parsed.vl_version

    png_bytes = vl_convert.vegalite_to_png(**kwargs)
    blob = ctx.write_blob("chart.png", png_bytes, media_type=PNG_MEDIA_TYPE)

    return Artifact(
        format_id=PNG_FORMAT,
        media_type=PNG_MEDIA_TYPE,
        data=ArtifactData(files={"image": blob}, entry="image"),
        metadata={
            "source_format": VEGALITE_FORMAT,
            "schema": _schema(spec),
            "scale": parsed.scale,
            "vl_version": parsed.vl_version,
        },
    )


def _vegalite_spec(value: "alt.TopLevelMixin") -> JsonObject:
    import_optional(
        "altair",
        package="altair",
        extra="altair",
        purpose="Altair export",
    )
    if not hasattr(value, "to_dict"):
        raise TypeError("Altair exporters require a chart-like value with to_dict().")

    spec = value.to_dict()
    json_value = cast(JsonValue, json.loads(json.dumps(spec)))
    if not isinstance(json_value, dict):
        raise TypeError("Altair chart did not produce a JSON object spec.")

    return json_value


def _metadata(spec: JsonObject) -> JsonObject:
    return {
        "schema": _schema(spec),
    }


def _vegalite_json(spec: JsonObject) -> str:
    return json.dumps(
        spec,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _vegalite_json_bytes(spec: JsonObject) -> bytes:
    return _vegalite_json(spec).encode("utf-8")


def _schema(spec: JsonObject) -> JsonValue:
    schema = spec.get("$schema")
    return schema if isinstance(schema, str) else None


def _parse_vegalite_options(options: dict[str, Any]) -> VegaLiteOptions:
    return _VEGALITE_OPTIONS.validate_python(options)


def _parse_png_options(options: dict[str, Any]) -> PngOptions:
    return _PNG_OPTIONS.validate_python(options)
