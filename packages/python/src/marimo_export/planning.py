"""Stable records describing resolved export preparation work."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from marimo_export._format import digest, export_name, identifier_name, ordered_names
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    sha256_bytes,
)
from marimo_export.spec import ExportSpec
from marimo_export.wire import FrozenJsonObject, _freeze_json, portable_json, state_fingerprint

if TYPE_CHECKING:
    from marimo_export.reader import NotebookExport
    from marimo_export.repository import ObservedState


@dataclass(frozen=True, slots=True, init=False)
class PlannedState:
    """One normalized input vector and every authored alias that selects it."""

    aliases: tuple[str, ...]
    fingerprint: str
    _inputs_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        aliases: tuple[str, ...],
        inputs: Mapping[str, JsonValue],
        fingerprint: str,
    ) -> None:
        if not isinstance(aliases, tuple) or not aliases:
            raise ValueError("planned state aliases must be a nonempty tuple")
        parsed_aliases = tuple(export_name(alias, "planned state alias") for alias in aliases)
        if parsed_aliases != tuple(sorted(set(parsed_aliases))):
            raise ValueError("planned state aliases must be sorted and unique")
        parsed_inputs = portable_json(inputs, "planned state inputs")
        if not isinstance(parsed_inputs, dict):
            raise TypeError("planned state inputs must be an object")
        parsed_fingerprint = digest(fingerprint, "planned state fingerprint")
        if state_fingerprint(parsed_inputs) != parsed_fingerprint:
            raise ValueError("planned state fingerprint must match its complete inputs")
        object.__setattr__(self, "aliases", parsed_aliases)
        object.__setattr__(self, "fingerprint", parsed_fingerprint)
        object.__setattr__(self, "_inputs_bytes", canonical_bytes(parsed_inputs))

    @property
    def inputs(self) -> FrozenJsonObject:
        """Return the complete immutable input vector."""

        return cast(
            FrozenJsonObject,
            _freeze_json(decode_json_object(self._inputs_bytes, "planned state inputs")),
        )

    def to_dict(self) -> JsonObject:
        return {
            "aliases": list(self.aliases),
            "fingerprint": self.fingerprint,
            "inputs": portable_json(self.inputs, "planned state inputs"),
        }


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """Resolved export work returned before preparation begins."""

    document_sha256: str
    producer_sha256: str
    output_plan_sha256: str
    spec_sha256: str
    default_alias: str
    default_fingerprint: str
    inputs: tuple[str, ...]
    states: tuple[PlannedState, ...]
    outputs: tuple[str, ...]
    reusable_states: tuple[str, ...]
    missing_states: tuple[str, ...]
    observation_revision: int = 0
    observations: tuple[ObservedState, ...] = ()
    exact_reuse: bool = False
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "document_sha256",
            "producer_sha256",
            "output_plan_sha256",
            "spec_sha256",
            "default_fingerprint",
        ):
            object.__setattr__(self, name, digest(getattr(self, name), f"export plan {name}"))
        object.__setattr__(
            self,
            "default_alias",
            export_name(self.default_alias, "export plan default_alias"),
        )
        if not isinstance(self.inputs, tuple):
            raise TypeError("export plan inputs must be a tuple")
        parsed_inputs = tuple(identifier_name(name, "export plan input") for name in self.inputs)
        if parsed_inputs != tuple(sorted(set(parsed_inputs))):
            raise ValueError("export plan inputs must be sorted and unique")
        object.__setattr__(self, "inputs", parsed_inputs)
        if not isinstance(self.states, tuple) or not self.states:
            raise ValueError("export plan states must be a nonempty tuple")
        if any(not isinstance(state, PlannedState) for state in self.states):
            raise TypeError("export plan states must contain PlannedState values")
        fingerprints = tuple(state.fingerprint for state in self.states)
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("export plan states must have unique fingerprints")
        expected_inputs = set(parsed_inputs)
        for state in self.states:
            if set(state.inputs) != expected_inputs:
                raise ValueError("every planned state must contain the export plan inputs")
        defaults = [state for state in self.states if self.default_alias in state.aliases]
        if len(defaults) != 1 or defaults[0].fingerprint != self.default_fingerprint:
            raise ValueError("export plan default alias and fingerprint must select one state")
        outputs = ordered_names(
            self.outputs,
            "export plan outputs",
            identifier=False,
            nonempty=True,
        )
        object.__setattr__(self, "outputs", outputs)
        reusable = _fingerprints(self.reusable_states, "export plan reusable_states")
        missing = _fingerprints(self.missing_states, "export plan missing_states")
        if set(reusable) & set(missing):
            raise ValueError("export plan reusable and missing states must be disjoint")
        if set(reusable) | set(missing) != set(fingerprints):
            raise ValueError("export plan reusable and missing states must cover every state")
        object.__setattr__(self, "reusable_states", reusable)
        object.__setattr__(self, "missing_states", missing)
        if (
            not isinstance(self.observation_revision, int)
            or isinstance(self.observation_revision, bool)
            or self.observation_revision < 0
        ):
            raise ValueError("export plan observation_revision must be a nonnegative integer")
        from marimo_export.repository import ObservedState

        if not isinstance(self.observations, tuple) or any(
            not isinstance(observation, ObservedState) for observation in self.observations
        ):
            raise TypeError("export plan observations must contain ObservedState values")
        for observation in self.observations:
            if observation.producer_sha256 != self.producer_sha256:
                raise ValueError("export plan observations must belong to its producer")
            if set(observation.values) != expected_inputs:
                raise ValueError("export plan observations must contain the export plan inputs")
        if not isinstance(self.exact_reuse, bool):
            raise TypeError("export plan exact_reuse must be a boolean")
        if self.exact_reuse and (missing or set(reusable) != set(fingerprints)):
            raise ValueError("exact reuse requires every planned state to be reusable")
        object.__setattr__(
            self,
            "identity",
            export_plan_identity(
                producer_sha256=self.producer_sha256,
                output_plan_sha256=self.output_plan_sha256,
                spec_sha256=self.spec_sha256,
            ),
        )

    @property
    def state_fingerprints(self) -> tuple[str, ...]:
        return tuple(state.fingerprint for state in self.states)

    def matches(self, notebook_export: NotebookExport) -> bool:
        """Return whether an opened export contains this resolved state relation."""

        from marimo_export.reader import NotebookExport

        if not isinstance(notebook_export, NotebookExport):
            raise TypeError("notebook_export must be a NotebookExport")
        if (
            notebook_export.spec_sha256 != self.spec_sha256
            or notebook_export.default_state.fingerprint != self.default_fingerprint
            or notebook_export.notebook.document_sha256 != self.document_sha256
            or notebook_export.input_names != self.inputs
            or notebook_export.output_names != self.outputs
        ):
            return False
        exported = {
            state.fingerprint: (tuple(state.aliases), dict(state.inputs))
            for state in notebook_export.states()
        }
        planned = {
            state.fingerprint: (tuple(state.aliases), dict(state.inputs)) for state in self.states
        }
        return exported == planned

    def to_dict(self) -> JsonObject:
        return {
            "identity": self.identity,
            "document_sha256": self.document_sha256,
            "producer_sha256": self.producer_sha256,
            "output_plan_sha256": self.output_plan_sha256,
            "spec_sha256": self.spec_sha256,
            "default_alias": self.default_alias,
            "default_fingerprint": self.default_fingerprint,
            "inputs": list(self.inputs),
            "states": [state.to_dict() for state in self.states],
            "outputs": list(self.outputs),
            "reusable_states": list(self.reusable_states),
            "missing_states": list(self.missing_states),
            "observation_revision": self.observation_revision,
            "observations": [
                {
                    "fingerprint": observation.fingerprint,
                    "revision": observation.revision,
                    "values": portable_json(observation.values, "observed state"),
                }
                for observation in self.observations
            ],
            "exact_reuse": self.exact_reuse,
        }


def export_plan_identity(
    *,
    producer_sha256: str,
    output_plan_sha256: str,
    spec_sha256: str,
) -> str:
    """Return the repository identity for one exact resolved export plan."""

    return sha256_bytes(
        canonical_bytes(
            {
                "output_plan_sha256": digest(
                    output_plan_sha256,
                    "output_plan_sha256",
                ),
                "producer_sha256": digest(producer_sha256, "producer_sha256"),
                "spec_sha256": digest(spec_sha256, "spec_sha256"),
            }
        )
    )


def output_plan_sha256(spec: ExportSpec) -> str:
    """Return the identity of the authored output declarations."""

    if not isinstance(spec, ExportSpec):
        raise TypeError("spec must be an ExportSpec")
    outputs = spec.to_value()["outputs"]
    return sha256_bytes(canonical_bytes(outputs))


def _fingerprints(values: tuple[str, ...], path: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{path} must be a tuple")
    parsed = tuple(digest(value, f"{path} item") for value in values)
    if parsed != tuple(sorted(set(parsed))):
        raise ValueError(f"{path} must be sorted and unique")
    return parsed


__all__ = [
    "ExportPlan",
    "PlannedState",
    "export_plan_identity",
    "output_plan_sha256",
]
