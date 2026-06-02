from __future__ import annotations

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from moexport.archive import EXPORT_ARCHIVE_MEDIA_TYPE
from moexport.client._code import (
    archive_code,
    export_code,
    marked_payload,
    marked_text,
    marker,
)
from moexport.client._runtime import ensure_runtime
from moexport.client._scratchpad import can_import, execute_scratchpad
from moexport.client._session import resolve_session
from moexport.client._types import (
    ExportArchiveResult,
    ExportResult,
    RuntimeInstall,
    SessionInfo,
    SpecInput,
)


@dataclass(frozen=True)
class ExportClient:
    """Drive static exports from one running marimo server."""

    server: str
    notebook: str | None = None
    session_id: str | None = None
    token: str | None = None
    runtime: Literal["preinstalled"] | RuntimeInstall = "preinstalled"

    def export(
        self,
        spec: SpecInput,
        *,
        to: str | Path | None = None,
        paths: Iterable[str | Path] = (),
        runtime: Literal["preinstalled"] | RuntimeInstall | None = None,
        timeout: int = 120,
    ) -> ExportResult:
        """Write a static export bundle from the resolved session."""

        session = self._session()
        self._ensure_runtime(session, runtime)
        export_marker = marker("EXPORT")
        result = execute_scratchpad(
            self.server,
            session.session_id,
            export_code(spec, to=to, paths=paths, marker=export_marker),
            token=self.token,
            timeout=timeout,
        )
        payload = marked_payload(result.stdout, export_marker)
        return _export_result(payload, session)

    def archive(
        self,
        spec: SpecInput,
        *,
        paths: Iterable[str | Path] = (),
        runtime: Literal["preinstalled"] | RuntimeInstall | None = None,
        timeout: int = 120,
    ) -> ExportArchiveResult:
        """Return an in-memory static export archive from the resolved session."""

        session = self._session()
        self._ensure_runtime(session, runtime)
        archive_marker = marker("ARCHIVE")
        result = execute_scratchpad(
            self.server,
            session.session_id,
            archive_code(spec, paths=paths, marker=archive_marker),
            token=self.token,
            timeout=timeout,
        )
        payload = marked_text(result.stdout, archive_marker)
        return _archive_result(base64.b64decode(payload), session)

    def _session(self) -> SessionInfo:
        return resolve_session(
            server=self.server,
            notebook=self.notebook,
            session_id=self.session_id,
            token=self.token,
        )

    def _ensure_runtime(
        self,
        session: SessionInfo,
        runtime: Literal["preinstalled"] | RuntimeInstall | None,
    ) -> None:
        requested = runtime if runtime is not None else self.runtime
        if requested == "preinstalled":
            if can_import(
                self.server,
                session.session_id,
                "moexport",
                token=self.token,
            ):
                return
            raise RuntimeError(
                "moexport is not importable in the target kernel. "
                "Pass RuntimeInstall(package=...) to install it before export."
            )
        if not isinstance(requested, RuntimeInstall):
            raise TypeError(
                "runtime must be 'preinstalled' or RuntimeInstall(package=...)"
            )
        ensure_runtime(
            server=self.server,
            session_id=session.session_id,
            package=requested.package,
            module=requested.module,
            manager=requested.manager,
            source=requested.source,
            force=requested.force,
            timeout_ms=requested.timeout_ms,
            poll_interval_ms=requested.poll_interval_ms,
            token=self.token,
        )


def _export_result(payload: dict[str, Any], session: SessionInfo) -> ExportResult:
    return ExportResult(
        bundle_path=_string_field(payload, "bundle_path"),
        manifest_path=_string_field(payload, "manifest_path"),
        invocation_path=_string_field(payload, "invocation_path"),
        invocation_index_path=_string_field(payload, "invocation_index_path"),
        manifest=_dict_field(payload, "manifest"),
        invocation=_dict_field(payload, "invocation"),
        session_id=session.session_id,
        session_name=session.name,
        session_path=session.path,
        session_initialization_id=session.initialization_id,
    )


def _archive_result(payload: bytes, session: SessionInfo) -> ExportArchiveResult:
    return ExportArchiveResult(
        bytes=payload,
        media_type=EXPORT_ARCHIVE_MEDIA_TYPE,
        session_id=session.session_id,
        session_name=session.name,
        session_path=session.path,
        session_initialization_id=session.initialization_id,
    )


def _string_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"export payload field {key!r} must be a string")
    return value


def _dict_field(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"export payload field {key!r} must be an object")
    return value
