# @marimo-team/export-reader

Browser-side reader for finished marimo static export bundles.

The reader opens a hosted root, local directory, or in-memory archive. It selects
records by `scenario`, `value`, and authored `format` name. It never talks to a
marimo server and never executes Python.

## Open A Hosted Export

```ts
import { readLatestExport } from "@marimo-team/export-reader";
import { arrowLoader } from "@marimo-team/export-loader-arrow";

const exp = await readLatestExport({
  root: "/export/",
  loaders: [arrowLoader()],
});

const prices = await exp.get({ scenario: "default", value: "prices", format: "arrow" }).load();
```

`readLatestExport(...)` reads `index.json`, opens the latest manifest, and
validates the bundle shape before returning a `StaticExport`.

Use an explicit manifest when the caller already has one:

```ts
import { readExportManifest } from "@marimo-team/export-reader";

const exp = await readExportManifest({
  root: "/export/",
  manifest: "bundles/sha256-abc123/manifest.json",
});
```

## Open A Local Directory

```ts
import fs from "node:fs/promises";
import { readExportDirectory } from "@marimo-team/export-reader";

const exp = await readExportDirectory({
  root: "public/export",
  readFile: (file) => fs.readFile(file),
  url: (href) => `/export/${href}`,
});
```

`readFile` supplies bytes for `index.json`, manifests, and blobs. `url` is used by
`.url()` for browser APIs that need a direct URL.

## Open An Archive

```ts
import { readExportArchive } from "@marimo-team/export-reader";

const response = await fetch("/api/export");
const exp = await readExportArchive({ bytes: await response.arrayBuffer() });

try {
  const summary = await exp.get({ scenario: "default", value: "summary", format: "json" }).json();
} finally {
  exp.dispose();
}
```

Archive readers create object URLs for `.url()`. Call `dispose()` after the
caller finishes reading archive-backed formats.

## Raw Access And Loaders

Every selected format returns a handle:

```ts
const handle = exp.get({
  scenario: "default",
  value: "comparison_chart",
  format: "png_nogrid",
});

const entry = handle.entry();
const url = entry.url();
const bytes = await entry.bytes();
const metadata = handle.record.metadata;
```

`bytes()`, `text()`, `json()`, `fetch()`, and `load()` verify the selected blob's
size and SHA-256 digest before returning data. `url()` returns a direct bundle
URL without reading bytes, so callers that fetch it directly own integrity
checks.

Common JSON, text, and HTML formats can use built-in loaders:

```ts
import { htmlLoader, jsonLoader, readLatestExport } from "@marimo-team/export-reader";

const exp = await readLatestExport({
  root: "/export/",
  loaders: [jsonLoader("summary.json.v1"), htmlLoader("note.html.v1")],
});
```

Custom loaders match `FormatRecord.format_id`, the portable payload type
written by export. The authored `format` selector chooses the slot. `load()`
dispatches by `record.format_id`.

```ts
import { defineLoader } from "@marimo-team/export-reader";

const svgLoader = defineLoader({
  formatId: "playground.sparkline.svg.v1",
  async load(ctx) {
    return ctx.entry().text();
  },
});
```

## API Surface

### `readLatestExport(options)`

Opens the latest manifest from a hosted export root.

- `options.root`: Base URL for the export root.
- `options.index`: Root index href. Defaults to `index.json`.
- `options.loaders`: Format loaders used by `handle.load()`.
- `options.fetch`: Fetch implementation. Defaults to global `fetch`.

Returns `StaticExport`. Throws when the root index, manifest, or requested files
fail validation.

### `readExportManifest(options)`

Opens an explicit manifest from a hosted export root.

- `options.root`: Base URL for the export root.
- `options.manifest`: Manifest href.
- `options.loaders`: Format loaders used by `handle.load()`.
- `options.fetch`: Fetch implementation. Defaults to global `fetch`.

### `readExportDirectory(options)`

Opens the latest manifest or an explicit manifest from a local directory.

- `options.root`: Local export root path.
- `options.readFile`: Async file reader.
- `options.index`: Root index href. Defaults to `index.json`.
- `options.manifest`: Manifest href. When present, `readExportDirectory` skips the
  root index.
- `options.url`: Optional URL resolver for `.url()`.
- `options.loaders`: Format loaders used by `handle.load()`.

### `readExportArchive(options)`

Opens the latest manifest or an explicit manifest from zip bytes.

- `options.bytes`: Archive bytes as `ArrayBuffer`, `ArrayBufferView`, or `Blob`.
- `options.manifest`: Manifest href. Defaults to the latest bundle in
  `index.json`.
- `options.loaders`: Format loaders used by `handle.load()`.

### `defineLoader(loader)`

Returns a `FormatLoader`.

- `loader.formatId`: One `FormatRecord.format_id`. Mutually exclusive with
  `formatIds`.
- `loader.formatIds`: Accepted `format_id` values. Mutually exclusive with
  `formatId`.
- `loader.load(context)`: Loader function. It receives the selected format,
  selection metadata, `entry()`, and `file(key)`.

`handle.load()` calls the first loader whose format ids match
`handle.record.format_id`. It throws when no loader matches.

### `StaticExport`

- `exp.scenarios()`: Returns scenario ids in manifest order.
- `exp.scenario(id)`: Returns a scenario-bound reader with `id`, `state`,
  `record`, `values()`, `formats(value)`, and `get(value, format)`. Throws when
  `id` does not exist.
- `exp.scenarioRecords()`: Returns cloned scenario records.
- `exp.values()`: Returns exported value names.
- `exp.formats(value)`: Returns authored format names for one value. Throws
  when `value` does not exist.
- `exp.get({ scenario, value, format })`: Returns a `FormatHandle`.
  Throws when the scenario, value, or format does not exist.

### `FormatHandle`

- `handle.record`: The manifest `FormatRecord`.
- `handle.selection`: Scenario, value, and authored format selector.
- `handle.entry()`: Returns the canonical `FormatFile`.
- `handle.file(key)`: Returns an explicit `FormatFile`.
- `handle.url()`, `handle.fetch(init?)`, `handle.bytes()`, `handle.text()`, and
  `handle.json()`: Convenience methods for the canonical entry.
- `handle.load()`: Runs the registered loader matching `handle.record.format_id`.

### `FormatFile`

- `file.ref`: The `BlobRef` for this file.
- `file.url()`: Returns a direct URL without reading bytes.
- `file.fetch(init?)`, `file.bytes()`, `file.text()`, and `file.json()`: Read and
  verify the blob before returning data.
