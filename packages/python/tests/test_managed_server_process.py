from __future__ import annotations

import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import marimo_export._remote.managed as managed_module
import pytest
from marimo_export._diagnostics import cleanup_failures
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import TransportError


def _process_tree(root_pid: int) -> tuple[set[int], set[int]]:
    listed = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,stat="],
        check=True,
        capture_output=True,
        text=True,
    )
    children: dict[int, list[tuple[int, int]]] = {}
    for line in listed.stdout.splitlines():
        pid_text, parent_text, group_text, _status = line.split(maxsplit=3)
        children.setdefault(int(parent_text), []).append((int(pid_text), int(group_text)))
    pids = {root_pid}
    groups: set[int] = set()
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for pid, group in children.get(parent, []):
            if pid in pids:
                continue
            pids.add(pid)
            groups.add(group)
            pending.append(pid)
    groups.add(root_pid)
    groups.discard(os.getpgrp())
    return pids, groups


def _live_processes_and_groups(
    pids: set[int],
    groups: set[int],
) -> tuple[set[int], set[int]]:
    listed = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,stat="],
        check=True,
        capture_output=True,
        text=True,
    )
    live_pids: set[int] = set()
    live_groups: set[int] = set()
    for line in listed.stdout.splitlines():
        pid_text, group_text, status = line.split(maxsplit=2)
        if status.startswith("Z"):
            continue
        pid = int(pid_text)
        group = int(group_text)
        if pid in pids:
            live_pids.add(pid)
        if group in groups:
            live_groups.add(group)
    return live_pids, live_groups


class _Stream:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def request_close(self) -> None:
        self.events.append("stream-closing")

    def close(self) -> None:
        self.events.append("stream-closed")


class _TokenInput:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.payload = bytearray()
        self.writes = 0
        self.flushes = 0
        self.closed = False

    def write(self, payload: bytes) -> int:
        self.writes += 1
        if self.failure is not None:
            raise self.failure
        self.payload.extend(payload)
        return len(payload)

    def flush(self) -> None:
        self.flushes += 1

    def close(self) -> None:
        self.closed = True


class _NoopTreeOwner:
    def terminate(self) -> None:
        pass

    def wait(self, timeout: float) -> bool:
        assert timeout > 0
        return True

    def close(self) -> None:
        pass


class _ManagedServerHarness(ManagedServer):
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self._stream = cast(Any, _Stream(events))

    def _stop_process(self, owned_groups: set[int] | None = None) -> None:
        assert owned_groups == {100}
        self.events.append("process-stopped")

    def _owned_process_groups(self) -> set[int]:
        return {100}

    def _request_server_shutdown(self) -> None:
        self.events.append("server-shutdown-requested")

    def _close_files(self) -> None:
        self.events.append("files-closed")


def test_managed_server_stops_process_before_joining_session_stream() -> None:
    events: list[str] = []

    _ManagedServerHarness(events).stop()

    assert events == [
        "stream-closing",
        "server-shutdown-requested",
        "process-stopped",
        "stream-closed",
        "files-closed",
    ]


def test_managed_server_reports_incomplete_process_ownership() -> None:
    events: list[str] = []

    class _IncompleteOwnership(_ManagedServerHarness):
        def __init__(self, values: list[str]) -> None:
            super().__init__(values)
            self._owned_groups = {100}

        def _owned_process_groups(self) -> set[int]:
            raise OSError("ps unavailable")

        def _request_server_shutdown(self) -> None:
            self.events.append("server-shutdown-failed")
            raise TransportError("shutdown unavailable")

    server = _IncompleteOwnership(events)

    with pytest.raises(TransportError) as raised:
        server.stop()

    assert raised.value.code == "server_shutdown_failed"
    assert events == [
        "stream-closing",
        "server-shutdown-failed",
        "process-stopped",
        "stream-closed",
        "files-closed",
    ]


def test_managed_server_finishes_cleanup_when_process_stop_is_cancelled() -> None:
    events: list[str] = []

    class _CancellingHarness(_ManagedServerHarness):
        def _stop_process(self, owned_groups: set[int] | None = None) -> None:
            assert owned_groups == {100}
            self.events.append("process-stop-cancelled")
            raise KeyboardInterrupt("cancelled")

    server = _CancellingHarness(events)

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        server.stop()

    assert server._stream is None
    assert events == [
        "stream-closing",
        "server-shutdown-requested",
        "process-stop-cancelled",
        "stream-closed",
        "files-closed",
    ]


