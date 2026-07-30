# Market dashboard with vanilla Vite

This example exports five views from a live Yahoo Finance notebook into a
static dashboard.

## Run

```bash
make bootstrap
cd examples/vite-vanilla
pnpm run export
pnpm run verify:export
pnpm run dev
```

Open the URL printed by Vite. The export writes `public/export` and depends on
Yahoo Finance availability.

## Capture

```bash
pnpm run notebook
```

After the notebook loads, run:

```bash
pnpm run capture -- http://127.0.0.1:2718
```

The session remains open.

## Files

- [`finance.py`](finance.py) contains the analysis.
- [`finance.export.yaml`](finance.export.yaml) selects states and results.
- [`src/main.ts`](src/main.ts) renders the dashboard.
