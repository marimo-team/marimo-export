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
  exports/<repository-identity>/<generation-instance>/
  staging/<operation>/
```

The repository is local producer infrastructure. A deployed notebook export
contains `index.json` and its declared assets.

## Repository and computation cache are separate

| Store               | Owner         | Retained value                                                         |
| ------------------- | ------------- | ---------------------------------------------------------------------- |
| Marimo native cache | marimo        | Restorable notebook cell definitions and returns                       |
| Export repository   | marimo-export | Prepared-state and export-generation artifacts plus lifecycle metadata |
| Notebook export     | marimo-export | Canonical index and content-addressed consumer assets                  |

Marimo computes cache keys, selects the configured cache store, validates cache
entries, serializes values, verifies signatures, and restores cell state.
marimo-export observes native hit and miss decisions and reads verified native
returns through the compatibility adapter.

The export repository stores a prepared-state artifact after its results have crossed the portable
output boundary. Repository reuse can therefore avoid notebook startup. If a
prepared state is absent, Marimo's native cache may still avoid notebook cell
computation while that state is rebuilt.

## Repository identities define reuse

A prepared-state scope is addressed by:

```text
producer_sha256 + output_plan_sha256 + state_fingerprint
```

An exact `ExportPlan` has this repository identity:

```text
producer_sha256 + output_plan_sha256 + spec_sha256
```

`producer_sha256` covers the stable notebook source, canonical notebook
document, Python and operating-system runtime, installed distributions, local
runtime sources, Marimo version, marimo-export version, and marimo-export
implementation identity.

`output_plan_sha256` is the output-plan identity. It covers the authored output
declarations. Presentation HTML, CSS, JavaScript, route names, and view
host IDs remain outside that identity.
Two applications can therefore reuse the same prepared states when they request
the same producer, outputs, and states.

`spec_sha256` covers the complete canonical ExportSpec, including state aliases
and the default alias. Changing presentation assets preserves repository reuse.
Adding one state retains matching prepared states and prepares the missing
fingerprint.

The repository identity finds a mutable current-generation pointer. The selected
generation instance is the notebook export identity, which is SHA-256 over the
generation's canonical `index.json`. A prepared-state instance is SHA-256 over
its private prepared-state manifest. Read
[Identities and protocols](identities-and-protocols.md) before changing a digest
scope or stored key.

## SQLite owns metadata and coordination

`_repository/sqlite` is the sole SQL owner. `catalog.sqlite3` tracks:

- producers and monotonic observation revisions
- canonical observed vectors and bounded observation history
- prepared-state scopes and immutable instances
- repository identities and immutable generations
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

The repository uses Python's built-in `sqlite3`. Applications reach it through
public marimo-export operations and carry no database schema or SQL.

## Observations are authoring evidence

`ObservationLedger` receives portable input vectors that are complete for the
eligible user-interface root relation observed in one successful normal notebook
run. The marimo adapter records a candidate after a run that
finished with no exception, interruption, cancellation, or scratch-cell work.

Before persistence, the worker verifies that the live cell signature matches
one stable saved notebook revision. The resulting producer identity binds the
observation to the source that produced it.

The ledger has a bounded coalescing queue. Repeated adjacent vectors accumulate
an occurrence count. Every accepted occurrence advances the producer's monotonic
revision. Queue eviction advances that revision without retaining a vector.
SQLite keeps one current row per fingerprint plus a bounded event history used
to resolve the latest vector for each input-name relation at a snapshot revision.
`flush()` waits for queued writes and surfaces a persistence failure. `close()`
drains the worker, closes an owned repository, and reports the first failure.

Application code records a vector through
`ExportRepository.record_observation(plan, inputs)`. The facade validates that
`inputs` contains exactly the inferred names in `plan`, then delegates the
producer-keyed write to `ObservationRepository`.

An ExportSpec is the reviewable state contract. Observations remain local
history until an application selects vectors and places them in an explicit
spec.

`clear_observations(plan)` deletes the producer's retained current rows and
event history. It returns the number of current rows deleted. The monotonic
producer revision remains unchanged, so clearing history does not create a new
observation revision.

## Immutable artifact lifecycle

Preparation writes into a private staging directory. State commit writes and
verifies a `marimo-export.prepared-state.v1` manifest that binds the producer,
output plan, state fingerprint, metadata, file closure, byte count, and instance
digest.

Generation commit verifies the complete notebook export, its exact state
membership, and its spec identity. Each instance's artifact bytes and canonical
identity metadata are immutable. Its access time and captured observation
revision can advance, and a scope row contains the mutable pointer to the
current instance.

Prepared-state commit uses five ordered phases:

1. Write `prepared-state.json` and enforce the per-state byte limit.
2. Apply repository retention before checking the candidate commit.
3. Use a short catalog transaction to validate the reservation, fence, and pointer.
4. Install and completely verify the filesystem tree outside the catalog writer transaction.
5. Use a short transaction to recheck the fence and pointer, enforce repository byte admission, record the instance, and acquire its lease.

The preparation service runs its source and cancellation guard before invoking
that state commit. Export-generation commit has its own ordered sequence:

1. Verify the staged `index.json`, asset closure, exact prepared-state membership, and metadata limits.
2. Apply repository retention before checking the candidate commit.
3. Use a short catalog transaction to validate the reservation, fence, and pointer.
4. Run the source and cancellation guard.
5. Install and completely verify the filesystem tree outside the catalog writer transaction.
6. Use a short transaction to recheck the fence and pointer, enforce repository byte admission, record the generation and its state membership, and acquire its lease.

Repository installation uses `os.replace`. When a target already exists, it
moves that target to a backup, installs the staging tree, verifies it, and
restores the backup if installation fails. A lost final compare-and-swap retires
the uncommitted installation for accounted cleanup. The prior current instance
remains selected.

Committed files become owner-read-only. Directories become owner-readable,
owner-writable, and owner-executable on POSIX. Cleanup first restores owner
write permission. Windows uses
the same logical verification and lifecycle with best-effort permission changes.

## Reservations and fencing

One preparation reservation owns one repository identity. The first claimant
receives a monotonically increasing fencing token. The lease manager extends the
reservation expiry while retaining its owner and token. It also renews staging
paths and live artifact leases.

Acquisition polls for a bounded interval and raises a repository failure with
code `repository_reservation_timeout` when another healthy owner retains the
identity. The active reservation exposes liveness to preparation cancellation.

Every state and generation commit proves:

1. the reservation identity matches the repository identity
2. the reservation owner still holds the row
3. the fencing token is current
4. the reservation has not expired
5. an initial pointer is still empty, the named replacement is still current,
   or another writer has already selected the same content-addressed instance

A stale worker receives `RepositoryFenceError` and cannot replace a newer
generation.

Prepared states are shared across repository identities. Concurrent preparations
that produce the same content-addressed state instance converge on that instance.
A conflicting instance still requires the caller's observed replacement pointer.

## Leased consumer handles

Opening a prepared state or generation creates a renewable cross-process lease.
`PreparedExport` owns the generation lease and renews it while the handle is in
use.

`PreparedExport.asset(relative)` detaches an independently owned generation
lease for access to one declared regular file. The response asset has a lifetime
independent of the view-level handle, but it pins the complete export generation.
A server closes it after sending the response. `PreparedExport.close()` is
idempotent and releases an owned repository after its artifact lease closes.

`PreparedExport` retains the verified file closure. It rechecks `index.json`
before path access, opening, or renewal. `PreparedAsset` checks the declared path,
size, and digest when borrowed and again when its bytes are read.
Detaching a `PreparedAsset` keeps the lease manager alive after the parent
`PreparedExport` or `ExportRepository` closes. The response owner can therefore
finish a verified byte read before closing its detached handle.

Retention treats active leases as pinned. A heartbeat failure makes the handle
unavailable so callers cannot use an unprotected artifact.

## Retention and admission

`RepositoryLimits` bounds observations, producers, identities, prepared states,
generations, metadata bytes, artifact bytes, total repository bytes, lease
lifetimes, and heartbeat cadence.

`observation_relation_bytes` is currently enforced per producer and separately
for the current-observation and observation-event tables. Each limit spans every
input-name relation retained for that producer.

The limits belong to one opened repository handle and are not persisted in the
SQLite catalog. Another process or later handle can apply another policy to the
same repository. `prepared_state_bytes` and `generation_bytes` each enforce both
a per-artifact maximum and an aggregate retained-content budget.

Admission applies retention before a new artifact commits. The retention pass
uses current repository contents and does not reserve room for the incoming
artifact. The final transaction applies the candidate's metadata and content
bytes as a hard cumulative check. A commit can therefore raise
`repository_limit_exceeded` even when older unleased artifacts exist that a
candidate-aware retention pass could evict.

Retention chooses victims from least-recently-used unleased instances. It
preserves active leases, the current generation for each identity admitted by
`retained_identities`, and the prepared states required by retained generations.
An older unleased identity can lose its current generation. The filesystem tree
is first moved to a repository-owned quarantine name. The catalog then removes
matching rows and accounts any tree awaiting deletion.

`repository.prune(dry_run=True)` reports eligible prepared states, generations,
and bytes. A live prune returns counts and released bytes after rechecking the
candidate snapshot. It can also remove producer rows outside retention and
cascade into their observations. Dry-run and `PruneResult` do not report that
observation deletion.

`repository_bytes` is a steady-state admission budget. Replacement can install
new bytes while leases still pin an old generation, temporarily placing the
repository above that value until retention can retire the predecessor.

## Recovery and failure classification

Repository opening validates the SQLite schema and attempts one maintenance
recovery. Another process may already hold maintenance ownership. In that case,
opening continues and later reads verify the selected artifact before returning
it.

An incompatible schema or confirmed catalog corruption causes a complete catalog
reset. Opening renames the catalog files, creates a fresh catalog, then retires
the renamed database and every prepared-state, export-generation, and staging
tree that no longer has a catalog row. The quarantine name is a transactional
step before accounted deletion, not a backup or recovery archive.

Normal maintenance recovery:

- removes abandoned unleased staging directories
- restores a verified catalog-known installation backup when directory replacement was interrupted
- quarantines and removes exact digest-shaped artifact roots whose catalog commit was interrupted
- verifies prepared-state identity, metadata, closure, and derived key
- verifies notebook export identity and closure
- quarantines invalid artifact trees and removes catalog rows only for confirmed integrity failures
- preserves healthy rows when another row is invalid
- keeps retired-artifact cleanup accounted until the retired path is gone

Public error translation depends on the failing boundary. Artifact-tree I/O can
become `ExportUnavailableError`. Member verification and lease heartbeat
failures can surface as `RepositoryError` or `RuntimeError`. Confirmed integrity
failures retire the affected artifact while temporary availability failures
preserve the current pointer. Confirmed SQLite corruption and incompatible
internal schemas follow the complete catalog-reset scope described in this
section.

## Status and maintenance results

`repository.status()` returns a `RepositoryStatus` with the absolute repository
path and counts for producers, retained observation fingerprints, prepared-state
instances, repository identities, export generation instances, and active
artifact leases. It removes expired artifact leases before counting them.

`active_leases` counts durable owner-artifact lease rows. Several Python handles
can share one durable row, so the value is not a live Python-object count.

`content_bytes` is accounted artifact content. It includes prepared states,
export generations, and retired artifacts awaiting deletion. It does not report
SQLite file size or active staging-directory bytes.

`repository.prune(dry_run=True)` returns the prepared-state count, generation
count, and content bytes eligible under the current retention policy. A live
prune rechecks the snapshot, retires matching files, commits matching catalog
changes, and returns the rows and bytes actually retired.

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
- `observations(plan)` returns vectors stored for the exact ordered input-name
  relation in the plan. Planning snapshots can project retained superset
  relations to the inferred input names.
- `clear_observations(plan)` clears that producer's observation history and
  returns the deleted row count.

The plan-shaped facade constructs raw repository keys internally.

Preparation services use `_repository/preparation.py`. The observation ledger
uses `_repository/observations.py`. Tests may construct private repository
fixtures to exercise concurrency and recovery. Application code imports neither
`_repository` nor `sqlite3`.
