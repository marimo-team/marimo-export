"""Create and read verified exports of prepared marimo notebook results."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from marimo_export._build import build
    from marimo_export._marimo.blob import BlobAsset
    from marimo_export.client import Client, Session, capture
    from marimo_export.reader import NotebookExport, open_export
    from marimo_export.result import ExportResult
    from marimo_export.spec import ExportSpec, OutputSpec

_EXPORTS = {
    "BlobAsset": ("marimo_export._marimo.blob", "BlobAsset"),
    "Client": ("marimo_export.client", "Client"),
    "ExportResult": ("marimo_export.result", "ExportResult"),
    "ExportSpec": ("marimo_export.spec", "ExportSpec"),
    "NotebookExport": ("marimo_export.reader", "NotebookExport"),
    "OutputSpec": ("marimo_export.spec", "OutputSpec"),
    "Session": ("marimo_export.client", "Session"),
    "build": ("marimo_export._build", "build"),
    "capture": ("marimo_export.client", "capture"),
    "open_export": ("marimo_export.reader", "open_export"),
}

__all__ = (
    "BlobAsset",
    "Client",
    "ExportResult",
    "ExportSpec",
    "NotebookExport",
    "OutputSpec",
    "Session",
    "build",
    "capture",
    "open_export",
)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
