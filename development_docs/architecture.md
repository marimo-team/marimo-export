# Architecture

marimo-export captures selected results from a running notebook, projects them inside that notebook's environment, stores each projection as a marimo `BlobAsset`, and publishes a static index for Python agents and browsers.

```text
Publication =
    Index[(variant, output, format) -> marimo cache asset]
```

The notebook is the source of truth. Capture reads the current kernel and document. The notebook source contains ordinary marimo code, while an external `ExportSpec` supplies publication selection.

## Responsibility map

| Owner                   | Responsibility                                                                                                         |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Running marimo notebook | Python environment, live globals, rendered outputs, UI controls, reactive execution, and configured cache              |
| marimo cache            | Source hashing, projector identity, lookup, restoration, lazy persistence, and configured storage                      |
| marimo-export Python    | Specification, source selection, UI variants, exporters, live capture, transfer, local commit, static reading, and CLI |
| marimo-export browser   | HTTP reading, publication validation, asset verification, `BlobAsset` decoding, and loader dispatch                    |
| Format loaders          | Format-specific decoding, rendering, mounting, and disposal                                                            |

`BlobAsset` and `CustomStub` have separate roles:

| Mechanism              | Contract                                                                     |
| ---------------------- | ---------------------------------------------------------------------------- |
| `BlobAsset`            | Portable bytes returned by the cached projector                              |
| Lazy `.bin` codec      | Serialization and restoration of the complete `BlobAsset`                    |
| `CustomStub`           | Deterministic cache identity and restoration for a source Python type        |
| Stub registration hook | Lazy installation of a `CustomStub` for an optional source package           |
| marimo cache           | Computation identity and persisted objects                                   |
| marimo-export          | Selection, variants, representation mapping, indexing, transfer, and reading |

Source packages own direct stub registration and marimo's lazy stub registration hooks. A hook can register a `CustomStub` when marimo first encounters a matching type in its method resolution order, which avoids importing optional packages during marimo startup. marimo-export consumes the resulting source identity. It does not register or remove process-global stubs during capture.

## Package boundaries

```text
packages/python/
  src/marimo_export/
    client.py
    cli.py
    errors.py
    projection.py
    publication.py
    reader.py
    spec.py
    exporters/
    _remote/
    _marimo/

packages/browser/
  src/
    index.ts
    publication.ts
    source.ts
    blob-asset.ts
    integrity.ts
    loader.ts

packages/loader-*/
schemas/
```

The dependency direction is:

```text
spec, projection, publication, errors
    <- capture and reader services
    <- _marimo and _remote adapters
    <- public Python API and CLI

publication schema
    <- browser reader
    <- format loaders
```

Private `marimo._...` imports stay under `marimo_export._marimo`. HTTP, authentication, session discovery, Server-Sent Events parsing, and scratchpad transport stay under `marimo_export._remote`. Domain modules accept narrow ports and do not import either adapter.

The browser package uses web platform APIs. It contains static publication behavior and no live-session or local-filesystem control path. Each loader package carries the dependency for one format family.

Private Pydantic v2 models own the specification and publication wire structures. `_SpecWire` and `_PublicationWire` generate the checked-in JSON Schemas through `make schemas`. Python semantic validators retain the checks that JSON Schema cannot express, including Python names, expression syntax, exporter imports, built-in option normalization, and canonical projection-metadata byte size. The generated schemas carry no custom metadata-size keyword.

## Core objects

| Object               | Contract                                                                                                     |
| -------------------- | ------------------------------------------------------------------------------------------------------------ |
| `Client`             | Connection to a user-managed marimo server                                                                   |
| `Session`            | Borrowed active notebook session                                                                             |
| `SessionDescription` | Producer versions, typed global descriptors, cell outputs, controls, built-in exporters, and document digest |
| `ExportSpec`         | Strict external selection, format, and variant document                                                      |
| `Projection`         | Portable data, format ID, media type, filename, and JSON metadata                                            |
| `CaptureResult`      | Local publication path and capture receipt                                                                   |
| `Publication`        | Immutable static reader rooted at `index.json`                                                               |
| `PublishedOutput`    | One public result within a variant                                                                           |
| `PublishedFormat`    | One verified portable representation                                                                         |
| `FormatLoader`       | Browser decoder or mount implementation for one format ID                                                    |

