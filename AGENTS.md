# AGENTS.md

Guidance for coding agents working in this uv, pnpm, and Vite+ workspace.
marimo-export runs selected marimo notebook states and writes one verified
export for applications, agents, Python, and custom clients.

## Commands

Use Python 3.12 for local development with Node 22.18, pnpm 11.15.1, uv,
and Vite+. CI verifies the Python package on 3.10 through 3.14 on Ubuntu and
Windows.

| Task                | Command           | Expected result                                            |
| ------------------- | ----------------- | ---------------------------------------------------------- |
| Install             | `make bootstrap`  | Locked Python and TypeScript workspaces sync               |
| Format              | `make format`     | Authored source is formatted                               |
| Lint                | `make lint`       | Python and TypeScript boundaries pass                      |
| Type-check          | `make typecheck`  | Python and TypeScript types pass                           |
| Test                | `make test`       | Python, browser, loader, skill, and app tests pass         |
| Build               | `make build`      | Python, npm, docs, and example packages build              |
| Build docs          | `make docs-build` | VitePress site and LLM text bundles build                  |
| Serve docs          | `make docs-serve` | Documentation runs at `127.0.0.1:54173`                    |
| Package             | `make package`    | Python and npm release artifacts pass isolated smoke tests |
| Complete local gate | `make check`      | Format, lint, types, tests, builds, and package smoke pass |

Run focused package commands while developing, then finish with `make check`.

## Architecture in nine rules

1. marimo owns notebook parsing, reactive execution, dependency pruning, cell
   hashing, computation-cache persistence, UI updates, and native
   serialization.
2. marimo-export owns ExportSpec normalization, observations, state planning,
   preparation, reusable prepared outputs, export integrity, and typed Python
   and browser consumption.
3. The export repository stores preparation metadata in private SQLite tables
   and portable artifacts in immutable directories. Marimo's native cache
   remains the computation cache.
4. Producer functions call services under `_services`. Those services use the
   private `PreparationRepository`. Observation workers use the private
   `ObservationRepository`. Reader, public repository, inspection, and
   diagnostic operations use their focused owners.
5. Private `marimo._*` imports stay under `_marimo/compat`. The package pins
   Marimo 0.24.0 and validates the exact supported cache sources before
   installing a reversible adapter lease.
6. `OwnedNotebook` owns a temporary notebook copy, loopback server, session,
   process groups, and cleanup. `prepare` uses one owned context when work is
   missing. `capture` borrows one active edit session.
7. `packages/portable-json` owns the cross-language JSON boundary.
   `packages/browser` owns export parsing, integrity, immutable readers,
   prepared-publication control, and loader contracts. Each
   `packages/loader-*` owns one representation runtime.
8. marimo-studio compiles an authored view to ExportSpec outputs and view
   bindings. It consumes the public Python SDK, the public browser `prepared`
   subpath, and the public host-cache integration capability. Preparation,
   repository, and Marimo cache behavior stay in marimo-export.
9. `docs/` owns user workflows and reference. `development_docs/` owns code
   ownership, lifecycle, compatibility seams, and contributor validation.

Read [Architecture](development_docs/architecture.md) before changing an
ownership or lifecycle boundary.

## Dependency rule

Dependencies point from each public operation toward its focused owner. The
producer path continues through narrow capabilities to private adapters:

```text
CLI and applications -> public Python SDK
  producer operations -> services -> records and capabilities
  reader operations   -> reader and verification
  repository API      -> ExportRepository -> SQLite and artifact adapters
  diagnostics         -> Marimo composition root

Observation worker -> ObservationRepository -> SQLite adapter
Producer services  -> PreparationRepository -> repository adapters

Marimo composition roots -> private Marimo compatibility adapters

Browser application -> prepared controller -> browser core -> loader facade
                                                        -> one loader runtime
```

- `_services` owns planning, preparation, capture, artifact assembly, and
  durable writes.
