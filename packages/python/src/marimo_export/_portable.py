from __future__ import annotations

from marimo_export._format import edge_whitespace

MAX_ASSET_KEY_UTF8_BYTES = 1024
MAX_PORTABLE_BASENAME_UTF8_BYTES = 255

_TRUE_END = r"(?![\s\S])"
_UNICODE_SCALAR_LOOKAHEAD = r"(?![\s\S]*[\uD800-\uDFFF])"
_PORTABLE_EDGE_WHITESPACE = (
    r"\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680"
    r"\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_WINDOWS_RESERVED_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_DEVICE_BASENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        *(f"COM{index}" for index in "¹²³"),
        *(f"LPT{index}" for index in "¹²³"),
    }
)
_WINDOWS_DEVICE_SCHEMA = (
    r"(?:[Cc][Oo][Nn](?:[Ii][Nn]\$|[Oo][Uu][Tt]\$)?|"
    r"[Pp][Rr][Nn]|[Aa][Uu][Xx]|[Nn][Uu][Ll]|"
    r"[Cc][Oo][Mm][1-9¹²³]|[Ll][Pp][Tt][1-9¹²³])"
)
_PORTABLE_COMPONENT_SCHEMA = (
    rf"(?![{_PORTABLE_EDGE_WHITESPACE}])"
    rf"(?![^/]*[{_PORTABLE_EDGE_WHITESPACE}](?:/|{_TRUE_END}))"
    rf"(?!\.{{1,2}}(?:/|{_TRUE_END}))"
    rf"(?!{_WINDOWS_DEVICE_SCHEMA}[ .]*(?:\.[^/]*)?(?:/|{_TRUE_END}))"
    rf"(?![^/]*[. ](?:/|{_TRUE_END}))"
    r"[^<>:\"/\\|?*\u0000-\u001f\u007f]{1,255}"
)

PORTABLE_BASENAME_SCHEMA_PATTERN = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}{_PORTABLE_COMPONENT_SCHEMA}{_TRUE_END}"
)
NOTEBOOK_BASENAME_SCHEMA_PATTERN = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}(?![\s\S]*[\u0000/])[\s\S]+{_TRUE_END}"
)
ASSET_KEY_SCHEMA_PATTERN = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}(?=[\s\S]+\.bin{_TRUE_END})"
    rf"{_PORTABLE_COMPONENT_SCHEMA}(?:/{_PORTABLE_COMPONENT_SCHEMA})*{_TRUE_END}"
)


def validate_portable_basename(value: object, label: str) -> str:
    """Return a Windows-portable base filename."""

    name = _nonempty_string(value, label)
    _validate_unicode_scalar(name, label)
    _validate_portable_component(name, label)
    return name


def validate_notebook_basename(value: object, label: str) -> str:
    """Return an active notebook source basename."""

    name = _nonempty_string(value, label)
    _validate_unicode_scalar(name, label)
    if "\x00" in name or "/" in name:
        raise ValueError(f"{label} must be a notebook source basename")
    return name


def validate_asset_key(value: object, label: str) -> str:
    """Return a portable relative ``.bin`` cache key."""

    key, _ = _validated_asset_key(value, label)
    return key


def asset_key_components(value: object, label: str) -> tuple[str, ...]:
    """Return the validated components of a portable cache key."""

    _, components = _validated_asset_key(value, label)
    return components


def _validated_asset_key(value: object, label: str) -> tuple[str, tuple[str, ...]]:
    key = _nonempty_string(value, label)
    _validate_unicode_scalar(key, label)
    if len(key.encode("utf-8")) > MAX_ASSET_KEY_UTF8_BYTES or not key.endswith(".bin"):
        raise ValueError(f"{label} must be a portable relative .bin cache key")
    components = tuple(key.split("/"))
    try:
        for component in components:
            _validate_portable_component(component, f"{label} component")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a portable relative .bin cache key") from error
    return key, components


def _validate_portable_component(value: str, label: str) -> None:
    if not value or value in {".", ".."}:
        raise ValueError(f"{label} must be a portable filename")
    basename = value.split(".", 1)[0].rstrip(" .").upper()
    if (
        len(value.encode("utf-8")) > MAX_PORTABLE_BASENAME_UTF8_BYTES
        or edge_whitespace(value)
        or any(
            ord(character) < 32
            or ord(character) == 127
            or character in _WINDOWS_RESERVED_CHARACTERS
            for character in value
        )
        or value.endswith((".", " "))
        or basename in _WINDOWS_DEVICE_BASENAMES
    ):
        raise ValueError(f"{label} must be a portable filename")


def _validate_unicode_scalar(value: str, label: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError(f"{label} must contain Unicode scalar values")


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{label} must be a non-empty string")
    return value


__all__ = [
    "ASSET_KEY_SCHEMA_PATTERN",
    "MAX_ASSET_KEY_UTF8_BYTES",
    "MAX_PORTABLE_BASENAME_UTF8_BYTES",
    "NOTEBOOK_BASENAME_SCHEMA_PATTERN",
    "PORTABLE_BASENAME_SCHEMA_PATTERN",
    "asset_key_components",
    "validate_asset_key",
    "validate_notebook_basename",
    "validate_portable_basename",
]
