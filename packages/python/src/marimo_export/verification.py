"""Verify the complete file closure of a notebook export."""

from __future__ import annotations

from marimo_export.reader import VerificationResult, open_export
from marimo_export.spec import StrPath


def verify_export(path: StrPath) -> VerificationResult:
    """Verify every declared asset and return the verified closure counts."""

    return open_export(path).verify()


__all__ = ["VerificationResult", "verify_export"]
