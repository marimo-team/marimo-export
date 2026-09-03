"""Stable integration signals for marimo hosts."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from marimo_export._format import identifier_name
from marimo_export.index import ControlBinding
from marimo_export.wire import FrozenJsonValue, _freeze_json, portable_json

_OWNED_SESSION_ENV = "MARIMO_EXPORT_OWNED_SESSION"
_MAX_CONTROL_ID_BYTES = 1_024


@dataclass(frozen=True, slots=True, init=False)
class KernelInputObservation:
    """Portable input values and live control bindings from one kernel graph."""

    values: Mapping[str, FrozenJsonValue]
    control_bindings: Mapping[str, ControlBinding]

    def __init__(
        self,
        values: Mapping[str, object],
        control_bindings: Mapping[str, ControlBinding],
    ) -> None:
        if not isinstance(values, Mapping):
            raise TypeError("observed input values must be a mapping")
        parsed = portable_json(values, "observed inputs")
        if not isinstance(parsed, dict):
            raise TypeError("observed input values must be an object")
        for name in parsed:
            if not _valid_input_name(name):
                raise ValueError("observed input names must be valid input identifiers")
        if not isinstance(control_bindings, Mapping):
            raise TypeError("observed control bindings must be a mapping")
        bindings: dict[str, ControlBinding] = {}
        for object_id, binding in control_bindings.items():
            if (
                not isinstance(object_id, str)
                or not object_id
                or len(object_id.encode("utf-8")) > _MAX_CONTROL_ID_BYTES
            ):
                raise ValueError("observed control IDs must be bounded non-empty strings")
            if not isinstance(binding, ControlBinding):
                raise TypeError("observed control bindings must contain ControlBinding values")
            if binding.input not in parsed:
                raise ValueError("observed control bindings must name observed inputs")
            bindings[object_id] = binding
        object.__setattr__(
            self,
            "values",
            cast(
                Mapping[str, FrozenJsonValue],
                _freeze_json({name: parsed[name] for name in sorted(parsed)}),
            ),
        )
        object.__setattr__(
            self,
            "control_bindings",
            MappingProxyType(dict(sorted(bindings.items()))),
        )

    def to_value(self) -> dict[str, object]:
        """Return the portable observation wire value."""

        return {
            "values": portable_json(self.values, "observed inputs"),
            "control_bindings": {
                object_id: binding.to_value()
                for object_id, binding in self.control_bindings.items()
            },
        }


def observe_kernel_inputs(kernel: object) -> KernelInputObservation:
    """Observe canonical portable UI roots from a running marimo kernel."""

    from marimo_export._marimo.composition import observe_kernel_inputs as observe

    return observe(kernel)


def keep_cached_cells_compatible() -> Callable[[], None]:
    """Install Marimo cache repairs for one interactive host lifecycle."""

    from marimo_export._marimo.composition import keep_cached_cells_compatible as install

    return install()


def _valid_input_name(value: object) -> bool:
    try:
        identifier_name(value, "control root dependency")
    except (TypeError, ValueError):
        return False
    return True


def is_owned_session() -> bool:
    """Return whether this process belongs to a marimo-export owned session."""

    return os.environ.get(_OWNED_SESSION_ENV) == "1"


def _owned_session_environment() -> dict[str, str]:
    return {_OWNED_SESSION_ENV: "1"}


__all__ = [
    "KernelInputObservation",
    "is_owned_session",
    "keep_cached_cells_compatible",
    "observe_kernel_inputs",
]
