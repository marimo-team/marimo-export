# Browser loaders and mounts

The browser package opens one notebook export, resolves exact prepared states,
loads verified representations, and mounts interactive values into an
application-owned document.

## Package direction

```mermaid
flowchart TB
    app[Browser application]
    core[packages/browser]
    loaders[OutputLoader workspaces]
    anywidget[AnyWidget byte decoder]
    runtimes[Optional peer runtimes]

    app --> core
    app --> loaders
    loaders --> core
    core --> anywidget
    loaders --> runtimes
    anywidget --> runtimes
```

`packages/browser` owns canonical parsing, immutable reader values, asset
transport, integrity, native BlobAsset decoding, built-in loaders, and
`OutputLoader` contracts. Each loader workspace owns one specialized decoder,
result type, runtime dependency, cancellation behavior, and mount disposal.

Most loader workspaces implement `OutputLoader` against browser contracts. The
AnyWidget workspace accepts verified bytes and returns a loaded mount value. Its
public browser facade owns media matching and `BlobAssetLoader` construction, so
the package dependency remains one-way from browser to the AnyWidget decoder.

The public npm package exposes loader facades through
`@marimo-team/marimo-export/loader/*`. Vite+ prevents browser core from
importing specialized runtimes and prevents loader packages from importing one
another.

## Opening and resolution are immutable

`openExport(base)` fetches and validates `index.json`, then returns an immutable
`NotebookExport`. Its `identity` is the SHA-256 of the fetched canonical index
bytes. Assets remain lazy.

```ts
import { openExport } from "@marimo-team/marimo-export";

const notebookExport = await openExport("./export/");
const leaders = notebookExport.state("leaders");
const cloud = leaders.resolve({
  symbols_selector: ["MSFT", "GOOGL", "AMZN"],
});
```

`state(name)` selects an authored name. `resolve(completeInputs)` selects one
exact vector. `state.resolve(patch)` completes a sparse transition from the
current vector, then resolves the matching fingerprint.

The detached base URL cannot be mutated to redirect later asset requests. A
fixed base query is copied to the index and every asset URL. Derived object
paths cannot replace that query.

## The prepared subpath owns mutable selection

`@marimo-team/marimo-export/prepared` connects one mutable manifest route to
immutable notebook export instances. The strict core manifest is:

```json
{
  "schema": "marimo-export.prepared.v1",
  "instance": "<export identity>",
  "export_url": "./<instance>/",
  "inputs": {},
  "state_fingerprint": "<state fingerprint>",
  "refresh_interval_ms": 1000
}
```

The parser rejects unknown fields, bounds the export URL and refresh interval,
opens the immutable export, checks its identity and base URL, resolves the
complete input vector, and verifies the selected fingerprint.

`PreparedStateController` owns pending input intent, sparse input updates,
patchable control updates, URL query updates, supersession, cancellation,
publication replacement, settlement, and disposal. A control binding with an
`element` path stays application-owned and `updateControl()` returns `false`.
An application supplies one `PreparedStatePort`:

```ts
interface PreparedStatePort {
  apply(change: PreparedStateChange, signal: AbortSignal): Promise<void>;
  restore?(publication: PreparedPublication): void | Promise<void>;
  dispose?(): void | Promise<void>;
}
```

`apply()` loads every required output before publishing the complete state.
`restore()` returns application controls to the last committed publication after
a rejected transition.

`PreparedPublicationRefresh` fetches the manifest with `cache: "no-store"`.
When the export identity and base URL remain equal, it reuses the opened export.
When a new instance appears, it opens and validates that export before replacing
the publication. A local selection survives replacement when the input names
match and the replacement export resolves the complete current vector. The
controller adopts the manifest state when that vector is unavailable.

## Loading crosses the integrity boundary

`ExportOutput.load(loader, options)` requires one explicit loader whose codec
and media-type predicate accept the output descriptor.

Before invoking a representation runtime, browser core checks:

- same-origin relative asset path
- declared and caller byte limits
- response body availability and exact length
- SHA-256 digest
- codec framing
- BlobAsset envelope and descriptor agreement

The loader then validates representation shape, allocation bounds, and the
abort signal.

