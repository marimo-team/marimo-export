from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from marimo_export import open_publication

_REPOSITORY = Path(__file__).parents[3]
_EXAMPLE = _REPOSITORY / "examples" / "finance"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["UV_NO_PROGRESS"] = "1"
    return subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "--package",
            "marimo-export-finance-example",
            *arguments,
        ],
        cwd=_REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        timeout=360,
        check=False,
    )


def test_finance_example_builds_live_publication(tmp_path: Path) -> None:
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
    assert "Published 6 states and 7 outputs" in build.stdout

    publication = open_publication(publication_path)
    assert tuple(state.name for state in publication.states()) == (
        "baseline",
        "compact",
        "focus",
        "narrow_universe",
        "short_window",
        "weekly",
    )
    assert publication.output_names == (
        "chart_png",
        "chart_vegalite",
        "dashboard",
        "ohlc_matrix",
        "prices_arrow",
        "prices_parquet",
        "row_count",
    )
    row_count = publication.state("baseline").output("row_count").scalar()
    assert isinstance(row_count, int) and not isinstance(row_count, bool)
    assert row_count > 0
    assert publication.verify().states == 6

    verify = _run("marimo-export", "verify", str(publication_path))
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "for 6 states" in verify.stdout
