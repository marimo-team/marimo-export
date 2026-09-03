from __future__ import annotations

import os
from pathlib import Path

import pytest
from export_integration_support import (
    capture_export as _capture,
)
from export_integration_support import (
    capture_live as _capture_live,
)
from marimo_export import ExportSpec, OutputSpec, open_export
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import OutputError
from marimo_export.exporters import importable
from marimo_export.sessions import Client


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
from marimo_export.outputs import BlobAsset
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
from marimo_export.outputs import BlobAsset


class Encoder:
    def __init__(self, label):
        self.label = label
        self.calls = 0

    def __call__(self, value, *, increment):
        self.calls += 1
        return BlobAsset(
            data=f"{self.label}:{value + increment}".encode("utf-8"),
            media_type="application/vnd.example.summary.v1+text",
        )


encode = Encoder("callable")
""".lstrip(),
        encoding="utf-8",
    )


def test_live_capture_forces_custom_output_and_requires_restart_after_source_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    source = notebook.read_bytes()
    exporter = tmp_path / "export_exports.py"
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
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec.export(
                "answer",
                importable(
                    "export_exports:encode",
                    options={"increment": 1},
                    dependencies=("helper", "shared"),
                ),
            )
        },
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        first_activity = _capture_live(server, spec, tmp_path / "first")
        warm_activity = _capture_live(server, spec, tmp_path / "warm")
        shared_stat = shared.stat()
        _write_transform(shared, "helper-b")
        os.utime(
            shared,
            ns=(shared_stat.st_atime_ns, shared_stat.st_mtime_ns),
        )
        with pytest.raises(OutputError) as raised:
            _capture_live(server, spec, tmp_path / "dependency-changed")
    finally:
        server.stop()

    assert (first_activity.projection_hits, first_activity.projection_misses) == (0, 1)
    assert (warm_activity.projection_hits, warm_activity.projection_misses) == (0, 1)
    assert raised.value.code == "exporter_source_changed"
    assert (
        open_export(tmp_path / "first").state("baseline").output("summary").blob_asset().data
        == b"helper-a:42:first"
    )
    assert notebook.read_bytes() == source


def test_capture_sideloads_an_importable_callable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    source = notebook.read_bytes()
    _write_callable_exporter(tmp_path / "export_exports.py")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec.export(
                "answer",
                importable(
                    "export_exports:encode",
                    options={"increment": 1},
                ),
            )
        },
    )

    activity = _capture(notebook, spec, tmp_path / "export")
    assert (activity.projection_hits, activity.projection_misses) == (0, 1)
    output = open_export(tmp_path / "export").state("baseline").output("summary")
    assert output.blob_asset().data == b"callable:42"
    assert output.descriptor.provenance.python_type == "marimo_export.outputs.BlobAsset"
    assert notebook.read_bytes() == source


def test_custom_exporter_builds_are_deterministic_while_both_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
    count = tmp_path / "count.txt"
    (tmp_path / "export_exports.py").write_text(
        """
import os
from pathlib import Path
from marimo_export.outputs import BlobAsset


def encode(value):
    path = Path(os.environ["MARIMO_EXPORT_TEST_COUNT"])
    current = int(path.read_text(encoding="utf-8")) if path.exists() else 0
    path.write_text(str(current + 1), encoding="utf-8")
    return BlobAsset(
        data=f"stable:{value}".encode("utf-8"),
        media_type="application/vnd.example.summary.v1+text",
    )
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("MARIMO_EXPORT_TEST_COUNT", str(count))
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec.export(
                "answer",
                importable("export_exports:encode"),
            )
        },
    )

    first_cache = _capture(notebook, spec, tmp_path / "first")
    second_cache = _capture(notebook, spec, tmp_path / "second")
    first = open_export(tmp_path / "first")
    second = open_export(tmp_path / "second")

    assert (
        first_cache.projection_hits,
        first_cache.projection_misses,
        second_cache.projection_hits,
        second_cache.projection_misses,
    ) == (0, 1, 0, 1)
    assert count.read_text(encoding="utf-8") == "2"
    assert (tmp_path / "first" / "index.json").read_bytes() == (
        tmp_path / "second" / "index.json"
    ).read_bytes()
    assert first.identity == second.identity


def test_preloaded_custom_exporter_uses_loaded_code_until_process_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import export_exports
    answer = 41
    preloaded_encode = export_exports.encode
    return answer, export_exports, preloaded_encode


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    exporter = tmp_path / "export_exports.py"
    _write_exporter(exporter, "loaded-v1")
    _write_helper(tmp_path / "helper.py")
    _write_transform(tmp_path / "shared.py", "stable")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "summary": OutputSpec.export(
                "answer",
                importable(
                    "export_exports:encode",
                    dependencies=("helper", "shared"),
                ),
            )
        },
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        with Client(
            server.base_url,
            access_token=server.access_token,
            timeout=30,
        ) as client:
            description = client.session(server.session_id).inspect()
        assert any(definition.name == "preloaded_encode" for definition in description.definitions)
        exporter_stat = exporter.stat()
        _write_exporter(exporter, "disk-v222")
        os.utime(
            exporter,
            ns=(exporter_stat.st_atime_ns, exporter_stat.st_mtime_ns),
        )
        first_activity = _capture_live(server, spec, tmp_path / "loaded")
    finally:
        server.stop()
    exporter_stat = exporter.stat()
    os.utime(
        exporter,
        ns=(exporter_stat.st_atime_ns, exporter_stat.st_mtime_ns + 1_000_000_000),
    )
    restarted_cache = _capture(notebook, spec, tmp_path / "restarted")

    assert (first_activity.projection_hits, first_activity.projection_misses) == (0, 1)
    assert (restarted_cache.projection_hits, restarted_cache.projection_misses) == (0, 1)
    assert (
        open_export(tmp_path / "loaded").state("baseline").output("summary").blob_asset().data
        == b"stable:41:loaded-v1"
    )
    assert (
        open_export(tmp_path / "restarted").state("baseline").output("summary").blob_asset().data
        == b"stable:41:disk-v222"
    )
