# marimo static export

marimo static export captures selected values, display cells, and scenario
states from a marimo notebook and writes a static bundle that web apps can read
without Python.

The capture runtime records a manifest, content-addressed blobs, provenance, and
invocation traces. Browser code opens the bundle with
`@marimo-team/export-reader`, then loads only the JSON, Arrow, Parquet,
Vega-Lite, AnyWidget, HTML, Markdown, or custom formats it needs.

## Capture A Notebook

Start from an export spec. Each value names a notebook source and the formats to
materialize for each scenario:

```yaml
scenarios:
  - id: default
  - id: wide_chart
    state:
      chart_width: 1200

values:
  prices:
    source: { def: df }
    formats: [arrow, parquet]

  comparison_chart:
    source: { def: chart }
    formats: [vegalite, png]
```

Capture the notebook from this checkout:

```bash
uv run marimo-export notebook notebooks/finance.py \
  --spec notebooks/export-specs/yaml/finance--dashboard.yaml \
  --to notebooks/__marimo__/static-export
```

Inspect the finished bundle from Python:

```bash
uv run marimo-export query notebooks/__marimo__/static-export
uv run marimo-export query notebooks/__marimo__/static-export entries \
  --value summary \
  --format json \
  --content
```

The export root contains `index.json`, one or more `bundles/<id>/manifest.json`
files, invocation traces, and `blobs/sha256/...` payload files.

## Read A Bundle

Hosted bundles load through `readExport({ root })`:

```ts
import { readExport } from "@marimo-team/export-reader";
import { arrowLoader } from "@marimo-team/export-loader-arrow";

const exp = await readExport({ root: "/export/" });
const summary = await exp.get({ scenario: "default", value: "summary", format: "json" }).json();

const prices = await exp
  .get({ scenario: "default", value: "prices", format: "arrow" })
  .load(arrowLoader());
```

`bytes()`, `text()`, `json()`, `fetch()`, and loader-backed `load(...)` verify
the recorded size and SHA-256 digest before returning payload data. `url()`
returns the bundle URL directly for browser APIs that need a URL.

Archive-backed reads use the same API:

```ts
const response = await fetch("/api/export");
const exp = await readExport({ bytes: await response.arrayBuffer() });

try {
  const html = await exp.get({ scenario: "default", value: "report", format: "html" }).text();
} finally {
  exp.dispose();
}
```

Call `dispose()` when an archive reader has created object URLs.

## Capture From JavaScript

Use `@marimo-team/export-client` when JavaScript can reach a running marimo
server:

```ts
import { createMarimoExportClient } from "@marimo-team/export-client";

const client = createMarimoExportClient({ server: "http://localhost:2718" });

await client.export(spec, {
  notebook: "notebooks/finance.py",
  outputRoot: "examples/vanilla-vite/public/export",
  runtime: "preinstalled",
});
```

`client.export(...)` writes a persistent export root from the target kernel.
`client.archive(...)` captures into a temporary root and returns zip bytes for
API routes that stream a bundle to the caller.

Use `@marimo-team/export-client/browser` for frameworkless pages that need
plain `fetch` plus marimo HTTP and WebSocket endpoints. Use
`@marimo-team/export-client/workspace` for notebook discovery and source
previews from the marimo workspace API.

## Package Map

| Package                                | Runtime    | Contract                                                                                         |
| -------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------ |
| `moexport`                             | Python     | Captures notebook sources, applies exporters, writes bundles, archives, and queryable manifests. |
| `@marimo-team/export-client`           | TypeScript | Asks a running marimo server to write or archive a bundle.                                       |
| `@marimo-team/export-reader`           | TypeScript | Opens a hosted root, local directory, or archive and returns integrity-checked format handles.   |
| `@marimo-team/export-loader-arrow`     | TypeScript | Loads `dataframe.arrow.v1` payloads into `@uwdata/flechette` tables.                             |
| `@marimo-team/export-loader-parquet`   | TypeScript | Loads `dataframe.parquet.v1` payloads with `hyparquet`.                                          |
| `@marimo-team/export-loader-vegalite`  | TypeScript | Reads `vegalite.v1` specs and renders charts with `vega-embed`.                                  |
| `@marimo-team/export-loader-anywidget` | TypeScript | Hydrates `anywidget.bundle.v1` payloads without Python, Pyodide, or a marimo server.             |

## Spec Sources

Specs can select several notebook surfaces:

| Source                         | Captures                                                 |
| ------------------------------ | -------------------------------------------------------- |
| `{ def: df }`                  | A notebook definition by name.                           |
| `{ expr: "df.head(10)" }`      | A Python expression evaluated in the scenario.           |
| `{ cell: summary }`            | A marimo cell output by name, id, or index.              |
| `{ snapshot: true }`           | A linear notebook snapshot.                              |
| `{ report: { cells: [...] } }` | A selected display-cell report with labels and ordering. |

Scenario `state` keys override notebook definitions. Dotted keys patch object
attributes after producer cells run, for example `symbols_selector.value`.
Code-authored state values use `{ code: "..." }` and the evaluated value must be
JSON-compatible.

## Examples

- `examples/vanilla-vite`: browser SPA over the checked-in finance bundle.
- `examples/frameworkless`: single-file HTML apps that import local package
  `dist/` entrypoints and read static bundles directly.
- `examples/next-ssg`: static Next.js pages built from public bundles or
  archive capture during `next build`.
- `examples/astro-learn`: Astro SSG gallery built from marimo learn notebook
  metadata.
- `examples/self-contained`: Markdown export that writes `output.md` and
  `media/` for review workflows.
- `notebooks`: source notebooks and YAML or JSON specs used by the packages and
  examples.

## Development

Install workspace dependencies:

```bash
pnpm install
uv sync --all-extras
```

Run the package checks:

```bash
pnpm build
pnpm lint
pnpm typecheck
pnpm test
```

Before handoff in this checkout, run:

```bash
pnpm format
pnpm lint
pnpm typecheck
```
