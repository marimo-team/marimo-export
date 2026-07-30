# Browser API

The browser package opens an export, selects a prepared state, and loads its
outputs.

```bash
pnpm add @marimo-team/marimo-export
```

```ts
import { openExport, scalarLoader } from "@marimo-team/marimo-export";

const notebookExport = await openExport("/exports/report/");
const state = notebookExport.state("baseline");
const title = await state.output("title").load(scalarLoader());

document.querySelector("#title")!.textContent = String(title);
```

`openExport()` fetches and validates `index.json`. Output files load on demand.

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

`state(name)` selects by ExportSpec name. `resolve(inputs)` selects the state
with that complete input set. `state.resolve(patch)` applies a smaller change
to an existing state.

Resolution selects results already present in the export.

## Load outputs

```ts
import { imageLoader } from "@marimo-team/marimo-export";
import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";

const matrix = await state.output("matrix").load(numpyLoader());
const rows = await state.output("prices").load(parquetRowsLoader());
const image = await state.output("snapshot").load(imageLoader());
```

[Choose a loader and install its runtime](representations.md).

Pass a signal to cancel stale work and `maxBytes` to cap one output:

```ts
const controller = new AbortController();
const rows = await state.output("prices").load(parquetRowsLoader(), {
  signal: controller.signal,
  maxBytes: 256 * 1024 * 1024,
});
```

## Mount interactive output

```ts
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const chart = await state.output("chart").load(vegaLiteLoader({ actions: false }));
const mounted = await chart.mount(document.querySelector("#chart")!, {
  renderer: "svg",
});

await mounted.dispose();
```

Dispose a mounted chart or widget before replacing it. Use the same abort
signal for `load()` and `mount()` when state changes may overlap.

AnyWidget starts from its saved model state. Its browser interactions do not
call Python.

## Verify an export

```ts
const result = await notebookExport.verify({
  maxBytes: 512 * 1024 * 1024,
  maxTotalBytes: 2 * 1024 * 1024 * 1024,
});
```

`verify()` checks every asset and returns state, output, asset, and byte counts.

## Errors and custom loaders

`NotebookExportError` provides `code`, optional `details`, and `cause`. Common
codes include `state_not_found`, `state_unavailable`, `output_not_found`,
`loader_unavailable`, `integrity_failed`, `read_limit_exceeded`, and `abort`.

Use [`defineBlobAssetLoader`](representations.md#custom-output) for a custom
media type.
