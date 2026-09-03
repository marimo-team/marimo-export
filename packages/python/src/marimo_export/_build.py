from __future__ import annotations

from collections.abc import Callable

from marimo_export._delivery import preflight_export_destination
from marimo_export._services.prepare_export import prepare
from marimo_export.progress import ProgressEvent
from marimo_export.repository import ExportRepository
from marimo_export.result import ExportResult
from marimo_export.spec import ExportSpec, StrPath


def build(
    notebook: StrPath,
    *,
    spec: ExportSpec,
    output: StrPath,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    replace: bool = False,
    progress: Callable[[ProgressEvent], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ExportResult:
    """Prepare, write, and verify one notebook export."""

    preflight_export_destination(output, replace=replace)
    with prepare(
        notebook,
        spec=spec,
        repository=repository,
        timeout=timeout,
        progress=progress,
        cancelled=cancelled,
    ) as prepared:
        return prepared.write(output, replace=replace, progress=progress)


__all__ = ["build"]
