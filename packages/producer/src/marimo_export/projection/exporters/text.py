from __future__ import annotations

from typing import Any

from marimo_export import Projection
from marimo_export._builtin_exporters import normalize_builtin_options


def text(value: Any, **options: Any) -> Projection:
    normalize_builtin_options("text", options, "text options")
    return Projection(
        str(value).encode("utf-8"),
        format_id="text.v1",
        media_type="text/plain; charset=utf-8",
    )
