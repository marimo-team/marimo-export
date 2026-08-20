from __future__ import annotations

import stat
from hashlib import sha256
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import msgspec
import pytest
from marimo_export._json import JsonObject
from marimo_export._repository.preparation import (
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export._services.capture_export import capture_session
from marimo_export._services.prepare_export import prepare
from marimo_export.descriptors import (
    BLOB_ASSET_CODEC,
    AssetRef,
    BlobAssetDescriptor,
    Provenance,
    asset_path,
)
from marimo_export.errors import ExecutionError, IntegrityError
from marimo_export.index import (
    ExportIndex,
    NotebookProvenance,
    StateEntry,
)
from marimo_export.planning import output_plan_sha256
from marimo_export.repository import (
    ExportRepository,
    RepositoryError,
    RepositoryLimits,
)
from marimo_export.result import CacheSummary
from marimo_export.spec import ExportSpec, OutputSpec
from marimo_export.wire import state_fingerprint
from preparation_test_support import (
    _BorrowedSession,
    _entry,
    _FakeProducer,
    _install_export,
    _install_export_handle,
    _install_state,
    _preflight,
    _producer,
    _spec,
    _spec_sha256,
)


def test_implicit_repository_lives_until_prepared_export_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
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
        "marimo_export._services.prepare_export.ExportRepository.open",
        lambda: repository,
    )

    prepared = prepare(spec=spec, source=tmp_path / "notebook.py")
    assert repository.status().generations == 1
    prepared.close()
    with pytest.raises(RuntimeError, match="closed"):
        repository.status()


def test_prepared_asset_outlives_parent_and_blocks_prune_until_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    limits = RepositoryLimits(
        retained_identities=1,
        retained_generations=1,
        retained_generations_per_identity=1,
    )
    repository = ExportRepository.open(tmp_path / "repository", limits=limits)
    first = _spec(states={"baseline": {"choice": "A"}})
    first_preflight = _preflight(tmp_path / "notebook.py", first)
    _install_export(
        repository, first_preflight.repository_identity, first, first_preflight.producer
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: first_preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.require_preflight_current",
        lambda _preflight: None,
    )
    prepared = prepare(spec=first, source=tmp_path / "notebook.py", repository=repository)
    with pytest.raises(RepositoryError, match="unavailable"):
        prepared.asset("../index.json")
    asset = prepared.asset("index.json")
    expected = asset.read_bytes()
    assert asset.path == prepared.path / "index.json"
    assert asset.size == len(expected)
    closing = Thread(target=prepared.close)
    closing.start()
    closing.join(timeout=5)
    assert not closing.is_alive()

    assert asset.read_bytes() == expected
    second = _spec(states={"baseline": {"choice": "B"}})
    second_preflight = _preflight(tmp_path / "notebook.py", second)
    second_artifact = _install_export_handle(
        repository,
        second_preflight.repository_identity,
        second,
        second_preflight.producer,
    )
    repository.prune()
    assert repository.status().generations == 2

    asset.close()
    asset.close()
    repository.prune()
    assert repository.status().generations == 1
    second_artifact.close()
    repository.close()


def test_prepared_handle_rejects_committed_index_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
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
    prepared = prepare(spec=spec, source=tmp_path / "notebook.py", repository=repository)
    index_path = prepared.path / "index.json"
    index_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    index_path.write_bytes(index_path.read_bytes() + b" ")

    with pytest.raises(IntegrityError, match="changed"):
        _ = prepared.path
    with pytest.raises(IntegrityError, match="changed"):
        prepared.asset("index.json")
    with pytest.raises(IntegrityError, match="changed"):
        prepared.open()

    prepared.close()
    repository.close()


