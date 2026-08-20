from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from marimo_export.planning import ExportPlan, PlannedState
from marimo_export.progress import CacheActivity, ProgressEvent
from marimo_export.reader import VerificationResult
from marimo_export.repository import ObservedState
from marimo_export.result import ExportResult
from marimo_export.wire import state_fingerprint


def plan() -> ExportPlan:
    inputs = {"selector": "AAPL"}
    fingerprint = state_fingerprint(inputs)
    return ExportPlan(
        document_sha256="a" * 64,
        producer_sha256="b" * 64,
        output_plan_sha256="c" * 64,
        spec_sha256="d" * 64,
        default_alias="baseline",
        default_fingerprint=fingerprint,
        inputs=("selector",),
        states=(
            PlannedState(
                aliases=("baseline",),
                inputs=inputs,
                fingerprint=fingerprint,
            ),
        ),
        outputs=("count",),
        reusable_states=(),
        missing_states=(fingerprint,),
    )


def observed_plan() -> ExportPlan:
    resolved = plan()
    return replace(
        resolved,
        observation_revision=1,
        observations=(
            ObservedState(
                producer_sha256=resolved.producer_sha256,
                revision=1,
                values={"selector": "MSFT"},
            ),
        ),
    )


def result(path: Path) -> ExportResult:
    resolved = plan()
    fingerprint = resolved.state_fingerprints[0]
    return ExportResult(
        path=path.resolve(),
        identity="e" * 64,
        plan=resolved,
        reused=False,
        prepared_states=(fingerprint,),
        reused_states=(),
        cache_activity=CacheActivity(
            authored_hits=2,
            authored_misses=1,
            projection_hits=1,
            projection_misses=0,
        ),
        assets=0,
        asset_bytes=0,
        index_bytes=512,
        verification=VerificationResult(states=1, outputs=1, assets=0, bytes_verified=0),
        elapsed_seconds=0.25,
    )


def spec(path: Path) -> Path:
    path.write_text(
        "schema: marimo-export.spec.v1\n"
        "default_state: baseline\n"
        "states:\n"
        "  baseline: {}\n"
        "outputs:\n"
        "  count:\n"
        "    source: {kind: value, selector: count}\n",
        encoding="utf-8",
    )
    return path


class Prepared:
    def __init__(self, path: Path, resolved: ExportPlan, export_result: ExportResult) -> None:
        self.identity = "f" * 64
        self.path = path.resolve()
        self.plan = resolved
        self.reused = False
        self.prepared_states = resolved.state_fingerprints
        self.reused_states: tuple[str, ...] = ()
        self.cache_activity = CacheActivity(authored_misses=1, projection_hits=1)
        self.result = export_result
        self.write_calls: list[tuple[str, bool]] = []
        self.closed = False

    def __enter__(self) -> Prepared:
        return self

    def __exit__(self, *_error: object) -> None:
        self.closed = True

    def write(
        self,
        output: str,
        *,
        replace: bool,
        progress: Callable[[ProgressEvent], None] | None,
    ) -> ExportResult:
        self.write_calls.append((output, replace))
        if progress is not None:
            progress(
                ProgressEvent(
                    kind="write_finished",
                    completed=len(self.plan.states),
                    total=len(self.plan.states),
                )
            )
        return self.result


def description() -> dict[str, object]:
    return {
        "capabilities": ["cell_cache_receipts"],
        "definitions": [
            {
                "cell_id": "cell-1",
                "domain": {"options": ["AAPL", "MSFT"]},
                "kind": "ui",
                "input_mode": "value",
                "name": "selector",
                "portable_input": True,
                "python_type": "marimo.ui.dropdown",
                "sensitive": False,
                "siblings": ["selector"],
                "value": "AAPL",
                "value_available": True,
            }
        ],
        "cells": [],
        "document_sha256": "a" * 64,
        "filename": "finance.py",
        "implementation_sha256": "c" * 64,
        "marimo_export_version": "0.0.0",
        "marimo_version": "0.24.0",
        "path": "/workspace/finance.py",
        "session_id": "s_01",
    }


class Session:
    id = "s_01"
    filename = "finance.py"
    path = "/workspace/finance.py"

    def inspect(self) -> Any:
        return SimpleNamespace(to_dict=description)


class Client:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_error: object) -> None:
        pass

    def sessions(self) -> tuple[Session, ...]:
        return (Session(),)

    def session(self, session_id: str | None = None) -> Session:
        assert session_id == "s_01"
        return Session()


class BrokenOutput:
    def __init__(self) -> None:
        self.closed = False

    def write(self, value: str) -> int:
        del value
        raise BrokenPipeError

    def close(self) -> None:
        self.closed = True
