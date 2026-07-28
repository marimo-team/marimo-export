# Representations

Publication v1 has four stable native codecs:

| Codec                          | Producer value        | Browser payload      |
| ------------------------------ | --------------------- | -------------------- |
| `marimo.scalar.v1`             | Portable scalar       | scalar or BigInt     |
| `numpy.npy.v1`                 | Numeric NumPy array   | verified NPY bytes   |
| `apache.arrow.file.v1`         | Supported table value | verified Arrow bytes |
| `marimo.blob-asset.msgpack.v1` | marimo `BlobAsset`    | decoded `BlobAsset`  |

Codec selects the stable byte envelope. Media type selects the semantic
representation inside a `BlobAsset`.

## Built-in Exporters

| Function                     | Result media type                                 |
| ---------------------------- | ------------------------------------------------- |
| `exporters.anywidget.bundle` | `application/vnd.marimo-export.anywidget.v1+json` |
| `exporters.altair.vegalite`  | `application/vnd.vegalite.v<major>+json`          |
| `exporters.altair.png`       | `image/png`                                       |
| `exporters.parquet.table`    | `application/vnd.apache.parquet`                  |
| `exporters.blob.json`        | `application/json` by default                     |
| `exporters.blob.text`        | `text/plain; charset=utf-8` by default            |
| `exporters.blob.html`        | `text/html; charset=utf-8`                        |

Each function accepts a Python object and explicit options, returns the exact
native `BlobAsset`, emits deterministic bytes for equal semantic inputs, and
validates its result.

## Browser loaders

| Package                                       | Result                        |
| --------------------------------------------- | ----------------------------- |
| `@marimo-team/marimo-export-loader-numpy`     | typed array plus NPY metadata |
| `@marimo-team/marimo-export-loader-arrow`     | Flechette table               |
| `@marimo-team/marimo-export-loader-parquet`   | Hyparquet row objects         |
| `@marimo-team/marimo-export-loader-anywidget` | mountable local model graph   |
| `@marimo-team/marimo-export-loader-vegalite`  | mountable Vega-Lite chart     |

Core supplies scalar and image loaders.

## Custom representation

A custom representation needs:

1. A versioned media type.
2. An authored Python function that returns a validated `BlobAsset`.
3. Bounded public metadata.
4. An `OutputLoader` that matches the codec and media type.
5. Inner-byte validation and allocation limits.
6. Disposal when browser resources are created.
7. A producer-to-browser test over exact bytes.
