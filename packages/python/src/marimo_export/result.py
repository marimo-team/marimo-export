from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from marimo_export._format import (
    bounded_printable as _bounded_printable,
)
from marimo_export._format import (
    digest as _digest,
)
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    json_object,
)
from marimo_export.planning import ExportPlan
from marimo_export.progress import CacheActivity
from marimo_export.reader import VerificationResult

_MAX_DIAGNOSTIC_TEXT_BYTES = 2_048


@dataclass(frozen=True, slots=True)
class CacheSummary:
    """Final cache dispositions observed while creating one export."""

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
    """Verified write facts and preparation work for one notebook export."""

    path: Path
    identity: str
    plan: ExportPlan
    reused: bool
    prepared_states: tuple[str, ...]
    reused_states: tuple[str, ...]
    cache_activity: CacheActivity
    assets: int
    asset_bytes: int
    index_bytes: int
    verification: VerificationResult
    warnings: tuple[ExportWarning, ...] = ()
    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("export result path must be absolute")
        object.__setattr__(self, "identity", _digest(self.identity, "export result identity"))
        if not isinstance(self.plan, ExportPlan):
            raise TypeError("export result plan must be ExportPlan")
        if not isinstance(self.reused, bool):
            raise TypeError("export result reused must be a boolean")
        prepared = _result_fingerprints(self.prepared_states, "prepared_states")
        reused = _result_fingerprints(self.reused_states, "reused_states")
        if set(prepared) & set(reused):
            raise ValueError("prepared_states and reused_states must be disjoint")
        if set(prepared) | set(reused) != set(self.plan.state_fingerprints):
            raise ValueError("prepared_states and reused_states must cover the export plan")
        if self.reused != self.plan.exact_reuse:
            raise ValueError("export result reuse must match the export plan")
        if self.reused and prepared:
            raise ValueError("an exactly reused export cannot prepare states")
        object.__setattr__(self, "prepared_states", prepared)
        object.__setattr__(self, "reused_states", reused)
        if not isinstance(self.cache_activity, CacheActivity):
            raise TypeError("export result cache_activity must be CacheActivity")
        for name, value in (
            ("assets", self.assets),
            ("asset_bytes", self.asset_bytes),
            ("index_bytes", self.index_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.verification, VerificationResult):
            raise TypeError("export result verification must be VerificationResult")
        if (
            self.verification.states != len(self.plan.states)
            or self.verification.outputs != len(self.plan.states) * len(self.plan.outputs)
            or self.verification.assets != self.assets
            or self.verification.bytes_verified != self.asset_bytes
        ):
            raise ValueError("export result verification must match the written export")
        if any(not isinstance(warning, ExportWarning) for warning in self.warnings):
            raise TypeError("warnings must contain ExportWarning values")
        _validate_seconds(self.elapsed_seconds, "elapsed_seconds")

    def to_dict(self) -> JsonObject:
        return {
            "path": str(self.path),
            "identity": self.identity,
            "plan": self.plan.to_dict(),
            "reused": self.reused,
            "prepared_states": list(self.prepared_states),
            "reused_states": list(self.reused_states),
            "cache_activity": self.cache_activity.to_dict(),
            "assets": self.assets,
            "asset_bytes": self.asset_bytes,
            "index_bytes": self.index_bytes,
            "verification": self.verification.to_dict(),
            "warnings": [warning.to_dict() for warning in self.warnings],
            "elapsed_seconds": self.elapsed_seconds,
        }


def _result_fingerprints(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"export result {label} must be a tuple")
    parsed = tuple(_digest(value, f"export result {label} item") for value in values)
    if parsed != tuple(sorted(set(parsed))):
        raise ValueError(f"export result {label} must be sorted and unique")
    return parsed


__all__ = [
    "CacheSummary",
    "ExportResult",
    "ExportWarning",
    "PhaseTimings",
    "StateRunTimings",
]
