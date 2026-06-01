"""HTTP helpers for driving export capture from a live marimo session."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScratchpadResult:
    """Result returned by a marimo scratchpad execution request."""

    stdout: list[str]
    stderr: list[str]
    output: Any | None = None


def resolve_session(
    *,
    server: str,
    notebook: str | None,
    session_id: str | None,
    token: str | None,
) -> dict[str, Any]:
    """Resolve one running marimo session for `notebook`."""

    if session_id:
        return {
            "sessionId": session_id,
            "name": None,
            "path": notebook,
            "initializationId": None,
        }

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
        return matches[0]
    if not matches:
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


def ensure_runtime(
    *,
    server: str,
    session_id: str,
    package: str,
    force: bool,
    token: str | None,
) -> None:
    """Install `package` into a live kernel when `moexport` is unavailable."""

    if not force and can_import(server, session_id, "moexport", token=token):
        return

    post_json(
        server,
        "/api/kernel/install_missing_packages",
        body={
            "manager": "uv",
            "source": "kernel",
            "versions": {package: ""},
        },
        headers={"Marimo-Session-Id": session_id},
        token=token,
        timeout=30,
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        if can_import(server, session_id, "moexport", token=token):
            return
        time.sleep(1)
    raise TimeoutError("Timed out waiting for moexport in the live kernel")


def can_import(
    server: str,
    session_id: str,
    module: str,
    *,
    token: str | None,
) -> bool:
    """Return whether `module` can be imported inside the live kernel."""

    code = (
        "import importlib.util\n"
        f"assert importlib.util.find_spec({module!r}) is not None\n"
    )
    try:
        execute_scratchpad(server, session_id, code, token=token, timeout=10)
        return True
    except Exception:
        return False


def execute_scratchpad(
    server: str,
    session_id: str,
    code: str,
    *,
    token: str | None,
    timeout: int,
) -> ScratchpadResult:
    """Execute Python code in a live marimo session scratchpad."""

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Marimo-Session-Id": session_id,
    }
    if token:
        headers["Marimo-Server-Token"] = token
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        f"{server.rstrip('/')}/api/kernel/execute",
        data=json.dumps({"code": code}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Scratchpad execution failed with HTTP {exc.code}: {detail}"
        ) from exc

    stdout: list[str] = []
    stderr: list[str] = []
    done: dict[str, Any] | None = None
    for event in parse_sse(text):
        data = event.get("data")
        if event["event"] == "stdout" and isinstance(data, dict):
            value = data.get("data")
            if isinstance(value, str):
                stdout.append(value)
        elif event["event"] == "stderr" and isinstance(data, dict):
            value = data.get("data")
            if isinstance(value, str):
                stderr.append(value)
        elif event["event"] == "done" and isinstance(data, dict):
            done = data

    if done is None:
        raise RuntimeError("marimo scratchpad stream ended without a done event")
    if done.get("success") is False:
        error = done.get("error")
        message = (
            error.get("msg") if isinstance(error, dict) else "marimo scratchpad failed"
        )
        raise RuntimeError(str(message))
    return ScratchpadResult(stdout=stdout, stderr=stderr, output=done.get("output"))


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

    request = urllib.request.Request(
        f"{server.rstrip('/')}/{path.lstrip('/')}",
        data=data,
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"POST {path} failed with HTTP {exc.code}: {detail}"
        ) from exc
    return json.loads(text) if text else {}


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse the server-sent events returned by marimo scratchpad execution."""

    events: list[dict[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        event_name: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())
        if event_name is None:
            continue
        raw_data = "\n".join(data_lines)
        events.append(
            {"event": event_name, "data": json.loads(raw_data) if raw_data else None}
        )
    return events


def notebook_matches(item: dict[str, Any], query: str) -> bool:
    """Return whether a running notebook record matches a path or filename."""

    path = str(item.get("path") or "")
    name = str(item.get("name") or "")
    return path == query or name == query or path.endswith(f"/{query}")


__all__ = [
    "ScratchpadResult",
    "can_import",
    "ensure_runtime",
    "execute_scratchpad",
    "notebook_matches",
    "parse_sse",
    "post_json",
    "resolve_session",
]
