from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from marimo._messaging.notification import (
    EsmSpec,
    ModelLifecycleNotification,
    ModelOpen,
)
from marimo._plugins.ui._impl.anywidget.utils import extract_buffer_paths
from marimo._plugins.ui._impl.comm import MarimoComm
from marimo._plugins.ui._impl.from_anywidget import (
    anywidget as MarimoAnyWidget,
)
from marimo._plugins.ui._impl.from_anywidget import get_anywidget_model_id
from marimo._runtime.virtual_file import read_virtual_file
from marimo._types.ids import WidgetModelId
from marimo._utils.code import hash_code
from marimo._utils.data_uri import build_data_url

from marimo_export._json import json_value
from marimo_export.errors import ProjectionError

from ._anywidget_assets import portable_css, validate_embedded_esm

ANYWIDGET_PAYLOAD_SCHEMA = "marimo-export.anywidget.v1"

_ANYWIDGET_REF_PREFIX = "anywidget:"
_IPYWIDGET_REF_PREFIX = "IPY_MODEL_"
_VIRTUAL_FILE = re.compile(r"^(?:\./|/)?@file/(?P<size>\d+)-(?P<name>.+)$")


@dataclass(frozen=True, slots=True)
class AnyWidgetPayload:
    payload: bytes
    root_model_id: str
    model_count: int


def capture_anywidget_payload(value: object) -> bytes:
    """Snapshot one live AnyWidget model graph as canonical payload bytes."""

    return anywidget_payload(value).payload


def anywidget_payload(value: object) -> AnyWidgetPayload:
    root = _widget_value(value)
    runtime_root_id = _model_id(root)
    ordered = _live_model_graph(root, runtime_root_id)
    canonical_ids = {
        str(notification.model_id): f"model-{index}" for index, notification in enumerate(ordered)
    }
    files: dict[str, str] = {}
    canonical = [
        _canonical_notification(notification, canonical_ids, files).to_json_serializable()
        for notification in ordered
    ]
    root_model_id = canonical_ids[runtime_root_id]
    document = {
        "schema": ANYWIDGET_PAYLOAD_SCHEMA,
        "rootModelId": root_model_id,
        "files": files,
        "modelNotifications": canonical,
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


def _widget_value(value: object) -> Any:
    candidate = value
    if isinstance(value, MarimoAnyWidget):
        candidate = value.widget

    try:
        import ipywidgets
    except ImportError as error:
        raise ProjectionError(
            "AnyWidget export requires the anywidget and ipywidgets packages"
        ) from error
    if not isinstance(candidate, ipywidgets.Widget):
        raise ProjectionError(
            "AnyWidget export requires an anywidget.AnyWidget or mo.ui.anywidget value"
        )
    return candidate


def _live_model_graph(
    root: Any,
    root_id: str,
) -> list[ModelLifecycleNotification]:
    import ipywidgets
    from ipywidgets.widgets import widget as widget_module

    registry = getattr(widget_module, "_instances", None)
    if not isinstance(registry, Mapping):
        raise ProjectionError("AnyWidget export could not inspect active widget models")
    models = cast(Mapping[str, Any], registry)
    if models.get(root_id) is not root:
        raise ProjectionError(
            "AnyWidget export requires a model already active in the running marimo session"
        )

    ordered: list[ModelLifecycleNotification] = []
    queue = deque([root_id])
    seen: set[str] = set()
    while queue:
        model_id = queue.popleft()
        if model_id in seen:
            continue
        seen.add(model_id)
        widget = models.get(model_id)
        if not isinstance(widget, ipywidgets.Widget):
            raise ProjectionError(
                f"AnyWidget state references model {model_id!r} outside the live model graph"
            )
        notification = _model_open(widget)
        if str(notification.model_id) != model_id:
            raise ProjectionError(f"AnyWidget model {model_id!r} changed identity during capture")
        if not isinstance(notification.message, ModelOpen):
            raise ProjectionError("AnyWidget snapshot contains a non-open model")
        ordered.append(notification)
        queue.extend(_model_refs(notification.message.state))
    return ordered


def _model_id(widget: Any) -> str:
    try:
        model_id = get_anywidget_model_id(widget)
    except RuntimeError as error:
        raise ProjectionError("AnyWidget export could not resolve a model ID") from error
    except Exception as error:
        raise ProjectionError("AnyWidget export could not resolve a model ID") from error
    return str(model_id)


def _model_open(widget: Any) -> ModelLifecycleNotification:
    comm = _active_marimo_comm(widget)
    state, buffer_paths, buffers = extract_buffer_paths(widget.get_state())
    if _active_marimo_comm(widget) is not comm:
        raise ProjectionError("AnyWidget model changed comm during capture")
    state = dict(state)
    state.pop("_esm", None)
    return ModelLifecycleNotification(
        model_id=WidgetModelId(_model_id(widget)),
        message=ModelOpen(
            state=state,
            buffer_paths=[list(path) for path in buffer_paths],
            buffers=[bytes(buffer) for buffer in buffers],
            esm_spec=comm.esm_spec,
        ),
    )


def _active_marimo_comm(widget: Any) -> MarimoComm:
    comm = getattr(widget, "comm", None)
    if (
        not isinstance(comm, MarimoComm)
        or comm._closed
        or comm.comm_manager.comms.get(comm.comm_id) is not comm
    ):
        raise ProjectionError(
            "AnyWidget export requires a model already active in the running marimo session"
        )
    return comm


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
    if not isinstance(message, ModelOpen):
        raise ProjectionError("AnyWidget snapshot contains a non-open model")
    state = cast(
        dict[str, Any],
        _rewrite_model_refs(message.state, canonical_ids),
    )
    css = state.get("_css")
    if css is not None:
        if not isinstance(css, str):
            raise ProjectionError("AnyWidget _css state must be a string")
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


def _canonical_esm_spec(
    spec: EsmSpec | None,
    files: dict[str, str],
) -> EsmSpec | None:
    if spec is None:
        return None
    if spec.url.startswith(("https://", "http://", "data:")):
        return spec
    match = _VIRTUAL_FILE.fullmatch(spec.url)
    if match is None:
        raise ProjectionError(f"AnyWidget ESM uses an unsupported URL: {spec.url!r}")
    expected_size = int(match.group("size"))
    contents = read_virtual_file(match.group("name"), expected_size)
    if len(contents) != expected_size:
        raise ProjectionError(
            f"AnyWidget ESM {spec.url!r} declared {expected_size} bytes but "
            f"returned {len(contents)}"
        )
    try:
        source = contents.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectionError("AnyWidget ESM must contain UTF-8 JavaScript") from error
    if hash_code(source) != spec.hash:
        raise ProjectionError("AnyWidget ESM contents do not match marimo's model code hash")
    validate_embedded_esm(source)
    digest = hashlib.sha256(contents).hexdigest()
    url = f"./@file/{len(contents)}-anywidget-{digest}.js"
    media_type = mimetypes.guess_type(url)[0] or "text/javascript"
    files[url] = build_data_url(media_type, base64.b64encode(contents))
    return EsmSpec(url=url, hash=spec.hash)


def _rewrite_model_refs(
    value: object,
    canonical_ids: dict[str, str],
) -> object:
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
        raise ProjectionError(
            f"AnyWidget state references model {runtime_id!r} outside its graph"
        ) from error


__all__ = [
    "ANYWIDGET_PAYLOAD_SCHEMA",
    "AnyWidgetPayload",
    "anywidget_payload",
    "capture_anywidget_payload",
]
