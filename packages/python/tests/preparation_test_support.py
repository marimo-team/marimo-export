from __future__ import annotations

from collections.abc import Callable, Mapping
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from marimo_export._json import JsonObject, canonical_bytes, sha256_bytes
from marimo_export._repository.preparation import (
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export._services.identity import ProducerIdentity
from marimo_export._services.plan_export import (
    PlanPreflight,
)
from marimo_export.client import Session
from marimo_export.descriptors import (
    Provenance,
    ScalarDescriptor,
)
from marimo_export.index import (
    ExportIndex,
    NotebookProvenance,
    StateEntry,
)
from marimo_export.planning import output_plan_sha256
from marimo_export.repository import (
    ExportRepository,
)
from marimo_export.result import CacheSummary
from marimo_export.spec import ExportSpec, OutputSpec
from marimo_export.wire import state_fingerprint


class _BorrowedSession(Session):
    def __init__(
        self,
        wire: Mapping[str, object],
        captured: SimpleNamespace,
        *,
        on_capture: Callable[[], None] | None = None,
    ) -> None:
        self.wire = wire
        self.captured = captured
        self.on_capture = on_capture
        self.capture_calls = 0

    def _plan(self, spec: ExportSpec):
        del spec
        return self.wire

    def _capture(self, spec: ExportSpec, limits):
        del spec, limits
        self.capture_calls += 1
        if self.on_capture is not None:
            self.on_capture()
        return self.captured


class _FakeProducer:
    def __init__(
        self,
        spec: ExportSpec,
        producer: ProducerIdentity,
        *,
        capture_producer: ProducerIdentity | None = None,
        on_capture=None,
        commit_guard=None,
    ) -> None:
        self.spec = spec
        self.producer = producer
        self.capture_producer = capture_producer or producer
        self.on_capture = on_capture
        self.commit_guard = commit_guard
        self.captured: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_error: object) -> None:
        return None

    def _plan(self, spec: ExportSpec):
        assert spec is self.spec
        return _plan_wire(spec, self.producer)

    def _capture_data(self, spec: ExportSpec, _limits):
        alias = next(iter(spec.states))
        self.captured.append(alias)
        if self.on_capture is not None:
            self.on_capture()
        index = _index(spec, self.capture_producer)
        return SimpleNamespace(
            index=index,
            assets={},
            output_cache=CacheSummary(hits=0, misses=1),
            notebook_cache=CacheSummary(hits=1, misses=1),
            capture_seconds=0.01,
        )

    def _require_source_stable(self) -> None:
        if self.commit_guard is not None:
            self.commit_guard()


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _entry(inputs: JsonObject) -> StateEntry:
    choice = inputs["choice"]
    assert isinstance(choice, str)
    return StateEntry(
        inputs=inputs,
        outputs={
            "answer": ScalarDescriptor(
                value=choice,
                provenance=Provenance(python_type="builtins.str"),
            )
        },
    )


def _identity_notebook(path: Path) -> None:
    path.write_text(
        """import marimo

app = marimo.App()

@app.cell
def _():
    answer = "A"
    return (answer,)
""",
        encoding="utf-8",
    )


def _index(spec: ExportSpec, producer: ProducerIdentity) -> ExportIndex:
    aliases: dict[str, str] = {}
    states: dict[str, StateEntry] = {}
    for alias, values in spec.states.items():
        choice = values["choice"]
        assert isinstance(choice, str)
        inputs: JsonObject = {"choice": choice}
        fingerprint = state_fingerprint(inputs)
        aliases[alias] = fingerprint
        states[fingerprint] = _entry(inputs)
    return ExportIndex(
        spec_sha256=_spec_sha256(spec),
        default_state=aliases[spec.default_state],
        notebook=NotebookProvenance(
            filename=producer.source.name if producer.source is not None else None,
            document_sha256=producer.document_sha256,
        ),
        producer=producer.provenance,
        inputs=("choice",),
        control_bindings={},
        outputs=("answer",),
        aliases=aliases,
        states=states,
    )


