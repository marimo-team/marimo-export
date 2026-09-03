"""Close replay model, function, file, and UI resources for one projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import msgspec

from marimo_export._cell_ids import canonical_cell_id
from marimo_export._json import JsonObject, JsonValue, canonical_bytes, json_value, sha256_bytes
from marimo_export._marimo.compat.output_data import (
    ProjectionReplacements,
    canonical_object_id,
    output_references,
    rewrite_ui_value,
    scoped_ui_id,
)
from marimo_export.errors import OutputError

if TYPE_CHECKING:
    from marimo_export._marimo.compat.projections import ProjectionRecording


def resources(
    recording: ProjectionRecording,
    outputs: tuple[Any, ...],
    projection_identity: str,
    owner_cell_id: str,
) -> tuple[JsonObject, ProjectionReplacements]:
    _projection_identity(projection_identity)
    files: dict[str, str] = {}
    available_notifications = {
        str(notification.model_id): notification
        for notification in recording.view.get_model_notifications()
    }
    roots: set[str] = set()
    ui_ids: set[str] = set()
    random_ids: set[str] = set()
    ui_random_ids: dict[str, str] = {}
    for output in outputs:
        model_ids, object_ids, output_random_ids, output_ui_random_ids = output_references(
            str(output.mimetype),
            msgspec.to_builtins(output.data),
            frozenset(available_notifications),
        )
        roots.update(model_ids)
        ui_ids.update(object_ids)
        random_ids.update(output_random_ids)
        for object_id, random_id in output_ui_random_ids.items():
            previous = ui_random_ids.setdefault(object_id, random_id)
            if previous != random_id:
                raise OutputError(
                    f"UI object {object_id!r} has conflicting random IDs",
                    code="output_execution_failed",
                )
    notifications = _reachable_models(roots, available_notifications)
    canonical_ids = {
        str(notification.model_id): f"projection-{projection_identity}-model-{index}"
        for index, notification in enumerate(notifications)
    }
    canonical_notifications: list[JsonValue] = []
    if notifications:
        from marimo_export._marimo.compat.anywidget import _canonical_notification

        canonical_notifications = [
            cast(
                JsonValue,
                _canonical_notification(notification, canonical_ids, files).to_json_serializable(),
            )
            for notification in notifications
        ]
    cell_replacements = {
        str(runtime_id): source_id for source_id, runtime_id in recording.cell_ids.items()
    }
    registry = recording.child._runtime_context.function_registry.namespaces
    ui_registry = recording.child._runtime_context.ui_element_registry
    from marimo_export._marimo.compat.inspection import _control_tree_entries, _is_sensitive

    ui_elements: dict[str, Any] = {}
    ui_aliases: dict[str, str] = {}
    ui_scope_keys: dict[str, str] = {}
    ordered_roots = sorted(
        ui_ids,
        key=lambda object_id: canonical_object_id(object_id, recording.cell_ids),
    )
    for root_index, object_id in enumerate(ordered_roots):
        try:
            resolved_id = _resolve_ui_object_id(ui_registry, object_id, owner_cell_id)
            element = ui_registry.get_object(cast(Any, resolved_id))
        except (AssertionError, KeyError):
            if object_id in available_notifications:
                continue
            raise OutputError(
                f"UI object {object_id!r} is unavailable during projection capture",
                code="output_execution_failed",
            ) from None
        ui_aliases[object_id] = resolved_id
        for control, path in _control_tree_entries(element):
            control_id = str(control._id)
            scope_key = _ui_scope_key(owner_cell_id, root_index, path)
            previous = ui_scope_keys.setdefault(control_id, scope_key)
            if scope_key < previous:
                ui_scope_keys[control_id] = scope_key
            ui_elements[control_id] = control
    ui_ids = set(ui_elements)
    scoped_ui_ids = {
        object_id: scoped_ui_id(
            ui_scope_keys[object_id],
            projection_identity,
            owner_cell_id,
        )
        for object_id in sorted(ui_ids)
    }
    scoped_random_ids = {
        str(element._random_id): f"{scoped_ui_ids[object_id]}-random"
        for object_id, element in ui_elements.items()
    }
    recorded_random_ids = {
        random_id: f"{scoped_ui_ids[ui_aliases[object_id]]}-random"
        for object_id, random_id in ui_random_ids.items()
        if object_id in ui_aliases
    }
    missing_random_ids = random_ids - set(scoped_random_ids) - set(recorded_random_ids)
    if missing_random_ids:
        missing = sorted(missing_random_ids)[0]
        raise OutputError(
            f"UI random ID {missing!r} is unavailable during projection capture",
            code="output_execution_failed",
        )
    for object_id, scoped_id in scoped_ui_ids.items():
        recording.ui_scopes.setdefault(object_id, set()).add(scoped_id)
    functions: JsonObject = {}
    for object_id in sorted(ui_ids):
        names = sorted(registry[object_id].functions) if object_id in registry else []
        if names:
            element = ui_elements[object_id]
            args = getattr(element, "_component_args", None)
            inert_form_validation = (
                names == ["validate"]
                and isinstance(args, Mapping)
                and args.get("should-validate") is False
            )
            if not inert_form_validation:
                raise OutputError(
                    f"UI object {object_id!r} exposes nonportable Python functions",
                    code="output_execution_failed",
                    details={"object_id": object_id, "functions": names},
                )
            names = []
        functions[scoped_ui_ids[object_id]] = names
    ui_replacements = {
        **scoped_ui_ids,
        **scoped_random_ids,
        **recorded_random_ids,
    }
    for alias, object_id in ui_aliases.items():
        _add_ui_alias(ui_replacements, alias, scoped_ui_ids[object_id])
    for object_id, scoped_id in scoped_ui_ids.items():
        for alias in {
            canonical_object_id(object_id, recording.cell_ids),
            canonical_cell_id(object_id),
        }:
            _add_ui_alias(ui_replacements, alias, scoped_id)
    replacements = ProjectionReplacements(
        identifiers={
            **canonical_ids,
            **ui_replacements,
            **cell_replacements,
        },
        models=canonical_ids,
        ui=ui_replacements,
    )
    ui_values: JsonObject = {}

    for object_id in sorted(ui_ids):
        element = ui_elements[object_id]
        if _is_sensitive(element):
            raise OutputError(
                f"UI object {object_id!r} contains sensitive input state",
                code="output_execution_failed",
            )
        frontend_value = msgspec.to_builtins(element._value_frontend)
        ui_values[scoped_ui_ids[object_id]] = json_value(
            rewrite_ui_value(frontend_value, canonical_ids, ui_replacements),
            f"UI object {object_id!r} replay value",
        )
    resources: JsonObject = {
        "files": files,
        "modelNotifications": canonical_notifications,
        "functions": functions,
        "uiValues": ui_values,
    }
    return resources, replacements


def _resolve_ui_object_id(registry: Any, object_id: str, owner_cell_id: str) -> str:
    objects = getattr(registry, "_objects", {})
    if object_id in objects:
        return object_id
    from marimo._runtime.scratch import SCRATCH_CELL_ID

    prefix = f"{SCRATCH_CELL_ID}-"
    if not object_id.startswith(prefix):
        raise KeyError(object_id)
    ordinal = object_id.removeprefix(prefix)
    if not ordinal.isdigit():
        raise KeyError(object_id)
    constructing = getattr(registry, "_constructing_cells", {})
    matches = [
        str(candidate)
        for candidate, cell_id in objects.items()
        if canonical_cell_id(constructing.get(candidate, cell_id)) == owner_cell_id
        and canonical_cell_id(candidate).endswith(f"-{ordinal}")
    ]
    if len(matches) != 1:
        raise KeyError(object_id)
    return matches[0]


def _add_ui_alias(replacements: dict[str, str], alias: str, scoped_id: str) -> None:
    previous = replacements.setdefault(alias, scoped_id)
    if previous != scoped_id:
        raise OutputError(
            f"UI object alias {alias!r} has conflicting projection ownership",
            code="output_execution_failed",
        )


def _ui_scope_key(owner_cell_id: str, root_index: int, path: tuple[Any, ...]) -> str:
    path_value = [step.to_value() for step in path]
    path_sha256 = sha256_bytes(canonical_bytes(cast(JsonValue, path_value)))
    return f"{owner_cell_id}-root-{root_index}-{path_sha256}"


def _projection_identity(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise OutputError(
            "projection identity must be a lowercase SHA-256 digest",
            code="output_execution_failed",
        )
    return value


def _reachable_models(
    roots: set[str],
    available: Mapping[str, Any],
) -> list[Any]:
    from marimo._messaging.notification import ModelOpen

    from marimo_export._marimo.compat.anywidget import _model_refs

    ordered: list[Any] = []
    pending = list(reversed(sorted(roots)))
    seen: set[str] = set()
    while pending:
        model_id = pending.pop()
        if model_id in seen:
            continue
        notification = available.get(model_id)
        if notification is None or not isinstance(notification.message, ModelOpen):
            raise OutputError(
                f"AnyWidget model {model_id!r} is unavailable during projection capture",
                code="output_execution_failed",
            )
        seen.add(model_id)
        ordered.append(notification)
        children = sorted(_model_refs(notification.message.state))
        pending.extend(reversed(children))
    return ordered


__all__ = ["resources"]
