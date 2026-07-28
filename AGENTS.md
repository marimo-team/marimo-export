# AGENTS.md

Guidance for coding agents working in this pnpm, Vite+, and uv workspace for static marimo notebook publications.

Read [`development_docs/README.md`](./development_docs/README.md) for the contributor documentation map. Read [`development_docs/architecture.md`](./development_docs/architecture.md) before changing schemas, live capture, cache integration, transfer, or package boundaries.

## Setup

Use Node 22.18.0 from [`.node-version`](./.node-version), pnpm 11.15.1 from [`package.json`](./package.json), and Python 3.12 from [`.python-version`](./.python-version).

```bash
corepack enable
pnpm install --frozen-lockfile
pnpm --filter @marimo-team/marimo-export-loader-anywidget exec \
  playwright install --only-shell chromium
uv sync --all-extras --locked
```

The Python package temporarily pins `peter-gy/marimo` commit `0f5fd5d55b4d65d06a814842af3228f57c8ae9c8`. That revision supplies the `BlobAsset` lazy-cache codec used by capture and publication tests.

For unpublished cross-repository work, overlay `/Users/petergy/Projects/personal/marimo` with `uv pip install --python .venv/bin/python --editable /Users/petergy/Projects/personal/marimo`, then set `UV_NO_SYNC=1` on commands that should retain the overlay. Run `uv sync --all-extras --locked` to restore the pinned Git revision.

Python package publication requires an official marimo release with that codec and a matching released lower bound. Wheel and source distribution builds from the current checkout are development artifacts.

## Commands

| Purpose       | Command              | Expected result                                                           |
| ------------- | -------------------- | ------------------------------------------------------------------------- |
| Format        | `make format`        | TypeScript, Markdown, and Python sources are formatted                    |
| Format check  | `make format-check`  | Committed source formatting is current                                    |
| Lint          | `make lint`          | Vite+ and Ruff rules pass                                                 |
| Type check    | `make typecheck`     | TypeScript, ty, and Pyrefly pass                                          |
| Test          | `make test`          | Browser, loader, and Python unit suites pass                              |
| Integration   | `make integration`   | A running marimo session completes capture and transfer                   |
| Build         | `make build`         | Workspace packages and the Python distribution build                      |
| Package smoke | `make package-smoke` | Packed browser and Python distributions import through public entrypoints |
| Full gate     | `make check`         | Formatting, lint, types, tests, integration, and package smoke pass       |

Before handoff, run `make format`, review its changes, then run `make check`.

## Product contract

marimo-export captures selected results from a running notebook, projects them inside that notebook's Python environment, stores each projection as a marimo `BlobAsset`, and publishes a static index for Python agents and browsers.

```text
Publication =
    Index[(variant, output, format) -> marimo cache asset]
```

The notebook remains ordinary marimo code. An external `ExportSpec` selects live globals, trusted expressions, or rendered cell payloads. A variant supplies frontend values for existing marimo UI controls.

## Architecture

| Path                                        | Responsibility                                                                                                  |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `packages/python`                           | Python API, CLI, specification, projection, capture, transfer, publication reader, and adapters                 |
| `packages/python/src/marimo_export/_marimo` | Private marimo imports and the live kernel, cache, `BlobAsset`, and virtual-file adapters                       |
| `packages/python/src/marimo_export/_remote` | HTTP authentication, session discovery, scratchpad execution, Server-Sent Events parsing, and bounded downloads |
| `packages/browser`                          | Browser publication reading, integrity checks, `BlobAsset` decoding, and loader dispatch                        |
| `packages/loader-*`                         | Format-specific browser decoding and mounting dependencies                                                      |
| `schemas`                                   | Generated external specification and publication wire schemas                                                   |
| `examples/_notebooks`                       | Ordinary notebooks and adjacent export specifications                                                           |

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

Domain modules do not import adapters. Private `marimo._...` imports stay under `_marimo`. HTTP and Server-Sent Events behavior stays under `_remote`. Browser modules use web platform APIs and contain no session-control or filesystem implementation. Loader packages depend on the browser loader contract and own their format dependencies.

Public composition roots are `Client`, `capture()`, `open_publication()`, the Python CLI, and browser `openPublication()`. Sessions and publication navigation objects are returned through these roots. Keep transport markers, transfer tickets, cache manifests, and cache asset references private.

## Invariants

