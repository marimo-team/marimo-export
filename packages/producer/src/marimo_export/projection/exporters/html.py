from __future__ import annotations

from typing import Any

import marimo

from marimo_export import Projection
from marimo_export._builtin_exporters import normalize_builtin_options
from marimo_export._marimo.html import prepare_html_projection


def html(value: Any, **options: Any) -> Projection:
    normalize_builtin_options("html", options, "html options")
    payload = prepare_html_projection(marimo.as_html(value).text).encode("utf-8")
    return Projection(
        payload,
        format_id="html.v1",
        media_type="text/html; charset=utf-8",
    )
