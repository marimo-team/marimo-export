---
title: Output loaders
description: Built-in output results, peer runtimes, mount ownership, cancellation, custom loaders, and prepared AnyWidget graphs.
---

# Output loaders

An `OutputLoader` accepts one codec and media type, validates its representation,
and returns browser data or a mountable value. `ExportOutput.load()` selects the
loader explicitly and verifies asset bytes before calling it.

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("/exports/report/");
const state = notebookExport.defaultState;
const summary = await state.output("summary").load(jsonLoader());
```

## Loader catalog

| Loader and import                                   | Accepted representation                             | Result                                       | Dependency                                     |
| --------------------------------------------------- | --------------------------------------------------- | -------------------------------------------- | ---------------------------------------------- |
| `scalarLoader()` from package root                  | `marimo.scalar.v1`                                  | `null`, boolean, string, number, or `bigint` | None                                           |
| `jsonLoader()` from `/loader/json`                  | `marimo.json.v1`                                    | Frozen portable `JsonValue`                  | None                                           |
| `textLoader()` from `/loader/text`                  | Non-HTML `text/*` with UTF-8 or unspecified charset | String                                       | None                                           |
| `htmlLoader()` from `/loader/html`                  | `text/html` with UTF-8 or unspecified charset       | Inert HTML source string                     | None                                           |
| `imageLoader()` from package root                   | Any image BlobAsset media type                      | `MountableValue`                             | Browser Blob and object URL APIs               |
| `numpyLoader()` from `/loader/numpy`                | `numpy.npy.v1`                                      | `NumpyArray`                                 | None                                           |
| `arrowTableLoader()` from `/loader/arrow`           | `apache.arrow.file.v1`                              | Flechette `Table`                            | `@uwdata/flechette ^2.5.0`, `lz4js 0.2.0`      |
| `parquetRowsLoader()` from `/loader/parquet`        | Apache Parquet BlobAsset                            | Frozen array of `ParquetRow`                 | `hyparquet ^1.26.2`                            |
| `vegaLiteLoader()` from `/loader/vegalite`          | Versioned Vega-Lite BlobAsset                       | `VegaLiteChart`                              | `vega-embed ^7.1.0`                            |
| `anyWidgetLoader()` from `/loader/anywidget`        | `application/vnd.marimo-export.anywidget.v1+json`   | `LoadedAnyWidget`                            | `@anywidget/types ^0.4.0` for TypeScript types |
| `marimoOutputLoader()` from `/loader/marimo-output` | `marimo.output.v1`                                  | `MarimoOutputSnapshot`                       | None                                           |
| `marimoCellLoader()` from `/loader/marimo-cell`     | `marimo.cell.v1`                                    | `MarimoCellSnapshot`                         | None                                           |

Install the package and the peers for the loaders your application imports:

```bash
pnpm add @marimo-team/marimo-export
pnpm add @uwdata/flechette lz4js
pnpm add hyparquet
pnpm add vega-embed
pnpm add @anywidget/types
```

The dependencies are optional at the package root. A specialized loader subpath
uses its listed dependency. [Output representations](../representations)
maps producer forms to these browser loaders. [Compatibility](../compatibility)
lists the supported peer version ranges.

## Scalar and JSON

### `scalarLoader()`

Returns an inline scalar without an asset request. The result can be `null`, a
boolean, string, number, or `bigint`. Tagged scalar values restore integers
outside the JavaScript safe range, NaN, positive or negative infinity, and
negative zero before the loader runs.

### `jsonLoader()`

Returns a detached recursively frozen portable JSON value. Numbers are finite,
integers stay within the JavaScript safe range, and negative zero has already
become zero. [Portable JSON](../portable-json) defines the exact value
contract.

## Text and HTML

`textLoader()` accepts a media type whose top-level type is `text`, except
`text/html`. `htmlLoader()` accepts `text/html`. Both accept an absent charset,
`charset=utf-8`, or `charset=utf8`. They decode with fatal UTF-8 validation and
reject malformed bytes or another declared charset.

`htmlLoader()` returns a string. It does not insert markup into the document.
Apply the application's sanitization, rendering, and trust policy before
inserting that string.

## Images

```ts
import { imageLoader } from "@marimo-team/marimo-export";

const image = await state.output("snapshot").load(imageLoader());
const mounted = await image.mount(document.querySelector("#snapshot")!);

window.addEventListener("pagehide", () => void mounted.dispose(), { once: true });
```

`imageLoader()` creates a Blob and object URL during `mount()`, appends an
`HTMLImageElement`, sets its `alt` text to the BlobAsset filename or an empty
string, and returns an idempotent disposal handle. Disposal removes the image
and revokes the object URL. Aborting the mount signal performs the same cleanup.

The mount resolves after inserting the image. It does not wait for image decode.
Call `HTMLImageElement.decode()` when visible commit depends on successful image
decoding.

Keep the mount alive while the image is visible. Dispose it during route
teardown or after a replacement commits. Supply a meaningful accessible name or
nearby text when the portable filename is not suitable alternative text.

## NumPy arrays

[NumPy's NPY format](https://numpy.org/doc/stable/reference/generated/numpy.lib.format.html)
stores an array header and contiguous value bytes.

```ts
import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";

const array = await state.output("matrix").load(numpyLoader());
console.log(array.shape, array.dtype, array.data);
```

```ts
type NumpyDTypeKind =
  "boolean" | "signed-integer" | "unsigned-integer" | "floating-point" | "complex-floating-point";

interface NumpyDType {
  readonly descriptor: string;
  readonly kind: NumpyDTypeKind;
  readonly itemSize: number;
  readonly byteOrder: "little" | "big" | "not-applicable";
}

interface NumpyArray {
  readonly data: ArrayBufferView;
  readonly shape: readonly number[];
  readonly dtype: NumpyDType;
  readonly fortranOrder: boolean;
}
```

The loader accepts NPY versions 1.0, 2.0, and 3.0. It supports one-byte boolean,
signed integers of 1, 2, 4, or 8 bytes, unsigned integers of the same widths,
floats of 2, 4, or 8 bytes, and complex values of 8 or 16 bytes.

The result uses `Int8Array`, `Int16Array`, `Int32Array`, `BigInt64Array`, the
matching unsigned arrays, `Float32Array`, or `Float64Array`. Float16 values are
expanded into `Float32Array`. Complex values are interleaved real and imaginary
components. Multi-byte data is normalized to the host's typed-array byte order.

`fortranOrder` reports the stored order. The loader does not transpose or
reorder values. The result object, `shape`, and `dtype` are frozen. The typed
array in `data` remains mutable.

## Apache Arrow

[Apache Arrow
IPC](https://arrow.apache.org/docs/format/Columnar.html#serialization-and-interprocess-communication-ipc)
stores a typed columnar table for exchange between runtimes.

```ts
import { arrowTableLoader } from "@marimo-team/marimo-export/loader/arrow";

const table = await state
  .output("table")
  .load(arrowTableLoader({ extraction: { useBigInt: true } }));
```

```ts
interface ArrowTableLoaderOptions {
  readonly extraction?: ExtractionOptions;
}
```

The loader returns a [`Table`](https://github.com/uwdata/flechette) from
Flechette. It accepts Arrow file and stream framing. `extraction.useBigInt`
defaults to `true`, then explicit extraction options override that default.

The loader registers the LZ4 frame codec through `lz4js`. A declared
decompressed LZ4 buffer is limited to 512 MiB and must produce its declared
length.

Arrow decoding is synchronous after the asset read. The loader checks an abort
signal before and after decoding.

## Parquet rows

[Apache Parquet](https://parquet.apache.org/docs/) stores columnar table data.
The loader uses [`hyparquet`](https://github.com/hyparam/hyparquet) to produce
row objects.

```ts
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";

const rows = await state.output("prices").load(
  parquetRowsLoader({
    columns: ["Symbol", "Close"],
    rowStart: 0,
    rowEnd: 100,
  }),
);
```

`ParquetRowsLoaderOptions` accepts Hyparquet's `ParquetReadOptions` except
`file`, `onComplete`, and `rowFormat`. The loader owns those fields and fixes the
result to object rows. It also accepts a `compressors` map.

```ts
type ParquetValue =
  | null
  | boolean
  | number
  | bigint
  | string
  | Date
  | Uint8Array
  | readonly ParquetValue[]
  | ParquetRow;

interface ParquetRow {
  readonly [column: string]: ParquetValue;
}
```

The returned row array is frozen. Row properties are readonly in TypeScript,
while the runtime does not recursively freeze each row or nested value.

Cancellation rejects the loader's wait and prevents the result from committing.
Hyparquet decoding that already started continues until its promise settles.

## Vega-Lite charts

[Vega-Lite](https://vega.github.io/vega-lite/) is a declarative JSON grammar for
interactive graphics. [`vega-embed`](https://github.com/vega/vega-embed) creates
and finalizes its browser view.

```ts
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const chart = await state.output("chart").load(vegaLiteLoader({ actions: false }));
const mounted = await chart.mount(document.querySelector("#chart")!, {
  renderer: "svg",
});

window.addEventListener("pagehide", () => void mounted.dispose(), { once: true });
```

```ts
type VegaLiteSpec = Readonly<JsonObject>;
type VegaLiteMountOptions = EmbedOptions & { readonly signal?: AbortSignal };

interface VegaLiteChart {
  readonly spec: VegaLiteSpec;
  mount(element: HTMLElement, options?: VegaLiteMountOptions): Promise<MountedVegaLite>;
}

interface MountedVegaLite extends MountedView {
  readonly result: VegaEmbedResult;
}
```

Loading decodes and freezes the portable chart specification. The options passed
to `vegaLiteLoader()` become mount defaults. Options passed to `mount()` override
them. The renderer defaults to `canvas`.

Mounting replaces the target element's children before importing
`vega-embed`. Use a staging host when failure must leave the visible view
unchanged. Disposal calls the Vega result's `finalize()`, clears the owned
container, and removes it. Cleanup remains idempotent if finalization throws.

An aborted dynamic import or embed operation can settle later. The loader
finalizes a late result and removes its staging container. A chart specification
can request external data or images. Apply the page's origin and Content
Security Policy to those requests.

## AnyWidget

[AnyWidget](https://anywidget.dev/) defines a model, initialization, render, and
cleanup lifecycle for browser widgets.

```ts
import { anyWidgetLoader } from "@marimo-team/marimo-export/loader/anywidget";

const widget = await state.output("explorer").load(anyWidgetLoader());
console.log(widget.initialState);

const mounted = await widget.mount(document.querySelector("#explorer")!);
mounted.model.set("metric", "Open");
mounted.model.save_changes();

window.addEventListener("pagehide", () => void mounted.dispose(), { once: true });
```

```ts
interface LoadedAnyWidget<State, Exports> {
  readonly initialState: Readonly<State>;
  mount(
    element: HTMLElement,
    options?: AnyWidgetMountOptions,
  ): Promise<MountedAnyWidget<State, Exports>>;
}

interface AnyWidgetMountOptions {
  readonly signal?: AbortSignal;
}

interface MountedAnyWidget<State, Exports> {
  readonly model: AnyModel<State>;
  readonly exports: Exports;
  dispose(): Promise<void>;
}
```

The subpath exports `ModelState` for open-ended saved state and
`AnyWidgetStateShape<State>` for an application-defined interface. Model fields
can contain `null`, booleans, numbers, strings, `DataView` buffers, arrays,
nested model-state objects, and `undefined`. `AnyModel` comes from
`@anywidget/types`.

`anyWidgetLoader<State, Exports>()` lets the application specialize the saved
model shape and the object returned by the widget's `initialize()` function.
Loading validates the complete reachable model graph, restores binary buffers,
and returns a frozen copy of the root model's saved state. Buffer `DataView`
instances remain mutable. Loading does not import the widget module.

The loader decodes the inner AnyWidget document with the browser's `JSON.parse()`
before graph validation. Duplicate keys therefore use the final decoded value.
The inner graph traversal has no separate portable JSON depth or value-count
limit. Apply a conservative `ExportOutput.load(loader, { maxBytes })` budget and
load AnyWidget payloads from a trusted publisher.

Mounting imports module definitions, creates an isolated browser-local model
graph, inserts model CSS into the element's document or shadow root, initializes
the graph, and renders the root view. Nested model references resolve through
the AnyWidget host and widget manager APIs.

`model.get()`, `set()`, change listeners, `save_changes()`, nested model lookup,
and exported initialization methods operate in the browser-local graph.
`save_changes()` acknowledges local dirty fields and sends no request to Python.
The experimental Python invocation API raises an error.

The mounted model implements the `AnyModel` shape with these browser-local
semantics:

| Member                             | Browser-local behavior                                            |
| ---------------------------------- | ----------------------------------------------------------------- |
| `get()` and `set()`                | Read and update one mount's isolated model state                  |
| `on()` and `off()`                 | Register and remove local change listeners                        |
| `save_changes()`                   | Clear local dirty-field bookkeeping without a network request     |
| `send(content, callback, buffers)` | Discard content and buffers, then queue a callable callback       |
| `widget_manager.get_model()`       | Resolve a model inside the mounted graph                          |
| `msg:custom` listeners             | Register successfully, with no kernel channel that emits messages |
| `experimental.invoke()`            | Reject because the exported widget has no Python kernel           |

Each mount owns separate models, views, styles, listeners, and cleanup
callbacks. Dispose a mount before reusing its element. Disposal runs view and
initialization cleanup in reverse binding order, clears the element and styles,
and aggregates cleanup failures.

### Module identity and trust

Module definitions are shared across package copies through one page-global
cache. The cache accepts at most 1,024 unique definitions and retains successful
imports for the page lifetime. Failed embedded imports leave the cache and can
retry through a fresh Blob URL. A browser cannot cancel dynamic module
evaluation after it begins.

Embedded virtual files contribute their complete source to cache identity.
Direct `data:`, HTTP, and HTTPS module URLs use the declared hash and canonical
URL as cache identity. The loader imports an HTTP response directly and does not
compare those remote source bytes with the declared hash. Remote module content
is outside the export's verified asset closure and can change after export
creation.

Mount trusted widget modules. A widget has page authority and can create
page-global side effects that survive disposal.

### `PreparedWidgetGraph`

The AnyWidget loader subpath also exports a transaction coordinator for an
application that replays several widget records into one long-lived model
registry.

```ts
interface PreparedWidgetGraphSnapshot<Record> {
  readonly files: Readonly<{ [path: string]: string }>;
  readonly records: ReadonlyMap<string, Record>;
}

interface PreparedWidgetGraphPort<Record, LiveState> {
  id(record: Record): string;
  active(record: Record): boolean;
  same(left: Record, right: Record): boolean;
  changesModule(previous: Record, next: Record): boolean;
  capture(id: string): LiveState;
  merge(record: Record, state: LiveState): Record;
  replay(record: Record, signal?: AbortSignal): Promise<void>;
  restore(id: string, state: LiveState): void | Promise<void>;
  close(id: string): Promise<void>;
  setFiles(files: Readonly<{ [path: string]: string }>): void;
  validate?(record: Record, signal?: AbortSignal): Promise<void>;
  preflight?(record: Record, signal?: AbortSignal): Promise<void>;
}

interface PreparedWidgetGraphReplacement<Record> {
  readonly mutated: boolean;
  readonly remount: boolean;
  commit(): Promise<PreparedWidgetGraphSnapshot<Record> | undefined>;
  rollback(): Promise<void>;
}

class PreparedWidgetGraphReplacementError extends Error {
  readonly remount: true;
  constructor(cause: Error);
}

class PreparedWidgetGraph<Record, LiveState> {
  constructor(
    port: PreparedWidgetGraphPort<Record, LiveState>,
    initial?: PreparedWidgetGraphSnapshot<Record>,
  );
  checkpoint(): PreparedWidgetGraphCheckpoint<Record>;
  replace(
    target: PreparedWidgetGraphSnapshot<Record> | PreparedWidgetGraphCheckpoint<Record>,
    signal?: AbortSignal,
  ): Promise<PreparedWidgetGraphReplacement<Record>>;
  dispose(): Promise<void>;
}
```

Given `port`, `initialSnapshot`, and `nextSnapshot` from the application's model
registry adapter, construct and settle a graph replacement. In this partial
example, `commitApplicationView()` is the application's atomic DOM commit:

```ts
const graph = new PreparedWidgetGraph(port, initialSnapshot);
const checkpoint = graph.checkpoint();
const replacement = await graph.replace(nextSnapshot, signal);

try {
  commitApplicationView();
  await replacement.commit();
} catch (error) {
  await replacement.rollback();
  throw error;
}

await graph.dispose();
```

`checkpoint()` captures live state for active records and returns an opaque
checkpoint owned by that graph. The graph must be idle.

`replace()` compares IDs and records through the port and stages the union of
the current and target file maps. It validates additions, stable updates, and
module replacements, then preflights additions and module replacements. A
staging failure restores the previous file map. The operation then replays
additions, applies stable updates, and closes and replays module replacements.
`commit()` closes removals before it installs and adopts the exact target file
map. If closing a removal fails, the target map remains uninstalled. Call
`rollback()` to restore the previous committed graph. The returned
`PreparedWidgetGraphReplacement` reports:

- `mutated`, whether files or active records changed
- `remount`, whether a model module changed and the outer view must remount
- `commit()`, which closes removals and adopts the target
- `rollback()`, which restores files, captured live state, and replaced records

Settle each replacement with `commit()` or `rollback()` before starting another
replacement or creating a checkpoint. Both settlement methods are idempotent.
The first successful settlement is final. A later settlement call has no effect.
When rollback settles first, a later `commit()` resolves to `undefined`. When a
commit attempt fails, `rollback()` can still restore the previous graph.

`PreparedWidgetGraphReplacementError` has `remount: true`. It means rollback
could not restore a trustworthy live registry or a failure occurred after a
module identity changed. Dispose the graph and rebuild the outer mount from the
last committed visible application view.

`dispose()` aborts active work, rolls back an unsettled replacement, closes each
active committed record, clears files, and aggregates cleanup failures.
`replace()` after disposal returns a settled no-op replacement. `checkpoint()`
after disposal raises `Error`.

## Custom output loaders

```ts
interface OutputLoader<C extends OutputCodec, T> {
  readonly codec: C;
  accepts(descriptor: DescriptorFor<C>, mediaType: MediaType): boolean;
  load(input: {
    readonly descriptor: DescriptorFor<C>;
    readonly mediaType: MediaType;
    readonly payload: OutputPayloadMap[C];
    readonly signal?: AbortSignal;
  }): T | Promise<T>;
}

interface OutputPayloadMap {
  readonly "marimo.scalar.v1": ScalarValue;
  readonly "marimo.json.v1": JsonValue;
  readonly "marimo.output.v1": Uint8Array;
  readonly "marimo.cell.v1": Uint8Array;
  readonly "numpy.npy.v1": Uint8Array;
  readonly "apache.arrow.file.v1": Uint8Array;
  readonly "marimo.blob-asset.msgpack.v1": BlobAsset;
}
```

`OutputCodec` is the key union of `OutputPayloadMap`. `DescriptorFor<C>` selects
the matching descriptor. `AnyOutputLoader` is the union of loaders across every
`OutputCodec`.

For BlobAsset representations, the loader receives these values:

```ts
interface MediaType {
  readonly raw: string;
  readonly essence: string;
  readonly type: string;
  readonly subtype: string;
  readonly parameters: ReadonlyMap<string, string>;
}

interface BlobAsset {
  readonly data: Uint8Array;
  readonly mediaType: MediaType;
  readonly filename: string | null;
  readonly metadata: JsonObject;
}

interface BlobAssetLoadInput {
  readonly descriptor: BlobAssetDescriptor;
  readonly mediaType: MediaType;
  readonly payload: BlobAsset;
  readonly signal?: AbortSignal;
}

type BlobAssetLoader<T> = OutputLoader<"marimo.blob-asset.msgpack.v1", T>;
```

`defineOutputLoader(loader)` validates and freezes a loader for one of the seven
closed version 1 codecs. Use it to adapt a native codec.

`defineBlobAssetLoader({ mediaTypes, load })` defines the extension path for a
custom BlobAsset. `mediaTypes` accepts one media-type string, a nonempty array of
strings, or a predicate over the parsed `MediaType`. String matching uses the
lowercase media-type essence and ignores parameters.

`resolveOutputLoader(output, loaders)` selects exactly one compatible loader
from the candidate loader array. No match raises `loader_unavailable`. More than one match
raises `loader_ambiguous`. A malformed loader or non-boolean `accepts()` result
raises `loader_invalid`.

Validate custom bytes, schema, allocation, and cancellation inside `load()`.
Preserve a `NotebookExportError` when it already describes the public failure.
Another thrown value becomes `decode_failed` at `ExportOutput.load()`.

## Mount and policy requirements

A mount receives an `HTMLElement` and returns a `MountedView` with idempotent
`dispose()`. The mount owns the resources it creates. Module side effects and
other page-global mutations can remain after disposal.

```ts
interface MountedView {
  dispose(): void | Promise<void>;
}

interface MountableValue {
  mount(element: HTMLElement, options?: { readonly signal?: AbortSignal }): Promise<MountedView>;
}
```

### Abort signal duration

Pass separate signals to `ExportOutput.load()` and a later `mount()` when both
phases can become stale. The built-in mount signals have these lifetimes:

| Mount               | While `mount()` is pending                                           | After `mount()` resolves                                     |
| ------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------ |
| `imageLoader()`     | Aborts construction and removes partial resources                    | Aborting disposes the image and revokes its Blob URL         |
| `vegaLiteLoader()`  | Races the module import and embed task, then cleans up a late result | Aborting has no effect. Call the returned `dispose()` handle |
| `anyWidgetLoader()` | Aborts initialization and rendering, then runs available cleanup     | Aborting starts disposal of the mounted model graph          |
| Custom mount        | Defined by the loader                                                | Define and document whether the signal owns the settled view |

Explicit `dispose()` remains the common ownership contract. Call it during route
teardown and after a complete replacement commits.

A restrictive [Content Security Policy
(CSP)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CSP) may need to
permit:

- Blob or data module sources used by embedded AnyWidgets
- explicitly allowed HTTP or HTTPS widget module origins
- widget-created style elements
- Blob image URLs created by `imageLoader()`
- data, image, font, or network origins referenced by Vega-Lite specifications

Keep the policy as narrow as the representations require. Test load, mount,
interaction, cancellation, and disposal in the deployed browser because a
successful package build cannot prove runtime policy compatibility.
