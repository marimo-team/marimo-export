# ExportSpec

An ExportSpec has exactly four top-level fields:

```yaml
schema: marimo-export.spec.v1
inputs: []
states: {}
outputs: {}
```

## For Papermill users

[Papermill](https://papermill.readthedocs.io/en/latest/usage-parameterize.html)
treats values in a tagged `parameters` cell as defaults, injects overrides for
one execution, and writes the executed Jupyter notebook. marimo-export
addresses marimo definitions by name, normalizes multiple sparse state rows
against the live baseline, and publishes selected definitions.

| Workflow concept | Papermill                        | marimo-export                             |
| ---------------- | -------------------------------- | ----------------------------------------- |
| Defaults         | Tagged `parameters` cell         | Live value of each input definition       |
| Overrides        | Parameter dictionary or YAML     | Sparse map under each name in `states`    |
| Execution        | One notebook per API or CLI call | Every declared state per build or capture |
| Result           | Executed Jupyter notebook        | Static publication with named outputs     |

`build` is the closest lifecycle match. It starts the notebook, executes the
matrix, and shuts down its loopback server. `capture` keeps the same state and
output contract while attaching to a kernel that is already running.

Each normalized state follows normal marimo execution, so reactive dependency
ordering and cache restoration remain owned by marimo.

## Inputs

`inputs` lists notebook definition names. Any definition in the marimo graph
can participate when its baseline value and authored overrides fit portable
JSON. UI definitions use their frontend value.

```yaml
inputs:
  - symbols
  - interval
  - start
  - end
  - chart_width
  - symbols_selector
```

For an ordinary definition, marimo-export appends the state assignment to that
cell in the transient child document. The authored cell still creates its
siblings, including functions, classes, and UI elements. marimo then executes
the resulting graph normally. The source notebook remains byte-for-byte
unchanged.

## States

Each state is a sparse map from declared input names to override values:

```yaml
states:
  baseline: {}
  compact:
    chart_width: 480
  weekly:
    interval: 1wk
  focus:
    symbols_selector: [MSFT, GOOGL, AMZN]
```

The producer captures one baseline, fills every omitted input, and publishes
the complete vector. Equal complete vectors are rejected.

Portable input values are null, booleans, Unicode strings, finite ECMAScript
numbers, arrays, and string-keyed objects. Integral values stay inside the
JavaScript safe integer range.

## Outputs

Each public output names one notebook definition and an optional exporter:

```yaml
outputs:
  chart:
    source: performance
    exporter: altair.vegalite
  chart_image:
    source: performance
    exporter:
      name: altair.png
      options:
        scale: 2
  prices:
    source: selected_prices
    exporter:
      name: parquet.table
      options:
        compression: snappy
        filename: prices.parquet
  row_count:
    source: row_count
```

Omit `exporter` when the source already returns one supported native cache
value:

- scalar
- numeric NumPy array
- pandas, Polars, or Arrow table accepted by marimo's Arrow codec
- marimo `BlobAsset`

An exporter string selects a built-in with default options. The object form has
exactly `name` and `options`. Options use the same portable value grammar as
state overrides.

marimo-export generates a transient leaf that reads the state token and source,
imports the selected callable, and returns its result. marimo executes and
caches that leaf through its normal graph. The source notebook receives no
imports or publication cells.

Built-in names are:

- `altair.vegalite`
- `altair.png`
- `anywidget.bundle`
- `parquet.table`
- `blob.json`
- `blob.text`
- `blob.html`

### Custom exporter

Use `module:function` for an installed top-level callable:

```yaml
outputs:
  summary:
    source: report
    exporter:
      name: acme_exports:summary
      options:
        compact: true
```

The callable receives the source value as its first argument and exporter
options as keyword arguments:

```python
from marimo_export import BlobAsset


def summary(value: object, *, compact: bool) -> BlobAsset:
    ...
```

The module must be importable in the selected kernel. Capture can use a package
that was installed into the running environment before capture starts. Missing
modules, missing symbols, and non-callable symbols fail the publication.

## Programmatic construction

```python
from marimo_export import ExportSpec, OutputSpec
from marimo_export.exporters import altair, importable, parquet

spec = ExportSpec(
    inputs=("chart_width",),
    states={
        "baseline": {},
        "compact": {"chart_width": 480},
    },
    outputs={
        "chart": OutputSpec(
            source="performance",
            exporter=altair.vegalite(),
        ),
        "snapshot": OutputSpec(
            source="performance",
            exporter=altair.png(scale=2),
        ),
        "prices": OutputSpec(
            source="selected_prices",
            exporter=parquet.table(filename="prices.parquet"),
        ),
        "summary": OutputSpec(
            source="report",
            exporter=importable("acme_exports:summary", compact=True),
        ),
    },
)
```

Descriptor factories build spec values. Conversion runs later in the marimo
child.

`ExportSpec.to_value()` returns the wire object.
`ExportSpec.from_value()` validates a wire object.
`ExportSpec.from_file()` reads JSON or YAML.
`ExportSpec.json_schema()` generates an authoring schema on demand.
