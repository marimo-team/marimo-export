from __future__ import annotations

from pathlib import Path
from typing import Any

from moexport.archive import EXPORT_ARCHIVE_MEDIA_TYPE, archive_bundle
from moexport.export import CaptureResult
from moexport.notebook import capture_notebook
from moexport.spec import ExportSpec

__all__ = [
    "EXPORT_ARCHIVE_MEDIA_TYPE",
    "CaptureResult",
    "ExportSpec",
    "archive_bundle",
    "capture",
    "capture_notebook",
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


def main() -> None:
    from moexport.cli import main as cli_main

    cli_main()
