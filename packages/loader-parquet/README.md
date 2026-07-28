# @marimo-team/marimo-export-loader-parquet

`parquetRowsLoader()` reads verified Parquet `BlobAsset` bytes into row objects
with Hyparquet.

```bash
pnpm add @marimo-team/marimo-export \
  @marimo-team/marimo-export-loader-parquet
```

```ts
import { parquetRowsLoader } from "@marimo-team/marimo-export-loader-parquet";

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
