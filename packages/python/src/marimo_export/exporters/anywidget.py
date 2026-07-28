from __future__ import annotations

import builtins
from typing import Any

from marimo_export.exporters._anywidget_payload import (
    validate_anywidget_payload,
)
from marimo_export.exporters._registry import _normalize_options
from marimo_export.projection import Projection


def anywidget(value: Any, **options: Any) -> Projection:
    _normalize_options("anywidget", options, "anywidget options")
    if isinstance(value, memoryview):
        value = value.tobytes()
    elif isinstance(value, bytearray):
        value = builtins.bytes(value)
    if not isinstance(value, builtins.bytes):
        raise TypeError("anywidget exporter requires a canonical AnyWidget payload")
    return anywidget_from_payload(value)


def anywidget_from_payload(payload: bytes) -> Projection:
    """Validate canonical AnyWidget bytes and create their projection."""

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
