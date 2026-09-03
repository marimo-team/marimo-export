# marimo-studio integration

marimo-studio turns authored HTML projections into an application backed by a
prepared notebook export. Studio owns view authoring, presentation, and runtime
UX. marimo-export owns the reusable Zero-Python preparation and browser state
machinery.

[Runtime profiles](runtime-profiles.md) defines live server, browser
WebAssembly, and prepared execution before this page describes Studio's concrete
runtime integration.

```text
Studio provider artifact
  -> PresentationSnapshot(document, immutable mount declarations)
  -> CompiledExportView(ExportSpec, ViewBindings)
  -> marimo-export plan and capture or prepare
  -> PreparedExport
  -> Studio manifest and routes
  -> @marimo-team/marimo-export/prepared
  -> Studio renderer
```

## Dependency shape during development

The Studio Python workspace resolves `marimo-export` from the sibling editable
checkout. Its browser application links the sibling
`@marimo-team/marimo-export` and `@marimo-team/portable-json` packages.

Studio imports public Python modules:

```python
from marimo_export import ExportRepository, ExportSpec, OutputSpec, prepare
from marimo_export.manifest import prepared_manifest_bytes
from marimo_export.publication import PreparedPublicationController
from marimo_export.sessions import Session
```

Studio imports the prepared browser contract from:

```ts
import {
  PreparedPublicationRefresh,
  PreparedStateController,
} from "@marimo-team/marimo-export/prepared";
```

Studio source imports neither `marimo_export._repository` nor
`marimo_export._marimo`. Its Zero-Python path carries no SQLite schema or SQL.
Its kernel host imports `keep_cached_cells_compatible()` from the public
`marimo_export.integration` module and holds the returned release callback.

## View compilation stays in Studio

Studio providers own the authored projection grammar:

```html
<marimo-cell name="graph_views"></marimo-cell>
<marimo-output value="dataset_picker"></marimo-output>
<b mo-value="deck_stats.n_edges"></b>
```

Provider inspection turns these selectors into immutable mount declarations.
The Studio compiler consumes those declarations and returns:

```python
CompiledExportView(
    spec=...,
    bindings=...,
)
```

`spec.outputs` contains public `OutputSpec` values that marimo-export can plan.
`ViewBindings` maps canonical Studio target selectors to exported output names.
Repeated projection sources share one export output while retaining independent
document hosts. A wildcard mount has no finite output set, so Studio rejects it
for Zero-Python publication.

HTML host identity stays outside ExportSpec and repository identity. Renaming a
host or changing layout preserves prepared output reuse.

## Studio chooses the authored state space

A view can place `states.yaml` beside its authored entrypoint. The file uses the
public `marimo_export.StateSpace` schema and owns the saved states and default
state. The provider artifact owns the projection set.

When a live view has no `states.yaml`, Studio constructs an explicit relation
from marimo-export observations:

1. Plan the view outputs against a baseline state and retain the plan's
   revision-consistent observation snapshot.
2. Read the live session input vector.
3. Call `repository.record_observation(plan, current_inputs)`.
4. Name the current vector `baseline` and each distinct prior vector
   `observed-<fingerprint>`.
5. Submit the resulting ExportSpec to marimo-export.

This is application policy. marimo-export owns `StateSpace` parsing, matrix
expansion, portable-value validation, observation durability, normalization,
fingerprints, default-state validation, reuse analysis, and preparation of the
explicit spec it receives.

Static delivery uses the saved relation when `states.yaml` exists. Otherwise it
prepares one `baseline: {}` row, which planning completes from the initial
notebook baseline.

## Live preparation uses public publication and session APIs

Studio passes each `(view, browser binding, presentation revision, state-space
digest)` key and a capture callback to `PreparedPublicationController`. The
state-space digest invalidates prepared work when `states.yaml` changes.
Supersession groups requests by `(view, browser binding)`. The callback receives
the controller-owned `ExportRepository` and cancellation predicate, then uses
the public session APIs:

```python
plan = session.plan(spec=compiled.spec, repository=repository)
live = session.observe_inputs()
```

```python
resolved = resolve_prepared_view(snapshot, session, repository)
prepared = session.capture(
    spec=resolved.compiled.spec,
    repository=repository,
    cancelled=is_cancelled,
)
```

The callback returns `PreparedPublicationCandidate(prepared, metadata)`. Studio
metadata contains the view bindings, selected inputs, document identity, and
plan digest. `PreparedPublicationController` owns:

- lazy repository construction and teardown
- keyed supersession and cancellation
- last-good prepared exports
- route-grace retention during manifest rotation
- deadline-driven retired-route cleanup
- independently leased assets
- observation-revision refresh

