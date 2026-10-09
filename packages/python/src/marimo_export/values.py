"""Select notebook values and represent them in a consumer's media type.

A ``ValueSelector`` names a notebook definition followed by attribute and item
steps, such as ``report.rows[0]["total"]``. ``represent()`` converts a value to
the first media type in ``accept`` that the value supports. A typeset document
can accept PDF and SVG while a browser accepts PNG, and the notebook keeps its
default output settings. A ``Size`` draws a figure or chart at the size a
document places it, and data such as tables and dates represents as
``application/json``.

This module imports only the Python standard library. A host can load its
source where marimo-export is not installed, such as a Pyodide worker.
Plotting libraries are used only for values they created.
"""

from __future__ import annotations

import base64
import binascii
import copy
import dataclasses
import datetime
import decimal
import enum
import inspect
import io
import json
import math
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, TypeVar, cast

MAX_SELECTOR_BYTES = 4_096
MAX_SELECTOR_STEPS = 64
MAX_ACCEPTED_MEDIA_TYPES = 32
MAX_SIZE_POINTS = 3_600
MAX_JSON_VALUES = 100_000

_MAX_JSON_DEPTH = 256
_MAX_SAFE_INTEGER = 2**53 - 1

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INDEX = re.compile(r"(?:0|[1-9][0-9]*)")
_MAX_INDEX = 2**53 - 1
_JSON_DECODER = json.JSONDecoder()
_MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}")


class SelectorError(ValueError):
    """A value selector does not follow the selector grammar."""


class SelectorStep(NamedTuple):
    """One attribute or item step after a selector's root."""

    kind: Literal["attribute", "item"]
    key: str | int


@dataclass(frozen=True, slots=True)
class ValueSelector:
    """An ASCII identifier root followed by attribute and item steps.

    ``ValueSelector('report.rows[0]["total"]')`` parses its source and raises
    ``SelectorError`` when the source does not follow the grammar. Attribute
    steps name public attributes, so they cannot start with ``_``. Item steps
    take a nonnegative integer up to 2**53 - 1 or a JSON string. A selector
    contains at most ``MAX_SELECTOR_BYTES`` UTF-8 bytes and
    ``MAX_SELECTOR_STEPS`` steps.
    """

    source: str
    root: str = field(init=False, repr=False, compare=False)
    path: tuple[SelectorStep, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        root, path = _parse(self.source)
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "path", path)

    def resolve(self, namespace: Mapping[str, object]) -> object:
        """Return the selected value from a namespace such as notebook globals.

        Mapping keys take precedence over attributes. A step from ``None``
        selects ``None``, so ``peak.label`` reads as missing while ``peak`` is
        ``None``. Raises ``LookupError`` when the root is undefined or a step is
        unavailable.
        """

        if self.root not in namespace:
            raise LookupError(f"{self.root!r} is not defined")
        current = namespace[self.root]
        for kind, key in self.path:
            if current is None:
                return None
            if kind == "attribute" and isinstance(current, Mapping) and key in current:
                current = cast(Mapping[object, object], current)[key]
            elif kind == "attribute":
                try:
                    current = getattr(current, cast(str, key))
                except AttributeError as error:
                    raise LookupError(f"attribute {key!r} is unavailable") from error
            else:
                try:
                    current = cast(Any, current)[key]
                except (IndexError, KeyError, TypeError) as error:
                    raise LookupError(f"item {key!r} is unavailable") from error
        return current


def _parse(source: object) -> tuple[str, tuple[SelectorStep, ...]]:
    if not isinstance(source, str):
        raise TypeError("selector must be a string")
    if not source or source != source.strip():
        raise _selector_error(source, "must not be empty or contain surrounding whitespace")
    if len(_utf8(source, source)) > MAX_SELECTOR_BYTES:
        raise _selector_error(source, f"must contain at most {MAX_SELECTOR_BYTES} UTF-8 bytes")
    root = _IDENTIFIER.match(source)
    if root is None:
        raise _selector_error(source, "must start with an ASCII identifier")
    path: list[SelectorStep] = []
    position = root.end()
    while position < len(source):
        if len(path) == MAX_SELECTOR_STEPS:
            raise _selector_error(source, f"may contain at most {MAX_SELECTOR_STEPS} steps")
        if source[position] == ".":
            step, position = _parse_attribute(source, position)
        elif source[position] == "[":
            step, position = _parse_item(source, position)
        else:
            raise _selector_error(
                source, "may contain only ASCII identifiers, dot selection, and bracket indexing"
            )
        path.append(step)
    return root.group(), tuple(path)


