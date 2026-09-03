from __future__ import annotations

import re
from collections.abc import Iterable
from typing import cast

_DEFAULT_MAXIMUM_CHARS = 4096
_CLEANUP_FAILURES_ATTRIBUTE = "_marimo_export_cleanup_failures"
_URL_USERINFO_PATTERN = re.compile(r"(?i)(\bhttps?://)[^/?#\s@]+@")
_QUERY_CREDENTIAL_PATTERN = re.compile(
    r"(?i)((?:[?&]|\b)(?:access_token|server_token|token|api_key)(?:=|%3d))"
    r"([^&#\s'\"<>]+)"
)


def safe_diagnostic(
    *parts: object,
    secrets: Iterable[str] = (),
    maximum_chars: int = _DEFAULT_MAXIMUM_CHARS,
) -> str:
    """Render a bounded ASCII diagnostic with credentials redacted."""

    if not isinstance(maximum_chars, int) or isinstance(maximum_chars, bool) or maximum_chars < 4:
        raise ValueError("maximum_chars must be an integer of at least four")
    text_parts: list[str] = []
    for part in parts:
        try:
            text_parts.append(str(part))
        except Exception:
            text_parts.append(f"<{type(part).__name__}>")
    redacted = redact_credentials("".join(text_parts), secrets=secrets)
    return _ascii_prefix(redacted, maximum_chars)


def redact_credentials(value: str, *, secrets: Iterable[str] = ()) -> str:
    """Redact URL credentials and configured secret values from text."""

    if not isinstance(value, str):
        raise TypeError("diagnostic value must be a string")
    result = _URL_USERINFO_PATTERN.sub(r"\1<redacted>@", value)
    result = _QUERY_CREDENTIAL_PATTERN.sub(r"\1<redacted>", result)
    configured = sorted(
        {secret for secret in secrets if isinstance(secret, str) and secret},
        key=len,
        reverse=True,
    )
    for secret in configured:
        result = result.replace(secret, "<redacted>")
    return result


def record_cleanup_failure(
    primary: BaseException,
    operation: str,
    failure: BaseException,
) -> None:
    """Attach one bounded cleanup diagnostic to the primary failure."""

    diagnostic = safe_diagnostic(operation, " also failed: ", type(failure).__name__)
    setattr(
        primary,
        _CLEANUP_FAILURES_ATTRIBUTE,
        (*cleanup_failures(primary), diagnostic),
    )


def cleanup_failures(error: BaseException) -> tuple[str, ...]:
    """Return cleanup diagnostics attached while preserving the primary failure."""

    value = getattr(error, _CLEANUP_FAILURES_ATTRIBUTE, ())
    if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
        return ()
    return cast(tuple[str, ...], value)


def _ascii_prefix(value: str, maximum_chars: int) -> str:
    result: list[str] = []
    length = 0
    content_limit = maximum_chars - 3
    for character in value:
        codepoint = ord(character)
        if 0x20 <= codepoint <= 0x7E:
            escaped = character
        elif codepoint <= 0xFF:
            escaped = f"\\x{codepoint:02x}"
        elif codepoint <= 0xFFFF:
            escaped = f"\\u{codepoint:04x}"
        else:
            escaped = f"\\U{codepoint:08x}"
        if length + len(escaped) > content_limit:
            result.append("...")
            return "".join(result)
        result.append(escaped)
        length += len(escaped)
    return "".join(result)


__all__ = [
    "cleanup_failures",
    "record_cleanup_failure",
    "redact_credentials",
    "safe_diagnostic",
]
