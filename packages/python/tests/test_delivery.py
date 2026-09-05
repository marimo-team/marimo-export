from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import cast

import marimo_export._directory as directory_module
import marimo_export.delivery as delivery_module
import pytest
from marimo_export import open_export
from marimo_export._diagnostics import cleanup_failures
from marimo_export._directory_native import exchange_directories
from marimo_export._writer import write_export
from marimo_export.delivery import StagedDelivery, stage
from marimo_export.descriptors import Provenance, ScalarDescriptor
from marimo_export.errors import IntegrityError, NotebookExportError
from marimo_export.index import (
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    StateEntry,
)
from marimo_export.prepared import PreparedExport
from marimo_export.progress import ProgressEvent
from marimo_export.wire import state_fingerprint


def _index() -> ExportIndex:
    fingerprint = state_fingerprint({})
    return ExportIndex(
        spec_sha256="d" * 64,
        default_state=fingerprint,
        notebook=NotebookProvenance(
            filename="notebook.py",
            document_sha256="a" * 64,
        ),
        producer=ProducerProvenance(
            marimo="0.24.0",
            marimo_export="0.0.0",
            implementation_sha256="c" * 64,
        ),
        inputs=(),
        control_bindings={},
        outputs=("value",),
        aliases={"baseline": fingerprint},
        states={
            fingerprint: StateEntry(
                inputs={},
                outputs={
                    "value": ScalarDescriptor(
                        value=42,
                        provenance=Provenance(python_type="builtins.int"),
                    )
                },
            )
        },
    )


def _site(path: Path, value: str) -> None:
    path.mkdir()
    path.joinpath("index.html").write_text(value, encoding="utf-8")


class _PreparedFixture:
    def __init__(self) -> None:
        self.index = _index()

    def write(
        self,
        output: Path,
        *,
        replace: bool = False,
        progress: object = None,
    ) -> object:
        assert replace is False
        written = write_export(self.index, {}, output, replace=False)
        result = SimpleNamespace(path=written.path, identity=open_export(output).identity)
        if progress is not None:
            cast(Callable[[ProgressEvent], None], progress)(ProgressEvent(kind="write_finished"))
        return result


@pytest.fixture
def prepared(monkeypatch: pytest.MonkeyPatch) -> PreparedExport:
    monkeypatch.setattr(delivery_module, "PreparedExport", _PreparedFixture)
    monkeypatch.setattr(
        delivery_module,
        "_materialize_prepared_export",
        lambda prepared, output, *, progress: prepared.write(output, progress=progress),
    )
    return cast(PreparedExport, _PreparedFixture())


def test_delivery_materializes_and_commits_a_verified_nested_export(
    tmp_path: Path,
    prepared: PreparedExport,
) -> None:
    output = tmp_path / "site"

    with stage(output) as staged:
        staged.path.joinpath("index.html").write_text("site", encoding="utf-8")
        materialized = staged.materialize(prepared, "data/export")
        result = staged.commit()

    assert result.path == output
    assert result.files == 2
    assert materialized.identity == open_export(output / "data/export").identity
    assert open_export(output / "data/export").verify().states == 1
    assert output.joinpath("index.html").read_text(encoding="utf-8") == "site"


def test_delivery_reports_materialization_and_precommit_progress(
    tmp_path: Path,
    prepared: PreparedExport,
) -> None:
    output = tmp_path / "site"
    events: list[ProgressEvent] = []

    with stage(output) as staged:
        staged.path.joinpath("index.html").write_text("site", encoding="utf-8")

        def materialization_progress(event: ProgressEvent) -> None:
            events.append(event)
            with pytest.raises(RuntimeError, match="progress callback is active"):
                staged.commit()

        staged.materialize(
            prepared,
            "data/export",
            progress=materialization_progress,
        )

        def progress(event: ProgressEvent) -> None:
            events.append(event)
            if event.kind == "delivery_commit_started":
                assert not output.exists()

        staged.commit(progress=progress)

    assert [event.kind for event in events] == [
        "write_finished",
        "delivery_verification_started",
        "delivery_commit_started",
    ]
    assert events[-1].elapsed_seconds is not None


