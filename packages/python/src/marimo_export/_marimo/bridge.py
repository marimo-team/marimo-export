from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import cast

from marimo._code_mode import get_context as get_code_mode_context
from marimo._messaging.cell_output import CellOutput

from marimo_export._json import (
    JsonObject,
    canonical_bytes,
    decode_json_object,
    json_equal,
    json_object,
    json_value,
)
from marimo_export.errors import (
    CaptureError,
    MarimoExportError,
    ProjectionError,
    SelectionError,
    SessionError,
    SpecError,
    TransferError,
)
from marimo_export.exporters._registry import (
    _BUILTIN_EXPORTERS,
    _builtin_exporter,
    _BuiltinExporter,
    _resolve_builtin,
    _resolve_import,
    _resolve_variable,
    _ResolvedExporter,
)
from marimo_export.publication import PublicationIndex
from marimo_export.spec import ExportSpec, FormatSpec, Source, decode_spec

from . import code_mode
from .cache import CacheAssetReceipt, project_and_cache
from .compat import MarimoCapabilities, require_capabilities
from .transfer import create_ticket, release, sweep_expired

BRIDGE_SCHEMA = "marimo-export.bridge.v1"

_MAX_REQUEST_BYTES = 8 * 1024 * 1024
_MAX_SAFE_INTEGER = 2**53 - 1
_MAX_REPORTED_CONTROL_MISMATCHES = 64
_OPERATIONS = frozenset({"inspect", "capture", "release"})


@dataclass(frozen=True, slots=True)
class _RestorationReport:
    failures: tuple[JsonObject, ...]
    expected_controls: JsonObject
    best_known_controls: JsonObject
    controls_observed_after_cleanup: bool

    def wire(self) -> JsonObject:
        return json_object(
            {
                "failures": list(self.failures),
                "expected_controls": self.expected_controls,
                "best_known_controls": self.best_known_controls,
                "controls_observed_after_cleanup": self.controls_observed_after_cleanup,
            },
            "restoration report",
        )


async def dispatch_json(request_json: str) -> str:
    """Dispatch one strict bridge request inside the attached kernel."""

    request_id = _request_id_hint(request_json)
    operation = ""
    try:
        request = _decode_request(request_json)
        request_id = cast(str, request["request_id"])
        operation = cast(str, request["operation"])
        params = cast(JsonObject, request["params"])
        sweep_expired()
        data = await _dispatch(operation, params)
        response: JsonObject = {
            "schema": BRIDGE_SCHEMA,
            "request_id": request_id,
            "ok": True,
            "data": data,
        }
    except MarimoExportError as error:
        response = _error_response(
            request_id,
            error.code,
            str(error),
            details=error.details,
        )
    except (TypeError, ValueError) as error:
        code = "spec_error" if not operation else f"{operation}_error"
        response = _error_response(request_id, code, str(error))
    except Exception:
        code = "capture_error" if operation == "capture" else "session_error"
        response = _error_response(
            request_id,
            code,
            f"the kernel bridge could not complete {operation or 'the request'}",
        )
    return canonical_bytes(response).decode("utf-8")


async def _dispatch(operation: str, params: JsonObject) -> JsonObject:
    if operation == "inspect":
        _exact(params, set(), "inspect params")
        capabilities = require_capabilities()
        inspection = await code_mode.inspect_live()
        return json_object(
            {
                **inspection.wire(),
                "marimo_version": capabilities.version,
                "marimo_export_version": _package_version(),
                "builtin_exporters": _builtin_exporters(),
            },
            "inspect result",
        )
    if operation == "capture":
        _exact(
            params,
            {"maximum_index_bytes", "maximum_publication_bytes", "spec"},
            "capture params",
        )
        spec = decode_spec(params["spec"])
        maximum_index_bytes = _positive_safe_integer(
            params["maximum_index_bytes"],
            "capture params.maximum_index_bytes",
        )
        maximum_publication_bytes = _positive_safe_integer(
            params["maximum_publication_bytes"],
            "capture params.maximum_publication_bytes",
        )
        return await _capture(
            spec,
            require_capabilities(),
            maximum_index_bytes=maximum_index_bytes,
            maximum_publication_bytes=maximum_publication_bytes,
        )
    if operation == "release":
        _exact(params, {"ticket"}, "release params")
        ticket = _nonempty_string(params["ticket"], "release params.ticket")
        return {"released": release(ticket)}
    raise SessionError(f"unsupported bridge operation: {operation}")


