from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from marimo_export._json import JsonObject
from marimo_export._repository.preparation import (
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export._services.plan_wire import decode_plan_wire
from marimo_export._services.prepare_export import prepare
from marimo_export.errors import ExecutionError, IntegrityError
from marimo_export.planning import PlannedState
from marimo_export.prepared import PreparedExport, _prepared_manifest
from marimo_export.progress import CacheActivity
from marimo_export.repository import (
    ExportRepository,
)
from marimo_export.wire import parse_canonical_json, state_fingerprint
from preparation_test_support import (
    _FakeProducer,
    _install_export,
    _install_state,
    _plan_wire,
    _preflight,
    _producer,
    _spec,
)


def test_exact_prepare_reuses_without_opening_a_notebook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec()
    preflight = _preflight(tmp_path / "notebook.py", spec)
    repository = ExportRepository.open(tmp_path / "repository")
    _install_export(repository, preflight.repository_identity, spec, preflight.producer)
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
        lambda *_args, **_kwargs: pytest.fail("exact reuse started a notebook"),
    )
    events = []

    prepared = prepare(
        spec=spec, source=tmp_path / "notebook.py", repository=repository, progress=events.append
    )

    assert prepared.reused
    assert prepared.prepared_states == ()
    assert prepared.reused_states == prepared.plan.state_fingerprints
    assert [event.kind for event in events] == ["plan_ready", "prepared_reused"]
    assert prepared.to_dict() == {
        "identity": prepared.identity,
        "path": str(prepared.path),
        "reused": True,
        "plan": prepared.plan.to_dict(),
        "prepared_states": [],
        "reused_states": list(prepared.reused_states),
        "cache_activity": CacheActivity().to_dict(),
    }
    prepared.close()
    assert repository.status().generations == 1
    repository.close()


@pytest.mark.parametrize("after_reservation", [False, True])
def test_exact_prepare_closes_artifact_when_preflight_guard_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    after_reservation: bool,
) -> None:
    class Artifact:
        closed = False

        def close(self) -> None:
            self.closed = True

    artifact = Artifact()
    spec = _spec(states={"baseline": {"choice": "A"}})
    preflight = _preflight(tmp_path / "notebook.py", spec)
    repository = ExportRepository.open(tmp_path / "repository")
    storage = preparation_repository(repository)
    lookups = 0

    def current(_identity):
        nonlocal lookups
        lookups += 1
        if after_reservation and lookups == 1:
            return None
        return artifact

    def changed(_preflight) -> None:
        raise ExecutionError("producer changed", code="notebook_changed")

    monkeypatch.setattr(storage, "current", current)
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.require_preflight_current",
        changed,
    )

    with pytest.raises(ExecutionError, match="producer changed"):
        prepare(spec=spec, source=tmp_path / "notebook.py", repository=repository)

    assert artifact.closed
    assert lookups == (2 if after_reservation else 1)
    repository.close()


def test_exact_prepare_closes_artifact_when_plan_resolution_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Artifact:
        close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    artifact = Artifact()
    spec = _spec(states={"baseline": {"choice": "A"}})
    preflight = _preflight(tmp_path / "notebook.py", spec)
    repository = ExportRepository.open(tmp_path / "repository")
    storage = preparation_repository(repository)

    def fail_plan(*_args: object) -> None:
        raise IntegrityError("prepared export plan could not be resolved")

    monkeypatch.setattr(storage, "current", lambda _identity: artifact)
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.require_preflight_current",
        lambda _preflight: None,
    )
    monkeypatch.setattr(
        "marimo_export._services.preparation_support.plan_from_artifact",
        fail_plan,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *_args, **_kwargs: pytest.fail("exact reuse started a notebook"),
    )

    with pytest.raises(IntegrityError, match="plan could not be resolved"):
        prepare(spec=spec, source=tmp_path / "notebook.py", repository=repository)

    assert artifact.close_calls == 1
    assert repository.status().generations == 0
    repository.close()