def _parse_attribute(source: str, position: int) -> tuple[SelectorStep, int]:
    selected = _IDENTIFIER.match(source, position + 1)
    if selected is None:
        raise _selector_error(source, "dot selection requires an ASCII identifier")
    if selected.group().startswith("_"):
        raise _selector_error(source, "dot selection cannot name a private attribute")
    return SelectorStep("attribute", selected.group()), selected.end()


def _parse_item(source: str, position: int) -> tuple[SelectorStep, int]:
    position += 1
    selected_index = _INDEX.match(source, position)
    key: str | int
    if selected_index is not None:
        key = int(selected_index.group())
        if key > _MAX_INDEX:
            raise _selector_error(source, f"bracket indexes must be at most {_MAX_INDEX}")
        position = selected_index.end()
    elif source.startswith('"', position):
        try:
            key, consumed = _JSON_DECODER.raw_decode(source, position)
        except json.JSONDecodeError as error:
            raise _selector_error(source, "bracket keys must be JSON strings") from error
        _utf8(key, source)
        position = consumed
    else:
        raise _selector_error(source, "brackets require a nonnegative integer or JSON string")
    if not source.startswith("]", position):
        raise _selector_error(source, "bracket selection requires a closing ]")
    return SelectorStep("item", key), position + 1


def _utf8(text: str, source: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _selector_error(source, "must be well-formed Unicode") from error


def _selector_error(source: str, detail: str) -> SelectorError:
    return SelectorError(f"invalid value selector {source!r}: {detail}")


@dataclass(frozen=True, slots=True)
class Representation:
    """A value encoded in one media type. Text media types use UTF-8.

    ``width`` and ``height`` give a raster image's display size in CSS pixels.
    A matplotlib PNG displays at 100 CSS pixels per inch of figure size, as
    marimo shows figures, and a chart PNG at the chart's size. A PNG that
    ``represent()`` renders at ``scale`` 2 has twice as many pixels as its
    display size. A display method can report the size through IPython
    metadata. Otherwise both are ``None``.
    """

    media_type: str
    data: bytes = field(repr=False)
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.media_type, str) or not _MEDIA_TYPE.fullmatch(self.media_type):
            raise ValueError(f"{self.media_type!r} is not a lowercase type/subtype media type")
        if not isinstance(self.data, bytes):
            raise TypeError("representation data must be bytes")
        for size in (self.width, self.height):
            if size is not None and (type(size) is not int or size < 1):
                raise ValueError("representation width and height must be positive integers")


@dataclass(frozen=True, slots=True)
class Size:
    """A display size in points, 1/72 inch, such as a column's width.

    ``represent()`` draws a matplotlib figure or a Vega-Lite chart at this size,
    so its text keeps its point size where a document places it. Without a
    ``height``, a matplotlib figure keeps its aspect ratio and a chart keeps
    its height. Each length is a finite number from 1 to ``MAX_SIZE_POINTS``
    (50 inches), which bounds the pixels of a PNG drawn at that size.
    """

    width: float
    height: float | None = None

    def __post_init__(self) -> None:
        for name in ("width", "height"):
            length = getattr(self, name)
            if length is None and name == "height":
                continue
            if isinstance(length, bool) or not isinstance(length, (int, float)):
                raise TypeError(f"size {name} must be a number of points")
            if not math.isfinite(length) or not 1 <= length <= MAX_SIZE_POINTS:
                raise ValueError(f"size {name} must be from 1 to {MAX_SIZE_POINTS:,} points")


