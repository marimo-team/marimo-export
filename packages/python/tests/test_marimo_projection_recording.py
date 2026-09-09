from __future__ import annotations

import threading
from asyncio import CancelledError
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import marimo_export._marimo.compat.projections as projection_compat
import pytest
from marimo._messaging.streams import ThreadSafeStdout, ThreadSafeStream
from marimo._types.ids import CellId_t
from marimo_export._diagnostics import cleanup_failures
from marimo_export._marimo.bridge import _merge_control_bindings
from marimo_export._marimo.compat.child_run import (
    StopProvenance,
    raise_stopped_output,
)
from marimo_export._marimo.compat.output_data import rewrite_ui_value
from marimo_export._marimo.composition import create_kernel_runtime
from marimo_export.errors import ExecutionError, OutputError
from marimo_export.index import ControlBinding, ControlIndexStep


@contextmanager
def _recording_context(child: SimpleNamespace) -> Iterator[projection_compat.ProjectionRecording]:
    stream: ThreadSafeStream | None = None
    try:
        with projection_compat.record_child_notifications(child, {}) as recording:
            stream = child._runtime_context.stream
            yield recording
    finally:
        if stream is not None:
            writer = stream.buffered_console_thread
            try:
                writer.join(timeout=1)
                assert not writer.is_alive()
            finally:
                if writer.is_alive():
                    stream.stop()
                    writer.join(timeout=1)


@pytest.fixture
def recording_child() -> SimpleNamespace:
    return SimpleNamespace(
        _kernel=SimpleNamespace(_streams=object()),
        _runtime_context=SimpleNamespace(stream=object(), stdout=object(), stderr=object()),
    )


