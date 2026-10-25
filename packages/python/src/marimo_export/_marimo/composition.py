"""Construct process-specific adapters for the supported marimo runtime."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from marimo_export._json import JsonObject
from marimo_export._marimo.capabilities import KernelAdapters, KernelRuntime

if TYPE_CHECKING:
    from marimo_export._marimo.capabilities import KernelRun, NotebookCell
    from marimo_export.integration import KernelInputObservation


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


def saved_cells(notebook: Path, source: bytes) -> tuple[NotebookCell, ...]:
    """Parse one saved notebook's cells through the supported Marimo adapter."""

    from marimo_export._marimo.compat.inspection import saved_cells as parse

    return parse(notebook, source)


def observe_kernel_inputs(kernel: object) -> KernelInputObservation:
    """Observe live inputs through the supported Marimo adapter."""

    from marimo_export._marimo.compat.inspection import observe_kernel_inputs as observe

    return observe(kernel)


def subscribe_run_finished(
    context: object,
    callback: Callable[[KernelRun], None],
) -> Callable[[], None]:
    """Deliver the context's finished kernel runs through the supported adapter."""

    from marimo_export._marimo.compat.observations import subscribe_run_finished as subscribe

    return subscribe(context, callback)


def kernel_filename(context: object) -> str | None:
    """Return the notebook path reported by the context's kernel."""

    from marimo_export._marimo.compat.observations import kernel_filename as filename

    return filename(context)


def keep_cached_cells_compatible() -> Callable[[], None]:
    """Install the interactive-host cache compatibility lease."""

    from marimo_export._marimo.compat.cache.host import keep_cached_cells_compatible as install

    return install()


__all__ = [
    "create_kernel_adapters",
    "create_kernel_runtime",
    "keep_cached_cells_compatible",
    "kernel_filename",
    "marimo_compatibility_details",
    "observe_kernel_inputs",
    "saved_cells",
    "subscribe_run_finished",
]