class RepresentationError(ValueError):
    """A value supports none of the accepted media types.

    ``reasons`` holds one sentence for each display method or conversion that
    failed, such as ``The item at ["total"] is infinite.``
    """

    def __init__(self, message: str, reasons: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.reasons = tuple(reasons)


class RepresentationTooLarge(RepresentationError):
    """A value is data whose JSON form holds more than ``MAX_JSON_VALUES`` values."""


class _JsonLimit(ValueError):
    pass


def normalize_accept(accept: Iterable[str]) -> tuple[str, ...]:
    """Return accepted media types in preference order, lowercased.

    Each entry is a ``type/subtype`` media type without parameters or
    wildcards. Raises ``ValueError`` for an empty, duplicate, or invalid entry.
    """

    if isinstance(accept, str):
        raise TypeError("accept must be a sequence of media types, not a string")
    limit = f"accept must list 1 to {MAX_ACCEPTED_MEDIA_TYPES} media types"
    accepted: list[str] = []
    for item in accept:
        if len(accepted) == MAX_ACCEPTED_MEDIA_TYPES:
            raise ValueError(limit)
        if not isinstance(item, str):
            raise TypeError("accepted media types must be strings")
        media_type = item.lower()
        if _MEDIA_TYPE.fullmatch(media_type) is None:
            raise ValueError(f"{item!r} is not a type/subtype media type")
        if media_type in accepted:
            raise ValueError(f"{item!r} appears more than once")
        accepted.append(media_type)
    if not accepted:
        raise ValueError(limit)
    return tuple(accepted)


def represent(
    value: object,
    accept: Iterable[str],
    *,
    scale: float = 1.0,
    size: Size | None = None,
) -> Representation:
    """Represent ``value`` in the first accepted media type it supports.

    Matplotlib figures and artists render as PDF, SVG, or PNG. Altair charts and
    Vega-Lite specifications render as PDF, SVG, or PNG with vl-convert-python.
    Other values use their ``_repr_mimebundle_()``, ``_repr_*_()``, or marimo
    ``_mime_()`` display methods. Data without a JSON display method, such as
    dictionaries, lists, numbers, dates, NumPy arrays, and pandas, Polars, or
    PyArrow tables, represents as ``application/json``.

    ``scale`` multiplies the pixel density of the PNG images this function
    renders and keeps their display size. ``size`` draws a copy of a figure or
    chart at that size and leaves ``value`` unchanged. Display methods ignore
    it.

    A display method that raises leaves its media types unavailable. Raises
    ``RepresentationError`` naming those failures when the value supports none
    of the accepted types.
    """

    accepted = normalize_accept(accept)
    scale = _scale(scale)
    if size is not None and not isinstance(size, Size):
        raise TypeError("size must be a Size")
    calls = _Calls()
    sources = (
        _library_source(value, scale, size, calls),
        _bundle_source(value, accepted, calls),
        _method_source(value, calls),
        _mime_source(value, calls),
        _data_source(value, calls),
    )
    for media_type in accepted:
        for source in sources:
            representation = source(media_type) if source is not None else None
            if representation is not None:
                return representation
    name = f"{type(value).__module__}.{type(value).__qualname__}"
    detail = "".join(f" {reason}" for reason in calls.reasons)
    raise RepresentationError(
        f"{name} has no representation as {', '.join(accepted)}.{detail}", calls.reasons
    )


def _scale(scale: object) -> float:
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        raise TypeError("scale must be a number")
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be a positive finite number")
    return float(scale)


_Result = TypeVar("_Result")
_Source = Callable[[str], "Representation | None"]


class _Calls:
    """Run each display call once and keep the reasons calls failed."""

    def __init__(self) -> None:
        self.reasons: list[str] = []
        self._results: dict[str, object] = {}

    def __call__(self, label: str, call: Callable[[], _Result]) -> _Result | None:
        if label not in self._results:
            try:
                self._results[label] = call()
            except Exception as error:
                self._results[label] = None
                self.note(f"{label} raised {type(error).__name__}: {error}")
        return cast("_Result | None", self._results[label])

    def note(self, reason: str) -> None:
        reason = reason if reason.endswith(".") else f"{reason}."
        if reason not in self.reasons:
            self.reasons.append(reason)


def _displayed(media_type: str, data: object, metadata: object) -> Representation | None:
    payload = _payload(media_type, data)
    if payload is None:
        return None
    size = metadata if isinstance(metadata, Mapping) else {}
    return Representation(
        media_type,
        payload,
        _dimension(size.get("width")),
        _dimension(size.get("height")),
    )


def _rendered(media_type: str, data: bytes, density: float) -> Representation:
    """Return rendered bytes with a PNG's display size, its pixels over ``density``."""

    if media_type != "image/png" or data[:8] != _PNG_SIGNATURE or data[12:16] != b"IHDR":
        return Representation(media_type, data)
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return Representation(
        media_type, data, _dimension(width / density), _dimension(height / density)
    )


def _dimension(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value) if math.isfinite(value) and value >= 1 else None


def _split(result: object) -> tuple[object, object]:
    """Separate IPython display data from the metadata it may return with it."""

    if isinstance(result, tuple) and len(result) == 2:
        return result[0], result[1]
    return result, None


def _is_text(media_type: str) -> bool:
    kind, _, subtype = media_type.partition("/")
    return (
        kind == "text"
        or subtype.endswith(("+xml", "+json"))
        or subtype in {"json", "javascript", "xml"}
    )


def _payload(media_type: str, data: object) -> bytes | None:
    """Return display data as bytes. Binary data may arrive as base64 text."""

    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data) or None
    if media_type.endswith("json") and isinstance(data, (dict, list, bool, int, float)):
        # IPython display data for a JSON media type is the decoded value. A
        # string stays JSON text, as in marimo's ``_mime_()``.
        try:
            text = json.dumps(data, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return None
        return text.encode("utf-8")
    if not isinstance(data, str) or not data:
        return None
    data_url = f"data:{media_type};base64,"
    if data[: len(data_url)].lower() == data_url:
        return _base64(data[len(data_url) :])
    if _is_text(media_type):
        return data.encode("utf-8")
    return _base64(data)


def _base64(text: str) -> bytes | None:
    try:
        return base64.b64decode("".join(text.split()), validate=True) or None
    except (binascii.Error, ValueError):
        return None


def _bundle_source(value: object, accepted: tuple[str, ...], calls: _Calls) -> _Source | None:
    method = getattr(type(value), "_repr_mimebundle_", None)
    if not callable(method):
        return None

    # IPython passes include and exclude, and marimo calls the method without
    # arguments. Pass them when the signature takes them or cannot be read.
    arguments: dict[str, object] = {"include": list(accepted), "exclude": []}
    try:
        inspect.signature(method).bind(value, **arguments)
    except TypeError:
        arguments = {}
    except ValueError:
        pass

    def source(media_type: str) -> Representation | None:
        result = calls("_repr_mimebundle_()", lambda: method(value, **arguments))
        bundle, metadata = _split(result)
        if not isinstance(bundle, Mapping):
            return None
        bundle = cast(Mapping[str, object], bundle)
        sizes = cast(Mapping[str, object], metadata) if isinstance(metadata, Mapping) else {}
        return _displayed(media_type, bundle.get(media_type), sizes.get(media_type))

    return source


_REPR_METHODS = {
    "application/json": "_repr_json_",
    "application/pdf": "_repr_pdf_",
    "image/jpeg": "_repr_jpeg_",
    "image/png": "_repr_png_",
    "image/svg+xml": "_repr_svg_",
    "text/html": "_repr_html_",
    "text/latex": "_repr_latex_",
    "text/markdown": "_repr_markdown_",
}


def _method_source(value: object, calls: _Calls) -> _Source:
    def source(media_type: str) -> Representation | None:
        name = _REPR_METHODS.get(media_type)
        method = getattr(type(value), name, None) if name is not None else None
        if not callable(method):
            return None
        data, metadata = _split(calls(f"{name}()", lambda: method(value)))
        return _displayed(media_type, data, metadata)

    return source


def _mime_source(value: object, calls: _Calls) -> _Source | None:
    method = getattr(type(value), "_mime_", None)
    if not callable(method):
        return None

    def source(media_type: str) -> Representation | None:
        result = calls("_mime_()", lambda: method(value))
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            return None
        mimetype, data = result
        if not isinstance(mimetype, str) or mimetype.lower() != media_type:
            return None
        return _displayed(media_type, data, None)

    return source


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MATPLOTLIB_FORMATS = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/svg+xml": "svg",
}
# Omit the dates matplotlib writes by default, and salt SVG element IDs with a
# constant, so equal figures produce equal bytes. PDF embeds TrueType fonts,
# which print preflight checks accept and matplotlib's default Type 3 fonts fail.
_MATPLOTLIB_METADATA: dict[str, dict[str, None]] = {
    "pdf": {"CreationDate": None},
    "svg": {"Date": None},
}
_MATPLOTLIB_SETTINGS: dict[str, dict[str, object]] = {
    "pdf": {"pdf.fonttype": 42},
    "svg": {"svg.hashsalt": "marimo-export"},
}
_VEGA_LITE_SCHEMA = re.compile(
    r"https://vega\.github\.io/schema/vega-lite/v(?P<major>[1-9][0-9]*)"
    r"(?:\.[0-9]+)*\.json(?:[?#].*)?"
)


