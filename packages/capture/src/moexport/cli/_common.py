"""Shared helpers for the Click CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from moexport.export import CaptureResult
from moexport.jsonio import manifest_value, pretty_json
from moexport.spec import ExportSpec, parse_export_spec_text

JsonObject = dict[str, Any]


def echo_json(value: object) -> None:
    click.echo(pretty_json(value))


def load_spec(spec: str) -> ExportSpec:
    text = sys.stdin.read() if spec == "-" else Path(spec).read_text(encoding="utf-8")
    return parse_export_spec_text(text, source=spec)


def state_filters(
    *,
    state_json: str | None,
    state: tuple[str, ...],
) -> JsonObject:
    filters: JsonObject = {}
    if state_json:
        parsed = json.loads(state_json)
        if not isinstance(parsed, dict):
            raise click.ClickException("--state-json must be a JSON object")
        filters.update(parsed)

    for item in state:
        key, separator, raw_value = item.partition("=")
        if not separator or not key:
            raise click.ClickException(
                f"state filter must use KEY=JSON syntax: {item!r}"
            )
        filters[key] = parse_jsonish(raw_value)
    return filters


def parse_jsonish(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def export_summary(result: CaptureResult) -> JsonObject:
    manifest = result.manifest
    root = str(Path(result.bundle_path).parent.parent)
    return {
        "status": "ok",
        "bundle_id": manifest["id"],
        "bundle_path": result.bundle_path,
        "manifest_path": result.manifest_path,
        "invocation_path": result.invocation_path,
        "invocation_index_path": result.invocation_index_path,
        "notebook": manifest["notebook"],
        "source_spec_sha256": manifest["provenance"].get("source_spec_sha256"),
        "values": manifest["values"],
        "scenarios": [
            {
                "id": scenario["id"],
                "state": scenario["state"],
                "values": {
                    value: sorted(formats)
                    for value, formats in scenario["values"].items()
                },
            }
            for scenario in manifest["scenarios"]
        ],
        "next": {
            "catalog": f"marimo-export query {root}",
            "scenarios": f"marimo-export query {root} scenarios",
            "entries": f"marimo-export query {root} entries --bundle {manifest['id']}",
            "formats": f"marimo-export query {root} formats --bundle {manifest['id']}",
            "files": f"marimo-export query {root} files --bundle {manifest['id']}",
        },
    }


def export_details(result: CaptureResult) -> JsonObject:
    """Return a JSON-safe diagnostic view of the full export result."""

    return manifest_value(result.model_dump(mode="python"))
