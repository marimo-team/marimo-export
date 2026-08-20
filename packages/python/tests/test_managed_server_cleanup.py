from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import marimo_export._remote.managed as managed_module
import pytest
from marimo_export._remote.managed import ManagedServer


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object contract")
@pytest.mark.timeout(15)
def test_windows_job_stops_descendant_after_root_process_exits(tmp_path: Path) -> None:
    heartbeat = tmp_path / "windows-heartbeat.txt"
    child_pid = tmp_path / "windows-child-pid.txt"
    child_code = (
        "from pathlib import Path\n"
        "import sys\n"
        "import time\n"
        "heartbeat = Path(sys.argv[1])\n"
        "count = 0\n"
        "while True:\n"
        "    count += 1\n"
        "    heartbeat.write_text(str(count))\n"
        "    time.sleep(0.05)\n"
    )
    root_code = (
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}, sys.argv[1]])\n"
        "Path(sys.argv[2]).write_text(str(child.pid))\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", root_code, str(heartbeat), str(child_pid)],
        creationflags=managed_module._managed_creation_flags(),
    )
    server = ManagedServer.__new__(ManagedServer)
    server._process = process
    server._windows_job = managed_module._own_windows_process_tree(process)
    server._owned_groups = set()
    server.timeout = 2
    pid = 0
    try:
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (
            not child_pid.exists() or not heartbeat.exists() or not heartbeat.read_text()
        ):
            time.sleep(0.05)
        pid = int(child_pid.read_text(encoding="utf-8"))
        assert heartbeat.read_text(encoding="utf-8")

        server._stop_process()

        stopped_at = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.2)
        assert heartbeat.read_text(encoding="utf-8") == stopped_at
        assert server._windows_job is None
    finally:
        tree_owner = getattr(server, "_windows_job", None)
        if tree_owner is not None:
            with suppress(OSError):
                tree_owner.terminate()
            with suppress(OSError):
                tree_owner.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if pid:
            with suppress(OSError, ProcessLookupError):
                os.kill(pid, signal.SIGTERM)


def test_process_stop_reaps_after_soft_signal_cancellation(
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

    def signal_process(process: _Process, *, force: bool) -> None:
        events.append("force-signal" if force else "soft-signal")
        if not force:
            raise KeyboardInterrupt("cancelled")
        process.stopped = True

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(server, "_signal_process", signal_process)
    monkeypatch.setattr(
        server,
        "_kill_owned_process_groups",
        lambda groups: events.append(f"groups:{sorted(groups)}"),
    )

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        server._stop_process({123})

    assert server._process is None
    assert events == [
        "soft-signal",
        "wait",
        "force-signal",
        "wait",
        "groups:[123]",
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process group contract")
def test_process_group_cleanup_finishes_after_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    interrupted = False

    def kill_group(group_id: int, signal_number: int) -> None:
        nonlocal interrupted
        assert signal_number == signal.SIGKILL
        calls.append(group_id)
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt("cancelled")

    monkeypatch.setattr(
        ManagedServer,
        "_live_process_groups",
        staticmethod(lambda groups: groups),
    )
    monkeypatch.setattr(os, "killpg", kill_group)

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        ManagedServer._kill_owned_process_groups({123, 456})

    assert calls == [123, 123, 456]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process group contract")
def test_process_group_cleanup_accepts_a_group_that_exits_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = iter(({123}, set()))

    def signal_group(group_id: int, signal_number: int) -> None:
        assert group_id == 123
        assert signal_number == signal.SIGKILL
        raise PermissionError

    monkeypatch.setattr(
        ManagedServer,
        "_live_process_groups",
        staticmethod(lambda groups: set(next(probes))),
    )
    monkeypatch.setattr(os, "killpg", signal_group)

    ManagedServer._kill_owned_process_groups({123})
