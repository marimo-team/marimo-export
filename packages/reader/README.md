# @marimo-team/export-reader

Browser-side reader for finished marimo static export bundles.

The reader opens `index.json` or `manifest.json`, selects artifacts by
`scenario`, `value`, and `format`, and exposes raw file helpers plus an optional
loader contract. It does not talk to a marimo server and does not execute
Python.

## Open A Hosted Bundle

```ts
import { readLatestExport } from "@marimo-team/export-reader";
import { arrowLoader } from "@marimo-team/export-loader-arrow";

const exp = await readLatestExport({
  root: "/export/",
  loaders: [arrowLoader()],
});

const handle = exp.get({
  scenario: "default",
  value: "prices",
  format: "arrow",
});

const prices = await handle.load();
```

Scenario records expose the resolved JSON state:

```ts
for (const scenario of exp.scenarioRecords()) {
  console.log(scenario.id, scenario.state);
}
```

Use `readExport` when the manifest path is known:

```ts
import { readExport } from "@marimo-team/export-reader";

const exp = await readExport({
  root: "/export/",
  manifest: "bundles/sha256-abc123/manifest.json",
});
```

Use `readLatestLocalExport` in Node build steps that read a bundle from disk:

```ts
import fs from "node:fs/promises";
import { readLatestLocalExport } from "@marimo-team/export-reader";

const exp = await readLatestLocalExport({
  root: "public/export",
  readFile: (file) => fs.readFile(file),
  url: (href) => `/export/${href}`,
});
```

## Open An Archive

```ts
import { readExportArchive } from "@marimo-team/export-reader";

const response = await fetch("/api/export");
const exp = await readExportArchive({
  bytes: await response.arrayBuffer(),
});

try {
  const handle = exp.get({
    scenario: "default",
    value: "summary",
    format: "json",
  });

  const summary = await handle.json();
} finally {
  exp.dispose();
}
```

`readExportArchive` uses object URLs for archive-backed `.url()` calls. Call
`dispose()` when the archive reader is no longer needed.

## Raw Access And Loaders

Every selected artifact returns a handle:

```ts
const handle = exp.get({
  scenario: "default",
  value: "comparison_chart",
  format: "png_nogrid",
});

const url = handle.url();
const bytes = await handle.bytes();
const metadata = handle.artifact.metadata;
```

`bytes()`, `text()`, `json()`, `fetch()`, and `load()` verify the selected
blob's size and SHA-256 digest before returning payload bytes. `url()` returns
a direct bundle URL for browser APIs that need one. Fetch that URL yourself
only when the caller accepts responsibility for integrity checks.

Common JSON, text, and HTML artifacts can use built-in loaders:

```ts
import { htmlLoader, jsonLoader, readLatestExport } from "@marimo-team/export-reader";

const exp = await readLatestExport({
  root: "/export/",
  loaders: [jsonLoader("summary.json.v1"), htmlLoader("note.html.v1")],
});
```

Custom loaders match `ArtifactRecord.format_id`, the static export type hint
written by capture. The selection `format` chooses the artifact slot, then
`load()` dispatches by `artifact.format_id`.

Custom loaders use `defineLoader`:

```ts
import { defineLoader } from "@marimo-team/export-reader";

const svgLoader = defineLoader({
  formats: "playground.sparkline.svg.v1",
  async load(ctx) {
    return ctx.text();
  },
});
```

## API Surface

### `readExportIndex(options)`

Fetches `options.index ?? "index.json"` from an export root and validates the
root index.

- `options.root`: Base URL for the export root.
- `options.index`: Root index href. Defaults to `index.json`.
- `options.fetch`: Fetch implementation. Defaults to global `fetch`.

Returns an `ExportRootIndex`. Throws when the file cannot be fetched or root
index validation fails.

### `readLatestExport(options)`

Opens `options.index ?? "index.json"`, validates the root index, and opens
`index.latest.manifest_href`.

