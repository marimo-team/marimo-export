# Marimo upstream candidates

marimo-export targets the published Marimo 0.24.0 package through local ports
and private compatibility adapters. Each candidate on this page names a public
Marimo capability that could replace one contained adapter while preserving the
marimo-export service contract.

The migration rule is:

```text
keep marimo-export record and port
replace one compat adapter at a composition root
run the same contract and live consumer tests
delete the private probe for the replaced seam
```

Changes to an upstream interface should preserve Marimo's cache keys, stores,
serialization, signing, restoration, graph execution, and session lifecycle.

## Candidate map

| Candidate                       | Current adapter                                     | Stable marimo-export boundary        |
| ------------------------------- | --------------------------------------------------- | ------------------------------------ |
| Cached child execution          | cache patch, child runner, execution adapter        | `CachedStateExecutor`                |
| Final cache disposition         | graph-scoped attempt wrapper                        | `CacheActivity`                      |
| Forced live execution           | graph-scoped hit replacement                        | executor policy for selected cells   |
| Sequential lazy deserialization | `SequentialLazyLoader`                              | verified native return               |
| Native cache write barrier      | `flush_active_caches` adapter                       | executor run boundary                |
| Verified cached return          | private manifest, store, signing, and codec adapter | `NativeCacheReturn`                  |
| Complete cached UI restoration  | `CompleteCachedLifecycle`                           | state execution success              |
| Interactive host cache restore  | UI, Polars, and tensor host patch                   | `keep_cached_cells_compatible()`     |
| Successful-run observation hook | private final runner hook                           | `ObservedInputs` and ledger callback |
| Canonical rendered snapshots    | output hooks, `SessionView`, and UI registries      | output receipt and replay records    |

## Cached child execution

### Required behavior

An integration consumer creates a child graph, runs a selected topological
closure through native cached execution, receives final hit or miss decisions,
flushes writes, and closes the child deterministically.

The capability needs to support:

- selected cells that must execute live for one run
- native handling for every other cell
- one final disposition per executed cell
- UI definitions that require live registration after cache restoration
- unavailable cached values that must rerun
- explicit flush before dependent work
- cleanup after success, failure, and cancellation

### Current implementation

`_marimo/compat/cache/patch.py`, `attempts.py`, `lifecycle.py`,
`child_run.py`, and `execution.py` implement this behavior against private
Marimo objects. `_marimo/capabilities.py` exposes `CachedStateExecutor` and
`StateExecution` to the service side.

### Upstream shape

A Marimo context-managed cached child session could expose:

```python
class CachedChildSession(Protocol):
    async def run(
        self,
        cell_ids: Collection[CellId],
        *,
        force_live: Collection[CellId] = (),
    ) -> tuple[CellCacheReceipt, ...]: ...

    def flush(self) -> None: ...
```

The composition root would adapt those receipts to `CacheActivity` and
`StateExecution`. Planning, repository, preparation, and export records would
remain unchanged.

## Final cache disposition and forced live execution

### Required behavior

marimo-export needs the final decision after native cache validity checks and
unavailable-value handling. It also needs to request a live run for
complete-cell owners and exporter leaves whose contracts include uncached
side effects or current process resources.

### Current implementation

`cache_attempt_from_hash` is wrapped once. Active scopes are keyed by exact
graph identity. The wrapper records the effective disposition and replaces a
selected hit with an empty native attempt.

### Upstream shape

A run request could accept `force_live` cell IDs and return a typed disposition
for each cell. Marimo would retain ownership of cache attempt construction,
key semantics, and authored-cell invalidation.

The local `CacheActivity` record remains the consumer contract. The global
attempt-function patch and its source digest can then leave the adapter.

## Sequential lazy deserialization

### Required behavior

Some output codecs must deserialize on the kernel thread. Signature precedence,
missing blobs, unreadable blobs, store selection, and value restoration must
match native lazy loading.

### Current implementation

`SequentialLazyLoader` reproduces the control flow of Marimo's private
`LazyLoader._read_blobs` and invokes Marimo's store, signer, and deserializer.
The release probe pins the source digest for that method.

### Upstream shape

Marimo could make deserialization placement configurable on one loader or one
cached execution session. marimo-export would select kernel-thread execution
through a supported constructor option and remove its loader subclass.

