from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

import pytest
from marimo_export import _environment as environment
from marimo_export._services.identity import producer_identity
from marimo_export.errors import ExecutionError


def _notebook(path: Path, *imports: str, metadata: str = "") -> Path:
    statements = "".join(f"    import {name}\n" for name in imports)
    path.write_text(
        f"""{metadata}import marimo

app = marimo.App()


@app.cell
def _():
{statements}    answer = "A"
    return (answer,)
""",
        encoding="utf-8",
    )
    return path


def _install(
    site: Path,
    name: str,
    version: str,
    *,
    module: str | None = None,
    requires: Iterable[str] = (),
    extras: Iterable[str] = (),
    top_level: bool = True,
) -> None:
    for previous in site.glob(f"{name}-*.dist-info"):
        shutil.rmtree(previous)
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        *(f"Provides-Extra: {extra}" for extra in extras),
        *(f"Requires-Dist: {requirement}" for requirement in requires),
    ]
    (info / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")
    record = [f"{info.name}/METADATA,,"]
    if module is not None:
        package = site / module
        package.mkdir(exist_ok=True)
        # Module bytes stay equal across versions, so only the recorded
        # distribution version can change the identity.
        (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        record.append(f"{module}/__init__.py,,")
        if top_level:
            (info / "top_level.txt").write_text(f"{module}\n", encoding="utf-8")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("environment") / "site-packages"
    path.mkdir()
    monkeypatch.syspath_prepend(str(path))
    return path


def _producer(notebook: Path) -> str:
    return producer_identity(notebook).producer_sha256


def test_unrelated_distribution_keeps_producer_identity(site: Path, tmp_path: Path) -> None:
    _install(site, "imported-dist", "1.0", module="imported_module")
    notebook = _notebook(tmp_path / "notebook.py", "imported_module")
    before = _producer(notebook)

    _install(site, "unrelated-dist", "1.0", module="unrelated_module")
    assert _producer(notebook) == before

    _install(site, "unrelated-dist", "2.0", module="unrelated_module")
    assert _producer(notebook) == before


@pytest.mark.parametrize("top_level", [True, False], ids=["top-level-file", "record-only"])
def test_imported_distribution_version_changes_producer_identity(
    site: Path, tmp_path: Path, top_level: bool
) -> None:
    _install(site, "imported-dist", "1.0", module="imported_module", top_level=top_level)
    notebook = _notebook(tmp_path / "notebook.py", "imported_module")
    before = producer_identity(notebook)

    (site / "imported_module" / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
    after_source = producer_identity(notebook)
    _install(site, "imported-dist", "1.1", module="imported_module", top_level=top_level)

    assert after_source.producer_sha256 == before.producer_sha256
    assert producer_identity(notebook).producer_sha256 != before.producer_sha256


def test_runtime_extra_distribution_changes_producer_identity(site: Path, tmp_path: Path) -> None:
    _install(
        site,
        "marimo-export",
        "9.0",
        extras=("charts",),
        requires=("chart-dist; extra == 'charts'",),
    )
    _install(site, "chart-dist", "1.0")
    notebook = _notebook(tmp_path / "notebook.py")
    before = _producer(notebook)

    _install(site, "chart-dist", "2.0")

    assert _producer(notebook) != before


def test_required_distribution_follows_markers_and_extras(site: Path, tmp_path: Path) -> None:
    _install(
        site,
        "imported-dist",
        "1.0",
        module="imported_module",
        requires=(
            "required-dist[fast]>=1",
            "skipped-platform; sys_platform == 'never'",
            "skipped-extra; extra == 'docs'",
        ),
    )
    _install(site, "required-dist", "1.0", requires=("fast-dist; extra == 'fast'",))
    for name in ("fast-dist", "skipped-platform", "skipped-extra"):
        _install(site, name, "1.0")
    notebook = _notebook(tmp_path / "notebook.py", "imported_module")
    before = _producer(notebook)

    for name in ("skipped-platform", "skipped-extra"):
        _install(site, name, "2.0")
    assert _producer(notebook) == before

    _install(site, "fast-dist", "2.0")
    after_extra = _producer(notebook)
    _install(site, "required-dist", "2.0", requires=("fast-dist; extra == 'fast'",))

    assert after_extra != before
    assert _producer(notebook) != after_extra


def test_sibling_module_outside_imports_keeps_producer_identity(tmp_path: Path) -> None:
    notebook = _notebook(tmp_path / "notebook.py")
    sibling = tmp_path / "other_script.py"
    sibling.write_text("VALUE = 1\n", encoding="utf-8")
    package = tmp_path / "other_package"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = _producer(notebook)

    sibling.write_text("VALUE = 2\n", encoding="utf-8")
    (package / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
    _notebook(tmp_path / "other_notebook.py", "other_script")

    assert _producer(notebook) == before


def test_imported_local_module_changes_dependency_identity(tmp_path: Path) -> None:
    notebook = _notebook(tmp_path / "notebook.py", "helper")
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    before = producer_identity(notebook)

    _notebook(notebook, "helper", "math")
    after_notebook = producer_identity(notebook)
    helper.write_text("VALUE = 2\n", encoding="utf-8")
    after_helper = producer_identity(notebook)

    assert after_notebook.environment_sha256 == before.environment_sha256
    assert after_notebook.producer_sha256 != before.producer_sha256
    assert after_helper.environment_sha256 != before.environment_sha256


def test_transitive_local_import_changes_producer_identity(tmp_path: Path) -> None:
    notebook = _notebook(tmp_path / "notebook.py", "helper")
    (tmp_path / "helper.py").write_text(
        "def load():\n    from analysis import model\n    return model\n",
        encoding="utf-8",
    )
    package = tmp_path / "analysis"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    model = package / "model.py"
    model.write_text("from .weights import WEIGHTS\n", encoding="utf-8")
    weights = package / "weights.py"
    weights.write_text("WEIGHTS = 1\n", encoding="utf-8")
    before = _producer(notebook)

    weights.write_text("WEIGHTS = 2\n", encoding="utf-8")

    assert _producer(notebook) != before


def test_imported_package_skips_hidden_and_tool_directories(tmp_path: Path) -> None:
    notebook = _notebook(tmp_path / "notebook.py", "analysis")
    package = tmp_path / "analysis"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    ignored = []
    for directory in (".venv", "__pycache__", "node_modules"):
        (package / directory).mkdir()
        path = package / directory / "ignored.py"
        path.write_text("VALUE = 1\n", encoding="utf-8")
        ignored.append(path)
    before = _producer(notebook)

    for path in ignored:
        path.write_text("VALUE = 2\n", encoding="utf-8")

    assert _producer(notebook) == before


def test_declared_import_widens_local_scope(tmp_path: Path) -> None:
    metadata = "# /// script\n# [tool.marimo-export]\n# imports = ['plugins.loader']\n# ///\n"
    notebook = _notebook(tmp_path / "notebook.py", metadata=metadata)
    package = tmp_path / "plugins"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    loader = package / "loader.py"
    loader.write_text("VALUE = 1\n", encoding="utf-8")
    before = _producer(notebook)

    loader.write_text("VALUE = 2\n", encoding="utf-8")

    assert _producer(notebook) != before


@pytest.mark.parametrize(
    "declaration",
    [
        "# imports = 'plugins'\n",
        "# imports = ['plugins-loader']\n",
        "# modules = ['plugins']\n",
    ],
)
def test_invalid_import_declaration_is_rejected(tmp_path: Path, declaration: str) -> None:
    metadata = f"# /// script\n# [tool.marimo-export]\n{declaration}# ///\n"
    notebook = _notebook(tmp_path / "notebook.py", metadata=metadata)

    with pytest.raises(ExecutionError, match=r"tool\.marimo-export\.imports") as error:
        producer_identity(notebook)

    assert error.value.code == "notebook_invalid"


def test_project_source_resolves_equally_with_kernel_path_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'analysis'\n", encoding="utf-8")
    package = tmp_path / "src" / "analysis"
    package.mkdir(parents=True)
    module = package / "__init__.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "notebooks").mkdir()
    notebook = _notebook(tmp_path / "notebooks" / "notebook.py", "analysis")
    client = producer_identity(notebook)

    # A kernel adds `src` to `sys.path` from `runtime.pythonpath` or a cell.
    with monkeypatch.context() as kernel:
        kernel.syspath_prepend(str(tmp_path / "src"))
        assert producer_identity(notebook) == client

    module.write_text("VALUE = 2\n", encoding="utf-8")

    assert _producer(notebook) != client.producer_sha256


def test_markdown_notebook_tracks_cell_imports_and_front_matter_declaration(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.md"
    notebook.write_bytes(
        b"---\r\n"
        b"header: |-\r\n"
        b"  # /// script\r\n"
        b"  # [tool.marimo-export]\r\n"
        b"  # imports = ['plugins']\r\n"
        b"  # ///\r\n"
        b"---\r\n\r\n"
        b"```{.marimo}\r\n"
        b"import helper\r\n"
        b"```\r\n"
    )
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    plugins = tmp_path / "plugins.py"
    plugins.write_text("VALUE = 1\n", encoding="utf-8")
    before = environment.environment_identity(notebook)

    helper.write_text("VALUE = 2\n", encoding="utf-8")
    after_helper = environment.environment_identity(notebook)
    plugins.write_text("VALUE = 2\n", encoding="utf-8")

    assert after_helper != before
    assert environment.environment_identity(notebook) != after_helper


def test_imported_project_outside_notebook_directory_changes_producer_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    project = tmp_path_factory.mktemp("editable-project")
    package = project / "external_package"
    package.mkdir()
    module = package / "__init__.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(project))
    notebook = _notebook(tmp_path_factory.mktemp("notebooks") / "notebook.py", "external_package")
    before = _producer(notebook)

    module.write_text("VALUE = 2\n", encoding="utf-8")

    assert _producer(notebook) != before


def test_dependency_change_during_identity_scan_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    notebook = _notebook(tmp_path / "notebook.py", "helper")
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    native = environment._read_stable

    def changing(path: Path) -> tuple[bytes, tuple[int, int, int, int, int]]:
        result = native(path)
        if path == helper:
            helper.write_text(f"VALUE = {len(result[0])}\n", encoding="utf-8")
        return result

    monkeypatch.setattr(environment, "_read_stable", changing)

    with pytest.raises(RuntimeError, match="changed while identity was computed"):
        producer_identity(notebook)
