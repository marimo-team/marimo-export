"""Scenario object patching for materialized notebook objects.

Scenario state may target object attributes such as ``dropdown.value``. These
object patches are applied after the cell that creates the object has been
materialized and before downstream cells evaluate. This module validates those
dotted paths, applies the patches, and keeps marimo UI element HTML in sync so
static exports show the same value that downstream cells observed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moexport.evaluate._types import JsonDict, ObjectPatches
from moexport.evaluate._values import value_preview


def object_patch_roots(object_patches: ObjectPatches) -> set[str]:
    return {_object_patch_target_parts(target)[0] for target in object_patches}


def _object_patch_target_parts(target: str) -> list[str]:
    parts = target.split(".")
    if len(parts) < 2 or any(not part.isidentifier() for part in parts):
        raise ValueError(
            "scenario object patch targets must use dotted Python attribute paths, "
            f"got {target!r}"
        )
    return parts


def apply_object_patches(
    glbls: Mapping[str, Any],
    object_patches: ObjectPatches,
    *,
    roots: set[str],
) -> list[JsonDict]:
    applied: list[JsonDict] = []
    for target, value in object_patches.items():
        parts = _object_patch_target_parts(target)
        root = parts[0]
        if root not in roots:
            continue
        if root not in glbls:
            raise NameError(f"scenario object patch root {root!r} was not materialized")

        parent = glbls[root]
        for attr in parts[1:-1]:
            parent = getattr(parent, attr)

        leaf = parts[-1]
        if leaf == "value" and hasattr(parent, "_update"):
            frontend_value = _frontend_update_value(parent, value)
            parent._update(frontend_value)
            _sync_ui_element_initial_html(parent, frontend_value)
        else:
            setattr(parent, leaf, value)
        applied.append(
            {
                "target": target,
                "root": root,
                "value_preview": value_preview(value),
            }
        )
    return applied


def _frontend_update_value(element: Any, value: Any) -> Any:
    """Translate scenario values into UIElement frontend payloads.

    Scenario specs are authored against `element.value`, which is the Python
    value users see. marimo's private `_update()` method accepts frontend
    payloads. For dropdown and multiselect widgets, frontend payloads are option
    keys while Python values may be mapped option objects.
    """

    options = getattr(element, "options", None)
    if not isinstance(options, dict):
        return value

    current_frontend = getattr(element, "_value_frontend", None)
    if isinstance(current_frontend, list):
        if value is None:
            return []
        if isinstance(value, list | tuple | set):
            return [_option_key(options, item) for item in value]
        return [_option_key(options, value)]

    return _option_key(options, value)


def _option_key(options: Mapping[str, Any], value: Any) -> Any:
    if isinstance(value, str) and value in options:
        return value

    for key, option_value in options.items():
        if option_value == value:
            return key

    return value


def _sync_ui_element_initial_html(element: Any, value: Any) -> None:
    """Make exported static UI HTML show the scenario value.

    `UIElement._update()` changes the Python-side value, but the element's HTML
    was created when its defining cell ran. Static notebook snapshots need that
    HTML to carry the scenario value so downstream outputs and visible UI chips
    agree.
    """

    args = getattr(element, "_args", None)
    if args is None or not hasattr(element, "_mime_"):
        return

    try:
        frontend_value = element._frontend_initial_value(value)
    except AttributeError:
        frontend_value = value

    from marimo._plugins.core.web_component import build_ui_plugin

    inner_text = build_ui_plugin(
        args.component_name,
        frontend_value,
        args.label,
        args.args,
        args.slotted_html,
    )
    text = (
        f"<marimo-ui-element object-id='{element._id}' "
        + f"random-id='{element._random_id}'>"
        + inner_text
        + "</marimo-ui-element>"
    )

    element._initial_value_frontend = frontend_value
    element._value_frontend = frontend_value
    element._inner_text = inner_text
    element._text = text

    mimetype, data = element._mime_()
    serialized = {"mimetype": mimetype, "data": data}
    element._serialized_mime_bundle = serialized
    setattr(element, "serialized_mime_bundle", serialized)
