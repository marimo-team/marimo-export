from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from marimo_export import open_export
from marimo_export._remote.managed import ManagedServer
from marimo_export.sessions import Client

_REPOSITORY = Path(__file__).parents[3]
_EXAMPLE = _REPOSITORY / "examples" / "vite-vanilla"


def _run(
    *arguments: str,
    extra_environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["UV_NO_PROGRESS"] = "1"
    if extra_environment is not None:
        environment.update(extra_environment)
    return subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "--package",
            "marimo-export-vite-vanilla-example",
            *arguments,
        ],
        cwd=_REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        timeout=360,
        check=False,
    )


def _projection_cache(output: str) -> tuple[int, int]:
    activity = re.search(r"Projection cache: (\d+) hits?, (\d+) misses?", output)
    assert activity is not None
    return int(activity.group(1)), int(activity.group(2))


def _marimo_cache(output: str) -> tuple[int, int]:
    activity = re.search(
        r"Marimo cache: (\d+) authored hits?, (\d+) authored misses?",
        output,
    )
    assert activity is not None
    return int(activity.group(1)), int(activity.group(2))


def _assert_export(path: Path) -> None:
    export = open_export(path)
    states = export.states()
    assert tuple(state.fingerprint for state in states) == tuple(
        sorted(state.fingerprint for state in states)
    )
    assert tuple(sorted(alias for state in states for alias in state.aliases)) == (
        "ai_buildout",
        "baseline",
        "cloud_platforms",
        "full_watchlist",
        "weekly_view",
    )
    assert export.output_names == (
        "market_explorer",
        "market_summary",
        "performance_chart",
        "performance_snapshot",
        "price_history",
    )
    baseline = export.default_state
    assert "baseline" in baseline.aliases
    assert baseline.output("market_explorer").media_type == (
        "application/vnd.marimo-export.anywidget.v1+json"
    )
    summary = baseline.output("market_summary").blob_asset()
    assert summary.media_type == "application/vnd.marimo-export.market-summary.v1+json"
    assert summary.filename == "market-summary.json"
    summary_value = json.loads(summary.data)
    assert summary_value["schema"] == "marimo-export.market-summary.v1"
    assert summary_value["currency"] == "USD"
    assert summary_value["company_count"] == 3
    assert len(summary_value["period_returns"]) == 3
    assert baseline.output("performance_chart").media_type == ("application/vnd.vegalite.v6+json")
    assert baseline.output("performance_snapshot").media_type == "image/png"
    assert baseline.output("price_history").media_type == "application/vnd.apache.parquet"
    assert export.verify().states == 5


def test_vite_vanilla_example_builds_and_captures_live_export(
    tmp_path: Path,
) -> None:
    notebook_path = tmp_path / "finance.py"
    build_path = tmp_path / "build"
    warm_build_path = tmp_path / "build-warm"
    capture_path = tmp_path / "capture"
    build_repository = tmp_path / "build-repository"
    capture_repository = tmp_path / "capture-repository"
    source = (_EXAMPLE / "finance.py").read_bytes()
    shutil.copy2(_EXAMPLE / "finance.py", notebook_path)
    shutil.copy2(_EXAMPLE / "market_summary.py", tmp_path / "market_summary.py")

    build = _run(
        "marimo-export",
        "build",
        str(notebook_path),
        "--spec",
        str(_EXAMPLE / "finance.export.yaml"),
        "--output",
        str(build_path),
        "--repository",
        str(build_repository),
    )

    assert build.returncode == 0, build.stdout + build.stderr
    assert "Built notebook export at" in build.stdout
    assert "States: 5" in build.stdout
    assert "Outputs: 5" in build.stdout
    assert sum(_projection_cache(build.stdout)) == 25
    assert sum(_marimo_cache(build.stdout)) > 0
    assert "Elapsed:" in build.stdout
    assert notebook_path.read_bytes() == source

    warm_build = _run(
        "marimo-export",
        "build",
        str(notebook_path),
        "--spec",
        str(_EXAMPLE / "finance.export.yaml"),
        "--output",
        str(warm_build_path),
        "--repository",
        str(build_repository),
    )
    assert warm_build.returncode == 0, warm_build.stdout + warm_build.stderr
    assert _projection_cache(warm_build.stdout) == (0, 0)
    assert _marimo_cache(warm_build.stdout) == (0, 0)
    assert "Prepared reused (5/5)" in warm_build.stderr
    assert notebook_path.read_bytes() == source

    server = ManagedServer(notebook_path, timeout=120)
    try:
        server.activate()
        capture = _run(
            "marimo-export",
            "capture",
            server.base_url,
            "--session",
            server.session_id,
            "--spec",
            str(_EXAMPLE / "finance.export.yaml"),
            "--output",
            str(capture_path),
            "--repository",
            str(capture_repository),
            "--timeout",
            "120",
            extra_environment={"MARIMO_EXPORT_ACCESS_TOKEN": server.access_token},
        )
        assert capture.returncode == 0, capture.stdout + capture.stderr
        assert "Captured notebook export at" in capture.stdout
        assert "States: 5" in capture.stdout
        assert "Outputs: 5" in capture.stdout
        assert sum(_projection_cache(capture.stdout)) == 25
        with Client(
            server.base_url,
            access_token=server.access_token,
            timeout=30,
        ) as client:
            assert client.session(server.session_id).id == server.session_id
    finally:
        server.stop()

    assert notebook_path.read_bytes() == source
    for export_path in (build_path, warm_build_path, capture_path):
        _assert_export(export_path)
    relations = [
        tuple(state.fingerprint for state in open_export(export_path).states())
        for export_path in (build_path, warm_build_path, capture_path)
    ]
    assert relations[0] == relations[1] == relations[2]

    verify = _run("marimo-export", "verify", str(capture_path))
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "for 5 states" in verify.stdout