def test_prepared_asset_rejects_declared_payload_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    preflight = _preflight(tmp_path / "notebook.py", spec)
    inputs: JsonObject = {"choice": "A"}
    fingerprint = state_fingerprint(inputs)
    payload = msgspec.msgpack.encode(
        {
            "data": b"prepared asset payload",
            "media_type": "application/octet-stream",
            "filename": "payload.bin",
            "metadata": {},
        }
    )
    digest = sha256(payload).hexdigest()
    relative = asset_path(BLOB_ASSET_CODEC, digest)
    descriptor = BlobAssetDescriptor(
        asset=AssetRef(sha256=digest, size=len(payload)),
        provenance=Provenance(python_type="builtins.bytes"),
        media_type="application/octet-stream",
        filename="payload.bin",
        metadata={},
    )
    entry = StateEntry(inputs=inputs, outputs={"answer": descriptor})
    index = ExportIndex(
        spec_sha256=preflight.repository_identity.spec_sha256,
        default_state=fingerprint,
        notebook=NotebookProvenance(
            filename=preflight.producer.filename,
            document_sha256=preflight.producer.document_sha256,
        ),
        producer=preflight.producer.provenance,
        inputs=("choice",),
        control_bindings={},
        outputs=("answer",),
        aliases={"baseline": fingerprint},
        states={fingerprint: entry},
    )
    repository = ExportRepository.open(tmp_path / "repository")
    storage = preparation_repository(repository)
    with storage.reserve_preparation(preflight.repository_identity):
        with storage.stage_prepared_state(
            producer_sha256=preflight.producer.producer_sha256,
            output_plan_sha256=preflight.repository_identity.output_plan_sha256,
            state_fingerprint=fingerprint,
        ) as staged_state:
            state_asset = staged_state.path / relative
            state_asset.parent.mkdir(parents=True)
            state_asset.write_bytes(payload)
            state = staged_state.commit(
                metadata={
                    "inputs": entry.inputs,
                    "outputs": {"answer": descriptor.to_value()},
                    "control_bindings": {},
                }
            )
        try:
            with storage.stage_export(preflight.repository_identity) as staged_export:
                (staged_export.path / "index.json").write_bytes(index.to_bytes())
                export_asset = staged_export.path / relative
                export_asset.parent.mkdir(parents=True)
                export_asset.write_bytes(payload)
                artifact = staged_export.commit(
                    states=(state,),
                    captured_observation_revision=0,
                )
            artifact.close()
        finally:
            state.close()
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.require_preflight_current",
        lambda _preflight: None,
    )
    prepared = prepare(spec=spec, source=tmp_path / "notebook.py", repository=repository)
    asset = prepared.asset(relative)
    assert asset.size == len(payload)
    assert asset.read_bytes() == payload

    payload_path = prepared.path / relative
    payload_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    payload_path.write_bytes(b"changed asset payload")
    with pytest.raises(IntegrityError, match="changed"):
        _ = asset.path
    with pytest.raises(IntegrityError, match="changed"):
        asset.read_bytes()
    asset.close()
    with pytest.raises(IntegrityError, match="changed"):
        prepared.asset(relative)

    prepared.close()
    repository.close()


def test_cancellation_preserves_previously_committed_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = _spec(states={"baseline": {"choice": "A"}})
    first_preflight = _preflight(tmp_path / "notebook.py", first)
    repository = ExportRepository.open(tmp_path / "repository")
    _install_export(
        repository, first_preflight.repository_identity, first, first_preflight.producer
    )
    second = _spec()
    second_preflight = _preflight(tmp_path / "notebook.py", second)
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: second_preflight,
    )

    with pytest.raises(ExecutionError, match="cancelled"):
        prepare(
            spec=second,
            source=tmp_path / "notebook.py",
            repository=repository,
            cancelled=lambda: True,
        )

    current = preparation_repository(repository).current(first_preflight.repository_identity)
    assert current is not None
    current.close()
    repository.close()


