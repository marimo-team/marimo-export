from __future__ import annotations

from typing import Any

import marimo

from marimo_export.exporters._registry import _normalize_options
from marimo_export.projection import Projection


def html(value: Any, **options: Any) -> Projection:
    _normalize_options("html", options, "html options")
    return html_from_text(marimo.as_html(value).text)


def html_from_text(value: str) -> Projection:
    """Create an HTML projection from text prepared in the notebook environment."""

    if not isinstance(value, str):
        raise TypeError("HTML projection text must be a string")
    return Projection(
        value.encode("utf-8"),
        format_id="html.v1",
        media_type="text/html; charset=utf-8",
    )
