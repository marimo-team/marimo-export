# @marimo-team/marimo-export-loader-arrow

`arrowTableLoader()` decodes verified `apache.arrow.file.v1` outputs as
Flechette tables.

```bash
pnpm add @marimo-team/marimo-export \
  @marimo-team/marimo-export-loader-arrow
```

```ts
import { arrowTableLoader } from "@marimo-team/marimo-export-loader-arrow";

const table = await output.load(arrowTableLoader());
console.log(table.numRows, table.schema.fields);
```

The loader registers LZ4 frame decoding and defaults to BigInt for 64-bit
integers. Pass Flechette extraction options through
`arrowTableLoader({ extraction })`.
