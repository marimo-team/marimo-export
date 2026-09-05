from __future__ import annotations

import queue
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._execution.plan import PlannedOutput
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    json_value,
)
from marimo_export.descriptors import (
    ARROW_MEDIA_TYPE,
    JSON_MEDIA_TYPE,
    MARIMO_CELL_MEDIA_TYPE,
    MARIMO_OUTPUT_MEDIA_TYPE,
)
from marimo_export.errors import OutputError
from marimo_export.spec import CellSource, RenderedOutputSource

if TYPE_CHECKING:
    from marimo_export._marimo.compat.child_run import StateChild


@dataclass(slots=True)
class ProjectionRecording:
    child: Any
    view: Any
    cell_ids: Mapping[str, Any]
    ui_scopes: dict[str, set[str]] = field(default_factory=dict)


_CURRENT_RECORDING: ContextVar[ProjectionRecording | None] = ContextVar(
    "marimo_export_projection_recording",
    default=None,
)
_NATIVE_ARROW_SCHEMA = "marimo-export.native-arrow.v1"
_POLARS_TYPES = frozenset(
    {
        "polars.dataframe.frame.DataFrame",
        "polars.series.series.Series",
    }
)


class _RecordingPipe:
    def __init__(self, view: Any) -> None:
        self._view = view

    def send(self, obj: Any) -> None:
        self._view.add_raw_notification(obj)


@contextmanager
def record_child_notifications(
    child: Any,
    cell_ids: Mapping[str, Any],
) -> Iterator[ProjectionRecording]:
    """Record one child run through Marimo's SessionView aggregation."""

    from marimo._messaging.streams import (
        ThreadSafeStderr,
        ThreadSafeStdout,
        ThreadSafeStream,
    )
    from marimo._messaging.types import KernelStreams
    from marimo._session.state.session_view import SessionView

    view = SessionView()
    stream = ThreadSafeStream(
        pipe=_RecordingPipe(view),
        input_queue=queue.Queue(),
        redirect_console=True,
    )
    streams = KernelStreams(
        stream=stream,
        stdout=ThreadSafeStdout(stream, forward_os_streams=False),
        stderr=ThreadSafeStderr(stream, forward_os_streams=False),
        stdin=None,
    )
    context = child._runtime_context
    original_streams = child._kernel._streams
    original_context_streams = (context.stream, context.stdout, context.stderr)
    child._kernel._streams = streams
    context.stream = stream
    context.stdout = streams.stdout
    context.stderr = streams.stderr
    recording = ProjectionRecording(child=child, view=view, cell_ids=dict(cell_ids))
    token = _CURRENT_RECORDING.set(recording)
    try:
        yield recording
    finally:
        primary = sys.exc_info()[1]

        def reset_recording() -> None:
            _CURRENT_RECORDING.reset(token)

        def restore_kernel_streams() -> None:
            child._kernel._streams = original_streams

        def restore_context_streams() -> None:
            context.stream, context.stdout, context.stderr = original_context_streams

        _close_recording(
            operations=(
                ("recording console flush", stream.flush_console),
                ("recording context reset", reset_recording),
                ("recording kernel stream restoration", restore_kernel_streams),
                ("recording context stream restoration", restore_context_streams),
                ("recording stream stop", stream.stop),
            ),
            primary=primary,
        )


def _close_recording(
    *,
    operations: tuple[tuple[str, Callable[[], None]], ...],
    primary: BaseException | None,
) -> None:
    failures: list[tuple[str, BaseException]] = []
    for label, operation in operations:
        try:
            operation()
        except BaseException as error:
            failures.append((label, error))
    if not failures:
        return
    if primary is not None:
        for label, error in failures:
            record_cleanup_failure(primary, label, error)
        return
    _, error = failures[0]
    for secondary_label, secondary in failures[1:]:
        record_cleanup_failure(error, secondary_label, secondary)
    raise error


def resolve_value_path(
    root: object,
    path: tuple[tuple[str, str | int], ...],
) -> object:
    """Resolve one structural selector with Studio-compatible semantics."""

    current = root
    for kind, key in path:
        if kind == "attribute":
            if not isinstance(key, str):
                raise ValueError("attribute selector keys must be strings")
            if isinstance(current, Mapping) and key in current:
                current = cast(Mapping[object, object], current)[key]
                continue
            try:
                current = getattr(current, key)
            except AttributeError as error:
                raise ValueError(f"attribute {key!r} is unavailable") from error
            continue
        if kind != "item":
            raise ValueError(f"unknown selector step {kind!r}")
        try:
            current = cast(Any, current)[key]
        except (IndexError, KeyError, TypeError) as error:
            raise ValueError(f"item {key!r} is unavailable") from error
    return current


