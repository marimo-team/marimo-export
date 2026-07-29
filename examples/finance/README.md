# Finance publication

This example executes a real Yahoo Finance notebook across six declared input
states, publishes seven outputs, and loads them from a vanilla Vite and
TypeScript application after Python exits.

The notebook includes authored Exporter cells for AnyWidget, Vega-Lite, PNG,
and Parquet representations. Its native DataFrame, NumPy array, and scalar
outputs use marimo cache codecs directly.

## Build the publication

Install the repository workspaces from the repository root:

```bash
make bootstrap
```

Run the live notebook build:

```bash
pnpm --filter @marimo-team/marimo-export-example-finance run publish
```

The command queries Yahoo Finance and writes the static publication to
`examples/finance/public/publication`. A successful run reports:

```text
Published 6 states and 7 outputs to .../examples/finance/public/publication
```

Verify the publication independently:

```bash
pnpm --filter @marimo-team/marimo-export-example-finance run verify:publication
```

## Open the browser app

Start Vite:

```bash
pnpm --filter @marimo-team/marimo-export-example-finance dev
```

Open the local URL printed by Vite. The app verifies the publication, loads the
`baseline` state, and mounts:

- the interactive AnyWidget dashboard
- an interactive Vega-Lite chart
- the PNG rendering of the same chart
- Arrow and Parquet table summaries
- NumPy shape, type, and value-range details
- the scalar row count

Choose another state to dispose the current mounts and load the matching
precomputed assets. The unavailable-state action demonstrates exact finite
state lookup in the browser.

## Example structure

| Path                  | Contract                                                   |
| --------------------- | ---------------------------------------------------------- |
| `finance.py`          | marimo notebook plus authored Exporter cells               |
| `finance.export.yaml` | six sparse states and seven named outputs                  |
| `pyproject.toml`      | uv workspace dependencies, including local `marimo-export` |
| `index.html`          | application structure                                      |
| `src/main.ts`         | typed publication loading and mount lifecycle              |
| `src/style.css`       | marimo design tokens and responsive layout                 |
| `public/publication`  | generated static publication                               |
