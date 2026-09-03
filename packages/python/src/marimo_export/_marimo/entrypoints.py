"""marimo extension entry points."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from marimo_export.observations import ObservationLedger


def kernel_lifespan(value: None) -> Any:
    """Construct managed-build behavior for one marimo kernel."""

    from marimo_export._marimo.compat.managed_kernel import kernel_lifespan as construct

    return construct(value)


def install_observation_ledger(
    context: object,
    ledger: ObservationLedger,
) -> Callable[[], None]:
    """Attach repository-backed observation recording to a Marimo kernel."""

    from marimo_export._marimo.composition import install_observation_ledger as install

    return install(context, ledger)


__all__ = ["install_observation_ledger", "kernel_lifespan"]
