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
import { openExport, scalarLoader } from "@marimo-team/marimo-export";

const notebookExport = await openExport("/exports/report/");
const state = notebookExport.state("baseline");
const title = await state.output("title").load(scalarLoader());

document.querySelector("#title")!.textContent = String(title);
```

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

- `state(name)` selects one authored state name.
- `resolve(inputs)` selects one complete exported input vector.
- `state.resolve(patch)` completes a sparse transition from the current state.

Resolution selects results already present in the export.

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

AnyWidget starts from its saved model state. Its browser interactions call no
Python kernel.

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
`loader_unavailable`, `integrity_failed`, `read_limit_exceeded`, and `abort`.

Use [`defineBlobAssetLoader`](representations.md#custom-output) for a custom
media type.

[Consume an export](../guide/consume-an-export.md) compares browser, Python,
agent, and custom-client access.
