from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from marimo_export import Client, open_publication
from marimo_export._remote.managed import ManagedServer

_REPOSITORY = Path(__file__).parents[3]
_EXAMPLE = _REPOSITORY / "examples" / "vite-vanilla"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["UV_NO_PROGRESS"] = "1"
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


def _assert_publication(path: Path) -> None:
    publication = open_publication(path)
    assert tuple(state.name for state in publication.states()) == (
        "ai_buildout",
        "baseline",
        "cloud_platforms",
        "full_watchlist",
        "weekly_view",
    )
    assert publication.output_names == (
        "market_explorer",
        "market_summary",
        "performance_chart",
        "performance_snapshot",
        "price_history",
    )
    baseline = publication.state("baseline")
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
    assert publication.verify().states == 5


def test_vite_vanilla_example_builds_and_captures_live_publication(
    tmp_path: Path,
) -> None:
    notebook_path = tmp_path / "finance.py"
    build_path = tmp_path / "build"
    warm_build_path = tmp_path / "build-warm"
    capture_path = tmp_path / "capture"
    source = (_EXAMPLE / "finance.py").read_bytes()
    assert b"marimo_export" not in source
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
    )

    assert build.returncode == 0, build.stdout + build.stderr
    assert "Published 5 states and 5 outputs" in build.stdout
    assert sum(_projection_cache(build.stdout)) == 25
    activity = re.search(
        r"Upstream cache activity: (\d+) hits?, (\d+) misses?",
        build.stdout,
    )
    assert activity is not None
    assert int(activity.group(1)) + int(activity.group(2)) > 0
    assert "Phase timings:" in build.stdout
    assert "Fresh-child timings (5 states):" in build.stdout
    assert notebook_path.read_bytes() == source

    warm_build = _run(
        "marimo-export",
        "build",
        str(notebook_path),
        "--spec",
        str(_EXAMPLE / "finance.export.yaml"),
        "--output",
        str(warm_build_path),
    )
    assert warm_build.returncode == 0, warm_build.stdout + warm_build.stderr
    assert _projection_cache(warm_build.stdout) == (25, 0)
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
            f"--access-token={server.access_token}",
            "--spec",
            str(_EXAMPLE / "finance.export.yaml"),
            "--output",
            str(capture_path),
            "--timeout",
            "120",
        )
        assert capture.returncode == 0, capture.stdout + capture.stderr
        assert "Published 5 states and 5 outputs" in capture.stdout
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
    for publication_path in (build_path, warm_build_path, capture_path):
        _assert_publication(publication_path)
    assert (build_path / "index.json").read_bytes() == (warm_build_path / "index.json").read_bytes()
    assert (build_path / "index.json").read_bytes() == (capture_path / "index.json").read_bytes()

    verify = _run("marimo-export", "verify", str(capture_path))
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "for 5 states" in verify.stdout
