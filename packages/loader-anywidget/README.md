# @marimo-team/export-loader-anywidget

Loader for `anywidget.bundle.v1` formats.

This package hydrates an exported AnyWidget bundle in a browser page. It
includes the format loader and runtime needed to mount the widget without
Python, Pyodide, or a marimo server.

## Installation

```bash
npm install @marimo-team/export-loader-anywidget @marimo-team/export-reader
```

## Usage

```ts
import { anywidgetLoader } from "@marimo-team/export-loader-anywidget";
import { readExport } from "@marimo-team/export-reader";

const anywidget = anywidgetLoader();
const exp = await readExport({ root: "/export/" });

const handle = exp.get({
  scenario: "default",
  value: "dashboard",
  format: "bundle",
});

const widget = await handle.load(anywidget);
const mounted = await widget.mount(document.querySelector("#widget")!);

try {
  // Bridge model state to the host app while the widget is mounted.
} finally {
  await mounted.unmount();
}
```

## Contract

- Supports `anywidget.bundle.v1`.
- Reads the descriptor, frontend module, optional CSS, JSON state, and buffers.
- Restores binary buffers into the exported state tree.
- Imports the frontend module from a browser object URL.
- `mounted.unmount()` tears down the widget instance and revokes the module URL.
- Exposes a standalone runtime subpath at
  `@marimo-team/export-loader-anywidget/runtime`.
