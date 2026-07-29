# Vanilla Vite market dashboard

This example publishes a live Yahoo Finance notebook as a market dashboard
built with HTML, CSS, and TypeScript. Five saved views update the comparison
chart, latest-session table, quote explorer, and chart snapshot together.

## Publish the notebook

Install the repository workspaces:

```bash
make bootstrap
```

Run the notebook across the declared market views:

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run publish
```

The command requests historical prices from Yahoo Finance and writes the
publication to `examples/vite-vanilla/public/publication`. A successful run
reports:

```text
Published 5 states and 4 outputs to .../examples/vite-vanilla/public/publication
```

Verify the generated publication:

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla run verify:publication
```

## Open the dashboard

Start Vite:

```bash
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla dev
```

Open the local URL printed by Vite. Choose **Leaders**, **Cloud**,
**AI buildout**, **All names**, or **Weekly** to load the matching results.
The chart supports pointer inspection, and the quote detail control switches
among the companies in the current view.

## Example structure

| Path                  | Role                                                   |
| --------------------- | ------------------------------------------------------ |
| `finance.py`          | Yahoo Finance notebook and four representation cells   |
| `finance.export.yaml` | five saved market views and their published outputs    |
| `pyproject.toml`      | notebook dependencies from the local uv workspace      |
| `index.html`          | dashboard structure                                    |
| `src/main.ts`         | publication loading, view changes, and table rendering |
| `src/style.css`       | marimo-aligned dashboard layout                        |
| `public/publication`  | generated files served by Vite                         |
