"""Whole-notebook exporters built from value snapshots, not HTML export."""

from __future__ import annotations

import json
from typing import Any, cast

from pydantic import Field, TypeAdapter

from moexport.artifacts import Artifact, ArtifactData, JsonObject, JsonValue
from moexport.exporters._core import ExporterContext, ExporterOptions
from moexport.runtime import NotebookRuntime

NOTEBOOK_LINEAR_FORMAT = "marimo.notebook.linear.v1"
NOTEBOOK_LINEAR_MEDIA_TYPE = "application/vnd.marimo.notebook.linear+json"


class NotebookLinearOptions(ExporterOptions):
    """Options for linear whole-notebook snapshot export."""

    include_source: bool = Field(
        default=True,
        description="Include authored source in each cell record.",
    )
    include_empty_outputs: bool = Field(
        default=False,
        description="Keep cells whose display output is empty.",
    )


_OPTIONS = TypeAdapter(NotebookLinearOptions)


def linear(value: NotebookRuntime, ctx: ExporterContext, **options: Any) -> Artifact:
    """Export `mox.runtime().snapshot()` as ordered cells plus MIME outputs."""

    parsed = _OPTIONS.validate_python(options)
    if not isinstance(value, NotebookRuntime):
        raise TypeError("notebook.linear exporter expects `mox.runtime().snapshot()`")

    cells = [
        _cell_record(cell, include_source=parsed.include_source)
        for cell in value.cells()
    ]
    if not parsed.include_empty_outputs:
        cells = [cell for cell in cells if _outputs(cell)]

    snapshot: JsonObject = {
        "schema": NOTEBOOK_LINEAR_FORMAT,
        "version": 1,
        "notebook": _json_object(value.notebook.metadata()),
        "cells": cast(JsonValue, cells),
    }
    blob = ctx.write_blob(
        "notebook.json",
        json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        media_type=NOTEBOOK_LINEAR_MEDIA_TYPE,
    )

    return Artifact(
        format=NOTEBOOK_LINEAR_FORMAT,
        media_type=NOTEBOOK_LINEAR_MEDIA_TYPE,
        data=ArtifactData(files={"notebook": blob}, entry="notebook"),
        metadata={
            "name": value.notebook.name,
            "cell_count": len(cells),
            "output_count": sum(len(_outputs(cell)) for cell in cells),
        },
    )


def _cell_record(
    cell: Any,
    *,
    include_source: bool,
) -> JsonObject:
    output = _format_output(cell.output)
    outputs: list[JsonValue] = [] if output is None else [output]
    record: JsonObject = {
        **_json_object(cell.metadata),
        "outputs": outputs,
    }
    if include_source:
        record["source"] = cell.source
    return record


def _outputs(cell: JsonObject) -> list[JsonValue]:
    outputs = cell.get("outputs")
    return outputs if isinstance(outputs, list) else []


def _format_output(value: Any) -> JsonObject | None:
    if value is None:
        return None

    from marimo._output.formatting import try_format

    formatted = try_format(value)
    data: JsonValue = _json_value(formatted.data)
    output: JsonObject = {
        "channel": "output",
        "mimetype": formatted.mimetype,
        "data": data,
    }
    if formatted.traceback is not None:
        output["traceback"] = formatted.traceback
    return output


def _json_value(value: Any) -> JsonValue:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _json_object(value: Any) -> JsonObject:
    serialized = _json_value(value)
    if not isinstance(serialized, dict):
        raise TypeError("expected a JSON object")
    return serialized
