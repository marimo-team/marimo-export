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

`uv.lock` and `pnpm-lock.yaml` are the dependency records. Add a dependency to
the smallest workspace member that imports it.

## Focused commands

Python:

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

One loader or example:

```bash
pnpm --filter @marimo-export/internal-loader-arrow test
pnpm --filter @marimo-team/marimo-export-example-vite-vanilla build
```

Run `make format` before `make check`.

## Python organization

Public modules use stable marimo APIs and local domain types. Private marimo
imports belong below `_marimo/compat`. Add a capability probe before relying on
a new private seam.

Core files are:

| Path                 | Role                                                           |
| -------------------- | -------------------------------------------------------------- |
| `spec.py`            | ExportSpec and OutputSpec                                      |
| `export.py`          | export wire types, result types, codecs, and canonical parsing |
| `reader.py`          | immutable local NotebookExport reader                          |
| `_writer.py`         | staging, verification, and atomic commit                       |
| `_build.py`          | managed build lifecycle                                        |
| `client.py`          | borrowed-session client and capture                            |
| `_execution/plan.py` | baseline normalization and transient cell code                 |
| `_marimo/bridge.py`  | attached-kernel operation boundary                             |
| `_marimo/compat`     | private marimo adapter                                         |

The package root stays limited to:

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

An exporter is an importable callable:

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

Reference the callable from an ExportSpec:

```yaml
outputs:
  summary:
    source: report
    exporter: acme_exports:summary
```

The callable validates its input and returned value. marimo-export imports it
inside a transient output cell, then marimo executes and caches the conversion.
The module must be importable in the selected kernel.

Pass representation configuration through exporter options. Files, network
responses, mutable module state, and other external inputs require the same
cache invalidation discipline as notebook code.

Add optional dependencies under a focused package extra. Keep source-object
libraries in the notebook environment when the exporter can accept them
through a narrow conversion protocol.

To add a built-in:

1. register the ID in
   `packages/python/src/marimo_export/exporters/_definitions.py`
2. expose a typed descriptor factory
3. implement the callable under
   `packages/python/src/marimo_export/exporters/_runtime`
4. define a closed option schema and deterministic defaults
5. add producer and exact-byte tests

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
That package owns its runtime dependencies, focused tests, and result type.
Cover malformed bytes, allocation bounds, cancellation, and disposal when it
creates browser resources.

Expose the implementation through a browser facade:

```ts
export * from "#loaders/<name>";
```

The facade lives at `packages/browser/src/loader/<name>.ts`. Add it to the
browser package build entries and export map. Declare its runtime dependencies
as optional peers of `@marimo-team/marimo-export`.

The `#loaders/*` TypeScript path maps each facade to its private workspace
source. Workspace Vite applications mirror that mapping in `resolve.alias`.
The packed-package test builds the root entry and each loader subpath with the
required peers installed.

## Protocol changes

An export wire change updates these surfaces together:

1. Python wire construction and parsing
2. browser parsing and immutable types
3. cross-language canonical fixtures
4. malformed-input tests
5. CLI inspect and verify behavior
6. docs and example code
7. packed Python and npm checks

The current schema is `marimo-export.export.v1`.
`ExportSpec.json_schema()` generates the authoring schema on demand.

Canonical JSON fixtures are exact protocol bytes. Keep them outside
general-purpose formatting.