- `delivery.py` owns application-level staging and prepared-export
  materialization. `_directory*.py` owns destination races, native exchange,
  and rollback.
- `_repository/preparation.py` is the private preparation capability available
  to services. `_repository/observations.py` is the private raw observation
  capability available to the ledger and preparation. `_repository/sqlite`
  owns SQL and schema details.
- `_marimo/composition.py`, `_marimo/anywidget.py`, `_marimo/blob.py`, and
  `_marimo/entrypoints.py` construct private Marimo adapters.
- `_remote` owns HTTP, authentication, scratchpad transport, server-sent
  events, and managed process ownership.
- Browser core imports no specialized chart, table, array, or widget runtime.
- Loader packages either implement browser contracts or expose a
  browser-independent decoder bound by one public facade. A loader package
  referenced by browser remains browser-independent. Loader packages remain
  independent of one another.
- Add dependencies to the smallest workspace member that imports them.

Ruff and repository boundary tests enforce service, repository, and private
Marimo containment. Vite+ enforces browser and loader direction.

## Product language

marimo-export creates a **notebook export**. Use **export** after defining the
noun once.

| Action                                 | Verb            |
| -------------------------------------- | --------------- |
| Resolve reusable and missing work      | plan            |
| Prepare from a notebook file           | prepare         |
| Prepare from a running session         | capture         |
| Prepare and write from a notebook file | build           |
| Write a prepared export                | write           |
| Read in Python or TypeScript           | open            |
| Select a state by name or inputs       | resolve         |
| Decode an output for browser use       | load            |
| Attach an interactive value to the DOM | mount           |
| Put static files on a host             | deploy or serve |
| Release a package to a registry        | publish         |

A state is one complete assignment for the inferred ExportSpec inputs. Authors
may write sparse rows because one captured baseline supplies omitted values. An
observation is one successful complete input vector retained as authoring
evidence. A prepared state is a reusable portable result for one producer,
output plan, and state fingerprint. A prepared export is a leased immutable
generation for one exact ExportSpec. An output is one published name and
representation for every state. An asset is a content-addressed file referenced
by an output descriptor. A consumer is a Python reader, browser reader, agent,
or another implementation of the export format.

## Change routing

| Change                            | Primary owner                                                                  | Required companions                                          |
| --------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------ |
| ExportSpec or state normalization | `spec.py`, `_execution/plan.py`, `planning.py`                                 | YAML, JSON, programmatic, and live-state tests               |
| Observation lifecycle             | `observations.py`, `_observations`, `_repository/observations.py`, compat hook | Success, source-match, queue, revision, and close tests      |
| Planning or preparation           | `_services`, `prepared.py`, `progress.py`                                      | Exact reuse, incremental state, cancellation, and live tests |
| Repository or retention           | `repository.py`, `_repository`                                                 | SQLite, filesystem, concurrency, fencing, and recovery tests |
| Export format or local reader     | `descriptors.py`, `index.py`, `wire.py`, `reader.py`                           | Browser schema and canonical fixtures                        |
| Marimo integration or cache       | `_marimo/capabilities.py`, composition, one compat adapter                     | Probe, adapter, build, and capture tests                     |
| Managed process lifecycle         | `producer.py`, `_remote/managed.py`, managed entry points                      | Startup, shutdown, source, and descendant tests              |
| Prepared browser control          | `packages/browser/src/prepared`                                                | Manifest, refresh, transition, cancellation, packed tests    |
| Browser reader or loader contract | `packages/browser`                                                             | TypeScript and cross-language tests                          |
| Portable JSON contract            | `packages/portable-json`, `_json.py`                                           | Cross-language fixtures and packed consumers                 |
| One output representation         | `packages/loader-*` and exporter runtime                                       | Peer dependency, malformed input, abort, and disposal tests  |
| CLI or public Python API          | `_cli`, package root, public records                                           | Human output, JSON, JSONL, exit, and wheel smoke             |
| Application directory delivery    | `delivery.py`, `_directory*`                                                   | Materialization, races, rollback, and Windows tests          |
| marimo-studio integration         | Public SDK and browser `prepared` subpath                                      | Studio unit, server, static export, and browser tests        |
| Example or browser transition     | `examples/vite-vanilla`                                                        | Typecheck, build, desktop, and narrow browser proof          |
| Public documentation              | `docs/`, VitePress config                                                      | Examples, links, search, LLM bundles, and rendered proof     |

