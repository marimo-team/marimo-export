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

A value selector contains at most 2,048 UTF-8 bytes. Its root and dot steps use
ASCII identifier-shaped names. Brackets accept a nonnegative integer or a JSON
string key. Selector parsing does not apply Python keyword rules.

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
exporter returns a `BlobAsset` with bytes, media type, filename, and metadata.

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

JSON, native, export, and rendered-output selectors contain no whitespace
outside a JSON-string item and accept:

- one Python identifier root
- attribute steps such as `.summary`
- canonical nonnegative integer items such as `[0]` or `[10]`. Signs, leading
  zeroes, and spaces are invalid
- JSON-string items such as `["total"]`

Mapping keys take precedence over attributes. Every normalized state must
produce every configured output. One output name retains one codec and media
type across the relation.

A selector contains at most 2,048 UTF-8 bytes. Invalid roots, attribute steps,
indexes, quoted keys, or trailing content raise `SpecError` before notebook
execution. A cell name or runtime ID contains at most 255 UTF-8 bytes.

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

## File input

`ExportSpec.from_file()` accepts UTF-8 `.json`, `.yaml`, and `.yml` files up to
16 MiB. JSON and YAML reject duplicate keys. YAML aliases and merge keys are
invalid. YAML permits at most 256 container levels and 100,000 composed nodes.

## Python construction

```python
from marimo_export import ExportSpec, OutputSpec
from marimo_export.exporters import altair, parquet

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
        "chart": OutputSpec.export("performance", altair.vegalite()),
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
