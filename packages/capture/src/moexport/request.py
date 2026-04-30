"""Resolve user export specs into concrete kernel-side export requests."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeAlias

from marimo._runtime.context import get_context
from marimo._utils.paths import notebook_output_dir

from moexport.evaluate import EvaluateResult, evaluate
from moexport.jsonio import manifest_value, sha256_bytes, sha256_json
from moexport.spec import CodeStateValue, ExportSpec, ScenarioSpec, ValueSpec

EvaluateFn: TypeAlias = Callable[[str, Any], Awaitable[EvaluateResult]]


class NotebookSource:
    """Notebook source bytes used for provenance and request identity."""

    __slots__ = ("content", "name", "sha256")

    def __init__(
        self,
        *,
        name: str | None,
        sha256: str | None,
        content: bytes | None,
    ) -> None:
        self.name = name
        self.sha256 = sha256
        self.content = content


class ResolvedScenario:
    """One scenario after authored state values have been evaluated."""

    __slots__ = (
        "definition_overrides",
        "id",
        "manifest_state",
        "object_patches",
        "state",
        "declared_state",
    )

    def __init__(
        self,
        *,
        id: str,
        state: dict[str, Any],
        definition_overrides: dict[str, Any],
        object_patches: dict[str, Any],
        manifest_state: dict[str, Any],
        declared_state: dict[str, Any] | None,
    ) -> None:
        self.id = id
        self.state = state
        self.definition_overrides = definition_overrides
        self.object_patches = object_patches
        self.manifest_state = manifest_state
        self.declared_state = declared_state


class ScenarioSetIdentity:
    """Stable id for the resolved scenario matrix."""

    __slots__ = ("id", "sha256")

    def __init__(self, *, id: str, sha256: str) -> None:
        self.id = id
        self.sha256 = sha256


class ExportIdentity:
    """Stable id for one notebook source, scenario set, and value plan."""

    __slots__ = ("id", "sha256")

    def __init__(self, *, id: str, sha256: str) -> None:
        self.id = id
        self.sha256 = sha256


class ResolvedExportRequest:
    """All decisions needed before evaluating and writing artifacts."""

    __slots__ = (
        "blob_base_path",
        "blob_href_prefix",
        "export_identity",
        "notebook_source",
        "output_root",
        "scenario_set_identity",
        "scenarios",
        "spec",
        "target",
    )

    def __init__(
        self,
        *,
        spec: ExportSpec,
        notebook_source: NotebookSource,
        scenarios: list[ResolvedScenario],
        scenario_set_identity: ScenarioSetIdentity,
        export_identity: ExportIdentity,
        output_root: Path,
        blob_base_path: Path,
        blob_href_prefix: str,
        target: str,
    ) -> None:
        self.spec = spec
        self.notebook_source = notebook_source
        self.scenarios = scenarios
        self.scenario_set_identity = scenario_set_identity
        self.export_identity = export_identity
        self.output_root = output_root
        self.blob_base_path = blob_base_path
        self.blob_href_prefix = blob_href_prefix
        self.target = target


async def resolve_export_request(
    spec: ExportSpec,
    *,
    bundle: str | Path | None = None,
    evaluate_fn: EvaluateFn = evaluate,
) -> ResolvedExportRequest:
    """Resolve source, scenarios, identities, output path, and target expr."""

    notebook_path = _resolve_notebook_path(spec)
    notebook_source = _read_notebook_source(notebook_path)
    scenarios = await _resolve_scenarios(spec.scenarios, evaluate_fn=evaluate_fn)
    scenario_set_identity = _scenario_set_identity(scenarios)
    export_identity = _export_identity(spec, notebook_source, scenario_set_identity)
    output_root = _resolve_output_root(
        spec=spec,
        override=bundle,
        notebook_path=notebook_path,
    )
    return ResolvedExportRequest(
        spec=spec,
        notebook_source=notebook_source,
        scenarios=scenarios,
        scenario_set_identity=scenario_set_identity,
        export_identity=export_identity,
        output_root=output_root,
        blob_base_path=output_root,
        blob_href_prefix="blobs",
        target=_target_expression(spec.values),
    )


def _resolve_notebook_path(spec: ExportSpec) -> str | None:
    ctx = get_context()
    return ctx.filename or spec.notebook


def _read_notebook_source(notebook_path: str | None) -> NotebookSource:
    if notebook_path is None:
        return NotebookSource(name=None, sha256=None, content=None)

    path = Path(notebook_path)
    source = path.read_bytes()
    return NotebookSource(
        name=path.name,
        sha256=sha256_bytes(source),
        content=source,
    )


async def _resolve_scenarios(
    scenarios: list[ScenarioSpec],
    *,
    evaluate_fn: EvaluateFn,
) -> list[ResolvedScenario]:
    resolved = [
        await _resolve_scenario(scenario, evaluate_fn=evaluate_fn)
        for scenario in scenarios
    ]
    # Scenarios are named materializations. Sort by id so the same scenario set
    # has one path and one manifest even if the authored list is reordered.
    return sorted(resolved, key=lambda scenario: scenario.id)


async def _resolve_scenario(
    scenario: ScenarioSpec,
    *,
    evaluate_fn: EvaluateFn,
) -> ResolvedScenario:
    state = await _resolve_value_mapping(
        scenario.state,
        evaluate_fn=evaluate_fn,
    )
    definition_overrides, object_patches = _split_state(state)
    manifest_state = {name: manifest_value(value) for name, value in state.items()}
    declared_state = _dump_declared_state(scenario.state)
    return ResolvedScenario(
        id=scenario.id,
        state=state,
        definition_overrides=definition_overrides,
        object_patches=object_patches,
        manifest_state=manifest_state,
        declared_state=declared_state if declared_state != manifest_state else None,
    )


async def _resolve_value_mapping(
    values: Mapping[str, Any],
    *,
    evaluate_fn: EvaluateFn,
) -> dict[str, Any]:
    literals = {
        name: value
        for name, value in values.items()
        if not isinstance(value, CodeStateValue)
    }
    resolved: dict[str, Any] = dict(literals)

    for name, value in values.items():
        if not isinstance(value, CodeStateValue):
            continue

        result = await evaluate_fn(value.expression, resolved)
        resolved[name] = result["results"][0]["value"]

    return resolved


def _scenario_set_identity(
    scenarios: list[ResolvedScenario],
) -> ScenarioSetIdentity:
    # Hash by scenario id and resolved state. Author order remains
    # visible in the manifest but does not create a different scenario group.
    payload = {
        "scenarios": sorted(
            (
                {
                    "id": scenario.id,
                    "state": scenario.manifest_state,
                }
                for scenario in scenarios
            ),
            key=lambda scenario: scenario["id"],
        )
    }
    digest = sha256_json(payload)
    return ScenarioSetIdentity(id=f"sha256-{digest[:16]}", sha256=digest)


def _export_identity(
    spec: ExportSpec,
    notebook_source: NotebookSource,
    scenario_set_identity: ScenarioSetIdentity,
) -> ExportIdentity:
    # The export id addresses the request, not one run's produced bytes. The
    # same notebook source, scenario set, and value/format plan stay idempotent.
    request = {
        "notebook_sha256": notebook_source.sha256,
        "scenario_set_sha256": scenario_set_identity.sha256,
        "values": spec.model_dump(
            mode="json",
            exclude={"bundle", "notebook", "scenarios"},
        )["values"],
    }
    digest = sha256_json(request)
    return ExportIdentity(id=f"sha256-{digest[:16]}", sha256=digest)


def _resolve_output_root(
    *,
    spec: ExportSpec,
    override: str | Path | None,
    notebook_path: str | None,
) -> Path:
    if override is not None:
        return Path(override)

    if spec.bundle is not None:
        return Path(spec.bundle.path)

    return notebook_output_dir(notebook_path) / "static-export"


def _target_expression(values: Mapping[str, ValueSpec]) -> str:
    items = [f"{name!r}: ({value.source})" for name, value in values.items()]
    return "{\n" + ",\n".join(f"  {item}" for item in items) + "\n}"


def _split_state(state: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    definition_overrides: dict[str, Any] = {}
    object_patches: dict[str, Any] = {}
    for target, value in state.items():
        if "." in target:
            object_patches[target] = value
        else:
            definition_overrides[target] = value
    return definition_overrides, object_patches


def _dump_declared_state(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: value.model_dump(mode="json")
        if isinstance(value, CodeStateValue)
        else manifest_value(value)
        for name, value in state.items()
    }
