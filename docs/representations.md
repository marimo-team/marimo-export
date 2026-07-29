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

| Exporter ID        | Options                              | Result media type                                 |
| ------------------ | ------------------------------------ | ------------------------------------------------- |
| `anywidget.bundle` | none                                 | `application/vnd.marimo-export.anywidget.v1+json` |
| `altair.vegalite`  | none                                 | `application/vnd.vegalite.v<major>+json`          |
| `altair.png`       | `scale`                              | `image/png`                                       |
| `parquet.table`    | `compression`, `filename`            | `application/vnd.apache.parquet`                  |
| `blob.json`        | `media_type`, `filename`, `metadata` | `application/json` by default                     |
| `blob.text`        | `media_type`, `filename`, `metadata` | `text/plain; charset=utf-8` by default            |
| `blob.html`        | `filename`, `metadata`               | `text/html; charset=utf-8`                        |

Use an ID in YAML or a typed factory in Python:

```python
from marimo_export.exporters import altair, anywidget, blob, parquet

chart = altair.vegalite()
snapshot = altair.png(scale=2)
widget = anywidget.bundle()
table = parquet.table(compression="snappy", filename="prices.parquet")
document = blob.json(media_type="application/vnd.example.v1+json")
```

These calls construct immutable descriptors. The selected runtime function
receives the notebook source value later, inside a transient marimo child cell.
Its return enters the normal marimo cache before publication.

## Browser loaders

All browser loaders are entry points of `@marimo-team/marimo-export`.

| Import subpath     | Peer dependencies            | Result                        |
| ------------------ | ---------------------------- | ----------------------------- |
| `loader/numpy`     | none                         | typed array plus NPY metadata |
| `loader/arrow`     | `@uwdata/flechette`, `lz4js` | Flechette table               |
| `loader/parquet`   | `hyparquet`                  | Hyparquet row objects         |
| `loader/anywidget` | `@anywidget/types`           | mountable local model graph   |
| `loader/vegalite`  | `vega-embed`                 | mountable Vega-Lite chart     |

Import each row from
`@marimo-team/marimo-export/<subpath>`. The package root supplies scalar and
image loaders. Loader peers are optional at the root, so an application
installs the runtimes for the subpaths it imports.

## Custom representation

A custom representation needs:

1. A versioned media type.
2. An importable top-level Python function that returns a validated
   `BlobAsset`.
3. Bounded public metadata.
4. An `OutputLoader` that matches the codec and media type.
5. Inner-byte validation and allocation limits.
6. Disposal when browser resources are created.
7. A producer-to-browser test over exact bytes.

Reference the function as `module:function` in the ExportSpec. The module and
its dependencies must be available in the selected kernel. The function
receives one source value plus portable keyword options. No Python source or
serialized closure enters the spec.

Exporter functions are stateless. Immutable constants may be scalars, bytes,
tuples, frozensets, or regular expressions. Pass other configuration through
exporter options.

Preflight fingerprints the resolved module, function code, statically reachable
Python modules, available owning package version, and declared built-in runtime
dependencies. Changing one of those inputs invalidates the projection cache.
