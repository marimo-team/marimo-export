# AnyWidget loader workspace

`@marimo-export/internal-loader-anywidget` owns the browser-local
[AnyWidget](https://anywidget.dev/) model
runtime used by the public
[`@marimo-team/marimo-export/loader/anywidget`](../browser/src/loader/anywidget.ts)
facade. The workspace package is private and is bundled into the public browser
package during release builds.

The implementation decodes the exported widget graph, restores serializer-owned
model state, loads widget modules, mounts the root model, routes browser-local
model changes, and disposes the complete mounted graph.

Run focused checks from the repository root:

```bash
pnpm --filter @marimo-export/internal-loader-anywidget test
pnpm --filter @marimo-export/internal-loader-anywidget test:browser
pnpm --filter @marimo-export/internal-loader-anywidget typecheck
```

Public consumers install `@marimo-team/marimo-export` and follow the
[AnyWidget representation contract](../../docs/reference/representations.md).
