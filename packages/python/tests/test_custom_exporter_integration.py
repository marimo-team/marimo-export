from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from marimo_export import ExportSpec, OutputSpec, capture, open_publication
from marimo_export._remote.managed import ManagedServer
from marimo_export.exporters import importable


def _write_notebook(path: Path) -> None:
    path.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    answer = 41
    return (answer,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )


def _write_exporter(path: Path, label: str) -> None:
    path.write_text(
        f"""
from marimo_export import BlobAsset
import numpy as np


def encode(value, *, increment):
    return BlobAsset(
        data=f"{label}:{{int(np.int64(value + increment))}}".encode("utf-8"),
        media_type="application/vnd.example.summary.v1+text",
        filename="summary.txt",
        metadata={{"label": "{label}"}},
    )
""".lstrip(),
        encoding="utf-8",
    )


def _write_dependent_exporter(path: Path) -> None:
    path.write_text(
        """
from helper import transform
from marimo_export import BlobAsset


def encode(value, *, increment=0):
    return BlobAsset(
        data=transform(value + increment).encode("utf-8"),
        media_type="application/vnd.example.summary.v1+text",
    )
""".lstrip(),
        encoding="utf-8",
    )


def _write_local_import_exporter(path: Path) -> None:
    path.write_text(
        """
from marimo_export import BlobAsset


def encode(value):
    from helper import transform

    return BlobAsset(
        data=transform(value).encode("utf-8"),
        media_type="application/vnd.example.summary.v1+text",
    )
""".lstrip(),
        encoding="utf-8",
    )


def _write_helper(path: Path, label: str) -> None:
    path.write_text(
        f"""
def transform(value):
    return "{label}:" + str(value)
""".lstrip(),
        encoding="utf-8",
    )


def _write_default_exporter(path: Path) -> None:
    path.write_text(
        """
from helper import PREFIX
from marimo_export import BlobAsset


def encode(value, prefix=PREFIX):
    return BlobAsset(
        data=f"{prefix}:{value}".encode("utf-8"),
        media_type="application/vnd.example.summary.v1+text",
    )
""".lstrip(),
        encoding="utf-8",
    )


def _write_prefix(path: Path, label: str) -> None:
    path.write_text(f'PREFIX = "{label}"\n', encoding="utf-8")


def _write_callable_exporter(path: Path) -> None:
    path.write_text(
        """
from marimo_export import BlobAsset


class Encoder:
    def __init__(self, label):
        self.label = label

    def __call__(self, value, *, increment):
        return BlobAsset(
            data=f"{self.label}:{value + increment}".encode("utf-8"),
            media_type="application/vnd.example.summary.v1+text",
        )


encode = Encoder("callable")
""".lstrip(),
        encoding="utf-8",
    )


def _write_module_exporter(path: Path) -> None:
    path.write_text(
        """
import helper
from marimo_export import BlobAsset


def encode(value):
    return BlobAsset(
        data=helper.transform(value).encode("utf-8"),
        media_type="application/vnd.example.summary.v1+text",
    )
""".lstrip(),
        encoding="utf-8",
    )


def _write_reexport(path: Path) -> None:
    path.write_text("from shared import transform\n", encoding="utf-8")


def _capture(
    notebook: Path,
    spec: ExportSpec,
    output: Path,
) -> tuple[int, int]:
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        result = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=output,
            timeout=30,
        )
        return result.projection_cache.hits, result.projection_cache.misses
    finally:
        server.stop()


def test_capture_sideloads_an_importable_exporter_and_invalidates_changed_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    source = notebook.read_bytes()
    exporter = tmp_path / "publication_exports.py"
    _write_exporter(exporter, "first")
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = str(tmp_path)
    if existing_pythonpath:
        pythonpath = f"{pythonpath}{os.pathsep}{existing_pythonpath}"
    monkeypatch.setenv("PYTHONPATH", pythonpath)

    spec = ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec(
                source="answer",
                exporter=importable("publication_exports:encode", increment=1),
            )
        },
    )

    first_cache = _capture(notebook, spec, tmp_path / "first")
    warm_cache = _capture(notebook, spec, tmp_path / "warm")
    _write_exporter(exporter, "second")
    changed_cache = _capture(notebook, spec, tmp_path / "changed")

    assert first_cache == (0, 1)
    assert warm_cache == (1, 0)
    assert changed_cache == (0, 1)
    assert (
        open_publication(tmp_path / "first").state("baseline").output("summary").blob_asset().data
        == b"first:42"
    )
    assert (
        open_publication(tmp_path / "changed").state("baseline").output("summary").blob_asset().data
        == b"second:42"
    )
    assert notebook.read_bytes() == source


