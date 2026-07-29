# @marimo-team/marimo-export

Open a static marimo publication, select a precomputed state, and load its
outputs in a browser application.

```bash
pnpm add @marimo-team/marimo-export
```

```ts
import { openPublication, scalarLoader } from "@marimo-team/marimo-export";

const publication = await openPublication("/publications/finance/");
const state = publication.state("baseline");
const count = await state.output("row_count").load(scalarLoader());
```

`openPublication(base, options?)` fetches `index.json` and validates canonical
JSON, publication structure, complete input vectors, and state fingerprints.
Output assets remain lazy until `load()` or `verify()` reads them.

## Load published representations

The package root provides publication APIs, `scalarLoader()`, and
`imageLoader()`. Rich representations use explicit loader subpaths:

| Loader              | Import subpath     | Install with the package     |
| ------------------- | ------------------ | ---------------------------- |
| `anyWidgetLoader`   | `loader/anywidget` | `@anywidget/types`           |
| `arrowTableLoader`  | `loader/arrow`     | `@uwdata/flechette`, `lz4js` |
| `numpyLoader`       | `loader/numpy`     | no additional peer           |
| `parquetRowsLoader` | `loader/parquet`   | `hyparquet`                  |
| `vegaLiteLoader`    | `loader/vegalite`  | `vega-embed`                 |

For example, a client that loads Parquet rows and Vega-Lite charts installs:

```bash
pnpm add @marimo-team/marimo-export hyparquet vega-embed
```

```ts
import { openPublication } from "@marimo-team/marimo-export";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const publication = await openPublication("/publications/finance/");
const state = publication.state("baseline");

const rows = await state.output("prices").load(parquetRowsLoader());
const chart = await state.output("chart").load(vegaLiteLoader());
const mounted = await chart.mount(document.querySelector("#chart")!);
```

Loader runtimes are optional peer dependencies. Importing the package root
keeps them out of the application dependency graph. Importing a loader subpath
requires the peers listed for that loader.

## Resolve a published state

```ts
const next = state.resolve({ chart_width: 480 });
const exact = publication.resolve({
  symbols: ["AAPL", "MSFT"],
  chart_width: 480,
});
```

`Publication.resolve()` requires a complete input vector.
`PublishedState.resolve()` merges a sparse patch with that state's vector.
Both return an existing published state or raise `PublicationError` with
`code === "state_unavailable"`.

## Define a custom loader

Each output declares one stable codec and one media type. Use
`defineBlobAssetLoader()` to decode a custom `BlobAsset` representation:

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

The publication reader selects the supplied loader, verifies the asset,
decodes the native codec envelope, and calls the loader. Media-specific
allocation and execution behavior belongs to the loader.

## Cancel and verify

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
