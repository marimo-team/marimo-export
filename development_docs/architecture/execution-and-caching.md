# Execution and caching

marimo-export executes missing normalized states through marimo, commits their
portable prepared-state artifacts, and reuses marimo's native lazy cache. The
package adds export policy around that cache through a contained compatibility
adapter.

## Ownership boundary

| Behavior                                      | Owner                 |
| --------------------------------------------- | --------------------- |
| Reactive graph and topological execution      | marimo                |
| Cell hash and cache key                       | marimo                |
| Producer dependency input to native hashing   | marimo-export         |
| Cache store selection and persistence         | marimo                |
| Lazy value serialization and restoration      | marimo                |
| Cache signing and verification                | marimo                |
| State relation and missing-state selection    | marimo-export         |
| Transient output leaves                       | marimo-export         |
| Native cache activity reporting               | marimo-export adapter |
| Verified native return to output descriptor   | marimo-export adapter |
| Portable prepared-state and export repository | marimo-export         |

## Upstream caching foundation

marimo's [caching API](https://docs.marimo.io/api/caching/) defines the
author-facing `mo.cache`, `mo.lru_cache`, and `mo.persistent_cache` APIs plus the
notebook-wide automatic cell cache. marimo-export integrates with the automatic
cell-cache path. Managed parents set `cache_cells` in the script metadata of
their sibling notebook copy. State-child graphs set it in their runtime
configuration. Borrowed parent sessions retain their application-owned policy.

