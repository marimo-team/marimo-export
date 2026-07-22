from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import anywidget
import ipywidgets
import marimo as mo
import pytest
import traitlets
from anywidget._descriptor import MimeBundleDescriptor
from marimo._ast.app_config import _AppConfig
from marimo._config.config import DEFAULT_CONFIG
from marimo._messaging.types import KernelMessage, KernelStreams, NoopStream, Stream
from marimo._runtime.commands import AppMetadata
from marimo._runtime.context.types import RuntimeContext
from marimo._runtime.kernel_lifecycle import KernelArgs, kernel_session
from marimo._session.model import SessionMode
from marimo._types.ids import CellId_t
from marimo_export._marimo.anywidget import (
    ANYWIDGET_PAYLOAD_SCHEMA,
    AnyWidgetModelCapture,
    anywidget_payload,
    install_anywidget_capture,
)
from marimo_export.projection.exporters.anywidget import anywidget as export_anywidget

_CELL_ID = CellId_t("anywidget-test")
_COUNTER_ESM = """export function render({ model, el }) {
  el.textContent = String(model.get("count"));
}
"""
_EXTERNAL_ESM = "https://cdn.example.test/anywidget.js"


class _CounterWidget(anywidget.AnyWidget):
    _esm = _COUNTER_ESM
    _css = ".counter { color: rebeccapurple; }"
    count = traitlets.Int(3).tag(sync=True)


class _ChildWidget(anywidget.AnyWidget):
    _esm = "https://cdn.example.test/child.js"
    value = traitlets.Int(1).tag(sync=True)


class _ParentWidget(anywidget.AnyWidget):
    _esm = "https://cdn.example.test/parent.js"
    child = traitlets.Instance(ipywidgets.Widget).tag(
        sync=True,
        **ipywidgets.widget_serialization,
    )


class _BinaryWidget(anywidget.AnyWidget):
    _esm = _EXTERNAL_ESM
    zeta = traitlets.Bytes(b"zeta-bytes").tag(sync=True)
    alpha = traitlets.Bytes(b"alpha-bytes").tag(sync=True)


class _MarkedWidget(anywidget.AnyWidget):
    _esm = _EXTERNAL_ESM
    marker = traitlets.Unicode().tag(sync=True)


class _BrokenReferenceWidget(anywidget.AnyWidget):
    _esm = _EXTERNAL_ESM
    child = traitlets.Unicode("IPY_MODEL_missing-model").tag(sync=True)


class _RelativeCssWidget(anywidget.AnyWidget):
    _esm = _EXTERNAL_ESM
    _css = ".icon { background-image: url('./icon.svg'); }"


@dataclass
class _StreamLog:
    messages: list[KernelMessage]
    copies: int = 0
    flushes: int = 0
    stops: int = 0


class _RecordingStream(Stream):
    def __init__(self, log: _StreamLog) -> None:
        self.log = log

    def write(self, data: KernelMessage) -> None:
        self.log.messages.append(data)

    def flush_console(self) -> None:
        self.log.flushes += 1

    def stop(self) -> None:
        self.log.stops += 1

    def copy_for_thread(self) -> Stream:
        self.log.copies += 1
        return _RecordingStream(self.log)


@dataclass
class _ProtocolWidget:
    count: int

    _repr_mimebundle_: ClassVar[MimeBundleDescriptor] = MimeBundleDescriptor(
        autodetect_observer=False,
        _esm=_EXTERNAL_ESM,
    )


@contextmanager
def _widget_session() -> Iterator[tuple[AnyWidgetModelCapture, RuntimeContext]]:
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
            filename="anywidget-test.py",
            argv=[],
        ),
        user_config=copy.deepcopy(DEFAULT_CONFIG),
        mode=SessionMode.EDIT,
        control_queue=asyncio.Queue(),
        set_ui_element_queue=asyncio.Queue(),
        virtual_file_storage="shared_memory",
    )

    with kernel_session(args) as (kernel, context):
        capture = install_anywidget_capture(
            SimpleNamespace(_kernel=kernel, _runtime_context=context)
        )
        with context.with_cell_id(_CELL_ID), context.provide_ui_ids(str(_CELL_ID)):
            try:
                yield capture, context
            finally:
                context.cell_lifecycle_registry.dispose(_CELL_ID, deletion=True)


def _decode(payload: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(payload))


def test_capture_stream_stop_revokes_all_clones_without_stopping_transport() -> None:
    log = _StreamLog(messages=[])
    target = _RecordingStream(log)
    runner = SimpleNamespace(
        _kernel=SimpleNamespace(_streams=SimpleNamespace(stream=target)),
        _runtime_context=SimpleNamespace(stream=target),
    )
    install_anywidget_capture(runner)
    stream = cast(Stream, runner._kernel._streams.stream)
    clone = stream.copy_for_thread()
    first = KernelMessage(b"first")
    second = KernelMessage(b"second")

    stream.write(first)
    clone.write(second)
    clone.stop()
    stream.write(KernelMessage(b"late-root"))
    clone.write(KernelMessage(b"late-clone"))
    stream.flush_console()
    stream.copy_for_thread().write(KernelMessage(b"late-copy"))

    assert log.messages == [first, second]
    assert log.copies == 1
    assert log.flushes == 0
    assert log.stops == 0


def _notifications(document: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], document["modelNotifications"])


def _root_notification(document: dict[str, Any]) -> dict[str, Any]:
    root_model_id = document["rootModelId"]
    return next(
        notification
        for notification in _notifications(document)
        if notification["model_id"] == root_model_id
    )


