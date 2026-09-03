# Development

The workspace pins Python 3.12 for local development with Node 22.18.0, pnpm
11.15.1, uv, and Vite+. The root `devEngines.runtime` lets pnpm install the exact
Node runtime and records it in `pnpm-lock.yaml`. Package CI verifies Python 3.10
through 3.14 on Ubuntu and Windows.

## Install the workspace

```bash
corepack enable
make bootstrap
pnpm --filter @marimo-export/internal-loader-anywidget exec \
  playwright install --only-shell chromium
```

`uv.lock` and `pnpm-lock.yaml` are the dependency records. Add a dependency to
the smallest workspace member that imports it.

## Run focused checks

Python package:

```bash
uv run pytest packages/python/tests
uv run ruff check packages/python
uv run ruff format --check packages/python
uv run ty check packages/python
```

Browser core:

```bash
pnpm --filter @marimo-team/marimo-export test
pnpm --filter @marimo-team/marimo-export typecheck
pnpm --filter @marimo-team/marimo-export build
```

Portable JSON:

```bash
pnpm --filter @marimo-team/portable-json test
pnpm --filter @marimo-team/portable-json typecheck
pnpm --filter @marimo-team/portable-json test:package
```

Workspace package exports resolve browser and portable JSON TypeScript source.
`publishConfig.exports` maps packed packages to their built `dist` entry points.
The pnpm `beforePacking` hook emits the public browser dependency set after
Vite+ bundles its internal AnyWidget loader.
Run `pnpm run build:browser` to pack portable JSON before the browser package.

One loader, example, or docs application:

```bash
pnpm --filter @marimo-export/internal-loader-arrow test
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla build
pnpm --filter @marimo-team/marimo-export-docs build
```

Run `make format` before `make check`.

## Change Python producer behavior

Stable public records live in `spec.py`, `planning.py`, `prepared.py`,
`progress.py`, `descriptors.py`, `index.py`, `inspection.py`, `wire.py`,
`result.py`, `repository.py`, and `errors.py`. `_services` owns planning,
preparation, capture, artifact assembly, and write policy. Private Marimo imports
belong under `_marimo/compat`. SQL belongs under `_repository/sqlite`.

Add a capability probe before relying on a new private seam. Select the adapter
through a composition root. Test the stable port and the live build or capture
path that consumes it.

The package root remains limited to the common workflow:

```text
ExportPlan
ExportRepository
ExportResult
ExportSpec
NotebookExport
OutputSpec
PreparedExport
ProgressEvent
VerificationResult
build
capture
open_export
plan
prepare
verify_export
```

Advanced capabilities live in focused modules such as
`marimo_export.sessions`, `marimo_export.observations`,
`marimo_export.outputs`, `marimo_export.diagnostics`, and
`marimo_export.wire`. Core export failures live in `marimo_export.errors`.
Repository and observation modules expose the failures tied to their own
lifecycle contracts.

## Change planning or preparation

`plan()` reports exact and per-state reuse. `prepare()` and borrowed-session
capture execute missing states and return a leased `PreparedExport`. `build()`
adds caller-destination write and verification.

Keep file and borrowed-session paths aligned through `_services`. Test exact
reuse, missing-state reuse, default-state changes, cancellation, progress,
source drift, and cleanup through the public API.

Read [Planning and preparation](architecture/preparation.md) before changing
identity or lifecycle ordering.

## Change the export repository

`ExportRepository` is the public plan-shaped facade for observations, exact
prepared lookup, status, pruning, and lifecycle.
`_repository/preparation.py` is the private capability used by producer
services. `_repository/observations.py` is the private producer-keyed capability
used by the observation ledger and preparation. `_repository/sqlite` owns
connections, transactions, schema, and SQL. Artifact modules own verified
files, staging, leases, reservations, fencing, recovery, and retention.

Run the repository, concurrency, integrity, lifecycle, observation, and
boundary suites. A storage failure must preserve the current generation. A
stale reservation must fail before pointer publication. A live artifact lease
must survive concurrent preparation and retention.

Read [Export repository](architecture/repository.md) before changing a table,
identity, transaction, lease, fence, or cleanup path.

