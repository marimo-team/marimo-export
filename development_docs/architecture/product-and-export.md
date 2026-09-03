# Product model and export format

The product model connects one saved or running marimo notebook to a finite
set of complete input vectors and named output representations. The export
format stores that relation as canonical JSON and content-addressed assets.

## Product nouns

| Noun              | Contract                                                                                        |
| ----------------- | ----------------------------------------------------------------------------------------------- |
| Notebook          | Saved marimo source and its reactive dependency graph                                           |
| Baseline          | Private kernel record with definitions, values, UI state, cell ownership, and document identity |
| StateSpace        | Reusable named state rows, matrix expansion, and default state                                  |
| ExportSpec        | Default state, sparse named states, and output specifications                                   |
| ExportPlan        | Inferred inputs, complete states, identities, and reusable work                                 |
| Exported state    | One complete input assignment, canonical fingerprint, aliases, and named outputs                |
| Control binding   | Scoped UI object ID mapped to an input and semantic tree path                                   |
| Output            | Published name, typed source, and optional value exporter                                       |
| Representation    | Stable codec and media type for one output name                                                 |
| Output descriptor | Representation, provenance, and inline data or an asset reference                               |
| Asset             | Content-addressed export payload bytes                                                          |
| Notebook export   | One canonical `index.json` and its declared assets                                              |
| ExportResult      | Plan, preparation reuse, verification, bytes, warnings, elapsed time                            |
| Consumer          | Human-facing application, agent, Python reader, or custom client                                |

## StateSpace separates states from output discovery

`StateSpace` owns reusable authored state rows. It validates explicit rows,
expands a Cartesian input matrix, resolves the default name, and returns
validated matrix-expanded rows. Planning completes and normalizes those rows
against the captured input baseline. An application that discovers its outputs
composes the rows with `OutputSpec` values through
`ExportSpec.from_state_space()`.

`ExportSpec` remains the complete planning input. Its states and outputs affect
the spec and plan identities. Producer identity is independent of the
`ExportSpec`.

## ExportSpec resolves to execution and preparation plans

Inside the kernel, `create_execution_plan()` validates referenced definitions,
infers input names, fills omitted state values from the baseline, groups aliases
for equal complete vectors, resolves the explicit default state, and separates
ordinary assignments from UI updates. Its private `ExecutionPlan` holds the
normalized states and transient cells needed by child execution.

Ordinary values are written into transient copies of their authored cells. An
assignment is inserted immediately before one final expression or appended when
the cell has no final expression. This preserves sibling functions, classes,
and UI elements returned by the same cell. An AnyWidget row merges a sparse
trait patch over the complete baseline model. The state runner rejects
validation or coercion that changes the requested vector.

The public `ExportPlan` returned by `plan()` or `Session.plan()` combines that
kernel result with producer identities, repository observations, prepared-state
reuse, and exact prepared-export reuse. The authored notebook source remains
unchanged throughout planning and execution.

An output source is one of five records:

- a structurally parsed JSON selector
- a structurally parsed native selector
- a structurally parsed exporter selector
- a structurally parsed rendered-output selector
- a complete cell selected by native name or runtime ID

A JSON projection captures canonical portable JSON. A native projection keeps
scalar, NumPy, Arrow, and BlobAsset cache representations while encoding
composite portable values as canonical JSON. An export projection converts the
selected value to a BlobAsset. Rendered-output and complete-cell projections
capture Marimo-owned output records through the child recording stream and
`SessionView`.

## Selectors and exporters

`ValueSelector` owns JSON, native, exporter, and rendered-output selection. A
selector contains at most 2,048 UTF-8 bytes. It starts with an ASCII
identifier-shaped root, then traverses ASCII dot names, nonnegative integer
indexes, or JSON-string mapping keys. Mapping keys win over attributes when a
runtime value supports both.

`OutputSpec` binds one source to an optional `ExporterSpec`. Only an export
source accepts an exporter. `ExporterSpec` resolves a built-in name or an
importable `module:symbol`, portable option values, and up to 256 dependency
modules whose source contributes to exporter execution identity and output-cell
cache keys.

