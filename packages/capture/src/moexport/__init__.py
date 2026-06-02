from pathlib import Path
from typing import Any

from moexport.archive import (
    EXPORT_ARCHIVE_MEDIA_TYPE,
    archive_bundle,
    emit_bundle_archive,
)
from moexport.client import ExportClient, ExportResult, RuntimeInstall
from moexport.export import CaptureResult, capture as _capture
from moexport.exporters import ExporterContext
from moexport.evaluate import evaluate
from moexport.notebook import capture_notebook
from moexport.query import open_export
from moexport.runtime import NotebookRuntime
from moexport.runtime import runtime as _runtime
from moexport.spec import ExportSpec, load_export_spec as load_spec
from moexport.spec import parse_export_spec as parse_spec

__all__ = [
    "EXPORT_ARCHIVE_MEDIA_TYPE",
    "CaptureResult",
    "ExportClient",
    "ExportResult",
    "ExportSpec",
    "ExporterContext",
    "NotebookRuntime",
    "RuntimeInstall",
    "archive_bundle",
    "capture",
    "capture_notebook",
    "emit_bundle_archive",
    "evaluate",
    "load_spec",
    "open_export",
    "parse_spec",
    "runtime",
]


async def capture(
    spec: Any,
    *,
    to: str | Path | None = None,
) -> CaptureResult:
    """Capture the active marimo runtime and write a static export bundle.

    ``spec`` accepts an ``ExportSpec`` instance or raw spec mapping. ``to``
    sets the output root. Returns a ``CaptureResult`` with the written
    manifest, bundle identity, source spec, and invocation trace.
    Requires a running marimo runtime, usually through ``marimo-export notebook``
    or a notebook cell.
    """

    return await _capture(spec, to=to)


def runtime() -> NotebookRuntime:
    """Return the active export scenario runtime.

    The runtime exposes read-only access to notebook definitions, UI elements,
    and cell outputs for the scenario currently being captured.
    """

    return _runtime()


def main() -> None:
    from moexport.cli import main as cli_main

    cli_main()
