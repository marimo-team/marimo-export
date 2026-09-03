from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol, cast
from urllib.parse import urlencode

from marimo_export._diagnostics import record_cleanup_failure, safe_diagnostic
from marimo_export._json import JsonObject, json_object
from marimo_export._remote.sse import SSEError, SSEParser
from marimo_export.errors import TransportError
from marimo_export.integration import _owned_session_environment

_EVENT_LIMIT = 40 * 1024 * 1024
_HTTP_RESPONSE_LIMIT = 1024 * 1024
_LOG_LIMIT = 8192
_MANAGED_CACHE_COMPAT_ENV = "MARIMO_EXPORT_MANAGED_CACHE_COMPAT"
_MANAGED_CACHE_ACTIVATION_ENV = "MARIMO_EXPORT_MANAGED_CACHE_ACTIVATION"
_MANAGED_CACHE_TOKEN_ENV = "MARIMO_EXPORT_MANAGED_CACHE_TOKEN"
_MANAGED_SOURCE_ENV = "MARIMO_EXPORT_MANAGED_SOURCE"
_MANAGED_SNAPSHOT_ENV = "MARIMO_EXPORT_MANAGED_SNAPSHOT"
_KERNEL_LIFESPAN_ALLOWLIST_ENV = "MARIMO_KERNEL_LIFESPAN_ALLOWLIST"
_KERNEL_LIFESPAN_DENYLIST_ENV = "MARIMO_KERNEL_LIFESPAN_DENYLIST"
_KERNEL_LIFESPAN_NAME = "marimo-export"
_MARIMO_ANCESTOR_PID_ENV = "MARIMO_ANCESTOR_PID"


@dataclass(frozen=True, slots=True)
class _ActivationTimings:
    session_start_seconds: float
    initial_autorun_seconds: float


class _ProcessTreeOwner(Protocol):
    def terminate(self) -> None: ...

    def wait(self, timeout: float) -> bool: ...

    def close(self) -> None: ...


