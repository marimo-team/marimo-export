from __future__ import annotations

import hashlib
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

import pytest
from marimo_export import Client, ControlDescription, open_publication

pytestmark = pytest.mark.skipif(
    os.environ.get("MARIMO_EXPORT_REMOTE_INTEGRATION") != "1",
    reason="set MARIMO_EXPORT_REMOTE_INTEGRATION=1 to run the remote integration proof",
)

_WORKSPACE = Path(__file__).resolve().parents[3]
_SESSION_ID = "s_integration"
_HTTP_TIMEOUT = 30.0
_START_TIMEOUT = 30.0
_EVENT_LIMIT = 16 * 1024 * 1024
_ACCESS_TOKEN = "marimo-export-integration-token"

_NOTEBOOK = """\
import marimo

__generated_with = "0.23.14"
app = marimo.App()


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _(mo):
    category = mo.ui.dropdown(
        options=["alpha", "beta"],
        value="alpha",
        label="Category",
    )
    factor = mo.ui.number(
        start=1,
        stop=10,
        step=1,
        value=2,
        label="Factor",
    )
    mo.hstack([category, factor])
    return category, factor


@app.cell
def _(category, factor):
    base = 10 if category.value == "alpha" else 20
    summary = {
        "category": category.value,
        "factor": factor.value,
        "score": base * factor.value,
    }
    return (summary,)


@app.cell
def report(mo, summary):
    mo.Html(
        f'<p data-report="true">{summary["category"]}: '
        f'{summary["score"]}</p>'
    )
    return


if __name__ == "__main__":
    app.run()
"""

_SPEC = """\
schema: marimo-export.spec.v1
variants:
  current: {}
  changed:
    category: [beta]
    factor: 4
outputs:
  summary:
    source: summary
    formats:
      json: {}
  doubled:
    source:
      expression: summary["score"] * 2
    formats:
      json: {}
  report:
    source:
      cell: report
    formats:
      html: {}
"""

_UNSAVED_REPORT = """\
mo.Html(
    f'<p data-report="true" data-source="unsaved-edit">'
    f'live {summary["category"]}: {summary["score"]}</p>'
)
"""


