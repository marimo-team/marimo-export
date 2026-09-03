---
title: Browser reader
description: Open, inspect, resolve, load, and verify an immutable notebook export from TypeScript.
---

# Browser reader

`openExport()` validates one notebook export index and returns immutable reader
values. The reader resolves results already present in the export and fetches an
asset only when an output loader needs it.

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/exports/report/");
const state = notebookExport.state("baseline");
const summary = await state.output("summary").load(jsonLoader());
```

## `openExport(base, options?)`

```ts
interface OpenExportOptions {
  readonly fetch?: typeof globalThis.fetch;
  readonly signal?: AbortSignal;
}

function openExport(base: string | URL, options?: OpenExportOptions): Promise<NotebookExport>;
```

`base` names the directory or HTTP route that contains `index.json`. A relative
string resolves against `document.baseURI`. A string used outside a document
must be an absolute URL. The URL must use HTTP or HTTPS and cannot contain a
fragment or user information. A missing trailing slash is added.

Opening performs these operations:

1. Resolve `index.json` below `base`.
2. Fetch at most 16 MiB.
3. Decode strict UTF-8 JSON and reject duplicate keys.
4. Require the exact canonical JSON bytes and export schema.
5. Validate names, descriptors, aliases, state shapes, and representation consistency.
6. Recompute every state fingerprint.
7. Hash the index bytes as `NotebookExport.identity`.

The operation does not fetch output assets. Pass `signal` to abort the fetch,
stream read, parsing checkpoints, or fingerprint work.

### Preserve a routing query

A fixed query on `base` is copied to the index and every asset URL:

```ts
const notebookExport = await openExport("/api/notebook-export/?file=reports%2Ffinance.py");
```

`notebookExport.base` returns a detached copy of the normalized URL. Mutating
that copy cannot redirect later reads.

Treat a propagated query as application routing data. Query strings can appear
in browser history, server logs, and monitoring systems. Use request headers or
cookies for credentials.

### Supply authentication or request policy

`options.fetch` replaces the global [Fetch
API](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API) implementation
for the index and every later asset read through the returned reader.

The example assumes `accessToken` came from the application's authentication
flow:

```ts
const authenticatedFetch: typeof fetch = (input, init) => {
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(input, { ...init, headers });
};

const notebookExport = await openExport("https://data.example/exports/report/", {
  fetch: authenticatedFetch,
});
```

For a cross-origin export, the export host must permit the application origin
through [Cross-Origin Resource Sharing
(CORS)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS). A custom
fetch implementation can add credentials and enforce an origin allowlist. It
cannot bypass the browser's CORS policy.

## `NotebookExport`

```ts
interface NotebookExport {
  readonly base: URL;
  readonly identity: string;
  readonly specSha256: string;
  readonly defaultState: ExportState;
  readonly notebook: NotebookProvenance;
  readonly producer: ProducerProvenance;
  readonly inputNames: readonly string[];
  readonly controlBindings: Readonly<Record<string, ControlBinding>>;
  readonly outputNames: readonly string[];
  states(): readonly ExportState[];
  state(alias: string): ExportState;
  resolve(inputs: JsonObject): ExportState;
  verify(options?: VerifyOptions): Promise<VerificationResult>;
}

interface NotebookProvenance {
  readonly filename: string | null;
  readonly documentSha256: string;
}

interface ProducerProvenance {
  readonly marimo: string;
  readonly marimoExport: string;
  readonly implementationSha256: string;
}
```

| Property          | Value                                                             |
| ----------------- | ----------------------------------------------------------------- |
| `base`            | Detached normalized export URL, including its fixed query         |
| `identity`        | Lowercase SHA-256 of the exact canonical `index.json` bytes       |
| `specSha256`      | Identity of the canonical ExportSpec that selected the relation   |
| `defaultState`    | Exported state named by the index default fingerprint             |
| `notebook`        | Optional source filename and document SHA-256                     |
| `producer`        | marimo version, marimo-export version, and implementation SHA-256 |
| `inputNames`      | Ordered root input names                                          |
| `controlBindings` | Projection-scoped UI object IDs mapped to an input and typed path |
| `outputNames`     | Ordered output names present in every exported state              |

`states()` returns states in fingerprint order. Reader objects, arrays, state
inputs, descriptors, and portable JSON values are frozen. `base` is returned as
a new `URL` because `URL` instances are mutable.

## Select an exported state

Every authored state name becomes an alias. Several aliases can select the same
complete input vector and state fingerprint.

```ts
const leaders = notebookExport.state("leaders");

const weekly = notebookExport.resolve({
  interval: "1wk",
  symbols_selector: ["AAPL", "MSFT", "GOOGL", "AMZN"],
});

const cloud = leaders.resolve({
  symbols_selector: ["MSFT", "GOOGL", "AMZN"],
});
```

### `state(alias)`

Selects an authored alias. An unknown alias raises `NotebookExportError` with
code `state_not_found` and a bounded list of available aliases.

### `resolve(inputs)`

Selects one exact complete input vector. The object must contain every
`inputNames` member and no other key. An incomplete or unknown key set raises
`state_input_invalid`. A valid vector absent from the export raises
`state_unavailable`.

### `ExportState.resolve(patch)`

Merges a sparse root-input patch over the current exported state, then selects the exact
matching exported vector. An empty patch returns the same state object. Unknown
input names raise `state_input_invalid`.

```ts
interface ExportState {
  readonly notebookExport: NotebookExport;
  readonly fingerprint: string;
  readonly aliases: readonly string[];
  readonly inputs: JsonObject;
  outputs(): readonly ExportOutput[];
  output(name: string): ExportOutput;
  resolve(patch: JsonObject): ExportState;
}
```

`output(name)` selects a published output. An unknown name raises
`output_not_found`. `outputs()` returns outputs in `notebookExport.outputNames`
order.

## Load an output

```ts
interface LoadOptions {
  readonly signal?: AbortSignal;
  readonly maxBytes?: number;
}

