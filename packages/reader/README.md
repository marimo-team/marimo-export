# @marimo-team/export-reader

Browser-side reader for finished marimo static export bundles.

The reader opens a hosted root, local directory, or in-memory archive. It selects
records by `scenario`, `value`, and authored `format` name. It never talks to a
marimo server and never executes Python.

## Installation

```bash
npm install @marimo-team/export-reader
```

## Open A Hosted Export

```ts
import { readExport } from "@marimo-team/export-reader";
import { arrowLoader } from "@marimo-team/export-loader-arrow";

const arrow = arrowLoader();
const exp = await readExport({ root: "/export/" });

const prices = await exp.get({ scenario: "default", value: "prices", format: "arrow" }).load(arrow);
```

`readExport(...)` reads `index.json`, opens the latest manifest, and validates
the bundle shape before returning an `Export`.

## Open A Local Directory

```ts
import fs from "node:fs/promises";
import { readExport } from "@marimo-team/export-reader";

const exp = await readExport({
  root: "public/export",
  readFile: (file) => fs.readFile(file),
  url: (href) => `/export/${href}`,
});
```

`readFile` supplies bytes for `index.json`, manifests, and blobs. `url` is used by
`.url()` for browser APIs that need a direct URL.

## Open An Archive

```ts
import { readExport } from "@marimo-team/export-reader";

const response = await fetch("/api/export");
const exp = await readExport({ bytes: await response.arrayBuffer() });

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
const metadata = handle.metadata;
```

`bytes()`, `text()`, `json()`, `fetch()`, and `load(loader)` verify the selected
blob's size and SHA-256 digest before returning data. `url()` returns a direct
bundle URL without reading bytes, so callers that fetch it directly own
integrity checks.

Read JSON, text, and HTML formats directly from the selected entry:

```ts
import { readExport } from "@marimo-team/export-reader";

const exp = await readExport({ root: "/export/" });
const summary = await exp.get({ scenario: "default", value: "summary", format: "json" }).json();
const noteHtml = await exp.get({ scenario: "default", value: "note", format: "html" }).text();
```

Custom loaders match the portable `format_id` written by export. The authored
`format` selector chooses the slot.
`handle.load(loader)` validates `loader.formatId` or `loader.formatIds` against
`handle.formatId` before running the loader.

```ts
const svgLoader = {
  formatId: "playground.sparkline.svg.v1",
  async load(ctx) {
    return ctx.entry().text();
  },
};
```

## API Surface

### `readExport(options)`

Opens a hosted root, local directory, or archive.

- `options.root`: Base URL for the export root.
- `options.root` with `options.readFile`: Local export root path.
- `options.bytes`: Archive bytes as `ArrayBuffer`, `ArrayBufferView`, or `Blob`.
- `options.readFile`: Async file reader for local roots.
- `options.url`: Optional URL resolver for local `.url()` results.
- `options.fetch`: Fetch implementation. Defaults to global `fetch`.

Returns an `Export` for hosted and local roots. Returns an `ExportArchive` for
archives, which also has `dispose()`. Throws when the root index, manifest, or
requested files fail validation. Hosted roots, local directories, and archives
open the latest bundle from `index.json`.

### Loader Objects

- `loader.formatId`: One format identifier, such as `dataframe.arrow.v1`.
  Mutually exclusive with `formatIds`.
- `loader.formatIds`: Accepted `format_id` values. Mutually exclusive with
  `formatId`.
- `loader.load(context)`: Loader function. It receives the selected format,
  selection metadata, `entry()`, and `file(key)`.

Pass the loader to `handle.load(loader)`. It throws when the loader does not
match `handle.formatId`.

### `Export`

- `exp.id`: Bundle id from the latest opened manifest.
- `exp.notebook`: Notebook name and source SHA-256 from the opened bundle.
- `exp.sourceSpecSha256`: Source spec SHA-256 when the bundle includes it.
- `exp.raw.manifest`: Cloned manifest JSON for advanced inspection.
- `exp.scenarios()`: Returns scenario ids in manifest order.
- `exp.scenario(id)`: Returns a scenario-bound reader with `id`, `state`,
  `values()`, `formats(value)`, and `get(value, format)`. Throws when `id` does
  not exist.
- `exp.values()`: Returns exported value names.
- `exp.formats(value)`: Returns authored format names for one value. Throws
  when `value` does not exist.
- `exp.get({ scenario, value, format })`: Returns an `ExportEntry`.
  Throws when the scenario, value, or format does not exist.

### `ExportEntry`

- `handle.selection`: Scenario, value, and authored format selector.
- `handle.formatId`: Portable format id written by the exporter.
- `handle.mediaType`: Media type for the canonical entry.
- `handle.metadata`: Cloned metadata for the selected format.
- `handle.raw.record`: Cloned selected format record for advanced inspection.
- `handle.entry()`: Returns the canonical `ExportFile`.
- `handle.files()`: Returns file keys available on the selected format.
- `handle.file(key)`: Returns an explicit `ExportFile`.
- `handle.url()`, `handle.fetch(init?)`, `handle.bytes()`, `handle.text()`, and
  `handle.json()`: Convenience methods for the canonical entry.
- `handle.load(loader)`: Runs a loader whose format ids match
  `handle.formatId`.

### `ExportFile`

- `file.ref`: The content-addressed blob reference for this file.
- `file.url()`: Returns a direct URL without reading bytes.
- `file.fetch(init?)`, `file.bytes()`, `file.text()`, and `file.json()`: Read and
  verify the blob before returning data.