async def _capture(
    spec: ExportSpec,
    capabilities: MarimoCapabilities,
    *,
    maximum_index_bytes: int,
    maximum_publication_bytes: int,
) -> JsonObject:
    before = await code_mode.inspect_live()
    published_control_names = _published_control_names(spec)
    _validate_variant_controls(before, published_control_names)
    sources = tuple(output.source for output in spec.outputs)
    code_mode.preflight_named_sources(sources, before)
    # Freeze exporter callables before UI updates so variant reruns cannot
    # redefine the representation contract partway through capture.
    resolved_exporters = await _preflight_exporters(spec)
    control_names = {control.name for control in before.controls}
    sensitive_control_names = frozenset(
        control.name for control in before.controls if control.sensitive
    )
    snapshot = await code_mode.snapshot_controls(control_names)
    cell_state = code_mode.snapshot_cell_state()
    receipts: list[CacheAssetReceipt] = []
    variants: JsonObject = {}
    hits = 0
    misses = 0
    skipped = 0
    ticket_id: str | None = None
    cleanup_attempted = False
    best_known_controls = json_object(snapshot.values, "captured controls")

    try:
        for variant in sorted(spec.variants, key=lambda item: item.name):
            baseline = await code_mode.restore_controls(snapshot, sources)
            best_known_controls = json_object(snapshot.values, "restored controls")
            applied_vector: JsonObject = dict(snapshot.values)
            applied_vector.update(variant.controls)
            applied = await code_mode.apply_controls(variant.controls, sources)
            best_known_controls = dict(applied_vector)
            fresh_outputs = dict(baseline.outputs)
            fresh_outputs.update(applied.outputs)
            variant_outputs: JsonObject = {}
            async with get_code_mode_context() as context:
                for output in spec.outputs:
                    source = code_mode.resolve_source(
                        output.source,
                        context,
                        fresh_outputs=fresh_outputs,
                    )
                    formats: JsonObject = {}
                    for format_spec in output.formats:
                        exporter = resolved_exporters[(output.name, format_spec.name)]
                        source_value, exporter = _prepare_specialized_source(
                            source,
                            output.source,
                            format_spec,
                            exporter,
                        )
                        try:
                            receipt = await project_and_cache(
                                source_value,
                                exporter,
                                format_spec.options,
                            )
                        except MarimoExportError:
                            raise
                        except Exception as error:
                            raise ProjectionError(
                                f"output {output.name!r} format "
                                f"{format_spec.name!r} failed: {error}"
                            ) from error
                        receipts.append(receipt)
                        hits += int(receipt.disposition == "hit")
                        misses += int(receipt.disposition == "miss")
                        skipped += int(receipt.disposition == "skipped")
                        metadata = receipt.blob.metadata
                        if set(metadata) != {"format_id", "metadata_json"}:
                            raise ProjectionError(
                                "the cached BlobAsset metadata envelope is invalid"
                            )
                        format_id = _nonempty_string(
                            metadata["format_id"],
                            "BlobAsset.metadata.format_id",
                        )
                        metadata_json = metadata["metadata_json"]
                        if not isinstance(metadata_json, bytes):
                            raise ProjectionError("BlobAsset.metadata.metadata_json must be bytes")
                        public_metadata = decode_json_object(
                            metadata_json,
                            "BlobAsset.metadata.metadata_json",
                        )
                        media_type = _nonempty_string(
                            receipt.blob.media_type,
                            "BlobAsset.media_type",
                        )
                        formats[format_spec.name] = json_object(
                            {
                                "format_id": format_id,
                                "media_type": media_type,
                                "metadata": public_metadata,
                                "asset": receipt.asset.wire(),
                            },
                            f"output {output.name!r} format {format_spec.name!r}",
                        )
                    variant_outputs[output.name] = {"formats": formats}
            variants[variant.name] = {
                "controls": _select_control_values(
                    applied_vector,
                    published_control_names,
                ),
                "outputs": variant_outputs,
            }

        restoration = await _restore_capture_state(
            snapshot,
            cell_state,
            sources,
            control_names,
            published_control_names,
            sensitive_control_names,
            best_known_controls,
        )
        cleanup_attempted = True
        if restoration.failures:
            raise CaptureError(
                "could not restore notebook state after capture",
                details={"restoration": restoration.wire()},
            )
        after = await code_mode.inspect_live()
        if after.notebook.document_sha256 != before.notebook.document_sha256:
            raise CaptureError("the notebook document changed during capture")

        index = PublicationIndex.from_wire(
            {
                "schema": "marimo-export.publication.v1",
                "asset_codec": "marimo.blob-asset.msgpack.v1",
                "notebook": {
                    "filename": _portable_filename(before.notebook.filename),
                    "document_sha256": before.notebook.document_sha256,
                },
                "producer": {
                    "marimo": capabilities.version,
                    "marimo_export": _package_version(),
                },
                "variants": variants,
            }
        )
        index_size = len(index.to_bytes())
        if index_size > maximum_index_bytes:
            raise CaptureError(
                "publication index exceeds maximum_index_bytes",
                details={"size": index_size, "limit": maximum_index_bytes},
            )
        _validate_publication_size(
            index_size,
            receipts,
            maximum_publication_bytes,
        )
        ticket = create_ticket(receipts)
        ticket_id = ticket.id
        result = ticket.wire()
        result["index"] = index.wire()
        result["cache"] = {
            "hits": hits,
            "misses": misses,
            "skipped": skipped,
        }
        return result
    except BaseException as error:
        restoration: _RestorationReport | None = None
        if not cleanup_attempted:
            restoration = await _restore_capture_state(
                snapshot,
                cell_state,
                sources,
                control_names,
                published_control_names,
                sensitive_control_names,
                best_known_controls,
            )
        if ticket_id is not None:
            with suppress(BaseException):
                release(ticket_id)
        if restoration is not None and restoration.failures:
            enriched = _with_restoration(error, restoration)
            raise enriched from error
        raise


