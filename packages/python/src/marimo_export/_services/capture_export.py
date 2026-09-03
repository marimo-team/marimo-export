"""Prepare exports from existing authenticated Marimo sessions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from marimo_export._repository.preparation import preparation_repository
from marimo_export._services.export_artifacts import (
    repository_identity,
)
from marimo_export._services.plan_export import (
    ProgressCallback,
    artifact_matches_plan,
    resolve_repository_plan,
)
from marimo_export._services.plan_wire import decode_plan_wire, producer_from_plan_wire
from marimo_export._services.preparation_support import (
    emit,
    exact_prepared,
    require_not_cancelled,
)
from marimo_export._services.state_preparation import prepare_states
from marimo_export.client import Client, Session, _timeout
from marimo_export.errors import ExecutionError
from marimo_export.limits import _capture_limits
from marimo_export.planning import ExportPlan
from marimo_export.prepared import PreparedExport
from marimo_export.progress import ProgressEvent
from marimo_export.repository import ExportRepository
from marimo_export.spec import ExportSpec


def capture(
    server: str,
    *,
    session: str,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 30.0,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedExport:
    """Prepare one existing Marimo session without taking ownership of it."""

    if not isinstance(session, str) or not session:
        raise TypeError("session must be a nonempty string")
    duration = _timeout(timeout)
    with Client(
        server,
        access_token=access_token,
        server_token=server_token,
        timeout=duration,
    ) as client:
        return capture_session(
            client.session(session),
            spec=spec,
            repository=repository,
            timeout=duration,
            progress=progress,
            cancelled=cancelled,
        )


def capture_session(
    session: Session,
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedExport:
    """Prepare through one borrowed live-session handle."""

    if not isinstance(session, Session):
        raise TypeError("session must be a Session")
    if not isinstance(spec, ExportSpec):
        raise TypeError("spec must be an ExportSpec")
    if cancelled is not None and not callable(cancelled):
        raise TypeError("cancelled must be callable or None")
    duration = _timeout(timeout)
    owns_repository = repository is None
    selected = ExportRepository.open() if repository is None else repository
    preparation = preparation_repository(selected)
    transferred = False
    try:
        require_not_cancelled(cancelled)
        emit(progress, ProgressEvent(kind="inspection_started"))
        wire = session._plan(spec)
        producer = producer_from_plan_wire(wire)
        plan = resolve_repository_plan(decode_plan_wire(wire, spec, producer), preparation)
        identity = repository_identity(plan)
        replacing_instance: str | None = None
        with preparation.reserve_preparation(
            identity,
            cancelled=cancelled,
            timeout=duration,
        ):
            reservation_cancelled = preparation.cancellation(cancelled)
            require_not_cancelled(reservation_cancelled)
            reserved_wire = session._plan(spec)
            reserved_producer = producer_from_plan_wire(reserved_wire)
            reserved_plan = resolve_repository_plan(
                decode_plan_wire(reserved_wire, spec, reserved_producer),
                preparation,
            )
            if repository_identity(reserved_plan) != identity:
                raise ExecutionError(
                    "the live producer changed while preparation was reserved",
                    code="parent_document_changed",
                )
            producer = reserved_producer
            plan = reserved_plan
            exact = preparation.current(identity)
            if exact is not None:
                try:
                    matches = artifact_matches_plan(exact, plan)
                except BaseException:
                    exact.close()
                    raise
                if matches:
                    prepared = exact_prepared(
                        exact,
                        spec,
                        producer,
                        preparation,
                        selected,
                        owns_repository=owns_repository,
                        progress=progress,
                        resolved_plan=plan,
                    )
                    transferred = True
                    return prepared
                replacing_instance = exact.instance
                exact.close()

            def require_producer_current() -> None:
                current_wire = session._plan(spec)
                current = producer_from_plan_wire(current_wire)
                decode_plan_wire(current_wire, spec, current)
                if current != producer:
                    raise ExecutionError(
                        "the live producer changed during export preparation",
                        code="parent_document_changed",
                        details={
                            "before": producer.producer_sha256,
                            "after": current.producer_sha256,
                        },
                    )

            prepared = prepare_states(
                spec=spec,
                plan=plan,
                producer=producer,
                preparation=preparation,
                repository=selected,
                owns_repository=owns_repository,
                capture_state=lambda state_spec: session._capture(
                    state_spec,
                    _capture_limits(None),
                ),
                require_producer_current=require_producer_current,
                progress=progress,
                cancelled=reservation_cancelled,
                replacing_instance=replacing_instance,
            )
        transferred = True
        return prepared
    finally:
        if owns_repository and not transferred:
            selected.close()


def plan_session(
    session: Session,
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    progress: ProgressCallback | None = None,
) -> ExportPlan:
    """Plan one borrowed session without executing export states."""

    if not isinstance(session, Session):
        raise TypeError("session must be a Session")
    if not isinstance(spec, ExportSpec):
        raise TypeError("spec must be an ExportSpec")
    owns_repository = repository is None
    selected = ExportRepository.open() if repository is None else repository
    preparation = preparation_repository(selected)
    try:
        emit(progress, ProgressEvent(kind="inspection_started"))
        wire = session._plan(spec)
        producer = producer_from_plan_wire(wire)
        plan = resolve_repository_plan(decode_plan_wire(wire, spec, producer), preparation)
        exact = preparation.current(repository_identity(plan))
        if exact is not None:
            try:
                matches = artifact_matches_plan(exact, plan)
            finally:
                exact.close()
            if matches:
                plan = replace(
                    plan,
                    reusable_states=tuple(sorted(plan.state_fingerprints)),
                    missing_states=(),
                    exact_reuse=True,
                )
        emit(
            progress,
            ProgressEvent(
                kind="plan_ready",
                completed=len(plan.reusable_states),
                total=len(plan.states),
            ),
        )
        return plan
    finally:
        if owns_repository:
            selected.close()


__all__ = ["capture", "capture_session", "plan_session"]
