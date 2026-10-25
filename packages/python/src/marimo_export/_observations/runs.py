"""Record successful normal kernel runs into one observation ledger."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from marimo_export._notebook import _notebook_path
from marimo_export._observations.source import SavedProducerMatcher, cell_signature
from marimo_export.observations import ObservedInputs

if TYPE_CHECKING:
    from marimo_export._marimo.capabilities import KernelRun
    from marimo_export._observations.ledger import ObservationLedger

LOGGER = logging.getLogger(__name__)


def install_observation_ledger(
    context: object,
    ledger: ObservationLedger,
) -> Callable[[], None]:
    """Record the context's successful normal runs until the returned handle closes."""

    from marimo_export._marimo.composition import kernel_filename, subscribe_run_finished

    if not _bound(kernel_filename(context), ledger.source):
        raise ValueError("Marimo kernel source does not match the observation ledger source")
    matcher = SavedProducerMatcher(ledger.source)
    active: ObservationLedger | None = ledger

    def record(run: KernelRun) -> None:
        current = active
        if current is None or run.outcome != "completed" or run.scratch:
            return
        if not _bound(run.filename, current.source):
            return
        try:
            observed = ObservedInputs(run.read_inputs().values)
            scope, resolve_producer = matcher.defer(cell_signature(run.read_cells()))
            current._record_deferred(
                observed,
                scope=scope,
                resolve_producer=resolve_producer,
            )
        except Exception as error:
            LOGGER.warning("Could not record notebook input state: %s", error)

    unsubscribe = subscribe_run_finished(context, record)

    def release() -> None:
        nonlocal active
        if active is None:
            return
        active = None
        unsubscribe()

    return release


def _bound(filename: str | None, source: Path) -> bool:
    if filename is None:
        return False
    try:
        return _notebook_path(filename) == source
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


__all__ = ["install_observation_ledger"]
