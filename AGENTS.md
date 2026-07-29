# AGENTS.md

Guidance for coding agents working in this uv, pnpm, and Vite+ workspace for
static marimo publications. Read this file before changing the repository.

## Build, test, and lint commands

| Purpose | Command          | Expected on success                                        |
| ------- | ---------------- | ---------------------------------------------------------- |
| Install | `make bootstrap` | locked Python and TypeScript workspaces install            |
| Format  | `make format`    | tracked source is formatted                                |
| Check   | `make check`     | format, lint, types, tests, builds, and package smoke pass |
| Test    | `make test`      | Python, browser core, and loader tests pass                |
| Build   | `make build`     | Python distribution, packages, docs, and app build         |

Use Python 3.11 or newer, Node 22.18, pnpm 11.15.1, uv, and Vite+.
Run focused package commands during development, then run `make format` and
`make check` before handoff.

## Architecture

- `packages/python` owns ExportSpec, build, capture, the CLI, publication
  writing and reading, exporter descriptors, and exporter runtimes.
- `_execution` owns baseline and matrix records. `_marimo/compat` owns every
  private marimo import. `_remote` owns HTTP, SSE, authentication, kernel
  invocation, and managed server lifecycle.
- `packages/browser` owns canonical index parsing, immutable finite states,
  asset integrity, native payload decoding, the `OutputLoader` contract, and
  every published npm entry point.
- `packages/loader-*` are private workspace packages. Each owns one
  representation dependency family, decoder, result type, cancellation
  behavior, and mount disposal.
- `examples/vite-vanilla` is a uv and pnpm workspace member with a live market
  notebook, ExportSpec, and vanilla TypeScript dashboard. `apps/docs` builds
  the public documentation.

See [`development_docs/architecture.md`](development_docs/architecture.md).

## Dependency rule

Keep marimo graph and cache behavior inside marimo. marimo-export constructs
complete inputs, appends synthetic output leaves to in-memory state children,
invokes normal execution, reads native receipts, and publishes verified bytes.

Stable domain modules depend on stable types. Private marimo imports stay below
`packages/python/src/marimo_export/_marimo/compat`. Browser core imports no
table, array, chart, or widget runtime. Each specialized loader declares the
runtime it imports. The browser package publishes loader facades through
`@marimo-team/marimo-export/loader/*` and declares specialized runtimes as
optional peers.

Add dependencies to the smallest workspace member that uses them. Every
directly imported Python package belongs in `packages/python/pyproject.toml`.
Shared TypeScript versions belong in the pnpm catalog. Workspace edges use
`workspace:*`.

## Product contract

An ExportSpec contains:

```text
schema
inputs: notebook definition names
states: sparse authored overrides
outputs: public name -> source definition + optional exporter descriptor
```

The producer captures a baseline and normalizes every state into a complete
input vector. Every output runs through one synthetic child leaf for every
state. Omitting an exporter preserves the native cache representation.
Selecting an exporter invokes one built-in ID or explicit
`module:symbol` reference. The publication contains one canonical
`index.json` and content-addressed assets.

Publication v1 accepts:

```text
marimo.scalar.v1
numpy.npy.v1
apache.arrow.file.v1
marimo.blob-asset.msgpack.v1
```

Media type identifies semantics inside `BlobAsset`. An application selects a
codec-aware `OutputLoader` explicitly.

## Core invariants

- `build` owns an authenticated `127.0.0.1` server, one session, its process
  group, and cleanup. It runs through the current Python interpreter.
- `capture` borrows one active edit session and leaves the session and server
  running.
- The client and attached kernel import the same marimo-export version.
- Capability and exporter preflight runs before state execution.
- Sparse states use notebook definition names. Complete sibling packets
  preserve definitions returned by the same cell.
- Ordinary overrides and UI frontend values remain child-local. Parent UI
  values stay unchanged.
