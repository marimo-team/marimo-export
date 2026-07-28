# Trust and integrity

marimo-export separates static-byte verification from representation
execution.

## Producer boundary

Build and capture execute notebook code with the notebook environment's normal
authority. Input overrides run through marimo's dependency graph. Authored
Exporter cells can access the same files, credentials, network, and libraries
as the notebook.

Capture borrows a live session. Temporary output leaves are removed in
`finally`. State execution happens in child runtimes. The parent input values
and source document are checked across the operation.

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
