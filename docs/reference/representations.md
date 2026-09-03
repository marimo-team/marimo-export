---
title: Output representations
description: Stored output forms, Python access, browser loaders, agent suitability, peer dependencies, and custom BlobAsset pairs.
---

# Output representations

An output representation is the stored form of one published notebook result.
It determines which human-facing applications, agents, Python tools, and
browser clients can interpret that output.

| Notebook result        | OutputSpec form or exporter        | Python access   | Browser loader         | Agent use                              |
| ---------------------- | ---------------------------------- | --------------- | ---------------------- | -------------------------------------- |
| JSON-compatible value  | `OutputSpec.json()`                | `json()`        | `jsonLoader()`         | Summaries, records, and arrays         |
| Native scalar          | `OutputSpec.native()`              | `scalar()`      | `scalarLoader()`       | Metrics, labels, statuses, identifiers |
| Native NumPy array     | `OutputSpec.native()`              | `asset_bytes()` | `numpyLoader()`        | Numeric arrays with NPY tooling        |
| Native Arrow table     | `OutputSpec.native()`              | `asset_bytes()` | `arrowTableLoader()`   | Columnar data with Arrow tooling       |
| Native BlobAsset       | `OutputSpec.native()`              | `blob_asset()`  | Matching blob loader   | Media-typed application data           |
| Rendered Marimo output | `OutputSpec.output()`              | `asset_bytes()` | `marimoOutputLoader()` | Inert output and replay records        |
| Complete Marimo cell   | `OutputSpec.cell()`                | `asset_bytes()` | `marimoCellLoader()`   | Output, console, and cell provenance   |
| Text                   | `blob.text`                        | `blob_asset()`  | `textLoader()`         | Reports, labels, and source text       |
| HTML                   | `blob.html`                        | `blob_asset()`  | `htmlLoader()`         | Authored document fragments            |
| Table rows             | `parquet.table`                    | `blob_asset()`  | `parquetRowsLoader()`  | Tables, filtering, and aggregation     |
| Altair chart           | `altair.vegalite`                  | `blob_asset()`  | `vegaLiteLoader()`     | Chart specification and companion view |
| Chart image            | `altair.png`                       | `blob_asset()`  | `imageLoader()`        | Visual companion                       |
| AnyWidget              | `anywidget.bundle`                 | `blob_asset()`  | `anyWidgetLoader()`    | Saved state and interactive review     |
| Custom value           | `OutputSpec.export()` and callable | `blob_asset()`  | Custom loader          | Depends on its media type and schema   |

The codec identifies the stable native envelope. A BlobAsset media type
identifies the representation inside that envelope. Browser applications
select one codec-aware loader explicitly. Agents should select representations
their available tools can decode.

Every descriptor records the originating `python_type`. Producer-local Marimo
cache paths are not part of the portable representation contract.

When a state needs execution, custom exporter leaves call their loaded callable
for that state. Declared dependency modules bind source identity and drift
checks. `anywidget.bundle` also captures current model state. A reusable prepared
state skips the exporter call.

`marimo.output.v1` and `marimo.cell.v1` are renderer-neutral records. Their
replay resources contain closed file data URLs, reachable AnyWidget model
notifications, accepted UI values, and a function-name map for each concrete
UI object in the projection. Model and UI object IDs are scoped by planned
output, so several records can share one presentation state. Loading returns
immutable data. An application chooses how to adapt those records to its
renderer and lifecycle.

## Choose outputs for agents

For agent-oriented publication, combine:

- one concise scalar or versioned JSON summary
- one inspectable Parquet, Arrow, or NumPy output
- one chart, image, or widget when visual review helps

An image or interactive widget can support a human review while a paired table
or JSON record supplies machine-readable evidence. [Use with
agents](../guide/agents-and-automation.md) defines the grounding workflow and
evidence identity.

## Browser peer dependencies

Install the runtime used by each imported loader:

| Loader                           | Peer dependency                 |
| -------------------------------- | ------------------------------- |
| JSON, Marimo output, Marimo cell | None                            |
| Scalar, text, HTML, image        | None                            |
| NumPy                            | None                            |
| Arrow                            | `@uwdata/flechette` and `lz4js` |
| Parquet                          | `hyparquet`                     |
| Vega-Lite                        | `vega-embed`                    |
| AnyWidget                        | `@anywidget/types`              |

```bash
pnpm add @marimo-team/marimo-export hyparquet vega-embed
```

Import loaders from public subpaths:

```ts
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import { marimoCellLoader } from "@marimo-team/marimo-export/loader/marimo-cell";
import { marimoOutputLoader } from "@marimo-team/marimo-export/loader/marimo-output";
import { textLoader } from "@marimo-team/marimo-export/loader/text";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";
```

`htmlLoader()` returns the verified UTF-8 source string. Apply the
application's rendering and trust policy before inserting that string into the
document.

## Exporter options

| Exporter           | Options                              |
| ------------------ | ------------------------------------ |
| `altair.vegalite`  | None                                 |
| `altair.png`       | `scale`                              |
| `anywidget.bundle` | None                                 |
| `parquet.table`    | `compression`, `filename`            |
| `blob.json`        | `media_type`, `filename`, `metadata` |
| `blob.text`        | `media_type`, `filename`, `metadata` |
| `blob.html`        | `filename`, `metadata`               |

Typed exporter factories live under `marimo_export.exporters`.

## Custom output

A Python exporter converts one notebook result into a `BlobAsset`:

```python
import json

from marimo_export.outputs import BlobAsset


def encode_summary(value: object) -> BlobAsset:
    return BlobAsset(
        data=json.dumps(value).encode(),
        media_type="application/vnd.example.summary.v1+json",
        filename="summary.json",
    )
```

An ExportSpec selects that callable:

```yaml
outputs:
  summary:
    source: { kind: export, selector: report }
    exporter:
      name: summary_exporter:encode_summary
      options: {}
      dependencies:
        - json
```

A browser loader handles the same media type:

```ts
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";

export const summaryLoader = defineBlobAssetLoader({
  mediaTypes: "application/vnd.example.summary.v1+json",
  load({ payload }) {
    return JSON.parse(new TextDecoder().decode(payload.data));
  },
});
```

The loader can return data or a value with a browser `mount()` method. A mount
must return an idempotent disposable view.

Use a versioned media type for a custom representation consumed by agents or a
bespoke frontend. Validate the payload shape in the consumer before using its
data.
