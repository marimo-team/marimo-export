# Choose states and results

An ExportSpec names the notebook choices your audience can make and the results
your web app can load.

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

```bash
marimo-export build finance.py \
  --spec finance.export.yaml \
  --output dist/finance
```

The browser can now select `leaders`, `cloud`, or `weekly` and load the same
three output names from each state.

## Fields

| Field     | Meaning                                                         |
| --------- | --------------------------------------------------------------- |
| `inputs`  | Notebook definitions that may change                            |
| `states`  | Named input values available to the app                         |
| `outputs` | Browser-facing names mapped to notebook definitions and formats |

State rows may be sparse. Missing values come from the notebook state captured
at the start of the export. Input values support null, booleans, strings,
finite numbers, arrays, and string-keyed objects.

`source` names a notebook definition. `exporter` converts its result into a
browser format. Scalars, NumPy arrays, Arrow tables, and existing `BlobAsset`
values can keep their native form.

[Choose exporters and browser loaders](representations.md).

## Exporter options

Use the expanded form when an exporter accepts options:

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

Custom exporters live in a regular Python module beside the notebook:

```yaml
outputs:
  summary:
    source: report
    exporter:
      name: market_summary:encode
      options:
        currency: USD
```

The function receives the notebook result and returns the chosen browser
representation. Install or sideload its module into the notebook environment
before `build` or `capture`.

Construct the same model programmatically with
[`ExportSpec` and `OutputSpec`](python-api.md#construct-a-spec).

## Coming from Papermill

[Papermill](https://papermill.readthedocs.io/en/latest/usage-parameterize.html)
runs one parameter set and saves an executed notebook. marimo-export prepares
several named states and saves selected results for a browser app.

Use `build` for a notebook file. Use `capture` for an open notebook.
