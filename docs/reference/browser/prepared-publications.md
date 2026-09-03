---
title: Prepared publications
description: Manifest wire format, parsed values, state controller, refresh lifecycle, input routing, and application ownership.
---

# Prepared publications

The `prepared` subpath connects a changing manifest route to immutable notebook
exports. The application supplies a port that stages and commits each complete
visible state.

```ts
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import {
  PreparedPublicationRefresh,
  PreparedStateController,
  type PreparedStatePort,
} from "@marimo-team/marimo-export/prepared";

const port: PreparedStatePort = {
  async apply({ next }, signal) {
    const title = await next.state.output("title").load(jsonLoader(), { signal });
    signal.throwIfAborted();
    document.querySelector("#title")!.textContent = String(title);
  },
};

const controller = new PreparedStateController(port);
const manifestUrl = new URL("/runtime/prepared.json", location.href);
const refresh = new PreparedPublicationRefresh(manifestUrl, controller);

await refresh.start();
await controller.updateInputs({ interval: "1wk" });

await refresh.dispose();
await controller.dispose();
```

For several outputs, `apply()` should load and mount replacements in connected
staging hosts, check the signal, commit the complete replacement, then dispose
the previous mount owner. [Build a browser
application](../../guide/browser-applications.md) describes that DOM pattern.

## Publication objects

| Object                       | Contract                                                                                 |
| ---------------------------- | ---------------------------------------------------------------------------------------- |
| Prepared manifest document   | Snake-case JSON served by an application route                                           |
| `PreparedExportManifest`     | Parsed immutable TypeScript value with camel-case properties                             |
| `NotebookExport`             | Immutable export selected by the manifest `instance` and `export_url`                    |
| `PreparedPublication`        | `{ manifest, notebookExport, state }` after URL, identity, input, and fingerprint checks |
| `PreparedStateController`    | Mutable input intent and serialized application transitions                              |
| `PreparedPublicationRefresh` | Manifest fetching, immutable export reuse, publication replacement, and polling          |

The manifest is named after the prepared export protocol. The browser follows a
prepared publication because the route can select a new immutable export or
state over time.

## Prepared manifest document

Serve this exact JSON shape:

```json
{
  "schema": "marimo-export.prepared.v1",
  "instance": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "export_url": "./aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/",
  "inputs": { "interval": "1d" },
  "state_fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "refresh_interval_ms": 1000
}
```

| Wire field            | Parsed property     | Contract                                                                                               |
| --------------------- | ------------------- | ------------------------------------------------------------------------------------------------------ |
| `schema`              | `schema`            | Exact value `marimo-export.prepared.v1`                                                                |
| `instance`            | `instance`          | Lowercase SHA-256 of the selected export's canonical `index.json`                                      |
| `export_url`          | `exportUrl`         | Nonempty URL reference of at most 8,192 UTF-8 bytes                                                    |
| `inputs`              | `inputs`            | Complete portable JSON input vector                                                                    |
| `state_fingerprint`   | `stateFingerprint`  | Fingerprint of `inputs` in the selected export                                                         |
| `refresh_interval_ms` | `refreshIntervalMs` | Optional polling interval. Use zero to disable polling or 250 through 60,000 milliseconds to enable it |

```ts
interface PreparedExportManifest {
  readonly schema: "marimo-export.prepared.v1";
  readonly instance: string;
  readonly exportUrl: string;
  readonly inputs: JsonObject;
  readonly stateFingerprint: string;
  readonly refreshIntervalMs?: number;
}

interface PreparedPublication {
  readonly manifest: PreparedExportManifest;
  readonly notebookExport: NotebookExport;
  readonly state: ExportState;
}
```

Unknown or missing fields fail validation. `export_url` resolves relative to the
manifest URL, then must produce an HTTP or HTTPS export base without a fragment
or URL credentials.

An absolute `export_url` may name another origin. Enforce the application's
origin allowlist before serving or fetching a manifest. The target origin must
permit browser access through [Cross-Origin Resource Sharing
(CORS)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS).

## Parse, fetch, and open a publication

### `parsePreparedExportManifest(input)`

```ts
function parsePreparedExportManifest<Input>(input: Input): PreparedExportManifest;
```

Converts the snake-case document to the frozen camel-case value. It validates
portable JSON, the exact field set, digest spelling, complete nonempty input
names, URL length, and polling range. Invalid input raises `PreparedExportError`
with code `manifest_invalid`.

### `fetchPreparedExportManifest(url, options?)`

```ts
interface PreparedManifestFetchOptions {
  readonly fetch?: typeof globalThis.fetch;
  readonly signal?: AbortSignal;
}

function fetchPreparedExportManifest(
  url: URL,
  options?: PreparedManifestFetchOptions,
): Promise<PreparedExportManifest>;
```