- The authored notebook contains no marimo-export imports or publication
  cells. Each output leaf exists only in an in-memory state child.
- Each state uses marimo child execution with native caching enabled. marimo
  owns dependency pruning, cache identity, serializer choice, persistence, and
  hit or miss status.
- One failed state, output, transfer, verification, or cleanup requirement
  fails the publication.
- Borrowed capture checks parent document and UI identity. Managed build checks
  source bytes before and after execution.
- `index.json` is canonical UTF-8 JSON. State vectors are complete and
  fingerprinted.
- Asset paths derive from codec and SHA-256. Readers verify length, digest,
  native framing, and descriptor agreement before decoding representation
  data.
- New publications stage completely before commit. Replacement keeps
  `index.json` as the publication point.
- Credentials, managed endpoints, session internals, and operation paths stay
  out of publication data and public diagnostics.
- Opening and verification execute no authored browser module. Mounting an
  interactive value has page authority.
- Every mount owns disposal. State transitions abort stale loads and dispose
  replaced or late mounts.

## Python conventions

The package root exports exactly:

```text
BlobAsset
Client
ExportSpec
OutputSpec
Publication
PublicationResult
Session
build
capture
open_publication
```

Typed failures live in `marimo_export.errors`. Translate errors once at adapter
boundaries and preserve a stable code plus bounded JSON details. Use Pydantic
for the ExportSpec boundary and on-demand schema generation. Use structured
data construction for JSON, YAML, and manifests.

Built-in exporter factories return immutable `ExporterSpec` values. Custom
exporters use explicit `module:symbol` references with portable keyword
options. The resolved symbol is callable and returns a value supported by
marimo's native cache codecs. Synthetic leaf code owns conversion cache
identity.

## TypeScript conventions

Use strict TypeScript and web platform APIs in browser code. Core exposes
immutable `Publication`, `PublishedState`, and `PublishedOutput` values.
`PublishedOutput.load()` accepts one explicit typed loader.

A loader validates its inner representation, bounds allocation, checks the
abort signal, and owns every runtime dependency it imports. A mount returns an
idempotent disposable view.

Keep loader implementations in their private `packages/loader-*` workspace.
Expose each public subpath through a `packages/browser/src/loader` facade that
uses the `#loaders/*` TypeScript path. Consumers import the public package
subpath.

The vanilla Vite app uses DOM APIs, TypeScript, HTML, CSS, and Vite+. Keep it
framework-free and inspectable.

## Tests

Protect supported behavior through the nearest public or adapter boundary:

- exact ExportSpec JSON, YAML, and programmatic construction
- baseline normalization, siblings, setup definitions, and UI values
- capability probes and private-marimo containment
- child execution, native cache hits, receipt extraction, and failure cleanup
- transfer integrity, staging, replacement, and secure local reads
- Python and browser canonical wire parity
- exact state lookup and unavailable vectors
- loader matching, malformed bytes, cancellation, and disposal
- packed Python and npm entry points

Keep each test focused on one contract. Assert public outputs, files, protocol
records, or runtime behavior. Use browser evidence for visual and interaction
claims.

## Documentation

`docs` explains installation, ExportSpec, Python and browser APIs,
representations, CLI behavior, and trust. `development_docs` explains internal
architecture, development, and validation. Package READMEs document shipped
entry points.

Use current nouns from source. Start with the smallest working example. Keep
caveats beside the affected API. Comments explain lifecycle ordering, cache
behavior, wire shapes, worker boundaries, compatibility seams, or cleanup
requirements.

## Workflow

1. Inspect the owning package and related tests.
2. Make the smallest coherent change at the current ownership boundary.
3. Run focused formatting, lint, types, and tests.
4. Build the affected package or app.
5. Review the diff for stale contract nouns, private paths, generated noise,
   and narration comments.
6. Commit the coherent unit with a short contract-focused title.
7. Run `make check` before final handoff.
