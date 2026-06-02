# marimo static export workspace

This repository contains the capture runtime, TypeScript readers, modular
loaders, notebooks, and examples for marimo static export bundles.

The core flow is:

1. A marimo notebook runs in Python.
2. An export spec selects typed notebook sources and finite scenario states.
3. `moexport` evaluates those selections, turns Python objects into portable
   formats, and writes a static bundle.
4. Web code opens the bundle with `@marimo-team/export-reader` and loads only
   the formats it needs.

The finished site does not need a Python server, Pyodide, or a marimo runtime.

## Workspace

- `packages/capture`: Python package `moexport` and the `marimo-export` CLI.
  This is the only package that sees Python object handles.
- `packages/client`: TypeScript client for asking a running marimo server to
  produce a bundle from JavaScript.
- `packages/reader`: TypeScript reader for finished static bundles.
- `packages/loader-*`: optional web loaders for format families such as
  AnyWidget, Arrow, Parquet, and Vega-Lite.
- `notebooks`: source notebooks plus YAML and JSON export specs used by the
  examples.
- `examples`: framework and frameworkless apps that consume the exported
  bundles.
- `examples/self-contained`: a Markdown export example that turns one
  notebook into `output.md` plus a static `media/` directory for PR review.

## Capture A Notebook

Inspect the notebook first so the spec can refer to real defs and cell names:

```bash
uv run marimo-export inspect defs notebooks/finance.py
```

Write a static bundle:

```bash
uv run marimo-export notebook notebooks/finance.py \
  --spec notebooks/export-specs/yaml/finance--dashboard.yaml \
  --to notebooks/__marimo__/static-export
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
import { readExport } from "@marimo-team/export-reader";
import { arrowLoader } from "@marimo-team/export-loader-arrow";

const arrow = arrowLoader();
const exp = await readExport({ root: "/export/" });

const table = await exp.get({ scenario: "default", value: "prices", format: "arrow" }).load(arrow);
```

`@marimo-team/export-reader` also exposes raw `.url()`, `.bytes()`, `.text()`,
and `.json()` access for formats that do not need a loader. `.bytes()`,
`.text()`, `.json()`, `.fetch()`, and loader-backed `.load()` verify the
recorded size and SHA-256 digest before returning payload data. `.url()` returns
the bundle URL without reading the blob, so callers that fetch it directly own
integrity checks.

## Capture From JavaScript

Use `@marimo-team/export-client` when a build step or browser page can reach a
running marimo server:

```ts
import { createMarimoExportClient } from "@marimo-team/export-client";

const client = createMarimoExportClient({ server: "http://localhost:2718" });

await client.export(spec, {
  notebook: "notebooks/finance.py",
  outputRoot: "examples/vanilla-vite/public/export",
  runtime: "preinstalled",
});
```

Use `@marimo-team/export-client/browser` for frameworkless pages that need a
plain `fetch` implementation and do not import the generated marimo OpenAPI
client.

Use `@marimo-team/export-client/workspace` when a build step needs to list
running sessions, list workspace notebooks, or read notebook source from the
marimo workspace API.

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

### Browser Smoke Matrix

Run the browser smoke after `pnpm build`. Serve the repo root for
frameworkless pages:

```bash
python3 -m http.server 8799 --bind 127.0.0.1
```

Use one `agent-browser` session and assert each page through visible text plus
the no-horizontal-overflow predicate:

```bash
agent-browser --session marimo-export-smoke open http://127.0.0.1:8799/examples/frameworkless/index.html
agent-browser --session marimo-export-smoke wait --text "Single-file apps over static bundles"
agent-browser --session marimo-export-smoke wait --fn "document.documentElement.scrollWidth <= document.documentElement.clientWidth"

agent-browser --session marimo-export-smoke open http://127.0.0.1:8799/examples/frameworkless/agentic-playground.html
agent-browser --session marimo-export-smoke wait --text "Agentic export playground"
agent-browser --session marimo-export-smoke wait --fn "document.querySelectorAll('button').length >= 3 && document.documentElement.scrollWidth <= document.documentElement.clientWidth"

agent-browser --session marimo-export-smoke open http://127.0.0.1:8799/examples/frameworkless/queueing-lab.html
agent-browser --session marimo-export-smoke wait --text "Queueing lab"
agent-browser --session marimo-export-smoke wait --fn "document.querySelectorAll('img').length >= 2 && document.documentElement.scrollWidth <= document.documentElement.clientWidth"

agent-browser --session marimo-export-smoke open http://127.0.0.1:8799/examples/frameworkless/quadratic-program.html
agent-browser --session marimo-export-smoke wait --text "Quadratic program, from the bowl up"
agent-browser --session marimo-export-smoke wait --fn "document.querySelectorAll('button').length >= 5 && document.documentElement.scrollWidth <= document.documentElement.clientWidth"
```

For `server-archive.html`, run the queueing notebook on port `8383`, open the
page above from the same root server, click **Capture archive**, then assert
`Captured`, `pooled_rush`, `3`, and `9` appear in the summary tables.

For built examples, preview each app and run the same session:

```bash
pnpm --filter @marimo-team/export-example-vanilla build
pnpm --filter @marimo-team/export-example-vanilla preview
agent-browser --session marimo-export-smoke open http://localhost:4173
agent-browser --session marimo-export-smoke wait --text "Market monitor"

pnpm --filter @marimo-team/export-example-astro-learn build
pnpm --filter @marimo-team/export-example-astro-learn preview
agent-browser --session marimo-export-smoke open http://127.0.0.1:4175
agent-browser --session marimo-export-smoke wait --text "Notebook gallery"

pnpm --filter @marimo-team/export-example-next-ssg build
python3 -m http.server 5187 --bind 127.0.0.1 --directory examples/next-ssg/out
agent-browser --session marimo-export-smoke open http://127.0.0.1:5187/compare/crwv-msft/
agent-browser --session marimo-export-smoke wait --text "CoreWeave vs Microsoft"
```

For the self-contained example, render the Quarto output and assert the report
loads without broken images:

```bash
cd examples/self-contained/finance
quarto render output.md --output output.html
python3 -m http.server 5188 --bind 127.0.0.1
agent-browser --session marimo-export-smoke open http://127.0.0.1:5188/output.html
agent-browser --session marimo-export-smoke wait --text "Finance notebook static review"
agent-browser --session marimo-export-smoke wait --fn "Array.from(document.images).every((img) => img.complete && img.naturalWidth > 0)"
```

When the local metrics use cases exist under `nogit/use-cases`, run each
`run.py --strict`, open its `output/index.html` and `output/reader-check.html`,
and assert `Weekly Metrics Readout`, `Reader Check`, counters `27`, `19`, `19`,
and `0`, plus the labels `Rendered items`, `Image assets`, `Spec assets`, and
`Diagnostics`.