- Capture borrows an active edit-capable marimo session. `Client.close()` releases client resources and leaves the session and server running.
- Scratchpad requests run through `/api/kernel/execute`, carry a unique correlation marker, and use bounded Server-Sent Events parsing. The running kernel must import the same marimo-export version as the client and contain the requested exporter extras. A capture request is never retried automatically.
- Capture preflights named globals and cells before UI mutation. It reads live globals, passes rendered cell payload data to custom exporters, and evaluates trusted expressions after each variant settles. It does not edit notebook source or create cells.
- A variant targets existing nonsensitive marimo UI controls by global name. Every variant starts from the captured input vector. Capture rejects sensitive targets before UI mutation and restores the starting vector after each variant and after failures.
- Capture restores the initial stale-cell set after restoring controls. This restoration can rerun authored cells and preserves the primary capture failure when restoration also fails.
- Inspection redacts sensitive control values and domains. A publication records the union of control names declared across its variants, with starting values filled for variants that omit one of those names.
- Restoring UI controls restores inputs. Notebook-authored writes to files, databases, random generators, imported modules, native libraries, and background tasks remain observable.
- Capture records the live document digest before projection and checks it again before returning. A document change invalidates the capture.
- An exporter returns a public `Projection(data, format_id, media_type, filename, metadata)`. The marimo adapter converts it to a top-level `BlobAsset` for persistence.
- A cacheable source runs through a persistent cached projector. Source value, exporter identity, exporter version, normalized options, and projection ABI participate in identity. Variant, output, and format labels stay outside identity.
- Cacheability is an optimization. When marimo cannot hash a source value, capture runs the exporter live and persists the resulting primitive projection bytes through marimo's cache. The result reports projection reuse as `skipped`.
- Flush lazy-cache writes before resolving an asset. Resolve the exact `return.bin` for the projector call, then verify its size and SHA-256 through the configured root `Store`.
- A publication maps each variant, output, and format to one opaque cache key. `cache/<key>` contains the MessagePack `BlobAsset` envelope. Readers verify the envelope bytes before decoding them.
- The publication index and `BlobAsset` must agree on media type, format identifier, and metadata. Validate the envelope filename as a portable base name. Format loaders receive the inner `data` bytes.
- Projection metadata has a canonical UTF-8 JSON encoding of at most 262,144 bytes. The raw `BlobAsset` `metadata_json` field also accepts at most 262,144 exact bytes. Readers enforce the raw field bound before slicing or decoding and perform no post-parse canonical size check.
- Remote transfer registers selected cache objects as temporary marimo virtual files. The Python client preflights index, asset, and complete-publication byte limits, verifies every download, and releases the transfer ticket in `finally`.
- A new local publication commits through an atomic no-replace directory rename. Replacement keeps the destination path stable, hard-links verified new assets, retains old assets, rejects same-key content collisions, and atomically replaces `index.json` last.
- Server URLs may carry one `access_token` query value. Parse it immediately and keep credentials out of logs, errors, receipts, and publication data.
- Publication readers reject path traversal, malformed schemas, unsafe JSON numbers, digest mismatches, size mismatches, and incompatible `BlobAsset` envelopes before format decoding.
- POSIX publication reads use descriptor-relative no-follow opens. The Windows fallback rejects reparse points, verifies the opened file identity twice, and requires the publication tree to remain stable until the second check completes.
- Browser mounting can execute notebook-authored JavaScript. Loading, inspection, and integrity verification remain separate from mounting.

## API and schema ownership

- Private Pydantic v2 wire models own `marimo-export.spec.v1` and `marimo-export.publication.v1`.
- `schemas/spec.v1.json` and `schemas/publication.v1.json` are generated contract artifacts. Run `make schemas` after changing their owning wire models and commit the result. `make schemas-check` verifies freshness.
- JSON Schema owns structure and portable lexical constraints. Python decoders are authoritative for Python identifiers and keywords, expressions, exporter imports, built-in option semantics, runtime selection, and producer-side canonical metadata byte size. The raw `BlobAsset` reader enforces that byte size before JSON decoding. Do not add a custom schema keyword for it.
- Python and TypeScript readers must reject unknown fields and agree on publication wire values.
- A schema change updates Python models, browser models, fixtures, tests, examples, and docs in one change.
- Service, capture, and publication failures derive from `MarimoExportError` and preserve stable operation-specific types. Local argument, lifecycle, serializer, and filesystem operations may retain native Python errors. Translate an error once at each adapter boundary and redact credentials before constructing user-visible text.

## Exporters and loaders

Built-in Python serializers belong under `marimo_export.exporters`, grouped by portable contract. The registry owns names, versions, option normalization, extras, and availability. Each serializer owns byte production and returns a complete `Projection`.

A format extension pairs:

1. A Python exporter that returns a `Projection`.
2. A browser loader whose `formatId` matches `Projection.format_id`.

Test exporters through the public projection and capture boundary. Test loaders through `PublishedFormat.load()` or `mount()` so verification and envelope decoding remain in the path under test.

## Tests

Protect these contracts at their public or adapter boundary:

- Strict specification and publication decoding.
- Named global, expression, and rendered cell-payload selection.
- UI variant application, quiescence, restoration, and failure cleanup.
- Cold cache, warm cache, changed source, changed exporter version, changed options, unhashable fallback, and registered custom stubs.
- Exact `.bin` receipt resolution through the configured `Store`.
- Transfer ticket bounds, explicit release, expiry cleanup, and primary-error preservation.
- Atomic new-directory commit and stable-path replacement with `index.json` as commit point.
- Python and browser verification before MessagePack or format decoding, including aggregate Python closure limits and the raw metadata byte bound.
- POSIX descriptor-relative reads and the Windows stable-tree precondition.
- Browser loader matching, cancellation, mounting, and disposal.
- Package boundaries and packed public exports.

Conformance tests should exercise narrow internal ports such as kernel transport, projection cache, publication source, and exporter. Avoid tests that pin helper names or implementation order when callers cannot observe them.

## Documentation

- `docs` owns installation, capture, specification authoring, reading, CLI use, and trust guidance.
- `development_docs` owns architecture, private integration, package boundaries, and validation.
- Package READMEs describe the public surface that ships with each package.
- Examples keep notebook code focused on notebook behavior. Export selection belongs in adjacent specification files.

Comments preserve lifecycle ordering, cache identity, wire shapes, cleanup requirements, and compatibility constraints. Delete comments that narrate ordinary code.
