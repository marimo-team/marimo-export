from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import cast

from marimo_export._diagnostics import safe_diagnostic
from marimo_export._execution import normalize_matrix
from marimo_export._json import JsonObject, canonical_bytes, decode_json_object
from marimo_export._marimo.compat import (
    declared_ui_values,
    execute_state,
    flush_native_caches,
    inspect_baseline,
    preflight_exporters,
    require_capabilities,
    runtime_path,
)
from marimo_export._marimo.transfer import create_ticket, release, sweep_expired
from marimo_export.errors import (
    ExecutionError,
    MarimoExportError,
    SessionError,
)
from marimo_export.publication import (
    CacheSummary,
    FreshChildTimings,
    NotebookProvenance,
    ProducerProvenance,
    PublicationIndex,
    StateEntry,
)
from marimo_export.spec import ExportSpec

BRIDGE_SCHEMA = "marimo-export.bridge.v1"
_OPERATIONS = frozenset({"inspect", "capture", "release"})
_MAX_REQUEST_BYTES = 8 * 1024 * 1024


async def dispatch_json(request_json: str) -> str:
    """Dispatch one strict request inside the attached marimo kernel."""

    request_id = _request_id_hint(request_json)
    operation = ""
    try:
        request = _decode_request(request_json)
        request_id = cast(str, request["request_id"])
        operation = cast(str, request["operation"])
        data = await _dispatch(operation, cast(JsonObject, request["params"]))
        response: JsonObject = {
            "schema": BRIDGE_SCHEMA,
            "request_id": request_id,
            "ok": True,
            "data": data,
        }
    except MarimoExportError as error:
        response = {
            "schema": BRIDGE_SCHEMA,
            "request_id": request_id,
            "ok": False,
            "error": error.wire(),
        }
    except (TypeError, ValueError) as error:
        response = {
            "schema": BRIDGE_SCHEMA,
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": "spec_invalid" if operation == "capture" else "session_error",
                "message": str(error),
            },
        }
    except Exception as error:
        response = {
            "schema": BRIDGE_SCHEMA,
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": "state_execution_failed" if operation == "capture" else "session_error",
                "message": safe_diagnostic(
                    "the kernel bridge could not complete ",
                    operation or "the request",
                    ": ",
                    type(error).__name__,
                ),
            },
        }
    return canonical_bytes(response).decode("utf-8")


async def _dispatch(operation: str, params: JsonObject) -> JsonObject:
    sweep_expired()
    if operation == "inspect":
        _exact(params, set(), "inspect params")
        return await _inspect()
    if operation == "capture":
        _exact(params, {"spec"}, "capture params")
        return await _capture(ExportSpec.from_value(params["spec"]))
    if operation == "release":
        _exact(params, {"ticket"}, "release params")
        ticket = params["ticket"]
        if not isinstance(ticket, str):
            raise SessionError("release ticket must be a string")
        return {"released": release(ticket)}
    raise SessionError(f"unsupported bridge operation: {operation}")


async def _inspect() -> JsonObject:
    capabilities = require_capabilities()
    baseline = await inspect_baseline()
    definitions: list[JsonObject] = []
    for definition in baseline.definitions.values():
        portable = False
        value_available = False
        value = None
        if definition.kind == "ui" and not definition.sensitive:
            portable = True
            value_available = True
            value = definition.frontend_value
        elif definition.kind == "ordinary":
            try:
                from marimo_export._json import json_value

                json_value(definition.value, f"definition {definition.name!r}")
                portable = True
            except (TypeError, ValueError):
                pass
        definitions.append(
            {
                "name": definition.name,
                "cell_id": definition.cell_id,
                "python_type": definition.python_type,
                "kind": definition.kind,
                "siblings": list(definition.siblings),
                "portable_input": portable,
                "sensitive": definition.sensitive,
                "value_available": value_available,
                "value": value,
                "domain": dict(definition.domain),
            }
        )
    return {
        "filename": baseline.filename,
        "path": runtime_path(),
        "document_sha256": baseline.document_sha256,
        "marimo_version": capabilities.version,
        "marimo_export_version": _package_version(),
        "capabilities": list(capabilities.names),
        "definitions": definitions,
    }


