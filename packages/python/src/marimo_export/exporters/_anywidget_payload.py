from __future__ import annotations

import copy
import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, TypeAlias, cast

if TYPE_CHECKING:
    from urllib.parse import SplitResult

from marimo_export._json import JsonObject, JsonValue, decode_json_object

ANYWIDGET_PAYLOAD_SCHEMA = "marimo-export.anywidget.v1"

_BASE64_BODY = re.compile(r"[A-Za-z0-9+/]*")
_BASE64_PARAMETER = re.compile(r";base64(?=;|,)", re.ASCII | re.IGNORECASE)
_DATA_URL_PREFIX = re.compile(r"data:", re.ASCII | re.IGNORECASE)
_INVALID_HTTP_URL_CHARACTER = re.compile(r"[\x00-\x20\x7f\\]")
_HTTP_HOST = re.compile(r"[A-Za-z0-9._-]+")
_MODEL_REF_PREFIXES = ("anywidget:", "IPY_MODEL_")
_UNSAFE_PATH_KEYS = frozenset({"__proto__", "constructor", "prototype"})
_MAX_SAFE_INTEGER = 2**53 - 1
# Bound external module URLs before parsing. Inline data URLs follow the
# AnyWidget payload byte limits.
_MAX_EXTERNAL_ESM_URL_BYTES = 8 * 1024
_MAX_DATA_URL_MEDIA_TYPE_BYTES = 1024
_MAX_DIAGNOSTIC_QUOTE_CHARS = 128
_MAX_DIAGNOSTIC_PATH_CHARS = 256
_MAX_DIAGNOSTIC_LIST_CHARS = 256
_DIAGNOSTIC_ELLIPSIS = "..."

PathToken: TypeAlias = str | int
BufferPath: TypeAlias = tuple[PathToken, ...]


def _quoted(value: str) -> str:
    content_limit = _MAX_DIAGNOSTIC_QUOTE_CHARS - 2
    content: list[str] = []
    length = 0
    truncated = False
    for character in value:
        escaped = _escape_diagnostic_character(character)
        if length + len(escaped) > content_limit:
            truncated = True
            break
        content.append(escaped)
        length += len(escaped)
    if truncated:
        while content and length + len(_DIAGNOSTIC_ELLIPSIS) > content_limit:
            length -= len(content.pop())
        content.append(_DIAGNOSTIC_ELLIPSIS)
    return f"'{''.join(content)}'"


def _diagnostic_path(*parts: str) -> str:
    content: list[str] = []
    remaining = _MAX_DIAGNOSTIC_PATH_CHARS
    truncated = False
    for part in parts:
        if len(part) <= remaining:
            content.append(part)
            remaining -= len(part)
            continue
        content.append(part[:remaining])
        truncated = True
        break
    rendered = "".join(content)
    if truncated:
        rendered = rendered[: -len(_DIAGNOSTIC_ELLIPSIS)] + _DIAGNOSTIC_ELLIPSIS
    return rendered


def _diagnostic_list(values: Iterable[PathToken], *, brackets: bool = False) -> str:
    wrapper_size = 2 if brackets else 0
    limit = _MAX_DIAGNOSTIC_LIST_CHARS - wrapper_size
    items: list[str] = []
    length = 0
    truncated = False
    for value in values:
        item = _quoted(value) if isinstance(value, str) else str(value)
        separator_size = 2 if items else 0
        if length + separator_size + len(item) > limit:
            truncated = True
            break
        items.append(item)
        length += separator_size + len(item)
    if truncated:
        marker_size = len(_DIAGNOSTIC_ELLIPSIS) + (2 if items else 0)
        while items and length + marker_size > limit:
            removed = items.pop()
            length -= len(removed) + (2 if items else 0)
            marker_size = len(_DIAGNOSTIC_ELLIPSIS) + (2 if items else 0)
        items.append(_DIAGNOSTIC_ELLIPSIS)
    rendered = ", ".join(items)
    return f"[{rendered}]" if brackets else rendered


