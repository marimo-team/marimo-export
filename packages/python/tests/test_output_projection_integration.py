from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from export_integration_support import NOTEBOOK_TIMEOUT, build
from marimo_export import ExportSpec, OutputSpec, open_export
from marimo_export._json import decode_json_object
from marimo_export.descriptors import UnavailableOutput
from marimo_export.errors import OutputError


def test_json_and_rendered_output_projections_use_safe_selectors(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    report = {"summary": {"count": 2}, "view": mo.md("## Ready")}
    slider = mo.ui.slider(0, 10)
    return report, slider


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "count": OutputSpec.json('report["summary"].count'),
            "slider": OutputSpec.output("slider"),
            "view": OutputSpec.output('report["view"]'),
        },
    )

    result = build(notebook, spec=spec, output=tmp_path / "export", timeout=NOTEBOOK_TIMEOUT)
    state = open_export(result.path).state("baseline")
    snapshot = cast(
        dict[str, Any],
        decode_json_object(state.output("view").asset_bytes(), "output snapshot"),
    )
    slider = cast(
        dict[str, Any],
        decode_json_object(state.output("slider").asset_bytes(), "slider snapshot"),
    )

    assert state.output("count").json() == 2
    assert snapshot["schema"] == "marimo.output.v1"
    assert snapshot["ownerCellId"]
    assert snapshot["output"] == {
        "channel": "output",
        "mimetype": "text/markdown",
        "data": (
            '<span class="markdown prose dark:prose-invert contents">'
            '<h2 id="ready">Ready</h2></span>'
        ),
    }
    functions = slider["resources"]["functions"]
    assert len(functions) == 1
    assert list(functions.values()) == [[]]
    object_id = next(iter(functions))
    assert object_id in slider["output"]["data"]
    assert slider["resources"]["uiValues"] == {object_id: 0}


def test_default_form_omits_inactive_validation_function(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    region = mo.ui.dropdown(
        options={"Europe": "emea", "Asia Pacific": "apac"},
        value="Europe",
    )
    form = mo.ui.dictionary({"region": region}).form()
    return (form,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={"form": OutputSpec.output("form")},
        ),
        output=tmp_path / "export",
        timeout=NOTEBOOK_TIMEOUT,
    )
    snapshot = cast(
        dict[str, Any],
        decode_json_object(
            open_export(result.path).state("baseline").output("form").asset_bytes(),
            "form snapshot",
        ),
    )

    resources = snapshot["resources"]
    assert len(resources["functions"]) == 3
    assert all(not names for names in resources["functions"].values())
    html = snapshot["output"]["data"]
    assert "data-element-id" in html
    assert "data-element-ids" in html
    assert "data-json-data" in html
    assert all(object_id in html for object_id in resources["uiValues"])

    nested_attributes: list[str] = []

    class AttributeParser(HTMLParser):
        def handle_starttag(
            self,
            tag: str,
            attrs: list[tuple[str, str | None]],
        ) -> None:
            if tag == "marimo-json-output":
                value = dict(attrs).get("data-json-data")
                if value is not None:
                    nested_attributes.append(value)

    AttributeParser().feed(html)
    assert len(nested_attributes) == 1
    nested_html = cast(dict[str, str], json.loads(nested_attributes[0]))["region"]
    assert nested_html.startswith("text/html:")
    assert "projection-" in nested_html
    assert (
        re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}[A-Za-z]{4}-\d+",
            html,
        )
        is None
    )
    assert (
        re.search(
            r"random-id=['\"][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['\"]",
            nested_html,
        )
        is None
    )


