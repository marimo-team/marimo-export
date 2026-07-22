from __future__ import annotations

import copy
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import NoReturn, TypeAlias, cast
from urllib.parse import SplitResult, unquote, urlsplit

from marimo_export._json import JsonObject, JsonValue, json_object
from marimo_export._marimo.anywidget import ANYWIDGET_PAYLOAD_SCHEMA

_BASE64 = re.compile(r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_INVALID_HTTP_URL_CHARACTER = re.compile(r"[\x00-\x20\x7f\\]")
_HTTP_HOST = re.compile(r"[A-Za-z0-9._-]+")
_MODEL_REF_PREFIXES = ("anywidget:", "IPY_MODEL_")
_UNSAFE_PATH_KEYS = frozenset({"__proto__", "constructor", "prototype"})
_MAX_SAFE_INTEGER = 2**53 - 1

PathToken: TypeAlias = str | int
BufferPath: TypeAlias = tuple[PathToken, ...]


@dataclass(frozen=True)
class ValidatedAnyWidgetPayload:
    root_model_id: str
    model_count: int


@dataclass(frozen=True)
class _ValidatedModel:
    state: dict[str, object]
    has_esm: bool


def validate_anywidget_payload(payload: bytes) -> ValidatedAnyWidgetPayload:
    if not isinstance(payload, bytes):
        raise TypeError("AnyWidget payload must be bytes")
    try:
        document = json_object(
            json.loads(payload.decode("utf-8")),
            "AnyWidget payload",
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("AnyWidget payload must contain UTF-8 JSON") from error

    _exact_keys(
        document,
        ("schema", "rootModelId", "files", "modelNotifications"),
        "AnyWidget payload",
    )
    if document["schema"] != ANYWIDGET_PAYLOAD_SCHEMA:
        raise ValueError(f"AnyWidget payload schema must be {ANYWIDGET_PAYLOAD_SCHEMA!r}")

    root_model_id = _non_empty_string(document["rootModelId"], "rootModelId")
    if root_model_id != "model-0":
        raise ValueError("AnyWidget payload rootModelId must be 'model-0'")

    files = _parse_files(document["files"])
    notifications = _array(document["modelNotifications"], "modelNotifications")
    models: dict[str, _ValidatedModel] = {}
    for index, value in enumerate(notifications):
        notification_path = f"modelNotifications[{index}]"
        notification = _record(value, notification_path)
        _exact_keys(notification, ("op", "model_id", "message"), notification_path)
        if notification["op"] != "model-lifecycle":
            raise ValueError(f"{notification_path}.op must be 'model-lifecycle'")

        model_id = _non_empty_string(notification["model_id"], f"{notification_path}.model_id")
        expected_model_id = f"model-{index}"
        if model_id != expected_model_id:
            raise ValueError(f"{notification_path}.model_id must be {expected_model_id!r}")

        message_path = f"{notification_path}.message"
        message = _record(notification["message"], message_path)
        _exact_keys(
            message,
            ("method", "state", "buffer_paths", "buffers", "esm_spec"),
            message_path,
        )
        if message["method"] != "open":
            raise ValueError(f"AnyWidget model {model_id!r} must contain an open message")

        state = _record(message["state"], f"AnyWidget model {model_id!r} state")
        model_state = cast(dict[str, object], copy.deepcopy(state))
        buffer_paths = _parse_buffer_paths(message["buffer_paths"], model_id)
        buffers = _parse_buffers(message["buffers"], model_id)
        if len(buffer_paths) != len(buffers):
            raise ValueError(
                f"AnyWidget model {model_id!r} has {len(buffer_paths)} buffer paths "
                f"and {len(buffers)} buffers"
            )
        for path in buffer_paths:
            _set_buffer(model_state, path, model_id)

        models[model_id] = _ValidatedModel(
            state=model_state,
            has_esm=_validate_esm_spec(message["esm_spec"], files, model_id),
        )

    root = models.get(root_model_id)
    if root is None:
        raise ValueError(f"AnyWidget root model {root_model_id!r} is missing")
    if not root.has_esm:
        raise ValueError(f"AnyWidget root model {root_model_id!r} has no ESM spec")

    reachable = _reachable_models(root_model_id, models)
    if len(reachable) != len(models):
        unrelated = [model_id for model_id in models if model_id not in reachable]
        raise ValueError(
            "AnyWidget payload contains models outside the root closure: "
            + ", ".join(repr(model_id) for model_id in unrelated)
        )

    return ValidatedAnyWidgetPayload(
        root_model_id=root_model_id,
        model_count=len(models),
    )


def _parse_files(value: object) -> dict[str, str]:
    document = _record(value, "files")
    files: dict[str, str] = {}
    for path, data_url_value in document.items():
        if not path:
            raise ValueError("AnyWidget file paths must be non-empty strings")
        data_url = _non_empty_string(data_url_value, f"AnyWidget file {path!r}")
        if not data_url.startswith("data:"):
            raise ValueError(f"AnyWidget file {path!r} must contain a data URL")
        _validate_data_url(data_url, f"AnyWidget file {path!r}")
        files[path] = data_url
    return files


def _parse_buffer_paths(value: object, model_id: str) -> tuple[BufferPath, ...]:
    paths = _array(value, f"AnyWidget model {model_id!r} buffer_paths")
    result: list[BufferPath] = []
    seen: set[BufferPath] = set()
    for index, value in enumerate(paths):
        raw_path = _array(value, f"AnyWidget model {model_id!r} buffer path {index}")
        if not raw_path:
            raise ValueError(f"AnyWidget model {model_id!r} has an empty buffer path")
        path = tuple(_path_token(token, model_id) for token in raw_path)
        if path in seen:
            raise ValueError(f"AnyWidget model {model_id!r} repeats buffer path {path!r}")
        seen.add(path)
        result.append(path)
    return tuple(result)


def _path_token(value: object, model_id: str) -> PathToken:
    if isinstance(value, str):
        if value not in _UNSAFE_PATH_KEYS:
            return value
    elif isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= _MAX_SAFE_INTEGER:
            return value
    elif isinstance(value, float):
        float_value = cast(float, value)
        if float_value.is_integer():
            token = int(float_value)
            if 0 <= token <= _MAX_SAFE_INTEGER:
                return token
    raise TypeError(f"AnyWidget model {model_id!r} has an invalid buffer path token")


def _parse_buffers(value: object, model_id: str) -> tuple[str, ...]:
    buffers = _array(value, f"AnyWidget model {model_id!r} buffers")
    result: list[str] = []
    for index, value in enumerate(buffers):
        if not isinstance(value, str) or _BASE64.fullmatch(value) is None:
            raise ValueError(f"AnyWidget model {model_id!r} buffer {index} is not canonical base64")
        result.append(value)
    return tuple(result)


def _set_buffer(state: dict[str, object], path: BufferPath, model_id: str) -> None:
    target: object = state
    for token in path[:-1]:
        if isinstance(token, int):
            if not isinstance(target, list) or token >= len(target):
                _invalid_buffer_path(model_id, path)
            target = cast(list[object], target)[token]
        else:
            if not isinstance(target, dict) or token not in target:
                _invalid_buffer_path(model_id, path)
            target = cast(dict[str, object], target)[token]

    final_token = path[-1]
    if isinstance(final_token, int):
        if not isinstance(target, list) or final_token >= len(target):
            _invalid_buffer_path(model_id, path)
        cast(list[object], target)[final_token] = _BUFFER
        return
    if not isinstance(target, dict):
        _invalid_buffer_path(model_id, path)
    cast(dict[str, object], target)[final_token] = _BUFFER


def _invalid_buffer_path(model_id: str, path: BufferPath) -> NoReturn:
    raise ValueError(
        f"AnyWidget model {model_id!r} buffer path {path!r} does not target existing state"
    )


def _validate_esm_spec(value: object, files: dict[str, str], model_id: str) -> bool:
    if value is None:
        return False
    path = f"AnyWidget model {model_id!r} ESM spec"
    spec = _record(value, path)
    _exact_keys(spec, ("url", "hash"), path)
    url = _non_empty_string(spec["url"], f"AnyWidget model {model_id!r} ESM URL")
    _non_empty_string(spec["hash"], f"AnyWidget model {model_id!r} ESM hash")
    if url in files:
        return True

    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise ValueError(
            f"AnyWidget model {model_id!r} references missing virtual file {url!r}"
        ) from error
    protocol = parsed.scheme.lower()
    if protocol == "data":
        _validate_data_url(url, f"AnyWidget model {model_id!r} ESM URL")
        return True
    if protocol in {"http", "https"}:
        _validate_http_url(url, parsed, model_id)
        return True
    if not protocol:
        raise ValueError(f"AnyWidget model {model_id!r} references missing virtual file {url!r}")
    if protocol not in {"data", "http", "https"}:
        raise ValueError(
            f"AnyWidget model {model_id!r} uses incompatible ESM URL protocol {protocol + ':'!r}"
        )
    raise ValueError(f"AnyWidget model {model_id!r} contains an invalid ESM URL {url!r}")


def _validate_http_url(value: str, parsed: SplitResult, model_id: str) -> None:
    if _INVALID_HTTP_URL_CHARACTER.search(value) is not None:
        raise ValueError(f"AnyWidget model {model_id!r} contains an invalid ESM URL {value!r}")
    try:
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as error:
        raise ValueError(
            f"AnyWidget model {model_id!r} contains an invalid ESM URL {value!r}"
        ) from error
    if not hostname:
        raise ValueError(f"AnyWidget model {model_id!r} contains an invalid ESM URL {value!r}")
    if ":" in hostname:
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError as error:
            raise ValueError(
                f"AnyWidget model {model_id!r} contains an invalid ESM URL {value!r}"
            ) from error
        return
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError(
            f"AnyWidget model {model_id!r} contains an invalid ESM URL {value!r}"
        ) from error
    if _HTTP_HOST.fullmatch(ascii_hostname) is None:
        raise ValueError(f"AnyWidget model {model_id!r} contains an invalid ESM URL {value!r}")
    for label in ascii_hostname.split("."):
        if not label.lower().startswith("xn--"):
            continue
        try:
            decoded = label.encode("ascii").decode("idna")
            encoded = decoded.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise ValueError(
                f"AnyWidget model {model_id!r} contains an invalid ESM URL {value!r}"
            ) from error
        if encoded.lower() != label.lower():
            raise ValueError(f"AnyWidget model {model_id!r} contains an invalid ESM URL {value!r}")
    if ascii_hostname.replace(".", "").isdigit():
        try:
            ipaddress.IPv4Address(ascii_hostname)
        except ValueError as error:
            raise ValueError(
                f"AnyWidget model {model_id!r} contains an invalid ESM URL {value!r}"
            ) from error


def _validate_data_url(value: str, path: str) -> None:
    comma = value.find(",")
    if comma == -1:
        raise ValueError(f"{path} is a malformed data URL")
    metadata = value[5:comma].lower().split(";")
    body = value[comma + 1 :]
    if "base64" in metadata:
        if _BASE64.fullmatch(body) is None:
            raise ValueError(f"{path} contains malformed base64 data")
        return
    if _INVALID_PERCENT_ESCAPE.search(body) is not None:
        raise ValueError(f"{path} contains malformed percent-encoded data")
    try:
        unquote(body, encoding="utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError(f"{path} contains malformed percent-encoded data") from error


def _reachable_models(
    root_model_id: str,
    models: dict[str, _ValidatedModel],
) -> set[str]:
    reachable: set[str] = set()
    pending = [root_model_id]
    while pending:
        model_id = pending.pop()
        if model_id in reachable:
            continue
        model = models.get(model_id)
        if model is None:
            raise ValueError(f"AnyWidget model reference {model_id!r} is unresolved")
        reachable.add(model_id)
        pending.extend(_model_references(model.state))
    return reachable


def _model_references(value: object) -> list[str]:
    references: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            for prefix in _MODEL_REF_PREFIXES:
                if not item.startswith(prefix):
                    continue
                model_id = item.removeprefix(prefix)
                if not model_id:
                    raise ValueError("AnyWidget state contains an empty model reference")
                references.append(model_id)
                return
            return
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if isinstance(item, dict):
            for child in item.values():
                visit(child)

    visit(value)
    return references


def _exact_keys(value: JsonObject, expected: tuple[str, ...], path: str) -> None:
    allowed = frozenset(expected)
    unexpected = [key for key in value if key not in allowed]
    missing = [key for key in expected if key not in value]
    if unexpected or missing:
        raise TypeError(
            f"{path} fields are invalid. Missing: {', '.join(missing) or 'none'}. "
            f"Unexpected: {', '.join(unexpected) or 'none'}."
        )


def _non_empty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{path} must be a non-empty string")
    return value


def _array(value: object, path: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be an array")
    return cast(list[JsonValue], value)


def _record(value: object, path: str) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be an object")
    return cast(JsonObject, value)


_BUFFER = object()