The SciPy 2026 article [Content-Addressed Caching for Reactive
Notebooks](https://dmadisetti.github.io/scipy_proceedings_2026/) supplies the
design rationale for graph-derived keys, typed cache blobs, and cached
WebAssembly delivery:

- Cache keys combine compiled cell behavior with content-addressed references.
- A reference that cannot be addressed directly contributes its producing
  parent cell's hash, forming a Merkle dependency graph.
- Per-definition manifests and typed blobs separate cache identity from stored
  values.
- Cached WebAssembly exports carry native manifests and blobs so browser Python
  can derive the same keys and restore values.

The loader for the supported marimo release verifies the manifest and loads
every resolvable referenced definition and return value before skipping the cell
body. Values whose required module is unavailable can remain as stubs and cause
live recomputation when a consumer needs them.

marimo-export preserves those upstream decisions. It adds graph-scoped execution
policy needed to prepare an explicit state-output relation, flushes pending
native writes, verifies the selected return receipt, and translates supported
returns into package-owned output descriptors. Marimo remains authoritative for
the cache key, store, manifest, blob, codec, signature, and restoration behavior.

```mermaid
flowchart LR
    scheduler["marimo reactive scheduler"] --> lookup["Native cache key and lookup"]
    lookup --> execution["Execute miss or hydrate hit"]
    execution --> native["Native manifest and value blobs"]
    native --> verification["marimo-export receipt verification"]
    verification --> output["OutputDescriptor and prepared-state artifact"]
```

The export repository stores prepared-state artifacts after Marimo has executed
or restored the notebook cells that produce them. Marimo's native store remains
the computation cache.

## Exact supported adapter

The Python package pins its supported marimo release in
`packages/python/pyproject.toml`. `_marimo/compat/release.json` records:

- the pinned Marimo version and release commit
- source SHA-256 digests for the private cache functions adapted by the package

`_marimo/compat/cache/probe.py` checks the installed distribution version,
required cryptography support, private function sources, lazy-loader registry,
restored UI check, Polars stub loaders, tensor encoder, and active runtime store
shape before adapter construction. A mismatch raises `CompatibilityError` with
code `marimo_incompatible`.

This architecture targets the published release recorded by the compatibility
metadata as-is. Marimo integration changes remain inside marimo-export until a
matching public Marimo capability is available.

Applications can inspect the boundary before loading a notebook:

```python
from marimo_export.diagnostics import marimo_compatibility

check = marimo_compatibility()
print(check.status, check.details)
```

## Stable cache port

`_marimo/capabilities.py` exposes package-owned records and protocols.
`CachedStateExecutor.execute_state()` accepts a normalized state, execution
plan, prepared exporters, and producer identities. It returns `StateExecution`
with:

- native receipts for every output
- scoped control bindings
- authored and projection cache activity
- state-run timings

`NativeCacheReturn` represents a verified scalar, NumPy array, Arrow table, or
BlobAsset return. It carries no Marimo loader, store, schema, graph, stub, or
cache-key object.

The execution adapter translates these records into export descriptors before
repository, CLI, and browser consumers see them. Private Marimo objects remain
under `_marimo/compat`.

## Private cache adapter

`_marimo/compat/cache` separates each private responsibility:

| Module         | Responsibility                                                  |
| -------------- | --------------------------------------------------------------- |
| `probe.py`     | Validate the exact supported private contract                   |
| `patch.py`     | Own one reversible lease over process-global Marimo cache hooks |
| `loader.py`    | Run native lazy deserialization on the kernel thread            |
| `lifecycle.py` | Recreate unavailable values and session-bound Marimo state      |
| `attempts.py`  | Scope live-only cells and cache activity to one exact graph     |
| `barrier.py`   | Flush pending native cache writes at execution boundaries       |
| `receipts.py`  | Verify and decode one persisted native return                   |
| `host.py`      | Keep UI, Polars, and tensor cache restore compatible in hosts   |

`_marimo/composition.py` runs the cache probe before constructing
`PrivateKernelRuntime`. Code outside `_marimo/compat` and named composition
roots imports neither cache modules nor private Marimo modules.

## Reversible process-global patch

The supported Marimo release exposes the required cache seams through process
globals. The adapter temporarily owns:

- `PERSISTENT_LOADERS["lazy"]`
- `CachedLifecycle`
- `cache_attempt_from_hash`

`_CachePatchCoordinator` snapshots the native values on the first lease.
Equivalent overlapping managed leases share one installation. The last close
restores each global still owned by the adapter. A foreign replacement during
the lease raises `marimo_cache_patch_conflict` after releasing adapter
bookkeeping and restoring the globals it still owns.

Borrowed child runs serialize through one async-aware lock while their patch
lease is active. Managed kernels retain the same patch for their lifespan.
One coordinator owns every global mutation.

## Graph-scoped policy

The installed attempt wrapper checks the exact graph identity before applying
export behavior. An unmatched graph receives the native Marimo cache attempt
unchanged.

For an owned export graph, the adapter can:

- add one frozen producer-dependency input to native cell hashing
- retry a failed native hash with a digest of the type and Arrow IPC stream of
  each referenced PyArrow value, because Marimo's NumPy view rejects
  object-typed columns such as strings. Cells whose native hash succeeds keep
  their native keys.
- record the effective hit or miss for authored and projection cells
- rerun the cells that the execution plan names live
- turn a hit containing an unavailable UI or unhashable stub into a live run
- recreate cells that define `mo.state` so getters and setters belong to the
  current session
- retain the verified attempt required for output receipt extraction

Graph scopes are registered and removed through context managers. Forced cells
exist only inside one active scope.

### The execution plan names live cells

`ExecutionPlan` derives two live sets from its planned outputs:

| Set                          | Members                                                                                              | Reason                                                                                                           |
| ---------------------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `ExecutionPlan.live_cells`   | Authored owner cell ID of each complete-cell output                                                  | Console records are uncached side effects                                                                        |
| `ExecutionPlan.live_outputs` | Name of each output whose `ExporterDefinition` is live: `anywidget.bundle` and every custom exporter | Custom callables can read undeclared state, and the AnyWidget bundle captures the current session's widget model |

`compat/execution.py` maps owner cell IDs and output leaves to runtime cell
IDs and passes the forced cells of each phase to `force_cache_misses`. A forced
cell skips native restoration, reruns, and saves its result under the same key,
so output receipts read the fresh entry. The live rule stays in
`exporters/_definitions.py`. Leaf source is the same for live and cached
outputs, so the live sets change no native cache key. UI and `mo.state` cells
rerun through the lifecycle adapter described above, and every other output
leaf follows Marimo's native hit or miss.

### Dependency identity enters native lookup

`_environment.py` fingerprints the distribution closure and local module
sources reachable from the notebook's imports, as described in
[Producer environment sources](identities-and-protocols.md#producer-environment-sources).
The authored notebook stays out of that digest. Marimo hashes notebook cells and
their reactive references individually, so a notebook edit that keeps the import
set preserves reusable computations.

The managed parent freezes this dependency identity before its initial autorun.
State children reuse that identity. A borrowed child freezes the identity from
its runtime filename. The attempt adapter supplies the digest as a string
reference in a temporary Python [abstract syntax tree](https://docs.python.org/3/library/ast.html)
and lookup scope. Marimo derives the key and
persists the result through its native loader. The executed cell and notebook
globals retain their authored form.

This input covers local modules whose `__version__` stays constant or is absent,
including imports inside a cell body. Marimo's own cell hash records an imported
module by name, and by `__version__` only for `pin_modules`. Changing an
imported helper source or a distribution in the closure invalidates automatic
cell-cache entries before they can populate a new prepared export. Adding an
import that resolves to a distribution or local module outside the current scope
changes the digest too. State vectors, output declarations, and notebook source remain outside
the dependency digest, preserving native reuse across state combinations and
output plans. Native entries created outside this scoped integration have a
different key because they carry different dependency provenance.

Explicit `mo.cache` and `mo.persistent_cache` calls retain their authored key and
reference boundaries. Pass changing source or data revisions as referenced values
or cached function arguments when they determine freshness.

## Cache activity counts executed child work

`CacheActivity` records effective marimo cache decisions for cells that entered
a missing-state child run:

- `authored_hits` and `authored_misses` count non-projection cell attempts.
- `projection_hits` and `projection_misses` count the requested output receipts
  returned by the capture bridge.
- A forced live run counts as an effective miss even when marimo initially found
  a stored entry.

The private `CacheAttemptLog` tracks output and snapshot-token cells together.
The bridge reports projection counts from output receipts, so its public counts
exclude snapshot-token attempts. Keep that distinction when interpreting internal
execution diagnostics.

Exact prepared-export reuse and prepared-state reuse execute no child work and
therefore add no cache activity. Zero counts can mean repository reuse. They do
not prove that the notebook contains no cacheable cells.

## Sequential lazy loader

`SequentialLazyLoader` subclasses Marimo's native `LazyLoader`. It moves blob
deserialization onto the kernel thread for runtimes that cannot enter safely
from cache workers.

The adapter keeps Marimo's store, manifest, blob hash, signing, missing-data,
and unreadable-data behavior. Signature failures remain authoritative. Missing
or unreadable blobs follow native incomplete-cache behavior.

## Write barriers and receipt extraction

Marimo may serialize lazy-cache values in background workers. The adapter adds
one early post-execution hook to managed kernels and calls
`flush_active_caches()` at receipt boundaries. A dependent state therefore sees
the completed native write.

Receipt extraction:

1. flushes native writes
2. snapshots reads from the active loader store
3. decodes the native manifest through Marimo's schema
4. checks the expected native hash
5. resolves Marimo's effective verification mode and signer
6. verifies the selected return blob
7. compares an inline scalar with the live value and Python type
8. returns a package-owned `NativeCacheReturn`

The registered live loader remains unchanged during extraction. Descriptor
translation occurs after the cache adapter returns its package record.

## Interactive host compatibility

`marimo_export.integration.keep_cached_cells_compatible()` installs the cache
repairs required by an interactive host process. The host retains the returned
release callback for its kernel lifecycle. Private host repair imports and
mutations remain in `marimo_export._marimo.compat.cache.host`.

The host lease owns two pinned Marimo seams:

- restored UI-definition detection for cached composite values
- contiguous tensor bytes for Polars values

`cache/host.py` reference-counts equivalent leases and coordinates mutation with
the main cache patch owner. Polars values use Marimo's native Arrow IPC loader.
The last close restores the native UI check and tensor encoder values still
owned by marimo-export. Foreign mutation raises `marimo_cache_patch_conflict`.

## Child execution

Each complete state runs in an in-memory Marimo child graph containing:

- one copied set of authored notebook cells, with selected ordinary assignments
  rewritten in their owning cells
- one complete state fingerprint cell
- registry update commands for selected UI inputs
- transient snapshot-token cells when an output needs one
- one deterministic leaf per requested output

The child runs the dependency closure in topological phases. UI-defining cells
receive accepted upstream inputs before dependent controls are constructed. A
final phase runs the remaining available authored cells, preserving notebook
failure semantics beyond the projected cells.

Projection leaves carry notebook, producer, implementation, output-plan, and
snapshot identity in their deterministic source. A changed identity changes the
Marimo cell hash and native cache key through Marimo's own hashing path.

State children enable native cell caching locally so every output can produce a
verified receipt. Marimo decides authored-cell validity from cell code,
content-addressed references, dependency hashes, registered side effects, and
the configured native store. Complete-cell owners run live because their
contract includes console messages, which Marimo documents as uncached side
effects. Cells that create UI elements or `mo.state` also run live so their
session identity remains current.

Notebook code must register external file dependencies through
`mo.watch.file`. Other external systems need an author-owned Marimo cache key or
side-effect boundary. marimo-export does not inspect files, network responses,
or mutable modules to replace Marimo's invalidation decision.

Read [marimo integration](marimo-integration.md) for transient assignments,
output recording, replay closure, exporter preparation, transfer tickets, and
process ownership.

## Validation

Changes to this boundary require:

- exact release and source-drift probe tests
- reversible patch and overlapping lease tests
- unrelated graph isolation tests
- signed, missing, corrupt, and unavailable-value cache tests
- cache write-barrier and cleanup tests
- warm owned build and borrowed capture
- producer identity and mid-capture source-drift tests

Run `marimo-export doctor` before a live reproduction when the adapter fails to
load.
