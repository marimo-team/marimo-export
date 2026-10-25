"""Project the fewest JSON values and bytes an export index can hold.

A projection measures real index records with the counters that
``ExportIndex.to_bytes()`` applies. A state that is not prepared yet counts
each output at the cheapest entry it can write: the cheapest descriptor, or an
unavailable entry when the output is optional. The projection therefore never
exceeds the index that preparation writes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    json_value_count,
    portable_json_object,
)
from marimo_export._limits import MAX_INDEX_BYTES as _MAX_INDEX_BYTES
from marimo_export._limits import MAX_INDEX_VALUES as _MAX_INDEX_VALUES
from marimo_export.descriptors import (
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
    JsonDescriptor,
    MarimoCellDescriptor,
    MarimoOutputDescriptor,
    NumpyDescriptor,
    OutputDescriptor,
    OutputEntry,
    Provenance,
    ScalarDescriptor,
    UnavailableOutput,
)
from marimo_export.errors import NotebookExportError
from marimo_export.index import ExportIndex, NotebookProvenance, ProducerProvenance, StateEntry
from marimo_export.planning import IndexBudget, optional_outputs
from marimo_export.spec import ExportSpec
from marimo_export.wire import state_fingerprint

if TYPE_CHECKING:
    from marimo_export.planning import ExportPlan

# Every digest in the index has the same canonical length, and the shortest
# legal strings stand in for provenance text that a plan does not know yet.
_DIGEST = "0" * 64
_SHORTEST_TEXT = "a"


@dataclass(frozen=True, slots=True)
class _Cost:
    values: int
    bytes: int

    def __add__(self, other: _Cost) -> _Cost:
        return _Cost(self.values + other.values, self.bytes + other.bytes)

    def __sub__(self, other: _Cost) -> _Cost:
        return _Cost(self.values - other.values, self.bytes - other.bytes)


def _cost(value: JsonValue) -> _Cost:
    values = json_value_count(value)
    return _Cost(values, len(canonical_bytes(value, max_values=values)))


_EMPTY_OBJECT = _Cost(1, 2)


def _member(key: str, value: _Cost) -> _Cost:
    """Return what one ``"key":value`` member adds to an object, before separators."""

    return _Cost(1 + value.values, len(canonical_bytes(key)) + 1 + value.bytes)


def _object(members: Iterable[_Cost]) -> _Cost:
    total = _EMPTY_OBJECT
    count = 0
    for member in members:
        total += member
        count += 1
    return total + _Cost(0, max(count - 1, 0))


def _minimal_descriptors() -> tuple[OutputDescriptor, ...]:
    provenance = Provenance(python_type=_SHORTEST_TEXT)
    asset = AssetRef(sha256=_DIGEST, size=1)
    return (
        ScalarDescriptor(value=0, provenance=provenance),
        JsonDescriptor(value=0, provenance=provenance),
        NumpyDescriptor(asset=asset, provenance=provenance),
        ArrowDescriptor(asset=asset, provenance=provenance),
        MarimoOutputDescriptor(asset=asset, provenance=provenance),
        MarimoCellDescriptor(asset=asset, provenance=provenance),
        BlobAssetDescriptor(
            asset=asset,
            provenance=provenance,
            media_type="a/b",
            filename=None,
            metadata={},
        ),
    )


def _minimal_unavailable() -> tuple[UnavailableOutput, ...]:
    return tuple(
        UnavailableOutput(reason=reason, message=_SHORTEST_TEXT)
        for reason in ("representation_unavailable", "stopped", "value_missing")
    )


def _cheapest(entries: Iterable[OutputEntry]) -> _Cost:
    costs = [_cost(entry.to_value()) for entry in entries]
    return _Cost(min(cost.values for cost in costs), min(cost.bytes for cost in costs))


@cache
def _minimal_output_cost(optional: bool) -> _Cost:
    """Return the cheapest entry a required or an optional output can write."""

    if optional:
        return _cheapest((*_minimal_descriptors(), *_minimal_unavailable()))
    return _cheapest(_minimal_descriptors())


class IndexProjection:
    """Track the lower bound of one index while its states are prepared."""

    def __init__(
        self,
        *,
        inputs: tuple[str, ...],
        outputs: tuple[str, ...],
        optional_outputs: tuple[str, ...],
        aliases: tuple[str, ...],
        states: Mapping[str, JsonObject],
    ) -> None:
        self._skeleton = _skeleton(inputs, outputs, aliases)
        minimal_outputs = _object(
            _member(name, _minimal_output_cost(name in optional_outputs)) for name in outputs
        )
        self._minimal_outputs = minimal_outputs - _EMPTY_OBJECT
        self._entries: dict[str, _Cost] = {}
        self._members = _Cost(0, 0)
        for key, inputs_value in states.items():
            self._set(key, self._minimal_entry(inputs_value))

    @classmethod
    def for_plan(cls, plan: ExportPlan) -> IndexProjection:
        return cls(
            inputs=plan.inputs,
            outputs=plan.outputs,
            optional_outputs=plan.optional_outputs,
            aliases=tuple(alias for state in plan.states for alias in state.aliases),
            states={
                state.fingerprint: portable_json_object(state.inputs, "planned state inputs")
                for state in plan.states
            },
        )

    @classmethod
    def for_spec(cls, spec: ExportSpec) -> IndexProjection:
        """Bound the index from the authored rows, before any notebook runs.

        Inputs include at least every name a row assigns. An omitted input
        takes its baseline value, which costs at least one value and one byte.
        Rows can collapse into one state through baseline values, so only rows
        that assign the same names and differ in a non-object value count as
        distinct states.
        """

        names = tuple(sorted({name for row in spec.states.values() for name in row}))
        groups: dict[frozenset[str], dict[bytes, JsonObject]] = {}
        for row in spec.states.values():
            authored = portable_json_object(row, "state row")
            distinct = canonical_bytes(
                {
                    name: None if isinstance(value, dict) else value
                    for name, value in authored.items()
                }
            )
            groups.setdefault(frozenset(authored), {}).setdefault(
                distinct,
                {**dict.fromkeys(names, 0), **authored},
            )
        rows = max(groups.values(), key=len)
        return cls(
            inputs=names,
            outputs=tuple(spec.outputs),
            optional_outputs=optional_outputs(spec),
            aliases=tuple(spec.states),
            states={str(position): inputs for position, inputs in enumerate(rows.values())},
        )

    def measure(self, fingerprint: str, entry: StateEntry) -> None:
        """Count one prepared state at its exact index cost."""

        self._set(fingerprint, _cost(entry.to_value()))

    def budget(self) -> IndexBudget:
        total = self._skeleton + self._members + _Cost(0, max(len(self._entries) - 1, 0))
        return IndexBudget(
            index_values=total.values,
            max_index_values=_MAX_INDEX_VALUES,
            index_bytes=total.bytes,
            max_index_bytes=_MAX_INDEX_BYTES,
        )

    def require_fits(self) -> None:
        """Raise ``export_invalid`` when even the lower bound exceeds a limit."""

        budget = self.budget()
        states = len(self._entries)
        if budget.index_values > budget.max_index_values:
            raise NotebookExportError(
                f"the export index needs at least {budget.index_values:,} JSON values, more "
                f"than the {budget.max_index_values:,}-value limit, so "
                + _reduction(states, budget.index_values, budget.max_index_values),
                code="export_invalid",
                details={
                    "index_values": budget.index_values,
                    "max_index_values": budget.max_index_values,
                },
            )
        if budget.index_bytes > budget.max_index_bytes:
            raise NotebookExportError(
                f"the export index needs at least {budget.index_bytes:,} bytes, more than "
                f"the {budget.max_index_bytes:,}-byte limit, so "
                + _reduction(states, budget.index_bytes, budget.max_index_bytes),
                code="export_invalid",
                details={
                    "index_bytes": budget.index_bytes,
                    "max_index_bytes": budget.max_index_bytes,
                },
            )

    def _minimal_entry(self, inputs: JsonObject) -> _Cost:
        return _cost({"inputs": inputs, "outputs": {}}) + self._minimal_outputs

    def _set(self, key: str, entry: _Cost) -> None:
        previous = self._entries.get(key)
        if previous is not None:
            self._members -= _member(_DIGEST, previous)
        self._entries[key] = entry
        self._members += _member(_DIGEST, entry)


def _reduction(states: int, needed: int, limit: int) -> str:
    keep = states * limit // needed
    if 0 < keep < states:
        return f"export at most about {keep:,} of the {states:,} states, or fewer outputs"
    return "export fewer outputs"


def _skeleton(inputs: tuple[str, ...], outputs: tuple[str, ...], aliases: tuple[str, ...]) -> _Cost:
    """Measure the index root with an empty ``states`` object."""

    placeholder: JsonObject = {name: 0 for name in inputs}
    fingerprint = state_fingerprint(placeholder)
    descriptor = JsonDescriptor(value=0, provenance=Provenance(python_type=_SHORTEST_TEXT))
    index = ExportIndex(
        spec_sha256=_DIGEST,
        default_state=fingerprint,
        notebook=NotebookProvenance(filename=_SHORTEST_TEXT, document_sha256=_DIGEST),
        producer=ProducerProvenance(
            marimo=_SHORTEST_TEXT,
            marimo_export=_SHORTEST_TEXT,
            implementation_sha256=_DIGEST,
        ),
        inputs=inputs,
        control_bindings={},
        outputs=outputs,
        aliases=dict.fromkeys(aliases, fingerprint),
        states={
            fingerprint: StateEntry(
                inputs=placeholder,
                outputs=dict.fromkeys(outputs, descriptor),
            )
        },
    )
    root = index.to_value()
    root["states"] = {}
    return _cost(root)


__all__ = ["IndexProjection"]
