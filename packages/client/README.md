# @marimo-team/marimo-export

`@marimo-team/marimo-export` opens verified marimo notebook publications in browsers and server runtimes. Its remote and Node entrypoints build, transfer, and verify the same `index.json` plus `cache/` layout. Reading a pulled publication uses JavaScript and ordinary files after the Python producer stops.

```bash
pnpm add @marimo-team/marimo-export
```

## Read from HTTP

```ts
import { httpSource, openExport } from "@marimo-team/marimo-export";

const published = await openExport(httpSource("/export/"));
const scenario = published.scenario("large");
const result = await scenario.output("calculation", "json").json();

console.log(scenario.inputs, result);
```

The root entrypoint is compatible with browsers and server-side rendering. `openExport()` validates the strict index schema, derives a canonical `published.ref`, and exposes immutable domain objects. Payload bytes are verified against their declared size and SHA-256 before any read returns them.

## Read a local directory

The `/node` entrypoint requires Node 22 or newer:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { directorySource } from "@marimo-team/marimo-export/node";

const published = await openExport(directorySource("/tmp/cache-matrix-export"));
const projected = await published.scenario("baseline").output("projected", "json").json();
```

## Reader API

### `openExport(source, options?)`

Reads `index.json`, verifies `options.ref` when supplied, validates the index, and returns `Promise<NotebookExport>`.

- `source`: An `ExportSource` that reads portable relative paths.
- `options.ref`: Expected index key, SHA-256, and size.
- `options.signal`: Abort signal for the index read.
- `options.maxBytes`: Caller limit for index bytes. Unanchored indexes default to 16 MiB. With `ref`, the reader rejects before source I/O when `ref.size` exceeds this limit, then bounds the read to `ref.size`.

### `NotebookExport`

- `ref`: Canonical index key, SHA-256, and size derived from the opened bytes.
- `notebook`: Saved notebook name and source digest.
- `planSha256`: Canonical plan digest.
- `producer`: marimo and marimo-export versions.
- `scenarios()`: Frozen scenarios in plan order.
- `scenario(id)`: Scenario selected by ID.
- `resolve(inputs)`: Scenario selected by its exact resolved public inputs.

### `ExportScenario`

- `id`: Public scenario ID.
- `inputs`: Complete frozen public input object.
- `outputs()`: Every output-format pair.
- `output(name, formatName?)`: One output. The format name is inferred when exactly one exists.

### `ExportOutput`

- `name`, plan-defined `formatName`, codec `formatId`, `mediaType`, `metadata`, and content-addressed `ref`.
- `bytes()`, `text()`, `json()`, and `blob()` decode verified payload bytes.
- `json(decode)` passes the parsed unknown value to an application decoder.
- `load(loader)` checks the loader's `formatId` and invokes it.

Every read method accepts `{signal, maxBytes}`. The byte limit must be a nonnegative safe integer. A smaller limit rejects the declared payload before reading it, and built-in sources enforce the indexed payload size while materializing bytes. Overflow raises `MarimoExportError` with code `output_too_large`. Invalid limits raise `TypeError`.

Concurrent unsignaled reads of the same payload share one in-flight verified source read. The entry is evicted when it settles, so later reads go through the source and verification again. Signaled reads run independently. Returned byte arrays are defensive copies.

`load(loader, {signal, maxBytes})` applies those options to every payload read through the loader context and checks the signal before and after invocation. The loader context exposes the signal so custom decoding work can observe cancellation.

## Sources

### `httpSource(root, options?)`

Reads a publication over HTTP or HTTPS. Relative roots resolve against `location.href` in a browser or `options.base` in another runtime. `options.fetch` replaces the Fetch implementation, and `options.headers` adds request headers. Reads reject redirects and enforce `maxBytes` while streaming a response.

### `memorySource(entries)`

Reads strings or byte arrays from a record or `ReadonlyMap` keyed by portable publication paths.

### `directorySource(root)`

Reads a local directory through the `/node` entrypoint and rejects paths that resolve outside the real root.

Keep local publication source and destination trees under the caller's control while reads, pulls, or verification are active. The Node entrypoint rejects ordinary symlinks and non-files, while concurrent directory replacement by an untrusted local process remains outside its filesystem contract.

## Remote build and transfer

```ts
import { openExport } from "@marimo-team/marimo-export";
import { directorySource, pullRemote, verifyExport } from "@marimo-team/marimo-export/node";
import { connectRemote, validateExportPlan } from "@marimo-team/marimo-export/remote";

const plan = validateExportPlan({
  schema: "marimo-export.plan.v1",
  outputs: {
    summary: {
      source: "summary",
      formats: { json: {} },
    },
  },
});

const remote = await connectRemote({
  server: "http://127.0.0.1:2718/",
  target: { notebook: "/absolute/path/on/server/notebook.py" },
});

