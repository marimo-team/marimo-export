"""Compose private marimo kernel capabilities."""

from marimo_export._marimo.compat.execution import execute_state, flush_native_caches
from marimo_export._marimo.compat.exporters import prepared_exporters
from marimo_export._marimo.compat.inspection import (
    declared_ui_values,
    inspect_baseline,
    require_capabilities,
    runtime_path,
)


class PrivateKernelRuntime:
    """Adapt the supported marimo kernel to marimo-export capabilities."""

    require_capabilities = staticmethod(require_capabilities)
    runtime_path = staticmethod(runtime_path)
    inspect_baseline = staticmethod(inspect_baseline)
    declared_ui_values = staticmethod(declared_ui_values)
    prepared_exporters = staticmethod(prepared_exporters)
    flush_native_caches = staticmethod(flush_native_caches)
    execute_state = staticmethod(execute_state)


__all__ = ["PrivateKernelRuntime"]
