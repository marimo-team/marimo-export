# @marimo-team/export-loader-arrow

Loader for `dataframe.arrow.v1` artifacts.

This package converts exported Arrow IPC bytes into browser-usable table handles
with `@uwdata/flechette`.

```ts
import { arrowLoader } from "@marimo-team/export-loader-arrow";
import { exportRoot, openExport } from "@marimo-team/export-reader";

const exp = await openExport(exportRoot("/export/"), {
  loaders: [arrowLoader()],
});

const handle = exp.artifact({
  scenario: "default",
  value: "prices",
  artifact: "arrow",
});

const frame = await handle.load();

const rows = await frame.rows();
const columns = await frame.columns();
```

Mechanics:

- Supports `dataframe.arrow.v1`.
- Reads the artifact entry file through the reader context.
- Parses Arrow IPC bytes with `tableFromIPC`.
- Exposes `.table()`, `.rows()`, and `.columns()`.