def _library_source(
    value: object, scale: float, size: Size | None, calls: _Calls
) -> _Source | None:
    figure = _matplotlib_figure(value)
    if figure is not None:
        return lambda media_type: _matplotlib(figure, media_type, scale, size, calls)
    if _is_vega_lite(value):
        return lambda media_type: _vega_lite(value, media_type, scale, size, calls)
    return None


def _matplotlib_figure(value: object) -> Any:
    module = sys.modules.get("matplotlib.figure")
    artist = sys.modules.get("matplotlib.artist")
    if module is None or artist is None or not isinstance(value, (module.Figure, artist.Artist)):
        return None
    figure: Any = value
    while not isinstance(figure, module.Figure):
        figure = getattr(figure, "figure", None)
        if figure is None or figure is value:
            return None
    return figure


def _matplotlib(
    figure: Any, media_type: str, scale: float, size: Size | None, calls: _Calls
) -> Representation | None:
    format = _MATPLOTLIB_FORMATS.get(media_type)
    if format is None:
        return None
    data = calls(f"savefig(format={format!r})", lambda: _savefig(figure, format, scale, size))
    # marimo displays a figure at 100 CSS pixels per inch whatever its DPI.
    return _rendered(media_type, data, figure.dpi * scale / 100) if data else None


def _savefig(figure: Any, format: str, scale: float, size: Size | None) -> bytes:
    buffer = io.BytesIO()
    matplotlib = sys.modules["matplotlib"]
    sized = _resized(figure, size) if size is not None else None
    try:
        drawn = figure if sized is None else sized
        with matplotlib.rc_context(_MATPLOTLIB_SETTINGS.get(format, {})):
            drawn.savefig(
                buffer,
                format=format,
                dpi=figure.dpi * scale,
                # A layout engine fits the figure's text inside its requested
                # size. Without one, the tight box keeps labels from clipping.
                bbox_inches=None if sized is not None and _laid_out(sized) else "tight",
                metadata=_MATPLOTLIB_METADATA.get(format),
            )
    finally:
        pyplot = sys.modules.get("matplotlib.pyplot")
        if sized is not None and pyplot is not None:
            # Copying a pyplot figure registers the copy with pyplot.
            pyplot.close(sized)
    return buffer.getvalue()