def _validate_publication_size(
    index_size: int,
    receipts: Iterable[CacheAssetReceipt],
    maximum_publication_bytes: int,
) -> None:
    if index_size > maximum_publication_bytes:
        raise TransferError(
            "publication exceeds maximum_publication_bytes",
            details={
                "limit": maximum_publication_bytes,
                "accounted_bytes": 0,
                "index_size": index_size,
            },
        )
    total = index_size
    seen: set[str] = set()
    for receipt in receipts:
        asset = receipt.asset
        if asset.key in seen:
            continue
        seen.add(asset.key)
        if asset.size > maximum_publication_bytes - total:
            raise TransferError(
                "publication exceeds maximum_publication_bytes",
                details={
                    "limit": maximum_publication_bytes,
                    "accounted_bytes": total,
                    "asset": asset.key,
                    "asset_size": asset.size,
                },
            )
        total += asset.size


async def _restore_capture_state(
    snapshot: code_mode.ControlSnapshot,
    cell_state: code_mode.CellStateSnapshot,
    sources: tuple[Source, ...],
    control_names: set[str],
    published_control_names: frozenset[str],
    sensitive_control_names: frozenset[str],
    fallback_controls: Mapping[str, object],
) -> _RestorationReport:
    failures: list[JsonObject] = []
    expected_controls = _select_control_values(
        snapshot.values,
        published_control_names,
    )
    best_known_controls = _select_control_values(
        fallback_controls,
        published_control_names,
    )

    try:
        await code_mode.restore_controls(snapshot, sources)
        best_known_controls = dict(expected_controls)
    except BaseException as error:
        failures.append(
            _restoration_failure(
                "restore_controls",
                error,
                safe_message="control restoration failed",
            )
        )

    try:
        await code_mode.restore_cell_state(cell_state)
    except BaseException as error:
        failures.append(_restoration_failure("restore_cell_state", error))

    controls_observed_after_cleanup = False
    try:
        observed = await code_mode.snapshot_controls(control_names)
        best_known_controls = _select_control_values(
            observed.values,
            published_control_names,
        )
        controls_observed_after_cleanup = True
        if not json_equal(dict(observed.values), dict(snapshot.values)):
            mismatched = _control_mismatches(snapshot.values, observed.values)
            reported_mismatches = mismatched[:_MAX_REPORTED_CONTROL_MISMATCHES]
            failures.append(
                {
                    "operation": "verify_controls",
                    "exception_type": "ControlStateMismatch",
                    "message": "restored controls differ from the captured input vector",
                    "details": {
                        "expected": expected_controls,
                        "actual": best_known_controls,
                        "mismatched_controls": [
                            {
                                "name": name,
                                "sensitive": name in sensitive_control_names,
                            }
                            for name in reported_mismatches
                        ],
                        "mismatched_controls_truncated": (
                            len(mismatched) > len(reported_mismatches)
                        ),
                    },
                }
            )
    except BaseException as error:
        failures.append(
            _restoration_failure(
                "inspect_controls",
                error,
                safe_message="control inspection after cleanup failed",
            )
        )

    return _RestorationReport(
        failures=tuple(failures),
        expected_controls=expected_controls,
        best_known_controls=best_known_controls,
        controls_observed_after_cleanup=controls_observed_after_cleanup,
    )


