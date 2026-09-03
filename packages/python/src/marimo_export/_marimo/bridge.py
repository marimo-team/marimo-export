from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import cast

from marimo_export._diagnostics import safe_diagnostic
from marimo_export._execution import create_execution_plan
from marimo_export._identity import (
    ImplementationDriftError,
    require_implementation_stable,
)
from marimo_export._json import JsonObject, canonical_bytes, decode_json_object, json_equal
from marimo_export._marimo.capabilities import KernelAdapters, KernelRuntime
from marimo_export._marimo.composition import create_kernel_adapters
from marimo_export._marimo.transfer import create_ticket, release, sweep_expired
from marimo_export._services.identity import managed_runtime_source, runtime_producer_identity
from marimo_export.errors import (
    ExecutionError,
    MarimoExportError,
    SessionError,
)
from marimo_export.index import (
    ControlBinding,
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    StateEntry,
)
from marimo_export.result import CacheSummary, StateRunTimings
from marimo_export.spec import ExportSpec

BRIDGE_SCHEMA = "marimo-export.bridge.v1"
_OPERATIONS = frozenset(
    {"validate_baseline", "inspect", "observe_inputs", "plan", "capture", "release"}
)
_MAX_REQUEST_BYTES = 8 * 1024 * 1024


async def dispatch_json(request_json: str) -> str:
    """Dispatch one strict request inside the attached marimo kernel."""

    request_id = _request_id_hint(request_json)
    operation = ""
    try:
        request = _decode_request(request_json)
        request_id = cast(str, request["request_id"])
        operation = cast(str, request["operation"])
        data = await _dispatch(
            operation,
            cast(JsonObject, request["params"]),
            create_kernel_adapters(),
        )
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


async def _dispatch(
    operation: str,
    params: JsonObject,
    adapters: KernelAdapters,
) -> JsonObject:
    sweep_expired()
    if operation == "validate_baseline":
        _exact(params, set(), "validate_baseline params")
        adapters.kernel.validate_parent_state()
        return {"valid": True}
    if operation == "inspect":
        _exact(params, set(), "inspect params")
        return await _inspect(adapters.kernel)
    if operation == "observe_inputs":
        _exact(params, set(), "observe_inputs params")
        implementation_sha256 = _require_stable_implementation()
        result = cast(JsonObject, adapters.kernel.observe_inputs().to_value())
        if _require_stable_implementation() != implementation_sha256:
            raise SessionError(
                "marimo-export implementation changed during input observation",
                code="implementation_changed",
            )
        return result
    if operation == "plan":
        _exact(params, {"spec"}, "plan params")
        return await _plan(ExportSpec.from_value(params["spec"]), adapters.kernel)
    if operation == "capture":
        _exact(params, {"spec"}, "capture params")
        return await _capture(ExportSpec.from_value(params["spec"]), adapters)
    if operation == "release":
        _exact(params, {"ticket"}, "release params")
        ticket = params["ticket"]
        if not isinstance(ticket, str):
            raise SessionError("release ticket must be a string")
        return {"released": release(ticket)}
    raise SessionError(f"unsupported bridge operation: {operation}")


async def _plan(spec: ExportSpec, runtime: KernelRuntime) -> JsonObject:
    runtime.require_capabilities()
    implementation_sha256 = _require_stable_implementation()
    baseline = await runtime.inspect_baseline()
    plan = create_execution_plan(spec, baseline)
    producer_identity = runtime_producer_identity(
        document_sha256=baseline.document_sha256,
        source=managed_runtime_source(runtime.runtime_path()),
        filename=baseline.filename,
    )
    _require_stable_implementation()
    if plan.document_sha256 != baseline.document_sha256:
        raise ExecutionError(
            "the notebook document changed during export planning",
            code="parent_document_changed",
        )
    return {
        "default_alias": plan.default_alias,
        "default_fingerprint": plan.default_fingerprint,
        "document_sha256": plan.document_sha256,
        "inputs": list(plan.inputs),
        "output_plan_sha256": plan.output_plan_sha256,
        "outputs": list(plan.outputs),
        "environment_sha256": producer_identity.environment_sha256,
        "filename": producer_identity.filename,
        "producer": producer_identity.provenance.to_value(),
        "producer_sha256": producer_identity.producer_sha256,
        "source_sha256": producer_identity.source_sha256,
        "spec_sha256": plan.spec_sha256,
        "states": [
            {
                "aliases": list(state.aliases),
                "fingerprint": state.fingerprint,
                "inputs": dict(state.inputs),
            }
            for state in plan.states
        ],
        "implementation_sha256": implementation_sha256,
    }


