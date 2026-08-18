from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    json_object,
)
from marimo_export._portable import validate_portable_basename
from marimo_export.export import (
    ProducerProvenance,
    _bounded_printable,
    _digest,
    _ordered_names,
)

_MAX_DIAGNOSTIC_TEXT_BYTES = 2_048


@dataclass(frozen=True, slots=True)
class CacheSummary:
    """Cache attempts observed while creating one export."""

    hits: int
    misses: int

    def __post_init__(self) -> None:
        for name, value in (("hits", self.hits), ("misses", self.misses)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"cache.{name} must be a non-negative integer")


def _validate_seconds(value: object, name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or not math.isfinite(value)
    ):
        suffix = " or null" if optional else ""
        raise ValueError(f"{name} must be a non-negative finite number{suffix}")


@dataclass(frozen=True, slots=True)
class StateRunTimings:
    """Aggregated timings for state execution phases."""

    states: int
    setup_seconds: float
    dependency_execution_seconds: float
    ui_update_seconds: float
    output_materialization_seconds: float
    cleanup_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.states, int) or isinstance(self.states, bool) or self.states < 0:
            raise ValueError("state_run_timings.states must be a non-negative integer")
        for name, value in (
            ("setup_seconds", self.setup_seconds),
            ("dependency_execution_seconds", self.dependency_execution_seconds),
            ("ui_update_seconds", self.ui_update_seconds),
            ("output_materialization_seconds", self.output_materialization_seconds),
            ("cleanup_seconds", self.cleanup_seconds),
        ):
            _validate_seconds(value, f"state_run_timings.{name}")

    def to_dict(self) -> JsonObject:
        return {
            "states": self.states,
            "setup_seconds": self.setup_seconds,
            "dependency_execution_seconds": self.dependency_execution_seconds,
            "ui_update_seconds": self.ui_update_seconds,
            "output_materialization_seconds": self.output_materialization_seconds,
            "cleanup_seconds": self.cleanup_seconds,
        }


@dataclass(frozen=True, slots=True)
class PhaseTimings:
    """Producer timings for build or capture."""

    total_seconds: float
    capture_seconds: float
    export_write_seconds: float
    state_runs: StateRunTimings
    server_start_seconds: float | None = None
    initial_autorun_seconds: float | None = None
    server_shutdown_seconds: float | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("total_seconds", self.total_seconds),
            ("capture_seconds", self.capture_seconds),
            ("export_write_seconds", self.export_write_seconds),
        ):
            _validate_seconds(value, f"timings.{name}")
        for name, value in (
            ("server_start_seconds", self.server_start_seconds),
            ("initial_autorun_seconds", self.initial_autorun_seconds),
            ("server_shutdown_seconds", self.server_shutdown_seconds),
        ):
            _validate_seconds(value, f"timings.{name}", optional=True)
        if not isinstance(self.state_runs, StateRunTimings):
            raise TypeError("timings.state_runs must be StateRunTimings")

    def to_dict(self) -> JsonObject:
        return {
            "total_seconds": self.total_seconds,
            "server_start_seconds": self.server_start_seconds,
            "initial_autorun_seconds": self.initial_autorun_seconds,
            "capture_seconds": self.capture_seconds,
            "server_shutdown_seconds": self.server_shutdown_seconds,
            "export_write_seconds": self.export_write_seconds,
            "state_runs": self.state_runs.to_dict(),
        }


