"""Rewrite structured Marimo output data into portable snapshot values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any, cast

import msgspec

from marimo_export._cell_ids import canonical_cell_id
from marimo_export._json import JsonValue, canonical_bytes, json_value
from marimo_export._marimo.compat.file_closure import (
    MIMEBUNDLE_MIMETYPE,
    close_output_files,
    decode_mimebundle,
    mimebundle_entries,
)

if TYPE_CHECKING:
    from marimo_export._marimo.compat.projections import ProjectionRecording

_HTML_MIMETYPES = frozenset({"text/html", "text/markdown"})
_WIDGET_VIEW_MIMETYPE = "application/vnd.jupyter.widget-view+json"
_UI_REFERENCE_FIELDS = frozenset(
    {"object_id", "object-id", "objectId", "random_id", "random-id", "randomId"}
)
_COMPONENT_JSON_ATTRIBUTES = (
    ({"marimo-json-output"}, {"data-json-data"}, "nested-html"),
    ({"marimo-dict"}, {"data-element-ids"}, "identifier-keys"),
    ({"marimo-form"}, {"data-element-id"}, "identifier"),
)


@dataclass(frozen=True, slots=True)
class ProjectionReplacements:
    identifiers: Mapping[str, str]
    models: Mapping[str, str]
    ui: Mapping[str, str]


def cell_output_value(
    recording: ProjectionRecording,
    output: Any,
    replacements: ProjectionReplacements,
) -> JsonValue:
    if output is None:
        return None
    channel = output.channel.value if hasattr(output.channel, "value") else str(output.channel)
    mimetype = str(output.mimetype)
    data = _portable_data(output.data, mimetype, replacements)
    data = close_output_files(recording, data, mimetype)
    return {
        "channel": channel,
        "mimetype": str(output.mimetype),
        "data": data,
    }


def _portable_data(
    value: object,
    mimetype: str,
    replacements: ProjectionReplacements,
) -> JsonValue:
    built = msgspec.to_builtins(value)
    rewritten = _rewrite_output_data(built, mimetype, replacements)
    return json_value(rewritten, "Marimo output data")


def _rewrite_output_data(
    value: object,
    mimetype: str,
    replacements: ProjectionReplacements,
) -> object:
    if mimetype in _HTML_MIMETYPES and isinstance(value, str):
        return _rewrite_html_identifiers(value, replacements)
    if mimetype == _WIDGET_VIEW_MIMETYPE and isinstance(value, dict):
        return _rewrite_widget_view(cast(Mapping[object, object], value), replacements.models)
    if mimetype == MIMEBUNDLE_MIMETYPE:
        bundle = decode_mimebundle(value)
        if bundle is None:
            return value
        rewritten = _rewrite_mimebundle(bundle, replacements)
        if isinstance(value, str):
            return canonical_bytes(cast(JsonValue, rewritten)).decode("utf-8")
        return rewritten
    return value


def _rewrite_html_identifiers(value: str, replacements: ProjectionReplacements) -> str:
    if not any(
        attribute in value
        for attribute in (
            "object-id",
            "random-id",
            "model-id",
            "data-initial-value",
            "data-element-id",
            "data-element-ids",
            "data-json-data",
        )
    ):
        return value
    parser = _StructuredHtmlRewriter(replacements)
    parser.feed(value)
    parser.close()
    return parser.output()


class _StructuredHtmlRewriter(HTMLParser):
    def __init__(self, replacements: ProjectionReplacements) -> None:
        super().__init__()
        self._replacements = replacements
        self._output: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._output.append(f"<{tag}{self._attributes(tag, attrs)}>")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._output.append(f"<{tag}{self._attributes(tag, attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        self._output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._output.append(data)

    def handle_comment(self, data: str) -> None:
        self._output.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self._output.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self._output.append(f"<?{data}>")

    def output(self) -> str:
        return "".join(self._output)

    def _attributes(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> str:
        values: list[str] = []
        for name, value in attrs:
            if value is None:
                values.append(name)
                continue
            rewritten = self._attribute(tag.lower(), name.lower(), value)
            escaped = rewritten.replace("&", "&amp;").replace('"', "&quot;")
            values.append(f'{name}="{escaped}"')
        return f" {' '.join(values)}" if values else ""

    def _attribute(self, tag: str, name: str, value: str) -> str:
        if tag == "marimo-ui-element" and name in {"object-id", "random-id"}:
            return rewrite_identifier(value, self._replacements.identifiers)
        if tag == "marimo-anywidget" and name in {"data-model-id", "model-id"}:
            return _rewrite_model_attribute(value, self._replacements.models)
        if tag == "marimo-anywidget" and name == "data-initial-value":
            return _rewrite_json_attribute(value, self._replacements)
        for tags, attributes, mode in _COMPONENT_JSON_ATTRIBUTES:
            if tag in tags and name in attributes:
                return _rewrite_component_json_attribute(
                    value,
                    self._replacements,
                    mode,
                )
        return value


def rewrite_identifier(value: str, replacements: Mapping[str, str]) -> str:
    if value in replacements:
        return replacements[value]
    try:
        canonical = canonical_cell_id(value)
    except ValueError:
        canonical = value
    if canonical in replacements:
        return replacements[canonical]
    rewritten = value
    for source, target in replacements.items():
        rewritten = rewritten.replace(source, target)
    return rewritten


def _rewrite_component_json_attribute(
    value: str,
    replacements: ProjectionReplacements,
    mode: str,
) -> str:
    try:
        decoded = msgspec.json.decode(value)
    except msgspec.DecodeError:
        return value
    if mode == "identifier" and isinstance(decoded, str):
        rewritten: object = rewrite_identifier(decoded, replacements.ui)
    elif mode == "identifier-keys" and isinstance(decoded, dict):
        rewritten = {
            rewrite_identifier(str(key), replacements.ui): item for key, item in decoded.items()
        }
    elif mode == "nested-html":
        rewritten = _rewrite_nested_html_values(decoded, replacements)
    else:
        return value
    return canonical_bytes(json_value(rewritten, "Marimo component attribute")).decode("utf-8")


def _rewrite_nested_html_values(
    value: object,
    replacements: ProjectionReplacements,
) -> object:
    if isinstance(value, str):
        prefix = "text/html:"
        if value.startswith(prefix):
            return prefix + _rewrite_html_identifiers(value[len(prefix) :], replacements)
        return value
    if isinstance(value, list):
        return [_rewrite_nested_html_values(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _rewrite_nested_html_values(
                item,
                replacements,
            )
            for key, item in value.items()
        }
    return value


def canonical_object_id(value: str, cell_ids: Mapping[str, Any]) -> str:
    return rewrite_identifier(
        value,
        {str(runtime_id): source_id for source_id, runtime_id in cell_ids.items()},
    )


def scoped_ui_id(
    scope_key: str,
    projection_identity: str,
    owner_cell_id: str,
) -> str:
    return f"{owner_cell_id}-projection-{projection_identity}-ui-{scope_key}"


def _rewrite_model_attribute(value: str, replacements: Mapping[str, str]) -> str:
    model_id = _model_attribute_id(value)
    if model_id is None:
        return value
    rewritten = replacements.get(model_id, model_id)
    if value == model_id:
        return rewritten
    return canonical_bytes(rewritten).decode("utf-8")


def _rewrite_json_attribute(value: str, replacements: ProjectionReplacements) -> str:
    try:
        decoded = msgspec.json.decode(value)
    except msgspec.DecodeError:
        return value
    rewritten = rewrite_ui_value(decoded, replacements.models, replacements.ui)
    return canonical_bytes(json_value(rewritten, "Marimo UI attribute")).decode("utf-8")


def _rewrite_widget_view(
    value: Mapping[object, object],
    replacements: Mapping[str, str],
) -> dict[str, object]:
    result = {str(key): item for key, item in value.items()}
    model_id = result.get("model_id")
    if isinstance(model_id, str) and model_id in replacements:
        result["model_id"] = replacements[model_id]
    return result


def rewrite_ui_value(
    value: object,
    model_replacements: Mapping[str, str],
    ui_replacements: Mapping[str, str],
) -> object:
    if isinstance(value, list):
        return [rewrite_ui_value(item, model_replacements, ui_replacements) for item in value]
    if isinstance(value, dict):
        rewritten = {
            str(key): rewrite_ui_value(item, model_replacements, ui_replacements)
            for key, item in value.items()
        }
        model_id = rewritten.get("model_id")
        if isinstance(model_id, str) and model_id in model_replacements:
            rewritten["model_id"] = model_replacements[model_id]
        for field in _UI_REFERENCE_FIELDS:
            object_id = rewritten.get(field)
            if isinstance(object_id, str) and object_id in ui_replacements:
                rewritten[field] = ui_replacements[object_id]
        return rewritten
    return value


def _rewrite_mimebundle(
    value: Mapping[str, object],
    replacements: ProjectionReplacements,
) -> dict[str, object]:
    result = dict(value)
    for entries in mimebundle_entries(result):
        html = entries.get("text/html")
        if isinstance(html, str):
            entries["text/html"] = _rewrite_html_identifiers(html, replacements)
        widget = entries.get(_WIDGET_VIEW_MIMETYPE)
        if isinstance(widget, dict):
            entries[_WIDGET_VIEW_MIMETYPE] = _rewrite_widget_view(
                cast(Mapping[object, object], widget),
                replacements.models,
            )
    return result


def output_references(
    mimetype: str,
    value: object,
    available_models: frozenset[str],
) -> tuple[set[str], set[str], set[str], dict[str, str]]:
    models: set[str] = set()
    object_ids: set[str] = set()
    random_ids: set[str] = set()
    ui_random_ids: dict[str, str] = {}
    if mimetype in _HTML_MIMETYPES and isinstance(value, str):
        _collect_html_references(
            value,
            available_models,
            models,
            object_ids,
            random_ids,
        )
        _merge_ui_random_ids(ui_random_ids, _ui_random_id_pairs(value))
    elif mimetype == _WIDGET_VIEW_MIMETYPE and isinstance(value, dict):
        _collect_widget_view_reference(
            cast(Mapping[object, object], value),
            available_models,
            models,
        )
    elif mimetype == MIMEBUNDLE_MIMETYPE:
        bundle = decode_mimebundle(value)
        if bundle is not None:
            for entries in mimebundle_entries(bundle):
                html = entries.get("text/html")
                if isinstance(html, str):
                    _collect_html_references(
                        html,
                        available_models,
                        models,
                        object_ids,
                        random_ids,
                    )
                    _merge_ui_random_ids(ui_random_ids, _ui_random_id_pairs(html))
                widget = entries.get(_WIDGET_VIEW_MIMETYPE)
                if isinstance(widget, dict):
                    _collect_widget_view_reference(
                        cast(Mapping[object, object], widget),
                        available_models,
                        models,
                    )
    return models, object_ids, random_ids, ui_random_ids


class _UiReferenceCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.pairs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "marimo-ui-element":
            return
        values = {name.lower(): value for name, value in attrs}
        object_id = values.get("object-id")
        random_id = values.get("random-id")
        if object_id is not None and random_id is not None:
            _merge_ui_random_ids(self.pairs, {object_id: random_id})


def _ui_random_id_pairs(value: str) -> dict[str, str]:
    parser = _UiReferenceCollector()
    parser.feed(value)
    parser.close()
    return parser.pairs


def _merge_ui_random_ids(target: dict[str, str], incoming: Mapping[str, str]) -> None:
    for object_id, random_id in incoming.items():
        previous = target.setdefault(object_id, random_id)
        if previous != random_id:
            raise ValueError(f"UI object {object_id!r} has conflicting random IDs")


def _collect_html_references(
    value: str,
    available_models: frozenset[str],
    models: set[str],
    object_ids: set[str],
    random_ids: set[str],
) -> None:
    from marimo._convert.common.dom_traversal import replace_html_attributes

    def collect_object(identifier: str) -> None:
        object_ids.add(identifier)
        if identifier in available_models:
            models.add(identifier)
        return None

    def collect_model(identifier: str) -> None:
        model_id = _model_attribute_id(identifier)
        if model_id in available_models:
            models.add(model_id)
        return None

    def collect_random(identifier: str) -> None:
        random_ids.add(identifier)
        return None

    replace_html_attributes(
        html=value,
        allowed_tags={"marimo-ui-element"},
        allowed_attributes={"object-id"},
        replacer_fn=collect_object,
    )
    replace_html_attributes(
        html=value,
        allowed_tags={"marimo-ui-element"},
        allowed_attributes={"random-id"},
        replacer_fn=collect_random,
    )
    replace_html_attributes(
        html=value,
        allowed_tags={"marimo-anywidget"},
        allowed_attributes={"data-model-id", "model-id"},
        replacer_fn=collect_model,
    )
    replace_html_attributes(
        html=value,
        allowed_tags={"marimo-json-output"},
        allowed_attributes={"data-json-data"},
        replacer_fn=lambda attribute: _collect_component_json_references(
            attribute,
            available_models,
            models,
            random_ids,
        ),
    )


def _collect_component_json_references(
    value: str,
    available_models: frozenset[str],
    models: set[str],
    random_ids: set[str],
) -> None:
    try:
        decoded = msgspec.json.decode(value)
    except msgspec.DecodeError:
        return None
    _collect_component_value_references(
        decoded,
        available_models,
        models,
        random_ids,
    )
    return None


def _collect_component_value_references(
    value: object,
    available_models: frozenset[str],
    models: set[str],
    random_ids: set[str],
) -> None:
    if isinstance(value, str):
        prefix = "text/html:"
        if value.startswith(prefix):
            _collect_nested_html_references(
                value[len(prefix) :],
                available_models,
                models,
                random_ids,
            )
        return
    if isinstance(value, list):
        for item in value:
            _collect_component_value_references(item, available_models, models, random_ids)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_component_value_references(item, available_models, models, random_ids)


def _collect_nested_html_references(
    value: str,
    available_models: frozenset[str],
    models: set[str],
    random_ids: set[str],
) -> None:
    from marimo._convert.common.dom_traversal import replace_html_attributes

    replace_html_attributes(
        html=value,
        allowed_tags={"marimo-ui-element"},
        allowed_attributes={"random-id"},
        replacer_fn=lambda identifier: random_ids.add(identifier),
    )

    def collect_model(identifier: str) -> None:
        model_id = _model_attribute_id(identifier)
        if model_id in available_models:
            models.add(model_id)
        return None

    replace_html_attributes(
        html=value,
        allowed_tags={"marimo-anywidget"},
        allowed_attributes={"data-model-id", "model-id"},
        replacer_fn=collect_model,
    )
    replace_html_attributes(
        html=value,
        allowed_tags={"marimo-json-output"},
        allowed_attributes={"data-json-data"},
        replacer_fn=lambda attribute: _collect_component_json_references(
            attribute,
            available_models,
            models,
            random_ids,
        ),
    )


def _collect_widget_view_reference(
    value: Mapping[object, object],
    available_models: frozenset[str],
    models: set[str],
) -> None:
    model_id = value.get("model_id")
    if isinstance(model_id, str) and model_id in available_models:
        models.add(model_id)


def _model_attribute_id(value: str) -> str | None:
    if value:
        try:
            decoded = msgspec.json.decode(value)
        except msgspec.DecodeError:
            decoded = None
        if isinstance(decoded, str):
            return decoded
        return value
    return None


__all__ = [
    "ProjectionReplacements",
    "canonical_object_id",
    "cell_output_value",
    "output_references",
    "rewrite_ui_value",
    "scoped_ui_id",
]
