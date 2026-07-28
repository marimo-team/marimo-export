from __future__ import annotations

from collections.abc import Mapping

from marimo_export._json import JsonObject, canonical_bytes, decode_json_object, json_object


class MarimoExportError(Exception):
    """Base class for errors exposed by marimo-export."""

    code: str = "marimo_export_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        if not isinstance(message, str) or not message:
            raise TypeError("error message must be a non-empty string")
        if code is not None:
            if not isinstance(code, str) or not code:
                raise TypeError("error code must be a non-empty string")
            self.code = code
        parsed_details = json_object(
            {} if details is None else details,
            "error.details",
        )
        self._details_bytes = canonical_bytes(parsed_details)
        super().__init__(message)

    @property
    def details(self) -> JsonObject:
        return decode_json_object(self._details_bytes, "error.details")

    def wire(self) -> JsonObject:
        error: JsonObject = {"code": self.code, "message": str(self)}
        details = self.details
        if details:
            error["details"] = details
        return error

    def _merge_details(self, details: Mapping[str, object]) -> None:
        merged = self.details
        merged.update(json_object(details, "error.details"))
        self._details_bytes = canonical_bytes(merged)


class SpecError(MarimoExportError):
    """The export specification is invalid."""

    code = "spec_invalid"


class TransportError(MarimoExportError):
    """A server request, stream, or bridge response failed."""

    code = "transport_failed"


class SessionError(MarimoExportError):
    """A running marimo session could not be selected or used."""

    code = "session_error"


class CaptureError(MarimoExportError):
    """The running notebook could not be captured."""

    code = "capture_error"


class SelectionError(CaptureError):
    """A requested global, expression, cell, or UI control is unavailable."""

    code = "selection_failed"


class ProjectionError(CaptureError):
    """An exporter could not produce the requested representation."""

    code = "output_execution_failed"


class TransferError(CaptureError):
    """A captured cache asset could not be transferred."""

    code = "integrity_failed"


class PublicationError(MarimoExportError):
    """A static publication is missing, malformed, or cannot be read."""

    code = "publication_invalid"


class IntegrityError(PublicationError):
    """A publication asset failed integrity or envelope validation."""

    code = "integrity_failed"


class CompatibilityError(MarimoExportError):
    """The installed marimo runtime lacks a required capability."""

    code = "marimo_incompatible"


class ExecutionError(MarimoExportError):
    """A notebook baseline or state could not execute."""

    code = "state_execution_failed"


class OutputError(ExecutionError):
    """A projected notebook output could not execute."""

    code = "output_execution_failed"


class CodecError(MarimoExportError):
    """A native cache return cannot enter the publication protocol."""

    code = "codec_invalid"


class StateUnavailableError(PublicationError):
    """A publication has no state for the requested complete input vector."""

    code = "state_unavailable"
