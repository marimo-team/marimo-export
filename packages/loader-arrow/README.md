# @marimo-team/export-loader-arrow

Loader for `dataframe.arrow.v1` formats.

This package converts exported Arrow IPC bytes into browser-usable table handles
with `@uwdata/flechette`.

```ts
import { arrowLoader } from "@marimo-team/export-loader-arrow";
import { readExport } from "@marimo-team/export-reader";

const arrow = arrowLoader();
const exp = await readExport({ root: "/export/" });

const handle = exp.get({
  scenario: "default",
  value: "prices",
  format: "arrow",
});

const frame = await handle.load(arrow);

const rows = await frame.rows();
const columns = await frame.columns();
```

Mechanics:

- Supports `dataframe.arrow.v1`.
- Reads the format entry file through the reader context.
- Parses Arrow IPC bytes with `tableFromIPC`.
- Exposes `.table()`, `.rows()`, and `.columns()`.
