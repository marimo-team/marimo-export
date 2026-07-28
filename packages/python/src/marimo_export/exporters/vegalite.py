from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from marimo_export._json import json_value
from marimo_export.exporters._optional import optional
from marimo_export.exporters._registry import _normalize_options
from marimo_export.projection import Projection

_SCHEMA_PATTERN = re.compile(
    r"^https://vega\.github\.io/schema/vega-lite/v(?P<major>[1-9]\d*)"
    r"(?:\.\d+)*\.json(?:[?#].*)?$"
)
_UNVERSIONED_MEDIA_TYPE = "application/vnd.vegalite+json"


def vegalite(value: Any, **options: Any) -> Projection:
    _normalize_options("vegalite", options, "vegalite options")
    spec = value.to_dict() if hasattr(value, "to_dict") else value
    normalized_spec = json_value(spec)
    payload = json.dumps(normalized_spec, allow_nan=False, sort_keys=True).encode("utf-8")
    return Projection(
        payload,
        format_id="vegalite.v1",
        media_type=_media_type(normalized_spec),
    )


def png(value: Any, **options: Any) -> Projection:
    normalized = _normalize_options("png", options, "png options")
    scale_value = normalized["scale"]
    assert isinstance(scale_value, (int, float))
    scale = float(scale_value)
    converter = optional("vl_convert", "png")
    spec = value.to_dict() if hasattr(value, "to_dict") else value
    payload = converter.vegalite_to_png(spec, scale=scale)
    return Projection(
        payload,
        format_id="vegalite.png.v1",
        media_type="image/png",
        metadata={"scale": scale},
    )


def _media_type(spec: object) -> str:
    if not isinstance(spec, Mapping):
        return _UNVERSIONED_MEDIA_TYPE
    schema = spec.get("$schema")
    if not isinstance(schema, str):
        return _UNVERSIONED_MEDIA_TYPE
    match = _SCHEMA_PATTERN.fullmatch(schema)
    if match is None:
        return _UNVERSIONED_MEDIA_TYPE
    return f"application/vnd.vegalite.v{match.group('major')}+json"