def test_delivery_reverifies_callback_changes_before_commit(
    tmp_path: Path,
    prepared: PreparedExport,
) -> None:
    output = tmp_path / "site"

    with stage(output) as staged:
        staged_path = staged.path
        staged.materialize(prepared, "data/export")

        def change_nested_export(event: ProgressEvent) -> None:
            if event.kind == "delivery_commit_started":
                staged_path.joinpath("data/export/index.json").write_text(
                    "changed",
                    encoding="utf-8",
                )

        with pytest.raises((IntegrityError, NotebookExportError)):
            staged.commit(progress=change_nested_export)

    assert not output.exists()


def test_materialization_progress_failure_keeps_registered_export(
    tmp_path: Path,
    prepared: PreparedExport,
) -> None:
    output = tmp_path / "site"

    with stage(output) as staged:

        def fail(event: ProgressEvent) -> None:
            assert event.kind == "write_finished"
            raise RuntimeError("progress failed")

        with pytest.raises(RuntimeError, match="progress failed"):
            staged.materialize(prepared, "data/export", progress=fail)
        staged.commit()

    assert open_export(output / "data/export").verify().states == 1


def test_delivery_progress_failure_before_commit_preserves_destination(
    tmp_path: Path,
) -> None:
    output = tmp_path / "site"
    _site(output, "old")

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")

        def fail_before_commit(event: ProgressEvent) -> None:
            if event.kind == "delivery_commit_started":
                raise RuntimeError("progress failed")

        with pytest.raises(RuntimeError, match="progress failed"):
            staged.commit(progress=fail_before_commit)

    assert output.joinpath("index.html").read_text(encoding="utf-8") == "old"


@pytest.mark.skipif(
    not (sys.platform == "darwin" or sys.platform.startswith("linux")),
    reason="native directory exchange is available on macOS and Linux",
)
def test_native_exchange_keeps_the_reader_path_available(tmp_path: Path) -> None:
    output = tmp_path / "site"
    staged = tmp_path / "staged"
    _site(output, "old")
    _site(staged, "new")
    if not exchange_directories(staged, output):
        pytest.skip("the test filesystem does not support atomic directory exchange")

    errors: list[OSError] = []
    observed = {output.joinpath("index.html").read_text(encoding="utf-8")}
    stop = Event()

    def read_site() -> None:
        while not stop.is_set():
            try:
                observed.add(output.joinpath("index.html").read_text(encoding="utf-8"))
            except OSError as error:
                errors.append(error)

    reader = Thread(target=read_site)
    reader.start()
    try:
        for _ in range(1_000):
            assert exchange_directories(staged, output)
    finally:
        stop.set()
        reader.join(timeout=5)

    assert not reader.is_alive()
    assert errors == []
    assert observed == {"old", "new"}


def test_delivery_reverifies_materialized_exports_before_commit(
    tmp_path: Path,
    prepared: PreparedExport,
) -> None:
    output = tmp_path / "site"

    with stage(output) as staged:
        staged.materialize(prepared, "data/export")
        staged.path.joinpath("data/export/index.json").write_text(
            "changed",
            encoding="utf-8",
        )
        with pytest.raises((IntegrityError, NotebookExportError)):
            staged.commit()

    assert not output.exists()


def test_delivery_rejects_changes_to_a_materialized_export_tree(
    tmp_path: Path,
    prepared: PreparedExport,
) -> None:
    output = tmp_path / "site"

    with stage(output) as staged:
        staged.materialize(prepared, "data/export")
        staged.path.joinpath("data/export/extra.txt").write_text(
            "extra",
            encoding="utf-8",
        )
        with pytest.raises(IntegrityError, match="changed"):
            staged.commit()

    assert not output.exists()


def test_delivery_rejects_overlapping_materialization_roots(
    tmp_path: Path,
    prepared: PreparedExport,
) -> None:
    with stage(tmp_path / "site") as staged:
        staged.materialize(prepared, "data/export")
        with pytest.raises(ValueError, match="overlaps"):
            staged.materialize(prepared, "data/export/nested")


