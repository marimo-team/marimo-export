from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, cast

import marimo_export._repository.files as files_module
import marimo_export.reader as reader_module
import pytest
from marimo_export._repository.artifact_context import ArtifactContext
from marimo_export._repository.models import (
    MAX_SQLITE_INTEGER,
)
from marimo_export._repository.observations import observation_repository
from marimo_export._repository.preparation import (
    preparation_repository,
)
from marimo_export.descriptors import Provenance, ScalarDescriptor
from marimo_export.errors import ExportUnavailableError
from marimo_export.index import (
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    StateEntry,
)
from marimo_export.repository import (
    ExportRepository,
    RepositoryError,
    RepositoryLimits,
)
from marimo_export.wire import state_fingerprint
from repository_test_support import (
    _digest,
    _export,
    _identity,
    _state,
    _write_index,
)


def test_corrupt_observation_fails_closed_and_is_removed(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    producer = _digest("observation")
    with ExportRepository.open(root) as repository:
        observation_repository(repository).record(
            producer_sha256=producer,
            values={"value": 1},
        )
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute(
                "UPDATE observations SET values_json = ? WHERE producer_sha256 = ?",
                (b'{"value":2}', producer),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(RepositoryError, match="corrupt"):
            preparation_repository(repository).observations(
                producer_sha256=producer,
                inputs=("value",),
            )
        assert (
            preparation_repository(repository).observations(
                producer_sha256=producer,
                inputs=("value",),
            )
            == ()
        )


@pytest.mark.parametrize("reader", ["latest", "snapshot"])
def test_corrupt_observation_event_fails_closed(
    tmp_path: Path,
    reader: str,
) -> None:
    root = tmp_path / reader
    producer = _digest(f"event-{reader}")
    with ExportRepository.open(root) as repository:
        observation_repository(repository).record(
            producer_sha256=producer,
            values={"value": 1},
        )
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute(
                "UPDATE observation_events SET values_json = ? WHERE producer_sha256 = ?",
                (b'{"value":2}', producer),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(RepositoryError, match="corrupt"):
            if reader == "latest":
                preparation_repository(repository).latest_observation(
                    producer_sha256=producer,
                    inputs=("value",),
                )
            else:
                preparation_repository(repository).observation_snapshot(producer)
        assert (
            preparation_repository(repository).latest_observation(
                producer_sha256=producer,
                inputs=("value",),
            )
            is None
        )


def test_observation_revision_overflow_is_failure_atomic(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    producer = _digest("overflow")
    with ExportRepository.open(root) as repository:
        observation_repository(repository).record(
            producer_sha256=producer,
            values={"value": 1},
        )
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute(
                "UPDATE producers SET observation_revision = ? WHERE producer_sha256 = ?",
                (MAX_SQLITE_INTEGER, producer),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(ValueError, match="integer range"):
            observation_repository(repository).record(
                producer_sha256=producer,
                values={"value": 2},
            )
        assert observation_repository(repository).revision(producer) == MAX_SQLITE_INTEGER
        assert (
            len(
                preparation_repository(repository).observations(
                    producer_sha256=producer,
                    inputs=("value",),
                )
            )
            == 1
        )


def test_corrupt_generation_metadata_is_removed_with_files(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("metadata")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, "metadata")
        path = export.path
        export.close()
        state.close()
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute(
                """
                UPDATE generations SET metadata_json = ?, metadata_bytes = ?
                WHERE identity_key = ?
                """,
                (b"{}", 2, identity.key),
            )
            connection.commit()
        finally:
            connection.close()
        assert preparation_repository(repository).current(identity) is None
        assert repository.status().generations == 0
        assert not path.exists()


def test_failed_corrupt_artifact_removal_remains_accounted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    identity = _identity("corrupt-retired")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, "valid")
        retired_bytes = export.content_bytes
        index = export.path / "index.json"
        export.close()
        state.close()
        files_module.make_tree_writable(index.parent)
        index.write_text("corrupt", encoding="utf-8")
        native_rmtree = files_module.shutil.rmtree

        def fail_retired(candidate, *args, **kwargs):
            if "-quarantine-" in Path(candidate).name:
                raise PermissionError("corrupt artifact is open")
            return cast(Any, native_rmtree)(candidate, *args, **kwargs)

        monkeypatch.setattr(files_module.shutil, "rmtree", fail_retired)
        with pytest.raises(PermissionError, match="corrupt artifact"):
            preparation_repository(repository).current(identity)
        accounted = repository.status().content_bytes
        assert accounted >= retired_bytes
        assert tuple(root.rglob("*-quarantine-*"))

        monkeypatch.setattr(files_module.shutil, "rmtree", native_rmtree)
        repository._recover()
        assert repository.status().content_bytes <= accounted - retired_bytes
        assert not tuple(root.rglob("*-quarantine-*"))


def test_restart_discards_corrupt_producer_revision_and_dependents(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("corrupt-producer")
    with ExportRepository.open(root) as repository:
        observation_repository(repository).record(
            producer_sha256=identity.producer_sha256,
            values={"value": 1},
        )
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, "producer")
        export_path = export.path
        export.close()
        state.close()
    connection = sqlite3.connect(root / "catalog.sqlite3")
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE producers SET observation_revision = ? WHERE producer_sha256 = ?",
            ("bad", identity.producer_sha256),
        )
        connection.commit()
    finally:
        connection.close()

    with ExportRepository.open(root) as recovered:
        assert observation_repository(recovered).revision(identity.producer_sha256) == 0
        assert recovered.status().generations == 0
        assert recovered.status().prepared_states == 0
        assert not export_path.exists()


def test_symlinked_prepared_state_member_is_rejected(tmp_path: Path) -> None:
    identity = _identity("symlink")
    with ExportRepository.open(tmp_path / "repository") as repository:
        preparation = preparation_repository(repository)
        with preparation.reserve_preparation(identity):
            staged = preparation.stage_prepared_state(
                producer_sha256=identity.producer_sha256,
                output_plan_sha256=identity.output_plan_sha256,
                state_fingerprint=_digest("state"),
            )
            with staged:
                target = staged.path / "target.txt"
                target.write_text("data", encoding="utf-8")
                link = staged.path / "link.txt"
                try:
                    link.symlink_to(target)
                except OSError:
                    pytest.skip("symbolic links are unavailable")
                with pytest.raises(RepositoryError, match="symlink"):
                    staged.commit(metadata={})


def test_default_verifier_requires_exact_spec_and_state_membership(tmp_path: Path) -> None:
    identity = _identity("verified")
    inputs = {"value": 1}
    fingerprint = state_fingerprint(inputs)
    index = ExportIndex(
        spec_sha256=identity.spec_sha256,
        default_state=fingerprint,
        notebook=NotebookProvenance(
            filename="notebook.py",
            document_sha256=_digest("document"),
        ),
        producer=ProducerProvenance(
            marimo="0.24.0",
            marimo_export="0.0.0",
            implementation_sha256=_digest("implementation"),
        ),
        inputs=("value",),
        control_bindings={},
        outputs=("result",),
        aliases={"baseline": fingerprint},
        states={
            fingerprint: StateEntry(
                inputs=inputs,
                outputs={
                    "result": ScalarDescriptor(
                        value=1,
                        provenance=Provenance(python_type="builtins.int"),
                    )
                },
            )
        },
    )
    with ExportRepository.open(tmp_path / "repository") as repository:
        state = _state(repository, identity, 1)
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_export(identity) as staged,
        ):
            (staged.path / "index.json").write_bytes(index.to_bytes())
            export = staged.commit(states=(state,), captured_observation_revision=0)
        assert export.state_fingerprints == (fingerprint,)
        export.close()
        state.close()


def test_slow_recovery_does_not_starve_live_generation_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        lease_ttl_seconds=2.0,
        lease_heartbeat_seconds=0.05,
    )
    identity = _identity("slow-recovery")
    owner = ExportRepository.open(root, limits=limits)
    state = _state(owner, identity, 1)
    export = _export(owner, identity, state, "live")
    recovering = ExportRepository.open(root, limits=limits)
    native_verify = ArtifactContext.verify_export
    native_renew = owner._catalog.renew_lifecycle
    verification_started = threading.Event()
    renewed = threading.Event()

    def observe_renewal(**kwargs):
        result = native_renew(**kwargs)
        if verification_started.is_set() and kwargs["artifacts"]:
            renewed.set()
        return result

    def slow_verify(context: ArtifactContext, path: Path):
        verification_started.set()
        assert renewed.wait(timeout=2)
        return native_verify(context, path)

    monkeypatch.setattr(owner._catalog, "renew_lifecycle", observe_renewal)
    monkeypatch.setattr(ArtifactContext, "verify_export", slow_verify)
    recovering._recover()
    assert export.asset("index.json") is not None
    assert owner.status().active_leases >= 1
    export.close()
    state.close()
    recovering.close()
    owner.close()


@pytest.mark.parametrize("failure", [PermissionError("denied"), TimeoutError("timed out")])
def test_transient_export_read_failure_preserves_catalog_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
) -> None:
    root = tmp_path / type(failure).__name__
    identity = _identity(type(failure).__name__)
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, "stable")
        path = export.path
        export.close()
        state.close()

        def fail_read(_root: Path, *, max_bytes: int):
            del max_bytes
            raise failure

        monkeypatch.setattr(reader_module, "read_export_index", fail_read)
        with pytest.raises(ExportUnavailableError):
            preparation_repository(repository).current(identity)
        assert repository.status().generations == 1
        assert path.is_dir()


