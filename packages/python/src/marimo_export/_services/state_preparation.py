"""Prepare one resolved state relation through an owned or borrowed producer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from marimo_export._repository.preparation import PreparationRepository
from marimo_export._services.export_artifacts import (
    assemble_export,
    commit_captured_state,
    single_state_spec,
)
from marimo_export._services.identity import ProducerIdentity
from marimo_export._services.plan_export import ProgressCallback
from marimo_export._services.preparation_support import (
    add_activity,
    close_states,
    emit,
    missing_states,
    replace_reuse_partition,
    require_not_cancelled,
)
from marimo_export.descriptors import OutputCodec
from marimo_export.index import ExportIndex
from marimo_export.planning import ExportPlan
from marimo_export.prepared import PreparedExport
from marimo_export.progress import CacheActivity, ProgressEvent
from marimo_export.repository import ExportRepository
from marimo_export.spec import ExportSpec


class _CacheCounts(Protocol):
    @property
    def hits(self) -> int: ...

    @property
    def misses(self) -> int: ...


class CapturedState(Protocol):
    @property
    def index(self) -> ExportIndex: ...

    @property
    def assets(self) -> Mapping[tuple[OutputCodec, str], bytes]: ...

    @property
    def output_cache(self) -> _CacheCounts: ...

    @property
    def notebook_cache(self) -> _CacheCounts: ...

    @property
    def capture_seconds(self) -> float: ...


def prepare_states(
    *,
    spec: ExportSpec,
    plan: ExportPlan,
    producer: ProducerIdentity,
    preparation: PreparationRepository,
    repository: ExportRepository,
    owns_repository: bool,
    capture_state: Callable[[ExportSpec], CapturedState],
    require_producer_current: Callable[[], None],
    progress: ProgressCallback | None,
    cancelled: Callable[[], bool] | None,
    replacing_instance: str | None = None,
) -> PreparedExport:
    """Capture missing fingerprints and publish one exact prepared export."""

    states = dict(
        preparation.lookup_prepared_states(
            producer_sha256=plan.producer_sha256,
            output_plan_sha256=plan.output_plan_sha256,
            state_fingerprints=plan.state_fingerprints,
        )
    )
    plan = replace_reuse_partition(plan, tuple(sorted(states)))
    emit(
        progress,
        ProgressEvent(
            kind="plan_ready",
            completed=len(plan.reusable_states),
            total=len(plan.states),
        ),
    )
    captured: list[str] = []
    activity = CacheActivity()

    def require_commit_current() -> None:
        require_not_cancelled(cancelled)
        require_producer_current()
        require_not_cancelled(cancelled)

    try:
        for completed, state in enumerate(missing_states(plan, states), start=1):
            require_not_cancelled(cancelled)
            emit(
                progress,
                ProgressEvent(
                    kind="state_started",
                    completed=completed - 1,
                    total=len(plan.missing_states),
                    state=state.aliases[0],
                ),
            )
            result = capture_state(single_state_spec(spec, state))
            require_not_cancelled(cancelled)
            states[state.fingerprint] = commit_captured_state(
                preparation,
                plan,
                state,
                producer,
                result.index,
                result.assets,
                commit_guard=require_commit_current,
            )
            captured.append(state.fingerprint)
            current = CacheActivity(
                authored_hits=result.notebook_cache.hits,
                authored_misses=result.notebook_cache.misses,
                projection_hits=result.output_cache.hits,
                projection_misses=result.output_cache.misses,
            )
            activity = add_activity(activity, current)
            emit(
                progress,
                ProgressEvent(
                    kind="state_finished",
                    completed=completed,
                    total=len(plan.missing_states),
                    state=state.aliases[0],
                    cache=current,
                    elapsed_seconds=result.capture_seconds,
                ),
            )
        require_not_cancelled(cancelled)
        artifact = assemble_export(
            preparation,
            plan,
            producer,
            states,
            replacing_instance=replacing_instance,
            commit_guard=require_commit_current,
        )
    finally:
        close_states(states.values())
    prepared_states = tuple(sorted(captured))
    reused_states = tuple(sorted(set(plan.state_fingerprints) - set(prepared_states)))
    try:
        prepared = PreparedExport._create(
            artifact=artifact,
            repository=repository,
            owns_repository=owns_repository,
            plan=plan,
            reused=False,
            prepared_states=prepared_states,
            reused_states=reused_states,
            cache_activity=activity,
        )
    except BaseException:
        artifact.close()
        raise
    try:
        emit(
            progress,
            ProgressEvent(
                kind="prepared_committed",
                completed=len(plan.states),
                total=len(plan.states),
            ),
        )
    except BaseException:
        prepared.close()
        raise
    return prepared


__all__ = ["CapturedState", "prepare_states"]