class ManagedServer:
    """One authenticated loopback marimo server owned by a build."""

    def __init__(
        self,
        notebook: Path,
        *,
        timeout: float,
        runtime_notebook: Path | None = None,
    ) -> None:
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
        self._windows_job: _ProcessTreeOwner | None = None
        self._stream: _SessionStream | None = None
        self._owned_groups: set[int] = set()
        self._activation_path = temporary_path / "kernel-cache-active"
        self._activation_token = secrets.token_hex(32)
        environment = dict(os.environ)
        environment.update(
            {
                _MANAGED_CACHE_COMPAT_ENV: "1",
                _MANAGED_CACHE_ACTIVATION_ENV: str(self._activation_path),
                _MANAGED_CACHE_TOKEN_ENV: self._activation_token,
                _MANAGED_SNAPSHOT_ENV: str(notebook),
                _MANAGED_SOURCE_ENV: str(runtime_notebook or notebook),
                _MARIMO_ANCESTOR_PID_ENV: str(os.getpid()),
                "MARIMO_SKIP_UPDATE_CHECK": "1",
                "NO_COLOR": "1",
            }
        )
        environment.update(_owned_session_environment())
        try:
            _require_kernel_lifespan_policy(environment)
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "marimo_export._marimo.managed_server",
                    "edit",
                    str(notebook),
                    "--headless",
                    "--token-password-file",
                    "-",
                    "--no-skew-protection",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                ],
                cwd=notebook.parent,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                creationflags=_managed_creation_flags(),
                start_new_session=sys.platform != "win32",
            )
            if sys.platform == "win32":
                self._windows_job = _own_windows_process_tree(self._process)
            else:
                self._owned_groups.add(self._process.pid)
            self._send_access_token()
            self._wait_ready()
        except BaseException as error:
            try:
                self._stop_process()
            except BaseException as cleanup_error:
                record_cleanup_failure(error, "managed process cleanup", cleanup_error)
            try:
                self._close_files()
            except BaseException as cleanup_error:
                record_cleanup_failure(error, "managed file cleanup", cleanup_error)
            raise

    def activate(self) -> _ActivationTimings:
        """Open the edit session and finish its initial autorun."""

        if self._stream is not None:
            raise RuntimeError("managed server session is already active")
        session_started = time.monotonic()
        stream = _SessionStream(self, timeout=self.timeout)
        self._stream = stream
        stream.wait_for_kernel()
        self._record_owned_process_groups()
        instantiated_runs = stream.completed_runs
        self._post_json(
            "/api/kernel/instantiate",
            {"objectIds": [], "values": [], "autoRun": False},
        )
        stream.wait_for_completed_run(instantiated_runs)
        self._require_cache_activation()
        session_start_seconds = time.monotonic() - session_started
        completed_runs = stream.completed_runs
        autorun_started = time.monotonic()
        self._post_json(
            "/api/kernel/run",
            {
                "cellIds": list(stream.cell_ids),
                "codes": list(stream.codes),
            },
        )
        stream.wait_for_completed_run(completed_runs)
        from marimo_export._client_protocol import _bridge_error
        from marimo_export._remote.client import BridgeError, HttpKernelTransport

        transport = HttpKernelTransport(
            self.base_url,
            access_token=self.access_token,
            timeout=self.timeout,
        )
        try:
            validation = transport.invoke(self.session_id, "validate_baseline", {})
        except BridgeError as error:
            raise _bridge_error(error) from error
        if validation != {"valid": True}:
            raise TransportError(
                "the managed baseline validation returned an invalid response",
                code="server_start_failed",
            )
        self._record_owned_process_groups()
        return _ActivationTimings(
            session_start_seconds=session_start_seconds,
            initial_autorun_seconds=time.monotonic() - autorun_started,
        )

    def stop(self) -> None:
        """Stop the edit stream and owned process within the build timeout."""

        failures: list[BaseException] = []
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.request_close()
            except BaseException as error:
                failures.append(error)
        owned_groups = set(getattr(self, "_owned_groups", set()))
        try:
            owned_groups.update(self._owned_process_groups())
        except BaseException as error:
            failures.append(error)
        try:
            self._request_server_shutdown()
        except BaseException as error:
            failures.append(error)
        try:
            self._stop_process(owned_groups)
        except BaseException as error:
            failures.append(error)
        if stream is not None:
            try:
                stream.close()
            except BaseException as error:
                failures.append(error)
        try:
            self._close_files()
        except BaseException as error:
            failures.append(error)
        if failures:
            cancellation = next(
                (failure for failure in failures if not isinstance(failure, Exception)),
                None,
            )
            if cancellation is not None:
                for failure in failures:
                    if failure is not cancellation:
                        record_cleanup_failure(cancellation, "managed cleanup", failure)
                raise cancellation
            first_failure = failures[0]
            raise TransportError(
                "the managed marimo server could not be stopped",
                code="server_shutdown_failed",
                details={
                    "failures": [safe_diagnostic(type(failure).__name__) for failure in failures]
                },
            ) from first_failure

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

    def _send_access_token(self) -> None:
        process = self._process
        stream = None if process is None else process.stdin
        if process is None or stream is None:
            raise RuntimeError("managed process token input is unavailable")
        primary: BaseException | None = None
        try:
            payload = f"{self.access_token}\n".encode()
            if stream.write(payload) != len(payload):
                raise BrokenPipeError("managed token input accepted a partial write")
            stream.flush()
        except BaseException as error:
            if isinstance(error, Exception):
                primary = TransportError(
                    "the managed marimo server did not accept its access token",
                    code="server_start_failed",
                    details={
                        "return_code": process.poll(),
                        "log": self._logs(),
                    },
                )
                primary.__cause__ = error
            else:
                primary = error
        finally:
            try:
                stream.close()
            except BaseException as error:
                if primary is None:
                    if isinstance(error, Exception):
                        primary = TransportError(
                            "the managed marimo token input could not be closed",
                            code="server_start_failed",
                            details={
                                "return_code": process.poll(),
                                "log": self._logs(),
                            },
                        )
                        primary.__cause__ = error
                    else:
                        primary = error
                else:
                    record_cleanup_failure(primary, "managed token input cleanup", error)
        if primary is not None:
            raise primary

    def _post_json(
        self,
        path: str,
        value: Mapping[str, object],
        *,
        timeout: float | None = None,
    ) -> JsonObject:
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
            with urllib.request.urlopen(
                request,
                timeout=self.timeout if timeout is None else timeout,
            ) as response:
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

    def _request_server_shutdown(self) -> None:
        process = self._process
        if process is None or process.poll() is not None or sys.platform == "win32":
            return
        self._post_json(
            "/api/kernel/shutdown",
            {},
            timeout=min(self.timeout, 2.0),
        )

    def _require_cache_activation(self) -> None:
        try:
            value = self._activation_path.read_text(encoding="utf-8")
        except OSError as error:
            raise TransportError(
                "the managed marimo kernel did not activate cache integration",
                code="server_start_failed",
            ) from error
        if value != self._activation_token:
            raise TransportError(
                "the managed marimo kernel returned an invalid cache activation",
                code="server_start_failed",
            )

    def _record_owned_process_groups(self) -> None:
        self._owned_groups.update(self._owned_process_groups())

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _stop_process(self, owned_groups: set[int] | None = None) -> None:
        process = self._process
        tree_owner = getattr(self, "_windows_job", None)
        if process is None and tree_owner is None:
            return
        failures: list[BaseException] = []
        reaped = process is None

        def is_running() -> bool:
            if reaped or process is None:
                return False
            try:
                return process.poll() is None
            except BaseException as error:
                failures.append(error)
                return True

        groups = owned_groups
        if groups is None and process is not None:
            groups = set(getattr(self, "_owned_groups", set()))
            try:
                groups.update(self._owned_process_groups())
            except BaseException as error:
                failures.append(error)
                if sys.platform != "win32":
                    groups.add(process.pid)
        if groups is None:
            groups = set()
        tree_terminated = False
        if sys.platform == "win32" and tree_owner is not None:
            try:
                tree_owner.terminate()
                tree_terminated = True
            except BaseException as error:
                failures.append(error)
            if tree_terminated:
                try:
                    if not tree_owner.wait(min(self.timeout, 5.0)):
                        raise TransportError(
                            "the managed marimo process tree did not stop",
                            code="server_shutdown_failed",
                        )
                except BaseException as error:
                    failures.append(error)
        if process is not None and is_running():
            try:
                self._signal_process(process, force=False)
            except BaseException as error:
                failures.append(error)
            try:
                process.wait(timeout=self.timeout)
                reaped = True
            except subprocess.TimeoutExpired:
                pass
            except BaseException as error:
                failures.append(error)
        if process is not None and is_running():
            try:
                self._signal_process(process, force=True)
            except BaseException as error:
                failures.append(error)
            try:
                process.wait(timeout=min(self.timeout, 5.0))
                reaped = True
            except subprocess.TimeoutExpired:
                pass
            except BaseException as error:
                failures.append(error)
        if sys.platform != "win32" and process is not None:
            try:
                self._kill_owned_process_groups(groups)
            except BaseException as error:
                failures.append(error)
            if is_running():
                try:
                    process.wait(timeout=min(self.timeout, 1.0))
                    reaped = True
                except subprocess.TimeoutExpired:
                    pass
                except BaseException as error:
                    failures.append(error)
        if process is not None:
            if is_running():
                failures.append(
                    TransportError(
                        "the managed marimo process did not stop",
                        code="server_shutdown_failed",
                    )
                )
            else:
                self._process = None
        if tree_owner is not None:
            try:
                tree_owner.close()
            except BaseException as error:
                failures.append(error)
            else:
                self._windows_job = None
        if failures:
            cancellation = next(
                (failure for failure in failures if not isinstance(failure, Exception)),
                None,
            )
            if cancellation is not None:
                for failure in failures:
                    if failure is not cancellation:
                        record_cleanup_failure(cancellation, "managed process cleanup", failure)
                raise cancellation
            first_failure = failures[0]
            raise TransportError(
                "the managed marimo process tree did not stop cleanly",
                code="server_shutdown_failed",
                details={
                    "failures": [safe_diagnostic(type(failure).__name__) for failure in failures]
                },
            ) from first_failure

    def _owned_process_groups(self) -> set[int]:
        process = self._process
        if process is None or sys.platform == "win32" or process.poll() is not None:
            return set()
        groups = {process.pid}
        try:
            listed = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,pgid="],
                capture_output=True,
                check=False,
                text=True,
                timeout=min(self.timeout, 2.0),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TransportError(
                "the managed marimo process tree could not be inspected",
                code="server_shutdown_failed",
            ) from error
        if listed.returncode != 0:
            raise TransportError(
                "the managed marimo process tree could not be inspected",
                code="server_shutdown_failed",
                details={"return_code": listed.returncode},
            )
        children: dict[int, list[tuple[int, int]]] = {}
        for line in listed.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) != 3:
                raise TransportError(
                    "the managed marimo process tree report was invalid",
                    code="server_shutdown_failed",
                )
            try:
                pid, parent_pid, group_id = map(int, parts)
            except ValueError as error:
                raise TransportError(
                    "the managed marimo process tree report was invalid",
                    code="server_shutdown_failed",
                ) from error
            children.setdefault(parent_pid, []).append((pid, group_id))
        pending = [process.pid]
        descendants = {process.pid}
        while pending:
            parent_pid = pending.pop()
            for child_pid, group_id in children.get(parent_pid, ()):
                if child_pid in descendants:
                    continue
                descendants.add(child_pid)
                groups.add(group_id)
                pending.append(child_pid)
        groups.discard(os.getpgrp())
        return groups

    @staticmethod
    def _kill_owned_process_groups(groups: set[int]) -> None:
        failures: list[BaseException] = []
        try:
            live_groups = ManagedServer._live_process_groups(groups)
        except BaseException as error:
            failures.append(error)
            live_groups = {
                group_id for group_id in groups if group_id > 0 and group_id != os.getpgrp()
            }
        for group_id in sorted(live_groups):
            try:
                os.killpg(group_id, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except PermissionError as error:
                try:
                    if group_id not in ManagedServer._live_process_groups({group_id}):
                        continue
                except BaseException as probe_error:
                    failures.append(probe_error)
                failures.append(error)
            except BaseException as error:
                failures.append(error)
                if isinstance(error, Exception):
                    continue
                try:
                    os.killpg(group_id, signal.SIGKILL)
                except ProcessLookupError:
                    continue
                except BaseException as retry_error:
                    failures.append(retry_error)
        if failures:
            cancellation = next(
                (failure for failure in failures if not isinstance(failure, Exception)),
                None,
            )
            if cancellation is not None:
                for failure in failures:
                    if failure is not cancellation:
                        record_cleanup_failure(
                            cancellation,
                            "managed process group cleanup",
                            failure,
                        )
                raise cancellation
            raise TransportError(
                "the managed marimo process groups could not be stopped",
                code="server_shutdown_failed",
                details={
                    "failures": [safe_diagnostic(type(failure).__name__) for failure in failures]
                },
            ) from failures[0]

    @staticmethod
    def _live_process_groups(groups: set[int]) -> set[int]:
        candidates = {group for group in groups if group > 0 and group != os.getpgrp()}
        if not candidates:
            return set()
        try:
            listed = subprocess.run(
                ["ps", "-axo", "pgid=,stat="],
                capture_output=True,
                check=False,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return candidates
        if listed.returncode != 0:
            return candidates
        live: set[int] = set()
        for line in listed.stdout.splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                group_id = int(parts[0])
            except ValueError:
                continue
            if group_id in candidates and not parts[1].startswith("Z"):
                live.add(group_id)
        return live

    @staticmethod
    def _signal_process(
        process: subprocess.Popen[bytes],
        *,
        force: bool,
    ) -> None:
        if sys.platform == "win32":
            if force:
                process.kill()
            else:
                process.terminate()
            return
        with suppress(ProcessLookupError):
            os.killpg(
                process.pid,
                signal.SIGKILL if force else signal.SIGTERM,
            )

    def _close_files(self) -> None:
        if not self._log_file.closed:
            self._log_file.close()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._temporary.cleanup()
                return
            except OSError:
                remaining = deadline - time.monotonic()
                if sys.platform != "win32" or remaining <= 0:
                    raise
                time.sleep(min(remaining, 0.01))

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
        self._cell_ids: tuple[str, ...] = ()
        self._codes: tuple[str, ...] = ()
        self._completed_runs = 0
        self._activity_generation = 0
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

    @property
    def cell_ids(self) -> tuple[str, ...]:
        with self._condition:
            return self._cell_ids

    @property
    def codes(self) -> tuple[str, ...]:
        with self._condition:
            return self._codes

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

    def request_close(self) -> None:
        self._closed.set()

    def close(self) -> None:
        self.request_close()
        self._thread.join(timeout=min(self._timeout, 5.0))
        if self._thread.is_alive():
            self._response.close()
            self._thread.join(timeout=min(self._timeout, 1.0))
        else:
            self._response.close()
        if self._thread.is_alive():
            raise TransportError(
                "the managed marimo edit stream did not close",
                code="server_shutdown_failed",
            )

    def _wait(self, predicate: Callable[[], bool], label: str) -> None:
        deadline = time.monotonic() + self._timeout
        with self._condition:
            activity_generation = self._activity_generation
            while not predicate():
                if self._failure is not None:
                    raise TransportError(
                        f"the managed marimo {label} stream failed",
                        code="server_start_failed",
                    ) from self._failure
                if self._activity_generation != activity_generation:
                    activity_generation = self._activity_generation
                    deadline = time.monotonic() + self._timeout
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
            accepted = False
            if operation == "kernel-ready":
                cell_ids = data.get("cell_ids") if isinstance(data, dict) else None
                codes = data.get("codes") if isinstance(data, dict) else None
                if (
                    isinstance(cell_ids, list)
                    and isinstance(codes, list)
                    and len(cell_ids) == len(codes)
                    and all(isinstance(item, str) for item in cell_ids)
                    and all(isinstance(item, str) for item in codes)
                ):
                    self._cell_ids = tuple(cell_ids)
                    self._codes = tuple(codes)
                    self._kernel_ready = True
                    accepted = True
                else:
                    self._failure = ValueError("kernel-ready cells are invalid")
            elif operation == "cell-op" and isinstance(data, dict):
                cell_id = data.get("cell_id")
                status = data.get("status")
                if isinstance(cell_id, str) and isinstance(status, str):
                    self._cell_statuses[cell_id] = status
                    accepted = True
            elif operation == "completed-run":
                self._completed_runs += 1
                accepted = True
            if accepted:
                self._activity_generation += 1
            self._condition.notify_all()


def _managed_creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess,
        "CREATE_SUSPENDED",
        0x00000004,
    )


def _own_windows_process_tree(
    process: subprocess.Popen[bytes],
) -> _ProcessTreeOwner:
    from marimo_export._remote.windows_job import WindowsJob

    return WindowsJob.create_for_process(process.pid)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return cast(int, stream.getsockname()[1])


def _require_kernel_lifespan_policy(environment: Mapping[str, str]) -> None:
    denylist = {
        name.strip().lower()
        for name in environment.get(_KERNEL_LIFESPAN_DENYLIST_ENV, "").split(",")
        if name.strip()
    }
    if _KERNEL_LIFESPAN_NAME in denylist:
        raise TransportError(
            "the managed marimo kernel policy excludes marimo-export",
            code="server_start_failed",
            details={"environment": _KERNEL_LIFESPAN_DENYLIST_ENV},
        )
    if _KERNEL_LIFESPAN_ALLOWLIST_ENV not in environment:
        return
    allowlist = {
        name.strip().lower()
        for name in environment[_KERNEL_LIFESPAN_ALLOWLIST_ENV].split(",")
        if name.strip()
    }
    if _KERNEL_LIFESPAN_NAME not in allowlist:
        raise TransportError(
            "the managed marimo kernel policy excludes marimo-export",
            code="server_start_failed",
            details={"environment": _KERNEL_LIFESPAN_ALLOWLIST_ENV},
        )


__all__ = ["ManagedServer"]