Transport tickets, scratchpad markers, code-mode contexts, cache manifests, and `CacheAssetRef` remain internal.

## Live capture flow

```text
Python API or CLI
    -> validate specification and destination locally
    -> discover or select active session
    -> POST one correlated request to /api/kernel/execute
        -> inspect live document and kernel values
        -> resolve and freeze all exporters
        -> preflight named global and cell selectors
        -> snapshot starting UI vector and stale-cell set
        -> apply one finite UI variant
        -> resolve selected sources
        -> run or restore cached projectors
        -> flush cache and resolve exact .bin assets
        -> restore starting UI vector and stale-cell set
        -> register temporary virtual files
    <- index receipt and transfer ticket
    -> preflight index, per-asset, and complete-publication byte limits
    -> download and verify selected cache objects
    -> stage and verify cache objects and index.json
    -> commit a new directory or replace index.json at a stable path
    -> release temporary virtual files
```

Capture uses marimo's edit-scoped `/api/kernel/execute` endpoint. The scratchpad imports the marimo-export bridge inside the running kernel, so the notebook environment must contain the same marimo-export version as the client and every requested exporter extra. Each request carries a unique marker and parses bounded Server-Sent Events. A timeout stops the client from waiting. Already-dispatched kernel work may continue, so capture requests receive no automatic retry.

The client selects existing primary sessions from `/api/sessions`. Omitting a session ID requires exactly one active session. Closing `Client` releases local resources and leaves the server and session running.

Inside code mode:

- `ctx.globals` exposes live Python values.
- `ctx.cells` exposes frozen rendered outputs.
- `ctx.set_ui_value()` applies frontend values to existing UI controls.
- The current document supplies ordered cell source and configuration for provenance hashing.

`ctx.cells` remains frozen for the lifetime of one scratchpad request. Before a variant update, the adapter installs a temporary late post-execution hook on the kernel. The hook captures each rerun's raw last-expression output or stacked imperative output. Source resolution uses that fresh overlay for cells that reran and the frozen code-mode snapshot for other cells. The adapter restores the original hook set before projection continues.

Capture records the document digest before projection and checks it again before returning. It does not create cells or rewrite notebook source.

## Sources and variants

An `ExportSpec` source selects one of:

- A named live global.
- A trusted Python expression evaluated against live globals.
- Rendered cell payload data selected by cell ID or name.

The bridge preflights named global and cell existence before snapshotting or mutating controls. Expressions evaluate after each variant settles. Custom import and variable exporters receive the selected cell's `.data` payload. They never receive the private marimo display record.

A variant maps existing nonsensitive UI-element global names to frontend values. Every variant resolves against the starting UI vector. Capture rejects sensitive targets before mutation. The bridge applies a variant as one batch, waits for reactive dependents, projects outputs, then restores the starting vector.

The publication records the union of control names declared across all variants. A variant that omits one of those names records the control's starting value. Inspection represents sensitive values as `null` and their domains as empty objects.

UI restoration changes inputs and can rerun notebook cells. It cannot roll back file writes, database transactions, service calls, imported-module state, random generators, native-library globals, or background tasks. The public contract promises input restoration and leaves external-effect isolation to the notebook environment.

## Projection and cache integration

Authored cells keep marimo's normal execution and cache semantics while capture applies UI variants. Projection caching is a separate layer over the resulting live values, backed by the same configured root `Store`.

An exporter returns:

```python
Projection(
    data: bytes,
    *,
    format_id: str,
    media_type: str,
    filename: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
)
```

`Projection` remains a transport-independent domain value. The `_marimo` adapter converts it to:

```text
BlobAsset {
  data: portable format bytes
  media_type: string
  filename: string | null
  metadata: {
    format_id: string
    metadata_json: bytes
  }
}
```

