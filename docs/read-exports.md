# Read exports

`openExport()` reads one published directory and returns immutable notebook, scenario, and output objects. The root package runs in browsers and server runtimes after the Python producer stops. The `/node` entrypoint adds local filesystem transfer and reading.

## Browser

Serve the publication at `/export/`, then open it from the page:

```ts
import { httpSource, openExport } from "@marimo-team/marimo-export";

const published = await openExport(httpSource("/export/"));
const scenario = published.scenario("large");
const calculation = await scenario.output("calculation", "json").json();

console.log(scenario.inputs, calculation);
```

`httpSource()` resolves a relative root against `location.href` in a browser. Pass `base` when resolving a relative root in another runtime:

```ts
const source = httpSource("./export/", {
  base: "http://127.0.0.1:4113/reports/",
  headers: { "X-Export-Access": "reader" },
});
```

The HTTP root must use HTTP or HTTPS and contain no embedded credentials, query, or fragment. Reads reject redirects.

Run the checked-in browser example against the [Getting started](./getting-started.md) publication:

```bash
make build
mkdir -p examples/browser/public/export
cp -R /tmp/cache-matrix-export/. examples/browser/public/export/
pnpm --dir examples/browser dev
```

Open `http://127.0.0.1:4113/`.

## Node

Use `directorySource()` for a local publication:

```ts
import { openExport } from "@marimo-team/marimo-export";
import { directorySource } from "@marimo-team/marimo-export/node";

const published = await openExport(directorySource("/tmp/cache-matrix-export"));
const rows = await Promise.all(
  published.scenarios().map(async (scenario) => ({
    id: scenario.id,
    inputs: scenario.inputs,
    projected: await scenario.output("projected", "json").json(),
  })),
);

console.log(rows);
```

