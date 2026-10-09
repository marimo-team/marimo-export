from __future__ import annotations

import base64
import datetime
import decimal
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from marimo_export import values
from marimo_export.values import (
    Representation,
    RepresentationError,
    RepresentationTooLarge,
    SelectorError,
    SelectorStep,
    Size,
    ValueSelector,
    normalize_accept,
    represent,
)

_PNG = b"\x89PNG\r\n\x1a\n"


def test_selector_parses_attribute_index_and_key_steps() -> None:
    selector = ValueSelector('report.rows[0]["total value"]')

    assert selector.root == "report"
    assert selector.path == (
        SelectorStep("attribute", "rows"),
        SelectorStep("item", 0),
        SelectorStep("item", "total value"),
    )


@pytest.mark.parametrize(
    "source",
    [
        "",
        " report",
        "1report",
        "report()",
        "report['rows']",
        "report._cache",
        "report.__class__",
        "report[01]",
        "report[-1]",
        "report[9007199254740992]",
        'report["\\ud800"]',
        "report.rows[0",
        "x" * 4_097,
        "x" + ".y" * 65,
    ],
)
def test_selector_rejects_text_outside_the_grammar(source: str) -> None:
    with pytest.raises(SelectorError, match="invalid value selector"):
        ValueSelector(source)


@pytest.mark.parametrize("source", ["données", "é", "report.données"])
def test_selectors_name_ascii_identifiers(source: str) -> None:
    with pytest.raises(SelectorError, match="ASCII identifier"):
        ValueSelector(source)


def test_selector_resolves_mapping_keys_before_attributes() -> None:
    class Report:
        rows = ({"total": 3},)

    namespace = {"report": Report(), "config": {"items": "key wins"}}

    assert ValueSelector('report.rows[0]["total"]').resolve(namespace) == 3
    assert ValueSelector("config.items").resolve(namespace) == "key wins"


def test_selector_steps_from_none_select_none() -> None:
    namespace = {"peak": None, "report": {"peak": None}}

    assert ValueSelector("peak.label").resolve(namespace) is None
    assert ValueSelector('report.peak["label"][0]').resolve(namespace) is None


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("missing", "'missing' is not defined"),
        ("report.title", "attribute 'title' is unavailable"),
        ("report.rows[4]", "item 4 is unavailable"),
    ],
)
def test_selector_resolution_reports_the_unavailable_step(source: str, message: str) -> None:
    with pytest.raises(LookupError, match=message):
        ValueSelector(source).resolve({"report": {"rows": []}})


def test_accept_lists_are_lowercased_media_types_in_preference_order() -> None:
    assert normalize_accept(["Image/SVG+xml", "image/png"]) == ("image/svg+xml", "image/png")


@pytest.mark.parametrize(
    "accept",
    [[], ["image/*"], ["image/png; q=1"], ["png"], ["image/png", "IMAGE/PNG"]],
)
def test_accept_lists_reject_ambiguous_media_types(accept: list[str]) -> None:
    with pytest.raises(ValueError):
        normalize_accept(accept)


def test_accept_rejects_a_bare_string() -> None:
    with pytest.raises(TypeError):
        normalize_accept("image/png")


def test_accept_stops_reading_past_the_media_type_limit() -> None:
    read: list[str] = []

    def endless() -> Any:
        while True:
            read.append(f"image/x-{len(read)}")
            yield read[-1]

    with pytest.raises(ValueError, match="1 to 32 media types"):
        normalize_accept(endless())
    assert len(read) == 33


def _figure() -> Any:
    figure_module = pytest.importorskip("matplotlib.figure")
    figure = figure_module.Figure(figsize=(2, 1), dpi=100)
    figure.subplots().plot([1, 2, 3])
    return figure


def test_matplotlib_figures_render_the_first_accepted_format() -> None:
    figure = _figure()

    pdf = represent(figure, ["application/pdf", "image/svg+xml"])
    svg = represent(figure, ["text/html", "image/svg+xml", "image/png"])

    assert pdf.media_type == "application/pdf" and pdf.data.startswith(b"%PDF-")
    assert svg.media_type == "image/svg+xml" and b"<svg" in svg.data


