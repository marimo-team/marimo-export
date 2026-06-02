from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias

from moexport.spec import ExportSpec

SpecInput: TypeAlias = ExportSpec | Mapping[str, Any]


@dataclass(frozen=True)
class ScratchpadResult:
    """Result returned by a marimo scratchpad execution request."""

    stdout: list[str]
    stderr: list[str]
    output: Any | None = None


@dataclass(frozen=True)
class RuntimeInstall:
    """Runtime installation requested before export."""

    package: str
    module: str = "moexport"
    manager: str = "uv"
    source: str = "kernel"
    force: bool = False
    timeout_ms: int = 120_000
    poll_interval_ms: int = 1_000


@dataclass(frozen=True)
class SessionInfo:
    """Resolved marimo session used for export."""

    session_id: str
    name: str | None = None
    path: str | None = None
    initialization_id: str | None = None


@dataclass(frozen=True)
class ExportResult:
    """Static export bundle result returned by `ExportClient.export(...)`."""

    bundle_path: str
    manifest_path: str
    invocation_path: str
    invocation_index_path: str
    manifest: dict[str, Any]
    invocation: dict[str, Any]
    session_id: str
    session_name: str | None
    session_path: str | None
    session_initialization_id: str | None


@dataclass(frozen=True)
class ExportArchiveResult:
    """In-memory archive result returned by `ExportClient.archive(...)`."""

    bytes: bytes
    media_type: str
    session_id: str
    session_name: str | None
    session_path: str | None
    session_initialization_id: str | None