The package requires Node 22 or newer for its Node API and CLI. [`examples/read-checkout.mjs`](https://github.com/marimo-team/marimo-export/blob/main/examples/read-checkout.mjs) contains this workflow for the source checkout.

Keep local publication source and destination trees under the caller's control while a read, pull, or verification is active. Node rejects ordinary symlinks, non-files, and paths outside the anchored root, while an untrusted local process that concurrently replaces directory components remains outside the filesystem contract.

## Next.js server rendering

Read a publication from a Server Component through the `/node` entrypoint:

```tsx
import { openExport } from "@marimo-team/marimo-export";
import { directorySource } from "@marimo-team/marimo-export/node";

export default async function Page() {
  const published = await openExport(directorySource(process.env.MARIMO_EXPORT_DIR!));
  const scenario = published.scenario("large");
  const result = await scenario.output("calculation", "json").json();

  return <pre>{JSON.stringify(result, null, 2)}</pre>;
}
```

Run the checked-in Next.js example after the Python producer stops:

```bash
MARIMO_EXPORT_DIR=/tmp/cache-matrix-export \
  pnpm --dir examples/next-ssr dev
```

Open `http://127.0.0.1:4111/`.

## Astro rendering

Astro can read the same directory during development or a static build:

```astro
---
import { openExport } from "@marimo-team/marimo-export";
import { directorySource } from "@marimo-team/marimo-export/node";

const published = await openExport(
  directorySource(process.env.MARIMO_EXPORT_DIR!),
);
const scenario = published.resolve({ scale: 5, multiplier: 3 });
const result = await scenario.output("calculation", "json").json();
---

<pre>{JSON.stringify(result, null, 2)}</pre>
```

Build the checked-in Astro example after the Python producer stops:

```bash
MARIMO_EXPORT_DIR=/tmp/cache-matrix-export \
  pnpm --dir examples/astro-ssr build
```

The generated site in `examples/astro-ssr/dist/` contains the rendered scenario data.

## Select scenarios

List scenarios in plan order:

```ts
for (const scenario of published.scenarios()) {
  console.log(scenario.id, scenario.inputs);
}
```

Select by durable scenario ID:

```ts
const compact = published.scenario("compact");
```

Select by the complete resolved input object:

```ts
const compact = published.resolve({
  lookback: 3,
  width: 480,
  symbols: ["AAPL", "NVDA"],
});
```

`resolve()` requires the exact public input vector recorded in the index, including values supplied by plan defaults.

## Read outputs

```ts
const output = published.scenario("baseline").output("summary", "json");
```

Omit the format when the output has exactly one format:

```ts
const headline = published.scenario("baseline").output("headline");
```

Each `ExportOutput` exposes:

| Member                   | Contract                                                                  |
| ------------------------ | ------------------------------------------------------------------------- |
| `name`                   | Public output name from the plan.                                         |
| `formatName`             | Public format name from the plan.                                         |
| `formatId`               | Codec identity returned by the Python exporter.                           |
| `mediaType`              | Media type returned by the Python exporter.                               |
| `metadata`               | Frozen JSON metadata returned by the exporter.                            |
| `ref`                    | Content-addressed payload key, byte size, and SHA-256.                    |
| `bytes(options?)`        | Returns a defensive `Uint8Array` copy after size and digest verification. |
| `text(options?)`         | Decodes verified bytes as strict UTF-8.                                   |
| `json(options?)`         | Parses verified UTF-8 bytes as JSON.                                      |
| `json(decode, options?)` | Parses JSON, then passes the unknown value to a caller-supplied decoder.  |
| `blob(options?)`         | Returns a browser `Blob` with `mediaType`.                                |
| `load(loader, options?)` | Checks `formatId` and invokes a typed `OutputLoader`.                     |

Read options accept `signal` and `maxBytes`. `maxBytes` must be a nonnegative safe integer. A smaller value rejects a declared payload before its source is read. Built-in sources also bound the body while reading it. A declared or observed overflow throws `MarimoExportError` with code `output_too_large` and structured size details. An invalid option throws `TypeError`.

Concurrent unsignaled reads of the same payload share one in-flight verified source read. The reader releases that in-flight entry when it settles, so a later call reads and verifies again. Signaled reads run independently and forward the signal to the source. HTTP or application caching owns longer-lived reuse.

`load(loader, { signal, maxBytes })` applies those options to every payload read through the loader context. The reader checks the signal before and after loader invocation. `OutputLoaderContext.signal` exposes the same signal so a loader can cancel decoding work or pass it to another API.

```ts
const output = published.scenario("baseline").output("summary", "json");
const value = await output.json({ maxBytes: 256_000 });
```

`openExport()` applies `maxBytes` to `index.json`. An unanchored index defaults to a 16 MiB limit. With `ref`, the reader rejects before source I/O when `ref.size` exceeds the caller limit, then bounds the read to `ref.size`.

A decoder keeps application types at the boundary:

```ts
interface Summary {
  readonly symbols: readonly string[];
  readonly rows: number;
}

function summary(value: unknown): Summary {
  if (typeof value !== "object" || value === null) {
    throw new TypeError("summary must be an object");
  }
  const record = value as Record<string, unknown>;
  if (
    !Array.isArray(record.symbols) ||
    !record.symbols.every((item) => typeof item === "string") ||
    typeof record.rows !== "number"
  ) {
    throw new TypeError("summary has an invalid shape");
  }
  return { symbols: record.symbols, rows: record.rows };
}

const value = await output.json(summary);
```

## Verify the index

`openExport()` always derives `published.ref` from the bytes it read. Pass a trusted external `ExportRef` to verify `index.json` before parsing:

```ts
const published = await openExport(directorySource("/tmp/cache-matrix-export"), {
  ref: build.ref,
});
```

The external reference usually comes from `remote.build()`, a saved CLI build record, or a trusted deployment manifest. See [Trust and integrity](./trust.md) for the distinction between byte integrity and source authenticity.

## Transfer and verify in Node

`pullExport()` copies a projection closure from any `ExportSource` into a local directory:

```ts
import { httpSource } from "@marimo-team/marimo-export";
import { directorySource, pullExport, verifyExport } from "@marimo-team/marimo-export/node";

const source = httpSource("http://127.0.0.1:4113/export/");
const receipt = await pullExport({
  source,
  into: "/tmp/finance-export",
  ref: build.ref,
  concurrency: 8,
});

const verification = await verifyExport({
  source: directorySource("/tmp/finance-export"),
  ref: build.ref,
});
```

The receipt is `{files, downloaded, skipped, bytes}`. `files` counts unique payloads and excludes `index.json`. `bytes` counts the full payload closure, including files that were already valid locally.

Pulls deduplicate payload references, verify every downloaded payload, skip matching local files, use atomic file writes, and write `index.json` after its payload closure completes. A failed pull leaves the previous index in place. Verified content-addressed payloads written before the failure remain available for the next pull. Concurrency defaults to `8` and accepts integers from `1` through `64`.

`verifyExport()` returns `{ok, files, bytes, failures}`. Each payload failure names its key and message, and `bytes` counts successfully verified payload bytes. An unreadable or invalid index, reference mismatch, invalid concurrency, or abort rejects the call before a report can represent the requested verification.

Use `pullRemote(remote, ref, options)` when a `Remote` owns the server-side transfer lease. It stages, pulls, and closes that lease.

## Built-in and optional formats

The core reader handles raw bytes, UTF-8 text, JSON, and blobs. Optional packages implement the shared `OutputLoader` contract.

### Arrow IPC

```ts
import { arrow } from "@marimo-team/marimo-export-arrow";

const table = await scenario.output("market_table", "arrow").load(arrow());
```

`arrow()` returns a Flechette table. Pass Flechette extraction options to `arrow(options)`.

### Parquet

```ts
import { parquet } from "@marimo-team/marimo-export-parquet";

interface PriceRow {
  readonly date: string;
  readonly symbol: string;
  readonly close: number;
}

const rows = await scenario.output("market_table", "parquet").load(parquet<PriceRow>());
```

`parquet()` decodes the payload into row objects with Hyparquet.

### Vega-Lite

```ts
import { vegaLite } from "@marimo-team/marimo-export-vegalite";

const chart = await scenario.output("chart", "vegalite").load(vegaLite());
const host = document.querySelector<HTMLElement>("#chart");
if (host === null) throw new Error("Missing #chart mount point");

const mounted = await chart.mount(host);
window.addEventListener("pagehide", () => mounted.finalize(), { once: true });
```

The loaded object exposes a frozen `spec` for server inspection and a browser `mount()` method backed by `vega-embed`. A specification can request external data or resources, so configure Vega network access through the host application.

### AnyWidget

```ts
import { anywidget } from "@marimo-team/marimo-export-anywidget";

const widget = await scenario
  .output("raw_counter", "anywidget")
  .load(anywidget<{ count: number }>());

console.log(widget.initialState.count);

const host = document.querySelector<HTMLElement>("#counter");
if (host === null) throw new Error("Missing #counter mount point");

const mounted = await widget.mount(host);
mounted.model.set("count", mounted.model.get("count") + 1);
mounted.model.save_changes();
```

Loading is inert and exposes `initialState` during server rendering. Mounting executes the notebook-authored frontend module in a browser. Call `await mounted.dispose()` when the view leaves the page. [Publish AnyWidget outputs](./anywidget.md) covers producer setup, initialization exports, nested widgets, buffers, cleanup, and content security policy.

### PNG, HTML, and bytes

```ts
const image = await scenario.output("chart", "png").blob();
const markup = await scenario.output("market_note", "html").text();
const data = await scenario.output("attachment", "bytes").bytes();
```

Treat HTML as active authored content when inserting it into a document.

## Custom loaders

A project-specific loader declares the `formatId` returned by the Python `Projection` and receives a narrow verified-output context:

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

const summary = await scenario.output("summary", "card").load(projectSummary);
```

`OutputLoaderContext` exposes `formatId`, `mediaType`, `metadata`, `size`, `signal`, `bytes()`, `text()`, and `json()`. Its generic result types are compile-time assertions, so validate untrusted payload shapes inside the loader. Read `signal` during long decoding work. Keep decoding and rendering dependencies in the package that owns the format.

## In-memory and custom sources

`memorySource()` accepts a record or `ReadonlyMap` of portable paths to strings or byte arrays. Given verified `indexBytes`, its referenced `payloadRef`, and `payloadBytes`:

```ts
import { memorySource, openExport } from "@marimo-team/marimo-export";

const published = await openExport(
  memorySource(
    new Map([
      ["index.json", indexBytes],
      [`cache/${payloadRef.key}`, payloadBytes],
    ]),
  ),
);
```

Implement `ExportSource` for another store:

```ts
interface ExportSource {
  read(path: string, options?: { signal?: AbortSignal; maxBytes?: number }): Promise<Uint8Array>;
}
```

Paths are portable relative paths. A custom source must honor `signal` and enforce `maxBytes` before or while materializing a response. `openExport()` takes ownership of source bytes before asynchronous integrity checks.
