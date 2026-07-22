# Export plans

An export plan declares public inputs, a finite scenario matrix, and the projections to publish from each scenario. CLI plan files may use JSON or YAML with the `marimo-export.plan.v1` schema. The Python producer receives the decoded JSON-compatible object.

This plan runs against [`cache_matrix.py`](https://github.com/marimo-team/marimo-export/blob/main/examples/_notebooks/cache_matrix.py):

```yaml
schema: marimo-export.plan.v1

inputs:
  scale:
    definition: scale
    default: 2
  multiplier:
    ui: multiplier
    default: 2

scenarios:
  - id: baseline
    inputs: {}
  - id: large
    inputs:
      scale: 5
      multiplier: 3

outputs:
  projected:
    source: projected
    formats:
      json: {}
      text: {}
  calculation:
    source:
      expression: "{'scale': scale, 'multiplier': multiplier.value}"
    formats:
      json:
        options:
          indent: 2
```

The plan schema is strict. Unknown fields fail validation. Names, options, defaults, and scenario values must be representable as JSON. Numbers must be finite, and integral values must fit the JavaScript safe integer range.

The TypeScript client performs structural wire preflight before a request. The Python producer remains authoritative for Python identifier and keyword validity, notebook definitions, import resolution, serializer availability, and exporter results.

## Inputs

`inputs` names the public controls stored in the export index. Each input binds to exactly one notebook target:

```yaml
inputs:
  width:
    definition: chart_width
    default: 640
  symbols:
    ui: symbol_picker
    default: [AAPL, MSFT]
```

| Binding      | Behavior                                                                                                                                           |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `definition` | Replaces the named Python definition before dependent cells execute. The target must be a Python identifier defined by the notebook.               |
| `ui`         | Runs the UI element's defining closure, then applies the JSON value through marimo's UI update path. The target must produce a marimo `UIElement`. |

Binding targets must be unique across the plan. A default may be any JSON value, including `null`. An input that lacks a default must appear in every scenario.

A definition input must target a regular notebook definition, not one owned by a setup cell. marimo prunes an overridden defining cell as a unit, so a cell that defines several names can be overridden only when the plan declares definition inputs for every definition from that cell. A UI binding must resolve to a marimo `UIElement`, and each scenario value must pass that element's conversion and update path. The producer checks these notebook-dependent contracts when it builds the scenario.

## Scenarios

Each scenario has a unique ID and an input object keyed by the public input names:

```yaml
scenarios:
  - id: baseline
    inputs: {}
  - id: compact
    inputs:
      width: 480
      symbols: [AAPL, NVDA]
```

The producer resolves defaults before execution and records the complete input object in the index. Unknown input names and missing required inputs fail validation.

Resolved input vectors must be unique. JSON number identity follows JavaScript behavior, so `1` and `1.0` identify the same input value while booleans remain distinct from numbers.

When `scenarios` is absent, the producer creates one `default` scenario with an empty input object. This default is valid when every declared input has a default.

Each scenario starts from a fresh deserialization of one saved-notebook snapshot. The producer deep-copies resolved inputs, inserts definition overrides, and prunes their defining cells. For UI inputs, it runs the UI creator cells and their ancestors, applies the requested values through marimo's update path, then settles every remaining valid authored cell through marimo's cache lifecycle. If authored execution recreates a bound UI element, the producer reapplies its requested value until the graph converges. A recreation cycle fails the scenario. The declared projections run as terminal cells after authored execution settles.

The fresh runner resets notebook graph state, globals, UI elements, and marimo state objects. Scenarios still share the attached kernel process, including imported modules, environment variables, files, random generators, native-library globals, and background tasks. Use separate producer processes when those resources also require isolation.

Matching authored cells may restore from cache, and any scheduled authored-cell failure can fail the scenario. Projection reachability does not narrow this authored execution set. Unsaved editor state is outside the build boundary. After scenario and payload verification, the producer rereads the saved file once before writing the index and requires its bytes to match the captured snapshot.

## Outputs and sources

`outputs` is a nonempty mapping. Each output selects one notebook result and declares one or more named formats.

Sources address values in the notebook graph. Assign a displayed or computed result to a named notebook definition, then select that definition in the plan. Use an expression when the published value is a derived combination of graph definitions.

### Definition source

A definition source is a Python identifier string:

```yaml
outputs:
  summary:
    source: summary
    formats:
      json: {}
```

Definition names must be Python identifiers.

### Expression source

An expression runs in the scenario's notebook scope:

```yaml
source:
  expression: "public_summary(frame)"
```

Expression sources execute Python in the producer environment. Accept plans from callers who are allowed to execute code in that notebook environment.

## Formats

`formats` is a mapping from a public lookup name to an exporter declaration. An empty object selects the built-in exporter with the same name:

```yaml
formats:
  json: {}
  text: {}
```

An explicit exporter lets the public name differ from the codec:

```yaml
formats:
  pretty:
    exporter: json
    options:
      indent: 2
      sort_keys: true
```

The format name is what TypeScript passes to `scenario.output(name, formatName)`. The exporter's `Projection.format_id` identifies the codec and selects an `OutputLoader`.

## Built-in exporters

| Exporter    | Format ID              | Media type                                     | Producer dependency        | Options                                                                    |
| ----------- | ---------------------- | ---------------------------------------------- | -------------------------- | -------------------------------------------------------------------------- |
| `json`      | `json.v1`              | `application/json`                             | Base                       | `indent`, default `null`. `sort_keys`, default `true`.                     |
| `text`      | `text.v1`              | `text/plain; charset=utf-8`                    | Base                       | None. Calls `str(value)`.                                                  |
| `html`      | `html.v1`              | `text/html; charset=utf-8`                     | Base                       | None. Converts with `marimo.as_html()` and embeds supported virtual media. |
| `bytes`     | `bytes.v1`             | `application/octet-stream`                     | Base                       | None. Accepts bytes, bytearray, or memoryview.                             |
| `arrow`     | `dataframe.arrow.v1`   | `application/vnd.apache.arrow.stream`          | `marimo-export[dataframe]` | None. Writes an Arrow IPC stream.                                          |
| `parquet`   | `dataframe.parquet.v1` | `application/vnd.apache.parquet`               | `marimo-export[dataframe]` | `compression`, default `NONE`.                                             |
| `vegalite`  | `vegalite.v1`          | Vega-Lite JSON                                 | Base                       | None. Accepts a JSON spec or an object with `to_dict()`.                   |
| `png`       | `vegalite.png.v1`      | `image/png`                                    | `marimo-export[png]`       | `scale`, default `1`, must be finite and positive.                         |
| `anywidget` | `anywidget.v1`         | `application/vnd.marimo-export.anywidget+json` | `marimo-export[anywidget]` | None. Accepts a raw AnyWidget or `mo.ui.anywidget(...)` value.             |

`json.indent` accepts `null` or a nonnegative safe integer. `json.sort_keys` accepts a boolean. The JSON exporter also converts dataclass instances and Pydantic models before enforcing JSON compatibility.

Arrow and Parquet accept a PyArrow table, a Narwhals-compatible eager dataframe, or a list of row mappings.

`parquet.compression` accepts `null` or a case-insensitive `NONE`, `SNAPPY`, `GZIP`, `BROTLI`, `LZ4`, or `ZSTD` value.

The Vega-Lite exporter derives `application/vnd.vegalite.vN+json` from the major version in an official `$schema` URL. A spec with `.../v6.1.0.json` uses `application/vnd.vegalite.v6+json`. Specs with an absent or unrecognized `$schema` use `application/vnd.vegalite+json`.

The HTML exporter publishes a standalone HTML fragment. It converts through `marimo.as_html()`, embeds virtual files referenced by `img`, `audio`, and `video` `src` attributes as data URLs, then rejects remaining `@file` references and `<marimo-...>` runtime elements. Ordinary static `Html` and `mo.md()` values work directly. Use Arrow or Parquet for dataframes, Vega-Lite or PNG for charts, and bytes or a custom `Projection` plus loader for other frontend contracts.

The AnyWidget exporter captures the selected root model, reachable child models, synchronized state, binary buffers, styles, module descriptors, and embedded module files. See [Publish AnyWidget outputs](./anywidget.md) for producer setup and Pythonless mounting.

Invalid built-in options fail plan validation. Options rejected by a custom exporter fail that scenario. Built-in format IDs, media types, and metadata follow the exporter contract.

## Custom projections

A custom exporter receives the selected Python value plus declared keyword options. It may be synchronous or awaitable and must return `marimo_export.Projection`:

```python
import json

from marimo_export import Projection


def export_summary(value, *, label: str) -> Projection:
    payload = json.dumps(
        {"label": label, "summary": value},
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return Projection(
        payload,
        format_id="project.summary.v1",
        media_type="application/vnd.project.summary+json",
        metadata={"label": label},
    )
```

Define the function in the notebook and reference the definition:

```yaml
outputs:
  summary:
    source: summary
    formats:
      card:
        exporter:
          definition: export_summary
          version: "1"
        options:
          label: Market summary
```

The `version` is optional for a notebook definition because marimo tracks the function's graph lineage. Add a version when the external serialization contract has its own revision.

An importable exporter uses `module:object` syntax and requires a version:

```yaml
formats:
  card:
    exporter:
      ref: project_exporters:export_summary
      version: "1"
    options:
      label: Market summary
```

The referenced package must be installed in the producer environment. The frontend pairs `project.summary.v1` with a custom `OutputLoader`. See [Custom loaders](./read-exports.md#custom-loaders).

Construct a projection with payload bytes and a codec identity:

```python
Projection(
    b'{"answer":42}',
    format_id="project.answer.v1",
    media_type="application/vnd.project.answer+json",
    metadata={"schema": 1},
)
```

`payload` must be bytes. `format_id` and `media_type` must be nonempty strings. `metadata` must be a JSON object with finite numbers and JavaScript-safe integral values.

## Cache identity

marimo-export creates one terminal synthetic marimo cell for each distinct projection-cell ABI, source, exporter declaration, and normalized exporter options object. marimo then includes tracked dependency state in that cell's native cache identity.

An AnyWidget projection also has an uncached preparation cell that evaluates the source and returns canonical graph bytes. The cacheable terminal cell depends on those bytes. A matching build can therefore restore the complete `Projection` while still preparing the current static graph for comparison.

The following changes prevent reuse of a previous projection computation:

- Source definition or expression.
- Projection-cell ABI.
- Importable exporter reference or version.
- Notebook exporter definition lineage or explicit version.
- Normalized exporter options. Equivalent built-in defaults, casing, and numeric forms share one identity.
- Canonical AnyWidget graph bytes.
- A tracked upstream dependency used by the source or exporter.

Scenario IDs, public input names, output names, format names, and plan ordering stay outside projection cache identity. Resolved input values still participate through the tracked dependency state they affect. The plan and index remain independently content-addressed from their complete normalized documents.

Producer builds require marimo's default `relaxed` execution type. marimo 0.23.14 uses the same native cell-cache identity for relaxed and strict execution, so the producer cannot safely admit strict execution or distinguish strict entries already stored in the notebook cache. When moving a notebook from strict execution to the producer, begin with a fresh `__marimo__/cache` directory.

Interactive execution can warm authored dependency cells when marimo computes the same native cache identity. Synthetic projections are created during export, so an equivalent earlier export projection warms them when the notebook has no user arguments. User arguments disable native cell caching because they are process state outside marimo's cache identity. Similar serialization code in an authored cell has a different cell identity.

Cells that can write marimo state use a conservative cache policy. A direct state setter is cache eligible when the same cell also directly references its paired state getter. Setter-only references and setters reached through a function, class, or wrapper run live. Getter-only cells retain normal cache eligibility.

A marimo `Html` value referenced directly or inside a dictionary, list, or tuple, including `mo.md()`, contributes its concrete type and portable rendered text to projection cache identity. When cached HTML refers to virtual media from an earlier child runner, the producer reruns the defining closure to recover the media before projection lookup. The rendered cache token contains the embedded media bytes, so unchanged HTML can restore its `Projection` while changed markup or media invalidates that entry.

Custom source objects retain marimo's native cache semantics. When marimo can represent an unpicklable source by execution identity, a warm terminal projection can restore the cached `Projection` without executing that source again. A miss still executes the source and exporter, and marimo determines cache eligibility for each object graph.

The complete `Projection` is saved through marimo's native lazy cache. Its portable payload is also stored under `marimo-export/payloads/sha256/<digest>`. If that payload object is missing, a cached `Projection` can recreate it during a later build.

## Exemplary plan

[`finance.plan.yaml`](https://github.com/marimo-team/marimo-export/blob/main/examples/_notebooks/finance.plan.yaml) documents every field beside a deterministic notebook. It publishes JSON, text, Arrow, Parquet, Vega-Lite, PNG, and HTML outputs.
