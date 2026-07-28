# @marimo-team/marimo-export

The browser package opens a canonical marimo publication over HTTP, resolves
finite states, verifies content-addressed assets, and hands each native payload
to an explicit `OutputLoader`.

```bash
pnpm add @marimo-team/marimo-export
```

```ts
import { openPublication, scalarLoader } from "@marimo-team/marimo-export";

const publication = await openPublication("/publications/finance/");
const state = publication.state("baseline");
const rows = await state.output("row_count").load(scalarLoader());
```

`openPublication(base, options?)` fetches `index.json` and validates canonical
JSON, the publication schema, complete input vectors, and state fingerprints.
It fetches assets when `load()` or `verify()` requests them.

```ts
const next = state.resolve({ chart_width: 480 });
const exact = publication.resolve({
  symbols: ["AAPL", "MSFT"],
  chart_width: 480,
});
```

`Publication.resolve()` requires the complete input vector.
`PublishedState.resolve()` merges a sparse patch with that state's vector.
Both return an existing published state or raise `PublicationError` with
`code === "state_unavailable"`.

## OutputLoader

Each output declares one stable codec and one media type. The core package
supports:

```text
marimo.scalar.v1
numpy.npy.v1
apache.arrow.file.v1
marimo.blob-asset.msgpack.v1
```

Loaders declare the codec at the type level and inspect the parsed media type:

```ts
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";

const geoJsonLoader = defineBlobAssetLoader({
  mediaTypes: "application/geo+json",
  load({ payload, signal }) {
    signal?.throwIfAborted();
    return JSON.parse(new TextDecoder().decode(payload.data));
  },
});

const regions = await state.output("regions").load(geoJsonLoader);
```

The publication reader selects exactly one supplied loader, verifies the asset,
decodes the native codec envelope, and calls that loader. Media-specific
allocation and execution behavior belongs to the loader package.

Core includes `scalarLoader()` and `imageLoader()`. Specialized packages
provide NumPy, Arrow, Parquet, AnyWidget, and Vega-Lite values.

## Cancellation and verification

```ts
const controller = new AbortController();
const table = await output.load(loader, {
  signal: controller.signal,
  maxBytes: 256 * 1024 * 1024,
});

const result = await publication.verify({
  maxBytes: 256 * 1024 * 1024,
  maxTotalBytes: 1024 * 1024 * 1024,
});
```

Every mounted value returns a `MountedView`. Call `dispose()` when replacing
its host or leaving the page. Aborting a mount also triggers the loader's
cleanup path.

`PublicationError` exposes a stable `code`, optional JSON `details`, and the
original `cause`. Digest failures use `integrity_failed`. Missing loaders use
`loader_unavailable`. Ambiguous matches use `loader_ambiguous`.
