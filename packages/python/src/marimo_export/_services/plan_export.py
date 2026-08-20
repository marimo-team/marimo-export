"""Resolve export plans against notebook inspection and repository state."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from marimo_export._json import sha256_bytes
from marimo_export._repository.preparation import (
    PreparationRepository,
    PreparedExportArtifact,
    PreparedState,
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export._services.identity import ProducerIdentity, producer_identity
from marimo_export._services.plan_wire import decode_plan_wire, spec_sha256
from marimo_export.errors import ExecutionError, NotebookExportError
from marimo_export.index import ExportIndex
from marimo_export.planning import ExportPlan, PlannedState, output_plan_sha256
from marimo_export.producer import OwnedNotebook, _timeout, open_notebook
from marimo_export.progress import ProgressEvent
from marimo_export.reader import NotebookExport
from marimo_export.repository import ExportRepository
from marimo_export.spec import ExportSpec, StrPath

ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True, slots=True)
class PlanPreflight:
    producer: ProducerIdentity
    repository_identity: RepositoryIdentity


def preflight_plan(source: StrPath, spec: ExportSpec) -> PlanPreflight:
    """Compute the exact repository identity without opening a notebook session."""

    identity = producer_identity(source)
    repository_identity = RepositoryIdentity(
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=output_plan_sha256(spec),
        spec_sha256=spec_sha256(spec),
    )
    return PlanPreflight(producer=identity, repository_identity=repository_identity)


def require_preflight_current(preflight: PlanPreflight) -> None:
    """Reject source or runtime drift after a file identity preflight."""

    source = preflight.producer.source
    if source is None:
        raise AssertionError("file planning requires a local source")
    current = producer_identity(source)
    if current.producer_sha256 != preflight.producer.producer_sha256:
        raise ExecutionError(
            "the notebook source or runtime changed after export preflight",
            code="notebook_changed",
        )


def plan(
    source: StrPath,
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    progress: ProgressCallback | None = None,
) -> ExportPlan:
    """Inspect one source and report exact reusable and missing export work."""

    if not isinstance(spec, ExportSpec):
        raise TypeError("spec must be an ExportSpec")
    owned_repository = repository is None
    duration = _timeout(timeout)
    selected = ExportRepository.open() if repository is None else repository
    preparation = preparation_repository(selected)
    try:
        preflight = preflight_plan(source, spec)
        exact = preparation.current(preflight.repository_identity)
        if exact is not None:
            try:
                require_preflight_current(preflight)
                result = plan_from_artifact(exact, spec, preflight.producer, preparation)
            finally:
                exact.close()
            _emit(
                progress,
                ProgressEvent(
                    kind="plan_ready",
                    completed=len(result.reusable_states),
                    total=len(result.states),
                ),
            )
            return result
        _emit(progress, ProgressEvent(kind="inspection_started"))
        source_path = preflight.producer.source
        if source_path is None:
            raise AssertionError("file planning requires a local source")
        with open_notebook(source_path, timeout=duration) as producer:
            result = plan_with_producer(producer, spec, preflight.producer, preparation)
            producer._require_source_stable()
        _emit(
            progress,
            ProgressEvent(
                kind="plan_ready",
                completed=len(result.reusable_states),
                total=len(result.states),
            ),
        )
        return result
    finally:
        if owned_repository:
            selected.close()


def plan_with_producer(
    producer: OwnedNotebook,
    spec: ExportSpec,
    identity: ProducerIdentity,
    repository: PreparationRepository,
) -> ExportPlan:
    """Resolve one plan through an already-open owned notebook session."""

    wire = producer._plan(spec)
    planned = decode_plan_wire(wire, spec, identity)
    return resolve_repository_plan(planned, repository)


def resolve_repository_plan(
    planned: ExportPlan,
    repository: PreparationRepository,
) -> ExportPlan:
    """Attach one live repository reuse and observation snapshot to a plan."""

    prepared = repository.lookup_prepared_states(
        producer_sha256=planned.producer_sha256,
        output_plan_sha256=planned.output_plan_sha256,
        state_fingerprints=planned.state_fingerprints,
    )
    try:
        reusable = tuple(sorted(prepared))
    finally:
        _close_states(prepared.values())
    observations = repository.observation_snapshot(planned.producer_sha256)
    return replace(
        planned,
        reusable_states=reusable,
        missing_states=tuple(sorted(set(planned.state_fingerprints) - set(reusable))),
        observation_revision=observations.revision,
        observations=observations.observations(planned.inputs),
    )


def plan_from_artifact(
    artifact: PreparedExportArtifact,
    spec: ExportSpec,
    identity: ProducerIdentity,
    repository: PreparationRepository,
) -> ExportPlan:
    """Reconstruct an exact plan from a verified repository export."""

    index_path = artifact.asset("index.json")
    if index_path is None:
        raise ExecutionError(
            "the prepared export has no index",
            code="export_invalid",
        )
    index = ExportIndex.from_bytes(index_path.read_bytes())
    expected_spec = spec_sha256(spec)
    if (
        index.spec_sha256 != expected_spec
        or index.notebook.document_sha256 != identity.document_sha256
        or artifact.identity.producer_sha256 != identity.producer_sha256
        or artifact.identity.output_plan_sha256 != output_plan_sha256(spec)
        or artifact.identity.spec_sha256 != expected_spec
    ):
        raise ExecutionError(
            "the prepared export identity does not match the requested source",
            code="export_invalid",
        )
    aliases: dict[str, list[str]] = {fingerprint: [] for fingerprint in index.states}
    for alias, fingerprint in index.aliases.items():
        aliases[fingerprint].append(alias)
    states = tuple(
        PlannedState(
            aliases=tuple(sorted(aliases[fingerprint])),
            inputs=entry.inputs,
            fingerprint=fingerprint,
        )
        for fingerprint, entry in sorted(index.states.items())
    )
    fingerprints = tuple(state.fingerprint for state in states)
    observations = repository.observation_snapshot(identity.producer_sha256)
    return ExportPlan(
        document_sha256=index.notebook.document_sha256,
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=artifact.identity.output_plan_sha256,
        spec_sha256=index.spec_sha256,
        default_alias=spec.default_state,
        default_fingerprint=index.default_state,
        inputs=index.inputs,
        states=states,
        outputs=index.outputs,
        reusable_states=tuple(sorted(fingerprints)),
        missing_states=(),
        observation_revision=observations.revision,
        observations=observations.observations(index.inputs),
        exact_reuse=True,
    )


def artifact_matches_plan(
    artifact: PreparedExportArtifact,
    plan: ExportPlan,
) -> bool:
    """Return whether one exact artifact contains the resolved live relation."""

    index_path = artifact.asset("index.json")
    if index_path is None:
        return False
    try:
        data = index_path.read_bytes()
        index = ExportIndex.from_bytes(data)
    except NotebookExportError:
        return False
    return plan.matches(NotebookExport(artifact.path, index, sha256_bytes(data)))


def _close_states(states: Iterable[PreparedState]) -> None:
    for state in states:
        state.close()


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback is not None:
        callback(event)


__all__ = [
    "PlanPreflight",
    "ProgressCallback",
    "artifact_matches_plan",
    "plan",
    "plan_from_artifact",
    "plan_with_producer",
    "preflight_plan",
    "require_preflight_current",
    "resolve_repository_plan",
]