@pytest.mark.parametrize("cancelled", [False, True])
def test_recording_closes_console_before_stopping_writer(
    recording_child: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    context = recording_child._runtime_context
    original_kernel_streams = recording_child._kernel._streams
    original_streams = (context.stream, context.stdout, context.stderr)
    closed_at_stop: tuple[bool, bool] | None = None
    native_stop = ThreadSafeStream.stop

    def observed_stop(stream: ThreadSafeStream) -> None:
        nonlocal closed_at_stop
        closed_at_stop = (owned.stdout.closed, owned.stderr.closed)
        native_stop(stream)

    monkeypatch.setattr(ThreadSafeStream, "stop", observed_stop)
    primary = CancelledError("capture cancelled")
    with (
        pytest.raises(CancelledError) if cancelled else nullcontext() as raised,
        _recording_context(recording_child),
    ):
        owned = recording_child._kernel._streams
        if cancelled:
            raise primary

    assert closed_at_stop == (True, True)
    if cancelled:
        assert raised is not None
        assert raised.value is primary
    assert recording_child._kernel._streams is original_kernel_streams
    assert (context.stream, context.stdout, context.stderr) == original_streams
    with pytest.raises(OutputError, match="requires an active child recording"):
        projection_compat._recording()


@pytest.mark.parametrize("cancelled", [False, True])
def test_recording_finishes_cleanup_after_console_close_failure(
    recording_child: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    context = recording_child._runtime_context
    original_kernel_streams = recording_child._kernel._streams
    original_streams = (context.stream, context.stdout, context.stderr)
    close_error = RuntimeError("stdout close failed")
    primary = CancelledError("capture cancelled")

    def fail_flush(_stdout: ThreadSafeStdout) -> None:
        raise close_error

    monkeypatch.setattr(ThreadSafeStdout, "flush", fail_flush)
    with (
        pytest.raises(BaseException) as raised,
        _recording_context(recording_child),
    ):
        owned = recording_child._kernel._streams
        if cancelled:
            raise primary

    assert raised.value is (primary if cancelled else close_error)
    assert owned.stdout.closed
    assert owned.stderr.closed
    assert recording_child._kernel._streams is original_kernel_streams
    assert (context.stream, context.stdout, context.stderr) == original_streams
    if cancelled:
        assert cleanup_failures(primary) == ("recording stdout close also failed: RuntimeError",)


def test_recording_drains_concurrent_console_writers_on_exit(
    recording_child: SimpleNamespace,
) -> None:
    with _recording_context(recording_child) as recording:
        stream = recording_child._runtime_context.stream
        stream.cell_id = CellId_t("report")
        writers = [
            threading.Thread(
                target=recording_child._runtime_context.stdout.write,
                args=("result ready\n",),
                daemon=True,
            ),
            threading.Thread(
                target=recording_child._runtime_context.stderr.write,
                args=("source warning\n",),
                daemon=True,
            ),
        ]
        started: list[threading.Thread] = []
        try:
            for writer in writers:
                writer.start()
                started.append(writer)
        finally:
            for writer in started:
                writer.join(timeout=1)
        assert all(not writer.is_alive() for writer in started)

    assert {
        output.channel: output.data
        for output in recording.view.get_cell_console_outputs([CellId_t("report")])[
            CellId_t("report")
        ]
    } == {"stdout": "result ready\n", "stderr": "source warning\n"}


def test_attached_marimo_exposes_live_capture_capabilities() -> None:
    report = create_kernel_runtime().require_capabilities()

    assert report.version
    assert report.names == (
        "asset_transfer",
        "blob_asset",
        "cache_cells",
        "cell_cache_receipts",
        "child_sessions",
        "child_ui_updates",
        "definition_overrides",
        "projection_snapshots",
        "setup_definition_overrides",
        "synthetic_output_cells",
    )


def test_stop_provenance_tracks_exact_descendants_and_latest_result() -> None:
    from marimo._runtime.control_flow import MarimoStopError
    from marimo._runtime.runner.hook_context import CancelledCells
    from marimo._types.ids import CellId_t

    provenance = StopProvenance()
    stopped = MarimoStopError("waiting")
    guard_id = CellId_t("guard")
    answer_id = CellId_t("answer")
    guard = SimpleNamespace(cell_id=guard_id)
    context = SimpleNamespace(graph=None)
    provenance.record_cell(guard, context, SimpleNamespace(exception=stopped))
    cancelled = CancelledCells()
    cancelled.add(guard_id, {guard_id, answer_id})
    provenance.record_finish(
        SimpleNamespace(
            exceptions={guard_id: stopped},
            cancelled_cells=cancelled,
            graph=None,
        )
    )

    assert provenance.stopping_cell(guard_id) == guard_id
    assert provenance.stopping_cell(answer_id) == guard_id
    with pytest.raises(OutputError) as raised:
        raise_stopped_output(
            state_name="blocked",
            output="answer",
            owner_cell=answer_id,
            dependency_cells=frozenset({guard_id, answer_id}),
            source_cell_ids={guard_id: "source-guard", answer_id: "source-answer"},
            stop_provenance=provenance,
        )
    assert raised.value.details == {
        "state": "blocked",
        "output": "answer",
        "cell_id": "source-answer",
        "raising_cell_id": "source-guard",
        "status": "stopped",
    }

    provenance.record_cell(guard, context, SimpleNamespace(exception=None))

    assert provenance.stopping_cell(guard_id) is None
    assert provenance.stopping_cell(answer_id) is None


def test_control_input_union_rejects_cross_state_ownership_changes() -> None:
    controls = {"cell-controls-0": ControlBinding(input="first", path=())}

    with pytest.raises(ExecutionError) as raised:
        _merge_control_bindings(
            controls,
            {
                "cell-controls-0": ControlBinding(
                    input="second",
                    path=(ControlIndexStep(value=0),),
                )
            },
        )

    assert raised.value.code == "control_input_conflict"
    assert controls == {"cell-controls-0": ControlBinding(input="first", path=())}


def test_recording_cleanup_restores_streams_after_console_flush_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_kernel_streams = object()
    original_streams = (object(), object(), object())
    context = SimpleNamespace(
        stream=original_streams[0],
        stdout=original_streams[1],
        stderr=original_streams[2],
    )
    child = SimpleNamespace(
        _kernel=SimpleNamespace(_streams=original_kernel_streams),
        _runtime_context=context,
    )
    stopped = threading.Event()
    captured: dict[str, ThreadSafeStream] = {}
    native_stop = ThreadSafeStream.stop

    def fail_flush(stream: ThreadSafeStream) -> None:
        captured["stream"] = stream
        raise RuntimeError("flush failed")

    def observed_stop(stream: ThreadSafeStream) -> None:
        native_stop(stream)
        stopped.set()

    monkeypatch.setattr(ThreadSafeStream, "flush_console", fail_flush)
    monkeypatch.setattr(ThreadSafeStream, "stop", observed_stop)
    primary = ValueError("execution failed")

    with (
        pytest.raises(ValueError, match="execution failed") as raised,
        projection_compat.record_child_notifications(child, {}),
    ):
        assert projection_compat._recording().child is child
        raise primary

    assert raised.value is primary
    assert cleanup_failures(primary) == (
        "recording console flush also failed: RuntimeError",
        "recording stdout close also failed: RuntimeError",
        "recording stderr close also failed: RuntimeError",
    )
    assert child._kernel._streams is original_kernel_streams
    assert (context.stream, context.stdout, context.stderr) == original_streams
    with pytest.raises(OutputError, match="requires an active child recording"):
        projection_compat._recording()
    assert stopped.wait(timeout=1)
    stream = captured["stream"]
    stream.buffered_console_thread.join(timeout=1)
    assert not stream.buffered_console_thread.is_alive()


def test_structured_ui_references_are_scoped_without_rewriting_literal_values() -> None:
    value = {
        "children": [
            {
                "model_id": "runtime-model",
                "object_id": "runtime-control",
                "randomId": "runtime-random",
                "label": "runtime-control",
            }
        ]
    }

    rewritten = rewrite_ui_value(
        value,
        {"runtime-model": "projection-model"},
        {
            "runtime-control": "projection-control",
            "runtime-random": "projection-random",
        },
    )

    assert rewritten == {
        "children": [
            {
                "model_id": "projection-model",
                "object_id": "projection-control",
                "randomId": "projection-random",
                "label": "runtime-control",
            }
        ]
    }
