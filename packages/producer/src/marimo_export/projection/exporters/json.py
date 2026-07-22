from __future__ import annotations

import dataclasses
import json as json_module
from typing import Any

from marimo_export import Projection
from marimo_export._builtin_exporters import normalize_builtin_options
from marimo_export._json import json_value


def json(value: Any, **options: Any) -> Projection:
    normalized = normalize_builtin_options("json", options, "json options")
    indent = normalized["indent"]
    sort_keys = normalized["sort_keys"]
    assert indent is None or isinstance(indent, int)
    assert isinstance(sort_keys, bool)
    payload = json_module.dumps(
        _jsonable(value),
        allow_nan=False,
        ensure_ascii=False,
        indent=indent,
        sort_keys=sort_keys,
    ).encode("utf-8")
    return Projection(payload, format_id="json.v1", media_type="application/json")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
    return json_value(value)
