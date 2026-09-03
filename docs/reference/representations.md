---
title: Output representations
description: Stored output forms, producer choices, consumer access, peer dependencies, and custom BlobAsset pairs.
---

# Output representations

An output representation is the stored form of one published notebook result.
It determines which applications, agents, Python tools, and browser clients can
interpret that output.

| Notebook result           | OutputSpec form or exporter        | Python access   | Browser loader         | Agent use                                  |
| ------------------------- | ---------------------------------- | --------------- | ---------------------- | ------------------------------------------ |
| JSON-compatible value     | `OutputSpec.json()`                | `json()`        | `jsonLoader()`         | Summaries, records, and arrays             |
| Native scalar             | `OutputSpec.native()`              | `scalar()`      | `scalarLoader()`       | Metrics, labels, statuses, and identifiers |
| Native NumPy array        | `OutputSpec.native()`              | `asset_bytes()` | `numpyLoader()`        | Numeric arrays with NPY tooling            |
| Native Apache Arrow table | `OutputSpec.native()`              | `asset_bytes()` | `arrowTableLoader()`   | Columnar data with Arrow tooling           |
| Native BlobAsset          | `OutputSpec.native()`              | `blob_asset()`  | Matching blob loader   | Media-typed application data               |
| Rendered marimo output    | `OutputSpec.output()`              | `asset_bytes()` | `marimoOutputLoader()` | Inert output and replay records            |
| Complete marimo cell      | `OutputSpec.cell()`                | `asset_bytes()` | `marimoCellLoader()`   | Output, console, and cell provenance       |
| Text                      | `blob.text`                        | `blob_asset()`  | `textLoader()`         | Reports, labels, and source text           |
| HTML                      | `blob.html`                        | `blob_asset()`  | `htmlLoader()`         | Authored document fragments                |
| Table rows                | `parquet.table`                    | `blob_asset()`  | `parquetRowsLoader()`  | Tables, filtering, and aggregation         |
| Altair chart              | `altair.vegalite`                  | `blob_asset()`  | `vegaLiteLoader()`     | Chart specification and companion view     |
| Chart image               | `altair.png`                       | `blob_asset()`  | `imageLoader()`        | Visual companion                           |
| AnyWidget                 | `anywidget.bundle`                 | `blob_asset()`  | `anyWidgetLoader()`    | Saved state and browser-local interaction  |
| Custom value              | `OutputSpec.export()` and callable | `blob_asset()`  | Custom loader          | Depends on its media type and schema       |

The codec identifies the stable native envelope. A BlobAsset media type
identifies the representation inside that envelope. Browser applications select
one codec-aware loader explicitly.

Every descriptor records the originating `python_type`. Producer-local marimo
cache paths are outside the portable representation contract.

When an exported state needs execution, a custom exporter runs for that state.
Declared dependency modules contribute to exporter source identity and drift
checks. `anywidget.bundle` also captures current model state. Reusing a prepared
state reuses its representation bytes.

## Choose representations for agents

For agent-oriented publication, combine:

- one concise scalar or versioned JSON summary
- one inspectable Parquet, Arrow, or NumPy output
- one chart, image, or widget when visual review is part of the task

An image or interactive widget supports human review, while a paired table or
JSON record supplies machine-readable evidence. [Use notebook exports with
agents](../guide/agents-and-automation.md) defines the grounding workflow and
evidence identity.

## Browser runtimes

[NumPy](https://numpy.org/doc/stable/reference/generated/numpy.lib.format.html)
defines the NPY array-file format. [Apache
Arrow](https://arrow.apache.org/docs/format/Columnar.html#serialization-and-interprocess-communication-ipc)
defines the columnar interprocess communication format used by Arrow assets.
[Parquet](https://parquet.apache.org/docs/) defines a columnar file format for
table data. [Vega-Lite](https://vega.github.io/vega-lite/) defines a declarative
chart specification. [AnyWidget](https://anywidget.dev/) defines a browser
widget model and view lifecycle.

Install the runtime used by each imported loader:

| Loader                          | Peer dependency                                                                                             | Role                                           |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| JSON, scalar, text, HTML, image | None                                                                                                        | Browser-native values and DOM APIs             |
| marimo output and marimo cell   | None                                                                                                        | Inert replay records                           |
| NumPy                           | None                                                                                                        | Built-in NPY decoder                           |
| Arrow                           | [`@uwdata/flechette`](https://github.com/uwdata/flechette) and [`lz4js`](https://github.com/Benzinga/lz4js) | Arrow table API and LZ4 decompression          |
| Parquet                         | [`hyparquet`](https://github.com/hyparam/hyparquet)                                                         | Parquet row decoding                           |
| Vega-Lite                       | [`vega-embed`](https://github.com/vega/vega-embed)                                                          | Chart rendering and disposal                   |
| AnyWidget                       | [`@anywidget/types`](https://github.com/manzt/anywidget)                                                    | Public widget model, host, and lifecycle types |

```bash
pnpm add @marimo-team/marimo-export hyparquet vega-embed
```

Import each loader from its public subpath. [Output loaders](browser/loaders.md)
defines every result type, option, default, cancellation point, and disposal
contract.

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

## Define a custom representation

A Python exporter converts one notebook result into a `BlobAsset`:

```python
import json

from marimo_export.outputs import BlobAsset


def encode_summary(value: list[object]) -> BlobAsset:
    payload = {
        "schema": "example.summary.v1",
        "rows": len(value),
    }
    return BlobAsset(
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
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

The browser loader validates the same media type and payload:

```ts
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";
import { parsePortableJson, portableJsonObject } from "@marimo-team/portable-json";

interface Summary {
  readonly rows: number;
}

export const summaryLoader = defineBlobAssetLoader<Summary>({
  mediaTypes: "application/vnd.example.summary.v1+json",
  load({ payload, signal }) {
    signal?.throwIfAborted();
    const text = new TextDecoder("utf-8", { fatal: true }).decode(payload.data);
    const value = portableJsonObject(parsePortableJson(text), "summary");
    if (
      value.schema !== "example.summary.v1" ||
      typeof value.rows !== "number" ||
      !Number.isSafeInteger(value.rows) ||
      value.rows < 0
    ) {
      throw new TypeError("Summary payload is invalid.");
    }
    signal?.throwIfAborted();
    return Object.freeze({ rows: value.rows });
  },
});
```

Install the companion parser when the custom loader uses it:

```bash
pnpm add @marimo-team/marimo-export @marimo-team/portable-json
```

Use a versioned media type for a representation shared with agents or another
client. A loader can return data or a value with a browser `mount()` method. A
mount returns an idempotent disposable view and owns every node, listener,
object URL, model, and renderer resource that it creates.

[Portable JSON](portable-json.md) defines the cross-language value contract.
[Errors and limits](browser/errors-and-limits.md) defines the integrity and
execution boundaries for custom loaders.