Browser core brands `NotebookExportError` with a versioned global symbol.
The constructor freezes each instance. The error class is an immutable direct
value. `isNotebookExportError()` checks the shared brand, public error name,
string message, known code set, and optional portable details. This preserves a
compatible error object across realms and separately bundled package copies.

| Loader                 | Application result                      | Runtime dependency               |
| ---------------------- | --------------------------------------- | -------------------------------- |
| `scalarLoader()`       | JSON-compatible scalar                  | None                             |
| `jsonLoader()`         | Immutable portable JSON                 | None                             |
| `marimoOutputLoader()` | Immutable rendered-output record        | None                             |
| `marimoCellLoader()`   | Immutable complete-cell record          | None                             |
| `textLoader()`         | UTF-8 text                              | None                             |
| `htmlLoader()`         | UTF-8 HTML source                       | None                             |
| `imageLoader()`        | Mountable image                         | Browser Blob and object URL APIs |
| `numpyLoader()`        | Shape, dtype, order, numeric buffer     | None                             |
| `arrowTableLoader()`   | Flechette table                         | `@uwdata/flechette`, `lz4js`     |
| `parquetRowsLoader()`  | Readonly row objects                    | `hyparquet`                      |
| `vegaLiteLoader()`     | Immutable specification and mount       | `vega-embed`                     |
| `anyWidgetLoader()`    | Saved model graph, model API, and mount | `@anywidget/types`               |

The Marimo output and cell loaders validate canonical replay records. They
return data that an application can adapt to its own renderer. The records
carry closed file resources, the reachable model lifecycle closure, and a
function map keyed by every projected UI object ID. `uiValues` carries the
accepted frontend value for each registry-owned UI object after state updates.
Model and UI object IDs are scoped by planned output so applications can merge
several projection records before committing one presentation state.

## A mount owns its resources

A mount receives an application element and returns an idempotent disposable
view. The handle owns its nodes, listeners, object URLs, renderer finalizers,
models, module URLs, styles, child views, and cleanup callbacks.

Opening, resolution, loading, and verification execute no notebook-authored
browser module. Mounting an interactive representation grants that code page
authority.

## The application commits visible state

The controller keeps the current publication until `PreparedStatePort.apply()`
succeeds. It aborts superseded work and asks the port to restore the last
committed publication after a rejected transition. The application port owns
output loading, DOM staging, visible commit, and mount disposal.

A DOM application can implement that port with two owners:

1. The load generation owns fetch and decode work until a newer request aborts
   it.
2. The committed mount owner remains active until a complete replacement
   commits.

```mermaid
sequenceDiagram
    actor User
    participant App as Transition owner
    participant Stage as Connected staging hosts
    participant Page as Visible document
    participant Old as Previous mount owner

    User->>App: Select state
    App->>App: Load every required output
    App->>Stage: Mount interactive replacements
    Stage-->>App: Disposable handles ready
    App->>Page: Commit hosts and data
    App->>Old: Abort and dispose
```

In this application pattern, staging hosts stay connected, measurable,
offscreen, and free of committed DOM IDs. A late generation bails out before
commit. Failed staging disposes the new handles and leaves the committed
application hosts visible.

The disposable releases resources owned by the mount. Module side effects and
other page-global mutations may persist after failed staging or disposal.

## AnyWidget interaction stays in the browser

Loading validates the saved model graph and restores buffers without importing
its frontend modules. Mounting imports modules and creates a browser-local
model registry. Model changes, listeners, nested resolution, and
`save_changes()` use that registry.

The page caches at most 1,024 module definitions by declared ESM hash and
canonical source identity. A versioned `Symbol.for` record on `globalThis`
gives independently bundled loader copies the same map and admission limit.
Successful imports remain cached for the page lifetime, which matches the
browser ESM registry. A failed cache promise is evicted. Embedded definitions
then retry through a fresh blob URL. Retry for direct data and HTTP URLs follows
the browser's native module-map behavior. Temporary embedded module URLs are
revoked when their import settles.

Each mount creates its own models, bindings, views, styles, and abort lifecycle.
Disposal releases those resources while a shared pending module import continues
for other mounts. The exported widget has no connection to its former Python
kernel.

Read [Agents and delivery](agents-and-delivery.md) for the example, packaging,
and browser evidence that exercise this lifecycle.
