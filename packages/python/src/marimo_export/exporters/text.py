from __future__ import annotations

from typing import Any

from marimo_export.exporters._registry import _normalize_options
from marimo_export.projection import Projection


def text(value: Any, **options: Any) -> Projection:
    _normalize_options("text", options, "text options")
    return Projection(
        str(value).encode("utf-8"),
        format_id="text.v1",
        media_type="text/plain; charset=utf-8",
    )
