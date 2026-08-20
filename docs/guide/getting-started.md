---
title: Run the market dashboard
description: Build and open the live Yahoo Finance example from the repository checkout.
---

# Run the market dashboard

Precompute five result sets from a Yahoo Finance notebook, verify the
notebook export, then open the vanilla TypeScript dashboard that consumes it.

::: warning Live data
The export requests historical prices from Yahoo Finance. Network availability
and response data can affect the run.
:::

## Build and open the app

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

Open the URL printed by Vite. The five controls update the summary, chart,
table, and quote explorer from prepared notebook states. The browser runs no
Python process.

The example connects three authored sources:

- [`finance.py`](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/finance.py)
  owns the analysis and controls.
- [`finance.export.yaml`](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/finance.export.yaml)
  selects five states and five outputs.
- [`src/main.ts`](https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/src/main.ts)
  owns the browser layout and transitions.

The same notebook export can be inspected by an agent, opened from Python, or
loaded by another browser application. The dashboard is one consumer of the
precomputed results.

## Capture the open notebook

Start the notebook:

```bash
pnpm run notebook
```

After the notebook finishes loading, run from another terminal:

```bash
uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export inspect http://127.0.0.1:2718

pnpm run capture -- http://127.0.0.1:2718 --session SESSION_ID
```

Copy the session ID reported by `inspect` into the capture command. Capture
replaces the same local export and leaves the selected notebook session open.
The dashboard consumes the same `index.json` contract from either producer.

Next, [choose states and results](choose-states.md) for your own notebook or
[consume the existing export](consume-an-export.md) from another client.
