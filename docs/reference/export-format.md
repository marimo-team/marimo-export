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
    <sha256>.bin
```

`index.json` is the single entry point. Asset paths are derived from codec and
SHA-256 rather than supplied as arbitrary paths.

## Index schema

The root object contains exactly:

| Field      | Contract                                                 |
| ---------- | -------------------------------------------------------- |
| `schema`   | Exact value `marimo-export.export.v1`                    |
| `notebook` | Notebook filename and document SHA-256                   |
| `producer` | marimo and marimo-export versions                        |
| `inputs`   | Ordered input definition names                           |
| `outputs`  | Ordered published output names                           |
| `states`   | Nonempty mapping from state names to complete state data |

The index is canonical UTF-8 JSON. Object keys, number spelling, string
encoding, and scalar tags must match the canonical producer representation
byte for byte.

## State entries

Each state contains exactly:

| Field         | Contract                                           |
| ------------- | -------------------------------------------------- |
| `inputs`      | Complete object with the exact root input-name set |
| `fingerprint` | SHA-256 over canonical JSON for `inputs`           |
| `outputs`     | Object with the exact root output-name set         |

Complete input vectors are unique across states. One output name keeps one
codec and media type across every state.

## Output descriptors

Every descriptor contains:

- codec
- media type
- provenance
- inline scalar value or asset reference

Provenance records:

- marimo cache key
- native return reference when the output has an asset
- Python type

BlobAsset descriptors also record filename and portable metadata.

## Native codecs

Export format version 1 accepts four codecs:

| Codec                          | Stored form                       | Asset suffix |
| ------------------------------ | --------------------------------- | ------------ |
| `marimo.scalar.v1`             | Inline scalar                     | None         |
| `numpy.npy.v1`                 | NumPy NPY file                    | `.npy`       |
| `apache.arrow.file.v1`         | Arrow IPC file                    | `.arrow`     |
| `marimo.blob-asset.msgpack.v1` | Canonical MessagePack `BlobAsset` | `.bin`       |

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

## Consumer behavior

Opening an export reads and validates `index.json`. Assets remain lazy until a
consumer reads one output or verifies the complete export.

State selection supports:

- authored state name
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
