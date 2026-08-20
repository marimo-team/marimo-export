"""Prepare reusable finite notebook states and commit one immutable export."""

from __future__ import annotations

from collections.abc import Callable

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._repository.preparation import (
    PreparationRepository,
    preparation_repository,
)
from marimo_export._services.plan_export import (
    PlanPreflight,
    ProgressCallback,
    plan_with_producer,
    preflight_plan,
    require_preflight_current,
)
from marimo_export._services.preparation_support import (
    emit,
    exact_prepared,
    require_not_cancelled,
)
from marimo_export._services.state_preparation import prepare_states
from marimo_export.limits import _capture_limits
from marimo_export.prepared import PreparedExport
from marimo_export.producer import _timeout, open_notebook
from marimo_export.progress import ProgressEvent
from marimo_export.repository import ExportRepository
from marimo_export.spec import ExportSpec, StrPath


def prepare(
    source: StrPath,
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedExport:
    """Prepare missing states and return one leased immutable export."""

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
        preflight = preflight_plan(source, spec)
        require_not_cancelled(cancelled)
        exact = preparation.current(preflight.repository_identity)
        if exact is not None:
            prepared = exact_prepared(
                exact,
                spec,
                preflight.producer,
                preparation,
                selected,
                owns_repository=owns_repository,
                progress=progress,
                require_current=lambda: require_preflight_current(preflight),
            )
            transferred = True
            return prepared
        with preparation.reserve_preparation(
            preflight.repository_identity,
            cancelled=cancelled,
            timeout=duration,
        ):
            reservation_cancelled = preparation.cancellation(cancelled)
            require_not_cancelled(reservation_cancelled)
            exact = preparation.current(preflight.repository_identity)
            if exact is not None:
                prepared = exact_prepared(
                    exact,
                    spec,
                    preflight.producer,
                    preparation,
                    selected,
                    owns_repository=owns_repository,
                    progress=progress,
                    require_current=lambda: require_preflight_current(preflight),
                )
                transferred = True
                return prepared
            prepared = _prepare_reserved(
                spec,
                preflight,
                preparation,
                selected,
                owns_repository=owns_repository,
                timeout=duration,
                progress=progress,
                cancelled=reservation_cancelled,
            )
            transferred = True
            return prepared
    finally:
        if owns_repository and not transferred:
            selected.close()


def _prepare_reserved(
    spec: ExportSpec,
    preflight: PlanPreflight,
    preparation: PreparationRepository,
    repository: ExportRepository,
    *,
    owns_repository: bool,
    timeout: float,
    progress: ProgressCallback | None,
    cancelled: Callable[[], bool] | None,
) -> PreparedExport:
    source = preflight.producer.source
    if source is None:
        raise AssertionError("file preparation requires a local source")
    emit(progress, ProgressEvent(kind="inspection_started"))
    prepared: PreparedExport | None = None
    try:
        with open_notebook(source, timeout=timeout) as producer:
            plan = plan_with_producer(producer, spec, preflight.producer, preparation)
            require_not_cancelled(cancelled)

            def require_producer_current() -> None:
                producer._require_source_stable()
                require_preflight_current(preflight)

            prepared = prepare_states(
                spec=spec,
                plan=plan,
                producer=preflight.producer,
                preparation=preparation,
                repository=repository,
                owns_repository=owns_repository,
                capture_state=lambda state_spec: producer._capture_data(
                    state_spec,
                    _capture_limits(None),
                ),
                require_producer_current=require_producer_current,
                progress=progress,
                cancelled=cancelled,
            )
    except BaseException as error:
        if prepared is not None:
            try:
                prepared.close()
            except BaseException as cleanup_error:
                record_cleanup_failure(error, "prepared export cleanup", cleanup_error)
        raise
    if prepared is None:
        raise AssertionError("file preparation returned no prepared export")
    return prepared


__all__ = ["prepare"]
