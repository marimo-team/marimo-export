"""Shared lifecycle operations for file and borrowed-session preparation."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._repository.preparation import (
    PreparationRepository,
    PreparedExportArtifact,
    PreparedState,
)
from marimo_export._services.identity import ProducerIdentity
from marimo_export._services.plan_export import plan_from_artifact
from marimo_export.errors import ExecutionError
from marimo_export.planning import ExportPlan, PlannedState
from marimo_export.prepared import PreparedExport, _exact_prepared_export
from marimo_export.progress import CacheActivity, ProgressEvent
from marimo_export.repository import ExportRepository
from marimo_export.spec import ExportSpec

ProgressCallback = Callable[[ProgressEvent], None]


def exact_prepared(
    artifact: PreparedExportArtifact,
    spec: ExportSpec,
    producer: ProducerIdentity,
    preparation: PreparationRepository,
    repository: ExportRepository,
    *,
    owns_repository: bool,
    progress: ProgressCallback | None,
    resolved_plan: ExportPlan | None = None,
    require_current: Callable[[], None] | None = None,
) -> PreparedExport:
    """Wrap one exact repository generation and report its reuse."""

    def resolve_plan() -> ExportPlan:
        return (
            plan_from_artifact(artifact, spec, producer, preparation)
            if resolved_plan is None
            else resolved_plan
        )

    prepared = _exact_prepared_export(
        artifact=artifact,
        repository=repository,
        owns_repository=owns_repository,
        resolve_plan=resolve_plan,
        require_current=require_current,
    )
    plan = prepared.plan
    try:
        emit(
            progress,
            ProgressEvent(
                kind="plan_ready",
                completed=len(plan.states),
                total=len(plan.states),
            ),
        )
        emit(
            progress,
            ProgressEvent(
                kind="prepared_reused",
                completed=len(plan.states),
                total=len(plan.states),
            ),
        )
    except BaseException:
        prepared.close()
        raise
    return prepared


def missing_states(
    plan: ExportPlan,
    prepared: Mapping[str, PreparedState],
) -> tuple[PlannedState, ...]:
    return tuple(state for state in plan.states if state.fingerprint not in prepared)


def replace_reuse_partition(
    plan: ExportPlan,
    reusable: tuple[str, ...],
) -> ExportPlan:
    return replace(
        plan,
        reusable_states=reusable,
        missing_states=tuple(sorted(set(plan.state_fingerprints) - set(reusable))),
    )


def add_activity(left: CacheActivity, right: CacheActivity) -> CacheActivity:
    return CacheActivity(
        authored_hits=left.authored_hits + right.authored_hits,
        authored_misses=left.authored_misses + right.authored_misses,
        projection_hits=left.projection_hits + right.projection_hits,
        projection_misses=left.projection_misses + right.projection_misses,
    )


def require_not_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise ExecutionError("export preparation was cancelled", code="preparation_cancelled")


def close_states(states: Iterable[PreparedState]) -> None:
    active = sys.exc_info()[1]
    failure: BaseException | None = None
    for state in states:
        try:
            state.close()
        except BaseException as error:
            owner = active or failure
            if owner is None:
                failure = error
            else:
                record_cleanup_failure(owner, "prepared state lease cleanup", error)
    if active is None and failure is not None:
        raise failure


def emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback is not None:
        callback(event)


__all__ = [
    "ProgressCallback",
    "add_activity",
    "close_states",
    "emit",
    "exact_prepared",
    "missing_states",
    "replace_reuse_partition",
    "require_not_cancelled",
]