def _install_export(
    repository: ExportRepository,
    identity: RepositoryIdentity,
    spec: ExportSpec,
    producer: ProducerIdentity,
) -> None:
    artifact = _install_export_handle(repository, identity, spec, producer)
    artifact.close()


def _install_export_handle(
    repository: ExportRepository,
    identity: RepositoryIdentity,
    spec: ExportSpec,
    producer: ProducerIdentity,
):
    states = []
    for values in spec.states.values():
        choice = values["choice"]
        assert isinstance(choice, str)
        states.append(_install_state(repository, identity, {"choice": choice}))
    try:
        storage = preparation_repository(repository)
        with storage.reserve_preparation(identity), storage.stage_export(identity) as staged:
            (staged.path / "index.json").write_bytes(_index(spec, producer).to_bytes())
            artifact = staged.commit(states=states, captured_observation_revision=0)
        return artifact
    finally:
        for state in states:
            state.close()


def _install_state(
    repository: ExportRepository,
    identity: RepositoryIdentity,
    inputs: JsonObject,
):
    fingerprint = state_fingerprint(inputs)
    entry = _entry(inputs)
    storage = preparation_repository(repository)
    with (
        storage.reserve_preparation(identity),
        storage.stage_prepared_state(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprint=fingerprint,
        ) as staged,
    ):
        return staged.commit(
            metadata={
                "inputs": entry.inputs,
                "outputs": {
                    name: descriptor.to_value() for name, descriptor in entry.outputs.items()
                },
                "control_bindings": {},
            }
        )


def _plan_wire(spec: ExportSpec, producer: ProducerIdentity) -> dict[str, object]:
    states = [
        {
            "aliases": [alias],
            "inputs": {"choice": values["choice"]},
            "fingerprint": state_fingerprint({"choice": values["choice"]}),
        }
        for alias, values in sorted(spec.states.items())
    ]
    default = next(item["fingerprint"] for item in states if spec.default_state in item["aliases"])
    return {
        "default_alias": spec.default_state,
        "default_fingerprint": default,
        "document_sha256": producer.document_sha256,
        "environment_sha256": producer.environment_sha256,
        "filename": producer.filename,
        "implementation_sha256": producer.implementation_sha256,
        "inputs": ["choice"],
        "output_plan_sha256": output_plan_sha256(spec),
        "outputs": ["answer"],
        "producer": producer.provenance.to_value(),
        "producer_sha256": producer.producer_sha256,
        "source_sha256": producer.source_sha256,
        "spec_sha256": _spec_sha256(spec),
        "states": states,
    }


def _preflight(path: Path, spec: ExportSpec) -> PlanPreflight:
    producer = _producer(path)
    return PlanPreflight(
        producer=producer,
        repository_identity=RepositoryIdentity(
            producer_sha256=producer.producer_sha256,
            output_plan_sha256=output_plan_sha256(spec),
            spec_sha256=_spec_sha256(spec),
        ),
    )


def _producer(path: Path) -> ProducerIdentity:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# test notebook\n", encoding="utf-8")
    return ProducerIdentity(
        source=path,
        filename=path.name,
        source_sha256=_digest("source"),
        document_sha256=_digest("document"),
        producer_sha256=_digest("producer"),
        marimo_version="0.24.0",
        marimo_export_version="0.0.0",
        implementation_sha256=_digest("implementation"),
        environment_sha256=_digest("environment"),
    )


def _spec(*, states: dict[str, JsonObject] | None = None) -> ExportSpec:
    return ExportSpec(
        default_state="baseline",
        states=states or {"baseline": {"choice": "A"}, "other": {"choice": "B"}},
        outputs={"answer": OutputSpec.json("answer")},
    )


def _spec_sha256(spec: ExportSpec) -> str:
    return sha256_bytes(canonical_bytes(spec.to_value()))


__all__ = [
    "_BorrowedSession",
    "_FakeProducer",
    "_digest",
    "_entry",
    "_identity_notebook",
    "_index",
    "_install_export",
    "_install_export_handle",
    "_install_state",
    "_plan_wire",
    "_preflight",
    "_producer",
    "_spec",
    "_spec_sha256",
]
