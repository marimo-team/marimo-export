from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, DecimalException
from typing import NoReturn, TypeAlias, cast

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
JsonInput: TypeAlias = str | bytes | bytearray | memoryview
_ScanJsonString: TypeAlias = Callable[[str, int, bool], tuple[str, int]]

_MAX_SAFE_INTEGER = 2**53 - 1
_MAX_JSON_DEPTH = 256
_MAX_JSON_VALUES = 100_000
_MAX_JSON_NUMBER_CHARS = 1024
_MAX_DIAGNOSTIC_PATH = 512
_MAX_DIAGNOSTIC_VALUE = 256
_TRUNCATED_PATH = "..."
_LEADING_JSON_WHITESPACE = re.compile(rb"[ \t\r\n]*")
_TRAILING_JSON_WHITESPACE = re.compile(rb"[ \t\r\n]*\Z")
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_SCAN_JSON_STRING = cast(_ScanJsonString, vars(json.decoder)["scanstring"])


class _DuplicateJsonKey(ValueError):
    pass


class _InvalidJsonSyntax(ValueError):
    pass


def json_value(
    value: object,
    path: str = "value",
    *,
    max_values: int = _MAX_JSON_VALUES,
) -> JsonValue:
    """Return a detached JSON value or raise at the first incompatible value."""

    _validate_max_values(max_values)
    return _json_value(value, path, 0, [0], max_values)


def _json_value(
    value: object,
    path: str,
    depth: int,
    count: list[int],
    max_values: int,
) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"{path} exceeds the maximum JSON nesting depth")
    _count_json_value(count, path, max_values)

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return json_string(value, path)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        result: JsonObject = {}
        for key, item in value.items():
            key_path = _bounded_path(path, "<object key>")
            _count_json_value(count, key_path, max_values)
            key = json_string(key, key_path)
            result[key] = _json_value(
                item,
                _bounded_path(path, key),
                depth + 1,
                count,
                max_values,
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _json_value(
                item,
                _bounded_path(path, f"[{index}]"),
                depth + 1,
                count,
                max_values,
            )
            for index, item in enumerate(value)
        ]
    raise TypeError(f"{path} must be JSON-compatible, got {type(value).__name__}")


def _count_json_value(count: list[int], path: str, max_values: int) -> None:
    count[0] += 1
    if count[0] > max_values:
        raise ValueError(f"{path} exceeds the maximum JSON value count")


def _validate_max_values(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > _MAX_SAFE_INTEGER
    ):
        raise TypeError("max_values must be a positive safe integer")
    return value


def _bounded_path(path: str, segment: str) -> str:
    if path == _TRUNCATED_PATH:
        return path
    if len(path) >= _MAX_DIAGNOSTIC_PATH:
        return _TRUNCATED_PATH
    remaining = _MAX_DIAGNOSTIC_PATH - len(path) - 1
    shown = _bounded_string_repr(segment, remaining)
    if not shown:
        return _TRUNCATED_PATH
    return f"{path}.{shown}"


def _bounded_string_repr(value: str, max_length: int) -> str:
    if max_length < 2:
        return ""
    content_limit = max_length - 2
    content: list[str] = []
    length = 0
    for character in value:
        escaped = _escape_diagnostic_character(character)
        if length + len(escaped) > content_limit:
            while content and length + len(_TRUNCATED_PATH) > content_limit:
                length -= len(content.pop())
            if len(_TRUNCATED_PATH) <= content_limit:
                content.append(_TRUNCATED_PATH)
            break
        content.append(escaped)
        length += len(escaped)
    return f"'{''.join(content)}'"


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


def json_string(value: object, path: str = "value") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError(f"{path} must contain Unicode scalar values")
    return value


def json_object(value: object, path: str = "value") -> JsonObject:
    parsed = json_value(value, path)
    if not isinstance(parsed, dict):
        raise TypeError(f"{path} must be an object")
    return parsed


def portable_json_object(value: object, path: str = "value") -> JsonObject:
    """Return a JSON object whose numbers preserve ECMAScript value identity."""

    parsed = json_object(value, path)
    _validate_portable_numbers(parsed, path)
    return parsed


