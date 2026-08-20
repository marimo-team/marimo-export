---
title: Export format reference
description: Durable directory, index, state, representation, asset, integrity, and version contracts for notebook exports.
---

# Export format reference

A notebook export is one canonical `index.json` and the content-addressed assets
declared by that index. Python, browser, agent, and custom clients consume the
same durable relation between prepared states and named outputs.

## Directory layout

```text
report/
  index.json
  assets/
    <sha256>.npy
    <sha256>.arrow
    <sha256>.output.json
    <sha256>.cell.json
    <sha256>.bin
```

`index.json` is the single entry point. Asset paths are derived from codec and
SHA-256 rather than supplied as arbitrary paths.

## Index schema

The root object contains exactly:

| Field              | Contract                                                        |
| ------------------ | --------------------------------------------------------------- |
| `schema`           | Exact value `marimo-export.export.v1`                           |
| `spec_sha256`      | SHA-256 of canonical ExportSpec bytes                           |
| `default_state`    | Fingerprint of the default normalized state                     |
| `notebook`         | Notebook filename and document SHA-256                          |
| `producer`         | marimo version, marimo-export version, implementation SHA-256   |
| `inputs`           | Ordered input definition names                                  |
| `control_bindings` | Scoped UI object IDs mapped to inputs and semantic paths        |
| `outputs`          | Ordered published output names                                  |
| `aliases`          | Authored state names mapped to state fingerprints               |
| `states`           | Nonempty mapping from state fingerprints to complete state data |

The index is canonical UTF-8 JSON. Object keys, number spelling, string
encoding, and scalar tags must match the canonical producer representation
byte for byte.

Each root input name is an opaque, nonempty string of at most 255 UTF-8 bytes.
ExportSpec and notebook inspection validate Python identifiers before export
production. Durable readers preserve the written names across Unicode database
versions.

`control_bindings` is the browser event-routing authority for prepared UI
controls. Each binding contains a root `input` name and a typed `path` through
that input's control tree. The `input` value must exactly match one root input
name. Root controls use an empty path. Sequence children append an `index`
step, mapping children append a `key` step, and wrapper or form children append
an `element` step. Multiple scoped IDs may carry the same binding when one
source control appears in several projections. Consumers use these records
directly and do not parse projection-scoped object IDs.

## State entries

Each state key is the lowercase SHA-256 over canonical JSON for its `inputs`.
Each state entry contains exactly:

| Field     | Contract                                           |
| --------- | -------------------------------------------------- |
| `inputs`  | Complete object with the exact root input-name set |
| `outputs` | Object with the exact root output-name set         |

Every alias targets a declared state. Several aliases may target the same
fingerprint when authored rows normalize to the same complete vector. One
output name keeps one codec and media type across every state. `default_state`
references one declared fingerprint.

## Output descriptors

Every descriptor contains:

- codec
- media type
- provenance
- inline scalar or JSON value, or an asset reference

The producer implementation SHA-256 identifies the exact installed
marimo-export Python source set that created the index. Capture freezes this
identity before execution and commits it after the end-of-operation identity
check succeeds.

Descriptor provenance contains the originating `python_type`. Native Marimo
cache keys and return references remain inside the producer process.

BlobAsset descriptors also record filename and portable metadata.

## Native codecs

Export format version 1 accepts seven codecs:

| Codec                          | Stored form                        | Asset suffix   |
| ------------------------------ | ---------------------------------- | -------------- |
| `marimo.scalar.v1`             | Inline scalar                      | None           |
| `marimo.json.v1`               | Inline portable JSON               | None           |
| `marimo.output.v1`             | Canonical rendered-output snapshot | `.output.json` |
| `marimo.cell.v1`               | Canonical complete-cell snapshot   | `.cell.json`   |
| `numpy.npy.v1`                 | NumPy NPY file                     | `.npy`         |
| `apache.arrow.file.v1`         | Arrow IPC file                     | `.arrow`       |
| `marimo.blob-asset.msgpack.v1` | Canonical MessagePack `BlobAsset`  | `.bin`         |

The codec identifies the native envelope. A BlobAsset media type identifies
the representation within that envelope. Custom media types extend the output
space while the codec set remains closed for version 1.

## Scalar wire values

JSON-compatible scalars remain inline. Values that JSON cannot preserve use
closed tagged forms:

- integers outside the JavaScript safe range become tagged big integers
- NaN becomes a tagged special float
- positive and negative infinity become tagged special floats
- negative zero becomes a tagged special float

Readers reject unknown or noncanonical scalar tags.

## JSON values

`marimo.json.v1` stores any portable JSON value inline in the descriptor.
Objects, arrays, strings, booleans, finite numbers in the JavaScript-safe
range, and null retain their JSON shape. Python returns a frozen value through
`ExportOutput.json()`. Browser clients load it through `jsonLoader()`.

