# Architecture

marimo-export turns a finite set of notebook inputs into verified static
outputs while leaving graph execution and cache identity with marimo.

```text
ExportSpec
  -> baseline inspection
  -> complete state normalization
  -> temporary output leaves
  -> isolated marimo child execution
  -> native cache receipts
  -> canonical publication
  -> explicit browser loaders
```

## Package boundaries

| Path                                               | Responsibility                                                                          |
| -------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `packages/python`                                  | ExportSpec, build, capture, publication writer and reader, CLI, authored Exporters      |
| `packages/python/src/marimo_export/_execution`     | Baseline, normalized states, projection code, matrix records                            |
| `packages/python/src/marimo_export/_marimo/compat` | Every private marimo import and capability probe                                        |
| `packages/python/src/marimo_export/_remote`        | HTTP, SSE, credentials, bridge invocation, managed server lifecycle                     |
| `packages/browser`                                 | Canonical index parsing, immutable states, integrity, native payloads, loader contracts |
| `packages/loader-*`                                | One representation dependency family and result contract                                |
| `apps/finance-demo`                                | Vanilla TypeScript acceptance client over all codecs                                    |

Stable domain modules depend on stable types. Adapters depend on the domain
contract. The Python public roots are `build`, `capture`, `Client`, and
`open_publication`. The browser public root is `openPublication`.

## ExportSpec and baseline

An ExportSpec declares definition names under `inputs`, sparse rows under
`states`, and source definition names under `outputs`.

The selected live session supplies the baseline. Inspection records:

- one defining cell per definition
- every sibling returned by that cell
- ordinary or UI kind
- Python type
- portable baseline or frontend value
- UI domain and sensitivity

Normalization fills every sparse row into a complete input vector. Ordinary
siblings travel as one override packet. UI overrides remain child-local.
Setup definitions receive their override through the compatibility adapter.

## Temporary output leaves

Each output becomes one deterministic leaf cell that returns its source
definition. Code mode creates these leaves in the live document so marimo sees
ordinary graph nodes. The lease records the cell IDs and deletes every leaf in
`finally`.

The parent document digest and public UI values are checked around capture.
Borrowed sessions remain active. Managed build uses an operation-local sibling
copy and checks the original source digest before and after execution.

## Native execution and caching

Every state runs through marimo's `AppKernelRunner` with cache execution
enabled. marimo owns:

- dependency pruning
- cell hashing
- lazy cache lookup
- cache writes
- serializer choice
- cache hit or miss status

The compatibility layer reads the projection cell's native cache attempt and
maps exactly four return families into publication descriptors:

```text
marimo.scalar.v1
numpy.npy.v1
apache.arrow.file.v1
marimo.blob-asset.msgpack.v1
```

marimo-export records cache keys and return references as provenance. It copies
verified native bytes into content-addressed publication paths.

## Capture and build

`capture` selects an existing edit session, invokes the kernel bridge, downloads
temporary virtual files, writes the publication, and releases the transfer
ticket.

`build` starts an authenticated server bound to `127.0.0.1` through the current
Python interpreter. It activates exactly one session and delegates to the same
capture engine. Owned server processes, sockets, notebook copies, and secrets
remain operation-local.

## Publication protocol

One canonical `index.json` contains:

- schema version
- notebook filename and document SHA-256
- producer versions
- sorted input and output names
- complete state vectors and fingerprints
- one descriptor per state and output

Asset paths derive from codec and SHA-256. Equal native bytes share one asset.
The writer stages a complete verified directory and commits `index.json` as the
publication point.

Python local reads defend the filesystem boundary. Browser reads resolve paths
relative to the publication base URL. Both validate length, digest, native
framing, and descriptor agreement.

## OutputLoader

Browser core decodes the stable codec envelope. An application supplies one
`OutputLoader` for the expected codec and media type. Specialized packages own
NPY, Arrow, Parquet, AnyWidget, and Vega-Lite dependencies.

Interactive loaders return a value with `mount()`. Each mount returns a
disposable view that owns DOM, listeners, object URLs, module state, and
renderer finalizers.

## Extension path

A custom Python representation returns native `BlobAsset` with a versioned
media type. A paired `BlobAssetLoader` validates the inner bytes and returns the
application value. This path keeps the stable cache codec closed while media
representations remain extensible.
