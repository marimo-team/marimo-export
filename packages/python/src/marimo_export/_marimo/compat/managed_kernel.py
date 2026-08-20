"""Private managed-build adapter for marimo's kernel lifespan."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_MANAGED_CACHE_COMPAT_ENV = "MARIMO_EXPORT_MANAGED_CACHE_COMPAT"
_MANAGED_CACHE_ACTIVATION_ENV = "MARIMO_EXPORT_MANAGED_CACHE_ACTIVATION"
_MANAGED_CACHE_TOKEN_ENV = "MARIMO_EXPORT_MANAGED_CACHE_TOKEN"


@asynccontextmanager
async def kernel_lifespan(_: None) -> AsyncIterator[None]:
    """Install managed-build cache behavior inside the owned kernel."""

    managed = os.environ.pop(_MANAGED_CACHE_COMPAT_ENV, None)
    activation_path = os.environ.pop(_MANAGED_CACHE_ACTIVATION_ENV, None)
    activation_token = os.environ.pop(_MANAGED_CACHE_TOKEN_ENV, None)
    if managed != "1":
        yield
        return
    if not activation_path or not activation_token:
        raise RuntimeError("managed cache integration has no activation handshake")

    try:
        import cryptography
    except ImportError as error:
        raise RuntimeError("managed cache integration requires cryptography") from error
    if not getattr(cryptography, "__version__", None):
        raise RuntimeError("managed cache integration requires cryptography")

    from marimo._runtime.context import get_context
    from marimo._runtime.context.kernel_context import KernelRuntimeContext

    from marimo_export._marimo.compat.cache.patch import managed_cache_compat
    from marimo_export._marimo.compat.cache.probe import require_cache_capabilities
    from marimo_export._marimo.compat.inspection import install_parent_stop_provenance

    context = get_context()
    if not isinstance(context, KernelRuntimeContext):
        raise RuntimeError("managed cache integration requires a file-backed marimo kernel")
    require_cache_capabilities()
    release_stop_provenance = install_parent_stop_provenance(context)
    try:
        with managed_cache_compat(context._kernel._hooks, context.graph):
            from pathlib import Path

            Path(activation_path).write_text(activation_token, encoding="utf-8")
            yield
    finally:
        release_stop_provenance()


__all__ = ["kernel_lifespan"]
