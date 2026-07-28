# Browser API

Install core plus the loader families used by the application:

```bash
pnpm add \
  @marimo-team/marimo-export \
  @marimo-team/marimo-export-loader-arrow \
  @marimo-team/marimo-export-loader-numpy \
  @marimo-team/marimo-export-loader-parquet \
  @marimo-team/marimo-export-loader-anywidget \
  @marimo-team/marimo-export-loader-vegalite
```

## Open and resolve

```ts
const publication = await openPublication("/publications/notebook/");

const baseline = publication.state("baseline");
const compact = baseline.resolve({ chart_width: 480 });
const exact = publication.resolve({
  symbols: ["AAPL", "MSFT"],
  chart_width: 480,
});
```

Publication, state, descriptor, input, and metadata objects are immutable.
Opening reads `index.json`. Output loading reads assets lazily.

## Load

```ts
const count = await state.output("row_count").load(scalarLoader());
const matrix = await state.output("matrix").load(numpyLoader());
const table = await state.output("prices").load(arrowTableLoader());
const rows = await state.output("prices_file").load(parquetRowsLoader());
```

`PublishedOutput.load(loader, options?)` checks that the loader's codec and
media-type predicate match. The core then fetches, bounds, hashes, validates,
and decodes the native payload before calling the loader.

## Mount

```ts
const chart = await state.output("chart").load(vegaLiteLoader());
const mounted = await chart.mount(host, { renderer: "svg" });

await mounted.dispose();
```

Interactive values own their DOM, object URLs, listeners, module state, and
finalizers. Dispose a mount before replacing its host. Pass one `AbortSignal`
through load and mount to cancel stale state transitions.

## Custom BlobAssetLoader

```ts
const loader = defineBlobAssetLoader({
  mediaTypes: "application/vnd.example.summary.v1+json",
  load({ payload, signal }) {
    signal?.throwIfAborted();
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(payload.data));
  },
});
```

The media type owns representation identity. A custom loader can use any client
runtime, including table or query libraries selected by the application.

## Verify

```ts
const result = await publication.verify({
  maxBytes: 512 * 1024 * 1024,
  maxTotalBytes: 2 * 1024 * 1024 * 1024,
});
```

Verification reads every unique declared asset and returns state, output,
asset, and byte counts.
