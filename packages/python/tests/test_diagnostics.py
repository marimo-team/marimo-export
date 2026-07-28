from __future__ import annotations

from marimo_export._diagnostics import redact_credentials, safe_diagnostic


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
