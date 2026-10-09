from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from export_integration_support import build
from marimo_export import ExportSpec, OutputSpec, open_export
from marimo_export.errors import OutputError
from marimo_export.exporters import media
from marimo_export.values import Size


def test_media_outputs_render_the_accepted_format_with_notebook_defaults_unchanged(
    tmp_path: Path,
) -> None:
    pytest.importorskip("matplotlib")
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    width = 3
    return (width,)


@app.cell
def _(width):
    from matplotlib.figure import Figure

    figure = Figure(figsize=(width, 2))
    figure.subplots().plot([1, 3, 2])
    figure
    return (figure,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="narrow",
        states={"narrow": {}, "wide": {"width": 6}},
        outputs={
            "document_figure": OutputSpec.export(
                "figure", media(["application/pdf", "image/svg+xml"])
            ),
            "page_figure": OutputSpec.export("figure", media(["image/svg+xml", "image/png"])),
            "thumbnail": OutputSpec.export("figure", media(["image/png"], scale=2)),
            "notebook_figure": OutputSpec.output("figure"),
        },
    )

    result = build(notebook, spec=spec, output=tmp_path / "export", timeout=60)
    export = open_export(result.path)

    for name in ("narrow", "wide"):
        state = export.state(name)
        document = state.output("document_figure").blob_asset()
        page = state.output("page_figure").blob_asset()
        notebook_output = json.dumps(
            json.loads(state.output("notebook_figure").asset_bytes())["output"]
        )
        assert document.media_type == "application/pdf"
        assert document.data.startswith(b"%PDF-")
        assert page.media_type == "image/svg+xml"
        assert b"<svg" in page.data
        thumbnail = state.output("thumbnail").blob_asset()
        pixels = int.from_bytes(thumbnail.data[16:20], "big")
        assert pixels / 2 == pytest.approx(thumbnail.metadata["width"], abs=1)
        # marimo keeps rendering the notebook's own output as a PNG.
        assert "image/png" in notebook_output and "image/svg+xml" not in notebook_output
    assert (
        export.state("narrow").output("page_figure").blob_asset().data
        != export.state("wide").output("page_figure").blob_asset().data
    )


def test_media_outputs_draw_figures_at_their_size(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    from matplotlib.figure import Figure

    figure = Figure(figsize=(6, 2), layout="constrained")
    figure.subplots().plot([1, 3, 2])
    return (figure,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="base",
        states={"base": {}},
        outputs={
            "figure": OutputSpec.export("figure", media(["application/pdf"], size=Size(250.38)))
        },
    )

    result = build(notebook, spec=spec, output=tmp_path / "export", timeout=60)
    state = open_export(result.path).state("base")

    assert b"/MediaBox [ 0 0 250.38 83.46 ]" in state.output("figure").blob_asset().data


def test_json_outputs_store_tables_dates_and_steps_from_none(tmp_path: Path) -> None:
    pytest.importorskip("polars")
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import datetime

    import polars as pl

    days = pl.DataFrame(
        {"day": [datetime.date(2015, 2, 4), datetime.date(2015, 2, 5)], "rate": [0.5, None]}
    )
    peak = None
    return days, peak


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="base",
        states={"base": {}},
        outputs={
            "days": OutputSpec.json("days"),
            "peak_label": OutputSpec.json("peak.label"),
        },
    )

    result = build(notebook, spec=spec, output=tmp_path / "export", timeout=60)
    state = open_export(result.path).state("base")

    assert state.output("days").json() == (
        {"day": "2015-02-04", "rate": 0.5},
        {"day": "2015-02-05", "rate": None},
    )
    assert state.output("peak_label").json() is None


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (
            OutputSpec.export("figure", media(["text/csv"])),
            "matplotlib.figure.Figure has no representation as text/csv",
        ),
        (
            OutputSpec.output("figure.missing"),
            "selector 'figure.missing' is unavailable: attribute 'missing' is unavailable",
        ),
    ],
)
def test_an_unavailable_output_names_the_output_and_the_reason(
    tmp_path: Path, output: OutputSpec, message: str
) -> None:
    pytest.importorskip("matplotlib")
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    from matplotlib.figure import Figure

    figure = Figure()
    return (figure,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(default_state="base", states={"base": {}}, outputs={"chart": output})

    with pytest.raises(OutputError, match=re.escape(message)) as raised:
        build(notebook, spec=spec, output=tmp_path / "export", timeout=60)

    assert raised.value.code == "output_execution_failed"
    assert raised.value.details["output"] == "chart"
    assert raised.value.details["state"] == "base"
