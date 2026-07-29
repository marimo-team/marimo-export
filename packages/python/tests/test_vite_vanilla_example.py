from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from marimo_export import open_publication

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


def test_vite_vanilla_example_builds_live_publication(tmp_path: Path) -> None:
    notebook_path = tmp_path / "finance.py"
    publication_path = tmp_path / "publication"
    shutil.copy2(_EXAMPLE / "finance.py", notebook_path)

    build = _run(
        "marimo-export",
        "build",
        str(notebook_path),
        "--spec",
        str(_EXAMPLE / "finance.export.yaml"),
        "--output",
        str(publication_path),
    )

    assert build.returncode == 0, build.stdout + build.stderr
    assert "Published 5 states and 4 outputs" in build.stdout
    assert "Projection cache:" in build.stdout
    activity = re.search(
        r"Upstream cache activity: (\d+) hits?, (\d+) misses?",
        build.stdout,
    )
    assert activity is not None
    assert int(activity.group(1)) + int(activity.group(2)) > 0
    assert "Phase timings:" in build.stdout
    assert "Fresh-child timings (5 states):" in build.stdout

    publication = open_publication(publication_path)
    assert tuple(state.name for state in publication.states()) == (
        "ai_buildout",
        "baseline",
        "cloud_platforms",
        "full_watchlist",
        "weekly_view",
    )
    assert publication.output_names == (
        "market_explorer",
        "performance_chart",
        "performance_snapshot",
        "price_history",
    )
    assert publication.verify().states == 5

    verify = _run("marimo-export", "verify", str(publication_path))
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "for 5 states" in verify.stdout
