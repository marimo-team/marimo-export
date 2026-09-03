from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import pytest
from marimo_export._repository.artifacts import ArtifactRepository
from marimo_export._repository.models import RepositoryBusyError
from marimo_export._repository.observations import observation_repository
from marimo_export._repository.preparation import (
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export.errors import ExecutionError
from marimo_export.repository import (
    ExportRepository,
    RepositoryLimitError,
    RepositoryLimits,
)
from marimo_export.wire import state_fingerprint


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def test_concurrent_first_open_creates_one_valid_catalog(tmp_path: Path) -> None:
    root = tmp_path / "repository"

    def open_once(_index: int) -> int:
        with ExportRepository.open(root) as repository:
            return repository.status().producers

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(open_once, range(16))) == [0] * 16

    with ExportRepository.open(root) as repository:
        assert repository.status().generations == 0


def test_concurrent_processes_open_one_repository(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    source = """\
import sys
from pathlib import Path
from marimo_export import ExportRepository

with ExportRepository.open(Path(sys.argv[1])) as repository:
    assert repository.status().generations == 0
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", source, str(root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _index in range(4)
    ]
    failures: list[str] = []
    for process in processes:
        _stdout, stderr = process.communicate(timeout=30)
        if process.returncode != 0:
            failures.append(stderr)
    assert failures == []


def test_open_defers_recovery_owned_by_another_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def busy(_repository: ArtifactRepository) -> None:
        raise RepositoryBusyError("maintenance owned by another process")

    monkeypatch.setattr(ArtifactRepository, "recover", busy)

    with ExportRepository.open(tmp_path / "repository") as repository:
        assert repository.status().generations == 0


def test_concurrent_threads_retain_every_observation(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    producer = _digest("producer")
    with ExportRepository.open(root) as repository:
        observations = observation_repository(repository)

        def record(value: int) -> None:
            observations.record(
                producer_sha256=producer,
                values={"value": value},
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            tuple(executor.map(record, range(32)))

        states = preparation_repository(repository).observations(
            producer_sha256=producer,
            inputs=("value",),
        )
        assert {state.values["value"] for state in states} == set(range(32))
        assert observations.revision(producer) == 32


def test_active_staging_survives_another_repository_recovery(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    producer = _digest("producer")
    output_plan = _digest("outputs")
    state = _digest("state")
    with ExportRepository.open(root) as owner:
        preparation = preparation_repository(owner)
        identity = RepositoryIdentity(producer, output_plan, _digest("spec"))
        with preparation.reserve_preparation(identity):
            staged = preparation.stage_prepared_state(
                producer_sha256=producer,
                output_plan_sha256=output_plan,
                state_fingerprint=state,
            )
            (staged.path / "pending.txt").write_text("pending", encoding="utf-8")
            with ExportRepository.open(root) as recovering:
                recovering._recover()
                assert (staged.path / "pending.txt").read_text(encoding="utf-8") == "pending"
            staged.close()


def test_abandoned_staging_is_removed_on_restart(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    with ExportRepository.open(root):
        abandoned = root / "staging" / "stage-abandoned"
        abandoned.mkdir(parents=True)
        (abandoned / "partial.txt").write_text("partial", encoding="utf-8")
    with ExportRepository.open(root):
        assert not abandoned.exists()


def test_concurrent_processes_retain_every_observation(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    producer = _digest("process-producer")
    code = """
import sys
from marimo_export._repository.observations import observation_repository
from marimo_export.repository import ExportRepository
with ExportRepository.open(sys.argv[1]) as repository:
    observation_repository(repository).record(
        producer_sha256=sys.argv[2],
        values={"value": int(sys.argv[3])},
    )
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(root), producer, str(value)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for value in range(8)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)

    with ExportRepository.open(root) as repository:
        states = preparation_repository(repository).observations(
            producer_sha256=producer,
            inputs=("value",),
        )
        assert {state.values["value"] for state in states} == set(range(8))
        assert observation_repository(repository).revision(producer) == 8


def test_concurrent_artifact_admission_cannot_overbook_repository_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    limits = RepositoryLimits(
        prepared_state_bytes=1500,
        repository_bytes=1500,
    )
    first = ExportRepository.open(root, limits=limits)
    second = ExportRepository.open(root, limits=limits)

    def commit(repository: ExportRepository, value: int):
        inputs = {"value": value}
        identity = RepositoryIdentity(
            _digest("producer"),
            _digest("outputs"),
            _digest(f"spec-{value}"),
        )
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_prepared_state(
                producer_sha256=identity.producer_sha256,
                output_plan_sha256=identity.output_plan_sha256,
                state_fingerprint=state_fingerprint(inputs),
            ) as staged,
        ):
            (staged.path / "payload.bin").write_bytes(bytes([value]) * 400)
            return staged.commit(metadata={"inputs": inputs})

    handles = []
    errors = []
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (executor.submit(commit, first, 1), executor.submit(commit, second, 2))
            for future in futures:
                try:
                    handles.append(future.result(timeout=20))
                except RepositoryLimitError as error:
                    errors.append(error)
        assert len(handles) == 1
        assert len(errors) == 1
        assert first.status().content_bytes <= limits.repository_bytes
    finally:
        for handle in handles:
            handle.close()
        first.close()
        second.close()


