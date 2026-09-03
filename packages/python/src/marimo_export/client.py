from __future__ import annotations

import math
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from marimo_export._client_protocol import (
    _bridge_error,
    _cache_summary,
    _exact,
    _kernel_input_observation,
    _session_description,
    _state_run_timings,
    _transfer,
    _transfer_ticket,
)
from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._json import (
    sha256_bytes,
)
from marimo_export._remote import BridgeError, HttpKernelTransport, SessionInfo
from marimo_export.descriptors import OutputCodec
from marimo_export.errors import (
    ExecutionError,
    IntegrityError,
    SessionError,
)
from marimo_export.index import ExportIndex
from marimo_export.inspection import SessionDescription
from marimo_export.integration import KernelInputObservation
from marimo_export.result import CacheSummary, StateRunTimings
from marimo_export.spec import ExportSpec

if TYPE_CHECKING:
    from marimo_export.limits import CaptureLimits
    from marimo_export.planning import ExportPlan
    from marimo_export.prepared import PreparedExport
    from marimo_export.progress import ProgressEvent
    from marimo_export.repository import ExportRepository


class Session:
    """A live session handle bound to one `Client`."""

    __slots__ = ("_client", "_info")

    def __init__(self, client: Client, info: SessionInfo) -> None:
        self._client = client
        self._info = info

    @property
    def id(self) -> str:
        return self._info.id

    @property
    def filename(self) -> str | None:
        return self._info.filename

    @property
    def path(self) -> str | None:
        return self._info.path

    def inspect(self) -> SessionDescription:
        self._client._require_open()
        try:
            value = self._client._transport.invoke(self.id, "inspect", {})
        except BridgeError as error:
            raise _bridge_error(error) from error
        return _session_description(self._info, value)

    def observe_inputs(self) -> KernelInputObservation:
        """Return portable live input values and typed control bindings."""

        self._client._require_open()
        try:
            value = self._client._transport.invoke(self.id, "observe_inputs", {})
        except BridgeError as error:
            raise _bridge_error(error) from error
        return _kernel_input_observation(value)

    def _plan(self, spec: ExportSpec) -> Mapping[str, object]:
        """Return normalized planning data without executing export states."""

        self._client._require_open()
        if not isinstance(spec, ExportSpec):
            raise TypeError("spec must be an ExportSpec")
        try:
            return self._client._transport.invoke(
                self.id,
                "plan",
                {"spec": spec.to_value()},
            )
        except BridgeError as error:
            raise _bridge_error(error) from error

    def capture(
        self,
        *,
        spec: ExportSpec,
        repository: ExportRepository | None = None,
        timeout: float = 30.0,
        progress: Callable[[ProgressEvent], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PreparedExport:
        """Prepare a reusable export from this borrowed session."""

        from marimo_export._services.capture_export import capture_session

        return capture_session(
            self,
            spec=spec,
            repository=repository,
            timeout=timeout,
            progress=progress,
            cancelled=cancelled,
        )

    def plan(
        self,
        *,
        spec: ExportSpec,
        repository: ExportRepository | None = None,
        progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ExportPlan:
        """Resolve export work for this session without executing states."""

        from marimo_export._services.capture_export import plan_session

        return plan_session(
            self,
            spec=spec,
            repository=repository,
            progress=progress,
        )

    def _capture(self, spec: ExportSpec, limits: CaptureLimits) -> _CaptureData:
        """Capture verified export data before local commit."""

        capture_started = time.monotonic()
        self._client._require_open()
        if not isinstance(spec, ExportSpec):
            raise TypeError("spec must be an ExportSpec")
        ticket: str | None = None
        try:
            try:
                response = self._client._transport.invoke(
                    self.id,
                    "capture",
                    {"spec": spec.to_value()},
                )
            except BridgeError as error:
                raise _bridge_error(error) from error
            transfer_value = response.get("transfer")
            ticket = _transfer_ticket(transfer_value)
            _exact(
                response,
                {
                    "index",
                    "transfer",
                    "output_cache",
                    "notebook_cache",
                    "state_run_timings",
                },
                "capture response",
            )
            index = ExportIndex.from_value(response.get("index"))
            transfer = _transfer(transfer_value, index, limits)
            assets: dict[tuple[OutputCodec, str], bytes] = {}
            for item in transfer.assets:
                data = self._client._transport.download_asset(
                    self.id,
                    item.url,
                    item.size,
                )
                if len(data) != item.size:
                    raise IntegrityError("a transferred asset length disagrees with its descriptor")
                if sha256_bytes(data) != item.sha256:
                    raise IntegrityError("a transferred asset digest disagrees with its descriptor")
                assets[(item.codec, item.sha256)] = data
            output_cache = _cache_summary(
                response.get("output_cache"),
                "output cache",
                expected=len(index.states) * len(index.outputs),
            )
            notebook_cache = _cache_summary(
                response.get("notebook_cache"),
                "notebook cache",
            )
            state_run_timings = _state_run_timings(
                response.get("state_run_timings"),
                states=len(index.states),
            )
            live_document_sha256 = self.inspect().document_sha256
            if live_document_sha256 != index.notebook.document_sha256:
                raise ExecutionError(
                    "the parent notebook document changed during capture",
                    code="parent_document_changed",
                    details={
                        "before": index.notebook.document_sha256,
                        "after": live_document_sha256,
                    },
                )
            self._release(ticket)
            ticket = None
            return _CaptureData(
                index=index,
                assets=assets,
                output_cache=output_cache,
                notebook_cache=notebook_cache,
                state_run_timings=state_run_timings,
                capture_seconds=time.monotonic() - capture_started,
            )
        finally:
            if ticket is not None:
                primary = sys.exc_info()[1]
                try:
                    self._release(ticket)
                except Exception as cleanup_error:
                    if primary is None:
                        raise
                    record_cleanup_failure(primary, "transfer ticket cleanup", cleanup_error)

    def _release(self, ticket: str) -> None:
        try:
            self._client._transport.invoke(
                self.id,
                "release",
                {"ticket": ticket},
            )
        except BridgeError as error:
            raise _bridge_error(error) from error


class Client:
    """Authenticated client for borrowed marimo edit sessions."""

    __slots__ = ("_closed", "_transport")

    def __init__(
        self,
        server: str,
        *,
        access_token: str | None = None,
        server_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        _timeout(timeout)
        explicit_access = access_token
        explicit_server = server_token
        if explicit_access is None:
            explicit_access = os.environ.get("MARIMO_EXPORT_ACCESS_TOKEN")
        if explicit_server is None:
            explicit_server = os.environ.get("MARIMO_EXPORT_SERVER_TOKEN")
        self._transport = HttpKernelTransport(
            server,
            access_token=explicit_access,
            server_token=explicit_server,
            timeout=float(timeout),
        )
        self._closed = False

    def __enter__(self) -> Client:
        self._require_open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        del exc_info
        self.close()

    def sessions(self) -> tuple[Session, ...]:
        self._require_open()
        return tuple(Session(self, info) for info in self._transport.list_sessions())

    def session(self, session_id: str | None = None) -> Session:
        sessions = self.sessions()
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id:
                raise TypeError("session_id must be a non-empty string or None")
            for session in sessions:
                if session.id == session_id:
                    return session
            raise SessionError(
                f"session {session_id!r} was not found",
                code="session_not_found",
                details={"session_id": session_id},
            )
        if not sessions:
            raise SessionError("no live marimo session was found", code="session_not_found")
        if len(sessions) != 1:
            raise SessionError(
                "more than one live marimo session is available",
                code="session_ambiguous",
                details={"sessions": [session.id for session in sessions[:16]]},
            )
        return sessions[0]

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise SessionError("the client is closed", code="client_closed")


def capture(
    server: str,
    *,
    spec: ExportSpec,
    session: str,
    repository: ExportRepository | None = None,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 30.0,
    progress: Callable[[ProgressEvent], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedExport:
    """Prepare one existing Marimo session through the shared service."""

    from marimo_export._services.capture_export import capture as capture_export

    return capture_export(
        server,
        session=session,
        spec=spec,
        repository=repository,
        access_token=access_token,
        server_token=server_token,
        timeout=timeout,
        progress=progress,
        cancelled=cancelled,
    )


@dataclass(frozen=True, slots=True)
class _CaptureData:
    index: ExportIndex
    assets: Mapping[tuple[OutputCodec, str], bytes]
    output_cache: CacheSummary
    notebook_cache: CacheSummary
    state_run_timings: StateRunTimings
    capture_seconds: float
    server_start_seconds: float | None = None
    initial_autorun_seconds: float | None = None
    server_shutdown_seconds: float | None = None


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout must be a number")
    result = float(value)
    if result <= 0 or not math.isfinite(result):
        raise ValueError("timeout must be a positive finite number")
    return result


__all__ = [
    "Client",
    "Session",
    "capture",
]
