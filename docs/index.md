# Publish results from a running marimo notebook

marimo-export captures selected results from an active marimo kernel and writes a verified static publication for Python agents and browsers.

```bash
export MARIMO_EXPORT_TOKEN="<token>"
uv run marimo-export capture \
  http://localhost:3456/ \
  --spec finance.export.yaml \
  --output dist/finance
```

The notebook supplies its live values, rendered outputs, UI controls, installed packages, files, credentials, and marimo cache. An external `ExportSpec` supplies three publication decisions:

- Which live result becomes each output.
- Which portable formats represent that result.
- Which finite UI control vectors become variants.

The capture runs exporters in the notebook environment. It stores each projection as a marimo `BlobAsset` and writes an index over the selected cache assets.

```text
publication/
  index.json
  cache/<opaque marimo cache keys>
```

Python and browser readers verify each cache object before decoding its `BlobAsset` envelope. Reader methods and optional loaders then expose the inner JSON, text, binary, Arrow, Parquet, chart, HTML, or widget representation.

The Python package owns live attachment, capture, local publication reads, and the agent-facing CLI. The TypeScript packages own HTTP publication reads and browser format loaders.

## One value, several formats

```yaml
outputs:
  chart:
    source: price_chart
    formats:
      vegalite: {}
      png:
        options:
          scale: 2
```

The Vega-Lite projection supports browser rendering and inspection. The PNG projection supports slides, reports, and image pipelines. Both projections resolve from the same live `price_chart` value.

## Finite browser interaction

```yaml
variants:
  current: {}
  aapl:
    symbol_picker: [AAPL]
  nvda:
    symbol_picker: [NVDA]
```

A variant supplies frontend values to existing marimo UI controls. Capturing the variants lets a browser switch between precomputed states while the Python kernel is offline.

Each variant starts from the UI vector present when capture begins. marimo runs reactive dependents after the update, and marimo-export restores the starting vector after projection. Restoring controls cannot roll back notebook-authored writes to files, databases, random generators, or background tasks.

## Cache reuse

Authored notebook cells retain their normal marimo cache behavior as UI variants trigger reactive execution. marimo-export adds persistent cached projectors for the selected representations through the notebook's configured marimo cache.

marimo owns source hashing, projector identity, lookup, restoration, and persisted `BlobAsset` bytes. A cacheable projection can return from marimo's persistent cache on a later capture.

Cacheability is an optimization. If marimo cannot hash a selected Python object, marimo-export runs the exporter live and persists the resulting portable bytes through a primitive cache call. The capture reports that projection as `skipped` for reuse.

## Continue

- [Getting started](./getting-started.md)
- [Export specifications](./export-specification.md)
- [Live capture](./live-capture.md)
- [Read publications](./read-publications.md)
- [Publish AnyWidget outputs](./anywidget.md)
- [CLI](./cli.md)
- [Trust and integrity](./trust.md)
