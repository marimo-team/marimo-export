"""Stable progress records emitted while preparing and writing exports."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from marimo_export._json import JsonObject

ProgressKind = Literal[
    "inspection_started",
    "plan_ready",
    "prepared_reused",
    "state_started",
    "state_finished",
    "prepared_committed",
    "write_finished",
]


@dataclass(frozen=True, slots=True)
class CacheActivity:
    """Marimo cache dispositions observed for export work that ran."""

    authored_hits: int = 0
    authored_misses: int = 0
    projection_hits: int = 0
    projection_misses: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("authored_hits", self.authored_hits),
            ("authored_misses", self.authored_misses),
            ("projection_hits", self.projection_hits),
            ("projection_misses", self.projection_misses),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"cache activity {name} must be a nonnegative integer")

    def to_dict(self) -> JsonObject:
        return {
            "authored_hits": self.authored_hits,
            "authored_misses": self.authored_misses,
            "projection_hits": self.projection_hits,
            "projection_misses": self.projection_misses,
        }


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One ordered preparation or export progress event."""

    kind: ProgressKind
    completed: int | None = None
    total: int | None = None
    state: str | None = None
    cache: CacheActivity | None = None
    elapsed_seconds: float | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {
            "inspection_started",
            "plan_ready",
            "prepared_reused",
            "state_started",
            "state_finished",
            "prepared_committed",
            "write_finished",
        }:
            raise ValueError("progress event kind is invalid")
        for name, value in (("completed", self.completed), ("total", self.total)):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"progress event {name} must be a nonnegative integer or None")
        if self.completed is not None and self.total is not None and self.completed > self.total:
            raise ValueError("progress event completed cannot exceed total")
        if self.state is not None and (not isinstance(self.state, str) or not self.state):
            raise ValueError("progress event state must be a nonempty string or None")
        if self.cache is not None and not isinstance(self.cache, CacheActivity):
            raise TypeError("progress event cache must be CacheActivity or None")
        if self.elapsed_seconds is not None and (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or self.elapsed_seconds < 0
            or not math.isfinite(self.elapsed_seconds)
        ):
            raise ValueError(
                "progress event elapsed_seconds must be a nonnegative finite number or None"
            )
        if self.message is not None and not isinstance(self.message, str):
            raise TypeError("progress event message must be a string or None")

    def to_dict(self) -> JsonObject:
        return {
            "kind": self.kind,
            "completed": self.completed,
            "total": self.total,
            "state": self.state,
            "cache": None if self.cache is None else self.cache.to_dict(),
            "elapsed_seconds": self.elapsed_seconds,
            "message": self.message,
        }


__all__ = ["CacheActivity", "ProgressEvent", "ProgressKind"]
