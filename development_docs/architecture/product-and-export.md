# Product model and export format

The product model connects one saved or running marimo notebook to a finite
set of complete input states and named output representations. The export
format stores that relation as canonical JSON and content-addressed assets.

## Product nouns

| Noun            | Contract                                                         |
| --------------- | ---------------------------------------------------------------- |
| Notebook        | Saved marimo source and its reactive dependency graph            |
| Baseline        | Definitions, values, UI state, cell ownership, document identity |
| ExportSpec      | Input names, sparse named states, and output specifications      |
| State           | One complete input assignment and canonical fingerprint          |
| Output          | Published name, source definition, and optional exporter         |
| Representation  | Native cache codec plus media type and metadata                  |
| Asset           | Content-addressed native return bytes                            |
| Notebook export | One canonical `index.json` and its declared assets               |
| ExportResult    | Completed producer facts, cache activity, timings, warnings      |
| Consumer        | Human-facing application, agent, Python reader, or custom client |

## ExportSpec becomes an export plan

`create_export_plan()` validates referenced definitions, fills omitted input
names from the baseline, rejects duplicate complete vectors, and separates
ordinary assignments from UI updates.

Ordinary values are appended to transient copies of their authored cells. This
preserves sibling functions, classes, and UI elements returned by the same
cell. An AnyWidget row merges a sparse trait patch over the complete baseline
model. The state runner rejects validation or coercion that changes the
requested vector.

The authored notebook source remains unchanged. The plan holds the transient
state and output cells used by child execution.

## Every state has every output

The durable relation is:

```text
states × outputs -> descriptor
```

The state fingerprint is SHA-256 over canonical JSON for the complete input
object. One output name keeps one codec and media type across every state.

Export format version 1 accepts four native codecs:

```text
marimo.scalar.v1
numpy.npy.v1
apache.arrow.file.v1
marimo.blob-asset.msgpack.v1
```

The BlobAsset codec carries extensible media types. A custom representation
adds a media type, Python exporter, and consumer decoder while the codec set
remains closed for version 1.

## `ExportIndex` owns durable data

`ExportIndex` records:

- schema identifier
- notebook filename and document SHA-256
- marimo and marimo-export producer versions
- ordered input and output names
- complete state vectors and fingerprints
- one descriptor for every state and output

`ExportIndex.to_bytes()` emits canonical UTF-8 JSON. Python and TypeScript use
the same number spelling, key order, scalar tags, fingerprints, codec names,
and media types. Exact cross-language fixtures protect this boundary.

Producer timings, cache activity, and warnings live in `result.py` outside the
durable index. Equal notebook, state, exporter, and return bytes can therefore
produce the same export despite different run-local diagnostics.

## Assets and commit form one transaction

Asset paths derive from codec and SHA-256:

```text
assets/<sha256>.npy
assets/<sha256>.arrow
assets/<sha256>.bin
```

The writer validates the exact asset closure, length, digest, native framing,
and descriptor agreement. It writes a staging directory, opens and verifies
that directory through the local reader, then commits the complete directory.
Replacement uses an atomic directory exchange where the platform provides one.

`open_export()` validates `index.json` through secure filesystem handles and
returns immutable `NotebookExport`, `ExportState`, and `ExportOutput` values.
Assets remain lazy until one output is read or the export is verified.

## Coherence rules

1. One baseline supplies every omitted state value in a producer run.
2. Every state vector is complete before execution.
3. An input name has one defining notebook cell.
4. An AnyWidget patch records complete serializer-owned state.
5. Every state records the configured output set.
6. One output name keeps one representation across states.
7. `index.json` references every asset and no undeclared asset.
8. A committed directory passes the same reader used by consumers.

Read [marimo integration](marimo-integration.md) for the execution and cache
path that produces native receipts.