Fetches with `cache: "no-store"` and `Accept: application/json`. It reads at most
256 KiB, requires strict UTF-8 portable JSON, then calls the manifest parser.
Transport and response failures use `manifest_read_failed`. Shape and value
failures use `manifest_invalid`.

### `resolvePreparedPublication(manifest, manifestUrl, notebookExport)`

```ts
function resolvePreparedPublication(
  manifest: PreparedExportManifest,
  manifestUrl: URL,
  notebookExport: NotebookExport,
): PreparedPublication;
```

Checks that `manifest.instance` equals `notebookExport.identity`, that the
resolved export URL equals `notebookExport.base`, that the complete input vector
exists, and that its fingerprint matches. It reuses an already opened export and
performs no network request.

### `openPreparedPublication(manifest, manifestUrl, options?)`

```ts
interface OpenPreparedPublicationOptions extends OpenExportOptions {
  readonly openExport?: (
    base: string | URL,
    options?: OpenExportOptions,
  ) => Promise<NotebookExport>;
}

function openPreparedPublication(
  manifest: PreparedExportManifest,
  manifestUrl: URL,
  options?: OpenPreparedPublicationOptions,
): Promise<PreparedPublication>;
```

Resolves the export base, opens its index, then applies the same publication
checks. `fetch` and `signal` pass to `openExport()`. `openExport` can supply an
application-specific compatible reader.

Opening a publication validates the index and selected state. Output assets
remain lazy until the application loads or verifies them.

## `PreparedStatePort`

```ts
type PreparedStateChangeReason = "start" | "state" | "publication";

interface PreparedStateChange {
  readonly previous: PreparedPublication | undefined;
  readonly next: PreparedPublication;
  readonly reason: PreparedStateChangeReason;
}

interface PreparedStatePort {
  apply(change: PreparedStateChange, signal: AbortSignal): Promise<void>;
  restore?(publication: PreparedPublication): void | Promise<void>;
  dispose?(): void | Promise<void>;
}
```

`apply()` owns output loading, DOM staging, visible commit, and disposal of the
previous application state. Resolve its promise after the complete next state
has committed. A later request aborts `signal` and removes the transition's
authority to commit.

`restore()` synchronizes optimistic controls or application state to the last
committed publication when an input request requires no new commit or a
non-cancellation transition fails. An abort-shaped port failure does not invoke
`restore()`. If restoration also fails, the controller raises an
`AggregateError` containing the transition and restoration failures.

`dispose()` releases application-owned resources after active transitions have
settled during controller disposal.

## `PreparedStateController`

```ts
class PreparedStateController {
  constructor(port: PreparedStatePort, signal?: AbortSignal);
  snapshot(): PreparedStateSnapshot;
  start(publication: PreparedPublication, signal?: AbortSignal): Promise<void>;
  updateInputs(patch: JsonObject, signal?: AbortSignal): Promise<void>;
  updateControl(objectId: string, value: JsonValue, signal?: AbortSignal): Promise<boolean>;
  updateQuery(query: string, signal?: AbortSignal): Promise<boolean>;
  replacePublication(publication: PreparedPublication, signal?: AbortSignal): Promise<void>;
  cancel<Reason>(reason?: Reason): void;
  settle(): Promise<readonly PromiseSettledResult<void>[]>;
  dispose(): Promise<void>;
}
```

### Start and inspect

`start()` applies one initial publication with reason `start`. A controller can
start once. `snapshot()` returns frozen current state without waiting:

```ts
interface PreparedStateSnapshot {
  readonly current: PreparedPublication | undefined;
  readonly pendingInputs: JsonObject | undefined;
  readonly transition: {
    readonly generation: number;
    readonly target: PreparedPublication | undefined;
    readonly active: boolean;
  };
  readonly disposed: boolean;
}
```

### Update inputs

`updateInputs(patch)` merges a sparse root-input patch over pending intent or the
current transition target. It resolves the resulting complete vector against
the current immutable export and applies it with reason `state`.

Rapid updates abort prior transition signals and execute port calls serially.
Only the latest generation can become current. If an unavailable state or a
missing asset response interrupts the request, the requested input vector stays
pending so a later publication can satisfy it. A caller-aborted request or an
incompatible replacement input contract clears that pending intent.

When the requested vector equals the committed state, the controller calls
`restore()` and performs no `apply()`. When it equals an active target, the call
waits for that transition.

### Route a control

`updateControl(objectId, value)` finds `objectId` in
`notebookExport.controlBindings`, converts the accepted frontend value to
portable JSON, and builds a sparse root-input patch. It returns:

