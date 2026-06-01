"""Small built-in exporters for common JSON, text, and HTML payloads."""

from __future__ import annotations

import json as _json
from typing import Any

from pydantic import Field, TypeAdapter

from moexport.artifacts import Artifact, JsonObject
from moexport.exporters._core import ExporterContext, ExporterOptions
from moexport.jsonio import jsonable


class JsonOptions(ExporterOptions):
    filename: str = Field(default="data.json", description="Blob filename hint.")
    format_id: str = Field(default="json.v1", description="Artifact format id.")
    media_type: str = Field(
        default="application/json", description="Artifact MIME type."
    )
    metadata: JsonObject | None = Field(default=None, description="Artifact metadata.")


class TextOptions(ExporterOptions):
    filename: str = Field(default="data.txt", description="Blob filename hint.")
    format_id: str = Field(default="text.v1", description="Artifact format id.")
    media_type: str = Field(default="text/plain", description="Artifact MIME type.")
    metadata: JsonObject | None = Field(default=None, description="Artifact metadata.")


class HtmlOptions(ExporterOptions):
    filename: str = Field(default="data.html", description="Blob filename hint.")
    format_id: str = Field(default="html.v1", description="Artifact format id.")
    media_type: str = Field(default="text/html", description="Artifact MIME type.")
    metadata: JsonObject | None = Field(default=None, description="Artifact metadata.")


_JSON_OPTIONS = TypeAdapter(JsonOptions)
_TEXT_OPTIONS = TypeAdapter(TextOptions)
_HTML_OPTIONS = TypeAdapter(HtmlOptions)


def json(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
    """Export a JSON-compatible value as a content-addressed JSON blob."""

    parsed = _JSON_OPTIONS.validate_python(options)
    blob = ctx.write_blob(
        parsed.filename,
        _json.dumps(jsonable(value), allow_nan=False, indent=2).encode("utf-8"),
        media_type=parsed.media_type,
    )
    return ctx.artifact(
        format_id=parsed.format_id,
        media_type=parsed.media_type,
        files={"data": blob},
        entry="data",
        metadata=parsed.metadata,
    )


def text(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
    """Export a value's text representation as a content-addressed text blob."""

    parsed = _TEXT_OPTIONS.validate_python(options)
    blob = ctx.write_blob(
        parsed.filename,
        str(value).encode("utf-8"),
        media_type=parsed.media_type,
    )
    return ctx.artifact(
        format_id=parsed.format_id,
        media_type=parsed.media_type,
        files={"text": blob},
        entry="text",
        metadata=parsed.metadata,
    )


def html(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
    """Export HTML-like values, including marimo markdown/HTML outputs."""

    parsed = _HTML_OPTIONS.validate_python(options)
    html_text = value.text if hasattr(value, "text") else str(value)
    blob = ctx.write_blob(
        parsed.filename,
        html_text.encode("utf-8"),
        media_type=parsed.media_type,
    )
    return ctx.artifact(
        format_id=parsed.format_id,
        media_type=parsed.media_type,
        files={"html": blob},
        entry="html",
        metadata=parsed.metadata,
    )
