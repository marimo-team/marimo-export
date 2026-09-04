---
title: Repository and observations
description: Configure reusable export storage, inspect retention, and record successful notebook input vectors.
---

# Repository and observations

The export repository keeps reusable prepared states, immutable prepared-export
generations, and observed input vectors. A notebook export written to a
deployment directory is a separate portable artifact.

```python
from marimo_export import ExportRepository

with ExportRepository.open(".exports") as repository:
    status = repository.status()
    preview = repository.prune(dry_run=True)

print(status.content_bytes)
print(preview.bytes_released)
```

Use one repository across related plans and preparations when those operations
should share observations and prepared work.

## `ExportRepository.open()`

```python
ExportRepository.open(
    path: str | os.PathLike[str] | None = None,
    *,
    limits: RepositoryLimits | None = None,
) -> ExportRepository
```

When `path` is absent, `MARIMO_EXPORT_REPOSITORY` takes precedence over the
platform cache directory:

| Platform        | Default root                                                                      |
| --------------- | --------------------------------------------------------------------------------- |
| macOS           | `~/Library/Caches/marimo-export/repository`                                       |
| Windows         | `%LOCALAPPDATA%/marimo-export/repository`                                         |
| Other platforms | `$XDG_CACHE_HOME/marimo-export/repository` or `~/.cache/marimo-export/repository` |

Opening creates the directory when needed, rejects a symbolic-link root, and
sets owner-only permissions on POSIX systems. It attempts maintenance recovery
for the private SQLite catalog and invalid repository artifacts. When another
process holds the maintenance transaction lock, opening continues without that pass.
Recovery can quarantine a corrupt catalog and open a fresh one, which also
resets catalog-backed observation history. Prepared states, export generations,
and staging directories from the replaced catalog become unindexed and are
retired by maintenance. Catalog quarantine is a transactional rename before
accounted cleanup, not a durable backup or forensic archive. Recovery never
treats the repository as a notebook export directory.

`limits` defaults to `RepositoryLimits()`. The policy belongs to the opened
handle and is not persisted with the repository path. A later handle can apply
different limits. CLI commands open with the default policy.

`default_path()` returns the selected default path without creating it:

```python
ExportRepository.default_path() -> Path
```

## Repository ownership

`ExportRepository` is a context manager. `close()` is idempotent. Operations on
a closed repository raise `RuntimeError`.

High-level producer calls follow one ownership rule:

| Call                                | Repository ownership                          |
| ----------------------------------- | --------------------------------------------- |
| `plan(..., repository=None)`        | Opens and closes a repository during the call |
| `prepare(..., repository=None)`     | Returned `PreparedExport` owns it until close |
| `capture(..., repository=None)`     | Returned `PreparedExport` owns it until close |
| Any call with a supplied repository | Caller keeps ownership                        |

A `PreparedExport` returned by `repository.prepared(plan)` has its own artifact
lease. Close that handle before closing or pruning related resources.

## Repository operations

```python
repository.record_observation(
    plan: ExportPlan,
    inputs: Mapping[str, object],
) -> ObservedState
repository.observation_revision(plan: ExportPlan) -> int
repository.observations(plan: ExportPlan) -> tuple[ObservedState, ...]
repository.clear_observations(plan: ExportPlan) -> int
repository.prepared(plan: ExportPlan) -> PreparedExport | None
repository.status() -> RepositoryStatus
repository.prune(*, dry_run: bool = False) -> PruneResult
repository.close() -> None
```

`record_observation()` requires exactly the plan's complete input-name set. It
canonicalizes the values, advances the producer observation revision, and
returns the stored `ObservedState`.

`observations()` returns observations stored for the plan's exact ordered input
relation. Planning performs the separate projection that can select a subset of
values from broader observations. `clear_observations()` removes the producer's
observation history and returns the number removed.

`prepared()` returns an exact verified prepared export when the repository has
one matching producer, output plan, and exact spec identity. It returns `None`
when no exact export generation matches.