@dataclass(frozen=True, slots=True, init=False)
class ExportWarning:
    """A recoverable producer warning returned after export commit."""

    code: Literal[
        "export_parent_sync_failed",
        "retired_destination_cleanup_failed",
    ]
    message: str
    _details_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        code: Literal[
            "export_parent_sync_failed",
            "retired_destination_cleanup_failed",
        ],
        message: str,
        details: Mapping[str, JsonValue],
    ) -> None:
        if code not in {
            "export_parent_sync_failed",
            "retired_destination_cleanup_failed",
        }:
            raise ValueError("export warning code is invalid")
        _bounded_printable(message, "export warning message", _MAX_DIAGNOSTIC_TEXT_BYTES)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        object.__setattr__(
            self,
            "_details_bytes",
            canonical_bytes(json_object(details, "export warning details")),
        )

    @property
    def details(self) -> JsonObject:
        return decode_json_object(self._details_bytes, "export warning details")

    def to_dict(self) -> JsonObject:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Durable export facts and run-local producer diagnostics."""

    path: Path
    mode: Literal["build", "capture"]
    session_id: str | None
    notebook_filename: str | None
    document_sha256: str
    producer: ProducerProvenance
    states: tuple[str, ...]
    outputs: tuple[str, ...]
    assets: int
    asset_bytes: int
    index_bytes: int
    output_cache: CacheSummary
    notebook_cache: CacheSummary
    timings: PhaseTimings
    warnings: tuple[ExportWarning, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("export result path must be absolute")
        if self.mode not in {"build", "capture"}:
            raise ValueError("export result mode must be build or capture")
        if self.mode == "build" and self.session_id is not None:
            raise ValueError("build results cannot name a borrowed session")
        if self.mode == "capture" and self.session_id is None:
            raise ValueError("capture results must name the borrowed session")
        if self.session_id is not None:
            _bounded_printable(self.session_id, "session_id", _MAX_DIAGNOSTIC_TEXT_BYTES)
        if self.notebook_filename is not None:
            validate_portable_basename(self.notebook_filename, "notebook_filename")
        _digest(self.document_sha256, "document_sha256")
        if not isinstance(self.producer, ProducerProvenance):
            raise TypeError("producer must be ProducerProvenance")
        _ordered_names(self.states, "states", identifier=False)
        _ordered_names(self.outputs, "outputs", identifier=False, nonempty=True)
        for name, value in (
            ("assets", self.assets),
            ("asset_bytes", self.asset_bytes),
            ("index_bytes", self.index_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.output_cache, CacheSummary):
            raise TypeError("output_cache must be CacheSummary")
        if self.output_cache.hits + self.output_cache.misses != len(self.states) * len(
            self.outputs
        ):
            raise ValueError("output cache activity must cover every state and output")
        if not isinstance(self.notebook_cache, CacheSummary):
            raise TypeError("notebook_cache must be CacheSummary")
        if not isinstance(self.timings, PhaseTimings):
            raise TypeError("timings must be PhaseTimings")
        if self.timings.state_runs.states != len(self.states):
            raise ValueError("state run timing count must match export states")
        managed = (
            self.timings.server_start_seconds,
            self.timings.initial_autorun_seconds,
            self.timings.server_shutdown_seconds,
        )
        if self.mode == "build" and any(value is None for value in managed):
            raise ValueError("build timings must include every managed server phase")
        if self.mode == "capture" and any(value is not None for value in managed):
            raise ValueError("capture timings cannot include managed server phases")
        if any(not isinstance(warning, ExportWarning) for warning in self.warnings):
            raise TypeError("warnings must contain ExportWarning values")

    def to_dict(self) -> JsonObject:
        return {
            "path": str(self.path),
            "mode": self.mode,
            "session_id": self.session_id,
            "notebook_filename": self.notebook_filename,
            "document_sha256": self.document_sha256,
            "producer": self.producer.to_value(),
            "states": list(self.states),
            "outputs": list(self.outputs),
            "assets": self.assets,
            "asset_bytes": self.asset_bytes,
            "index_bytes": self.index_bytes,
            "output_cache": {
                "hits": self.output_cache.hits,
                "misses": self.output_cache.misses,
            },
            "notebook_cache": {
                "hits": self.notebook_cache.hits,
                "misses": self.notebook_cache.misses,
            },
            "timings": self.timings.to_dict(),
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


__all__ = [
    "CacheSummary",
    "ExportResult",
    "ExportWarning",
    "PhaseTimings",
    "StateRunTimings",
]
