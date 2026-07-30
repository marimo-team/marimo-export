# marimo-export contracts

## Contents

- [ExportSpec](#exportspec)
- [Representations](#representations)
- [Browser entry](#browser-entry)
- [Transition ownership](#transition-ownership)
- [Custom BlobAsset pair](#custom-blobasset-pair)
- [Trust boundary](#trust-boundary)

## ExportSpec

An ExportSpec contains notebook definition names, sparse named states, and
browser-facing outputs:

```yaml
schema: marimo-export.spec.v1
inputs:
  - region
  - threshold_slider
states:
  national: {}
  northeast:
    region: Northeast
  strict:
    threshold_slider: 0.8
outputs:
  headline:
    source: summary
  chart:
    source: comparison_chart
    exporter: altair.vegalite
  rows:
    source: filtered_table
    exporter:
      name: parquet.table
      options:
        compression: snappy
        filename: rows.parquet
```

Each state is a complete input assignment after baseline normalization. State
rows may omit values that should stay at their baseline.

Inputs can target ordinary authored definitions or marimo UI element
definitions. Use the UI element's definition name, not `.value`.

Outputs map a browser name to a notebook definition. The source definition must
exist after the selected state runs.

## Representations

| Notebook result    | Exporter                       | Browser loader        |
| ------------------ | ------------------------------ | --------------------- |
| scalar             | omit                           | `scalarLoader()`      |
| NumPy array        | omit                           | `numpyLoader()`       |
| Arrow table        | omit                           | `arrowTableLoader()`  |
| table or DataFrame | `parquet.table`                | `parquetRowsLoader()` |
| Altair chart       | `altair.vegalite`              | `vegaLiteLoader()`    |
| Altair chart image | `altair.png`                   | `imageLoader()`       |
| AnyWidget          | `anywidget.bundle`             | `anyWidgetLoader()`   |
| custom value       | callable returning `BlobAsset` | custom loader         |

Install the peer dependency owned by each imported loader:

| Loader    | Peer dependency              |
| --------- | ---------------------------- |
| Arrow     | `@uwdata/flechette`, `lz4js` |
| Parquet   | `hyparquet`                  |
| Vega-Lite | `vega-embed`                 |
| AnyWidget | `@anywidget/types`           |

## Browser entry

```ts
import { openExport, scalarLoader } from "@marimo-team/marimo-export";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const notebookExport = await openExport("./export/");
const state = notebookExport.state("national");

const headline = await state.output("headline").load(scalarLoader());
const rows = await state.output("rows").load(parquetRowsLoader());
const chart = await state.output("chart").load(vegaLiteLoader({ actions: false }));
```

`openExport()` reads `index.json`. Assets load when an output loader requests
them.

Use `notebookExport.state(name)` for a named choice. Use
`notebookExport.resolve(completeInputs)` for a complete vector, or
`state.resolve(patch)` for a sparse transition from a current state.

## Transition ownership

Keep abort and disposal in one controller:

```ts
import type { MountedView } from "@marimo-team/marimo-export";

let transition: AbortController | undefined;
let mounted: MountedView[] = [];

async function selectState(name: string): Promise<void> {
  transition?.abort();
  const controller = new AbortController();
  transition = controller;

  await Promise.allSettled(mounted.map((view) => view.dispose()));
  mounted = [];

  const state = notebookExport.state(name);
  const [rows, chart] = await Promise.all([
    state.output("rows").load(parquetRowsLoader(), {
      signal: controller.signal,
    }),
    state.output("chart").load(vegaLiteLoader({ actions: false }), {
      signal: controller.signal,
    }),
  ]);

  controller.signal.throwIfAborted();
  renderRows(rows);
  const chartView = await chart.mount(chartElement, {
    signal: controller.signal,
  });
  controller.signal.throwIfAborted();
  mounted = [chartView];
}
```

Dispose every mounted value when replacing it. Abort work again during page
teardown.

## Custom BlobAsset pair

Use a custom exporter when the notebook value needs a representation that the
built-ins do not provide.

Python:

```python
import json

from marimo_export import BlobAsset


def encode(value: object) -> BlobAsset:
    return BlobAsset(
        data=json.dumps(value).encode(),
        media_type="application/vnd.example.summary.v1+json",
        filename="summary.json",
    )
```

ExportSpec:

```yaml
outputs:
  summary:
    source: report
    exporter: summary_exporter:encode
```

TypeScript:

```ts
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";

interface Summary {
  readonly total: number;
}

const summaryLoader = defineBlobAssetLoader<Summary>({
  mediaTypes: "application/vnd.example.summary.v1+json",
  load({ payload, signal }) {
    signal?.throwIfAborted();
    return JSON.parse(new TextDecoder().decode(payload.data)) as Summary;
  },
});
```

Keep the exporter module in the app directory and add that directory to
`PYTHONPATH` for notebook startup, build, and capture. Validate untrusted bytes
inside the loader before returning an application value.

## Trust boundary

`build` and `capture` execute notebook and exporter code with the environment's
file, credential, and network access.

Opening and verifying an export do not execute notebook-authored browser code.
Mounting AnyWidget, Vega-Lite, or custom interactive values grants that code
page authority.

Verify every export before deployment. Serve `index.json` and the referenced
assets from the same static directory.
