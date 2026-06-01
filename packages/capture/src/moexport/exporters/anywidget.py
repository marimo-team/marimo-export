"""AnyWidget exporters.

The exporter stores the widget frontend module, optional stylesheet, synced
state, and binary buffers as content-addressed files. The static web runtime
reads this descriptor and chooses the hydration strategy.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from moexport.artifacts import Artifact, ArtifactData, JsonObject, JsonValue
from moexport.blobs import BlobRef
from moexport.exporters._core import ExporterContext, ExporterOptions
from moexport.exporters._optional import import_optional
from moexport.jsonio import jsonable

if TYPE_CHECKING:
    import anywidget

FORMAT = "anywidget.bundle.v1"
MEDIA_TYPE = "application/vnd.moexport.anywidget+json"
MODULE_MEDIA_TYPE = "text/javascript"
CSS_MEDIA_TYPE = "text/css"
BUFFER_MEDIA_TYPE = "application/octet-stream"

_SCHEMA: Literal["moexport.anywidget.bundle.v1"] = "moexport.anywidget.bundle.v1"
_JSON_OBJECT = TypeAdapter(JsonObject)

_RESERVED_STATE_KEYS = {
    "_anywidget_id",
    "_css",
    "_dom_classes",
    "_esm",
    "_model_module",
    "_model_module_version",
    "_model_name",
    "_msg_callbacks",
    "_property_lock",
    "_states_to_send",
    "_view_count",
    "_view_module",
    "_view_module_version",
    "_view_name",
    "comm",
    "keys",
    "layout",
    "log",
    "tabbable",
    "tooltip",
}

_REMOTE_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
_RELATIVE_JS_PATTERN = re.compile(
    r"""(?:import\s+(?:[^'"]+?\s+from\s+)?|export\s+[^'"]*?\s+from\s+|import\s*\()\s*["'](\.{1,2}/[^"']+)["']""",
    re.MULTILINE,
)
_RELATIVE_JS_URL_PATTERN = re.compile(
    r"""new\s+URL\(\s*["'](\.{1,2}/[^"']+)["']\s*,\s*import\.meta\.url\s*\)""",
    re.MULTILINE,
)
_RELATIVE_CSS_URL_PATTERN = re.compile(
    r"""url\(\s*["']?(?!data:|https?:|/|#)(\.{1,2}/[^)"']+)""",
    re.IGNORECASE,
)


class AnyWidgetOptions(ExporterOptions):
    """Options for AnyWidget bundle export."""


class AnyWidgetAssetRefs(BaseModel):
    """Blob references for browser-visible widget assets."""

    model_config = ConfigDict(frozen=True)

    module: BlobRef = Field(
        description="JavaScript module containing the widget frontend.",
    )
    style: BlobRef | None = Field(
        default=None,
        description="Optional CSS stylesheet used by the widget frontend.",
    )


class AnyWidgetBufferRef(BaseModel):
    """One binary widget-state buffer stored outside the JSON state."""

    model_config = ConfigDict(frozen=True)

    path: list[str | int] = Field(
        description="Nested JSON path where the loader should restore the buffer.",
    )
    data: BlobRef = Field(
        description="Content-addressed bytes for this binary buffer.",
    )


class AnyWidgetDescriptor(BaseModel):
    """Portable descriptor for reconstructing one AnyWidget instance."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_: Literal["moexport.anywidget.bundle.v1"] = Field(
        default=_SCHEMA,
        alias="schema",
        description="Schema identifier for this AnyWidget descriptor.",
    )
    anywidget_id: str = Field(
        description="Stable widget id exposed by anywidget or derived from the class.",
    )
    state: dict[str, BlobRef] = Field(
        description=(
            "Top-level synced widget state traits stored as separate JSON blobs. "
            "Loaders reconstruct the full state by reading each keyed value."
        ),
    )
    assets: AnyWidgetAssetRefs = Field(
        description="Frontend assets needed by a browser-side AnyWidget reader.",
    )
    buffers: list[AnyWidgetBufferRef] = Field(
        description="Binary state buffers paired with their restoration paths.",
    )


class _Asset:
    """Resolved frontend asset source text."""

    __slots__ = ("text",)

    def __init__(self, *, text: str) -> None:
        self.text = text


_OPTIONS = TypeAdapter(AnyWidgetOptions)


def bundle(
    value: "anywidget.AnyWidget",
    ctx: ExporterContext,
    **options: Any,
) -> Artifact:
    """Export an AnyWidget or `mo.ui.anywidget(...)` value as portable files."""

    _OPTIONS.validate_python(options)
    widget = _unwrap_widget(value)
    widget_cls = type(widget)

    module_asset = _extract_asset(widget_cls, widget, "_esm", required=True)
    if module_asset is None:
        raise ValueError(f"{widget_cls.__name__} is missing required '_esm' contents.")

    style_asset = _extract_asset(widget_cls, widget, "_css", required=False)
    _validate_portable_asset(module_asset, kind="module")
    if style_asset is not None:
        _validate_portable_asset(style_asset, kind="style")

    state, buffer_paths, buffers = _serialize_widget_state(widget)
    anywidget_id = str(getattr(widget, "_anywidget_id", widget_cls.__name__))

    module_blob = ctx.write_blob(
        "widget.js",
        module_asset.text.encode("utf-8"),
        media_type=MODULE_MEDIA_TYPE,
    )
    style_blob = (
        ctx.write_blob(
            "widget.css",
            style_asset.text.encode("utf-8"),
            media_type=CSS_MEDIA_TYPE,
        )
        if style_asset is not None
        else None
    )
    buffer_records = [
        AnyWidgetBufferRef(
            path=path,
            data=ctx.write_blob(
                f"buffer-{index}.bin",
                bytes(buffer),
                media_type=BUFFER_MEDIA_TYPE,
            ),
        )
        for index, (path, buffer) in enumerate(zip(buffer_paths, buffers, strict=True))
    ]
    state_blobs = {
        key: ctx.write_blob(
            f"state-{key}.json",
            _json_bytes(value),
            media_type="application/json",
        )
        for key, value in sorted(state.items())
    }
    descriptor = AnyWidgetDescriptor(
        anywidget_id=anywidget_id,
        state=state_blobs,
        assets=AnyWidgetAssetRefs(module=module_blob, style=style_blob),
        buffers=buffer_records,
    )
    descriptor_blob = ctx.write_blob(
        "anywidget.json",
        _json_bytes(descriptor),
        media_type=MEDIA_TYPE,
    )

    files = {"descriptor": descriptor_blob, "module": module_blob}
    if style_blob is not None:
        files["style"] = style_blob
    for key, state_blob in state_blobs.items():
        files[f"state.{key}"] = state_blob
    for index, record in enumerate(buffer_records):
        files[f"buffer_{index}"] = record.data

    return Artifact(
        format=FORMAT,
        media_type=MEDIA_TYPE,
        data=ArtifactData(files=files, entry="descriptor"),
        metadata=_metadata(
            anywidget_id=anywidget_id,
            buffer_count=len(buffer_records),
            has_style=style_blob is not None,
            state_keys=sorted(state),
        ),
    )


def _unwrap_widget(value: object) -> Any:
    _sync_marimo_wrapper(value)
    widget = _anywidget_instance(value)
    if widget is not None:
        return widget

    wrapped = getattr(value, "widget", None)
    widget = _anywidget_instance(wrapped)
    if widget is not None:
        return widget

    if isinstance(value, type):
        raise TypeError("widget must be an AnyWidget instance, not a class.")

    raise TypeError(
        "AnyWidget export requires an anywidget.AnyWidget instance or a "
        "mo.ui.anywidget(...) wrapper."
    )


def _sync_marimo_wrapper(value: object) -> None:
    sync = getattr(value, "_ensure_widget_synced", None)
    if callable(sync):
        sync()


def _anywidget_instance(value: object) -> Any | None:
    anywidget_module = import_optional(
        "anywidget",
        package="anywidget",
        extra="anywidget",
        purpose="AnyWidget export",
    )
    if isinstance(value, type):
        return None

    return value if isinstance(value, anywidget_module.AnyWidget) else None


def _extract_asset(
    widget_cls: type[Any],
    widget: Any,
    attr_name: str,
    *,
    required: bool,
) -> _Asset | None:
    value = getattr(widget, attr_name, None)
    if value is None:
        if required:
            raise ValueError(
                f"{widget_cls.__name__} is missing required {attr_name!r} contents."
            )
        return None

    text = str(value)
    if not text.strip():
        if required:
            raise ValueError(
                f"{widget_cls.__name__} has empty required {attr_name!r} contents."
            )
        return None

    if _REMOTE_URL_PATTERN.match(text.strip()):
        raise ValueError(f"Remote {attr_name} URLs are not supported: {text}")

    class_value = getattr(widget_cls, attr_name, None)
    if isinstance(class_value, str) and _REMOTE_URL_PATTERN.match(class_value.strip()):
        raise ValueError(f"Remote {attr_name} URLs are not supported: {class_value}")

    return _Asset(text=text)


def _validate_portable_asset(
    asset: _Asset, *, kind: Literal["module", "style"]
) -> None:
    if kind == "module":
        if _RELATIVE_JS_PATTERN.search(asset.text) or _RELATIVE_JS_URL_PATTERN.search(
            asset.text
        ):
            raise ValueError(
                "AnyWidget module assets with relative imports or relative "
                "import.meta URLs require a bundling step before static export."
            )
        return

    if _RELATIVE_CSS_URL_PATTERN.search(asset.text):
        raise ValueError(
            "AnyWidget CSS assets with relative url(...) references require a "
            "bundling step before static export."
        )


def _serialize_widget_state(
    widget: Any,
) -> tuple[JsonObject, list[list[str | int]], list[memoryview]]:
    raw_state = _get_widget_state(widget)
    filtered_state = {
        key: value
        for key, value in raw_state.items()
        if key not in _RESERVED_STATE_KEYS
    }
    clean_state, buffer_paths, buffers = remove_buffers(filtered_state)
    return _json_object(clean_state), buffer_paths, buffers


def _get_widget_state(widget: Any) -> dict[str, Any]:
    get_state = getattr(widget, "get_state", None)
    if callable(get_state):
        try:
            return dict(get_state(drop_defaults=False))
        except TypeError:
            return dict(get_state())

    traits = getattr(widget, "traits", None)
    trait_values = getattr(widget, "trait_values", None)
    if callable(traits) and callable(trait_values):
        sync_traits = traits(sync=True)
        values = trait_values(sync=True)
        return {key: values[key] for key in sync_traits}

    raise TypeError(
        "Could not serialize widget state. Expected get_state() or "
        "traitlets-style trait_values()/traits()."
    )


def _metadata(
    *,
    anywidget_id: str,
    buffer_count: int,
    has_style: bool,
    state_keys: list[str],
) -> JsonObject:
    state_key_values: list[JsonValue] = [key for key in state_keys]
    return {
        "anywidget_id": anywidget_id,
        "buffer_count": buffer_count,
        "has_style": has_style,
        "state_keys": state_key_values,
    }


def _json_object(value: object) -> JsonObject:
    # json round-tripping accepts common tuple/list shapes while still failing
    # fast for Python objects that cannot be represented in a static bundle.
    payload = json.loads(json.dumps(value, allow_nan=False))
    return cast(JsonObject, _JSON_OBJECT.validate_python(payload))


def _json_bytes(value: object) -> bytes:
    payload = (
        value.model_dump(mode="json", by_alias=True)
        if isinstance(value, BaseModel)
        else jsonable(value)
    )
    return (
        json.dumps(
            payload,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _separate_buffers(
    substate: object,
    path: list[str | int],
    buffer_paths: list[list[str | int]],
    buffers: list[memoryview],
) -> object:
    binary_types = (memoryview, bytearray, bytes)
    clone: list[Any] | dict[str, Any] | None = None

    if isinstance(substate, (list, tuple)):
        for index, value in enumerate(substate):
            if isinstance(value, binary_types):
                if clone is None:
                    clone = list(substate)
                clone[index] = None
                buffers.append(memoryview(value))
                buffer_paths.append([*path, index])
            elif isinstance(value, (dict, list, tuple)):
                next_value = _separate_buffers(
                    value,
                    [*path, index],
                    buffer_paths,
                    buffers,
                )
                if next_value is not value:
                    if clone is None:
                        clone = list(substate)
                    clone[index] = next_value
    elif isinstance(substate, dict):
        for key, value in substate.items():
            typed_key = cast("str | int", key)
            if isinstance(value, binary_types):
                if clone is None:
                    clone = dict(substate)
                del clone[typed_key]
                buffers.append(memoryview(value))
                buffer_paths.append([*path, typed_key])
            elif isinstance(value, (dict, list, tuple)):
                next_value = _separate_buffers(
                    value,
                    [*path, typed_key],
                    buffer_paths,
                    buffers,
                )
                if next_value is not value:
                    if clone is None:
                        clone = dict(substate)
                    clone[typed_key] = next_value
    else:
        raise TypeError(f"expected state to be a list or dict, not {substate!r}")

    return clone if clone is not None else substate


def remove_buffers(
    state: dict[str, Any],
) -> tuple[dict[str, Any], list[list[str | int]], list[memoryview]]:
    """Split binary buffers out of nested widget state."""

    buffer_paths: list[list[str | int]] = []
    buffers: list[memoryview] = []
    clean_state = _separate_buffers(state, [], buffer_paths, buffers)
    return cast(dict[str, Any], clean_state), buffer_paths, buffers
