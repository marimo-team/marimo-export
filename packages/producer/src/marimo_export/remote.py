from __future__ import annotations

import asyncio
import json

from marimo_export._json import JsonObject, canonical_bytes, json_object
from marimo_export._marimo.compat import require_supported_marimo
from marimo_export.errors import (
    IntegrityError,
    InvalidPlanError,
    ScenarioBuildError,
    UnsupportedMarimoError,
    UnsupportedProducerModeError,
)

PROTOCOL = "marimo-export.remote.v1"
RESPONSE_PREFIX = "__MARIMO_EXPORT_RESPONSE__:"


class RemoteError(Exception):
    def __init__(self, code: str, message: str, details: JsonObject | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


async def dispatch_json(request_json: str) -> str:
    request_id = "unknown"
    operation: str | None = None
    try:
        try:
            request = json_object(json.loads(request_json), "request")
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise RemoteError("invalid_request", str(error)) from error
        raw_request_id = request.get("request_id")
        if isinstance(raw_request_id, str) and raw_request_id:
            request_id = raw_request_id
        raw_operation = request.get("operation")
        if isinstance(raw_operation, str):
            operation = raw_operation
        response = await _dispatch(request)
        envelope: JsonObject = {
            "protocol": PROTOCOL,
            "request_id": request_id,
            "ok": True,
            "data": response,
        }
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        remote = _remote_error(error, operation)
        error_value: JsonObject = {"code": remote.code, "message": remote.message}
        if remote.details is not None:
            error_value["details"] = remote.details
        envelope = {
            "protocol": PROTOCOL,
            "request_id": request_id,
            "ok": False,
            "error": error_value,
        }
    return canonical_bytes(envelope).decode("utf-8")


async def _dispatch(request: JsonObject) -> JsonObject:
    if set(request) != {"protocol", "request_id", "operation", "params"}:
        raise RemoteError(
            "invalid_request",
            "request must contain protocol, request_id, operation, and params",
        )
    if request.get("protocol") != PROTOCOL:
        raise RemoteError("protocol_mismatch", f"expected protocol {PROTOCOL}")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise RemoteError("invalid_request", "request_id must be a non-empty string")
    operation = request.get("operation")
    try:
        params = json_object(request.get("params"), "request.params")
    except (TypeError, ValueError) as error:
        raise RemoteError("invalid_request", str(error)) from error
    if operation == "describe":
        _exact(params, set(), "describe params")
        require_supported_marimo()
        from marimo_export.worker import describe

        return describe()
    if operation == "build":
        _exact(params, {"plan"}, "build params")
        require_supported_marimo()
        from marimo_export.worker import build

        ref, receipt = await build(params.get("plan"))
        return {"ref": ref.wire(), "receipt": receipt.wire()}
    if operation == "stage":
        _exact(params, {"ref"}, "stage params")
        require_supported_marimo()
        from marimo_export._marimo.delivery import stage
        from marimo_export.index import ExportRef

        try:
            ref = ExportRef.from_wire(params.get("ref"))
        except (TypeError, ValueError) as error:
            raise RemoteError("invalid_ref", str(error)) from error
        return stage(ref)
    if operation == "release":
        _exact(params, {"id"}, "release params")
        require_supported_marimo()
        from marimo_export._marimo.delivery import release

        stage_id = params.get("id")
        if not isinstance(stage_id, str) or not stage_id:
            raise RemoteError("invalid_request", "release id must be a non-empty string")
        return {"released": release(stage_id)}
    raise RemoteError("invalid_request", f"unknown operation: {operation!r}")


def _exact(value: JsonObject, keys: set[str], label: str) -> None:
    if set(value) != keys:
        expected = ", ".join(sorted(keys)) if keys else "no fields"
        raise RemoteError("invalid_request", f"{label} must contain exactly {expected}")


def _remote_error(error: BaseException, operation: str | None) -> RemoteError:
    if isinstance(error, RemoteError):
        return error
    if isinstance(error, UnsupportedMarimoError):
        return RemoteError("unsupported_marimo", str(error))
    if isinstance(error, UnsupportedProducerModeError):
        return RemoteError("unsupported_mode", str(error))
    if isinstance(error, InvalidPlanError):
        return RemoteError("invalid_plan", str(error))
    if isinstance(error, ScenarioBuildError):
        return RemoteError(
            "scenario_failed",
            str(error),
            {"scenario_id": error.scenario_id},
        )
    if isinstance(error, IntegrityError):
        return RemoteError("integrity_failed", str(error))
    if isinstance(error, FileNotFoundError):
        return RemoteError("cache_read_failed", str(error))
    message = str(error) or type(error).__name__
    codes = {
        "describe": "describe_failed",
        "build": "build_failed",
        "stage": "stage_failed",
        "release": "release_failed",
    }
    code = codes.get(operation, "internal_error") if operation is not None else "internal_error"
    return RemoteError(code, message)