## Rendered-output snapshots

`marimo.output.v1` stores this exact canonical JSON record:

```json
{
  "schema": "marimo.output.v1",
  "projectionSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "ownerCellId": "cell-id",
  "output": {
    "channel": "output",
    "mimetype": "text/markdown",
    "data": "<span>Ready</span>"
  },
  "resources": {
    "files": {},
    "modelNotifications": [],
    "functions": {
      "cell-id-projection-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-ui-cell-id-ui": []
    },
    "uiValues": {
      "cell-id-projection-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-ui-cell-id-ui": 3
    }
  }
}
```

`output` is null when the formatted value has no terminal output. The record
is inert. `ownerCellId` identifies the authored source cell whose UI object
graph produced the output.

## Complete-cell snapshots

`marimo.cell.v1` stores this exact canonical JSON record:

```json
{
  "schema": "marimo.cell.v1",
  "projectionSha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "cell": {
    "id": "cell-id",
    "name": "summary",
    "codeSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "config": { "disabled": false }
  },
  "outcome": "completed",
  "output": {
    "channel": "output",
    "mimetype": "text/plain",
    "data": "42"
  },
  "console": [{ "channel": "stdout", "mimetype": "text/plain", "data": "ready\n" }],
  "resources": {
    "files": {},
    "modelNotifications": [],
    "functions": {},
    "uiValues": {}
  }
}
```

`cell.name` is null for an unnamed cell. `output` is null when the completed
cell has no terminal output. `console` preserves the ordered Marimo console
records captured during the selected cell's fresh execution.

Both snapshot records use the same replay resources:

- `files` maps each closed virtual resource to a slash-prefixed `/@file/`
  path and data URL. Model notifications retain Marimo's trusted relative
  `./@file/` URL, which resolves through that normalized key.
- `modelNotifications` contains the reachable AnyWidget model lifecycle
  closure in replay order. Model IDs contain the record's
  `projectionSha256` and are scoped by planned output.
- `functions` maps every projection-scoped UI object ID to an empty array. A
  live Python function makes the snapshot nonportable. The producer removes a
  form's inert `validate` registration when `should-validate` is false.
- `uiValues` maps every projection-scoped registry-owned UI object ID to its
  accepted frontend value after state updates.

The producer applies the same scope to UI object IDs, random IDs, HTML
attributes, and structured UI references within one snapshot. Each UI ID begins
with the snapshot's `ownerCellId` or `cell.id`, preserving Marimo's ownership
authorization, and ends in a projection-root structural path. Common controls
keep the same ID when a conditional tree adds or removes siblings. Model IDs
use their projection-scoped model namespace. This lets a consumer merge
rendered-output and complete-cell resources from the same live UI element.

Browser loaders validate and freeze these records. Rendering and model replay
belong to the consuming application.

## Asset identity

An asset reference contains its lowercase SHA-256 and byte size. Equal
`(codec, SHA-256)` identities share one asset across states. Reused identities
must agree on every descriptor fact.

Producer and local-reader bounds are:

- 64 MiB per asset
- 512 MiB across unique assets in one export

Browser callers can apply their own per-output and aggregate limits through the
browser API.

## BlobAsset envelope

A native BlobAsset MessagePack envelope contains exactly:

- `data`: representation bytes
- `media_type`: validated media type
- `filename`: optional portable basename
- `metadata`: portable JSON object

The envelope's media type, filename, and metadata must agree with the index
descriptor before a reader returns the representation.

## Verification

Readers verify:

- canonical index bytes
- exact input and output key sets
- state fingerprints
- representation consistency across states
- asset path identity
- declared and observed byte length
- SHA-256
- NPY or Arrow framing
- canonical BlobAsset envelope
- descriptor and envelope agreement
- complete declared asset closure

The loaded `index.json` is the integrity root. Verification establishes
consistency with that index. It does not authenticate who produced the index.
Python `NotebookExport.identity` and browser `NotebookExport.identity` expose
the lowercase SHA-256 of the exact canonical `index.json` bytes.

## Consumer behavior

Opening an export reads and validates `index.json`. Assets remain lazy until a
consumer reads one output or verifies the complete export.

State selection supports:

- the declared default state
- authored state alias
- exact complete input vector
- sparse patch from an existing state

Resolution returns a prepared state already present in the export.

## Versioning

The schema identifier and codec identifiers version durable behavior. A reader
rejects an unknown export schema or native codec. Custom representations should
use versioned media types so producer and consumer changes remain explicit.

[Consume an export](../guide/consume-an-export.md) provides Python, browser, and
agent workflows. [Output representations](representations.md) maps built-in and
custom representations to their consumers.
