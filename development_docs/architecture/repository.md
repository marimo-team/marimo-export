# Export repository

The export repository retains observed input vectors, reusable prepared states,
and immutable prepared exports. `ExportRepository` is the public facade. SQLite
coordinates metadata and concurrent owners while verified files carry the
portable artifacts.

```text
repository/
  catalog.sqlite3
  maintenance.sqlite3
  prepared-states/<producer>/<output-plan>/<state>/<instance>/
  exports/<export-identity>/<instance>/
  staging/<operation>/
```

The repository is local producer infrastructure. A deployed notebook export
contains `index.json` and its declared assets.

## Repository and computation cache are separate

| Store               | Owner         | Retained value                                        |
| ------------------- | ------------- | ----------------------------------------------------- |
| Marimo native cache | marimo        | Restorable notebook cell definitions and returns      |
| Export repository   | marimo-export | Portable prepared outputs and lifecycle metadata      |
| Notebook export     | marimo-export | Canonical index and content-addressed consumer assets |

Marimo computes cache keys, selects the configured cache store, validates cache
entries, serializes values, verifies signatures, and restores cell state.
marimo-export observes native hit and miss decisions and reads verified native
returns through the compatibility adapter.

The export repository stores the result after it has crossed the portable
output boundary. Repository reuse can therefore avoid notebook startup. If a
prepared state is absent, Marimo's native cache may still avoid notebook cell
computation while that state is rebuilt.

## Identities define reuse

A prepared state is addressed by:

```text
producer_sha256 + output_plan_sha256 + state_fingerprint
```

An exact prepared export is addressed by:

```text
producer_sha256 + output_plan_sha256 + spec_sha256
```

`producer_sha256` covers the stable notebook source, canonical notebook
document, Python and operating-system runtime, installed distributions, local
runtime sources, Marimo version, marimo-export version, and marimo-export
implementation identity.

`output_plan_sha256` covers the authored output declarations. Presentation HTML,
CSS, JavaScript, route names, and view host IDs remain outside that identity.
Two applications can therefore reuse the same prepared outputs when they request
the same producer, outputs, and states.

`spec_sha256` covers the complete canonical ExportSpec, including state aliases
and the default state. Changing presentation assets preserves repository reuse.
Adding one state retains matching prepared states and prepares the missing
fingerprint.

## SQLite owns metadata and coordination

`_repository/sqlite` is the sole SQL owner. `catalog.sqlite3` tracks:

- producers and monotonic observation revisions
- canonical observed vectors and bounded observation history
- prepared-state scopes and immutable instances
- exact export identities and immutable generations
- generation membership in prepared states
- artifact, staging, and preparation leases
- preparation fencing tokens
- retention and retired-artifact accounting

`maintenance.sqlite3` supplies one cross-process `BEGIN IMMEDIATE` lock for
filesystem mutation. Retention and recovery take catalog snapshots, perform
filesystem work outside the catalog writer transaction, then commit matching
results through short catalog transactions.

SQLite stores canonical metadata bytes and artifact identities. Prepared output
payloads stay in immutable directories. Repository services outside
`_repository/sqlite` operate on package records and capability methods.

`SqliteCatalog` owns connection and transaction boundaries. Focused modules
under `_repository/sqlite` own observations, prepared states, generations,
leases, retention, and recovery queries. Splitting query ownership keeps one
transaction root while avoiding SQL in lifecycle and preparation services.

Two private repository facades keep raw keys and storage mechanics out of their
callers:

- `ObservationRepository` owns producer-keyed observation writes, revisions,
  queries, latest-vector lookup, and snapshots for the observation ledger and
  preparation.
- `PreparationRepository` owns exact-generation and prepared-state lookup,
  reservations, staging, guarded commit, and revision-consistent observation
  reads for producer services.

The repository uses Python's built-in `sqlite3`. marimo-studio reaches the
repository through public marimo-export operations and carries no database
schema or SQL.

## Observations are authoring evidence

`ObservationLedger` receives complete portable input vectors from successful
normal notebook runs. The Marimo adapter records a candidate after a run that
finished with no exception, interruption, cancellation, or scratch-cell work.

Before persistence, the worker verifies that the live cell signature matches
one stable saved notebook revision. The resulting producer identity binds the
observation to the source that produced it.

The ledger has a bounded coalescing queue. Repeated adjacent vectors accumulate
an occurrence count. Queue eviction preserves the monotonic observation
revision even when the vector itself is not retained. `flush()` waits for queued
writes and surfaces a persistence failure. `close()` drains the worker, closes
an owned repository, and reports the first failure.

Application code records a vector through
`ExportRepository.record_observation(plan, inputs)`. The facade validates that
`inputs` contains exactly the inferred names in `plan`, then delegates the
producer-keyed write to `ObservationRepository`.

An ExportSpec is the reviewable state contract. Observations remain local
history until an application selects vectors and places them in an explicit
spec.

## Immutable artifact lifecycle