def _restoration_failure(
    operation: str,
    error: BaseException,
    *,
    safe_message: str | None = None,
) -> JsonObject:
    failure: JsonObject = {
        "operation": operation,
        "exception_type": type(error).__name__,
        "message": safe_message or str(error) or type(error).__name__,
    }
    if safe_message is None and isinstance(error, MarimoExportError) and error.details:
        failure["details"] = error.details
    return failure


def _with_restoration(
    error: BaseException,
    restoration: _RestorationReport,
) -> MarimoExportError:
    if isinstance(error, MarimoExportError):
        error._merge_details({"restoration": restoration.wire()})
        return error
    return CaptureError(
        "capture failed and notebook state could not be restored",
        details={"restoration": restoration.wire()},
    )


def _published_control_names(spec: ExportSpec) -> frozenset[str]:
    return frozenset(name for variant in spec.variants for name in variant.controls)


def _validate_variant_controls(
    inspection: code_mode.LiveInspection,
    published_control_names: frozenset[str],
) -> None:
    controls = {control.name: control for control in inspection.controls}
    missing = sorted(published_control_names - controls.keys())
    if missing:
        raise SelectionError(
            "variant controls are unavailable in the running notebook",
            details={"controls": missing},
        )
    sensitive = sorted(name for name in published_control_names if controls[name].sensitive)
    if sensitive:
        raise SelectionError(
            "sensitive controls cannot be used as variant inputs",
            details={"controls": sensitive},
        )


def _select_control_values(
    values: Mapping[str, object],
    names: frozenset[str],
) -> JsonObject:
    return json_object(
        {name: values[name] for name in sorted(names)},
        "published controls",
    )


