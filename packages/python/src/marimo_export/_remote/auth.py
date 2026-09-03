from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class ServerAddress:
    """A normalized marimo server address and its extracted credential."""

    base_url: str
    access_token: str | None = field(default=None, repr=False)


def parse_server_address(
    server: str,
    *,
    access_token: str | None = None,
) -> ServerAddress:
    """Parse a marimo URL and keep credentials in dedicated arguments."""

    if not isinstance(server, str) or not server:
        raise ValueError("server must be a non-empty HTTP or HTTPS URL.")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in server
    ):
        raise ValueError("server must not contain whitespace or control characters.")
    if access_token is not None and (not isinstance(access_token, str) or not access_token):
        raise ValueError("access_token must be a non-empty string when provided.")
    if access_token is not None:
        _validate_credential(access_token, "access_token")

    try:
        parsed = urlsplit(server)
        port = parsed.port
    except ValueError as error:
        raise ValueError("server must be a valid HTTP or HTTPS URL.") from error

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("server must use http:// or https://.")
    if parsed.hostname is None:
        raise ValueError("server must contain a host.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("server must not contain user information.")
    if parsed.fragment:
        raise ValueError("server must not contain a fragment.")
    if parsed.query:
        raise ValueError("server must not contain a query string.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("server port must be between 1 and 65535.")
    if parsed.scheme.lower() == "http" and not _loopback(parsed.hostname):
        raise ValueError("plain HTTP servers must use a loopback host.")

    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    base_url = urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            path,
            "",
            "",
        )
    )
    return ServerAddress(
        base_url=base_url,
        access_token=access_token,
    )


def auth_headers(
    address: ServerAddress,
    *,
    server_token: str | None = None,
) -> dict[str, str]:
    """Return marimo authentication headers without conflating token roles."""

    if server_token is not None and (not isinstance(server_token, str) or not server_token):
        raise ValueError("server_token must be a non-empty string when provided.")
    if server_token is not None:
        _validate_credential(server_token, "server_token")
    headers: dict[str, str] = {}
    if address.access_token is not None:
        headers["Authorization"] = f"Bearer {address.access_token}"
    if server_token is not None:
        headers["Marimo-Server-Token"] = server_token
    return headers


def _validate_credential(value: str, label: str) -> None:
    if any(
        ord(character) < 32 or ord(character) == 127 or ord(character) > 255 for character in value
    ):
        raise ValueError(f"{label} must contain HTTP header-compatible characters.")


def _loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
