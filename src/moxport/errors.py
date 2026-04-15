from __future__ import annotations


class MoxportError(RuntimeError):
    """Base exception for moxport."""


class ConnectionSpecError(MoxportError):
    """Raised when connect() is called without a usable selector."""


class SessionResolutionError(MoxportError):
    """Base exception for session resolution failures."""


class SessionNotFoundError(SessionResolutionError):
    """Raised when a requested session id does not exist."""


class NotebookNotFoundError(SessionResolutionError):
    """Raised when a requested notebook cannot be found in the workspace."""


class NotebookNotRunningError(SessionResolutionError):
    """Raised when a notebook exists in the workspace but has no active session."""


class SessionNotebookMismatchError(SessionResolutionError):
    """Raised when a session id and notebook name point to different notebooks."""


class NotebookParseError(MoxportError):
    """Raised when marimo-backed IR parsing fails."""


class ScratchpadProtocolError(MoxportError):
    """Raised when the scratchpad SSE stream is malformed."""


class ScratchpadExecutionError(MoxportError):
    """Raised when scratchpad execution completes with an error."""


class ExportError(MoxportError):
    """Raised when an export endpoint fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PackageOperationError(MoxportError):
    """Raised when package operations fail."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
