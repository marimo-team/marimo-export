---
title: Browser API reference
description: TypeScript contracts for opening, resolving, loading, verifying, and mounting notebook exports.
---

# Browser API reference

Install browser core:

```bash
pnpm add @marimo-team/marimo-export
```

## `openExport(base)`

Fetches and validates `index.json` below `base`, then returns an immutable
`NotebookExport`. Output assets remain lazy.

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/exports/report/");
const state = notebookExport.state("baseline");
const title = await state.output("title").load(jsonLoader());

document.querySelector("#title")!.textContent = String(title);
```

`notebookExport.identity` is the lowercase SHA-256 of the exact canonical
`index.json` bytes fetched by `openExport`.
`notebookExport.specSha256` identifies the canonical ExportSpec that selected
the relation. `notebookExport.defaultState` resolves the index's default state
fingerprint.
`notebookExport.inputNames` preserves opaque, nonempty names of at most 255
UTF-8 bytes.
`notebookExport.controlBindings` is the immutable mapping from
projection-scoped Marimo object IDs to `{ input, path }` records. `path` uses
the exported `ControlIndexStep`, `ControlKeyStep`, and `ControlElementStep`
union. Prepared runtimes use these records to translate control events into
root state patches without parsing object IDs.

`base` may include a fixed query for file-routing endpoints:

```ts
const notebookExport = await openExport("/api/notebook-export/?file=reports%2Ffinance.py");
```

`openExport` preserves that query on `index.json` and every content-addressed
asset request. The returned `notebookExport.base` includes the canonical query.
Fragments and URL credentials are invalid.

## Select a state

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

- `state(alias)` selects one authored state alias.
- `resolve(inputs)` selects one complete exported input vector.
- `state.resolve(patch)` completes a sparse transition from the current state.

Resolution selects results already present in the export.
`states()` returns states in fingerprint order. Each state exposes
`fingerprint` and its immutable `aliases` array.

## `ExportOutput.load(loader, options?)`

Loads and verifies one output, then asks the explicit loader to decode its
representation.

```ts
import { imageLoader } from "@marimo-team/marimo-export";
import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";

const matrix = await state.output("matrix").load(numpyLoader());
const rows = await state.output("prices").load(parquetRowsLoader());
const image = await state.output("snapshot").load(imageLoader());
```

Load inert Marimo projection records from their public subpaths:

```ts
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import { marimoCellLoader } from "@marimo-team/marimo-export/loader/marimo-cell";
import { marimoOutputLoader } from "@marimo-team/marimo-export/loader/marimo-output";

const summary = await state.output("summary").load(jsonLoader());
const output = await state.output("report").load(marimoOutputLoader());
const cell = await state.output("summary_cell").load(marimoCellLoader());
```

`jsonLoader()` returns a frozen JSON value.
`marimoOutputLoader()` returns a frozen `MarimoOutputSnapshot` with
`ownerCellId`, `output`, and `resources`. `marimoCellLoader()` returns a frozen
`MarimoCellSnapshot` with cell identity, outcome, output, ordered console
records, and resources. Replay resources expose `files`, `modelNotifications`,
`functions`, and `uiValues`. The loaders validate their exact media type and
canonical record shape.

`load()` preserves the exact `NotebookExportError` raised by a loader, including
an error created in an iframe or another installed copy of the package. Another
loader rejection becomes `NotebookExportError` code `decode_failed` with the
output name, codec, and media type in `details`. Cancellation remains code
`abort` and retains the abort reason as `cause`.

Pass an abort signal and per-output limit when loading can become stale or
consume untrusted bytes:

```ts
const controller = new AbortController();
const rows = await state.output("prices").load(parquetRowsLoader(), {
  signal: controller.signal,
  maxBytes: 256 * 1024 * 1024,
});
```

[Output representations](representations.md) lists each loader and its peer
dependency.

## Mount interactive output

Mountable values return an idempotent disposable view:

```ts
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const chart = await state.output("chart").load(vegaLiteLoader({ actions: false }));
const mounted = await chart.mount(document.querySelector("#chart")!, {
  renderer: "svg",
});

