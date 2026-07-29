from __future__ import annotations

import re

from marimo_export._json import json_string

MAX_BLOB_METADATA_JSON_BYTES = 256 * 1024
MAX_MEDIA_TYPE_ASCII_BYTES = 1024

_TRUE_END = r"(?![\s\S])"
_PYTHON_WHITESPACE = (
    r"\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680"
    r"\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
)
_UNICODE_SCALAR_LOOKAHEAD = r"(?![\s\S]*[\uD800-\uDFFF])"
_MEDIA_TOKEN_SCHEMA = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"

MEDIA_TYPE_SCHEMA_PATTERN = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}(?![\s\S]*[^\u0020-\u007e])"
    rf"(?![{_PYTHON_WHITESPACE}])"
    rf"(?![\s\S]*[{_PYTHON_WHITESPACE}]{_TRUE_END})"
    rf"{_MEDIA_TOKEN_SCHEMA}/{_MEDIA_TOKEN_SCHEMA}"
    rf"(?:[{_PYTHON_WHITESPACE}]*;[\s\S]*)?{_TRUE_END}"
)

_MEDIA_TYPE = re.compile(MEDIA_TYPE_SCHEMA_PATTERN)
_MEDIA_TOKEN_CHARACTERS = frozenset(
    "!#$%&'*+.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-"
)


def validate_media_type(value: object, label: str) -> str:
    media_type = json_string(value, label)
    if (
        len(media_type) > MAX_MEDIA_TYPE_ASCII_BYTES
        or _MEDIA_TYPE.fullmatch(media_type) is None
        or not _parameters_are_valid(media_type)
    ):
        raise ValueError(
            f"{label} must use type/subtype syntax in at most "
            f"{MAX_MEDIA_TYPE_ASCII_BYTES} printable ASCII bytes"
        )
    return media_type


def _parameters_are_valid(media_type: str) -> bool:
    offset = 0
    length = len(media_type)

    def token() -> str | None:
        nonlocal offset
        start = offset
        while offset < length and media_type[offset] in _MEDIA_TOKEN_CHARACTERS:
            offset += 1
        return None if offset == start else media_type[start:offset]

    def spaces() -> None:
        nonlocal offset
        while offset < length and media_type[offset] == " ":
            offset += 1

    if token() is None or offset >= length or media_type[offset] != "/":
        return False
    offset += 1
    if token() is None:
        return False
    spaces()
    parameters: set[str] = set()
    while offset < length:
        if media_type[offset] != ";":
            return False
        offset += 1
        spaces()
        name = token()
        if name is None:
            return False
        lowered = name.lower()
        if lowered in parameters:
            return False
        parameters.add(lowered)
        spaces()
        if offset >= length or media_type[offset] != "=":
            return False
        offset += 1
        spaces()
        if offset < length and media_type[offset] == '"':
            offset += 1
            closed = False
            while offset < length:
                character = media_type[offset]
                offset += 1
                if character == '"':
                    closed = True
                    break
                if character == "\\":
                    if offset >= length:
                        return False
                    offset += 1
            if not closed:
                return False
        elif token() is None:
            return False
        spaces()
    return True


__all__ = [
    "MAX_BLOB_METADATA_JSON_BYTES",
    "MAX_MEDIA_TYPE_ASCII_BYTES",
    "MEDIA_TYPE_SCHEMA_PATTERN",
    "validate_media_type",
]
