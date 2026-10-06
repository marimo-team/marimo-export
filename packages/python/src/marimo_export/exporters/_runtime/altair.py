from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from marimo_export._json import canonical_bytes, json_object
from marimo_export.outputs import BlobAsset
from marimo_export.values import _VEGA_LITE_SCHEMA, _is_vega_lite, _vega_lite_specification


def vegalite(chart: object) -> BlobAsset:
    specification, major = _specification(chart)
    return BlobAsset(
        data=canonical_bytes(specification),
        media_type=f"application/vnd.vegalite.v{major}+json",
        filename=None,
        metadata={"schema_major": major},
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
