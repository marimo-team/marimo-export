from __future__ import annotations

import os
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest
from marimo_export import ExportSpec, OutputSpec, build, open_export
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import TransportError


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
            assert server._process is not None
            assert server._process.stdin is not None
            assert server._process.stdin.closed
            server.activate()
        finally:
            server.stop()

    assert marker.read_text(encoding="utf-8") == "1"


@pytest.mark.timeout(30)
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
        timeout=30,
    )

    assert open_export(result.path).state("baseline").output("startup").json() == "loaded"


@pytest.mark.timeout(30)
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
        timeout=30,
    )

    assert open_export(result.path).state("baseline").output("visibility").json() == "None:None"


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
