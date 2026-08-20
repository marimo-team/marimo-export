from __future__ import annotations

from types import MappingProxyType

import pytest
from marimo_export import ExportSpec, OutputSpec
from marimo_export._execution import (
    Baseline,
    Definition,
    create_execution_plan,
)
from marimo_export._execution.plan import public_export_plan
from marimo_export.planning import (
    ExportPlan,
    PlannedState,
    export_plan_identity,
    output_plan_sha256,
)


def _baseline() -> Baseline:
    return Baseline(
        definitions={
            "selector": Definition(
                name="selector",
                cell_id="cell-selector",
                siblings=("selector",),
                kind="ui",
                python_type="marimo.ui.dropdown",
                value=object(),
                frontend_value="AAPL",
            ),
            "result": Definition(
                name="result",
                cell_id="cell-result",
                siblings=("result",),
                kind="ordinary",
                python_type="builtins.str",
                value="AAPL result",
                input_dependencies=("selector",),
            ),
        },
        cells=(),
        document_sha256="a" * 64,
        filename="report.py",
    )


def _spec(*, default_state: str = "focus") -> ExportSpec:
    return ExportSpec(
        default_state=default_state,
        states={
            "baseline": {},
            "same-baseline": {"selector": "AAPL"},
            "focus": {"selector": "MSFT"},
        },
        outputs={"result": OutputSpec.value("result")},
    )


def test_execution_plan_converts_to_the_public_contract() -> None:
    execution = create_execution_plan(_spec(), _baseline())
    baseline_fingerprint = execution.states[0].fingerprint

    plan = public_export_plan(
        execution,
        producer_sha256="b" * 64,
        reusable_states=(baseline_fingerprint,),
        exact_reuse=False,
    )

    assert isinstance(plan, ExportPlan)
    assert plan.document_sha256 == "a" * 64
    assert plan.producer_sha256 == "b" * 64
    assert plan.output_plan_sha256 == output_plan_sha256(_spec())
    assert plan.spec_sha256 == execution.spec_sha256
    assert plan.default_alias == "focus"
    assert plan.default_fingerprint == execution.default_fingerprint
    assert plan.inputs == ("selector",)
    assert plan.outputs == ("result",)
    assert plan.reusable_states == (baseline_fingerprint,)
    assert plan.missing_states == (execution.states[1].fingerprint,)
    assert plan.exact_reuse is False
    assert plan.identity == export_plan_identity(
        producer_sha256=plan.producer_sha256,
        output_plan_sha256=plan.output_plan_sha256,
        spec_sha256=plan.spec_sha256,
    )
    assert plan.to_dict()["states"] == [state.to_dict() for state in plan.states]


def test_authored_default_alias_survives_state_deduplication() -> None:
    execution = create_execution_plan(_spec(default_state="same-baseline"), _baseline())

    plan = public_export_plan(
        execution,
        producer_sha256="b" * 64,
        reusable_states=tuple(state.fingerprint for state in execution.states),
        exact_reuse=True,
    )

    assert execution.states[0].aliases == ("baseline", "same-baseline")
    assert plan.default_alias == "same-baseline"
    assert plan.default_fingerprint == execution.states[0].fingerprint
    assert plan.reusable_states == tuple(sorted(plan.state_fingerprints))
    assert plan.missing_states == ()
    assert plan.exact_reuse is True


def test_output_plan_identity_uses_only_authored_output_declarations() -> None:
    first = _spec(default_state="baseline")
    changed_states = ExportSpec(
        default_state="other",
        states={"other": {"selector": "GOOGL"}},
        outputs={"result": OutputSpec.value("result")},
    )
    changed_output = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"rendered": OutputSpec.output("result")},
    )

    assert output_plan_sha256(first) == output_plan_sha256(changed_states)
    assert output_plan_sha256(first) != output_plan_sha256(changed_output)


def test_planned_state_is_immutable_and_matches_its_fingerprint() -> None:
    execution = create_execution_plan(_spec(), _baseline())
    source = execution.states[0]
    state = PlannedState(
        aliases=source.aliases,
        inputs=source.inputs,
        fingerprint=source.fingerprint,
    )

    assert isinstance(state.inputs, MappingProxyType)
    with pytest.raises(TypeError):
        state.inputs["selector"] = "MSFT"  # type: ignore[index]
    with pytest.raises(ValueError, match="match its complete inputs"):
        PlannedState(
            aliases=("baseline",),
            inputs={"selector": "AAPL"},
            fingerprint="f" * 64,
        )


def test_exact_reuse_requires_every_state() -> None:
    execution = create_execution_plan(_spec(), _baseline())

    with pytest.raises(ValueError, match="exact reuse"):
        public_export_plan(
            execution,
            producer_sha256="b" * 64,
            reusable_states=(execution.states[0].fingerprint,),
            exact_reuse=True,
        )


def test_public_plan_rejects_incomplete_reuse_partition() -> None:
    execution = create_execution_plan(_spec(), _baseline())
    states = tuple(
        PlannedState(
            aliases=state.aliases,
            inputs=state.inputs,
            fingerprint=state.fingerprint,
        )
        for state in execution.states
    )

    with pytest.raises(ValueError, match="cover every state"):
        ExportPlan(
            document_sha256=execution.document_sha256,
            producer_sha256="b" * 64,
            output_plan_sha256=execution.output_plan_sha256,
            spec_sha256=execution.spec_sha256,
            default_alias=execution.default_alias,
            default_fingerprint=execution.default_fingerprint,
            inputs=execution.inputs,
            states=states,
            outputs=execution.outputs,
            reusable_states=(),
            missing_states=(),
        )
