"""HTTP helpers for driving export capture from a live marimo session."""

from __future__ import annotations

import json
import time
import base64
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias

import httpx

from moexport.spec import ExportSpec, parse_export_spec

SpecInput: TypeAlias = ExportSpec | Mapping[str, Any]


@dataclass(frozen=True)
class ScratchpadResult:
    """Result returned by a marimo scratchpad execution request."""

    stdout: list[str]
    stderr: list[str]
    output: Any | None = None


@dataclass(frozen=True)
class RuntimeInstall:
    """Runtime installation requested before capture."""

    package: str
    module: str = "moexport"
    force: bool = False


@dataclass(frozen=True)
class LiveCapture:
    """Capture exports from one live marimo server."""

    server: str
    notebook: str | None = None
    session_id: str | None = None
    token: str | None = None
    runtime: Literal["preinstalled"] | RuntimeInstall = "preinstalled"

    def session(self) -> dict[str, Any]:
        """Resolve the running marimo session used by this client."""

        return resolve_session(
            server=self.server,
            notebook=self.notebook,
            session_id=self.session_id,
            token=self.token,
        )

    def export(
        self,
        spec: SpecInput,
        *,
        to: str | Path | None = None,
        runtime: Literal["preinstalled"] | RuntimeInstall | None = None,
        timeout: int = 120,
    ) -> dict[str, Any]:
        """Write a static export bundle from the resolved live session."""

        session = self.session()
        self._ensure_runtime(session, runtime)
        marker = _marker("CAPTURE")
        result = execute_scratchpad(
            self.server,
            str(session["sessionId"]),
            _capture_code(spec, to=to, marker=marker),
            token=self.token,
            timeout=timeout,
        )
        payload = _marked_payload(result.stdout, marker)
        payload["session"] = session
        return payload

    def archive(
        self,
        spec: SpecInput,
        *,
        runtime: Literal["preinstalled"] | RuntimeInstall | None = None,
        timeout: int = 120,
    ) -> bytes:
        """Return zip bytes for an in-memory static export bundle."""

        session = self.session()
        self._ensure_runtime(session, runtime)
        marker = _marker("ARCHIVE")
        result = execute_scratchpad(
            self.server,
            str(session["sessionId"]),
            _archive_code(spec, marker=marker),
            token=self.token,
            timeout=timeout,
        )
        payload = _marked_text(result.stdout, marker)
        return base64.b64decode(payload)

    def _ensure_runtime(
        self,
        session: dict[str, Any],
        runtime: Literal["preinstalled"] | RuntimeInstall | None,
    ) -> None:
        requested = runtime if runtime is not None else self.runtime
        if requested == "preinstalled":
            if can_import(
                self.server,
                str(session["sessionId"]),
                "moexport",
                token=self.token,
            ):
                return
            raise RuntimeError(
                "moexport is not importable in the live kernel. "
                "Pass RuntimeInstall(package=...) to install it before capture."
            )
        if not isinstance(requested, RuntimeInstall):
            raise TypeError(
                "runtime must be 'preinstalled' or RuntimeInstall(package=...)"
            )
        ensure_runtime(
            server=self.server,
            session_id=str(session["sessionId"]),
            package=requested.package,
            module=requested.module,
            force=requested.force,
            token=self.token,
        )


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
    module: str = "moexport",
) -> None:
    """Install `package` into a live kernel when `moexport` is unavailable."""

    if not force and can_import(server, session_id, module, token=token):
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
        if can_import(server, session_id, module, token=token):
            return
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for {module} in the live kernel")


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


def _capture_code(
    spec: SpecInput,
    *,
    to: str | Path | None,
    marker: str,
) -> str:
    spec_json = _spec_json(spec)
    to_expression = "None" if to is None else json.dumps(str(to))
    return "\n".join(
        [
            "import json",
            "import moexport as mox",
            f"__moexport_spec = json.loads({json.dumps(spec_json)})",
            f"__moexport_result = await mox.export(__moexport_spec, to={to_expression})",
            "__moexport_payload = {",
            '    "bundle_path": __moexport_result.bundle_path,',
            '    "manifest_path": __moexport_result.manifest_path,',
            '    "invocation_path": __moexport_result.invocation_path,',
            '    "invocation_index_path": __moexport_result.invocation_index_path,',
            '    "manifest": __moexport_result.manifest,',
            '    "invocation": __moexport_result.invocation,',
            "}",
            f"print({json.dumps(marker)} + json.dumps(__moexport_payload, allow_nan=False))",
        ]
    )


def _archive_code(spec: SpecInput, *, marker: str) -> str:
    spec_json = _spec_json(spec)
    return "\n".join(
        [
            "import json",
            "import moexport.archive as __moexport_archive",
            f"__moexport_spec = json.loads({json.dumps(spec_json)})",
            f"await __moexport_archive.emit_bundle_archive(__moexport_spec, marker={json.dumps(marker)})",
        ]
    )


def _marked_payload(stdout: list[str], marker: str) -> dict[str, Any]:
    payload = _marked_text(stdout, marker)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("capture payload was not a JSON object")
    return data


def _marked_text(stdout: list[str], marker: str) -> str:
    for line in reversed(stdout):
        if line.startswith(marker):
            return line[len(marker) :]
    raise RuntimeError("marimo scratchpad output did not include the capture marker")


def _marker(kind: str) -> str:
    return f"__MOEXPORT_{kind}_{time.time_ns()}__"


def _spec_json(spec: SpecInput) -> str:
    normalized = parse_export_spec(spec).model_dump(mode="json", exclude_none=True)
    return json.dumps(normalized, allow_nan=False)


__all__ = [
    "LiveCapture",
    "RuntimeInstall",
    "ScratchpadResult",
    "can_import",
    "ensure_runtime",
    "execute_scratchpad",
    "notebook_matches",
    "parse_sse",
    "post_json",
    "resolve_session",
]
