---
title: Choose states and outputs
description: Declare the finite notebook states and output representations available to every consumer.
---

# Choose states and outputs

An `ExportSpec` names a default state, sparse state rows, and the output
representation produced for each normalized state.

```yaml
schema: marimo-export.spec.v2
default_state: leaders
states:
  leaders: {}
  cloud:
    symbols_selector: [MSFT, GOOGL, AMZN]
  weekly:
    interval: 1wk
    symbols_selector: [AAPL, MSFT, GOOGL, AMZN]
outputs:
  summary:
    source: { kind: json, selector: report.summary }
  chart:
    source: { kind: export, selector: performance }
    exporter: altair.vegalite
  prices:
    source: { kind: export, selector: selected_prices }
    exporter: parquet.table
  report:
    source: { kind: output, selector: report.view }
```

Build the relation:

```bash
marimo-export plan finance.py --spec finance.export.yaml
marimo-export build finance.py \
  --spec finance.export.yaml \
  --output dist/finance
```

Every state exposes the same output names. Python and browser readers select
`leaders` when no other state is requested.

## Let planning infer inputs

The authored spec has no `inputs` field. marimo-export infers input definitions
from:

- portable stateful roots in the selected outputs' dependency closure
- definition names used as keys in state rows

Input names are notebook definition names. Use the UI element definition such as
`symbols_selector`, not its `.value` property.

Inspect available definitions and cells before authoring output selectors:

```bash
marimo-export inspect finance.py --json
```

::: warning File inspection executes the notebook
`inspect NOTEBOOK` performs the notebook's initial autorun with the current file,
credential, network, and package access.
:::

Planning rejects missing, sensitive, unavailable, and nonportable inputs. It also
rejects an ordinary input assigned by the defining cell's final named expression
because the authored assignment and selected state would compete for ownership.

## Write sparse named states

Each state row may omit inputs that should retain the captured baseline. The
producer records a complete input vector and SHA-256 fingerprint for each
distinct normalized row.

Equivalent rows become aliases of one state:

```yaml
default_state: current
states:
  baseline: {}
  current: {}
```

Both aliases select one prepared vector. The authored `default_state` remains
`current` in the `ExportPlan`, while the export index stores its resolved
fingerprint.

Portable state values support null, booleans, Unicode strings, JavaScript-safe
finite numbers, arrays, and string-keyed objects. Negative zero normalizes to
zero for state identity.

An AnyWidget input uses an object as a sparse trait patch. The producer merges
the patch over the baseline serializer-owned model state and verifies the
accepted complete value.

## Select output sources

Each output has one source kind:

| Source         | Stored result                                                 |
| -------------- | ------------------------------------------------------------- |
| `kind: json`   | Canonical portable JSON                                       |
| `kind: native` | Scalar, JSON, NumPy, Arrow, or BlobAsset cache representation |
| `kind: export` | BlobAsset returned by one declared exporter                   |
| `kind: output` | Formatted Marimo output and replay resources                  |
| `kind: cell`   | Cell identity, terminal output, console, and replay resources |

JSON, native, export, and output selectors accept a Python identifier root,
attribute steps, nonnegative integer items, and JSON-string items. Mapping keys
take precedence over attributes.

Select a complete cell by native name or an inspected runtime ID:

```yaml
outputs:
  summary_cell:
    source: { kind: cell, by: name, value: summary_cell }
```

## Choose representations for consumers

- portable JSON for summaries, records, and metrics
- rendered output or complete cells for Marimo-aware applications
- Parquet, Arrow, or NumPy for typed data and frontend computation
- Vega-Lite or PNG for visual presentation
- AnyWidget for browser-local model interaction
- a versioned `BlobAsset` media type for domain-specific data

Use expanded exporter form when options or source dependencies are required:

```yaml
outputs:
  prices:
    source: { kind: export, selector: selected_prices }
    exporter:
      name: parquet.table
      options:
        compression: snappy
        filename: prices.parquet
      dependencies: []
```

A custom exporter uses an importable `module:symbol` callable:

```yaml
outputs:
  summary:
    source: { kind: export, selector: report }
    exporter:
      name: market_summary:encode
      options:
        currency: USD
      dependencies:
        - market_summary.formatting
```

Declare dynamically loaded modules that affect conversion bytes. Custom
exporter leaves execute for each prepared state. Built-in deterministic leaves
can reuse Marimo's native cache.

Use [Output representations](../reference/representations.md) for exporter and
loader pairs and the [ExportSpec reference](../reference/export-spec.md) for the
exact wire schema.
