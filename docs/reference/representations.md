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
| JSON BlobAsset            | `blob.json`                        | `blob_asset()`  | Matching blob loader   | Versioned JSON in a media-typed envelope   |
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

Every descriptor records the stored value's `python_type`. Exporter-backed
outputs record `marimo_export.outputs.BlobAsset`. Producer-local marimo cache
paths are outside the portable representation contract.

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
agents](../guide/agents-and-automation) defines the grounding workflow and
evidence identity.

## Browser loader dependencies

[NumPy](https://numpy.org/doc/stable/reference/generated/numpy.lib.format.html)
defines the NPY array-file format. [Apache
Arrow](https://arrow.apache.org/docs/format/Columnar.html#serialization-and-interprocess-communication-ipc)
defines the columnar interprocess communication format used by Arrow assets.
[Parquet](https://parquet.apache.org/docs/) defines a columnar file format for
table data. [Vega-Lite](https://vega.github.io/vega-lite/) defines a declarative
chart specification. [AnyWidget](https://anywidget.dev/) defines a browser
widget model and view lifecycle.

Install the dependency used by each imported loader:

| Loader                          | Dependency                                                                                                               | Role                                        |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------- |
| JSON, scalar, text, HTML, image | None                                                                                                                     | Browser-native values and DOM APIs          |
| marimo output and marimo cell   | None                                                                                                                     | Inert replay records                        |
| NumPy                           | None                                                                                                                     | Built-in NPY decoder                        |
| Arrow                           | [`@uwdata/flechette ^2.5.0`](https://github.com/uwdata/flechette) and [`lz4js 0.2.0`](https://github.com/Benzinga/lz4js) | Arrow table API and LZ4 decompression       |
| Parquet                         | [`hyparquet ^1.26.2`](https://github.com/hyparam/hyparquet)                                                              | Parquet row decoding                        |
| Vega-Lite                       | [`vega-embed ^7.1.0`](https://github.com/vega/vega-embed)                                                                | Chart rendering and disposal                |
| AnyWidget                       | [`@anywidget/types ^0.4.0`](https://github.com/manzt/anywidget)                                                          | TypeScript model, host, and lifecycle types |

```bash
pnpm add @marimo-team/marimo-export hyparquet vega-embed
```

Import each loader from its public subpath. [Output loaders](browser/loaders)
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

The browser loader validates the same media type and payload. It uses the
published browser package and owns the representation-specific JSON checks:

```ts
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";

interface Summary {
  readonly rows: number;
}

export const summaryLoader = defineBlobAssetLoader<Summary>({
  mediaTypes: "application/vnd.example.summary.v1+json",
  load({ payload, signal }) {
    signal?.throwIfAborted();
    const value: unknown = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(payload.data),
    );
    if (
      value === null ||
      Array.isArray(value) ||
      typeof value !== "object" ||
      (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null)
    ) {
      throw new TypeError("Summary payload is invalid.");
    }
    const record = value as Record<string, unknown>;
    if (
      Reflect.ownKeys(record).some((key) => typeof key !== "string") ||
      Object.keys(record).length !== 2 ||
      !Object.hasOwn(record, "schema") ||
      !Object.hasOwn(record, "rows") ||
      record.schema !== "example.summary.v1" ||
      typeof record.rows !== "number" ||
      !Number.isSafeInteger(record.rows) ||
      record.rows < 0
    ) {
      throw new TypeError("Summary payload is invalid.");
    }
    signal?.throwIfAborted();
    return Object.freeze({ rows: record.rows });
  },
});
```

Install the browser package before importing the loader definition:

```bash
pnpm add @marimo-team/marimo-export
```

Use a versioned media type for a representation shared with agents or another
client. A loader can return data or a value with a browser `mount()` method. A
mount returns an idempotent disposable view and owns every node, listener,
object URL, model, and renderer resource that it creates.

[Portable JSON](portable-json) defines the cross-language value contract that a
custom JSON representation should preserve.
[Errors and limits](browser/errors-and-limits) defines the integrity and
execution boundaries for custom loaders.
