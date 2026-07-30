# Parquet loader

Load selected rows and columns from an exported Parquet table:

```ts
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";

const rows = await output.load(
  parquetRowsLoader({
    columns: ["Symbol", "Close"],
    rowStart: 0,
    rowEnd: 100,
  }),
);
```

Install `hyparquet` beside `@marimo-team/marimo-export`.
