"""Whole-notebook exporters built from value snapshots, not HTML export."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field, TypeAdapter

from moexport.artifacts import Artifact, ArtifactData, JsonObject
from moexport.exporters._core import ExporterContext, ExporterOptions
from moexport.snapshots import NotebookSnapshot

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
    include_internal_cells: bool = Field(
        default=False,
        description="Include exporter-generated internal cells in the snapshot.",
    )


_OPTIONS = TypeAdapter(NotebookLinearOptions)


def linear(value: NotebookSnapshot, ctx: ExporterContext, **options: Any) -> Artifact:
    """Export `mox.runtime().snapshot()` as ordered cells plus MIME outputs."""

    parsed = _OPTIONS.validate_python(options)
    if not isinstance(value, NotebookSnapshot):
        raise TypeError("notebook.linear exporter expects `mox.runtime().snapshot()`")

    cells = value.cells
    if not parsed.include_internal_cells:
        cells = [cell for cell in cells if not _is_internal_cell(cell.to_json())]
    if not parsed.include_empty_outputs:
        cells = [cell for cell in cells if cell.outputs]

    snapshot: JsonObject = {
        "schema": NOTEBOOK_LINEAR_FORMAT,
        "version": 1,
        "kind": value.kind,
        "notebook": value.notebook,
        "cells": [cell.to_json() for cell in cells],
    }
    blob = ctx.write_blob(
        "notebook.json",
        json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        media_type=NOTEBOOK_LINEAR_MEDIA_TYPE,
    )

    return Artifact(
        format_id=NOTEBOOK_LINEAR_FORMAT,
        media_type=NOTEBOOK_LINEAR_MEDIA_TYPE,
        data=ArtifactData(files={"notebook": blob}, entry="notebook"),
        metadata={
            "name": value.notebook.get("name"),
            "cell_count": len(cells),
            "output_count": sum(len(cell.outputs) for cell in cells),
        },
    )


def _is_internal_cell(cell: Any) -> bool:
    if isinstance(cell, dict):
        return str(cell.get("name") or "").startswith("_moexport_")
    return False
