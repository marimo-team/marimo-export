from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from marimo_export._repository.observations import observation_repository
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
)

pytestmark = pytest.mark.serial


def test_interrupted_generation_swap_restores_verified_backup(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("swap")
    with ExportRepository.open(root) as repository:
        state = _state(repository, identity, 1)
        export = _export(repository, identity, state, "one")
        target = export.path
        instance = export.instance
        export.close()
        state.close()
        backup = target.parent / f".{instance}-incomplete-1"
        os.replace(target, backup)
        repository._recover()
        recovered = preparation_repository(repository).current(identity)
        assert recovered is not None
        assert recovered.asset("index.json") is not None
        assert not backup.exists()
        recovered.close()


def test_same_instance_advances_coverage_without_regression(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity = _identity("coverage")
    with ExportRepository.open(root) as repository:
        observation_repository(repository).record(
            producer_sha256=identity.producer_sha256,
            values={"value": 1},
        )
        state = _state(repository, identity, 1)
        first = _export(repository, identity, state, "stable", revision=1)
        observation_repository(repository).record(
            producer_sha256=identity.producer_sha256,
            values={"value": 1},
        )
        second = _export(
            repository,
            identity,
            state,
            "stable",
            revision=2,
            replacing=first.instance,
        )
        assert second.instance == first.instance
        assert second.captured_observation_revision == 2
        current = preparation_repository(repository).current(identity)
        assert current is not None
        assert current.captured_observation_revision == 2
        first.close()
        second.close()
        current.close()
        state.close()


def test_future_observation_coverage_is_rejected_atomically(tmp_path: Path) -> None:
    identity = _identity("future")
    with ExportRepository.open(tmp_path / "repository") as repository:
        state = _state(repository, identity, 1)
        with pytest.raises(ValueError, match="exceeds producer revision"):
            _export(repository, identity, state, "future", revision=1)
        assert preparation_repository(repository).current(identity) is None
        state.close()


def test_retained_generation_is_immutable_after_current_advances(tmp_path: Path) -> None:
    identity = _identity("immutable")
    with ExportRepository.open(tmp_path / "repository") as repository:
        state = _state(repository, identity, 1)
        first = _export(repository, identity, state, "one")
        first_index = first.asset("index.json")
        assert first_index is not None
        first_bytes = first_index.read_bytes()
        second = _export(
            repository,
            identity,
            state,
            "two",
            replacing=first.instance,
        )
        assert first_index.read_bytes() == first_bytes
        assert second.asset("index.json") is not None
        first.close()
        second.close()
        state.close()


def test_detached_lease_survives_repository_close_and_prune(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        retained_identities=1,
        retained_generations=1,
        retained_prepared_states=1,
        lease_ttl_seconds=2.0,
        lease_heartbeat_seconds=0.1,
    )
    identity = _identity("detached")
    repository = ExportRepository.open(root, limits=limits)
    state = _state(repository, identity, 1)
    export = _export(repository, identity, state, "detached")
    detached = export.detach()
    path = export.path
    export.close()
    state.close()
    repository.close()

    with ExportRepository.open(root, limits=limits) as pruning:
        newer_identity = _identity("detached-newer")
        newer_state = _state(pruning, newer_identity, 2)
        newer_export = _export(pruning, newer_identity, newer_state, "newer")
        pruning.prune()
        assert (path / "index.json").is_file()
        detached.close()
        deadline = time.monotonic() + limits.lease_ttl_seconds + 2
        while path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
            pruning.prune()
        assert not path.exists()
        newer_export.close()
        newer_state.close()


def test_replacement_overcommit_preserves_old_handle_then_returns_to_quota(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    identity = _identity("replacement")
    fingerprint = state_fingerprint({"value": 1})
    with ExportRepository.open(root) as seed:
        state = _state(seed, identity, 1)
        first = _export(seed, identity, state, "oversized")
        steady_bytes = seed.status().content_bytes
        generation_bytes = first.content_bytes
        first.close()
        state.close()

    limits = RepositoryLimits(
        generation_bytes=generation_bytes,
        repository_bytes=steady_bytes,
    )
    with ExportRepository.open(root, limits=limits) as repository:
        preparation = preparation_repository(repository)
        states = preparation.lookup_prepared_states(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprints=(fingerprint,),
        )
        state = states[fingerprint]
        old = preparation.current(identity)
        assert old is not None
        old_path = old.path
        replacement = _export(
            repository,
            identity,
            state,
            "new",
            replacing=old.instance,
        )
        assert old.asset("index.json") is not None
        assert replacement.asset("index.json") is not None
        assert repository.status().content_bytes > limits.repository_bytes

        old.close()
        replacement.close()
        state.close()
        repository.prune()
        assert repository.status().content_bytes <= limits.repository_bytes
        assert not old_path.exists()


def test_crashed_process_lease_expires_before_generation_is_pruned(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        retained_identities=1,
        retained_generations=1,
        retained_prepared_states=1,
        lease_ttl_seconds=10.0,
        lease_heartbeat_seconds=0.1,
    )
    old_identity = _identity("process-old")
    with ExportRepository.open(root, limits=limits) as seed:
        old_state = _state(seed, old_identity, 1)
        old_export = _export(seed, old_identity, old_state, "old")
        old_path = old_export.path
        old_export.close()
        old_state.close()

    ready = tmp_path / "ready"
    code = """
import sys
import time
from hashlib import sha256
from pathlib import Path
from marimo_export._repository.preparation import RepositoryIdentity, preparation_repository
from marimo_export.repository import ExportRepository, RepositoryLimits

def verify(path):
    return sha256((path / "index.json").read_bytes()).hexdigest()

identity = RepositoryIdentity(sys.argv[2], sys.argv[3], sys.argv[4])
limits = RepositoryLimits(
    retained_identities=1,
    retained_generations=1,
    retained_prepared_states=1,
    lease_ttl_seconds=10.0,
    lease_heartbeat_seconds=0.1,
)
repository = ExportRepository.open(sys.argv[1], limits=limits)
handle = preparation_repository(repository).current(identity)
if handle is None:
    raise RuntimeError("generation missing")
Path(sys.argv[5]).write_text("ready", encoding="utf-8")
while True:
    time.sleep(1)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(root),
            old_identity.producer_sha256,
            old_identity.output_plan_sha256,
            old_identity.spec_sha256,
            str(ready),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if not ready.exists():
        if process.poll() is None:
            process.kill()
        stdout, stderr = process.communicate(timeout=10)
        pytest.fail(
            f"lease holder did not become ready (exit {process.returncode})\n{stdout}{stderr}"
        )

    try:
        with ExportRepository.open(root, limits=limits) as repository:
            newer_identity = _identity("process-new")
            newer_state = _state(repository, newer_identity, 2)
            newer_export = _export(repository, newer_identity, newer_state, "new")
            repository.prune()
            assert old_path.is_dir()

            process.kill()
            process.communicate(timeout=10)
            deadline = time.monotonic() + limits.lease_ttl_seconds + 5
            while old_path.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
                repository.prune()
            assert not old_path.exists()
            newer_export.close()
            newer_state.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
