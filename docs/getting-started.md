# Run the market dashboard

This example turns a Yahoo Finance notebook into a static dashboard with five
interactive market views.

::: warning Live data
The export requests historical prices from Yahoo Finance. Network availability
and response data can affect the run.
:::

## Build and open it

Install Git, uv, Node 22.18 or newer, and pnpm 11.15.1.

```bash
git clone https://github.com/marimo-team/marimo-export.git
cd marimo-export
make bootstrap
cd examples/vite-vanilla
pnpm run export
pnpm run verify:export
pnpm run dev
```

Open the URL printed by Vite. Switching views updates the summary, chart,
table, and quote explorer from one prepared notebook state.

The complete path is
[`finance.py`](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/finance.py),
[`finance.export.yaml`](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/finance.export.yaml),
and
[`src/main.ts`](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/src/main.ts).

## Capture the open notebook

Start the notebook:

```bash
pnpm run notebook
```

After it finishes loading, run from another terminal:

```bash
pnpm run capture -- http://127.0.0.1:2718
```

Capture updates the same export and leaves the notebook open.

Next, [choose states and results](export-spec.md) for your own notebook.