def test_startup_preserves_primary_error_and_closes_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=123, stdin=io.BytesIO()),
    )

    def fail_startup(server: ManagedServer) -> None:
        del server
        raise ValueError("startup failed")

    def fail_cleanup(
        server: ManagedServer,
        owned_groups: set[int] | None = None,
    ) -> None:
        del server, owned_groups
        events.append("process-cleanup")
        raise RuntimeError("cleanup failed")

    def close_files(server: ManagedServer) -> None:
        events.append("files-closed")
        server._log_file.close()
        server._temporary.cleanup()

    monkeypatch.setattr(ManagedServer, "_wait_ready", fail_startup)
    monkeypatch.setattr(ManagedServer, "_stop_process", fail_cleanup)
    monkeypatch.setattr(ManagedServer, "_close_files", close_files)
    monkeypatch.setattr(
        managed_module,
        "_own_windows_process_tree",
        lambda _process: _NoopTreeOwner(),
    )

    with pytest.raises(ValueError, match="startup failed") as raised:
        ManagedServer(notebook, timeout=1)

    assert cleanup_failures(raised.value) == ("managed process cleanup also failed: RuntimeError",)
    assert events == ["process-cleanup", "files-closed"]


def test_managed_server_passes_token_once_through_closed_stdin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "managed-access-secret"
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")
    token_input = _TokenInput()
    observed: dict[str, object] = {}

    def start_process(args: list[str], **kwargs: object) -> SimpleNamespace:
        observed["argv"] = tuple(args)
        observed["process"] = (tuple(args), kwargs)
        log_file = cast(Any, kwargs["stdout"])
        observed["temporary_path"] = Path(log_file.name).parent
        observed["files_at_launch"] = tuple(
            sorted(path.name for path in observed["temporary_path"].iterdir())
        )
        return SimpleNamespace(pid=123, stdin=token_input)

    monkeypatch.setattr(managed_module.secrets, "token_urlsafe", lambda _size=None: secret)
    monkeypatch.setattr(subprocess, "Popen", start_process)
    monkeypatch.setattr(ManagedServer, "_wait_ready", lambda _server: None)
    monkeypatch.setattr(
        managed_module,
        "_own_windows_process_tree",
        lambda _process: _NoopTreeOwner(),
    )

    server = ManagedServer(notebook, timeout=1)
    try:
        argv = cast(tuple[str, ...], observed["argv"])
        assert "--token-password" not in argv
        assert argv[argv.index("--token-password-file") + 1] == "-"
        assert secret not in repr(observed["process"])
        assert observed["files_at_launch"] == ("server.log",)
        environment = cast(
            dict[str, str], cast(tuple[object, dict[str, object]], observed["process"])[1]["env"]
        )
        assert environment["MARIMO_ANCESTOR_PID"] == str(os.getpid())
        assert token_input.payload == f"{secret}\n".encode()
        assert token_input.writes == 1
        assert token_input.flushes == 1
        assert token_input.closed is True
        assert secret not in server._logs()
    finally:
        server._process = None
        server._close_files()


def test_windows_server_assigns_suspended_process_to_job_before_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")
    process = SimpleNamespace(pid=123, stdin=io.BytesIO())

    class _Job:
        def terminate(self) -> None:
            events.append("job-terminate")

        def wait(self, timeout: float) -> bool:
            assert timeout > 0
            events.append("job-wait")
            return True

        def close(self) -> None:
            events.append("job-close")

    def start_process(args: list[str], **kwargs: object) -> SimpleNamespace:
        del args
        events.append("process-created")
        assert kwargs["creationflags"] == 0x204
        assert kwargs["start_new_session"] is False
        return process

    def own_process(started: object) -> _Job:
        assert started is process
        events.append("job-assigned-and-process-resumed")
        return _Job()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_SUSPENDED", 0x4, raising=False)
    monkeypatch.setattr(subprocess, "Popen", start_process)
    monkeypatch.setattr(managed_module, "_own_windows_process_tree", own_process)
    monkeypatch.setattr(
        ManagedServer,
        "_send_access_token",
        lambda _server: events.append("token-sent"),
    )
    monkeypatch.setattr(
        ManagedServer,
        "_wait_ready",
        lambda _server: events.append("server-ready"),
    )

    server = ManagedServer(notebook, timeout=1)
    try:
        assert events == [
            "process-created",
            "job-assigned-and-process-resumed",
            "token-sent",
            "server-ready",
        ]
    finally:
        server._process = None
        server._windows_job = None
        server._close_files()


