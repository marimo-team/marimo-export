---
title: Format records and errors
description: Canonical JSON, export indexes, output descriptors, diagnostic records, typed failures, and narrow public compatibility types.
---

# Format records and errors

The high-level reader validates these records for you. Use the APIs on this page
to implement a Python format tool, inspect descriptor provenance, exchange
canonical state values, or handle stable error codes.

Start with `open_export()` when the task is to consume output data. Construct
`ExportIndex` and descriptor records directly when the task needs the exact
wire model.

## Portable and canonical JSON

Import portable JSON types and functions from `marimo_export.wire`:

```python
from marimo_export.wire import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonValue,
    canonical_json_bytes,
    canonical_json_sha256,
    parse_canonical_json,
    portable_json,
    state_fingerprint,
)
```

### `portable_json()`

```python
portable_json(value: object, path: str = "value") -> JsonValue
```

Returns a detached mutable value containing JSON null, booleans, strings,
numbers, arrays, and string-keyed objects. Integers and integer-valued floats
must fit the JavaScript safe-integer range. Floats must be finite. Negative zero
normalizes to integer zero.

Mappings become dictionaries. Non-string, non-byte sequences become lists.
The conversion rejects cycles through its bounded depth or value-count checks,
Unicode surrogate code points, incompatible object types, nesting deeper than
256 containers, and more than 100,000 JSON values.

`path` labels validation errors. It does not change the encoded value.

### Canonical encoding and identity

```python
canonical_json_bytes(value: object, path: str = "value") -> bytes
canonical_json_sha256(value: object, path: str = "value") -> str
state_fingerprint(inputs: Mapping[str, object]) -> str
```

`canonical_json_bytes()` first applies portable conversion, then emits the
canonical UTF-8 JSON representation used by notebook exports.
`canonical_json_sha256()` hashes those bytes. `state_fingerprint()` requires an
object and hashes it with the same rules.

```python
inputs = {"items": [1, -0.0], "label": "ready"}
assert canonical_json_bytes(inputs) == b'{"items":[1,0],"label":"ready"}'
assert state_fingerprint(inputs) == canonical_json_sha256(inputs)
```

### `parse_canonical_json()`

```python
parse_canonical_json(
    data: str | bytes | bytearray | memoryview,
    path: str = "value",
) -> JsonValue
```

Parses a canonical document and returns detached mutable data. Byte buffers must
be C-contiguous. The function rejects invalid UTF-8, duplicate keys, whitespace
or number spellings that differ from the canonical form, nonportable numbers,
and any byte sequence whose canonical re-encoding differs.

`FrozenJsonValue` is the recursively immutable form returned by reader,
inspection, observation, and spec records. Arrays become tuples and objects
implement immutable `Mapping`. `FrozenJsonObject` is the object-shaped subtype.

## Export index records

Import the durable index model from `marimo_export.index`:

```python
from marimo_export.index import ExportIndex

index = ExportIndex.from_bytes(index_bytes)
```

`EXPORT_SCHEMA` is `marimo-export.export.v1`.

### `ExportIndex`

```python
ExportIndex(
    spec_sha256: str,
    default_state: str,
    notebook: NotebookProvenance,
    producer: ProducerProvenance,
    inputs: tuple[str, ...],
    control_bindings: Mapping[str, ControlBinding],
    outputs: tuple[str, ...],
    aliases: Mapping[str, str],
    states: Mapping[str, StateEntry],
)
```

`default_state` is a state fingerprint. `aliases` maps authored state names to
fingerprints. Every `StateEntry` must have the exact declared input and output
sets. A state key must equal the fingerprint of its complete inputs. One output
name keeps the same codec and media type across every state.

Methods:

```python
index.to_value() -> dict[str, object]
index.to_bytes() -> bytes
ExportIndex.from_value(value: object) -> ExportIndex
ExportIndex.from_bytes(data: bytes) -> ExportIndex
index.assets() -> tuple[tuple[OutputCodec, AssetRef], ...]
index.descriptor_entries() -> Iterator[tuple[str, str, OutputDescriptor]]
```

`to_bytes()` returns canonical JSON and rejects an index larger than 16 MiB.
`from_value()` validates the exact object shape. `from_bytes()` also requires
exact canonical bytes and limits the parsed value count. Format failures raise
`NotebookExportError` with `export_invalid` or `export_noncanonical`.