def test_matplotlib_vector_formats_render_equal_figures_to_equal_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = []
    # matplotlib dates documents from SOURCE_DATE_EPOCH when it writes a date.
    for epoch in ("0", "86400"):
        monkeypatch.setenv("SOURCE_DATE_EPOCH", epoch)
        figure = _figure()
        rendered.append(
            [
                represent(figure, [media_type]).data
                for media_type in ("application/pdf", "image/svg+xml")
            ]
        )

    assert rendered[0] == rendered[1]


def test_matplotlib_pdf_embeds_truetype_fonts() -> None:
    figure = _figure()
    figure.axes[0].set_xlabel("Light (lux)")

    pdf = represent(figure, ["application/pdf"]).data

    assert b"/FontFile2" in pdf
    assert b"/Type3" not in pdf


def _media_box(pdf: bytes) -> tuple[float, float]:
    import re

    match = re.search(rb"/MediaBox \[ *0 0 ([0-9.]+) ([0-9.]+)", pdf)
    assert match is not None
    return float(match[1]), float(match[2])


def test_a_size_draws_a_laid_out_figure_at_that_size_and_keeps_the_original() -> None:
    figure_module = pytest.importorskip("matplotlib.figure")
    figure = figure_module.Figure(figsize=(6, 2), layout="constrained")
    figure.subplots().set_xlabel("Light (lux)")

    column = represent(figure, ["application/pdf"], size=Size(250.38))
    framed = represent(figure, ["application/pdf"], size=Size(250.38, 120))

    assert _media_box(column.data) == pytest.approx((250.38, 250.38 / 3), abs=0.01)
    assert _media_box(framed.data) == pytest.approx((250.38, 120), abs=0.01)
    assert tuple(figure.get_size_inches()) == (6, 2)


@pytest.mark.parametrize("colorbar", [False, True])
def test_a_size_keeps_its_page_for_a_figure_without_a_layout_engine(colorbar: bool) -> None:
    figure_module = pytest.importorskip("matplotlib.figure")
    figure = figure_module.Figure(figsize=(4, 2))
    axes = figure.subplots()
    image = axes.imshow([[0, 1], [1, 0]])
    axes.set_ylabel("Very long label (units)")
    if colorbar:
        figure.colorbar(image, ax=axes)

    pdf = represent(figure, ["application/pdf"], size=Size(250.38, 120)).data

    assert _media_box(pdf) == pytest.approx((250.38, 120), abs=0.01)
    assert figure.get_layout_engine() is None


def test_a_size_fills_its_page_to_the_edges() -> None:
    figure_module = pytest.importorskip("matplotlib.figure")
    figure = figure_module.Figure(figsize=(6, 2), layout="constrained")
    axes = figure.subplots()
    axes.plot([1, 2])
    axes.set_ylabel("Light (lux)")
    sized = values._resized(figure, Size(250.38))
    assert sized is not None

    # The layout engine places the axes while the figure saves.
    sized.savefig(io.BytesIO(), format="pdf")
    ink = sized.get_tightbbox()

    assert ink.x0 == pytest.approx(0, abs=0.01)
    assert ink.x1 == pytest.approx(250.38 / 72, abs=0.01)


def test_a_figure_python_cannot_copy_draws_at_its_own_size() -> None:
    figure_module = pytest.importorskip("matplotlib.figure")
    figure = figure_module.Figure(figsize=(3, 1))
    figure.subplots().plot([1, 2])
    # A generator stands in for state that deepcopy rejects.
    figure.uncopyable = (index for index in range(2))

    pdf = represent(figure, ["application/pdf"], size=Size(250.38)).data

    assert pdf.startswith(b"%PDF-")
    assert _media_box(pdf)[0] < 250


def test_a_size_keeps_text_at_its_point_size() -> None:
    matplotlib = pytest.importorskip("matplotlib")
    figure_module = pytest.importorskip("matplotlib.figure")
    figure = figure_module.Figure(figsize=(6, 2), layout="constrained")
    figure.subplots().set_xlabel("Light (lux)", fontsize=7)

    # Uncompressed content streams show each text run's font size.
    with matplotlib.rc_context({"pdf.compression": 0}):
        pdf = represent(figure, ["application/pdf"], size=Size(180)).data

    assert _media_box(pdf)[0] == pytest.approx(180, abs=0.01)
    assert b" 7 Tf" in pdf


