from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import BinaryIO, cast
from urllib.parse import urlencode

from marimo_export._diagnostics import safe_diagnostic
from marimo_export._json import JsonObject, json_object
from marimo_export._remote.sse import SSEError, SSEParser
from marimo_export.errors import TransportError

_EVENT_LIMIT = 40 * 1024 * 1024
_HTTP_RESPONSE_LIMIT = 1024 * 1024
_LOG_LIMIT = 8192


class ManagedServer:
    """One authenticated loopback marimo server owned by a build."""

    def __init__(self, notebook: Path, *, timeout: float) -> None:
        self.notebook = notebook
        self.timeout = timeout
        self.access_token = secrets.token_urlsafe(32)
        self.session_id = f"s_{secrets.token_hex(16)}"
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._temporary = tempfile.TemporaryDirectory(prefix="marimo-export-build-")
        temporary_path = Path(self._temporary.name)
        self._log_path = temporary_path / "server.log"
        self._log_file: BinaryIO = self._log_path.open("wb")
        self._process: subprocess.Popen[bytes] | None = None
        self._stream: _SessionStream | None = None
        environment = dict(os.environ)
        environment.update(
            {
                "MARIMO_SKIP_UPDATE_CHECK": "1",
                "NO_COLOR": "1",
            }
        )
        try:
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "marimo",
                    "edit",
                    str(notebook),
                    "--headless",
                    "--token-password",
                    self.access_token,
                    "--no-skew-protection",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                ],
                cwd=notebook.parent,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
            )
            self._wait_ready()
        except BaseException:
            self._stop_process()
            self._close_files()
            raise

    def activate(self) -> None:
        """Open the edit session and finish its initial autorun."""

        if self._stream is not None:
            raise RuntimeError("managed server session is already active")
        stream = _SessionStream(self, timeout=self.timeout)
        self._stream = stream
        stream.wait_for_kernel()
        completed_runs = stream.completed_runs
        self._post_json(
            "/api/kernel/instantiate",
            {"objectIds": [], "values": [], "autoRun": True},
        )
        stream.wait_for_completed_run(completed_runs)

    def stop(self) -> None:
        """Stop the edit stream and owned process within the build timeout."""

        failures: list[str] = []
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception as error:
                failures.append(safe_diagnostic(type(error).__name__))
            self._stream = None
        try:
            self._stop_process()
        except Exception as error:
            failures.append(safe_diagnostic(type(error).__name__))
        self._close_files()
        if failures:
            raise TransportError(
                "the managed marimo server could not be stopped",
                code="server_shutdown_failed",
                details={"failures": failures},
            )

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            process = self._process
            if process is None:
                raise RuntimeError("managed process was not started")
            return_code = process.poll()
            if return_code is not None:
                raise TransportError(
                    "the managed marimo server exited during startup",
                    code="server_start_failed",
                    details={
                        "return_code": return_code,
                        "log": self._logs(),
                    },
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError(
                    "the managed marimo server did not become ready",
                    code="server_start_failed",
                    details={"log": self._logs()},
                )
            request = urllib.request.Request(
                f"{self.base_url}/api/sessions",
                headers=self._headers(),
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=min(1.0, remaining),
                ) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(min(0.05, max(remaining, 0)))

    def _post_json(self, path: str, value: Mapping[str, object]) -> JsonObject:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(
                dict(value),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            headers={
                **self._headers(),
                "Content-Type": "application/json",
                "Marimo-Session-Id": self.session_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read(_HTTP_RESPONSE_LIMIT + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TransportError(
                "the managed marimo session request failed",
                code="server_start_failed",
            ) from error
        if len(payload) > _HTTP_RESPONSE_LIMIT:
            raise TransportError(
                "the managed marimo session response exceeded its limit",
                code="server_start_failed",
            )
        try:
            decoded = json.loads(payload.decode("utf-8", errors="strict"))
            return json_object(decoded, "managed marimo response")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise TransportError(
                "the managed marimo session returned an invalid response",
                code="server_start_failed",
            ) from error

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _stop_process(self) -> None:
        process = self._process
        if process is None:
            return
        self._process = None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=min(self.timeout, 5.0))
                except subprocess.TimeoutExpired as error:
                    raise TransportError(
                        "the managed marimo process did not stop",
                        code="server_shutdown_failed",
                    ) from error

    def _close_files(self) -> None:
        if not self._log_file.closed:
            self._log_file.close()
        self._temporary.cleanup()

    def _logs(self) -> str:
        try:
            data = self._log_path.read_bytes()
        except OSError:
            return ""
        return safe_diagnostic(
            data[-_LOG_LIMIT:].decode("utf-8", errors="replace"),
            secrets=(self.access_token,),
            maximum_chars=_LOG_LIMIT,
        )


class _SessionStream:
    def __init__(self, server: ManagedServer, *, timeout: float) -> None:
        self._server = server
        self._timeout = timeout
        self._condition = threading.Condition()
        self._kernel_ready = False
        self._completed_runs = 0
        self._cell_statuses: dict[str, str] = {}
        self._failure: BaseException | None = None
        self._closed = threading.Event()
        query = urlencode({"session_id": server.session_id})
        request = urllib.request.Request(
            f"{server.base_url}/sse?{query}",
            headers=server._headers(),
            method="GET",
        )
        try:
            self._response = urllib.request.urlopen(request, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TransportError(
                "the managed marimo edit stream could not be opened",
                code="server_start_failed",
            ) from error
        self._thread = threading.Thread(
            target=self._read,
            name="marimo-export-managed-session",
            daemon=True,
        )
        self._thread.start()

    @property
    def completed_runs(self) -> int:
        with self._condition:
            return self._completed_runs

    def wait_for_kernel(self) -> None:
        self._wait(lambda: self._kernel_ready, "kernel readiness")

    def wait_for_completed_run(self, after: int) -> None:
        self._wait(lambda: self._completed_runs > after, "initial notebook run")
        with self._condition:
            active = {
                cell_id: status
                for cell_id, status in self._cell_statuses.items()
                if status != "idle"
            }
        if active:
            raise TransportError(
                "the managed notebook left cells active after its initial run",
                code="server_start_failed",
                details={"cells": sorted(active)[:16]},
            )

    def close(self) -> None:
        self._closed.set()
        self._response.close()
        self._thread.join(timeout=min(self._timeout, 5.0))
        if self._thread.is_alive():
            raise TransportError(
                "the managed marimo edit stream did not close",
                code="server_shutdown_failed",
            )

    def _wait(self, predicate: Callable[[], bool], label: str) -> None:
        deadline = time.monotonic() + self._timeout
        with self._condition:
            while not predicate():
                if self._failure is not None:
                    raise TransportError(
                        f"the managed marimo {label} stream failed",
                        code="server_start_failed",
                    ) from self._failure
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError(
                        f"the managed marimo {label} timed out",
                        code="server_start_failed",
                    )
                self._condition.wait(timeout=min(remaining, 0.2))

    def _read(self) -> None:
        parser = SSEParser(_EVENT_LIMIT)
        try:
            while not self._closed.is_set():
                line = self._response.readline(_EVENT_LIMIT + 1)
                if not line:
                    break
                if len(line) > _EVENT_LIMIT:
                    raise SSEError("managed session event exceeded its limit")
                for event in parser.feed(line):
                    self._handle(event.data)
            for event in parser.close():
                self._handle(event.data)
            if not self._closed.is_set():
                raise EOFError("managed session stream ended")
        except BaseException as error:
            if not self._closed.is_set():
                with self._condition:
                    self._failure = error
                    self._condition.notify_all()

    def _handle(self, payload: str) -> None:
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(value, dict):
            return
        operation = value.get("op")
        data = value.get("data")
        with self._condition:
            if operation == "kernel-ready":
                self._kernel_ready = True
            elif operation == "cell-op" and isinstance(data, dict):
                cell_id = data.get("cell_id")
                status = data.get("status")
                if isinstance(cell_id, str) and isinstance(status, str):
                    self._cell_statuses[cell_id] = status
            elif operation == "completed-run":
                self._completed_runs += 1
            self._condition.notify_all()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return cast(int, stream.getsockname()[1])


__all__ = ["ManagedServer"]