def _validate_portable_numbers(value: JsonValue, path: str) -> None:
    pending: list[tuple[JsonValue, str]] = [(value, path)]
    while pending:
        item, item_path = pending.pop()
        if isinstance(item, bool) or item is None or isinstance(item, str):
            continue
        if isinstance(item, int):
            if abs(item) > _MAX_SAFE_INTEGER:
                raise ValueError(f"{item_path} integer must be within the JavaScript safe range")
            continue
        if isinstance(item, float):
            if item.is_integer() and abs(item) > _MAX_SAFE_INTEGER:
                raise ValueError(f"{item_path} integer must be within the JavaScript safe range")
            continue
        if isinstance(item, list):
            pending.extend(
                (child, _bounded_path(item_path, f"[{index}]")) for index, child in enumerate(item)
            )
            continue
        pending.extend(
            (child, _bounded_path(item_path, key))
            for key, child in cast(dict[str, JsonValue], item).items()
        )


def json_equal(left: JsonValue, right: JsonValue) -> bool:
    """Compare validated JSON trees using JSON type and number semantics."""

    pending = [(left, right)]
    while pending:
        current_left, current_right = pending.pop()
        if current_left is current_right:
            continue
        if current_left is None or current_right is None:
            return False
        if isinstance(current_left, bool) or isinstance(current_right, bool):
            if not (
                isinstance(current_left, bool)
                and isinstance(current_right, bool)
                and current_left == current_right
            ):
                return False
            continue
        if isinstance(current_left, (int, float)) or isinstance(current_right, (int, float)):
            if not (
                isinstance(current_left, (int, float))
                and isinstance(current_right, (int, float))
                and current_left == current_right
            ):
                return False
            continue
        if isinstance(current_left, str) or isinstance(current_right, str):
            if not (
                isinstance(current_left, str)
                and isinstance(current_right, str)
                and current_left == current_right
            ):
                return False
            continue
        if isinstance(current_left, list) or isinstance(current_right, list):
            if not (
                isinstance(current_left, list)
                and isinstance(current_right, list)
                and len(current_left) == len(current_right)
            ):
                return False
            pending.extend(zip(current_left, current_right, strict=True))
            continue
        if not isinstance(current_left, dict) or not isinstance(current_right, dict):
            return False
        if current_left.keys() != current_right.keys():
            return False
        pending.extend((value, current_right[key]) for key, value in current_left.items())
    return True


def canonical_bytes(value: object) -> bytes:
    """Serialize a portable value with the publication's canonical JSON rules."""

    parsed = json_value(value)
    chunks: list[str] = []
    _write_canonical(parsed, chunks)
    return "".join(chunks).encode("utf-8")


def _write_canonical(value: JsonValue, chunks: list[str]) -> None:
    if value is None:
        chunks.append("null")
    elif value is True:
        chunks.append("true")
    elif value is False:
        chunks.append("false")
    elif isinstance(value, str):
        chunks.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    elif isinstance(value, int):
        chunks.append(str(value))
    elif isinstance(value, float):
        chunks.append(_ecmascript_number(value))
    elif isinstance(value, list):
        chunks.append("[")
        for index, item in enumerate(value):
            if index:
                chunks.append(",")
            _write_canonical(item, chunks)
        chunks.append("]")
    else:
        chunks.append("{")
        for index, key in enumerate(sorted(value)):
            if index:
                chunks.append(",")
            chunks.append(json.dumps(key, ensure_ascii=False, separators=(",", ":")))
            chunks.append(":")
            _write_canonical(value[key], chunks)
        chunks.append("}")


