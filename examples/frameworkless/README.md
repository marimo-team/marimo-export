# Frameworkless examples

Single-file static apps over marimo export bundles. The apps have no Vite,
framework, bundler, or package-local install step.

Build package `dist/` files once:

```bash
pnpm --filter './packages/*' build
```

Serve the repository root so package `dist/` paths and bundle paths resolve:

```bash
npx -y serve .
```

Open:

```text
http://localhost:3000/examples/frameworkless/
```

## Apps

- `agentic-playground.html`: reads `notebooks/agentic_playground.py` exports.
  The spec uses inline Python exporters for JSON, SVG, and HTML formats, and
  the page uses local loaders defined in the HTML file.
- `queueing-lab.html`: reads `notebooks/queueing_lab.py` exports. It covers a
  scenario matrix, JSON and HTML formats, Arrow and Parquet dataframes, and
  Vega-Lite charts.
- `quadratic-program.html`: reads a notebook export captured from a
  quadratic-program tutorial. It mounts the exported `wigglystuff.Matrix`
  AnyWidget bundle and recomputes the 2D QP client-side.
- `server-archive.html`: builds a spec in browser JavaScript, asks a
  running marimo server to capture an archive, and opens the returned zip with
  `readExport(...)`.

The static reader pages use a CDN import map for third-party dependencies. That
keeps the examples browser-native while still reusing the local package
`dist/` entrypoints.

## Server Archive

Start the queueing notebook from the repository root:

```bash
uv run marimo edit notebooks/queueing_lab.py \
  --no-token \
  --no-skew-protection \
  --allow-origins http://localhost:3000 \
  --port 8383
```

Then open:

```text
http://localhost:3000/examples/frameworkless/server-archive.html
```

The page imports `@marimo-team/export-client/browser`, creates an `ExportSpec`
with `parseExportSpec(...)`, calls `client.archive(...)`, and reads the archive
with `@marimo-team/export-reader`.

## Regenerate Bundles

```bash
uv run marimo-export notebook notebooks/agentic_playground.py \
  --spec notebooks/export-specs/yaml/agentic-playground.yaml \
  --to examples/frameworkless/exports/agentic-playground

uv run marimo-export notebook notebooks/queueing_lab.py \
  --spec notebooks/export-specs/yaml/queueing-lab.yaml \
  --to examples/frameworkless/exports/queueing-lab
```

`quadratic-program.html` reads the checked-in quadratic export fixture under
`examples/frameworkless/exports/quadratic-program`.