@pytest.mark.skipif(sys.platform == "win32", reason="Marimo parent polling is POSIX-only")
@pytest.mark.timeout(45)
def test_managed_server_stops_after_its_owner_is_killed(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        "import marimo\n\napp = marimo.App()\n\n"
        "@app.cell\n"
        "def _():\n"
        "    value = 1\n"
        "    return (value,)\n\n"
        "if __name__ == '__main__':\n"
        "    app.run()\n",
        encoding="utf-8",
    )
    program = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "from marimo_export._remote.managed import ManagedServer\n"
        "server = ManagedServer(Path(sys.argv[1]), timeout=15)\n"
        "server.activate()\n"
        "print(json.dumps({'pid': server._process.pid, 'port': server.port}), flush=True)\n"
        "time.sleep(60)\n"
    )
    owner = subprocess.Popen(
        [sys.executable, "-c", program, str(notebook)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    server_pid = 0
    process_ids: set[int] = set()
    process_groups: set[int] = set()
    try:
        assert owner.stdout is not None
        line = owner.stdout.readline()
        assert line, owner.stderr.read() if owner.stderr is not None else ""
        started = json.loads(line)
        server_pid = int(started["pid"])
        port = int(started["port"])
        process_ids, process_groups = _process_tree(server_pid)
        assert len(process_groups) >= 2
        owner.kill()
        owner.wait(timeout=5)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            live_pids, live_groups = _live_processes_and_groups(
                process_ids,
                process_groups,
            )
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0 and not live_pids and not live_groups:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("managed server survived abrupt owner termination")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        for group in sorted(process_groups | ({server_pid} if server_pid > 1 else set())):
            with suppress(ProcessLookupError):
                os.killpg(group, signal.SIGKILL)


def test_managed_early_exit_redacts_secret_and_closes_stdin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "managed-startup-secret"
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")
    token_input = _TokenInput(BrokenPipeError("server exited"))
    observed: dict[str, object] = {}

    def start_process(args: list[str], **kwargs: object) -> SimpleNamespace:
        observed["argv"] = tuple(args)
        log_file = cast(Any, kwargs["stdout"])
        log_file.write(f"startup rejected {secret}".encode())
        log_file.flush()
        observed["temporary_path"] = Path(log_file.name).parent
        return SimpleNamespace(pid=123, stdin=token_input, poll=lambda: 17)

    monkeypatch.setattr(managed_module.secrets, "token_urlsafe", lambda _size=None: secret)
    monkeypatch.setattr(subprocess, "Popen", start_process)
    monkeypatch.setattr(
        managed_module,
        "_own_windows_process_tree",
        lambda _process: _NoopTreeOwner(),
    )
    monkeypatch.setattr(
        ManagedServer, "_stop_process", lambda server: setattr(server, "_process", None)
    )

    with pytest.raises(TransportError) as raised:
        ManagedServer(notebook, timeout=1)

    argv = cast(tuple[str, ...], observed["argv"])
    assert secret not in repr(argv)
    assert argv[argv.index("--token-password-file") + 1] == "-"
    assert token_input.closed is True
    assert not cast(Path, observed["temporary_path"]).exists()
    assert secret not in str(raised.value)
    assert secret not in repr(raised.value.wire())
    assert "<redacted>" in cast(str, raised.value.details["log"])
    assert raised.value.details["return_code"] == 17


def test_managed_token_input_cancellation_closes_stdin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")
    token_input = _TokenInput(KeyboardInterrupt("cancelled"))
    temporary_path: Path | None = None

    def start_process(args: list[str], **kwargs: object) -> SimpleNamespace:
        del args
        nonlocal temporary_path
        temporary_path = Path(cast(Any, kwargs["stdout"]).name).parent
        return SimpleNamespace(pid=123, stdin=token_input, poll=lambda: None)

    monkeypatch.setattr(subprocess, "Popen", start_process)
    monkeypatch.setattr(
        managed_module,
        "_own_windows_process_tree",
        lambda _process: _NoopTreeOwner(),
    )
    monkeypatch.setattr(
        ManagedServer, "_stop_process", lambda server: setattr(server, "_process", None)
    )

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        ManagedServer(notebook, timeout=1)

    assert token_input.closed is True
    assert temporary_path is not None
    assert not temporary_path.exists()


def test_windows_file_cleanup_waits_for_terminated_process_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    class _Temporary:
        def cleanup(self) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise PermissionError("process handle is still closing")

    server = ManagedServer.__new__(ManagedServer)
    server._log_file = io.BytesIO()
    server._temporary = cast(Any, _Temporary())
    server.timeout = 1
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    server._close_files()

    assert attempts == 2
    assert server._log_file.closed
