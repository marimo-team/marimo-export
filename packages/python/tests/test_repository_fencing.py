from __future__ import annotations

import os
import sqlite3
import threading
import time
from hashlib import sha256
from pathlib import Path

import marimo_export._repository.files as files_module
import pytest
from marimo_export._json import canonical_bytes, decode_json_object
from marimo_export._repository.models import (
    RepositoryFenceError,
    RepositoryReservationTimeoutError,
)
from marimo_export._repository.observations import observation_repository
from marimo_export._repository.preparation import (
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export.repository import (
    ExportRepository,
    RepositoryBusyError,
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


def test_stale_reservation_fence_cannot_replace_new_owner_state(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("fence")
    first = ExportRepository.open(root)
    second = ExportRepository.open(root)
    first_preparation = preparation_repository(first)
    second_preparation = preparation_repository(second)
    first_context = first_preparation.reserve_preparation(identity)
    first_reservation = first_context.__enter__()
    first_staged = first_preparation.stage_prepared_state(
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        state_fingerprint=state_fingerprint({"value": 1}),
    )
    (first_staged.path / "value.txt").write_text("first", encoding="utf-8")
    with first._leases._condition:
        first._leases._condition.wait_for(
            lambda: not first._leases._maintaining,
            timeout=2,
        )
        first._leases._reservations.pop(identity.key, None)
    connection = sqlite3.connect(root / "catalog.sqlite3")
    try:
        connection.execute(
            "UPDATE preparation_reservations SET expires_at_us = 0 WHERE identity_key = ?",
            (identity.key,),
        )
        connection.commit()
    finally:
        connection.close()

    with second_preparation.reserve_preparation(identity) as second_reservation:
        assert second_reservation.fence > first_reservation.fence
        with second_preparation.stage_prepared_state(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprint=state_fingerprint({"value": 1}),
        ) as staged:
            (staged.path / "value.txt").write_text("second", encoding="utf-8")
            winner = staged.commit(metadata={"value": 1})
    with pytest.raises(RepositoryFenceError, match="stale"):
        first_staged.commit(metadata={"value": 1})
    reused = second_preparation.lookup_prepared_states(
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        state_fingerprints=(state_fingerprint({"value": 1}),),
    )
    assert reused[state_fingerprint({"value": 1})].instance == winner.instance
    for handle in reused.values():
        handle.close()
    winner.close()
    first_context.__exit__(None, None, None)
    first.close()
    second.close()


def test_reservation_binds_staged_state_to_producer_and_output_plan(
    tmp_path: Path,
) -> None:
    identity = _identity("binding")
    with ExportRepository.open(tmp_path / "repository") as repository:
        preparation = preparation_repository(repository)
        with preparation.reserve_preparation(identity):
            with pytest.raises(RuntimeError, match="another producer or output plan"):
                preparation.stage_prepared_state(
                    producer_sha256=_digest("wrong-producer"),
                    output_plan_sha256=identity.output_plan_sha256,
                    state_fingerprint=_digest("state"),
                )
            staged = preparation.stage_prepared_state(
                producer_sha256=identity.producer_sha256,
                output_plan_sha256=identity.output_plan_sha256,
                state_fingerprint=_digest("state"),
            )
            staged._producer_sha256 = _digest("tampered-producer")
            (staged.path / "value.txt").write_text("tampered", encoding="utf-8")
            with pytest.raises(RepositoryFenceError, match="stale"):
                staged.commit(metadata={"value": 1})
            with pytest.raises(RuntimeError, match="another producer or output plan"):
                preparation.stage_prepared_state(
                    producer_sha256=identity.producer_sha256,
                    output_plan_sha256=_digest("wrong-output-plan"),
                    state_fingerprint=_digest("state"),
                )


def test_reservation_timeout_is_bounded_and_typed(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("timeout")
    with (
        ExportRepository.open(root) as first,
        ExportRepository.open(root) as second,
        preparation_repository(first).reserve_preparation(identity),
    ):
        started = time.monotonic()
        with (
            pytest.raises(RepositoryReservationTimeoutError),
            preparation_repository(second).reserve_preparation(
                identity,
                timeout=0.1,
                poll_seconds=0.01,
            ),
        ):
            raise AssertionError("reservation must not be acquired")
        assert time.monotonic() - started < 1


def test_catalog_lock_respects_reservation_acquisition_timeout(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("catalog-timeout")
    with ExportRepository.open(root) as repository:
        connection = sqlite3.connect(root / "catalog.sqlite3", timeout=0)
        try:
            connection.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            with (
                pytest.raises(RepositoryReservationTimeoutError),
                preparation_repository(repository).reserve_preparation(
                    identity,
                    timeout=0.1,
                    poll_seconds=0.01,
                ),
            ):
                raise AssertionError("reservation must not be acquired")
            assert time.monotonic() - started < 1
        finally:
            connection.rollback()
            connection.close()


def test_acquisition_timeout_does_not_expire_owned_reservation(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("long-preparation")
    fingerprint = _digest("long-preparation-state")
    with ExportRepository.open(root) as repository:
        preparation = preparation_repository(repository)
        with preparation.reserve_preparation(identity, timeout=0.05) as reservation:
            time.sleep(0.1)
            assert reservation.alive
            with preparation.stage_prepared_state(
                producer_sha256=identity.producer_sha256,
                output_plan_sha256=identity.output_plan_sha256,
                state_fingerprint=fingerprint,
            ) as staged:
                (staged.path / "value.txt").write_text("ready", encoding="utf-8")
                state = staged.commit(metadata={"value": 1})
        assert state.state_fingerprint == fingerprint
        state.close()


def test_lost_reservation_renewal_poison_stops_next_stage(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        lease_ttl_seconds=2.0,
        lease_heartbeat_seconds=0.1,
    )
    identity = _identity("lost-renewal")
    with ExportRepository.open(root, limits=limits) as repository:
        preparation = preparation_repository(repository)
        with preparation.reserve_preparation(identity) as reservation:
            connection = sqlite3.connect(root / "catalog.sqlite3")
            try:
                connection.execute(
                    "DELETE FROM preparation_reservations WHERE identity_key = ?",
                    (identity.key,),
                )
                connection.commit()
            finally:
                connection.close()
            repository._leases._wake.set()
            deadline = time.monotonic() + 2
            while reservation.alive and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not reservation.alive
            assert preparation.cancellation(lambda: False)()
            with pytest.raises(RepositoryFenceError, match="stale"):
                preparation.stage_prepared_state(
                    producer_sha256=identity.producer_sha256,
                    output_plan_sha256=identity.output_plan_sha256,
                    state_fingerprint=_digest("state"),
                )


def test_busy_renewal_expires_reservation_and_staging_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        lease_ttl_seconds=1.0,
        lease_heartbeat_seconds=0.1,
    )
    identity = _identity("busy-lifecycle-expiry")
    repository = ExportRepository.open(root, limits=limits)
    state = _state(repository, identity, 1)
    state.close()
    preparation = preparation_repository(repository)
    native_renew = repository._catalog.renew_lifecycle
    attempted = threading.Event()
    with preparation.reserve_preparation(identity) as reservation:
        staged_state = preparation.stage_prepared_state(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprint=state_fingerprint({"value": 2}),
        )
        (staged_state.path / "value.txt").write_text("2", encoding="utf-8")
        staged_export = preparation.stage_export(identity)
        _write_index(
            staged_export.path,
            identity,
            state_fingerprint({"value": 1}),
            1,
            "stale",
        )
        staging_paths = {
            staged_state.path.relative_to(root).as_posix(),
            staged_export.path.relative_to(root).as_posix(),
        }

        def busy_heartbeat(**_kwargs):
            attempted.set()
            raise RepositoryBusyError("catalog busy")

        monkeypatch.setattr(repository._catalog, "renew_lifecycle", busy_heartbeat)
        repository._leases._wake.set()
        assert attempted.wait(timeout=2)
        deadline = time.monotonic() + 2
        while reservation.alive and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not reservation.alive
        assert preparation.cancellation(lambda: False)()
        assert identity.key in repository._leases._lost_reservations
        deadline = time.monotonic() + 2
        while (
            not staging_paths.isdisjoint(repository._leases._staging)
            and time.monotonic() < deadline
        ):
            with repository._leases._condition:
                repository._leases._expire_unconfirmed_lifecycle()
            time.sleep(0.01)
        assert staging_paths.isdisjoint(repository._leases._staging)
        with pytest.raises(RepositoryFenceError, match="stale"):
            staged_state.commit(metadata={"value": 2})
        with pytest.raises(RepositoryFenceError, match="stale"):
            staged_export.commit(states=(), captured_observation_revision=0)
        monkeypatch.setattr(repository._catalog, "renew_lifecycle", native_renew)

    assert repository.status().prepared_states == 1
    assert repository.status().generations == 0
    repository.close()


def test_delayed_success_after_deadline_does_not_revive_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        lease_ttl_seconds=2.0,
        lease_heartbeat_seconds=1.5,
    )
    identity = _identity("delayed-reservation-renewal")
    repository = ExportRepository.open(root, limits=limits)
    state = _state(repository, identity, 1)
    state.close()
    preparation = preparation_repository(repository)
    native_renew = repository._catalog.renew_lifecycle
    entered = threading.Event()
    proceed = threading.Event()
    blocked = False
    with preparation.reserve_preparation(identity) as reservation:
        staged_state = preparation.stage_prepared_state(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprint=state_fingerprint({"value": 2}),
        )
        (staged_state.path / "value.txt").write_text("2", encoding="utf-8")
        staged_export = preparation.stage_export(identity)

        def delayed_renewal(**kwargs):
            nonlocal blocked
            renewed = native_renew(**kwargs)
            if kwargs["reservations"] and not blocked:
                blocked = True
                entered.set()
                assert proceed.wait(timeout=limits.lease_ttl_seconds + 2)
            return renewed

        monkeypatch.setattr(repository._catalog, "renew_lifecycle", delayed_renewal)
        assert entered.wait(timeout=limits.lease_ttl_seconds + 2)
        try:
            deadline = time.monotonic() + limits.lease_ttl_seconds + 2
            while reservation.alive and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            proceed.set()
        assert not reservation.alive
        assert preparation.cancellation(lambda: False)()
        with pytest.raises(RepositoryFenceError, match="stale"):
            staged_state.commit(metadata={"value": 2})
        with pytest.raises(RepositoryFenceError, match="stale"):
            staged_export.commit(states=(), captured_observation_revision=0)
        monkeypatch.setattr(repository._catalog, "renew_lifecycle", native_renew)

    assert repository.status().prepared_states == 1
    assert repository.status().generations == 0
    repository.close()


def test_catalog_renewal_does_not_resurrect_expired_lifecycle_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    identity = _identity("expired-catalog-renewal")
    owner = "owner"
    artifact = ("state", _digest("state-key"), _digest("state-instance"))
    staging = "staging/stage-expired"
    expired_at_us = time.time_ns() // 1000 - 1
    with ExportRepository.open(root) as repository:
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute(
                """
                INSERT INTO artifact_leases(
                    owner, kind, artifact_key, instance, expires_at_us
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (owner, *artifact, expired_at_us),
            )
            connection.execute(
                """
                INSERT INTO staging_leases(owner, relative_path, expires_at_us)
                VALUES (?, ?, ?)
                """,
                (owner, staging, expired_at_us),
            )
            connection.execute(
                """
                INSERT INTO preparation_reservations(
                    identity_key, owner, fence, producer_sha256,
                    output_plan_sha256, spec_sha256, expires_at_us
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.key,
                    owner,
                    1,
                    identity.producer_sha256,
                    identity.output_plan_sha256,
                    identity.spec_sha256,
                    expired_at_us,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        lost = repository._catalog.renew_lifecycle(
            owner=owner,
            artifacts=(artifact,),
            staging=(staging,),
            reservations=(identity.key,),
            expires_at_us=time.time_ns() // 1000 + 1_000_000,
        )
        assert lost.artifacts == frozenset({artifact})
        assert lost.staging == frozenset({staging})
        assert lost.reservations == frozenset({identity.key})
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            expiries = {
                int(row[0])
                for table in (
                    "artifact_leases",
                    "staging_leases",
                    "preparation_reservations",
                )
                for row in connection.execute(f"SELECT expires_at_us FROM {table}")
            }
        finally:
            connection.close()
        assert expiries == {expired_at_us}


def test_stale_heartbeat_cannot_shorten_fresh_staging_or_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    identity = _identity("lifecycle-renewal-monotonic")
    repository = ExportRepository.open(root)
    preparation = preparation_repository(repository)
    native_renew = repository._catalog.renew_lifecycle
    entered = threading.Event()
    proceed = threading.Event()
    requested: list[int] = []
    with preparation.reserve_preparation(identity) as reservation:
        staged = preparation.stage_prepared_state(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprint=_digest("state"),
        )
        relative = staged.path.relative_to(root).as_posix()

        def delayed_renewal(**kwargs):
            if kwargs["reservations"] and not requested:
                requested.append(kwargs["expires_at_us"])
                entered.set()
                assert proceed.wait(timeout=2)
            return native_renew(**kwargs)

        monkeypatch.setattr(repository._catalog, "renew_lifecycle", delayed_renewal)
        repository._leases._wake.set()
        assert entered.wait(timeout=2)
        time.sleep(0.05)
        assert (
            repository._leases.claim_reservation(identity, timeout_seconds=1) == reservation.fence
        )
        repository._leases.reserve_staging(relative, staged.path)
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            fresh_reservation = int(
                connection.execute(
                    """
                    SELECT expires_at_us FROM preparation_reservations
                    WHERE identity_key = ?
                    """,
                    (identity.key,),
                ).fetchone()[0]
            )
            fresh_staging = int(
                connection.execute(
                    """
                    SELECT expires_at_us FROM staging_leases
                    WHERE owner = ? AND relative_path = ?
                    """,
                    (repository._leases.owner, relative),
                ).fetchone()[0]
            )
        finally:
            connection.close()
        assert fresh_reservation > requested[0]
        assert fresh_staging > requested[0]
        reservation_deadline = repository._leases._confirmed_reservation_deadline[identity.key]
        staging_deadline = repository._leases._confirmed_staging_deadline[relative]
        proceed.set()
        with repository._leases._condition:
            assert repository._leases._condition.wait_for(
                lambda: not repository._leases._maintaining,
                timeout=2,
            )
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            final_reservation = int(
                connection.execute(
                    "SELECT expires_at_us FROM preparation_reservations WHERE identity_key = ?",
                    (identity.key,),
                ).fetchone()[0]
            )
            final_staging = int(
                connection.execute(
                    """
                    SELECT expires_at_us FROM staging_leases
                    WHERE owner = ? AND relative_path = ?
                    """,
                    (repository._leases.owner, relative),
                ).fetchone()[0]
            )
        finally:
            connection.close()
        assert final_reservation == fresh_reservation
        assert final_staging == fresh_staging
        assert (
            repository._leases._confirmed_reservation_deadline[identity.key] >= reservation_deadline
        )
        assert repository._leases._confirmed_staging_deadline[relative] >= staging_deadline
        assert reservation.alive
        repository._leases.release_reservation(identity.key)
        staged.close()
    repository.close()


def test_prepared_state_manifest_identity_is_bound_to_catalog_row(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("manifest-identity")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        old_path = state.path
        old_instance = state.instance
        fingerprint = state.state_fingerprint
        state.close()
        manifest_path = old_path / "prepared-state.json"
        value = decode_json_object(manifest_path.read_bytes(), "prepared state")
        value["producer_sha256"] = _digest("another-producer")
        encoded = canonical_bytes(value)
        new_instance = sha256(encoded).hexdigest()
        new_path = old_path.parent / new_instance
        os.replace(old_path, new_path)
        files_module.make_tree_writable(new_path)
        (new_path / "prepared-state.json").write_bytes(encoded)
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute(
                """
                UPDATE prepared_states SET instance = ?
                WHERE instance = ?
                """,
                (new_instance, old_instance),
            )
            connection.execute(
                "UPDATE state_scopes SET current_instance = ? WHERE current_instance = ?",
                (new_instance, old_instance),
            )
            connection.commit()
        finally:
            connection.close()
        prepared = preparation_repository(repository).lookup_prepared_states(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprints=(fingerprint,),
        )
        assert prepared == {}
        assert not new_path.exists()


def test_stale_reservation_fence_cannot_replace_new_owner_generation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    identity = _identity("generation-fence")
    first = ExportRepository.open(root)
    second = ExportRepository.open(root)
    state = _state(first, identity, 1)
    first_preparation = preparation_repository(first)
    second_preparation = preparation_repository(second)
    first_context = first_preparation.reserve_preparation(identity)
    first_reservation = first_context.__enter__()
    first_staged = first_preparation.stage_export(identity)
    _write_index(first_staged.path, identity, state.state_fingerprint, 1, "first")
    with first._leases._condition:
        first._leases._condition.wait_for(
            lambda: not first._leases._maintaining,
            timeout=2,
        )
        first._leases._reservations.pop(identity.key, None)
    connection = sqlite3.connect(root / "catalog.sqlite3")
    try:
        connection.execute(
            "UPDATE preparation_reservations SET expires_at_us = 0 WHERE identity_key = ?",
            (identity.key,),
        )
        connection.commit()
    finally:
        connection.close()

    with second_preparation.reserve_preparation(identity) as second_reservation:
        assert second_reservation.fence > first_reservation.fence
        with second_preparation.stage_export(identity) as staged:
            _write_index(staged.path, identity, state.state_fingerprint, 1, "second")
            winner = staged.commit(states=(state,), captured_observation_revision=0)
    with pytest.raises(RepositoryFenceError, match="stale"):
        first_staged.commit(states=(state,), captured_observation_revision=0)
    current = second_preparation.current(identity)
    assert current is not None
    assert current.instance == winner.instance
    current.close()
    winner.close()
    state.close()
    first_context.__exit__(None, None, None)
    first.close()
    second.close()


def test_recovery_isolates_corrupt_generation_row_and_preserves_healthy_row(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    corrupt_identity = _identity("row-corrupt")
    healthy_identity = _identity("row-healthy")
    with ExportRepository.open(root) as repository:
        corrupt_state = _state(repository, corrupt_identity, 1)
        corrupt_export = _export(repository, corrupt_identity, corrupt_state, "corrupt")
        corrupt_path = corrupt_export.path
        healthy_state = _state(repository, healthy_identity, 2)
        healthy_export = _export(repository, healthy_identity, healthy_state, "healthy")
        corrupt_export.close()
        corrupt_state.close()
        healthy_export.close()
        healthy_state.close()
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute(
                """
                UPDATE generations SET metadata_json = ?, metadata_bytes = ?
                WHERE identity_key = ?
                """,
                ("wrong affinity", 14, corrupt_identity.key),
            )
            connection.commit()
        finally:
            connection.close()
        repository._recover()
        assert preparation_repository(repository).current(corrupt_identity) is None
        healthy = preparation_repository(repository).current(healthy_identity)
        assert healthy is not None
        assert healthy.asset("index.json") is not None
        assert not corrupt_path.exists()
        healthy.close()


def test_recovery_drops_malformed_relation_keys_and_preserves_healthy_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    producer_identity = _identity("bad-producer-key")
    state_identity = _identity("bad-state-key")
    export_identity = _identity("bad-identity-key")
    healthy_identity = _identity("healthy-keys")
    with ExportRepository.open(root) as repository:
        observation_repository(repository).record(
            producer_sha256=producer_identity.producer_sha256,
            values={"value": 1},
        )
        artifacts: dict[RepositoryIdentity, tuple[Path, Path]] = {}
        for value, identity in enumerate(
            (
                producer_identity,
                state_identity,
                export_identity,
                healthy_identity,
            ),
            start=1,
        ):
            state = _state(repository, identity, value)
            export = _export(repository, identity, state, str(value))
            artifacts[identity] = (state.path, export.path)
            export.close()
            state.close()

        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            connection.execute(
                "UPDATE producers SET producer_sha256 = ? WHERE producer_sha256 = ?",
                ("bad-producer", producer_identity.producer_sha256),
            )
            connection.execute(
                """
                UPDATE state_scopes SET state_key = ?
                WHERE producer_sha256 = ?
                """,
                ("bad-state", state_identity.producer_sha256),
            )
            connection.execute(
                "UPDATE identities SET identity_key = ? WHERE identity_key = ?",
                ("bad-identity", export_identity.key),
            )
            connection.commit()
        finally:
            connection.close()

        repository._recover()
        preparation = preparation_repository(repository)
        for identity in (producer_identity, state_identity, export_identity):
            assert preparation.current(identity) is None
        for identity in (producer_identity, state_identity):
            assert not artifacts[identity][0].exists()
            assert not artifacts[identity][1].exists()
        assert artifacts[export_identity][0].is_dir()
        assert not artifacts[export_identity][1].exists()
        reusable = preparation.lookup_prepared_states(
            producer_sha256=export_identity.producer_sha256,
            output_plan_sha256=export_identity.output_plan_sha256,
            state_fingerprints=(state_fingerprint({"value": 3}),),
        )
        assert tuple(reusable) == (state_fingerprint({"value": 3}),)
        reusable[state_fingerprint({"value": 3})].close()
        healthy = preparation.current(healthy_identity)
        assert healthy is not None
        assert healthy.asset("index.json") is not None
        assert artifacts[healthy_identity][0].is_dir()
        assert artifacts[healthy_identity][1].is_dir()
        healthy.close()
        status = repository.status()
        assert status.prepared_states == 2
        assert status.generations == 1
        assert status.observations == 0
        connection = sqlite3.connect(root / "catalog.sqlite3")
        try:
            event_count = connection.execute("SELECT COUNT(*) FROM observation_events").fetchone()
        finally:
            connection.close()
        assert event_count == (0,)
