from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import pytest
from marimo_export._marimo.compat.managed_server import (
    _cache_enabled_script_config,
)
from marimo_export._remote.managed import ManagedServer, _SessionStream
from marimo_export.errors import TransportError


class _Stream:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def request_close(self) -> None:
        self.events.append("stream-closing")

    def close(self) -> None:
        self.events.append("stream-closed")


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


def test_startup_preserves_primary_error_and_closes_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: object())

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

    with pytest.raises(ValueError, match="startup failed") as raised:
        ManagedServer(notebook, timeout=1)

    assert raised.value.__notes__ == ["managed process cleanup also failed: RuntimeError"]
    assert events == ["process-cleanup", "files-closed"]


def test_managed_script_config_forces_native_cell_caching_last() -> None:
    manager = object()

    def native(
        value: object,
        *,
        hide_secrets: bool,
    ) -> dict[str, object]:
        assert value is manager
        assert hide_secrets is False
        return {
            "runtime": {
                "auto_reload": "off",
                "cache_cells": False,
            }
        }

    config = _cache_enabled_script_config(
        native,
        manager,
        hide_secrets=False,
    )

    assert config == {
        "runtime": {
            "auto_reload": "off",
            "cache_cells": True,
        }
    }


def test_session_stream_closes_response_after_initial_join_timeout() -> None:
    events: list[str] = []

    class _Thread:
        alive = True

        def join(self, *, timeout: float) -> None:
            assert timeout > 0
            events.append("thread-joined")

        def is_alive(self) -> bool:
            return self.alive

    thread = _Thread()

    class _Response:
        def close(self) -> None:
            events.append("response-closed")
            thread.alive = False

    stream = _SessionStream.__new__(_SessionStream)
    stream._closed = threading.Event()
    stream._thread = cast(Any, thread)
    stream._response = cast(Any, _Response())
    stream._timeout = 1.0

    stream.close()

    assert events == [
        "thread-joined",
        "response-closed",
        "thread-joined",
    ]


def test_stop_process_uses_fallback_after_windows_tree_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class _Process:
        pid = 123
        stopped = False

        def poll(self) -> int | None:
            return 1 if self.stopped else None

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")
            self.stopped = True

        def wait(self, *, timeout: float) -> int:
            assert timeout > 0
            events.append("wait")
            if not self.stopped:
                raise subprocess.TimeoutExpired("managed", timeout)
            return 1

    process = _Process()
    server = ManagedServer.__new__(ManagedServer)
    server._process = cast(Any, process)
    server.timeout = 1

    def fail_tree(
        value: ManagedServer,
        candidate: subprocess.Popen[bytes],
    ) -> None:
        del value
        assert candidate is process
        events.append("taskkill")
        raise TransportError("taskkill failed", code="server_shutdown_failed")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ManagedServer, "_terminate_windows_tree", fail_tree)

    with pytest.raises(TransportError, match="process tree did not stop cleanly"):
        server._stop_process()

    assert events == ["taskkill", "terminate", "wait", "kill", "wait"]
    assert server._process is None


def test_stop_process_drains_posix_groups_after_forced_wait_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class _Process:
        pid = 123
        stopped = False

        def poll(self) -> int | None:
            return 1 if self.stopped else None

        def wait(self, *, timeout: float) -> int:
            assert timeout > 0
            events.append("wait")
            if not self.stopped:
                raise subprocess.TimeoutExpired("managed", timeout)
            return 1

    process = _Process()
    server = ManagedServer.__new__(ManagedServer)
    server._process = cast(Any, process)
    server.timeout = 1

    def signal_process(
        candidate: subprocess.Popen[bytes],
        *,
        force: bool,
    ) -> None:
        assert candidate is process
        events.append("kill" if force else "terminate")

    def kill_groups(groups: set[int]) -> None:
        assert groups == {123, 456}
        events.append("groups-killed")
        process.stopped = True

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ManagedServer, "_signal_process", staticmethod(signal_process))
    monkeypatch.setattr(
        ManagedServer,
        "_kill_owned_process_groups",
        staticmethod(kill_groups),
    )

    server._stop_process({123, 456})

    assert events == [
        "terminate",
        "wait",
        "kill",
        "wait",
        "groups-killed",
    ]
    assert server._process is None


def test_windows_tree_termination_includes_descendants(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    command: list[str] = []

    def run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        command.extend(args)
        return subprocess.CompletedProcess(args, 0)

    log_file = (tmp_path / "managed.log").open("wb")
    server = ManagedServer.__new__(ManagedServer)
    server._log_file = log_file
    server.timeout = 1
    process = cast(Any, type("_Process", (), {"pid": 123})())
    monkeypatch.setattr(subprocess, "run", run)
    try:
        server._terminate_windows_tree(process)
    finally:
        log_file.close()

    assert command == ["taskkill", "/PID", "123", "/T", "/F"]


@pytest.mark.timeout(30)
def test_managed_initial_autorun_restores_native_cell_cache(tmp_path: Path) -> None:
    marker = tmp_path / "autorun-count.txt"
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        "# /// script\n"
        "# [tool.marimo.runtime]\n"
        "# cache_cells = false\n"
        "# ///\n"
        "\n"
        "import marimo\n"
        "\n"
        "app = marimo.App()\n"
        "\n"
        "@app.cell\n"
        "def _():\n"
        "    from pathlib import Path\n"
        f"    marker = Path({str(marker)!r})\n"
        "    count = int(marker.read_text()) if marker.exists() else 0\n"
        "    marker.write_text(str(count + 1))\n"
        "    return (count,)\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    app.run()\n",
        encoding="utf-8",
    )

    for _ in range(2):
        server = ManagedServer(notebook, timeout=10)
        try:
            server.activate()
        finally:
            server.stop()

    assert marker.read_text(encoding="utf-8") == "1"


@pytest.mark.timeout(30)
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process group contract")
def test_managed_shutdown_stops_notebook_child_process(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat.txt"
    child_pid = tmp_path / "child-pid.txt"
    notebook = tmp_path / "notebook.py"
    child_code = (
        "from pathlib import Path\n"
        "import signal\n"
        "import time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"heartbeat = Path({str(heartbeat)!r})\n"
        "count = 0\n"
        "while True:\n"
        "    count += 1\n"
        "    heartbeat.write_text(str(count))\n"
        "    time.sleep(0.05)\n"
    )
    notebook.write_text(
        "import marimo\n"
        "\n"
        "app = marimo.App()\n"
        "\n"
        "@app.cell\n"
        "def _():\n"
        "    from pathlib import Path\n"
        "    import subprocess\n"
        "    import sys\n"
        f"    child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"    Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "    return (child,)\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    app.run()\n",
        encoding="utf-8",
    )

    server = ManagedServer(notebook, timeout=10)
    pid = 0
    try:
        server.activate()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (not heartbeat.exists() or not heartbeat.read_text()):
            time.sleep(0.05)
        pid = int(child_pid.read_text(encoding="utf-8"))
        assert heartbeat.read_text(encoding="utf-8")
    finally:
        server.stop()

    stopped_at = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.2)
    try:
        assert heartbeat.read_text(encoding="utf-8") == stopped_at
    finally:
        if pid:
            with suppress(ProcessLookupError):
                os.kill(pid, 9)
