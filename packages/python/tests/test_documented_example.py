from __future__ import annotations

import os
import subprocess
from pathlib import Path

from marimo_export import open_publication

_REPOSITORY = Path(__file__).parents[3]
_EXAMPLE = _REPOSITORY / "docs" / "examples"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["UV_NO_PROGRESS"] = "1"
    return subprocess.run(
        ["uv", "run", "--locked", *arguments],
        cwd=_EXAMPLE,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_linear_program_example_builds_and_verifies(tmp_path: Path) -> None:
    publication_path = tmp_path / "publication"
    build = _run(
        "marimo-export",
        "build",
        "02_linear_program.py",
        "--spec",
        "linear-program.export.yaml",
        "--output",
        str(publication_path),
    )

    assert build.returncode == 0, build.stdout + build.stderr
    assert "Published 3 states and 2 outputs" in build.stdout

    publication = open_publication(publication_path)
    assert tuple(state.name for state in publication.states()) == (
        "balanced",
        "favor-first-variable",
        "favor-second-variable",
    )
    assert publication.output_names == ("objective", "solution")

    verify = _run("marimo-export", "verify", str(publication_path))
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "for 3 states" in verify.stdout
