# @marimo-team/export-reader

Browser-side reader for finished marimo static export bundles.

The reader opens a hosted root, local directory, or in-memory archive. It selects
artifacts by `scenario`, `value`, and authored `artifact` name. It never talks to
a marimo server and never executes Python.

## Open A Hosted Export

```ts
import { exportRoot, openExport } from "@marimo-team/export-reader";
import { arrowLoader } from "@marimo-team/export-loader-arrow";

const exp = await openExport(exportRoot("/export/"), {
  loaders: [arrowLoader()],
});

const prices = await exp
  .artifact({ scenario: "default", value: "prices", artifact: "arrow" })
  .load();
```

`openExport(exportRoot(...))` reads `index.json`, opens the latest manifest, and
validates the bundle shape before returning a `StaticExport`.

Use an explicit manifest when the caller already has one:

```ts
const exp = await openExport(
  exportRoot("/export/", {
    manifest: "bundles/sha256-abc123/manifest.json",
  }),
);
```

## Open A Local Directory

```ts
import fs from "node:fs/promises";
import { exportDirectory, openExport } from "@marimo-team/export-reader";

const exp = await openExport(
  exportDirectory("public/export", {
    readFile: (file) => fs.readFile(file),
    url: (href) => `/export/${href}`,
  }),
);
```

`readFile` supplies bytes for `index.json`, manifests, and blobs. `url` is used by
`.url()` for browser APIs that need a direct URL.

## Open An Archive

```ts
import { exportArchive, openExport } from "@marimo-team/export-reader";

const response = await fetch("/api/export");
const exp = await openExport(exportArchive(await response.arrayBuffer()));

try {
  const summary = await exp
    .artifact({ scenario: "default", value: "summary", artifact: "json" })
    .json();
} finally {
  exp.dispose();
}
```

Archive readers create object URLs for `.url()`. Call `dispose()` when the
archive reader is no longer needed.

## Raw Access And Loaders

Every selected artifact returns a handle:

```ts
const handle = exp.artifact({
  scenario: "default",
  value: "comparison_chart",
  artifact: "png_nogrid",
});

const entry = handle.entry();
const url = entry.url();
const bytes = await entry.bytes();
const metadata = handle.artifact.metadata;
```

`bytes()`, `text()`, `json()`, `fetch()`, and `load()` verify the selected blob's
size and SHA-256 digest before returning data. `url()` returns a direct bundle
URL without reading bytes, so callers that fetch it directly own integrity
checks.

Common JSON, text, and HTML artifacts can use built-in loaders:

```ts
import { htmlLoader, jsonLoader, openExport, exportRoot } from "@marimo-team/export-reader";

const exp = await openExport(exportRoot("/export/"), {
  loaders: [jsonLoader("summary.json.v1"), htmlLoader("note.html.v1")],
});
```

Custom loaders match `ArtifactRecord.format_id`, the portable payload type
written by capture. The authored `artifact` selector chooses the slot. `load()`
dispatches by `artifact.format_id`.

```ts
import { defineLoader } from "@marimo-team/export-reader";

const svgLoader = defineLoader({
  supports: "playground.sparkline.svg.v1",
  async load(ctx) {
    return ctx.entry().text();
  },
});
```

## API Surface

### `openExport(source, options?)`

Opens a static export from an `exportRoot(...)`, `exportDirectory(...)`, or
`exportArchive(...)` source.

- `source`: Source descriptor created by the matching helper.
- `options.loaders`: Artifact loaders used by `handle.load()`.

Returns `StaticExport` for roots and directories. Returns `StaticExportArchive`
for archives. Throws when the root index, manifest, archive, or requested files
fail validation.

### `exportRoot(root, options?)`

Creates a hosted export source.

- `root`: Base URL for the export root.
- `options.index`: Root index href. Defaults to `index.json`.
- `options.manifest`: Manifest href. When present, `openExport` skips the root
  index.
- `options.fetch`: Fetch implementation. Defaults to global `fetch`.

### `exportDirectory(root, options)`

Creates a local directory source.

- `root`: Local export root path.
- `options.readFile`: Async file reader.
- `options.index`: Root index href. Defaults to `index.json`.
- `options.manifest`: Manifest href. When present, `openExport` skips the root
  index.
- `options.url`: Optional URL resolver for `.url()`.

### `exportArchive(bytes, options?)`

Creates an archive source.

- `bytes`: Archive bytes as `ArrayBuffer`, `ArrayBufferView`, or `Blob`.
- `options.manifest`: Manifest href. Defaults to the latest bundle in
  `index.json`.

### `defineLoader(loader)`

Returns an `ArtifactLoader`.

- `loader.supports`: One `ArtifactRecord.format_id` or a list of accepted
  `format_id` values.
- `loader.load(context)`: Loader function. It receives the selected artifact,
  selection metadata, `entry()`, and `file(key)`.

`handle.load()` calls the first loader whose `supports` match
`artifact.format_id`. It throws when no loader matches.

### `StaticExport`

- `exp.scenarios()`: Returns scenario ids in manifest order.
- `exp.scenario(id)`: Returns one cloned scenario record. Throws when `id` does
  not exist.
- `exp.scenarioRecords()`: Returns cloned scenario records.
- `exp.values()`: Returns exported value names.
- `exp.artifacts(value)`: Returns authored artifact names for one value. Throws
  when `value` does not exist.
- `exp.artifact({ scenario, value, artifact })`: Returns an `ArtifactHandle`.
  Throws when the scenario, value, or artifact does not exist.

### `ArtifactHandle`

- `handle.artifact`: The manifest `ArtifactRecord`.
- `handle.selection`: Scenario, value, and authored artifact selector.
- `handle.entry()`: Returns the canonical `ArtifactFile`.
- `handle.file(key)`: Returns an explicit `ArtifactFile`.
- `handle.url()`, `handle.fetch(init?)`, `handle.bytes()`, `handle.text()`, and
  `handle.json()`: Convenience methods for the canonical entry.
- `handle.load()`: Runs the registered loader matching `artifact.format_id`.

### `ArtifactFile`

- `file.ref`: The `BlobRef` for this file.
- `file.url()`: Returns a direct URL without reading bytes.
- `file.fetch(init?)`, `file.bytes()`, `file.text()`, and `file.json()`: Read and
  verify the blob before returning data.
