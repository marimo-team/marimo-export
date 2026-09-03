# Architecture

marimo-export prepares a finite relation of notebook states and named outputs,
retains reusable results, and writes one verified notebook export for Python,
browsers, agents, and custom applications.

```text
notebook + ExportSpec
  -> ExportPlan
  -> reusable states + missing states
  -> marimo execution for missing work
  -> leased PreparedExport
  -> index.json + content-addressed assets
  -> Python, browser, agent, or application
```

## Responsibility boundary

| Owner         | Responsibilities                                                                                                                          |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| marimo        | Notebook parsing, reactive graph, execution, controls, cache keys, cache stores, restoration, native serialization                        |
| marimo-export | ExportSpec, observations, planning, preparation, prepared-state reuse, repository coordination, export format, Python and browser readers |
| application   | Authored presentation, selected spec relation, view bindings, renderer, runtime UX, deployment assembly                                   |

marimo-studio is one application of these capabilities. It compiles HTML
projection hosts into ExportSpec outputs and Studio-owned view bindings. It
uses the public Python SDK to prepare exports and the public browser `prepared`
subpath to drive static state transitions.

The published Marimo 0.24.0 package remains the execution dependency. Private
integration code stays under `marimo_export._marimo.compat` behind
package-owned records and protocols.

## Three persisted layers

```text
Marimo native cache
  restorable notebook computation

marimo-export repository
  observations, prepared output artifacts, generations, leases, reservations

notebook export
  canonical index and declared consumer assets
```

Marimo owns computation-cache identity, persistence, signing, codecs, and
validity. marimo-export stores portable prepared outputs after they cross the
native cache receipt boundary. Exact repository reuse can skip notebook startup.
Native cache reuse can reduce computation when a missing prepared state runs.