@pytest.mark.parametrize(
    ("operation", "failure"),
    [
        ("lstat", PermissionError("denied")),
        ("resolve", TimeoutError("timed out")),
        ("lstat", MemoryError("exhausted")),
        ("resolve", OSError(5, "storage unavailable")),
    ],
)
def test_transient_export_root_failure_preserves_catalog_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure: BaseException,
) -> None:
    root = tmp_path / f"root-{operation}-{type(failure).__name__}"
    identity = _identity(f"root-{operation}-{type(failure).__name__}")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, "stable")
        path = export.path
        export.close()
        state.close()
        native = getattr(Path, operation)
        matching_calls = 0

        def fail(candidate: Path, *args: object, **kwargs: object):
            nonlocal matching_calls
            if candidate == path:
                matching_calls += 1
                if operation != "lstat" or matching_calls > 1:
                    raise failure
            return native(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, operation, fail)
        with pytest.raises(ExportUnavailableError):
            preparation_repository(repository).current(identity)
        assert repository.status().generations == 1
        assert path.is_dir()


@pytest.mark.parametrize("operation", ["lstat", "iterdir"])
def test_transient_asset_directory_failure_preserves_catalog_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    root = tmp_path / f"assets-{operation}"
    identity = _identity(f"assets-{operation}")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_export(identity) as staged,
        ):
            _write_index(staged.path, identity, state.state_fingerprint, 1, "stable")
            (staged.path / "assets").mkdir()
            export = staged.commit(states=(state,), captured_observation_revision=0)
        path = export.path
        directory = path / "assets"
        export.close()
        state.close()
        native = getattr(Path, operation)

        def fail(candidate: Path, *args: object, **kwargs: object):
            if candidate == directory:
                raise PermissionError("assets unavailable")
            return native(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, operation, fail)
        with pytest.raises(ExportUnavailableError):
            preparation.current(identity)
        assert repository.status().generations == 1
        assert path.is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_unreadable_nested_prepared_state_preserves_catalog_and_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state-permission"
    identity = _identity("state-permission")
    fingerprint = state_fingerprint({"value": 1})
    with ExportRepository.open(root) as repository:
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_prepared_state(
                producer_sha256=identity.producer_sha256,
                output_plan_sha256=identity.output_plan_sha256,
                state_fingerprint=fingerprint,
            ) as staged,
        ):
            nested = staged.path / "nested"
            nested.mkdir()
            (nested / "value.txt").write_text("stable", encoding="utf-8")
            state = staged.commit(metadata={"value": 1})
        path = state.path
        nested = path / "nested"
        state.close()
        os.chmod(nested, 0)
        try:
            with pytest.raises(ExportUnavailableError):
                preparation.lookup_prepared_states(
                    producer_sha256=identity.producer_sha256,
                    output_plan_sha256=identity.output_plan_sha256,
                    state_fingerprints=(fingerprint,),
                )
            assert repository.status().prepared_states == 1
            assert path.is_dir()
        finally:
            os.chmod(nested, 0o700)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_unreadable_nested_export_tree_preserves_catalog_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "export-permission"
    identity = _identity("export-permission")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_export(identity) as staged,
        ):
            _write_index(staged.path, identity, state.state_fingerprint, 1, "stable")
            (staged.path / "assets").mkdir()
            export = staged.commit(states=(state,), captured_observation_revision=0)
        path = export.path
        nested = path / "assets"
        export.close()
        state.close()
        monkeypatch.setattr(reader_module, "_verify_asset_directory", lambda *_args: None)
        os.chmod(nested, 0)
        try:
            with pytest.raises(ExportUnavailableError):
                preparation.current(identity)
            assert repository.status().generations == 1
            assert path.is_dir()
        finally:
            os.chmod(nested, 0o700)
