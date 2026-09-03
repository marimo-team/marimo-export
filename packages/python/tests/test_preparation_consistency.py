from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from marimo_export._repository.preparation import (
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export._services.capture_export import capture_session
from marimo_export._services.identity import ProducerIdentity
from marimo_export._services.plan_export import (
    preflight_plan,
)
from marimo_export._services.plan_wire import decode_plan_wire
from marimo_export._services.prepare_export import prepare
from marimo_export.errors import ExecutionError
from marimo_export.planning import output_plan_sha256
from marimo_export.repository import (
    ExportRepository,
)
from marimo_export.result import CacheSummary
from marimo_export.spec import ExportSpec
from marimo_export.wire import state_fingerprint
from preparation_test_support import (
    _BorrowedSession,
    _digest,
    _FakeProducer,
    _identity_notebook,
    _index,
    _install_state,
    _plan_wire,
    _preflight,
    _producer,
    _spec,
    _spec_sha256,
)


def test_preparation_rejects_capture_from_another_producer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    preflight = _preflight(tmp_path / "notebook.py", spec)
    other = ProducerIdentity(
        source=preflight.producer.source,
        filename=preflight.producer.filename,
        source_sha256=preflight.producer.source_sha256,
        document_sha256=preflight.producer.document_sha256,
        producer_sha256=_digest("other-producer"),
        marimo_version="0.24.0",
        marimo_export_version="0.0.0",
        implementation_sha256=_digest("other-implementation"),
        environment_sha256=_digest("other-environment"),
    )
    fake = _FakeProducer(spec, preflight.producer, capture_producer=other)
    repository = ExportRepository.open(tmp_path / "repository")
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.require_preflight_current",
        lambda _preflight: None,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *_args, **_kwargs: fake,
    )

    with pytest.raises(ExecutionError, match="does not match"):
        prepare(spec=spec, source=tmp_path / "notebook.py", repository=repository)

    assert preparation_repository(repository).current(preflight.repository_identity) is None
    repository.close()


def test_source_guard_failure_preserves_current_generation_pointer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    preflight = _preflight(tmp_path / "notebook.py", spec)
    repository = ExportRepository.open(tmp_path / "repository")

    def source_changed() -> None:
        raise ExecutionError("source changed", code="notebook_changed")

    fake = _FakeProducer(
        spec,
        preflight.producer,
        commit_guard=source_changed,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *_args, **_kwargs: fake,
    )

    with pytest.raises(ExecutionError, match="source changed"):
        prepare(spec=spec, source=tmp_path / "notebook.py", repository=repository)

    assert preparation_repository(repository).current(preflight.repository_identity) is None
    repository.close()


def test_owned_capture_rejects_dependency_drift_before_state_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    notebook = tmp_path / "notebook.py"
    dependency = tmp_path / "dependency.py"
    _identity_notebook(notebook)
    dependency.write_text("VALUE = 1\n", encoding="utf-8")
    preflight = preflight_plan(notebook, spec)
    producer = _FakeProducer(
        spec,
        preflight.producer,
        on_capture=lambda: dependency.write_text("VALUE = 2\n", encoding="utf-8"),
    )
    repository = ExportRepository.open(tmp_path / "repository")
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *_args, **_kwargs: producer,
    )

    with pytest.raises(ExecutionError, match="changed"):
        prepare(spec=spec, source=notebook, repository=repository)

    storage = preparation_repository(repository)
    assert storage.current(preflight.repository_identity) is None
    states = storage.lookup_prepared_states(
        producer_sha256=preflight.producer.producer_sha256,
        output_plan_sha256=preflight.repository_identity.output_plan_sha256,
        state_fingerprints=(state_fingerprint({"choice": "A"}),),
    )
    assert states == {}
    repository.close()


def test_owned_capture_rejects_dependency_drift_before_generation_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    notebook = tmp_path / "notebook.py"
    dependency = tmp_path / "dependency.py"
    _identity_notebook(notebook)
    dependency.write_text("VALUE = 1\n", encoding="utf-8")
    preflight = preflight_plan(notebook, spec)
    guard_calls = 0

    def drift_on_generation() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            dependency.write_text("VALUE = 2\n", encoding="utf-8")

    producer = _FakeProducer(
        spec,
        preflight.producer,
        commit_guard=drift_on_generation,
    )
    repository = ExportRepository.open(tmp_path / "repository")
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *_args, **_kwargs: producer,
    )

    with pytest.raises(ExecutionError, match="changed"):
        prepare(spec=spec, source=notebook, repository=repository)

    assert guard_calls == 2
    storage = preparation_repository(repository)
    assert storage.current(preflight.repository_identity) is None
    states = storage.lookup_prepared_states(
        producer_sha256=preflight.producer.producer_sha256,
        output_plan_sha256=preflight.repository_identity.output_plan_sha256,
        state_fingerprints=(state_fingerprint({"choice": "A"}),),
    )
    assert set(states) == {state_fingerprint({"choice": "A"})}
    for state in states.values():
        state.close()
    repository.close()


