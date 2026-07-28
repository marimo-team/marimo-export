# Export specifications

An `ExportSpec` selects live notebook results, portable formats, and finite UI variants. CLI files use JSON or YAML with schema `marimo-export.spec.v1`.

```yaml
schema: marimo-export.spec.v1

variants:
  current: {}
  aapl:
    symbol_picker: [AAPL]

outputs:
  summary:
    source: summary
    formats:
      json: {}

  chart:
    source:
      expression: price_chart.properties(width=800)
    formats:
      vegalite: {}
      png:
        options:
          scale: 2
```

The schema is strict. Unknown fields, invalid names, nonfinite numbers, and integers outside the JavaScript safe range fail validation.

The JSON Schema covers document structure and portable lexical constraints. `ExportSpec` decoding remains authoritative for Python identifier and keyword rules and for built-in exporter option semantics.

Parse the same contract in Python:

```python
from marimo_export import ExportSpec

spec = ExportSpec.from_file("finance.export.yaml")
print(spec.wire())
```

`ExportSpec.from_value(value)` validates an existing mapping. `spec.wire()` returns the normalized JSON-compatible form used for capture.

## Variants

`variants` maps a public variant name to values for existing marimo UI controls:

```yaml
variants:
  current: {}
  compact:
    symbol_picker: [AAPL, NVDA]
    window_picker: 5
```

The keys inside a variant are live global names whose values are marimo UI elements. Values use the same frontend shape the control receives from the notebook UI.

Omitting `variants` creates one variant named `current`. An empty variant captures the starting UI vector unchanged.

Each declared variant resolves from the same starting vector. marimo applies its UI values as one batch and runs reactive dependents before source selection. A publication records the union of control names declared across its variants. When one variant omits a declared name, its record contains the starting value for that control.

Session inspection marks sensitive controls and redacts their values. A variant cannot target a sensitive control. Capture rejects that specification before applying any UI value.

Capture restores the starting controls after each variant and when projection fails. Restoring an input vector cannot undo external writes performed by notebook code. Use isolated data targets when variant execution can write to files, services, databases, or process-global state.

## Outputs

`outputs` maps each public output name to one source and one or more named formats:

```yaml
outputs:
  summary:
    source: summary
    formats:
      json: {}
      text: {}
```

The output name and format name are publication labels. They stay outside projection cache identity.

## Sources

### Named global

A source string selects a live global by name:

```yaml
source: summary
```

The global must exist in the running kernel when the variant settles.

### Expression

An expression evaluates trusted Python against the live globals:

```yaml
source:
  expression: price_chart.properties(width=800)
```

Expressions run with the notebook's packages, credentials, files, and process permissions. Accept specifications from callers who are allowed to execute Python in that environment.

### Rendered cell payload

A cell source selects the payload data from the current rendered output by cell ID or cell name:

```yaml
source:
  cell: market_note
```

When the selected cell reruns for a variant, capture uses the rendered payload observed from that rerun. Otherwise it uses the frozen payload visible when code mode attached. A custom import or variable exporter receives this payload data, not a marimo display record. Use a named global when an exporter needs the original Python object or a live AnyWidget model.

Capture preflights every named global and cell selector before it snapshots or changes UI controls. Expressions evaluate against the live globals after each variant settles.

## Built-in formats

An empty format object selects the built-in exporter with the same name:

```yaml
formats:
  json: {}
  parquet:
    options:
      compression: ZSTD
```

| Name        | Projection format      | Result                       |
| ----------- | ---------------------- | ---------------------------- |
| `json`      | `json.v1`              | UTF-8 JSON                   |
| `text`      | `text.v1`              | UTF-8 text                   |
| `bytes`     | `bytes.v1`             | Binary bytes                 |
| `html`      | `html.v1`              | Static HTML fragment         |
| `arrow`     | `dataframe.arrow.v1`   | Arrow IPC stream             |
| `parquet`   | `dataframe.parquet.v1` | Portable Parquet file        |
| `vegalite`  | `vegalite.v1`          | Vega-Lite JSON               |
| `png`       | `vegalite.png.v1`      | Rendered PNG                 |
| `anywidget` | `anywidget.v1`         | Static AnyWidget model graph |