async def _inspect(runtime: KernelRuntime) -> JsonObject:
    capabilities = runtime.require_capabilities()
    implementation_sha256 = _require_stable_implementation()
    baseline = await runtime.inspect_baseline()
    definitions: list[JsonObject] = []
    for definition in baseline.definitions.values():
        portable = definition.portable_input and not definition.sensitive
        value_available = definition.kind == "ui" and portable
        value = definition.frontend_value if value_available else None
        definitions.append(
            {
                "name": definition.name,
                "cell_id": definition.cell_id,
                "python_type": definition.python_type,
                "kind": definition.kind,
                "input_mode": "patch" if definition.ui_patch else "value",
                "siblings": list(definition.siblings),
                "portable_input": portable,
                "sensitive": definition.sensitive,
                "value_available": value_available,
                "value": value,
                "domain": dict(definition.domain),
                "control_paths": {
                    object_id: [step.to_value() for step in path]
                    for object_id, path in definition.control_paths.items()
                },
                "input_dependencies": list(definition.input_dependencies),
            }
        )
    result: JsonObject = {
        "filename": baseline.filename,
        "path": runtime.runtime_path(),
        "document_sha256": baseline.document_sha256,
        "marimo_version": capabilities.version,
        "marimo_export_version": _package_version(),
        "implementation_sha256": implementation_sha256,
        "capabilities": list(capabilities.names),
        "definitions": definitions,
        "cells": [
            {
                "id": cell.id,
                "name": cell.name,
                "code_sha256": cell.code_sha256,
                "config": dict(cell.config),
                "input_dependencies": list(cell.input_dependencies),
            }
            for cell in baseline.cells
        ],
    }
    _require_stable_implementation()
    return result