def test_a_size_leaves_pyplot_with_the_figures_it_had() -> None:
    pyplot = pytest.importorskip("matplotlib.pyplot")
    figure, axes = pyplot.subplots(figsize=(4, 2))
    axes.plot([1, 2, 3])
    try:
        before = pyplot.get_fignums()
        represent(figure, ["application/pdf"], size=Size(200))
        assert pyplot.get_fignums() == before
    finally:
        pyplot.close(figure)


def test_a_size_draws_a_single_vega_lite_view_at_that_width() -> None:
    pytest.importorskip("vl_convert")
    specification = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "data": {"values": [{"x": 1, "y": 2}, {"x": 2, "y": 3}]},
        "mark": "line",
        "encoding": {
            "x": {"field": "x", "type": "quantitative"},
            "y": {"field": "y", "type": "quantitative"},
        },
    }

    pdf = represent(specification, ["application/pdf"], size=Size(250.38, 140))

    assert _media_box(pdf.data) == pytest.approx((250.38, 140), abs=0.01)
    assert "width" not in specification


@pytest.mark.parametrize(
    ("width", "height", "error"),
    [
        (0.5, None, ValueError),
        (300, 3_601, ValueError),
        (float("nan"), None, ValueError),
        (True, None, TypeError),
        ("3in", None, TypeError),
    ],
)
def test_sizes_are_finite_point_lengths(
    width: object, height: object, error: type[Exception]
) -> None:
    with pytest.raises(error, match="size"):
        Size(cast(Any, width), cast(Any, height))


def test_matplotlib_png_scale_adds_pixels_and_keeps_the_display_size() -> None:
    axes = _figure().axes[0]

    standard = represent(axes, ["image/png"])
    dense = represent(axes, ["image/png"], scale=2)

    assert standard.data.startswith(_PNG)
    assert (standard.width, standard.height) == _png_size(standard.data)
    # A tight bounding box rounds each edge to whole pixels.
    assert dense.width == pytest.approx(standard.width, abs=1)
    assert dense.height == pytest.approx(standard.height, abs=1)
    assert _png_size(dense.data)[0] / 2 == pytest.approx(standard.width, abs=1)


def test_matplotlib_png_displays_at_marimo_figure_size_whatever_its_dpi() -> None:
    figure_module = pytest.importorskip("matplotlib.figure")
    sizes = []
    for dpi in (100, 200):
        figure = figure_module.Figure(figsize=(3, 2), dpi=dpi)
        figure.subplots().plot([1, 2, 3])
        png = represent(figure, ["image/png"], scale=2)
        sizes.append((png.width, png.height))
        assert _png_size(png.data)[0] * 50 / dpi == pytest.approx(png.width, abs=1)

    assert sizes[0] == pytest.approx(sizes[1], abs=1)


def _png_size(data: bytes) -> tuple[int, int]:
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_altair_charts_and_vega_lite_specifications_render_through_vl_convert() -> None:
    altair = pytest.importorskip("altair")
    pandas = pytest.importorskip("pandas")
    pytest.importorskip("vl_convert")
    # More rows than Altair embeds by default.
    rows = pandas.DataFrame({"x": range(6_000), "y": [index % 7 for index in range(6_000)]})
    chart = altair.Chart(rows).mark_point().encode(x="x:Q", y="y:Q")
    specification = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "data": {"values": [{"x": 1}]},
        "mark": "point",
        "width": 120,
    }

    svg = represent(chart, ["text/html", "image/svg+xml"])
    png = represent(specification, ["image/png"], scale=2)

    assert svg.media_type == "image/svg+xml" and svg.data.startswith(b"<svg")
    assert png.data.startswith(_PNG)
    width, height = _png_size(png.data)
    assert (width / 2, height / 2) == (png.width, png.height)


def test_vega_lite_charts_render_as_pdf_with_embedded_fonts() -> None:
    pytest.importorskip("vl_convert")
    specification = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "data": {"values": [{"x": 1, "y": 2}, {"x": 2, "y": 3}]},
        "mark": "line",
        "encoding": {"x": {"field": "x", "type": "quantitative", "title": "Light (lux)"}},
    }

    pdf = represent(specification, ["application/pdf"]).data

    assert pdf.startswith(b"%PDF-")
    assert b"/FontFile2" in pdf