def _resized(figure: Any, size: Size) -> Any:
    """Return a copy of ``figure`` at ``size``, with fonts at their point sizes."""

    width, height = figure.get_size_inches()
    sized = copy.deepcopy(figure)
    inches = size.width / 72
    sized.set_size_inches(
        inches, size.height / 72 if size.height is not None else inches * height / width
    )
    return sized


def _laid_out(figure: Any) -> bool:
    return figure.get_layout_engine() is not None


def _is_vega_lite(value: object) -> bool:
    """Return whether ``value`` is an Altair chart or a Vega-Lite specification."""

    altair = sys.modules.get("altair")
    if altair is not None and isinstance(value, altair.TopLevelMixin):
        return True
    schema = (
        cast(Mapping[str, object], value).get("$schema") if isinstance(value, Mapping) else None
    )
    return isinstance(schema, str) and _VEGA_LITE_SCHEMA.fullmatch(schema) is not None


def _vega_lite(
    value: object, media_type: str, scale: float, size: Size | None, calls: _Calls
) -> Representation | None:
    if media_type not in {"application/pdf", "image/png", "image/svg+xml"}:
        return None
    try:
        import vl_convert
    except ImportError:
        calls.note("Vega-Lite charts need vl-convert-python for PDF, SVG, and PNG output")
        return None
    specification = calls("Vega-Lite specification", lambda: _vega_lite_specification(value))
    if specification is None:
        return None
    if size is not None:
        specification = _sized_vega_lite(specification, size)
    if media_type == "application/pdf":
        pdf = calls("vl-convert PDF", lambda: vl_convert.vegalite_to_pdf(specification))
        return Representation(media_type, pdf) if pdf else None
    if media_type == "image/svg+xml":
        svg = calls("vl-convert SVG", lambda: vl_convert.vegalite_to_svg(specification))
        return Representation(media_type, svg.encode("utf-8")) if svg else None
    png = calls("vl-convert PNG", lambda: vl_convert.vegalite_to_png(specification, scale=scale))
    return _rendered(media_type, png, scale) if png else None


