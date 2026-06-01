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

Common JSON, text, and HTML artifacts can use built-in loaders:

```ts
import { htmlLoader, jsonLoader, readLatestExport } from "@marimo-team/export-reader";

const exp = await readLatestExport({
  root: "/export/",
  loaders: [jsonLoader("summary.json.v1"), htmlLoader("note.html.v1")],
});
```

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

- `readExportIndex({ root })`: fetch the root `index.json`.
- `readLatestExport({ root, loaders })`: open `index.latest.manifest_href`.
- `readExport({ root, manifest, loaders })`: open a specific manifest.
- `readLatestLocalExport({ root, readFile, loaders })`: open a local
  `index.json` through a caller-provided file reader.
- `readLocalExport({ root, manifest, readFile, loaders })`: open a specific
  local manifest through a caller-provided file reader.
- `readExportArchive({ bytes, loaders })`: open a zipped export root.
- `defineLoader(loader)`: create an artifact loader.
- `jsonLoader`, `textLoader`, `htmlLoader`: generic loaders for small
  artifacts.