@dataclass
class _Server:
    base_url: str
    process: subprocess.Popen[bytes]
    log_path: Path
    log_file: BinaryIO

    @property
    def client_url(self) -> str:
        return f"{self.base_url}?access_token={_ACCESS_TOKEN}"

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_file.close()

    def logs(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        except OSError:
            return ""


class _SessionStream:
    """Keep one edit session attached while the public client borrows it."""

    def __init__(self, server: _Server, session_id: str) -> None:
        self._server = server
        self._session_id = session_id
        self._messages: queue.Queue[dict[str, object]] = queue.Queue()
        self._failure: BaseException | None = None
        self._closed = threading.Event()
        request = urllib.request.Request(
            f"{server.base_url}/sse?session_id={session_id}",
            headers={"Authorization": f"Bearer {_ACCESS_TOKEN}"},
            method="GET",
        )
        self._response = urllib.request.urlopen(request, timeout=120)
        self._thread = threading.Thread(
            target=self._read,
            name="marimo-export-integration-sse",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._closed.set()
        self._response.close()
        self._thread.join(timeout=2)

    def wait_for(self, operation: str, timeout: float = _HTTP_TIMEOUT) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while True:
            if self._failure is not None:
                raise RuntimeError("marimo session stream failed") from self._failure
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"marimo session did not emit {operation!r}:\n{self._server.logs()}"
                )
            try:
                message = self._messages.get(timeout=min(remaining, 0.2))
            except queue.Empty:
                continue
            if message.get("op") == operation:
                return message

    def wait_until_idle(self, timeout: float = _HTTP_TIMEOUT) -> None:
        statuses: dict[str, str] = {}
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"marimo notebook did not finish its initial run:\n{self._server.logs()}"
                )
            message = self._next_message(remaining)
            operation = message.get("op")
            data = message.get("data")
            if operation == "cell-op" and isinstance(data, dict):
                cell_id = data.get("cell_id")
                status = data.get("status")
                if isinstance(cell_id, str) and isinstance(status, str):
                    statuses[cell_id] = status
            if operation == "completed-run":
                active = {
                    cell_id: status for cell_id, status in statuses.items() if status != "idle"
                }
                assert active == {}, f"cells remained active after completed-run: {active}"
                return

    def wait_for_cell_run(self, cell_id: str, timeout: float = _HTTP_TIMEOUT) -> None:
        statuses: dict[str, str] = {}
        saw_target = False
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"marimo cell {cell_id!r} did not finish:\n{self._server.logs()}"
                )
            message = self._next_message(remaining)
            operation = message.get("op")
            data = message.get("data")
            if operation == "cell-op" and isinstance(data, dict):
                message_cell_id = data.get("cell_id")
                status = data.get("status")
                if isinstance(message_cell_id, str) and isinstance(status, str):
                    statuses[message_cell_id] = status
                    saw_target = saw_target or message_cell_id == cell_id
            if operation == "completed-run" and saw_target:
                assert statuses.get(cell_id) == "idle", (
                    f"cell {cell_id!r} did not return to idle: {statuses.get(cell_id)!r}"
                )
                return

    def _next_message(self, timeout: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while True:
            if self._failure is not None:
                raise RuntimeError("marimo session stream failed") from self._failure
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("marimo session event stream timed out")
            try:
                return self._messages.get(timeout=min(remaining, 0.2))
            except queue.Empty:
                continue

    def _read(self) -> None:
        data_lines: list[bytes] = []
        event_bytes = 0
        try:
            while not self._closed.is_set():
                line = self._response.readline(_EVENT_LIMIT + 1)
                if not line:
                    break
                if len(line) > _EVENT_LIMIT:
                    raise ValueError("marimo session event exceeded the test transport limit")
                line = line.rstrip(b"\r\n")
                if not line:
                    if data_lines:
                        payload = b"\n".join(data_lines).decode("utf-8", errors="strict")
                        value = json.loads(payload)
                        if isinstance(value, dict):
                            self._messages.put(cast(dict[str, object], value))
                    data_lines = []
                    event_bytes = 0
                    continue
                if line.startswith(b"data:"):
                    value = line[5:].removeprefix(b" ")
                    event_bytes += len(value)
                    if event_bytes > _EVENT_LIMIT:
                        raise ValueError("marimo session event exceeded the test transport limit")
                    data_lines.append(value)
        except BaseException as error:
            if not self._closed.is_set():
                self._failure = error


def test_running_notebook_capture_is_cache_backed_and_statically_readable(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    spec = tmp_path / "notebook.export.yaml"
    notebook.write_text(_NOTEBOOK, encoding="utf-8")
    spec.write_text(_SPEC, encoding="utf-8")
    notebook_source = notebook.read_bytes()
    source_digest = hashlib.sha256(notebook_source).hexdigest()

    server = _start_server(notebook, tmp_path)
    stream: _SessionStream | None = None
    stopped = False
    try:
        stream = _SessionStream(server, _SESSION_ID)
        stream.wait_for("kernel-ready")
        _post_json(
            server.base_url,
            "/api/kernel/instantiate",
            {"objectIds": [], "values": [], "autoRun": True},
            session_id=_SESSION_ID,
            access_token=_ACCESS_TOKEN,
        )
        stream.wait_until_idle()

        cold_path = tmp_path / "cold-publication"
        warm_path = tmp_path / "warm-publication"
        cli_path = tmp_path / "cli-publication"
        with Client(server.client_url, timeout=_HTTP_TIMEOUT) as client:
            session = client.session(_SESSION_ID)
            before = session.inspect()
            controls_before = _control_values(before.controls)

            assert before.filename == notebook.name
            assert {"summary", "category", "factor"} <= {
                global_value.name for global_value in before.globals
            }
            assert "report" in {cell.name for cell in before.cells}
            assert controls_before == {"category": ["alpha"], "factor": 2}

            report_cell = next(cell for cell in before.cells if cell.name == "report")
            report_cell_id = report_cell.id
            _post_json(
                server.base_url,
                "/api/document/transaction",
                {
                    "changes": [
                        {
                            "type": "set-code",
                            "cellId": report_cell_id,
                            "code": _UNSAVED_REPORT,
                        }
                    ]
                },
                session_id=_SESSION_ID,
                access_token=_ACCESS_TOKEN,
            )
            _post_json(
                server.base_url,
                "/api/kernel/run",
                {"cellIds": [report_cell_id], "codes": [_UNSAVED_REPORT]},
                session_id=_SESSION_ID,
                access_token=_ACCESS_TOKEN,
            )
            stream.wait_for_cell_run(report_cell_id)
            live = session.inspect()

            assert live.document_sha256 != before.document_sha256
            assert notebook.read_bytes() == notebook_source

            cold = session.capture(spec=spec, into=cold_path)
            after_cold = session.inspect()
            warm = session.capture(spec=spec, into=warm_path)
            after_warm = session.inspect()

            assert set(cold.variants) == {"current", "changed"}
            assert set(cold.outputs) == {"summary", "doubled", "report"}
            assert cold.assets == 6
            assert cold.cache.hits == 0
            assert cold.cache.misses == 6
            assert cold.cache.skipped == 0
            assert warm.assets == 6
            assert warm.cache.hits == 6
            assert warm.cache.misses == 0
            assert warm.cache.skipped == 0
            assert after_cold.document_sha256 == live.document_sha256
            assert after_warm.document_sha256 == live.document_sha256
            assert _control_values(after_cold.controls) == controls_before
            assert _control_values(after_warm.controls) == controls_before

        assert hashlib.sha256(notebook.read_bytes()).hexdigest() == source_digest
        assert server.process.poll() is None
        sessions = _get_json(
            server.base_url,
            "/api/sessions",
            access_token=_ACCESS_TOKEN,
        )
        active_session = _object(sessions[_SESSION_ID])
        assert active_session["filename"] == str(notebook)

        publication = open_publication(warm_path)
        description = publication.describe()
        assert description["schema"] == "marimo-export.publication.v1"
        assert set(_object(description["variants"])) == {"current", "changed"}
        assert set(publication.variant_names) == {"current", "changed"}
        assert publication.variant("current").output("summary").format("json").json() == {
            "category": "alpha",
            "factor": 2,
            "score": 20,
        }
        assert publication.variant("current").output("doubled").format("json").json() == 40
        assert publication.variant("changed").output("summary").format("json").json() == {
            "category": "beta",
            "factor": 4,
            "score": 80,
        }
        assert publication.variant("changed").output("doubled").format("json").json() == 160
        changed_report = publication.variant("changed").output("report").format("html").text()
        assert 'data-source="unsaved-edit"' in changed_report
        assert "live beta" in changed_report
        assert "beta" in changed_report
        assert "80" in changed_report
        assert publication.verify() == 6

        cache_assets = sorted((warm_path / "cache").rglob("*.bin"))
        assert len(cache_assets) == 6
        assert all(asset.read_bytes() for asset in cache_assets)

        cli_capture = _run_cli(
            "capture",
            server.client_url,
            "--session",
            _SESSION_ID,
            "--spec",
            str(spec),
            "--output",
            str(cli_path),
            "--json",
        )
        cli_assets = sorted((cli_path / "cache").rglob("*.bin"))
        cli_bytes = sum(asset.stat().st_size for asset in cli_assets)
        assert cli_capture == {
            "ok": True,
            "result": {
                "assets": 6,
                "bytes_transferred": cli_bytes,
                "cache": {"hits": 6, "misses": 0, "skipped": 0},
                "outputs": ["doubled", "report", "summary"],
                "path": str(cli_path),
                "session_id": _SESSION_ID,
                "variants": ["changed", "current"],
            },
        }
        assert len(cli_assets) == 6

        cli_session = _run_cli(
            "session",
            server.client_url,
            "--session",
            _SESSION_ID,
            "--json",
        )
        cli_session_data = _object(cli_session["result"])
        assert cli_session_data["document_sha256"] == live.document_sha256
        cli_controls = cli_session_data["controls"]
        assert isinstance(cli_controls, list)
        assert _control_values_from_list(cli_controls) == controls_before
        stream.close()
        stream = None
        server.stop()
        stopped = True

        detached = open_publication(cli_path)
        assert detached.variant("changed").output("summary").format("json").json() == {
            "category": "beta",
            "factor": 4,
            "score": 80,
        }
        inspect_result = _run_cli("inspect", str(cli_path), "--json")
        inspect_data = _object(inspect_result["result"])
        assert inspect_data["schema"] == "marimo-export.publication.v1"
        read_result = _run_cli(
            "read",
            str(cli_path),
            "summary",
            "--variant",
            "changed",
            "--format",
            "json",
            "--json",
        )
        read_data = _object(read_result["result"])
        assert read_data["value"] == {
            "category": "beta",
            "factor": 4,
            "score": 80,
        }
        verify_result = _run_cli("verify", str(cli_path), "--json")
        verify_data = _object(verify_result["result"])
        assert verify_data["verified"] is True
        assert verify_data["assets"] == 6
        assert notebook.read_bytes() == notebook_source
    finally:
        if stream is not None:
            stream.close()
        if not stopped:
            server.stop()


def _control_values(controls: tuple[ControlDescription, ...]) -> dict[str, object]:
    return {control.name: control.value for control in controls}


def _control_values_from_list(controls: Sequence[object]) -> dict[str, object]:
    return {
        cast(str, control["name"]): control["value"]
        for item in controls
        for control in (_object(item),)
    }


def _object(value: object) -> Mapping[str, object]:
    assert isinstance(value, dict)
    return cast(Mapping[str, object], value)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return cast(int, stream.getsockname()[1])


def _start_server(notebook: Path, root: Path) -> _Server:
    port = _free_port()
    log_path = root / "marimo-server.log"
    log_file = log_path.open("wb")
    environment = dict(os.environ)
    environment.update(
        {
            "MARIMO_SKIP_UPDATE_CHECK": "1",
            "NO_COLOR": "1",
            "XDG_STATE_HOME": str(root / "state"),
        }
    )
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "marimo",
            "edit",
            str(notebook),
            "--headless",
            "--token-password",
            _ACCESS_TOKEN,
            "--no-skew-protection",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=_WORKSPACE,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    server = _Server(
        base_url=f"http://127.0.0.1:{port}",
        process=process,
        log_path=log_path,
        log_file=log_file,
    )
    try:
        _wait_for_server(server)
    except BaseException:
        server.stop()
        raise
    return server


def _wait_for_server(server: _Server) -> None:
    deadline = time.monotonic() + _START_TIMEOUT
    while time.monotonic() < deadline:
        if server.process.poll() is not None:
            raise RuntimeError(
                f"marimo server exited with {server.process.returncode}:\n{server.logs()}"
            )
        try:
            request = urllib.request.Request(
                f"{server.base_url}/api/sessions",
                headers={"Authorization": f"Bearer {_ACCESS_TOKEN}"},
            )
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.05)
    raise TimeoutError(f"marimo server did not become ready:\n{server.logs()}")


def _post_json(
    base_url: str,
    path: str,
    value: dict[str, object],
    *,
    session_id: str,
    access_token: str,
) -> dict[str, object]:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(value, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Marimo-Session-Id": session_id,
            "Authorization": f"Bearer {access_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:
        result = json.loads(response.read().decode("utf-8", errors="strict"))
    assert isinstance(result, dict)
    return cast(dict[str, object], result)


def _get_json(base_url: str, path: str, *, access_token: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:
        result = json.loads(response.read().decode("utf-8", errors="strict"))
    assert isinstance(result, dict)
    return cast(dict[str, object], result)


def _run_cli(*arguments: str) -> dict[str, object]:
    executable = Path(sys.executable).with_name("marimo-export")
    assert executable.is_file(), f"CLI entrypoint is missing: {executable}"
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=_WORKSPACE,
        capture_output=True,
        text=True,
        timeout=_HTTP_TIMEOUT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    assert value.get("ok") is True
    return cast(dict[str, object], value)
