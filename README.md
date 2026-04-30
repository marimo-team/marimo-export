# marimo static export workspace

This repository contains the capture runtime, TypeScript readers, modular
loaders, notebooks, and examples for marimo static export bundles.

The core flow is:

1. A marimo notebook runs in Python.
2. An export spec selects notebook expressions and finite scenario states.
3. `moexport` evaluates those selections, turns Python objects into portable
   artifacts, and writes a static bundle.
4. Web code opens the bundle with `@marimo-team/export-reader` and loads only
   the artifacts it needs.

The finished site does not need a Python server, Pyodide, or a marimo runtime.

## Workspace

- `packages/capture`: Python package `moexport` and the `marimo-export` CLI.
  This is the only package that sees Python object handles.
- `packages/client`: TypeScript client for asking a running marimo server to
  produce a bundle from JavaScript.
- `packages/reader`: TypeScript reader for finished static bundles.
- `packages/loader-*`: optional web loaders for artifact families such as
  AnyWidget, Arrow, Parquet, and Vega-Lite.
- `notebooks`: source notebooks plus YAML and JSON export specs used by the
  examples.
- `examples`: framework and frameworkless apps that consume the exported
  bundles.

## Capture A Notebook

Inspect the notebook first so the spec can refer to real defs and cell names:

```bash
uv run marimo-export inspect defs notebooks/finance.py
```

Write a static bundle:

```bash
uv run marimo-export notebook notebooks/finance.py \
  --spec notebooks/export-specs/yaml/finance--dashboard.yaml \
  --bundle notebooks/__marimo__/static-export
```

Query the result without a web runtime:

```bash
uv run marimo-export query notebooks/__marimo__/static-export
uv run marimo-export query notebooks/__marimo__/static-export scenarios
uv run marimo-export query notebooks/__marimo__/static-export entries \
  --value summary \
  --format json \
  --content
```

## Read In The Browser

```ts
import { readLatestExport } from "@marimo-team/export-reader";
import { arrowLoader } from "@marimo-team/export-loader-arrow";

const exp = await readLatestExport({
  root: "/export/",
  loaders: [arrowLoader()],
});

const table = await exp.get({ scenario: "default", value: "prices", format: "arrow" }).load();
```

`@marimo-team/export-reader` also exposes raw `.url()`, `.bytes()`, `.text()`,
and `.json()` access for artifacts that do not need a loader.

## Capture From JavaScript

Use `@marimo-team/export-client` when a build step or browser page can reach a
running marimo server:

```ts
import { captureExport, createCaptureClient } from "@marimo-team/export-client";

const client = createCaptureClient({ server: "http://localhost:2718" });

await captureExport(spec, {
  client,
  notebook: "notebooks/finance.py",
  bundle: "examples/vanilla-vite/public/export",
});
```

Use `@marimo-team/export-client/browser` for frameworkless pages that need a
plain `fetch` implementation instead of the generated marimo OpenAPI client.

## Development

Install dependencies with pnpm and run package commands through the workspace:

```bash
pnpm install
pnpm build
pnpm lint
pnpm typecheck
```

Before handoff, this workspace expects:

```bash
pnpm format
pnpm lint
pnpm typecheck
```
