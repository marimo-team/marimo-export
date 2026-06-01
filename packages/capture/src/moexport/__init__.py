from pathlib import Path
from typing import Any

from moexport.archive import (
    EXPORT_ARCHIVE_MEDIA_TYPE,
    archive_bundle,
    emit_bundle_archive,
)
from moexport.blobs import ContentAddressedBlobStore
from moexport.exporters import BundleExporterContext, ExporterContext
from moexport.evaluate import evaluate
from moexport.export import ExportResult, export as _export
from moexport.notebook import (
    NotebookDefs,
    NotebookRunOptions,
    NotebookSource,
    export_notebook,
    inspect_notebook_defs,
    read_notebook_source,
)
from moexport.runtime import RuntimeCell, NotebookRuntime, RuntimeNotebook
from moexport.runtime import runtime as _runtime
from moexport.query import BundleQuery, ExportQuery, open_export
from moexport.spec import ExportSpec, load_export_spec, parse_export_spec

__all__ = [
    "BundleQuery",
    "BundleExporterContext",
    "RuntimeCell",
    "ContentAddressedBlobStore",
    "EXPORT_ARCHIVE_MEDIA_TYPE",
    "ExporterContext",
    "ExportSpec",
    "ExportResult",
    "ExportQuery",
    "NotebookRuntime",
    "NotebookRunOptions",
    "NotebookDefs",
    "RuntimeNotebook",
    "NotebookSource",
    "archive_bundle",
    "emit_bundle_archive",
    "evaluate",
    "export",
    "export_notebook",
    "inspect_notebook_defs",
    "load_export_spec",
    "open_export",
    "parse_export_spec",
    "read_notebook_source",
    "runtime",
]


async def export(
    spec: Any,
    *,
    bundle: str | Path | None = None,
) -> ExportResult:
    """Capture the active marimo runtime and write a static export bundle.

    ``spec`` accepts an ``ExportSpec`` instance or raw spec mapping. ``bundle``
    overrides the output root from the spec. Returns an ``ExportResult`` with
    the written manifest, bundle identity, source spec, and invocation trace.
    Requires a live marimo runtime, usually through ``marimo-export notebook``
    or a notebook cell.
    """

    return await _export(spec, bundle=bundle)


def runtime() -> NotebookRuntime:
    """Return the active export scenario runtime.

    The runtime exposes read-only access to notebook definitions, UI elements,
    and cell outputs for the scenario currently being captured.
    """

    return _runtime()


def main() -> None:
    print("moexport capture")