def test_live_capture_refreshes_exporter_modules_with_reload_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    source = notebook.read_bytes()
    exporter = tmp_path / "publication_exports.py"
    _write_exporter(exporter, "first")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    config_home = tmp_path / "config"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    spec = ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec(
                source="answer",
                exporter=importable("publication_exports:encode", increment=1),
            )
        },
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        first = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "first",
            timeout=30,
        )
        warm = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "warm",
            timeout=30,
        )
        exporter_stat = exporter.stat()
        _write_exporter(exporter, "other")
        os.utime(
            exporter,
            ns=(exporter_stat.st_atime_ns, exporter_stat.st_mtime_ns),
        )
        changed = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "changed",
            timeout=30,
        )
        _write_dependent_exporter(exporter)
        helper = tmp_path / "helper.py"
        _write_helper(helper, "helper-a")
        dependent = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "dependent",
            timeout=30,
        )
        helper_stat = helper.stat()
        _write_helper(helper, "helper-b")
        os.utime(
            helper,
            ns=(helper_stat.st_atime_ns, helper_stat.st_mtime_ns),
        )
        refreshed = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "refreshed",
            timeout=30,
        )
    finally:
        server.stop()

    assert (first.projection_cache.hits, first.projection_cache.misses) == (0, 1)
    assert (warm.projection_cache.hits, warm.projection_cache.misses) == (1, 0)
    assert (changed.projection_cache.hits, changed.projection_cache.misses) == (0, 1)
    assert (dependent.projection_cache.hits, dependent.projection_cache.misses) == (0, 1)
    assert (refreshed.projection_cache.hits, refreshed.projection_cache.misses) == (0, 1)
    assert (
        open_publication(changed.path).state("baseline").output("summary").blob_asset().data
        == b"other:42"
    )
    assert (
        open_publication(dependent.path).state("baseline").output("summary").blob_asset().data
        == b"helper-a:42"
    )
    assert (
        open_publication(refreshed.path).state("baseline").output("summary").blob_asset().data
        == b"helper-b:42"
    )
    assert notebook.read_bytes() == source


def test_capture_sideloads_an_importable_callable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    source = notebook.read_bytes()
    _write_callable_exporter(tmp_path / "publication_exports.py")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    spec = ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec(
                source="answer",
                exporter=importable("publication_exports:encode", increment=1),
            )
        },
    )

    assert _capture(notebook, spec, tmp_path / "publication") == (0, 1)
    assert (
        open_publication(tmp_path / "publication")
        .state("baseline")
        .output("summary")
        .blob_asset()
        .data
        == b"callable:42"
    )
    assert notebook.read_bytes() == source


@pytest.mark.parametrize(
    "write_exporter",
    [_write_dependent_exporter, _write_local_import_exporter],
    ids=["module-import", "function-local-import"],
)
def test_custom_exporter_cache_identity_tracks_local_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_exporter: Callable[[Path], None],
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    write_exporter(tmp_path / "publication_exports.py")
    helper = tmp_path / "helper.py"
    _write_helper(helper, "first")
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = str(tmp_path)
    if existing_pythonpath:
        pythonpath = f"{pythonpath}{os.pathsep}{existing_pythonpath}"
    monkeypatch.setenv("PYTHONPATH", pythonpath)
    spec = ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec(
                source="answer",
                exporter=importable("publication_exports:encode"),
            )
        },
    )

    first_cache = _capture(notebook, spec, tmp_path / "first")
    warm_cache = _capture(notebook, spec, tmp_path / "warm")
    _write_helper(helper, "changed-helper")
    changed_cache = _capture(notebook, spec, tmp_path / "changed")

    assert first_cache == (0, 1)
    assert warm_cache == (1, 0)
    assert changed_cache == (0, 1)
    assert (
        open_publication(tmp_path / "changed").state("baseline").output("summary").blob_asset().data
        == b"changed-helper:41"
    )


def test_custom_exporter_cache_identity_tracks_default_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    _write_default_exporter(tmp_path / "publication_exports.py")
    helper = tmp_path / "helper.py"
    _write_prefix(helper, "first")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    spec = ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec(
                source="answer",
                exporter=importable("publication_exports:encode"),
            )
        },
    )

    assert _capture(notebook, spec, tmp_path / "first") == (0, 1)
    assert _capture(notebook, spec, tmp_path / "warm") == (1, 0)
    _write_prefix(helper, "changed-default")
    assert _capture(notebook, spec, tmp_path / "changed") == (0, 1)
    assert (
        open_publication(tmp_path / "changed").state("baseline").output("summary").blob_asset().data
        == b"changed-default:41"
    )


def test_custom_exporter_cache_identity_tracks_transitive_reexports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    _write_module_exporter(tmp_path / "publication_exports.py")
    _write_reexport(tmp_path / "helper.py")
    shared = tmp_path / "shared.py"
    _write_helper(shared, "first")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    spec = ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec(
                source="answer",
                exporter=importable("publication_exports:encode"),
            )
        },
    )

    assert _capture(notebook, spec, tmp_path / "first") == (0, 1)
    assert _capture(notebook, spec, tmp_path / "warm") == (1, 0)
    _write_helper(shared, "changed-transitive")
    assert _capture(notebook, spec, tmp_path / "changed") == (0, 1)
    assert (
        open_publication(tmp_path / "changed").state("baseline").output("summary").blob_asset().data
        == b"changed-transitive:41"
    )
