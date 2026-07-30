from __future__ import annotations

import os
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


def _write_exporter(path: Path, suffix: str) -> None:
    path.write_text(
        f"""
from marimo_export import BlobAsset
from helper import transform


def encode(value, *, increment=0):
    return BlobAsset(
        data=f"{{transform(value + increment)}}:{suffix}".encode("utf-8"),
        media_type="application/vnd.example.summary.v1+text",
        filename="summary.txt",
        metadata={{"suffix": "{suffix}"}},
    )
""".lstrip(),
        encoding="utf-8",
    )


def _write_helper(path: Path) -> None:
    path.write_text("from shared import transform\n", encoding="utf-8")


def _write_transform(path: Path, label: str) -> None:
    path.write_text(
        f'def transform(value):\n    return "{label}:" + str(value)\n',
        encoding="utf-8",
    )


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


def test_live_capture_refreshes_sideloaded_exporter_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    source = notebook.read_bytes()
    exporter = tmp_path / "publication_exports.py"
    helper = tmp_path / "helper.py"
    shared = tmp_path / "shared.py"
    _write_exporter(exporter, "first")
    _write_helper(helper)
    _write_transform(shared, "helper-a")
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
        shared_stat = shared.stat()
        _write_transform(shared, "helper-b")
        os.utime(
            shared,
            ns=(shared_stat.st_atime_ns, shared_stat.st_mtime_ns),
        )
        dependency_changed = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "dependency-changed",
            timeout=30,
        )
        exporter_stat = exporter.stat()
        _write_exporter(exporter, "second")
        os.utime(
            exporter,
            ns=(exporter_stat.st_atime_ns, exporter_stat.st_mtime_ns),
        )
        exporter_changed = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "exporter-changed",
            timeout=30,
        )
    finally:
        server.stop()

    assert (first.projection_cache.hits, first.projection_cache.misses) == (0, 1)
    assert (warm.projection_cache.hits, warm.projection_cache.misses) == (1, 0)
    assert (
        dependency_changed.projection_cache.hits,
        dependency_changed.projection_cache.misses,
    ) == (0, 1)
    assert (
        exporter_changed.projection_cache.hits,
        exporter_changed.projection_cache.misses,
    ) == (0, 1)
    assert (
        open_publication(dependency_changed.path)
        .state("baseline")
        .output("summary")
        .blob_asset()
        .data
        == b"helper-b:42:first"
    )
    assert (
        open_publication(exporter_changed.path)
        .state("baseline")
        .output("summary")
        .blob_asset()
        .data
        == b"helper-b:42:second"
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
