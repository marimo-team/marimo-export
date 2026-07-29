# Development

The workspace uses Python 3.11 or newer, Node 22.18, pnpm 11.15.1, uv, and
Vite+.

## Setup

```bash
corepack enable
make bootstrap
pnpm --filter @marimo-export/internal-loader-anywidget exec \
  playwright install --only-shell chromium
```

`uv.lock` and `pnpm-lock.yaml` are the dependency records. Add dependencies to
the smallest workspace member that imports them.

## Focused commands

```bash
uv run pytest packages/python/tests
uv run ruff check packages/python
uv run ty check

pnpm --filter @marimo-team/marimo-export test
pnpm --filter @marimo-export/internal-loader-arrow test
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla build
```

Run `make format` before `make check`.

## Python organization

Public modules use stable marimo APIs and local domain types. Private marimo
imports belong below `_marimo/compat`. Add a capability probe before relying on
a new private seam.

The package root stays limited to:

```text
BlobAsset
Client
ExportSpec
OutputSpec
Publication
PublicationResult
Session
build
capture
open_publication
```

Typed failures live in `marimo_export.errors`.

## Add an exporter

An exporter is an importable top-level callable:

```python
import json
from collections.abc import Mapping

from marimo_export import BlobAsset


def summary(value: Mapping[str, object]) -> BlobAsset:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return BlobAsset(
        data=payload,
        media_type="application/vnd.example.summary.v1+json",
        filename=None,
        metadata={"version": 1},
    )
```

Reference the callable from an ExportSpec with `module:function`:

```yaml
outputs:
  summary:
    source: report
    exporter: acme_exports:summary
```

The callable validates its input and returned value. marimo-export imports it
inside a synthetic child leaf, then marimo executes and caches the conversion.
The module must be importable in the selected kernel.

Add optional dependencies under a focused package extra. Keep source object
libraries in the notebook environment when the exporter can accept them
through a narrow conversion protocol.

Add a built-in exporter ID to
`packages/python/src/marimo_export/exporters/_definitions.py`, expose a typed
descriptor factory, and put its callable under
`packages/python/src/marimo_export/exporters/_runtime`. Built-in IDs have
closed option schemas and deterministic defaults.

## Add a browser loader

Use `defineOutputLoader` for a native codec or `defineBlobAssetLoader` for a
media representation:

```ts
export function summaryLoader() {
  return defineBlobAssetLoader({
    mediaTypes: "application/vnd.example.summary.v1+json",
    load({ payload, signal }) {
      signal?.throwIfAborted();
      return decode(payload.data);
    },
  });
}
```

Create the implementation in a private `packages/loader-<name>` workspace.
That package owns its runtime dependencies, focused tests, and result type. Add
malformed byte tests, allocation bounds, cancellation checks, and disposal
tests when it creates browser resources.

Expose the implementation through
`packages/browser/src/loader/<name>.ts`:

```ts
export * from "#loaders/<name>";
```

The `#loaders/*` TypeScript path maps the facade to the private workspace
source. Add the facade to the browser package build entries and export map, then
declare its external runtime dependencies as optional peers of
`@marimo-team/marimo-export`. The packed-package test builds the root entry
alone and every loader subpath with its peers installed.

Workspace Vite applications mirror `#loaders/*` in `resolve.alias` so the
development server transforms the facade and private source together. Published
loader subpaths resolve to the bundled browser package files.

## Protocol changes

Publication wire changes update Python production, browser parsing,
cross-language fixtures, malformed cases, package docs, and app code together.
`ExportSpec.json_schema()` generates the authoring schema on demand.

Canonical JSON fixtures are exact protocol bytes. Keep them excluded from
general-purpose formatting.
