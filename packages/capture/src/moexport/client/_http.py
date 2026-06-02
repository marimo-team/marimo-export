from __future__ import annotations

import json
from typing import Any

import httpx


def post_json(
    server: str,
    path: str,
    *,
    body: Any | None,
    headers: dict[str, str] | None,
    token: str | None,
    timeout: int,
) -> dict[str, Any]:
    """POST JSON to one marimo server endpoint."""

    request_headers = {"Accept": "application/json", **(headers or {})}
    data: bytes | None = None
    if body is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if token:
        request_headers["Marimo-Server-Token"] = token
        request_headers["Authorization"] = f"Bearer {token}"

    request_kwargs: dict[str, Any] = {"headers": request_headers}
    if body is not None:
        request_kwargs["content"] = data
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(
                f"{server.rstrip('/')}/{path.lstrip('/')}",
                **request_kwargs,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"POST {path} failed with HTTP {exc.response.status_code}: "
            f"{exc.response.text}"
        ) from exc
    return json.loads(response.text) if response.text else {}
