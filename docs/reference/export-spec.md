---
title: ExportSpec reference
description: Exact version 2 schema for default state, sparse states, outputs, selectors, and exporters.
---

# ExportSpec reference

An ExportSpec defines the finite state relation and named output representations
prepared from one notebook.

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

## Inferred inputs

Planning derives input names from selected output dependencies and state-row
keys. Each inferred name must identify one eligible notebook definition.

Eligible definitions include ordinary Python definitions, supported Marimo UI
elements, and AnyWidget values with portable serializer-owned state. Planning
rejects missing, sensitive, unavailable, and nonportable definitions.

Use `marimo-export inspect NOTEBOOK --json` or
`marimo_export.inspection.inspect_notebook()` to inspect definitions, cells,
input modes, current values, dependencies, portability, and sensitivity.

## State names and rows

State names are nonempty UTF-8 strings with at most 255 encoded bytes. They have
no surrounding whitespace or control characters.

Each row maps input definition names to portable values. Rows are sparse. The
producer fills omitted inputs from one captured baseline, then records the
complete vector and its SHA-256 fingerprint.

Rows that normalize to the same complete vector share one prepared state and
retain every authored alias. `default_state` retains the selected authored alias
in `ExportPlan`. The export index stores its resolved fingerprint.

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

A native source uses Marimo's cache representation. Scalars remain inline.
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

Set `by` to `name` for a native cell name or `id` for an inspected runtime cell
ID. A complete-cell source stores `marimo.cell.v1` with cell identity, config,
terminal output, console records, outcome, and replay resources.

JSON, native, export, and rendered-output selectors accept:

- one Python identifier root
- attribute steps such as `.summary`
- nonnegative integer items such as `[0]`
- JSON-string items such as `["total"]`

Mapping keys take precedence over attributes. Every normalized state must
produce every configured output. One output name retains one codec and media
type across the relation.

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

A borrowed session uses its loaded module objects. Restart the session after
changing an already imported exporter module. Source drift during preparation
raises a typed output error.

## File input

`ExportSpec.from_file()` accepts UTF-8 `.json`, `.yaml`, and `.yml` files up to
16 MiB. JSON and YAML reject duplicate keys. YAML aliases and merge keys are
invalid. YAML composition is bounded by depth and node count.

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
states and outputs](../guide/choose-states.md) provides the authoring workflow.
