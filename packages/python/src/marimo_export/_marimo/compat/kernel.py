"""Compose private marimo kernel capabilities."""

from marimo_export._marimo.compat.cache.barrier import flush_native_caches
from marimo_export._marimo.compat.execution import execute_state
from marimo_export._marimo.compat.exporters import prepared_exporters
from marimo_export._marimo.compat.inspection import (
    declared_ui_values,
    inspect_baseline,
    require_capabilities,
    runtime_path,
    validate_parent_state,
)
from marimo_export._marimo.compat.inspection import (
    observe_kernel_inputs as _observe_kernel_inputs,
)
from marimo_export.integration import KernelInputObservation


def observe_inputs() -> KernelInputObservation:
    from marimo._runtime.context import get_context

    return _observe_kernel_inputs(get_context())


class PrivateKernelRuntime:
    """Adapt the supported marimo kernel to marimo-export capabilities."""

    require_capabilities = staticmethod(require_capabilities)
    runtime_path = staticmethod(runtime_path)
    validate_parent_state = staticmethod(validate_parent_state)
    inspect_baseline = staticmethod(inspect_baseline)
    observe_inputs = staticmethod(observe_inputs)
    declared_ui_values = staticmethod(declared_ui_values)
    prepared_exporters = staticmethod(prepared_exporters)
    flush_native_caches = staticmethod(flush_native_caches)
    execute_state = staticmethod(execute_state)


__all__ = ["PrivateKernelRuntime"]
