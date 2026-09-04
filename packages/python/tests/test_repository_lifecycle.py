from __future__ import annotations

import sqlite3
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import marimo_export._repository.artifact_lifecycle as lifecycle_module
import marimo_export._repository.files as files_module
import marimo_export._repository.sqlite.open as open_module
import pytest
from marimo_export._repository.artifact_context import ArtifactContext
from marimo_export._repository.observations import observation_repository
from marimo_export._repository.preparation import (
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export.descriptors import Provenance, ScalarDescriptor
from marimo_export.errors import ExecutionError
from marimo_export.index import (
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    StateEntry,
)
from marimo_export.repository import (
    ExportRepository,
    RepositoryBusyError,
    RepositoryLimits,
)
from marimo_export.wire import state_fingerprint

pytestmark = pytest.mark.serial


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _identity(name: str) -> RepositoryIdentity:
    return RepositoryIdentity(
        producer_sha256=_digest(f"producer-{name}"),
        output_plan_sha256=_digest("outputs"),
        spec_sha256=_digest(f"spec-{name}"),
    )


def _state(repository: ExportRepository, identity: RepositoryIdentity, value: int):
    fingerprint = state_fingerprint({"value": value})
    preparation = preparation_repository(repository)
    with (
        preparation.reserve_preparation(identity),
        preparation.stage_prepared_state(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprint=fingerprint,
        ) as staged,
    ):
        (staged.path / "value.txt").write_text(str(value), encoding="utf-8")
        return staged.commit(metadata={"value": value})


def _export(repository: ExportRepository, identity: RepositoryIdentity, state, value: int):
    preparation = preparation_repository(repository)
    with (
        preparation.reserve_preparation(identity),
        preparation.stage_export(identity) as staged,
    ):
        _write_index(staged.path, identity, state.state_fingerprint, value)
        return staged.commit(states=(state,), captured_observation_revision=0)


def _write_index(
    path: Path,
    identity: RepositoryIdentity,
    fingerprint: str,
    value: int,
) -> None:
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
                inputs={"value": value},
                outputs={
                    "result": ScalarDescriptor(
                        value=value,
                        provenance=Provenance(python_type="builtins.int"),
                    )
                },
            )
        },
    )
    (path / "index.json").write_bytes(index.to_bytes())


def test_active_leases_protect_artifacts_then_lru_prunes_them(tmp_path: Path) -> None:
    limits = RepositoryLimits(
        retained_identities=1,
        retained_generations=1,
        retained_generations_per_identity=1,
        retained_prepared_states=1,
    )
    root = tmp_path / "repository"
    first_identity = _identity("first")
    second_identity = _identity("second")
    with ExportRepository.open(root, limits=limits) as repository:
        first_state = _state(repository, first_identity, 1)
        first_export = _export(repository, first_identity, first_state, 1)
        second_state = _state(repository, second_identity, 2)
        second_export = _export(repository, second_identity, second_state, 2)
        repository.prune()
        assert first_export.asset("index.json") is not None
        assert first_state.asset("value.txt") is not None

        first_export.close()
        first_state.close()
        second_export.close()
        second_state.close()
        repository.prune()
        preparation = preparation_repository(repository)
        assert preparation.current(first_identity) is None
        kept = preparation.current(second_identity)
        assert kept is not None
        kept.close()


