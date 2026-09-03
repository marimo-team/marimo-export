from __future__ import annotations

import json
import subprocess
import sys

import marimo_export.diagnostics as diagnostics
import pytest
from marimo_export.diagnostics import CheckResult, marimo_compatibility
from marimo_export.errors import CompatibilityError


def test_marimo_compatibility_reports_the_pinned_adapter() -> None:
    result = marimo_compatibility()

    assert result.status == "pass"
    assert result.name == "marimo"
    assert result.details == {
        "adapter": "private",
        "release_commit": "854f7f2910b4bb4b6aebe650efc1f83ad40d9bef",
        "version": "0.24.0",
    }
    assert result.to_dict()["details"] == result.details


def test_marimo_compatibility_translates_supported_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> object:
        raise CompatibilityError(
            "release mismatch",
            code="marimo_incompatible",
            details={"expected": "0.24.0", "observed": "other"},
        )

    monkeypatch.setattr(diagnostics, "_marimo_compatibility_details", fail)

    result = marimo_compatibility()

    assert result == CheckResult(
        name="marimo",
        status="fail",
        message="release mismatch",
        details={
            "code": "marimo_incompatible",
            "error": {"expected": "0.24.0", "observed": "other"},
        },
    )


def test_marimo_compatibility_bounds_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> object:
        raise RuntimeError("private path or secret detail")

    monkeypatch.setattr(diagnostics, "_marimo_compatibility_details", fail)

    result = marimo_compatibility()

    assert result.status == "fail"
    assert result.message == "The Marimo compatibility check failed unexpectedly."
    assert result.details == {"exception_type": "RuntimeError"}


def test_diagnostics_import_defers_marimo_private_modules() -> None:
    script = """
import json
import sys
import marimo_export.diagnostics

print(json.dumps({
    "cache": "marimo._save.loaders" in sys.modules,
    "lifecycle": "marimo._runtime.executor.lifecycles.cached" in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {"cache": False, "lifecycle": False}
