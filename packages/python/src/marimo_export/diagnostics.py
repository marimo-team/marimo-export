"""Public diagnostics for marimo-export runtime capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from marimo_export._format import MAX_NAME_BYTES, MAX_PROVENANCE_BYTES, bounded_printable
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    json_object,
)
from marimo_export.errors import CompatibilityError


@dataclass(frozen=True, slots=True, init=False)
class CheckResult:
    """One named capability check and its bounded diagnostic facts."""

    name: str
    status: Literal["pass", "fail"]
    message: str
    _details_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        name: str,
        status: Literal["pass", "fail"],
        message: str,
        details: Mapping[str, JsonValue],
    ) -> None:
        if status not in {"pass", "fail"}:
            raise ValueError("check result status must be pass or fail")
        object.__setattr__(self, "name", bounded_printable(name, "check name", MAX_NAME_BYTES))
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "message",
            bounded_printable(message, "check message", MAX_PROVENANCE_BYTES),
        )
        object.__setattr__(
            self,
            "_details_bytes",
            canonical_bytes(json_object(details, "check details")),
        )

    @property
    def details(self) -> JsonObject:
        """Return a detached JSON-compatible copy of the check facts."""

        return decode_json_object(self._details_bytes, "check details")

    def to_dict(self) -> JsonObject:
        """Return the complete check as portable JSON data."""

        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


def marimo_compatibility() -> CheckResult:
    """Validate the installed Marimo release and private adapter contract."""

    try:
        details = _marimo_compatibility_details()
    except CompatibilityError as error:
        return CheckResult(
            name="marimo",
            status="fail",
            message=str(error),
            details={"code": error.code, "error": error.details},
        )
    except Exception as error:
        return CheckResult(
            name="marimo",
            status="fail",
            message="The Marimo compatibility check failed unexpectedly.",
            details={"exception_type": type(error).__name__},
        )
    return CheckResult(
        name="marimo",
        status="pass",
        message=f"Marimo {details['version']} matches the supported adapter.",
        details=details,
    )


def _marimo_compatibility_details() -> JsonObject:
    from marimo_export._marimo.composition import marimo_compatibility_details

    return marimo_compatibility_details()


__all__ = ["CheckResult", "marimo_compatibility"]