def test_borrowed_capture_rejects_runtime_drift_before_state_commit(
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    producer = _producer(tmp_path / "notebook.py")
    changed = ProducerIdentity(
        source=producer.source,
        filename=producer.filename,
        source_sha256=producer.source_sha256,
        document_sha256=producer.document_sha256,
        producer_sha256=_digest("changed-producer"),
        marimo_version=producer.marimo_version,
        marimo_export_version=producer.marimo_export_version,
        implementation_sha256=producer.implementation_sha256,
        environment_sha256=_digest("changed-environment"),
    )
    session = _BorrowedSession(
        _plan_wire(spec, producer),
        SimpleNamespace(
            index=_index(spec, producer),
            assets={},
            output_cache=CacheSummary(hits=0, misses=1),
            notebook_cache=CacheSummary(hits=0, misses=1),
            capture_seconds=0.01,
        ),
    )
    session.on_capture = lambda: setattr(session, "wire", _plan_wire(spec, changed))
    repository = ExportRepository.open(tmp_path / "repository")

    with pytest.raises(ExecutionError, match="live producer changed"):
        capture_session(session, spec=spec, repository=repository)

    identity = RepositoryIdentity(
        producer_sha256=producer.producer_sha256,
        output_plan_sha256=output_plan_sha256(spec),
        spec_sha256=_spec_sha256(spec),
    )
    storage = preparation_repository(repository)
    assert storage.current(identity) is None
    states = storage.lookup_prepared_states(
        producer_sha256=producer.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        state_fingerprints=(state_fingerprint({"choice": "A"}),),
    )
    assert states == {}
    repository.close()


def test_borrowed_capture_rejects_runtime_drift_before_generation_commit(
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    producer = _producer(tmp_path / "notebook.py")
    changed = ProducerIdentity(
        source=producer.source,
        filename=producer.filename,
        source_sha256=producer.source_sha256,
        document_sha256=producer.document_sha256,
        producer_sha256=_digest("changed-producer"),
        marimo_version=producer.marimo_version,
        marimo_export_version=producer.marimo_export_version,
        implementation_sha256=producer.implementation_sha256,
        environment_sha256=_digest("changed-environment"),
    )
    original_wire = _plan_wire(spec, producer)
    changed_wire = _plan_wire(spec, changed)

    class ChangingSession(_BorrowedSession):
        plan_calls = 0

        def _plan(self, spec: ExportSpec):
            del spec
            self.plan_calls += 1
            return original_wire if self.plan_calls <= 3 else changed_wire

    session = ChangingSession(
        original_wire,
        SimpleNamespace(
            index=_index(spec, producer),
            assets={},
            output_cache=CacheSummary(hits=0, misses=1),
            notebook_cache=CacheSummary(hits=0, misses=1),
            capture_seconds=0.01,
        ),
    )
    repository = ExportRepository.open(tmp_path / "repository")

    with pytest.raises(ExecutionError, match="live producer changed"):
        capture_session(session, spec=spec, repository=repository)

    assert session.plan_calls == 4
    identity = RepositoryIdentity(
        producer_sha256=producer.producer_sha256,
        output_plan_sha256=output_plan_sha256(spec),
        spec_sha256=_spec_sha256(spec),
    )
    storage = preparation_repository(repository)
    assert storage.current(identity) is None
    states = storage.lookup_prepared_states(
        producer_sha256=producer.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        state_fingerprints=(state_fingerprint({"choice": "A"}),),
    )
    assert set(states) == {state_fingerprint({"choice": "A"})}
    for state in states.values():
        state.close()
    repository.close()


def test_export_commits_only_the_observation_revision_used_by_its_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec()
    preflight = _preflight(tmp_path / "notebook.py", spec)
    observation_plan = decode_plan_wire(
        _plan_wire(spec, preflight.producer),
        spec,
        preflight.producer,
    )
    repository = ExportRepository.open(tmp_path / "repository")
    repository.record_observation(observation_plan, {"choice": "A"})
    baseline = _install_state(repository, preflight.repository_identity, {"choice": "A"})
    baseline.close()
    fake = _FakeProducer(
        spec,
        preflight.producer,
        on_capture=lambda: repository.record_observation(
            observation_plan,
            {"choice": "B"},
        ),
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *_args, **_kwargs: fake,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.require_preflight_current",
        lambda _preflight: None,
    )

    prepared = prepare(spec=spec, source=tmp_path / "notebook.py", repository=repository)
    current = preparation_repository(repository).current(preflight.repository_identity)
    assert current is not None
    assert prepared.plan.observation_revision == 1
    assert current.captured_observation_revision == 1
    assert repository.observation_revision(observation_plan) == 2
    current.close()
    prepared.close()
    repository.close()


def test_prepare_replans_progress_from_the_second_live_state_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec()
    preflight = _preflight(tmp_path / "notebook.py", spec)
    repository = ExportRepository.open(tmp_path / "repository")
    producer = _FakeProducer(spec, preflight.producer)
    storage = preparation_repository(repository)
    original_lookup = storage.lookup_prepared_states
    calls = 0

    class VanishedState:
        def close(self) -> None:
            return None

    def drifting_lookup(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {state_fingerprint({"choice": "A"}): VanishedState()}
        return original_lookup(**kwargs)

    monkeypatch.setattr(storage, "lookup_prepared_states", drifting_lookup)
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *_args, **_kwargs: producer,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.require_preflight_current",
        lambda _preflight: None,
    )
    events = []

    prepared = prepare(
        spec=spec,
        source=tmp_path / "notebook.py",
        repository=repository,
        progress=events.append,
    )

    plan_ready = next(event for event in events if event.kind == "plan_ready")
    state_events = [event for event in events if event.kind == "state_finished"]
    assert plan_ready.completed == 0
    assert plan_ready.total == 2
    assert len(state_events) == 2
    assert all(event.completed <= event.total for event in state_events)
    assert producer.captured == ["baseline", "other"]
    prepared.close()
    repository.close()