@pytest.mark.skipif(os.name == "nt", reason="creating a symlink may require elevation")
def test_delivery_rejects_a_symlinked_materialization_parent(
    tmp_path: Path,
    prepared: PreparedExport,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    with stage(tmp_path / "site") as staged:
        staged.path.joinpath("data").symlink_to(outside, target_is_directory=True)
        with pytest.raises(NotebookExportError, match="real directory"):
            staged.materialize(prepared, "data/export")

    assert not outside.joinpath("export").exists()


def test_delivery_guard_preserves_the_previous_directory(
    tmp_path: Path,
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    output.joinpath("index.html").write_text("old", encoding="utf-8")

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")

        def cancel() -> None:
            raise KeyboardInterrupt("cancelled")

        with pytest.raises(KeyboardInterrupt, match="cancelled"):
            staged.commit(guard=cancel)

    assert output.joinpath("index.html").read_text(encoding="utf-8") == "old"


def test_delivery_preserves_a_destination_created_after_preflight(
    tmp_path: Path,
) -> None:
    output = tmp_path / "site"

    with stage(output) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        output.mkdir()
        output.joinpath("concurrent.txt").write_text("keep", encoding="utf-8")
        with pytest.raises(NotebookExportError) as raised:
            staged.commit()

    assert raised.value.code == "destination_changed"
    assert output.joinpath("concurrent.txt").read_text(encoding="utf-8") == "keep"


def test_delivery_restores_a_changed_replacement_target(
    tmp_path: Path,
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    output.joinpath("index.html").write_text("old", encoding="utf-8")

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        output.joinpath("concurrent.txt").write_text("keep", encoding="utf-8")
        with pytest.raises(NotebookExportError) as raised:
            staged.commit()

    assert raised.value.code == "destination_changed"
    assert output.joinpath("index.html").read_text(encoding="utf-8") == "old"
    assert output.joinpath("concurrent.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_delivery_rejects_an_empty_destination_root_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    output = tmp_path / "site"
    output.mkdir()

    def exchange(first: Path, second: Path) -> bool:
        if transaction == "fallback":
            return False
        temporary = tmp_path / "exchange"
        os.replace(first, temporary)
        os.replace(second, first)
        os.replace(temporary, second)
        return True

    monkeypatch.setattr(directory_module, "_exchange_directories", exchange)
    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        previous = tmp_path / "previous"
        os.replace(output, previous)
        output.mkdir()
        with pytest.raises(NotebookExportError) as raised:
            staged.commit()

    assert raised.value.code == "destination_changed"
    assert output.is_dir()
    assert tuple(output.iterdir()) == ()
    assert previous.is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory mode contract")
@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_delivery_rejects_destination_root_mode_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    output = tmp_path / "site"
    output.mkdir()

    def exchange(first: Path, second: Path) -> bool:
        if transaction == "fallback":
            return False
        temporary = tmp_path / "exchange"
        os.replace(first, temporary)
        os.replace(second, first)
        os.replace(temporary, second)
        return True

    monkeypatch.setattr(directory_module, "_exchange_directories", exchange)
    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        os.chmod(output, 0o700)
        with pytest.raises(NotebookExportError) as raised:
            staged.commit()

    assert raised.value.code == "destination_changed"
    assert stat.S_IMODE(output.stat().st_mode) == 0o700


@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_delivery_rejects_destination_root_revision_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    output = tmp_path / "site"
    output.mkdir()

    def exchange(first: Path, second: Path) -> bool:
        if transaction == "fallback":
            return False
        temporary = tmp_path / "exchange"
        os.replace(first, temporary)
        os.replace(second, first)
        os.replace(temporary, second)
        return True

    monkeypatch.setattr(directory_module, "_exchange_directories", exchange)
    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        before = output.stat()
        os.utime(
            output,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        with pytest.raises(NotebookExportError) as raised:
            staged.commit()

    assert raised.value.code == "destination_changed"


def test_delivery_fallback_replaces_a_complete_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    output.joinpath("index.html").write_text("old", encoding="utf-8")
    monkeypatch.setattr(
        directory_module,
        "_exchange_directories",
        lambda _first, _second: False,
    )

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        staged.commit()

    assert output.joinpath("index.html").read_text(encoding="utf-8") == "new"
    assert not tuple(tmp_path.glob(".site.recovery-*"))


def test_delivery_fallback_restores_after_a_publish_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    output.joinpath("index.html").write_text("old", encoding="utf-8")
    commit_absent = directory_module._commit_absent
    monkeypatch.setattr(
        directory_module,
        "_exchange_directories",
        lambda _first, _second: False,
    )

    def interrupt(staged: Path, target: Path) -> None:
        commit_absent(staged, target)
        raise KeyboardInterrupt("interrupted")

    monkeypatch.setattr(directory_module, "_commit_absent", interrupt)

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        with pytest.raises(KeyboardInterrupt, match="interrupted"):
            staged.commit()

    assert output.joinpath("index.html").read_text(encoding="utf-8") == "old"


def test_delivery_preserves_primary_when_interrupted_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "site"
    _site(output, "old")
    commit_absent = directory_module._commit_absent
    native_remove = directory_module._remove
    monkeypatch.setattr(
        directory_module,
        "_exchange_directories",
        lambda _first, _second: False,
    )

    def interrupt(staged: Path, target: Path) -> None:
        commit_absent(staged, target)
        raise KeyboardInterrupt("interrupted")

    def fail_cleanup(_path: Path) -> None:
        raise OSError("cleanup failed")

    monkeypatch.setattr(directory_module, "_commit_absent", interrupt)
    monkeypatch.setattr(directory_module, "_remove", fail_cleanup)
    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        with pytest.raises(KeyboardInterrupt, match="interrupted") as raised:
            staged.commit()

    assert cleanup_failures(raised.value) == ("interrupted directory cleanup also failed: OSError",)
    assert output.joinpath("index.html").read_text(encoding="utf-8") == "old"
    monkeypatch.setattr(directory_module, "_remove", native_remove)
    for interrupted in tmp_path.glob(".site.interrupted-*"):
        native_remove(interrupted)


@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_delivery_reports_replaced_directory_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    output = tmp_path / "site"
    _site(output, "old")

    def exchange(first: Path, second: Path) -> bool:
        if transaction == "fallback":
            return False
        temporary = tmp_path / "exchange"
        os.replace(first, temporary)
        os.replace(second, first)
        os.replace(temporary, second)
        return True

    native_remove = delivery_module.shutil.rmtree

    def fail_replaced(path: Path) -> None:
        if path.name.startswith((".site.staging-", ".site.recovery-")):
            raise OSError("cleanup failed")
        native_remove(path)

    monkeypatch.setattr(directory_module, "_exchange_directories", exchange)
    monkeypatch.setattr(delivery_module.shutil, "rmtree", fail_replaced)
    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        result = staged.commit()

    assert output.joinpath("index.html").read_text(encoding="utf-8") == "new"
    assert [warning.code for warning in result.warnings] == ["retired_destination_cleanup_failed"]
    assert tuple(tmp_path.glob(".site.staging-*")) or tuple(tmp_path.glob(".site.recovery-*"))


def test_delivery_reports_parent_sync_failure_after_visible_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "site"

    def fail_sync(_path: Path) -> None:
        raise OSError("sync failed")

    monkeypatch.setattr(delivery_module, "_sync_directory", fail_sync)
    with stage(output) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        result = staged.commit()

    assert output.joinpath("index.html").read_text(encoding="utf-8") == "new"
    assert [warning.code for warning in result.warnings] == ["export_parent_sync_failed"]


@pytest.mark.skipif(os.name != "nt", reason="Windows open-file cleanup contract")
def test_delivery_reports_locked_replaced_file_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "site"
    _site(output, "old")
    locked = output / "locked.txt"
    locked.write_text("locked", encoding="utf-8")
    native_commit = delivery_module.commit_directory
    handles = []

    def commit_with_locked_replaced(staged, target, *, retain_replaced=False):
        retired = native_commit(
            staged,
            target,
            retain_replaced=retain_replaced,
        )
        assert retired is not None
        handles.append(retired.joinpath("locked.txt").open("rb"))
        return retired

    monkeypatch.setattr(delivery_module, "commit_directory", commit_with_locked_replaced)
    try:
        with stage(output, replace=True) as staged:
            staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
            result = staged.commit()
        assert output.joinpath("index.html").read_text(encoding="utf-8") == "new"
        assert [warning.code for warning in result.warnings] == [
            "retired_destination_cleanup_failed"
        ]
    finally:
        for handle in handles:
            handle.close()
        for retired in tmp_path.glob(".site.recovery-*"):
            delivery_module.shutil.rmtree(retired)


@pytest.mark.parametrize("relative", ("", ".", "../export", "/export", "bad\\path"))
def test_delivery_rejects_nonportable_materialization_paths(
    tmp_path: Path,
    prepared: PreparedExport,
    relative: str,
) -> None:
    with (
        stage(tmp_path / "site") as staged,
        pytest.raises(ValueError, match="portable relative directory"),
    ):
        staged.materialize(prepared, relative)


def test_closed_delivery_rejects_further_writes(tmp_path: Path) -> None:
    staged: StagedDelivery = stage(tmp_path / "site")
    path = staged.path
    staged.close()

    with pytest.raises(RuntimeError, match="closed"):
        _ = staged.path
    assert not path.exists()
