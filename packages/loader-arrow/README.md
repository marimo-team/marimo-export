# @marimo-team/export-loader-arrow

Loader for `dataframe.arrow.v1` artifacts.

This package converts exported Arrow IPC bytes into browser-usable table handles
with `@uwdata/flechette`.

```ts
import { arrowLoader } from "@marimo-team/export-loader-arrow";
import { readLatestExport } from "@marimo-team/export-reader";

const exp = await readLatestExport({
  root: "/export/",
  loaders: [arrowLoader()],
});

const handle = exp.get({
  scenario: "default",
  value: "prices",
  format: "arrow",
});

const table = await handle.load();

const rows = await table.rows();
const columns = table.columns();
```

Mechanics:

- Supports `dataframe.arrow.v1`.
- Reads the artifact entry file through the reader context.
- Parses Arrow IPC bytes with `tableFromIPC`.
- Exposes `.table()`, `.rows()`, and `.columns()`.
