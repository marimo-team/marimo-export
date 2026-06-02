# @marimo-team/export-client

TypeScript client for asking a running marimo server to produce a static export.

Use this package when JavaScript can reach a running marimo session. It sends
export code through marimo's scratchpad API and returns either a written export
result or an in-memory archive. Finished bundle reading belongs to
`@marimo-team/export-reader`.

## Installation

```bash
npm install @marimo-team/export-client
```

## Export Through The Server Client

```ts
import { createMarimoExportClient } from "@marimo-team/export-client";

const client = createMarimoExportClient({
  server: "http://localhost:2718",
});

const spec = {
  values: {
    summary: {
      source: { def: "summary" },
      formats: ["json"],
    },
  },
};

const result = await client.export(spec, {
  notebook: "notebooks/finance.py",
  outputRoot: "examples/vanilla-vite/public/export",
  paths: ["./local-exporters"],
  runtime: "preinstalled",
});
```

If `notebook` is provided and no matching session is running, the client opens a
marimo websocket session for that notebook before dispatching export code. The
promise resolves after `moexport` writes the export root and returns manifest and
invocation paths.

`paths` prepends directories to the kernel `sys.path` before export execution.
Pass it when a spec references local exporter modules.

## Runtime Installation

`runtime: "preinstalled"` checks that `moexport` is importable in the target
kernel and fails with a clear error when it is missing.

Install explicitly when the caller owns the package source:

```ts
await client.export(spec, {
  notebook: "notebooks/finance.py",
  runtime: {
    package: "moexport[all]",
    force: true,
    manager: "uv",
    source: "kernel",
    timeoutMs: 120_000,
    pollIntervalMs: 1_000,
  },
});
```

The install request is a `Runtime`.

`package` is passed to marimo's package installer. `module` defaults to
`"moexport"` for the post-install import check. `manager` and `source` are
forwarded to marimo's installer. `timeoutMs` and `pollIntervalMs` control the
post-install import probe.

## Archive Export

```ts
import { createMarimoExportClient } from "@marimo-team/export-client";
import { readExport } from "@marimo-team/export-reader";

const client = createMarimoExportClient({ server: "http://localhost:2718" });
const spec = {
  values: {
    summary: {
      source: { def: "summary" },
      formats: ["json"],
    },
  },
};

const archive = await client.archive(spec, {
  sessionId: "session-id",
  timeoutMs: 60_000,
});

const exp = await readExport({ bytes: archive.bytes });
```

Archive export returns zip bytes with media type
`application/vnd.marimo.static-export+zip`.

## Spec Validation

Use `parseExportSpec(input)` when a spec is generated from user input, JSON, or
framework state:

```ts
import { parseExportSpec } from "@marimo-team/export-client";

const spec = parseExportSpec({
  values: {
    summary: {
      source: { def: "summary" },
      formats: ["json"],
    },
  },
});
```

`parseExportSpec` returns an `ExportSpec`. Use it when a caller needs to inspect
validation errors before calling `client.export(...)` or `client.archive(...)`.

The parser validates the same public spec shape that Python accepts. It rejects
unknown source keys, unknown built-in format names, malformed format configs,
empty report sources, empty format maps, and duplicate scenario ids.
Code-authored scenario state must use `{code: "..."}`.

Custom format names use explicit list entries:

```ts
const spec = parseExportSpec({
  values: {
    chart: {
      source: { def: "chart" },
      formats: [
        "vegalite",
        {
          format: "png_nogrid",
          export: { type: "ref", ref: "my_exporters:png_nogrid" },
          options: { scale: 2 },
        },
      ],
    },
  },
});
```

Format maps accept built-in names only. Use a list entry when the format name is
not one of the built-ins.

`client.export(...)` and `client.archive(...)` call `parseExportSpec` before
session discovery, runtime installation, or scratchpad execution. Use
`safeParseExportSpec(input)` when the caller needs an issue list instead of an
exception. Each issue has a `path` and `message`.

## Browser Entry

Frameworkless pages can import the browser-native subpath. It avoids the
generated OpenAPI dependency and uses plain `fetch` plus marimo HTTP/WebSocket
endpoints.

```ts
import { createMarimoExportClient } from "@marimo-team/export-client/browser";

const client = createMarimoExportClient({
  server: "http://localhost:2718",
});

const archive = await client.archive(spec, {
  notebook: "notebooks/queueing_lab.py",
  runtime: "preinstalled",
});
```

## Workspace Browsing

Use the workspace subpath when a build step needs notebook discovery or source
previews from a running marimo server:

```ts
import { createMarimoWorkspaceClient } from "@marimo-team/export-client/workspace";

const workspace = createMarimoWorkspaceClient({
  server: "http://localhost:2718",
});

const notebooks = await workspace.notebooks.list();
const source = await workspace.notebooks.source(notebooks[0].path);
```

Keep workspace browsing separate from capture code that only needs
`client.export(...)` or `client.archive(...)`.

## API Surface

### `createMarimoExportClient(options)`

Creates a `MarimoExportClient`.

- `options.server`: marimo server URL.
- `options.fetch`: Fetch implementation. Defaults to global `fetch`.
- `options.headers`, `options.token`, and `options.serverToken`: Request
  authentication inputs passed to marimo endpoints.
- `options.WebSocket`: WebSocket constructor used when opening a notebook.

### `client.export(spec, options?)`

Writes a static export root through the target marimo session.

- `spec`: Public export spec input. The client validates it before session
  discovery, runtime installation, or scratchpad execution.
- `options.notebook`: Notebook path or name to open or match.
- `options.sessionId`: Existing marimo session id. Takes precedence over
  `notebook`.
- `options.outputRoot`: Output root path seen by the Python kernel.
- `options.paths`: Directories to prepend to kernel `sys.path` for local
  exporters.
- `options.runtime`: `"preinstalled"` or a `Runtime` install request.
- `options.timeoutMs`: Scratchpad execution timeout.

Returns bundle path, manifest path, invocation trace paths, manifest JSON,
invocation JSON, `sessionId`, `sessionName`, `sessionPath`, and
`sessionInitializationId`.

### `client.archive(spec, options?)`

Runs the export in a temporary root and returns zipped export bytes for API
routes that stream the bundle to a caller.

- `spec`: Public export spec input. The client validates it before session
  discovery, runtime installation, or scratchpad execution.
- `options.notebook`: Notebook path or name to open or match.
- `options.sessionId`: Existing marimo session id. Takes precedence over
  `notebook`.
- `options.paths`: Directories to prepend to kernel `sys.path` for local
  exporters.
- `options.runtime`: `"preinstalled"` or a `Runtime` install request.
- `options.timeoutMs`: Scratchpad execution timeout.

Archive calls do not accept `outputRoot`. Use `client.export(...)` when the
kernel should write a persistent export root.
