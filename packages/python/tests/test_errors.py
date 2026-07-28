from __future__ import annotations

from typing import Any, cast

import pytest
from marimo_export.errors import (
    CaptureError,
    IntegrityError,
    MarimoExportError,
    ProjectionError,
    PublicationError,
    TransportError,
)


def test_errors_expose_stable_codes_and_json_wire_shape() -> None:
    error = TransportError("server unavailable", details={"status": 503})

    assert isinstance(error, MarimoExportError)
    assert error.code == "transport_error"
    assert error.wire() == {
        "code": "transport_error",
        "message": "server unavailable",
        "details": {"status": 503},
    }


def test_error_hierarchy_matches_capture_and_publication_boundaries() -> None:
    assert isinstance(ProjectionError("failed"), CaptureError)
    assert isinstance(IntegrityError("corrupt"), PublicationError)


def test_error_details_must_be_json_safe() -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        CaptureError("failed", details={"cause": object()})

    with pytest.raises(TypeError, match="must be an object"):
        CaptureError("failed", details=cast(Any, []))


def test_error_merges_validated_internal_details_without_reconstruction() -> None:
    error = CaptureError("capture failed", details={"phase": "projection"})
    restoration = {"failures": ["controls"]}

    error._merge_details({"restoration": restoration})
    restoration["failures"].append("cells")

    assert error.details == {
        "phase": "projection",
        "restoration": {"failures": ["controls"]},
    }