def _vega_lite_specification(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    # Inline every row under Altair's default transformer, so the specification
    # renders without the notebook's data files or row limit. The transformer
    # registry is process-wide state.
    with sys.modules["altair"].data_transformers.enable("default", max_rows=None):
        return cast(Any, value).to_dict()


# Compound charts size each of their views, so a size applies to single and
# layered views.
_VEGA_LITE_COMPOUND = frozenset({"concat", "facet", "hconcat", "repeat", "vconcat"})


def _sized_vega_lite(specification: dict[str, Any], size: Size) -> dict[str, Any]:
    if not _VEGA_LITE_COMPOUND.isdisjoint(specification):
        return specification
    # vl-convert draws one Vega-Lite pixel as one point, and "fit" sizes the
    # whole chart, with its axes and legends, to the given width.
    sized = {
        **specification,
        "width": size.width,
        "autosize": {"type": "fit", "contains": "padding"},
    }
    if size.height is not None:
        sized["height"] = size.height
    return sized


def _data_source(value: object, calls: _Calls) -> _Source:
    def source(media_type: str) -> Representation | None:
        if media_type != "application/json":
            return None
        try:
            data = _json_bytes(value)
        except _JsonLimit as error:
            reason = f"{str(error)[:1].upper()}{str(error)[1:]}."
            raise RepresentationTooLarge(reason, (reason,)) from error
        except ValueError as error:
            reason = str(error)
            calls.note(reason[:1].upper() + reason[1:])
            return None
        return Representation(media_type, data)

    return source


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        _json_form(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_form(value: object) -> object:
    """Return ``value`` as JSON data, or raise ``ValueError`` naming the part.

    JSON outputs in exports and ``application/json`` representations share
    this conversion.
    """

    return _json_data(value, "", 0, [0])


def _part(path: str) -> str:
    return f"the item at {path}" if path else "the value"


def _json_data(value: object, path: str, depth: int, count: list[int]) -> object:
    """Return ``value`` as JSON data, or raise ``ValueError`` naming the path.

    Tables become lists of row objects, dates and times become ISO 8601 text,
    durations become seconds, and NaN and missing table cells become null.
    """

    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"{_part(path)} nests deeper than {_MAX_JSON_DEPTH} levels")
    count[0] += 1
    if count[0] > MAX_JSON_VALUES:
        raise _JsonLimit(
            f"the value holds more than {MAX_JSON_VALUES:,} JSON values, so filter or aggregate it"
        )
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError(f"{_part(path)} is an integer beyond 2**53 - 1")
        return value
    if isinstance(value, (float, decimal.Decimal)):
        number = float(value)
        if math.isnan(number):
            return None
        if math.isinf(number):
            raise ValueError(f"{_part(path)} is infinite")
        return number
    if _is_missing_time(value):
        return None
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, enum.Enum):
        return _json_data(value.value, path, depth, count)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        items: dict[str, object] = {}
        for key, item in mapping.items():
            if not isinstance(key, str):
                raise ValueError(f"{_part(path)} has a key that is not text: {key!r}")
            items[key] = _json_data(item, f"{path}[{json.dumps(key)}]", depth + 1, count)
        return items
    if isinstance(value, (list, tuple)):
        sequence = cast(Sequence[object], value)
        return [
            _json_data(item, f"{path}[{index}]", depth + 1, count)
            for index, item in enumerate(sequence)
        ]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_data(
            {item.name: getattr(value, item.name) for item in dataclasses.fields(value)},
            path,
            depth,
            count,
        )
    rows = _library_data(value, count)
    if rows is not _NOT_DATA:
        return _json_data(rows, path, depth, count)
    name = f"{type(value).__module__}.{type(value).__qualname__}"
    raise ValueError(f"{_part(path)} is a {name}, which has no JSON form")


