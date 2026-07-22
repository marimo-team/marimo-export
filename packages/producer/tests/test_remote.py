from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import Any, cast

import marimo
import pytest
from marimo._ast.app_config import _AppConfig
from marimo._config.config import DEFAULT_CONFIG
from marimo._messaging.types import KernelStreams, NoopStream
from marimo._runtime.commands import AppMetadata
from marimo._runtime.kernel_lifecycle import KernelArgs, kernel_session
from marimo._session.model import SessionMode
from marimo_export.errors import (
    InvalidPlanError,
    ScenarioBuildError,
    UnsupportedProducerModeError,
)
from marimo_export.remote import PROTOCOL, RESPONSE_PREFIX, dispatch_json


def dispatch(request: dict[str, object]) -> dict[str, object]:
    return json.loads(asyncio.run(dispatch_json(json.dumps(request))))


def test_describe_uses_the_versioned_remote_envelope() -> None:
    response = dispatch(
        {
            "protocol": PROTOCOL,
            "request_id": "describe-1",
            "operation": "describe",
            "params": {},
        }
    )

    assert RESPONSE_PREFIX == "__MARIMO_EXPORT_RESPONSE__:"
    assert response["ok"] is True
    data = cast(dict[str, object], response["data"])
    assert data["protocol"] == PROTOCOL
    assert data["marimo_export_version"] == "0.0.0"


def test_protocol_error_is_structured_and_correlated() -> None:
    response = dispatch(
        {
            "protocol": "wrong",
            "request_id": "request-1",
            "operation": "describe",
            "params": {},
        }
    )

    assert response == {
        "protocol": PROTOCOL,
        "request_id": "request-1",
        "ok": False,
        "error": {"code": "protocol_mismatch", "message": f"expected protocol {PROTOCOL}"},
    }


def test_unknown_operation_is_an_invalid_request() -> None:
    response = dispatch(
        {
            "protocol": PROTOCOL,
            "request_id": "request-2",
            "operation": "unknown",
            "params": {},
        }
    )

    assert response == {
        "protocol": PROTOCOL,
        "request_id": "request-2",
        "ok": False,
        "error": {"code": "invalid_request", "message": "unknown operation: 'unknown'"},
    }


def test_stage_rejects_an_invalid_ref() -> None:
    response = dispatch(
        {
            "protocol": PROTOCOL,
            "request_id": "bad-ref",
            "operation": "stage",
            "params": {"ref": {}},
        }
    )

    assert response["ok"] is False
    error = cast(dict[str, object], response["error"])
    assert error["code"] == "invalid_ref"


def test_unsupported_marimo_is_reported_before_worker_import(monkeypatch) -> None:
    monkeypatch.setattr(marimo, "__version__", "99.0.0")
    monkeypatch.delitem(sys.modules, "marimo_export.worker", raising=False)

    response = dispatch(
        {
            "protocol": PROTOCOL,
            "request_id": "unsupported",
            "operation": "describe",
            "params": {},
        }
    )

    assert response["ok"] is False
    error = cast(dict[str, object], response["error"])
    assert error["code"] == "unsupported_marimo"
    assert "marimo_export.worker" not in sys.modules