## Native cache write barrier

### Required behavior

Background lazy-cache writes must be visible before a dependent state hashes or
before output receipt extraction reads the native store.

### Current implementation

`cache/barrier.py` registers one early post-execution hook and delegates flush to
Marimo's private `flush_active_caches()`.

### Upstream shape

A cached execution session should expose `flush()` or guarantee that run
completion includes pending writes. The marimo-export executor keeps the call
at the same state and receipt boundaries.

## Verified cached return

### Required behavior

For one output cell, marimo-export needs a stable verified view of:

- the final native manifest hash
- an inline scalar and its Python type
- or one verified NumPy, Arrow, or BlobAsset payload

### Current implementation

`cache/receipts.py` snapshots reads from the selected store, decodes Marimo's
private cache schema, resolves the effective signer, verifies the selected blob,
and returns `NativeCacheReturn`.

### Upstream shape

Marimo could return an immutable verified return receipt from cached execution:

```python
@dataclass(frozen=True)
class VerifiedReturn:
    python_type: str
    value: object | None
    codec: str
    payload: bytes | None
```

The exact upstream record may retain Marimo-native codec types. One compat
adapter maps it to `NativeCacheReturn`. Descriptor and portable asset policy
remain in marimo-export.

## Complete cached UI restoration

### Required behavior

A cache hit that restores an unavailable `UIElementStub`, `UnhashableStub`, UI
element, or `mo.state` object must execute live before export.

### Current implementation

`CompleteCachedLifecycle` reruns unavailable hits and recreates session-bound
state inside an owned graph scope.

### Upstream shape

Marimo could define cached lifecycle completeness as a native post-restore
contract and report final execution disposition through the cached execution
receipt. The local lifecycle subclass can then be replaced behind
`CachedStateExecutor`.

## Interactive host cache restoration

### Required behavior

An interactive host must restore cached composite UI values, load Polars values
through a compatible serializer, and produce contiguous bytes for Polars tensor
encoding. The capability needs a reversible lifecycle that composes with other
cached execution owners.

### Current implementation

`cache/host.py` owns one reference-counted lease over the private restored-UI
check, Polars lazy-stub loader entries, and tensor byte encoder. Studio acquires
the lease through `marimo_export.integration.keep_cached_cells_compatible()`.

### Upstream shape

Marimo could make composite UI discovery, Polars stub selection, and tensor
serialization native cache behavior. The Studio call can remain as a capability
check until every supported Marimo release provides those semantics, then the
composition root can return an idempotent empty release handle.

## Successful-run observation hook

### Required behavior

An application can subscribe to successful normal notebook runs and receive a
callback after final cell state is stable. The callback must distinguish scratch
execution, interruption, cancellation, and exceptions.

### Current implementation

`_marimo/compat/observations.py` installs one final runner callback and
multiplexes ledger registrations. It validates the live cell signature against
the saved source before resolving producer identity.

### Upstream shape

A supported run-finished subscription could provide the final graph, outcome,
and source identity. marimo-export would keep portable input inspection,
saved-source binding, queueing, revisions, and repository persistence.

## Canonical rendered snapshots

### Required behavior

Rendered-output and complete-cell projections need one final immutable record
that includes output, console, run status, UI values, virtual files, public
files, AnyWidget model closure, and function namespaces.

### Current implementation

The projection adapter installs private output hooks, records through
`SessionView`, rewrites scoped identities, closes reachable resources, and
serializes package-owned replay records.

### Upstream shape

Marimo could expose a supported snapshot API for one cell or output owner after
a run reaches final state. The snapshot should carry native ownership and
cleanup rules for files, models, and functions.

marimo-export would continue to select outputs, bind them to state and producer
identity, enforce portable limits, and write the durable export format.

## Upstream acceptance

Replace a private seam when the supported Marimo capability passes the existing
marimo-export tests for:

- cache key and store behavior
- signed and unsigned cache receipts
- missing, corrupt, and unavailable values
- live complete-cell and exporter-leaf execution
- unrelated graph isolation
- write visibility
- state failure atomicity
- observation filtering and source identity
- replay closure and browser fidelity
- cleanup after cancellation and process shutdown

The replacement is complete when `_marimo/composition.py` selects the supported
adapter and the corresponding private source digest and patch code are gone.