def test_dependent_ui_input_reconstructs_its_projected_control_tree(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    scale = mo.ui.slider(1, 3, value=2)
    return mo, scale


@app.cell
def filters_control(mo, scale):
    region = mo.ui.dropdown(
        options={"Europe": "emea", "Asia Pacific": "apac"},
        value="Europe",
        label="Region",
    )
    fields = {"region": region}
    if scale.value == 3:
        fields["detail"] = mo.ui.text(value="ready", label="Detail")
    filters = mo.ui.dictionary(fields).form()
    print(f"filters scale={scale.value}")
    filters
    return filters, region


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="scale-2",
        states={
            "scale-2": {"filters": None, "region": ["Europe"], "scale": 2},
            "scale-3": {"filters": None, "region": ["Europe"], "scale": 3},
        },
        outputs={
            "cell": OutputSpec.cell("filters_control"),
            "output": OutputSpec.output("filters"),
        },
    )

    result = build(notebook, spec=spec, output=tmp_path / "export", timeout=NOTEBOOK_TIMEOUT)
    export = open_export(result.path)
    resource_ids: dict[str, dict[str, set[str]]] = {"cell": {}, "output": {}}
    for state_name, scale in (("scale-2", 2), ("scale-3", 3)):
        state = export.state(state_name)
        for output_name in ("cell", "output"):
            snapshot = cast(
                dict[str, Any],
                decode_json_object(
                    state.output(output_name).asset_bytes(),
                    f"{output_name} snapshot",
                ),
            )
            resources = snapshot["resources"]
            ids = set(resources["uiValues"])
            assert ids == set(resources["functions"])
            resource_ids[output_name][state_name] = ids
            serialized = json.dumps(snapshot)
            assert ("Detail" in serialized) is (scale == 3)
            if output_name == "cell":
                assert snapshot["console"] == [
                    {
                        "channel": "stdout",
                        "data": f"filters scale={scale}\n",
                        "mimetype": "text/plain",
                    }
                ]

    for output_name in ("cell", "output"):
        scale_2 = resource_ids[output_name]["scale-2"]
        scale_3 = resource_ids[output_name]["scale-3"]
        assert scale_2 < scale_3
        for object_id in scale_3:
            binding = export.control_bindings[object_id]
            assert binding.input == "filters"