_NOT_DATA = object()


def _is_missing_time(value: object) -> bool:
    # pandas and NumPy spell a missing time as NaT, which subclasses datetime.
    return type(value).__name__ == "NaTType"


def _library_data(value: object, count: list[int]) -> object:
    """Return the rows of a table, or the items of an array or series."""

    numpy = sys.modules.get("numpy")
    if numpy is not None and isinstance(value, (numpy.generic, numpy.ndarray)):
        # NumPy turns nanosecond times into integers, so read them as
        # microseconds, which Python's datetime holds.
        if value.dtype.kind == "M":
            value = value.astype("datetime64[us]")
        elif value.dtype.kind == "m":
            value = value.astype("timedelta64[us]")
        return value.tolist() if isinstance(value, numpy.ndarray) else value.item()
    polars = sys.modules.get("polars")
    if polars is not None:
        if isinstance(value, polars.DataFrame):
            _check_table(value.height, value.width, count)
            return value.to_dicts()
        if isinstance(value, polars.Series):
            return value.to_list()
    pandas = sys.modules.get("pandas")
    if pandas is not None:
        if isinstance(value, pandas.DataFrame):
            rows, columns = value.shape
            _check_table(rows, columns, count)
            # Object columns keep missing values as None and NaN rather than
            # pandas' NA scalars, which are not JSON data.
            return value.astype(object).where(value.notna(), None).to_dict("records")
        if isinstance(value, pandas.Series):
            return value.astype(object).where(value.notna(), None).tolist()
    pyarrow = sys.modules.get("pyarrow")
    if pyarrow is not None:
        if isinstance(value, (pyarrow.Table, pyarrow.RecordBatch)):
            _check_table(value.num_rows, value.num_columns, count)
            return value.to_pylist()
        if isinstance(value, (pyarrow.Array, pyarrow.ChunkedArray)):
            return value.to_pylist()
    return _NOT_DATA


def _check_table(rows: int, columns: int, count: list[int]) -> None:
    if count[0] + rows * (columns + 1) > MAX_JSON_VALUES:
        raise _JsonLimit(
            f"the table's {rows:,} rows of {columns:,} columns hold more than "
            f"{MAX_JSON_VALUES:,} JSON values, so filter or aggregate it"
        )


__all__ = [
    "MAX_ACCEPTED_MEDIA_TYPES",
    "MAX_JSON_VALUES",
    "MAX_SELECTOR_BYTES",
    "MAX_SELECTOR_STEPS",
    "MAX_SIZE_POINTS",
    "Representation",
    "RepresentationError",
    "RepresentationTooLarge",
    "SelectorError",
    "SelectorStep",
    "Size",
    "ValueSelector",
    "normalize_accept",
    "represent",
]
