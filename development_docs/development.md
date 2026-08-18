# Development

The workspace uses Python 3.11 or newer, Node 22.18, pnpm 11.15.1, uv, and
Vite+.

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
uv run ruff format packages/python
uv run ty check packages/python
```

Browser core:

```bash
pnpm --filter @marimo-team/marimo-export test
pnpm --filter @marimo-team/marimo-export typecheck
pnpm --filter @marimo-team/marimo-export build
```

One loader, example, or docs application:

```bash
pnpm --filter @marimo-export/internal-loader-arrow test
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla build
pnpm --filter @marimo-team/marimo-export-docs build
```

Run `make format` before `make check`.

## Change Python producer behavior

Stable public records live in `spec.py`, `export.py`, `result.py`, and
`errors.py`. Build and capture policy depend on local records and marimo
capability protocols. Private marimo imports belong under `_marimo/compat`.

Add a capability probe before relying on a new private seam. Select the adapter
through a composition root. Test the stable port and the live build or capture
path that consumes it.

The package root remains limited to:

```text
BlobAsset
Client
ExportResult
ExportSpec
NotebookExport
OutputSpec
Session
build
capture
open_export
```

Typed failures live in `marimo_export.errors`.

## Add an exporter

An exporter is an importable callable that receives one notebook result and
returns a value supported by marimo's native cache codecs:

```python
import json
from collections.abc import Mapping

from marimo_export import BlobAsset


def summary(value: Mapping[str, object]) -> BlobAsset:
    return BlobAsset(
        data=json.dumps(value, sort_keys=True).encode(),
        media_type="application/vnd.example.summary.v1+json",
        filename="summary.json",
    )
```

Reference the callable as `module:symbol` in an ExportSpec. The selected module
must be importable in the notebook kernel. Files, network responses, mutable
module state, and other external inputs need explicit cache invalidation.

To add a built-in exporter:

1. Register the ID in `exporters/_definitions.py`.
2. Expose a typed descriptor factory.
3. Implement the callable under `exporters/_runtime`.
4. Define a closed option schema and deterministic defaults.
5. Add producer and exact-byte tests.

## Add a browser loader

Create one private `packages/loader-<name>` workspace. It owns the decoder,
runtime dependency, result type, malformed-input bounds, cancellation, and
mount disposal.

Expose it through `packages/browser/src/loader/<name>.ts`:

```ts
export * from "#loaders/<name>";
```

Add the facade to the browser build entries and export map. Declare specialized
runtimes as optional peers of `@marimo-team/marimo-export`. The packed-package
test builds browser core and every loader subpath with its peers installed.

Use `defineOutputLoader` for a native codec and `defineBlobAssetLoader` for a
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

Public workflows live under `docs/guide/`. Exact contracts live under
`docs/reference/`. VitePress 2.0.0-alpha.19 builds the site, local search,
`llms.txt`, and `llms-full.txt`.

Run:

```bash
make docs-build
make docs-serve
```

Inspect navigation, search, code blocks, desktop layout, and narrow layout in a
browser. Generated VitePress output remains untracked.