def _json(value: object) -> object:
    representation = represent(value, ["application/json"])
    assert representation.media_type == "application/json"
    return json.loads(representation.data)


def test_tables_represent_as_json_rows_with_iso_dates_and_null_cells() -> None:
    polars = pytest.importorskip("polars")
    pandas = pytest.importorskip("pandas")
    pyarrow = pytest.importorskip("pyarrow")
    day = datetime.date(2015, 2, 4)
    reading = datetime.datetime(2015, 2, 4, 9, 41)
    rows = [
        {"day": "2015-02-04", "rate": 0.5, "read": "2015-02-04T09:41:00"},
        {"day": None, "rate": None, "read": None},
    ]

    assert (
        _json(polars.DataFrame({"day": [day, None], "rate": [0.5, None], "read": [reading, None]}))
        == rows
    )
    assert (
        _json(pyarrow.table({"day": [day, None], "rate": [0.5, None], "read": [reading, None]}))
        == rows
    )
    assert _json(
        pandas.DataFrame(
            {"read": pandas.to_datetime(["2015-02-04 09:41", None]), "rate": [0.5, None]}
        )
    ) == [{"read": "2015-02-04T09:41:00", "rate": 0.5}, {"read": None, "rate": None}]


def test_data_values_represent_as_json() -> None:
    numpy = pytest.importorskip("numpy")
    zone = datetime.timezone(datetime.timedelta(hours=1))

    assert _json(
        {
            "peak": numpy.float32(0.5),
            "count": numpy.int64(3),
            "missing": float("nan"),
            "nanoseconds": numpy.array(["2015-02-04T09:41:00.123456789"], dtype="datetime64[ns]"),
            "local": datetime.datetime(2015, 2, 4, 9, 41, tzinfo=zone),
            "time": datetime.time(9, 41),
            "interval": datetime.timedelta(minutes=1),
            "pair": (1, "two"),
        }
    ) == {
        "peak": 0.5,
        "count": 3,
        "missing": None,
        "nanoseconds": ["2015-02-04T09:41:00.123456"],
        "local": "2015-02-04T09:41:00+01:00",
        "time": "09:41:00",
        "interval": 60.0,
        "pair": [1, "two"],
    }


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (object(), "The value is a builtins.object, which has no JSON form."),
        ({1: "one"}, "The value has a key that is not text: 1."),
        ({"rows": [{"total": float("inf")}]}, 'The item at ["rows"][0]["total"] is infinite.'),
        (2**53, "The value is an integer beyond 2**53 - 1."),
        (decimal.Decimal("1e400"), "The value is beyond the range of a float."),
    ],
)
def test_values_without_a_json_form_name_the_failing_part(value: object, reason: str) -> None:
    with pytest.raises(RepresentationError) as raised:
        represent(value, ["application/json"])

    assert raised.value.reasons == (reason,)


def test_tables_that_repeat_a_column_name_have_no_json_form() -> None:
    pandas = pytest.importorskip("pandas")
    pyarrow = pytest.importorskip("pyarrow")
    repeated = "The value is a table that repeats the column 'x'."

    for table in (
        pandas.DataFrame([[1, 2]], columns=["x", "x"]),
        pyarrow.table([[1], [2]], names=["x", "x"]),
    ):
        with pytest.raises(RepresentationError) as raised:
            represent(table, ["application/json"])
        assert raised.value.reasons == (repeated,)


def test_json_values_count_object_keys_as_portable_json_does() -> None:
    numpy = pytest.importorskip("numpy")

    with pytest.raises(RepresentationTooLarge, match="more than 100,000 JSON values"):
        represent({str(index): index for index in range(60_000)}, ["application/json"])
    with pytest.raises(RepresentationTooLarge, match="100,001 items"):
        represent(numpy.zeros(100_001), ["application/json"])


def test_a_table_over_the_json_value_limit_fails_before_it_builds_rows() -> None:
    polars = pytest.importorskip("polars")
    table = polars.DataFrame({"x": range(60_000), "y": range(60_000)})

    with pytest.raises(RepresentationTooLarge, match="60,000 rows of 2 columns"):
        represent(table, ["application/json"])
    with pytest.raises(RepresentationTooLarge, match="more than 100,000 JSON values"):
        represent(list(range(100_001)), ["application/json"])


