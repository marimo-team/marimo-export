---
title: Read and verify exports from Python
description: Open an immutable notebook export, select an exported state, decode an output, and verify every asset.
---

# Read and verify exports from Python

`open_export()` validates canonical `index.json` and returns an immutable
`NotebookExport`. Output assets remain unread until a caller decodes one output
or verifies the complete export.

```python
from marimo_export import open_export

export = open_export("dist/report")
state = export.default_state
summary = state.output("summary").json()

print(state.aliases)
print(dict(summary))
```

Opening and verification execute no notebook-authored browser module. Treat an
HTML string or interactive module according to the consuming application's
trust policy when that application later renders or mounts it.

## `open_export()`

```python
open_export(path: str | os.PathLike[str]) -> NotebookExport
```

`path` must be a real directory. Opening rejects a symbolic-link export root, a
symbolic-link or nonregular `index.json`, a missing or noncanonical index, an
unknown schema or codec, a declared asset larger than 64 MiB, and a declared
index-plus-unique-asset closure larger than 512 MiB. Asset access and complete
verification separately reject symbolic-link or nonregular asset files. Opening
validates the complete declared relation without reading asset contents.

Storage that is temporarily unavailable raises `ExportUnavailableError`.
Malformed paths, indexes, and relations raise `NotebookExportError`.

## `NotebookExport`

`NotebookExport` is immutable. Its identity is the lowercase SHA-256 of the
exact canonical `index.json` bytes.

Obtain a validated reader from `open_export()` or `PreparedExport.open()`.
Direct `NotebookExport(path, index, identity)` construction accepts already
parsed records and does not perform the canonical-byte, filesystem, symlink,
size, or asset-closure checks owned by those opening operations. An
`isinstance(value, NotebookExport)` check is therefore not evidence that files
were opened and verified through the public reader boundary.

Properties:

```python
export.path: Path
export.identity: str
export.spec_sha256: str
export.default_state: ExportState
export.input_names: tuple[str, ...]
export.control_bindings: Mapping[str, ControlBinding]
export.output_names: tuple[str, ...]
export.notebook: NotebookProvenance
export.producer: ProducerProvenance
```

`default_state` resolves the fingerprint selected by the authored
`default_state` alias. `control_bindings` maps projection-scoped UI object IDs to
root input names and typed paths.

Selection methods:

```python
export.states() -> tuple[ExportState, ...]
export.state(alias: str) -> ExportState
export.state_by_fingerprint(fingerprint: str) -> ExportState
export.resolve(inputs: Mapping[str, JsonValue]) -> ExportState
export.verify() -> VerificationResult
```

`state()` selects an authored alias. Unknown aliases raise
`NotebookExportError` with code `state_not_found` and up to 16 available aliases
in `details`.

`state_by_fingerprint()` accepts a lowercase SHA-256 digest. An unknown digest
raises `state_not_found`.

`resolve()` requires a complete mapping with exactly `input_names`. It
canonicalizes the values and returns the existing state with that complete
vector. Missing or extra names raise `state_input_invalid`. A valid vector that
was not prepared raises `StateUnavailableError` with code `state_unavailable`.

## `ExportState`

An `ExportState` is one exported state: a complete input vector and its named
outputs in the notebook export. Several authored aliases can select the same
exported state.

```python
state.aliases: tuple[str, ...]
state.fingerprint: str
state.notebook_export: NotebookExport
state.inputs: Mapping[str, FrozenJsonValue]
state.outputs() -> tuple[ExportOutput, ...]
state.output(name: str) -> ExportOutput
state.resolve(patch: Mapping[str, JsonValue]) -> ExportState
```

`inputs` is recursively immutable. `output()` raises `NotebookExportError` with
code `output_not_found` when `name` is absent.

`resolve(patch)` replaces the named root input values in the current complete
vector, then performs exact export resolution. It does not deep-merge nested
objects. An empty patch returns the same state. Unknown input names raise
`state_input_invalid`. A resulting vector absent from the export raises
`StateUnavailableError`.

```python
weekly = export.default_state.resolve({"interval": "1wk"})
```

Resolution selects prepared data. A value outside the exported relation needs a
new producer run or a Python service.

## `ExportOutput`

An `ExportOutput` binds one published output name to one state and one validated
descriptor.

```python
output.name: str
output.state: ExportState
output.codec: str
output.media_type: str
output.descriptor: OutputDescriptor

output.scalar() -> None | bool | str | int | float
output.json() -> FrozenJsonValue
output.asset_bytes() -> bytes
output.blob_asset() -> BlobAsset
```

Choose the accessor from the descriptor codec:

| Codec                          | Python accessor                   |
| ------------------------------ | --------------------------------- |
| `marimo.scalar.v1`             | `scalar()`                        |
| `marimo.json.v1`               | `json()`                          |
| `marimo.output.v1`             | `asset_bytes()`                   |
| `marimo.cell.v1`               | `asset_bytes()`                   |
| `numpy.npy.v1`                 | `asset_bytes()`                   |
| `apache.arrow.file.v1`         | `asset_bytes()`                   |
| `marimo.blob-asset.msgpack.v1` | `blob_asset()` or `asset_bytes()` |

Calling an accessor for another codec raises `NotebookExportError` with code
`codec_invalid`.

`json()` returns tuples and immutable mappings. `blob_asset()` returns a new
immutable `BlobAsset` with `data`, `media_type`, `filename`, and recursively
immutable `metadata`.

Asset access reads one declared file, verifies its size and SHA-256, validates
its native framing, and validates BlobAsset descriptor agreement where
applicable. A missing or changed asset raises `IntegrityError`. Temporarily
unavailable storage raises `ExportUnavailableError`.

## Verify the complete asset closure

```python
from marimo_export import verify_export

verified = verify_export("dist/report")
```

```python
verify_export(path: str | os.PathLike[str]) -> VerificationResult
export.verify() -> VerificationResult
```

Both forms inspect the export directory, reject undeclared files under
`assets/`, and read each unique declared asset once. `VerificationResult.states`
counts exported states. `outputs` counts state-output pairs. `assets` counts
unique asset files. `bytes_verified` counts those asset bytes and excludes
`index.json` and inline values. `to_dict()` returns those four fields.

Verification proves consistency with `index.json`. It does not authenticate the
person or system that produced that index. Bind provenance to a trusted index
identity when producer authenticity affects the application.

## Provenance records

`NotebookProvenance` contains:

```python
filename: str | None
document_sha256: str
```

`ProducerProvenance` contains:

```python
marimo: str
marimo_export: str
implementation_sha256: str
```

Both records are immutable and expose `to_value()`. Import them from
`marimo_export.reader` when handling values returned by `NotebookExport`. Their
defining low-level module is `marimo_export.index`.

Use [Output representations](../representations) to choose a representation
for the next consumer. Use [Format records and errors](format-records-and-errors)
to inspect descriptors or implement directly against the export format.
