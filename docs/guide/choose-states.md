---
title: Choose states and results
description: Choose the prepared notebook inputs and output representations available to people, agents, Python automation, and applications.
---

# Choose states and results

Choose the notebook inputs that should vary and the results each consumer
should receive for every prepared choice. An ExportSpec records those choices
and named outputs.

```yaml
schema: marimo-export.spec.v1
inputs:
  - interval
  - symbols_selector
states:
  leaders: {}
  cloud:
    symbols_selector: [MSFT, GOOGL, AMZN]
  weekly:
    interval: 1wk
    symbols_selector: [AAPL, MSFT, GOOGL, AMZN]
outputs:
  chart:
    source: performance
    exporter: altair.vegalite
  prices:
    source: selected_prices
    exporter: parquet.table
  explorer:
    source: quote_detail
    exporter: anywidget.bundle
```

Build the selected relation:

```bash
marimo-export build finance.py \
  --spec finance.export.yaml \
  --output dist/finance
```

Every consumer can now select `leaders`, `cloud`, or `weekly` and find `chart`,
`prices`, and `explorer` in each state.

## Name notebook inputs

`inputs` contains notebook definition names. An input can be an ordinary Python
definition or a marimo UI element. Use the UI element's definition name, not
its `.value` property.

Inspect a notebook before authoring the matrix:

```bash
marimo-export session finance.py --json
```

::: warning File inspection executes the notebook
`session NOTEBOOK` starts and executes the notebook with the current file,
credential, network, and package access.
:::

## Write sparse named states

Each state row may omit values that should remain at the captured baseline.
The producer records one complete input vector and fingerprint per state.

Input values support null, booleans, strings, finite numbers, arrays, and
string-keyed objects.

An AnyWidget input uses a string-keyed object as a sparse trait patch. The
producer merges the patch over the complete baseline state and rejects a trait
validator that changes the requested value. Session inspection reports
`input_mode: patch` for these definitions.

Binary AnyWidget state is available as an output representation. It cannot be
an input while ExportSpec state vectors use JSON values.

## Select published outputs

Each output maps one published name to a notebook definition. Omit `exporter`
for supported scalars, NumPy arrays, Arrow tables, and existing `BlobAsset`
values. Select an exporter when the notebook result needs another stored
representation.

Choose outputs for the consumers that need them:

- scalars or versioned JSON for concise human and agent summaries
- Parquet, Arrow, or NumPy for inspectable data and frontend computation
- Vega-Lite or images for visual presentation
- AnyWidget bundles for browser-local interaction
- custom media types for domain-specific agent or application data

Use the expanded form for exporter options:

```yaml
outputs:
  prices:
    source: selected_prices
    exporter:
      name: parquet.table
      options:
        compression: snappy
        filename: prices.parquet
```

Custom exporters use an importable `module:symbol` reference:

```yaml
outputs:
  summary:
    source: report
    exporter:
      name: market_summary:encode
      options:
        currency: USD
```

The callable receives the notebook result and returns a value supported by
marimo's native cache codecs. Install or sideload the module into the notebook
environment before `build` or `capture`.

[Output representations](../reference/representations.md) lists built-in
exporter, consumer, and loader pairs. The [ExportSpec
reference](../reference/export-spec.md) defines the exact schema. The [Python
API](../reference/python-api.md) shows programmatic construction.

## Coming from Papermill

[Papermill](https://papermill.readthedocs.io/en/latest/usage-parameterize.html)
runs one parameter set and saves an executed notebook. marimo-export prepares
several named states and saves selected results for multiple consumers.

Use `build` for a notebook file. Use `capture` for an open notebook session.
