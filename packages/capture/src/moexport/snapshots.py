"""Notebook display snapshots used by export sources and report exporters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from moexport.artifacts import JsonObject, JsonValue


@dataclass(frozen=True)
class OutputSnapshot:
    """Formatted output captured from one cell."""

    channel: str
    mimetype: str
    data: JsonValue
    traceback: JsonValue | None = None

    def to_json(self) -> JsonObject:
        record: JsonObject = {
            "channel": self.channel,
            "mimetype": self.mimetype,
            "data": self.data,
        }
        if self.traceback is not None:
            record["traceback"] = self.traceback
        return record


@dataclass(frozen=True)
class CellSnapshot:
    """A notebook cell with metadata and captured outputs."""

    index: int
    id: str
    name: str | None
    defs: list[str]
    refs: list[str]
    config: JsonObject
    source: str | None
    outputs: list[OutputSnapshot]
    label: str | None = None
    order: int | None = None

    def to_json(self) -> JsonObject:
        record = cast(
            JsonObject,
            {
                "index": self.index,
                "id": self.id,
                "name": self.name,
                "defs": self.defs,
                "refs": self.refs,
                "config": self.config,
                "outputs": [output.to_json() for output in self.outputs],
            },
        )
        if self.source is not None:
            record["source"] = self.source
        if self.label is not None:
            record["label"] = self.label
        if self.order is not None:
            record["order"] = self.order
        return record


@dataclass(frozen=True)
class NotebookSnapshot:
    """Ordered cell-output snapshot for a notebook or selected report."""

    schema: str
    version: int
    notebook: JsonObject
    cells: list[CellSnapshot]
    kind: str = "notebook"

    def to_json(self) -> JsonObject:
        return {
            "schema": self.schema,
            "version": self.version,
            "kind": self.kind,
            "notebook": self.notebook,
            "cells": [cell.to_json() for cell in self.cells],
        }


def format_output(value: Any, *, on_error: str = "raise") -> OutputSnapshot | None:
    """Format a live output into JSON-safe display data."""

    if value is None:
        return None

    from marimo._output.formatting import try_format

    try:
        formatted = try_format(value)
        data: JsonValue = json_value(formatted.data)
        traceback = (
            None if formatted.traceback is None else json_value(formatted.traceback)
        )
        return OutputSnapshot(
            channel="output",
            mimetype=formatted.mimetype,
            data=data,
            traceback=traceback,
        )
    except Exception as exc:
        if on_error != "record":
            raise
        return OutputSnapshot(
            channel="error",
            mimetype="application/vnd.marimo.export.error+json",
            data={
                "type": type(exc).__name__,
                "message": str(exc),
            },
        )


def json_value(value: Any) -> JsonValue:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def json_object(value: Any) -> JsonObject:
    serialized = json_value(value)
    if not isinstance(serialized, dict):
        raise TypeError("expected a JSON object")
    return serialized


__all__ = [
    "CellSnapshot",
    "NotebookSnapshot",
    "OutputSnapshot",
    "format_output",
    "json_object",
    "json_value",
]
