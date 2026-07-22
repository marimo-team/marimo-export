from __future__ import annotations

from typing import Any

from marimo_export import Projection
from marimo_export._builtin_exporters import normalize_builtin_options
from marimo_export._marimo.anywidget import anywidget_payload
from marimo_export.projection.exporters._anywidget_payload import (
    validate_anywidget_payload,
)


def anywidget(value: Any, **options: Any) -> Projection:
    normalize_builtin_options("anywidget", options, "anywidget options")
    return _from_payload(anywidget_payload(value).payload)


def _from_payload(payload: bytes) -> Projection:
    validated = validate_anywidget_payload(payload)
    return Projection(
        payload,
        format_id="anywidget.v1",
        media_type="application/vnd.marimo-export.anywidget+json",
        metadata={
            "models": validated.model_count,
            "root_model_id": validated.root_model_id,
        },
    )