async def _capture(spec: ExportSpec, adapters: KernelAdapters) -> JsonObject:
    runtime = adapters.kernel
    capabilities = runtime.require_capabilities()
    implementation_sha256 = _require_stable_implementation()
    producer_identity = f"marimo:{capabilities.version}"
    baseline = await runtime.inspect_baseline()
    plan = create_execution_plan(spec, baseline)
    ui_names = tuple(name for name in plan.inputs if baseline.definitions[name].kind == "ui")
    parent_ui = await runtime.declared_ui_values(ui_names)
    receipts = []
    control_bindings: dict[str, ControlBinding] = {}
    notebook_hits = 0
    notebook_misses = 0
    state_setup_seconds = 0.0
    dependency_execution_seconds = 0.0
    ui_update_seconds = 0.0
    output_materialization_seconds = 0.0
    state_cleanup_seconds = 0.0
    with runtime.prepared_exporters(plan, baseline) as exporter_identities:
        runtime.flush_native_caches()
        primary: BaseException | None = None
        try:
            for state in plan.states:
                executed = await runtime.execute_state(
                    state,
                    plan,
                    exporter_identities,
                    implementation_sha256,
                    producer_identity,
                )
                receipts.extend(executed.receipts)
                _merge_control_bindings(control_bindings, executed.control_bindings)
                notebook_hits += executed.cache.authored_hits
                notebook_misses += executed.cache.authored_misses
                state_setup_seconds += executed.timings.setup_seconds
                dependency_execution_seconds += executed.timings.dependency_execution_seconds
                ui_update_seconds += executed.timings.ui_update_seconds
                output_materialization_seconds += executed.timings.output_materialization_seconds
                state_cleanup_seconds += executed.timings.cleanup_seconds
        except BaseException as error:
            primary = error
        finally:
            consistency_error: BaseException | None = None
            try:
                after_ui = await runtime.declared_ui_values(ui_names)
                if not json_equal(after_ui, parent_ui):
                    raise ExecutionError(
                        "the parent UI state changed during capture",
                        code="parent_state_changed",
                        details={"inputs": list(ui_names)},
                    )
                _require_stable_implementation()
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
            "state execution did not produce the complete output relation",
            code="cache_receipt_missing",
            details={"expected": expected_receipts, "actual": len(receipts)},
        )
    aliases: dict[str, str] = {}
    states: dict[str, StateEntry] = {}
    offset = 0
    for state in plan.states:
        state_receipts = receipts[offset : offset + len(plan.outputs)]
        offset += len(plan.outputs)
        states[state.fingerprint] = StateEntry(
            inputs=state.inputs,
            outputs={receipt.output: receipt.descriptor for receipt in state_receipts},
        )
        aliases.update((alias, state.fingerprint) for alias in state.aliases)
    producer = ProducerProvenance(
        marimo=capabilities.version,
        marimo_export=_package_version(),
        implementation_sha256=implementation_sha256,
    )
    index = ExportIndex(
        spec_sha256=plan.spec_sha256,
        default_state=plan.default_fingerprint,
        notebook=NotebookProvenance(
            filename=baseline.filename,
            document_sha256=baseline.document_sha256,
        ),
        producer=producer,
        inputs=plan.inputs,
        control_bindings=control_bindings,
        outputs=plan.outputs,
        aliases=aliases,
        states=states,
    )
    ticket = create_ticket(receipts, host=adapters.transfer)
    output_cache = CacheSummary(
        hits=sum(receipt.disposition == "hit" for receipt in receipts),
        misses=sum(receipt.disposition == "miss" for receipt in receipts),
    )
    notebook_cache = CacheSummary(
        hits=notebook_hits,
        misses=notebook_misses,
    )
    state_runs = StateRunTimings(
        states=len(plan.states),
        setup_seconds=state_setup_seconds,
        dependency_execution_seconds=dependency_execution_seconds,
        ui_update_seconds=ui_update_seconds,
        output_materialization_seconds=output_materialization_seconds,
        cleanup_seconds=state_cleanup_seconds,
    )
    return {
        "index": index.to_value(),
        "transfer": ticket.wire(),
        "output_cache": {
            "hits": output_cache.hits,
            "misses": output_cache.misses,
        },
        "notebook_cache": {
            "hits": notebook_cache.hits,
            "misses": notebook_cache.misses,
        },
        "state_run_timings": state_runs.to_dict(),
    }


def _merge_control_bindings(
    target: dict[str, ControlBinding],
    state_mapping: Mapping[str, ControlBinding],
) -> None:
    for object_id, binding in state_mapping.items():
        previous = target.setdefault(object_id, binding)
        if previous != binding:
            raise ExecutionError(
                f"UI object {object_id!r} changes input ownership across states",
                code="control_input_conflict",
                details={
                    "object_id": object_id,
                    "inputs": sorted({previous.input, binding.input}),
                },
            )


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
        {
            "schema",
            "client_version",
            "client_identity",
            "request_id",
            "operation",
            "params",
        },
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
    client_identity = request["client_identity"]
    kernel_identity = _require_stable_implementation()
    if (
        not isinstance(client_identity, str)
        or len(client_identity) != 64
        or any(character not in "0123456789abcdef" for character in client_identity)
        or client_identity != kernel_identity
    ):
        raise SessionError(
            "marimo-export implementations differ between the client and attached kernel",
            code="bridge_version_mismatch",
            details={
                "client_identity": client_identity,
                "kernel_identity": kernel_identity,
            },
        )
    request_id = request["request_id"]
    if not isinstance(request_id, str) or not request_id:
        raise SessionError("bridge request_id must be a non-empty string")
    operation = request["operation"]
    if operation not in _OPERATIONS:
        raise SessionError(
            "bridge operation must be validate_baseline, inspect, observe_inputs, plan, "
            "capture, or release"
        )
    if not isinstance(request["params"], dict):
        raise SessionError("bridge params must be an object")
    return request


def _require_stable_implementation() -> str:
    try:
        return require_implementation_stable()
    except ImplementationDriftError as error:
        raise SessionError(
            str(error),
            code="implementation_changed",
            details={"loaded": error.loaded, "current": error.current},
        ) from error


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
