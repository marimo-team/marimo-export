# @marimo-team/export-client

TypeScript client for asking a running marimo server to produce an export
bundle.

Use this package when JavaScript has network access to a live marimo session.
It sends capture code through marimo's scratchpad API and returns either a
written bundle result or an in-memory archive. It does not read finished
bundles.

## Server Client

```ts
import { captureExport, createCaptureClient } from "@marimo-team/export-client";

const client = createCaptureClient({
  server: "http://localhost:2718",
});

await captureExport(spec, {
  client,
  notebook: "notebooks/finance.py",
  bundle: "examples/vanilla-vite/public/export",
});
```

If `notebook` is provided and no matching session is running, the client opens a
marimo websocket session for that notebook before dispatching capture code.
The promise resolves after `moexport` writes the bundle and returns manifest and
invocation paths for the completed export.

By default, capture checks whether `moexport` is importable in the target kernel
and asks marimo to install the runtime if needed:

```txt
moexport @ https://files.peter.gy/pkg/py/moexport/moexport-0.1.0-py3-none-any.whl
```

Override or disable the runtime install per request:

```ts
await captureExport(spec, {
  client,
  notebook: "notebooks/finance.py",
  runtime: {
    package: "moexport @ https://example.com/moexport.whl",
    force: true,
    manager: "uv",
  },
});

await captureExport(spec, {
  client,
  notebook: "notebooks/finance.py",
  runtime: false,
});
```

## Archive Capture

```ts
import { captureExportArchive } from "@marimo-team/export-client";
import { readExportArchive } from "@marimo-team/export-reader";

const archive = await captureExportArchive(spec, {
  client,
  sessionId: "session-id",
  executionTimeoutMs: 60_000,
});

const exp = await readExportArchive({ bytes: archive.bytes });
```

## Browser Entry

Frameworkless pages can import the browser-native subpath. It avoids the
generated OpenAPI dependency and uses plain `fetch` plus the marimo
HTTP/WebSocket endpoints used for capture.

```ts
import {
  captureExportArchive,
  createBrowserCaptureClient,
} from "@marimo-team/export-client/browser";

const client = createBrowserCaptureClient({
  server: "http://localhost:2718",
});

const archive = await captureExportArchive(spec, {
  client,
  notebook: "notebooks/queueing_lab.py",
});
```

## Mechanics

- `createCaptureClient(...)` wraps the generated `@marimo-team/marimo-api`
  client and adds scratchpad and notebook-opening helpers.
- `createBrowserCaptureClient(...)` provides the same capture shape with plain
  browser transport.
- `captureExport(...)` writes a bundle on the Python side.
- `captureExportArchive(...)` returns zip bytes produced by
  `mox.archive_bundle(...)`.
- `{ client }` injection keeps client construction separate from capture
  semantics.
- `tsc` emits declarations, and `esbuild.config.mjs` emits the ESM JavaScript
  entrypoints listed in `package.json` exports.
- `capture-core` contains environment-neutral capture orchestration.
- `transport` contains marimo HTTP/WebSocket transport details.

Bundle reading is handled by `@marimo-team/export-reader`.