`metadata_json` contains at most 262,144 exact bytes for the projection metadata. Readers enforce the byte bound before slicing, strict-parse the UTF-8 JSON into the shared value contract, and compare the resulting value with the index metadata. The comparison is semantic. It does not require Python and browser JSON encoders to produce identical bytes.

The top-level cached return must be the `BlobAsset`. The lazy cache serializes the complete value as MessagePack under `return.bin` and restores it on a hit.

### Cacheable source path

The bridge calls `marimo.persistent_cache()` with `method="lazy"`, module pinning, and a `LazyStore` over the attached context's configured root `Store`. marimo chooses the invocation hash, manifest path, cache key, and `return.bin` bytes. marimo-export reads the exact object selected by that invocation.

Projector identity includes:

- Source value or registered custom-stub bytes.
- Exporter callable identity.
- Exporter version.
- Normalized options.
- Projection application binary interface version, currently `marimo-export.projection.v1`.

HTML uses its prepared portable text as the cached source. AnyWidget uses its canonical live-model payload bytes. Changes to embedded virtual files, widget state, or reachable models therefore participate in the projector identity.

Variant names, output names, format names, and specification order stay outside identity.

### Unhashable source path

Cacheability is an optimization. If marimo cannot hash the source, the bridge runs the exporter live, encodes the resulting `BlobAsset`, and passes those primitive bytes through a separate cached function.

This path still stores a marimo `BlobAsset` and reports projection reuse as `skipped`. A registered `CustomStub` can move the source type onto the cacheable path. marimo-export does not mutate the process-global stub registry per request.

A custom stub's deterministic `to_bytes()` must include the concrete source type and its codec or schema version. Semantically different values or restoration contracts must produce different bytes so they cannot share a cache identity.

### Exact asset receipt

`marimo_export._marimo.cache` owns receipt derivation:

```python
@dataclass(frozen=True)
class CacheAssetRef:
    key: str
    sha256: str
    size: int
```

After a projector call, the adapter:

1. Flushes active lazy-cache writes.
2. Uses the callable's last cache hash to identify the exact invocation.
3. Resolves that invocation's `return.bin` reference.
4. Reads the object through the configured `Store`.
5. Requires the `.bin` codec.
6. Computes the exact size and SHA-256.
7. Checks the manifest's blob digest when it provides one.

Session-wide touched-key sets can contain unrelated notebook cache work and are not a valid receipt source.

## Publication contract

A publication uses schema `marimo-export.publication.v1` and codec `marimo.blob-asset.msgpack.v1`:

```text
publication/
  index.json
  cache/<opaque marimo cache keys>
```

Representative index entry:

```json
{
  "schema": "marimo-export.publication.v1",
  "asset_codec": "marimo.blob-asset.msgpack.v1",
  "notebook": {
    "filename": "finance.py",
    "document_sha256": "..."
  },
  "producer": {
    "marimo": "...",
    "marimo_export": "..."
  },
  "variants": {
    "current": {
      "controls": {
        "symbol_picker": ["AAPL"]
      },
      "outputs": {
        "summary": {
          "formats": {
            "json": {
              "format_id": "json.v1",
              "media_type": "application/json",
              "metadata": {},
              "asset": {
                "key": "<opaque-marimo-key>/return.bin",
                "sha256": "...",
                "size": 184
              }
            }
          }
        }
      }
    }
  }
}
```

The cache key identifies the marimo computation. The digest verifies the transferred envelope bytes. Readers keep those roles separate.

The index is the local commit record. Capture stages and verifies every referenced cache object before commit.

For a new destination, capture uses an atomic no-replace directory rename. A destination created concurrently wins, and capture leaves it untouched.

For `replace=True`, capture keeps the destination path stable. It hard-links verified new cache assets into the existing cache and retains previous assets for readers that already loaded the old index. An existing cache key must match the new digest and size. A mismatch fails before commit. After every referenced asset is available, capture atomically replaces `index.json`. Readers therefore observe either the previous index or the replacement index with all referenced assets present.