`assets()` deduplicates by `(codec, SHA-256)` and rejects conflicting descriptor
facts for a reused identity. `descriptor_entries()` yields state fingerprint,
output name, and descriptor for every relation entry.

### Index component records

| Record               | Fields or signature                                                               |
| -------------------- | --------------------------------------------------------------------------------- |
| `NotebookProvenance` | `filename: str \| None`, `document_sha256: str`                                   |
| `ProducerProvenance` | `marimo: str`, `marimo_export: str`, `implementation_sha256: str`                 |
| `StateEntry`         | `StateEntry(*, inputs, outputs)`, immutable `outputs`, detached `inputs` property |
| `ControlBinding`     | `input: str`, `path: tuple[ControlPathStep, ...]`                                 |
| `ControlIndexStep`   | `value: int`, fixed `kind="index"`                                                |
| `ControlKeyStep`     | `value: str`, fixed `kind="key"`                                                  |
| `ControlElementStep` | Fixed `kind="element"`                                                            |

Every record exposes `to_value()`. `ControlPathStep` is the union of the three
step records. An empty path binds a root control. An `index` step selects a
sequence child, a `key` step selects a mapping child, and an `element` step
passes through a wrapper or form element.

## Output descriptor records

Import descriptor values from `marimo_export.descriptors`.

### Codec constants

| Constant              | Value                          | Media-type constant        | Value                                   |
| --------------------- | ------------------------------ | -------------------------- | --------------------------------------- |
| `SCALAR_CODEC`        | `marimo.scalar.v1`             | `SCALAR_MEDIA_TYPE`        | `application/vnd.marimo.scalar.v1+json` |
| `JSON_CODEC`          | `marimo.json.v1`               | `JSON_MEDIA_TYPE`          | `application/vnd.marimo.json.v1+json`   |
| `MARIMO_OUTPUT_CODEC` | `marimo.output.v1`             | `MARIMO_OUTPUT_MEDIA_TYPE` | `application/vnd.marimo.output.v1+json` |
| `MARIMO_CELL_CODEC`   | `marimo.cell.v1`               | `MARIMO_CELL_MEDIA_TYPE`   | `application/vnd.marimo.cell.v1+json`   |
| `NUMPY_CODEC`         | `numpy.npy.v1`                 | `NUMPY_MEDIA_TYPE`         | `application/x-npy`                     |
| `ARROW_CODEC`         | `apache.arrow.file.v1`         | `ARROW_MEDIA_TYPE`         | `application/vnd.apache.arrow.file`     |
| `BLOB_ASSET_CODEC`    | `marimo.blob-asset.msgpack.v1` | Descriptor field           | Versioned BlobAsset media type          |

`OutputCodec` is the closed union of these seven codec strings.
`ScalarValue` is `None | bool | str | int | float`.

### Shared descriptor records

```python
Provenance(python_type: str)
AssetRef(sha256: str, size: int)
AssetRef.path(codec: OutputCodec) -> str
asset_path(codec: OutputCodec, digest: str) -> str
```

`Provenance` records the originating Python type. `AssetRef` requires a lowercase
SHA-256 and a size from 1 through 2,147,483,647 bytes. `asset_path()` derives a
closed path under `assets/` for asset-backed codecs. Inline scalar and JSON
codecs have no asset path.

### Descriptor constructors

| Descriptor               | Constructor-owned fields                                                    |
| ------------------------ | --------------------------------------------------------------------------- |
| `ScalarDescriptor`       | `value`, `provenance`                                                       |
| `JsonDescriptor`         | `JsonDescriptor(*, value, provenance)`                                      |
| `MarimoOutputDescriptor` | `asset`, `provenance`                                                       |
| `MarimoCellDescriptor`   | `asset`, `provenance`                                                       |
| `NumpyDescriptor`        | `asset`, `provenance`                                                       |
| `ArrowDescriptor`        | `asset`, `provenance`                                                       |
| `BlobAssetDescriptor`    | `BlobAssetDescriptor(*, asset, provenance, media_type, filename, metadata)` |

Each descriptor exposes fixed `codec`, `media_type`, and `to_value()` fields.
Asset-backed descriptors expose `asset`. `JsonDescriptor.value` is recursively
immutable. `BlobAssetDescriptor.metadata` returns detached portable JSON.

