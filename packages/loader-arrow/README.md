# Arrow loader workspace

This private workspace package owns verified Arrow IPC decoding through
Flechette and LZ4 frame support.

Consumers install `@marimo-team/marimo-export`, `@uwdata/flechette`, and
`lz4js`, then import the public loader subpath:

```ts
import { arrowTableLoader } from "@marimo-team/marimo-export/loader/arrow";

const table = await output.load(arrowTableLoader());
console.log(table.numRows, table.schema.fields);
```

The loader defaults to BigInt for 64-bit integers. Pass Flechette extraction
options through `arrowTableLoader({ extraction })`.