def test_crashed_staging_and_reservation_expire_together(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    identity_parts = (_digest("producer"), _digest("outputs"), _digest("spec"))
    ready = tmp_path / "ready"
    code = """
import os
import sys
from pathlib import Path
from marimo_export._repository.preparation import RepositoryIdentity, preparation_repository
from marimo_export.repository import ExportRepository, RepositoryLimits

limits = RepositoryLimits(lease_ttl_seconds=0.5, lease_heartbeat_seconds=0.05)
identity = RepositoryIdentity(sys.argv[2], sys.argv[3], sys.argv[4])
repository = ExportRepository.open(sys.argv[1], limits=limits)
preparation = preparation_repository(repository)
with preparation.reserve_preparation(identity):
    staged = preparation.stage_prepared_state(
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        state_fingerprint="0" * 64,
    )
    (staged.path / "partial.txt").write_text("partial", encoding="utf-8")
    Path(sys.argv[5]).write_text(str(staged.path), encoding="utf-8")
    os._exit(0)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(root), *identity_parts, str(ready)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    staged_path = Path(ready.read_text(encoding="utf-8"))
    identity = RepositoryIdentity(*identity_parts)
    limits = RepositoryLimits(lease_ttl_seconds=0.5, lease_heartbeat_seconds=0.05)

    with ExportRepository.open(root, limits=limits) as repository:
        assert staged_path.is_dir()
        preparation = preparation_repository(repository)
        with (
            pytest.raises(ExecutionError, match="cancelled") as raised,
            preparation.reserve_preparation(identity, cancelled=lambda: True),
        ):
            raise AssertionError("crashed reservation has not expired")
        assert raised.value.code == "preparation_cancelled"

    time.sleep(0.6)
    with ExportRepository.open(root, limits=limits) as repository:
        assert not staged_path.exists()
        with preparation_repository(repository).reserve_preparation(identity):
            pass


def test_busy_renewal_expires_cross_process_reservation_and_staging(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    ready = tmp_path / "ready.json"
    proceed = tmp_path / "proceed"
    result = tmp_path / "result.json"
    identity_parts = (_digest("busy-producer"), _digest("busy-outputs"), _digest("busy-spec"))
    code = """
import json
import os
import sys
import time
from pathlib import Path
from marimo_export._repository.models import RepositoryBusyError
from marimo_export._repository.preparation import RepositoryIdentity, preparation_repository
from marimo_export.repository import ExportRepository, RepositoryLimits

root = Path(sys.argv[1])
ready = Path(sys.argv[5])
proceed = Path(sys.argv[6])
result = Path(sys.argv[7])
limits = RepositoryLimits(lease_ttl_seconds=0.3, lease_heartbeat_seconds=0.05)
identity = RepositoryIdentity(sys.argv[2], sys.argv[3], sys.argv[4])
repository = ExportRepository.open(root, limits=limits)
preparation = preparation_repository(repository)
context = preparation.reserve_preparation(identity)
reservation = context.__enter__()
staged_state = preparation.stage_prepared_state(
    producer_sha256=identity.producer_sha256,
    output_plan_sha256=identity.output_plan_sha256,
    state_fingerprint="0" * 64,
)
(staged_state.path / "value.txt").write_text("stale", encoding="utf-8")
staged_export = preparation.stage_export(identity)
attempts = 0

def busy_heartbeat(**_kwargs):
    global attempts
    attempts += 1
    raise RepositoryBusyError("catalog busy")

repository._catalog.renew_lifecycle = busy_heartbeat
repository._leases._wake.set()
ready.write_text(
    json.dumps(
        {
            "fence": reservation.fence,
            "paths": [str(staged_state.path), str(staged_export.path)],
        }
    ),
    encoding="utf-8",
)
deadline = time.monotonic() + 10
while not proceed.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
errors = []
for commit in (
    lambda: staged_state.commit(metadata={"value": 1}),
    lambda: staged_export.commit(states=(), captured_observation_revision=0),
):
    try:
        commit()
    except BaseException as error:
        errors.append(type(error).__name__)
status = repository.status()
result.write_text(
    json.dumps(
        {
            "alive": reservation.alive,
            "cancelled": preparation.cancellation(lambda: False)(),
            "attempts": attempts,
            "errors": errors,
            "prepared_states": status.prepared_states,
            "generations": status.generations,
        }
    ),
    encoding="utf-8",
)
os._exit(0)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(root),
            *identity_parts,
            str(ready),
            str(proceed),
            str(result),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    initial = json.loads(ready.read_text(encoding="utf-8"))
    time.sleep(0.7)
    identity = RepositoryIdentity(*identity_parts)
    limits = RepositoryLimits(lease_ttl_seconds=0.3, lease_heartbeat_seconds=0.05)
    with ExportRepository.open(root, limits=limits) as repository:
        assert all(not Path(path).exists() for path in initial["paths"])
        with preparation_repository(repository).reserve_preparation(identity) as winner:
            assert winner.fence > initial["fence"]
        assert repository.status().prepared_states == 0
        assert repository.status().generations == 0
    proceed.write_text("continue", encoding="utf-8")
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    final = json.loads(result.read_text(encoding="utf-8"))
    assert final["attempts"] >= 2
    assert final["alive"] is False
    assert final["cancelled"] is True
    assert final["errors"] == ["RepositoryFenceError", "RepositoryFenceError"]
    assert final["prepared_states"] == 0
    assert final["generations"] == 0


def test_different_specs_adopt_one_identical_shared_state(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    producer = _digest("shared-producer")
    output_plan = _digest("shared-outputs")
    first = ExportRepository.open(root)
    second = ExportRepository.open(root)

    def prepare(repository: ExportRepository, spec: str):
        identity = RepositoryIdentity(producer, output_plan, _digest(spec))
        inputs = {"value": 1}
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_prepared_state(
                producer_sha256=producer,
                output_plan_sha256=output_plan,
                state_fingerprint=state_fingerprint(inputs),
            ) as staged,
        ):
            (staged.path / "payload.bin").write_bytes(b"same")
            return staged.commit(metadata={"inputs": inputs})

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(prepare, first, "first")
            second_future = executor.submit(prepare, second, "second")
            first_state = first_future.result(timeout=20)
            second_state = second_future.result(timeout=20)
        assert first_state.instance == second_state.instance
        assert first.status().prepared_states == 1
        first_state.close()
        second_state.close()
    finally:
        first.close()
        second.close()


def test_same_repository_serializes_same_identity_reservations(tmp_path: Path) -> None:
    repository = ExportRepository.open(tmp_path / "repository")
    preparation = preparation_repository(repository)
    identity = RepositoryIdentity(_digest("producer"), _digest("outputs"), _digest("spec"))
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_operation() -> None:
        with preparation.reserve_preparation(identity, timeout=2):
            first_entered.set()
            assert release_first.wait(timeout=2)

    def second_operation() -> None:
        assert first_entered.wait(timeout=2)
        with preparation.reserve_preparation(identity, timeout=2):
            second_entered.set()

    first_thread = threading.Thread(target=first_operation)
    second_thread = threading.Thread(target=second_operation)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(timeout=2)
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert second_entered.is_set()
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    repository.close()


def test_same_repository_reservation_wait_cancellation_is_public_error(
    tmp_path: Path,
) -> None:
    repository = ExportRepository.open(tmp_path / "repository")
    preparation = preparation_repository(repository)
    identity = RepositoryIdentity(_digest("producer"), _digest("outputs"), _digest("spec"))
    failures: list[BaseException] = []

    def wait_for_reservation() -> None:
        try:
            with preparation.reserve_preparation(
                identity,
                cancelled=lambda: True,
                timeout=1,
            ):
                raise AssertionError("cancelled reservation must not be acquired")
        except BaseException as error:
            failures.append(error)

    with preparation.reserve_preparation(identity):
        waiting = threading.Thread(target=wait_for_reservation)
        waiting.start()
        waiting.join(timeout=2)
    assert not waiting.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], ExecutionError)
    assert failures[0].code == "preparation_cancelled"
    repository.close()


def test_concurrent_open_recovers_wrong_maintenance_schema(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    connection = sqlite3.connect(root / "maintenance.sqlite3")
    try:
        connection.execute("CREATE TABLE maintenance_lock(wrong INTEGER)")
        connection.commit()
    finally:
        connection.close()

    def open_once(_index: int) -> int:
        with ExportRepository.open(root) as repository:
            return repository.status().generations

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(open_once, range(8))) == [0] * 8


@pytest.mark.skipif(os.name != "nt", reason="Windows open-handle replacement contract")
def test_wrong_maintenance_schema_waits_for_peer_handle_on_windows(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    path = root / "maintenance.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("CREATE TABLE maintenance_lock(wrong INTEGER)")
        connection.commit()
    finally:
        connection.close()
    peer = sqlite3.connect(path)
    peer.execute("SELECT * FROM maintenance_lock").fetchall()

    def open_once() -> int:
        with ExportRepository.open(root) as repository:
            return repository.status().generations

    with ThreadPoolExecutor(max_workers=1) as executor:
        opening = executor.submit(open_once)
        time.sleep(0.1)
        assert not opening.done()
        peer.close()
        assert opening.result(timeout=5) == 0