@pytest.mark.parametrize(
    ("mode", "execution_type", "message"),
    [
        pytest.param(SessionMode.RUN, "relaxed", "marimo edit", id="run-mode"),
        pytest.param(
            SessionMode.EDIT,
            "strict",
            "fresh `__marimo__/cache`",
            id="strict-execution",
        ),
    ],
)
def test_build_rejects_an_unsupported_real_kernel_before_producer_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: SessionMode,
    execution_type: str,
    message: str,
) -> None:
    from marimo_export._marimo import cache, context, runner

    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\napp = marimo.App()\n", encoding="utf-8")
    producer_io: list[str] = []

    def unexpected(name: str):
        def fail(*args: object, **kwargs: object) -> object:
            del args, kwargs
            producer_io.append(name)
            raise AssertionError(f"unsupported build reached {name}")

        return fail

    monkeypatch.setattr(context, "notebook_snapshot", unexpected("snapshot"))
    monkeypatch.setattr(runner, "run_scenario_in_child", unexpected("scenario"))
    monkeypatch.setattr(runner, "put_payload", unexpected("payload write"))
    monkeypatch.setattr(cache, "put_index", unexpected("index write"))
    user_config: Any = copy.deepcopy(DEFAULT_CONFIG)
    user_config.setdefault("experimental", {})["execution_type"] = execution_type
    args = KernelArgs(
        streams=KernelStreams(
            stream=NoopStream(),
            stdout=None,
            stderr=None,
            stdin=None,
        ),
        debugger=None,
        configs={},
        app_metadata=AppMetadata(
            query_params={},
            cli_args={},
            app_config=_AppConfig(),
            filename=str(notebook),
            argv=[],
        ),
        user_config=user_config,
        mode=mode,
        control_queue=asyncio.Queue(),
        set_ui_element_queue=asyncio.Queue(),
        virtual_file_storage="shared_memory",
    )
    process_argv = sys.argv
    process_path = sys.path[:]
    try:
        with kernel_session(args):
            response = dispatch(
                {
                    "protocol": PROTOCOL,
                    "request_id": f"{mode.value}-{execution_type}-build",
                    "operation": "build",
                    "params": {
                        "plan": {
                            "schema": "marimo-export.plan.v1",
                            "outputs": {
                                "value": {
                                    "source": "value",
                                    "formats": {"json": {}},
                                }
                            },
                        }
                    },
                }
            )
    finally:
        sys.argv = process_argv
        sys.path[:] = process_path

    assert response["ok"] is False
    error = cast(dict[str, object], response["error"])
    assert error["code"] == "unsupported_mode"
    assert message in cast(str, error["message"])
    assert producer_io == []
    assert not (tmp_path / "__marimo__").exists()


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (RuntimeError("failed"), "build_failed"),
        (
            ScenarioBuildError("market-open", RuntimeError("projection failed")),
            "scenario_failed",
        ),
        (UnsupportedProducerModeError("use marimo edit"), "unsupported_mode"),
    ],
)
def test_build_errors_use_typed_operation_codes(
    monkeypatch, error: BaseException, expected_code: str
) -> None:
    from marimo_export import worker

    async def fail_build(plan: object) -> object:
        del plan
        raise error

    monkeypatch.setattr(worker, "build", fail_build)
    response = dispatch(
        {
            "protocol": PROTOCOL,
            "request_id": "failed-build",
            "operation": "build",
            "params": {"plan": {}},
        }
    )

    assert response["ok"] is False
    remote_error = cast(dict[str, object], response["error"])
    assert remote_error["code"] == expected_code


def test_scenario_failure_identifies_the_scenario_once(monkeypatch) -> None:
    from marimo_export import worker

    error = ScenarioBuildError("market-open", RuntimeError("projection failed"))

    async def fail_build(plan: object) -> object:
        del plan
        raise error

    monkeypatch.setattr(worker, "build", fail_build)
    response = dispatch(
        {
            "protocol": PROTOCOL,
            "request_id": "failed-scenario",
            "operation": "build",
            "params": {"plan": {}},
        }
    )

    assert response == {
        "protocol": PROTOCOL,
        "request_id": "failed-scenario",
        "ok": False,
        "error": {
            "code": "scenario_failed",
            "message": "scenario 'market-open' failed: projection failed",
            "details": {"scenario_id": "market-open"},
        },
    }


def test_invalid_plan_preserves_the_decoder_path(monkeypatch) -> None:
    from marimo_export import worker

    error = InvalidPlanError("plan.outputs.summary.formats.json.options.indent must be an integer")

    async def fail_build(plan: object) -> object:
        del plan
        raise error

    monkeypatch.setattr(worker, "build", fail_build)
    response = dispatch(
        {
            "protocol": PROTOCOL,
            "request_id": "invalid-plan",
            "operation": "build",
            "params": {"plan": {}},
        }
    )

    assert response == {
        "protocol": PROTOCOL,
        "request_id": "invalid-plan",
        "ok": False,
        "error": {
            "code": "invalid_plan",
            "message": ("plan.outputs.summary.formats.json.options.indent must be an integer"),
        },
    }
