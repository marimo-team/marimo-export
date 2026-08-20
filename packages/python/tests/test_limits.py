from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from marimo_export.errors import CaptureLimitError, TransportError
from marimo_export.limits import CaptureLimits


def test_capture_limits_are_immutable_positive_bounded_integers() -> None:
    limits = CaptureLimits(
        max_asset_bytes=1,
        max_closure_bytes=2,
    )

    assert limits.max_asset_bytes == 1
    assert limits.max_closure_bytes == 2
    with pytest.raises(FrozenInstanceError):
        limits.max_asset_bytes = 2  # ty: ignore[invalid-assignment]
    with pytest.raises(FrozenInstanceError):
        delattr(limits, "max_closure_bytes")

    defaults = CaptureLimits()
    for field, maximum in (
        ("max_asset_bytes", defaults.max_asset_bytes),
        ("max_closure_bytes", defaults.max_closure_bytes),
    ):
        values = {
            "max_asset_bytes": 1,
            "max_closure_bytes": 1,
        }
        for invalid in (True, 1.5, 0, -1, 2**53, maximum + 1):
            values[field] = invalid
            with pytest.raises((TypeError, ValueError), match=field):
                CaptureLimits(**values)  # type: ignore[arg-type]


def test_capture_limits_accept_the_library_ceilings() -> None:
    expected = CaptureLimits(max_asset_bytes=64 * 1024 * 1024, max_closure_bytes=512 * 1024 * 1024)

    assert CaptureLimits() == expected
    assert CaptureLimits(64 * 1024 * 1024, 512 * 1024 * 1024) == expected


def test_capture_limit_error_is_a_typed_transport_failure() -> None:
    error = CaptureLimitError("declared closure is too large")

    assert isinstance(error, TransportError)
    assert error.code == "capture_limit_exceeded"