## Transfer

The configured marimo `Store` has no general HTTP key endpoint. The bridge reads each selected `.bin` object through the store and registers its bytes in marimo's virtual-file registry.

A transfer ticket contains a private ID, finite expiry, and authenticated URLs for the selected objects. The Python client preflights the serialized index plus declared sizes of the unique asset closure. It then downloads each object, checks its indexed size and SHA-256, and releases the ticket in `finally`. The bridge sweeps expired tickets when handling requests.

Temporary virtual files materialize transport. Durable publication bytes remain the selected marimo cache objects copied into the local publication.

## Readers

Python `open_publication(path)` reads a local publication for agents and applications. Browser `openPublication(url, options)` reads the same wire shape through HTTP.

Both readers:

1. Validate the strict index schema.
2. Reject unsafe cache paths.
3. Bound the asset read.
4. Verify envelope size and SHA-256.
5. Decode the MessagePack `BlobAsset`.
6. Confirm envelope fields against the index.
7. Expose the inner data to a convenience method or loader.

POSIX reads walk the publication through descriptor-relative, no-follow file opens. Windows uses path-based opens with repeated reparse, containment, and identity checks. The Windows path requires the publication tree to remain stable between inspection and the second identity check. A change detected during those checks fails the read.

The Python reader also preflights the actual index bytes plus declared sizes of the unique asset closure. Its defaults are 16 MiB for the index, 64 MiB for one envelope, and 512 MiB for the complete publication. Browser reads apply the index and per-asset limits exposed by `openPublication()`.

The browser package provides bytes, text, JSON, and Blob methods. A `FormatLoader` adds trusted format-specific decoding or mounting after generic integrity verification. Browser mounting is the explicit boundary for notebook-authored JavaScript.

## Format extensions

A format extension starts with a Python exporter and one format ID. Add a browser loader when consumers need browser-side decoding or mounting for that representation.

The exporter owns value normalization, option validation, portable bytes, media type, filename, metadata, and an explicit version for custom import or variable exporters. The loader owns verified-byte decoding, browser dependencies, rendering, mounting, cancellation, and disposal.

Format packages depend on the browser loader contract. They do not read publication paths or decode the marimo envelope themselves.

## Error boundaries

Service, capture, and publication errors derive from `MarimoExportError`:

```text
MarimoExportError
  SpecError
  TransportError
  SessionError
  CaptureError
    SelectionError
    ProjectionError
    TransferError
  PublicationError
    IntegrityError
```

Each adapter translates errors once at its boundary. Cleanup preserves the primary failure. Credentials are redacted before an error crosses into a public exception, CLI response, or receipt.

Local argument validation, object lifecycle, serializer code, and filesystem operations may retain native `TypeError`, `ValueError`, `RuntimeError`, or `OSError` boundaries.

## Marimo release gate

The Python package and `uv.lock` temporarily pin `marimo @ git+https://github.com/peter-gy/marimo.git@0f5fd5d55b4d65d06a814842af3228f57c8ae9c8`, which supplies `BlobAsset` and the lazy `.bin` codec. `/Users/petergy/Projects/personal/marimo` is the inspected upstream reference. Unpublished cross-repository tests may opt into an explicit local overlay while tracked dependency resolution remains on the Git commit.

Python package publication requires an official marimo release that includes that codec. The release change replaces the Git pin with the compatible lower version bound, runs the private-seam and package gates, and confirms the same capture contract against released core. Wheel and source distribution builds from the current checkout are development artifacts.

## Private marimo boundary

`_marimo/compat.py` checks the capabilities needed by the bridge at session attachment:

- Edit-scoped scratchpad execution.
- Code-mode globals, cells, and UI updates.
- Persistent lazy caching against the root `Store`.
- `BlobAsset` and its `.bin` codec.
- Durable cache flush and exact object reads.
- Virtual-file registration and explicit removal.

Keep private imports and version-specific interpretation inside `_marimo`. Domain services should consume narrow results such as a session snapshot, `CacheAssetRef`, or transfer ticket.
