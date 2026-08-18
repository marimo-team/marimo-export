"""marimo extension entry points."""

from __future__ import annotations

from typing import Any


def kernel_lifespan(value: None) -> Any:
    """Construct managed-build behavior for one marimo kernel."""

    from marimo_export._marimo.compat.managed_kernel import kernel_lifespan as construct

    return construct(value)


__all__ = ["kernel_lifespan"]
