from __future__ import annotations

from typing import Any, cast

import pytest
from marimo_export.errors import (
    ExecutionError,
    IntegrityError,
    MarimoExportError,
    OutputError,
    PublicationError,
    TransportError,
)


def test_errors_expose_stable_codes_and_json_wire_shape() -> None:
    error = TransportError("server unavailable", details={"status": 503})

    assert isinstance(error, MarimoExportError)
    assert error.code == "transport_failed"
    assert error.wire() == {
        "code": "transport_failed",
        "message": "server unavailable",
        "details": {"status": 503},
    }


def test_error_hierarchy_matches_execution_and_publication_boundaries() -> None:
    assert isinstance(OutputError("failed"), ExecutionError)
    assert isinstance(IntegrityError("corrupt"), PublicationError)


def test_error_details_must_be_json_safe() -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        ExecutionError("failed", details={"cause": object()})

    with pytest.raises(TypeError, match="must be an object"):
        ExecutionError("failed", details=cast(Any, []))


def test_error_merges_validated_internal_details_without_reconstruction() -> None:
    error = ExecutionError("state failed", details={"phase": "execution"})
    restoration = {"failures": ["controls"]}

    error._merge_details({"restoration": restoration})
    restoration["failures"].append("cells")

    assert error.details == {
        "phase": "execution",
        "restoration": {"failures": ["controls"]},
    }
