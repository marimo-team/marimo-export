from __future__ import annotations

import json
from typing import Any

import httpx

from moexport.client._code import marked_text, marker
from moexport.client._types import ScratchpadResult


def execute_scratchpad(
    server: str,
    session_id: str,
    code: str,
    *,
    token: str | None,
    timeout: int,
) -> ScratchpadResult:
    """Execute Python code in a marimo session scratchpad."""

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Marimo-Session-Id": session_id,
    }
    if token:
        headers["Marimo-Server-Token"] = token
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(
                f"{server.rstrip('/')}/api/kernel/execute",
                json={"code": code},
                headers=headers,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Scratchpad execution failed with HTTP {exc.response.status_code}: "
            f"{exc.response.text}"
        ) from exc

    stdout: list[str] = []
    stderr: list[str] = []
    done: dict[str, Any] | None = None
    for event in parse_sse(response.text):
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


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse server-sent events returned by marimo scratchpad execution."""

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


def can_import(
    server: str,
    session_id: str,
    module: str,
    *,
    token: str | None,
) -> bool:
    """Return whether `module` can be imported inside the kernel."""

    probe_marker = marker("IMPORT")
    code = "\n".join(
        [
            "import importlib.util",
            "import json",
            f"__moexport_can_import = importlib.util.find_spec({module!r}) is not None",
            f"print({probe_marker!r} + json.dumps(__moexport_can_import))",
        ]
    )
    result = execute_scratchpad(server, session_id, code, token=token, timeout=10)
    value = json.loads(marked_text(result.stdout, probe_marker))
    if not isinstance(value, bool):
        raise RuntimeError("marimo import probe returned a non-boolean payload")
    return value