- `options.root`: Base URL for the export root.
- `options.index`: Root index href. Defaults to `index.json`.
- `options.loaders`: Artifact loaders used by `handle.load()`.
- `options.fetch`: Fetch implementation. Defaults to global `fetch`.

Returns a `StaticExport`. Throws when the root index has no `latest` bundle or
when index or manifest validation fails.

### `readExport(options)`

Opens a specific manifest from an export root.

- `options.root`: Base URL for the export root.
- `options.manifest`: Bundle-relative manifest href.
- `options.loaders`: Artifact loaders used by `handle.load()`.
- `options.fetch`: Fetch implementation. Defaults to global `fetch`.

Returns a `StaticExport`. Throws when the manifest cannot be fetched or manifest
validation fails.

### `readLatestLocalExport(options)`

Opens a local `index.json` through a caller-provided file reader, then opens
`index.latest.manifest_href`.

- `options.root`: Local export root path.
- `options.index`: Root index href. Defaults to `index.json`.
- `options.readFile`: Async file reader for local paths.
- `options.url`: Optional URL resolver for `.url()`.
- `options.loaders`: Artifact loaders used by `handle.load()`.

Returns a `StaticExport`. Throws when the root index has no `latest` bundle or
when index or manifest validation fails.

### `readLocalExport(options)`

Opens a specific local manifest through a caller-provided file reader.

- `options.root`: Local export root path.
- `options.manifest`: Bundle-relative manifest href.
- `options.readFile`: Async file reader for local paths.
- `options.url`: Optional URL resolver for `.url()`.
- `options.loaders`: Artifact loaders used by `handle.load()`.

Returns a `StaticExport`. Throws when the manifest cannot be read or manifest
validation fails.

### `readExportArchive(options)`

Opens a zipped export root from bytes.

- `options.bytes`: Archive bytes as `ArrayBuffer`, `ArrayBufferView`, or `Blob`.
- `options.manifest`: Manifest href. Defaults to the latest bundle in
  `index.json`.
- `options.loaders`: Artifact loaders used by `handle.load()`.

Returns a `StaticExportArchive`. Call `dispose()` after using archive-backed
`.url()` values so object URLs are revoked.

### `defineLoader(loader)`

Returns an `ArtifactLoader`.

- `loader.formats`: One `ArtifactRecord.format_id` or a list of accepted
  `format_id` values.
- `loader.load(context)`: Loader function. It receives the selected artifact,
  selection metadata, file lookup, URL, fetch, bytes, text, and JSON helpers.

`handle.load()` calls the first loader whose `formats` match
`artifact.format_id`. It throws when no loader matches.

### `jsonLoader(formats)`, `textLoader(formats)`, and `htmlLoader(formats)`

Create loaders for small artifacts whose payload can be read through
`context.json()` or `context.text()`. `htmlLoader` is an alias for the text
loader.

### `StaticExport`

- `exp.scenarios()`: Returns scenario ids in manifest order.
- `exp.scenario(id)`: Returns one cloned scenario record. Throws when `id` does
  not exist.
- `exp.scenarioRecords()`: Returns cloned scenario records.
- `exp.values()`: Returns exported value names.
- `exp.formats(value)`: Returns format keys for one value. Throws when `value`
  does not exist.
- `exp.get({ scenario, value, format })`: Returns an `ArtifactHandle`. Throws
  when the scenario, value, or format does not exist.

### `ArtifactHandle`

- `handle.file(key?)`: Returns the selected `BlobRef`. Defaults to
  `artifact.data.entry`.
- `handle.url(key?)`: Returns the artifact URL without reading the blob.
- `handle.fetch(key?, init?)`: Fetches the blob and verifies size and SHA-256
  before returning a `Response`.
- `handle.bytes(key?)`, `handle.text(key?)`, and `handle.json(key?)`: Read and
  verify the blob before returning decoded data.
- `handle.load()`: Runs the registered loader matching `artifact.format_id`.
