# marimo-export contracts

## ExportSpec

Declare one default, sparse states, and published outputs:

```yaml
schema: marimo-export.spec.v1
default_state: national
states:
  national: {}
  northeast:
    region: Northeast
  strict:
    threshold_slider: 0.8
outputs:
  headline:
    source: { kind: value, selector: summary }
  chart:
    source: { kind: value, selector: comparison_chart }
    exporter: altair.vegalite
  rows:
    source: { kind: value, selector: filtered_table }
    exporter: parquet.table
```

Planning infers inputs from output dependencies and state-row keys. Sparse rows
inherit omitted inputs from one baseline. Equivalent rows retain their aliases
and share one fingerprint.

Run `marimo-export inspect NOTEBOOK --json` to discover definitions and cells.
Run `marimo-export plan NOTEBOOK --spec FILE --json` to inspect normalized and
reusable work.

## Preparation

```python
from marimo_export import ExportSpec, prepare

spec = ExportSpec.from_file("app.export.yaml")
with prepare("notebook.py", spec=spec) as prepared:
    prepared.write("public/export", replace=True)
```

`prepare()` opens a notebook only when repository work is missing. `capture()`
returns the same leased `PreparedExport` from a named live session.

`PreparedExport` can:

- open the immutable `NotebookExport`
- lease one file through `asset(relative)`
- create `marimo-export.prepared.v1` browser metadata through `manifest()`
- copy and verify a deployment directory through `write()`
- report prepared and reused state fingerprints

Keep the handle open while serving its files. Close independently leased assets
after their responses finish.

## Representations

| Notebook result | Exporter or source             | Browser loader         |
| --------------- | ------------------------------ | ---------------------- |
| JSON value      | `OutputSpec.value()`           | `jsonLoader()`         |
| Marimo output   | `OutputSpec.output()`          | `marimoOutputLoader()` |
| Marimo cell     | `OutputSpec.cell()`            | `marimoCellLoader()`   |
| Scalar          | scalar exporter                | `scalarLoader()`       |
| NumPy array     | array exporter                 | `numpyLoader()`        |
| Arrow table     | table exporter                 | `arrowTableLoader()`   |
| DataFrame       | `parquet.table`                | `parquetRowsLoader()`  |
| Altair chart    | `altair.vegalite`              | `vegaLiteLoader()`     |
| PNG             | `altair.png`                   | `imageLoader()`        |
| AnyWidget       | `anywidget.bundle`             | `anyWidgetLoader()`    |
| Custom value    | callable returning `BlobAsset` | custom loader          |

Each descriptor exposes codec, media type, originating Python type, and an inline
value or content-addressed asset.

## Immutable browser entry

```ts
import { openExport } from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";

const notebookExport = await openExport("./export/");
const state = notebookExport.defaultState;
const headline = await state.output("headline").load(jsonLoader());
```

Use `state(alias)`, `resolve(completeInputs)`, or `state.resolve(patch)` to select
another exported vector.

## Prepared browser entry

```ts
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import {
  PreparedPublicationRefresh,
  PreparedStateController,
  type PreparedStatePort,
} from "@marimo-team/marimo-export/prepared";

const port: PreparedStatePort = {
  async apply({ next }, signal) {
    const headline = await next.state.output("headline").load(jsonLoader(), { signal });
    signal.throwIfAborted();
    document.querySelector("#headline")!.textContent = String(headline);
  },
};

const controller = new PreparedStateController(port);
const manifestUrl = new URL("/runtime/prepared.json", location.href);
const refresh = new PreparedPublicationRefresh(manifestUrl, controller);

await refresh.start();
await controller.updateInputs({ region: "Northeast" });
```

The port loads and commits the complete application state. Stage multi-output
views before changing visible hosts. `restore()` can reinstate the last committed
publication after a failure.

## Custom BlobAsset pair

Python:

```python
import json

from marimo_export.outputs import BlobAsset


def encode(value: object) -> BlobAsset:
    return BlobAsset(
        data=json.dumps(value).encode(),
        media_type="application/vnd.example.summary.v1+json",
        filename="summary.json",
    )
```

TypeScript:

```ts
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";

const summaryLoader = defineBlobAssetLoader({
  mediaTypes: "application/vnd.example.summary.v1+json",
  load({ payload, signal }) {
    signal?.throwIfAborted();
    return JSON.parse(new TextDecoder().decode(payload.data));
  },
});
```

## Trust boundary

`build`, `prepare`, `capture`, planning that requires inspection, and notebook
inspection execute notebook or exporter code with the environment's file,
credential, network, and package access.

Opening and verifying parse inert records. Mounting AnyWidget, Vega-Lite, or a
custom interactive value grants its module browser page authority.
