"""Public live-session connection APIs."""

from __future__ import annotations

from marimo_export.client import Client, Session


def connect(
    server: str,
    *,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 30.0,
) -> Client:
    """Return a client connected to one Marimo server."""

    return Client(
        server,
        access_token=access_token,
        server_token=server_token,
        timeout=timeout,
    )


__all__ = ["Client", "Session", "connect"]
