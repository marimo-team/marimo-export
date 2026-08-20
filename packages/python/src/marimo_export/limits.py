"""Resource limits for producer capture operations."""

from __future__ import annotations

from dataclasses import dataclass

from marimo_export._limits import MAX_EXPORT_ASSET_BYTES, MAX_EXPORT_CLOSURE_BYTES

_MAX_SAFE_INTEGER = 2**53 - 1


@dataclass(frozen=True, slots=True)
class CaptureLimits:
    """Maximum declared asset and complete export sizes accepted by capture."""

    max_asset_bytes: int = MAX_EXPORT_ASSET_BYTES
    max_closure_bytes: int = MAX_EXPORT_CLOSURE_BYTES

    def __post_init__(self) -> None:
        _limit(self.max_asset_bytes, "max_asset_bytes", MAX_EXPORT_ASSET_BYTES)
        _limit(self.max_closure_bytes, "max_closure_bytes", MAX_EXPORT_CLOSURE_BYTES)


def _limit(value: object, name: str, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0 or value > _MAX_SAFE_INTEGER:
        raise ValueError(f"{name} must be a positive safe integer")
    if value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")


_DEFAULT_CAPTURE_LIMITS = CaptureLimits()


def _capture_limits(value: object) -> CaptureLimits:
    if value is None:
        return _DEFAULT_CAPTURE_LIMITS
    if not isinstance(value, CaptureLimits):
        raise TypeError("limits must be a CaptureLimits or None")
    return value


__all__ = ["CaptureLimits"]
