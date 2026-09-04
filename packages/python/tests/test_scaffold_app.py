from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from importlib.metadata import version as distribution_version
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_REPOSITORY = Path(__file__).parents[3]
_SKILL = _REPOSITORY / "skills/notebook-to-static-app"
_SCRIPT = _SKILL / "scripts/scaffold_app.py"


def _scaffold_module():
    spec = importlib.util.spec_from_file_location("marimo_export_scaffold_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _notebook(path: Path) -> Path:
    path.write_text(
        """# /// script
# dependencies = ["polars==1.40.0"]
# requires-python = ">=3.10"
# ///

print("analysis")
""",
        encoding="utf-8",
    )
    return path


def test_scaffold_requires_python_intersects_the_package_range() -> None:
    validate = _scaffold_module()._validate_requires_python

    assert validate(">=3.12") == "<3.15,>=3.12"
    assert validate(">=3.10,<3.11") == ">=3.10,<3.11"
    assert validate(">=3.9") == "<3.15,>=3.10"
    with pytest.raises(SystemExit, match="does not intersect"):
        validate("<3.10")
    with pytest.raises(SystemExit, match="does not intersect"):
        validate(">=3.15")


def test_scaffold_is_source_preserving_and_relocatable(tmp_path: Path) -> None:
    notebook = _notebook(tmp_path / "analysis.py")
    source = notebook.read_bytes()
    browser_package = tmp_path / "browser.tgz"
    browser_package.write_bytes(b"browser package")
    python_package = tmp_path / "marimo_export-0.0.0-py3-none-any.whl"
    python_package.write_bytes(b"python package")
    output = tmp_path / "app"

    completed = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--group",
            "dev",
            "python",
            str(_SCRIPT),
            "--notebook",
            str(notebook),
            "--output",
            str(output),
            "--browser-package",
            str(browser_package),
            "--python-package",
            str(python_package),
            "--loader",
            "parquet",
            "--loader",
            "marimo-cell",
        ],
        check=True,
        capture_output=True,
        cwd=_REPOSITORY,
        text=True,
    )

    assert completed.stdout.strip() == str(output)
    assert notebook.read_bytes() == source
    package = json.loads((output / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["@marimo-team/marimo-export"] == (
        "file:vendor/marimo-export.tgz"
    )
    assert package["dependencies"]["hyparquet"] == "1.29.2"
    project = tomllib.loads((output / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == "<3.15,>=3.10"
    assert project["tool"]["uv"]["sources"]["marimo-export"] == {
        "path": "vendor/marimo_export-0.0.0-py3-none-any.whl"
    }
    assert json.loads((output / ".notebook-source.json").read_text(encoding="utf-8")) == {
        "filename": "analysis.py",
        "sha256": hashlib.sha256(source).hexdigest(),
    }
    assert 'base: "./"' in (output / "vite.config.ts").read_text(encoding="utf-8")
    assert 'src="./src/main.ts"' in (output / "index.html").read_text(encoding="utf-8")

    relocated = tmp_path / "relocated" / "app"
    relocated.parent.mkdir()
    shutil.move(output, relocated)
    assert (relocated / "vendor/marimo-export.tgz").read_bytes() == b"browser package"
    assert (relocated / "vendor/marimo_export-0.0.0-py3-none-any.whl").read_bytes() == (
        b"python package"
    )


def test_installed_skill_scaffolds_with_matching_registry_packages(tmp_path: Path) -> None:
    installed_skill = tmp_path / "marimo_export.agent-plugin/skills/notebook-to-static-app"
    shutil.copytree(_SKILL, installed_skill)
    notebook = _notebook(tmp_path / "analysis.py")
    output = tmp_path / "app"

    subprocess.run(
        [
            sys.executable,
            str(installed_skill / "scripts/scaffold_app.py"),
            "--notebook",
            str(notebook),
            "--output",
            str(output),
            "--loader",
            "parquet",
        ],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        text=True,
    )

    package_version = distribution_version("marimo-export")
    package = json.loads((output / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"] == {
        "@marimo-team/marimo-export": package_version,
        "hyparquet": "1.29.2",
    }
    project = tomllib.loads((output / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == [
        "polars==1.40.0",
        f"marimo-export[all]=={package_version}",
    ]
    assert "sources" not in project["tool"]["uv"]
    assert not (output / "vendor").exists()