try {
  const build = await remote.build(plan);
  await pullRemote(remote, build.ref, { into: "/tmp/notebook-export" });
  const published = await openExport(directorySource("/tmp/notebook-export"), {
    ref: build.ref,
  });
  const result = await verifyExport({
    source: directorySource("/tmp/notebook-export"),
    ref: build.ref,
  });
  console.log(build, published.notebook, result);
} finally {
  await remote.close();
}
```

`connectRemote()` requires marimo edit-scoped control and targets one notebook path or primary session ID. Producer builds also require marimo's default `relaxed` execution type. When a notebook has run with `strict` execution, use a fresh notebook `__marimo__/cache` directory before building because marimo 0.23.14 shares native cache identity across those execution types. A notebook target routes the file through marimo's WebSocket connector, which may create a managed session or resume an orphaned session. Client-side instantiation uses `autoRun: false`, and the connector result determines `remote.session.owned`. A session target must match a top-level key from `GET /api/sessions`. Session targets and resumed orphans are borrowed sessions. Export scenarios run in fresh child runners from the saved notebook snapshot.

`remote.close()` waits for active work, attempts every open-lease release, and requests managed-session shutdown after no active work or leases remain. It waits until marimo stops reporting that session. Borrowed sessions remain running. The method always closes the retained local socket and reports the first cleanup error. Call it again to retry unresolved remote cleanup, followed by managed-session shutdown. A completed close is idempotent.

`remote.build()` returns exactly `{ ref, receipt }` as an immutable runtime result. `remote.open()` returns a temporary source, its Unix epoch expiration as `expiresAt`, and an idempotent `close()`. `pullRemote()` opens a fresh stage for the referenced projection closure, performs an incremental verified pull, and closes the stage.

The CLI `build` command accepts a notebook target and emits a `marimo-export.build.v1` record with the server and notebook locator. `publish --record` also requires a notebook target. Place the record outside the publication directory. `publish` writes it after the remote build and before transfer, so it remains available to `pull` if transfer or local verification fails. `pull` uses that record to open a fresh remote session. `publish --session ID` builds and pulls through one connection without creating a durable record.

`validateExportPlan(value)` accepts `unknown`, performs structural wire preflight, and returns a frozen `ExportPlan`. `remote.build()` runs the same preflight before sending the plan. The Python producer remains authoritative for Python identifiers, notebook definitions, imports, serializer availability, and exporter results.

## Node transfer API

### `pullExport(options)`

Copies one index and its deduplicated payload closure from any `ExportSource` into `options.into`. Existing matching payloads are skipped. Payloads use atomic writes, and `index.json` is written after the closure completes.

Returns `{files, downloaded, skipped, bytes}`. `files` counts unique payloads and excludes `index.json`. `bytes` counts the complete payload closure, including skipped files. Concurrency defaults to `8` and accepts values through `64`. On failure, the new index is not committed. Verified content-addressed payloads written before the failure remain available to a later pull.

### `pullRemote(remote, ref, options)`

Opens a temporary source through `remote`, delegates to `pullExport()`, and closes the transfer lease.

Returns the same pull receipt. A transfer failure remains the primary error if lease cleanup also fails.

### `verifyExport(options)`

Verifies the index against `options.ref` when supplied, then verifies each unique payload. Returns `{ok, files, bytes, failures}` for payload verification, with `bytes` counting successful payload bytes. Index, reference, option, and abort failures reject the operation.

## Typed loaders

An `OutputLoader<T>` owns one codec:

```ts
import type { OutputLoader } from "@marimo-team/marimo-export";

interface ProjectSummary {
  readonly label: string;
  readonly summary: unknown;
}

const projectSummary: OutputLoader<ProjectSummary> = {
  formatId: "project.summary.v1",
  async load(output) {
    const value = await output.json();
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new TypeError("project summary has an invalid shape");
    }
    const record = value as Record<string, unknown>;
    if (typeof record.label !== "string") {
      throw new TypeError("project summary has an invalid label");
    }
    return { label: record.label, summary: record.summary };
  },
};

const value = await scenario.output("summary", "card").load(projectSummary);
```

`OutputLoaderContext` exposes the format ID, media type, metadata, byte size, load signal, and verified `bytes()`, `text()`, and `json()` methods. Validate application-specific JSON inside the loader before returning a domain type.

Published format packages provide loaders for:

- `arrow()` from `@marimo-team/marimo-export-arrow`.
- `parquet()` from `@marimo-team/marimo-export-parquet`.
- `vegaLite()` from `@marimo-team/marimo-export-vegalite`.

## CLI

The package installs `marimo-export`:

```bash
marimo-export inspect /tmp/cache-matrix-export --json
marimo-export read /tmp/cache-matrix-export large calculation --format json --json
marimo-export verify /tmp/cache-matrix-export --json
```

The repository source checkout runs the built entrypoint with `node packages/client/dist/cli.mjs`.

## Errors

`MarimoExportError` exposes `code` and optional JSON `details`. Codes distinguish invalid indexes and references, missing scenarios or outputs, unsupported formats, source reads, integrity failures, session lifecycle, remote requests, timeouts, plans, scenarios, and stages.

## Guides

- [Getting started](https://github.com/marimo-team/marimo-export/blob/main/docs/getting-started.md)
- [Export plans](https://github.com/marimo-team/marimo-export/blob/main/docs/export-plans.md)
- [Remote execution](https://github.com/marimo-team/marimo-export/blob/main/docs/remote-execution.md)
- [Read exports](https://github.com/marimo-team/marimo-export/blob/main/docs/read-exports.md)
- [CLI](https://github.com/marimo-team/marimo-export/blob/main/docs/cli.md)
- [Trust and integrity](https://github.com/marimo-team/marimo-export/blob/main/docs/trust.md)