def test_staging_acquisition_waits_within_operation_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    with ExportRepository.open(root) as repository:
        connection = sqlite3.connect(root / "catalog.sqlite3", isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        connected = threading.Event()
        finished = threading.Event()
        staged: list[Path] = []
        errors: list[BaseException] = []
        native_connect = repository._catalog._connect

        def observed_connect(*, timeout_seconds: float = 10) -> sqlite3.Connection:
            candidate = native_connect(timeout_seconds=timeout_seconds)
            connected.set()
            return candidate

        def create_staging() -> None:
            try:
                staged.append(repository._artifacts.new_staging(timeout_seconds=2))
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        monkeypatch.setattr(repository._catalog, "_connect", observed_connect)
        worker = threading.Thread(target=create_staging)
        worker.start()
        try:
            assert connected.wait(timeout=2)
            assert not finished.wait(timeout=0.5)
        finally:
            connection.rollback()
            connection.close()
        assert finished.wait(timeout=2)
        worker.join()
        assert not errors
        repository._artifacts.discard_staging(staged.pop())


def test_reservation_is_exclusive_across_repository_process_owners(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("reservation")
    with (
        ExportRepository.open(root) as first,
        ExportRepository.open(root) as second,
    ):
        with (
            preparation_repository(first).reserve_preparation(identity),
            pytest.raises(ExecutionError, match="cancelled") as raised,
            preparation_repository(second).reserve_preparation(
                identity,
                cancelled=lambda: True,
            ),
        ):
            raise AssertionError("reservation must remain exclusive")
        assert raised.value.code == "preparation_cancelled"
        with preparation_repository(second).reserve_preparation(identity):
            pass


def test_corrupt_generation_is_quarantined_and_removed(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("corrupt")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, 1)
        index = export.path / "index.json"
        export.close()
        state.close()
        files_module.make_tree_writable(index.parent)
        index.write_text("corrupt", encoding="utf-8")
        assert preparation_repository(repository).current(identity) is None
        assert repository.status().generations == 0


def test_corrupt_catalog_restarts_empty_and_removes_orphan_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("catalog")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, 1)
        artifact = export.path
        export.close()
        state.close()
    (root / "catalog.sqlite3").write_bytes(b"not sqlite")

    with ExportRepository.open(root) as recovered:
        assert recovered.status().generations == 0
        assert recovered.status().prepared_states == 0
        assert not artifact.exists()
        assert not tuple(root.glob(".catalog.sqlite3.corrupt-*"))


def test_failed_verification_discards_staging_without_catalog_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("failure")

    def reject(_context: ArtifactContext, _path: Path):
        raise ValueError("verification failed")

    with ExportRepository.open(tmp_path / "repository") as repository:
        monkeypatch.setattr(ArtifactContext, "verify_export", reject)
        state = _state(repository, identity, 1)
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_export(identity) as staged,
        ):
            staging = staged.path
            (staging / "index.json").write_text("bad", encoding="utf-8")
            with pytest.raises(ValueError, match="verification failed"):
                staged.commit(states=(state,), captured_observation_revision=0)
        assert not staging.exists()
        assert repository.status().generations == 0
        state.close()


def test_locked_catalog_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repository"
    with ExportRepository.open(root):
        pass
    catalog = root / "catalog.sqlite3"
    before = catalog.read_bytes()

    def locked(_path: Path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(open_module, "SqliteCatalog", locked)
    with pytest.raises(RepositoryBusyError, match="busy"):
        open_module.open_catalog(root)
    assert catalog.read_bytes() == before
    assert not tuple(root.glob(".catalog.sqlite3.corrupt-*"))


def test_busy_catalog_uses_repository_owned_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = _digest("busy")
    with ExportRepository.open(tmp_path / "repository") as repository:

        def locked(*, timeout_seconds: float = 10):
            del timeout_seconds
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(repository._catalog, "_connect", locked)
        with pytest.raises(RepositoryBusyError):
            observation_repository(repository).record(
                producer_sha256=producer,
                values={"value": 1},
            )


def test_staging_lease_releases_only_after_confirmed_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    identity = _identity("staging")
    owner = ExportRepository.open(root)
    preparation = preparation_repository(owner)
    reservation = preparation.reserve_preparation(identity)
    reservation.__enter__()
    staged = preparation.stage_prepared_state(
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        state_fingerprint=_digest("state"),
    )
    path = staged.path
    native_rmtree = files_module.shutil.rmtree

    def fail_staging(candidate, *args, **kwargs):
        if Path(candidate) == path:
            raise PermissionError("staging is open")
        return cast(Any, native_rmtree)(candidate, *args, **kwargs)

    monkeypatch.setattr(files_module.shutil, "rmtree", fail_staging)
    with pytest.raises(PermissionError, match="open"):
        staged.close()
    assert path.is_dir()
    with ExportRepository.open(root) as recovering:
        recovering._recover()
        assert path.is_dir()

    monkeypatch.setattr(files_module.shutil, "rmtree", native_rmtree)
    owner.close()
    with ExportRepository.open(root):
        assert not path.exists()


def test_close_is_bounded_when_catalog_release_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        lease_ttl_seconds=1.0,
        lease_heartbeat_seconds=0.1,
    )
    identity = _identity("blocked-close")
    repository = ExportRepository.open(root, limits=limits)
    reservation = preparation_repository(repository).reserve_preparation(identity)
    reservation.__enter__()
    staged = preparation_repository(repository).stage_prepared_state(
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        state_fingerprint=_digest("state"),
    )
    connection = sqlite3.connect(root / "catalog.sqlite3", isolation_level=None)
    connection.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        repository.close()
    finally:
        connection.rollback()
        connection.close()
    assert time.monotonic() - started < 2
    deadline = time.monotonic() + limits.lease_ttl_seconds + 2
    while staged.path.exists() and time.monotonic() < deadline:
        with ExportRepository.open(root, limits=limits):
            pass
        time.sleep(0.05)
    assert not staged.path.exists()


