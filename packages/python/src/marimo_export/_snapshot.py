"""Validate native Marimo output and cell snapshot assets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, cast
from urllib.parse import urlsplit

from marimo_export._http_url import validate_http_url_authority
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    portable_json_object,
    portable_json_value,
)

SnapshotSchema = Literal["marimo.output.v1", "marimo.cell.v1"]

_MAX_SNAPSHOT_VALUES = 2_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MODEL_INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")
_CELL_CHANNELS = frozenset(
    {
        "stdout",
        "stderr",
        "stdin",
        "pdb",
        "output",
        "marimo-error",
        "media",
    }
)


def validate_snapshot(data: bytes, schema: SnapshotSchema) -> None:
    """Validate one canonical snapshot against its complete wire schema."""

    if schema == "marimo.output.v1":
        fields = ("output", "ownerCellId", "projectionSha256", "resources", "schema")
    elif schema == "marimo.cell.v1":
        fields = (
            "cell",
            "console",
            "outcome",
            "output",
            "projectionSha256",
            "resources",
            "schema",
        )
    else:
        raise ValueError(f"unsupported Marimo snapshot schema {schema!r}")
    root = decode_json_object(
        data,
        f"{schema} snapshot",
        max_values=_MAX_SNAPSHOT_VALUES,
    )
    root = _strict_object(root, f"{schema} snapshot", fields)
    if root["schema"] != schema:
        raise TypeError(f"snapshot schema must be {schema!r}")
    if canonical_bytes(root, max_values=_MAX_SNAPSHOT_VALUES) != data:
        raise TypeError(f"{schema} snapshot must be canonical JSON")
    projection = _digest(root["projectionSha256"], "projectionSha256")
    if schema == "marimo.output.v1":
        owner = _nonempty_string(root["ownerCellId"], "ownerCellId")
        _cell_output(root["output"], "output", nullable=True)
    else:
        cell = _cell_identity(root["cell"])
        owner = cast(str, cell["id"])
        if root["outcome"] != "completed":
            raise TypeError('Marimo cell snapshot outcome must be "completed"')
        _cell_output(root["output"], "output", nullable=True)
        console = root["console"]
        if not isinstance(console, list):
            raise TypeError("Marimo cell snapshot console must be an array")
        for index, output in enumerate(console):
            _cell_output(output, f"console[{index}]", nullable=False)
    _resources(root["resources"], owner, projection)


def _cell_identity(value: JsonValue) -> JsonObject:
    cell = _strict_object(value, "cell", ("codeSha256", "config", "id", "name"))
    _nonempty_string(cell["id"], "cell.id")
    name = cell["name"]
    if name is not None:
        _nonempty_string(name, "cell.name")
    _digest(cell["codeSha256"], "cell.codeSha256")
    portable_json_object(cell["config"], "cell.config")
    return cell


def _cell_output(value: JsonValue, path: str, *, nullable: bool) -> None:
    if value is None:
        if nullable:
            return
        raise TypeError(f"{path} must contain an output")
    output = _strict_object(value, path, ("channel", "data", "mimetype"))
    channel = _nonempty_string(output["channel"], f"{path}.channel")
    if channel not in _CELL_CHANNELS:
        raise TypeError(f"{path}.channel is not a Marimo cell channel")
    _nonempty_string(output["mimetype"], f"{path}.mimetype")
    portable_json_value(output["data"], f"{path}.data")


def _resources(value: JsonValue, owner: str, projection: str) -> None:
    resources = _strict_object(
        value,
        "resources",
        ("files", "functions", "modelNotifications", "uiValues"),
    )
    files = _object(resources["files"], "resources.files")
    parsed_files: dict[str, str] = {}
    for path, data_url in files.items():
        parsed_path = _nonempty_string(path, "resources.files key")
        parsed_url = _nonempty_string(data_url, f"resources.files[{path!r}]")
        if not parsed_url.startswith("data:"):
            raise TypeError(f"resources.files[{path!r}] must contain a data URL")
        parsed_files[parsed_path] = parsed_url

    notifications = resources["modelNotifications"]
    if not isinstance(notifications, list):
        raise TypeError("resources.modelNotifications must be an array")
    for index, notification in enumerate(notifications):
        _model_lifecycle(notification, index, parsed_files, projection)

    functions = _object(resources["functions"], "resources.functions")
    parsed_functions: set[str] = set()
    for namespace, names in functions.items():
        object_id = _projection_ui_object_id(
            namespace,
            "resources.functions key",
            owner,
            projection,
        )
        if not isinstance(names, list) or any(
            not isinstance(name, str) or not name for name in names
        ):
            raise TypeError(f"resources.functions[{namespace!r}] must be an array of names")
        if names:
            raise TypeError(f"resources.functions[{namespace!r}] must be empty for static replay")
        parsed_functions.add(object_id)

    ui_values = _object(resources["uiValues"], "resources.uiValues")
    parsed_ui_values: set[str] = set()
    for object_id, item in ui_values.items():
        parsed_id = _projection_ui_object_id(
            object_id,
            "resources.uiValues key",
            owner,
            projection,
        )
        portable_json_value(item, f"resources.uiValues[{object_id!r}]")
        parsed_ui_values.add(parsed_id)
    missing_functions = parsed_ui_values - parsed_functions
    if missing_functions:
        object_id = min(missing_functions)
        raise TypeError(f"resources.uiValues key {object_id!r} has no function namespace")
    missing_values = parsed_functions - parsed_ui_values
    if missing_values:
        object_id = min(missing_values)
        raise TypeError(f"resources.functions key {object_id!r} has no replay UI value")


def _projection_ui_object_id(
    value: object,
    path: str,
    owner: str,
    projection: str,
) -> str:
    object_id = _nonempty_string(value, path)
    prefix = f"{owner}-projection-{projection}-ui-"
    if not object_id.startswith(prefix) or len(object_id) == len(prefix):
        raise TypeError(f"{path} must be a projection-scoped UI object owned by {owner!r}")
    return object_id


def _model_lifecycle(
    value: JsonValue,
    index: int,
    files: Mapping[str, str],
    projection: str,
) -> None:
    path = f"resources.modelNotifications[{index}]"
    notification = _strict_object(value, path, ("message", "model_id", "op"))
    if notification["op"] != "model-lifecycle":
        raise TypeError(f'{path}.op must be "model-lifecycle"')
    model_id = _nonempty_string(notification["model_id"], f"{path}.model_id")
    prefix = f"projection-{projection}-model-"
    model_index = model_id.removeprefix(prefix)
    if not model_id.startswith(prefix) or _MODEL_INDEX.fullmatch(model_index) is None:
        raise TypeError(f"{path}.model_id must belong to the snapshot projection")
    message = _object(notification["message"], f"{path}.message")
    method = message.get("method")
    if method in ("open", "update"):
        _exact_fields(
            message,
            ("buffer_paths", "buffers", "esm_spec", "method", "state"),
            f"{path}.message",
        )
        portable_json_object(message["state"], f"{path}.message.state")
        paths = _buffer_paths(message["buffer_paths"], f"{path}.message.buffer_paths")
        buffers = _string_array(message["buffers"], f"{path}.message.buffers")
        if len(paths) != len(buffers):
            raise TypeError(f"{path}.message buffer paths and buffers must have equal length")
        _esm_spec(message["esm_spec"], f"{path}.message.esm_spec", files)
        return
    if method == "custom":
        _exact_fields(message, ("buffers", "content", "method"), f"{path}.message")
        portable_json_value(message["content"], f"{path}.message.content")
        _string_array(message["buffers"], f"{path}.message.buffers")
        return
    if method == "close":
        _exact_fields(message, ("method",), f"{path}.message")
        return
    raise TypeError(f"{path}.message.method is invalid")


def _buffer_paths(value: JsonValue, path: str) -> tuple[tuple[str | int, ...], ...]:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be an array")
    parsed: list[tuple[str | int, ...]] = []
    for index, item in enumerate(value):
        if not isinstance(item, list) or not item:
            raise TypeError(f"{path}[{index}] must be a non-empty array")
        tokens: list[str | int] = []
        for token in item:
            if isinstance(token, str) or (
                isinstance(token, int) and not isinstance(token, bool) and 0 <= token <= 2**53 - 1
            ):
                tokens.append(token)
            else:
                raise TypeError(f"{path}[{index}] contains an invalid token")
        parsed.append(tuple(tokens))
    return tuple(parsed)


def _esm_spec(value: JsonValue, path: str, files: Mapping[str, str]) -> None:
    if value is None:
        return
    spec = _strict_object(value, path, ("hash", "url"))
    _nonempty_string(spec["hash"], f"{path}.hash")
    url = _nonempty_string(spec["url"], f"{path}.url")
    embedded_key = url[1:] if url.startswith("./@file/") else url
    if embedded_key in files or url.startswith("data:"):
        return
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise TypeError(f"{path}.url references an unavailable resource") from error
    if parsed.scheme not in {"http", "https"}:
        if parsed.scheme:
            raise TypeError(f"{path}.url uses an incompatible protocol")
        raise TypeError(f"{path}.url references an unavailable resource")
    try:
        validate_http_url_authority(url)
    except ValueError as error:
        raise TypeError(f"{path}.url references an unavailable resource") from error


def _string_array(value: JsonValue, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{path} must be an array of strings")
    return tuple(value)


def _strict_object(
    value: object,
    path: str,
    fields: tuple[str, ...],
) -> JsonObject:
    parsed = _object(value, path)
    _exact_fields(parsed, fields, path)
    return parsed


def _exact_fields(value: Mapping[str, object], fields: tuple[str, ...], path: str) -> None:
    expected = set(fields)
    missing = [field for field in fields if field not in value]
    extra = [field for field in value if field not in expected]
    if missing:
        raise TypeError(f"{path} is missing fields: {', '.join(missing)}")
    if extra:
        raise TypeError(f"{path} has unknown fields: {', '.join(extra)}")


def _object(value: object, path: str) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be an object")
    return cast(JsonObject, value)


def _nonempty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{path} must be a non-empty string")
    return value


def _digest(value: object, path: str) -> str:
    parsed = _nonempty_string(value, path)
    if _SHA256.fullmatch(parsed) is None:
        raise TypeError(f"{path} must be a lowercase SHA-256 digest")
    return parsed


__all__ = ["SnapshotSchema", "validate_snapshot"]
