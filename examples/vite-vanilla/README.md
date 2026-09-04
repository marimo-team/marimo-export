# Market dashboard with vanilla Vite

This example prepares five states and five outputs from a
[Yahoo Finance](https://finance.yahoo.com/) notebook, verifies the notebook export, and renders it as a
static browser dashboard. Python and agents can read the same `public/export`
directory.

[View the published dashboard](https://marimo-team.github.io/marimo-export/examples/market-dashboard/application/)
or follow the [complete example guide](../../docs/guide/market-dashboard.md) for
prerequisites and expected browser behavior.

## Build and open the dashboard

From the repository root, install the locked workspaces, build the export, verify
every declared asset, and start the [Vite](https://vite.dev/) development server:

```bash
make bootstrap
cd examples/vite-vanilla
pnpm run export
pnpm run verify:export
pnpm run dev
```

Preparation requests historical prices from Yahoo Finance. Network availability
and response data can affect the run.

After the export completes, `public/export/index.json` describes five states and
five named outputs. Open the URL printed by Vite. The saved-view buttons switch
the summary, chart, table, image, and quote explorer between exported states. The
browser runs no Python process.

A second matching export reuses the prepared export before notebook startup.
Edit one state row in `finance.export.yaml` to see planning prepare the new state
fingerprint while retaining matching states.

## Inspect the plan

```bash
uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export plan finance.py \
  --spec finance.export.yaml
```

The plan reports inferred inputs, five normalized states, the `baseline` default,
five outputs, observations, reusable states, and states that still need
preparation.

## Prepare from a live session

Start the notebook:

```bash
pnpm run notebook
```

After the notebook finishes loading, list its session from another terminal:

```bash
uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export inspect http://127.0.0.1:2718 --json
```

Copy the reported session ID into the capture command:

```bash
uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec finance.export.yaml \
  --output public/export \
  --replace \
  --jsonl
```

Capture replaces the local export and leaves the selected notebook session
active. Python callers can use `capture()` when they need to retain the leased
`PreparedExport` before writing or serving it.

## Inspect the authored files

- [`finance.py`](finance.py) contains the analysis, controls, chart, and widget.
- [`finance.export.yaml`](finance.export.yaml) declares the default, states,
  outputs, and representations.
- [`quote_detail.py`](quote_detail.py) owns the browser widget rendered by the
  notebook and application.
- [`src/main.ts`](src/main.ts) verifies the export, loads each representation,
  and owns browser transitions and mount disposal.

The [browser application guide](../../docs/guide/browser-applications.md) develops
the reader, state-transition, cancellation, and disposal contracts used here.
