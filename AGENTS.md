# AGENTS.md

Guidance for coding agents working in this pnpm, Vite+, and uv workspace for portable marimo notebook projections.

Read [`development_docs/README.md`](./development_docs/README.md) for the contributor documentation map. Read [`development_docs/architecture.md`](./development_docs/architecture.md) before changing cache behavior, schemas, remote execution, transfer, or package boundaries.

## Setup

Use Node 22.18.0 from [`.node-version`](./.node-version), pnpm 11.13.0 from [`package.json`](./package.json), and Python 3.12 from [`.python-version`](./.python-version).

```bash
corepack enable
pnpm install --frozen-lockfile
uv sync --all-extras
```

## Commands

| Purpose            | Command            | Expected result                                              |
| ------------------ | ------------------ | ------------------------------------------------------------ |
| Format             | `make format`      | TypeScript and Python sources are formatted                  |
| Lint               | `make lint`        | Vite+ and Ruff rules pass                                    |
| Type check         | `make typecheck`   | TypeScript, ty, and Pyrefly pass                             |
| Test               | `make test`        | TypeScript and Python unit suites pass                       |
| Remote integration | `make integration` | A real marimo server completes the cache and transfer proof  |
| Build              | `make build`       | Every workspace package and example builds                   |
| Full gate          | `make check`       | Formatting, lint, types, tests, integration, and builds pass |

Before handoff, run `make format`, review its changes, then run `make check`.

## Architecture

- `packages/producer` owns the producer plane. It decodes plans, creates fresh graph-state scenario runners, appends synthetic projection cells, and stores indexes and payloads through marimo's root cache store.
- `packages/client/src/remote` and `packages/client/src/node` own the transfer plane. They attach to a marimo session, stage a verified projection closure, and pull it into a durable directory.
- The root `@marimo-team/marimo-export` entrypoint owns the consumer plane. It validates an index, verifies payloads, and exposes immutable notebook, scenario, and output objects in browsers and server-side rendering runtimes.
- `packages/loader-*` own the codec plane. Each loader matches one Python projection `formatId` and carries its parsing or rendering dependency.
- AnyWidget support is a paired codec. The Python exporter captures a portable static model graph, and `packages/loader-anywidget` owns Pythonless AFM hydration. A widget exporter is incomplete without its matching loader and lifecycle tests.
- A publication directory has one layout: `index.json` plus `cache/<payload-key>`.

## Dependency rule

Python domain modules depend on `marimo_export._marimo`. Private `marimo._...` imports stay inside that adapter package. Universal TypeScript modules use web platform APIs. Node built-ins stay behind the `/node` entrypoint and CLI. Format dependencies stay in Python extras and dedicated loader packages.

Built-in Python serializers belong under `marimo_export.projection.exporters`, grouped by portable contract such as JSON, HTML, dataframes, Vega-Lite, and AnyWidget. The built-in registry owns names, references, option normalization, versions, extras, and availability. It does not own serialization logic. Do not add another serializer to a shared exporter module.

## Invariants

- Each scenario starts from the same immutable saved-notebook bytes in a fresh child runner. The child copies the attached kernel configuration, enables native cell caching when the root notebook has no user arguments, forces autoreload off, keeps the supported relaxed execution type and notebook arguments, and owns a fresh nested-runner registry. User arguments disable native cell caching because `sys.argv` is ambient process state outside marimo's cache identity.
- Builds require an edit-mode kernel using relaxed execution. The stock marimo server hosts each edit-mode kernel in its own process. Run mode does not guarantee process isolation, and the kernel context does not expose its hosting topology. marimo 0.23.14 shares native cache identity across relaxed and strict execution, so a notebook that has run in strict mode needs a fresh `__marimo__/cache` directory before producer use.
- Scenario runs are serialized across the supported producer process because marimo contexts, `sys.argv`, `sys.path`, and cache codec registries share process state. A fresh runner isolates graph state, globals, UI elements, and state objects. It does not isolate imported modules, files, environment variables, random generators, native-library globals, or background tasks.
- Each scenario initializes bound UI elements, applies their values in lazy mode, and schedules the remaining valid authored graph to quiescence after definition overrides. It then runs projections as targeted terminal leaves. Release disposes nested runners deepest first, lifecycle items, hooks, outputs, globals, autoreload, and runtime contexts while preserving the first cleanup failure.
- marimo owns native cache identity, lookup, restoration, and invalidation for eligible authored and synthetic cells. State-writing frontier groups and targeted repairs run live when native restoration cannot preserve their runtime links or side effects.
- Scenario IDs, output names, format names, and plan ordering stay outside projection cache identity. The projection ABI, source, exporter, exporter version, options, prepared HTML token, canonical AnyWidget payload bytes, and notebook dependency lineage participate in identity.
- A terminal synthetic projection cell returns a complete `Projection` as its bare return value. The native cell cache restores that object, including its portable bytes.
- Publication payloads and indexes use the root marimo cache `Store` directly. They stay outside native lazy-cache manifests and touched-key tracking.
- Publication objects use `marimo-export/payloads/sha256/<digest>` and `marimo-export/indexes/<digest>.json`. The producer commits the index after every referenced payload verifies and the saved notebook remains unchanged.
- `ExportRef` anchors the index. The index anchors every payload by key, SHA-256 digest, and byte size.
- marimo-export attaches to a user-managed marimo server by URL and credentials. It never installs an environment, launches a marimo server, or owns the server process. A notebook target may ask that existing server to create or resume a kernel. A session target borrows an existing kernel. The server owns both paths.
- An AnyWidget projection reuses marimo's `ModelLifecycleNotification` wire shape. The producer captures those notifications through `SessionView`, selects the closed graph from the projected root, canonicalizes model IDs, and embeds virtual ESM files.
- AnyWidget capture wraps the child runner stream without taking ownership of the borrowed parent transport. Keep every capture clone active through widget lifecycle disposal so model-close messages are delivered, then detach the complete relay before scenario release. Retained comms must not write through that relay afterward.
- AnyWidget export uses two synthetic cells. An uncached preparation cell evaluates the notebook source and returns canonical `anywidget.v1` payload bytes. A cacheable terminal cell references those bytes as its runtime value dependency and returns the complete `Projection` through marimo's native lazy cache. Keep this path on primitive values and native cell caching. Do not introduce `CustomStub`, `BlobAsset`, a process-global codec, or a parallel cache.
- Embedded AnyWidget modules may reference literal `data:`, HTTP, or HTTPS dependencies. Bundle package and path-relative dependencies before export. Inline marimo virtual files referenced by widget CSS so the publication stays independent from the producer server.
- AnyWidget hydration executes notebook-authored JavaScript as an explicit loader action. Loading and inspecting the payload stays safe in server-side rendering runtimes. Mounting requires a browser DOM. Local model interaction and composed child views work without Python. Python callbacks, comm messages, and notebook recomputation are outside the static hydration contract.
- Staging is a lease. Release every lease and close every remote connection. A connection shuts down a marimo session only when it created that session.
- Write a pulled `index.json` after its payloads so readers never observe a newly committed partial checkout.

## Documentation

- `docs` is the end-user documentation source.
- `development_docs` owns architecture, internals, package boundaries, and validation.
- Package READMEs ship with published packages.
- Examples under `examples/_notebooks` are self-contained marimo notebooks with PEP 723 metadata and adjacent plan files.

Tests should assert public behavior, wire shapes, cache invariants, lifecycle guarantees, and package boundaries. Comments should preserve constraints that the code cannot state directly.
