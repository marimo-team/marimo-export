---
title: Outputs and representations
description: Follow a notebook result through selection, export, storage, loading, and mounting.
---

# Outputs and representations

A notebook computes results as Python values or rendered cell output. An
`ExportSpec` publishes selected results under output names. Each output keeps
one representation across every exported state.

Consider three notebook results. The `outputs` fragment of their `ExportSpec`
publishes each result under one name:

```yaml
outputs:
  summary:
    source: { kind: json, selector: report.summary }
  prices:
    source: { kind: export, selector: selected_prices }
    exporter: parquet.table
  chart:
    source: { kind: export, selector: performance }
    exporter: altair.vegalite
```

`summary`, `prices`, and `chart` are output names. `report.summary`,
`selected_prices`, and `performance` are selectors for notebook results. The
source kind and optional exporter choose the stored representation.

```text
notebook result
      |
      v
 output source     select a value, rendered output, or complete cell
      |
      v
   exporter        convert a selected value when kind is export
      |
      v
  descriptor       declare codec, media type, provenance, and data location
      |
      v
 inline value or content-addressed asset
      |
      v
 consumer loader   validate and decode the representation
```

## An output is a published name

Every normalized state exposes the exact same output-name set. A consumer asks
for `summary` or `chart` without knowing which notebook cell produced it.

An output name is part of the application contract. The selected notebook value
can change from state to state, while the output's codec and media type remain
stable.

Use “notebook result” for the Python value or rendered result before export. Use
“output” for the published name and descriptor available to consumers.

## A source selects the notebook result

Each output has one source kind:

| Source kind | Selected result                              | Stored form                                                           |
| ----------- | -------------------------------------------- | --------------------------------------------------------------------- |
| `json`      | Portable Python value                        | Canonical portable JSON inline in `index.json`                        |
| `native`    | Value supported by marimo's cache serializer | Scalar, portable JSON, NumPy, Arrow, or `BlobAsset` form              |
| `export`    | Python value accepted by an exporter         | `BlobAsset` returned by that exporter                                 |
| `output`    | Value formatted by marimo                    | Rendered-output snapshot and replay resources                         |
| `cell`      | Named cell or inspected runtime cell ID      | Cell identity, output, console records, outcome, and replay resources |

JSON, native, export, and rendered-output sources use a selector. A selector
starts from one Python definition and can follow attributes, nonnegative integer
items, or JSON-string items. Mapping keys take precedence over attributes.

A cell source uses an authored cell name or an inspected runtime cell ID because it
targets the complete cell record.

## An exporter creates a BlobAsset

An exporter converts one selected Python value into a `BlobAsset`. A
`BlobAsset` contains representation bytes, a media type, an optional portable
filename, and portable JSON metadata.

Built-in exporters cover JSON, text, HTML, Parquet tables, Altair charts, PNG
images, and AnyWidget state. A custom exporter is an importable `module:symbol`
callable that returns a `BlobAsset`.

Declare every helper module whose source affects the returned bytes, including
ordinary imported helpers. The exporter module and declared dependencies then
participate in source identity and drift detection.

## A representation joins producer and consumer

An output representation consists of a codec and media type:

- The codec identifies the stable marimo-export storage envelope.
- The media type identifies the stored data. For a `BlobAsset`, it identifies
  the data carried inside the envelope.

The closed codec set covers inline scalar and JSON values, rendered marimo
output, complete marimo cells, [NumPy](https://numpy.org/) arrays,
[Apache Arrow](https://arrow.apache.org/) tables, and `BlobAsset` envelopes.
Versioned media types let custom producer and consumer code evolve together.

An asset is a content-addressed file referenced by an output descriptor. Its
path follows from the codec and SHA-256 digest. Equal codec and digest pairs
share one asset across states.

## A loader decodes one representation

Python readers expose representation-specific methods such as `scalar()`,
`json()`, `asset_bytes()`, and `blob_asset()`.

Browser readers require an explicit loader:

| Representation                                 | Browser loader        | Decoded result                           |
| ---------------------------------------------- | --------------------- | ---------------------------------------- |
| Portable JSON                                  | `jsonLoader()`        | Frozen JSON value                        |
| NumPy NPY                                      | `numpyLoader()`       | Typed multidimensional array record      |
| Arrow IPC                                      | `arrowTableLoader()`  | Arrow table                              |
| [Parquet](https://parquet.apache.org/)         | `parquetRowsLoader()` | Array of row objects                     |
| [Vega-Lite](https://vega.github.io/vega-lite/) | `vegaLiteLoader()`    | Mountable chart                          |
| [AnyWidget](https://anywidget.dev/)            | `anyWidgetLoader()`   | Saved model state with a mount lifecycle |

Loading verifies the selected asset before decoding it. A loader can return inert
data or a mountable value.

## Mounting starts an executable lifecycle

`mount(element)` attaches a chart, widget, image, or custom interactive value to
the document. It returns a disposable view. Dispose that view before replacing
it or tearing down the page.

Opening, resolving, and verifying parse inert records. Mounting AnyWidget,
Vega-Lite, or custom interactive code grants that code the page's authority.
Review executable modules, allowed origins, and Content Security Policy before
mounting them.

Use [Output representations](../reference/representations) for the complete
exporter, loader, and peer-dependency matrix. Use [Integrity and
trust](integrity-and-trust) for the executable-code boundary.
