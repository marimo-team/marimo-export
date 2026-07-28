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


def validate_media_type(value: object, label: str) -> str:
    media_type = json_string(value, label)
    if len(media_type) > MAX_MEDIA_TYPE_ASCII_BYTES or _MEDIA_TYPE.fullmatch(media_type) is None:
        raise ValueError(
            f"{label} must use type/subtype syntax in at most "
            f"{MAX_MEDIA_TYPE_ASCII_BYTES} printable ASCII bytes"
        )
    return media_type


__all__ = [
    "MAX_BLOB_METADATA_JSON_BYTES",
    "MAX_MEDIA_TYPE_ASCII_BYTES",
    "MEDIA_TYPE_SCHEMA_PATTERN",
    "validate_media_type",
]