def test_failed_retired_artifact_removal_remains_accounted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        retained_identities=1,
        retained_generations=1,
        retained_generations_per_identity=1,
        retained_prepared_states=1,
    )
    first_identity = _identity("retired-first")
    second_identity = _identity("retired-second")
    with ExportRepository.open(root, limits=limits) as repository:
        first_state = _state(repository, first_identity, 1)
        first_export = _export(repository, first_identity, first_state, 1)
        retired_bytes = first_state.content_bytes + first_export.content_bytes
        first_state.close()
        first_export.close()

        second_state = _state(repository, second_identity, 2)
        native_rmtree = files_module.shutil.rmtree
        failed = False

        def fail_retired(candidate, *args, **kwargs):
            nonlocal failed
            if "-quarantine-" in Path(candidate).name and not failed:
                failed = True
                raise PermissionError("retired artifact is open")
            return cast(Any, native_rmtree)(candidate, *args, **kwargs)

        monkeypatch.setattr(files_module.shutil, "rmtree", fail_retired)
        second_export = _export(repository, second_identity, second_state, 2)
        second_export.close()
        with pytest.raises(PermissionError, match="retired artifact"):
            repository.prune()
        accounted = repository.status().content_bytes
        assert accounted >= retired_bytes
        assert tuple(root.rglob("*-quarantine-*"))

        monkeypatch.setattr(files_module.shutil, "rmtree", native_rmtree)
        repository._recover()
        assert repository.status().content_bytes <= accounted - retired_bytes
        assert not tuple(root.rglob("*-quarantine-*"))
        second_state.close()


def test_heartbeat_failure_surfaces_through_public_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("heartbeat-failure")
    limits = RepositoryLimits(
        lease_ttl_seconds=2.0,
        lease_heartbeat_seconds=0.1,
    )
    repository = ExportRepository.open(tmp_path / "repository", limits=limits)
    state = _state(repository, identity, 1)

    def fail_heartbeat(**_kwargs):
        raise RuntimeError("heartbeat storage failed")

    monkeypatch.setattr(repository._catalog, "renew_lifecycle", fail_heartbeat)
    repository._leases._wake.set()
    deadline = time.monotonic() + 2
    while repository._leases._failure is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert repository._leases._failure is not None
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        state.asset("value.txt")
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        repository.prune()
    state.close()
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        repository.close()
    assert time.monotonic() - started < 2


def test_repeated_busy_renewal_expires_artifact_handle_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("busy-renewal-expiry")
    limits = RepositoryLimits(
        lease_ttl_seconds=1.0,
        lease_heartbeat_seconds=0.1,
    )
    repository = ExportRepository.open(tmp_path / "repository", limits=limits)
    state = _state(repository, identity, 1)
    attempted = threading.Event()

    def busy_heartbeat(**_kwargs):
        attempted.set()
        raise RepositoryBusyError("catalog busy")

    monkeypatch.setattr(repository._catalog, "renew_lifecycle", busy_heartbeat)
    repository._leases._wake.set()
    assert attempted.wait(timeout=2)
    deadline = time.monotonic() + 2
    while state.alive and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not state.alive
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        state.asset("value.txt")
    state.close()
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        repository.close()


