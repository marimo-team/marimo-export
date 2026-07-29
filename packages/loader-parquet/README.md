# Parquet loader workspace

This private workspace package owns verified Parquet decoding through
Hyparquet.

Consumers install `@marimo-team/marimo-export` with `hyparquet`, then import
the public loader subpath:

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

Loader options pass through to Hyparquet, including columns, row ranges,
filters, logical-type parsers, and compressor plugins. Cancellation is checked
before and after decoding.