`PreparedViewRegistry` compiles the view, maps Studio keys to supersession and
route groups, and wraps the selected publication in `marimo-studio.prepared.v1`.
An HTTP response borrows `PreparedAsset` through the controller, sends the
declared regular file, then closes the independent lease.

## Studio wraps the core manifest

`PreparedExport.manifest()` produces the core record:

```json
{
  "schema": "marimo-export.prepared.v1",
  "instance": "<export identity>",
  "export_url": "./<instance>/",
  "inputs": {},
  "state_fingerprint": "<state fingerprint>"
}
```

Studio adds presentation metadata:

```json
{
  "schema": "marimo-studio.prepared.v1",
  "prepared": {},
  "projections": {
    "cells": {},
    "outputs": {},
    "values": {}
  },
  "document_sha256": "<digest>",
  "view": "slides",
  "plan_digest": "<digest>"
}
```

The nested `prepared` record remains the marimo-export contract. Projection
hosts, document identity, view name, and Studio plan identity remain
application metadata. Studio passes the complete record to
`prepared_manifest_bytes()`, which emits canonical portable JSON and enforces
the browser's 256 KiB prepared-manifest limit at the producer boundary.

## Live routes serve leased immutable files

Studio exposes one mutable manifest route and immutable instance routes:

```text
.../views/<view>/zero-python/current
.../views/<view>/zero-python/<instance>/index.json
.../views/<view>/zero-python/<instance>/assets/...
```

The current route can advance to a new prepared instance. An instance route
serves files borrowed from that exact `PreparedExport`. Route grace keeps the
previous handle alive while browsers observe the new manifest and finish prior
asset requests. The controller schedules each retirement deadline and releases
the prepared export when it expires, including periods with no route traffic.

## Static delivery reuses the same artifact

Static export opens the configured persistent `ExportRepository` and passes it
to public `prepare()` with the compiled view spec. Exact repository reuse can
return the same prepared generation before notebook startup. Missing prepared
states still run through Marimo's notebook-relative native cache. Studio closes
its prepared-export lease and repository connection after bundle commit while
the verified generation remains available for later commands.

The site contains:

```text
_marimo-studio/views/<view>/zero-python/current
_marimo-studio/views/<view>/zero-python/<instance>/index.json
_marimo-studio/views/<view>/zero-python/<instance>/assets/...
```

Static assembly verifies `PreparedExport`, copies its immutable directory beside
the provider artifact, writes the Studio manifest and bindings, verifies every
copied file, and commits the complete site through native exchange when
available or guarded rollback replacement elsewhere. Studio
performs no state execution after `PreparedExport` is returned.

Live preview and static delivery therefore consume the same notebook export and
prepared manifest contract.

## Browser application adapter

Studio composes `PreparedStateController` with a Studio renderer that implements
`PreparedStatePort`.

marimo-export owns:

- strict prepared-manifest parsing
- immutable export opening and identity verification
- exact state resolution
- pending input intent
- query and control patches
- superseded transition cancellation
- publication refresh and selection preservation
- settlement and disposal

Studio owns:

- authored host discovery
- host-to-output binding validation
- Marimo frontend rendering
- peer control synchronization
- revision rollback in its workspace
- runtime readiness and diagnostics
- the `globalThis.marimoStudio.state` application API

`PreparedStatePort.apply()` receives a complete next publication and an abort
signal. Studio loads every required output into connected staging hosts, then
commits the complete document state. A failed or superseded application leaves
the last committed document visible and disposes staged mounts.

## Interactive host cache compatibility

Studio calls `marimo_export.integration.keep_cached_cells_compatible()` while
its kernel adapter is active. marimo-export owns the pinned Marimo repairs for
cached UI definitions, Polars lazy stubs, and contiguous tensor bytes. Studio
owns the release handle as part of its kernel lifecycle. Export-owned sessions
skip Studio's renderer, value encoder, observation ledger, and cache host so one
integration owns cache capture. All private Marimo cache imports remain in
marimo-export.

Execution receipts cross the contained Marimo compatibility boundary.
Repository reuse and prepared browser control consume package-owned export
records and never receive Marimo internals.

## Integration acceptance

An integration change is complete when:

- Studio imports public marimo-export Python and npm surfaces
- Studio cache-host integration imports the public marimo-export capability
- one saved spec prepares through file and live-session sources
- exact repeat preparation starts no notebook
- host-only document changes reuse the prepared export
- live routes retain current and route-grace assets correctly
- static delivery contains no repository database or lease metadata
- the prepared controller handles rapid updates, cancellation, refresh, and disposal
- desktop and narrow browser states render every saved vector