def test_shared_prepared_manifest_fixture_matches_python_emission() -> None:
    fixture = Path(__file__).parents[3] / "tests/fixtures/export/prepared-manifest.json"
    value = parse_canonical_json(
        fixture.read_bytes().removesuffix(b"\n"),
        "prepared manifest fixture",
    )
    assert value == _prepared_manifest(
        instance="1" * 64,
        export_url="./publication/",
        inputs={"choice": "A"},
        state_fingerprint="2" * 64,
        refresh_interval_ms=1000,
    )


def test_repository_prepared_requires_the_complete_plan_relation(tmp_path: Path) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    producer = _producer(tmp_path / "notebook.py")
    plan = decode_plan_wire(_plan_wire(spec, producer), spec, producer)
    identity = RepositoryIdentity(
        producer_sha256=producer.producer_sha256,
        output_plan_sha256=plan.output_plan_sha256,
        spec_sha256=plan.spec_sha256,
    )
    repository = ExportRepository.open(tmp_path / "repository")
    _install_export(repository, identity, spec, producer)

    assert repository.prepared(plan)

    changed_inputs: JsonObject = {"choice": "B"}
    changed_fingerprint = state_fingerprint(changed_inputs)
    changed = replace(
        plan,
        default_fingerprint=changed_fingerprint,
        states=(
            PlannedState(
                aliases=("baseline",),
                inputs=changed_inputs,
                fingerprint=changed_fingerprint,
            ),
        ),
        reusable_states=(),
        missing_states=(changed_fingerprint,),
    )
    assert not repository.prepared(changed)
    repository.close()


def test_repository_prepared_closes_artifact_when_handle_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Artifact:
        path = tmp_path / "artifact"
        close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    artifact = Artifact()
    spec = _spec(states={"baseline": {"choice": "A"}})
    producer = _producer(tmp_path / "notebook.py")
    plan = decode_plan_wire(_plan_wire(spec, producer), spec, producer)
    repository = ExportRepository.open(tmp_path / "repository")

    def fail_create(_cls: type[PreparedExport], **_kwargs: object) -> PreparedExport:
        raise IntegrityError("prepared export handle could not be created")

    monkeypatch.setattr(
        type(repository._artifacts),
        "current",
        lambda _artifacts, _identity: artifact,
    )
    monkeypatch.setattr(
        "marimo_export.repository._artifact_matches_plan",
        lambda _path, _plan: True,
    )
    monkeypatch.setattr(PreparedExport, "_create", classmethod(fail_create))

    with pytest.raises(IntegrityError, match="handle could not be created"):
        repository.prepared(plan)

    assert artifact.close_calls == 1
    assert repository.status().generations == 0
    repository.close()


def test_partial_reuse_executes_only_the_missing_state_and_then_reuses_exactly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec()
    preflight = _preflight(tmp_path / "notebook.py", spec)
    repository = ExportRepository.open(tmp_path / "repository")
    baseline = _install_state(repository, preflight.repository_identity, {"choice": "A"})
    baseline.close()
    producer = _FakeProducer(spec, preflight.producer)
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
        lambda *_args, **_kwargs: producer,
    )

    prepared = prepare(spec=spec, source=tmp_path / "notebook.py", repository=repository)

    other = state_fingerprint({"choice": "B"})
    baseline_fingerprint = state_fingerprint({"choice": "A"})
    assert producer.captured == ["other"]
    assert prepared.prepared_states == (other,)
    assert prepared.reused_states == (baseline_fingerprint,)
    assert prepared.cache_activity.projection_misses == 1
    opened = prepared.open()
    assert prepared.plan.matches(opened)
    assert opened.default_state.aliases == ("baseline",)
    manifest = prepared.manifest("./export/")
    assert set(manifest) == {
        "schema",
        "instance",
        "export_url",
        "inputs",
        "state_fingerprint",
    }
    assert manifest["state_fingerprint"] == baseline_fingerprint
    assert manifest["instance"] == prepared.identity

    destination = tmp_path / "dist"
    result = prepared.write(destination)
    assert result.identity == prepared.identity
    assert result.verification.states == 2
    prepared.close()

    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *_args, **_kwargs: pytest.fail("repeated prepare started a notebook"),
    )
    repeated = prepare(spec=spec, source=tmp_path / "notebook.py", repository=repository)
    assert repeated.reused
    repeated.close()
    repository.close()