Preparation writes into a private staging directory. State commit writes and
verifies a `marimo-export.prepared-state.v1` manifest that binds the producer,
output plan, state fingerprint, metadata, file closure, byte count, and instance
digest.

Generation commit verifies the complete notebook export, its exact state
membership, and its spec identity. Each committed instance is immutable. A
scope row contains the mutable pointer to the current instance.

Artifact commit uses three phases:

1. A short catalog transaction validates reservation, fence, pointer, and admission.
2. Filesystem installation and complete verification run outside the catalog writer transaction.
3. A short transaction rechecks the fence and pointer, records the instance, and acquires its lease.

Filesystem installation uses a same-filesystem atomic replacement sequence. A
lost final compare-and-swap retires the uncommitted installation for accounted
cleanup. The prior current instance remains selected.

Committed files become owner-readable and directories become owner-readable and
executable on POSIX. Cleanup first restores owner write permission. Windows uses
the same logical verification and lifecycle with best-effort permission changes.

## Reservations and fencing

One preparation reservation owns one exact export identity. The first claimant
receives a monotonically increasing fencing token. The lease manager renews the
owner, token, staging paths, and live artifact leases.

Acquisition polls for a bounded interval and raises a repository failure with
code `repository_reservation_timeout` when another healthy owner retains the
identity. The active reservation exposes liveness to preparation cancellation.

Every state and generation commit proves:

1. the reservation identity matches the export identity
2. the reservation owner still holds the row
3. the fencing token is current
4. the reservation has not expired
5. an initial pointer is still empty, or the named replacement is still current

A stale worker receives `RepositoryFenceError` and cannot replace a newer
generation.

Prepared states are shared across exact spec identities. Concurrent preparations
that produce the same content-addressed state instance converge on that instance.
A conflicting instance still requires the caller's observed replacement pointer.

## Leased consumer handles

Opening a prepared state or generation creates a renewable cross-process lease.
`PreparedExport` owns the generation lease and renews it while the handle is in
use.

`PreparedExport.asset(relative)` detaches an independent lease for one declared
regular file. The response asset has a lifetime independent of the view-level
handle. A server closes it after sending the response. `PreparedExport.close()`
is idempotent and releases an owned repository after its artifact lease closes.

`PreparedExport` retains the verified file closure. It rechecks `index.json`
before path access, opening, or renewal. `PreparedAsset` checks the declared path,
size, and digest when borrowed and again when its bytes are read.

Retention treats active leases as pinned. A heartbeat failure makes the handle
unavailable so callers cannot use an unprotected artifact.

## Retention and admission

`RepositoryLimits` bounds observations, producers, identities, prepared states,
generations, metadata bytes, artifact bytes, total repository bytes, lease
lifetimes, and heartbeat cadence.

Admission applies retention before a new artifact commits. Retention chooses
victims from least-recently-used unleased instances while preserving current
generations and their member states. The filesystem tree is first moved to a
repository-owned quarantine name. The catalog then removes matching rows and
accounts any tree awaiting deletion.

`repository.prune(dry_run=True)` reports eligible prepared states, generations,
and bytes. A live prune returns the rows actually retired after rechecking the
candidate snapshot.

## Recovery and failure classification

Repository opening validates the SQLite schema and recovers active catalog
artifacts. Recovery:

- removes abandoned unleased staging directories
- restores a verified installation backup when atomic replacement was interrupted
- verifies prepared-state identity, metadata, closure, and derived key
- verifies notebook export identity and closure
- retires catalog rows only for confirmed integrity failures
- preserves healthy rows when another row is invalid
- keeps retired-artifact cleanup accounted until the retired path is gone

Operational filesystem errors such as permission and resource failures surface
as repository availability errors. They preserve the current catalog pointer.
A confirmed SQLite corruption or incompatible internal schema is quarantined
before a fresh catalog opens.

## Public and private boundaries

Applications use:

```python
from marimo_export import ExportRepository, ExportSpec, plan, prepare

spec = ExportSpec.from_file("report.export.yaml")

with ExportRepository.open() as repository:
    work = plan("report.py", spec=spec, repository=repository)
    prepared = repository.prepared(work)
    if prepared is None:
        prepared = prepare("report.py", spec=spec, repository=repository)
    with prepared:
        print(prepared.identity)
```

`repository.prepared(plan)` returns a leased `PreparedExport` when the current
verified generation matches the complete plan. It returns `None` for a miss.
The caller closes the returned handle before closing the repository.

The public observation methods also accept the resolved plan:

- `record_observation(plan, inputs)` records one complete vector and returns its
  `ObservedState`.
- `observation_revision(plan)` returns the producer's current monotonic revision.
- `observations(plan)` returns vectors projected to the producer and inferred
  input names.
- `clear_observations(plan)` clears that producer's observation history and
  returns the deleted row count.

The plan-shaped facade constructs raw repository keys internally.

Preparation services use `_repository/preparation.py`. The observation ledger
uses `_repository/observations.py`. Tests may construct private repository
fixtures to exercise concurrency and recovery. Application code and
marimo-studio import neither `_repository` nor `sqlite3`.