def capture_json_value(
    root: object,
    path: tuple[tuple[str, str | int], ...],
) -> object:
    """Return one canonical JSON value through Marimo's BlobAsset cache codec."""

    from marimo._save.stubs import BlobAsset

    value = json_value(resolve_value_path(root, path), "JSON projection")
    return BlobAsset(
        data=canonical_bytes(value),
        media_type=JSON_MEDIA_TYPE,
        metadata={"schema": "marimo.json.v1"},
    )


def capture_native_value(
    root: object,
    path: tuple[tuple[str, str | int], ...],
) -> object:
    """Return one value through a supported native cache representation."""

    value = resolve_value_path(root, path)
    if value is None or isinstance(value, (bool, str, int, float)):
        return value
    try:
        portable = json_value(value, "native JSON projection")
    except (TypeError, ValueError):
        arrow = _native_arrow_value(value)
        return value if arrow is None else arrow

    from marimo._save.stubs import BlobAsset

    return BlobAsset(
        data=canonical_bytes(portable),
        media_type=JSON_MEDIA_TYPE,
        metadata={"schema": "marimo.json.v1"},
    )


def _native_arrow_value(value: object) -> object | None:
    python_type = f"{type(value).__module__}.{type(value).__qualname__}"
    if python_type not in _POLARS_TYPES:
        return None
    frame = cast(Any, value).to_frame() if python_type.endswith(".Series") else value
    buffer = BytesIO()
    cast(Any, frame).write_ipc_stream(buffer, compression="uncompressed")

    from marimo._save.stubs import BlobAsset

    return BlobAsset(
        data=buffer.getvalue(),
        media_type=ARROW_MEDIA_TYPE,
        metadata={
            "python_type": python_type,
            "schema": _NATIVE_ARROW_SCHEMA,
        },
    )


def materialize_rendered_output(
    root: object,
    path: tuple[tuple[str, str | int], ...],
    *,
    owner_cell_id: str | None,
    projection_identity: str,
) -> bytes:
    """Format one value into canonical inert output snapshot bytes."""

    from marimo._messaging.cell_output import CellChannel, CellOutput
    from marimo._output.formatting import try_format

    value = resolve_value_path(root, path)
    formatted = try_format(value)
    if formatted.exception is not None:
        formatted = try_format(value, include_opinionated=False)
    if formatted.exception is not None or formatted.traceback is not None:
        error = formatted.exception
        raise OutputError(
            "Marimo could not format the selected output",
            code="output_execution_failed",
            details={
                "exception_type": type(error).__name__ if error is not None else "FormatterError"
            },
        ) from error
    output = CellOutput(
        channel=CellChannel.OUTPUT,
        mimetype=formatted.mimetype,
        data=formatted.data,
    )
    if not isinstance(owner_cell_id, str) or not owner_cell_id:
        raise OutputError(
            "Marimo output projection has no source owner",
            code="output_execution_failed",
        )
    snapshot = _output_snapshot(output, owner_cell_id, projection_identity)
    return canonical_bytes(snapshot)


def capture_materialized_output(snapshot_bytes: bytes) -> object:
    """Wrap one pre-materialized canonical rendered-output snapshot."""

    return _capture_materialized_snapshot(
        snapshot_bytes,
        schema="marimo.output.v1",
        media_type=MARIMO_OUTPUT_MEDIA_TYPE,
        label="rendered-output",
    )


def materialize_projection_token(
    child: StateChild,
    planned_output: PlannedOutput,
) -> None:
    """Populate one snapshot token before its transient output cell runs."""

    from marimo_export._execution.plan import (
        planned_output_identity,
        snapshot_token_name,
    )

    source = planned_output.source
    if isinstance(source, RenderedOutputSource):
        path = tuple((step.kind, step.key) for step in source.selector.path)
        child.runner.globals[snapshot_token_name(planned_output)] = materialize_rendered_output(
            child.runner.globals[source.selector.root],
            path,
            owner_cell_id=planned_output.owner_cell_id,
            projection_identity=planned_output_identity(planned_output),
        )
        return
    if not isinstance(source, CellSource):
        return
    cell = planned_output.cell
    if cell is None:
        raise OutputError(
            f"complete-cell output {planned_output.name!r} has no resolved cell",
            code="output_execution_failed",
        )
    child.runner.globals[snapshot_token_name(planned_output)] = materialize_complete_cell(
        cell_id=cell.id,
        name=cell.name,
        code_sha256=cell.code_sha256,
        config=cell.config,
        projection_identity=planned_output_identity(planned_output),
    )


