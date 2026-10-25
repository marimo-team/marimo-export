---
title: StateSpace and ExportSpec reference
description: Exact schemas for reusable state spaces and complete export specifications.
---

# StateSpace and ExportSpec reference

## StateSpace

A `StateSpace` declares a finite set of notebook input assignments independently
of any output plan. Applications that infer outputs can load the state space and
compose its states with their own `OutputSpec` values.

```yaml
schema: marimo-export.states.v1
default_state: matrix-000000
states:
  current: {}
matrix:
  interval: [1d, 1wk]
  region: [All, Europe]
```

`states` contains named sparse input rows. `matrix` maps input names to nonempty
value arrays and expands their Cartesian product into `matrix-000000`,
`matrix-000001`, and subsequent states. Matrix input names are sorted before
expansion, while each value array retains its authored order. A state space can
contain explicit states, a matrix, or both. It supports at most 10,000 expanded
states.

Each matrix domain must contain distinct portable values. Explicit state names
that collide with generated `matrix-NNNNNN` names are invalid. `default_state`
must name either an explicit row or an expanded matrix row.

```python
from marimo_export import ExportSpec, OutputSpec, StateSpace

state_space = StateSpace.from_file("states.yaml")
spec = ExportSpec.from_state_space(
    state_space,
    outputs={"summary": OutputSpec.json("report.summary")},
)
```

`StateSpace.from_file()` accepts strict UTF-8 JSON or YAML.
`StateSpace.from_yaml()` accepts UTF-8 text or bytes already read by another
filesystem owner. `from_value()` validates a decoded object. `json_schema()`
returns the Draft 2020-12 authoring schema. `to_value()` returns normalized
explicit states after matrix expansion. `digest` identifies that normalized
state space.

Invalid documents and state-space values raise `SpecError`. Syntax and schema
failures use `spec_invalid`. Invalid rows, matrix domains, collisions, and
defaults use `spec_value_invalid`.

`ExportSpec.from_state_space(state_space, outputs=...)` combines the validated
state space with one application-owned output plan.

## ExportSpec

An ExportSpec defines the finite state-output relation to publish. Each named
output selects a notebook value and may name an exporter. Execution turns that
source and exporter into one stable output representation across all states.

```yaml
schema: marimo-export.spec.v2
default_state: baseline
states:
  baseline: {}
  cloud:
    symbols_selector: [MSFT, GOOGL, AMZN]
  weekly:
    interval: 1wk
outputs:
  summary:
    source: { kind: json, selector: report.summary }
  report:
    source: { kind: output, selector: report.view }
  summary_cell:
    source: { kind: cell, by: name, value: summary_cell }
  chart:
    source: { kind: export, selector: performance }
    exporter: altair.vegalite
```

## Root fields

The root contains exactly:

| Field           | Contract                                                    |
| --------------- | ----------------------------------------------------------- |
| `schema`        | Exact string `marimo-export.spec.v2`                        |
| `default_state` | Name of one entry in `states`                               |
| `states`        | Nonempty mapping from aliases to sparse portable input rows |
| `outputs`       | Nonempty mapping from published names to output specs       |

Unknown fields are invalid.

State and output names are nonempty Unicode scalar strings with no surrounding
whitespace or control characters and at most 255 UTF-8 bytes. State-row keys and
exporter option names are non-keyword Python identifiers of at most 255 UTF-8
bytes.

A value selector contains at most 4,096 UTF-8 bytes and 64 steps. Its root and
dot steps use ASCII identifier-shaped names, and dot steps cannot start with
`_`. Brackets accept a nonnegative integer up to 2\*\*53 - 1 or a JSON string
key. Selector parsing does not apply Python keyword rules.

## Inferred inputs

Planning derives input names from state-row keys and the canonical UI roots in
the selected outputs' dependency closures. An ordinary Python definition enters
the input set when a state row names it explicitly. Each inferred name must
identify one eligible notebook definition.

Eligible definitions include ordinary Python definitions, supported marimo UI
elements, and AnyWidget values with portable serializer-owned state. Planning
rejects missing, sensitive, unavailable, and nonportable definitions.

Use `uv run marimo-export inspect NOTEBOOK --json` or
`marimo_export.inspection.inspect_notebook()` to inspect definitions, cells,
input modes, current values, dependencies, portability, and sensitivity.

## State names and rows

State names are nonempty UTF-8 strings with at most 255 encoded bytes. They have
no surrounding whitespace or control characters.

