from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from marimo_export._repository.preparation import preparation_repository
from marimo_export.repository import (
    ExportRepository,
    RepositoryLimitError,
    RepositoryLimits,
)
from marimo_export.wire import state_fingerprint
from repository_test_support import _export, _identity, _state

pytestmark = pytest.mark.serial


def _publish(repository: ExportRepository, name: str, value: int) -> None:
    identity = _identity(name)
    state = _state(repository, identity, value)
    _export(repository, identity, state, name).close()
    state.close()


def _metadata_bytes(root: Path, *tables: str) -> int:
    connection = sqlite3.connect(root / "catalog.sqlite3")
    try:
        return sum(
            int(connection.execute(f"SELECT SUM(metadata_bytes) FROM {table}").fetchone()[0])
            for table in tables or ("prepared_states", "generations")
        )
    finally:
        connection.close()


def _is_current(repository: ExportRepository, name: str) -> bool:
    current = preparation_repository(repository).current(_identity(name))
    if current is None:
        return False
    current.close()
    return True


def test_full_metadata_budget_evicts_the_least_recent_identity(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    with ExportRepository.open(root) as repository:
        _publish(repository, "first", 1)
        _publish(repository, "second", 2)
    limits = RepositoryLimits(metadata_bytes=_metadata_bytes(root) * 5 // 4)

    with ExportRepository.open(root, limits=limits) as repository:
        _publish(repository, "third", 3)
        assert not _is_current(repository, "first")
        assert _is_current(repository, "second")
        assert _is_current(repository, "third")


def test_leased_export_counts_once_against_the_budget(tmp_path: Path) -> None:
    probe = tmp_path / "probe"
    with ExportRepository.open(probe) as repository:
        for value, name in enumerate(("first", "second", "third"), 1):
            _publish(repository, name, value)
    root = tmp_path / "repository"
    with ExportRepository.open(root) as repository:
        _publish(repository, "first", 1)
        _publish(repository, "second", 2)
    limits = RepositoryLimits(metadata_bytes=_metadata_bytes(probe))

    with ExportRepository.open(root, limits=limits) as repository:
        reader = preparation_repository(repository).current(_identity("second"))
        assert reader is not None
        _publish(repository, "third", 3)
        reader.close()
        assert _is_current(repository, "first")


def test_leased_export_survives_a_full_budget(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    with ExportRepository.open(root) as repository:
        _publish(repository, "first", 1)
        _publish(repository, "second", 2)
    limits = RepositoryLimits(metadata_bytes=_metadata_bytes(root) * 5 // 4)

    with ExportRepository.open(root, limits=limits) as repository:
        reader = preparation_repository(repository).current(_identity("first"))
        assert reader is not None
        # Opening an export refreshes its access time. Read the other one
        # afterwards so the leased export is the least recently used.
        assert _is_current(repository, "second")
        _publish(repository, "third", 3)
        assert reader.asset("index.json") is not None
        reader.close()
        assert _is_current(repository, "first")
        assert not _is_current(repository, "second")


def test_recommitting_an_indexed_export_evicts_nothing(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    with ExportRepository.open(root) as repository:
        _publish(repository, "first", 1)
        _publish(repository, "second", 2)
    limits = RepositoryLimits(metadata_bytes=_metadata_bytes(root))

    with ExportRepository.open(root, limits=limits) as repository:
        _publish(repository, "second", 2)
        assert _is_current(repository, "first")


def test_replacement_at_a_full_budget_evicts_nothing(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("report")
    with ExportRepository.open(root) as repository:
        _publish(repository, "other", 1)
        state = _state(repository, identity, 2)
        export = _export(repository, identity, state, "first")
        replaced = export.instance
        export.close()
        state.close()
    limits = RepositoryLimits(
        metadata_bytes=_metadata_bytes(root) + _metadata_bytes(root, "prepared_states") // 2
    )

    with ExportRepository.open(root, limits=limits) as repository:
        state = _state(repository, identity, 3)
        export = _export(repository, identity, state, "second", replacing=replaced)
        assert export.instance != replaced
        export.close()
        state.close()
        assert _is_current(repository, "report")
        assert _is_current(repository, "other")


def test_identity_under_preparation_keeps_its_current_export(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("report")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, "first")
        replaced = export.instance
        export.close()
        state.close()
        _publish(repository, "other", 2)
    limits = RepositoryLimits(
        metadata_bytes=_metadata_bytes(root) + _metadata_bytes(root, "prepared_states") // 4
    )

    with ExportRepository.open(root, limits=limits) as repository:
        state = _state(repository, identity, 3)
        export = _export(repository, identity, state, "second", replacing=replaced)
        export.close()
        state.close()
        assert _is_current(repository, "report")
        assert not _is_current(repository, "other")


def test_oversized_state_metadata_fails_without_evicting_exports(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    with ExportRepository.open(root) as repository:
        _publish(repository, "first", 1)
        _publish(repository, "second", 2)
    limits = RepositoryLimits(metadata_bytes=4096)

    with ExportRepository.open(root, limits=limits) as repository:
        identity = _identity("oversized")
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_prepared_state(
                producer_sha256=identity.producer_sha256,
                output_plan_sha256=identity.output_plan_sha256,
                state_fingerprint=state_fingerprint({"value": 3}),
            ) as staged,
            pytest.raises(RepositoryLimitError, match="state metadata exceeds"),
        ):
            (staged.path / "value.txt").write_text("3", encoding="utf-8")
            staged.commit(metadata={"value": 3, "notes": "x" * 4096})
        assert _is_current(repository, "first")
        assert _is_current(repository, "second")
