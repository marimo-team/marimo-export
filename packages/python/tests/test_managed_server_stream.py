from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any, cast

import pytest
from marimo_export._remote.managed import ManagedServer, _SessionStream
from marimo_export.errors import TransportError


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


def test_session_stream_inactivity_timeout_resets_on_accepted_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    stream = _SessionStream.__new__(_SessionStream)
    stream._timeout = 1.0
    stream._failure = None
    stream._completed_runs = 0
    stream._activity_generation = 0

    class _Condition:
        def __enter__(self) -> _Condition:
            return self

        def __exit__(self, *exc_info: object) -> None:
            del exc_info

        def wait(self, *, timeout: float) -> None:
            assert timeout > 0
            now[0] += 0.75
            stream._activity_generation += 1
            if stream._activity_generation == 3:
                stream._completed_runs += 1

    stream._condition = cast(Any, _Condition())
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    stream._wait(lambda: stream._completed_runs > 0, "test run")

    assert now[0] == 2.25


def test_windows_job_owns_descendants_after_root_process_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class _Process:
        pid = 123

        @staticmethod
        def poll() -> int:
            return 1

    class _Job:
        def terminate(self) -> None:
            events.append("job-terminate")

        def wait(self, timeout: float) -> bool:
            assert timeout > 0
            events.append("job-wait")
            return True

        def close(self) -> None:
            events.append("job-close")

    server = ManagedServer.__new__(ManagedServer)
    server._process = cast(Any, _Process())
    server._windows_job = cast(Any, _Job())
    server.timeout = 1
    monkeypatch.setattr(sys, "platform", "win32")

    server._stop_process()

    assert events == ["job-terminate", "job-wait", "job-close"]
    assert server._process is None
    assert server._windows_job is None


def test_windows_job_failure_falls_back_to_direct_process_kill(
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

    class _Job:
        def terminate(self) -> None:
            events.append("job-terminate")
            raise OSError("job termination failed")

        def close(self) -> None:
            events.append("job-close")

    process = _Process()
    server = ManagedServer.__new__(ManagedServer)
    server._process = cast(Any, process)
    server._windows_job = cast(Any, _Job())
    server.timeout = 1

    monkeypatch.setattr(sys, "platform", "win32")

    with pytest.raises(TransportError, match="process tree did not stop cleanly"):
        server._stop_process()

    assert events == [
        "job-terminate",
        "terminate",
        "wait",
        "kill",
        "wait",
        "job-close",
    ]
    assert server._process is None
    assert server._windows_job is None
