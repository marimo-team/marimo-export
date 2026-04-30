# @marimo-team/export-loader-parquet

Loader for `dataframe.parquet.v1` artifacts.

This package converts exported Parquet bytes into browser-usable row and
metadata handles with `hyparquet`.

```ts
import { parquetLoader } from "@marimo-team/export-loader-parquet";
import { readExport } from "@marimo-team/export-reader";

const exp = await readExport({
  root: "/export/",
  manifest: "manifest.json",
  loaders: [parquetLoader()],
});

const handle = exp.get({
  scenario: "default",
  value: "prices",
  format: "parquet",
});

const parquet = await handle.load();

const metadata = await parquet.readMetadata();
const rows = await parquet.readRows({ columns: ["Date", "Close"] });
```

Mechanics:

- Supports `dataframe.parquet.v1`.
- Reads bytes from the artifact URL through `hyparquet`.
- Exposes `.readMetadata()` and `.readRows()`.
- Supports column and row-range reads.
