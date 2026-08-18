"""Construct process-specific adapters for the supported marimo runtime."""

from __future__ import annotations

from marimo_export._marimo.capabilities import KernelAdapters, KernelRuntime


def create_kernel_runtime() -> KernelRuntime:
    """Construct the adapter for one attached marimo kernel."""

    from marimo_export._marimo.compat.kernel import PrivateKernelRuntime

    return PrivateKernelRuntime()


def create_kernel_adapters() -> KernelAdapters:
    """Construct adapters for one attached-kernel bridge request."""

    from marimo_export._marimo.compat.transfer import PrivateTransferRuntime

    return KernelAdapters(
        kernel=create_kernel_runtime(),
        transfer=PrivateTransferRuntime(),
    )


__all__ = ["create_kernel_adapters", "create_kernel_runtime"]