Scalar descriptors preserve Python integers outside the JavaScript safe range,
NaN, positive and negative infinity, and negative zero through closed tagged
wire values. Portable JSON descriptors accept finite safe-range numbers and
normalize negative zero.

`OutputDescriptor` is the union of every descriptor. `InlineDescriptor` is the
scalar or JSON union. `AssetDescriptor` is the union of the five asset-backed
descriptors.

The [export format reference](../export-format) defines the serialized index,
descriptor, scalar-tag, snapshot, and asset-envelope shapes.

## Typed Python failures

Import general failures from `marimo_export.errors`:

```python
from marimo_export.errors import MarimoExportError

try:
    ...
except MarimoExportError as error:
    report = error.wire()
    print(error.code, error.details)
```

Use explicit exception imports from this module. It has no explicit `__all__`,
so wildcard import can also bind implementation imports that are not part of the
exception contract.

```python
MarimoExportError(
    message: str,
    *,
    code: str | None = None,
    details: Mapping[str, object] | None = None,
)
```

`message` and an explicit `code` must be nonempty strings. `details` must be a
portable JSON object. The `details` property returns a detached object.
`wire()` returns `code` and `message`, plus `details` when nonempty.

### General error classes

| Error                    | Default code              | Contract                                           |
| ------------------------ | ------------------------- | -------------------------------------------------- |
| `MarimoExportError`      | `marimo_export_error`     | Base typed failure                                 |
| `SpecError`              | `spec_invalid`            | Export specification invalid                       |
| `TransportError`         | `transport_failed`        | Server request, stream, or bridge failure          |
| `CaptureLimitError`      | `capture_limit_exceeded`  | Capture exceeded its transfer limits               |
| `SessionError`           | `session_error`           | Live or owned session unavailable                  |
| `NotebookExportError`    | `export_invalid`          | Export missing, malformed, or unreadable           |
| `ExportUnavailableError` | `export_unavailable`      | Valid export storage temporarily unavailable       |
| `IntegrityError`         | `integrity_failed`        | Asset integrity or envelope validation failed      |
| `CompatibilityError`     | `marimo_incompatible`     | Installed marimo adapter capability mismatch       |
| `ExecutionError`         | `state_execution_failed`  | Baseline or state execution failed                 |
| `OutputError`            | `output_execution_failed` | Published output execution failed                  |
| `CodecError`             | `codec_invalid`           | Native cache return cannot enter the export format |
| `StateUnavailableError`  | `state_unavailable`       | Complete input vector absent from the export       |

Repository failures live in `marimo_export.repository`. Observation failures
live in `marimo_export.observations`. `PreparedManifestLimitError` lives in
`marimo_export.manifest`. Each inherits `MarimoExportError`.

### Operation-specific codes

An error instance can refine its class default. Handle the code when recovery
depends on the exact failed operation:

| Area                  | Codes                                                                                                                                                                                                                                                                                              |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spec and selection    | `spec_invalid`, `spec_value_invalid`, `spec_output_invalid`, `spec_exporter_invalid`, `spec_definition_missing`, `spec_definition_conflict`, `spec_input_invalid`, `spec_input_sensitive`, `input_value_invalid`, `control_input_invalid`, `control_input_conflict`                                |
| Exporter and codec    | `runtime_distribution_unavailable`, `exporter_unavailable`, `exporter_invalid`, `exporter_dependency_unavailable`, `exporter_identity_failed`, `exporter_source_changed`, `codec_invalid`, `cache_receipt_missing`, `cache_receipt_invalid`                                                        |
| Session and transport | `transport_failed`, `session_error`, `session_not_found`, `session_ambiguous`, `client_closed`, `owned_notebook_closed`, `bridge_version_mismatch`, `implementation_changed`                                                                                                                       |
| Notebook execution    | `notebook_invalid`, `notebook_changed`, `parent_document_changed`, `parent_state_changed`, `state_execution_failed`, `state_cleanup_failed`, `output_execution_failed`, `output_not_portable`, `output_cell_unavailable`, `preparation_cancelled`, `server_start_failed`, `server_shutdown_failed` |
| Reader and integrity  | `export_invalid`, `export_noncanonical`, `export_unavailable`, `integrity_failed`, `asset_invalid`, `asset_undeclared`, `asset_conflict`, `state_input_invalid`, `state_not_found`, `state_unavailable`, `output_not_found`                                                                        |
| Destination           | `destination_invalid`, `destination_exists`, `destination_changed`, `export_commit_failed`                                                                                                                                                                                                         |
| Repository            | `repository_error`, `repository_limit_exceeded`, `repository_integrity_failed`, `repository_unavailable`, `repository_busy`, `repository_reservation_timeout`, `repository_fence_stale`                                                                                                            |
| Host and observation  | `marimo_incompatible`, `marimo_cache_patch_conflict`, `observation_rejected`, `observation_persistence_failed`, `prepared_manifest_limit_exceeded`                                                                                                                                                 |