def test_delayed_success_after_deadline_does_not_revive_artifact_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("delayed-artifact-renewal")
    limits = RepositoryLimits(
        lease_ttl_seconds=2.0,
        lease_heartbeat_seconds=1.5,
    )
    repository = ExportRepository.open(tmp_path / "repository", limits=limits)
    state = _state(repository, identity, 1)
    native_renew = repository._catalog.renew_lifecycle
    entered = threading.Event()
    proceed = threading.Event()
    blocked = False

    def delayed_renewal(**kwargs):
        nonlocal blocked
        renewed = native_renew(**kwargs)
        if kwargs["artifacts"] and not blocked:
            blocked = True
            entered.set()
            assert proceed.wait(timeout=limits.lease_ttl_seconds + 2)
        return renewed

    monkeypatch.setattr(repository._catalog, "renew_lifecycle", delayed_renewal)
    assert entered.wait(timeout=limits.lease_ttl_seconds + 2)
    try:
        deadline = time.monotonic() + limits.lease_ttl_seconds + 2
        while state.alive and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        proceed.set()
    assert repository._leases._failure is not None
    assert not state.alive
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        state.asset("value.txt")
    state.close()
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        repository.close()


def test_stale_artifact_heartbeat_cannot_shorten_fresh_reacquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("artifact-renewal-monotonic")
    repository = ExportRepository.open(tmp_path / "repository")
    state = _state(repository, identity, 1)
    key = state._lease._key
    native_renew = repository._catalog.renew_lifecycle
    entered = threading.Event()
    proceed = threading.Event()
    requested: list[int] = []

    def delayed_renewal(**kwargs):
        if kwargs["artifacts"] and not requested:
            requested.append(kwargs["expires_at_us"])
            entered.set()
            assert proceed.wait(timeout=2)
        return native_renew(**kwargs)

    monkeypatch.setattr(repository._catalog, "renew_lifecycle", delayed_renewal)
    repository._leases._wake.set()
    assert entered.wait(timeout=2)
    time.sleep(0.05)
    reacquired = preparation_repository(repository).lookup_prepared_states(
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        state_fingerprints=(state.state_fingerprint,),
    )[state.state_fingerprint]
    connection = sqlite3.connect(repository.path / "catalog.sqlite3")
    try:
        fresh_expiry = int(
            connection.execute(
                """
                SELECT expires_at_us FROM artifact_leases
                WHERE owner = ? AND kind = ? AND artifact_key = ? AND instance = ?
                """,
                (repository._leases.owner, *key),
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert fresh_expiry > requested[0]
    fresh_deadline = repository._leases._confirmed_artifact_deadline[key]
    proceed.set()
    with repository._leases._condition:
        assert repository._leases._condition.wait_for(
            lambda: not repository._leases._maintaining,
            timeout=2,
        )
    connection = sqlite3.connect(repository.path / "catalog.sqlite3")
    try:
        final_expiry = int(
            connection.execute(
                """
                SELECT expires_at_us FROM artifact_leases
                WHERE owner = ? AND kind = ? AND artifact_key = ? AND instance = ?
                """,
                (repository._leases.owner, *key),
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert final_expiry == fresh_expiry
    assert repository._leases._confirmed_artifact_expires_at_us[key] == fresh_expiry
    assert repository._leases._confirmed_artifact_deadline[key] >= fresh_deadline
    reacquired.close()
    state.close()
    repository._leases.flush_releases()
    repository.close()


def test_delayed_success_after_deadline_does_not_revive_staging_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = RepositoryLimits(
        lease_ttl_seconds=2.0,
        lease_heartbeat_seconds=1.5,
    )
    repository = ExportRepository.open(tmp_path / "repository", limits=limits)
    staged = repository._artifacts.new_staging()
    relative = staged.relative_to(repository.path).as_posix()
    native_renew = repository._catalog.renew_lifecycle
    entered = threading.Event()
    proceed = threading.Event()
    blocked = False

    def delayed_renewal(**kwargs):
        nonlocal blocked
        renewed = native_renew(**kwargs)
        if kwargs["staging"] and not blocked:
            blocked = True
            entered.set()
            assert proceed.wait(timeout=limits.lease_ttl_seconds + 2)
        return renewed

    monkeypatch.setattr(repository._catalog, "renew_lifecycle", delayed_renewal)
    assert entered.wait(timeout=limits.lease_ttl_seconds + 2)
    try:
        deadline = time.monotonic() + limits.lease_ttl_seconds + 2
        while relative in repository._leases._staging and time.monotonic() < deadline:
            with repository._leases._condition:
                repository._leases._expire_unconfirmed_lifecycle()
            time.sleep(0.01)
    finally:
        proceed.set()
    assert relative not in repository._leases._staging
    assert relative not in repository._leases._confirmed_staging_deadline
    monkeypatch.setattr(repository._catalog, "renew_lifecycle", native_renew)
    repository._artifacts.discard_staging(staged)
    repository.close()


def test_release_during_heartbeat_renewal_removes_renewed_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("renewal-race")
    limits = RepositoryLimits(
        lease_ttl_seconds=2.0,
        lease_heartbeat_seconds=0.1,
    )
    with ExportRepository.open(tmp_path / "repository", limits=limits) as repository:
        state = _state(repository, identity, 1)
        native_renew = repository._catalog.renew_lifecycle
        entered = threading.Event()
        proceed = threading.Event()
        blocked = False

        def block_renewal(**kwargs):
            nonlocal blocked
            if kwargs["artifacts"] and not blocked:
                blocked = True
                entered.set()
                assert proceed.wait(timeout=2)
            return native_renew(**kwargs)

        monkeypatch.setattr(repository._catalog, "renew_lifecycle", block_renewal)
        repository._leases._wake.set()
        assert entered.wait(timeout=2)
        state.close()
        proceed.set()
        repository._leases.flush_releases()
        assert repository.status().active_leases == 0


def test_generation_membership_pins_prepared_state(tmp_path: Path) -> None:
    limits = RepositoryLimits(retained_prepared_states=1)
    first_identity = _identity("member")
    second_identity = _identity("unreferenced")
    with ExportRepository.open(
        tmp_path / "repository",
        limits=limits,
    ) as repository:
        member = _state(repository, first_identity, 1)
        generation = _export(repository, first_identity, member, 1)
        fingerprint = member.state_fingerprint
        member.close()
        unreferenced = _state(repository, second_identity, 2)
        repository.prune()
        reused = preparation_repository(repository).lookup_prepared_states(
            producer_sha256=first_identity.producer_sha256,
            output_plan_sha256=first_identity.output_plan_sha256,
            state_fingerprints=(fingerprint,),
        )
        assert fingerprint in reused
        reused[fingerprint].close()
        generation.close()
        unreferenced.close()


def test_prune_dry_run_reports_without_mutating_artifacts(tmp_path: Path) -> None:
    limits = RepositoryLimits(
        retained_identities=1,
        retained_generations=1,
        retained_prepared_states=1,
    )
    first_identity = _identity("dry-first")
    second_identity = _identity("dry-second")
    with ExportRepository.open(
        tmp_path / "repository",
        limits=limits,
    ) as repository:
        first_state = _state(repository, first_identity, 1)
        first_export = _export(repository, first_identity, first_state, 1)
        second_state = _state(repository, second_identity, 2)
        second_export = _export(repository, second_identity, second_state, 2)
        paths = (first_state.path, first_export.path, second_state.path, second_export.path)
        first_state.close()
        first_export.close()
        second_state.close()
        second_export.close()
        preview = repository.prune(dry_run=True)
        before = repository.status()
        assert preview.generations + preview.prepared_states > 0
        assert all(path.exists() for path in paths)
        repeated = repository.prune(dry_run=True)
        assert repeated == preview
        assert repository.status() == before

        applied = repository.prune()
        assert (applied.generations, applied.prepared_states, applied.bytes_released) == (
            preview.generations,
            preview.prepared_states,
            preview.bytes_released,
        )


def test_commit_returns_after_linearization_when_deferred_prune_would_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("post-commit")
    with ExportRepository.open(tmp_path / "repository") as repository:
        state = _state(repository, identity, 1)

        def fail_prune(*, dry_run: bool = False):
            del dry_run
            raise PermissionError("deferred prune failed")

        monkeypatch.setattr(repository._artifacts, "prune", fail_prune)
        export = _export(repository, identity, state, 1)
        assert export.asset("index.json") is not None
        current = preparation_repository(repository).current(identity)
        assert current is not None
        current.close()
        export.close()
        state.close()


def test_observation_only_producer_retention_runs_without_artifact_victims(
    tmp_path: Path,
) -> None:
    limits = RepositoryLimits(retained_producers=1)
    root = tmp_path / "repository"
    with ExportRepository.open(root, limits=limits) as repository:
        for index in range(3):
            observation_repository(repository).record(
                producer_sha256=_digest(f"producer-{index}"),
                values={"value": index},
            )
        assert repository.status().producers == 3
    with ExportRepository.open(root, limits=limits) as reopened:
        assert reopened.status().producers == 3
        reopened.prune(dry_run=True)
        assert reopened.status().producers == 3
        reopened.prune()
        assert reopened.status().producers == 1


def test_slow_prune_quarantine_does_not_starve_live_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        retained_identities=1,
        retained_generations=1,
        retained_prepared_states=1,
        lease_ttl_seconds=2.0,
        lease_heartbeat_seconds=0.05,
    )
    victim_identity = _identity("slow-prune-victim")
    live_identity = _identity("slow-prune-live")
    with ExportRepository.open(root, limits=limits) as seed:
        victim_state = _state(seed, victim_identity, 1)
        victim_export = _export(seed, victim_identity, victim_state, 1)
        victim_state.close()
        victim_export.close()

    owner = ExportRepository.open(root, limits=limits)
    live_state = _state(owner, live_identity, 2)
    live_export = _export(owner, live_identity, live_state, 2)
    pruning = ExportRepository.open(root, limits=limits)
    native_quarantine = lifecycle_module.quarantine
    native_renew = owner._catalog.renew_lifecycle
    quarantine_started = threading.Event()
    renewed = threading.Event()

    def observe_renewal(**kwargs):
        result = native_renew(**kwargs)
        if quarantine_started.is_set() and kwargs["artifacts"]:
            renewed.set()
        return result

    def slow_quarantine(path: Path):
        quarantine_started.set()
        assert renewed.wait(timeout=2)
        return native_quarantine(path)

    monkeypatch.setattr(owner._catalog, "renew_lifecycle", observe_renewal)
    monkeypatch.setattr(lifecycle_module, "quarantine", slow_quarantine)
    pruning.prune()
    assert live_export.asset("index.json") is not None
    live_export.close()
    live_state.close()
    pruning.close()
    owner.close()


def test_same_version_missing_fence_schema_is_reset(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    connection = sqlite3.connect(root / "catalog.sqlite3")
    try:
        connection.executescript(
            """
            CREATE TABLE repository_schema (
                version INTEGER PRIMARY KEY CHECK (version = 1)
            );
            INSERT INTO repository_schema(version) VALUES (1);
            CREATE TABLE preparation_reservations (
                identity_key TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                expires_at_us INTEGER NOT NULL
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
    with ExportRepository.open(root) as repository:
        assert repository.status().generations == 0
    assert not tuple(root.glob(".catalog.sqlite3.incompatible-*"))


def test_exact_v1_schema_reopens_without_reset(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    producer = _digest("healthy-schema")
    with ExportRepository.open(root) as repository:
        observation_repository(repository).record(
            producer_sha256=producer,
            values={"value": 1},
        )
    with ExportRepository.open(root) as reopened:
        assert observation_repository(reopened).revision(producer) == 1
    assert not tuple(root.glob(".catalog.sqlite3.incompatible-*"))


@pytest.mark.parametrize("drift", ["missing-primary-key", "wrong-foreign-key", "wrong-index"])
def test_same_column_schema_drift_is_reset(tmp_path: Path, drift: str) -> None:
    root = tmp_path / "repository"
    with ExportRepository.open(root):
        pass
    connection = sqlite3.connect(root / "catalog.sqlite3")
    try:
        if drift == "missing-primary-key":
            connection.executescript(
                """
                DROP INDEX artifact_leases_expiry;
                ALTER TABLE artifact_leases RENAME TO artifact_leases_old;
                CREATE TABLE artifact_leases (
                    owner TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('generation', 'state')),
                    artifact_key TEXT NOT NULL,
                    instance TEXT NOT NULL,
                    expires_at_us INTEGER NOT NULL
                );
                DROP TABLE artifact_leases_old;
                CREATE INDEX artifact_leases_expiry
                    ON artifact_leases(expires_at_us, kind, artifact_key, instance);
                """
            )
        elif drift == "wrong-foreign-key":
            connection.executescript(
                """
                ALTER TABLE state_scopes RENAME TO state_scopes_old;
                CREATE TABLE state_scopes (
                    state_key TEXT PRIMARY KEY,
                    producer_sha256 TEXT NOT NULL,
                    output_plan_sha256 TEXT NOT NULL,
                    state_fingerprint TEXT NOT NULL,
                    current_instance TEXT,
                    touched_at_us INTEGER NOT NULL,
                    UNIQUE(producer_sha256, output_plan_sha256, state_fingerprint),
                    FOREIGN KEY(producer_sha256) REFERENCES producers(producer_sha256)
                        ON DELETE RESTRICT
                );
                DROP TABLE state_scopes_old;
                """
            )
        else:
            connection.executescript(
                """
                DROP INDEX artifact_leases_expiry;
                CREATE INDEX artifact_leases_expiry
                    ON artifact_leases(kind, expires_at_us, artifact_key, instance);
                """
            )
        connection.commit()
    finally:
        connection.close()

    with ExportRepository.open(root) as recovered:
        assert recovered.status().generations == 0
    connection = sqlite3.connect(root / "catalog.sqlite3")
    try:
        primary_key = tuple(
            int(row[5]) for row in connection.execute('PRAGMA table_info("artifact_leases")')
        )
        state_foreign_key = connection.execute('PRAGMA foreign_key_list("state_scopes")').fetchone()
        lease_index = tuple(
            str(row[2]) for row in connection.execute('PRAGMA index_info("artifact_leases_expiry")')
        )
    finally:
        connection.close()
    assert primary_key == (1, 2, 3, 4, 0)
    assert state_foreign_key is not None and state_foreign_key[6] == "CASCADE"
    assert lease_index == ("expires_at_us", "kind", "artifact_key", "instance")


@pytest.mark.parametrize("mode", ["corrupt", "wrong-schema"])
def test_maintenance_lock_is_recreated_without_repository_loss(
    tmp_path: Path,
    mode: str,
) -> None:
    root = tmp_path / "repository"
    producer = _digest(f"maintenance-{mode}")
    with ExportRepository.open(root) as repository:
        observation_repository(repository).record(
            producer_sha256=producer,
            values={"value": 1},
        )
    maintenance = root / "maintenance.sqlite3"
    if mode == "corrupt":
        maintenance.write_bytes(b"not sqlite")
    else:
        maintenance.unlink()
        Path(f"{maintenance}-wal").unlink(missing_ok=True)
        Path(f"{maintenance}-shm").unlink(missing_ok=True)
        connection = sqlite3.connect(maintenance)
        try:
            connection.execute("CREATE TABLE maintenance_lock(wrong INTEGER)")
            connection.commit()
        finally:
            connection.close()
    with ExportRepository.open(root) as reopened:
        assert observation_repository(reopened).revision(producer) == 1
        reopened.prune(dry_run=True)


def test_failed_corrupt_catalog_snapshot_cleanup_stays_accounted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    identity = _identity("catalog-cleanup")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, 1)
        export.close()
        state.close()
    (root / "catalog.sqlite3").write_bytes(b"not sqlite")
    native_rmtree = files_module.shutil.rmtree

    def fail_snapshot(candidate, *args, **kwargs):
        if "-unindexed-" in Path(candidate).name:
            raise PermissionError("snapshot is open")
        return cast(Any, native_rmtree)(candidate, *args, **kwargs)

    monkeypatch.setattr(files_module.shutil, "rmtree", fail_snapshot)
    with pytest.raises(PermissionError, match="snapshot is open"):
        ExportRepository.open(root)
    connection = sqlite3.connect(root / "catalog.sqlite3")
    try:
        accounted = connection.execute(
            "SELECT COALESCE(SUM(content_bytes), 0) FROM retired_artifacts"
        ).fetchone()
    finally:
        connection.close()
    assert accounted is not None and int(accounted[0]) > 0

    monkeypatch.setattr(files_module.shutil, "rmtree", native_rmtree)
    with ExportRepository.open(root) as recovered:
        assert recovered.status().content_bytes == 0
