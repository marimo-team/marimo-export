from __future__ import annotations

import base64
import hashlib
import inspect
import json
import mimetypes
import re
import sys
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, cast

from marimo._messaging.notification import (
    EsmSpec,
    ModelLifecycleNotification,
    ModelOpen,
)
from marimo._messaging.serde import try_deserialize_kernel_notification_name
from marimo._messaging.types import KernelMessage, Stream
from marimo._plugins.ui._impl.anywidget.widget_ref import (
    _try_get_widget_model_id,
)
from marimo._plugins.ui._impl.from_anywidget import (
    _sync_widget_state,
)
from marimo._plugins.ui._impl.from_anywidget import (
    anywidget as MarimoAnyWidget,
)
from marimo._runtime.context import safe_get_context
from marimo._runtime.virtual_file import read_virtual_file
from marimo._session.state.session_view import SessionView
from marimo._types.ids import WidgetModelId
from marimo._utils.code import hash_code
from marimo._utils.data_uri import build_data_url

from marimo_export._json import json_value
from marimo_export._marimo._anywidget_assets import portable_css, validate_embedded_esm

ANYWIDGET_PAYLOAD_SCHEMA = "marimo-export.anywidget.v1"

_CAPTURE_ATTR = "_marimo_export_anywidget_capture"
_ANYWIDGET_REF_PREFIX = "anywidget:"
_IPYWIDGET_REF_PREFIX = "IPY_MODEL_"
_VIRTUAL_FILE = re.compile(r"^(?:\./|/)?@file/(?P<size>\d+)-(?P<name>.+)$")


@dataclass(frozen=True)
class AnyWidgetPayload:
    payload: bytes
    root_model_id: str
    model_count: int


class AnyWidgetModelCapture:
    def __init__(self) -> None:
        self._view = SessionView()
        self._lock = threading.Lock()
        self._stream_state = _CaptureStreamState()

    def add(self, message: KernelMessage) -> None:
        with self._lock:
            self._view.add_raw_notification(message)

    def notifications(self) -> tuple[ModelLifecycleNotification, ...]:
        with self._lock:
            return tuple(self._view.get_model_notifications())

    def detach(self) -> None:
        self._stream_state.detach()


class _CaptureStreamState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = True

    def detach(self) -> None:
        with self.lock:
            self.active = False


class _CaptureStream:
    def __init__(self, target: Stream, capture: AnyWidgetModelCapture) -> None:
        self._target = target
        self._capture = capture
        self._state = capture._stream_state
        self._cell_id = target.cell_id

    @property
    def cell_id(self) -> Any:
        with self._state.lock:
            if self._state.active:
                self._cell_id = self._target.cell_id
            return self._cell_id

    @cell_id.setter
    def cell_id(self, value: Any) -> None:
        with self._state.lock:
            self._cell_id = value
            if self._state.active:
                self._target.cell_id = value

    def write(self, data: KernelMessage) -> None:
        with self._state.lock:
            if not self._state.active:
                return
            self._target.write(data)
            if try_deserialize_kernel_notification_name(data) == ModelLifecycleNotification.name:
                self._capture.add(data)

    def flush_console(self) -> None:
        with self._state.lock:
            if self._state.active:
                self._target.flush_console()

    def stop(self) -> None:
        self._state.detach()

    def copy_for_thread(self) -> Stream:
        with self._state.lock:
            target = self._target.copy_for_thread() if self._state.active else self._target
        return cast(Stream, _CaptureStream(target, self._capture))


def install_anywidget_capture(runner: Any) -> AnyWidgetModelCapture:
    capture = AnyWidgetModelCapture()
    stream = cast(Stream, _CaptureStream(runner._kernel._streams.stream, capture))
    runner._kernel._streams.stream = stream
    runner._runtime_context.stream = stream
    setattr(runner._runtime_context, _CAPTURE_ATTR, capture)
    setattr(runner, _CAPTURE_ATTR, capture)
    return capture


def detach_anywidget_capture(runner: Any) -> None:
    capture = getattr(runner, _CAPTURE_ATTR, None)
    if not isinstance(capture, AnyWidgetModelCapture):
        return
    capture.detach()
    for owner in (runner, runner._runtime_context):
        if getattr(owner, _CAPTURE_ATTR, None) is capture:
            delattr(owner, _CAPTURE_ATTR)


