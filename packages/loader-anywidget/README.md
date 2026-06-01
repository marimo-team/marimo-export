# @marimo-team/export-loader-anywidget

Loader for `anywidget.bundle.v1` formats.

This package hydrates an exported AnyWidget bundle in a browser page. It
includes the format loader and runtime needed to mount the widget without
Python, Pyodide, or a marimo server.

```ts
import { anywidgetLoader } from "@marimo-team/export-loader-anywidget";
import { readLatestExport } from "@marimo-team/export-reader";

const exp = await readLatestExport({
  root: "/export/",
  loaders: [anywidgetLoader()],
});

const handle = exp.get({
  scenario: "default",
  value: "dashboard",
  format: "bundle",
});

const widget = await handle.load();
const mounted = await widget.mount(document.querySelector("#widget")!);

try {
  // The widget is mounted and can be bridged to application state here.
} finally {
  await mounted.unmount();
}
```

Mechanics:

- Supports `anywidget.bundle.v1`.
- Reads the descriptor, frontend module, optional CSS, JSON state, and buffers.
- Restores binary buffers into the exported state tree.
- Imports the frontend module from a browser object URL.
- `mounted.unmount()` tears down the widget instance and revokes the module URL.
- Exposes a standalone runtime subpath at
  `@marimo-team/export-loader-anywidget/runtime`.