def materialize_complete_cell(
    *,
    cell_id: str,
    name: str | None,
    code_sha256: str,
    config: Mapping[str, JsonValue],
    projection_identity: str,
) -> bytes:
    """Materialize one terminal cell snapshot from the current child recording."""

    from marimo._types.ids import CellId_t
    from marimo._utils.lists import as_list

    recording = _recording()
    runtime_id = CellId_t(recording.cell_ids.get(cell_id, cell_id))
    notification = recording.view.cell_notifications.get(runtime_id)
    if notification is None:
        raise OutputError(
            f"cell {name or cell_id!r} produced no terminal notification",
            code="output_execution_failed",
            details={"cell_id": cell_id},
        )
    cell = recording.child._kernel.graph.cells.get(runtime_id)
    status = None if cell is None else cell.run_result_status
    if status != "success":
        raise OutputError(
            f"cell {name or cell_id!r} did not complete successfully",
            code="output_execution_failed",
            details={"cell_id": cell_id, "status": status or "unknown"},
        )
    from marimo_export._execution.plan import _ends_with_semicolon

    terminal_output = (
        None if cell is not None and _ends_with_semicolon(cell.code) else notification.output
    )
    console = tuple(as_list(notification.console))
    outputs = tuple(item for item in (terminal_output, *console) if item is not None)
    from marimo_export._marimo.compat.output_data import cell_output_value
    from marimo_export._marimo.compat.replay import resources

    replay_resources, replacements = resources(
        recording,
        outputs,
        projection_identity,
        cell_id,
    )
    snapshot: JsonObject = {
        "schema": "marimo.cell.v1",
        "projectionSha256": projection_identity,
        "cell": {
            "id": cell_id,
            "name": name,
            "codeSha256": code_sha256,
            "config": dict(config),
        },
        "outcome": "completed",
        "output": cell_output_value(recording, terminal_output, replacements),
        "console": [
            cast(JsonValue, cell_output_value(recording, item, replacements)) for item in console
        ],
        "resources": replay_resources,
    }
    return canonical_bytes(snapshot)


def capture_materialized_cell(snapshot_bytes: bytes) -> object:
    """Wrap one pre-materialized canonical complete-cell snapshot."""

    return _capture_materialized_snapshot(
        snapshot_bytes,
        schema="marimo.cell.v1",
        media_type=MARIMO_CELL_MEDIA_TYPE,
        label="complete-cell",
    )


def _capture_materialized_snapshot(
    snapshot_bytes: bytes,
    *,
    schema: str,
    media_type: str,
    label: str,
) -> object:
    from marimo._save.stubs import BlobAsset

    if not isinstance(snapshot_bytes, bytes):
        raise OutputError(
            f"{label} snapshot token must contain bytes",
            code="output_execution_failed",
        )
    try:
        snapshot = decode_json_object(snapshot_bytes, f"{label} snapshot token")
    except (TypeError, ValueError) as error:
        raise OutputError(
            f"{label} snapshot token is invalid",
            code="output_execution_failed",
        ) from error
    if snapshot.get("schema") != schema or canonical_bytes(snapshot) != snapshot_bytes:
        raise OutputError(
            f"{label} snapshot token is not canonical",
            code="output_execution_failed",
        )
    return BlobAsset(
        data=snapshot_bytes,
        media_type=media_type,
        metadata={"schema": schema},
    )


def _output_snapshot(
    output: Any,
    owner_cell_id: str,
    projection_identity: str,
) -> JsonObject:
    from marimo_export._marimo.compat.output_data import cell_output_value
    from marimo_export._marimo.compat.replay import resources

    recording = _recording()
    replay_resources, replacements = resources(
        recording,
        (output,),
        projection_identity,
        owner_cell_id,
    )
    return {
        "schema": "marimo.output.v1",
        "projectionSha256": projection_identity,
        "ownerCellId": owner_cell_id,
        "output": cell_output_value(recording, output, replacements),
        "resources": replay_resources,
    }


def _recording() -> ProjectionRecording:
    recording = _CURRENT_RECORDING.get()
    if recording is None:
        raise OutputError(
            "Marimo projection capture requires an active child recording",
            code="output_execution_failed",
        )
    return recording


__all__ = [
    "capture_json_value",
    "capture_materialized_cell",
    "capture_materialized_output",
    "capture_native_value",
    "materialize_complete_cell",
    "materialize_projection_token",
    "materialize_rendered_output",
    "record_child_notifications",
    "resolve_value_path",
]
