"""Persist prepared state artifacts and assemble exact notebook exports."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from marimo_export._json import JsonObject, json_object
from marimo_export._repository.preparation import (
    PreparationRepository,
    PreparedExportArtifact,
    PreparedState,
    RepositoryIdentity,
)
from marimo_export._services.identity import ProducerIdentity
from marimo_export.descriptors import (
    JsonDescriptor,
    OutputCodec,
    ScalarDescriptor,
    _descriptor,
    asset_path,
)
from marimo_export.errors import ExecutionError
from marimo_export.index import (
    ControlBinding,
    ExportIndex,
    NotebookProvenance,
    StateEntry,
    _control_binding,
)
from marimo_export.planning import ExportPlan, PlannedState
from marimo_export.spec import ExportSpec
from marimo_export.wire import portable_json


def single_state_spec(spec: ExportSpec, state: PlannedState) -> ExportSpec:
    alias = state.aliases[0]
    if alias not in spec.states:
        raise AssertionError("planned state alias is not present in the export spec")
    complete = portable_json(state.inputs, f"state {alias!r} complete inputs")
    if not isinstance(complete, dict):
        raise AssertionError("planned state inputs are not an object")
    return ExportSpec(
        default_state=alias,
        states={alias: complete},
        outputs=spec.outputs,
    )


def commit_captured_state(
    repository: PreparationRepository,
    plan: ExportPlan,
    state: PlannedState,
    producer: ProducerIdentity,
    index: ExportIndex,
    assets: Mapping[tuple[OutputCodec, str], bytes],
    commit_guard: Callable[[], None] | None = None,
) -> PreparedState:
    if (
        index.notebook.document_sha256 != plan.document_sha256
        or index.notebook.filename != producer.filename
        or index.producer != producer.provenance
        or index.outputs != plan.outputs
        or tuple(index.states) != (state.fingerprint,)
    ):
        raise ExecutionError(
            f"captured state {state.aliases[0]!r} does not match its export plan",
            code="state_execution_failed",
            details={"state": state.aliases[0]},
        )
    entry = index.states[state.fingerprint]
    metadata: JsonObject = {
        "inputs": entry.inputs,
        "outputs": {name: descriptor.to_value() for name, descriptor in entry.outputs.items()},
        "control_bindings": {
            object_id: binding.to_value() for object_id, binding in index.control_bindings.items()
        },
    }
    with repository.stage_prepared_state(
        producer_sha256=plan.producer_sha256,
        output_plan_sha256=plan.output_plan_sha256,
        state_fingerprint=state.fingerprint,
    ) as staged:
        for descriptor in entry.outputs.values():
            if isinstance(descriptor, (ScalarDescriptor, JsonDescriptor)):
                continue
            identity = (descriptor.codec, descriptor.asset.sha256)
            try:
                payload = assets[identity]
            except KeyError as error:
                raise ExecutionError(
                    f"captured state {state.aliases[0]!r} is missing an output asset",
                    code="cache_receipt_missing",
                    details={"state": state.aliases[0]},
                ) from error
            target = staged.path / asset_path(*identity)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        if commit_guard is not None:
            commit_guard()
        return staged.commit(metadata=metadata)


def assemble_export(
    repository: PreparationRepository,
    plan: ExportPlan,
    producer: ProducerIdentity,
    prepared: Mapping[str, PreparedState],
    replacing_instance: str | None = None,
    commit_guard: Callable[[], None] | None = None,
) -> PreparedExportArtifact:
    if set(prepared) != set(plan.state_fingerprints):
        raise ExecutionError(
            "prepared states do not cover the export plan",
            code="state_execution_failed",
        )
    entries, bindings = _prepared_records(plan, prepared)
    index = ExportIndex(
        spec_sha256=plan.spec_sha256,
        default_state=plan.default_fingerprint,
        notebook=NotebookProvenance(
            filename=producer.filename,
            document_sha256=plan.document_sha256,
        ),
        producer=producer.provenance,
        inputs=plan.inputs,
        control_bindings=bindings,
        outputs=plan.outputs,
        aliases={alias: state.fingerprint for state in plan.states for alias in state.aliases},
        states=entries,
    )
    with repository.stage_export(repository_identity(plan)) as staged:
        (staged.path / "index.json").write_bytes(index.to_bytes())
        _copy_assets(staged.path, index, entries, prepared)
        return staged.commit(
            states=[prepared[fingerprint] for fingerprint in plan.state_fingerprints],
            captured_observation_revision=plan.observation_revision,
            replacing_instance=replacing_instance,
            commit_guard=commit_guard,
        )


def repository_identity(plan: ExportPlan) -> RepositoryIdentity:
    return RepositoryIdentity(
        producer_sha256=plan.producer_sha256,
        output_plan_sha256=plan.output_plan_sha256,
        spec_sha256=plan.spec_sha256,
    )


def _prepared_records(
    plan: ExportPlan,
    prepared: Mapping[str, PreparedState],
) -> tuple[dict[str, StateEntry], dict[str, ControlBinding]]:
    entries: dict[str, StateEntry] = {}
    bindings: dict[str, ControlBinding] = {}
    for state in plan.states:
        metadata = json_object(prepared[state.fingerprint].metadata, "prepared state metadata")
        if set(metadata) != {"inputs", "outputs", "control_bindings"}:
            raise ExecutionError("prepared state metadata is invalid", code="export_invalid")
        outputs = json_object(metadata["outputs"], "prepared state outputs")
        entries[state.fingerprint] = StateEntry(
            inputs=json_object(metadata["inputs"], "prepared state inputs"),
            outputs={name: _descriptor(value, name) for name, value in outputs.items()},
        )
        control_values = json_object(
            metadata["control_bindings"],
            "prepared state control bindings",
        )
        for object_id, value in control_values.items():
            binding = _control_binding(value, object_id)
            previous = bindings.setdefault(object_id, binding)
            if previous != binding:
                raise ExecutionError(
                    f"UI object {object_id!r} changes input ownership across prepared states",
                    code="control_input_conflict",
                    details={"object_id": object_id},
                )
    return entries, bindings


def _copy_assets(
    destination: Path,
    index: ExportIndex,
    entries: Mapping[str, StateEntry],
    prepared: Mapping[str, PreparedState],
) -> None:
    for codec, asset in index.assets():
        relative = asset_path(codec, asset.sha256)
        source = next(
            (
                prepared[fingerprint].asset(relative)
                for fingerprint, entry in entries.items()
                if any(
                    not isinstance(descriptor, (ScalarDescriptor, JsonDescriptor))
                    and descriptor.codec == codec
                    and descriptor.asset.sha256 == asset.sha256
                    for descriptor in entry.outputs.values()
                )
            ),
            None,
        )
        if source is None:
            raise ExecutionError("prepared state asset is unavailable", code="export_invalid")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


__all__ = ["assemble_export", "commit_captured_state", "repository_identity", "single_state_spec"]
