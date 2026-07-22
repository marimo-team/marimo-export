# @marimo-team/marimo-export-parquet

`parquet()` decodes a verified `dataframe.parquet.v1` output into row objects.

```sh
pnpm add @marimo-team/marimo-export @marimo-team/marimo-export-parquet
```

```ts
import { httpSource, openExport } from "@marimo-team/marimo-export";
import { parquet } from "@marimo-team/marimo-export-parquet";

interface Price {
  symbol: string;
  close: number;
}

const published = await openExport(httpSource("/published"));
const output = published.scenario("baseline").output("prices", "parquet");
const rows = await output.load(
  parquet<Price>({
    columns: ["symbol", "close"],
    rowStart: 0,
    rowEnd: 100,
  }),
);
```

`parquet(options)` accepts Hyparquet read options such as `columns`, `rowStart`, `rowEnd`, `filter`, `utf8`, and custom decompressors. The export reader downloads and verifies the complete payload before Hyparquet applies row and column selection in memory.