await mounted.dispose();
```

Dispose a mounted chart or widget before replacing it. Use one abort signal for
related loads and a separate controller for staged mounts. Commit the complete
replacement before disposing the previous mount owner.

Mounted code can create page-global effects while staging. [Build a browser
application](../guide/browser-applications.md) describes the transition order
and ownership boundaries.

AnyWidget starts from its saved model state. Each mount owns separate model and
view state. Module definitions are shared per page by declared ESM hash and
source identity, including when an application contains multiple loader bundle
copies. The cache accepts at most 1,024 unique definitions and retains successful
imports for the page lifetime. Failed embedded definitions retry through a fresh
blob URL. Retry for direct data and HTTP URLs follows the browser's native
module-map behavior. Its browser interactions call no Python kernel.

## Marimo snapshot records

`parseMarimoOutputSnapshot()` and `parseMarimoCellSnapshot()` return strict,
immutable replay records. The package exports the exact wire types used by
those parsers:

- `MarimoCellChannel` and `MarimoCellOutput`
- `MarimoEsmSpec`, `MarimoBufferPathToken`, and `MarimoBufferPath`
- the open, update, custom, and close model message types
- `MarimoModelLifecycleMessage` and `MarimoModelLifecycleNotification`
- `MarimoReplayResources`

Applications can consume these unions directly while retaining the parser's
closed field and lifecycle validation.

## Prepared publication API

Import prepared-publication capabilities from the dedicated subpath:

```ts
import {
  fetchPreparedExportManifest,
  openPreparedPublication,
  PreparedPublicationRefresh,
  PreparedStateController,
} from "@marimo-team/marimo-export/prepared";
```

### `fetchPreparedExportManifest(url, options?)`

Fetches and validates one bounded `marimo-export.prepared.v1` manifest. The
manifest contains:

- `instance`, the immutable export identity
- `exportUrl`, resolved relative to the manifest URL
- complete `inputs`
- `stateFingerprint`
- optional `refreshIntervalMs`

### `openPreparedPublication(manifest, manifestUrl, options?)`

Opens the immutable export, verifies that its identity and base URL match the
manifest, resolves the complete inputs, and verifies the state fingerprint. It
returns `{ manifest, notebookExport, state }`.

### `PreparedStateController`

Given an application `PreparedStatePort` named `port`:

```ts
const controller = new PreparedStateController(port);
await controller.start(publication);
await controller.updateInputs({ interval: "1wk" });
await controller.updateControl("cell-region", "Northeast");
await controller.updateQuery(location.search);
await controller.dispose();
```

The `PreparedStatePort.apply(change, signal)` callback loads and commits one
complete state. Optional `restore(publication)` reinstates the last committed
publication after a rejected transition. Optional `dispose()` releases the
application adapter.

When refresh opens a new export identity with the same input names, the
controller resolves the current local input vector against that export. It
keeps the local selection when the vector remains available and uses the
manifest selection otherwise.

`snapshot()` reports the current publication, pending inputs, active target,
transition generation, and disposal state. `cancel()` aborts the active
transition. `settle()` waits for tracked work.

### `PreparedPublicationRefresh`

```ts
const liveController = new PreparedStateController(port);
const refresh = new PreparedPublicationRefresh(manifestUrl, liveController, {
  onError: console.error,
});

await refresh.start();
await refresh.dispose();
```

`start()` initializes an unused controller from the manifest. `refresh()` checks
for a replacement publication. Startup and successful refresh synchronize
polling with the current manifest. `syncPolling()` reschedules that interval.
The refresh object reuses an opened immutable export when its identity and base
match.

### Control and query helpers

`preparedControlInputPatch()` applies one typed control binding to complete
inputs. `resolvePreparedQueryState()` and `resolvePreparedQuerySelection()` map
URL query values to prepared states. `samePreparedInputs()` compares canonical
portable values.

Prepared failures use `PreparedExportError`. Use `isPreparedExportError()` for
typed handling and `isPreparedAbort()` to recognize cancellation. Prepared APIs
preserve error-shaped abort reasons and convert other reasons to a
`DOMException` named `AbortError`.

## `NotebookExport.verify(options?)`

```ts
const result = await notebookExport.verify({
  maxBytes: 512 * 1024 * 1024,
  maxTotalBytes: 2 * 1024 * 1024 * 1024,
});
```

Verifies every asset and returns state, output, asset, and byte counts.

## Errors and custom loaders

`NotebookExportError` provides `code`, optional `details`, and `cause`. Common
codes include `state_not_found`, `state_unavailable`, `output_not_found`,
`loader_unavailable`, `decode_failed`, `integrity_failed`,
`read_limit_exceeded`, and `abort`.

Construct `NotebookExportError` directly. Each instance is frozen and
non-extensible after construction. The constructor raises `TypeError` for an
unknown runtime code or a non-string message.

Use `isNotebookExportError(value)` when errors can cross an iframe or package
copy boundary:

```ts
import { isNotebookExportError } from "@marimo-team/marimo-export";

try {
  await output.load(loader);
} catch (error) {
  if (isNotebookExportError(error)) {
    reportExportFailure(error.code, error.details, error.cause);
  }
}
```

The guard checks the versioned global brand, error name, string message, known
code, and optional portable JSON `details`. Property access or validation
failure returns `false`.

Use [`defineBlobAssetLoader`](representations.md#custom-output) for a custom
media type.

[Consume an export](../guide/consume-an-export.md) compares browser, Python,
agent, and custom-client access.
