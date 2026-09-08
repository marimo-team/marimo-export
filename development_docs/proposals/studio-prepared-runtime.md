# marimo-studio integration record

[Marimo Studio](https://github.com/marimo-team/marimo-studio) implements a
Prepared runtime through marimo-export's public Python and browser APIs.
Studio owns view authoring, runtime selection, and presentation. marimo-export
owns the finite state-output relation, preparation, integrity, and export
consumption.

The consumer's [architecture guide](https://github.com/marimo-team/marimo-studio/blob/main/development_docs/architecture.md)
owns its implementation map. This record connects that application to the
framework contracts maintained in this repository.

## State and view policy

Studio compiles finite projection targets into an `ExportSpec` and separate
host bindings. Repeated targets share exported outputs while retaining their
own presentation hosts. Layout and host identity stay in the application, so
presentation edits can reuse prepared states.

Studio's `states.yaml` supplies an authored `StateSpace`. In a live session,
Studio plans the compiled outputs and calls `Session.observe_inputs(plan=plan)`
for the complete current vector, including ordinary Python inputs and UI
controls. It records that vector as an observation. An authored state space
controls which vectors can be selected. When the file is absent, Studio uses
the current vector and the plan's recorded observations to construct its state
space.

The [planning boundary](../architecture/preparation.md) owns normalization,
producer identity, reuse, and execution. Applications decide which observed
vectors enter an export.

## Publication and delivery

Studio supplies publication keys, capture callbacks, source-admission checks,
and an observation-based refresh predicate to
`PreparedPublicationController`. The controller owns preparation, cancellation,
replacement, and generation leases. A rejected admission leaves the last-good
publication available.

Studio owns authentication, its manifest envelope, route construction, cache
headers, and HTTP response lifetime. `PreparedExport.manifest()` supplies the
inner `marimo-export.prepared.v1` record. The controller lends a `PreparedAsset`
whose lease stays open until response delivery finishes.

Static delivery calls public `prepare()` with the compiled specification and
uses `delivery.stage()` to assemble the provider files and prepared export
before committing the application directory. The deployed Prepared runtime
reads captured states and executes browser interactions. Python execution
belongs to the producer environment.

See [Application publication and delivery](../architecture/application-publication-and-delivery.md)
for candidate admission, application-directed refresh, retained routes, and
atomic directory publication.

## Browser and progress boundaries

`PreparedStateController` owns requested inputs, exact selection, cancellation,
restoration, and transition ordering. `loadOutputs()` loads a named set of
outputs through explicit loaders with shared cancellation. Studio selects the
loaders and adapts decoded values to its projection bindings.

Studio's `PreparedStatePort` implementation owns connected staging hosts,
native Marimo model and UI registries, peer controls, and the complete visible
commit. Its native model graph and checkpoints remain inside the consumer's
Marimo frontend adapter. A failed or stale transition retains the last
committed presentation.

Preparation events report completed states across the normalized plan,
including reused states. Studio translates those events into runtime progress
and separately reports publication and browser initialization. A completed
state count does not mean that the application is ready.

See [Browser loaders and mounts](../architecture/browser-loaders-and-mounts.md)
for output consumption and the application port, and
[Preparation progress](../architecture/preparation.md#progress-and-cancellation) for event
ownership.

## Cache compatibility and validation

Studio acquires `integration.keep_cached_cells_compatible()` for its kernel
lifetime and releases the returned handle during teardown. marimo-export owns
the reversible Marimo cache adapters and their overlapping leases. See
[Execution and caching](../architecture/execution-and-caching.md).

Each coordinated dependency change needs the
[external consumer acceptance checks](../validation.md#external-consumer-acceptance).
Record candidate artifacts, repository revisions, Marimo version, and the
commands used for that run. Release acceptance is tied to those artifacts.
Current dependency selection belongs to the two repositories' manifests and
lockfiles.

The consumer tests source admission, ordinary and UI input isolation, response
leases, native presentation restoration, and runtime readiness. This repository
independently tests the producer, publication, loading, and disposal contracts
through its Python suite, browser suite, examples, and installed packages.
