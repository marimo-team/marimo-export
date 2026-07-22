# @marimo-team/marimo-export-arrow

`arrow()` decodes a verified `dataframe.arrow.v1` output into a Flechette table.

```sh
pnpm add @marimo-team/marimo-export @marimo-team/marimo-export-arrow
```

```ts
import { httpSource, openExport } from "@marimo-team/marimo-export";
import { arrow } from "@marimo-team/marimo-export-arrow";

interface Price {
  symbol: string;
  close: number;
}

const published = await openExport(httpSource("/published"));
const output = published.scenario("baseline").output("prices", "arrow");
const table = await output.load(arrow<Price>());

console.log(table.numRows);
console.log(table.toArray());
```

Pass Flechette extraction options to `arrow(options)`. The returned table exposes Flechette's column selection, row access, iteration, `toArray()`, and `toColumns()` APIs.

```ts
const table = await output.load(
  arrow<Price>({
    useBigInt: true,
    useDate: true,
  }),
);
```
