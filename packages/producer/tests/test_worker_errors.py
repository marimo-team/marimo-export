from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from marimo_export import worker
from marimo_export.errors import InvalidPlanError, ScenarioBuildError


def test_worker_wraps_a_scenario_failure_once_and_preserves_its_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marimo_export._marimo import context, runner

    cause = ValueError("projection failed")
    scenario = SimpleNamespace(id="market-open")
    plan = SimpleNamespace(scenarios=(scenario,))

    async def fail_scenario(*args: object) -> object:
        del args
        raise cause

    monkeypatch.setattr(worker, "require_supported_marimo", lambda: "0.23.14")
    monkeypatch.setattr(worker, "decode_plan", lambda value: plan)
    monkeypatch.setattr(context, "require_producer_context", lambda: None)
    monkeypatch.setattr(context, "notebook_snapshot", lambda: object())
    monkeypatch.setattr(runner, "run_scenario_in_child", fail_scenario)

    with pytest.raises(ScenarioBuildError) as raised:
        asyncio.run(worker.build({}))

    error = raised.value
    assert error.scenario_id == "market-open"
    assert error.cause is cause
    assert error.cause_message == "projection failed"
    assert str(error) == "scenario 'market-open' failed: projection failed"
    assert error.__cause__ is cause


def test_worker_preserves_the_plan_error_path_and_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cause = ValueError("plan.outputs.summary.formats.json.options.indent is invalid")

    def fail_decode(value: object) -> Any:
        del value
        raise cause

    monkeypatch.setattr(worker, "require_supported_marimo", lambda: "0.23.14")
    monkeypatch.setattr(worker, "decode_plan", fail_decode)

    with pytest.raises(InvalidPlanError) as raised:
        asyncio.run(worker.build({}))

    assert str(raised.value) == str(cause)
    assert raised.value.__cause__ is cause