`status()` reports current counts and bytes. `prune()` applies the configured
retention policy and removes candidates when `dry_run=False`. A dry run reports
prepared states, generations, and bytes. A live prune can also remove producer
records and their observation history, which `PruneResult` does not count.
Active staging, state, and generation leases protect their repository artifacts
from pruning. A detached `PreparedAsset` owns an independent handle to its
generation lease, so one open asset protects the complete export generation.

## `RepositoryLimits`

`RepositoryLimits` is an immutable storage and lifecycle policy:

| Field                               | Default | Contract                                                                              |
| ----------------------------------- | ------: | ------------------------------------------------------------------------------------- |
| `observation_bytes`                 |   1 MiB | Maximum canonical bytes in one observation                                            |
| `observations_per_producer`         |     256 | Retained observations per producer                                                    |
| `observation_relation_bytes`        |  16 MiB | Retained bytes per observation table for one producer across its input-name relations |
| `retained_producers`                |      32 | Producer histories retained by observation cleanup                                    |
| `retained_identities`               |     128 | Exact prepared-export identities retained                                             |
| `retained_generations_per_identity` |       4 | Generations retained for one identity                                                 |
| `retained_generations`              |     128 | Generations retained across the repository                                            |
| `retained_prepared_states`          |    4096 | Prepared states retained across producers                                             |
| `metadata_bytes`                    |  16 MiB | Repository metadata budget                                                            |
| `prepared_state_bytes`              | 512 MiB | Per-state maximum and aggregate prepared-state budget                                 |
| `generation_bytes`                  |   1 GiB | Per-generation maximum and aggregate generation budget                                |
| `repository_bytes`                  |   2 GiB | Total repository content budget                                                       |
| `lease_ttl_seconds`                 |  `30.0` | Lease expiry after heartbeat loss                                                     |
| `lease_heartbeat_seconds`           |   `5.0` | Active lease renewal interval                                                         |

Integer limits must be positive and fit SQLite's signed integer range. Lease
durations must be positive finite numbers. The heartbeat interval must be
shorter than the time to live.

`repository_bytes` is a steady-state admission budget. Replacing a leased
generation can temporarily retain old and new bytes above that value.
Admission applies retention to current repository contents before it adds the
candidate size. The candidate size does not select additional least-recently-used
victims. A write can therefore raise `repository_limit_exceeded` while an
older unleased artifact remains. To make room, prune through a handle with a
tighter retention policy or raise the admission budget. Inspect
`repository.prune(dry_run=True)` before changing either policy.

`observation_relation_bytes` is applied separately to canonical observation
rows and observation event rows. Despite the field name, each table's budget is
producer-wide and can combine several recorded input-name relations.

## Repository result records

### `ObservedState`

One `ObservedState` contains:

```python
producer_sha256: str
revision: int
fingerprint: str
values: Mapping[str, JsonValue]
input_names: tuple[str, ...]
canonical_values: bytes
byte_count: int
```

`values` is a read-only top-level mapping decoded from canonical bytes. Nested
lists and dictionaries are detached mutable values. `fingerprint` is computed
from the complete canonical values. `to_dict()` returns producer identity,
revision, fingerprint, and another detached values object.

An `ObservedState` returned by `repository.observations(plan)` has the plan's
exact ordered input relation. Planning can also project a compatible broader
observation to the plan's input names. A record created by that revision-consistent
planning snapshot carries the snapshot revision. Latest-observation selection
retains the selected event revision.

### `RepositoryStatus`

```python
path: Path
producers: int
observations: int
prepared_states: int
identities: int
generations: int
content_bytes: int
active_leases: int
```

`to_dict()` returns the same fields and serializes `path` as a string.
`active_leases` counts durable owner-artifact lease rows in the catalog. Several
Python handles can share one durable owner and artifact row, so this field is not
a live Python-object count.

### `PruneResult`

```python
prepared_states: int
generations: int
bytes_released: int
dry_run: bool
```

`to_dict()` returns the same fields.

## Observation model

An observation is one successful input vector that is complete for its recorded
input-name relation and retained as authoring evidence. A host hook records
eligible user-interface roots, which can form a broader relation than a later
plan. Planning projects compatible superset relations to its inferred input
names. Observations enter a notebook export only when an author places the
desired values in an explicit `ExportSpec` state row.