`export_parent_sync_failed` and `retired_destination_cleanup_failed` are
post-commit `ExportWarning` codes. They accompany a successful result after the
new destination becomes visible.

`output_not_portable` identifies a selected output whose replay resources
require live Python behavior. The error is an `OutputError`. Its details identify
the `state`, published `output`, `source_kind`, authored `selector`, and source
`cell_id`. A complete-cell source also includes `selector_by`. When a projected
UI object exposes Python callbacks, `reason` is `python_functions`, `functions`
lists their registered names, and `object_id` identifies the UI object.

Some repository subclasses remain implementation-owned, but their codes can
surface through the public `RepositoryError` base. Catch the public base and
branch on `code` when a retry or operator action differs.

## Diagnostic records

`marimo_export.diagnostics.CheckResult` and `marimo_compatibility()` provide a
non-throwing compatibility check for normal diagnostic use. See [Host
integration](host-integration#check-adapter-compatibility) for the signature
and fields.

## Canonical imports and narrow exports

Several public modules re-export the same type for a focused use. Prefer these
locations in application code:

| API                                                                        | Canonical application import                    |
| -------------------------------------------------------------------------- | ----------------------------------------------- |
| Common producer and reader workflow                                        | `marimo_export`                                 |
| `Client`, `Session`, `connect`                                             | `marimo_export.sessions`                        |
| `VerificationResult`, `verify_export`                                      | `marimo_export` or `marimo_export.verification` |
| `NotebookProvenance`, `ProducerProvenance` while reading                   | `marimo_export.reader`                          |
| `JsonValue`, `FrozenJsonValue`, `FrozenJsonObject`, canonical JSON helpers | `marimo_export.wire`                            |
| `ExporterSpec`, `importable`                                               | `marimo_export.exporters`                       |
| Output descriptors and codec constants                                     | `marimo_export.descriptors`                     |
| Durable index records                                                      | `marimo_export.index`                           |

`StrPath` is the public alias `str | os.PathLike[str]` exported by
`marimo_export.spec`. It appears in signatures but ordinary callers can pass a
string or path-like object without importing the alias.

`CaptureLimits`, `CacheSummary`, `StateRunTimings`, and `PhaseTimings` are
lower-level producer protocol records. [Produce an
export](produce#narrow-protocol-records) defines their fields and current
reachability from high-level calls.

`OwnedNotebook` is a single-use inspection context. [Sessions and
inspection](sessions-and-inspection#narrow-ownednotebook-handle) defines its
public lifecycle and the high-level producer replacements for planning and
preparation.

## Programmatic CLI entry point

`marimo_export.cli.main(argv=None) -> int` backs the installed `marimo-export`
console command. Operational success and expected runtime failures return an
exit code. Help, version, and argument-parser outcomes raise `SystemExit` as
defined by Python's argument parser.

The module exports constants for stable process categories:

| Constant           | Value |
| ------------------ | ----: |
| `EXIT_USAGE`       |   `2` |
| `EXIT_ENVIRONMENT` |   `3` |
| `EXIT_PLANNING`    |   `4` |
| `EXIT_EXECUTION`   |   `5` |
| `EXIT_INTEGRITY`   |   `6` |
| `EXIT_REPOSITORY`  |   `7` |
| `EXIT_INTERRUPT`   | `130` |
| `EXIT_BROKEN_PIPE` | `141` |

Unexpected internal failures return `1` with a request ID. Prefer the importable
SDK operations for Python applications. Use the [CLI reference](../cli) for
command syntax, standard streams, and machine-output envelopes.
