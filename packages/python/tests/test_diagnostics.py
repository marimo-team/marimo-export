from __future__ import annotations

from marimo_export._diagnostics import (
    cleanup_failures,
    record_cleanup_failure,
    redact_credentials,
    safe_diagnostic,
)


def test_safe_diagnostic_redacts_url_and_configured_credentials() -> None:
    value = safe_diagnostic(
        "failed for https://user:password@example.test/?access_token=url-secret ",
        "server-secret",
        secrets=("server-secret",),
    )

    assert value == (
        "failed for https://<redacted>@example.test/?access_token=<redacted> <redacted>"
    )


def test_safe_diagnostic_escapes_controls_and_bounds_rendered_text() -> None:
    value = safe_diagnostic("line\n", "\N{EURO SIGN}" * 20, maximum_chars=32)

    assert value == r"line\x0a\u20ac\u20ac\u20ac..."
    assert len(value) <= 32


def test_redact_credentials_recognizes_encoded_query_separator() -> None:
    assert (
        redact_credentials("https://example.test/?access_token%3Dsecret&next=1")
        == "https://example.test/?access_token%3D<redacted>&next=1"
    )


def test_cleanup_failures_preserve_structured_secondary_diagnostics() -> None:
    primary = ValueError("primary")

    record_cleanup_failure(primary, "managed process cleanup", RuntimeError("secret"))
    record_cleanup_failure(primary, "managed file cleanup", OSError("private path"))

    assert cleanup_failures(primary) == (
        "managed process cleanup also failed: RuntimeError",
        "managed file cleanup also failed: OSError",
    )
