"""Construct process-specific adapters for the supported marimo runtime."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from marimo_export._json import JsonObject
from marimo_export._marimo.capabilities import KernelAdapters, KernelRuntime

if TYPE_CHECKING:
    from marimo_export.integration import KernelInputObservation
    from marimo_export.observations import ObservationLedger


def create_kernel_runtime() -> KernelRuntime:
    """Construct the adapter for one attached marimo kernel."""

    from marimo_export._marimo.compat.cache.probe import require_cache_capabilities
    from marimo_export._marimo.compat.kernel import PrivateKernelRuntime

    require_cache_capabilities()
    return PrivateKernelRuntime()


def marimo_compatibility_details() -> JsonObject:
    """Return the validated Marimo adapter identity."""

    from marimo_export._marimo.compat.cache.probe import (
        MARIMO_RELEASE_COMMIT,
        MARIMO_VERSION,
        require_cache_capabilities,
    )

    require_cache_capabilities()
    return {
        "adapter": "private",
        "version": MARIMO_VERSION,
        "release_commit": MARIMO_RELEASE_COMMIT,
    }


def create_kernel_adapters() -> KernelAdapters:
    """Construct adapters for one attached-kernel bridge request."""

    from marimo_export._marimo.compat.transfer import PrivateTransferRuntime

    return KernelAdapters(
        kernel=create_kernel_runtime(),
        transfer=PrivateTransferRuntime(),
    )


def notebook_document_sha256(notebook: Path, source: bytes) -> str:
    """Read one saved notebook through the supported Marimo adapter."""

    from marimo_export._marimo.compat.inspection import document_sha256_from_source

    return document_sha256_from_source(notebook, source)


def observe_kernel_inputs(kernel: object) -> KernelInputObservation:
    """Observe live inputs through the supported Marimo adapter."""

    from marimo_export._marimo.compat.inspection import observe_kernel_inputs as observe

    return observe(kernel)


def install_observation_ledger(
    context: object,
    ledger: ObservationLedger,
) -> Callable[[], None]:
    """Attach an observation ledger through the supported private adapter."""

    from marimo_export._marimo.compat.observations import install_observation_ledger as install

    return install(context, ledger)


def keep_cached_cells_compatible() -> Callable[[], None]:
    """Install the interactive-host cache compatibility lease."""

    from marimo_export._marimo.compat.cache.host import keep_cached_cells_compatible as install

    return install()


__all__ = [
    "create_kernel_adapters",
    "create_kernel_runtime",
    "install_observation_ledger",
    "keep_cached_cells_compatible",
    "marimo_compatibility_details",
    "notebook_document_sha256",
    "observe_kernel_inputs",
]