def test_cancellation_after_state_execution_skips_state_and_generation_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    preflight = _preflight(tmp_path / "notebook.py", spec)
    repository = ExportRepository.open(tmp_path / "repository")
    cancelled = False

    def cancel_after_capture() -> None:
        nonlocal cancelled
        cancelled = True

    producer = _FakeProducer(spec, preflight.producer, on_capture=cancel_after_capture)
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.preflight_plan",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *_args, **_kwargs: producer,
    )

    with pytest.raises(ExecutionError, match="cancelled"):
        prepare(
            spec=spec,
            source=tmp_path / "notebook.py",
            repository=repository,
            cancelled=lambda: cancelled,
        )

    storage = preparation_repository(repository)
    assert storage.current(preflight.repository_identity) is None
    prepared_states = storage.lookup_prepared_states(
        producer_sha256=preflight.producer.producer_sha256,
        output_plan_sha256=preflight.repository_identity.output_plan_sha256,
        state_fingerprints=(state_fingerprint({"choice": "A"}),),
    )
    assert prepared_states == {}
    repository.close()


def test_live_sparse_baseline_change_replaces_nonmatching_exact_artifact(tmp_path: Path) -> None:
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"answer": OutputSpec.value("answer")},
    )
    producer = _producer(tmp_path / "notebook.py")
    identity = RepositoryIdentity(
        producer_sha256=producer.producer_sha256,
        output_plan_sha256=output_plan_sha256(spec),
        spec_sha256=_spec_sha256(spec),
    )
    repository = ExportRepository.open(tmp_path / "repository")
    old_inputs: JsonObject = {"choice": "A"}
    old_state = _install_state(repository, identity, old_inputs)
    old_fingerprint = state_fingerprint(old_inputs)
    old_index = ExportIndex(
        spec_sha256=identity.spec_sha256,
        default_state=old_fingerprint,
        notebook=NotebookProvenance(
            filename=producer.filename,
            document_sha256=producer.document_sha256,
        ),
        producer=producer.provenance,
        inputs=("choice",),
        control_bindings={},
        outputs=("answer",),
        aliases={"baseline": old_fingerprint},
        states={old_fingerprint: _entry(old_inputs)},
    )
    storage = preparation_repository(repository)
    with storage.reserve_preparation(identity), storage.stage_export(identity) as staged:
        (staged.path / "index.json").write_bytes(old_index.to_bytes())
        old_artifact = staged.commit(states=(old_state,), captured_observation_revision=0)
    old_instance = old_artifact.instance
    old_artifact.close()
    old_state.close()

    new_inputs: JsonObject = {"choice": "B"}
    new_fingerprint = state_fingerprint(new_inputs)
    wire = {
        "default_alias": "baseline",
        "default_fingerprint": new_fingerprint,
        "document_sha256": producer.document_sha256,
        "environment_sha256": producer.environment_sha256,
        "filename": producer.filename,
        "implementation_sha256": producer.implementation_sha256,
        "inputs": ["choice"],
        "output_plan_sha256": identity.output_plan_sha256,
        "outputs": ["answer"],
        "producer": producer.provenance.to_value(),
        "producer_sha256": producer.producer_sha256,
        "source_sha256": producer.source_sha256,
        "spec_sha256": identity.spec_sha256,
        "states": [
            {
                "aliases": ["baseline"],
                "fingerprint": new_fingerprint,
                "inputs": new_inputs,
            }
        ],
    }
    new_index = ExportIndex(
        spec_sha256=identity.spec_sha256,
        default_state=new_fingerprint,
        notebook=NotebookProvenance(
            filename=producer.filename,
            document_sha256=producer.document_sha256,
        ),
        producer=producer.provenance,
        inputs=("choice",),
        control_bindings={},
        outputs=("answer",),
        aliases={"baseline": new_fingerprint},
        states={new_fingerprint: _entry(new_inputs)},
    )
    session = _BorrowedSession(
        wire,
        SimpleNamespace(
            index=new_index,
            assets={},
            output_cache=CacheSummary(hits=0, misses=1),
            notebook_cache=CacheSummary(hits=0, misses=1),
            capture_seconds=0.01,
        ),
    )

    prepared = capture_session(session, spec=spec, repository=repository)

    assert prepared.reused is False
    assert prepared.open().default_state.inputs == {"choice": "B"}
    assert session.capture_calls == 1
    current = preparation_repository(repository).current(identity)
    assert current is not None
    assert current.instance != old_instance
    current.close()
    prepared.close()
    repository.close()