## Core invariants

- Every output runs through one transient marimo leaf for every state.
- State vectors are complete and fingerprinted before execution.
- The ExportSpec names an explicit default state. Authoring observations remain
  repository history until an application places them in an explicit spec.
- Public observation and exact-prepared repository operations accept an
  `ExportPlan`. Raw producer keys remain inside private repository capabilities.
- A prepared state is reusable by producer identity, output-plan identity, and
  complete state fingerprint. A prepared export adds the exact spec identity.
- Exact reuse returns a leased `PreparedExport` before starting a notebook.
- Ordinary overrides and UI updates stay local to one child state run.
- AnyWidget patches record the complete serializer-owned model state.
- The authored notebook source remains byte-for-byte unchanged.
- The client and attached kernel use the same marimo-export version and source
  identity.
- One failed state, output, transfer, or verification fails the complete
  producer operation. Cleanup preserves the primary error. Post-commit parent
  sync and retired-destination cleanup failures return typed warnings.
- Preparation reservations carry monotonically increasing fencing tokens.
  Publication compares the current pointer and reservation owner before commit.
- Active artifact, staging, and preparation leases renew until their owner
  closes. Retention preserves every live artifact.
- Repository storage failures preserve the current prepared export. Confirmed
  integrity failures retire the affected artifact for cleanup.
- `index.json` is canonical UTF-8 JSON and the single export entry point.
- Readers verify the declared asset closure before decoding output data.
- Application delivery re-verifies each materialized export before committing
  the complete outer directory.
- Opening and verification execute no notebook-authored browser module.
  Mounting an interactive value grants it page authority.

## Validation

- Test through public APIs, command results, files, protocol records, package
  imports, or browser state. Avoid assertions on private helper trivia.
- Run live build and capture evidence after changing marimo integration,
  process ownership, state execution, cache behavior, or transfer.
- Use browser inspection for layout, responsive behavior, rapid state changes,
  charts, AnyWidgets, cancellation, and mount disposal.
- Rebuild generated package and documentation output from its owning source.
  Do not commit raw build directories.

## Reference

- [Contributor guide](development_docs/README.md)
- [Architecture](development_docs/architecture.md)
- [Product model and export format](development_docs/architecture/product-and-export.md)
- [Preparation](development_docs/architecture/preparation.md)
- [Export repository](development_docs/architecture/repository.md)
- [Ports and composition](development_docs/architecture/ports.md)
- [Execution and caching](development_docs/architecture/execution-and-caching.md)
- [marimo integration](development_docs/architecture/marimo-integration.md)
- [Marimo upstream candidates](development_docs/architecture/marimo-upstream-candidates.md)
- [Browser loaders and mounts](development_docs/architecture/browser-loaders-and-mounts.md)
- [Live transport and processes](development_docs/architecture/live-transport-and-processes.md)
- [Application publication and delivery](development_docs/architecture/application-publication-and-delivery.md)
- [Identities and protocols](development_docs/architecture/identities-and-protocols.md)
- [Portable JSON](development_docs/architecture/portable-json.md)
- [Runtime profiles](development_docs/architecture/runtime-profiles.md)
- [marimo-studio integration](development_docs/architecture/studio-integration.md)
- [Product surfaces and distribution](development_docs/architecture/agents-and-delivery.md)
- [Development](development_docs/development.md)
- [Documentation system](development_docs/documentation.md)
- [Validation](development_docs/validation.md)
- [Releasing](development_docs/releasing.md)
