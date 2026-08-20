# Market dashboard with vanilla Vite

This example prepares five states from a Yahoo Finance notebook, verifies the
export, and renders it as a static browser dashboard. Python and agents can read
the same `public/export` directory.

## Build and run

From the repository root:

```bash
make bootstrap
cd examples/vite-vanilla
pnpm run export
pnpm run verify:export
pnpm run dev
```

Open the URL printed by Vite. The dashboard switches between five prepared
states and loads Parquet rows, a domain summary, Vega-Lite, PNG, and AnyWidget
representations. Yahoo Finance availability affects the preparation run.

The matching second export reuses its prepared generation before notebook
startup. Edit one state row in `finance.export.yaml` to see planning prepare the
new fingerprint while retaining matching states.

## Inspect the plan

```bash
uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export plan finance.py \
  --spec finance.export.yaml
```

The plan reports inferred inputs, five normalized states, the `baseline`
default, output count, observations, reusable states, and states to prepare.

## Prepare from a live session

Start the notebook:

```bash
pnpm run notebook
```

List its live session and write the captured export:

```bash
uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export inspect http://127.0.0.1:2718 --json

uv run --locked --package marimo-export-vite-vanilla-example \
  marimo-export capture http://127.0.0.1:2718 \
  --session SESSION_ID \
  --spec finance.export.yaml \
  --output public/export \
  --replace \
  --jsonl
```

The selected session remains active. Python callers can use `capture()` directly
when they need to retain the `PreparedExport` before writing or serving it.

## Files

- [`finance.py`](finance.py) contains the analysis.
- [`finance.export.yaml`](finance.export.yaml) declares the default, states, and
  outputs.
- [`src/main.ts`](src/main.ts) loads and renders the verified export.