Built-in exporters own BlobAsset creation for JSON, text, HTML, Altair
Vega-Lite, PNG, Parquet, and AnyWidget. Exporter execution loads optional
distributions when their implementation needs them. The capture-scoped registry
freezes the resolved callables and modules, invokes each callable as
`exporter(value, **options)`, and verifies source stability before commit.

## Every state has every output

The durable relation is:

```text
states × outputs -> descriptor
```

The state fingerprint is SHA-256 over canonical JSON for the complete input
object. One output name keeps one codec and media type across every state.

Export format version 1 accepts seven export codecs:

```text
marimo.scalar.v1
marimo.json.v1
marimo.output.v1
marimo.cell.v1
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
- canonical ExportSpec SHA-256
- explicit default state fingerprint
- notebook filename and document SHA-256
- marimo and marimo-export producer versions
- exact marimo-export implementation SHA-256
- ordered input and output names
- projection-scoped UI object IDs bound to input names and semantic paths
- authored aliases mapped to state fingerprints
- fingerprint-keyed complete state vectors
- one descriptor for every state and output

`ExportIndex.to_bytes()` emits canonical UTF-8 JSON. Python and TypeScript use
the same number spelling, key order, scalar tags, fingerprints, codec names,
and media types. Exact cross-language fixtures protect this boundary.

Preparation reuse, cache activity, warnings, elapsed time, and verification
counts live in `result.py` outside the durable index. Equal notebook, state,
exporter, and return bytes can therefore produce the same export despite
different run-local diagnostics.

`ExportResult.plan` carries the complete states and aliases.
`prepared_states` and `reused_states` form an exact partition of the plan's
fingerprints. `cache_activity` reports Marimo work that ran while preparing
missing states.

Session inspection and the durable producer record expose the kernel's
`implementation_sha256`. Capture freezes the value before state execution and
verifies it again before committing the index. Publication coordinators bind
reuse keys and capture receipts to that same digest.

## Assets and commit form one transaction

Asset paths derive from codec and SHA-256:

```text
assets/<sha256>.npy
assets/<sha256>.arrow
assets/<sha256>.output.json
assets/<sha256>.cell.json
assets/<sha256>.bin
```

The writer validates the exact asset closure, length, digest, native framing,
and descriptor agreement. It writes a staging directory, opens and verifies
that directory through the local reader, then commits the complete directory.
Replacement uses an atomic directory exchange where the host filesystem
provides one and guarded rollback elsewhere. A pre-commit failure leaves the
destination unchanged. A rollback failure or post-commit verification failure
can leave a replacement visible while the operation raises.

File preparation runs its producer source guard before the export generation
commit. That guard binds the reusable prepared export to the source identity
used during execution.

`open_export()` validates `index.json` through secure filesystem handles and
returns immutable `NotebookExport`, `ExportState`, and `ExportOutput` values.
Assets remain lazy until one output is read or the export is verified.
`NotebookExport.identity` is the SHA-256 of the exact canonical index bytes.

The Python reader resolves aliases, fingerprints, complete input vectors, and
shallow root-input patches. Scalar and portable JSON accessors decode semantic
values. NumPy, Arrow, rendered-output, and complete-cell accessors return
verified raw bytes after framing checks. `verify()` reads each unique asset once
and reports exported states, state-output pairs, unique assets, and asset bytes.
Secure reads reject symbolic links, reparse-point escapes, nonregular files,
file replacement races, and size or digest changes.

## Coherence rules

1. One baseline supplies every omitted state value in a producer run.
2. Every state vector is complete before execution.
3. An input name has one defining notebook cell.
4. An AnyWidget patch records complete serializer-owned state.
5. Every state records the configured output set.
6. One output name keeps one representation across states.
7. `index.json` references every asset and no undeclared asset.
8. A committed directory passes the same reader used by consumers.
9. Snapshot resources contain the reachable AnyWidget model closure and every
   projection-scoped UI object namespace.

Read [Planning and preparation](preparation.md) for prepared-state and exact
generation reuse. Read [Execution and caching](execution-and-caching.md) for the
native receipt path.
