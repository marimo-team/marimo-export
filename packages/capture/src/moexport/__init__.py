from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from moexport.archive import EXPORT_ARCHIVE_MEDIA_TYPE, archive_bundle
from moexport.evaluate import evaluate
from moexport.export import CaptureResult
from moexport.notebook import capture_notebook
from moexport.query import open_export
from moexport.spec import ExportSpec

if TYPE_CHECKING:
    from moexport.runtime import NotebookRuntime

__all__ = [
    "EXPORT_ARCHIVE_MEDIA_TYPE",
    "CaptureResult",
    "ExportSpec",
    "archive_bundle",
    "capture",
    "capture_notebook",
    "evaluate",
    "main",
    "open_export",
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

    from moexport.export import capture as capture_export

    return await capture_export(spec, to=to)


def runtime() -> NotebookRuntime:
    """Return the active export scenario runtime.

    The runtime exposes read-only access to notebook definitions, UI elements,
    and cell outputs for the scenario currently being captured.
    """

    from moexport.runtime import runtime as active_runtime

    return active_runtime()


def main() -> None:
    from moexport.cli import main as cli_main

    cli_main()
