from __future__ import annotations

import pytest
from marimo_export._remote.auth import auth_headers, parse_server_address


def test_server_url_keeps_explicit_access_token_out_of_the_address() -> None:
    address = parse_server_address(
        "http://localhost:3456/workspace",
        access_token="top secret",
    )

    assert address.base_url == "http://localhost:3456/workspace/"
    assert address.access_token == "top secret"
    assert "top secret" not in repr(address)
    assert "access_token" not in address.base_url
    assert auth_headers(address) == {"Authorization": "Bearer top secret"}


def test_authentication_and_skew_tokens_keep_distinct_headers() -> None:
    address = parse_server_address(
        "https://marimo.test/",
        access_token="auth-secret",
    )

    assert auth_headers(address, server_token="skew-secret") == {
        "Authorization": "Bearer auth-secret",
        "Marimo-Server-Token": "skew-secret",
    }


@pytest.mark.parametrize(
    "server",
    [
        "ftp://marimo.test/",
        "http://marimo.test/",
        "https://user:secret@marimo.test/",
        "https://marimo.test/?other=value",
        "https://marimo.test/?access_token=",
        "https://marimo.test/?access_token=a&access_token=b",
        "https://marimo.test/#fragment",
        "https://marimo.test/unsafe path",
        "https://marimo.test/unsafe\x1bpath",
    ],
)
def test_server_url_rejects_ambiguous_or_unsafe_routing(server: str) -> None:
    with pytest.raises(ValueError):
        parse_server_address(server)


def test_server_url_rejects_credentials_in_query_strings() -> None:
    with pytest.raises(ValueError, match="query"):
        parse_server_address("https://marimo.test/?access_token=from-url")


def test_auth_headers_reject_unsafe_header_characters() -> None:
    with pytest.raises(ValueError, match="HTTP header-compatible"):
        parse_server_address(
            "https://marimo.test/",
            access_token="secret\nInjected: value",
        )

    address = parse_server_address("https://marimo.test/")
    with pytest.raises(ValueError, match="HTTP header-compatible"):
        auth_headers(address, server_token="secret\r\nInjected: value")

    with pytest.raises(ValueError, match="HTTP header-compatible"):
        auth_headers(address, server_token="secret\N{EURO SIGN}")