Each row maps input definition names to portable values. Rows are sparse. The
producer fills omitted inputs from one captured baseline, then records the
complete vector and its SHA-256 fingerprint.

Rows that normalize to the same complete vector share one state fingerprint and
later reuse one prepared-state artifact. They retain every authored alias.
`default_state` retains the selected authored alias in `ExportPlan`. The export
index stores its resolved fingerprint.

## Portable state values

State values support:

- null
- booleans
- Unicode scalar strings
- integers in the JavaScript safe-integer range
- finite numbers in the same bounded range
- arrays of portable values
- objects with string keys and portable values

NaN and infinity are invalid state inputs. Negative zero normalizes to zero for
state identity.

An AnyWidget row uses an object as a sparse trait patch. The producer merges the
patch over baseline model state and verifies the serializer-owned accepted
value.

## Output specs

Each output contains `source`. An export source also contains `exporter`.

| Field       | Contract                                                                 |
| ----------- | ------------------------------------------------------------------------ |
| `source`    | One source object from the following sections                            |
| `exporter`  | Required for an export source and invalid for other source kinds         |
| `read_only` | Boolean, default `false`. Valid for `output` and `cell` sources. See [Read-only tables](#read-only-tables) |
| `optional`  | Boolean, default `false`. Valid for every source. See [Optional outputs](#optional-outputs) |

### JSON source

```yaml
source: { kind: json, selector: 'report.rows[0]["total"]' }
```

A JSON source stores one canonical portable value through `marimo.json.v1`.

### Native source

```yaml
source: { kind: native, selector: selected_prices }
```

A native source uses marimo's cache representation. Scalars remain inline.
Composite portable values use canonical JSON. NumPy arrays, Arrow tables, and
BlobAsset values retain their native verified payload. A pickle-backed value
fails preparation with a typed output error.

### Export source

```yaml
source: { kind: export, selector: performance }
exporter: altair.vegalite
```

An export source passes the selected value to one declared exporter. The
exporter returns a `BlobAsset` with bytes, media type, filename, and metadata,
or a JSON value that the export stores as canonical `marimo.json.v1` JSON.

### Rendered-output source

```yaml
source: { kind: output, selector: report.view }
```

A rendered-output source stores one canonical `marimo.output.v1` snapshot with
the formatted output, source cell identity, and replay resources.

### Complete-cell source

```yaml
source: { kind: cell, by: name, value: summary_cell }
```

Set `by` to `name` for an authored cell name or `id` for an inspected runtime cell
ID. A complete-cell source stores `marimo.cell.v1` with cell identity, config,
terminal output, console records, outcome, and replay resources.

### Read-only tables

```yaml
source: { kind: output, selector: sales }
read_only: true
```

A marimo table calls Python functions for search, paging, sorting, filtering,
column summaries, and downloads. `read_only: true` lets a rendered-output or
complete-cell source export `mo.ui.table` values and dataframes that marimo
displays as tables. The snapshot keeps the table's first page, column types,
and row count, and its `resources.functions` lists each function the table
cannot call. Build results report an `output_read_only` warning that names
those functions.

A `mo.ui.table` selected by its definition name is also an
[inferred input](#inferred-inputs) whose value is the row selection, `[]` by
default. A state row can declare another selection like any other input.

Without `read_only`, a table fails preparation with `output_not_portable`.
Lazy tables, such as `mo.ui.table.lazy()`, displayed Polars LazyFrames, and
lazy `mo.sql` results, load their first page through Python and fail with the
same code. So do `mo.ui.dataframe`, `mo.ui.chat`, `mo.ui.file_browser`, and
`mo.lazy`. Set `read_only` on JSON, native, or export sources and the spec
fails with `spec_output_invalid`.

JSON, native, export, and rendered-output selectors contain no whitespace
outside a JSON-string item and accept:

- one ASCII identifier root
- attribute steps such as `.summary`. Private names that start with `_` are
  invalid
- canonical nonnegative integer items such as `[0]` or `[10]`, up to
  2\*\*53 - 1. Signs, leading zeroes, and spaces are invalid
- JSON-string items such as `["total"]`

Mapping keys take precedence over attributes. Every normalized state must
produce every required output. One output name retains one codec and media type
across the states where it is available.

A selector contains at most 4,096 UTF-8 bytes and 64 steps. Invalid roots,
attribute steps, indexes, quoted keys, or trailing content raise `SpecError`
before notebook execution. A cell name or runtime ID contains at most 255 UTF-8
bytes. [`ValueSelector`](python/values#valueselector) parses and resolves the
same grammar for hosts that read live values.

### Optional outputs

```yaml
chart:
  source: { kind: export, selector: chart }
  exporter:
    name: media
    options: { accept: [image/png] }
    dependencies: []
  optional: true
```

An optional output may be unavailable in a state. The export records the
reason in that state's entry in place of a descriptor:

| Reason                       | Condition                                                                |
| ---------------------------- | ------------------------------------------------------------------------ |
| `representation_unavailable` | The exporter raised `marimo_export.values.RepresentationError`           |
| `value_missing`              | The selector names an attribute or item the selected value lacks         |
| `stopped`                    | A cell in the output's dependency closure called `mo.stop` in that state |

The built-in `media` exporter raises `RepresentationError` when the value has
none of the accepted media types, and `altair.png` raises it when vl-convert
cannot render the chart. A custom exporter can raise it for the same condition.
Two subclasses still fail the producer operation: `RepresentationTooLarge` for a
JSON form over 100,000 values and `RepresentationDependencyError` for a missing
renderer such as vl-convert-python. Every other failure fails it too, including
`altair.png` on a value that is not a chart.
A required output fails with code `output_execution_failed` under each of these
conditions.

Normalization writes `optional: true` and drops `optional: false`. Changing
`optional` changes the output-plan identity, so prepared states are prepared
again under the new plan.

## Exporter forms

Built-in shorthand:

```yaml
exporter: altair.vegalite
```

Expanded built-in:

```yaml
exporter:
  name: parquet.table
  options:
    compression: snappy
    filename: prices.parquet
  dependencies: []
```

Media in the consumer's preferred format:

```yaml
exporter:
  name: media
  options:
    accept: [application/pdf, image/svg+xml]
    scale: 1
    size: { width: 251.3, height: null }
  dependencies: []
```

`size` is optional. Its `width` and `height` are points, and at least one of
them is a number. A `null` or omitted length keeps a matplotlib figure's aspect
ratio and a Vega-Lite chart's own length in that direction. Compound Vega-Lite
charts keep their own sizes.

Custom callable:

```yaml
exporter:
  name: market_summary:encode
  options:
    currency: USD
  dependencies:
    - market_summary.formatting
```

The callable receives the selected value as its first argument and exporter
options as keyword arguments. `dependencies` contains sorted unique module names
whose code affects the returned bytes. Declare dynamically imported modules.

Each dependency is an importable dotted module name. A declaration contains at
most 256 dependencies. Exporter option keys are Python identifiers.

A borrowed session uses its loaded module objects. Restart the session after
changing an already imported exporter module. Source drift during preparation
raises a typed output error.

Exporter source and `dependencies` enter the marimo cache key of each executed
output. Prepared-state reuse follows the notebook's producer environment. Name
an exporter module that the notebook never imports in the notebook's
`[tool.marimo-export] imports` metadata, as described in
[Declare modules loaded by name](../concepts/preparation-and-reuse#declare-modules-loaded-by-name).

## File input

`ExportSpec.from_file()` accepts UTF-8 `.json`, `.yaml`, and `.yml` files up to
16 MiB. JSON and YAML reject duplicate keys. YAML aliases and merge keys are
invalid. YAML permits at most 256 container levels and 100,000 composed nodes.

## Python construction

```python
from marimo_export import ExportSpec, OutputSpec
from marimo_export.exporters import altair, media, parquet

spec = ExportSpec(
    default_state="baseline",
    states={
        "baseline": {},
        "cloud": {"symbols_selector": ["MSFT", "GOOGL", "AMZN"]},
    },
    outputs={
        "summary": OutputSpec.json("report.summary"),
        "table": OutputSpec.native("selected_prices"),
        "report": OutputSpec.output("report.view"),
        "summary_cell": OutputSpec.cell("summary_cell"),
        "sales": OutputSpec.output("sales", read_only=True),
        "chart": OutputSpec.export("performance", altair.vegalite()),
        "chart_image": OutputSpec.export(
            "performance",
            media(["image/png"]),
            optional=True,
        ),
        "prices": OutputSpec.export(
            "selected_prices",
            parquet.table(filename="prices.parquet"),
        ),
    },
)
```

Invalid specs raise `SpecError` with a stable code and portable details. [Choose
states and outputs](../guide/choose-states) provides the authoring workflow.

`ExportSpec.json_schema()` returns the Draft 2020-12 authoring schema as a
portable Python object. [Python production](python/produce) defines the
programmatic constructors and error boundary.
