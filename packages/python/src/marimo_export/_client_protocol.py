"""Strict wire decoders for the marimo-export client."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from marimo_export._json import JsonObject, JsonValue, json_object
from marimo_export._remote import BridgeError, SessionInfo
from marimo_export.descriptors import OutputCodec
from marimo_export.errors import (
    CaptureLimitError,
    CodecError,
    CompatibilityError,
    ExecutionError,
    IntegrityError,
    OutputError,
    SessionError,
    SpecError,
    TransportError,
)
from marimo_export.index import ControlBinding, ControlPathStep, ExportIndex, _control_path_step
from marimo_export.inspection import (
    CellDescription,
    DefinitionDescription,
    SessionDescription,
)
from marimo_export.integration import KernelInputObservation
from marimo_export.result import CacheSummary, StateRunTimings

if TYPE_CHECKING:
    from marimo_export.limits import CaptureLimits

_CODECS = frozenset(
    {
        "marimo.scalar.v1",
        "marimo.json.v1",
        "marimo.output.v1",
        "marimo.cell.v1",
        "numpy.npy.v1",
        "apache.arrow.file.v1",
        "marimo.blob-asset.msgpack.v1",
    }
)
_CAPABILITIES = frozenset(
    {
        "asset_transfer",
        "blob_asset",
        "cache_cells",
        "cell_cache_receipts",
        "child_sessions",
        "child_ui_updates",
        "definition_overrides",
        "projection_snapshots",
        "setup_definition_overrides",
        "synthetic_output_cells",
    }
)


@dataclass(frozen=True, slots=True)
class _TransferAsset:
    codec: OutputCodec
    sha256: str
    size: int
    url: str


@dataclass(frozen=True, slots=True)
class _Transfer:
    ticket: str
    expires_at_ms: int
    assets: tuple[_TransferAsset, ...]


def _transfer(value: object, index: ExportIndex, limits: CaptureLimits) -> _Transfer:
    data = _mapping(value, "capture transfer")
    _exact(data, {"ticket", "expires_at_ms", "assets"}, "capture transfer")
    ticket = data["ticket"]
    expires = data["expires_at_ms"]
    raw_assets = data["assets"]
    if not isinstance(ticket, str) or not ticket:
        raise TransportError("capture transfer ticket is invalid")
    if isinstance(expires, bool) or not isinstance(expires, int) or expires <= 0:
        raise TransportError("capture transfer expiry is invalid")
    if not isinstance(raw_assets, list):
        raise TransportError("capture transfer assets must be a list")
    assets: list[_TransferAsset] = []
    for position, raw in enumerate(raw_assets):
        item = _mapping(raw, f"capture transfer asset {position}")
        _exact(
            item,
            {"codec", "sha256", "size", "url"},
            f"capture transfer asset {position}",
        )
        codec = item["codec"]
        digest = item["sha256"]
        size = item["size"]
        url = item["url"]
        if codec not in _CODECS or codec in {"marimo.scalar.v1", "marimo.json.v1"}:
            raise TransportError("capture transfer asset codec is invalid")
        if not isinstance(digest, str) or len(digest) != 64:
            raise TransportError("capture transfer asset digest is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise TransportError("capture transfer asset size is invalid")
        if not isinstance(url, str) or not url:
            raise TransportError("capture transfer asset URL is invalid")
        assets.append(
            _TransferAsset(
                codec=cast(OutputCodec, codec),
                sha256=digest,
                size=size,
                url=url,
            )
        )
    expected = {(codec, asset.sha256, asset.size) for codec, asset in index.assets()}
    actual = {(asset.codec, asset.sha256, asset.size) for asset in assets}
    if actual != expected or len(actual) != len(assets):
        raise TransportError("capture transfer assets do not match the export")
    largest_asset = max((asset.size for asset in assets), default=0)
    if largest_asset > limits.max_asset_bytes:
        raise CaptureLimitError(
            "capture transfer asset exceeds max_asset_bytes",
            details={
                "declared_bytes": largest_asset,
                "max_asset_bytes": limits.max_asset_bytes,
            },
        )
    index_bytes = len(index.to_bytes())
    asset_bytes = sum(asset.size for asset in assets)
    closure = index_bytes + asset_bytes
    if closure > limits.max_closure_bytes:
        raise CaptureLimitError(
            "capture transfer closure exceeds max_closure_bytes",
            details={
                "asset_bytes": asset_bytes,
                "declared_bytes": closure,
                "index_bytes": index_bytes,
                "max_closure_bytes": limits.max_closure_bytes,
            },
        )
    return _Transfer(ticket=ticket, expires_at_ms=expires, assets=tuple(assets))


def _transfer_ticket(value: object) -> str:
    data = _mapping(value, "capture transfer")
    ticket = data.get("ticket")
    if not isinstance(ticket, str) or not ticket:
        raise TransportError("capture transfer ticket is invalid")
    return ticket


def _cache_summary(
    value: object,
    path: str,
    *,
    expected: int | None = None,
) -> CacheSummary:
    data = _mapping(value, path)
    _exact(data, {"hits", "misses"}, path)
    hits = data["hits"]
    misses = data["misses"]
    if isinstance(hits, bool) or not isinstance(hits, int) or hits < 0:
        raise TransportError(f"{path} counts are invalid")
    if isinstance(misses, bool) or not isinstance(misses, int) or misses < 0:
        raise TransportError(f"{path} counts are invalid")
    if expected is not None and hits + misses != expected:
        raise TransportError(f"{path} counts do not cover every state output")
    return CacheSummary(hits=hits, misses=misses)


def _state_run_timings(value: object, *, states: int) -> StateRunTimings:
    path = "state run timings"
    data = _mapping(value, path)
    fields = {
        "states",
        "setup_seconds",
        "dependency_execution_seconds",
        "ui_update_seconds",
        "output_materialization_seconds",
        "cleanup_seconds",
    }
    _exact(data, fields, path)
    if data["states"] != states:
        raise TransportError("state run timing count does not match export states")
    try:
        return StateRunTimings(
            states=cast(int, data["states"]),
            setup_seconds=cast(float, data["setup_seconds"]),
            dependency_execution_seconds=cast(float, data["dependency_execution_seconds"]),
            ui_update_seconds=cast(float, data["ui_update_seconds"]),
            output_materialization_seconds=cast(float, data["output_materialization_seconds"]),
            cleanup_seconds=cast(float, data["cleanup_seconds"]),
        )
    except (TypeError, ValueError) as error:
        raise TransportError("state run timings are invalid") from error


def _session_description(
    info: SessionInfo,
    value: Mapping[str, object],
) -> SessionDescription:
    data = json_object(value, "session inspection")
    _exact(
        data,
        {
            "filename",
            "path",
            "document_sha256",
            "marimo_version",
            "marimo_export_version",
            "implementation_sha256",
            "capabilities",
            "definitions",
            "cells",
        },
        "session inspection",
    )
    capabilities = data["capabilities"]
    raw_definitions = data["definitions"]
    raw_cells = data["cells"]
    if not isinstance(capabilities, list) or any(
        not isinstance(name, str) or name not in _CAPABILITIES for name in capabilities
    ):
        raise SessionError("session inspection capabilities are invalid")
    if not isinstance(raw_definitions, list):
        raise SessionError("session inspection definitions must be a list")
    if not isinstance(raw_cells, list):
        raise SessionError("session inspection cells must be a list")
    definitions = tuple(
        _definition(item, position) for position, item in enumerate(raw_definitions)
    )
    if tuple(sorted(definition.name for definition in definitions)) != tuple(
        definition.name for definition in definitions
    ):
        raise SessionError("session inspection definitions are not sorted")
    cells = tuple(_cell(item, position) for position, item in enumerate(raw_cells))
    if len({cell.id for cell in cells}) != len(cells):
        raise SessionError("session inspection cell IDs must be unique")
    names = [cell.name for cell in cells if cell.name is not None]
    if len(set(names)) != len(names):
        raise SessionError("session inspection cell names must be unique")
    return SessionDescription(
        session_id=info.id,
        filename=_optional_string(data["filename"], "session filename"),
        path=_optional_string(data["path"], "session path"),
        document_sha256=_digest(data["document_sha256"], "session document digest"),
        marimo_version=_string(data["marimo_version"], "marimo version"),
        marimo_export_version=_string(
            data["marimo_export_version"],
            "marimo-export version",
        ),
        implementation_sha256=_digest(
            data["implementation_sha256"],
            "marimo-export implementation digest",
        ),
        capabilities=tuple(cast(list[str], capabilities)),
        definitions=definitions,
        cells=cells,
    )


def _kernel_input_observation(value: Mapping[str, object]) -> KernelInputObservation:
    data = json_object(value, "input observation")
    _exact(data, {"values", "control_bindings"}, "input observation")
    values = _mapping(data["values"], "input observation values")
    raw_bindings = _mapping(
        data["control_bindings"],
        "input observation control bindings",
    )
    bindings: dict[str, ControlBinding] = {}
    for object_id, raw_binding in raw_bindings.items():
        path = f"input observation control binding {object_id!r}"
        item = _mapping(raw_binding, path)
        _exact(item, {"input", "path"}, path)
        steps = item["path"]
        if not isinstance(steps, list):
            raise SessionError(f"{path} path is invalid")
        try:
            bindings[object_id] = ControlBinding(
                input=_string(item["input"], f"{path} input"),
                path=tuple(
                    _control_path_step(step, f"{path} path item {index}")
                    for index, step in enumerate(steps)
                ),
            )
        except (TypeError, ValueError) as error:
            raise SessionError(f"{path} is invalid") from error
    try:
        return KernelInputObservation(values, bindings)
    except (TypeError, ValueError) as error:
        raise SessionError("input observation is invalid") from error


def _definition(value: object, position: int) -> DefinitionDescription:
    path = f"session definition {position}"
    data = _mapping(value, path)
    _exact(
        data,
        {
            "name",
            "cell_id",
            "python_type",
            "kind",
            "input_mode",
            "siblings",
            "portable_input",
            "sensitive",
            "value_available",
            "value",
            "domain",
            "control_paths",
            "input_dependencies",
        },
        path,
    )
    siblings = data["siblings"]
    kind = data["kind"]
    input_mode = data["input_mode"]
    if not isinstance(siblings, list) or any(not isinstance(item, str) for item in siblings):
        raise SessionError(f"{path} siblings are invalid")
    if kind not in {"ordinary", "ui"}:
        raise SessionError(f"{path} kind is invalid")
    if input_mode not in {"value", "patch"}:
        raise SessionError(f"{path} input_mode is invalid")
    domain = _mapping(data["domain"], f"{path} domain")
    control_paths_value = _mapping(data["control_paths"], f"{path} control_paths")
    control_paths: dict[str, tuple[ControlPathStep, ...]] = {}
    for control_id, steps in control_paths_value.items():
        if not isinstance(steps, list):
            raise SessionError(f"{path} control path is invalid")
        try:
            control_paths[control_id] = tuple(
                _control_path_step(step, f"{path} control_paths[{control_id!r}][{index}]")
                for index, step in enumerate(steps)
            )
        except (TypeError, ValueError) as error:
            raise SessionError(f"{path} control path is invalid") from error
    input_dependencies = data["input_dependencies"]
    if not isinstance(input_dependencies, list) or any(
        not isinstance(dependency, str) for dependency in input_dependencies
    ):
        raise SessionError(f"{path} input_dependencies are invalid")
    return DefinitionDescription(
        name=_string(data["name"], f"{path} name"),
        cell_id=_string(data["cell_id"], f"{path} cell_id"),
        python_type=_string(data["python_type"], f"{path} python_type"),
        kind=cast(Literal["ordinary", "ui"], kind),
        input_mode=cast(Literal["value", "patch"], input_mode),
        siblings=tuple(cast(list[str], siblings)),
        portable_input=_boolean(data["portable_input"], f"{path} portable_input"),
        sensitive=_boolean(data["sensitive"], f"{path} sensitive"),
        value_available=_boolean(data["value_available"], f"{path} value_available"),
        control_paths=control_paths,
        input_dependencies=tuple(cast(list[str], input_dependencies)),
        value=cast(JsonValue, data["value"]),
        domain=domain,
    )


def _cell(value: object, position: int) -> CellDescription:
    path = f"session cell {position}"
    data = _mapping(value, path)
    _exact(data, {"id", "name", "code_sha256", "config", "input_dependencies"}, path)
    input_dependencies = data["input_dependencies"]
    if not isinstance(input_dependencies, list) or any(
        not isinstance(dependency, str) for dependency in input_dependencies
    ):
        raise SessionError(f"{path} input_dependencies are invalid")
    return CellDescription(
        id=_string(data["id"], f"{path} id"),
        name=_optional_string(data["name"], f"{path} name"),
        code_sha256=_string(data["code_sha256"], f"{path} code_sha256"),
        config=_mapping(data["config"], f"{path} config"),
        input_dependencies=tuple(cast(list[str], input_dependencies)),
    )


def _bridge_error(error: BridgeError) -> Exception:
    code = error.remote_code
    kwargs = {"code": code, "details": error.details}
    if code.startswith("spec_"):
        return SpecError(str(error), **kwargs)
    if code in {"marimo_incompatible"}:
        return CompatibilityError(str(error), **kwargs)
    if (
        code.startswith("output_")
        or code.startswith("cache_receipt")
        or code.startswith("exporter_")
    ):
        return OutputError(str(error), **kwargs)
    if code.startswith("codec_"):
        return CodecError(str(error), **kwargs)
    if code.startswith("state_") or code.startswith("input_") or code.startswith("parent_"):
        return ExecutionError(str(error), **kwargs)
    if code.startswith("integrity_"):
        return IntegrityError(str(error), **kwargs)
    return SessionError(str(error), **kwargs)


def _mapping(value: object, path: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise TransportError(f"{path} must be an object")
    try:
        return json_object(value, path)
    except (TypeError, ValueError) as error:
        raise TransportError(f"{path} is invalid") from error


def _exact(value: Mapping[str, object], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise TransportError(f"{path} has invalid fields")


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise SessionError(f"{path} must be a non-empty string")
    return value


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise SessionError(f"{path} must be a boolean")
    return value


def _digest(value: object, path: str) -> str:
    digest = _string(value, path)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise SessionError(f"{path} must be a lowercase SHA-256 digest")
    return digest


__all__ = [
    "_Transfer",
    "_bridge_error",
    "_cache_summary",
    "_exact",
    "_session_description",
    "_state_run_timings",
    "_transfer",
    "_transfer_ticket",
]