def test_display_methods_supply_other_values_and_their_display_size() -> None:
    class Bundle:
        def _repr_mimebundle_(self, include: object = None, exclude: object = None) -> object:
            data = {"image/png": base64.b64encode(_PNG).decode()}
            return data, {"image/png": {"width": 40, "height": 20}}

    class Methods:
        def _repr_svg_(self) -> str:
            return "<svg/>"

        def _repr_png_(self) -> tuple[bytes, dict[str, object]]:
            return _PNG, {"width": 10.4}

        def _repr_html_(self) -> str:
            return "data:text/html,<b>total</b>"

    class Marimo:
        def _mime_(self) -> tuple[str, str]:
            return "image/svg+xml", "data:image/svg+xml;base64," + base64.b64encode(
                b"<svg/>"
            ).decode()

    assert represent(Bundle(), ["image/png"]) == Representation("image/png", _PNG, 40, 20)
    assert represent(Methods(), ["image/svg+xml"]) == Representation("image/svg+xml", b"<svg/>")
    assert represent(Methods(), ["image/png"]) == Representation("image/png", _PNG, 10, None)
    assert represent(Methods(), ["text/html"]).data == b"data:text/html,<b>total</b>"
    assert represent(Marimo(), ["image/png", "image/svg+xml"]).data == b"<svg/>"


def test_mimebundle_methods_receive_include_and_exclude_only_when_they_take_them() -> None:
    received: list[dict[str, object]] = []

    class Plain:
        def _repr_mimebundle_(self) -> dict[str, str]:
            return {"image/svg+xml": "<svg/>"}

    class Keywords:
        def _repr_mimebundle_(self, **kwargs: object) -> dict[str, str]:
            received.append(kwargs)
            return {"image/svg+xml": "<svg/>"}

    assert represent(Plain(), ["image/svg+xml"]).data == b"<svg/>"
    assert represent(Keywords(), ["image/svg+xml"]).data == b"<svg/>"
    assert received == [{"include": ["image/svg+xml"], "exclude": []}]


@pytest.mark.parametrize(("data", "encoded"), [(0, b"0"), (False, b"false"), (2.5, b"2.5")])
def test_json_display_data_can_be_a_scalar(data: object, encoded: bytes) -> None:
    class Scalar:
        def _repr_json_(self) -> object:
            return data

    assert represent(Scalar(), ["application/json"]) == Representation("application/json", encoded)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (("IMAGE/PNG", _PNG), ValueError),
        (("image/png", "PNG"), TypeError),
        (("image/png", _PNG, 0), ValueError),
        (("image/png", _PNG, True), ValueError),
        (("image/png", _PNG, 300, 2.5), ValueError),
    ],
)
def test_representations_reject_malformed_fields(
    arguments: tuple[Any, ...], error: type[Exception]
) -> None:
    with pytest.raises(error):
        Representation(*arguments)


def test_a_failing_display_method_leaves_the_next_accepted_type() -> None:
    class Broken:
        def _repr_svg_(self) -> str:
            raise ValueError("no vector form")

        def _repr_png_(self) -> bytes:
            return _PNG

    assert represent(Broken(), ["image/svg+xml", "image/png"]).media_type == "image/png"
    with pytest.raises(
        RepresentationError,
        match=r"_repr_svg_\(\) raised ValueError: no vector form\.",
    ):
        represent(Broken(), ["image/svg+xml"])


def test_a_value_without_an_accepted_representation_names_the_type() -> None:
    with pytest.raises(
        RepresentationError,
        match=r"builtins\.dict has no representation as application/pdf, image/png",
    ):
        represent({"total": 3}, ["application/pdf", "image/png"])


def test_the_module_runs_with_only_the_standard_library(tmp_path: Path) -> None:
    script = tmp_path / "check.py"
    script.write_text(
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('portable_values', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['portable_values'] = module\n"
        "spec.loader.exec_module(module)\n"
        "class Svg:\n"
        "    def _repr_svg_(self):\n"
        "        return '<svg/>'\n"
        "value = module.ValueSelector('chart.view').resolve({'chart': {'view': Svg()}})\n"
        "print(module.represent(value, ['image/svg+xml']).data.decode())\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(script), str(Path(values.__file__))],
        capture_output=True,
        check=True,
        text=True,
    )

    assert completed.stdout.strip() == "<svg/>"
