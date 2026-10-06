from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from marimo_export._json import canonical_bytes, json_object
from marimo_export.errors import OutputError
from marimo_export.exporters._optional import optional
from marimo_export.outputs import BlobAsset
from marimo_export.values import (
    _VEGA_LITE_SCHEMA,
    RepresentationError,
    _is_vega_lite,
    _vega_lite_specification,
    represent,
)


def vegalite(chart: object) -> BlobAsset:
    specification, major = _specification(chart)
    return BlobAsset(
        data=canonical_bytes(specification),
        media_type=f"application/vnd.vegalite.v{major}+json",
        filename=None,
        metadata={"schema_major": major},
    )


def png(chart: object, *, scale: float = 1.0) -> BlobAsset:
    specification, _ = _specification(chart)
    optional("vl_convert", "charts")
    try:
        image = represent(specification, ["image/png"], scale=scale)
    except RepresentationError as error:
        raise OutputError(str(error), code="output_execution_failed") from error
    size = {"width": image.width, "height": image.height}
    return BlobAsset(
        data=image.data,
        media_type=image.media_type,
        filename=None,
        metadata={"scale": float(scale)}
        | {name: pixels for name, pixels in size.items() if pixels is not None},
    )


def _specification(chart: object) -> tuple[dict[str, Any], int]:
    if not isinstance(chart, Mapping) and not _is_vega_lite(chart):
        raise TypeError("chart must be an Altair chart or Vega-Lite mapping")
    specification = json_object(_vega_lite_specification(chart), "Vega-Lite specification")
    schema = specification.get("$schema")
    if not isinstance(schema, str):
        raise ValueError("Vega-Lite specification must declare $schema")
    match = _VEGA_LITE_SCHEMA.fullmatch(schema)
    if match is None:
        raise ValueError("Vega-Lite $schema must contain a supported major version")
    return specification, int(match.group("major"))
