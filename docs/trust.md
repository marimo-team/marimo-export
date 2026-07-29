# Trust and integrity

marimo-export separates static-byte verification from representation
execution.

## Producer boundary

Build and capture execute notebook code with the notebook environment's normal
authority. Input overrides run through marimo's dependency graph. Exporter
functions selected by the sidecar spec execute with the same access to files,
credentials, network, and libraries as the notebook.

Capture borrows a live session. State execution and transient output leaves
exist in child runtimes. Destroying a child removes its leaves. The parent input
controls and source document are checked across the operation.

Custom exporter references resolve installed kernel modules. Review and pin
those packages like notebook dependencies. The spec carries the import
reference and portable options, never executable source or serialized
closures. The projection cache includes the resolved module, function, package
version when available, and declared built-in runtime dependency versions.
Network responses, files read by the function, and other external state require
the same explicit invalidation discipline as notebook code.

## Publication boundary

`index.json` is canonical UTF-8 JSON. It contains complete state vectors,
state fingerprints, output descriptors, producer provenance, and
content-addressed asset references.

Readers validate:

- schema and exact fields
- bounded strings, values, depth, and counts
- portable number semantics
- state fingerprints and vector uniqueness
- codec and media-type agreement
- declared asset size and SHA-256
- NPY and Arrow framing
- native `BlobAsset` MessagePack fields
- filename and local path safety

Python reads local assets through no-follow filesystem operations. Browser
reads use relative URLs and Web Crypto.

## Loader boundary

Opening, inspection, and verification execute no notebook-authored JavaScript.
A loader begins representation-specific parsing. Mounting AnyWidget or
Vega-Lite can execute authored modules, expressions, external requests, and
rendering logic with page authority.

Applications choose loaders explicitly, apply Content Security Policy and
CORS, pass byte limits, cancel stale loads, and dispose mounted values.

## Hosting

Serve the publication directory unchanged through HTTPS or localhost. Asset
URLs resolve relative to the URL containing `index.json`. Configure immutable
caching for content-addressed assets. Revalidate `index.json` according to the
deployment's publication update policy.