The upstream mechanism is documented in marimo's
[caching API](https://docs.marimo.io/api/caching/) and the SciPy 2026 article
[Content-Addressed Caching for Reactive
Notebooks](https://dmadisetti.github.io/scipy_proceedings_2026/). The article
also demonstrates cached WebAssembly publication, where browser Python restores
native cache artifacts. marimo-export uses the same producer-side cache and
publishes selected results through its own language-neutral export format.

Read [Execution and caching](architecture/execution-and-caching.md) and
[Export repository](architecture/repository.md) before changing this boundary.

## Dependency direction

```mermaid
flowchart TB
    apps[CLI, marimo-studio, Python applications]
    sdk[Public Python SDK]
    services[Planning and preparation services]
    readers[Reader and verification]
    publicRepository[ExportRepository]
    integration[Inspection and diagnostics]
    observationWorker[Observation ledger and worker]
    records[Stable package records]
    preparationRepository[Private PreparationRepository]
    observationRepository[Private ObservationRepository]
    marimoPorts[Marimo capability ports]
    sqlite[Private SQLite and artifact adapters]
    compat[Private Marimo compatibility adapters]
    host[Marimo 0.24.0]

    apps --> sdk
    sdk --> records
    sdk --> services
    sdk --> readers
    sdk --> publicRepository
    sdk --> integration
    sdk --> observationWorker
    services --> records
    services --> preparationRepository
    services --> marimoPorts
    readers --> records
    publicRepository --> records
    publicRepository --> sqlite
    publicRepository --> observationRepository
    publicRepository --> preparationRepository
    integration --> marimoPorts
    observationWorker --> observationRepository
    preparationRepository --> observationRepository
    preparationRepository --> sqlite
    observationRepository --> sqlite
    marimoPorts --> compat
    compat --> host
```

The package root exposes common records and operations. `_services` owns
producer planning, preparation, capture, artifact assembly, and durable write
policy. Reader, repository, inspection, and diagnostic operations use their
focused modules. `_repository/preparation.py` contains the private preparation
capability used by producer services. `_repository/observations.py` contains the
private raw observation capability used by the ledger and preparation.
`_marimo/capabilities.py` contains package-owned execution records and
protocols.

The browser dependency direction is:

```text
application renderer
  <- PreparedStatePort
  <- @marimo-team/marimo-export/prepared
  -> browser export reader
  -> one loader facade
  -> one representation runtime
```

Read [Ports and composition](architecture/ports.md) for the public modules,
internal capabilities, composition roots, and enforced import rules.

## Preparation lifecycle

An ExportSpec declares sparse named states, one explicit default state, and
named outputs. Planning infers input names, fills sparse rows from the baseline,
deduplicates equal complete vectors, and resolves repository reuse.

Preparation first checks for an exact verified generation. A hit returns a
leased `PreparedExport` before starting a notebook. A miss claims a fenced
reservation, rechecks repository state, opens or borrows one Marimo session,
captures missing state fingerprints, assembles the relation, and commits one
generation.

`PreparedExport` can be opened, served through independently leased assets,
described by a prepared browser manifest, or written to a caller destination.

Read [Planning and preparation](architecture/preparation.md) for identities,
file and session sources, progress, cancellation, and incremental reuse.

## Durable product model

The durable notebook export stores:

```text
states x outputs -> descriptor
```

`index.json` names the explicit default state, aliases, complete state vectors,
output descriptors, control bindings, producer provenance, and asset closure. A
consumer opens the index before loading representation assets.

Read [Product model and export format](architecture/product-and-export.md) for
the exact records, codecs, writer transaction, and reader invariants.

## Browser lifecycle

The browser core opens and verifies immutable notebook exports. Loader subpaths
decode one representation. The `prepared` subpath adds:

- strict `marimo-export.prepared.v1` parsing
- immutable publication opening
- exact state selection
- sparse input, query, and control updates
- supersession and cancellation
- manifest refresh
- selection rules across publication refresh
- settlement and disposal

An application implements `PreparedStatePort` to load every required output and
publish one complete visible state.

Read [Browser loaders and mounts](architecture/browser-loaders-and-mounts.md)
for integrity, staging, mounts, and disposal. Read
[marimo-studio integration](architecture/studio-integration.md) for the concrete
view compiler, server routes, static bundle, and renderer adapter.

## Ownership zones

| Zone                               | Owns                                                              |
| ---------------------------------- | ----------------------------------------------------------------- |
| `spec.py`                          | Authored ExportSpec and OutputSpec                                |
| `planning.py`, `_execution`        | Public plan records, baseline, normalization, transient cells     |
| `_services`                        | Plan, prepare, capture, artifact assembly, durable write          |
| `observations.py`, `_observations` | Public observation records, queue, worker, source binding         |
| `repository.py`                    | Public repository facade, status, pruning, lifecycle              |
| `_repository/preparation.py`       | Private service-facing repository capability                      |
| `_repository/observations.py`      | Private raw observation persistence for ledger and preparation    |
| `_repository/sqlite`               | SQL schema, connections, transactions, retention queries          |
| `_repository` artifact modules     | Immutable files, staging, leases, fencing, recovery               |
| `_marimo/capabilities.py`          | Package-owned execution records and protocols                     |
| `_marimo` composition roots        | Concrete adapter construction and managed entry points            |
| `_marimo/compat`                   | Private Marimo inspection, execution, cache, projection, transfer |
| `_remote`                          | HTTP, authentication, scratchpad transport, managed process tree  |
| `descriptors.py`, `index.py`       | Durable output and export records                                 |
| `reader.py`, `_writer.py`          | Verified consumer reads and caller destination commit             |
| `_secure_io.py`                    | Bounded platform-safe reads of export indexes and assets          |
| `manifest.py`, `publication.py`    | Prepared manifest serialization and last-good route coordination  |
| `delivery.py`, `_directory*`       | Complete application staging, verification, commit, and rollback  |
| `packages/portable-json`           | Cross-language JSON types, parsing, conversion, Zod adapter       |
| `packages/browser`                 | Browser reader, prepared controller, built-in loaders             |
| `packages/loader-*`                | One specialized decoder and optional runtime                      |

## Secure local reads

`_secure_io.py` is the filesystem trust boundary for local export readers. On
POSIX it traverses from an opened root descriptor and refuses symbolic links. On
Windows it rejects reparse points and rechecks containment and file identity.
Both paths accept the fixed `index.json` name or one portable `assets/<name>`
path, require a regular file, enforce byte limits, and detect size changes during
the read. The Windows path also rechecks file identity during open. Asset digest
verification detects same-size content changes after the secure read.
`reader.py` translates those failures into public format and availability
errors.

## Lifecycle owners

| Resource                          | Owner                           | Release boundary                                   |
| --------------------------------- | ------------------------------- | -------------------------------------------------- |
| Observation worker                | `ObservationLedger`             | `close()` joins and reports persistence status     |
| Preparation reservation           | preparation context             | exact generation returns, failure, or cancellation |
| Prepared-state staging            | `StagedPreparedState`           | state commit or close                              |
| Export staging                    | `StagedExport`                  | generation commit or close                         |
| Repository artifact lease         | prepared state or export handle | handle close or lease failure                      |
| Detached response lease           | `PreparedAsset`                 | response completion and close                      |
| Managed notebook source copy      | `OwnedNotebook`                 | producer context close                             |
| Managed server and process tree   | `OwnedNotebook`                 | producer context close                             |
| Borrowed server and session       | application                     | application close                                  |
| State child graph                 | marimo execution adapter        | state completion, failure, or cancellation         |
| Transfer ticket and virtual files | transfer registry               | client release or lease expiry                     |
| Browser state transition          | `PreparedStateController`       | commit, supersession, failure, or disposal         |
| Mounted representation            | application renderer            | replacement commit or page teardown                |

## Contributor maps

| Question                                                 | Map                                                                                          |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| What is stored in a notebook export?                     | [Product model and export format](architecture/product-and-export.md)                        |
| How are states planned and prepared?                     | [Planning and preparation](architecture/preparation.md)                                      |
| What does SQLite own?                                    | [Export repository](architecture/repository.md)                                              |
| Which dependencies are ports or adapters?                | [Ports and composition](architecture/ports.md)                                               |
| How is Marimo caching reused?                            | [Execution and caching](architecture/execution-and-caching.md)                               |
| How are notebook outputs captured?                       | [marimo integration](architecture/marimo-integration.md)                                     |
| Which private seams could move upstream?                 | [Marimo upstream candidates](architecture/marimo-upstream-candidates.md)                     |
| How does browser state become visible UI?                | [Browser loaders and mounts](architecture/browser-loaders-and-mounts.md)                     |
| How are live sessions authenticated and owned?           | [Live transport and processes](architecture/live-transport-and-processes.md)                 |
| How are prepared routes and application trees committed? | [Application publication and delivery](architecture/application-publication-and-delivery.md) |
| Which hash and schema identifies each boundary?          | [Identities and protocols](architecture/identities-and-protocols.md)                         |
| Which JSON values cross Python and TypeScript?           | [Portable JSON](architecture/portable-json.md)                                               |
| Where does Python run for each application profile?      | [Runtime profiles](architecture/runtime-profiles.md)                                         |
| How does Studio consume the package?                     | [marimo-studio integration](architecture/studio-integration.md)                              |
| How are packages, agents, and docs shipped?              | [Product surfaces and distribution](architecture/agents-and-delivery.md)                     |

[Development](development.md) contains focused workflows. [Validation](validation.md)
maps each changed boundary to required evidence.
