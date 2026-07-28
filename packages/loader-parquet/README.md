# marimo-export Parquet loader

`parquetRowsLoader()` reads verified Parquet BlobAssets into row objects with
Hyparquet.

```ts
import { parquetRowsLoader } from "@marimo-team/marimo-export-loader-parquet";

const rows = await output.load(parquetRowsLoader({ columns: ["symbol", "close"] }));
```