async def _capture(spec: ExportSpec) -> JsonObject:
    capabilities = require_capabilities()
    baseline = await inspect_baseline()
    plan = normalize_matrix(spec, baseline)
    ui_names = tuple(name for name in plan.inputs if baseline.definitions[name].kind == "ui")
    parent_ui = await declared_ui_values(ui_names)
    exporter_identities = preflight_exporters(plan)
    flush_native_caches()
    primary: BaseException | None = None
    receipts = []
    upstream_hits = 0
    upstream_misses = 0
    child_construction_seconds = 0.0
    upstream_execution_seconds = 0.0
    ui_application_seconds = 0.0
    projection_execution_seconds = 0.0
    child_cleanup_seconds = 0.0
    try:
        for state in plan.states:
            executed = await execute_state(state, plan, exporter_identities)
            receipts.extend(executed.receipts)
            upstream_hits += executed.upstream_cache.hits
            upstream_misses += executed.upstream_cache.misses
            child_construction_seconds += executed.timings.construction_seconds
            upstream_execution_seconds += executed.timings.upstream_execution_seconds
            ui_application_seconds += executed.timings.ui_application_seconds
            projection_execution_seconds += executed.timings.projection_execution_seconds
            child_cleanup_seconds += executed.timings.cleanup_seconds
    except BaseException as error:
        primary = error
    finally:
        consistency_error: BaseException | None = None
        try:
            after_ui = await declared_ui_values(ui_names)
            if after_ui != parent_ui:
                raise ExecutionError(
                    "the parent UI state changed during capture",
                    code="parent_state_changed",
                    details={"inputs": list(ui_names)},
                )
        except BaseException as error:
            consistency_error = error
        if primary is not None:
            if consistency_error is not None and isinstance(primary, MarimoExportError):
                primary._merge_details(
                    {
                        "parent_consistency": [
                            {
                                "code": getattr(
                                    consistency_error,
                                    "code",
                                    "parent_consistency_failed",
                                ),
                                "message": str(consistency_error),
                            }
                        ]
                    }
                )
            raise primary
        if consistency_error is not None:
            raise consistency_error

    expected_receipts = len(plan.states) * len(plan.outputs)
    if len(receipts) != expected_receipts:
        raise ExecutionError(
            "the matrix did not produce a complete output relation",
            code="cache_receipt_missing",
            details={"expected": expected_receipts, "actual": len(receipts)},
        )

    states: dict[str, StateEntry] = {}
    offset = 0
    for state in plan.states:
        state_receipts = receipts[offset : offset + len(plan.outputs)]
        offset += len(plan.outputs)
        states[state.name] = StateEntry(
            inputs=state.inputs,
            fingerprint=state.fingerprint,
            outputs={receipt.output: receipt.descriptor for receipt in state_receipts},
        )
    producer = ProducerProvenance(
        marimo=capabilities.version,
        marimo_export=_package_version(),
    )
    index = PublicationIndex(
        notebook=NotebookProvenance(
            filename=baseline.filename,
            document_sha256=baseline.document_sha256,
        ),
        producer=producer,
        inputs=plan.inputs,
        outputs=plan.outputs,
        states=states,
    )
    ticket = create_ticket(receipts)
    projection_cache = CacheSummary(
        hits=sum(receipt.disposition == "hit" for receipt in receipts),
        misses=sum(receipt.disposition == "miss" for receipt in receipts),
    )
    upstream_cache = CacheSummary(
        hits=upstream_hits,
        misses=upstream_misses,
    )
    fresh_children = FreshChildTimings(
        states=len(plan.states),
        construction_seconds=child_construction_seconds,
        upstream_execution_seconds=upstream_execution_seconds,
        ui_application_seconds=ui_application_seconds,
        projection_execution_seconds=projection_execution_seconds,
        cleanup_seconds=child_cleanup_seconds,
    )
    return {
        "index": index.to_value(),
        "transfer": ticket.wire(),
        "projection_cache": {
            "hits": projection_cache.hits,
            "misses": projection_cache.misses,
        },
        "upstream_cache": {
            "hits": upstream_cache.hits,
            "misses": upstream_cache.misses,
        },
        "fresh_child_timings": fresh_children.to_dict(),
    }


def _decode_request(request_json: str) -> JsonObject:
    if not isinstance(request_json, str):
        raise SessionError("bridge request must be a JSON string")
    if len(request_json.encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise SessionError("bridge request exceeds the request limit")
    try:
        request = decode_json_object(request_json, "bridge request")
    except (TypeError, ValueError) as error:
        raise SessionError(f"bridge request is invalid: {error}") from error
    _exact(
        request,
        {"schema", "client_version", "request_id", "operation", "params"},
        "bridge request",
    )
    if request["schema"] != BRIDGE_SCHEMA:
        raise SessionError("bridge request schema does not match the kernel")
    client_version = request["client_version"]
    if client_version != _package_version():
        raise SessionError(
            "marimo-export versions differ between the client and attached kernel",
            code="bridge_version_mismatch",
            details={
                "client_version": client_version,
                "kernel_version": _package_version(),
            },
        )
    request_id = request["request_id"]
    if not isinstance(request_id, str) or not request_id:
        raise SessionError("bridge request_id must be a non-empty string")
    operation = request["operation"]
    if operation not in _OPERATIONS:
        raise SessionError("bridge operation must be inspect, capture, or release")
    if not isinstance(request["params"], dict):
        raise SessionError("bridge params must be an object")
    return request


def _request_id_hint(request_json: object) -> str:
    if not isinstance(request_json, str):
        return "unknown"
    try:
        value = decode_json_object(request_json, "bridge request")
    except (TypeError, ValueError):
        return "unknown"
    request_id = value.get("request_id")
    return request_id if isinstance(request_id, str) and request_id else "unknown"


def _package_version() -> str:
    try:
        return version("marimo-export")
    except PackageNotFoundError:
        return "0.0.0"


def _exact(value: JsonObject, expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        pieces = []
        if missing:
            pieces.append("missing " + ", ".join(missing))
        if extra:
            pieces.append("unexpected " + ", ".join(extra))
        raise SessionError(f"{path} has invalid fields: {', '.join(pieces)}")


__all__ = ["BRIDGE_SCHEMA", "dispatch_json"]
