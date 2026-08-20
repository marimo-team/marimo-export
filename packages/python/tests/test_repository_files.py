from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import marimo_export._repository.files as files_module
import pytest
from marimo_export._diagnostics import cleanup_failures
from marimo_export._repository.files import atomic_install
from marimo_export._repository.paths import export_path
from marimo_export._repository.preparation import (
    preparation_repository,
)
from marimo_export.repository import (
    ExportRepository,
    RepositoryLimits,
)
from marimo_export.wire import state_fingerprint
from repository_test_support import (
    _export,
    _identity,
    _state,
    _write_index,
)


def test_malformed_retired_row_is_repaired_and_cleaned(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    with ExportRepository.open(root) as repository:
        retired = root / ".exports-unindexed-00000000000000000000000000000000"
        retired.mkdir()
        (retired / "payload.bin").write_bytes(b"payload")
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                """
                INSERT INTO retired_artifacts(relative_path, content_bytes, created_at_us)
                VALUES (?, ?, ?)
                """,
                (retired.name, "wrong affinity", 1),
            )
            connection.commit()
        finally:
            connection.close()
        repository._recover()
        assert not retired.exists()
        assert repository.status().content_bytes == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_committed_artifact_files_are_read_only_and_prunable(tmp_path: Path) -> None:
    identity = _identity("permissions")
    limits = RepositoryLimits(retained_identities=1, retained_generations=1)
    root = tmp_path / "repository"
    with ExportRepository.open(root, limits=limits) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, "protected")
        for tree in (state.path, export.path):
            assert tree.stat().st_mode & stat.S_IWUSR != 0
            for path in tree.rglob("*"):
                if path.is_dir():
                    assert path.stat().st_mode & stat.S_IWUSR != 0
                else:
                    assert path.stat().st_mode & stat.S_IWUSR == 0
        state.close()
        export.close()
        newer_identity = _identity("permissions-newer")
        newer_state = _state(repository, newer_identity, 2)
        newer_export = _export(repository, newer_identity, newer_state, "newer")
        repository.prune()
        assert not export.path.exists()
        newer_export.close()
        newer_state.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_closed_repository_supports_standard_recursive_cleanup(tmp_path: Path) -> None:
    identity = _identity("external-cleanup")
    root = tmp_path / "repository"
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        exported = _export(repository, identity, state, "protected")
        exported.close()
        state.close()

    shutil.rmtree(root)

    assert not root.exists()


def test_atomic_install_restores_staging_after_post_rename_verifier_failure(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    staging.mkdir()
    (staging / "index.json").write_bytes(b"payload")

    def fail(_path: Path):
        raise PermissionError("verification unavailable")

    with pytest.raises(PermissionError, match="unavailable"):
        atomic_install(staging, target, "0" * 64, fail)
    assert staging.is_dir()
    assert (staging / "index.json").read_bytes() == b"payload"
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows file flush contract")
def test_sync_tree_flushes_regular_files_on_windows(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"durable")

    files_module.sync_tree(root)

    assert payload.read_bytes() == b"durable"


def test_atomic_install_preserves_primary_and_records_rollback_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    staging.mkdir()
    (staging / "index.json").write_bytes(b"payload")
    native_replace = files_module.os.replace

    def replace(source: Path, destination: Path) -> None:
        if Path(source) == target and Path(destination) == staging:
            raise PermissionError("rollback unavailable")
        native_replace(source, destination)

    def reject(_path: Path):
        raise RuntimeError("verification failed")

    monkeypatch.setattr(files_module.os, "replace", replace)
    with pytest.raises(RuntimeError, match="verification failed") as raised:
        atomic_install(staging, target, "0" * 64, reject)
    assert cleanup_failures(raised.value) == (
        "artifact install rollback also failed: PermissionError",
    )
    files_module.make_tree_writable(target)
    shutil.rmtree(target)


def test_state_commit_preserves_primary_and_records_staging_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("combined-cleanup")
    repository = ExportRepository.open(tmp_path / "repository")
    preparation = preparation_repository(repository)
    native_discard = repository._artifacts.discard_staging
    with preparation.reserve_preparation(identity):
        staged = preparation.stage_prepared_state(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprint=state_fingerprint({"value": 1}),
        )

        def reject(*_args: object, **_kwargs: object):
            raise ValueError("commit failed")

        def reject_cleanup(_path: Path) -> None:
            raise PermissionError("cleanup unavailable")

        monkeypatch.setattr(repository._artifacts, "commit_prepared_state", reject)
        monkeypatch.setattr(repository._artifacts, "discard_staging", reject_cleanup)
        with pytest.raises(ValueError, match="commit failed") as raised:
            staged.commit(metadata={"value": 1})
        assert cleanup_failures(raised.value) == (
            "prepared state staging cleanup also failed: PermissionError",
        )
        monkeypatch.setattr(repository._artifacts, "discard_staging", native_discard)
        native_discard(staged.path)
    repository.close()


def test_recovery_removes_export_installed_before_process_crash(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("install-crash")
    staging = tmp_path / "crashed-staging"
    staging.mkdir()
    fingerprint = state_fingerprint({"value": 1})
    _write_index(staging, identity, fingerprint, 1, "orphan")
    instance = sha256((staging / "index.json").read_bytes()).hexdigest()
    target = export_path(root, identity, instance)
    with ExportRepository.open(root):
        pass
    code = """
import os
import sys
from pathlib import Path
from marimo_export._repository.files import atomic_install, verify_export

atomic_install(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], verify_export)
os._exit(0)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(staging), str(target), instance],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, (process.stdout, process.stderr)
    assert target.is_dir()

    with ExportRepository.open(root) as recovered:
        assert recovered.status().generations == 0
        assert recovered.status().content_bytes == 0
        assert not target.exists()


def test_committed_export_survives_staging_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("cleanup-linearization")
    with ExportRepository.open(tmp_path / "repository") as repository:
        state = _state(repository, identity, 1)

        def fail_cleanup(_path: Path) -> None:
            raise PermissionError("lease cleanup unavailable")

        monkeypatch.setattr(repository._artifacts, "discard_staging", fail_cleanup)
        export = _export(repository, identity, state, "committed")
        assert export.asset("index.json") is not None
        current = preparation_repository(repository).current(identity)
        assert current is not None
        assert current.instance == export.instance
        current.close()
        export.close()
        state.close()


def test_invalid_retired_path_is_dropped_without_following_it(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    outside = tmp_path / "outside"
    outside.write_text("keep", encoding="utf-8")
    with ExportRepository.open(root) as repository:
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute(
                """
                INSERT INTO retired_artifacts(relative_path, content_bytes, created_at_us)
                VALUES (?, ?, ?)
                """,
                ("../outside", outside.stat().st_size, 1),
            )
            connection.commit()
        finally:
            connection.close()
        repository._recover()
        assert outside.read_text(encoding="utf-8") == "keep"
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            assert connection.execute("SELECT COUNT(*) FROM retired_artifacts").fetchone() == (0,)
        finally:
            connection.close()