Use `record_observation()` when an application already has a complete plan and
input vector. Use `ObservationLedger` when a host records successful notebook
runs asynchronously.

## `ObservedInputs`

```python
from marimo_export.observations import ObservedInputs

observed = ObservedInputs({"interval": "1wk", "region": "EU"})
```

```python
ObservedInputs(values: Mapping[str, object])
```

Input names must be valid non-keyword Python identifiers. Values must be
portable JSON. The record copies and canonicalizes the mapping, then exposes:

```python
observed.fingerprint: str
observed.values: FrozenJsonObject
observed.canonical_values: bytes
observed.byte_count: int
```

`ObservedInputs.values` is recursively immutable. Nested arrays become tuples
and nested objects become immutable mappings. This differs from the persisted
`ObservedState.values` access described earlier, whose top-level mapping is
read-only and whose newly decoded nested containers are mutable.

## `ObservationLedger`

```python
from marimo_export.observations import ObservationLedger, ObservedInputs

with ObservationLedger("report.py") as ledger:
    ledger.record(ObservedInputs({"interval": "1wk"}))
    ledger.flush()
```

```python
ObservationLedger(
    source: str | os.PathLike[str],
    *,
    repository: ExportRepository | None = None,
)
ledger.record(
    observed: ObservedInputs,
    *,
    producer_sha256: str | None = None,
) -> None
ledger.flush() -> None
ledger.close() -> None
```

Construction validates `source` and starts one daemon persistence worker. The
default repository opens lazily after the first record. A supplied repository
stays caller-owned.

`record()` validates the current source identity when `producer_sha256` is
absent, queues the observation, and returns before persistence completes.
Repeated pending vectors coalesce without losing their occurrence count. The
direct queue retains at most 256 observations, 16 MiB, and 32 producers. Each
observation is limited to 1 MiB. It can evict older pending vectors while still
advancing their observation revisions. Deferred host observations reject new
work when the corresponding bounds are full.

`flush()` waits for queued and deferred writes to settle. After a successful
close it returns immediately. `close()` is idempotent and joins the worker
without a separate timeout. Busy repository writes make up to three attempts
with 10 and 20 millisecond waits between attempts. Both methods replay a
terminal worker failure as `ObservationPersistenceError`. Recording after close
replays a prior failure or raises `RuntimeError`.

A repository-limit rejection advances the producer revision without retaining
the oversized vector. Queue ingestion that exceeds its own bound raises
`ObservationRejectedError` and leaves the worker available for later records.

## Attach a ledger to a running kernel

```python
from marimo_export.observations import (
    ObservationLedger,
    install_observation_ledger,
)

ledger = ObservationLedger("report.py")
release = install_observation_ledger(context, ledger)
try:
    ...
finally:
    release()
    ledger.close()
```

`install_observation_ledger(context, ledger)` attaches a final kernel hook and
returns an idempotent release callback. It records runs that complete without an
interrupt, exception, cancelled cell, or scratch cell. It also requires the
live kernel to remain bound to the ledger's saved notebook source.

This is an advanced host integration. Applications that do not own a marimo
kernel context should record through `ExportRepository.record_observation()` or
use an integration that owns the hook lifecycle. See [Host
integration](host-integration).

## Repository errors

Import public repository failures from `marimo_export.repository`:

| Error                        | Default code                | Meaning                                     |
| ---------------------------- | --------------------------- | ------------------------------------------- |
| `RepositoryError`            | `repository_error`          | Base repository failure                     |
| `RepositoryLimitError`       | `repository_limit_exceeded` | Configured storage or record limit exceeded |
| `RepositoryUnavailableError` | `repository_unavailable`    | Storage unavailable                         |
| `RepositoryBusyError`        | `repository_busy`           | Lock contention exceeded its bounded wait   |

Repository errors inherit `MarimoExportError`, so each exposes `code`,
`details`, and `wire()`. A confirmed integrity failure retires the affected
artifact. A temporary availability failure preserves the current prepared
export.

Use [Produce an export](produce) to pass the repository into planning and
preparation. Use [Delivery and publications](delivery-and-publications) to
retain prepared generations for an application.
