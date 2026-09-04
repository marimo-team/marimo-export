from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import marimo_export._observations.candidates as candidates_module
import marimo_export._observations.ledger as ledger_module
import marimo_export._observations.queue as queue_module
import marimo_export._observations.source as source_module
import pytest
from marimo_export._repository.observations import observation_repository
from marimo_export.observations import (
    ObservationLedger,
    ObservationPersistenceError,
    ObservationRejectedError,
    ObservedInputs,
)
from marimo_export.repository import (
    ExportRepository,
    RepositoryBusyError,
    RepositoryLimits,
    RepositoryUnavailableError,
)


class _Repository:
    def __init__(
        self,
        *,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.started = started
        self.release = release
        self.records: list[tuple[str, Mapping[str, object], int]] = []
        self.revisions: list[tuple[str, int]] = []
        self.writes: list[tuple[str, str, int]] = []
        self.closed = False
        self._lock = threading.Lock()

    def record(
        self,
        *,
        producer_sha256: str,
        values: Mapping[str, object],
        occurrences: int = 1,
    ) -> object:
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            assert self.release.wait(5)
        with self._lock:
            self.records.append((producer_sha256, dict(values), occurrences))
            self.writes.append(("record", producer_sha256, occurrences))
        return object()

    def advance_revision(
        self,
        *,
        producer_sha256: str,
        occurrences: int = 1,
    ) -> int:
        with self._lock:
            self.revisions.append((producer_sha256, occurrences))
            self.writes.append(("advance", producer_sha256, occurrences))
        return occurrences

    def close(self) -> None:
        self.closed = True


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "notebook.py"
    source.write_text("one", encoding="utf-8")
    return source


def _opened(repository: _Repository) -> ledger_module._OpenedObservations:
    return ledger_module._OpenedObservations(
        repository=cast(Any, repository),
        close=repository.close,
    )


def _ledger(source: Path, repository: _Repository) -> ObservationLedger:
    return ObservationLedger(
        source,
        _repository_factory=lambda: _opened(repository),
    )


def test_observed_inputs_are_canonical_immutable_and_detached() -> None:
    authored = {"theme": {"dark": True}, "values": [2, 1]}
    observed = ObservedInputs(authored)
    authored["theme"] = {"dark": False}
    cast_values = observed.values

    assert tuple(cast_values) == ("theme", "values")
    assert cast_values == {"theme": {"dark": True}, "values": (2, 1)}
    assert observed.byte_count == len(observed.canonical_values)
    assert observed == ObservedInputs({"values": [2, 1], "theme": {"dark": True}})
    with pytest.raises(TypeError):
        cast(Any, cast_values)["theme"] = {}
    with pytest.raises(TypeError):
        cast(Any, cast_values["theme"])["dark"] = False


def test_repository_opens_on_the_worker_after_first_record(tmp_path: Path) -> None:
    source = _source(tmp_path)
    repository = _Repository()
    created = threading.Event()
    threads: list[int] = []

    def factory() -> ledger_module._OpenedObservations:
        threads.append(threading.get_ident())
        created.set()
        return _opened(repository)

    ledger = ObservationLedger(source, _repository_factory=factory)

    assert not created.is_set()
    ledger.record(ObservedInputs({"scale": 1}), producer_sha256="a" * 64)
    assert created.wait(5)
    ledger.close()

    assert threads == [ledger._worker.ident]
    assert repository.records == [("a" * 64, {"scale": 1}, 1)]
    assert repository.closed


def test_close_before_first_record_creates_no_repository(tmp_path: Path) -> None:
    created = 0

    def factory() -> ledger_module._OpenedObservations:
        nonlocal created
        created += 1
        return _opened(_Repository())

    ledger = ObservationLedger(_source(tmp_path), _repository_factory=factory)
    ledger.close()

    assert created == 0
    with pytest.raises(RuntimeError, match="closed"):
        ledger.record(ObservedInputs({"scale": 1}))
    assert created == 0


def test_injected_repository_remains_caller_owned(tmp_path: Path) -> None:
    repository = ExportRepository.open(tmp_path / "repository")
    ledger = ObservationLedger(
        _source(tmp_path),
        repository=repository,
    )
    ledger.record(ObservedInputs({"scale": 1}), producer_sha256="a" * 64)
    ledger.close()

    assert observation_repository(repository).revision("a" * 64) == 1
    assert repository.status().observations == 1
    repository.close()


def test_context_manager_closes_implicit_repository_and_joins_worker(tmp_path: Path) -> None:
    repository = _Repository()

    with ObservationLedger(
        _source(tmp_path),
        _repository_factory=lambda: _opened(repository),
    ) as ledger:
        ledger.record(ObservedInputs({"scale": 1}), producer_sha256="a" * 64)

    assert repository.closed
    assert not ledger._worker.is_alive()
    ledger.close()


def test_context_manager_leaves_injected_repository_caller_owned(tmp_path: Path) -> None:
    repository = ExportRepository.open(tmp_path / "repository")

    with ObservationLedger(
        _source(tmp_path),
        repository=repository,
    ) as ledger:
        ledger.record(ObservedInputs({"scale": 1}), producer_sha256="a" * 64)

    assert observation_repository(repository).revision("a" * 64) == 1
    assert repository.status().observations == 1
    assert not ledger._worker.is_alive()
    repository.close()


def test_concurrent_close_waits_for_owned_repository_close(tmp_path: Path) -> None:
    source = _source(tmp_path)
    close_started = threading.Event()
    close_release = threading.Event()
    second_finished = threading.Event()

    class Repository(_Repository):
        def close(self) -> None:
            close_started.set()
            assert close_release.wait(5)
            super().close()

    repository = Repository()
    ledger = _ledger(source, repository)
    ledger.record(ObservedInputs({"scale": 1}), producer_sha256="a" * 64)
    ledger.flush()

    first = threading.Thread(target=ledger.close)

    def close_second() -> None:
        ledger.close()
        second_finished.set()

    second = threading.Thread(target=close_second)
    first.start()
    assert close_started.wait(5)
    second.start()
    assert not second_finished.wait(0.05)
    close_release.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert second_finished.is_set()


def test_same_state_coalesces_without_losing_revision_count(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    repository = _Repository(started=started, release=release)
    ledger = _ledger(_source(tmp_path), repository)
    observed = ObservedInputs({"scale": 1})

    ledger.record(observed, producer_sha256="a" * 64)
    assert started.wait(5)
    for _ in range(4):
        ledger.record(observed, producer_sha256="a" * 64)
    release.set()
    ledger.close()

    assert sum(occurrences for _producer, _values, occurrences in repository.records) == 5
    assert len(repository.records) <= 2


def test_record_remains_nonblocking_while_worker_persists(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    repository = _Repository(started=started, release=release)
    ledger = _ledger(_source(tmp_path), repository)
    observed = ObservedInputs({"values": list(range(20_000))})
    finished = threading.Event()
    errors: list[BaseException] = []

    def record() -> None:
        try:
            ledger.record(observed, producer_sha256="a" * 64)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    caller = threading.Thread(target=record)
    caller.start()
    try:
        assert started.wait(5)
        assert finished.wait(5)
    finally:
        release.set()
        caller.join(timeout=5)
    ledger.close()

    assert not caller.is_alive()
    assert errors == []


def test_bounded_eviction_advances_revision_without_retaining_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(queue_module, "MAX_PENDING_OBSERVATIONS", 2)
    started = threading.Event()
    release = threading.Event()
    repository = _Repository(started=started, release=release)
    ledger = _ledger(_source(tmp_path), repository)

    ledger.record(ObservedInputs({"scale": 0}), producer_sha256="a" * 64)
    assert started.wait(5)
    for scale in (1, 2, 3):
        ledger.record(ObservedInputs({"scale": scale}), producer_sha256="a" * 64)
    release.set()
    ledger.close()

    revisions = sum(item[2] for item in repository.records) + sum(
        occurrences for _producer, occurrences in repository.revisions
    )
    assert revisions == 4
    assert {item[1]["scale"] for item in repository.records} == {0, 2, 3}
    assert repository.revisions == [("a" * 64, 1)]
    assert repository.writes == [
        ("record", "a" * 64, 1),
        ("advance", "a" * 64, 1),
        ("record", "a" * 64, 1),
        ("record", "a" * 64, 1),
    ]


def test_pending_byte_limit_evicts_the_oldest_vector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ObservedInputs({"scale": "a" * 40})
    second = ObservedInputs({"scale": "b" * 40})
    monkeypatch.setattr(queue_module, "MAX_PENDING_BYTES", first.byte_count + 1)
    started = threading.Event()
    release = threading.Event()
    repository = _Repository(started=started, release=release)
    ledger = _ledger(_source(tmp_path), repository)

    ledger.record(ObservedInputs({"scale": "active"}), producer_sha256="a" * 64)
    assert started.wait(5)
    ledger.record(first, producer_sha256="a" * 64)
    ledger.record(second, producer_sha256="a" * 64)
    release.set()
    ledger.close()

    assert [values["scale"] for _producer, values, _count in repository.records] == [
        "active",
        "b" * 40,
    ]
    assert repository.revisions == [("a" * 64, 1)]


def test_distinct_pending_producers_apply_bounded_backpressure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(queue_module, "MAX_PENDING_PRODUCERS", 2)
    started = threading.Event()
    release = threading.Event()
    repository = _Repository(started=started, release=release)
    ledger = _ledger(_source(tmp_path), repository)

    ledger.record(ObservedInputs({"scale": 0}), producer_sha256="0" * 64)
    assert started.wait(5)
    ledger.record(ObservedInputs({"scale": 1}), producer_sha256="1" * 64)
    ledger.record(ObservedInputs({"scale": 2}), producer_sha256="2" * 64)
    with pytest.raises(ObservationRejectedError, match="producer"):
        ledger.record(ObservedInputs({"scale": 3}), producer_sha256="3" * 64)
    release.set()
    ledger.close()

    assert {producer for producer, _values, _count in repository.records} == {
        "0" * 64,
        "1" * 64,
        "2" * 64,
    }


def test_deferred_source_churn_applies_bounded_backpressure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(candidates_module, "MAX_PENDING_PRODUCERS", 2)
    started = threading.Event()
    release = threading.Event()
    repository = _Repository()
    ledger = _ledger(_source(tmp_path), repository)

    def blocking() -> str:
        started.set()
        assert release.wait(5)
        return "0" * 64

    ledger._record_deferred(
        ObservedInputs({"scale": 0}),
        scope="revision-0",
        resolve_producer=blocking,
    )
    assert started.wait(5)
    for index in (1, 2):
        ledger._record_deferred(
            ObservedInputs({"scale": index}),
            scope=f"revision-{index}",
            resolve_producer=lambda index=index: str(index) * 64,
        )
    with pytest.raises(ObservationRejectedError, match="source revisions"):
        ledger._record_deferred(
            ObservedInputs({"scale": 3}),
            scope="revision-3",
            resolve_producer=lambda: "3" * 64,
        )
    release.set()
    ledger.close()

    assert {producer for producer, _values, _count in repository.records} == {
        "0" * 64,
        "1" * 64,
        "2" * 64,
    }


def test_oversized_observation_is_rejected_without_poisoning_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = ObservedInputs({"scale": "valid"})
    oversized = ObservedInputs({"scale": "x" * 100})
    monkeypatch.setattr(queue_module, "MAX_OBSERVATION_BYTES", valid.byte_count)
    repository = _Repository()
    ledger = _ledger(_source(tmp_path), repository)

    with pytest.raises(ObservationRejectedError, match="byte limit"):
        ledger.record(oversized, producer_sha256="a" * 64)
    ledger.record(valid, producer_sha256="a" * 64)
    ledger.close()

    assert repository.records == [("a" * 64, {"scale": "valid"}, 1)]


def test_repository_specific_limit_advances_rejected_occurrence_and_continues(
    tmp_path: Path,
) -> None:
    producer = "a" * 64
    limits = RepositoryLimits(observation_bytes=32)
    with ExportRepository.open(tmp_path / "repository", limits=limits) as repository:
        ledger = ObservationLedger(_source(tmp_path), repository=repository)
        ledger.record(
            ObservedInputs({"scale": "x" * 40}),
            producer_sha256=producer,
        )
        ledger.record(ObservedInputs({"scale": 1}), producer_sha256=producer)
        ledger.close()

        raw = observation_repository(repository)
        assert raw.revision(producer) == 2
        observed = raw.observations(
            producer_sha256=producer,
            inputs=("scale",),
        )
        assert len(observed) == 1
        assert observed[0].revision == 2
        assert observed[0].values == {"scale": 1}


def test_repository_busy_failure_retries_then_succeeds(tmp_path: Path) -> None:
    class Repository(_Repository):
        attempts = 0

        def record(
            self,
            *,
            producer_sha256: str,
            values: Mapping[str, object],
            occurrences: int = 1,
        ) -> object:
            self.attempts += 1
            if self.attempts < 3:
                raise RepositoryBusyError("busy")
            return super().record(
                producer_sha256=producer_sha256,
                values=values,
                occurrences=occurrences,
            )

    repository = Repository()
    ledger = _ledger(_source(tmp_path), repository)
    ledger.record(ObservedInputs({"scale": 1}), producer_sha256="a" * 64)
    ledger.close()

    assert repository.attempts == 3
    assert len(repository.records) == 1


def test_repository_unavailable_failure_is_terminal(tmp_path: Path) -> None:
    failure = RepositoryUnavailableError("repository unavailable")

    class Repository(_Repository):
        attempts = 0

        def record(
            self,
            *,
            producer_sha256: str,
            values: Mapping[str, object],
            occurrences: int = 1,
        ) -> object:
            del producer_sha256, values, occurrences
            self.attempts += 1
            raise failure

    repository = Repository()
    ledger = _ledger(_source(tmp_path), repository)
    ledger.record(ObservedInputs({"scale": 1}), producer_sha256="a" * 64)

    with pytest.raises(ObservationPersistenceError) as raised:
        ledger.close()

    assert raised.value.__cause__ is failure
    assert repository.attempts == 1


def test_terminal_failure_is_replayed_by_flush_close_and_record(tmp_path: Path) -> None:
    failure = ValueError("invalid observation")

    class Repository(_Repository):
        def record(
            self,
            *,
            producer_sha256: str,
            values: Mapping[str, object],
            occurrences: int = 1,
        ) -> object:
            del producer_sha256, values, occurrences
            raise failure

    ledger = _ledger(_source(tmp_path), Repository())
    ledger.record(ObservedInputs({"scale": 1}), producer_sha256="a" * 64)

    causes: list[BaseException | None] = []
    for operation in (
        ledger.flush,
        ledger.close,
        lambda: ledger.record(ObservedInputs({"scale": 2})),
    ):
        with pytest.raises(ObservationPersistenceError) as raised:
            operation()
        causes.append(raised.value.__cause__)

    assert causes == [failure, failure, failure]


def test_owned_repository_close_failure_is_stable(tmp_path: Path) -> None:
    failure = ValueError("close failed")

    class Repository(_Repository):
        def close(self) -> None:
            self.closed = True
            raise failure

    ledger = _ledger(_source(tmp_path), Repository())
    ledger.record(ObservedInputs({"scale": 1}), producer_sha256="a" * 64)
    ledger.flush()

    causes: list[BaseException | None] = []
    for _attempt in range(2):
        with pytest.raises(ObservationPersistenceError) as raised:
            ledger.close()
        causes.append(raised.value.__cause__)

    assert causes == [failure, failure]


def test_source_identity_is_cached_by_safe_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    repository = _Repository()
    calls: list[str] = []

    def identify(path: Path) -> str:
        value = path.read_text(encoding="utf-8")
        calls.append(value)
        return "a" * 64 if value == "one" else "b" * 64

    monkeypatch.setattr(source_module, "identify_producer", identify)
    ledger = _ledger(source, repository)
    ledger.record(ObservedInputs({"scale": 1}))
    ledger.record(ObservedInputs({"scale": 2}))
    ledger.flush()
    source.write_text("second revision", encoding="utf-8")
    ledger.record(ObservedInputs({"scale": 3}))
    ledger.close()

    assert calls == ["one", "second revision"]
    assert [producer for producer, _values, _occurrences in repository.records] == [
        "a" * 64,
        "a" * 64,
        "b" * 64,
    ]


def test_source_change_during_identity_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    writes = 0

    def identify(path: Path) -> str:
        nonlocal writes
        writes += 1
        path.write_text(f"changed-{writes}", encoding="utf-8")
        return "a" * 64

    monkeypatch.setattr(source_module, "identify_producer", identify)
    ledger = _ledger(source, _Repository())

    with pytest.raises(OSError, match="changed"):
        ledger.record(ObservedInputs({"scale": 1}))
    ledger.close()