## Change the Marimo adapter

The package pins `marimo==0.24.0`. `_marimo/compat/release.json` records the
release commit and source digests required by the cache adapter. Update the pin,
release record, focused probe, adapter tests, and live build and capture evidence
together.

The Marimo checkout is an external source reference for this integration.
Implement current adapter behavior in marimo-export through package-owned ports.
Read [Execution and caching](architecture/execution-and-caching.md) and
[Marimo upstream candidates](architecture/marimo-upstream-candidates.md).

## Add an exporter

An exporter is an importable callable that receives one notebook result and
returns a value supported by marimo's native cache codecs:

```python
import json
from collections.abc import Mapping

from marimo_export.outputs import BlobAsset
from marimo_export.wire import portable_json


def summary(value: Mapping[str, object], *, indent: int = 2) -> BlobAsset:
    normalized = portable_json(value, "summary")
    return BlobAsset(
        data=json.dumps(normalized, indent=indent, sort_keys=True).encode(),
        media_type="application/vnd.example.summary.v1+json",
        filename="summary.json",
    )
```

Reference the callable as `module:symbol` in an ExportSpec. The selected module
and every declared dependency must be importable in the notebook kernel.
Declare source modules that affect conversion output, including modules loaded
dynamically. A borrowed session uses module objects already loaded at first
exporter preparation. Restart the session for earlier edits to take effect.
Later disk drift fails with `exporter_source_changed`. Custom exporter leaves
run live when their process resources require it. The selected notebook value
follows Marimo's native cache policy. Create `mo.watch.file` in an upstream
cell before reading the watched path from dependent cells. Give other external
systems an author-owned Marimo cache or side-effect boundary so native
invalidation can observe them.

```python
from marimo_export.exporters import importable

summary_exporter = importable(
    "summary_exporter:summary",
    options={"indent": 2},
    dependencies=("json",),
)
```

To add a built-in exporter:

1. Register the ID in `exporters/_definitions.py`.
2. Expose a typed descriptor factory.
3. Implement the callable under `exporters/_runtime`.
4. Define a closed option schema and deterministic defaults.
5. Add producer, cache-reuse, and exact-byte tests.

## Add a browser loader

Implement zero-runtime JSON, text, or core format decoding in
`packages/browser/src/loader`. Add a private `packages/loader-<name>` workspace
when a specialized decoder owns a runtime dependency, result type,
malformed-input bounds, cancellation, or mount disposal.

Expose a loader workspace through `packages/browser/src/loader/<name>.ts`:

```ts
export * from "#loaders/<name>";
```

When browser must depend on a loader workspace for linked source consumption,
keep that workspace independent of browser contracts. Export a decoder over
verified bytes or package-owned records, then bind it to `defineOutputLoader` or
`defineBlobAssetLoader` in the public browser facade.

Add the loader entry to the browser build and export map. Declare specialized
runtimes as optional peers of `@marimo-team/marimo-export`. The packed-package
test builds browser core and every loader subpath with its peers installed.

Use `defineOutputLoader` for an export codec and `defineBlobAssetLoader` for a
media representation.

## Change a cross-language protocol

Update these surfaces together:

1. Python construction and parsing
2. browser parsing and immutable types
3. canonical cross-language fixtures
4. malformed-input and boundary tests
5. CLI inspect and verify behavior
6. public reference and example code
7. packed Python and npm checks

The current durable schema is `marimo-export.export.v1`.
`ExportSpec.json_schema()` generates the authoring schema on demand.

## Change documentation

Public concepts live under `docs/concepts/`, workflows under `docs/guide/`, and
exact contracts under `docs/reference/`. `apps/docs/navigation.mjs` owns every
route and feeds the site and LLM text bundles. Read
[Documentation system](documentation.md) before adding or moving a page.

VitePress 2.0.0-alpha.19 builds the site, local search, per-page Markdown,
`llms.txt`, and `llms-full.txt`.

Run:

```bash
node apps/docs/scripts/check-navigation.mjs
make docs-build
make docs-serve
```

Inspect navigation, search, code blocks, desktop layout, and narrow layout in a
browser. Generated VitePress output remains untracked.