interface ExportOutput {
  readonly state: ExportState;
  readonly name: string;
  readonly codec: OutputCodec;
  readonly mediaType: MediaType;
  readonly descriptor: OutputDescriptor;
  load<C extends OutputCodec, T>(loader: OutputLoader<C, T>, options?: LoadOptions): Promise<T>;
}
```

`load()` requires one explicit loader. It checks the loader codec and media-type
predicate before reading bytes. Inline scalar and JSON values require no asset
request. Asset-backed outputs use a path derived from codec and SHA-256, request
it with `cache: "force-cache"`, enforce the declared and caller byte limits,
verify size and SHA-256, validate native framing, then invoke the loader.

The default `maxBytes` is 512 MiB. Pass a smaller value for an untrusted or
memory-constrained workflow. The maximum accepted value is 2,147,483,647 bytes.

```ts
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";

const abort = new AbortController();
const rows = await state.output("prices").load(parquetRowsLoader(), {
  maxBytes: 256 * 1024 * 1024,
  signal: abort.signal,
});
```

An abort prevents the loaded value from receiving commit authority. Some
decoders and browser module evaluations cannot stop after they begin. They can
settle later while their result remains stale. [Output loaders](loaders)
defines each loader's cancellation and cleanup behavior.

## Verify the complete export

```ts
interface VerifyOptions extends LoadOptions {
  readonly maxTotalBytes?: number;
}

interface VerificationResult {
  readonly states: number;
  readonly outputs: number;
  readonly assets: number;
  readonly bytesVerified: number;
}

const result = await notebookExport.verify();
```

`verify()` collects every unique asset identified by codec and SHA-256. It
checks the declared total before fetching, then reads and verifies each asset
sequentially. The default per-asset limit is 512 MiB. The default aggregate
limit is 2 GiB.

The result counts exported states, state-output pairs, unique assets, and
verified bytes. Inline scalar and JSON values contribute to `outputs` but not to
`assets` or `bytesVerified`.

Verification establishes consistency with `index.json`. It does not authenticate
the index producer or execute an interactive representation.

## Reader types

### Control bindings

```ts
interface ControlIndexStep {
  readonly kind: "index";
  readonly value: number;
}

interface ControlKeyStep {
  readonly kind: "key";
  readonly value: string;
}

interface ControlElementStep {
  readonly kind: "element";
}

type ControlPathStep = ControlIndexStep | ControlKeyStep | ControlElementStep;

interface ControlBinding {
  readonly input: string;
  readonly path: readonly ControlPathStep[];
}
```

An empty path binds a root input. `index` selects a sequence or numeric mapping
member. `key` selects a string mapping member. `element` marks a wrapper or form
child whose frontend event remains application-owned.

### Output descriptors

```ts
type ScalarValue = null | boolean | string | number | bigint;

interface Provenance {
  readonly pythonType: string;
}

interface AssetDescriptor {
  readonly sha256: string;
  readonly size: number;
}

interface ScalarDescriptor {
  readonly codec: "marimo.scalar.v1";
  readonly mediaType: "application/vnd.marimo.scalar.v1+json";
  readonly provenance: Provenance;
  readonly value: ScalarValue;
}

interface JsonDescriptor {
  readonly codec: "marimo.json.v1";
  readonly mediaType: "application/vnd.marimo.json.v1+json";
  readonly provenance: Provenance;
  readonly value: JsonValue;
}

interface MarimoOutputDescriptor {
  readonly codec: "marimo.output.v1";
  readonly mediaType: "application/vnd.marimo.output.v1+json";
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
}

interface MarimoCellDescriptor {
  readonly codec: "marimo.cell.v1";
  readonly mediaType: "application/vnd.marimo.cell.v1+json";
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
}

interface NumpyDescriptor {
  readonly codec: "numpy.npy.v1";
  readonly mediaType: "application/x-npy";
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
}

interface ArrowDescriptor {
  readonly codec: "apache.arrow.file.v1";
  readonly mediaType: "application/vnd.apache.arrow.file";
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
}

interface BlobAssetDescriptor {
  readonly codec: "marimo.blob-asset.msgpack.v1";
  readonly mediaType: string;
  readonly filename: string | null;
  readonly metadata: JsonObject;
  readonly provenance: Provenance;
  readonly asset: AssetDescriptor;
}

type OutputDescriptor =
  | ScalarDescriptor
  | JsonDescriptor
  | MarimoOutputDescriptor
  | MarimoCellDescriptor
  | NumpyDescriptor
  | ArrowDescriptor
  | BlobAssetDescriptor;
```

The exported `DescriptorFor<C>` type selects the descriptor for one
`OutputCodec`. [Export format](../export-format) defines the corresponding
snake-case wire fields and durable invariants.

[Output loaders](loaders) defines loader, media-type, payload, and mount
types. [Errors and limits](errors-and-limits) defines every reader error code
and browser requirement. [Portable JSON](../portable-json) defines
`JsonPrimitive`, `JsonValue`, and `JsonObject`.
