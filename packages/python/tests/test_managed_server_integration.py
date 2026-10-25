from __future__ import annotations

import os
import sys
import textwrap
import time
from contextlib import suppress
from pathlib import Path

import pytest
from export_integration_support import NOTEBOOK_TIMEOUT
from marimo_export import ExportSpec, OutputSpec, build, open_export
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import TransportError
from marimo_export.producer import open_notebook

pytestmark = pytest.mark.serial

# Whole-build watchdogs include startup, activation, capture, and teardown.
# The API timeouts bound individual waits within those phases.
_WATCHDOG = 2 * NOTEBOOK_TIMEOUT


@pytest.mark.timeout(_WATCHDOG)
def test_owned_initial_autorun_restores_native_cell_cache(tmp_path: Path) -> None:
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

    authored = notebook.read_bytes()

    for _ in range(2):
        with open_notebook(notebook, timeout=NOTEBOOK_TIMEOUT) as producer:
            server = producer._server
            assert server is not None
            assert server._process is not None
            assert server._process.stdin is not None
            assert server._process.stdin.closed

    assert marker.read_text(encoding="utf-8") == "1"
    assert notebook.read_bytes() == authored


@pytest.mark.timeout(_WATCHDOG)
def test_managed_server_preserves_environment_sitecustomize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "sitecustomize.py").write_text(
        'import os\nos.environ["MARIMO_EXPORT_TEST_SITE"] = "loaded"\n',
        encoding="utf-8",
    )
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import os
    startup = os.environ.get("MARIMO_EXPORT_TEST_SITE", "missing")
    return (startup,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={"startup": OutputSpec.json("startup")},
        ),
        output=tmp_path / "export",
        timeout=NOTEBOOK_TIMEOUT,
    )

    assert open_export(result.path).state("baseline").output("startup").json() == "loaded"


@pytest.mark.timeout(_WATCHDOG)
def test_managed_cache_markers_are_absent_from_notebook_processes(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import os
    import subprocess
    import sys

    parent = os.environ.get("MARIMO_EXPORT_MANAGED_CACHE_COMPAT")
    child = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "import os; "
            "print(os.environ.get('MARIMO_EXPORT_MANAGED_CACHE_COMPAT'))",
        ],
        text=True,
    ).strip()
    visibility = f"{parent}:{child}"
    return (visibility,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={"visibility": OutputSpec.json("visibility")},
        ),
        output=tmp_path / "export",
        timeout=NOTEBOOK_TIMEOUT,
    )

    assert open_export(result.path).state("baseline").output("visibility").json() == "None:None"


# Each hold runs in marimo's spawned kernel process until the test releases it,
# with the session wait budget for that phase. A released kernel that never
# connected exits so the server can answer the shutdown request.
_KERNEL_HOLDS = {
    "kernel readiness": (5.0, "hold()\nos._exit(0)\n"),
    "kernel instantiation": (
        # The kernel must connect within this budget before its lifespan holds.
        15.0,
        # The marimo-export entry point resolves `kernel_lifespan` on each call.
        "from contextlib import asynccontextmanager\n"
        "from marimo_export._marimo.compat import managed_kernel\n"
        "lifespan = managed_kernel.kernel_lifespan\n"
        "\n"
        "@asynccontextmanager\n"
        "async def held_lifespan(value):\n"
        "    hold()\n"
        "    async with lifespan(value):\n"
        "        yield\n"
        "\n"
        "managed_kernel.kernel_lifespan = held_lifespan\n",
    ),
}


@pytest.mark.timeout(_WATCHDOG)
@pytest.mark.parametrize("phase", sorted(_KERNEL_HOLDS))
def test_managed_activation_names_the_kernel_phase_that_timed_out(
    phase: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget, held = _KERNEL_HOLDS[phase]
    release = tmp_path / "release"
    (tmp_path / "sitecustomize.py").write_text(
        "import os\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "\n"
        "def hold():\n"
        f"    while not Path({str(release)!r}).exists():\n"
        "        time.sleep(0.05)\n"
        "\n"
        "if sys.argv[-1:] == ['--multiprocessing-fork']:\n"
        f"{textwrap.indent(held, '    ')}",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n\napp = marimo.App()\n", encoding="utf-8")

    server = ManagedServer(notebook, timeout=NOTEBOOK_TIMEOUT)
    try:
        # Server startup and shutdown keep the full budget.
        server.timeout = budget
        with pytest.raises(TransportError) as raised:
            server.activate()
        assert str(raised.value) == f"the managed marimo {phase} timed out"
    finally:
        server.timeout = NOTEBOOK_TIMEOUT
        release.touch()
        server.stop()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MARIMO_KERNEL_LIFESPAN_ALLOWLIST", "another-extension"),
        ("MARIMO_KERNEL_LIFESPAN_DENYLIST", "marimo-export"),
    ],
)
def test_managed_build_rejects_extension_policy_before_notebook_execution(
    name: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "executed"
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        "import marimo\n\n"
        "app = marimo.App()\n\n"
        "@app.cell\n"
        "def _():\n"
        "    from pathlib import Path\n"
        f"    Path({str(marker)!r}).write_text('executed')\n"
        "    value = 1\n"
        "    return (value,)\n\n"
        "if __name__ == '__main__':\n"
        "    app.run()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(name, value)

    with pytest.raises(TransportError) as raised:
        build(
            notebook,
            spec=ExportSpec(
                default_state="baseline",
                states={"baseline": {}},
                outputs={"value": OutputSpec.json("value")},
            ),
            output=tmp_path / "export",
        )

    assert raised.value.code == "server_start_failed"
    assert not marker.exists()


@pytest.mark.timeout(_WATCHDOG)
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
        "while True:\n"
        "    with heartbeat.open('a', encoding='utf-8') as stream:\n"
        "        stream.write('1')\n"
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

    server = ManagedServer(notebook, timeout=NOTEBOOK_TIMEOUT)
    pid = 0
    try:
        server.activate()
        # Shutdown keeps a short bound for its SIGTERM grace period.
        server.timeout = 10
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


@pytest.mark.timeout(_WATCHDOG)
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process group contract")
def test_managed_shutdown_stops_child_session_while_ps_stalls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    stalled_ps = fake_bin / "ps"
    stalled_ps.write_text("#!/bin/sh\nsleep 5\nexit 1\n", encoding="utf-8")
    stalled_ps.chmod(0o755)
    heartbeat = tmp_path / "heartbeat.txt"
    child_pid = tmp_path / "child-pid.txt"
    notebook = tmp_path / "notebook.py"
    child_code = (
        "from pathlib import Path\n"
        "import signal\n"
        "import time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"heartbeat = Path({str(heartbeat)!r})\n"
        "while True:\n"
        "    with heartbeat.open('a', encoding='utf-8') as stream:\n"
        "        stream.write('1')\n"
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
        "    child = subprocess.Popen(\n"
        f"        [sys.executable, '-c', {child_code!r}],\n"
        "        start_new_session=True,\n"
        "    )\n"
        f"    Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "    return (child,)\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    app.run()\n",
        encoding="utf-8",
    )

    server = ManagedServer(notebook, timeout=NOTEBOOK_TIMEOUT)
    pid = 0
    try:
        server.activate()
        # Shutdown keeps a short bound for its SIGTERM grace period.
        server.timeout = 10
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (not heartbeat.exists() or not heartbeat.read_text()):
            time.sleep(0.05)
        pid = int(child_pid.read_text(encoding="utf-8"))
        assert os.getpgid(pid) == pid
        # A loaded runner can take seconds to start `ps`. Shutdown must still
        # find and stop descendants that left the kernel's process group.
        monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
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
