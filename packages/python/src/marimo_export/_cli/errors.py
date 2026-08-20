from __future__ import annotations

from marimo_export.errors import (
    CodecError,
    CompatibilityError,
    ExecutionError,
    ExportUnavailableError,
    IntegrityError,
    NotebookExportError,
    OutputError,
    SessionError,
    SpecError,
    TransportError,
)
from marimo_export.repository import RepositoryError

EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
EXIT_PLANNING = 4
EXIT_EXECUTION = 5
EXIT_INTEGRITY = 6
EXIT_REPOSITORY = 7
EXIT_INTERRUPT = 130
EXIT_BROKEN_PIPE = 141


def exit_code(error: BaseException, *, command: str) -> int | None:
    """Return the stable CLI category for one expected failure."""

    if isinstance(error, (TypeError, ValueError)):
        return EXIT_USAGE
    if isinstance(error, (TransportError, SessionError)):
        return EXIT_ENVIRONMENT
    if isinstance(error, (SpecError, CompatibilityError)):
        return EXIT_PLANNING
    if isinstance(error, ExportUnavailableError):
        return EXIT_REPOSITORY
    if isinstance(error, IntegrityError):
        return EXIT_INTEGRITY
    if isinstance(error, RepositoryError):
        return EXIT_REPOSITORY
    if isinstance(error, NotebookExportError):
        if error.code.startswith("destination_") or error.code == "export_commit_failed":
            return EXIT_REPOSITORY
        return EXIT_INTEGRITY
    if isinstance(error, (ExecutionError, OutputError, CodecError)):
        if error.code.startswith("server_"):
            return EXIT_ENVIRONMENT
        if error.code == "session_error":
            return EXIT_ENVIRONMENT
        if command == "plan":
            return EXIT_PLANNING
        if error.code in {
            "implementation_changed",
            "marimo_incompatible",
            "notebook_changed",
            "notebook_invalid",
            "parent_document_changed",
            "spec_invalid",
        }:
            return EXIT_PLANNING
        return EXIT_EXECUTION
    if isinstance(error, OSError):
        return EXIT_REPOSITORY
    return None


__all__ = [
    "EXIT_BROKEN_PIPE",
    "EXIT_ENVIRONMENT",
    "EXIT_EXECUTION",
    "EXIT_INTEGRITY",
    "EXIT_INTERRUPT",
    "EXIT_PLANNING",
    "EXIT_REPOSITORY",
    "EXIT_USAGE",
    "exit_code",
]
