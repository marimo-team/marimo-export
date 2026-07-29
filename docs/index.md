# Publish marimo results for a Python-free client

marimo-export runs the notebook states you choose while Python is available,
then writes a static publication for a browser app. Deploy `index.json`, its
content-addressed assets, and your client to any static HTTP host.

The browser selects a published state and loads requested outputs through typed
loaders. Python has already finished. Client-side controls can switch among
precomputed states or interact with a published chart, image, table, array, or
AnyWidget.

[Publish a notebook from the wild](getting-started.md)

## Parameterize, execute, publish

[Papermill](https://github.com/nteract/papermill) describes a familiar Jupyter
workflow: parameterize a notebook, execute it, and save the executed notebook.
marimo-export follows that sequence for marimo and adds a publication step for
client applications:

1. **Parameterize:** `inputs` names marimo definitions and `states` supplies
   sparse override rows.
2. **Execute:** `build` starts the notebook, or `capture` attaches to a live
   kernel, and each state runs through marimo.
3. **Publish:** the chosen `outputs` become `index.json` entries and
   content-addressed assets.

Papermill writes an executed notebook for each run. marimo-export writes one
static publication containing the declared finite state matrix.

[Compare Papermill and ExportSpec](export-spec.md#for-papermill-users)

## A live notebook and a complete browser app

The checked-in
[vanilla Vite example](https://github.com/marimo-team/marimo-export/tree/main/examples/vite-vanilla)
queries Yahoo Finance, executes five saved market views, and publishes four
outputs. Its ExportSpec varies an interval definition and a marimo UI
definition:

<<< ../examples/vite-vanilla/finance.export.yaml

Run the notebook through the uv workspace:

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run publish
```

The resulting `public/publication` directory contains one canonical
`index.json` plus content-addressed Parquet, PNG, Vega-Lite, and AnyWidget
assets. The adjacent Vite app turns them into a comparison chart, latest-close
table, quote explorer, and chart snapshot.

The [getting-started guide](getting-started.md) covers the live build,
verification command, browser application, and state transitions.

## Build from a file or capture a live kernel

Use `build` when marimo-export should start the notebook, execute the declared
matrix, and stop its loopback server:

```bash
uv run --package marimo-export-vite-vanilla-example marimo-export build \
  examples/vite-vanilla/finance.py \
  --spec examples/vite-vanilla/finance.export.yaml \
  --output examples/vite-vanilla/public/publication
```

Use `capture` when a live kernel already holds credentials, configured
services, or expensive results:

```bash
uv run marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec examples/vite-vanilla/finance.export.yaml \
  --output publication
```

Both commands execute each state through marimo and read the resulting native
cache receipts. See the [command-line interface](cli.md) for session discovery,
credentials, replacement behavior, JSON output, and exit categories.

## Publish native values or authored representations

Scalar values, numeric NumPy arrays, and supported tables use marimo's native
cache codecs. Exporter functions convert object families such as Altair charts
and AnyWidgets into a versioned `BlobAsset` inside an ordinary notebook cell.

| Notebook value   | Browser entry point                           |
| ---------------- | --------------------------------------------- |
| Scalar           | `@marimo-team/marimo-export`                  |
| NumPy array      | `@marimo-team/marimo-export/loader/numpy`     |
| Arrow table      | `@marimo-team/marimo-export/loader/arrow`     |
| Parquet file     | `@marimo-team/marimo-export/loader/parquet`   |
| Vega-Lite chart  | `@marimo-team/marimo-export/loader/vegalite`  |
| PNG image        | `@marimo-team/marimo-export`                  |
| AnyWidget bundle | `@marimo-team/marimo-export/loader/anywidget` |

Continue with [ExportSpec](export-spec.md) for the input matrix or
[representations](representations.md) for exporter and loader contracts.
