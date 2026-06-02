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
    force: bool = False


@dataclass(frozen=True)
class ExportResult:
    """Static export bundle result returned by `ExportClient.export(...)`."""

    bundle_path: str
    manifest_path: str
    invocation_path: str
    invocation_index_path: str
    manifest: dict[str, Any]
    invocation: dict[str, Any]
    session: dict[str, Any]
