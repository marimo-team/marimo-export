"""Decode authenticated kernel planning records into package-owned values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from marimo_export._format import identifier_name, ordered_names
from marimo_export._json import canonical_bytes, json_object, sha256_bytes
from marimo_export._services.identity import ProducerIdentity
from marimo_export.errors import ExecutionError
from marimo_export.index import ProducerProvenance
from marimo_export.planning import ExportPlan, PlannedState, output_plan_sha256
from marimo_export.spec import ExportSpec
from marimo_export.wire import portable_json


def decode_plan_wire(
    value: Mapping[str, object],
    spec: ExportSpec,
    identity: ProducerIdentity,
) -> ExportPlan:
    root = json_object(value, "export plan")
    expected = {
        "default_alias",
        "default_fingerprint",
        "document_sha256",
        "environment_sha256",
        "filename",
        "implementation_sha256",
        "inputs",
        "output_plan_sha256",
        "outputs",
        "producer",
        "producer_sha256",
        "source_sha256",
        "spec_sha256",
        "states",
    }
    if set(root) != expected:
        raise ExecutionError("the kernel returned an invalid export plan", code="session_error")
    if root["implementation_sha256"] != identity.implementation_sha256:
        raise ExecutionError(
            "the kernel and client marimo-export implementations differ",
            code="implementation_changed",
        )
    if root["producer_sha256"] != identity.producer_sha256:
        raise ExecutionError(
            "the kernel and client producer identities differ",
            code="implementation_changed",
        )
    if root["producer"] != identity.provenance.to_value():
        raise ExecutionError(
            "the kernel and client producer provenance differ",
            code="implementation_changed",
        )
    if (
        root["source_sha256"] != identity.source_sha256
        or root["environment_sha256"] != identity.environment_sha256
        or root["filename"] != identity.filename
    ):
        raise ExecutionError(
            "the kernel and client producer source facts differ",
            code="implementation_changed",
        )
    if root["document_sha256"] != identity.document_sha256:
        raise ExecutionError(
            "the notebook document changed during export planning",
            code="parent_document_changed",
        )
    if root["spec_sha256"] != spec_sha256(spec):
        raise ExecutionError(
            "the kernel returned a plan for another export specification",
            code="session_error",
        )
    if root["output_plan_sha256"] != output_plan_sha256(spec):
        raise ExecutionError(
            "the kernel returned a plan for another output selection",
            code="session_error",
        )
    if root["default_alias"] != spec.default_state:
        raise ExecutionError(
            "the kernel returned an unexpected default state",
            code="session_error",
        )
    inputs = _names(root["inputs"], "export plan inputs", identifiers=True)
    outputs = _names(root["outputs"], "export plan outputs", identifiers=False)
    if outputs != tuple(spec.outputs):
        raise ExecutionError("the kernel returned unexpected export outputs", code="session_error")
    states_value = root["states"]
    if not isinstance(states_value, list) or not states_value:
        raise ExecutionError("the kernel returned no export states", code="session_error")
    states = tuple(_planned_state(item) for item in states_value)
    return ExportPlan(
        document_sha256=cast(str, root["document_sha256"]),
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=cast(str, root["output_plan_sha256"]),
        spec_sha256=cast(str, root["spec_sha256"]),
        default_alias=cast(str, root["default_alias"]),
        default_fingerprint=cast(str, root["default_fingerprint"]),
        inputs=inputs,
        states=states,
        outputs=outputs,
        reusable_states=(),
        missing_states=tuple(sorted(state.fingerprint for state in states)),
        observations=(),
    )


def producer_from_plan_wire(value: Mapping[str, object]) -> ProducerIdentity:
    """Return authenticated producer facts reported by a kernel plan."""

    root = json_object(value, "export plan")
    producer_value = json_object(root.get("producer"), "export plan producer")
    if set(producer_value) != {"implementation_sha256", "marimo", "marimo_export"}:
        raise ExecutionError(
            "the kernel returned invalid producer provenance",
            code="session_error",
        )
    provenance = ProducerProvenance(
        marimo=cast(str, producer_value["marimo"]),
        marimo_export=cast(str, producer_value["marimo_export"]),
        implementation_sha256=cast(str, producer_value["implementation_sha256"]),
    )
    document = cast(str, root.get("document_sha256"))
    producer_sha256 = cast(str, root.get("producer_sha256"))
    return ProducerIdentity(
        source=None,
        filename=cast(str | None, root.get("filename")),
        source_sha256=cast(str, root.get("source_sha256")),
        document_sha256=document,
        producer_sha256=producer_sha256,
        marimo_version=provenance.marimo,
        marimo_export_version=provenance.marimo_export,
        implementation_sha256=provenance.implementation_sha256,
        environment_sha256=cast(str, root.get("environment_sha256")),
    )


def spec_sha256(spec: ExportSpec) -> str:
    return sha256_bytes(canonical_bytes(spec.to_value()))


def _planned_state(value: object) -> PlannedState:
    item = json_object(value, "planned state")
    if set(item) != {"aliases", "fingerprint", "inputs"}:
        raise ExecutionError("the kernel returned an invalid planned state", code="session_error")
    aliases = _names(item["aliases"], "planned state aliases", identifiers=False)
    inputs = portable_json(item["inputs"], "planned state inputs")
    if not isinstance(inputs, dict):
        raise ExecutionError("the kernel returned invalid planned inputs", code="session_error")
    return PlannedState(
        aliases=tuple(sorted(aliases)),
        inputs=inputs,
        fingerprint=cast(str, item["fingerprint"]),
    )


def _names(value: object, label: str, *, identifiers: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ExecutionError(f"the kernel returned invalid {label}", code="session_error")
    names = cast(list[str], value)
    if identifiers:
        parsed = tuple(identifier_name(name, label) for name in names)
        if parsed != tuple(sorted(set(parsed))):
            raise ExecutionError(f"the kernel returned invalid {label}", code="session_error")
        return parsed
    return ordered_names(tuple(names), label, identifier=False, nonempty=True)


__all__ = ["decode_plan_wire", "producer_from_plan_wire", "spec_sha256"]
