---
title: Select and represent values
description: Parse value selectors, resolve them against notebook globals, and render values in an accepted media type.
---

# Select and represent values

`marimo_export.values` holds the selector grammar used by every selected-value
output and the media negotiation behind the `media` exporter. Hosts that read
live notebook values use the same functions, so a selector and a figure render
the same way in a live kernel and in an export that has the same plotting
libraries.

```python
from marimo_export.values import Size, ValueSelector, represent

figure = ValueSelector("report.figure").resolve(globals())
pdf = represent(figure, ["application/pdf", "image/svg+xml"], size=Size(251.3))
assert pdf.media_type == "application/pdf"
```

The module imports only the Python standard library. A host can load its source
where marimo-export is not installed, such as a
[Pyodide](https://pyodide.org/) worker that runs a notebook in the browser.

## `ValueSelector`

```python
ValueSelector(source: str) -> ValueSelector
selector.resolve(namespace: Mapping[str, object]) -> object
```

`ValueSelector(source)` parses `source` into a frozen record with `source`,
`root`, and `path`. Selectors with the same source are equal. Each path item is
a `SelectorStep(kind, key)` named tuple, where `kind` is `"attribute"` or
`"item"`.

| Syntax            | Step                                                  |
| ----------------- | ----------------------------------------------------- |
| `report`          | Root: an ASCII identifier                             |
| `.summary`        | Attribute: an identifier that does not start with `_` |
| `[0]`             | Item: a canonical integer from 0 through 2\*\*53 - 1  |
| `["total value"]` | Item: a JSON string                                   |

A selector has no surrounding whitespace, contains at most `MAX_SELECTOR_BYTES`
(4,096) UTF-8 bytes and `MAX_SELECTOR_STEPS` (64) steps, and must be
well-formed Unicode. Other text raises `SelectorError`, a `ValueError`.
`OutputSpec` factories report the same failure as `SpecError` with code
`spec_output_invalid`.

`resolve()` reads the root from `namespace`, then applies each step. An
attribute step reads a mapping key when the current value is a mapping that
contains it, and the attribute otherwise. It raises `LookupError` when the root
is undefined or a step is unavailable.

## `represent()`

```python
represent(
    value: object,
    accept: Iterable[str],
    *,
    scale: float = 1.0,
    size: Size | None = None,
) -> Representation
```

Returns a `Representation` for the first media type in `accept` that the value
supports. Raises `RepresentationError`, a `ValueError`, when the value supports
none of them. The message names the value's type, each display method that
raised, and the package to install when a renderer is missing.

Figures and charts use their library's renderer. A Vega-Lite specification is a
mapping whose `$schema` is a `https://vega.github.io/schema/vega-lite/` URL.
Other values use the
[IPython display methods](https://ipython.readthedocs.io/en/stable/config/integrating.html)
or marimo's [`_mime_()`](https://docs.marimo.io/guides/integrating_with_marimo/displaying_objects/)
method, looked up on the value's type as Python looks up special methods. A
display method that raises leaves its media types unavailable, and
`represent()` tries the next accepted type.

| Value                                   | Media types                                                              |
| --------------------------------------- | ------------------------------------------------------------------------ |
| Matplotlib figure or artist             | `application/pdf`, `image/svg+xml`, `image/png`                          |
| Altair chart or Vega-Lite specification | `application/pdf`, `image/svg+xml`, `image/png` with `vl-convert-python` |
| Value with `_repr_mimebundle_()`        | The types in its bundle                                                  |
| Value with `_repr_*_()`                 | PNG, JPEG, SVG, PDF, HTML, Markdown, LaTeX, or JSON                      |
| Value with marimo's `_mime_()`          | The type it returns                                                      |

`represent()` passes IPython's `include` and `exclude` arguments to
`_repr_mimebundle_()` when its signature takes them, and calls it without
arguments otherwise, as marimo does. Data for a JSON media type can be any JSON
value, such as an object, an array, a number, or a boolean.

A matplotlib artist, such as an `Axes`, renders its whole figure. Matplotlib
PDF embeds TrueType fonts, which print preflight checks accept, and its PDF and
SVG output omits creation dates and uses constant SVG element IDs, so equal
figures produce equal bytes. An Altair chart renders with all of its rows,
because the image carries no data. vl-convert-python draws charts with its
bundled Vega-Lite release and embeds their fonts in PDF. It numbers those fonts
in varying order, so the PDF bytes of equal charts can differ.

The notebook's own settings stay unchanged. A figure that marimo displays as a
PNG can render as PDF for a typeset document and as SVG for a web page in the
same session.

### `Size`

```python
Size(width: float, height: float | None = None) -> Size
```

A display size in points, 1/72 inch, such as the width of the column a
document places a figure in. `represent()` draws a copy of a matplotlib figure
at that size, so its text keeps the point size the notebook gave it, and leaves
the notebook's figure unchanged. A figure with a layout engine, such as
`layout="constrained"`, fits its labels inside the size exactly. A figure
without one keeps the tight bounding box, which can differ from the size by its
padding. Without a `height`, the figure keeps its aspect ratio.

A single or layered Vega-Lite chart draws at the size with `autosize` set to
`fit`, so its axes and legends fit inside the width. Without a `height`, the
chart keeps its own height. Compound charts and values drawn by display methods
ignore the size.

Each length is a finite number from 1 to `MAX_SIZE_POINTS` (3,600, or 50
inches). Other numbers raise `ValueError`, and other types raise `TypeError`.

### `Representation`

| Field        | Type          | Meaning                                        |
| ------------ | ------------- | ---------------------------------------------- |
| `media_type` | `str`         | The accepted media type, lowercased            |
| `data`       | `bytes`       | The encoded value. Text media types use UTF-8  |
| `width`      | `int \| None` | Display width of a raster image in CSS pixels  |
| `height`     | `int \| None` | Display height of a raster image in CSS pixels |

A matplotlib PNG displays at 100 CSS pixels per inch of figure size, as marimo
shows figures whatever their DPI, and a chart PNG displays at the chart's size.
`scale` multiplies the pixel density of the PNG images that `represent()`
renders, and of rasterized artists inside a matplotlib PDF or SVG. A PNG
rendered with `scale=2` has twice as many pixels in each direction as its
display size, so it stays sharp on a high-density screen.

Display methods can report a size through IPython metadata, such as
`{"width": 300}` returned beside `_repr_png_()` data. Media without a reported
size, including the PDF and SVG that `represent()` renders, leave both fields
`None`. A `Representation` raises `ValueError` for a malformed media type or a
size that is not a positive integer, and `TypeError` when `data` is not bytes.
The [`media` exporter](produce#built-in-exporters) stores the size in the
`BlobAsset` metadata, and [`imageLoader()`](../browser/loaders#images) sizes
the image element with it.

## `normalize_accept()`

```python
normalize_accept(accept: Iterable[str]) -> tuple[str, ...]
```

Returns the accepted media types lowercased and in preference order. Each entry
is a `type/subtype` media type without parameters or wildcards. A list holds 1
to `MAX_ACCEPTED_MEDIA_TYPES` (32) unique entries, and reading stops at the
first entry past that limit. A bare string raises `TypeError`, and an empty,
duplicate, or malformed entry raises `ValueError`.

The [`media` exporter](produce#built-in-exporters) applies `represent()` to a
selected value in every exported state.