def _collect_model_refs(value: object) -> list[str]:
    references: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            for prefix in ("anywidget:", "IPY_MODEL_"):
                if item.startswith(prefix):
                    references.append(item.removeprefix(prefix))
            return
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
            return
        if isinstance(item, list | tuple):
            for child in item:
                visit(child)

    visit(value)
    return references


def _export_counter() -> tuple[str, bytes]:
    with _widget_session():
        widget = _CounterWidget(count=7)
        projection = export_anywidget(widget)
        return widget.model_id, projection.payload


def test_raw_anywidget_exports_the_static_model_graph() -> None:
    with _widget_session():
        widget = _CounterWidget(count=7)
        projection = export_anywidget(widget)

    document = _decode(projection.payload)
    notifications = _notifications(document)
    root = _root_notification(document)

    assert projection.format_id == "anywidget.v1"
    assert projection.media_type == "application/vnd.marimo-export.anywidget+json"
    assert projection.metadata == {
        "models": len(notifications),
        "root_model_id": document["rootModelId"],
    }
    assert document["schema"] == ANYWIDGET_PAYLOAD_SCHEMA
    assert root["message"]["state"]["count"] == 7
    assert root["message"]["state"]["_css"] == _CounterWidget._css


def test_marimo_anywidget_wrapper_exports_the_same_graph() -> None:
    with _widget_session():
        widget = _CounterWidget(count=11)
        raw_payload = export_anywidget(widget).payload
        wrapped_payload = export_anywidget(mo.ui.anywidget(widget)).payload

    assert wrapped_payload == raw_payload


def test_payload_is_deterministic_across_runtime_ids_and_embeds_virtual_esm() -> None:
    first_runtime_id, first_payload = _export_counter()
    second_runtime_id, second_payload = _export_counter()

    assert first_runtime_id != second_runtime_id
    assert first_payload == second_payload
    assert first_runtime_id.encode() not in first_payload
    assert second_runtime_id.encode() not in second_payload

    document = _decode(first_payload)
    source = _COUNTER_ESM.encode()
    digest = hashlib.sha256(source).hexdigest()
    file_name = f"./@file/{len(source)}-anywidget-{digest}.js"
    encoded_file = cast(str, document["files"][file_name])
    header, encoded_contents = encoded_file.split(",", maxsplit=1)
    root = _root_notification(document)

    assert header == "data:text/javascript;base64"
    assert base64.b64decode(encoded_contents) == source
    assert root["message"]["esm_spec"]["url"] == file_name


def test_nested_widget_references_are_canonical_and_closed() -> None:
    with _widget_session() as (capture, _):
        child = _ChildWidget(value=8)
        parent = _ParentWidget(child=child)
        snapshot = anywidget_payload(parent, capture=capture)
        runtime_ids = (parent.model_id, child.model_id)

    document = _decode(snapshot.payload)
    notifications = _notifications(document)
    model_ids = {cast(str, notification["model_id"]) for notification in notifications}
    root_state = _root_notification(document)["message"]["state"]

    assert model_ids == {f"model-{index}" for index in range(len(notifications))}
    assert root_state["child"].startswith("IPY_MODEL_model-")
    assert set(_collect_model_refs(document)).issubset(model_ids)
    assert all(runtime_id.encode() not in snapshot.payload for runtime_id in runtime_ids)


def test_binary_buffers_are_base64_encoded_in_sorted_path_order() -> None:
    with _widget_session() as (capture, _):
        snapshot = anywidget_payload(_BinaryWidget(), capture=capture)

    message = _root_notification(_decode(snapshot.payload))["message"]

    assert message["buffer_paths"] == [["alpha"], ["zeta"]]
    assert message["buffers"] == [
        base64.b64encode(b"alpha-bytes").decode(),
        base64.b64encode(b"zeta-bytes").decode(),
    ]


def test_descriptor_protocol_widget_exports_through_the_same_wire_shape() -> None:
    with _widget_session() as (capture, _):
        widget = _ProtocolWidget(count=13)
        try:
            snapshot = anywidget_payload(widget, capture=capture)
            bundle = widget._repr_mimebundle_
        finally:
            bundle = widget._repr_mimebundle_
            bundle._comm.close()

    document = _decode(snapshot.payload)
    root = _root_notification(document)

    assert snapshot.model_count == 1
    assert root["message"]["state"]["count"] == 13
    assert root["message"]["esm_spec"]["url"] == _EXTERNAL_ESM


def test_projection_excludes_models_outside_the_root_closure() -> None:
    with _widget_session() as (capture, _):
        unrelated = _MarkedWidget(marker="unrelated")
        root = _MarkedWidget(marker="included")
        snapshot = anywidget_payload(root, capture=capture)
        unrelated_runtime_id = unrelated.model_id

    document = _decode(snapshot.payload)
    states = [notification["message"]["state"] for notification in _notifications(document)]

    assert snapshot.model_count == 2
    assert any(state.get("marker") == "included" for state in states)
    assert all(state.get("marker") != "unrelated" for state in states)
    assert unrelated_runtime_id.encode() not in snapshot.payload


def test_missing_model_reference_is_rejected() -> None:
    with (
        _widget_session() as (capture, _),
        pytest.raises(
            ValueError,
            match=r"references model 'missing-model'.*captured no open model",
        ),
    ):
        anywidget_payload(_BrokenReferenceWidget(), capture=capture)


def test_relative_css_url_is_rejected() -> None:
    with (
        _widget_session() as (capture, _),
        pytest.raises(ValueError, match=r"CSS url.*unsupported asset URL"),
    ):
        anywidget_payload(_RelativeCssWidget(), capture=capture)