def _ecmascript_number(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("canonical JSON cannot contain NaN or infinity")
    if value == 0:
        return "0"

    absolute = abs(value)
    rendered = repr(value).lower()
    if 1e-6 <= absolute < 1e21:
        if "e" in rendered:
            rendered = format(Decimal(rendered), "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered

    if "e" not in rendered:
        decimal = Decimal(rendered)
        exponent = decimal.adjusted()
        digits = "".join(str(digit) for digit in decimal.copy_abs().normalize().as_tuple().digits)
        mantissa = digits[0]
        if len(digits) > 1:
            mantissa += f".{digits[1:]}"
        rendered = f"{'-' if value < 0 else ''}{mantissa}e{exponent:+d}"

    mantissa, exponent_text = rendered.split("e", 1)
    mantissa = mantissa.rstrip("0").rstrip(".")
    exponent = int(exponent_text)
    return f"{mantissa}e{exponent:+d}" if exponent >= 0 else f"{mantissa}e{exponent}"


def decode_json(
    data: JsonInput,
    path: str = "value",
    *,
    max_values: int = _MAX_JSON_VALUES,
) -> JsonValue:
    """Decode strict JSON, rejecting duplicate keys and non-finite numbers."""

    text = _decode_json_text(data, path)
    _validate_max_values(max_values)
    try:
        _preflight_json(text, path, max_values)
    except _DuplicateJsonKey as error:
        duplicate_key = _consume_duplicate_key(error)
        text = ""
        raise ValueError(f"{path} contains duplicate key {duplicate_key}") from None
    except _InvalidJsonSyntax as error:
        error.__traceback__ = None
        error.__context__ = None

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonKey(_bounded_string_repr(key, _MAX_DIAGNOSTIC_VALUE))
            result[key] = value
        return result

    def invalid_constant(value: str) -> object:
        raise ValueError(f"{path} must not contain {value}")

    def parse_float(value: str) -> int | float:
        if _zero_float_lexeme(value):
            return -0.0 if value.startswith("-") else 0.0
        try:
            exact = Decimal(value)
        except (DecimalException, ValueError) as error:
            raise ValueError(f"{path} contains an invalid JSON number") from error
        if not exact.is_finite():
            raise ValueError(f"{path} must not contain a non-finite number")
        try:
            converted = float(exact)
        except (OverflowError, ValueError) as error:
            raise ValueError(f"{path} must not contain a non-finite number") from error
        if not math.isfinite(converted):
            raise ValueError(f"{path} must not contain a non-finite number")
        lexeme_is_integral = _decimal_lexeme_is_integral(value)
        if not lexeme_is_integral and converted.is_integer():
            raise ValueError(f"{path} contains a JSON number that loses its fractional component")
        if converted.is_integer() and abs(converted) > _MAX_SAFE_INTEGER:
            raise ValueError(f"{path} integer must be within the JavaScript safe range")
        return converted

    def parse_int(value: str) -> int | float:
        # JSON.parse preserves the sign of lexical -0, so keep the same wire
        # value when Python's decoder would otherwise normalize it to int(0).
        if value == "-0":
            return -0.0
        return int(value)

    syntax_message = ""
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=invalid_constant,
            parse_float=parse_float,
            parse_int=parse_int,
        )
    except json.JSONDecodeError as error:
        syntax_message = error.msg
        error.doc = ""
        error.__traceback__ = None
        error.__context__ = None
        data = ""
        text = ""
    except _DuplicateJsonKey as error:
        duplicate_key = _consume_duplicate_key(error)
        text = ""
        raise ValueError(f"{path} contains duplicate key {duplicate_key}") from None
    except RecursionError as error:
        raise ValueError(f"{path} exceeds the maximum JSON nesting depth") from error
    else:
        return json_value(decoded, path, max_values=max_values)
    raise ValueError(f"{path} is invalid JSON: {syntax_message}") from None


def _consume_duplicate_key(error: _DuplicateJsonKey) -> str:
    shown = str(error)
    error.__traceback__ = None
    error.__context__ = None
    return shown


def _zero_float_lexeme(value: str) -> bool:
    significand = re.split("[eE]", value, maxsplit=1)[0].lstrip("-")
    return all(character in "0." for character in significand)


def _decimal_lexeme_is_integral(value: str) -> bool:
    unsigned = value.lstrip("-")
    significand, marker, exponent_text = unsigned.lower().partition("e")
    fractional_digits = len(significand) - significand.find(".") - 1 if "." in significand else 0
    exponent = int(exponent_text) if marker else 0
    if exponent >= fractional_digits:
        return True
    digits = significand.replace(".", "")
    required_zeros = fractional_digits - exponent
    return required_zeros <= len(digits) and digits.endswith("0" * required_zeros)


def _decode_json_text(data: JsonInput, path: str) -> str:
    if isinstance(data, str):
        return data
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"{path} must be JSON text or a contiguous byte buffer")

    try:
        source = memoryview(data)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{path} must be JSON text or a contiguous byte buffer") from error
    with source:
        if not source.c_contiguous:
            raise TypeError(f"{path} byte buffer must be C-contiguous")
        with source.cast("B") as octets:
            leading = _LEADING_JSON_WHITESPACE.match(octets)
            start = 0 if leading is None else leading.end()
            trailing = _TRAILING_JSON_WHITESPACE.search(octets, start)
            end = len(octets) if trailing is None else trailing.start()
            with octets[start:end] as payload:
                try:
                    return str(payload, "utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError(f"{path} must be UTF-8 JSON") from error


def _preflight_json(text: str, path: str, max_values: int) -> None:
    count = 0
    index = 0
    length = len(text)
    containers: list[tuple[str, str, set[str] | None]] = []
    root_state = "value"

    def count_value() -> None:
        nonlocal count
        count += 1
        if count > max_values:
            raise ValueError(f"{path} exceeds the maximum JSON value count")
        if len(containers) > _MAX_JSON_DEPTH:
            raise ValueError(f"{path} exceeds the maximum JSON nesting depth")

    def replace_state(state: str) -> None:
        kind, _, keys = containers[-1]
        containers[-1] = (kind, state, keys)

    def invalidate() -> NoReturn:
        raise _InvalidJsonSyntax

    def begin_value() -> None:
        nonlocal root_state
        if not containers:
            if root_state != "value":
                invalidate()
            else:
                root_state = "active"
            return
        kind, state, _ = containers[-1]
        allowed = state == "value" if kind == "object" else state in {"value", "value_or_end"}
        if not allowed:
            invalidate()

    def complete_value() -> None:
        nonlocal root_state
        if not containers:
            root_state = "done"
            return
        kind, state, _ = containers[-1]
        allowed = state == "value" if kind == "object" else state in {"value", "value_or_end"}
        if allowed:
            replace_state("after_value")
        else:
            invalidate()

    while index < length:
        character = text[index]
        if character in " \t\r\n":
            index += 1
            continue
        if character == '"':
            count_value()
            is_key = bool(
                containers
                and containers[-1][0] == "object"
                and containers[-1][1] in {"key", "key_or_end"}
            )
            if is_key:
                try:
                    key, index = _SCAN_JSON_STRING(text, index + 1, True)
                except json.JSONDecodeError:
                    invalidate()
                keys = containers[-1][2]
                if keys is None:
                    invalidate()
                if key in keys:
                    raise _DuplicateJsonKey(_bounded_string_repr(key, _MAX_DIAGNOSTIC_VALUE))
                keys.add(key)
                replace_state("colon")
                continue
            begin_value()
            index = _skip_json_string(text, index)
            complete_value()
            continue
        if character in "[{":
            count_value()
            begin_value()
            if character == "{":
                containers.append(("object", "key_or_end", set()))
            else:
                containers.append(("array", "value_or_end", None))
            index += 1
            continue
        if character in "]}":
            if not containers:
                invalidate()
            kind, state, _ = containers[-1]
            expected = "}" if kind == "object" else "]"
            allowed = (
                state in {"key_or_end", "after_value"}
                if kind == "object"
                else state in {"value_or_end", "after_value"}
            )
            if character != expected or not allowed:
                invalidate()
            containers.pop()
            complete_value()
            index += 1
            continue
        if character == ":":
            if containers and containers[-1][0] == "object" and containers[-1][1] == "colon":
                replace_state("value")
            else:
                invalidate()
            index += 1
            continue
        if character == ",":
            if containers and containers[-1][1] == "after_value":
                replace_state("key" if containers[-1][0] == "object" else "value")
            else:
                invalidate()
            index += 1
            continue
        if character == "-" or character in "0123456789":
            count_value()
            start = index
            matched_number = _JSON_NUMBER.match(text, index)
            if matched_number is None:
                invalidate()
            index = matched_number.end()
            if index < length and text[index] in "0123456789+-.eE":
                invalidate()
            if index - start > _MAX_JSON_NUMBER_CHARS:
                raise ValueError(
                    f"{path} contains a JSON number longer than {_MAX_JSON_NUMBER_CHARS} characters"
                )
            begin_value()
            complete_value()
            continue
        matched_literal = False
        for literal in ("true", "false", "null"):
            if text.startswith(literal, index):
                count_value()
                begin_value()
                index += len(literal)
                complete_value()
                matched_literal = True
                break
        if not matched_literal:
            invalidate()


def _skip_json_string(text: str, index: int) -> int:
    index += 1
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 1
            if index >= len(text):
                raise _InvalidJsonSyntax
            escape = text[index]
            if escape == "u":
                digits = text[index + 1 : index + 5]
                if len(digits) != 4 or any(
                    digit not in "0123456789abcdefABCDEF" for digit in digits
                ):
                    raise _InvalidJsonSyntax
                index += 5
                continue
            if escape not in '"\\/bfnrt':
                raise _InvalidJsonSyntax
            index += 1
            continue
        if ord(character) < 0x20:
            raise _InvalidJsonSyntax
        index += 1
        if character == '"':
            return index
    raise _InvalidJsonSyntax


def decode_json_object(
    data: JsonInput,
    path: str = "value",
    *,
    max_values: int = _MAX_JSON_VALUES,
) -> JsonObject:
    decoded = decode_json(data, path, max_values=max_values)
    if not isinstance(decoded, dict):
        raise TypeError(f"{path} must be a JSON object")
    return cast(JsonObject, decoded)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