def _control_mismatches(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> list[str]:
    result: list[str] = []
    for name in sorted(set(expected) | set(actual)):
        if name not in expected or name not in actual:
            result.append(name)
            continue
        expected_value = json_value(expected[name], f"expected control {name!r}")
        actual_value = json_value(actual[name], f"actual control {name!r}")
        if not json_equal(expected_value, actual_value):
            result.append(name)
    return result


def _builtin_exporters() -> list[JsonObject]:
    return [
        json_object(
            {
                "name": exporter.name,
                "format_id": exporter.version,
                "available": _is_builtin_available(exporter),
                "extra": exporter.extra,
            },
            f"built-in exporter {exporter.name!r}",
        )
        for exporter in sorted(_BUILTIN_EXPORTERS, key=lambda item: item.name)
    ]


async def _preflight_exporters(
    spec: ExportSpec,
) -> dict[tuple[str, str], _ResolvedExporter]:
    resolved: dict[tuple[str, str], _ResolvedExporter] = {}
    async with get_code_mode_context() as context:
        for output in spec.outputs:
            for format_spec in output.formats:
                resolved[(output.name, format_spec.name)] = _resolve_exporter(
                    format_spec,
                    context.globals,
                )
    return resolved


def _is_builtin_available(exporter: _BuiltinExporter) -> bool:
    try:
        return exporter.available
    except (ImportError, ValueError):
        return False


def _resolve_exporter(
    format_spec: FormatSpec,
    live_globals: Mapping[str, object],
) -> _ResolvedExporter:
    exporter = format_spec.exporter
    try:
        if exporter.kind == "builtin":
            descriptor = _builtin_exporter(exporter.reference)
            if not _is_builtin_available(descriptor):
                raise ProjectionError(
                    f"built-in exporter {exporter.reference!r} is unavailable "
                    "in the attached notebook environment",
                    details={
                        "exporter": exporter.reference,
                        "extra": descriptor.extra,
                    },
                )
            return _resolve_builtin(exporter.reference)
        if exporter.kind == "import":
            return _resolve_import(
                exporter.reference,
                version=exporter.version,
            )
        if exporter.kind == "variable":
            return _resolve_variable(
                exporter.reference,
                live_globals,
                version=exporter.version,
            )
    except (ImportError, LookupError, TypeError, ValueError) as error:
        raise ProjectionError(
            f"could not resolve exporter for format {format_spec.name!r}: {error}"
        ) from error
    raise ProjectionError(f"unsupported exporter kind: {exporter.kind}")


def _prepare_specialized_source(
    value: object,
    source_spec: Source,
    format_spec: FormatSpec,
    exporter: _ResolvedExporter,
) -> tuple[object, _ResolvedExporter]:
    """Map selections to exporter inputs without exposing marimo envelopes.

    A cell source supplies its rendered ``CellOutput.data`` payload to every
    exporter. The media type remains adapter-local for HTML preparation.
    """

    cell_media_type: str | None = None
    if source_spec.kind == "cell" and isinstance(value, CellOutput):
        cell_media_type = value.mimetype
        value = value.data
    elif isinstance(value, CellOutput):
        value = CellOutput(
            channel=value.channel,
            mimetype=value.mimetype,
            data=value.data,
            timestamp=0.0,
        )
    if (
        isinstance(value, CellOutput)
        and format_spec.exporter.kind == "builtin"
        and format_spec.exporter.reference != "html"
    ):
        value = value.data
    if format_spec.exporter.kind != "builtin" or format_spec.exporter.reference not in {
        "html",
        "anywidget",
    }:
        return value, exporter

    if format_spec.exporter.reference == "html":
        import marimo

        from marimo_export.exporters.html import html_from_text

        from .html import prepare_html_projection

        if cell_media_type == "text/html" and isinstance(value, str):
            text = value
        elif (
            isinstance(value, CellOutput)
            and value.mimetype == "text/html"
            and isinstance(value.data, str)
        ):
            text = value.data
        else:
            source = value.data if isinstance(value, CellOutput) else value
            text = marimo.as_html(source).text
        prepared = prepare_html_projection(text)
        return prepared, _ResolvedExporter(
            function=html_from_text,
            reference="marimo_export.exporters.html:html_from_text",
            version=exporter.version,
        )

    from marimo_export.exporters.anywidget import anywidget_from_payload

    from .anywidget import capture_anywidget_payload

    payload = capture_anywidget_payload(value)
    return payload, _ResolvedExporter(
        function=anywidget_from_payload,
        reference="marimo_export.exporters.anywidget:anywidget_from_payload",
        version=exporter.version,
    )


def _decode_request(request_json: str) -> JsonObject:
    if not isinstance(request_json, str):
        raise TypeError("bridge request must be a JSON string")
    try:
        encoded = request_json.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError("bridge request must be valid UTF-8") from error
    if len(encoded) > _MAX_REQUEST_BYTES:
        raise ValueError("bridge request exceeds the size limit")
    request = decode_json_object(request_json, "bridge request")
    _exact(
        request,
        {"schema", "client_version", "request_id", "operation", "params"},
        "bridge request",
    )
    if request["schema"] != BRIDGE_SCHEMA:
        raise ValueError(f"bridge request schema must be {BRIDGE_SCHEMA!r}")
    client_version = _nonempty_string(
        request["client_version"],
        "bridge request.client_version",
    )
    if len(client_version) > 256 or any(
        ord(character) < 32 or ord(character) == 127 for character in client_version
    ):
        raise ValueError("bridge request.client_version must be a valid package version")
    kernel_version = _package_version()
    if client_version != kernel_version:
        raise SessionError(
            "marimo-export versions differ between the client and attached kernel",
            details={
                "client_version": client_version,
                "kernel_version": kernel_version,
            },
        )
    _nonempty_string(request["request_id"], "bridge request.request_id")
    operation = _nonempty_string(
        request["operation"],
        "bridge request.operation",
    )
    if operation not in _OPERATIONS:
        raise ValueError("bridge request.operation must be inspect, capture, or release")
    request["params"] = json_object(request["params"], "bridge request.params")
    return request


def _request_id_hint(request_json: object) -> str:
    if not isinstance(request_json, str) or len(request_json) > _MAX_REQUEST_BYTES:
        return "invalid"
    try:
        value = json.loads(request_json)
    except (TypeError, ValueError):
        return "invalid"
    if isinstance(value, dict):
        request_id = value.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id[:256]
    return "invalid"


def _error_response(
    request_id: str,
    code: str,
    message: str,
    *,
    details: Mapping[str, object] | None = None,
) -> JsonObject:
    error: JsonObject = {"code": code, "message": message}
    parsed_details = json_object(
        {} if details is None else details,
        "bridge error details",
    )
    if parsed_details:
        error["details"] = parsed_details
    return {
        "schema": BRIDGE_SCHEMA,
        "request_id": request_id,
        "ok": False,
        "error": error,
    }


def _exact(value: Mapping[str, object], expected: set[str], path: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise SpecError(f"{path} does not accept: {', '.join(sorted(unknown))}")
    if missing:
        raise SpecError(f"{path} is missing: {', '.join(sorted(missing))}")


def _nonempty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TypeError(f"{path} must be a non-empty string")
    return value


def _positive_safe_integer(value: object, path: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > _MAX_SAFE_INTEGER
    ):
        raise TypeError(f"{path} must be a positive safe integer")
    return value


def _portable_filename(value: str | None) -> str:
    if not value:
        return "notebook.py"
    filename = Path(value).name
    return filename or "notebook.py"


def _package_version() -> str:
    try:
        return version("marimo-export")
    except PackageNotFoundError:
        return "0.0.0"


__all__ = ["BRIDGE_SCHEMA", "dispatch_json"]
