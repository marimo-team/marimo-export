"""Create and read verified exports of prepared marimo notebook results."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from marimo_export._identity import implementation_identity as _implementation_identity

_LOADED_SOURCE_IDENTITY = _implementation_identity()

if TYPE_CHECKING:
    from marimo_export._build import build
    from marimo_export._services.capture_export import capture
    from marimo_export._services.plan_export import plan
    from marimo_export._services.prepare_export import prepare
    from marimo_export.planning import ExportPlan
    from marimo_export.prepared import PreparedExport
    from marimo_export.progress import ProgressEvent
    from marimo_export.reader import NotebookExport, open_export
    from marimo_export.repository import ExportRepository
    from marimo_export.result import ExportResult
    from marimo_export.spec import ExportSpec, OutputSpec, StateSpace
    from marimo_export.verification import VerificationResult, verify_export

_EXPORTS = {
    "ExportPlan": ("marimo_export.planning", "ExportPlan"),
    "ExportRepository": ("marimo_export.repository", "ExportRepository"),
    "ExportResult": ("marimo_export.result", "ExportResult"),
    "ExportSpec": ("marimo_export.spec", "ExportSpec"),
    "NotebookExport": ("marimo_export.reader", "NotebookExport"),
    "OutputSpec": ("marimo_export.spec", "OutputSpec"),
    "PreparedExport": ("marimo_export.prepared", "PreparedExport"),
    "ProgressEvent": ("marimo_export.progress", "ProgressEvent"),
    "StateSpace": ("marimo_export.spec", "StateSpace"),
    "VerificationResult": ("marimo_export.verification", "VerificationResult"),
    "build": ("marimo_export._build", "build"),
    "capture": ("marimo_export._services.capture_export", "capture"),
    "open_export": ("marimo_export.reader", "open_export"),
    "plan": ("marimo_export._services.plan_export", "plan"),
    "prepare": ("marimo_export._services.prepare_export", "prepare"),
    "verify_export": ("marimo_export.verification", "verify_export"),
}

__all__ = (
    "ExportPlan",
    "ExportRepository",
    "ExportResult",
    "ExportSpec",
    "NotebookExport",
    "OutputSpec",
    "PreparedExport",
    "ProgressEvent",
    "StateSpace",
    "VerificationResult",
    "build",
    "capture",
    "open_export",
    "plan",
    "prepare",
    "verify_export",
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
