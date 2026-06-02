from __future__ import annotations

import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from moexport.client._http import post_json
from moexport.client._types import SessionInfo
from websockets.sync.client import connect


def resolve_session(
    *,
    server: str,
    notebook: str | None,
    session_id: str | None,
    token: str | None,
) -> SessionInfo:
    """Resolve or open one marimo session for `notebook`."""

    if session_id:
        return SessionInfo(session_id=session_id, path=notebook)

    response = post_json(
        server,
        "/api/home/running_notebooks",
        body=None,
        token=token,
        headers=None,
        timeout=30,
    )
    files = response.get("files")
    if not isinstance(files, list):
        raise RuntimeError("marimo did not return a running notebook list")

    matches = [
        item
        for item in files
        if isinstance(item, dict)
        and item.get("sessionId")
        and (not notebook or notebook_matches(item, notebook))
    ]
    if len(matches) == 1:
        return session_info(matches[0])
    if len(matches) > 1 and all(
        session_key(match) == session_key(matches[0]) for match in matches
    ):
        return session_info(matches[0])
    if not matches:
        if notebook:
            return open_notebook(
                server=server,
                notebook=notebook,
                token=token,
            )
        available = ", ".join(
            str(item.get("path") or item.get("name")) for item in files
        )
        raise RuntimeError(
            f"No running notebook matched {notebook!r}. Available: {available}"
        )

    available = ", ".join(str(item.get("path") or item.get("name")) for item in matches)
    raise RuntimeError(
        f"More than one running notebook matched {notebook!r}: {available}"
    )


def notebook_matches(item: dict[str, Any], query: str) -> bool:
    """Return whether a running notebook record matches a path or filename."""

    path = str(item.get("path") or "")
    name = str(item.get("name") or "")
    return path == query or name == query or path.endswith(f"/{query}")


def open_notebook(
    *,
    server: str,
    notebook: str,
    token: str | None,
    timeout: int = 30,
) -> SessionInfo:
    """Open `notebook` through marimo and wait for kernel readiness."""

    session_id = random_session_id()
    url = notebook_websocket_url(
        server=server,
        notebook=notebook,
        session_id=session_id,
        token=token,
    )
    deadline = time.monotonic() + timeout
    ready = False

    with connect(url, open_timeout=timeout, close_timeout=1) as socket:
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            message = parse_websocket_message(socket.recv(timeout=remaining))
            if message.get("op") == "kernel-ready":
                ready = True
                break

    if not ready:
        raise TimeoutError(
            f"Timed out opening marimo notebook session for {notebook!r}."
        )

    post_json(
        server,
        "/api/kernel/instantiate",
        body={"objectIds": [], "values": [], "autoRun": True},
        token=token,
        headers={"Marimo-Session-Id": session_id},
        timeout=timeout,
    )

    return SessionInfo(
        session_id=session_id,
        name=notebook.rsplit("/", 1)[-1] or notebook,
        path=notebook,
        initialization_id=None,
    )


def session_info(item: dict[str, Any]) -> SessionInfo:
    """Normalize one marimo running-notebook record."""

    session_id = item.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("marimo session record did not include sessionId")

    return SessionInfo(
        session_id=session_id,
        name=optional_string(item.get("name")),
        path=optional_string(item.get("path")),
        initialization_id=optional_string(item.get("initializationId")),
    )


def optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def session_key(item: dict[str, Any]) -> str:
    return f"{item.get('path') or ''}\0{item.get('name') or ''}"


def random_session_id() -> str:
    return f"s_{secrets.token_hex(6)}"


def notebook_websocket_url(
    *,
    server: str,
    notebook: str,
    session_id: str,
    token: str | None,
) -> str:
    base = urljoin(f"{server.rstrip('/')}/", "ws")
    parts = urlsplit(base)
    scheme = "wss" if parts.scheme == "https" else "ws"
    query = {
        "session_id": session_id,
        "file": notebook,
    }
    if token:
        query["access_token"] = token
    return urlunsplit(
        (
            scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            "",
        )
    )


def parse_websocket_message(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
