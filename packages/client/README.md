# @marimo-team/export-client

TypeScript client for asking a running marimo server to produce a static export.

Use this package when JavaScript can reach a live marimo session. It sends
capture code through marimo's scratchpad API and returns either a written export
result or an in-memory archive. Finished bundle reading belongs to
`@marimo-team/export-reader`.

## Capture Through The Server Client

```ts
import { createExportClient } from "@marimo-team/export-client";

const client = createExportClient({
  server: "http://localhost:2718",
});

const result = await client.capture(spec, {
  notebook: "notebooks/finance.py",
  to: "examples/vanilla-vite/public/export",
  runtime: "preinstalled",
});
```

If `notebook` is provided and no matching session is running, the client opens a
marimo websocket session for that notebook before dispatching capture code. The
promise resolves after `moexport` writes the export root and returns manifest and
invocation paths.

## Runtime Installation

`runtime: "preinstalled"` checks that `moexport` is importable in the target
kernel and fails with a clear error when it is missing.

Install explicitly when the caller owns the package source:

```ts
await client.capture(spec, {
  notebook: "notebooks/finance.py",
  runtime: {
    install: "moexport[all]",
    force: true,
    manager: "uv",
  },
});
```

`install` is passed to marimo's package installer. `module` defaults to
`"moexport"` for the post-install import check.

## Archive Capture

```ts
import { createExportClient } from "@marimo-team/export-client";
import { exportArchive, openExport } from "@marimo-team/export-reader";

const client = createExportClient({ server: "http://localhost:2718" });
const archive = await client.captureArchive(spec, {
  sessionId: "session-id",
  executionTimeoutMs: 60_000,
});

const exp = await openExport(exportArchive(archive.bytes));
```

Archive capture returns zip bytes with media type
`application/vnd.marimo.static-export+zip`.

## Browser Entry

Frameworkless pages can import the browser-native subpath. It avoids the
generated OpenAPI dependency and uses plain `fetch` plus marimo HTTP/WebSocket
endpoints.

```ts
import { createExportClient } from "@marimo-team/export-client/browser";

const client = createExportClient({
  server: "http://localhost:2718",
});

const archive = await client.captureArchive(spec, {
  notebook: "notebooks/queueing_lab.py",
  runtime: "preinstalled",
});
```

## Convenience Functions

`captureExport(spec, options)` and `captureExportArchive(spec, options)` remain
for short scripts. Production code should create one `ExportClient` and call
`client.capture(...)` or `client.captureArchive(...)` so session listing,
runtime checks, and raw marimo access live behind one object.

## API Surface

### `createExportClient(options)`

Creates an `ExportClient`.

- `options.server`: marimo server URL.
- `options.fetch`: Fetch implementation. Defaults to global `fetch`.
- `options.headers`, `options.token`, and `options.serverToken`: Request
  authentication inputs passed to marimo endpoints.
- `options.WebSocket`: WebSocket constructor used when opening a notebook.

### `client.capture(spec, options?)`

Writes a static export root through the target marimo session.

- `spec`: `ExportSpecInput` with `scenarios`, `values`, and optional
  `provenance`.
- `options.notebook`: Notebook path or name to open or match.
- `options.sessionId`: Existing marimo session id. Takes precedence over
  `notebook`.
- `options.to`: Output root path seen by the Python kernel.
- `options.runtime`: `"preinstalled"` or an explicit install request.
- `options.executionTimeoutMs`: Scratchpad execution timeout.

Returns bundle path, manifest path, invocation trace paths, manifest JSON,
invocation JSON, and the resolved session.

### `client.captureArchive(spec, options?)`

Runs the same capture and returns zipped export bytes instead of relying on a
public output directory.

### `client.listSessions()`

Returns running marimo sessions from `/api/home/running_notebooks`.

### `client.listWorkspaceNotebooks()`

Returns marimo notebook files from the workspace file API.

### `client.marimo`

Raw marimo transport used by the high-level methods. Use it only for endpoints
outside the export client contract.
