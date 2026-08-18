---
title: Output representations
description: Stored output forms, Python access, browser loaders, agent suitability, peer dependencies, and custom BlobAsset pairs.
---

# Output representations

An output representation is the stored form of one published notebook result.
It determines which human-facing applications, agents, Python tools, and
browser clients can interpret that output.

| Notebook result | Exporter                            | Python access   | Browser loader        | Agent use                              |
| --------------- | ----------------------------------- | --------------- | --------------------- | -------------------------------------- |
| Scalar          | Omit                                | `scalar()`      | `scalarLoader()`      | Metrics, labels, statuses, identifiers |
| NumPy array     | Omit                                | `asset_bytes()` | `numpyLoader()`       | Numeric arrays with NPY tooling        |
| Arrow table     | Omit                                | `asset_bytes()` | `arrowTableLoader()`  | Columnar data with Arrow tooling       |
| Table rows      | `parquet.table`                     | `blob_asset()`  | `parquetRowsLoader()` | Tables, filtering, and aggregation     |
| Altair chart    | `altair.vegalite`                   | `blob_asset()`  | `vegaLiteLoader()`    | Chart specification and companion view |
| Chart image     | `altair.png`                        | `blob_asset()`  | `imageLoader()`       | Visual companion                       |
| AnyWidget       | `anywidget.bundle`                  | `blob_asset()`  | `anyWidgetLoader()`   | Saved state and interactive review     |
| Custom value    | Function that returns a `BlobAsset` | `blob_asset()`  | Custom loader         | Depends on its media type and schema   |

The codec identifies the stable native envelope. A BlobAsset media type
identifies the representation inside that envelope. Browser applications
select one codec-aware loader explicitly. Agents should select representations
their available tools can decode.

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

| Loader        | Peer dependency                 |
| ------------- | ------------------------------- |
| Scalar, image | None                            |
| NumPy         | None                            |
| Arrow         | `@uwdata/flechette` and `lz4js` |
| Parquet       | `hyparquet`                     |
| Vega-Lite     | `vega-embed`                    |
| AnyWidget     | `@anywidget/types`              |

```bash
pnpm add @marimo-team/marimo-export hyparquet vega-embed
```

Import loaders from public subpaths:

```ts
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";
```

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

from marimo_export import BlobAsset


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
    source: report
    exporter: summary_exporter:encode_summary
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
