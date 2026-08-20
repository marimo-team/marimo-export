from __future__ import annotations

import inspect
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

import marimo_export._repository.paths as paths_module
import pytest
from marimo_export._json import JsonValue
from marimo_export._repository.observations import observation_repository
from marimo_export._repository.preparation import (
    PreparedState,
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export.descriptors import Provenance, ScalarDescriptor
from marimo_export.index import (
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    StateEntry,
)
from marimo_export.planning import ExportPlan, PlannedState, export_plan_identity
from marimo_export.repository import (
    ExportRepository,
    RepositoryLimits,
)
from marimo_export.wire import state_fingerprint


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _identity(name: str = "one") -> RepositoryIdentity:
    return RepositoryIdentity(
        producer_sha256=_digest(f"producer-{name}"),
        output_plan_sha256=_digest("outputs"),
        spec_sha256=_digest(f"spec-{name}"),
    )


def _prepare_state(
    repository: ExportRepository,
    identity: RepositoryIdentity,
    values: Mapping[str, JsonValue],
) -> PreparedState:
    fingerprint = state_fingerprint(values)
    preparation = preparation_repository(repository)
    with (
        preparation.reserve_preparation(identity),
        preparation.stage_prepared_state(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprint=fingerprint,
        ) as staged,
    ):
        (staged.path / "output.json").write_text("{}", encoding="utf-8")
        return staged.commit(metadata={"inputs": values, "outputs": {}})


def _write_index(
    path: Path,
    identity: RepositoryIdentity,
    fingerprint: str,
    inputs: Mapping[str, JsonValue],
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
        inputs=tuple(sorted(inputs)),
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
    (path / "index.json").write_bytes(index.to_bytes())


def test_observations_are_canonical_durable_and_projectable(tmp_path: Path) -> None:
    producer = _digest("producer")
    root = tmp_path / "repository"
    with ExportRepository.open(root) as repository:
        observations = observation_repository(repository)
        first = observations.record(
            producer_sha256=producer,
            values={"dataset": "moons", "neighbors": 3},
        )
        second = observations.record(
            producer_sha256=producer,
            values={"dataset": "circles", "neighbors": 4},
            occurrences=2,
        )
        assert first.revision == 1
        assert second.revision == 3
        assert observations.revision(producer) == 3
        assert {
            state.values["neighbors"]
            for state in preparation_repository(repository).observations(
                producer_sha256=producer,
                inputs=("dataset", "neighbors"),
            )
        } == {3, 4}

        snapshot = preparation_repository(repository).observation_snapshot(producer)
        projected = snapshot.observations(("dataset",))
        assert {state.values["dataset"] for state in projected} == {"moons", "circles"}
        latest = snapshot.latest(("dataset",))
        assert latest is not None
        assert latest.values == {"dataset": "circles"}

    with ExportRepository.open(root) as restarted:
        latest = preparation_repository(restarted).latest_observation(
            producer_sha256=producer,
            inputs=("dataset", "neighbors"),
        )
        assert latest is not None
        assert latest.values["neighbors"] == 4
        assert observation_repository(restarted).clear(producer) == 2
        assert (
            preparation_repository(restarted).observations(
                producer_sha256=producer,
                inputs=("dataset", "neighbors"),
            )
            == ()
        )


def test_prepared_states_are_reused_across_exact_export_specs(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    first_identity = _identity("first")
    second_identity = RepositoryIdentity(
        producer_sha256=first_identity.producer_sha256,
        output_plan_sha256=first_identity.output_plan_sha256,
        spec_sha256=_digest("changed-default-and-aliases"),
    )
    with ExportRepository.open(root) as repository:
        state = _prepare_state(repository, first_identity, {"neighbors": 3})
        preparation = preparation_repository(repository)
        reusable = preparation.lookup_prepared_states(
            producer_sha256=second_identity.producer_sha256,
            output_plan_sha256=second_identity.output_plan_sha256,
            state_fingerprints=(state.state_fingerprint,),
        )
        assert tuple(reusable) == (state.state_fingerprint,)
        assert reusable[state.state_fingerprint].asset("output.json") is not None

        with (
            preparation.reserve_preparation(first_identity),
            preparation.stage_export(first_identity) as staged,
        ):
            _write_index(
                staged.path,
                first_identity,
                state.state_fingerprint,
                {"neighbors": 3},
            )
            export = staged.commit(states=(state,), captured_observation_revision=0)
        exact = preparation.current(first_identity)
        assert exact is not None
        exact.close()
        assert preparation.current(second_identity) is None
        assert export.state_fingerprints == (state.state_fingerprint,)
        export.close()
        for handle in reusable.values():
            handle.close()
        state.close()

    with ExportRepository.open(root) as restarted:
        reused = preparation_repository(restarted).current(first_identity)
        assert reused is not None
        assert reused.asset("index.json") is not None
        reused.close()


def test_export_commit_requires_current_prepared_state_handles(tmp_path: Path) -> None:
    identity = _identity()
    with ExportRepository.open(tmp_path / "repository") as repository:
        state = _prepare_state(repository, identity, {"neighbors": 3})
        state.close()
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_export(identity) as staged,
        ):
            _write_index(
                staged.path,
                identity,
                state.state_fingerprint,
                {"neighbors": 3},
            )
            with pytest.raises(TypeError, match="live PreparedState"):
                staged.commit(states=(state,), captured_observation_revision=0)


def test_prepared_compares_the_complete_resolved_relation(tmp_path: Path) -> None:
    identity = _identity("relation")
    inputs = {"neighbors": 3}
    fingerprint = state_fingerprint(inputs)
    with ExportRepository.open(tmp_path / "repository") as repository:
        state = _prepare_state(repository, identity, inputs)
        preparation = preparation_repository(repository)
        with (
            preparation.reserve_preparation(identity),
            preparation.stage_export(identity) as staged,
        ):
            _write_index(staged.path, identity, fingerprint, inputs)
            export = staged.commit(states=(state,), captured_observation_revision=0)
        plan = ExportPlan(
            document_sha256=_digest("document"),
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            spec_sha256=identity.spec_sha256,
            default_alias="baseline",
            default_fingerprint=fingerprint,
            inputs=("neighbors",),
            states=(
                PlannedState(
                    aliases=("baseline",),
                    inputs=inputs,
                    fingerprint=fingerprint,
                ),
            ),
            outputs=("result",),
            reusable_states=(fingerprint,),
            missing_states=(),
            observation_revision=0,
            observations=(),
            exact_reuse=True,
        )
        repository.record_observation(plan, inputs)
        assert repository.observation_revision(plan) == 1
        assert repository.observations(plan)[0].values == inputs
        prepared = repository.prepared(plan)
        assert prepared is not None
        assert prepared.plan.exact_reuse
        prepared.close()
        changed_inputs = {"neighbors": 4}
        changed_fingerprint = state_fingerprint(changed_inputs)
        changed = ExportPlan(
            document_sha256=plan.document_sha256,
            producer_sha256=plan.producer_sha256,
            output_plan_sha256=plan.output_plan_sha256,
            spec_sha256=plan.spec_sha256,
            default_alias="baseline",
            default_fingerprint=changed_fingerprint,
            inputs=plan.inputs,
            states=(
                PlannedState(
                    aliases=("baseline",),
                    inputs=changed_inputs,
                    fingerprint=changed_fingerprint,
                ),
            ),
            outputs=plan.outputs,
            reusable_states=(changed_fingerprint,),
            missing_states=(),
            observation_revision=0,
            observations=(),
            exact_reuse=True,
        )
        assert repository.prepared(changed) is None
        assert repository.clear_observations(plan) == 1
        export.close()
        state.close()


def test_observation_limits_keep_recent_complete_vectors(tmp_path: Path) -> None:
    limits = RepositoryLimits(
        observations_per_producer=2,
        observation_relation_bytes=1024,
    )
    producer = _digest("producer")
    with ExportRepository.open(tmp_path / "repository", limits=limits) as repository:
        for value in range(4):
            observation_repository(repository).record(
                producer_sha256=producer,
                values={"value": value},
            )
        states = preparation_repository(repository).observations(
            producer_sha256=producer,
            inputs=("value",),
        )
        assert {state.values["value"] for state in states} == {2, 3}


def test_staging_cleanup_is_idempotent(tmp_path: Path) -> None:
    identity = _identity()
    with ExportRepository.open(tmp_path / "repository") as repository:
        preparation = preparation_repository(repository)
        with preparation.reserve_preparation(identity):
            staged = preparation.stage_prepared_state(
                producer_sha256=identity.producer_sha256,
                output_plan_sha256=identity.output_plan_sha256,
                state_fingerprint=_digest("state"),
            )
            path = staged.path
            staged.close()
            staged.close()
            assert not path.exists()


def test_repository_identity_matches_public_export_plan_identity() -> None:
    identity = _identity("plan")
    inputs = {"value": 1}
    fingerprint = state_fingerprint(inputs)
    plan = ExportPlan(
        document_sha256=_digest("document"),
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        spec_sha256=identity.spec_sha256,
        default_alias="baseline",
        default_fingerprint=fingerprint,
        inputs=("value",),
        states=(
            PlannedState(
                aliases=("baseline",),
                inputs=inputs,
                fingerprint=fingerprint,
            ),
        ),
        outputs=("result",),
        reusable_states=(),
        missing_states=(fingerprint,),
    )
    expected = export_plan_identity(
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        spec_sha256=identity.spec_sha256,
    )
    assert identity.key == plan.identity == expected


def test_public_repository_surface_is_plan_shaped() -> None:
    public = {name for name in vars(ExportRepository) if not name.startswith("_")}
    assert public == {
        "clear_observations",
        "close",
        "default_path",
        "observation_revision",
        "observations",
        "open",
        "prepared",
        "prune",
        "record_observation",
        "status",
    }
    assert "verifier" not in inspect.signature(ExportRepository.open).parameters


def test_default_path_is_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "configured"
    monkeypatch.setenv("MARIMO_EXPORT_REPOSITORY", str(configured))
    assert ExportRepository.default_path() == configured.absolute()
    assert not configured.exists()


def test_default_path_honors_xdg_and_windows_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MARIMO_EXPORT_REPOSITORY", raising=False)
    native_path = type(tmp_path)
    monkeypatch.setattr(paths_module, "Path", native_path)
    xdg = tmp_path / "xdg"
    monkeypatch.setattr(paths_module.sys, "platform", "linux")
    monkeypatch.setattr(paths_module.os, "name", "posix")
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    assert ExportRepository.default_path() == (xdg / "marimo-export" / "repository")
    assert not xdg.exists()

    local = tmp_path / "local"
    monkeypatch.setattr(paths_module.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert ExportRepository.default_path() == (local / "marimo-export" / "repository")
    assert not local.exists()


def test_public_repository_records_have_stable_dicts(tmp_path: Path) -> None:
    producer = _digest("records")
    with ExportRepository.open(tmp_path / "repository") as repository:
        observed = observation_repository(repository).record(
            producer_sha256=producer,
            values={"value": 1},
        )
        assert observed.to_dict() == {
            "producer_sha256": producer,
            "revision": 1,
            "fingerprint": observed.fingerprint,
            "values": {"value": 1},
        }
        status = repository.status()
        assert status.to_dict()["path"] == str(repository.path)
        dry_run = repository.prune(dry_run=True)
        assert dry_run.to_dict()["dry_run"] is True