- `true` after routing and applying a root, mapping-key, or sequence-index binding
- `false` when the object ID has no binding
- `false` when the binding contains an `element` step owned by the application

An invalid binding path raises `PreparedExportError` with code
`manifest_invalid`.

`preparedControlInputPatch(inputs, binding, value)` exposes the immutable patch
operation directly. It returns `undefined` for an `element` step.
`samePreparedInputs(left, right)` compares portable values structurally and
ignores object key order.

### Route a query

`updateQuery(query)` inspects parameters whose names occur in `inputNames`.
Unknown parameters are ignored. A recognized parameter must occur exactly once.
Strings match their raw text. Other exported values match their JSON spelling.
The supplied parameters form a sparse patch over the current state.

The method returns `false` when the query contains no recognized input. It
returns `true` after applying a recognized selection. No match raises
`query_miss`. Text that matches more than one typed exported value raises
`query_ambiguous`.

`resolvePreparedQuerySelection()` returns `undefined` when no recognized input
is present. `resolvePreparedQueryState()` returns the current state in that
case.

### Replace a publication

`replacePublication()` reconciles pending intent with a new publication and
applies the selected result with reason `publication`. Pending intent survives
when both exports have the same input-name set. It is discarded when the input
contract changes.

When the export identity and state fingerprint already match the committed
publication, replacement updates manifest metadata without calling `apply()`.

### Cancel, settle, and dispose

`cancel(reason)` aborts the active transition. Error-shaped reasons are
preserved. Other reasons become a `DOMException` named `AbortError`.

`settle()` waits for tracked work and returns `PromiseSettledResult` records. It
does not rethrow a tracked rejection.

`dispose()` is idempotent. It aborts the controller lifecycle, settles active
work, clears current and pending state, then calls `port.dispose()`. An external
signal passed to the constructor triggers the same disposal. Operations after
disposal raise `Error`.

## `PreparedPublicationRefresh`

```ts
interface PreparedPublicationRefreshOptions {
  readonly dependencies?: Partial<PreparedPublicationRefreshDependencies>;
  readonly fetch?: typeof globalThis.fetch;
  readonly openExport?: OpenPreparedPublicationOptions["openExport"];
  readonly signal?: AbortSignal;
  readonly onError?: (cause: unknown) => void;
}

interface PreparedPublicationRefreshDependencies {
  fetchManifest(url: URL, options?: PreparedManifestFetchOptions): Promise<PreparedExportManifest>;
  openPublication(
    manifest: PreparedExportManifest,
    manifestUrl: URL,
    options?: OpenPreparedPublicationOptions,
  ): Promise<PreparedPublication>;
}

class PreparedPublicationRefresh {
  constructor(
    manifestUrl: URL,
    state: PreparedStateController,
    options?: PreparedPublicationRefreshOptions,
  );
  start(signal?: AbortSignal): Promise<void>;
  refresh(signal?: AbortSignal): Promise<void>;
  settle(): Promise<readonly PromiseSettledResult<void>[]>;
  syncPolling(): void;
  dispose(): Promise<void>;
}
```

`start()` fetches the first manifest and starts a controller that has no current
or target publication. It can run once for that controller.

`refresh()` fetches the current manifest. Concurrent calls share the active
refresh operation. The refresh object reuses the opened `NotebookExport` when
identity and base URL match. A new identity is opened and validated before
publication replacement.

A local state selection survives refresh when the input-name set matches and
the next export contains that complete vector. The manifest selection wins when
the vector is unavailable or the input contract changes. A failed refresh leaves
the last committed publication current.

Successful startup and refresh call `syncPolling()`. The method clears the old
timer and schedules the current manifest interval. Background polling failures
call `onError`. An explicit `refresh()` returns its rejection to the caller.

`settle()` returns zero or one settled operation result. `dispose()` stops
polling, aborts active refresh work, and settles it. A later `refresh()` resolves
without work. Refresh disposal does not dispose the state controller, so the
application should dispose both owners.

`dependencies` replaces manifest fetching or publication opening. `fetch` is
the normal integration point for authentication and request policy and is
passed to both manifest and export requests.

## Cancellation and trust

Prepared cancellation prevents a stale transition from committing. A stream,
decoder, dynamic import, or renderer that already started may settle later. The
application port must check its signal immediately before visible commit and
dispose staged mounts on every failure path.

The manifest authenticates neither its server nor its export. Validate the
server through the deployment's HTTPS, authentication, and origin policy. The
`instance` digest detects an export whose index differs from the selected
identity.

[Errors and limits](errors-and-limits.md) separates prepared errors, reader
errors, application errors, and aborts.
