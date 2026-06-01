"""Display-output exporters for selected cells and reports."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field, TypeAdapter

from moexport.artifacts import Artifact, JsonObject
from moexport.exporters._core import ExporterContext, ExporterOptions
from moexport.snapshots import NotebookSnapshot, OutputSnapshot, format_output

DISPLAY_JSON_FORMAT = "marimo.display.v1"
DISPLAY_JSON_MEDIA_TYPE = "application/vnd.marimo.display+json"
MARKDOWN_FORMAT = "markdown.v1"
MARKDOWN_MEDIA_TYPE = "text/markdown"


class DisplayJsonOptions(ExporterOptions):
    filename: str = Field(default="display.json", description="Blob filename hint.")
    metadata: JsonObject | None = Field(default=None, description="Artifact metadata.")


class MarkdownOptions(ExporterOptions):
    filename: str = Field(default="report.md", description="Blob filename hint.")
    metadata: JsonObject | None = Field(default=None, description="Artifact metadata.")


_DISPLAY_JSON_OPTIONS = TypeAdapter(DisplayJsonOptions)
_MARKDOWN_OPTIONS = TypeAdapter(MarkdownOptions)


def display_json(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
    """Export display output records as JSON."""

    parsed = _DISPLAY_JSON_OPTIONS.validate_python(options)
    payload = _display_payload(value)
    blob = ctx.write_blob(
        parsed.filename,
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2).encode(
            "utf-8"
        ),
        media_type=DISPLAY_JSON_MEDIA_TYPE,
    )
    return ctx.artifact(
        format=DISPLAY_JSON_FORMAT,
        media_type=DISPLAY_JSON_MEDIA_TYPE,
        files={"display": blob},
        entry="display",
        metadata={
            **_display_metadata(payload),
            **(parsed.metadata or {}),
        },
    )


def markdown(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
    """Export selected display outputs as a Markdown report."""

    parsed = _MARKDOWN_OPTIONS.validate_python(options)
    payload = _display_payload(value)
    text = _markdown_payload(payload)
    blob = ctx.write_blob(
        parsed.filename,
        text.encode("utf-8"),
        media_type=MARKDOWN_MEDIA_TYPE,
    )
    return ctx.artifact(
        format=MARKDOWN_FORMAT,
        media_type=MARKDOWN_MEDIA_TYPE,
        files={"markdown": blob},
        entry="markdown",
        metadata={
            **_display_metadata(payload),
            **(parsed.metadata or {}),
        },
    )


def _display_payload(value: Any) -> JsonObject:
    if isinstance(value, NotebookSnapshot):
        return value.to_json()

    if isinstance(value, OutputSnapshot):
        return {
            "schema": DISPLAY_JSON_FORMAT,
            "version": 1,
            "kind": "output",
            "outputs": [value.to_json()],
        }

    output = format_output(value, on_error="record")
    return {
        "schema": DISPLAY_JSON_FORMAT,
        "version": 1,
        "kind": "output",
        "outputs": [] if output is None else [output.to_json()],
    }


def _display_metadata(payload: JsonObject) -> JsonObject:
    cells = payload.get("cells")
    if isinstance(cells, list):
        return {
            "kind": payload.get("kind"),
            "cell_count": len(cells),
            "output_count": sum(
                len(outputs)
                for cell in cells
                if isinstance(cell, dict)
                and isinstance(outputs := cell.get("outputs"), list)
            ),
        }
    outputs = payload.get("outputs")
    return {
        "kind": payload.get("kind"),
        "output_count": len(outputs) if isinstance(outputs, list) else 0,
    }


def _markdown_payload(payload: JsonObject) -> str:
    cells = payload.get("cells")
    if isinstance(cells, list):
        parts = []
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            label = cell.get("label") or cell.get("name") or cell.get("id")
            if label is not None:
                parts.append(f"## {label}\n")
            outputs = cell.get("outputs")
            if not isinstance(outputs, list):
                continue
            for output in outputs:
                if isinstance(output, dict):
                    parts.append(_markdown_output(output))
        return "\n".join(part for part in parts if part).strip() + "\n"

    outputs = payload.get("outputs")
    if isinstance(outputs, list):
        return (
            "\n".join(
                _markdown_output(output)
                for output in outputs
                if isinstance(output, dict)
            ).strip()
            + "\n"
        )
    return ""


def _markdown_output(output: JsonObject) -> str:
    mimetype = str(output.get("mimetype") or "")
    data = output.get("data")
    if mimetype in {"text/markdown", "text/plain"} and isinstance(data, str):
        return data
    if mimetype == "text/html" and isinstance(data, str):
        return data
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"


__all__ = [
    "DISPLAY_JSON_FORMAT",
    "DISPLAY_JSON_MEDIA_TYPE",
    "MARKDOWN_FORMAT",
    "MARKDOWN_MEDIA_TYPE",
    "display_json",
    "markdown",
]
