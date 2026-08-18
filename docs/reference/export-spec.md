---
title: ExportSpec reference
description: Exact schema, value, state, output, and exporter contracts for authoring a notebook export.
---

# ExportSpec reference

An ExportSpec defines the prepared notebook states and named output
representations included in one notebook export.

```yaml
schema: marimo-export.spec.v1
inputs:
  - interval
  - symbols_selector
states:
  baseline: {}
  cloud:
    symbols_selector: [MSFT, GOOGL, AMZN]
  weekly:
    interval: 1wk
outputs:
  chart:
    source: performance
    exporter: altair.vegalite
  prices:
    source: selected_prices
    exporter:
      name: parquet.table
      options:
        compression: snappy
        filename: prices.parquet
```

## Root fields

| Field     | Contract                                                         |
| --------- | ---------------------------------------------------------------- |
| `schema`  | Exact value `marimo-export.spec.v1`                              |
| `inputs`  | Ordered unique notebook definition names                         |
| `states`  | Nonempty mapping from published state names to sparse input rows |
| `outputs` | Nonempty mapping from published output names to output specs     |

Unknown fields are invalid.

## Input names

Each input name must be a non-keyword Python identifier. The named definition
must exist in the inspected notebook and have one defining cell.

An input can identify:

- an ordinary Python definition
- a supported marimo UI element
- an AnyWidget whose synchronized state is portable JSON

Use `marimo-export session NOTEBOOK --json` or `Session.inspect()` to discover
definitions, input mode, current value, domain, portability, and sensitivity.

## State names and rows

State names are nonempty UTF-8 strings without surrounding whitespace or
control characters. A state row maps declared input names to portable values.

Rows may be sparse. The producer captures one baseline and fills each omitted
input from that baseline before execution. Every durable state contains the
complete input vector and its fingerprint.

Two state names cannot resolve to the same complete input vector.

## Portable input values

Input values support:

- `null`
- booleans
- strings containing Unicode scalar values
- integers in the JavaScript safe-integer range
- finite numbers in the same bounded range
- arrays of portable values
- objects with string keys and portable values

Input numbers cannot contain NaN, infinity, or negative infinity. Negative zero
normalizes to zero for state identity.

## UI values

Ordinary UI values use the complete frontend value accepted by the marimo
control. The producer applies the value through marimo, runs reactive
dependents, then reads the accepted value back.

An AnyWidget input uses a string-keyed object as a sparse trait patch. The
producer merges the patch over the complete baseline model and records the
serializer-owned result. A trait validator that changes the requested value
fails the state.

Binary AnyWidget state can be published as an output representation. It cannot
form part of an ExportSpec state vector because state vectors use portable JSON
values.

## Output specs

Each output maps one published name to:

| Field      | Contract                                                |
| ---------- | ------------------------------------------------------- |
| `source`   | Notebook definition name                                |
| `exporter` | Optional built-in or importable representation function |

Omitting `exporter` preserves a supported native marimo cache representation:

- scalar
- NumPy array
- Arrow table
- existing `BlobAsset`

Every state must produce every configured output. One output name keeps one
codec and media type across all states.

## Exporter forms

String form:

```yaml
exporter: altair.vegalite
```

Expanded form:

```yaml
exporter:
  name: parquet.table
  options:
    compression: snappy
    filename: prices.parquet
```

Custom form:

```yaml
exporter:
  name: market_summary:encode
  options:
    currency: USD
```

The custom callable receives the notebook result as its first argument and the
configured options as keyword arguments. Install or sideload its module into
the notebook environment before `build` or `capture`.

[Output representations](representations.md) lists built-in exporter names,
options, stored forms, consumer support, and optional dependencies.

## File input

`ExportSpec.from_file()` accepts UTF-8 `.json`, `.yaml`, and `.yml` files up to
16 MiB. JSON and YAML reject duplicate object keys. YAML aliases and merge keys
are invalid. YAML composition is bounded by depth and node count.

## Python construction

```python
from marimo_export import ExportSpec, OutputSpec
from marimo_export.exporters import altair, parquet

spec = ExportSpec(
    inputs=("symbols_selector",),
    states={
        "baseline": {},
        "cloud": {"symbols_selector": ["MSFT", "GOOGL", "AMZN"]},
    },
    outputs={
        "chart": OutputSpec(
            source="performance",
            exporter=altair.vegalite(),
        ),
        "prices": OutputSpec(
            source="selected_prices",
            exporter=parquet.table(filename="prices.parquet"),
        ),
    },
)
```

`ExportSpec.from_value()` validates a Python wire value. `to_value()` returns a
detached mutable wire value. `json_schema()` returns the Draft 2020-12
authoring schema.

## Errors

Invalid specs raise `SpecError` with a stable code and bounded details. Common
codes include:

- `spec_invalid`
- `spec_value_invalid`
- `spec_state_input_unknown`
- `spec_output_invalid`
- `spec_exporter_invalid`

[Choose states and results](../guide/choose-states.md) provides the task-shaped
authoring workflow.