Arrow and Parquet require the `dataframe` Python extra. PNG requires the `png` extra. AnyWidget requires the `anywidget` extra and its browser loader.

Browser consumers read Arrow and Parquet projections through `bytes()` or `blob()` for download or application-managed decoding. A custom browser loader can decode either representation for a trusted publication.

Built-in value conversion follows these contracts:

- `json` accepts shared JSON values, dataclass instances, and Pydantic models.
- `arrow` and `parquet` accept a PyArrow table, a list of row mappings, a value with `to_arrow()`, or an eager native table supported by Narwhals.
- `vegalite` and `png` call `to_dict()` when the selected value provides it.
- `html` renders through marimo, inlines portable image, audio, and video virtual files, and rejects unresolved virtual-file references or marimo runtime elements.
- `anywidget` requires a live AnyWidget model. Select it through a named global.

Three built-in exporters accept options:

| Exporter  | Option        | Contract                                                                     |
| --------- | ------------- | ---------------------------------------------------------------------------- |
| `json`    | `indent`      | `null` by default, or a non-negative JavaScript-safe integer                 |
| `json`    | `sort_keys`   | Boolean, default `true`                                                      |
| `parquet` | `compression` | Default `NONE`. Accepts `NONE`, `SNAPPY`, `GZIP`, `BROTLI`, `LZ4`, or `ZSTD` |
| `png`     | `scale`       | Finite positive number, default `1`                                          |

Parquet compression names are case-insensitive and normalize to uppercase. `null` compression normalizes to `NONE`. Integer PNG scales must stay within the JavaScript safe range. `text`, `bytes`, `html`, `arrow`, `vegalite`, and `anywidget` accept no option keys.

Options are part of the exporter contract. Built-in exporters normalize defaults before computing cache identity. Unknown options fail validation.

A format label can select a different built-in exporter explicitly:

```yaml
formats:
  thumbnail:
    exporter: png
    options:
      scale: 0.5
```

The publication label is `thumbnail`. Its stored format ID is `vegalite.png.v1`.

## Custom exporters

A custom exporter receives the selected live value plus keyword options and returns a `Projection`. For a cell source, the selected value is the rendered payload data described above. An asynchronous exporter may return an awaitable that resolves to the same value.

```python
import json

from marimo_export import Projection


def geojson(value: object) -> Projection:
    return Projection(
        json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        format_id="geojson.v1",
        media_type="application/geo+json",
        filename="regions.geojson",
        metadata={},
    )
```

Reference an installed exporter with an explicit version:

```yaml
formats:
  geojson:
    exporter:
      import: my_project.exports:geojson
      version: "1"
```

Reference a callable already present in the notebook globals with `variable`:

```yaml
formats:
  geojson:
    exporter:
      variable: geojson
      version: "1"
```

Import and variable exporters both require an explicit `version`. Change it when exporter behavior that is outside notebook value identity can change the projected bytes.

Capture resolves every exporter before applying a UI variant. A variable exporter is read once from the starting live globals and reused for every variant. Define the callable before capture begins. Variant reruns do not replace the resolved exporter.

`Projection` fields are:

| Field        | Contract                                                                                               |
| ------------ | ------------------------------------------------------------------------------------------------------ |
| `data`       | Portable bytes passed to the reader or loader                                                          |
| `format_id`  | Alphanumeric first, then alphanumeric, dot, underscore, plus, or hyphen, using at most 255 ASCII bytes |
| `media_type` | Printable ASCII type/subtype with optional parameters using at most 1,024 bytes                        |
| `filename`   | Optional portable base name using at most 255 UTF-8 bytes                                              |
| `metadata`   | JSON facts within 100,000 units, 256 nesting levels, and 262,144 canonical UTF-8 JSON bytes            |

The marimo adapter persists the `Projection` through marimo's cache and includes it in the publication.

## Projection cache identity

The persistent projector identity includes:

- Selected source value or its registered custom-stub bytes.
- Exporter callable identity.
- Exporter version.
- Normalized options.
- Projection application binary interface version.

Variant names, output names, format names, and specification ordering stay outside that identity.

When marimo cannot hash the selected value, marimo-export runs the exporter live. It then persists the resulting portable bytes through a cache call whose inputs are primitive values. The output remains publishable and reports cache reuse as `skipped`.