def test_form_with_live_validation_function_is_not_portable(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    form = mo.ui.text(value="ready").form(
        validate=lambda value: "invalid" if value == "blocked" else None,
    )
    return (form,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(OutputError) as raised:
        build(
            notebook,
            spec=ExportSpec(
                default_state="baseline",
                states={"baseline": {}},
                outputs={"form": OutputSpec.output("form")},
            ),
            output=tmp_path / "export",
            timeout=NOTEBOOK_TIMEOUT,
        )

    assert raised.value.code == "output_not_portable"
    details = raised.value.details
    assert {
        key: value for key, value in details.items() if key not in {"cell_id", "object_id"}
    } == {
        "functions": ["validate"],
        "output": "form",
        "reason": "python_functions",
        "selector": "form",
        "source_kind": "output",
        "state": "baseline",
    }
    assert isinstance(details["cell_id"], str) and details["cell_id"]
    assert isinstance(details["object_id"], str) and details["object_id"]


_TABLE_NOTEBOOK = """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    import pandas as pd
    import polars as pl
    return mo, pd, pl


@app.cell
def _(mo, pl):
    frame = pl.DataFrame(
        {
            "city": [f"City {index}" for index in range(25)],
            "population": [1000 * (index + 1) for index in range(25)],
        }
    )
    knob = mo.ui.slider(1, 10)
    return frame, knob


@app.cell
def _(pd):
    sales = pd.DataFrame({"region": ["North", "South"], "total": [10, 20]})
    return (sales,)


@app.cell
def _(frame, mo):
    table = mo.ui.table(frame, page_size=10)
    return (table,)


@app.cell
def frame_cell(frame):
    frame
    return


if __name__ == "__main__":
    app.run()
""".lstrip()


def test_read_only_outputs_record_unavailable_table_functions(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(_TABLE_NOTEBOOK, encoding="utf-8")

    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={
                "table": OutputSpec.output("table", read_only=True),
                "frame": OutputSpec.output("frame", read_only=True),
                "sales": OutputSpec.output("sales", read_only=True),
                "frame_cell": OutputSpec.cell("frame_cell", read_only=True),
            },
        ),
        output=tmp_path / "export",
        timeout=NOTEBOOK_TIMEOUT,
    )
    state = open_export(result.path).state("baseline")

    for name, first_row in (
        ("table", "City 0"),
        ("frame", "City 0"),
        ("sales", "North"),
        ("frame_cell", "City 0"),
    ):
        snapshot = cast(
            dict[str, Any],
            decode_json_object(state.output(name).asset_bytes(), f"{name} snapshot"),
        )
        output = html.unescape(snapshot["output"]["data"])
        assert "<marimo-table" in output
        assert first_row in output
        assert "City 24" not in output
        [functions] = snapshot["resources"]["functions"].values()
        assert {"get_column_summaries", "search"} <= set(functions)
        assert functions == sorted(functions)

    warnings = {warning.details["output"]: warning for warning in result.warnings}
    assert set(warnings) == {"table", "frame", "sales", "frame_cell"}
    for warning in warnings.values():
        assert warning.code == "output_read_only"
        assert "search" in cast(list[str], warning.details["functions"])


def test_an_optional_read_only_table_is_unavailable_where_the_notebook_omits_it(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    import polars as pl

    rows = 3
    return mo, pl, rows


@app.cell
def _(mo, pl, rows):
    frame = pl.DataFrame({"city": [f"City {index}" for index in range(rows)]})
    report = {"table": mo.ui.table(frame, page_size=10)} if rows else {}
    return (report,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    table = OutputSpec.output('report["table"]', read_only=True, optional=True)

    # The empty state's fingerprint sorts first, so the read-only warning scan
    # meets the unavailable output before the table snapshot.
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="cities",
            states={"cities": {}, "empty": {"rows": 0}},
            outputs={"table": table},
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    export = open_export(result.path)
    snapshot = cast(
        dict[str, Any],
        decode_json_object(export.state("cities").output("table").asset_bytes(), "snapshot"),
    )

    assert "City 2" in html.unescape(snapshot["output"]["data"])
    assert export.state("empty").output("table").unavailable == UnavailableOutput(
        reason="value_missing",
        message="selector 'report[\"table\"]' is unavailable: item 'table' is unavailable",
    )
    assert result.unavailable == {"table": 1}
    [warning] = result.warnings
    assert warning.code == "output_read_only"
    assert warning.details["output"] == "table"
    assert "search" in cast(list[str], warning.details["functions"])


@pytest.mark.parametrize("selector", ["table", "frame"])
def test_table_output_without_read_only_is_not_portable(tmp_path: Path, selector: str) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(_TABLE_NOTEBOOK, encoding="utf-8")

    with pytest.raises(OutputError) as raised:
        build(
            notebook,
            spec=ExportSpec(
                default_state="baseline",
                states={"baseline": {}},
                outputs={selector: OutputSpec.output(selector)},
            ),
            output=tmp_path / "export",
            timeout=NOTEBOOK_TIMEOUT,
        )

    assert raised.value.code == "output_not_portable"
    assert raised.value.details["reason"] == "python_functions"
    assert "search" in cast(list[str], raised.value.details["functions"])


@pytest.mark.parametrize(
    ("control", "function"),
    [
        ("mo.ui.file_browser()", "list_directory"),
        ('mo.ui.table.lazy(pl.DataFrame({"city": ["Oslo"]}).lazy())', "search"),
    ],
)
def test_read_only_output_rejects_controls_that_render_through_python(
    tmp_path: Path,
    control: str,
    function: str,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    import polars as pl
    control = {control}
    return (control,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(OutputError) as raised:
        build(
            notebook,
            spec=ExportSpec(
                default_state="baseline",
                states={"baseline": {}},
                outputs={"control": OutputSpec.output("control", read_only=True)},
            ),
            output=tmp_path / "export",
            timeout=NOTEBOOK_TIMEOUT,
        )

    assert raised.value.code == "output_not_portable"
    assert function in cast(list[str], raised.value.details["functions"])


def test_rendered_plain_text_preserves_a_literal_cell_id(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    from marimo._runtime.context import get_context
    literal_cell_id = str(get_context().cell_id)
    return (literal_cell_id,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={
                "literal": OutputSpec.json("literal_cell_id"),
                "rendered": OutputSpec.output("literal_cell_id"),
            },
        ),
        output=tmp_path / "export",
        timeout=NOTEBOOK_TIMEOUT,
    )
    state = open_export(result.path).state("baseline")
    snapshot = cast(
        dict[str, Any],
        decode_json_object(state.output("rendered").asset_bytes(), "output snapshot"),
    )

    literal = state.output("literal").json()
    assert isinstance(literal, str)
    assert literal in snapshot["output"]["data"]
    assert literal == snapshot["ownerCellId"]
    assert snapshot["resources"] == {
        "files": {},
        "functions": {},
        "modelNotifications": [],
        "uiValues": {},
    }


def test_rendered_output_closes_only_its_reachable_widget_models(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import anywidget
    import marimo as mo
    import traitlets

    class First(anywidget.AnyWidget):
        _esm = "export default { render() {} }"
        value = traitlets.Int(1).tag(sync=True)

    class Second(anywidget.AnyWidget):
        _esm = "export default { render() {} }"
        value = traitlets.Int(2).tag(sync=True)

    first = mo.ui.anywidget(First())
    second = mo.ui.anywidget(Second())
    plain = first.widget._model_id
    return first, plain, second


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={
                "first": OutputSpec.output("first"),
                "plain_value": OutputSpec.json("plain"),
                "plain": OutputSpec.output("plain"),
                "second": OutputSpec.output("second"),
            },
        ),
        output=tmp_path / "export",
        timeout=NOTEBOOK_TIMEOUT,
    )
    state = open_export(result.path).state("baseline")

    first = cast(
        dict[str, Any],
        decode_json_object(state.output("first").asset_bytes(), "first snapshot"),
    )
    plain = cast(
        dict[str, Any],
        decode_json_object(state.output("plain").asset_bytes(), "plain snapshot"),
    )
    second = cast(
        dict[str, Any],
        decode_json_object(state.output("second").asset_bytes(), "second snapshot"),
    )

    assert len(first["resources"]["modelNotifications"]) == 2
    assert len(second["resources"]["modelNotifications"]) == 2
    assert first["resources"]["modelNotifications"][0]["message"]["state"]["value"] == 1
    assert second["resources"]["modelNotifications"][0]["message"]["state"]["value"] == 2
    first_models = {
        notification["model_id"] for notification in first["resources"]["modelNotifications"]
    }
    second_models = {
        notification["model_id"] for notification in second["resources"]["modelNotifications"]
    }
    assert first_models.isdisjoint(second_models)
    assert all(model_id.startswith("projection-") for model_id in first_models | second_models)
    assert first["resources"]["modelNotifications"][0]["model_id"] in first["output"]["data"]
    assert second["resources"]["modelNotifications"][0]["model_id"] in second["output"]["data"]
    for snapshot in (first, second):
        esm_url = snapshot["resources"]["modelNotifications"][0]["message"]["esm_spec"]["url"]
        assert esm_url.startswith("./@file/")
        assert esm_url.removeprefix(".") in snapshot["resources"]["files"]
        assert esm_url not in snapshot["resources"]["files"]
    assert set(first["resources"]["uiValues"]) == set(first["resources"]["functions"])
    assert set(second["resources"]["uiValues"]) == set(second["resources"]["functions"])
    assert "model-0" in repr(first["resources"]["uiValues"])
    assert "model-0" in repr(second["resources"]["uiValues"])
    plain_value = state.output("plain_value").json()
    assert isinstance(plain_value, str)
    assert plain_value in plain["output"]["data"]
    assert "model-0" not in plain["output"]["data"]
    assert plain["resources"] == {
        "files": {},
        "functions": {},
        "modelNotifications": [],
        "uiValues": {},
    }


def test_rendered_output_closes_public_media_and_preserves_literal_file_text(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    public = tmp_path / "public"
    public.mkdir()
    public.joinpath("pixel.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
        encoding="utf-8",
    )
    public.joinpath("tone.mp3").write_bytes(b"ID3")
    public.joinpath("poster.png").write_bytes(b"poster")
    public.joinpath("report.txt").write_text("report", encoding="utf-8")
    public.joinpath("frame.html").write_text("<p>frame</p>", encoding="utf-8")
    public.joinpath("app.js").write_text("globalThis.loaded = true", encoding="utf-8")
    public.joinpath("app.css").write_text("body { color: black }", encoding="utf-8")
    public.joinpath("captions.vtt").write_text("WEBVTT", encoding="utf-8")
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import html
    import json
    import marimo as mo

    class Bundle:
        def _repr_mimebundle_(self):
            return {"text/html": '<video><source src="public/tone.mp3"></video>'}

    bundle = Bundle()
    nested = html.escape(
        json.dumps({"image": "text/html:<img src='public/pixel.svg'>"}),
        quote=True,
    )
    srcdoc = html.escape("<img src='public/pixel.svg'>", quote=True)
    view = mo.Html(
        '<img src="public/pixel.svg">'
        '<img srcset="public/pixel.svg 1x, ./public/pixel.svg 2x">'
        '<video poster="public/poster.png">'
        '<track src="public/captions.vtt">'
        '</video>'
        '<audio><source src="public/tone.mp3"></audio>'
        '<object data="public/report.txt"></object>'
        '<embed src="public/report.txt">'
        '<iframe src="public/frame.html"></iframe>'
        f'<iframe srcdoc="{srcdoc}"></iframe>'
        '<script src="public/app.js"></script>'
        '<link rel="stylesheet" href="public/app.css">'
        '<a href="public/report.txt" download>Download</a>'
        '<div style="background-image: url(public/pixel.svg)"></div>'
        '<style>.hero { background: url("public/pixel.svg") }</style>'
        f'<marimo-json-output data-json-data="{nested}"></marimo-json-output>'
        '<code>./@file/999-literal.txt</code>'
    )
    return bundle, view


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={
                "bundle": OutputSpec.output("bundle"),
                "view": OutputSpec.output("view"),
            },
        ),
        output=tmp_path / "export",
        timeout=NOTEBOOK_TIMEOUT,
    )

    snapshot = cast(
        dict[str, Any],
        decode_json_object(
            open_export(result.path).state("baseline").output("view").asset_bytes(),
            "output snapshot",
        ),
    )
    data = snapshot["output"]["data"]
    assert 'src="data:image/svg+xml;base64,' in data
    assert 'src="data:audio/mpeg;base64,' in data
    assert 'srcset="data:image/svg+xml;base64,' in data
    assert 'poster="data:image/png;base64,' in data
    assert 'data="data:text/plain;base64,' in data
    assert 'href="data:text/plain;base64,' in data
    assert 'src="data:text/javascript;base64,' in data
    assert 'href="data:text/css;base64,' in data
    assert 'src="data:text/html;base64,' in data
    assert 'srcdoc="&lt;img src=&quot;data:image/svg+xml;base64,' in data
    assert '<track src="data:' in data
    assert "url(&quot;data:image/svg+xml;base64," in data
    assert 'url("data:image/svg+xml;base64,' in data
    assert "./@file/999-literal.txt" in data

    nested_attributes: list[str] = []

    class AttributeParser(HTMLParser):
        def handle_starttag(
            self,
            tag: str,
            attrs: list[tuple[str, str | None]],
        ) -> None:
            if tag == "marimo-json-output":
                value = dict(attrs).get("data-json-data")
                if value is not None:
                    nested_attributes.append(value)

    AttributeParser().feed(data)
    assert len(nested_attributes) == 1
    nested_html = cast(dict[str, str], json.loads(nested_attributes[0]))["image"]
    assert nested_html.startswith('text/html:<img src="data:image/svg+xml;base64,')
    bundle = cast(
        dict[str, Any],
        decode_json_object(
            open_export(result.path).state("baseline").output("bundle").asset_bytes(),
            "bundle snapshot",
        ),
    )
    assert bundle["output"]["data"]["text/html"].startswith(
        '<video><source src="data:audio/mpeg;base64,'
    )


def test_rendered_output_closes_virtual_anchor_before_child_teardown(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    from marimo._runtime.virtual_file import VirtualFile

    download = VirtualFile.create_and_register(b"download payload", "txt")
    view = mo.Html(f'<a href="{download.url}" download>Download</a>')
    return (view,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={"view": OutputSpec.output("view")},
        ),
        output=tmp_path / "export",
        timeout=NOTEBOOK_TIMEOUT,
    )
    snapshot = cast(
        dict[str, Any],
        decode_json_object(
            open_export(result.path).state("baseline").output("view").asset_bytes(),
            "anchor snapshot",
        ),
    )
    data = snapshot["output"]["data"]
    assert '<a href="data:text/plain;base64,' in data
    encoded = data.split("base64,", 1)[1].split('"', 1)[0]
    import base64

    assert base64.b64decode(encoded) == b"download payload"


def test_rendered_output_closes_virtual_file_plugin_arguments(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    editor = mo.ui.data_editor({"name": ["Ada", "Grace"], "value": [1, 2]})
    return (editor,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={"editor": OutputSpec.output("editor")},
        ),
        output=tmp_path / "export",
        timeout=NOTEBOOK_TIMEOUT,
    )
    snapshot = cast(
        dict[str, Any],
        decode_json_object(
            open_export(result.path).state("baseline").output("editor").asset_bytes(),
            "editor snapshot",
        ),
    )

    class EditorData(HTMLParser):
        value: str | None = None

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "marimo-data-editor":
                self.value = dict(attrs).get("data-data")

    parser = EditorData()
    parser.feed(snapshot["output"]["data"])
    data_url = json.loads(cast(str, parser.value))
    assert data_url.startswith("data:text/csv;base64,")
    import base64

    rows = base64.b64decode(data_url.removeprefix("data:text/csv;base64,"))
    assert rows.decode("utf-8").splitlines() == ["name,value", "Ada,1", "Grace,2"]


def test_plugin_argument_that_embeds_a_local_file_fails_closed(tmp_path: Path) -> None:
    from marimo_export._marimo.compat.file_closure import close_output_files

    context = SimpleNamespace(
        filename=str(tmp_path / "notebook.py"),
        parent=None,
        virtual_file_registry=SimpleNamespace(has=lambda _filename: True),
    )
    recording = SimpleNamespace(child=SimpleNamespace(_runtime_context=context))
    label = json.dumps('<img src="./@file/7-chip.png">')
    html_source = f'<marimo-button data-label="{html.escape(label)}"></marimo-button>'

    with pytest.raises(OutputError, match="unclosed local file"):
        close_output_files(cast(Any, recording), html_source, "text/html")


@pytest.mark.parametrize(
    "argument",
    (
        '"public/payload.bin"',
        '"public\\/payload.bin"',
    ),
)
def test_plugin_argument_closes_public_file_as_a_data_url(
    tmp_path: Path,
    argument: str,
) -> None:
    import base64

    from marimo_export._marimo.compat.file_closure import close_output_files

    public = tmp_path / "public"
    public.mkdir()
    # The Base64 payload of these bytes begins with "public/".
    payload = base64.b64decode("public/abcde")
    public.joinpath("payload.bin").write_bytes(payload)
    context = SimpleNamespace(
        filename=str(tmp_path / "notebook.py"),
        parent=None,
        virtual_file_registry=SimpleNamespace(has=lambda _filename: False),
    )
    recording = SimpleNamespace(child=SimpleNamespace(_runtime_context=context))
    html_source = f'<marimo-data-editor data-data="{html.escape(argument)}"></marimo-data-editor>'

    closed = close_output_files(cast(Any, recording), html_source, "text/html")

    class EditorData(HTMLParser):
        value: str | None = None

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "marimo-data-editor":
                self.value = dict(attrs).get("data-data")

    parser = EditorData()
    parser.feed(cast(str, closed))
    assert json.loads(cast(str, parser.value)) == (
        "data:application/octet-stream;base64,public/abcde"
    )


def test_rendered_output_rejects_oversized_virtual_media(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    view = mo.Html('<img src="./@file/10485761-oversized.png">')
    return (view,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(OutputError, match="inline limit"):
        build(
            notebook,
            spec=ExportSpec(
                default_state="baseline",
                states={"baseline": {}},
                outputs={"view": OutputSpec.output("view")},
            ),
            output=tmp_path / "export",
            timeout=NOTEBOOK_TIMEOUT,
        )


def test_rendered_output_rejects_local_file_in_an_unknown_attribute(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    public = tmp_path / "public"
    public.mkdir()
    public.joinpath("payload.txt").write_text("payload", encoding="utf-8")
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    view = mo.Html('<custom-element data-resource="public/payload.txt"></custom-element>')
    return (view,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(OutputError, match="unclosed local file"):
        build(
            notebook,
            spec=ExportSpec(
                default_state="baseline",
                states={"baseline": {}},
                outputs={"view": OutputSpec.output("view")},
            ),
            output=tmp_path / "export",
            timeout=NOTEBOOK_TIMEOUT,
        )


def test_public_file_mutation_during_inline_read_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marimo_export._marimo.compat.file_closure import close_output_files

    notebook = tmp_path / "notebook.py"
    notebook.write_text("", encoding="utf-8")
    public = tmp_path / "public"
    public.mkdir()
    asset = public / "payload.txt"
    asset.write_bytes(b"before")
    original_open = Path.open

    class MutatingStream:
        def __init__(self) -> None:
            self._stream: Any = None

        def __enter__(self) -> MutatingStream:
            self._stream = original_open(asset, "rb")
            return self

        def __exit__(self, *exc_info: object) -> None:
            self._stream.close()

        def fileno(self) -> int:
            return cast(int, self._stream.fileno())

        def read(self, size: int) -> bytes:
            data = cast(bytes, self._stream.read(size))
            asset.write_bytes(b"after mutation")
            return data

    def mutating_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        if path == asset and mode == "rb":
            return MutatingStream()
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", mutating_open)
    context = SimpleNamespace(
        filename=str(notebook),
        parent=None,
        virtual_file_registry=SimpleNamespace(has=lambda _filename: False),
    )
    recording = SimpleNamespace(child=SimpleNamespace(_runtime_context=context))

    with pytest.raises(OutputError, match="changed while it was closed"):
        close_output_files(
            cast(Any, recording),
            '<a href="public/payload.txt">payload</a>',
            "text/html",
        )


def test_windows_path_and_handle_ctime_disagreement_is_not_content_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marimo_export._marimo.compat.file_closure as file_closure

    path_details = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_size=3,
        st_mtime_ns=4,
        st_ctime_ns=5,
    )
    handle_details = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_size=3,
        st_mtime_ns=4,
        st_ctime_ns=4,
    )
    monkeypatch.setattr(file_closure.os, "name", "nt")

    assert file_closure._file_revision(cast(Any, path_details)) == (
        1,
        2,
        3,
        4,
    )
    assert file_closure._file_revision(cast(Any, path_details)) == (
        file_closure._file_revision(cast(Any, handle_details))
    )


@pytest.mark.parametrize(
    "html_source",
    (
        '<script>fetch("public/payload.txt")</script>',
        "<a href=\"javascript:fetch('public/payload.txt')\">payload</a>",
        "<iframe src=\"data:text/html,&lt;img src='public/payload.txt'&gt;\"></iframe>",
    ),
)
def test_embedded_local_file_reference_in_active_html_fails_closed(
    tmp_path: Path,
    html_source: str,
) -> None:
    from marimo_export._marimo.compat.file_closure import close_output_files

    context = SimpleNamespace(
        filename=str(tmp_path / "notebook.py"),
        parent=None,
        virtual_file_registry=SimpleNamespace(has=lambda _filename: False),
    )
    recording = SimpleNamespace(child=SimpleNamespace(_runtime_context=context))

    with pytest.raises(OutputError, match="unclosed local file"):
        close_output_files(cast(Any, recording), html_source, "text/html")


def test_external_url_and_code_literal_remain_unchanged(tmp_path: Path) -> None:
    from marimo_export._marimo.compat.file_closure import close_output_files

    context = SimpleNamespace(
        filename=str(tmp_path / "notebook.py"),
        parent=None,
        virtual_file_registry=SimpleNamespace(has=lambda _filename: False),
    )
    recording = SimpleNamespace(child=SimpleNamespace(_runtime_context=context))
    html_source = (
        '<a href="https://example.com/report.txt">external</a>'
        "<code>public/payload.txt ./@file/7-literal.txt</code>"
    )

    assert close_output_files(cast(Any, recording), html_source, "text/html") == html_source


@pytest.mark.parametrize(
    "html_source",
    (
        '<img src="public/../secret.txt">',
        '<img src="./public/sub/../../secret.txt">',
        '<img src="public/escape.txt">',
    ),
)
def test_public_file_outside_the_public_directory_fails_closed(
    tmp_path: Path,
    html_source: str,
) -> None:
    from marimo_export._marimo.compat.file_closure import close_output_files

    public = tmp_path / "public"
    (public / "sub").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    try:
        (public / "escape.txt").symlink_to(secret)
    except (OSError, NotImplementedError):
        if "escape" in html_source:
            pytest.skip("symlinks are unavailable on this platform")
    context = SimpleNamespace(
        filename=str(tmp_path / "notebook.py"),
        parent=None,
        virtual_file_registry=SimpleNamespace(has=lambda _filename: False),
    )
    recording = SimpleNamespace(child=SimpleNamespace(_runtime_context=context))

    with pytest.raises(OutputError, match="could not be closed"):
        close_output_files(cast(Any, recording), html_source, "text/html")


def test_empty_virtual_file_closes_without_a_registry_read(tmp_path: Path) -> None:
    from marimo_export._marimo.compat.file_closure import close_output_files

    context = SimpleNamespace(
        filename=str(tmp_path / "notebook.py"),
        parent=None,
        virtual_file_registry=SimpleNamespace(has=lambda _filename: False),
    )
    recording = SimpleNamespace(child=SimpleNamespace(_runtime_context=context))
    html_source = '<img src="@file/0-empty.txt">'

    assert close_output_files(cast(Any, recording), html_source, "text/html") == (
        '<img src="data:text/plain;base64,">'
    )
