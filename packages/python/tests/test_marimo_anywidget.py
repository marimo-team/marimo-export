from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

import anywidget
import ipywidgets
import pytest
import traitlets
from marimo._plugins.ui._impl.anywidget.init import init_marimo_widget
from marimo._plugins.ui._impl.comm import MarimoComm
from marimo_export._marimo.compat.anywidget import anywidget_payload
from marimo_export.errors import OutputError
from marimo_export.exporters.anywidget import bundle


class _Child(anywidget.AnyWidget):
    _esm = "export default { render() {} }"
    _css = "button { color: red; }"
    value = traitlets.Int(7).tag(sync=True)


@dataclass(frozen=True)
class _ChildRef:
    widget: _Child


def _serialize_child(value: _ChildRef, owner: object) -> str:
    del owner
    return f"anywidget:{value.widget._model_id}"


class _Parent(anywidget.AnyWidget):
    _esm = "export default { render() {} }"
    child = traitlets.Any().tag(sync=True, to_json=_serialize_child)


@contextmanager
def _active_graph() -> Iterator[tuple[_Child, _Parent]]:
    child = _Child()
    parent = _Parent(child=_ChildRef(child))
    widgets: tuple[ipywidgets.Widget, ...] = (
        child.layout,
        parent.layout,
        child,
        parent,
    )
    try:
        for widget in widgets:
            init_marimo_widget(widget)
        yield child, parent
    finally:
        for widget in reversed(widgets):
            widget.close()


def test_snapshot_follows_trait_serialized_model_references() -> None:
    with _active_graph() as (_, parent):
        snapshot = anywidget_payload(parent)
        asset = bundle(parent)

    document = cast(dict[str, Any], json.loads(snapshot.payload))
    notifications = cast(list[dict[str, Any]], document["modelNotifications"])
    root_state = cast(dict[str, Any], notifications[0]["message"])["state"]
    child_id = cast(str, root_state["child"]).removeprefix("anywidget:")

    assert asset.metadata["models"] == snapshot.model_count
    assert asset.media_type == "application/vnd.marimo-export.anywidget.v1+json"
    assert any(
        notification["model_id"] == child_id
        and cast(dict[str, Any], notification["message"])["state"]["value"] == 7
        for notification in notifications
    )


def test_snapshot_reads_current_state_and_css() -> None:
    with _active_graph() as (child, _):
        child.value = 19
        asset = bundle(child)

    document = cast(dict[str, Any], json.loads(asset.data))
    notifications = cast(list[dict[str, Any]], document["modelNotifications"])
    root_state = cast(dict[str, Any], notifications[0]["message"])["state"]

    assert root_state["value"] == 19
    assert root_state["_css"] == "button { color: red; }"


def test_snapshot_rejects_closed_reachable_model() -> None:
    with _active_graph() as (child, parent):
        comm = child.comm
        assert isinstance(comm, MarimoComm)
        comm.close()

        with pytest.raises(
            OutputError,
            match="requires a model already active",
        ):
            anywidget_payload(parent)
