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

Definitions returned by one cell form a sibling packet. Overriding one sibling
keeps the other baseline siblings available to downstream cells.

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

Each public output names one notebook definition:

```yaml
outputs:
  chart:
    source: chart_asset
  prices:
    source: df
  row_count:
    source: row_count
```

The source returns one supported native cache value:

- scalar
- numeric NumPy array
- pandas, Polars, or Arrow table accepted by marimo's Arrow codec
- marimo `BlobAsset`

Use an authored Exporter cell to convert another object family.

## Programmatic construction

```python
from marimo_export import ExportSpec, OutputSpec

spec = ExportSpec(
    inputs=("chart_width",),
    states={
        "baseline": {},
        "compact": {"chart_width": 480},
    },
    outputs={
        "chart": OutputSpec(source="chart_asset"),
    },
)
```

`ExportSpec.to_value()` returns the wire object.
`ExportSpec.from_value()` validates a wire object.
`ExportSpec.from_file()` reads JSON or YAML.
`ExportSpec.json_schema()` generates an authoring schema on demand.
