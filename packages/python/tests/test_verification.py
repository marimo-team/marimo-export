from __future__ import annotations

from pathlib import Path

import pytest
from marimo_export.reader import VerificationResult
from marimo_export.verification import verify_export


def test_verify_export_uses_the_public_reader_boundary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    expected = VerificationResult(states=2, outputs=4, assets=3, bytes_verified=128)

    class Opened:
        def verify(self) -> VerificationResult:
            return expected

    seen: list[object] = []

    def open_export(path: object) -> Opened:
        seen.append(path)
        return Opened()

    monkeypatch.setattr("marimo_export.verification.open_export", open_export)

    assert verify_export(tmp_path) is expected
    assert seen == [tmp_path]


def test_verification_result_rejects_boolean_counts() -> None:
    with pytest.raises(ValueError, match="states must be a nonnegative integer"):
        VerificationResult(states=True, outputs=0, assets=0, bytes_verified=0)  # type: ignore[arg-type]