def _escape_diagnostic_character(value: str) -> str:
    escaped = {
        "\\": r"\\",
        "'": r"\'",
        "\b": r"\b",
        "\t": r"\t",
        "\n": r"\n",
        "\f": r"\f",
        "\r": r"\r",
    }.get(value)
    if escaped is not None:
        return escaped
    if value.isprintable():
        return value
    codepoint = ord(value)
    if codepoint <= 0xFF:
        return f"\\x{codepoint:02x}"
    if codepoint <= 0xFFFF:
        return f"\\u{codepoint:04x}"
    return f"\\U{codepoint:08x}"


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
    document = decode_json_object(payload, "AnyWidget payload")

    _exact_keys(
        document,
        ("schema", "rootModelId", "files", "modelNotifications"),
        "AnyWidget payload",
    )
    if document["schema"] != ANYWIDGET_PAYLOAD_SCHEMA:
        raise ValueError(f"AnyWidget payload schema must be {_quoted(ANYWIDGET_PAYLOAD_SCHEMA)}")

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
            raise ValueError(f"{notification_path}.model_id must be {_quoted(expected_model_id)}")

        message_path = f"{notification_path}.message"
        message = _record(notification["message"], message_path)
        _exact_keys(
            message,
            ("method", "state", "buffer_paths", "buffers", "esm_spec"),
            message_path,
        )
        if message["method"] != "open":
            raise ValueError(f"AnyWidget model {_quoted(model_id)} must contain an open message")

        state = _record(
            message["state"],
            _diagnostic_path("AnyWidget model ", _quoted(model_id), " state"),
        )
        model_state = cast(dict[str, object], copy.deepcopy(state))
        buffer_paths = _parse_buffer_paths(message["buffer_paths"], model_id)
        buffers = _parse_buffers(message["buffers"], model_id)
        if len(buffer_paths) != len(buffers):
            raise ValueError(
                f"AnyWidget model {_quoted(model_id)} has {len(buffer_paths)} buffer paths "
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
        raise ValueError(f"AnyWidget root model {_quoted(root_model_id)} is missing")
    if not root.has_esm:
        raise ValueError(f"AnyWidget root model {_quoted(root_model_id)} has no ESM spec")

    reachable = _reachable_models(root_model_id, models)
    if len(reachable) != len(models):
        unrelated = [model_id for model_id in models if model_id not in reachable]
        raise ValueError(
            "AnyWidget payload contains models outside the root closure: "
            + _diagnostic_list(unrelated)
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
        file_path = _diagnostic_path("AnyWidget file ", _quoted(path))
        data_url = _non_empty_string(data_url_value, file_path)
        if not data_url.startswith("data:"):
            raise ValueError(f"{file_path} must contain a data URL")
        _validate_data_url(data_url, file_path)
        files[path] = data_url
    return files


def _parse_buffer_paths(value: object, model_id: str) -> tuple[BufferPath, ...]:
    model_path = _diagnostic_path("AnyWidget model ", _quoted(model_id))
    paths = _array(value, _diagnostic_path(model_path, " buffer_paths"))
    result: list[BufferPath] = []
    seen: set[BufferPath] = set()
    for index, value in enumerate(paths):
        raw_path = _array(value, _diagnostic_path(model_path, f" buffer path {index}"))
        if not raw_path:
            raise ValueError(f"{model_path} has an empty buffer path")
        path = tuple(_path_token(token, model_id) for token in raw_path)
        if path in seen:
            raise ValueError(
                f"{model_path} repeats buffer path {_diagnostic_list(path, brackets=True)}"
            )
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
    raise TypeError(f"AnyWidget model {_quoted(model_id)} has an invalid buffer path token")


def _parse_buffers(value: object, model_id: str) -> tuple[str, ...]:
    model_path = _diagnostic_path("AnyWidget model ", _quoted(model_id))
    buffers = _array(value, _diagnostic_path(model_path, " buffers"))
    result: list[str] = []
    for index, value in enumerate(buffers):
        if not isinstance(value, str) or not _is_canonical_base64(value):
            raise ValueError(f"{model_path} buffer {index} is not canonical base64")
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
        f"AnyWidget model {_quoted(model_id)} buffer path "
        f"{_diagnostic_list(path, brackets=True)} does not target existing state"
    )


def _validate_esm_spec(value: object, files: dict[str, str], model_id: str) -> bool:
    from urllib.parse import urlsplit

    if value is None:
        return False
    model_path = _diagnostic_path("AnyWidget model ", _quoted(model_id))
    path = _diagnostic_path(model_path, " ESM spec")
    spec = _record(value, path)
    _exact_keys(spec, ("url", "hash"), path)
    url_path = _diagnostic_path(model_path, " ESM URL")
    url = _non_empty_string(spec["url"], url_path)
    _non_empty_string(spec["hash"], _diagnostic_path(model_path, " ESM hash"))
    if url in files:
        return True

    if _DATA_URL_PREFIX.match(url) is not None:
        _validate_data_url(url, url_path)
        return True
    if not _fits_utf8_byte_limit(url, 0, len(url), _MAX_EXTERNAL_ESM_URL_BYTES):
        _invalid_esm_url(url, model_id)

    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise ValueError(f"{model_path} references missing virtual file {_quoted(url)}") from error
    protocol = parsed.scheme.lower()
    if protocol in {"http", "https"}:
        _validate_http_url(url, parsed, model_id)
        return True
    if not protocol:
        raise ValueError(f"{model_path} references missing virtual file {_quoted(url)}")
    if protocol not in {"data", "http", "https"}:
        raise ValueError(f"{model_path} uses incompatible ESM URL protocol {_quoted(protocol)}")
    raise ValueError(f"{model_path} contains an invalid ESM URL {_quoted(url)}")


def _validate_http_url(value: str, parsed: SplitResult, model_id: str) -> None:
    if _INVALID_HTTP_URL_CHARACTER.search(value) is not None:
        _invalid_esm_url(value, model_id)
    try:
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as error:
        _invalid_esm_url(value, model_id, error)
    if not hostname:
        _invalid_esm_url(value, model_id)
    if ":" in hostname:
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError as error:
            _invalid_esm_url(value, model_id, error)
        return
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        _invalid_esm_url(value, model_id, error)
    if _HTTP_HOST.fullmatch(ascii_hostname) is None:
        _invalid_esm_url(value, model_id)
    for label in ascii_hostname.split("."):
        if not label.lower().startswith("xn--"):
            continue
        try:
            decoded = label.encode("ascii").decode("idna")
            encoded = decoded.encode("idna").decode("ascii")
        except UnicodeError as error:
            _invalid_esm_url(value, model_id, error)
        if encoded.lower() != label.lower():
            _invalid_esm_url(value, model_id)
    if ascii_hostname.replace(".", "").isdigit():
        try:
            ipaddress.IPv4Address(ascii_hostname)
        except ValueError as error:
            _invalid_esm_url(value, model_id, error)


def _invalid_esm_url(
    value: str,
    model_id: str,
    cause: BaseException | None = None,
) -> NoReturn:
    error = ValueError(
        f"AnyWidget model {_quoted(model_id)} contains an invalid ESM URL {_quoted(value)}"
    )
    if cause is None:
        raise error
    raise error from cause


def _validate_data_url(value: str, path: str) -> None:
    comma = value.find(",")
    if comma == -1:
        raise ValueError(f"{path} is a malformed data URL")
    media_type_end = value.find(";", 5, comma)
    if media_type_end == -1:
        media_type_end = comma
    if not _fits_utf8_byte_limit(
        value,
        5,
        media_type_end,
        _MAX_DATA_URL_MEDIA_TYPE_BYTES,
    ):
        raise ValueError(
            f"{path} data URL media type exceeds {_MAX_DATA_URL_MEDIA_TYPE_BYTES} UTF-8 bytes"
        )
    if _BASE64_PARAMETER.search(value, 5, comma + 1) is not None:
        if not _is_canonical_base64(value, comma + 1):
            raise ValueError(f"{path} contains malformed base64 data")
        return
    if not _is_valid_percent_data(value, comma + 1):
        raise ValueError(f"{path} contains malformed percent-encoded data")


def _is_canonical_base64(value: str, start: int = 0) -> bool:
    size = len(value) - start
    if size % 4 != 0:
        return False
    padding = 0
    if size and value[-1] == "=":
        padding = 1
        if size > 1 and value[-2] == "=":
            padding = 2
    content_end = len(value) - padding
    if _BASE64_BODY.fullmatch(value, start, content_end) is None:
        return False
    if padding == 0:
        return True
    final_value = _base64_value(value[content_end - 1])
    return final_value & (0x0F if padding == 2 else 0x03) == 0


def _base64_value(value: str) -> int:
    codepoint = ord(value)
    if 0x41 <= codepoint <= 0x5A:
        return codepoint - 0x41
    if 0x61 <= codepoint <= 0x7A:
        return codepoint - 0x61 + 26
    if 0x30 <= codepoint <= 0x39:
        return codepoint - 0x30 + 52
    return 62 if value == "+" else 63


def _is_valid_percent_data(value: str, start: int) -> bool:
    if value.find("%", start) == -1:
        return True

    index = start
    remaining = 0
    continuation_min = 0x80
    continuation_max = 0xBF
    while index < len(value):
        codepoint = ord(value[index])
        if codepoint > 0x7F:
            if remaining:
                return False
            index += 1
            continue
        if codepoint == 0x25:
            if index + 2 >= len(value):
                return False
            high = _hex_value(value, index + 1)
            low = _hex_value(value, index + 2)
            if high < 0 or low < 0:
                return False
            byte = high * 16 + low
            index += 3
        else:
            byte = codepoint
            index += 1

        if remaining:
            if not continuation_min <= byte <= continuation_max:
                return False
            remaining -= 1
            continuation_min = 0x80
            continuation_max = 0xBF
            continue
        if byte <= 0x7F:
            continue
        if 0xC2 <= byte <= 0xDF:
            remaining = 1
        elif byte == 0xE0:
            remaining = 2
            continuation_min = 0xA0
        elif 0xE1 <= byte <= 0xEC or 0xEE <= byte <= 0xEF:
            remaining = 2
        elif byte == 0xED:
            remaining = 2
            continuation_max = 0x9F
        elif byte == 0xF0:
            remaining = 3
            continuation_min = 0x90
        elif 0xF1 <= byte <= 0xF3:
            remaining = 3
        elif byte == 0xF4:
            remaining = 3
            continuation_max = 0x8F
        else:
            return False
    return remaining == 0


def _hex_value(value: str, index: int) -> int:
    codepoint = ord(value[index])
    if 0x30 <= codepoint <= 0x39:
        return codepoint - 0x30
    if 0x41 <= codepoint <= 0x46:
        return codepoint - 0x37
    if 0x61 <= codepoint <= 0x66:
        return codepoint - 0x57
    return -1


def _fits_utf8_byte_limit(value: str, start: int, end: int, limit: int) -> bool:
    if end - start > limit:
        return False
    length = 0
    for index in range(start, end):
        codepoint = ord(value[index])
        if codepoint <= 0x7F:
            length += 1
        elif codepoint <= 0x7FF:
            length += 2
        elif 0xD800 <= codepoint <= 0xDFFF:
            return False
        elif codepoint <= 0xFFFF:
            length += 3
        else:
            length += 4
        if length > limit:
            return False
    return True


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
            raise ValueError(f"AnyWidget model reference {_quoted(model_id)} is unresolved")
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
        path = _diagnostic_path(path)
        raise TypeError(
            f"{path} fields are invalid. Missing: {_diagnostic_list(missing) or 'none'}. "
            f"Unexpected: {_diagnostic_list(unexpected) or 'none'}."
        )


def _non_empty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{_diagnostic_path(path)} must be a non-empty string")
    return value


def _array(value: object, path: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise TypeError(f"{_diagnostic_path(path)} must be an array")
    return cast(list[JsonValue], value)


def _record(value: object, path: str) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"{_diagnostic_path(path)} must be an object")
    return cast(JsonObject, value)


_BUFFER = object()
