from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from marimo_export._json import canonical_bytes, json_object
from marimo_export._marimo.compat import BlobAsset
from marimo_export.exporters._optional import optional

_SCHEMA_PATTERN = re.compile(
    r"^https://vega\.github\.io/schema/vega-lite/v(?P<major>[1-9]\d*)"
    r"(?:\.\d+)*\.json(?:[?#].*)?$"
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def vegalite(chart: object) -> BlobAsset:
    """Encode an Altair chart or Vega-Lite mapping as canonical JSON."""

    specification, major = _specification(chart)
    return BlobAsset(
        data=canonical_bytes(specification),
        media_type=f"application/vnd.vegalite.v{major}+json",
        filename=None,
        metadata={"schema_major": major},
    )


def png(
    chart: object,
    *,
    scale: float = 1.0,
) -> BlobAsset:
    """Render an Altair chart or Vega-Lite mapping as PNG."""

    if (
        isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not math.isfinite(scale)
        or scale <= 0
    ):
        raise TypeError("scale must be a positive finite number")
    specification, _ = _specification(chart)
    converter = optional("vl_convert", "charts")
    payload = converter.vegalite_to_png(specification, scale=float(scale))
    if not isinstance(payload, bytes) or not (
        payload.startswith(_PNG_SIGNATURE) and payload.endswith(_PNG_END)
    ):
        raise ValueError("Vega-Lite renderer returned an invalid PNG")
    return BlobAsset(
        data=payload,
        media_type="image/png",
        filename=None,
        metadata={"scale": float(scale)},
    )


def _specification(chart: object) -> tuple[dict[str, Any], int]:
    if isinstance(chart, Mapping):
        value = dict(chart)
    else:
        convert = getattr(chart, "to_dict", None)
        if not callable(convert):
            raise TypeError("chart must be an Altair chart or Vega-Lite mapping")
        value = convert()
    specification = json_object(value, "Vega-Lite specification")
    schema = specification.get("$schema")
    if not isinstance(schema, str):
        raise ValueError("Vega-Lite specification must declare $schema")
    match = _SCHEMA_PATTERN.fullmatch(schema)
    if match is None:
        raise ValueError("Vega-Lite $schema must contain a supported major version")
    return specification, int(match.group("major"))


__all__ = ["png", "vegalite"]