def anywidget_payload(
    value: object,
    *,
    capture: AnyWidgetModelCapture | None = None,
) -> AnyWidgetPayload:
    runtime_root_id = _root_model_id(value)
    if capture is None:
        context = safe_get_context()
        capture = getattr(context, _CAPTURE_ATTR, None) if context is not None else None
    if not isinstance(capture, AnyWidgetModelCapture):
        raise RuntimeError("AnyWidget projection requires a marimo-export scenario capture")

    ordered = _reachable_models(runtime_root_id, capture.notifications())
    canonical_ids = {
        str(notification.model_id): f"model-{index}" for index, notification in enumerate(ordered)
    }
    files: dict[str, str] = {}
    notifications = [
        _canonical_notification(notification, canonical_ids, files).to_json_serializable()
        for notification in ordered
    ]
    root_model_id = canonical_ids[runtime_root_id]
    document = {
        "schema": ANYWIDGET_PAYLOAD_SCHEMA,
        "rootModelId": root_model_id,
        "files": files,
        "modelNotifications": notifications,
    }
    payload = json.dumps(
        json_value(document),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return AnyWidgetPayload(
        payload=payload,
        root_model_id=root_model_id,
        model_count=len(ordered),
    )


def _root_model_id(value: object) -> str:
    candidate = value
    if isinstance(value, MarimoAnyWidget):
        sync = getattr(value, "_ensure_widget_synced", None)
        if callable(sync):
            sync()
        candidate = value.widget

    anywidget_module = sys.modules.get("anywidget")
    anywidget_type = getattr(anywidget_module, "AnyWidget", None)
    if isinstance(anywidget_type, type) and isinstance(candidate, anywidget_type):
        _activate_widget_graph(candidate)
        _sync_widget_state(candidate)
    else:
        from marimo._plugins.ui._impl.anywidget.comm_provider import (
            patch_comm_create,
        )

        patch_comm_create()
        if not _is_protocol_widget(candidate):
            raise TypeError(
                "AnyWidget projection requires an anywidget.AnyWidget instance, an "
                "anywidget protocol object, or a mo.ui.anywidget(...) value"
            )

    model_id = _try_get_widget_model_id(candidate)
    if model_id is None:
        raise RuntimeError("AnyWidget projection could not resolve the root model ID")
    return str(model_id)


def _activate_widget_graph(root: Any) -> None:
    import ipywidgets
    from marimo._plugins.ui._impl.anywidget.init import init_marimo_widget
    from marimo._plugins.ui._impl.comm import MarimoComm

    widget_type = ipywidgets.Widget
    seen: set[int] = set()

    def activate(widget: Any) -> None:
        identity = id(widget)
        if identity in seen:
            return
        seen.add(identity)
        synced_values = [getattr(widget, name) for name in sorted(widget.traits(sync=True))]
        for child in _nested_widgets(synced_values, widget_type):
            activate(child)
        if not isinstance(widget.comm, MarimoComm):
            init_marimo_widget(widget)

    activate(root)


def _nested_widgets(value: object, widget_type: type) -> list[Any]:
    result: list[Any] = []

    def visit(item: object) -> None:
        if isinstance(item, widget_type):
            result.append(item)
            return
        if isinstance(item, dict):
            for key in sorted(item):
                visit(item[key])
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return result


def _is_protocol_widget(value: object) -> bool:
    descriptor_module = sys.modules.get("anywidget._descriptor")
    if descriptor_module is None:
        return False
    descriptor_type = getattr(descriptor_module, "MimeBundleDescriptor", None)
    repr_type = getattr(descriptor_module, "ReprMimeBundle", None)
    representation = inspect.getattr_static(value, "_repr_mimebundle_", None)
    return (
        isinstance(descriptor_type, type)
        and isinstance(repr_type, type)
        and isinstance(representation, (descriptor_type, repr_type))
    )


def _reachable_models(
    root_id: str,
    notifications: tuple[ModelLifecycleNotification, ...],
) -> list[ModelLifecycleNotification]:
    by_id = {str(notification.model_id): notification for notification in notifications}
    ordered: list[ModelLifecycleNotification] = []
    queue = deque([root_id])
    seen: set[str] = set()
    while queue:
        model_id = queue.popleft()
        if model_id in seen:
            continue
        seen.add(model_id)
        try:
            notification = by_id[model_id]
        except KeyError as error:
            raise ValueError(
                f"AnyWidget state references model {model_id!r}, but marimo captured no open model"
            ) from error
        if not isinstance(notification.message, ModelOpen):
            raise TypeError("marimo returned a non-open AnyWidget model snapshot")
        ordered.append(notification)
        queue.extend(_model_refs(notification.message.state))
    return ordered


def _model_refs(value: object) -> list[str]:
    result: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            if item.startswith(_ANYWIDGET_REF_PREFIX):
                result.append(item.removeprefix(_ANYWIDGET_REF_PREFIX))
            elif item.startswith(_IPYWIDGET_REF_PREFIX):
                result.append(item.removeprefix(_IPYWIDGET_REF_PREFIX))
            return
        if isinstance(item, dict):
            for key in sorted(item):
                visit(item[key])
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return result


def _canonical_notification(
    notification: ModelLifecycleNotification,
    canonical_ids: dict[str, str],
    files: dict[str, str],
) -> ModelLifecycleNotification:
    message = notification.message
    assert isinstance(message, ModelOpen)
    state = cast(dict[str, Any], _rewrite_model_refs(message.state, canonical_ids))
    css = state.get("_css")
    if css is not None:
        if not isinstance(css, str):
            raise TypeError("AnyWidget _css state must be a string")
        state["_css"] = portable_css(css)
    pairs = sorted(
        zip(message.buffer_paths, message.buffers, strict=True),
        key=lambda pair: json.dumps(pair[0], separators=(",", ":")),
    )
    return ModelLifecycleNotification(
        model_id=WidgetModelId(canonical_ids[str(notification.model_id)]),
        message=ModelOpen(
            state=state,
            buffer_paths=[list(path) for path, _ in pairs],
            buffers=[bytes(buffer) for _, buffer in pairs],
            esm_spec=_canonical_esm_spec(message.esm_spec, files),
        ),
    )


def _canonical_esm_spec(spec: EsmSpec | None, files: dict[str, str]) -> EsmSpec | None:
    if spec is None:
        return None
    if spec.url.startswith(("https://", "http://", "data:")):
        return spec
    match = _VIRTUAL_FILE.fullmatch(spec.url)
    if match is None:
        raise ValueError(f"AnyWidget ESM uses an unsupported URL: {spec.url!r}")
    expected_size = int(match.group("size"))
    contents = read_virtual_file(match.group("name"), expected_size)
    if len(contents) != expected_size:
        raise ValueError(
            f"AnyWidget ESM {spec.url!r} declared {expected_size} bytes but returned "
            f"{len(contents)}"
        )
    try:
        source = contents.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("AnyWidget ESM must contain UTF-8 JavaScript") from error
    if hash_code(source) != spec.hash:
        raise ValueError("AnyWidget ESM contents do not match marimo's model code hash")
    validate_embedded_esm(source)
    digest = hashlib.sha256(contents).hexdigest()
    url = f"./@file/{len(contents)}-anywidget-{digest}.js"
    media_type = mimetypes.guess_type(url)[0] or "text/javascript"
    files[url] = build_data_url(media_type, base64.b64encode(contents))
    return EsmSpec(url=url, hash=spec.hash)


def _rewrite_model_refs(value: object, canonical_ids: dict[str, str]) -> object:
    if isinstance(value, str):
        if value.startswith(_ANYWIDGET_REF_PREFIX):
            runtime_id = value.removeprefix(_ANYWIDGET_REF_PREFIX)
            return f"{_ANYWIDGET_REF_PREFIX}{_canonical_id(runtime_id, canonical_ids)}"
        if value.startswith(_IPYWIDGET_REF_PREFIX):
            runtime_id = value.removeprefix(_IPYWIDGET_REF_PREFIX)
            return f"{_IPYWIDGET_REF_PREFIX}{_canonical_id(runtime_id, canonical_ids)}"
        return value
    if isinstance(value, dict):
        return {key: _rewrite_model_refs(item, canonical_ids) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rewrite_model_refs(item, canonical_ids) for item in value]
    return value


def _canonical_id(runtime_id: str, canonical_ids: dict[str, str]) -> str:
    try:
        return canonical_ids[runtime_id]
    except KeyError as error:
        raise ValueError(
            f"AnyWidget state references model {runtime_id!r} outside its graph"
        ) from error
