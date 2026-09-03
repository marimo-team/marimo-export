"""Record live notebook input vectors for later export planning."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from marimo_export._format import identifier_name
from marimo_export._json import canonical_bytes, decode_json_object
from marimo_export.errors import MarimoExportError
from marimo_export.wire import (
    FrozenJsonObject,
    _freeze_json,
    portable_json,
    state_fingerprint,
)

if TYPE_CHECKING:
    from marimo_export._observations.ledger import ObservationLedger


class ObservationPersistenceError(MarimoExportError):
    """Input observations could not be committed to the export repository."""

    code = "observation_persistence_failed"


class ObservationRejectedError(MarimoExportError):
    """One input observation exceeded the bounded ingestion contract."""

    code = "observation_rejected"


@dataclass(frozen=True, slots=True, init=False)
class ObservedInputs:
    """One complete canonical portable input vector observed from a notebook."""

    fingerprint: str
    _values_bytes: bytes = field(repr=False)

    def __init__(self, values: Mapping[str, object]) -> None:
        if not isinstance(values, Mapping):
            raise TypeError("observed inputs must be a mapping")
        parsed = portable_json(values, "observed inputs")
        if not isinstance(parsed, dict):
            raise TypeError("observed inputs must be an object")
        for name in parsed:
            identifier_name(name, "observed input name")
        ordered = {name: parsed[name] for name in sorted(parsed)}
        encoded = canonical_bytes(ordered)
        object.__setattr__(self, "fingerprint", state_fingerprint(ordered))
        object.__setattr__(self, "_values_bytes", encoded)

    @property
    def values(self) -> FrozenJsonObject:
        return cast(
            FrozenJsonObject,
            _freeze_json(decode_json_object(self._values_bytes, "observed inputs")),
        )

    @property
    def canonical_values(self) -> bytes:
        return self._values_bytes

    @property
    def byte_count(self) -> int:
        return len(self._values_bytes)


def install_observation_ledger(
    context: object,
    ledger: ObservationLedger,
) -> Callable[[], None]:
    """Record successful normal notebook runs until the returned handle closes."""

    from marimo_export._observations.ledger import ObservationLedger as Ledger

    if not isinstance(ledger, Ledger):
        raise TypeError("ledger must be an ObservationLedger")
    from marimo_export._marimo.entrypoints import install_observation_ledger as install

    return install(context, ledger)


def __getattr__(name: str) -> Any:
    if name != "ObservationLedger":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from marimo_export._observations.ledger import ObservationLedger

    globals()[name] = ObservationLedger
    return ObservationLedger


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})


__all__ = [
    "ObservationLedger",
    "ObservationPersistenceError",
    "ObservationRejectedError",
    "ObservedInputs",
    "install_observation_ledger",
]
